"""A strict run cannot certify work it never did.

The finding: a strict run could declare four expected phases, create zero calls,
and reconcile ``complete``/certified. ``expected_phases`` was written to the run
row and never read back by anything — so reconciliation asked "is there an
attempt without a terminal event?", found none because there were no attempts at
all, and reported a clean run. Absence of evidence was being read as evidence of
completeness, which is precisely the failure mode a paired experiment cannot
tolerate.

The remediation reads the manifest back *from the database* at the run-end
barrier and reconciles it against what actually landed:

* a declared phase with no call is ``missing_expected_phase``;
* a declared manifest with no calls at all is ``expected_work_not_started``;
* a call envelope with no SDK invocation is ``call_without_invocation``;
* an invocation with no HTTP attempt is ``invocation_without_http_attempt``
  unless it carries a durable authorized disposition — a ``skipped`` or
  ``cancelled`` terminal, or a strict start that refused before transport;
* a call in a phase nobody declared is ``unexpected_call_phase``;
* a ``complete`` verdict must bind the manifest digest, and PostgreSQL refuses
  one that does not;
* an earlier unresolved verdict cannot be hidden by a later clean one, and no
  verdict is ever overwritten — the history is append-only and stays readable.

Every assertion below reconciles against a real PostgreSQL database through the
real :class:`TelemetrySession`.
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
asyncpg = pytest.importorskip("asyncpg")

from provider_telemetry import service  # noqa: E402
from provider_telemetry.delivery import EventDelivery  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    POSTURE_STRICT,
    RECONCILIATION_COMPLETE,
    RECONCILIATION_INCOMPLETE,
    RECONCILIATION_UNCERTIFIED,
    TELEMETRY_SCHEMA_VERSION,
    TelemetryRunRecord,
)
from tools import provider_attempt_telemetry_migrate as migrate_tool  # noqa: E402
from tests import provider_telemetry_pg_support as pg_support  # noqa: E402

FINGERPRINT = "d" * 64
PHASES = ("classify", "research", "audit", "decide")


class _Sink:
    """A sink over a real asyncpg pool against the disposable schema.

    The reconciliation queries are what is under test, so they run against a
    genuine cluster; the write path is the ordinary repository sink.
    """

    def __init__(self, pool) -> None:
        self.pool = pool
        self.events = []

    async def _pool(self):
        return self.pool

    async def append_start(self, table, record):
        return None

    async def append_event(self, event):
        from provider_telemetry import repository

        table = (
            repository.RUN_EVENT_TABLE
            if type(event).__name__ == "RunEvent"
            else repository.EVENT_TABLE
        )
        row = repository.ROW_BUILDERS[table](event)
        async with self.pool.acquire() as conn:
            await conn.execute(repository.insert_sql(table), *row)
        self.events.append(event)


class ExpectedWorkReconciliationTests(unittest.IsolatedAsyncioTestCase):
    """The eleven scenarios the remediation has to distinguish."""

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

    async def asyncSetUp(self):
        parameters = pg_support.role_parameters(pg_support.WRITER_ROLE)
        self.pool = await asyncpg.create_pool(
            host=parameters.get("host"),
            port=int(parameters.get("port") or 5432),
            database=parameters.get("dbname"),
            user=parameters.get("user"),
            password=parameters.get("password") or None,
            server_settings={"search_path": self.schema},
            min_size=1,
            max_size=4,
        )
        self.addAsyncCleanup(self.pool.close)

    # ── construction helpers, all raw SQL: Python is not the thing under test ──

    async def _session(self, phases=PHASES):
        run = TelemetryRunRecord(
            posture=POSTURE_STRICT,
            telemetry_required=True,
            entry_point="cli_workflow",
            expected_phases=tuple(phases),
            runtime_fingerprint=FINGERPRINT,
        )
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
                "telemetry_required, entry_point, schema_version, "
                "runtime_fingerprint, expected_phases, started_at) "
                "VALUES ($1::uuid, 'strict', true, 'cli_workflow', $2, $3, $4, now())",
                run.telemetry_run_id, TELEMETRY_SCHEMA_VERSION, FINGERPRINT,
                list(run.expected_phases),
            )
        sink = _Sink(self.pool)
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=64)
        await delivery.start()
        session = service.TelemetrySession(
            run_record=run, sink=sink, delivery=delivery
        )
        return session

    async def _call(self, run_id, phase):
        call_id = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO provider_telemetry_call (call_id, telemetry_run_id, "
                "posture, entry_point, phase, requested_provider, requested_model, "
                "request_config_fingerprint, routing_decision_fingerprint, "
                "candidate_count, started_at) VALUES ($1::uuid, $2::uuid, 'strict', "
                "'cli_workflow', $3, 'anthropic', 'm', $4, $4, 1, now())",
                call_id, run_id, phase, FINGERPRINT,
            )
        return call_id

    async def _invocation(self, run_id, call_id, *, kind="provider_call", ordinal=1):
        invocation_id = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO provider_sdk_invocation (invocation_id, call_id, "
                "telemetry_run_id, posture, entry_point, invocation_kind, provider, "
                "requested_model, candidate_ordinal, retry_ordinal, attempt_ordinal, "
                "breaker_state_before, breaker_snapshot_status_before, "
                "request_config_fingerprint, routing_decision_fingerprint, started_at) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'strict', 'cli_workflow', $4, "
                "'anthropic', 'm', 1, 1, $5, 'unknown', 'unknown', $6, $6, now())",
                invocation_id, call_id, run_id, kind, ordinal, FINGERPRINT,
            )
        return invocation_id

    async def _attempt(self, run_id, call_id, invocation_id, *, ordinal=1):
        attempt_id = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO provider_attempt (attempt_id, invocation_id, call_id, "
                "telemetry_run_id, posture, provider, requested_model, "
                "http_retry_ordinal, request_started_at) VALUES ($1::uuid, $2::uuid, "
                "$3::uuid, $4::uuid, 'strict', 'anthropic', 'm', $5, now())",
                attempt_id, invocation_id, call_id, run_id, ordinal,
            )
        return attempt_id

    async def _terminal(self, run_id, call_id, subject_id, *, kind="completed",
                        subject_kind="http_attempt", failure_class="", ordinal=1):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO provider_attempt_event (event_id, subject_kind, "
                "subject_id, call_id, telemetry_run_id, event_kind, event_ordinal, "
                "is_terminal, observed_at, failure_class, "
                "response_metadata_fingerprint, schema_version) "
                "VALUES (gen_random_uuid(), $1, $2::uuid, $3::uuid, $4::uuid, $5, $6, "
                "true, now(), $7, $8, $9)",
                subject_kind, subject_id, call_id, run_id, kind, ordinal,
                failure_class, FINGERPRINT, TELEMETRY_SCHEMA_VERSION,
            )

    async def _complete_phase(self, session, phase):
        """One fully realized phase: call → invocation → attempt → terminals."""
        run_id = session.telemetry_run_id
        call_id = await self._call(run_id, phase)
        invocation_id = await self._invocation(run_id, call_id)
        attempt_id = await self._attempt(run_id, call_id, invocation_id)
        await self._terminal(run_id, call_id, attempt_id)
        await self._terminal(
            run_id, call_id, invocation_id, subject_kind="sdk_invocation"
        )
        return call_id, invocation_id, attempt_id

    # ── 1 ──

    async def test_four_expected_phases_and_zero_calls_is_incomplete(self):
        """The finding, exactly as written."""
        session = await self._session(PHASES)
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertNotEqual(attestation.reconciliation_status, RECONCILIATION_COMPLETE)
        self.assertIn("expected_work_not_started", attestation.failures)
        self.assertIn("missing_expected_phase", attestation.failures)
        self.assertEqual(attestation.expected_phases, PHASES)
        self.assertEqual(attestation.missing_phases, tuple(sorted(PHASES)))

    # ── 2 ──

    async def test_one_missing_expected_phase_is_incomplete(self):
        session = await self._session(PHASES)
        for phase in PHASES[:-1]:
            await self._complete_phase(session, phase)
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertIn("missing_expected_phase", attestation.failures)
        self.assertNotIn("expected_work_not_started", attestation.failures)
        self.assertEqual(attestation.missing_phases, (PHASES[-1],))

    # ── 3 ──

    async def test_a_call_envelope_without_an_invocation_is_incomplete(self):
        session = await self._session(("classify",))
        await self._call(session.telemetry_run_id, "classify")
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertIn("call_without_invocation", attestation.failures)
        self.assertEqual(attestation.calls_without_invocation, 1)

    # ── 4 ──

    async def test_an_invocation_without_an_http_attempt_is_incomplete(self):
        session = await self._session(("classify",))
        run_id = session.telemetry_run_id
        call_id = await self._call(run_id, "classify")
        invocation_id = await self._invocation(run_id, call_id)
        await self._terminal(
            run_id, call_id, invocation_id, subject_kind="sdk_invocation"
        )
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertIn("invocation_without_http_attempt", attestation.failures)
        self.assertEqual(attestation.invocations_without_attempt, 1)

    # ── 5 ──

    async def test_an_authorized_governance_no_call_result_is_accepted(self):
        """A skipped candidate is a durable, authorized reason for no attempt."""
        session = await self._session(("classify",))
        run_id = session.telemetry_run_id
        call_id = await self._call(run_id, "classify")
        invocation_id = await self._invocation(
            run_id, call_id, kind="skipped_candidate"
        )
        await self._terminal(
            run_id, call_id, invocation_id, kind="skipped",
            subject_kind="sdk_invocation",
        )
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertNotIn("invocation_without_http_attempt", attestation.failures)
        self.assertNotIn("call_without_invocation", attestation.failures)
        self.assertNotIn("missing_expected_phase", attestation.failures)
        self.assertEqual(attestation.reconciliation_status, RECONCILIATION_COMPLETE)
        self.assertTrue(attestation.certified)

    # ── 6 ──

    async def test_a_strict_start_persistence_failure_is_an_accepted_disposition(self):
        """Blocked before transport, and the block is itself durable.

        A strict start that refuses leaves no ``provider_attempt`` row by
        design — that is the fail-closed guarantee. The invocation's terminal
        event carries the failure class, which is what distinguishes it from an
        invocation whose attempt row was lost.
        """
        session = await self._session(("classify",))
        run_id = session.telemetry_run_id
        call_id = await self._call(run_id, "classify")
        invocation_id = await self._invocation(run_id, call_id)
        await self._terminal(
            run_id, call_id, invocation_id,
            kind="provider_failure", subject_kind="sdk_invocation",
            failure_class=service.STRICT_START_REFUSED_CLASS,
        )
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertNotIn("invocation_without_http_attempt", attestation.failures)
        self.assertEqual(attestation.invocations_without_attempt, 0)

    # ── 7 ──

    async def test_the_exact_expected_workload_reconciles_complete(self):
        session = await self._session(PHASES)
        for phase in PHASES:
            await self._complete_phase(session, phase)
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertEqual(attestation.failures, ())
        self.assertEqual(attestation.reconciliation_status, RECONCILIATION_COMPLETE)
        self.assertTrue(attestation.certified)
        self.assertEqual(attestation.missing_phases, ())
        self.assertEqual(attestation.observed_calls, len(PHASES))
        # And the claim is bound to the manifest it is about.
        self.assertRegex(attestation.expected_work_digest, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(
            attestation.expected_work_digest, session.expected_work_digest()
        )

    # ── 8 ──

    async def test_an_unexpected_extra_call_is_not_certified(self):
        session = await self._session(("classify",))
        await self._complete_phase(session, "classify")
        await self._complete_phase(session, "decide")  # never declared
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertIn("unexpected_call_phase", attestation.failures)

    # ── 9 ──

    async def test_a_duplicate_call_envelope_is_refused_by_the_database(self):
        session = await self._session(("classify",))
        run_id = session.telemetry_run_id
        call_id = await self._call(run_id, "classify")
        with self.assertRaises(asyncpg.UniqueViolationError):
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO provider_telemetry_call (call_id, telemetry_run_id, "
                    "posture, entry_point, phase, requested_provider, requested_model, "
                    "request_config_fingerprint, routing_decision_fingerprint, "
                    "candidate_count, started_at) VALUES ($1::uuid, $2::uuid, "
                    "'strict', 'cli_workflow', 'classify', 'anthropic', 'm', $3, $3, "
                    "1, now())",
                    call_id, run_id, FINGERPRINT,
                )
        await session.delivery.aclose(drain_timeout=5.0)

    # ── 10 ──

    async def test_reconciling_before_the_drain_completes_is_not_certified(self):
        """A reconciliation that cannot drain has not observed the whole run."""
        from tests.provider_telemetry_support import BlockingSink

        run = TelemetryRunRecord(
            posture=POSTURE_STRICT, telemetry_required=True,
            entry_point="cli_workflow", expected_phases=("classify",),
            runtime_fingerprint=FINGERPRINT,
        )
        sink = BlockingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=8)
        await delivery.start()
        session = service.TelemetrySession(
            run_record=run, sink=sink, delivery=delivery
        )
        from provider_telemetry.models import AttemptEvent, SUBJECT_SDK_INVOCATION

        session.submit_event(
            AttemptEvent(
                event_id=str(uuid.uuid4()),
                subject_kind=SUBJECT_SDK_INVOCATION,
                subject_id=str(uuid.uuid4()),
                call_id=str(uuid.uuid4()),
                telemetry_run_id=run.telemetry_run_id,
                event_kind="completed",
                event_ordinal=1,
                observed_at=service.utc_now(),
            )
        )
        attestation = await session.reconcile(drain_timeout=0.2)
        self.assertFalse(attestation.certified)
        self.assertNotEqual(attestation.reconciliation_status, RECONCILIATION_COMPLETE)
        sink.release.set()

    # ── 11 ──

    async def test_a_complete_result_cannot_hide_an_earlier_unresolved_one(self):
        session = await self._session(("classify",))
        run_id = session.telemetry_run_id

        # First barrier: the work is not done, and the verdict says so.
        first = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(first.certified)
        self.assertIn("missing_expected_phase", first.failures)

        # The work then happens, and a second barrier runs on a fresh session
        # bound to the same run — the shape a retried or resumed worker has.
        await self._complete_phase(session, "classify")
        second_delivery = EventDelivery(
            _Sink(self.pool), posture=POSTURE_STRICT, capacity=64
        )
        await second_delivery.start()
        second_session = service.TelemetrySession(
            run_record=session.run, sink=_Sink(self.pool), delivery=second_delivery
        )
        second = await second_session.reconcile(drain_timeout=5.0)

        self.assertIn("earlier_unresolved_reconciliation", second.failures)
        self.assertFalse(second.certified)
        self.assertEqual(second.reconciliation_status, RECONCILIATION_UNCERTIFIED)

        # History is preserved: both verdicts are readable, neither replaced.
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT reconciliation_status, expected_work_digest "
                "FROM provider_telemetry_run_event "
                "WHERE telemetry_run_id = $1::uuid AND event_kind = 'reconciliation' "
                "ORDER BY run_event_sequence",
                run_id,
            )
        statuses = [row["reconciliation_status"] for row in rows]
        self.assertEqual(len(statuses), 2, statuses)
        self.assertNotIn(RECONCILIATION_COMPLETE, statuses)
        # Every verdict names the manifest it was about.
        for row in rows:
            self.assertRegex(row["expected_work_digest"], r"\A[0-9a-f]{64}\Z")


class ManifestBindingTests(unittest.IsolatedAsyncioTestCase):
    """A completeness claim must name the work it is a claim about."""

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

    def test_the_model_refuses_an_unqualified_complete(self):
        from provider_telemetry.models import (
            RUN_EVENT_RECONCILIATION,
            RunEvent,
            TelemetryRecordError,
            utc_now,
        )

        with self.assertRaises(TelemetryRecordError):
            RunEvent(
                event_id=str(uuid.uuid4()),
                telemetry_run_id=str(uuid.uuid4()),
                event_kind=RUN_EVENT_RECONCILIATION,
                worker_id="w",
                posture=POSTURE_STRICT,
                observed_at=utc_now(),
                reconciliation_status=RECONCILIATION_COMPLETE,
            )

    def test_postgresql_refuses_an_unqualified_complete_independently(self):
        """Python bypassed entirely: a raw INSERT by the writer role."""
        conn = pg_support.connect_as(
            pg_support.WRITER_ROLE, self.schema, autocommit=False
        )
        self.addCleanup(conn.close)
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (run_id, TELEMETRY_SCHEMA_VERSION, FINGERPRINT),
        )
        with self.assertRaises(psycopg.errors.CheckViolation) as caught:
            conn.execute(
                "INSERT INTO provider_telemetry_run_event (event_id, telemetry_run_id, "
                "event_kind, posture, observed_at, reconciliation_status, drain_status) "
                "VALUES (gen_random_uuid(), %s::uuid, 'reconciliation', 'strict', now(), "
                "'complete', 'drained')",
                (run_id,),
            )
        self.assertIn("ck_ptre_complete_binds_manifest", str(caught.exception))
        conn.rollback()

    def test_a_malformed_digest_is_refused(self):
        conn = pg_support.connect_as(
            pg_support.WRITER_ROLE, self.schema, autocommit=False
        )
        self.addCleanup(conn.close)
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO provider_telemetry_run (telemetry_run_id, posture, "
            "telemetry_required, entry_point, schema_version, runtime_fingerprint, "
            "started_at) VALUES (%s::uuid, 'strict', true, 'cli_workflow', %s, %s, now())",
            (run_id, TELEMETRY_SCHEMA_VERSION, FINGERPRINT),
        )
        with self.assertRaises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO provider_telemetry_run_event (event_id, telemetry_run_id, "
                "event_kind, posture, observed_at, reconciliation_status, "
                "drain_status, expected_work_digest) "
                "VALUES (gen_random_uuid(), %s::uuid, 'reconciliation', 'strict', "
                "now(), 'complete', 'drained', 'not-a-digest')",
                (run_id,),
            )
        conn.rollback()

    def test_the_digest_distinguishes_two_different_manifests(self):
        from provider_telemetry.delivery import NullDelivery
        from provider_telemetry.repository import NullTelemetrySink

        digests = set()
        for phases in ((), ("classify",), ("classify", "audit"), ("audit", "classify")):
            run = TelemetryRunRecord(
                posture=POSTURE_STRICT, telemetry_required=True,
                entry_point="cli_workflow", expected_phases=phases,
                telemetry_run_id="00000000-0000-4000-8000-000000000041",
            )
            session = service.TelemetrySession(
                run_record=run, sink=NullTelemetrySink(), delivery=NullDelivery()
            )
            digests.add(session.expected_work_digest())
        # Order is part of the manifest: two phases in two orders are two
        # different declarations of what the run intended to do.
        self.assertEqual(len(digests), 4)

    def test_a_reapply_rejects_the_dropped_manifest_constraint(self):
        schema = pg_support.fresh_schema()
        try:
            self.assertEqual(pg_support.apply(schema)[0], migrate_tool.EXIT_OK)
            owner = pg_support.superuser()
            self.addCleanup(owner.close)
            owner.execute(psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(schema)))
            owner.execute(psycopg.sql.SQL("SET ROLE {}").format(
                psycopg.sql.Identifier(pg_support.OWNER_ROLE)))
            owner.execute(
                "ALTER TABLE provider_telemetry_run_event "
                "DROP CONSTRAINT ck_ptre_complete_binds_manifest"
            )
            code, payload = pg_support.apply(schema)
            self.assertEqual(code, migrate_tool.EXIT_FAILED, payload)
            self.assertIn("ck_ptre_complete_binds_manifest", payload["error"])
        finally:
            pg_support.drop_schema(schema)


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
