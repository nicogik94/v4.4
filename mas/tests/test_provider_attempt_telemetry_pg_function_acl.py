"""The EXECUTE ACL of every telemetry function is part of the catalog contract.

The finding: ``provider_telemetry_array_is_clean`` sat on PostgreSQL's default
function ACL, which grants EXECUTE to PUBLIC and is *stored as NULL*. Nothing
pinned it, so it was drift no check could see — while being drift with total
impact. PostgreSQL evaluates a CHECK constraint's function call with the
privileges of the role performing the INSERT, so revoking PUBLIC EXECUTE made
every telemetry INSERT fail with 42501, and yet ``apply`` reported
``reapplied_noop`` and ``verify`` reported healthy. The migration's own comment
claimed the helper had "no EXECUTE for PUBLIC", which was false.

The privilege model is now stated rather than defaulted:

===========================================  ===================================
``provider_telemetry_reject_mutation()``     owner
``provider_telemetry_array_is_clean(text[])``      owner, writer
``provider_telemetry_has_credential_shape(text)``  owner, writer
===========================================  ===================================

The guard is SECURITY DEFINER and invoked by the trigger machinery, so no caller
needs EXECUTE on it. The two CHECK helpers are genuinely required by the writer,
directly — that is the design decision, and it is written down, granted
explicitly, pinned in the SQL preflight (before the GRANT block could repair
it), re-checked in the SQL postflight, and stated independently in Python.

The reader appears nowhere: a CHECK constraint is never evaluated on SELECT.

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

from tests import provider_telemetry_pg_support as pg_support  # noqa: E402

OWNER_ROLE = pg_support.OWNER_ROLE
WRITER_ROLE = pg_support.WRITER_ROLE
READER_ROLE = pg_support.READER_ROLE
_apply = pg_support.apply
_connect_as = pg_support.connect_as
_drop_schema = pg_support.drop_schema
_dsn = pg_support.dsn
_fresh_schema = pg_support.fresh_schema
_provision_roles = pg_support.provision_roles
_superuser = pg_support.superuser

FINGERPRINT = "e" * 64

# Every protected function, with the signature needed to name it in GRANT and
# REVOKE, and the exact set of roles that must hold EXECUTE on it.
GUARD = "provider_telemetry_reject_mutation"
ARRAY_HELPER = "provider_telemetry_array_is_clean"
CREDENTIAL_HELPER = "provider_telemetry_has_credential_shape"

SIGNATURES = {
    GUARD: "()",
    ARRAY_HELPER: "(text[])",
    CREDENTIAL_HELPER: "(text)",
}

# The helpers a CHECK constraint calls, and therefore the ones the *inserting*
# role must be able to execute. Kept as its own list so "which functions are
# reachable from a constraint" is an enumerated claim, not a comment.
CHECK_CONSTRAINT_HELPERS = (ARRAY_HELPER, CREDENTIAL_HELPER)


def _as_owner(schema: str):
    conn = _superuser()
    conn.execute(psycopg.sql.SQL("SET search_path TO {}").format(
        psycopg.sql.Identifier(schema)))
    conn.execute(psycopg.sql.SQL("SET ROLE {}").format(
        psycopg.sql.Identifier(OWNER_ROLE)))
    return conn


def _execute_as_owner(schema: str, statement) -> None:
    with _as_owner(schema) as conn:
        conn.execute(statement)
        conn.execute("COMMIT")


def _grant(function: str, role: str):
    return psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION {}{} TO {}").format(
        psycopg.sql.Identifier(function),
        psycopg.sql.SQL(SIGNATURES[function]),
        psycopg.sql.Identifier(role),
    )


def _grant_public(function: str):
    return psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION {}{} TO PUBLIC").format(
        psycopg.sql.Identifier(function), psycopg.sql.SQL(SIGNATURES[function])
    )


def _revoke(function: str, role: str):
    return psycopg.sql.SQL("REVOKE EXECUTE ON FUNCTION {}{} FROM {}").format(
        psycopg.sql.Identifier(function),
        psycopg.sql.SQL(SIGNATURES[function]),
        psycopg.sql.Identifier(role),
    )


def _acl_grantees(schema: str, function: str) -> set:
    """Who holds EXECUTE, reading the default ACL for what it actually means.

    ``proacl IS NULL`` is not "no privileges"; it is PostgreSQL's storage for
    the default, which includes PUBLIC. Resolving it through ``acldefault`` is
    the whole reason this drift was invisible before.
    """
    with _superuser() as conn:
        row = conn.execute(
            """
            SELECT coalesce((
                SELECT array_agg(DISTINCT
                           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                ELSE pg_catalog.pg_get_userbyid(a.grantee) END)
                FROM aclexplode(coalesce(
                         p.proacl, pg_catalog.acldefault('f', p.proowner))) AS a
                WHERE a.privilege_type = 'EXECUTE'
            ), ARRAY[]::text[])
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s AND p.proname = %s
            """,
            (schema, function),
        ).fetchone()
    return set(row[0]) if row else set()


def _insert_run(schema: str, role: str):
    """A telemetry INSERT that exercises ``ck_ptr_expected_phases_sane``.

    Returns ``None`` on success or the SQLSTATE on refusal, so a test can assert
    *which* failure happened rather than merely that one did.
    """
    conn = _connect_as(role, schema)
    try:
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "expected_phases, started_at) VALUES (%s::uuid, 'strict', true, "
            "'cli_workflow', %s, %s, ARRAY['plan','build']::TEXT[], now())",
            (str(uuid.uuid4()), TELEMETRY_SCHEMA_VERSION, FINGERPRINT),
        )
        conn.execute("COMMIT")
        return None
    except psycopg.Error as exc:
        conn.execute("ROLLBACK")
        return exc.sqlstate
    finally:
        conn.close()


def _insert_attempt_event(schema: str, role: str):
    """An INSERT that exercises ``ck_pae_no_credential_shape``."""
    conn = _connect_as(role, schema)
    try:
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (str(uuid.uuid4()), TELEMETRY_SCHEMA_VERSION, FINGERPRINT),
        )
        conn.execute("ROLLBACK")
        return None
    except psycopg.Error as exc:
        conn.execute("ROLLBACK")
        return exc.sqlstate
    finally:
        conn.close()


class _SchemaCase(unittest.TestCase):
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


class IntendedPrivilegeModelTests(_SchemaCase):
    """Steps 1-3: a clean migration installs exactly the intended ACL."""

    def test_the_helper_acls_are_exactly_the_pinned_set(self):
        for function, expected in migrate_tool.EXPECTED_FUNCTION_ACLS.items():
            with self.subTest(function=function):
                self.assertEqual(_acl_grantees(self.schema, function), set(expected))

    def test_public_holds_execute_on_nothing(self):
        for function in SIGNATURES:
            with self.subTest(function=function):
                self.assertNotIn("PUBLIC", _acl_grantees(self.schema, function))

    def test_the_reader_holds_execute_on_nothing(self):
        """A CHECK constraint is never evaluated on SELECT."""
        for function in SIGNATURES:
            with self.subTest(function=function):
                self.assertNotIn(READER_ROLE, _acl_grantees(self.schema, function))

    def test_the_writer_holds_execute_on_the_check_helpers_and_not_the_guard(self):
        for function in CHECK_CONSTRAINT_HELPERS:
            with self.subTest(function=function):
                self.assertIn(WRITER_ROLE, _acl_grantees(self.schema, function))
        self.assertNotIn(WRITER_ROLE, _acl_grantees(self.schema, GUARD))

    def test_every_check_constraint_helper_is_enumerated(self):
        """The contract must cover the functions constraints actually call.

        Read out of ``pg_constraint`` rather than trusted from the list above:
        a helper added to a CHECK constraint and forgotten here would otherwise
        inherit the default PUBLIC ACL and reintroduce the finding.
        """
        with _superuser() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT p.proname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_depend d ON d.objid = con.oid
                     AND d.classid = 'pg_constraint'::regclass
                     AND d.refclassid = 'pg_proc'::regclass
                JOIN pg_proc p ON p.oid = d.refobjid
                WHERE n.nspname = %s AND con.contype = 'c'
                """,
                (self.schema,),
            ).fetchall()
        called = {row[0] for row in rows if row[0].startswith("provider_telemetry_")}
        self.assertTrue(called, "no CHECK constraint calls a telemetry helper")
        self.assertEqual(called, set(CHECK_CONSTRAINT_HELPERS))
        for function in called:
            self.assertIn(WRITER_ROLE, migrate_tool.EXPECTED_FUNCTION_ACLS[function])

    def test_a_normal_telemetry_insert_succeeds_as_the_writer(self):
        self.assertIsNone(_insert_run(self.schema, WRITER_ROLE))

    def test_verify_reports_the_clean_schema_healthy(self):
        code, payload = _apply(self.schema, argv=("verify",))
        self.assertEqual(code, migrate_tool.EXIT_OK, payload)
        self.assertEqual(payload["contract_problems"], [])


class RevokedHelperExecuteTests(_SchemaCase):
    """Steps 4-10: the drift, its impact, its detection and its repair."""

    def _revoke_writer_execute(self):
        _execute_as_owner(self.schema, _revoke(ARRAY_HELPER, WRITER_ROLE))

    def test_revoking_the_writer_grant_breaks_every_telemetry_insert(self):
        """Step 4-5: the material impact, reproduced rather than asserted."""
        self.assertIsNone(_insert_run(self.schema, WRITER_ROLE))
        self._revoke_writer_execute()
        # 42501 is insufficient_privilege: the CHECK constraint's function call
        # is what the writer may no longer make.
        self.assertEqual(_insert_run(self.schema, WRITER_ROLE), "42501")

    def test_reapply_fails_closed_before_repairing_the_acl(self):
        """Steps 6, 7 and 9 together.

        The GRANT block re-issues these privileges unconditionally, so without a
        preflight refusal the migration would put the revoked grant back and
        report a no-op — repairing the drift and reporting that there was none.
        The refusal has to happen *and* the ACL has to still be broken
        afterwards, or the fail-closed claim is untested.
        """
        self._revoke_writer_execute()
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("function EXECUTE ACL drift", payload["error"])
        self.assertIn(ARRAY_HELPER, payload["error"])
        self.assertNotEqual(payload.get("outcome"), "reapplied_noop")

        # Step 9: unrepaired.
        self.assertNotIn(WRITER_ROLE, _acl_grantees(self.schema, ARRAY_HELPER))
        self.assertEqual(_insert_run(self.schema, WRITER_ROLE), "42501")

    def test_verify_reports_the_exact_helper_acl_drift(self):
        """Step 8, stated independently of the SQL file."""
        self._revoke_writer_execute()
        code, payload = _apply(self.schema, argv=("verify",))
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        problems = payload["contract_problems"]
        self.assertIn(f"function_acl_drift:{ARRAY_HELPER}:{OWNER_ROLE}", problems)
        # And the consequence is named, not just the difference.
        self.assertIn(f"writer_cannot_execute:{ARRAY_HELPER}", problems)

    def test_preflight_reports_the_drift_as_unready(self):
        self._revoke_writer_execute()
        code, payload = _apply(self.schema, argv=("preflight",))
        self.assertEqual(code, migrate_tool.EXIT_UNAVAILABLE, payload)
        self.assertEqual(payload["status"], "unready")
        self.assertIn(f"writer_cannot_execute:{ARRAY_HELPER}",
                      payload["contract_problems"])

    def test_restoring_the_acl_explicitly_makes_reapply_clean(self):
        """Step 10: repair is an operator action, never a side effect."""
        self._revoke_writer_execute()
        code, _ = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED)

        _execute_as_owner(self.schema, _grant(ARRAY_HELPER, WRITER_ROLE))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_OK, payload)
        self.assertEqual(payload["outcome"], "reapplied_noop")
        self.assertEqual(payload["contract_problems"], [])
        self.assertIsNone(_insert_run(self.schema, WRITER_ROLE))


class AclDriftChallengeTests(_SchemaCase):
    """Every direction the ACL can drift is refused, and named.

    Widening matters as much as narrowing: an extra grantee is a privilege
    nobody decided to give, and the default ACL this finding was about *is* a
    widening — PUBLIC, granted by omission.
    """

    # PUBLIC gaining EXECUTE on the *guard* keeps its own, older refusal — a
    # role that can call the append-only guard directly is a sharper statement
    # than "an ACL differs", and that message is part of MAJ-1's evidence — so
    # the expected preflight text is named per case rather than assumed.
    GENERAL = "function EXECUTE ACL drift"
    GUARD_PUBLIC = "PUBLIC holds EXECUTE on the guard function"

    CASES = (
        ("extra_public_execute_on_array_helper", ARRAY_HELPER, "public-grant", GENERAL),
        ("extra_public_execute_on_credential_helper", CREDENTIAL_HELPER,
         "public-grant", GENERAL),
        ("extra_public_execute_on_guard", GUARD, "public-grant", GUARD_PUBLIC),
        ("extra_reader_execute_on_array_helper", ARRAY_HELPER, READER_ROLE, GENERAL),
        ("extra_reader_execute_on_credential_helper", CREDENTIAL_HELPER,
         READER_ROLE, GENERAL),
        ("extra_reader_execute_on_guard", GUARD, READER_ROLE, GENERAL),
        ("extra_writer_execute_on_guard", GUARD, WRITER_ROLE, GENERAL),
    )

    def test_every_widening_is_refused_by_reapply_and_named_by_verify(self):
        for label, function, grantee, expected_error in self.CASES:
            with self.subTest(case=label):
                schema = _fresh_schema()
                self.addCleanup(_drop_schema, schema)
                code, payload = _apply(schema)
                self.assertEqual(code, migrate_tool.EXIT_OK, payload)

                if grantee == "public-grant":
                    _execute_as_owner(schema, _grant_public(function))
                else:
                    _execute_as_owner(schema, _grant(function, grantee))

                code, payload = _apply(schema)
                self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
                self.assertIn(expected_error, payload["error"])

                code, payload = _apply(schema, argv=("verify",))
                self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
                self.assertTrue(
                    any(problem.startswith(f"function_acl_drift:{function}:")
                        for problem in payload["contract_problems"]),
                    payload["contract_problems"],
                )

    def test_a_removed_required_grantee_is_refused_for_both_helpers(self):
        for function in CHECK_CONSTRAINT_HELPERS:
            with self.subTest(function=function):
                schema = _fresh_schema()
                self.addCleanup(_drop_schema, schema)
                self.assertEqual(_apply(schema)[0], migrate_tool.EXIT_OK)
                _execute_as_owner(schema, _revoke(function, WRITER_ROLE))

                code, payload = _apply(schema)
                self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

                code, payload = _apply(schema, argv=("verify",))
                self.assertIn(f"writer_cannot_execute:{function}",
                              payload["contract_problems"])

    def test_an_added_grantee_outside_the_contract_is_refused(self):
        _execute_as_owner(
            self.schema, _grant(CREDENTIAL_HELPER, pg_support.MIGRATION_ROLE)
        )
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
        self.assertIn("function EXECUTE ACL drift", payload["error"])

    def test_a_changed_helper_owner_is_refused_and_named(self):
        with _superuser() as conn:
            conn.execute(psycopg.sql.SQL(
                "ALTER FUNCTION {}.{}(text[]) OWNER TO {}"
            ).format(
                psycopg.sql.Identifier(self.schema),
                psycopg.sql.Identifier(ARRAY_HELPER),
                psycopg.sql.Identifier(pg_support.MIGRATION_ROLE),
            ))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertIn(
            f"function_wrong_owner:{ARRAY_HELPER}:{pg_support.MIGRATION_ROLE}",
            payload["contract_problems"],
        )

    def test_a_helper_turned_security_definer_is_refused_and_named(self):
        """A CHECK helper that starts running as its owner is a different rule."""
        _execute_as_owner(self.schema, psycopg.sql.SQL(
            "ALTER FUNCTION {}(text[]) SECURITY DEFINER"
        ).format(psycopg.sql.Identifier(ARRAY_HELPER)))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertIn(f"function_security_drift:{ARRAY_HELPER}:definer",
                      payload["contract_problems"])

    def test_a_guard_turned_security_invoker_is_refused_and_named(self):
        _execute_as_owner(self.schema, psycopg.sql.SQL(
            "ALTER FUNCTION {}() SECURITY INVOKER"
        ).format(psycopg.sql.Identifier(GUARD)))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertIn(f"function_security_drift:{GUARD}:invoker",
                      payload["contract_problems"])

    def test_a_changed_helper_search_path_is_refused_and_named(self):
        _execute_as_owner(self.schema, psycopg.sql.SQL(
            "ALTER FUNCTION {}(text[]) SET search_path = public"
        ).format(psycopg.sql.Identifier(ARRAY_HELPER)))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertIn(f"function_search_path_drift:{ARRAY_HELPER}:search_path=public",
                      payload["contract_problems"])

    def test_a_reset_helper_search_path_is_refused_and_named(self):
        _execute_as_owner(self.schema, psycopg.sql.SQL(
            "ALTER FUNCTION {}(text[]) RESET search_path"
        ).format(psycopg.sql.Identifier(ARRAY_HELPER)))
        code, payload = _apply(self.schema)
        self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)

        code, payload = _apply(self.schema, argv=("verify",))
        self.assertIn(f"function_search_path_drift:{ARRAY_HELPER}:<none>",
                      payload["contract_problems"])


class HelperAclMutationTests(unittest.TestCase):
    """Bounded mutation: remove the ACL pin and the reproduction stops failing.

    Applied to a copy of the migration source and driven through the real tool,
    so what is proved is that the *pinned ACL* is doing the work — not that some
    neighbouring check happens to catch this drift by accident.
    """

    @classmethod
    def setUpClass(cls):
        _dsn()
        _provision_roles()

    def test_removing_the_acl_pin_lets_a_revoked_helper_grant_be_repaired(self):
        source = migrate_tool.MIGRATION_PATH.read_text(encoding="utf-8")

        start = source.index(
            "        -- ── Every telemetry function's EXECUTE ACL, pinned exactly, in PREflight ──"
        )
        end = source.index("    END IF;\n\n    -- ── No unexpected protected function")
        mutated = source[:start] + source[end:]
        self.assertNotIn(
            "v63 preflight: telemetry function EXECUTE ACL drift", mutated,
            "the mutation did not remove the preflight ACL pin",
        )
        # The postflight copy has to go too, or the mutation would merely move
        # the refusal later instead of removing it — and the claim under test is
        # that the *preflight* pin is what prevents a silent repair.
        post_start = mutated.index(
            "    -- ── The complete EXECUTE ACL of all three functions, as just installed ──"
        )
        # Anchored on the body-drift comment, not on section 6d: everything
        # between the two is a different contract, and a mutation that removed
        # it as well would be testing more than one thing.
        post_end = mutated.index(
            "    -- The body this file just installed must be the body this file declares."
        )
        mutated = mutated[:post_start] + mutated[post_end:]
        self.assertNotIn("telemetry function EXECUTE ACL is not the required", mutated)

        schema = _fresh_schema()
        original_path = migrate_tool.MIGRATION_PATH
        mutated_path = Path(str(original_path)).with_name("v63_acl_mutation_probe.sql")
        original_acls = dict(migrate_tool.EXPECTED_FUNCTION_ACLS)
        try:
            code, payload = _apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_OK, payload)

            _execute_as_owner(schema, _revoke(ARRAY_HELPER, WRITER_ROLE))
            self.assertEqual(_insert_run(schema, WRITER_ROLE), "42501")

            mutated_path.write_text(mutated, encoding="utf-8")
            migrate_tool.MIGRATION_PATH = mutated_path
            # The Python-side contract is the *other* statement of the same
            # rule; the mutation targets the SQL one, so it is removed here too
            # or the tool would refuse for the Python reason and the SQL pin
            # would appear load-bearing when it was not exercised.
            migrate_tool.EXPECTED_FUNCTION_ACLS.clear()
            migrate_tool.EXPECTED_FUNCTION_ACLS.update(original_acls)
            migrate_tool.EXPECTED_FUNCTION_ACLS.pop(ARRAY_HELPER)

            _apply(schema)

            # Without the pin, the GRANT block silently puts the privilege back:
            # the drift is repaired, the INSERT works again, and nothing ever
            # reported that the schema had been broken.
            self.assertIn(
                WRITER_ROLE, _acl_grantees(schema, ARRAY_HELPER),
                "the mutated migration was expected to repair the revoked grant",
            )
            self.assertIsNone(_insert_run(schema, WRITER_ROLE))
        finally:
            migrate_tool.MIGRATION_PATH = original_path
            migrate_tool.EXPECTED_FUNCTION_ACLS.clear()
            migrate_tool.EXPECTED_FUNCTION_ACLS.update(original_acls)
            mutated_path.unlink(missing_ok=True)
            _drop_schema(schema)


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
