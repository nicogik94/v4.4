import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scan_smoke_exports import (  # noqa: E402
    CORE_ARCHIVE_MEMBERS,
    EXPECTED_EXPORTS,
    classify_export_file,
    scan_export_directory,
    validate_machine_archive,
    validate_pdf,
    validate_xlsx,
)


def _write_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n" + b"0" * 128 + b"\n%%EOF\n")


def _write_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("xl/workbook.xml", "<workbook></workbook>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet></worksheet>")


def _write_machine_archive(path: Path, *, profile: str = "machine_archive", include_core: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        manifest = {
            "export_profile": profile,
            "export_format": "zip",
            "included_files": sorted(CORE_ARCHIVE_MEMBERS),
        }
        archive.writestr("export_manifest.json", json.dumps(manifest))
        if include_core:
            for name in CORE_ARCHIVE_MEMBERS - {"export_manifest.json"}:
                archive.writestr(name, "{}" if name.endswith(".json") else "report")


def _write_complete_export_set(directory: Path, project_id: str = "smoke-project") -> None:
    timestamp = "20260531T120000Z"
    for profile, fmt in EXPECTED_EXPORTS.items():
        path = directory / f"{project_id}-{profile}-{timestamp}.{fmt}"
        if fmt == "pdf":
            _write_pdf(path)
        elif fmt == "xlsx":
            _write_xlsx(path)
        elif fmt == "zip":
            _write_machine_archive(path)


def test_classify_export_file_parses_expected_profile_filename(tmp_path):
    path = tmp_path / "project-123-client_dossier-20260531T120000Z.pdf"
    _write_pdf(path)

    classified = classify_export_file(path)

    assert classified is not None
    assert classified["project_id"] == "project-123"
    assert classified["profile"] == "client_dossier"
    assert classified["format"] == "pdf"


def test_complete_export_set_passes(tmp_path):
    _write_complete_export_set(tmp_path)

    result = scan_export_directory(tmp_path)

    assert result["ok"] is True
    assert result["errors"] == []
    assert set(result["artifacts"]) == set(EXPECTED_EXPORTS)


def test_missing_profile_fails(tmp_path):
    _write_complete_export_set(tmp_path)
    (tmp_path / "smoke-project-report-20260531T120000Z.pdf").unlink()

    result = scan_export_directory(tmp_path)

    assert result["ok"] is False
    assert "Missing report pdf export." in result["errors"]


def test_duplicate_profile_fails(tmp_path):
    _write_complete_export_set(tmp_path)
    _write_pdf(tmp_path / "smoke-project-report-20260531T120001Z.pdf")

    result = scan_export_directory(tmp_path)

    assert result["ok"] is False
    assert any("Duplicate or ambiguous report exports" in error for error in result["errors"])


def test_invalid_pdf_fails(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a pdf")

    result = validate_pdf(path)

    assert result["ok"] is False
    assert any("PDF header is missing" in error for error in result["errors"])


def test_invalid_xlsx_fails(tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not an xlsx")

    result = validate_xlsx(path)

    assert result["ok"] is False
    assert any("Invalid XLSX zip package" in error for error in result["errors"])


def test_machine_archive_manifest_profile_validation_fails(tmp_path):
    path = tmp_path / "archive.zip"
    _write_machine_archive(path, profile="operator_dossier")

    result = validate_machine_archive(path)

    assert result["ok"] is False
    assert any("export_profile is not machine_archive" in error for error in result["errors"])


def test_machine_archive_requires_current_core_members(tmp_path):
    path = tmp_path / "archive.zip"
    _write_machine_archive(path, include_core=False)

    result = validate_machine_archive(path)

    assert result["ok"] is False
    assert any("Missing archive members" in error for error in result["errors"])


def test_project_id_filter_rejects_mismatched_project_files(tmp_path):
    _write_complete_export_set(tmp_path, project_id="other-project")

    result = scan_export_directory(tmp_path, project_id="expected-project")

    assert result["ok"] is False
    assert all(artifact["ok"] is False for artifact in result["artifacts"].values())
    assert any("Missing report pdf export." == error for error in result["errors"])


def test_local_runtime_check_script_contains_expected_operator_checks():
    script = (ROOT / "scripts" / "local_runtime_check.ps1").read_text(encoding="utf-8")

    for token in ("Build", "RebuildApp", "TimeoutSeconds", "ApiBase", "SkipComposeUp"):
        assert token in script
    for endpoint in ("/health", "/runtime/preflight", "/runtime/release-readiness"):
        assert endpoint in script
    for recovery in (
        "error while creating mount source path",
        "mkdir /run/desktop/mnt/host/c: file exists",
        "dockerDesktopLinuxEngine",
        "docker compose down --remove-orphans",
        "wsl --shutdown",
    ):
        assert recovery in script
