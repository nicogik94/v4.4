"""Genuine-PostgreSQL regressions for the two catalog-contract Majors.

**MAJ-1 — the guard function's body was not pinned.** Every property the
migration checked about ``provider_telemetry_reject_mutation()`` — its name,
language, argument and return types, ``SECURITY DEFINER``, the fixed
``search_path``, its owner, its ACL — is still satisfied by a function whose
body has been replaced with ``RETURN OLD``, and such a function permits every
``UPDATE`` and ``DELETE`` the append-only triggers exist to refuse. Reapplying
then ran ``CREATE OR REPLACE FUNCTION``, installed the correct body over the
tampered one, passed its own postflight and recorded ``reapplied_noop`` with an
empty ``contract_problems`` — repairing the evidence of tampering and reporting
a no-op. The tests below reproduce the disarmed state, prove mutation really
becomes possible, and require the reapply to fail closed *without* having
replaced the body.

**MAJ-5 — nothing at the database level enforced one terminal per subject.**
``InvocationCapture`` refuses a second terminal event, but a process-local flag
is not a constraint: two workers, a redelivered event, a restored artifact or
any writer that bypassed the dataclasses could store two terminal rows for one
subject — and reconciliation reads ``is_terminal`` to decide whether a start was
matched, so two of them make the chain ambiguous exactly where completeness is
being claimed. The tests below require the second terminal to be impossible,
including for two transactions racing each other, while leaving non-terminal
observations freely appendable.

Skips unless ``TEST_EVIDENCE_PG_DSN`` is set.
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

from tools import provider_attempt_telemetry_migrate as migrate_tool  # noqa: E402
from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION  # noqa: E402

# The disposable-schema harness is shared with the main PostgreSQL suite rather
# than reimplemented: two provisioning paths would be two things to keep true,
# and two *copies* of one path would each generate role passwords the other
# does not have.
from tests import provider_telemetry_pg_support as pg_support  # noqa: E402

OWNER_ROLE = pg_support.OWNER_ROLE
WRITER_ROLE = pg_support.WRITER_ROLE
_apply = pg_support.apply
_connect_as = pg_support.connect_as
_drop_schema = pg_support.drop_schema
_dsn = pg_support.dsn
_fresh_schema = pg_support.fresh_schema
_provision_roles = pg_support.provision_roles
_superuser = pg_support.superuser

FINGERPRINT = "d" * 64

# A body that satisfies every other property the contract pins — plpgsql,
# SECURITY DEFINER, `SET search_path = pg_catalog`, zero arguments, returns
# trigger, owned by the telemetry owner — and disarms the guard completely.
#
# `RETURN COALESCE(NEW, OLD)` rather than the audit's `RETURN OLD`: a BEFORE
# UPDATE trigger that returns OLD makes the update a silent *no-op* instead of
# letting it through, which disarms the exception but not the write. Returning
# NEW on UPDATE and OLD on DELETE is the strictly more permissive tampering —
# every mutation the guard exists to refuse actually lands — so it is the
# harder case to detect and the one worth pinning against.
PERMISSIVE_GUARD_BODY = """
CREATE OR REPLACE FUNCTION provider_telemetry_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $tampered$
BEGIN
    RETURN COALESCE(NEW, OLD);
END;
$tampered$
"""


def _guard_body(conn) -> str:
    return conn.execute(
        "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = current_schema() "
        "AND p.proname = 'provider_telemetry_reject_mutation'"
    ).fetchone()[0]


def _as_owner(schema: str):
    """A superuser connection with the schema selected and the owner assumed.

    Tampering is done as the *owner* on purpose: that is the only identity the
    security model concedes can rewrite these objects, so the scenario under
    test is the one the model actually leaves open, not an impossible one.
    """
    conn = _superuser()
    conn.execute(psycopg.sql.SQL("SET search_path TO {}").format(
        psycopg.sql.Identifier(schema)))
    conn.execute(psycopg.sql.SQL("SET ROLE {}").format(
        psycopg.sql.Identifier(OWNER_ROLE)))
    return conn


def _seed_run(conn) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
        "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
        "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
        (run_id, TELEMETRY_SCHEMA_VERSION, FINGERPRINT),
    )
    return run_id


def _insert_event(conn, *, run_id, subject_id, kind, ordinal, call_id=None):
    terminal = kind in ("completed", "provider_failure", "cancelled", "unknown", "skipped")
    conn.execute(
        "INSERT INTO provider_attempt_event (event_id, subject_kind, subject_id, "
        "call_id, telemetry_run_id, event_kind, event_ordinal, is_terminal, "
        "observed_at, response_metadata_fingerprint, schema_version) "
        "VALUES (gen_random_uuid(), 'sdk_invocation', %s::uuid, %s::uuid, %s::uuid, "
        "%s, %s, %s, now(), %s, %s)",
        (
            subject_id,
            call_id or str(uuid.uuid4()),
            run_id,
            kind,
            ordinal,
            terminal,
            FINGERPRINT,
            TELEMETRY_SCHEMA_VERSION,
        ),
    )


# ═══════════════════════════ MAJ-1 ═══════════════════════════


class GuardFunctionBodyDriftTests(unittest.TestCase):
    """The guard's *implementation* is part of the catalog contract."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def setUp(self):
        self.schema = _fresh_schema()
        code, payload = _apply(self.schema)
        if code != migrate_tool.EXIT_OK:  # pragma: no cover - harness failure
            _drop_schema(self.schema)
            raise AssertionError(f"migration did not apply: {payload}")
        self.addCleanup(_drop_schema, self.schema)

    # ── the reproduction ──

    def test_a_permissive_guard_body_disarms_the_append_only_triggers(self):
        """Step 1-2: replace the body, and prove mutation becomes possible.

        Nothing else changes. If this test ever stops being able to mutate the
        table, the tampering has stopped reproducing the finding and the
        refusal proved below would be proving nothing.
        """
        with _as_owner(self.schema) as conn:
            run_id = _seed_run(conn)
            # Before: the guard refuses, as it must.
            with self.assertRaises(psycopg.errors.RestrictViolation):
                conn.execute(
                    "UPDATE provider_telemetry_run SET entry_point = 'api_workflow' "
                    "WHERE telemetry_run_id = %s::uuid",
                    (run_id,),
                )
            conn.execute("ROLLBACK")

            conn.execute(PERMISSIVE_GUARD_BODY)

            # After: every append-only guarantee is gone, and the catalog still
            # says the function is plpgsql, SECURITY DEFINER, search_path-fixed
            # and owned by the telemetry owner.
            conn.execute(
                "UPDATE provider_telemetry_run SET entry_point = 'api_workflow' "
                "WHERE telemetry_run_id = %s::uuid",
                (run_id,),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT entry_point FROM provider_telemetry_run "
                    "WHERE telemetry_run_id = %s::uuid",
                    (run_id,),
                ).fetchone()[0],
                "api_workflow",
            )
            conn.execute("DELETE FROM provider_telemetry_run")
            self.assertEqual(
                conn.execute("SELECT count(*) FROM provider_telemetry_run").fetchone()[0],
                0,
            )
            conn.execute("TRUNCATE provider_attempt_event")

            properties = conn.execute(
                "SELECT p.prosecdef, p.proconfig, l.lanname, "
                "       pg_catalog.pg_get_userbyid(p.proowner) "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "JOIN pg_language l ON l.oid = p.prolang "
                "WHERE n.nspname = current_schema() "
                "AND p.proname = 'provider_telemetry_reject_mutation'"
            ).fetchone()
            self.assertEqual(
                properties,
                (True, ["search_path=pg_catalog"], "plpgsql", OWNER_ROLE),
                "the tampered guard must still satisfy every property other than "
                "its body, or this scenario is not the one MAJ-1 describes",
            )

    def test_reapply_fails_closed_and_does_not_repair_the_tampered_body(self):
        """Steps 3-6: exact reapply refuses, names the drift, replaces nothing."""
        with _as_owner(self.schema) as conn:
            conn.execute(PERMISSIVE_GUARD_BODY)
            conn.execute("COMMIT")
            tampered = _guard_body(conn)
        self.assertIn("COALESCE(NEW, OLD)", tampered)

        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("v63 preflight: telemetry function body drift", payload["error"])
        self.assertIn("provider_telemetry_reject_mutation", payload["error"])
        self.assertIn("body_drift", payload["error"])

        # The refusal must not be a repair. If CREATE OR REPLACE had run, the
        # body would be the correct one and the operator would be looking at a
        # healthy schema with no record that it was ever tampered with.
        with _as_owner(self.schema) as conn:
            self.assertEqual(_guard_body(conn), tampered)

    def test_the_refusal_is_never_recorded_as_reapplied_noop(self):
        """A refused reapply writes no ledger row at all."""
        with _as_owner(self.schema) as conn:
            before = conn.execute(
                "SELECT count(*) FROM provider_telemetry_migration_ledger"
            ).fetchone()[0]
            conn.execute(PERMISSIVE_GUARD_BODY)
            conn.execute("COMMIT")

        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertNotEqual(payload.get("outcome"), "reapplied_noop")

        with _as_owner(self.schema) as conn:
            after = conn.execute(
                "SELECT count(*) FROM provider_telemetry_migration_ledger"
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_the_python_contract_names_the_body_drift_independently(self):
        """`verify` reports it too, from the contract embedded in Python."""
        with _as_owner(self.schema) as conn:
            conn.execute(PERMISSIVE_GUARD_BODY)
            conn.execute("COMMIT")

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        problems = payload["contract_problems"]
        self.assertTrue(
            any(p.startswith("function_body_drift:provider_telemetry_reject_mutation")
                for p in problems),
            problems,
        )
        # The diagnostic carries a digest of the tampered body, never the body:
        # a rewritten body is attacker-controlled text.
        drift = next(p for p in problems if p.startswith("function_body_drift:"))
        self.assertNotIn("RETURN OLD", drift)
        self.assertRegex(drift.rsplit(":", 1)[-1], r"\A[0-9a-f]{64}\Z")

    def test_preflight_reports_the_body_drift_as_unready(self):
        with _as_owner(self.schema) as conn:
            conn.execute(PERMISSIVE_GUARD_BODY)
            conn.execute("COMMIT")
        code, payload = _apply(self.schema, argv=("preflight",))
        self.assertEqual(code, migrate_tool.EXIT_UNAVAILABLE, payload)
        self.assertEqual(payload["status"], "unready")
        self.assertTrue(
            any("function_body_drift" in p for p in payload["contract_problems"])
        )

    def test_a_rewritten_helper_body_is_rejected_too(self):
        """The array helper backs a CHECK constraint; its body is pinned as well."""
        with _as_owner(self.schema) as conn:
            conn.execute(
                """
                CREATE OR REPLACE FUNCTION provider_telemetry_array_is_clean(p_values TEXT[])
                RETURNS BOOLEAN LANGUAGE sql IMMUTABLE PARALLEL SAFE
                SET search_path = pg_catalog
                AS $weakened$ SELECT true $weakened$
                """
            )
            conn.execute("COMMIT")
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("telemetry function body drift", payload["error"])
        self.assertIn("provider_telemetry_array_is_clean", payload["error"])

    def test_an_unexpected_protected_function_is_rejected(self):
        with _as_owner(self.schema) as conn:
            conn.execute(
                "CREATE FUNCTION provider_telemetry_backdoor() RETURNS void "
                "LANGUAGE sql AS $$ SELECT $$"
            )
            conn.execute("COMMIT")
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("unexpected protected telemetry function", payload["error"])

    def test_verify_reports_an_unexpected_protected_function(self):
        with _as_owner(self.schema) as conn:
            conn.execute(
                "CREATE FUNCTION provider_telemetry_backdoor() RETURNS void "
                "LANGUAGE sql AS $$ SELECT $$"
            )
            conn.execute("COMMIT")
        code, payload = _apply(self.schema, argv=("verify",))
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn(
            "unexpected_protected_function:provider_telemetry_backdoor",
            payload["contract_problems"],
        )

    def test_a_widened_guard_acl_is_refused_before_the_revoke_repairs_it(self):
        with _as_owner(self.schema) as conn:
            conn.execute(
                "GRANT EXECUTE ON FUNCTION provider_telemetry_reject_mutation() TO PUBLIC"
            )
            conn.execute("COMMIT")
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("PUBLIC holds EXECUTE on the guard function", payload["error"])

    def test_a_narrowed_table_acl_is_not_silently_repaired_into_a_noop(self):
        """The other half of the category: drift the GRANT block would restore.

        The migration re-issues its GRANTs unconditionally, so a revoked
        privilege is put back and the postflight then passes. Without a
        before/after comparison the run is recorded ``reapplied_noop`` — a claim
        that nothing changed, made by the run that changed it.
        """
        with _as_owner(self.schema) as conn:
            conn.execute(
                psycopg.sql.SQL("REVOKE INSERT ON provider_attempt FROM {}").format(
                    psycopg.sql.Identifier(WRITER_ROLE)
                )
            )
            conn.execute("COMMIT")
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("repaired drift, not a no-op", payload["error"])
        self.assertNotEqual(payload.get("outcome"), "reapplied_noop")

    def test_an_undrifted_reapply_is_still_a_proven_no_op(self):
        """The check above must not make an honest reapply fail."""
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_OK, payload)
        self.assertEqual(payload["outcome"], "reapplied_noop")
        self.assertEqual(payload["contract_problems"], [])


class GuardBodyMutationTests(unittest.TestCase):
    """Bounded mutation: remove the body check and the reproduction stops failing.

    The mutation is applied to a *copy* of the migration source and driven
    through the real tool, so what is proved is that the pinned body is the
    thing doing the work — not that some other clause happens to catch this
    tampering by accident.
    """

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def test_removing_the_body_check_lets_a_tampered_guard_be_repaired(self):
        source = migrate_tool.MIGRATION_PATH.read_text(encoding="utf-8")

        # Excise exactly the preflight body comparison, leaving everything else
        # — including the postflight's copy — intact, so the mutation is the
        # narrowest one that removes the pre-CREATE-OR-REPLACE refusal.
        start = source.index("        -- ── The exact function body, pinned ──")
        end = source.index("        -- Argument names matter too")
        mutated = source[:start] + source[end:]
        self.assertNotIn("v63 preflight: telemetry function body drift", mutated)

        schema = _fresh_schema()
        original_path = migrate_tool.MIGRATION_PATH
        mutated_path = Path(str(original_path)) .with_name("v63_mutation_probe.sql")
        try:
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_OK, payload)
            with _as_owner(schema) as conn:
                conn.execute(PERMISSIVE_GUARD_BODY)
                conn.execute("COMMIT")

            mutated_path.write_text(mutated, encoding="utf-8")
            migrate_tool.MIGRATION_PATH = mutated_path
            code, payload = _apply(schema)

            # Without the pinned body the preflight has nothing to say about a
            # guard that no longer guards: the script reaches CREATE OR REPLACE
            # and silently reinstalls the correct body. The only reason this
            # does not end in a success payload is the *other* control added
            # alongside it — the catalog fingerprint — which is what "repaired
            # drift, not a no-op" is reporting.
            self.assertNotIn(
                "telemetry function body drift", payload.get("error", ""),
                "the mutation did not remove the body check",
            )
            with _as_owner(schema) as conn:
                repaired = _guard_body(conn)
            self.assertNotIn(
                "COALESCE(NEW, OLD)", repaired,
                "the mutated migration was expected to repair the tampered body",
            )
        finally:
            migrate_tool.MIGRATION_PATH = original_path
            mutated_path.unlink(missing_ok=True)
            _drop_schema(schema)


# ═══════════════════════════ MAJ-5 ═══════════════════════════


class TerminalUniquenessTests(unittest.TestCase):
    """Exactly one terminal lifecycle event per subject, enforced by PostgreSQL."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()
        cls.schema = _fresh_schema()
        code, payload = _apply(cls.schema)
        if code != migrate_tool.EXIT_OK:  # pragma: no cover - harness failure
            _drop_schema(cls.schema)
            raise AssertionError(f"migration did not apply: {payload}")

    @classmethod
    def tearDownClass(cls):
        _drop_schema(cls.schema)

    def test_a_sequential_duplicate_terminal_is_refused(self):
        """Python is bypassed entirely: this is a raw INSERT by the writer."""
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = _seed_run(conn)
            subject = str(uuid.uuid4())
            _insert_event(conn, run_id=run_id, subject_id=subject,
                          kind="completed", ordinal=1)
            with self.assertRaises(psycopg.errors.UniqueViolation):
                _insert_event(conn, run_id=run_id, subject_id=subject,
                              kind="completed", ordinal=2)
            conn.rollback()

    def test_a_different_terminal_kind_for_the_same_subject_is_refused(self):
        """One terminal, not one terminal per kind."""
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = _seed_run(conn)
            subject = str(uuid.uuid4())
            _insert_event(conn, run_id=run_id, subject_id=subject,
                          kind="completed", ordinal=1)
            for kind in ("provider_failure", "cancelled", "unknown", "skipped"):
                with self.subTest(kind=kind):
                    savepoint = conn.execute("SAVEPOINT k")
                    del savepoint
                    with self.assertRaises(psycopg.errors.UniqueViolation):
                        _insert_event(conn, run_id=run_id, subject_id=subject,
                                      kind=kind, ordinal=2)
                    conn.execute("ROLLBACK TO SAVEPOINT k")
            conn.rollback()

    def test_two_transactions_racing_to_insert_a_terminal_cannot_both_commit(self):
        """Deterministic: the loser blocks on the winner and is then rejected.

        A partial unique index turns the race into a lock wait, so the outcome
        does not depend on scheduling: the second transaction's INSERT blocks on
        the first transaction's uncommitted index entry, is released the moment
        the first commits, and is then refused. Python is not involved on either
        side — both are raw INSERTs by the writer role.
        """
        import threading

        subject = str(uuid.uuid4())
        first = _connect_as(WRITER_ROLE, self.schema, autocommit=False)
        second = _connect_as(WRITER_ROLE, self.schema, autocommit=False)
        blocked = threading.Event()
        outcome: dict[str, object] = {}

        def loser() -> None:
            try:
                blocked.set()
                _insert_event(second, run_id=outcome["run_id"], subject_id=subject,
                              kind="provider_failure", ordinal=1)
                second.commit()
                outcome["result"] = "committed"
            except BaseException as exc:  # noqa: BLE001 - reported to the assertions
                outcome["result"] = exc

        try:
            outcome["run_id"] = _seed_run(first)
            first.commit()

            _insert_event(first, run_id=outcome["run_id"], subject_id=subject,
                          kind="completed", ordinal=1)

            worker = threading.Thread(target=loser, daemon=True)
            worker.start()
            blocked.wait(timeout=5)

            # The second INSERT is now waiting on the first transaction's
            # uncommitted index entry. Prove that rather than assuming it: the
            # thread must still be alive after a moment, because the index is
            # holding it.
            worker.join(timeout=0.5)
            self.assertTrue(
                worker.is_alive(),
                "the duplicate terminal was not blocked by the unique index",
            )

            first.commit()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())

            self.assertIsInstance(outcome["result"], psycopg.errors.UniqueViolation)
            second.rollback()
            # `SET search_path` is transactional: the rollback that clears the
            # aborted transaction also discards the schema selection made when
            # this connection was opened, so it has to be re-established before
            # the unqualified read below.
            second.execute(psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(self.schema)))

            surviving = second.execute(
                "SELECT count(*), min(event_kind) FROM provider_attempt_event "
                "WHERE subject_id = %s::uuid AND is_terminal",
                (subject,),
            ).fetchone()
            self.assertEqual(surviving, (1, "completed"))
        finally:
            first.close()
            second.close()

    def test_multiple_nonterminal_observations_remain_appendable(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = _seed_run(conn)
            subject = str(uuid.uuid4())
            for ordinal, kind in enumerate(
                ("observation", "observation", "capture_failure",
                 "transformation_failure"), start=1
            ):
                _insert_event(conn, run_id=run_id, subject_id=subject,
                              kind=kind, ordinal=ordinal)
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM provider_attempt_event "
                    "WHERE subject_id = %s::uuid AND NOT is_terminal",
                    (subject,),
                ).fetchone()[0],
                4,
            )
            conn.rollback()

    def test_a_nonterminal_observation_after_the_terminal_is_still_allowed(self):
        """The lifecycle contract: events append, and nothing retracts.

        A transformation failure that happens after the provider answered
        arrives *after* the terminal event, and must be recordable beside it —
        that is the whole reason the model is append-only.
        """
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = _seed_run(conn)
            subject = str(uuid.uuid4())
            _insert_event(conn, run_id=run_id, subject_id=subject,
                          kind="completed", ordinal=1)
            _insert_event(conn, run_id=run_id, subject_id=subject,
                          kind="transformation_failure", ordinal=2)
            _insert_event(conn, run_id=run_id, subject_id=subject,
                          kind="capture_failure", ordinal=3)
            rows = conn.execute(
                "SELECT event_kind, is_terminal FROM provider_attempt_event "
                "WHERE subject_id = %s::uuid ORDER BY event_ordinal",
                (subject,),
            ).fetchall()
            self.assertEqual(
                rows,
                [("completed", True), ("transformation_failure", False),
                 ("capture_failure", False)],
            )
            conn.rollback()

    def test_two_different_subjects_may_each_carry_one_terminal(self):
        with _connect_as(WRITER_ROLE, self.schema, autocommit=False) as conn:
            run_id = _seed_run(conn)
            for _ in range(3):
                _insert_event(conn, run_id=run_id, subject_id=str(uuid.uuid4()),
                              kind="completed", ordinal=1)
            conn.rollback()


class TerminalIndexDriftTests(unittest.TestCase):
    """Missing, changed, extra or weakened terminal indexes are rejected."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def _tampered(self, statement: str, *, expect: str) -> None:
        schema = _fresh_schema()
        try:
            self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
            with _as_owner(schema) as conn:
                conn.execute(statement)
                conn.execute("COMMIT")
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn(expect, payload["error"])
        finally:
            _drop_schema(schema)

    def test_a_dropped_terminal_index_is_rejected(self):
        # A dropped index trips the all-or-nothing object count first; either
        # way the reapply fails closed rather than re-creating it and calling
        # the run a no-op.
        self._tampered(
            "DROP INDEX idx_provider_attempt_event_one_terminal",
            expect="partial or divergent telemetry foundation",
        )

    def test_a_terminal_index_downgraded_to_non_unique_is_rejected(self):
        self._tampered(
            "DROP INDEX idx_provider_attempt_event_one_terminal; "
            "CREATE INDEX idx_provider_attempt_event_one_terminal "
            "ON provider_attempt_event (subject_id) WHERE is_terminal",
            expect="index contract violated",
        )

    def test_a_terminal_index_that_lost_its_predicate_is_rejected(self):
        self._tampered(
            "DROP INDEX idx_provider_attempt_event_one_terminal; "
            "CREATE UNIQUE INDEX idx_provider_attempt_event_one_terminal "
            "ON provider_attempt_event (subject_id)",
            expect="index contract violated",
        )

    def test_a_terminal_index_widened_by_subject_kind_is_rejected(self):
        """`UNIQUE(subject_id, subject_kind)` permits one terminal *per kind*."""
        self._tampered(
            "DROP INDEX idx_provider_attempt_event_one_terminal; "
            "CREATE UNIQUE INDEX idx_provider_attempt_event_one_terminal "
            "ON provider_attempt_event (subject_id, subject_kind) WHERE is_terminal",
            expect="index contract violated",
        )

    def test_an_extra_index_on_the_event_relation_is_rejected(self):
        self._tampered(
            "CREATE INDEX idx_provider_attempt_event_smuggled "
            "ON provider_attempt_event (event_kind)",
            expect="partial or divergent telemetry foundation",
        )

    def test_verify_reports_the_terminal_index_independently_of_the_sql(self):
        schema = _fresh_schema()
        try:
            self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
            with _as_owner(schema) as conn:
                conn.execute(
                    "DROP INDEX idx_provider_attempt_event_one_terminal; "
                    "CREATE INDEX idx_provider_attempt_event_one_terminal "
                    "ON provider_attempt_event (subject_id) WHERE is_terminal"
                )
                conn.execute("COMMIT")
            code, payload = _apply(schema, argv=("verify",))
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn(
                "index_definition_drift:idx_provider_attempt_event_one_terminal",
                payload["contract_problems"],
            )
        finally:
            _drop_schema(schema)


class TerminalUniquenessMutationTests(unittest.TestCase):
    """Bounded mutation: drop the index and the duplicate becomes storable."""

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def test_without_the_partial_unique_index_two_terminals_commit(self):
        schema = _fresh_schema()
        try:
            self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
            with _as_owner(schema) as conn:
                conn.execute("DROP INDEX idx_provider_attempt_event_one_terminal")
                conn.execute("COMMIT")
            with _connect_as(WRITER_ROLE, schema, autocommit=False) as conn:
                run_id = _seed_run(conn)
                subject = str(uuid.uuid4())
                _insert_event(conn, run_id=run_id, subject_id=subject,
                              kind="completed", ordinal=1)
                _insert_event(conn, run_id=run_id, subject_id=subject,
                              kind="provider_failure", ordinal=2)
                conn.commit()
                self.assertEqual(
                    conn.execute(
                        "SELECT count(*) FROM provider_attempt_event "
                        "WHERE subject_id = %s::uuid AND is_terminal",
                        (subject,),
                    ).fetchone()[0],
                    2,
                    "the mutation must reproduce the pre-remediation behavior",
                )
        finally:
            _drop_schema(schema)


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
