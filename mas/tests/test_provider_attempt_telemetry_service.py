"""Postures, strict preflight, and run-end reconciliation.

Covers required regressions 22 and 23:

* strict preflight fails when telemetry is disabled or the sink is unhealthy
  (the schema/ACL half of that check is exercised against a genuine PostgreSQL
  cluster in ``test_provider_attempt_telemetry_pg``);
* the run-end drain and reconciliation detect missing and ambiguous events.

It also pins the claim that an observational run is never allowed to make: that
its telemetry is complete.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from provider_telemetry import repository, service  # noqa: E402
from provider_telemetry.delivery import EventDelivery, NullDelivery  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    DRAIN_DRAINED,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    RECONCILIATION_UNCERTIFIED,
    TelemetryRecordError,
    TelemetryRunRecord,
)
from tests.provider_telemetry_support import (  # noqa: E402
    AmbiguousSink,
    BlockingSink,
    FailingSink,
    RecordingSink,
)


class PostureConfigurationTests(unittest.TestCase):
    def setUp(self):
        for name in (
            config.PROVIDER_TELEMETRY_POSTURE_ENV,
            config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV,
        ):
            self.addCleanup(self._restore, name, __import__("os").environ.get(name))
            __import__("os").environ.pop(name, None)

    @staticmethod
    def _restore(name, value):
        import os

        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def _set(self, name, value):
        import os

        os.environ[name] = value

    def test_default_is_off(self):
        self.assertEqual(service.configured_posture(), service.POSTURE_OFF)
        self.assertFalse(service.telemetry_enabled())
        self.assertFalse(service.strict_mode_configured())

    def test_each_posture_is_selectable(self):
        for value in (POSTURE_OBSERVATIONAL, POSTURE_STRICT, service.POSTURE_OFF):
            with self.subTest(posture=value):
                self._set(config.PROVIDER_TELEMETRY_POSTURE_ENV, value)
                self.assertEqual(service.configured_posture(), value)

    def test_an_unknown_posture_falls_back_to_off_rather_than_guessing(self):
        self._set(config.PROVIDER_TELEMETRY_POSTURE_ENV, "strictish")
        self.assertEqual(service.configured_posture(), service.POSTURE_OFF)

    def test_the_legacy_boolean_flag_means_observational(self):
        self._set(config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV, "1")
        self.assertEqual(service.configured_posture(), POSTURE_OBSERVATIONAL)

    def test_the_posture_variable_wins_over_the_legacy_flag(self):
        self._set(config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV, "1")
        self._set(config.PROVIDER_TELEMETRY_POSTURE_ENV, service.POSTURE_OFF)
        self.assertEqual(service.configured_posture(), service.POSTURE_OFF)

    def test_strict_requires_telemetry_to_be_required(self):
        with self.assertRaises(TelemetryRecordError):
            TelemetryRunRecord(posture=POSTURE_STRICT, telemetry_required=False)


class StrictPreflightTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 22, in-process half."""

    async def test_preflight_fails_when_telemetry_is_disabled(self):
        result = await service.strict_preflight(RecordingSink())
        self.assertFalse(result.healthy)
        self.assertTrue(any(r.startswith("posture_is_") for r in result.reasons))

    async def test_preflight_fails_when_the_sink_is_unreachable(self):
        class Unreachable(RecordingSink):
            async def _pool(self):
                raise repository.ProviderTelemetryStorageUnavailable("no pool")

        result = await service.strict_preflight(Unreachable())
        self.assertFalse(result.healthy)
        self.assertTrue(
            any(r.startswith("sink_unavailable") for r in result.reasons), result.reasons
        )

    async def test_a_strict_run_refuses_to_start_when_preflight_fails(self):
        import os

        os.environ[config.PROVIDER_TELEMETRY_POSTURE_ENV] = POSTURE_STRICT
        self.addCleanup(os.environ.pop, config.PROVIDER_TELEMETRY_POSTURE_ENV, None)

        with self.assertRaises(service.StrictPreflightFailed):
            async with service.run_session(posture=POSTURE_STRICT, sink=RecordingSink()):
                raise AssertionError("the run body must never execute")


def _session(sink, *, posture=POSTURE_OBSERVATIONAL, delivery=None):
    return service.TelemetrySession(
        run_record=TelemetryRunRecord(
            posture=posture, telemetry_required=posture == POSTURE_STRICT
        ),
        sink=sink,
        delivery=delivery if delivery is not None else NullDelivery(),
    )


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 23: the barrier detects missing and ambiguous events."""

    async def test_an_unreachable_reconciliation_query_is_uncertified(self):
        # RecordingSink's pool is None, so the durable reconciliation cannot run.
        # An unverifiable run is uncertified — never quietly certified.
        session = _session(RecordingSink(), posture=POSTURE_STRICT)
        attestation = await session.reconcile(drain_timeout=1.0)
        self.assertIn("reconciliation_query_unavailable", attestation.failures)
        self.assertEqual(attestation.reconciliation_status, RECONCILIATION_UNCERTIFIED)
        self.assertFalse(attestation.certified)

    async def test_undurable_events_make_a_run_uncertified(self):
        sink = FailingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=8)
        await delivery.start()
        session = _session(sink, posture=POSTURE_STRICT, delivery=delivery)
        delivery.submit("event")

        attestation = await session.reconcile(drain_timeout=2.0)
        self.assertIn("undurable_events", attestation.failures)
        self.assertGreaterEqual(attestation.undurable_events, 1)
        self.assertFalse(attestation.certified)

    async def test_ambiguous_writes_make_a_run_uncertified(self):
        sink = AmbiguousSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=8)
        await delivery.start()
        session = _session(sink, posture=POSTURE_STRICT, delivery=delivery)
        delivery.submit("event")

        attestation = await session.reconcile(drain_timeout=2.0)
        self.assertIn("ambiguous_writes", attestation.failures)
        self.assertGreaterEqual(attestation.ambiguous_events, 1)
        self.assertFalse(attestation.certified)

    async def test_a_wedged_drain_is_reported_as_such(self):
        sink = BlockingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=8)
        await delivery.start()
        session = _session(sink, posture=POSTURE_STRICT, delivery=delivery)
        delivery.submit("event")

        attestation = await session.reconcile(drain_timeout=0.2)
        self.assertNotEqual(attestation.drain_status, DRAIN_DRAINED)
        self.assertTrue(any(f.startswith("drain_") for f in attestation.failures))
        self.assertFalse(attestation.certified)
        sink.release.set()

    async def test_a_failed_start_is_counted_against_the_run(self):
        session = _session(FailingSink(), posture=POSTURE_OBSERVATIONAL)
        # Observational: the failure is absorbed but never hidden.
        await session.persist_call_start(object())
        self.assertEqual(session.local_start_failures, 1)
        attestation = await session.reconcile(drain_timeout=1.0)
        self.assertIn("undurable_events", attestation.failures)

    async def test_attestation_payload_names_every_clause(self):
        session = _session(RecordingSink(), posture=POSTURE_STRICT)
        payload = (await session.reconcile(drain_timeout=1.0)).as_payload()
        for key in (
            "telemetry_required",
            "workers",
            "started_events",
            "terminal_events",
            "unmatched_starts",
            "undurable_events",
            "ambiguous_events",
            "drain_status",
            "reconciliation_status",
            "certified",
            "completeness_notice",
        ):
            with self.subTest(key=key):
                self.assertIn(key, payload)


class CompletenessClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_observational_run_never_claims_completeness(self):
        session = _session(RecordingSink(), posture=POSTURE_OBSERVATIONAL)
        health = session.health()
        self.assertFalse(health["completeness_guaranteed"])
        self.assertIn("NOT guaranteed", health["completeness_notice"])
        self.assertIn("Do not use for a paired experiment", health["completeness_notice"])

    async def test_a_strict_run_ties_its_claim_to_reconciliation(self):
        session = _session(RecordingSink(), posture=POSTURE_STRICT)
        health = session.health()
        # Even strict mode reports completeness_guaranteed=False here: the claim
        # is established by the run-end reconciliation, not by the posture.
        self.assertFalse(health["completeness_guaranteed"])
        self.assertIn("reconciliation", health["completeness_notice"])

    async def test_a_disabled_session_is_inert(self):
        session = service.NullTelemetrySession()
        await session.persist_attempt_start(object())
        await session.persist_invocation_start(object())
        await session.persist_call_start(object())
        self.assertFalse(session.submit_event(object()))
        attestation = await session.reconcile()
        self.assertFalse(attestation.certified)
        self.assertIn("telemetry_disabled", attestation.failures)

    async def test_run_session_with_posture_off_yields_a_null_session(self):
        import os

        os.environ.pop(config.PROVIDER_TELEMETRY_POSTURE_ENV, None)
        os.environ.pop(config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV, None)
        async with service.run_session() as session:
            self.assertIsInstance(session, service.NullTelemetrySession)
            self.assertIsNone(service.current_session())


class FingerprintTests(unittest.TestCase):
    def test_config_fingerprint_excludes_the_prompt(self):
        first = service.request_config_fingerprint(
            provider="anthropic", model="m", max_tokens=1, temperature=0.0,
            thinking_budget=0, max_retries=3, retry_delays=[1.0],
        )
        second = service.request_config_fingerprint(
            provider="anthropic", model="m", max_tokens=1, temperature=0.0,
            thinking_budget=0, max_retries=3, retry_delays=[1.0],
        )
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_routing_fingerprint_changes_with_the_decision(self):
        base = dict(provider="anthropic", model="m", candidate_ordinal=1, retry_ordinal=1)
        self.assertNotEqual(
            service.routing_decision_fingerprint(**base),
            service.routing_decision_fingerprint(**{**base, "candidate_ordinal": 2}),
        )

    def test_runtime_fingerprint_is_hex_and_stable(self):
        self.assertRegex(service.runtime_fingerprint(), r"^[0-9a-f]{64}$")
        self.assertEqual(service.runtime_fingerprint(), service.runtime_fingerprint())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
