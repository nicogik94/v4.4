"""Model tests for immutable R1.6A Automation ROI snapshots."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from research_evidence.automation_roi_use_models import (
    AutomationRoiInputSnapshotCreate,
)


IDS = tuple(f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 7))


def _command(**changes):
    values = {
        "project_id": IDS[0],
        "binding_set_id": "set-1",
        "binding_record_ids": IDS,
        "request_id": "request-1",
        "freshness_as_of": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "evaluated_by": "operator",
    }
    values.update(changes)
    return AutomationRoiInputSnapshotCreate(**values)


def test_exactly_six_distinct_explicit_ids_are_required():
    assert _command().binding_record_ids == IDS
    with pytest.raises(ValidationError, match="exactly six"):
        _command(binding_record_ids=IDS[:5])
    with pytest.raises(ValidationError, match="distinct"):
        _command(binding_record_ids=(*IDS[:5], IDS[0]))


def test_identity_text_and_timezone_are_strict():
    with pytest.raises(ValidationError, match="blank"):
        _command(request_id=" ")
    with pytest.raises(ValidationError, match="timezone"):
        _command(freshness_as_of=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        AutomationRoiInputSnapshotCreate(
            **_command().model_dump(),
            policy_evaluation_status="satisfies",
        )
