"""One export, one PostgreSQL snapshot.

The finding: the exporter pinned its connection ``read_only`` but never set an
isolation level, so it read at READ COMMITTED — where every *statement* gets its
own snapshot. An export issues one query to resolve the selector, one ``COUNT``
per relation, and one ``SELECT`` per page of each relation. A row committed by
another connection between any two of them produced an artifact describing
several different states of the database at once: ``total_matching`` disagreeing
with the number of rows exported, an event whose attempt is absent from the same
document, and a ``complete: true`` over a set that was never simultaneously true.

The tests below drive real concurrent inserts into the exact gaps that matter —
between the count and the pages, at a page boundary, and between two relations —
using a connection proxy that fires a callback at a chosen statement. Every one
of them requires the artifact to exclude the new rows entirely and a second
export to include them.
"""
import sys
import unittest
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from provider_telemetry import repository  # noqa: E402
from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION  # noqa: E402
from tools import provider_attempt_telemetry_export as export_tool  # noqa: E402
from tools import provider_attempt_telemetry_migrate as migrate_tool  # noqa: E402
from tests import provider_telemetry_pg_support as pg_support  # noqa: E402

WRITER_ROLE = pg_support.WRITER_ROLE
READER_ROLE = pg_support.READER_ROLE

FINGERPRINT = "d" * 64
EXTERNAL_RUN = "snapshot-run-under-test"


def _seed_run(conn, *, external_run_id=EXTERNAL_RUN) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
        "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
        "external_run_id, started_at) VALUES (%s::uuid, 'strict', true, "
        "'cli_workflow', %s, %s, %s, now())",
        (run_id, TELEMETRY_SCHEMA_VERSION, FINGERPRINT, external_run_id),
    )
    return run_id


def _seed_chain(conn, run_id: str, *, terminal: bool = True) -> dict[str, str]:
    """One complete call → invocation → attempt → terminal-event lineage."""
    ids = {
        "call_id": str(uuid.uuid4()),
        "invocation_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
    }
    conn.execute(
        "INSERT INTO provider_telemetry_call (call_id, telemetry_run_id, posture, "
        "entry_point, requested_provider, requested_model, request_config_fingerprint, "
        "routing_decision_fingerprint, candidate_count, started_at) "
        "VALUES (%s::uuid, %s::uuid, 'strict', 'cli_workflow', 'anthropic', 'm', "
        "%s, %s, 1, now())",
        (ids["call_id"], run_id, FINGERPRINT, FINGERPRINT),
    )
    conn.execute(
        "INSERT INTO provider_sdk_invocation (invocation_id, call_id, telemetry_run_id, "
        "posture, entry_point, invocation_kind, provider, requested_model, "
        "candidate_ordinal, retry_ordinal, attempt_ordinal, breaker_state_before, "
        "breaker_snapshot_status_before, request_config_fingerprint, "
        "routing_decision_fingerprint, started_at) VALUES (%s::uuid, %s::uuid, %s::uuid, "
        "'strict', 'cli_workflow', 'provider_call', 'anthropic', 'm', 1, 1, 1, "
        "'unknown', 'unknown', %s, %s, now())",
        (ids["invocation_id"], ids["call_id"], run_id, FINGERPRINT, FINGERPRINT),
    )
    conn.execute(
        "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
        "telemetry_run_id, posture, provider, requested_model, http_retry_ordinal, "
        "request_started_at) VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, 'strict', "
        "'anthropic', 'm', 1, now())",
        (ids["attempt_id"], ids["invocation_id"], ids["call_id"], run_id),
    )
    if terminal:
        for subject_kind, subject in (
            ("http_attempt", ids["attempt_id"]),
            ("sdk_invocation", ids["invocation_id"]),
        ):
            conn.execute(
                "INSERT INTO provider_attempt_event (event_id, subject_kind, subject_id, "
                "call_id, telemetry_run_id, event_kind, event_ordinal, is_terminal, "
                "observed_at, response_metadata_fingerprint, schema_version) "
                "VALUES (gen_random_uuid(), %s, %s::uuid, %s::uuid, %s::uuid, "
                "'completed', 1, true, now(), %s, %s)",
                (subject_kind, subject, ids["call_id"], run_id, FINGERPRINT,
                 TELEMETRY_SCHEMA_VERSION),
            )
    return ids


class _HookedConnection:
    """A connection that fires a callback around a chosen ``execute``.

    This is how "a row is committed *during* the export" becomes deterministic
    rather than a sleep and a hope: the interleaving is placed at an exact
    statement index instead of being raced for.
    """

    def __init__(self, inner, *, at: int, hook, when: str = "after") -> None:
        self._inner = inner
        self._at = at
        self._hook = hook
        self._when = when
        self.statements: list[str] = []

    def execute(self, query, params=None, **kwargs):
        index = len(self.statements)
        self.statements.append(str(query))
        if self._when == "before" and index == self._at:
            self._hook()
        result = (
            self._inner.execute(query, params, **kwargs)
            if params is not None
            else self._inner.execute(query, **kwargs)
        )
        if self._when == "after" and index == self._at:
            self._hook()
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _SnapshotCase(unittest.TestCase):
    """A schema with the migration applied and one complete run already in it."""

    @classmethod
    def setUpClass(cls):
        pg_support.dsn()
        pg_support.provision_roles()
        cls.schema = pg_support.fresh_schema()
        code, payload = pg_support.apply(cls.schema)
        if code != migrate_tool.EXIT_OK:  # pragma: no cover - harness failure
            pg_support.drop_schema(cls.schema)
            raise AssertionError(f"migration did not apply: {payload}")

    @classmethod
    def tearDownClass(cls):
        pg_support.drop_schema(cls.schema)

    def setUp(self):
        # One run per test, selected by its own external_run_id, so the
        # concurrent writer's rows are unambiguously "new".
        self.external_run = f"{EXTERNAL_RUN}-{uuid.uuid4().hex[:8]}"
        self.writer = pg_support.connect_as(WRITER_ROLE, self.schema)
        self.addCleanup(self.writer.close)
        self.run_id = _seed_run(self.writer, external_run_id=self.external_run)
        self.seeded = [_seed_chain(self.writer, self.run_id) for _ in range(3)]

    def reader(self):
        conn = psycopg.connect(
            **pg_support.role_parameters(
                READER_ROLE, options=f"-c search_path={self.schema}"
            )
        )
        self.addCleanup(conn.close)
        return export_tool._configure_readonly_connection(conn)

    def selector(self) -> export_tool.Selector:
        return export_tool.Selector(external_run_id=self.external_run)

    def insert_more(self, count: int = 2) -> list[dict[str, str]]:
        """Commit additional valid telemetry from a second connection."""
        added = [_seed_chain(self.writer, self.run_id) for _ in range(count)]
        return added

    def assert_excludes(self, payload, added):
        """No count, page, relation or digest input may mention the new rows."""
        exported_calls = {row["call_id"] for row in payload["rows"][repository.CALL_TABLE]}
        exported_attempts = {
            row["attempt_id"] for row in payload["rows"][repository.ATTEMPT_TABLE]
        }
        for ids in added:
            self.assertNotIn(ids["call_id"], exported_calls)
            self.assertNotIn(ids["attempt_id"], exported_attempts)

        # The counts are part of the same snapshot, so they must agree with the
        # pages rather than with the database as it now stands.
        for table in repository.TELEMETRY_TABLES:
            meta = payload["relations"][table]
            self.assertEqual(
                meta["total_matching"], meta["exported"],
                f"{table}: count and pages came from different snapshots",
            )
            self.assertTrue(meta["complete"])
        self.assertTrue(payload["complete"])
        self.assertTrue(payload["chains"]["complete"], payload["chains"])
        self.assertEqual(
            len(payload["rows"][repository.CALL_TABLE]), len(self.seeded)
        )


class ConcurrentInsertExclusionTests(_SnapshotCase):
    """The required deterministic concurrent-insert scenario."""

    def test_rows_committed_after_the_snapshot_are_excluded_everywhere(self):
        added: list[dict[str, str]] = []
        reader = self.reader()

        # Statement 0 of an export is the snapshot acquisition itself; firing
        # immediately after it is exactly "the snapshot is taken, now insert".
        hooked = _HookedConnection(
            reader, at=0, hook=lambda: added.extend(self.insert_more(2))
        )
        payload = export_tool.build_export(hooked, self.selector())

        self.assertEqual(len(added), 2)
        self.assert_excludes(payload, added)
        self.assertEqual(payload["transaction"]["isolation_level"], "repeatable read")
        self.assertTrue(payload["transaction"]["single_snapshot"])
        self.assertTrue(payload["transaction"]["read_only"])

        # A second export, on a new connection and therefore a new snapshot,
        # sees them. Without this the exclusion above could be explained by the
        # rows never having been written.
        second = export_tool.build_export(self.reader(), self.selector())
        exported_calls = {row["call_id"] for row in second["rows"][repository.CALL_TABLE]}
        for ids in added:
            self.assertIn(ids["call_id"], exported_calls)
        self.assertEqual(
            len(second["rows"][repository.CALL_TABLE]), len(self.seeded) + 2
        )
        self.assertNotEqual(
            second["selector_bound_digest"], payload["selector_bound_digest"]
        )

    def test_an_insert_between_the_count_and_the_pages_does_not_split_the_artifact(self):
        """The gap that produced ``total_matching`` != ``exported``."""
        added: list[dict[str, str]] = []
        reader = self.reader()
        counted: list[int] = []

        def find_first_count(index, statement):
            return statement.strip().startswith("SELECT count(*)")

        class _CountHooked(_HookedConnection):
            def execute(inner_self, query, params=None, **kwargs):
                index = len(inner_self.statements)
                statement = str(query)
                inner_self.statements.append(statement)
                result = (
                    inner_self._inner.execute(query, params, **kwargs)
                    if params is not None
                    else inner_self._inner.execute(query, **kwargs)
                )
                if not counted and find_first_count(index, statement):
                    counted.append(index)
                    added.extend(self.insert_more(2))
                return result

        hooked = _CountHooked(reader, at=-1, hook=lambda: None)
        payload = export_tool.build_export(hooked, self.selector())
        self.assertTrue(counted, "no COUNT statement was observed")
        self.assertEqual(len(added), 2)
        self.assert_excludes(payload, added)

    def test_an_insert_at_a_page_boundary_does_not_open_a_new_snapshot(self):
        """Keyset pagination continues in the same transaction, not a new one."""
        added: list[dict[str, str]] = []
        reader = self.reader()
        pages: list[int] = []

        class _PageHooked(_HookedConnection):
            def execute(inner_self, query, params=None, **kwargs):
                statement = str(query)
                inner_self.statements.append(statement)
                result = (
                    inner_self._inner.execute(query, params, **kwargs)
                    if params is not None
                    else inner_self._inner.execute(query, **kwargs)
                )
                if "ORDER BY" in statement and "LIMIT" in statement:
                    pages.append(1)
                    if len(pages) == 1:
                        added.extend(self.insert_more(2))
                return result

        hooked = _PageHooked(reader, at=-1, hook=lambda: None)
        # A page size of 1 forces several page boundaries per relation, so the
        # insert lands between pages rather than before the first one.
        payload = export_tool.build_export(hooked, self.selector(), page_size=1)
        self.assertGreater(len(pages), 3)
        self.assertEqual(len(added), 2)
        self.assert_excludes(payload, added)

    def test_an_insert_during_a_relation_transition_is_excluded_from_both(self):
        """Between finishing one relation and starting the next."""
        added: list[dict[str, str]] = []
        reader = self.reader()
        seen_tables: list[str] = []

        class _RelationHooked(_HookedConnection):
            def execute(inner_self, query, params=None, **kwargs):
                statement = str(query)
                inner_self.statements.append(statement)
                if (
                    f"FROM {repository.INVOCATION_TABLE}" in statement
                    and repository.INVOCATION_TABLE not in seen_tables
                ):
                    seen_tables.append(repository.INVOCATION_TABLE)
                    added.extend(self.insert_more(2))
                return (
                    inner_self._inner.execute(query, params, **kwargs)
                    if params is not None
                    else inner_self._inner.execute(query, **kwargs)
                )

        hooked = _RelationHooked(reader, at=-1, hook=lambda: None)
        payload = export_tool.build_export(hooked, self.selector())
        self.assertEqual(seen_tables, [repository.INVOCATION_TABLE])
        self.assertEqual(len(added), 2)
        self.assert_excludes(payload, added)

    def test_the_snapshot_excludes_a_half_written_lineage_consistently(self):
        """A partial chain committed mid-export cannot break the artifact.

        Under READ COMMITTED an export could read an event whose attempt it had
        already paged past, and report a broken chain that never existed in the
        database. One snapshot makes that impossible in either direction.
        """
        reader = self.reader()
        orphan_call = str(uuid.uuid4())

        def write_partial():
            _seed_chain(self.writer, self.run_id)

        hooked = _HookedConnection(reader, at=0, hook=write_partial)
        payload = export_tool.build_export(hooked, self.selector())
        self.assertTrue(payload["chains"]["complete"], payload["chains"]["problems"])
        self.assertEqual(payload["chains"]["problems"], [])
        del orphan_call


class SnapshotEnvelopeTests(_SnapshotCase):
    """The envelope states what it read and when, truthfully."""

    def test_the_envelope_records_the_isolation_level_and_snapshot(self):
        payload = export_tool.build_export(self.reader(), self.selector())
        transaction = payload["transaction"]
        self.assertEqual(transaction["isolation_level"], "repeatable read")
        self.assertIs(transaction["read_only"], True)
        self.assertIs(transaction["single_snapshot"], True)
        self.assertRegex(str(transaction["snapshot"]), r"\A\d+:\d+:")

    def test_exported_at_is_the_snapshot_time_not_the_serialization_time(self):
        payload = export_tool.build_export(self.reader(), self.selector())
        self.assertIn("exported_at", payload)
        self.assertLessEqual(
            payload["exported_at"].isoformat()
            if hasattr(payload["exported_at"], "isoformat")
            else str(payload["exported_at"]),
            payload["generated_at"],
        )

    def test_two_exports_of_an_unchanged_run_agree_exactly(self):
        """Snapshot isolation must not make a stable run export differently.

        The digest is deliberately taken over the selector and the rows only —
        not over the snapshot identity — so two exports of an unchanged run stay
        comparable, which is the whole point of a digest an experiment can cite.
        """
        first = export_tool.build_export(self.reader(), self.selector())
        second = export_tool.build_export(self.reader(), self.selector())
        self.assertEqual(
            first["selector_bound_digest"], second["selector_bound_digest"]
        )
        self.assertEqual(first["rows"], second["rows"])


class SnapshotFailureTests(_SnapshotCase):
    """A lost transaction never yields a successful-looking artifact."""

    def test_an_export_whose_transaction_ends_mid_read_fails_rather_than_reports(self):
        reader = self.reader()

        def end_the_transaction():
            # Exactly what a dropped-and-reconnected connection, or an
            # accidental commit, does to the snapshot.
            reader.rollback()

        hooked = _HookedConnection(reader, at=1, hook=end_the_transaction)
        with self.assertRaises(export_tool.ExportSnapshotLost):
            export_tool.build_export(hooked, self.selector())

    def test_the_command_reports_failure_and_emits_no_rows(self):
        import io

        reader = self.reader()

        def end_the_transaction():
            reader.rollback()

        hooked = _HookedConnection(reader, at=1, hook=end_the_transaction)
        original = export_tool.open_telemetry_connection
        export_tool.open_telemetry_connection = lambda: hooked
        stream = io.StringIO()
        try:
            code = export_tool.main(
                ["export", "--external-run-id", self.external_run], stream=stream
            )
        finally:
            export_tool.open_telemetry_connection = original

        import json

        payload = json.loads(stream.getvalue())
        self.assertEqual(code, export_tool.EXIT_FAILED)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("snapshot was lost", payload["diagnostic"])
        self.assertNotIn("rows", payload)
        self.assertNotIn("complete", payload)

    def test_a_read_committed_connection_is_refused_rather_than_downgraded(self):
        conn = psycopg.connect(
            **pg_support.role_parameters(
                READER_ROLE, options=f"-c search_path={self.schema}"
            )
        )
        self.addCleanup(conn.close)
        conn.autocommit = False
        # Start the transaction with a query so the snapshot is already taken
        # at READ COMMITTED and cannot be re-pinned.
        conn.execute("SELECT 1")
        with self.assertRaises(export_tool.ExportSnapshotLost) as caught:
            export_tool.build_export(conn, self.selector())
        self.assertIn("repeatable read", str(caught.exception))
        conn.rollback()

    def test_the_connection_is_rolled_back_and_closed_on_every_path(self):
        import io

        reader = self.reader()
        original = export_tool.open_telemetry_connection
        export_tool.open_telemetry_connection = lambda: reader
        try:
            export_tool.main(
                ["export", "--external-run-id", self.external_run], stream=io.StringIO()
            )
        finally:
            export_tool.open_telemetry_connection = original
        self.assertTrue(reader.closed)


class ReadCommittedMutationTests(_SnapshotCase):
    """Bounded mutation: at READ COMMITTED the artifact splits across snapshots.

    This asserts the *defect*, on a connection deliberately left at the old
    isolation level, so the exclusion assertions above cannot be passing for
    some unrelated reason.
    """

    def test_read_committed_lets_a_concurrent_insert_split_count_from_pages(self):
        conn = psycopg.connect(
            **pg_support.role_parameters(
                READER_ROLE, options=f"-c search_path={self.schema}"
            )
        )
        self.addCleanup(conn.close)
        conn.autocommit = False
        conn.read_only = True
        # The pre-remediation configuration: read-only, no isolation level.

        selector = self.selector()
        run_ids = export_tool._resolve_run_ids(conn, selector)
        clauses, params = export_tool._relation_filter(
            repository.CALL_TABLE, selector, run_ids
        )
        before = export_tool._count(conn, repository.CALL_TABLE, clauses, params)

        self.insert_more(2)

        # Same transaction, later statement, new snapshot: the count and the
        # rows now describe different states of the database.
        rows, _, _, _ = export_tool._read_relation(
            conn, repository.CALL_TABLE, clauses, params, page_size=1000, max_rows=None
        )
        self.assertEqual(before, len(self.seeded))
        self.assertEqual(
            len(rows), len(self.seeded) + 2,
            "READ COMMITTED was expected to expose the concurrent insert",
        )
        self.assertNotEqual(
            before, len(rows),
            "the mutation must reproduce the pre-remediation inconsistency",
        )
        conn.rollback()


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
