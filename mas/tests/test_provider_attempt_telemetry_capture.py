"""Capture-failure isolation and metadata preservation.

Covers the audit's required regressions 4, 5 and 10:

* a telemetry extraction failure cannot convert provider success into failure;
* a provider property whose ``__str__`` raises is isolated;
* rich response metadata survives a later transformation failure.

Every hostile object below is one the audit reproduced. The assertions are about
what the *runtime* sees, not about what telemetry recorded — the point of
isolation is that the runtime sees nothing at all.
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import capture  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_OBSERVATION,
    EVENT_PROVIDER_FAILURE,
    EVENT_TRANSFORMATION_FAILURE,
    UNKNOWN_BREAKER,
)
from provider_telemetry.values import (  # noqa: E402
    VALUE_INVALID,
    VALUE_UNSUPPORTED,
    VALUE_VALID,
)
from tests.provider_telemetry_support import new_capture  # noqa: E402


class _HostileId:
    """A response whose ``id`` is a property that raises."""

    model = "claude-sonnet-4-6"
    stop_reason = "end_turn"

    @property
    def id(self):
        raise RuntimeError("provider object is hostile")

    class usage:  # noqa: N801 - mimics an SDK attribute namespace
        input_tokens = 11
        output_tokens = 7
        cache_read_input_tokens = 3
        cache_creation_input_tokens = 2


class _HostileStr:
    """A response whose ``__str__`` raises — the audit's exact counterexample."""

    id = "msg_ok"
    stop_reason = "end_turn"

    class _Model:
        def __str__(self):
            raise RuntimeError("__str__ is hostile")

    model = _Model()
    usage = None


class CaptureIsolationTests(unittest.TestCase):
    def setUp(self):
        capture.reset_capture_log_latch()

    def test_hostile_property_costs_one_field_not_the_observation(self):
        buffer = new_capture(None)
        with capture.capture_scope(buffer):
            observation = capture.observe_anthropic_response(_HostileId())
        # The field that raised is invalid; everything else is still captured.
        self.assertEqual(observation.provider_response_id.status, VALUE_INVALID)
        self.assertEqual(observation.effective_model.status, VALUE_VALID)
        self.assertEqual(observation.stop_reason.status, VALUE_VALID)
        self.assertEqual(observation.input_tokens.value, 11)
        self.assertEqual(observation.cache_creation_tokens.value, 2)

    def test_hostile_str_is_isolated(self):
        buffer = new_capture(None)
        with capture.capture_scope(buffer):
            observation = capture.observe_anthropic_response(_HostileStr())
        self.assertEqual(observation.provider_response_id.value, "msg_ok")
        # `model.__str__` raising must not escape and must not be stored.
        self.assertNotEqual(observation.effective_model.status, VALUE_VALID)
        self.assertIsNone(observation.effective_model.stored)

    def test_capture_failures_are_counted_not_raised(self):
        buffer = new_capture(None)
        with capture.capture_scope(buffer):
            capture.observe_anthropic_response(_HostileId())
        self.assertTrue(buffer.capture_failures)

    def test_guard_absorbs_exception_but_never_baseexception(self):
        with capture.guard("probe"):
            raise ValueError("telemetry-only failure")

        with self.assertRaises(asyncio.CancelledError):
            with capture.guard("probe"):
                raise asyncio.CancelledError()

        with self.assertRaises(KeyboardInterrupt):
            with capture.guard("probe"):
                raise KeyboardInterrupt()

    def test_guarded_returns_the_default_on_failure(self):
        def explode():
            raise RuntimeError("nope")

        self.assertEqual(capture.guarded(explode, "fallback"), "fallback")

    def test_guarded_propagates_cancellation(self):
        def cancel():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            capture.guarded(cancel, "fallback")


def _rich_response():
    usage = type(
        "U",
        (),
        {
            "input_tokens": 11,
            "output_tokens": 7,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
        },
    )()
    return type(
        "Ok",
        (),
        {
            "id": "msg_rich",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "usage": usage,
        },
    )()


class MetadataPreservationTests(unittest.TestCase):
    """Requirement 10: rich metadata survives a later transformation failure."""

    def setUp(self):
        capture.reset_capture_log_latch()

    def test_transformation_failure_is_appended_beside_the_observation(self):
        buffer = new_capture(None)
        buffer.record_observation(capture.observe_anthropic_response(_rich_response()))
        buffer.record_transformation_failure(
            ValueError("could not parse"), error_category="model_schema_invalid"
        )

        kinds = [event.event_kind for event in buffer.events]
        self.assertEqual(kinds, [EVENT_OBSERVATION, EVENT_TRANSFORMATION_FAILURE])

        # The original observation is exactly what was captured: the later
        # failure appended, it did not overwrite.
        preserved = buffer.events[0]
        self.assertEqual(preserved.observation.provider_response_id.value, "msg_rich")
        self.assertEqual(preserved.observation.input_tokens.value, 11)
        self.assertEqual(preserved.observation.stop_reason.value, "end_turn")

        failure = buffer.events[1]
        self.assertEqual(failure.error_category, "model_schema_invalid")
        self.assertFalse(failure.is_terminal)
        # The failure event carries no provider values of its own, so a reader
        # cannot mistake it for a response that arrived empty.
        self.assertTrue(failure.observation.is_empty)

    def test_event_ordinals_are_monotonic_per_subject(self):
        buffer = new_capture(None)
        buffer.record_observation(capture.observe_anthropic_response(None))
        buffer.record_observation(capture.observe_anthropic_response(None))
        buffer.record_terminal(EVENT_COMPLETED, breaker_after=UNKNOWN_BREAKER)
        self.assertEqual([e.event_ordinal for e in buffer.events], [1, 2, 3])

    def test_a_second_terminal_state_is_refused(self):
        buffer = new_capture(None)
        buffer.record_terminal(EVENT_COMPLETED, breaker_after=UNKNOWN_BREAKER)
        second = buffer.record_terminal(
            EVENT_PROVIDER_FAILURE, breaker_after=UNKNOWN_BREAKER
        )
        self.assertIsNone(second)
        self.assertEqual(len(buffer.events), 1)
        self.assertIn("duplicate_terminal", buffer.capture_failures)

    def test_cancellation_is_classified_as_cancellation_not_failure(self):
        self.assertEqual(
            capture.terminal_kind_for_exception(asyncio.CancelledError()),
            EVENT_CANCELLED,
        )
        self.assertEqual(
            capture.terminal_kind_for_exception(RuntimeError("x")),
            EVENT_PROVIDER_FAILURE,
        )


class ProviderExtractionTests(unittest.TestCase):
    def test_openai_cache_creation_is_unsupported_not_absent(self):
        # The Chat Completions API has no cache-creation counter at all. That is a
        # permanent property of the API and must not be reported as a response
        # that merely omitted the field.
        observation = capture.observe_openai_response(None)
        self.assertEqual(observation.cache_creation_tokens.status, VALUE_UNSUPPORTED)

    def test_openai_finish_reason_normalizes_onto_stop_reason(self):
        class Choice:
            finish_reason = "stop"

        class Response:
            id = "chatcmpl-1"
            model = "gpt-4.1"
            choices = [Choice()]
            usage = type(
                "U",
                (),
                {
                    "prompt_tokens": 5,
                    "completion_tokens": 6,
                    "prompt_tokens_details": type("D", (), {"cached_tokens": 1})(),
                },
            )()

        observation = capture.observe_openai_response(Response())
        self.assertEqual(observation.stop_reason.value, "stop")
        self.assertEqual(observation.input_tokens.value, 5)
        self.assertEqual(observation.output_tokens.value, 6)
        self.assertEqual(observation.cache_read_tokens.value, 1)

    def test_hostile_choices_container_is_isolated(self):
        class Hostile:
            id = "chatcmpl-1"
            model = "gpt-4.1"
            usage = None

            @property
            def choices(self):
                raise RuntimeError("hostile choices")

        observation = capture.observe_openai_response(Hostile())
        self.assertEqual(observation.provider_response_id.value, "chatcmpl-1")
        self.assertNotEqual(observation.stop_reason.status, VALUE_VALID)

    def test_observation_fingerprint_is_stable_and_metadata_only(self):
        first = capture.observe_anthropic_response(None)
        second = capture.observe_anthropic_response(None)
        self.assertEqual(first.metadata_fingerprint(), second.metadata_fingerprint())
        self.assertRegex(first.metadata_fingerprint(), r"^[0-9a-f]{64}$")


class NoCaptureScopeTests(unittest.TestCase):
    def test_publishing_without_a_scope_is_a_no_op(self):
        self.assertIsNone(capture.current_capture())
        self.assertFalse(capture.is_capturing())
        # Nothing raises, nothing is stored.
        capture.observe_anthropic_response(_HostileId())

    def test_scope_is_restored_after_exit(self):
        buffer = new_capture(None)
        with capture.capture_scope(buffer):
            self.assertIs(capture.current_capture(), buffer)
        self.assertIsNone(capture.current_capture())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
