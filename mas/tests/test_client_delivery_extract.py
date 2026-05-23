import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client_delivery.extract import build_delivery_package  # noqa: E402
from client_delivery.models import DeliveryPackage  # noqa: E402
from tests.fixtures import fake_state  # noqa: E402


class OddObject:
    def __str__(self):
        return "odd-object"


def test_sparse_state_does_not_crash():
    package = build_delivery_package({"project_id": "p1"})

    assert isinstance(package, DeliveryPackage)
    assert package.project_id == "p1"
    assert package.extraction_warnings


def test_phase_normalization():
    state = fake_state()
    state["execution_plan"] = [
        {"phase": "30", "action": "A"},
        {"phase": "30d", "action": "B"},
        {"phase": "30 day", "action": "C"},
        {"phase": "1-30", "action": "D"},
        {"phase": "unexpected", "action": "E"},
    ]

    package = build_delivery_package(state)

    assert [action.phase for action in package.execution_plan] == ["30d", "30d", "30d", "30d", "90d"]
    assert any("Unknown execution phase" in warning for warning in package.extraction_warnings)


def test_non_string_evidence_does_not_crash():
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

    assert package.execution_plan[0].evidence
    assert package.project_id == state["project_id"]


def test_strategy_action_owners_are_preserved():
    state = fake_state()
    state.pop("execution_plan")
    state["strategy"]["strategies"][0]["owner"] = "Pilot owner"
    state["strategy"]["strategies"][1]["owner_role"] = "Operations lead"
    state["strategy"]["strategies"][2]["accountable"] = "Executive sponsor"

    package = build_delivery_package(state)

    assert [action.owner for action in package.execution_plan] == [
        "Pilot owner",
        "Operations lead",
        "Executive sponsor",
    ]


def test_decision_object_action_owners_are_preserved():
    state = fake_state()
    state.pop("execution_plan")
    state["strategy"]["strategies"] = []
    state["decision_objects"] = {
        "actions": [
            {"phase": "30d", "title": "Confirm pilot owner.", "owner": "Pilot owner"},
            {"phase": "60d", "title": "Run controlled workflows.", "responsible_role": "Operations lead"},
            {"phase": "90d", "title": "Hold scale review.", "assignee": "Executive sponsor"},
        ]
    }

    package = build_delivery_package(state)

    assert [action.owner for action in package.execution_plan] == [
        "Pilot owner",
        "Operations lead",
        "Executive sponsor",
    ]


def test_unknown_kpi_type_does_not_invent_type():
    state = fake_state()
    state["kpis"] = [
        {
            "name": "Unclassified metric",
            "threshold_red": 1,
            "threshold_amber": 2,
        }
    ]

    package = build_delivery_package(state)

    assert package.kpis[0].indicator_type == "unknown"
    assert any("KPI indicator_type is unknown" in warning for warning in package.extraction_warnings)
