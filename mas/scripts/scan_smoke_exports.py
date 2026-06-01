#!/usr/bin/env python
"""Validate the six expected local smoke export artifacts.

Usage:
    python scripts/scan_smoke_exports.py <export_dir> [--json] [--project-id <id>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


EXPECTED_EXPORTS: dict[str, str] = {
    "report": "pdf",
    "client_dossier": "pdf",
    "operator_dossier": "pdf",
    "machine_archive": "zip",
    "client_monitoring_template": "xlsx",
    "operator_monitoring_template": "xlsx",
}

CORE_ARCHIVE_MEMBERS = {
    "export_manifest.json",
    "project_state.json",
    "report.md",
    "phase_outputs.json",
    "clarifications.json",
    "evidence_locator_register.json",
    "uploaded_file_manifest.json",
    "policy_summary.json",
}

FILENAME_RE = re.compile(
    r"^(?P<project_id>.+)-(?P<profile>report|client_dossier|operator_dossier|"
    r"machine_archive|client_monitoring_template|operator_monitoring_template)-"
    r"(?P<timestamp>\d{8}T\d{6}Z)\.(?P<format>pdf|zip|xlsx)$",
    re.IGNORECASE,
)

PDF_MIN_BYTES = 32


def classify_export_file(path: str | Path) -> dict[str, Any] | None:
    """Return export metadata for a recognized smoke artifact filename."""
    file_path = Path(path)
    match = FILENAME_RE.match(file_path.name)
    if not match:
        return None
    data = match.groupdict()
    profile = data["profile"].lower()
    fmt = data["format"].lower()
    if EXPECTED_EXPORTS.get(profile) != fmt:
        return None
    return {
        "path": file_path,
        "filename": file_path.name,
        "project_id": data["project_id"],
        "profile": profile,
        "format": fmt,
        "timestamp": data["timestamp"],
    }


def validate_pdf(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    errors: list[str] = []
    try:
        size = file_path.stat().st_size
        if size <= PDF_MIN_BYTES:
            errors.append(f"PDF is too small ({size} bytes).")
        with file_path.open("rb") as handle:
            header = handle.read(4)
        if header != b"%PDF":
            errors.append("PDF header is missing.")
    except OSError as exc:
        errors.append(f"Could not read PDF: {exc}")
    return {"ok": not errors, "errors": errors}


def validate_xlsx(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    errors: list[str] = []
    try:
        with zipfile.ZipFile(file_path) as workbook:
            names = set(workbook.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "errors": [f"Invalid XLSX zip package: {exc}"]}

    required = {"[Content_Types].xml", "xl/workbook.xml"}
    missing = sorted(required - names)
    if missing:
        errors.append("Missing XLSX core members: " + ", ".join(missing))
    if not any(name.startswith("xl/worksheets/sheet") and name.endswith(".xml") for name in names):
        errors.append("Missing XLSX worksheet XML.")
    return {"ok": not errors, "errors": errors}


def validate_machine_archive(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    errors: list[str] = []
    try:
        with zipfile.ZipFile(file_path) as archive:
            names = set(archive.namelist())
            missing = sorted(CORE_ARCHIVE_MEMBERS - names)
            if missing:
                errors.append("Missing archive members: " + ", ".join(missing))
            if "export_manifest.json" in names:
                try:
                    manifest = json.loads(archive.read("export_manifest.json").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                    errors.append(f"Invalid export_manifest.json: {exc}")
                else:
                    if manifest.get("export_profile") != "machine_archive":
                        errors.append("export_manifest.json export_profile is not machine_archive.")
                    if manifest.get("export_format") != "zip":
                        errors.append("export_manifest.json export_format is not zip.")
            else:
                errors.append("export_manifest.json is missing.")
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "errors": [f"Invalid machine archive ZIP: {exc}"]}
    return {"ok": not errors, "errors": errors}


def _validate_artifact(profile: str, path: Path) -> dict[str, Any]:
    size = path.stat().st_size if path.exists() else 0
    errors: list[str] = []
    if size <= 0:
        errors.append("File is empty.")

    if profile == "machine_archive":
        validation = validate_machine_archive(path)
    elif EXPECTED_EXPORTS[profile] == "xlsx":
        validation = validate_xlsx(path)
    elif EXPECTED_EXPORTS[profile] == "pdf":
        validation = validate_pdf(path)
    else:  # pragma: no cover - defensive for future profile additions
        validation = {"ok": False, "errors": [f"Unsupported profile {profile}."]}

    errors.extend(validation.get("errors", []))
    return {"ok": not errors, "path": str(path), "size_bytes": size, "errors": errors}


def scan_export_directory(path: str | Path, project_id: str | None = None) -> dict[str, Any]:
    """Validate one complete smoke export set in a directory."""
    export_dir = Path(path)
    artifacts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not export_dir.exists() or not export_dir.is_dir():
        return {
            "ok": False,
            "directory": str(export_dir),
            "artifacts": artifacts,
            "errors": [f"Export directory does not exist: {export_dir}"],
        }

    matches: dict[str, list[dict[str, Any]]] = {profile: [] for profile in EXPECTED_EXPORTS}
    for item in export_dir.iterdir():
        if not item.is_file():
            continue
        classified = classify_export_file(item)
        if not classified:
            continue
        if project_id and classified["project_id"] != project_id:
            continue
        matches[classified["profile"]].append(classified)

    for profile in EXPECTED_EXPORTS:
        profile_matches = matches[profile]
        if not profile_matches:
            errors.append(f"Missing {profile} {EXPECTED_EXPORTS[profile]} export.")
            artifacts[profile] = {
                "ok": False,
                "path": None,
                "errors": [f"Missing {profile} {EXPECTED_EXPORTS[profile]} export."],
            }
            continue
        if len(profile_matches) > 1:
            names = ", ".join(sorted(match["filename"] for match in profile_matches))
            error = f"Duplicate or ambiguous {profile} exports: {names}"
            errors.append(error)
            artifacts[profile] = {"ok": False, "path": None, "errors": [error]}
            continue
        artifact = _validate_artifact(profile, profile_matches[0]["path"])
        artifacts[profile] = artifact
        errors.extend(f"{profile}: {error}" for error in artifact["errors"])

    return {
        "ok": not errors,
        "directory": str(export_dir),
        "project_id": project_id or "",
        "artifacts": artifacts,
        "errors": errors,
    }


def _print_text_summary(result: dict[str, Any]) -> None:
    print(f"Smoke export scan: {result['directory']}")
    for profile in EXPECTED_EXPORTS:
        artifact = result["artifacts"].get(profile, {})
        status = "PASS" if artifact.get("ok") else "FAIL"
        path = artifact.get("path") or "(missing)"
        print(f"[{status}] {profile}: {path}")
        for error in artifact.get("errors", []):
            print(f"  - {error}")
    print("PASS" if result["ok"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate v4 smoke export artifacts.")
    parser.add_argument("export_dir", help="Directory containing downloaded smoke exports.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON instead of text.")
    parser.add_argument("--project-id", help="Only consider exports whose filename starts with this project ID.")
    args = parser.parse_args(argv)

    result = scan_export_directory(args.export_dir, project_id=args.project_id)
    if args.json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text_summary(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
