"""Pure unit tests for the operator-workspace read model (no database).

Covers the exact-six readiness classifier that both the API workspace payload and
the dashboard's calculate-gating rely on. The PostgreSQL-backed behavior of the
``GET .../workspace`` endpoint lives in ``test_automation_roi_workspace_pg.py``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.automation_roi.calculator import ROLES  # noqa: E402
from knowledge.automation_roi import workspace  # noqa: E402


def test_complete_when_exactly_the_six_roles_once_each():
    result = workspace.classify_role_completeness(ROLES)
    assert result == {"complete": True, "missing": [], "duplicate": [], "extra": []}


def test_missing_role_is_reported_and_not_complete():
    roles = [r for r in ROLES if r != "periods_per_year"]
    result = workspace.classify_role_completeness(roles)
    assert result["complete"] is False
    assert result["missing"] == ["periods_per_year"]
    assert result["duplicate"] == []
    assert result["extra"] == []


def test_duplicate_role_is_reported_and_not_complete():
    roles = list(ROLES) + ["periods_per_year"]
    result = workspace.classify_role_completeness(roles)
    assert result["complete"] is False
    assert result["duplicate"] == ["periods_per_year"]
    assert result["missing"] == []
    assert result["extra"] == []


def test_extra_role_is_reported_and_not_complete():
    roles = list(ROLES) + ["made_up_role"]
    result = workspace.classify_role_completeness(roles)
    assert result["complete"] is False
    assert result["extra"] == ["made_up_role"]
    assert result["missing"] == []
    assert result["duplicate"] == []


def test_empty_map_reports_all_six_missing():
    result = workspace.classify_role_completeness([])
    assert result["complete"] is False
    assert sorted(result["missing"]) == sorted(ROLES)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
