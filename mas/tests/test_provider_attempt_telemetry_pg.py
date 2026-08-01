"""Genuine-PostgreSQL regressions: security, drift, migration, export, restore.

Runs against a fresh disposable PostgreSQL 16 database and covers the audit's
required regressions that only a real cluster can establish:

* 15. the runtime role cannot disable triggers, truncate, update or delete;
* 16. reapply rejects disabled triggers and altered constraints/indexes/functions;
* 17. migration/bootstrap cannot return success after a rollback;
* 18. export cannot silently truncate 10,001 rows;
* 19. broken attempt chains fail a strict export;
* 20. restored identity sequences advance correctly;
* 22. strict preflight fails when the sink, ACL or schema is unhealthy.

Skips unless ``TEST_EVIDENCE_PG_DSN`` is set. The three telemetry roles are
provisioned here because the migration deliberately creates none.
"""
import io
import json
import os
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
from tools import provider_attempt_telemetry_restore as restore_tool  # noqa: E402

# The disposable-schema harness lives in its own module so that every telemetry
# PostgreSQL suite shares one set of generated role passwords; see that module
# for why importing it from a sibling test module is not equivalent.
from tests import provider_telemetry_pg_support as pg_support  # noqa: E402

DSN_ENV = pg_support.DSN_ENV

OWNER_ROLE = pg_support.OWNER_ROLE
WRITER_ROLE = pg_support.WRITER_ROLE
READER_ROLE = pg_support.READER_ROLE
MIGRATION_ROLE = pg_support.MIGRATION_ROLE

MIGRATION_SQL = ROOT / "sql" / migrate_tool.MIGRATION_NAME

_dsn = pg_support.dsn
_superuser = pg_support.superuser
_role_parameters = pg_support.role_parameters
_connect_as = pg_support.connect_as
_provision_roles = pg_support.provision_roles
_fresh_schema = pg_support.fresh_schema
_drop_schema = pg_support.drop_schema
_apply = pg_support.apply

# The DSN regression suite reaches the harness through this module's names, and
# shares its generated role passwords: this is the same dict, not a copy.
_CREDENTIALS = pg_support.CREDENTIALS


class _SchemaCase(unittest.TestCase):
    """A fresh schema with the migration applied, per test class."""

    schema: str

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()
        cls.schema = _fresh_schema()
        code, payload = _apply(cls.schema)
        if code != migrate_tool.EXIT_OK:
            _drop_schema(cls.schema)
            raise AssertionError(f"migration did not apply: {payload}")

    @classmethod
    def tearDownClass(cls):
        _drop_schema(cls.schema)


class MigrationAndBootstrapTests(_SchemaCase):
    def test_apply_records_a_durable_ledger_entry(self):
        code, payload = _apply(self.schema, argv=("verify",))
        self.assertEqual(code, migrate_tool.EXIT_OK, payload)
        self.assertEqual(payload["contract_problems"], [])
        self.assertTrue(payload["ledger"])
        entry = payload["ledger"][-1]
        self.assertEqual(entry["migration_name"], migrate_tool.MIGRATION_NAME)
        self.assertEqual(entry["migration_sha256"], migrate_tool.migration_sha256())
        self.assertEqual(entry["schema_version"], TELEMETRY_SCHEMA_VERSION)

    def test_reapply_is_a_no_op_and_is_recorded_as_such(self):
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_OK, payload)
        self.assertEqual(payload["outcome"], "reapplied_noop")

    def test_apply_refuses_a_source_whose_digest_does_not_match(self):
        code, payload = _apply(
            self.schema, argv=("apply", "--expect-sha256", "0" * 64)
        )
        self.assertEqual(code, migrate_tool.EXIT_FAILED)
        self.assertEqual(payload["reason"], "source_digest_mismatch")

    def test_bootstrap_documents_the_ordering_and_names_the_migration(self):
        stream = io.StringIO()
        code = migrate_tool.main(["bootstrap"], stream=stream)
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, migrate_tool.EXIT_OK)
        self.assertIn(migrate_tool.MIGRATION_NAME, payload["order"])
        self.assertEqual(payload["order"][-1], migrate_tool.MIGRATION_NAME)
        self.assertTrue(any("v62" in note for note in payload["notes"]))
        self.assertTrue(
            any("provider_attempt_telemetry_migrate apply" in n for n in payload["notes"])
        )

    def test_a_one_column_impostor_table_never_satisfies_preflight(self):
        """The audit's exact phrasing, as an assertion."""
        schema = _fresh_schema()
        try:
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL("CREATE TABLE {}.provider_attempt (attempt_id uuid)").format(
                        psycopg.sql.Identifier(schema)
                    )
                )
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn("v63 preflight", payload["error"])
        finally:
            _drop_schema(schema)


class RollbackTests(unittest.TestCase):
    """Requirement 17: success cannot be reported after a rollback."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def test_a_failed_apply_leaves_no_ledger_row_and_no_tables(self):
        schema = _fresh_schema()
        try:
            # A divergent object makes the migration's own preflight fail; the
            # whole script is one transaction, so nothing at all may survive.
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL(
                        "CREATE TABLE {}.provider_telemetry_run (telemetry_run_id uuid)"
                    ).format(psycopg.sql.Identifier(schema))
                )
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL("SET search_path TO {}").format(
                        psycopg.sql.Identifier(schema)
                    )
                )
                # No ledger table at all: the transaction rolled back entirely.
                present = conn.execute(
                    "SELECT to_regclass(%s)",
                    (f"{schema}.provider_telemetry_migration_ledger",),
                ).fetchone()[0]
                self.assertIsNone(present)
        finally:
            _drop_schema(schema)

    def test_a_tampered_schema_cannot_be_reapplied_into_success(self):
        schema = _fresh_schema()
        try:
            code, _ = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_OK)
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL(
                        "ALTER TABLE {}.provider_attempt DISABLE TRIGGER "
                        "trg_provider_attempt_no_mutation"
                    ).format(psycopg.sql.Identifier(schema))
                )
            before = _apply(schema, argv=("verify",))[1]["ledger"]
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL("SET search_path TO {}").format(
                        psycopg.sql.Identifier(schema)
                    )
                )
                after = conn.execute(
                    "SELECT count(*) FROM provider_telemetry_migration_ledger"
                ).fetchone()[0]
            self.assertEqual(after, len(before))
        finally:
            _drop_schema(schema)


class AppendOnlySecurityTests(_SchemaCase):
    """Requirement 15: the runtime role cannot erase or rewrite its telemetry."""

    def _insert_run(self, conn) -> str:
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (run_id, TELEMETRY_SCHEMA_VERSION, "a" * 64),
        )
        return run_id

    def test_the_writer_can_append(self):
        with _connect_as(WRITER_ROLE, self.schema) as conn:
            run_id = self._insert_run(conn)
            count = conn.execute(
                "SELECT count(*) FROM provider_telemetry_run WHERE telemetry_run_id = %s::uuid",
                (run_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_the_writer_cannot_mutate_or_erase(self):
        with _connect_as(WRITER_ROLE, self.schema) as conn:
            self._insert_run(conn)
            for statement in (
                "UPDATE provider_telemetry_run SET job_id = 'x'",
                "DELETE FROM provider_telemetry_run",
                "TRUNCATE provider_telemetry_run",
                "ALTER TABLE provider_telemetry_run DISABLE TRIGGER "
                "trg_provider_telemetry_run_no_mutation",
                "DROP TRIGGER trg_provider_telemetry_run_no_mutation ON provider_telemetry_run",
                "ALTER TABLE provider_telemetry_run DROP CONSTRAINT ck_ptr_posture",
                "ALTER TABLE provider_attempt ALTER COLUMN attempt_sequence DROP IDENTITY",
                "CREATE OR REPLACE FUNCTION provider_telemetry_reject_mutation() "
                "RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$",
            ):
                with self.subTest(statement=statement[:48]):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        conn.execute(statement)

    def test_the_writer_does_not_own_a_single_relation(self):
        with _connect_as(WRITER_ROLE, self.schema) as conn:
            owned = conn.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() "
                "AND pg_catalog.pg_get_userbyid(c.relowner) = current_user"
            ).fetchone()[0]
        self.assertEqual(owned, 0)

    def test_the_append_only_trigger_blocks_even_the_owner(self):
        # Ownership lets the owner *disable* the guard, but not bypass it while
        # it is enabled. Superuser bypass remains a documented operational risk.
        with _superuser() as conn:
            conn.execute(
                psycopg.sql.SQL("SET search_path TO {}").format(
                    psycopg.sql.Identifier(self.schema)
                )
            )
            conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(OWNER_ROLE)))
            run_id = self._insert_run(conn)
            with self.assertRaises(psycopg.errors.RestrictViolation):
                conn.execute(
                    "UPDATE provider_telemetry_run SET job_id = 'x' "
                    "WHERE telemetry_run_id = %s::uuid",
                    (run_id,),
                )
            with self.assertRaises(psycopg.errors.RestrictViolation):
                conn.execute("TRUNCATE provider_telemetry_run")
            conn.execute("RESET ROLE")

    def test_the_reader_can_select_and_nothing_else(self):
        with _connect_as(READER_ROLE, self.schema) as conn:
            conn.execute("SELECT count(*) FROM provider_attempt")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
                    "telemetry_required, entry_point, schema_version, "
                    "runtime_fingerprint, started_at) "
                    "VALUES (gen_random_uuid(), 'strict', true, 'cli_workflow', 2, %s, now())",
                    ("a" * 64,),
                )

    def test_public_holds_nothing(self):
        with _superuser() as conn:
            conn.execute(
                psycopg.sql.SQL("SET search_path TO {}").format(
                    psycopg.sql.Identifier(self.schema)
                )
            )
            for table in repository.TELEMETRY_TABLES:
                with self.subTest(table=table):
                    granted = conn.execute(
                        "SELECT has_table_privilege('public', %s, 'SELECT') "
                        "OR has_table_privilege('public', %s, 'INSERT')",
                        (table, table),
                    ).fetchone()[0]
                    self.assertFalse(granted)


class ConstraintTests(_SchemaCase):
    """The database enforces the value contract independently of Python."""

    def _run(self, conn) -> str:
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (run_id, TELEMETRY_SCHEMA_VERSION, "b" * 64),
        )
        return run_id

    def test_a_value_without_a_valid_status_is_refused(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = self._run(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_attempt_event (event_id, subject_kind, "
                    "subject_id, call_id, telemetry_run_id, event_kind, event_ordinal, "
                    "is_terminal, observed_at, input_tokens, input_tokens_status, "
                    "response_metadata_fingerprint, schema_version) "
                    "VALUES (gen_random_uuid(), 'sdk_invocation', gen_random_uuid(), "
                    "gen_random_uuid(), %s::uuid, 'completed', 1, true, now(), 5, "
                    "'absent', %s, 2)",
                    (run_id, "c" * 64),
                )
            conn.rollback()

    def test_a_zero_ordinal_is_refused(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = self._run(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
                    "telemetry_run_id, posture, provider, requested_model, "
                    "http_retry_ordinal, request_started_at) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                    "%s::uuid, 'strict', 'anthropic', 'm', 0, now())",
                    (run_id,),
                )
            conn.rollback()

    def test_a_fabricated_breaker_snapshot_is_refused(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = self._run(conn)
            # "unknown status" must not carry a state or a count.
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_attempt_event (event_id, subject_kind, "
                    "subject_id, call_id, telemetry_run_id, event_kind, event_ordinal, "
                    "is_terminal, observed_at, breaker_state_after, "
                    "breaker_failure_count_after, breaker_snapshot_status_after, "
                    "response_metadata_fingerprint, schema_version) "
                    "VALUES (gen_random_uuid(), 'sdk_invocation', gen_random_uuid(), "
                    "gen_random_uuid(), %s::uuid, 'completed', 1, true, now(), "
                    "'closed', 0, 'unknown', %s, 2)",
                    (run_id, "c" * 64),
                )
            conn.rollback()

    def test_a_secret_shaped_response_id_is_refused_by_the_database(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = self._run(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_attempt_event (event_id, subject_kind, "
                    "subject_id, call_id, telemetry_run_id, event_kind, event_ordinal, "
                    "is_terminal, observed_at, provider_response_id, "
                    "provider_response_id_status, response_metadata_fingerprint, "
                    "schema_version) VALUES (gen_random_uuid(), 'sdk_invocation', "
                    "gen_random_uuid(), gen_random_uuid(), %s::uuid, 'completed', 1, "
                    "true, now(), %s, 'valid', %s, 2)",
                    (run_id, "Bearer sk-ant-SECRET", "c" * 64),
                )
            conn.rollback()

    def test_a_terminal_flag_that_disagrees_with_the_kind_is_refused(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = self._run(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_attempt_event (event_id, subject_kind, "
                    "subject_id, call_id, telemetry_run_id, event_kind, event_ordinal, "
                    "is_terminal, observed_at, response_metadata_fingerprint, "
                    "schema_version) VALUES (gen_random_uuid(), 'sdk_invocation', "
                    "gen_random_uuid(), gen_random_uuid(), %s::uuid, 'observation', 1, "
                    "true, now(), %s, 2)",
                    (run_id, "c" * 64),
                )
            conn.rollback()

    def test_duplicate_array_members_are_refused(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
                    "telemetry_required, entry_point, schema_version, "
                    "runtime_fingerprint, expected_phases, started_at) "
                    "VALUES (gen_random_uuid(), 'strict', true, 'cli_workflow', 2, %s, "
                    "ARRAY['audit','audit'], now())",
                    ("b" * 64,),
                )
            conn.rollback()


class DriftDetectionTests(unittest.TestCase):
    """Requirement 16: reapply rejects every category of catalog drift."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def _tampered(self, statement: str, *, expect: str) -> None:
        schema = _fresh_schema()
        try:
            self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL("SET search_path TO {}").format(
                        psycopg.sql.Identifier(schema)
                    )
                )
                conn.execute(statement)
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn(expect, payload["error"])
        finally:
            _drop_schema(schema)

    def test_a_disabled_trigger_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt DISABLE TRIGGER trg_provider_attempt_no_mutation",
            expect="append-only triggers absent, disabled, or redefined",
        )

    def test_a_dropped_trigger_is_rejected(self):
        # A dropped object trips the all-or-nothing preflight before the
        # postflight is ever reached; either way the reapply fails closed rather
        # than quietly re-creating the guard and reporting success.
        self._tampered(
            "DROP TRIGGER trg_provider_attempt_no_truncate ON provider_attempt",
            expect="partial or divergent telemetry foundation",
        )

    def test_a_weakened_constraint_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt DROP CONSTRAINT ck_pa_ordinal, "
            "ADD CONSTRAINT ck_pa_ordinal CHECK (http_retry_ordinal >= 0)",
            expect="constraint contract violated",
        )

    def test_a_dropped_constraint_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt_event DROP CONSTRAINT ck_pae_safe_grammars",
            expect="constraint contract violated",
        )

    def test_an_index_that_lost_its_predicate_is_rejected(self):
        self._tampered(
            "DROP INDEX idx_provider_attempt_event_terminal; "
            "CREATE INDEX idx_provider_attempt_event_terminal ON provider_attempt_event "
            "(telemetry_run_id, subject_kind, subject_id)",
            expect="index contract violated",
        )

    def test_a_reordered_index_is_rejected(self):
        self._tampered(
            "DROP INDEX idx_provider_attempt_invocation; "
            "CREATE INDEX idx_provider_attempt_invocation ON provider_attempt "
            "(http_retry_ordinal, invocation_id)",
            expect="index contract violated",
        )

    def test_an_altered_column_type_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt_event ALTER COLUMN input_tokens TYPE numeric",
            expect="column contract violated",
        )

    def test_a_relaxed_nullability_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt ALTER COLUMN provider DROP NOT NULL",
            expect="column contract violated",
        )

    def test_an_added_column_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt ADD COLUMN smuggled text",
            expect="column contract violated",
        )

    def test_a_function_whose_search_path_was_reset_is_rejected(self):
        self._tampered(
            "ALTER FUNCTION provider_telemetry_reject_mutation() RESET search_path",
            expect="telemetry function definition drift",
        )

    def test_a_function_downgraded_to_security_invoker_is_rejected(self):
        self._tampered(
            "ALTER FUNCTION provider_telemetry_reject_mutation() SECURITY INVOKER",
            expect="telemetry function definition drift",
        )

    def test_changed_ownership_is_rejected(self):
        self._tampered(
            f'ALTER TABLE provider_attempt OWNER TO "{WRITER_ROLE}"',
            expect="ownership drift",
        )

    def test_a_widened_acl_is_rejected(self):
        self._tampered(
            f'GRANT DELETE ON provider_attempt TO "{WRITER_ROLE}"',
            expect="ACL contract violated",
        )

    def test_an_unlogged_table_is_rejected(self):
        self._tampered(
            "ALTER TABLE provider_attempt SET UNLOGGED",
            expect="must be permanent",
        )


def _seed_chain(conn, *, run_id, call_id, invocation_id, attempt_id, terminal=True):
    fingerprint = "d" * 64
    conn.execute(
        "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
        "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
        "external_run_id, started_at) VALUES (%s::uuid, 'strict', true, "
        "'cli_workflow', %s, %s, 'run-under-test', now())",
        (run_id, TELEMETRY_SCHEMA_VERSION, fingerprint),
    )
    conn.execute(
        # A `complete` reconciliation must bind the expected-work manifest
        # digest; the database refuses an unqualified one.
        "INSERT INTO provider_telemetry_run_event (event_id, telemetry_run_id, "
        "event_kind, posture, observed_at, reconciliation_status, drain_status, "
        "expected_work_digest) "
        "VALUES (gen_random_uuid(), %s::uuid, 'reconciliation', 'strict', now(), "
        "'complete', 'drained', %s)",
        (run_id, fingerprint),
    )
    conn.execute(
        "INSERT INTO provider_telemetry_call (call_id, telemetry_run_id, posture, "
        "entry_point, requested_provider, requested_model, request_config_fingerprint, "
        "routing_decision_fingerprint, candidate_count, started_at) "
        "VALUES (%s::uuid, %s::uuid, 'strict', 'cli_workflow', 'anthropic', 'm', %s, %s, 1, now())",
        (call_id, run_id, fingerprint, fingerprint),
    )
    conn.execute(
        "INSERT INTO provider_sdk_invocation (invocation_id, call_id, telemetry_run_id, "
        "posture, entry_point, invocation_kind, provider, requested_model, "
        "candidate_ordinal, retry_ordinal, attempt_ordinal, breaker_state_before, "
        "breaker_snapshot_status_before, request_config_fingerprint, "
        "routing_decision_fingerprint, started_at) VALUES (%s::uuid, %s::uuid, %s::uuid, "
        "'strict', 'cli_workflow', 'provider_call', 'anthropic', 'm', 1, 1, 1, "
        "'unknown', 'unknown', %s, %s, now())",
        (invocation_id, call_id, run_id, fingerprint, fingerprint),
    )
    conn.execute(
        "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
        "telemetry_run_id, posture, provider, requested_model, http_retry_ordinal, "
        "request_started_at) VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, 'strict', "
        "'anthropic', 'm', 1, now())",
        (attempt_id, invocation_id, call_id, run_id),
    )
    if terminal:
        for subject_kind, subject_id in (
            ("http_attempt", attempt_id),
            ("sdk_invocation", invocation_id),
        ):
            conn.execute(
                "INSERT INTO provider_attempt_event (event_id, subject_kind, subject_id, "
                "call_id, telemetry_run_id, event_kind, event_ordinal, is_terminal, "
                "observed_at, response_metadata_fingerprint, schema_version) "
                "VALUES (gen_random_uuid(), %s, %s::uuid, %s::uuid, %s::uuid, "
                "'completed', 1, true, now(), %s, %s)",
                (subject_kind, subject_id, call_id, run_id, fingerprint,
                 TELEMETRY_SCHEMA_VERSION),
            )


def _export(schema: str, argv) -> tuple[int, dict]:
    stream = io.StringIO()

    def opener():
        return psycopg.connect(
            **_role_parameters(READER_ROLE, options=f"-c search_path={schema}")
        )

    original = export_tool.open_telemetry_connection
    export_tool.open_telemetry_connection = opener
    try:
        code = export_tool.main(list(argv), stream=stream)
    finally:
        export_tool.open_telemetry_connection = original
    return code, json.loads(stream.getvalue())


class ExportCompletenessTests(_SchemaCase):
    """Requirements 18 and 19."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.run_id = str(uuid.uuid4())
        cls.call_id = str(uuid.uuid4())
        cls.invocation_id = str(uuid.uuid4())
        cls.attempt_id = str(uuid.uuid4())
        with _connect_as(WRITER_ROLE, cls.schema) as conn:
            _seed_chain(
                conn,
                run_id=cls.run_id,
                call_id=cls.call_id,
                invocation_id=cls.invocation_id,
                attempt_id=cls.attempt_id,
            )
            # 10,000 further HTTP attempts under the same invocation: 10,001 in
            # total, which is exactly one more than the old silent LIMIT.
            conn.execute(
                "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
                "telemetry_run_id, posture, provider, requested_model, "
                "http_retry_ordinal, request_started_at) "
                "SELECT gen_random_uuid(), %s::uuid, %s::uuid, %s::uuid, 'strict', "
                "'anthropic', 'm', g, now() FROM generate_series(2, 10001) AS g",
                (cls.invocation_id, cls.call_id, cls.run_id),
            )
            conn.execute(
                "INSERT INTO provider_attempt_event (event_id, subject_kind, subject_id, "
                "call_id, telemetry_run_id, event_kind, event_ordinal, is_terminal, "
                "observed_at, response_metadata_fingerprint, schema_version) "
                "SELECT gen_random_uuid(), 'http_attempt', a.attempt_id, %s::uuid, "
                "%s::uuid, 'completed', 1, true, now(), %s, %s "
                "FROM provider_attempt a WHERE a.http_retry_ordinal > 1",
                (cls.call_id, cls.run_id, "d" * 64, TELEMETRY_SCHEMA_VERSION),
            )

    def test_the_default_export_reads_all_10001_rows(self):
        """Requirement 18."""
        code, payload = _export(
            self.schema, ("export", "--telemetry-run-id", self.run_id)
        )
        self.assertEqual(code, export_tool.EXIT_OK, payload.get("diagnostic"))
        attempts = payload["relations"][repository.ATTEMPT_TABLE]
        self.assertEqual(attempts["total_matching"], 10001)
        self.assertEqual(attempts["exported"], 10001)
        self.assertFalse(attempts["has_more"])
        self.assertTrue(attempts["complete"])
        self.assertTrue(payload["complete"])
        self.assertEqual(len(payload["rows"][repository.ATTEMPT_TABLE]), 10001)

    def test_a_capped_export_reports_incompleteness_rather_than_truncating_silently(self):
        code, payload = _export(
            self.schema,
            ("export", "--telemetry-run-id", self.run_id, "--max-rows", "10000"),
        )
        self.assertEqual(code, export_tool.EXIT_INCOMPLETE)
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["status"], "incomplete")
        attempts = payload["relations"][repository.ATTEMPT_TABLE]
        self.assertTrue(attempts["has_more"])
        self.assertEqual(attempts["total_matching"], 10001)
        self.assertEqual(attempts["exported"], 10000)
        self.assertIsNotNone(attempts["last_key"])

    def test_fail_on_overflow_is_a_loud_failure(self):
        code, payload = _export(
            self.schema,
            (
                "export", "--telemetry-run-id", self.run_id,
                "--max-rows", "10", "--on-overflow", "fail",
            ),
        )
        self.assertEqual(code, export_tool.EXIT_FAILED)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("NOT complete", payload["diagnostic"])

    def test_the_envelope_carries_every_required_field(self):
        _code, payload = _export(self.schema, ("export", "--telemetry-run-id", self.run_id))
        for field in (
            "selector", "export_version", "schema_version", "complete",
            "selector_bound_digest", "column_schema_digest", "chains",
            "reconciliation", "completeness_notice", "relations", "columns",
        ):
            with self.subTest(field=field):
                self.assertIn(field, payload)
        for name, meta in payload["relations"].items():
            with self.subTest(relation=name):
                for key in ("total_matching", "exported", "has_more", "complete",
                            "first_key", "last_key", "keyset_column"):
                    self.assertIn(key, meta)

    def test_exact_selectors_are_supported(self):
        for argv in (
            ("--telemetry-run-id", self.run_id),
            ("--external-run-id", "run-under-test"),
            ("--call-id", self.call_id),
        ):
            with self.subTest(selector=argv[0]):
                code, payload = _export(self.schema, ("list",) + argv)
                self.assertEqual(code, export_tool.EXIT_OK)
                self.assertGreaterEqual(payload["row_counts"][repository.ATTEMPT_TABLE], 1)

    def test_a_broken_chain_fails_a_strict_export(self):
        """Requirement 19."""
        schema = _fresh_schema()
        try:
            self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
            run_id = str(uuid.uuid4())
            with _connect_as(WRITER_ROLE, schema) as conn:
                _seed_chain(
                    conn,
                    run_id=run_id,
                    call_id=str(uuid.uuid4()),
                    invocation_id=str(uuid.uuid4()),
                    attempt_id=str(uuid.uuid4()),
                    terminal=False,
                )
            code, payload = _export(
                schema, ("export", "--telemetry-run-id", run_id, "--strict")
            )
            self.assertEqual(code, export_tool.EXIT_INCOMPLETE)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("unmatched_attempt_starts=1", payload["chains"]["problems"])
        finally:
            _drop_schema(schema)


class RestorationTests(unittest.TestCase):
    """Requirement 20 and the supported restoration path."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def _restore(self, schema: str, artifact_path: Path, argv=("restore",)):
        stream = io.StringIO()

        def opener():
            return _connect_as(MIGRATION_ROLE, schema, autocommit=False)

        original = restore_tool.open_restore_connection
        restore_tool.open_restore_connection = opener
        try:
            code = restore_tool.main(
                list(argv) + ["--artifact", str(artifact_path)], stream=stream
            )
        finally:
            restore_tool.open_restore_connection = original
        return code, json.loads(stream.getvalue())

    def test_a_run_restores_into_an_empty_schema_and_stays_appendable(self):
        source = _fresh_schema()
        target = _fresh_schema()
        artifact_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"tel-{uuid.uuid4().hex}.json"
        try:
            self.assertEqual(_apply(source)[0], migrate_tool.EXIT_OK)
            self.assertEqual(_apply(target)[0], migrate_tool.EXIT_OK)

            run_id = str(uuid.uuid4())
            with _connect_as(WRITER_ROLE, source) as conn:
                _seed_chain(
                    conn,
                    run_id=run_id,
                    call_id=str(uuid.uuid4()),
                    invocation_id=str(uuid.uuid4()),
                    attempt_id=str(uuid.uuid4()),
                )

            code, artifact = _export(source, ("export", "--telemetry-run-id", run_id))
            self.assertEqual(code, export_tool.EXIT_OK)
            self.assertTrue(artifact["complete"])
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

            code, payload = self._restore(target, artifact_path, argv=("verify",))
            self.assertEqual(code, restore_tool.EXIT_OK, payload)

            code, payload = self._restore(target, artifact_path)
            self.assertEqual(code, restore_tool.EXIT_OK, payload)
            self.assertEqual(payload["verification_problems"], [])
            self.assertEqual(payload["append_probe"], "append_verified_and_rolled_back")

            # The restored rows re-export to the same digest. The comparison is
            # over the *restorable* subset: the migration ledger is provenance for
            # the schema, not run data, so the target keeps its own.
            code, restored = _export(target, ("export", "--telemetry-run-id", run_id))
            self.assertEqual(code, export_tool.EXIT_OK)
            self.assertEqual(
                restore_tool.restorable_digest(restored["rows"]),
                restore_tool.restorable_digest(artifact["rows"]),
            )

            # Requirement 20: the identity sequences advanced past every restored
            # key, so a new append cannot collide with a restored row.
            with _connect_as(WRITER_ROLE, target) as conn:
                highest = conn.execute(
                    "SELECT max(attempt_sequence) FROM provider_attempt"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
                    "telemetry_run_id, posture, provider, requested_model, "
                    "http_retry_ordinal, request_started_at) VALUES (gen_random_uuid(), "
                    "gen_random_uuid(), gen_random_uuid(), %s::uuid, 'strict', "
                    "'anthropic', 'm', 1, now())",
                    (run_id,),
                )
                new_key = conn.execute(
                    "SELECT max(attempt_sequence) FROM provider_attempt"
                ).fetchone()[0]
            self.assertGreater(new_key, highest)
        finally:
            artifact_path.unlink(missing_ok=True)
            _drop_schema(source)
            _drop_schema(target)

    def test_a_tampered_artifact_is_refused_before_anything_is_written(self):
        target = _fresh_schema()
        artifact_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"tel-{uuid.uuid4().hex}.json"
        try:
            self.assertEqual(_apply(target)[0], migrate_tool.EXIT_OK)
            source = _fresh_schema()
            try:
                self.assertEqual(_apply(source)[0], migrate_tool.EXIT_OK)
                run_id = str(uuid.uuid4())
                with _connect_as(WRITER_ROLE, source) as conn:
                    _seed_chain(
                        conn,
                        run_id=run_id,
                        call_id=str(uuid.uuid4()),
                        invocation_id=str(uuid.uuid4()),
                        attempt_id=str(uuid.uuid4()),
                    )
                _code, artifact = _export(source, ("export", "--telemetry-run-id", run_id))
            finally:
                _drop_schema(source)

            artifact["rows"][repository.ATTEMPT_TABLE][0]["requested_model"] = "tampered"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

            code, payload = self._restore(target, artifact_path)
            self.assertEqual(code, restore_tool.EXIT_FAILED)
            self.assertEqual(payload["reason"], "artifact_validation_failed")
            self.assertEqual(payload["rows_written"], 0)

            with _connect_as(READER_ROLE, target) as conn:
                count = conn.execute("SELECT count(*) FROM provider_attempt").fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            artifact_path.unlink(missing_ok=True)
            _drop_schema(target)


class AsyncPreflightTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 22, against a genuine cluster."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()
        cls.schema = _fresh_schema()
        if _apply(cls.schema)[0] != migrate_tool.EXIT_OK:  # pragma: no cover
            _drop_schema(cls.schema)
            raise AssertionError("migration did not apply")

    @classmethod
    def tearDownClass(cls):
        _drop_schema(cls.schema)

    async def _sink(self, role=WRITER_ROLE):
        asyncpg = pytest.importorskip("asyncpg")
        # asyncpg takes no keyword/value DSN, so it is given libpq-parsed
        # parameters rather than the raw ``TEST_EVIDENCE_PG_DSN`` string.
        parts = _role_parameters(role)
        pool = await asyncpg.create_pool(
            host=parts.get("host"),
            port=int(parts.get("port", 5432)),
            database=parts.get("dbname"),
            user=parts["user"],
            password=parts.get("password"),
            min_size=1,
            max_size=2,
            server_settings={"search_path": self.schema},
        )
        self.addAsyncCleanup(pool.close)

        class Sink:
            async def _pool(self_inner):
                return pool

        return Sink()

    async def test_preflight_passes_against_a_healthy_strict_schema(self):
        import os as _os

        from provider_telemetry import service

        _os.environ["MAS_PROVIDER_TELEMETRY_POSTURE"] = "strict"
        self.addCleanup(_os.environ.pop, "MAS_PROVIDER_TELEMETRY_POSTURE", None)

        result = await service.strict_preflight(await self._sink())
        self.assertTrue(result.healthy, result.reasons)

    async def test_preflight_fails_when_the_writer_can_delete(self):
        import os as _os

        from provider_telemetry import service

        _os.environ["MAS_PROVIDER_TELEMETRY_POSTURE"] = "strict"
        self.addCleanup(_os.environ.pop, "MAS_PROVIDER_TELEMETRY_POSTURE", None)

        with _superuser() as conn:
            conn.execute(
                psycopg.sql.SQL("GRANT DELETE ON {}.provider_attempt TO {}").format(
                    psycopg.sql.Identifier(self.schema), psycopg.sql.Identifier(WRITER_ROLE)
                )
            )
        try:
            result = await service.strict_preflight(await self._sink())
            self.assertFalse(result.healthy)
            self.assertTrue(any("acl_unsafe" in reason for reason in result.reasons))
        finally:
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL("REVOKE DELETE ON {}.provider_attempt FROM {}").format(
                        psycopg.sql.Identifier(self.schema),
                        psycopg.sql.Identifier(WRITER_ROLE),
                    )
                )

    async def test_preflight_fails_when_a_trigger_is_disabled(self):
        import os as _os

        from provider_telemetry import service

        _os.environ["MAS_PROVIDER_TELEMETRY_POSTURE"] = "strict"
        self.addCleanup(_os.environ.pop, "MAS_PROVIDER_TELEMETRY_POSTURE", None)

        with _superuser() as conn:
            conn.execute(
                psycopg.sql.SQL(
                    "ALTER TABLE {}.provider_attempt DISABLE TRIGGER "
                    "trg_provider_attempt_no_mutation"
                ).format(psycopg.sql.Identifier(self.schema))
            )
        try:
            result = await service.strict_preflight(await self._sink())
            self.assertFalse(result.healthy)
            self.assertTrue(any("schema_incomplete" in r for r in result.reasons))
        finally:
            with _superuser() as conn:
                conn.execute(
                    psycopg.sql.SQL(
                        "ALTER TABLE {}.provider_attempt ENABLE TRIGGER "
                        "trg_provider_attempt_no_mutation"
                    ).format(psycopg.sql.Identifier(self.schema))
                )

    async def test_preflight_fails_when_telemetry_is_disabled(self):
        import os as _os

        from provider_telemetry import service

        _os.environ.pop("MAS_PROVIDER_TELEMETRY_POSTURE", None)
        _os.environ.pop("MAS_PROVIDER_ATTEMPT_TELEMETRY_ENABLED", None)
        result = await service.strict_preflight(await self._sink())
        self.assertFalse(result.healthy)
        self.assertIn("posture_is_off", result.reasons)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
