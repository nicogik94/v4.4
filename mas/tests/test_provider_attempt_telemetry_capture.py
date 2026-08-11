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
import json
import re
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


# ═════════════ response-shape observation (V7 eval provenance) ═════════════
#
# The V7 live release observation could not be read as eight independent
# analytical-quality failures because the artifacts said nothing about what came
# back: 49 phase attempts returned adapter-visible text that was exactly empty
# and nothing recorded it. These are the regressions for the four facts that
# make that distinguishable, and for the isolation that keeps recording them
# from ever being able to change a provider result.


_UNSET = object()


class _Details:
    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class _Usage:
    prompt_tokens = 120
    completion_tokens = 3

    def __init__(self, details=_UNSET):
        if details is not _UNSET:
            self.completion_tokens_details = details


def _message(**fields):
    message = type("Message", (), {})()
    for name, value in fields.items():
        setattr(message, name, value)
    return message


def _completion(message=None, *, finish_reason="stop", usage=None, choices=_UNSET):
    response = type("Completion", (), {})()
    response.id = "chatcmpl-1"
    response.model = "gpt-5-2026-01-01"
    if choices is _UNSET:
        choice = type("Choice", (), {})()
        choice.finish_reason = finish_reason
        if message is not None:
            choice.message = message
        response.choices = [choice]
    elif choices is not None:
        response.choices = choices
    if usage is not None:
        response.usage = usage
    return response


class ResponseShapeTests(unittest.TestCase):
    """Regressions 1-20: OpenAI-shaped response metadata."""

    def _shape(self, response):
        return capture.openai_response_shape(response)

    # ── message content ──

    def test_content_null_is_null_not_empty(self):
        shape = self._shape(_completion(_message(content=None)))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_NULL)
        self.assertEqual(shape["visible_content_length"]["status"], capture.SHAPE_NULL)
        self.assertIsNone(shape["visible_content_length"]["value"])

    def test_content_empty_string_is_empty_with_zero_length(self):
        shape = self._shape(_completion(_message(content="")))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_EMPTY)
        self.assertEqual(shape["visible_content_length"]["status"], capture.SHAPE_VALID)
        self.assertEqual(shape["visible_content_length"]["value"], 0)

    def test_content_nonempty_records_length_and_never_the_text(self):
        text = '{"executive_strategy": "ship it"}'
        shape = self._shape(_completion(_message(content=text)))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_NONEMPTY)
        self.assertEqual(shape["visible_content_length"]["value"], len(text))
        self.assertNotIn("executive_strategy", json.dumps(shape))
        self.assertNotIn("ship it", json.dumps(shape))

    def test_content_field_missing_is_absent_not_null(self):
        shape = self._shape(_completion(_message(refusal=None)))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_ABSENT)

    def test_hostile_content_accessor_is_isolated_as_invalid(self):
        class Hostile:
            @property
            def content(self):
                raise RuntimeError("hostile-content-sentinel")

        shape = self._shape(_completion(Hostile()))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_INVALID)
        self.assertNotIn("hostile-content-sentinel", json.dumps(shape))

    def test_message_container_missing_is_missing_not_absent(self):
        shape = self._shape(_completion(None))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_MISSING)

    # ── refusal ──

    def test_refusal_null_empty_and_nonempty_are_three_states(self):
        states = {
            None: capture.SHAPE_NULL,
            "": capture.SHAPE_EMPTY,
            "I cannot help with that.": capture.SHAPE_NONEMPTY,
        }
        for raw, expected in states.items():
            with self.subTest(refusal=expected):
                shape = self._shape(_completion(_message(content="x", refusal=raw)))
                self.assertEqual(shape["refusal_status"]["status"], expected)

    def test_refusal_missing_is_absent(self):
        shape = self._shape(_completion(_message(content="x")))
        self.assertEqual(shape["refusal_status"]["status"], capture.SHAPE_ABSENT)

    def test_refusal_never_carries_a_length_or_the_text(self):
        shape = self._shape(
            _completion(_message(content="x", refusal="refusal-text-sentinel"))
        )
        self.assertIsNone(shape["refusal_status"]["value"])
        self.assertNotIn("refusal-text-sentinel", json.dumps(shape))

    def test_anthropic_has_no_refusal_or_reasoning_field_at_all(self):
        shape = capture.anthropic_response_shape(object())
        self.assertEqual(shape["refusal_status"]["status"], capture.SHAPE_UNSUPPORTED)
        self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_UNSUPPORTED)

    # ── reasoning tokens ──

    def test_reasoning_tokens_zero_is_an_exact_value_not_an_absence(self):
        shape = self._shape(
            _completion(_message(content="x"), usage=_Usage(_Details(reasoning_tokens=0)))
        )
        self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_VALID)
        self.assertEqual(shape["reasoning_tokens"]["value"], 0)

    def test_reasoning_tokens_positive_is_recorded_exactly(self):
        shape = self._shape(
            _completion(
                _message(content=""), usage=_Usage(_Details(reasoning_tokens=4000))
            )
        )
        self.assertEqual(shape["reasoning_tokens"]["value"], 4000)

    def test_reasoning_tokens_absent_from_a_present_details_object(self):
        shape = self._shape(
            _completion(_message(content="x"), usage=_Usage(_Details(audio_tokens=0)))
        )
        self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_ABSENT)

    def test_missing_details_container_is_missing_not_absent(self):
        shape = self._shape(_completion(_message(content="x"), usage=_Usage()))
        self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_MISSING)
        self.assertEqual(shape["reasoning_tokens"]["detail"], "details_missing")

    def test_missing_usage_is_distinguished_from_missing_details(self):
        shape = self._shape(_completion(_message(content="x")))
        self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_MISSING)
        self.assertEqual(shape["reasoning_tokens"]["detail"], "usage_missing")

    def test_a_reasoning_count_that_violates_its_contract_is_never_coerced(self):
        for raw in (True, 1.9, "12", -1):
            with self.subTest(raw=raw):
                shape = self._shape(
                    _completion(
                        _message(content="x"),
                        usage=_Usage(_Details(reasoning_tokens=raw)),
                    )
                )
                self.assertEqual(shape["reasoning_tokens"]["status"], capture.SHAPE_INVALID)
                self.assertIsNone(shape["reasoning_tokens"]["value"])

    # ── finish reason, effective model, malformed shapes ──

    def test_stop_and_length_finish_reasons_are_both_preserved(self):
        for reason in ("stop", "length"):
            with self.subTest(reason=reason):
                observation = capture.observe_openai_response(
                    _completion(_message(content="x"), finish_reason=reason)
                )
                self.assertEqual(observation.stop_reason.value, reason)

    def test_effective_model_is_the_model_that_answered(self):
        observation = capture.observe_openai_response(_completion(_message(content="x")))
        self.assertEqual(observation.effective_model.value, "gpt-5-2026-01-01")

    def test_partially_missing_usage_leaves_the_rest_readable(self):
        usage = type("PartialUsage", (), {"prompt_tokens": 9})()
        response = _completion(_message(content="x"), usage=usage)
        observation = capture.observe_openai_response(response)
        shape = self._shape(response)
        self.assertEqual(observation.input_tokens.value, 9)
        self.assertNotEqual(observation.output_tokens.status, "valid")
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_NONEMPTY)

    def test_a_completion_with_no_choice_reports_missing_rather_than_empty(self):
        shape = self._shape(_completion(choices=[]))
        self.assertEqual(shape["content_status"]["status"], capture.SHAPE_MISSING)
        self.assertEqual(shape["refusal_status"]["status"], capture.SHAPE_MISSING)

    def test_a_malformed_provider_shape_is_recorded_not_guessed(self):
        for response in (None, object(), {"choices": "not-a-list"}, 17):
            with self.subTest(response=type(response).__name__):
                shape = self._shape(response)
                self.assertIn(
                    shape["content_status"]["status"],
                    (capture.SHAPE_MISSING, capture.SHAPE_INVALID, capture.SHAPE_ABSENT),
                )
                self.assertIsNone(shape["visible_content_length"]["value"])


class _RecordingObserver:
    def __init__(self, explode=None):
        self.records = []
        self._explode = explode

    def record_response_shape(self, payload):
        if self._explode is not None:
            raise self._explode
        self.records.append(payload)


class ShapeObserverIsolationTests(unittest.TestCase):
    """Regressions 21-28: recording can never change what the runtime sees."""

    def test_no_observer_means_nothing_is_published_and_nothing_raises(self):
        self.assertIsNone(capture.current_response_shape_observer())
        observation = capture.observe_openai_response(
            _completion(_message(content="x"))
        )
        self.assertEqual(observation.effective_model.value, "gpt-5-2026-01-01")

    def test_the_observation_is_unchanged_whether_an_observer_is_bound_or_not(self):
        response = _completion(_message(content="x"))
        without = capture.observe_openai_response(response)
        with capture.response_shape_scope(_RecordingObserver()):
            with_observer = capture.observe_openai_response(response)
        self.assertEqual(
            without.metadata_fingerprint(), with_observer.metadata_fingerprint()
        )

    def test_an_observer_that_raises_cannot_break_the_observation(self):
        observer = _RecordingObserver(explode=RuntimeError("observer-sentinel"))
        with capture.response_shape_scope(observer):
            observation = capture.observe_openai_response(
                _completion(_message(content="x"))
            )
        self.assertEqual(observation.effective_model.value, "gpt-5-2026-01-01")

    def test_an_observer_that_raises_cannot_change_a_successful_provider_result(self):
        # The adapter's own path, end to end: a fake SDK client, a recording
        # observer that explodes, and the assertion the audit actually cares
        # about — the runtime still sees the provider's success.
        import llm_client

        calls = []

        class Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return _completion(
                    _message(content="provider-answered"), usage=_Usage()
                )

        client = type("Client", (), {})()
        client.chat = type("Chat", (), {})()
        client.chat.completions = Completions()

        previous, llm_client._openai = llm_client._openai, client
        try:
            buffer = new_capture(None)
            observer = _RecordingObserver(explode=RuntimeError("observer-sentinel"))
            with capture.response_shape_scope(observer), capture.capture_scope(buffer):
                result = asyncio.run(
                    llm_client._call_openai("gpt-5", "sys", "user", 100, 0.0)
                )
        finally:
            llm_client._openai = previous

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "provider-answered")
        self.assertEqual(len(calls), 1, "a capture defect must not cause a retry")
        self.assertNotIn("observer-sentinel", str(result.error))

    def test_a_bound_observer_receives_metadata_and_never_content(self):
        import llm_client

        class Completions:
            async def create(self, **kwargs):
                return _completion(
                    _message(content="secret-response-text", refusal="secret-refusal"),
                    finish_reason="length",
                    usage=_Usage(_Details(reasoning_tokens=4000)),
                )

        client = type("Client", (), {})()
        client.chat = type("Chat", (), {})()
        client.chat.completions = Completions()

        previous, llm_client._openai = llm_client._openai, client
        try:
            buffer = new_capture(None)
            observer = _RecordingObserver()
            with capture.response_shape_scope(observer), capture.capture_scope(buffer):
                result = asyncio.run(
                    llm_client._call_openai(
                        "gpt-5", "secret-system-prompt", "secret-user-prompt", 100, 0.0
                    )
                )
        finally:
            llm_client._openai = previous

        self.assertTrue(result.ok)
        self.assertEqual(len(observer.records), 1)
        payload = json.dumps(observer.records[0])
        record = observer.records[0]

        self.assertEqual(record["content_status"]["status"], capture.SHAPE_NONEMPTY)
        self.assertEqual(
            record["visible_content_length"]["value"], len("secret-response-text")
        )
        self.assertEqual(record["refusal_status"]["status"], capture.SHAPE_NONEMPTY)
        self.assertEqual(record["reasoning_tokens"]["value"], 4000)
        self.assertEqual(record["invocation_id"], buffer.invocation_id)
        self.assertEqual(record["provider"], buffer.provider)

        for secret in (
            "secret-response-text",
            "secret-refusal",
            "secret-system-prompt",
            "secret-user-prompt",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, payload)
        # Nor any digest of them: a hash of a response is still derived from the
        # response, and this record carries no 32/40/64-hex value at all.
        self.assertIsNone(re.search(r"\b[0-9a-f]{32,}\b", payload))
        for token in ("sk-", "Bearer", "Authorization", "api_key"):
            with self.subTest(token=token):
                self.assertNotIn(token, payload)

    def test_cancellation_is_never_swallowed_by_the_observer_boundary(self):
        observer = _RecordingObserver(explode=asyncio.CancelledError())
        with capture.response_shape_scope(observer):
            with self.assertRaises(asyncio.CancelledError):
                capture.observe_openai_response(_completion(_message(content="x")))

    def test_the_observer_scope_is_restored_after_exit(self):
        observer = _RecordingObserver()
        with capture.response_shape_scope(observer):
            self.assertIs(capture.current_response_shape_observer(), observer)
        self.assertIsNone(capture.current_response_shape_observer())


class RuntimeCaptureChainTests(unittest.TestCase):
    """The one regression that pins the join between the two capture channels.

    The eval recorder reads the runtime's own records by *shape* — an invocation
    is a record carrying an invocation id and a candidate ordinal — and joins
    them to response-shape metadata on ``invocation_id``. Both halves are duck
    typed, so a rename on either side would silently stop classifying rather than
    fail. This drives a real gateway call against a fake SDK client and asserts
    the whole chain still lands, which is what the V7 live artifact could not do.
    """

    def test_a_real_gateway_call_populates_both_capture_channels(self):
        import llm_client
        from evals import provenance
        from provider_telemetry import (
            ENTRY_POINT_EVALUATION_PHASE,
            POSTURE_OBSERVATIONAL,
            telemetry_scope,
        )

        class Completions:
            def __init__(self):
                self.calls = 0

            async def create(self, **kwargs):
                self.calls += 1
                # The V7 live signature: adapter-visible text exactly empty,
                # finish_reason=length, output_tokens tiny, reasoning enormous.
                return _completion(
                    _message(content="", refusal=None),
                    finish_reason="length",
                    usage=_Usage(_Details(reasoning_tokens=4000)),
                )

        completions = Completions()
        client = type("Client", (), {})()
        client.chat = type("Chat", (), {})()
        client.chat.completions = completions

        recorder = provenance.EvalProvenanceRecorder(case_id="G01")
        previous = (llm_client._openai, llm_client.OPENAI_API_KEY, llm_client.ANTHROPIC_API_KEY)
        llm_client._openai = client
        llm_client.OPENAI_API_KEY = "test-key-not-a-credential"
        llm_client.ANTHROPIC_API_KEY = ""

        async def drive():
            with capture.response_shape_scope(recorder):
                async with telemetry_scope(
                    entry_point=ENTRY_POINT_EVALUATION_PHASE,
                    project_id="eval-G01",
                    run_id="eval-G01",
                    expected_phases=("strategy",),
                    posture=POSTURE_OBSERVATIONAL,
                    sink=recorder,
                ):
                    return await llm_client.call_llm(
                        "strategy", "system-sentinel", "user-sentinel", project_id="eval-G01"
                    )

        try:
            result = asyncio.run(drive())
        finally:
            llm_client._openai, llm_client.OPENAI_API_KEY, llm_client.ANTHROPIC_API_KEY = previous

        # This fixture is the V7 live signature, and the provider-output
        # contract (#115) now fails closed on it instead of reporting a
        # success that carried no text. So the gateway exhausts the OpenAI
        # fallback chain rather than stopping at the first candidate, and the
        # call ends unusable. Both are the corrected behaviour, not a
        # regression: an empty completion is no longer a usable result.
        self.assertEqual(completions.calls, 2)
        self.assertFalse(result.ok)
        self.assertIn("output_token_exhausted", result.error)

        records = recorder.invocation_records()
        answered = [r for r in records if r["provider"] == "openai"]
        # One record per OpenAI candidate actually attempted.
        self.assertEqual(len(answered), 2)
        record = answered[0]

        # Channel one: the runtime's own invocation start and attempt events.
        self.assertEqual(record["phase"], "strategy")
        self.assertGreaterEqual(record["candidate_ordinal"], 1)
        self.assertEqual(record["retry_ordinal"], 1)
        self.assertEqual(record["terminal_event_kind"], "provider_failure")
        self.assertEqual(record["effective_model"]["value"], "gpt-5-2026-01-01")
        self.assertEqual(record["stop_reason"]["value"], "length")
        self.assertEqual(record["input_tokens"]["value"], 120)
        self.assertEqual(record["output_tokens"]["value"], 3)

        # Channel two: the shape metadata the durable relations have no column
        # for, joined onto the same invocation.
        self.assertEqual(record["content_status"]["status"], capture.SHAPE_EMPTY)
        self.assertEqual(record["visible_content_length"]["value"], 0)
        self.assertEqual(record["refusal_status"]["status"], capture.SHAPE_NULL)
        self.assertEqual(record["reasoning_tokens"]["value"], 4000)

        # A candidate the gateway skipped is recorded as skipped, not as a
        # response that happened to say nothing.
        skipped = [r for r in records if r["terminal_event_kind"] == "skipped"]
        for entry in skipped:
            with self.subTest(provider=entry["provider"]):
                self.assertEqual(
                    entry["content_status"]["status"], provenance.STATUS_UNKNOWN
                )

        payload = json.dumps(records)
        for secret in ("system-sentinel", "user-sentinel", "test-key-not-a-credential"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, payload)


class ShapeVocabularyParityTests(unittest.TestCase):
    """The eval schema restates these statuses; the two must not drift."""

    def test_the_eval_schema_uses_exactly_this_vocabulary(self):
        from evals import provenance

        self.assertEqual(
            set(capture.PRESENCE_STATUSES) | set(capture.COUNT_STATUSES),
            set(provenance.VALUE_STATUSES),
        )
        for name in capture.RESPONSE_SHAPE_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, capture.openai_response_shape(None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
