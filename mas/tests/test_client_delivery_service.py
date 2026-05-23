import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client_delivery.service import generate_client_delivery_package  # noqa: E402
from tests.fixtures import fake_state  # noqa: E402


def _manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_service_returns_expected_shape(tmp_path):
    result = generate_client_delivery_package(fake_state(), tmp_path)

    assert result["project_id"] == "client-delivery-fixture"
    assert result["requires_human_review"] is True
    assert result["validation_status"] == "awaiting_side_by_side_defense_test"
    assert isinstance(result["quality_warnings"], list)
    assert set(result["outputs"]) == {"board_memo_docx", "execution_tracker_xlsx", "manifest_json"}
    for output in result["outputs"].values():
        assert Path(output).exists()


def test_service_writes_latest_and_run_id(tmp_path):
    generate_client_delivery_package(fake_state(), tmp_path)

    latest = tmp_path / "latest"
    run_dirs = [path for path in tmp_path.iterdir() if path.is_dir() and path.name != "latest"]

    assert latest.exists()
    assert len(run_dirs) == 1
    assert re.fullmatch(r"\d{8}T\d{6}Z", run_dirs[0].name)
    for directory in [latest, run_dirs[0]]:
        assert (directory / "strategic_decision_board_memo.docx").exists()
        assert (directory / "decision_execution_tracker.xlsx").exists()
        assert (directory / "delivery_manifest.json").exists()


def test_service_emits_warnings_on_sparse_state(tmp_path):
    result = generate_client_delivery_package({"project_id": "sparse"}, tmp_path)
    manifest = _manifest(result["outputs"]["manifest_json"])

    assert result["quality_warnings"]
    assert manifest["quality_warnings"]


def test_service_merges_extraction_and_quality_warnings(tmp_path):
    state = {
        "project_id": "merged-warnings",
        "execution_plan": [{"phase": "not-a-phase", "action": "Only one action"}],
    }

    result = generate_client_delivery_package(state, tmp_path)
    manifest = _manifest(result["outputs"]["manifest_json"])
    warnings = "\n".join(result["quality_warnings"])

    assert "Unknown execution phase" in warnings
    assert "fewer than 3 execution actions" in warnings
    assert manifest["quality_warnings"] == result["quality_warnings"]
