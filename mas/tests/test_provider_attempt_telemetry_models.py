"""Record contracts: ordinals, fingerprints, timestamps, breaker atomicity.

The Python half of the value contract the database also enforces. Both halves
exist on purpose: the database check catches a writer that bypassed these
dataclasses, and these dataclasses catch a malformed record before it costs a
round trip.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry.identity import TelemetryIdentity  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    EVENT_COMPLETED,
    EVENT_OBSERVATION,
    INVOCATION_PROVIDER_CALL,
    OBSERVABLE_PROVIDER_FIELDS,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    SUBJECT_HTTP_ATTEMPT,
    SUBJECT_SDK_INVOCATION,
    TELEMETRY_SCHEMA_VERSION,
    AttemptEvent,
    BreakerSnapshot,
    HttpAttemptRecord,
    ProviderObservation,
    RunEvent,
    SdkInvocationRecord,
    TelemetryCallRecord,
    TelemetryRecordError,
    TelemetryRunRecord,
    UNKNOWN_BREAKER,
    canonical_fingerprint,
    new_identity,
    utc_now,
)
from provider_telemetry.values import ABSENT, VALUE_VALID, valid  # noqa: E402

HEX = "a" * 64
NOW = datetime(2026, 7, 31, 9, 15, 30, 123456, tzinfo=timezone.utc)


def _identity() -> TelemetryIdentity:
    return TelemetryIdentity(entry_point="cli_workflow", run_id="run-1")


def _invocation(**overrides) -> SdkInvocationRecord:
    values = dict(
        invocation_id=new_identity(),
        call_id=new_identity(),
        telemetry_run_id=new_identity(),
        posture=POSTURE_OBSERVATIONAL,
        identity=_identity(),
        worker_id="host:1:abc",
        invocation_kind=INVOCATION_PROVIDER_CALL,
        provider="anthropic",
        requested_model="claude-sonnet-4-6",
        candidate_ordinal=1,
        retry_ordinal=1,
        attempt_ordinal=1,
        request_config_fingerprint=HEX,
        routing_decision_fingerprint=HEX,
        started_at=NOW,
    )
    values.update(overrides)
    return SdkInvocationRecord(**values)


def _attempt(**overrides) -> HttpAttemptRecord:
    values = dict(
        attempt_id=new_identity(),
        invocation_id=new_identity(),
        call_id=new_identity(),
        telemetry_run_id=new_identity(),
        posture=POSTURE_OBSERVATIONAL,
        worker_id="host:1:abc",
        provider="anthropic",
        requested_model="claude-sonnet-4-6",
        http_retry_ordinal=1,
        request_started_at=NOW,
    )
    values.update(overrides)
    return HttpAttemptRecord(**values)


def _event(**overrides) -> AttemptEvent:
    values = dict(
        event_id=new_identity(),
        subject_kind=SUBJECT_SDK_INVOCATION,
        subject_id=new_identity(),
        call_id=new_identity(),
        telemetry_run_id=new_identity(),
        event_kind=EVENT_COMPLETED,
        event_ordinal=1,
        observed_at=NOW,
    )
    values.update(overrides)
    return AttemptEvent(**values)


class OrdinalTests(unittest.TestCase):
    def test_ordinals_are_one_based(self):
        # A zero ordinal made "the first attempt" and "no attempt"
        # indistinguishable in the previous design.
        for field in ("candidate_ordinal", "retry_ordinal", "attempt_ordinal"):
            with self.subTest(field=field):
                with self.assertRaises(TelemetryRecordError):
                    _invocation(**{field: 0})
                with self.assertRaises(TelemetryRecordError):
                    _invocation(**{field: -1})

    def test_an_http_retry_ordinal_starts_at_one(self):
        with self.assertRaises(TelemetryRecordError):
            _attempt(http_retry_ordinal=0)
        self.assertEqual(_attempt(http_retry_ordinal=3).http_retry_ordinal, 3)

    def test_a_bool_is_not_an_ordinal(self):
        with self.assertRaises(TelemetryRecordError):
            _attempt(http_retry_ordinal=True)

    def test_event_ordinals_are_one_based(self):
        with self.assertRaises(TelemetryRecordError):
            _event(event_ordinal=0)


class FingerprintTests(unittest.TestCase):
    def test_a_fingerprint_must_be_64_hex_characters(self):
        for bad in ("a" * 63, "a" * 65, "z" * 64, "not-a-digest", "0x" + "a" * 62):
            with self.subTest(value=bad):
                with self.assertRaises(TelemetryRecordError):
                    _invocation(request_config_fingerprint=bad)

    def test_uppercase_hex_is_normalized_rather_than_refused(self):
        # The database CHECK requires lowercase; normalizing here means the two
        # agree instead of the writer discovering the mismatch at INSERT time.
        record = _invocation(request_config_fingerprint="A" * 64)
        self.assertEqual(record.request_config_fingerprint, "a" * 64)

    def test_an_empty_fingerprint_becomes_the_zero_digest(self):
        record = _invocation(request_config_fingerprint="")
        self.assertEqual(record.request_config_fingerprint, "0" * 64)

    def test_canonical_fingerprint_is_stable_and_order_independent(self):
        first = canonical_fingerprint({"b": 2, "a": 1})
        second = canonical_fingerprint({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")


class TimestampTests(unittest.TestCase):
    def test_a_naive_datetime_is_refused_not_assumed_utc(self):
        # Guessing the zone would silently corrupt the temporal ordering a
        # paired experiment depends on.
        with self.assertRaises(TelemetryRecordError):
            _attempt(request_started_at=datetime(2026, 7, 31, 9, 15, 30))

    def test_a_non_utc_aware_datetime_is_normalized(self):
        other = NOW.astimezone(timezone(timedelta(hours=5)))
        record = _attempt(request_started_at=other)
        self.assertEqual(record.request_started_at, NOW)
        self.assertEqual(record.request_started_at.tzinfo, timezone.utc)

    def test_a_required_timestamp_cannot_be_absent(self):
        with self.assertRaises(TelemetryRecordError):
            _attempt(request_started_at=None)


class BreakerSnapshotTests(unittest.TestCase):
    def test_an_unknown_snapshot_carries_neither_state_nor_count(self):
        snapshot = BreakerSnapshot.unknown()
        self.assertEqual(snapshot.state, "unknown")
        self.assertIsNone(snapshot.failure_count)
        self.assertEqual(snapshot.status, "unknown")

    def test_a_valid_snapshot_needs_both_a_state_and_a_count(self):
        snapshot = BreakerSnapshot.observed(state="open", failure_count=3)
        self.assertEqual(snapshot.state, "open")
        self.assertEqual(snapshot.failure_count, 3)
        with self.assertRaises(TelemetryRecordError):
            BreakerSnapshot(state="closed", failure_count=None, status="valid")
        with self.assertRaises(TelemetryRecordError):
            BreakerSnapshot(state="unknown", failure_count=0, status="valid")

    def test_an_unknown_snapshot_cannot_smuggle_a_fabricated_reading(self):
        # This is the exact shape the audit found: "closed, zero failures" for a
        # snapshot nobody took.
        with self.assertRaises(TelemetryRecordError):
            BreakerSnapshot(state="closed", failure_count=0, status="unknown")

    def test_a_negative_failure_count_is_refused(self):
        with self.assertRaises(TelemetryRecordError):
            BreakerSnapshot(state="closed", failure_count=-1, status="valid")


class ClosedVocabularyTests(unittest.TestCase):
    def test_unknown_vocabulary_values_are_refused(self):
        with self.assertRaises(TelemetryRecordError):
            _invocation(posture="semi-strict")
        with self.assertRaises(TelemetryRecordError):
            _invocation(invocation_kind="maybe_called")
        with self.assertRaises(TelemetryRecordError):
            _event(event_kind="sort_of_done")
        with self.assertRaises(TelemetryRecordError):
            _event(subject_kind="something_else")
        with self.assertRaises(TelemetryRecordError):
            _event(transport_outcome="probably")

    def test_terminal_kinds_are_identified_consistently(self):
        self.assertTrue(_event(event_kind=EVENT_COMPLETED).is_terminal)
        self.assertFalse(_event(event_kind=EVENT_OBSERVATION).is_terminal)


class RunRecordTests(unittest.TestCase):
    def test_strict_posture_requires_telemetry_to_be_required(self):
        with self.assertRaises(TelemetryRecordError):
            TelemetryRunRecord(posture=POSTURE_STRICT, telemetry_required=False)
        record = TelemetryRunRecord(posture=POSTURE_STRICT, telemetry_required=True)
        self.assertTrue(record.telemetry_required)

    def test_run_counters_must_be_nonnegative_integers(self):
        for field in ("started_events", "unmatched_starts", "dropped_events"):
            with self.subTest(field=field):
                with self.assertRaises(TelemetryRecordError):
                    RunEvent(
                        event_id=new_identity(),
                        telemetry_run_id=new_identity(),
                        event_kind="reconciliation",
                        worker_id="w",
                        posture=POSTURE_STRICT,
                        observed_at=NOW,
                        **{field: -1},
                    )

    def test_a_call_needs_at_least_one_candidate(self):
        with self.assertRaises(TelemetryRecordError):
            TelemetryCallRecord(
                call_id=new_identity(),
                telemetry_run_id=new_identity(),
                posture=POSTURE_OBSERVATIONAL,
                identity=_identity(),
                worker_id="w",
                requested_provider="anthropic",
                requested_model="m",
                request_config_fingerprint=HEX,
                routing_decision_fingerprint=HEX,
                candidate_count=0,
                started_at=NOW,
            )


class FallbackContractTests(unittest.TestCase):
    def test_a_fallback_candidate_must_name_what_it_fell_back_from(self):
        with self.assertRaises(TelemetryRecordError):
            _invocation(fallback_candidate=True)
        record = _invocation(
            fallback_candidate=True,
            fallback_from_provider="anthropic",
            fallback_from_model="claude-sonnet-4-6",
        )
        self.assertEqual(record.fallback_from_provider, "anthropic")


class ObservationTests(unittest.TestCase):
    def test_an_empty_observation_reports_itself_as_empty(self):
        self.assertTrue(ProviderObservation().is_empty)

    def test_any_valid_field_makes_an_observation_non_empty(self):
        self.assertFalse(ProviderObservation(input_tokens=valid(3)).is_empty)

    def test_every_observable_field_defaults_to_absent(self):
        observation = ProviderObservation()
        for name in OBSERVABLE_PROVIDER_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(observation, name), ABSENT)

    def test_a_raw_value_is_refused_where_a_provider_value_belongs(self):
        with self.assertRaises(TelemetryRecordError):
            ProviderObservation(input_tokens=3)

    def test_the_metadata_fingerprint_reflects_status_not_just_value(self):
        absent = ProviderObservation().metadata_fingerprint()
        present = ProviderObservation(input_tokens=valid(0)).metadata_fingerprint()
        # "absent" and "the provider said zero" must not fingerprint alike.
        self.assertNotEqual(absent, present)


class RequestPathTests(unittest.TestCase):
    def test_a_query_string_is_never_retained(self):
        record = _attempt(request_path="/v1/messages?key=SECRET")
        self.assertEqual(record.request_path, "/v1/messages")

    def test_the_method_is_normalized(self):
        self.assertEqual(_attempt(request_method="post").request_method, "POST")


class EventDefaultsTests(unittest.TestCase):
    def test_the_metadata_fingerprint_defaults_to_the_observation_digest(self):
        observation = ProviderObservation(input_tokens=valid(11))
        event = _event(observation=observation)
        self.assertEqual(
            event.response_metadata_fingerprint, observation.metadata_fingerprint()
        )

    def test_schema_version_is_stamped(self):
        self.assertEqual(_event().schema_version, TELEMETRY_SCHEMA_VERSION)

    def test_an_http_event_may_carry_transport_detail(self):
        event = _event(
            subject_kind=SUBJECT_HTTP_ATTEMPT,
            transport_outcome="response",
            http_status=valid(200),
        )
        self.assertEqual(event.http_status.status, VALUE_VALID)
        self.assertEqual(event.http_status.value, 200)

    def test_the_breaker_defaults_to_unknown_not_closed(self):
        self.assertIs(_event().breaker_after, UNKNOWN_BREAKER)
        self.assertIs(_invocation().breaker_before, UNKNOWN_BREAKER)

    def test_labels_are_bounded_and_single_line(self):
        event = _event(error_identity="a\nb  c" + "x" * 400)
        self.assertNotIn("\n", event.error_identity)
        self.assertLessEqual(len(event.error_identity), 256)


class UtcNowTests(unittest.TestCase):
    def test_the_package_clock_is_aware_utc(self):
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), timedelta(0))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
