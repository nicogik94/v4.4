"""The credential grammar is load-bearing, and here is what holds it up.

The finding: deleting the bearer/credential rules from
``provider_telemetry/redaction.py`` left the whole suite green, and a value such
as ``Bearer_abcdefghijkl`` could then be stored as a valid provider response
identifier — it satisfies the positive identifier grammar character for
character, and the identifier grammar was the only thing the database checked.

So there were two gaps, and this module closes both:

* **No test made the rule load-bearing.** The matrix below enumerates the shapes
  that must never be storable, asserts *which named rule* refuses each one, and
  finishes with four bounded mutations — remove the bearer rule, weaken its
  regex, bypass Python entirely, bypass the export's validation — each of which
  has to make at least one test here fail.
* **PostgreSQL had no independent opinion.** A positive grammar cannot express
  "not a credential", so the database now carries the credential shapes as their
  own CHECK, and the ``PostgresCredential*`` cases below insert as the writer
  role with Python nowhere in the picture.
"""
import re
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import redaction  # noqa: E402
from provider_telemetry.values import (  # noqa: E402
    VALUE_INVALID,
    VALUE_REDACTED,
    VALUE_UNKNOWN_VALUE,
)

REFUSED_STATUSES = (VALUE_REDACTED, VALUE_INVALID)

# The audit's own example, first, followed by every variant the remediation
# brief enumerates. Each entry is (label, value).
CREDENTIAL_VALUES: tuple[tuple[str, str], ...] = (
    ("the audit's example", "Bearer_abcdefghijkl"),
    ("hyphen separator", "Bearer-abcdefghijkl"),
    ("space separator", "Bearer abcdefghijkl"),
    ("dot separator", "Bearer.abcdefghijkl"),
    ("colon separator", "Bearer:abcdefghijkl"),
    ("equals separator", "Bearer=abcdefghijkl"),
    ("no separator", "Bearerabcdefghijkl"),
    ("repeated separators", "Bearer___abcdefghijkl"),
    ("basic scheme", "Basic_abcdefghijkl"),
    ("basic base64", "Basic_dXNlcjpwYXNzd29yZA=="),
    ("header name prefix", "Authorization_Bearer_abcdefghijkl"),
    ("header name alone", "Authorization_abcdef123456"),
    ("lowercase", "bearer_abcdefghijkl"),
    ("uppercase", "BEARER_ABCDEFGHIJKL"),
    ("mixed case", "BeArEr_abcdefghijkl"),
    ("fullwidth", "Ｂｅａｒｅｒ_abcdefghijkl"),
    ("zero width space", "Bearer​_abcdefghijkl"),
    ("zero width non joiner", "Be‌arer_abcdefghijkl"),
    ("word joiner", "Bearer⁠_abcdefghijkl"),
    ("right to left override", "‮Bearer_abcdefghijkl"),
    ("soft hyphen", "Bear­er_abcdefghijkl"),
    ("percent encoded space", "Bearer%20abcdefghijkl"),
    ("percent encoded colon", "Bearer%3Aabcdefghijkl"),
    ("percent encoded equals", "Bearer%3Dabcdefghijkl"),
    ("url embedded token", "https://user:sk-ant-secret@api.anthropic.com/v1"),
    ("url userinfo", "user:hunter2@example.com"),
    ("bare scheme", "postgresql://reader:pw@db/telemetry"),
    ("cookie assignment", "session=abc123def456"),
    ("session identifier", "sessionid_9f8a7b6c5d4e"),
    ("cookie name prefix", "cookie_9f8a7b6c5d4e"),
    ("api key assignment", "api_key=abcd1234efgh"),
    ("api key prefix", "api-key-abcd1234efgh"),
    ("access token prefix", "access_token_abcd1234efgh"),
    ("anthropic key", "sk-ant-api03-abcdefghijkl"),
    ("openai key", "sk-proj-abcdefghijklmnop"),
    ("github token", "ghp_abcdefghijklmnop"),
    ("github refresh token", "ghr_abcdefghijklmnop"),
    ("slack token", "xoxb-1234567890-abcdef"),
    ("aws access key id", "AKIAIOSFODNN7EXAMPLE"),
    ("google api key", "AIzaSyA1234567890abcdefghijklmnopqrst"),
    ("stripe key", "sk_live_abcdefghijkl"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
    ("password assignment", "password:hunter2hunter2"),
    ("secret prefix", "secret_abcdef123456"),
    ("private key prefix", "private_key_abcdef123456"),
)

# Provider identifiers that are genuinely legitimate and must keep working. A
# refusal rule that also refuses these is not a safety control, it is an outage.
SAFE_RESPONSE_IDS = (
    "msg_01TESTIDENTITY",
    "chatcmpl-9aBcDeFgHiJkLmNoPqRs",
    "msg_bdrk_01abcdefghijklmnop",
    "resp_68f9c2a1b3d4e5f6",
    "req-1234567890",
)
SAFE_MODELS = (
    "claude-sonnet-4-6",
    "claude-opus-4-1-20250805",
    "gpt-4.1",
    "gpt-4o-mini",
    "o3-mini-2025-01-31",
    "anthropic.claude-3-5-sonnet-20241022-v2:0".replace(":", "-"),
)
SAFE_STOP_REASONS = (
    "end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn",
    "refusal", "stop", "length", "tool_calls", "content_filter", "function_call",
)


class CredentialShapeMatrixTests(unittest.TestCase):
    """No credential shape survives any provider-text validator."""

    VALIDATORS = (
        ("provider_response_id", redaction.provider_response_id),
        ("provider_request_id", redaction.provider_request_id),
        ("effective_model", redaction.provider_model),
        ("stop_reason", redaction.stop_reason),
        ("error_category", redaction.error_category),
    )

    def test_no_credential_shape_is_ever_stored_as_a_valid_value(self):
        for label, value in CREDENTIAL_VALUES:
            for field, validator in self.VALIDATORS:
                with self.subTest(case=label, field=field):
                    result = validator(value)
                    self.assertFalse(
                        result.is_valid,
                        f"{field} accepted a credential-shaped value ({label})",
                    )
                    self.assertIsNone(result.stored)
                    self.assertIn(result.status, REFUSED_STATUSES + (VALUE_UNKNOWN_VALUE,))
                    # Whatever the status, the value itself is never carried.
                    self.assertNotIn(value, result.detail)

    def test_the_audit_example_is_refused_by_the_bearer_rule_specifically(self):
        """Not merely refused — refused by the rule that exists to refuse it."""
        self.assertEqual(
            redaction.credential_shape("Bearer_abcdefghijkl"), "bearer_scheme"
        )
        result = redaction.provider_response_id("Bearer_abcdefghijkl")
        self.assertEqual(result.status, VALUE_REDACTED)
        self.assertEqual(result.detail, "credential_shape")

    def test_retry_after_refuses_credential_shapes_too(self):
        for label, value in CREDENTIAL_VALUES:
            with self.subTest(case=label):
                result = redaction.retry_after(value)
                self.assertFalse(result.is_valid)
                self.assertIsNone(result.stored)

    def test_invisible_codepoints_are_refused_and_never_stripped(self):
        """Stripping is what turns an evasion into a conformant credential."""
        for value in ("sk​-ant-secret", "Bearer​_abcdefghijkl",
                      "msg_​01ABC", "msg‮_01ABC"):
            with self.subTest(value=repr(value)):
                result = redaction.provider_response_id(value)
                self.assertFalse(result.is_valid)
                self.assertIn(result.status, REFUSED_STATUSES)

    def test_unicode_normalization_folds_compatibility_forms_before_scanning(self):
        """NFKC first, then the scan: a fullwidth scheme must not evade it."""
        for value in ("Ｂｅａｒｅｒ_abcdefghijkl", "ｓｋ-ant-secret",
                      "ｓｋ_ｌｉｖｅ_abcdefghijkl"):
            with self.subTest(value=value):
                self.assertFalse(redaction.provider_response_id(value).is_valid)

    def test_a_credential_is_never_normalized_into_a_valid_identifier(self):
        """No path may return a *valid* value derived from a refused one."""
        for _, value in CREDENTIAL_VALUES:
            for _, validator in self.VALIDATORS:
                result = validator(value)
                if result.is_valid:  # pragma: no cover - the assertion below fails
                    self.fail(f"{value!r} was normalized into {result.value!r}")
                self.assertIsNone(result.value)

    def test_every_named_rule_refuses_at_least_one_case_in_the_matrix(self):
        """A rule nothing exercises is a rule nobody would notice losing."""
        exercised = {
            redaction.credential_shape(
                __import__("unicodedata").normalize("NFKC", value)
            )
            for _, value in CREDENTIAL_VALUES
        }
        exercised.discard(None)
        declared = {name for name, _ in redaction.CREDENTIAL_SHAPES}
        self.assertEqual(
            declared - exercised, set(),
            "every declared rule must be the *first* refusal for some case in "
            "the matrix, or losing it would go unnoticed",
        )

    def test_legitimate_provider_identifiers_are_still_accepted(self):
        for value in SAFE_RESPONSE_IDS:
            with self.subTest(response_id=value):
                result = redaction.provider_response_id(value)
                self.assertTrue(result.is_valid, f"{value} was refused")
                self.assertEqual(result.value, value)
        for value in SAFE_MODELS:
            with self.subTest(model=value):
                result = redaction.provider_model(value)
                self.assertTrue(result.is_valid, f"{value} was refused")
        for value in SAFE_STOP_REASONS:
            with self.subTest(stop_reason=value):
                result = redaction.stop_reason(value)
                self.assertTrue(result.is_valid, f"{value} was refused")
        for value in ("0", "30", "1.5", "120"):
            with self.subTest(retry_after=value):
                self.assertTrue(redaction.retry_after(value).is_valid)


class ExceptionIdentityCredentialTests(unittest.TestCase):
    """Structured exception fields go through the same grammar."""

    def test_a_credential_in_a_typed_exception_field_is_dropped(self):
        class Hostile(Exception):
            status_code = 401
            request_id = "Bearer_abcdefghijkl"
            body = {"error": {"type": "sk-ant-api03-leaked"}}

        identity = redaction.exception_identity(Hostile())
        self.assertIn("exception=Hostile", identity)
        self.assertIn("status_code=401", identity)
        self.assertNotIn("Bearer", identity)
        self.assertNotIn("sk-ant", identity)

    def test_a_safe_typed_exception_field_still_survives(self):
        class Ordinary(Exception):
            status_code = 429
            request_id = "req_01ABCDEF"
            body = {"error": {"type": "rate_limit_error"}}

        identity = redaction.exception_identity(Ordinary())
        self.assertIn("error_type=rate_limit_error", identity)
        self.assertIn("request_id=req_01ABCDEF", identity)


# ═══════════════════════════ bounded mutations ═══════════════════════════


class CredentialGrammarMutationTests(unittest.TestCase):
    """Four mutations, each of which must break at least one assertion here.

    The rules are patched on the module for the duration of a single test and
    restored afterwards, so the mutation is bounded to the assertion it is
    proving and never leaks into another test.
    """

    def _patched(self, shapes):
        original_named = redaction.CREDENTIAL_SHAPES
        original = redaction._CREDENTIAL_SHAPES
        redaction.CREDENTIAL_SHAPES = tuple(shapes)
        redaction._CREDENTIAL_SHAPES = tuple(p for _, p in shapes)
        self.addCleanup(
            lambda: (
                setattr(redaction, "CREDENTIAL_SHAPES", original_named),
                setattr(redaction, "_CREDENTIAL_SHAPES", original),
            )
        )

    # ── Mutation 1: remove the bearer rule ──

    def test_mutation_removing_the_bearer_rule_makes_the_example_storable(self):
        self._patched(
            [(n, p) for n, p in redaction.CREDENTIAL_SHAPES if n != "bearer_scheme"]
        )
        result = redaction.provider_response_id("Bearer_abcdefghijkl")
        self.assertTrue(
            result.is_valid,
            "removing the bearer rule must reproduce the finding",
        )
        self.assertEqual(result.value, "Bearer_abcdefghijkl")

        # And that is exactly what the matrix test above would then report.
        with self.assertRaises(AssertionError):
            CredentialShapeMatrixTests(
                "test_no_credential_shape_is_ever_stored_as_a_valid_value"
            ).debug()

    # ── Mutation 2: weaken the bearer regex ──

    def test_mutation_weakening_the_bearer_regex_makes_the_example_storable(self):
        weakened = re.compile(r"(?i)bearer\s+[A-Za-z0-9+/=]{4,}")  # space only
        self._patched(
            [
                (n, weakened if n == "bearer_scheme" else p)
                for n, p in redaction.CREDENTIAL_SHAPES
            ]
        )
        self.assertTrue(
            redaction.provider_response_id("Bearer_abcdefghijkl").is_valid,
            "a bearer rule that only matches a space separator must reproduce "
            "the finding",
        )
        with self.assertRaises(AssertionError):
            CredentialShapeMatrixTests(
                "test_the_audit_example_is_refused_by_the_bearer_rule_specifically"
            ).debug()

    # ── Mutation 3: bypass Python validation entirely ──

    def test_mutation_bypassing_python_still_leaves_the_database_contract(self):
        """Python is only the first of two independent refusals.

        Constructing the event record directly with a hand-made ``valid``
        ProviderValue skips ``redaction`` completely — which is exactly what a
        restore of a hand-edited artifact or a foreign writer does. The row it
        produces is still refused, by the export contract here and by the
        database in ``PostgresCredentialConstraintTests``.
        """
        from provider_telemetry import models, repository
        from provider_telemetry.values import valid

        event = models.AttemptEvent(
            event_id="00000000-0000-4000-8000-00000000000d",
            subject_kind=models.SUBJECT_SDK_INVOCATION,
            subject_id="00000000-0000-4000-8000-00000000000e",
            call_id="00000000-0000-4000-8000-00000000000f",
            telemetry_run_id="00000000-0000-4000-8000-000000000010",
            event_kind=models.EVENT_COMPLETED,
            event_ordinal=1,
            observed_at=models.utc_now(),
            observation=models.ProviderObservation(
                provider_response_id=valid("Bearer_abcdefghijkl")
            ),
        )
        # The dataclass layer accepts it: it validates *shape*, not provenance.
        row = dict(zip(repository.EVENT_COLUMNS, repository.event_row(event)))
        self.assertEqual(row["provider_response_id"], "Bearer_abcdefghijkl")
        self.assertEqual(row["provider_response_id_status"], "valid")

        # The export contract does not.
        from tools import provider_attempt_telemetry_export as export_tool

        report = export_tool.validate_values({repository.EVENT_TABLE: [row]})
        self.assertFalse(report["complete"])
        self.assertTrue(
            any(p.startswith("unsafe_stored_value:provider_response_id:redacted")
                for p in report["problems"]),
            report["problems"],
        )
        # The diagnostic names the column and the row, never the credential.
        for problem in report["problems"]:
            self.assertNotIn("Bearer", problem)

    # ── Mutation 4: bypass the export's validation ──

    def test_mutation_bypassing_export_validation_lets_a_restore_accept_it(self):
        """Removing the export check must make a restore stop refusing."""
        from provider_telemetry import repository
        from tools import provider_attempt_telemetry_export as export_tool
        from tools import provider_attempt_telemetry_restore as restore_tool

        row = {column: None for column in repository.READ_COLUMNS[repository.EVENT_TABLE]}
        row.update(
            {
                "event_id": "00000000-0000-4000-8000-000000000011",
                "provider_response_id": "Bearer_abcdefghijkl",
                "provider_response_id_status": "valid",
            }
        )
        rows = {table: [] for table in repository.TELEMETRY_TABLES}
        rows[repository.EVENT_TABLE] = [row]

        report = export_tool.validate_values(rows)
        self.assertFalse(report["complete"])

        artifact = {
            "export_format": export_tool.EXPORT_FORMAT,
            "export_version": export_tool.EXPORT_VERSION,
            "schema_version": __import__(
                "provider_telemetry.models", fromlist=["x"]
            ).TELEMETRY_SCHEMA_VERSION,
            "selector": {name: "" for name in export_tool.Selector.FIELDS},
            "columns": {
                table: list(repository.READ_COLUMNS[table])
                for table in repository.TELEMETRY_TABLES
            },
            "column_schema_digest": export_tool.column_schema_digest(),
            "relations": {},
            "chains": {"complete": True, "problems": []},
            "complete": True,
            "rows": rows,
        }
        artifact["selector_bound_digest"] = export_tool.digest(
            {
                "selector": artifact["selector"],
                "export_version": artifact["export_version"],
                "schema_version": artifact["schema_version"],
                "column_schema_digest": artifact["column_schema_digest"],
                "rows": rows,
            }
        )

        problems = restore_tool.validate_artifact(artifact)
        self.assertTrue(
            any(p.startswith("value:unsafe_stored_value") for p in problems), problems
        )

        # The mutation: the restore's value gate is removed.
        original = export_tool.validate_values
        export_tool.validate_values = lambda relations: {
            "checked_values": 0, "problems": [], "complete": True
        }
        try:
            mutated = restore_tool.validate_artifact(artifact)
        finally:
            export_tool.validate_values = original
        self.assertFalse(
            any(p.startswith("value:") for p in mutated),
            "removing the export value gate must let the artifact through",
        )


# ═══════════════════════════ PostgreSQL ═══════════════════════════


class PostgresCredentialConstraintTests(unittest.TestCase):
    """The database refuses these values with no Python in the picture."""

    @classmethod
    def setUpClass(cls):
        pytest.importorskip("psycopg")
        from tests import provider_telemetry_pg_support as pg_support
        from tools import provider_attempt_telemetry_migrate as migrate_tool

        cls.pg_support = pg_support
        cls.pg_support.dsn()
        cls.pg_support.provision_roles()
        cls.schema = cls.pg_support.fresh_schema()
        code, payload = cls.pg_support.apply(cls.schema)
        if code != migrate_tool.EXIT_OK:  # pragma: no cover - harness failure
            cls.pg_support.drop_schema(cls.schema)
            raise AssertionError(f"migration did not apply: {payload}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "schema"):
            cls.pg_support.drop_schema(cls.schema)

    def _writer(self):
        conn = self.pg_support.connect_as(
            self.pg_support.WRITER_ROLE, self.schema, autocommit=False
        )
        self.addCleanup(conn.close)
        return conn

    def _run(self, conn) -> str:
        import uuid

        from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION

        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (run_id, TELEMETRY_SCHEMA_VERSION, "b" * 64),
        )
        return run_id

    def _insert(self, conn, run_id, column, value):
        from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION

        conn.execute(
            f"INSERT INTO provider_attempt_event (event_id, subject_kind, subject_id, "
            f"call_id, telemetry_run_id, event_kind, event_ordinal, is_terminal, "
            f"observed_at, {column}, {column}_status, response_metadata_fingerprint, "
            f"schema_version) VALUES (gen_random_uuid(), 'sdk_invocation', "
            f"gen_random_uuid(), gen_random_uuid(), %s::uuid, 'completed', 1, true, "
            f"now(), %s, 'valid', %s, %s)",
            (run_id, value, "c" * 64, TELEMETRY_SCHEMA_VERSION),
        )

    def test_the_audit_example_is_refused_by_postgresql(self):
        import psycopg

        conn = self._writer()
        run_id = self._run(conn)
        with self.assertRaises(psycopg.errors.CheckViolation) as caught:
            self._insert(conn, run_id, "provider_response_id", "Bearer_abcdefghijkl")
        self.assertIn("ck_pae_no_credential_shape", str(caught.exception))
        conn.rollback()

    def test_every_credential_shape_the_grammar_admits_is_refused_by_postgresql(self):
        """Only the values the *column grammar* would otherwise accept.

        The rest are already refused by ck_pae_safe_grammars; this asserts the
        new constraint covers precisely the gap the positive grammar leaves.
        """
        import psycopg

        grammar = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_\-]{0,127}\Z")
        admitted = [
            (label, value)
            for label, value in CREDENTIAL_VALUES
            if grammar.match(value)
        ]
        self.assertGreaterEqual(len(admitted), 8, "the gap under test is not covered")

        conn = self._writer()
        run_id = self._run(conn)
        conn.commit()
        for label, value in admitted:
            with self.subTest(case=label):
                conn.execute("SAVEPOINT credential_case")
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self._insert(conn, run_id, "provider_response_id", value)
                conn.execute("ROLLBACK TO SAVEPOINT credential_case")
        conn.rollback()

    def test_the_constraint_covers_every_provider_sourced_column(self):
        import psycopg

        conn = self._writer()
        run_id = self._run(conn)
        conn.commit()
        for column in ("provider_response_id", "provider_request_id",
                       "effective_model", "stop_reason"):
            with self.subTest(column=column):
                conn.execute("SAVEPOINT column_case")
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self._insert(conn, run_id, column, "Bearer_abcdefghijkl")
                conn.execute("ROLLBACK TO SAVEPOINT column_case")
        conn.rollback()

    def test_legitimate_identifiers_are_still_insertable(self):
        conn = self._writer()
        run_id = self._run(conn)
        for value in SAFE_RESPONSE_IDS:
            with self.subTest(response_id=value):
                self._insert(conn, run_id, "provider_response_id", value)
        for value in SAFE_MODELS:
            with self.subTest(model=value):
                self._insert(conn, run_id, "effective_model", value)
        for value in SAFE_STOP_REASONS:
            with self.subTest(stop_reason=value):
                self._insert(conn, run_id, "stop_reason", value)
        conn.rollback()

    def test_without_the_constraint_the_value_is_storable(self):
        """Bounded mutation, at the database layer."""
        import psycopg

        from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION

        schema = self.pg_support.fresh_schema()
        try:
            from tools import provider_attempt_telemetry_migrate as migrate_tool

            self.assertEqual(
                self.pg_support.apply(schema)[0], migrate_tool.EXIT_OK
            )
            owner = self.pg_support.superuser()
            self.addCleanup(owner.close)
            owner.execute(psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(schema)))
            owner.execute(psycopg.sql.SQL("SET ROLE {}").format(
                psycopg.sql.Identifier(self.pg_support.OWNER_ROLE)))
            owner.execute(
                "ALTER TABLE provider_attempt_event "
                "DROP CONSTRAINT ck_pae_no_credential_shape"
            )

            conn = self.pg_support.connect_as(
                self.pg_support.WRITER_ROLE, schema, autocommit=False
            )
            try:
                run_id = self._run(conn)
                self._insert(conn, run_id, "provider_response_id", "Bearer_abcdefghijkl")
                stored = conn.execute(
                    "SELECT provider_response_id FROM provider_attempt_event"
                ).fetchone()[0]
                self.assertEqual(
                    stored, "Bearer_abcdefghijkl",
                    "the mutation must reproduce the pre-remediation behavior",
                )
                conn.rollback()
            finally:
                conn.close()
            del TELEMETRY_SCHEMA_VERSION
        finally:
            self.pg_support.drop_schema(schema)

    def test_a_reapply_rejects_the_dropped_credential_constraint(self):
        import psycopg

        from tools import provider_attempt_telemetry_migrate as migrate_tool

        schema = self.pg_support.fresh_schema()
        try:
            self.assertEqual(self.pg_support.apply(schema)[0], migrate_tool.EXIT_OK)
            owner = self.pg_support.superuser()
            self.addCleanup(owner.close)
            owner.execute(psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(schema)))
            owner.execute(psycopg.sql.SQL("SET ROLE {}").format(
                psycopg.sql.Identifier(self.pg_support.OWNER_ROLE)))
            owner.execute(
                "ALTER TABLE provider_attempt_event "
                "DROP CONSTRAINT ck_pae_no_credential_shape"
            )
            code, payload = self.pg_support.apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn("constraint contract violated", payload["error"])
            self.assertIn("ck_pae_no_credential_shape", payload["error"])
        finally:
            self.pg_support.drop_schema(schema)


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
