"""Capture failures reach durable storage, and block a strict certification.

The finding: ``InvocationCapture.record_capture_failure`` existed and had no
production caller. Every guarded extraction in the package called
``note_capture_failure``, which appended a string to a list that was never read
by anything and died with the process — so a run whose provider-metadata
extraction failed on every single call produced telemetry indistinguishable from
a run where extraction worked perfectly and the provider sent nothing.

The remediation gives it exactly one production site — ``_InvocationProbe.drain``
in the runtime gateway, which every invocation passes through on its way out —
and three consequences:

* each noted failure becomes a durable ``capture_failure`` event;
* a failure that cannot even be represented as an event becomes a run-level
  *undurable* outcome, which reconciliation refuses to certify;
* a strict run carrying a durable capture failure is not ``complete``.

Every test below drives the real gateway with the real session and the real
delivery worker. The failures are induced at the layer they actually occur at —
a provider object whose attribute raises, a breaker that will not report, a
queue that is full — never by calling the recording method directly.
"""
import asyncio
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extensions.runtime import GatewayRequest, RoutingContext  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from provider_telemetry import capture as attempt_capture  # noqa: E402
from provider_telemetry import repository, service  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    EVENT_CAPTURE_FAILURE,
    EVENT_OBSERVATION,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    SUBJECT_SDK_INVOCATION,
    TelemetryRunRecord,
)
from provider_telemetry.delivery import EventDelivery  # noqa: E402
from runtime.provider_gateway import DefaultProviderGateway  # noqa: E402
from tests.provider_telemetry_support import (  # noqa: E402
    BreakerStub,
    HalfBrokenBreaker,
    RecordingSink,
)

GATEWAY_SOURCE = ROOT / "runtime" / "provider_gateway.py"
CAPTURE_SOURCE = ROOT / "provider_telemetry" / "capture.py"


def _request(phase: str = "classify", **kwargs) -> GatewayRequest:
    return GatewayRequest(
        phase=phase,
        system_prompt="system",
        user_prompt="prompt",
        routing_context=RoutingContext(phase=phase),
        **kwargs,
    )


async def _ok(model, system, prompt, max_tokens, temperature, thinking_budget=0):
    return LLMResponse(text="ok", ok=True, model_used=model, input_tokens=3, output_tokens=4)


async def _session(sink, posture=POSTURE_OBSERVATIONAL, capacity=256):
    """A real TelemetrySession over a recording sink and a real delivery worker.

    Bound to the session context variable exactly as ``run_session`` binds it in
    production, because part of what is under test here is what happens to a
    capture failure raised *outside* any invocation — which can only be recorded
    if the session is reachable the way production makes it reachable.
    """
    run = TelemetryRunRecord(
        posture=posture,
        telemetry_required=posture == POSTURE_STRICT,
        entry_point="cli_workflow",
    )
    delivery = EventDelivery(sink, posture=posture, capacity=capacity)
    await delivery.start()
    session = service.TelemetrySession(run_record=run, sink=sink, delivery=delivery)
    service._session.set(session)
    return session


def _gateway(session, **kwargs):
    from runtime.cache import NoOpSemanticCache

    return DefaultProviderGateway(
        anthropic_executor=kwargs.pop("anthropic_executor", _ok),
        openai_executor=kwargs.pop("openai_executor", _ok),
        cache=kwargs.pop("cache", None) or NoOpSemanticCache(),
        breaker=kwargs.pop("breaker", None) or BreakerStub(),
        max_retries=kwargs.pop("max_retries", 1),
        telemetry=session,
        **kwargs,
    )


class _Hostile:
    """A provider response whose chosen attribute raises when read.

    Everything else on it is a perfectly ordinary, valid response, so the only
    difference between a run against this and a run against a healthy response
    is the one extraction that fails.
    """

    def __init__(self, exploding: str) -> None:
        self._exploding = exploding

    def __getattr__(self, name):
        if name == self._exploding:
            raise RuntimeError(f"{name} is unreadable")
        raise AttributeError(name)


class _HostileUsage:
    def __init__(self, exploding: str) -> None:
        self._exploding = exploding

    def __getattr__(self, name):
        if name == self._exploding:
            raise RuntimeError(f"{name} is unreadable")
        raise AttributeError(name)


class _Response:
    """A complete, valid Anthropic-shaped response with one hostile field."""

    def __init__(self, *, exploding_field=None, exploding_usage=None):
        self.id = "msg_01TESTIDENTITY"
        self.model = "claude-sonnet-4-6"
        self.stop_reason = "end_turn"
        self.usage = (
            _HostileUsage(exploding_usage)
            if exploding_usage
            else type("U", (), {
                "input_tokens": 11, "output_tokens": 7,
                "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2,
            })()
        )
        if exploding_field:
            self._exploding = exploding_field
            delattr_target = exploding_field
            type(self).__getattribute__  # noqa: B018 - documented below
            object.__setattr__(self, "_hostile_field", delattr_target)

    def __getattribute__(self, name):
        hostile = object.__getattribute__(self, "__dict__").get("_hostile_field")
        if hostile is not None and name == hostile:
            raise RuntimeError(f"{name} is unreadable")
        return object.__getattribute__(self, name)


class StaticReachabilityTests(unittest.TestCase):
    """``record_capture_failure`` is reachable from production, provably."""

    def test_the_gateway_flushes_capture_failures_on_the_publish_path(self):
        """Asserted against the AST, not a substring of a docstring."""
        tree = ast.parse(GATEWAY_SOURCE.read_text(encoding="utf-8"))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertIn(
            "flush_capture_failures", calls | attributes,
            "the gateway must materialize capture failures",
        )
        self.assertIn("note_unrepresented_capture_failures", calls | attributes)

    def test_flush_is_the_only_caller_of_record_capture_failure_in_the_package(self):
        """One production entry point, so there is one thing to keep true."""
        tree = ast.parse(CAPTURE_SOURCE.read_text(encoding="utf-8"))
        callers = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "record_capture_failure"
                ):
                    callers.append(node.name)
        self.assertEqual(callers, ["flush_capture_failures"])

    def test_the_gateway_reaches_flush_from_drain(self):
        source = GATEWAY_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        drains = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "drain"
        ]
        self.assertTrue(drains)
        self.assertTrue(
            any(
                isinstance(inner, ast.Attribute)
                and inner.attr == "flush_capture_failures"
                for drain in drains
                for inner in ast.walk(drain)
            ),
            "drain() must be the site that materializes capture failures",
        )


class ExtractionFailureTests(unittest.IsolatedAsyncioTestCase):
    """Every guarded extraction site produces a durable capture_failure event."""

    async def _run_with_response(self, response) -> tuple[RecordingSink, object]:
        sink = RecordingSink()
        session = await _session(sink)

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            # Exactly where llm_client observes the raw provider object: inside
            # the capture scope, while the response is still intact.
            attempt_capture.observe_anthropic_response(response)
            return LLMResponse(text="ok", ok=True, model_used=model,
                               input_tokens=3, output_tokens=4)

        gateway = _gateway(session, anthropic_executor=executor)
        result = await gateway.call(_request())
        await session.delivery.aclose(drain_timeout=5.0)
        return sink, result

    async def test_each_extraction_site_is_recorded_durably(self):
        cases = {
            "response id extraction": _Response(exploding_field="id"),
            "model extraction": _Response(exploding_field="model"),
            "stop reason extraction": _Response(exploding_field="stop_reason"),
            "usage extraction": _Response(exploding_field="usage"),
            "input token extraction": _Response(exploding_usage="input_tokens"),
            "output token extraction": _Response(exploding_usage="output_tokens"),
            "cache read extraction": _Response(exploding_usage="cache_read_input_tokens"),
        }
        for label, response in cases.items():
            with self.subTest(case=label):
                attempt_capture.reset_capture_log_latch()
                sink, result = await self._run_with_response(response)
                self.assertEqual(result.error, "", "the provider result must be unchanged")
                self.assertEqual(result.text, "ok")
                failures = [
                    event for event in sink.attempt_events
                    if event.event_kind == EVENT_CAPTURE_FAILURE
                ]
                self.assertTrue(
                    failures, f"{label} produced no durable capture_failure event"
                )
                for event in failures:
                    self.assertEqual(event.subject_kind, SUBJECT_SDK_INVOCATION)
                    self.assertFalse(event.is_terminal)
                    self.assertTrue(event.failure_class)

    async def test_a_breaker_snapshot_failure_becomes_an_undurable_run_outcome(self):
        """The one failure site that cannot produce an event, and why.

        The breaker snapshot is read in order to *build* the invocation record,
        so it happens before the capture buffer that would hold its failure
        exists — and ``provider_attempt_event`` has no row shape without a
        subject. It is therefore recorded at the level that does exist: the run,
        as a telemetry failure this process could not represent. Before the
        remediation it was logged once and discarded, and the run reconciled
        clean.
        """
        sink = RecordingSink()
        session = await _session(sink)
        gateway = _gateway(session, breaker=HalfBrokenBreaker())
        result = await gateway.call(_request())
        self.assertEqual(result.error, "", "the provider result must be unchanged")
        self.assertGreaterEqual(session.unrepresented_capture_failures, 1)

        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertIn("unrepresented_capture_failure", attestation.failures)
        self.assertFalse(attestation.certified)

        # And the invocation still records the breaker as *unknown* rather than
        # as a fabricated "closed with zero failures".
        invocation = sink.invocation_starts[0]
        self.assertEqual(invocation.breaker_before.state, "unknown")
        self.assertIsNone(invocation.breaker_before.failure_count)

    async def test_a_redaction_failure_is_recorded_durably(self):
        sink = RecordingSink()
        session = await _session(sink)
        original = attempt_capture.redaction.provider_response_id

        def exploding(_raw):
            raise RuntimeError("the validator is broken")

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            attempt_capture.observe_anthropic_response(_Response())
            return LLMResponse(text="ok", ok=True, model_used=model)

        attempt_capture.redaction.provider_response_id = exploding
        try:
            gateway = _gateway(session, anthropic_executor=executor)
            result = await gateway.call(_request())
        finally:
            attempt_capture.redaction.provider_response_id = original
        await session.delivery.aclose(drain_timeout=5.0)
        self.assertEqual(result.error, "")
        self.assertTrue(
            [e for e in sink.attempt_events if e.event_kind == EVENT_CAPTURE_FAILURE]
        )


class NeutralityTests(unittest.IsolatedAsyncioTestCase):
    """Recording a capture failure changes nothing the runtime does."""

    async def test_it_never_changes_the_provider_result(self):
        sink = RecordingSink()
        session = await _session(sink)

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            attempt_capture.observe_anthropic_response(_Response(exploding_field="id"))
            return LLMResponse(text="ok", ok=True, model_used=model,
                               input_tokens=3, output_tokens=4)

        gateway = _gateway(session, anthropic_executor=executor)
        response = await gateway.call(_request())
        await session.delivery.aclose(drain_timeout=5.0)
        self.assertEqual(response.error, "")
        self.assertEqual(response.text, "ok")

    async def test_it_never_triggers_retry_fallback_or_a_breaker_transition(self):
        sink = RecordingSink()
        session = await _session(sink)
        breaker = BreakerStub()
        calls: list[str] = []

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            calls.append(model)
            attempt_capture.observe_anthropic_response(_Response(exploding_field="id"))
            return LLMResponse(text="ok", ok=True, model_used=model)

        gateway = _gateway(session, anthropic_executor=executor, breaker=breaker,
                           max_retries=3)
        await gateway.call(_request())
        await session.delivery.aclose(drain_timeout=5.0)
        self.assertEqual(len(calls), 1, "a capture failure must not cause a retry")
        self.assertEqual(breaker.failure_calls, [],
                         "a capture failure must not open the breaker")

    async def test_it_never_overwrites_rich_earlier_metadata(self):
        """The events are appended beside the observation, never over it."""
        sink = RecordingSink()
        session = await _session(sink)

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            buffer = attempt_capture.current_capture()
            observation = attempt_capture.observe_anthropic_response(_Response())
            buffer.record_observation(observation)
            # ... and only *then* something fails.
            attempt_capture.guarded(
                lambda: (_ for _ in ()).throw(RuntimeError("late")),
                None,
                reason="late_failure",
            )
            return LLMResponse(text="ok", ok=True, model_used=model)

        gateway = _gateway(session, anthropic_executor=executor)
        await gateway.call(_request())
        await session.delivery.aclose(drain_timeout=5.0)

        observations = [
            e for e in sink.attempt_events if e.event_kind == EVENT_OBSERVATION
        ]
        failures = [
            e for e in sink.attempt_events if e.event_kind == EVENT_CAPTURE_FAILURE
        ]
        self.assertTrue(observations)
        self.assertTrue(failures)
        rich = observations[0].observation
        self.assertTrue(rich.provider_response_id.is_valid)
        self.assertEqual(rich.provider_response_id.value, "msg_01TESTIDENTITY")
        self.assertEqual(rich.input_tokens.value, 11)


class UndurableOutcomeTests(unittest.IsolatedAsyncioTestCase):
    """What happens when the capture failure itself cannot be recorded."""

    async def test_an_unrepresentable_capture_failure_becomes_an_undurable_outcome(self):
        sink = RecordingSink()
        session = await _session(sink)

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            buffer = attempt_capture.current_capture()
            buffer.note_capture_failure("read:id:RuntimeError")
            # Constructing the capture_failure event now fails too. This is the
            # end of the line: telemetry cannot write down that it failed.
            # `InvocationCapture` uses __slots__, so the method is replaced on
            # its type for the duration rather than on the instance.
            self.addCleanup(
                setattr, type(buffer), "append", type(buffer).append
            )
            type(buffer).append = lambda _self, **_kwargs: None
            return LLMResponse(text="ok", ok=True, model_used=model)

        gateway = _gateway(session, anthropic_executor=executor)
        response = await gateway.call(_request())
        self.assertEqual(response.error, "",
                         "the provider result must still be unchanged")
        self.assertEqual(session.unrepresented_capture_failures, 1)

        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertIn("unrepresented_capture_failure", attestation.failures)
        self.assertIn("undurable_events", attestation.failures)
        self.assertFalse(attestation.certified)
        self.assertEqual(attestation.unrepresented_capture_failures, 1)

    async def test_a_refused_queue_submission_is_detected_and_blocks_completion(self):
        sink = RecordingSink()
        # A queue with exactly one slot, already occupied: every further
        # submission is refused, which is what a saturated delivery queue does
        # to a completion event under load.
        session = await _session(sink, capacity=1)
        session.delivery._queue = asyncio.Queue(maxsize=1)
        session.delivery._queue.put_nowait(object())

        submitted = session.submit_events([object(), object()])
        self.assertEqual(submitted, 0)
        self.assertEqual(session.unqueued_events, 2)

        attestation = await session.reconcile(drain_timeout=1.0)
        self.assertIn("undurable_events", attestation.failures)
        self.assertFalse(attestation.certified)

    async def test_a_delivery_worker_failure_leaves_the_run_uncertified(self):
        from tests.provider_telemetry_support import FailingSink

        sink = FailingSink(fail_starts=False, fail_events=True)
        session = await _session(sink)
        gateway = _gateway(session)
        response = await gateway.call(_request())
        self.assertEqual(response.error, "")
        attestation = await session.reconcile(drain_timeout=5.0)
        self.assertFalse(attestation.certified)
        self.assertIn("undurable_events", attestation.failures)

    async def test_observational_health_never_claims_completeness(self):
        sink = RecordingSink()
        session = await _session(sink)
        health = session.health()
        self.assertFalse(health["completeness_guaranteed"])
        self.assertIn("Completeness is NOT guaranteed", health["completeness_notice"])
        self.assertEqual(health["unrepresented_capture_failures"], 0)
        await session.delivery.aclose(drain_timeout=1.0)


class FlushSemanticsTests(unittest.TestCase):
    """The flush itself: drains once, counts what it cannot represent."""

    def _buffer(self, cls=None):
        return (cls or attempt_capture.InvocationCapture)(
            invocation_id="00000000-0000-4000-8000-000000000021",
            call_id="00000000-0000-4000-8000-000000000022",
            telemetry_run_id="00000000-0000-4000-8000-000000000023",
            posture=POSTURE_OBSERVATIONAL,
            worker_id="test-worker",
            provider="anthropic",
            requested_model="claude-sonnet-4-6",
        )

    def test_each_noted_failure_becomes_one_event(self):
        buffer = self._buffer()
        buffer.note_capture_failure("read:id:RuntimeError")
        buffer.note_capture_failure("usage:input_tokens:TypeError")
        self.assertEqual(buffer.flush_capture_failures(), 0)
        kinds = [event.event_kind for event in buffer.events]
        self.assertEqual(kinds, [EVENT_CAPTURE_FAILURE, EVENT_CAPTURE_FAILURE])
        self.assertEqual(
            [event.failure_class for event in buffer.events],
            ["read:id:RuntimeError", "usage:input_tokens:TypeError"],
        )

    def test_flushing_twice_yields_nothing_the_second_time(self):
        buffer = self._buffer()
        buffer.note_capture_failure("read:id:RuntimeError")
        buffer.flush_capture_failures()
        before = len(buffer.events)
        self.assertEqual(buffer.flush_capture_failures(), 0)
        self.assertEqual(len(buffer.events), before)

    def test_a_failure_to_build_the_event_is_counted_not_raised(self):
        class Unbuildable(attempt_capture.InvocationCapture):
            def append(self, **_kwargs):
                return None

        buffer = self._buffer(Unbuildable)
        buffer.note_capture_failure("read:id:RuntimeError")
        buffer.note_capture_failure("read:model:RuntimeError")
        self.assertEqual(buffer.flush_capture_failures(), 2)
        self.assertEqual(buffer.capture_failures, [])

    def test_a_raising_append_is_also_counted_rather_than_propagated(self):
        class Exploding(attempt_capture.InvocationCapture):
            def append(self, **_kwargs):
                raise RuntimeError("even the failure record fails")

        buffer = self._buffer(Exploding)
        buffer.note_capture_failure("read:id:RuntimeError")
        self.assertEqual(buffer.flush_capture_failures(), 1)
        self.assertEqual(buffer.capture_failures, [])


class MutationTests(unittest.IsolatedAsyncioTestCase):
    """Removing the production wiring must make these tests fail."""

    async def test_without_the_flush_no_capture_failure_is_ever_stored(self):
        from runtime import provider_gateway

        sink = RecordingSink()
        session = await _session(sink)

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            attempt_capture.observe_anthropic_response(_Response(exploding_field="id"))
            return LLMResponse(text="ok", ok=True, model_used=model)

        # The mutation: drain() reverts to the pre-remediation body.
        original = provider_gateway._InvocationProbe.drain

        def unwired(self):
            events = list(self.capture.events)
            self.capture.events.clear()
            return events

        provider_gateway._InvocationProbe.drain = unwired
        try:
            gateway = _gateway(session, anthropic_executor=executor)
            await gateway.call(_request())
        finally:
            provider_gateway._InvocationProbe.drain = original
        await session.delivery.aclose(drain_timeout=5.0)

        self.assertEqual(
            [e for e in sink.attempt_events if e.event_kind == EVENT_CAPTURE_FAILURE],
            [],
            "the mutation must reproduce the finding: no durable record at all",
        )


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
