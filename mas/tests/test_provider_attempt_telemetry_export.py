"""Export envelope, chain validation, and artifact acceptance.

The database-backed halves of required regressions 18, 19 and 20 live in
``test_provider_attempt_telemetry_pg``; this file pins the parts that are pure
functions of the artifact — which is also where a hand-edited or truncated
artifact has to be caught before anything touches a database.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import repository  # noqa: E402
from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION  # noqa: E402
from tools import provider_attempt_telemetry_export as export_tool  # noqa: E402
from tools import provider_attempt_telemetry_restore as restore_tool  # noqa: E402

RUN_ID = "11111111-1111-4111-8111-111111111111"
CALL_ID = "22222222-2222-4222-8222-222222222222"
INVOCATION_ID = "33333333-3333-4333-8333-333333333333"
ATTEMPT_ID = "44444444-4444-4444-8444-444444444444"


def _row(table: str, **values):
    row = {column: None for column in repository.READ_COLUMNS[table]}
    row.update(values)
    return row


def _rows(*, with_terminal: bool = True, extra_events=()):
    events = []
    if with_terminal:
        events.append(
            _row(
                repository.EVENT_TABLE,
                event_sequence=1,
                event_id="55555555-5555-4555-8555-555555555555",
                subject_kind="http_attempt",
                subject_id=ATTEMPT_ID,
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
                event_kind="completed",
                event_ordinal=1,
                is_terminal=True,
            )
        )
        events.append(
            _row(
                repository.EVENT_TABLE,
                event_sequence=2,
                event_id="66666666-6666-4666-8666-666666666666",
                subject_kind="sdk_invocation",
                subject_id=INVOCATION_ID,
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
                event_kind="completed",
                event_ordinal=1,
                is_terminal=True,
            )
        )
    events.extend(extra_events)
    return {
        repository.RUN_TABLE: [
            _row(
                repository.RUN_TABLE,
                run_sequence=1,
                telemetry_run_id=RUN_ID,
                posture="strict",
                telemetry_required=True,
                entry_point="cli_workflow",
            )
        ],
        repository.RUN_EVENT_TABLE: [
            _row(
                repository.RUN_EVENT_TABLE,
                run_event_sequence=1,
                event_id="77777777-7777-4777-8777-777777777777",
                telemetry_run_id=RUN_ID,
                event_kind="reconciliation",
                reconciliation_status="complete",
                drain_status="drained",
                unmatched_starts=0,
                undurable_events=0,
                ambiguous_events=0,
                dropped_events=0,
                expected_calls=1,
                observed_calls=1,
                detail="",
            )
        ],
        repository.CALL_TABLE: [
            _row(
                repository.CALL_TABLE,
                call_sequence=1,
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
            )
        ],
        repository.INVOCATION_TABLE: [
            _row(
                repository.INVOCATION_TABLE,
                invocation_sequence=1,
                invocation_id=INVOCATION_ID,
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
            )
        ],
        repository.ATTEMPT_TABLE: [
            _row(
                repository.ATTEMPT_TABLE,
                attempt_sequence=1,
                attempt_id=ATTEMPT_ID,
                invocation_id=INVOCATION_ID,
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
                http_retry_ordinal=1,
            )
        ],
        repository.EVENT_TABLE: events,
        repository.LEDGER_TABLE: [],
    }


def _artifact(rows=None, **overrides):
    rows = rows if rows is not None else _rows()
    selector = {name: "" for name in export_tool.Selector.FIELDS}
    selector["telemetry_run_id"] = RUN_ID
    payload = {
        "export_format": export_tool.EXPORT_FORMAT,
        "export_version": export_tool.EXPORT_VERSION,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "selector": selector,
        "columns": {
            table: list(repository.READ_COLUMNS[table])
            for table in repository.TELEMETRY_TABLES
        },
        "column_schema_digest": export_tool.column_schema_digest(),
        "relations": {},
        "chains": export_tool.validate_chains(rows),
        "complete": True,
        "rows": rows,
    }
    payload.update(overrides)
    payload["selector_bound_digest"] = export_tool.digest(
        {
            "selector": payload["selector"],
            "export_version": payload["export_version"],
            "schema_version": payload["schema_version"],
            "column_schema_digest": payload["column_schema_digest"],
            "rows": payload["rows"],
        }
    )
    return payload


class ChainValidationTests(unittest.TestCase):
    def test_a_complete_chain_validates(self):
        result = export_tool.validate_chains(_rows())
        self.assertTrue(result["complete"])
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["counts"]["http_attempts"], 1)
        self.assertEqual(result["counts"]["terminal_events"], 2)

    def test_an_unmatched_attempt_start_is_reported(self):
        """Requirement 19, in its purest form."""
        result = export_tool.validate_chains(_rows(with_terminal=False))
        self.assertFalse(result["complete"])
        self.assertIn("unmatched_attempt_starts=1", result["problems"])
        self.assertIn("unmatched_invocation_starts=1", result["problems"])

    def test_a_duplicate_terminal_event_is_reported(self):
        rows = _rows()
        duplicate = copy.deepcopy(rows[repository.EVENT_TABLE][0])
        duplicate["event_sequence"] = 99
        duplicate["event_id"] = "88888888-8888-4888-8888-888888888888"
        duplicate["event_ordinal"] = 2
        rows[repository.EVENT_TABLE].append(duplicate)

        result = export_tool.validate_chains(rows)
        self.assertFalse(result["complete"])
        self.assertIn("duplicate_terminal_events=1", result["problems"])

    def test_an_orphan_attempt_is_reported(self):
        rows = _rows()
        rows[repository.INVOCATION_TABLE] = []
        result = export_tool.validate_chains(rows)
        self.assertFalse(result["complete"])
        self.assertIn("orphan_attempts=1", result["problems"])

    def test_the_retry_relationship_is_derived_not_stored(self):
        rows = _rows()
        second = copy.deepcopy(rows[repository.ATTEMPT_TABLE][0])
        second["attempt_sequence"] = 2
        second["attempt_id"] = "99999999-9999-4999-8999-999999999999"
        second["http_retry_ordinal"] = 2
        rows[repository.ATTEMPT_TABLE].append(second)
        rows[repository.EVENT_TABLE].append(
            _row(
                repository.EVENT_TABLE,
                event_sequence=3,
                event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                subject_kind="http_attempt",
                subject_id=second["attempt_id"],
                call_id=CALL_ID,
                telemetry_run_id=RUN_ID,
                event_kind="completed",
                event_ordinal=1,
                is_terminal=True,
            )
        )
        result = export_tool.validate_chains(rows)
        self.assertTrue(result["complete"])
        # Every attempt but the highest ordinal was superseded by a retry; the
        # highest is final for its invocation. Derived exactly, never guessed at
        # write time.
        self.assertEqual(
            result["http_retry_relationship"][ATTEMPT_ID], "superseded_by_retry"
        )
        self.assertEqual(
            result["http_retry_relationship"][second["attempt_id"]],
            "final_for_invocation",
        )


class ReconciliationSummaryTests(unittest.TestCase):
    def test_a_complete_reconciliation_is_reported_as_complete(self):
        summary = export_tool.reconciliation_summary(_rows())
        self.assertTrue(summary["present"])
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["runs"][0]["telemetry_run_id"], RUN_ID)

    def test_an_absent_reconciliation_is_never_reported_as_complete(self):
        rows = _rows()
        rows[repository.RUN_EVENT_TABLE] = []
        summary = export_tool.reconciliation_summary(rows)
        self.assertFalse(summary["present"])
        self.assertEqual(summary["status"], "absent")

    def test_an_uncertified_run_is_not_complete(self):
        rows = _rows()
        rows[repository.RUN_EVENT_TABLE][0]["reconciliation_status"] = "uncertified"
        self.assertEqual(export_tool.reconciliation_summary(rows)["status"], "not_complete")


class ArtifactValidationTests(unittest.TestCase):
    def test_a_well_formed_artifact_validates(self):
        self.assertEqual(restore_tool.validate_artifact(_artifact()), [])

    def test_an_unsupported_version_is_refused(self):
        problems = restore_tool.validate_artifact(_artifact(export_version=99))
        self.assertIn("unsupported_export_version:99", problems)

    def test_an_unsupported_format_is_refused(self):
        problems = restore_tool.validate_artifact(_artifact(export_format="something-else"))
        self.assertTrue(any(p.startswith("unsupported_format") for p in problems))

    def test_a_hand_edited_artifact_fails_the_digest(self):
        artifact = _artifact()
        artifact["rows"][repository.ATTEMPT_TABLE][0]["requested_model"] = "tampered"
        self.assertIn("selector_bound_digest_mismatch", restore_tool.validate_artifact(artifact))

    def test_a_relabelled_selector_fails_the_digest(self):
        artifact = _artifact()
        artifact["selector"]["telemetry_run_id"] = "00000000-0000-4000-8000-000000000000"
        self.assertIn("selector_bound_digest_mismatch", restore_tool.validate_artifact(artifact))

    def test_an_incomplete_artifact_is_refused(self):
        artifact = _artifact(complete=False)
        self.assertIn("artifact_incomplete", restore_tool.validate_artifact(artifact))

    def test_a_broken_chain_is_refused(self):
        """Requirement 19: a broken chain never restores."""
        artifact = _artifact(_rows(with_terminal=False))
        problems = restore_tool.validate_artifact(artifact)
        self.assertTrue(any(p.startswith("chain:") for p in problems))

    def test_a_missing_envelope_field_is_refused(self):
        artifact = _artifact()
        del artifact["chains"]
        self.assertIn("envelope_field_absent:chains", restore_tool.validate_artifact(artifact))

    def test_a_column_contract_mismatch_is_refused(self):
        artifact = _artifact()
        artifact["columns"][repository.ATTEMPT_TABLE] = ["attempt_id"]
        problems = restore_tool.validate_artifact(artifact)
        self.assertIn(f"column_contract_mismatch:{repository.ATTEMPT_TABLE}", problems)


class DigestStabilityTests(unittest.TestCase):
    def test_the_digest_is_stable_across_serialization(self):
        artifact = _artifact()
        first = artifact["selector_bound_digest"]
        round_tripped = json.loads(json.dumps(artifact))
        second = export_tool.digest(
            {
                "selector": round_tripped["selector"],
                "export_version": round_tripped["export_version"],
                "schema_version": round_tripped["schema_version"],
                "column_schema_digest": round_tripped["column_schema_digest"],
                "rows": round_tripped["rows"],
            }
        )
        self.assertEqual(first, second)

    def test_the_column_schema_digest_covers_every_relation(self):
        digest = export_tool.column_schema_digest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        # A changed column contract changes the digest, so an artifact written by
        # a different build cannot be silently restored by this one.
        self.assertNotEqual(
            digest,
            export_tool.digest(
                {
                    table: list(repository.READ_COLUMNS[table])[:-1]
                    for table in repository.TELEMETRY_TABLES
                }
            ),
        )


class SelectorTests(unittest.TestCase):
    def test_every_supported_selector_field_is_recorded(self):
        selector = export_tool.Selector(
            telemetry_run_id=RUN_ID,
            project_id="2a5a2e1c-0000-4000-8000-000000000001",
            external_project_id="eval-7",
            external_run_id="run-1",
            job_id="job-1",
            call_id=CALL_ID,
            worker_id="host:1:abc",
        )
        payload = selector.as_payload()
        self.assertEqual(set(payload), set(export_tool.Selector.FIELDS))
        self.assertTrue(selector)

    def test_an_empty_selector_is_falsey(self):
        self.assertFalse(export_tool.Selector())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
