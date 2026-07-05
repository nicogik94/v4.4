"""Repository unit tests for R1.6A Automation ROI snapshots."""
from datetime import datetime, timezone

import pytest

from research_evidence import automation_roi_use_repository as repo
from research_evidence.automation_roi_use_models import (
    AutomationRoiInputSnapshotCreate,
)
from research_evidence.automation_roi_use_policy import REQUIRED_ROLES


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROJECT = "00000000-0000-0000-0000-000000000001"
IDS = tuple(f"00000000-0000-0000-0000-{n:012d}" for n in range(10, 16))


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows if rows is not None else []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.calls = []
        self.snapshot_row = (
            IDS[0],
            PROJECT,
            "deterministic_calculation",
            "automation_roi.evidence_input.v1",
            "set-1",
            1,
            "request-1",
            "automation_roi.evidence_use",
            "1",
            {},
            "ca7aadce968c35f9839d79b61a4cbb62fe9bc05fcc692e6c773ee36ec4a13c9d",
            "automation_roi.evidence_use.evaluator.v1",
            NOW,
            "complete",
            "satisfies",
            ["policy_satisfied"],
            "operator",
            None,
            NOW,
        )

    def execute(self, statement, params=None):
        text = str(statement)
        self.calls.append((text, params))
        if "research_evidence_create_automation_roi_snapshot" in text:
            return Result(row=(IDS[0],))
        if "research_evidence_automation_roi_input_snapshot_binding" in text:
            rows = [
                (
                    f"00000000-0000-0000-0001-{index:012d}",
                    IDS[0],
                    PROJECT,
                    "deterministic_calculation",
                    "set-1",
                    role,
                    binding_id,
                    NOW,
                )
                for index, (role, binding_id) in enumerate(
                    zip(REQUIRED_ROLES, IDS), start=1
                )
            ]
            return Result(rows=rows)
        if (
            "research_evidence_automation_roi_input_snapshot" in text
            and "WHERE id =" in text
        ):
            return Result(row=self.snapshot_row)
        return Result()


def _command(**changes):
    values = {
        "project_id": PROJECT,
        "binding_set_id": "set-1",
        "binding_record_ids": IDS,
        "request_id": "request-1",
        "freshness_as_of": NOW,
        "evaluated_by": "operator",
    }
    values.update(changes)
    return AutomationRoiInputSnapshotCreate(**values)


def test_insert_uses_only_controlled_database_write_contract():
    conn = FakeConn()
    record = repo.insert_snapshot(conn, _command())
    assert record.policy_evaluation_status == "satisfies"
    writes = [
        (text, params)
        for text, params in conn.calls
        if "research_evidence_create_automation_roi_snapshot" in text
    ]
    assert len(writes) == 1
    assert writes[0][1][2] == list(IDS)
    assert not any("INSERT INTO" in text for text, _ in conn.calls)
    assert not any("policy_evaluation_status" in str(params) for _, params in writes)
    assert not any(
        "get_effective" in text or "latest" in text.lower()
        for text, _ in conn.calls
    )


def test_retry_compares_selected_ids_and_request_payload():
    conn = FakeConn()
    existing = repo._snapshot_from_row(conn, conn.snapshot_row)
    assert repo.ensure_retry_matches(existing, _command()) is existing
    with pytest.raises(repo.AutomationRoiSnapshotRequestConflict):
        repo.ensure_retry_matches(
            existing, _command(freshness_as_of=NOW.replace(year=2027))
        )
