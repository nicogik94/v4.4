import sys
import uuid
from pathlib import Path

from docx import Document
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client_delivery.extract import build_delivery_package  # noqa: E402
from client_delivery.quality import (  # noqa: E402
    MISSING_ACTION_OWNER_WARNING,
    NON_NUMERIC_THRESHOLD_WARNING,
    delivery_quality_warnings,
)
from client_delivery.render_docx import render_board_memo_docx  # noqa: E402
from client_delivery.render_xlsx import render_execution_tracker_xlsx  # noqa: E402
from tests.fixtures import fake_state  # noqa: E402


class OddObject:
    def __str__(self):
        return "odd-object"


def _docx_text(path: Path) -> str:
    document = Document(path)
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def test_docx_is_readable(tmp_path):
    package = build_delivery_package(fake_state())
    path = render_board_memo_docx(package, tmp_path / "memo.docx")

    text = _docx_text(path)

    assert "Board Memo" in text
    assert "Recommendation" in text
    assert "Human Review" in text


def test_xlsx_is_readable(tmp_path):
    package = build_delivery_package(fake_state())
    path = render_execution_tracker_xlsx(package, tmp_path / "tracker.xlsx")

    workbook = load_workbook(path)

    assert workbook.sheetnames == [
        "Decision Summary",
        "30-60-90 Actions",
        "KPI Tracker",
        "Assumptions",
        "Review Triggers",
    ]


def test_renderers_tolerate_object_evidence(tmp_path):
    state = fake_state()
    state["execution_plan"][0]["evidence"] = [
        uuid.uuid4(),
        {"source": "dict"},
        7,
        None,
        ["nested", uuid.uuid4()],
        OddObject(),
    ]
    package = build_delivery_package(state)

    render_board_memo_docx(package, tmp_path / "memo.docx")
    render_execution_tracker_xlsx(package, tmp_path / "tracker.xlsx")


def test_non_numeric_thresholds_do_not_crash(tmp_path):
    state = fake_state()
    state["kpis"][0]["threshold_red"] = "red if adoption is blocked"
    state["kpis"][0]["threshold_amber"] = "amber if adoption is uncertain"
    package = build_delivery_package(state)

    warnings = delivery_quality_warnings(package)
    path = render_execution_tracker_xlsx(package, tmp_path / "tracker.xlsx")

    assert path.exists()
    assert NON_NUMERIC_THRESHOLD_WARNING in warnings


def test_missing_action_owner_warning_is_emitted():
    state = fake_state()
    state["execution_plan"][1]["owner"] = ""
    package = build_delivery_package(state)

    warnings = delivery_quality_warnings(package)

    assert MISSING_ACTION_OWNER_WARNING in warnings
