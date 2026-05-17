"""Service orchestration for Client Delivery Generator v0.5."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .extract import build_delivery_package
from .manifest import VALIDATION_STATUS, write_delivery_manifest
from .quality import delivery_quality_warnings
from .render_docx import render_board_memo_docx
from .render_xlsx import render_execution_tracker_xlsx
from .utils import safe_text


BOARD_MEMO_FILENAME = "strategic_decision_board_memo.docx"
EXECUTION_TRACKER_FILENAME = "decision_execution_tracker.xlsx"
MANIFEST_FILENAME = "delivery_manifest.json"


def generate_client_delivery_package(state, output_dir) -> dict:
    base_dir = Path(output_dir)
    latest_dir = base_dir / "latest"
    run_id = _run_id()
    run_dir = base_dir / run_id
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    package = build_delivery_package(state)
    warnings = _dedupe([*package.extraction_warnings, *delivery_quality_warnings(package)])

    latest_outputs = _write_artifact_set(package, latest_dir, warnings)
    _write_artifact_set(package, run_dir, warnings)

    return {
        "project_id": safe_text(package.project_id),
        "requires_human_review": True,
        "validation_status": VALIDATION_STATUS,
        "quality_warnings": warnings,
        "outputs": {
            "board_memo_docx": str(latest_outputs["board_memo_docx"]),
            "execution_tracker_xlsx": str(latest_outputs["execution_tracker_xlsx"]),
            "manifest_json": str(latest_outputs["manifest_json"]),
        },
    }


def _write_artifact_set(package, directory: Path, warnings: list[str]) -> dict[str, Path]:
    board_memo = render_board_memo_docx(package, directory / BOARD_MEMO_FILENAME)
    tracker = render_execution_tracker_xlsx(package, directory / EXECUTION_TRACKER_FILENAME)
    manifest_path = directory / MANIFEST_FILENAME
    files = {
        "board_memo_docx": board_memo,
        "execution_tracker_xlsx": tracker,
        "manifest_json": manifest_path,
    }
    manifest = write_delivery_manifest(package, manifest_path, files, warnings)
    return {
        "board_memo_docx": board_memo,
        "execution_tracker_xlsx": tracker,
        "manifest_json": manifest,
    }


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = safe_text(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
