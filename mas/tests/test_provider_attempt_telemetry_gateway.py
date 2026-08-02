"""Behavioral neutrality of telemetry inside the runtime gateway.

This file holds the audit's central claim, restated as executable regressions:

* 1. delayed telemetry cannot change fallback provider/model selection;
* 2. delayed completion writes cannot change cache hit/miss behavior;
* 3. delayed completion writes cannot change a caller's timeout outcome;
* 6. cancellation during an SDK call leaves a durable start and an explicit
     terminal state;
* 12. a breaker snapshot failure is unknown, never a fabricated "closed with
      zero failures".

Plus the equivalence that makes the whole posture credible: a run with
telemetry enabled and *every* write failing produces the same attempt chain, the
same returned response and the same breaker state as a run with telemetry off.

The strict-mode fail-closed behavior is asserted here too, explicitly, because it
is the one place telemetry does change what the runtime does and that must be
visible in a test rather than only in a docstring.
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import MODEL_ROUTING  # noqa: E402
from extensions.runtime import GatewayRequest, RoutingContext  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from provider_telemetry import repository  # noqa: E402
from provider_telemetry.delivery import EventDelivery  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    EVENT_CANCELLED,
    EVENT_SKIPPED,
    INVOCATION_PROVIDER_CALL,
    INVOCATION_SKIPPED_CANDIDATE,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    SUBJECT_SDK_INVOCATION,
)
from provider_telemetry.transport import TelemetryStartUnavailable  # noqa: E402
from runtime.cache import InMemorySemanticCache, NoOpSemanticCache  # noqa: E402
from runtime.provider_gateway import DefaultProviderGateway  # noqa: E402
from tests.provider_telemetry_support import (  # noqa: E402
    AmbiguousSink,
    BlockingSink,
    BreakerStub,
    ExplodingBreaker,
    FailingSink,
    HalfBrokenBreaker,
    RecordingSink,
    SlowSink,
)


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


def _failing(category: str, message: str = "boom"):
    async def executor(model, system, prompt, max_tokens, temperature, thinking_budget=0):
        return LLMResponse(ok=False, error=message, error_type=category, model_used=model)

    return executor


class _TelemetrySession:
    """A minimal session double with a real delivery worker behind it."""

    def __init__(self, sink, posture=POSTURE_OBSERVATIONAL, capacity=256):
        self.sink = sink
        self.posture = posture
        self.telemetry_run_id = "00000000-0000-4000-8000-000000000001"
        self.delivery = EventDelivery(sink, posture=posture, capacity=capacity)
        self.start_failures = 0

    @property
    def strict(self):
        return self.posture == POSTURE_STRICT

    async def start(self):
        await self.delivery.start()
        return self

    async def _persist(self, table, record):
        try:
            await self.sink.append_start(table, record)
        except Exception as exc:
            self.start_failures += 1
            if self.strict:
                raise TelemetryStartUnavailable(str(exc)) from exc

    async def persist_call_start(self, record):
        await self._persist(repository.CALL_TABLE, record)

    async def persist_invocation_start(self, record):
        await self._persist(repository.INVOCATION_TABLE, record)

    async def persist_attempt_start(self, record):
        await self._persist(repository.ATTEMPT_TABLE, record)

    def submit_event(self, event):
        return self.delivery.submit(event)

    def submit_events(self, events):
        return sum(1 for event in events if self.submit_event(event))

    async def drain(self, timeout=5.0):
        return await self.delivery.drain(timeout=timeout)

    async def close(self):
        return await self.delivery.aclose(drain_timeout=5.0)


def _gateway(session, *, breaker=None, cache=None, max_retries=1, **kwargs):
    return DefaultProviderGateway(
        anthropic_executor=kwargs.pop("anthropic_executor", _ok),
        openai_executor=kwargs.pop("openai_executor", _ok),
        cache=cache if cache is not None else NoOpSemanticCache(),
        breaker=breaker if breaker is not None else BreakerStub(),
        max_retries=max_retries,
        telemetry=session,
        **kwargs,
    )


class DelayedTelemetryNeutralityTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_writes_cannot_change_fallback_selection(self):
        """Requirement 1."""
        seen = []

        async def anthropic(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            seen.append(("anthropic", model))
            return LLMResponse(
                ok=False, error="rate limited", error_type="rate_limited", model_used=model
            )

        async def openai(model, system, prompt, max_tokens, temperature):
            seen.append(("openai", model))
            return LLMResponse(text="ok", ok=True, model_used=model)

        async def run(session):
            gateway = _gateway(
                session,
                anthropic_executor=anthropic,
                openai_executor=openai,
                max_retries=1,
            )
            return await gateway.call(_request("classify"))

        # Baseline: no telemetry at all.
        seen.clear()
        baseline = await run(None)
        baseline_order = list(seen)

        # A sink whose every event write takes a quarter of a second. If the
        # gateway awaited it, the fallback would be serialized behind it — and if
        # it changed the selection at all, the order below would differ.
        seen.clear()
        session = await _TelemetrySession(SlowSink(delay=0.25)).start()
        delayed = await run(session)
        delayed_order = list(seen)
        await session.close()

        self.assertEqual(baseline_order, delayed_order)
        self.assertEqual(baseline.provider_used, delayed.provider_used)
        self.assertEqual(baseline.model_used, delayed.model_used)
        self.assertEqual(baseline.fallback_used, delayed.fallback_used)
        self.assertEqual(baseline.attempt_count, delayed.attempt_count)
        self.assertTrue(delayed.fallback_used)

    async def test_blocked_completion_delivery_does_not_delay_the_caller(self):
        """Requirement 3, in its sharpest form: a wedged sink cannot hang a call."""
        sink = BlockingSink()  # append_event never returns until released
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session)

        # If the completion write were awaited, this would never return.
        response = await asyncio.wait_for(gateway.call(_request()), timeout=2.0)
        self.assertFalse(response.error)

        sink.release.set()
        await session.close()

    async def test_delayed_writes_cannot_change_cache_hit_or_miss(self):
        """Requirement 2."""

        async def run(session):
            cache = InMemorySemanticCache()
            gateway = _gateway(session, cache=cache)
            first = await gateway.call(_request(allow_cache=True))
            second = await gateway.call(_request(allow_cache=True))
            return first, second

        baseline_first, baseline_second = await run(None)

        session = await _TelemetrySession(SlowSink(delay=0.2)).start()
        delayed_first, delayed_second = await run(session)
        await session.close()

        self.assertEqual(baseline_first.cache_status, delayed_first.cache_status)
        self.assertEqual(baseline_second.cache_status, delayed_second.cache_status)
        self.assertEqual(baseline_second.cache_hit, delayed_second.cache_hit)

    async def test_delayed_writes_cannot_change_a_caller_timeout_outcome(self):
        """Requirement 3."""

        async def slow_provider(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            await asyncio.sleep(0.30)
            return LLMResponse(text="ok", ok=True, model_used=model)

        async def run(session):
            gateway = _gateway(session, anthropic_executor=slow_provider)
            try:
                await asyncio.wait_for(gateway.call(_request()), timeout=0.10)
                return "completed"
            except asyncio.TimeoutError:
                return "timeout"

        self.assertEqual(await run(None), "timeout")

        sink = SlowSink(delay=1.0)
        session = await _TelemetrySession(sink).start()
        self.assertEqual(await run(session), "timeout")
        await session.close()


class TelemetryFailureNeutralityTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_totally_failing_sink_changes_nothing_observable(self):
        breaker_off = BreakerStub()
        breaker_on = BreakerStub()

        async def run(session, breaker):
            gateway = _gateway(
                session,
                breaker=breaker,
                anthropic_executor=_failing("rate_limited"),
                openai_executor=_failing("rate_limited"),
                max_retries=2,
            )
            return await gateway.call(_request())

        baseline = await run(None, breaker_off)

        session = await _TelemetrySession(FailingSink()).start()
        with_failing_telemetry = await run(session, breaker_on)
        await session.close()

        for field in (
            "text", "error", "error_type", "provider_used", "model_used",
            "attempt_count", "fallback_used", "fallback_reason",
            "failed_provider", "failed_model", "failed_error_type",
            "cache_status", "retryable",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    getattr(baseline, field), getattr(with_failing_telemetry, field)
                )
        self.assertEqual(baseline.attempts, with_failing_telemetry.attempts)
        self.assertEqual(breaker_off.failure_calls, breaker_on.failure_calls)
        self.assertEqual(breaker_off.reset_calls, breaker_on.reset_calls)

    async def test_an_ambiguous_sink_does_not_change_the_response(self):
        session = await _TelemetrySession(AmbiguousSink()).start()
        gateway = _gateway(session)
        response = await gateway.call(_request())
        await session.close()
        self.assertFalse(response.error)
        self.assertEqual(response.text, "ok")


class BreakerSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 12: a snapshot failure is unknown, never fabricated."""

    async def test_unreadable_breaker_state_yields_unknown(self):
        # Driven against the snapshot helper directly: a breaker whose `is_open`
        # raises breaks routing long before telemetry is involved, so putting one
        # behind a whole gateway call would test the wrong thing.
        gateway = _gateway(None, breaker=ExplodingBreaker())
        snapshot = gateway._breaker_snapshot("anthropic:model")  # noqa: SLF001

        self.assertEqual(snapshot.status, "unknown")
        self.assertEqual(snapshot.state, "unknown")
        self.assertIsNone(snapshot.failure_count)

    async def test_unreadable_failure_counter_yields_unknown_not_zero(self):
        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session, breaker=HalfBrokenBreaker())
        await gateway.call(_request())
        await session.close()

        invocation = sink.invocation_starts[0]
        # The audit's exact counterexample: this must not read
        # "closed, 0 failures" for a reading that was never taken.
        self.assertEqual(invocation.breaker_before.status, "unknown")
        self.assertIsNone(invocation.breaker_before.failure_count)

    async def test_a_readable_breaker_is_recorded_exactly(self):
        sink = RecordingSink()
        breaker = BreakerStub()
        breaker.record_failure("anthropic:" + MODEL_ROUTING["classify"].model)
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session, breaker=breaker)
        await gateway.call(_request())
        await session.close()

        invocation = sink.invocation_starts[0]
        self.assertEqual(invocation.breaker_before.status, "valid")
        self.assertIn(invocation.breaker_before.state, ("closed", "open"))
        self.assertIsNotNone(invocation.breaker_before.failure_count)


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 6: a cancelled call leaves a durable start and a terminal."""

    async def test_cancellation_leaves_a_start_and_an_explicit_terminal(self):
        started = asyncio.Event()

        async def hanging(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            started.set()
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session, anthropic_executor=hanging)

        task = asyncio.create_task(gateway.call(_request()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        await session.drain(timeout=5.0)
        await session.close()

        # The start is durable even though the call never finished.
        self.assertEqual(len(sink.invocation_starts), 1)
        # …and the terminal state is explicitly a cancellation, not a failure.
        terminal = [event for event in sink.attempt_events if event.is_terminal]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].event_kind, EVENT_CANCELLED)
        self.assertEqual(terminal[0].subject_kind, SUBJECT_SDK_INVOCATION)

    async def test_a_process_stop_leaves_a_detectable_unmatched_start(self):
        """Requirement 8: simulated process stop leaves an unmatched start."""
        started = asyncio.Event()

        async def hanging(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            started.set()
            await asyncio.sleep(10)

        # A blocking sink models a process that stopped before its delivery
        # worker could write anything: the start is durable, the terminal event
        # never lands.
        sink = BlockingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session, anthropic_executor=hanging)

        task = asyncio.create_task(gateway.call(_request()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # The process disappears here: no drain, no flush, nothing written.
        await session.delivery.aclose(drain_timeout=0.1)

        starts = {record.invocation_id for record in sink.invocation_starts}
        terminals = {
            event.subject_id for event in sink.attempt_events if event.is_terminal
        }
        unmatched = starts - terminals
        self.assertEqual(len(unmatched), 1)


class SkippedCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_skipped_candidate_records_a_start_and_a_skipped_terminal(self):
        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = DefaultProviderGateway(
            anthropic_executor=_ok,
            openai_executor=_ok,
            cache=NoOpSemanticCache(),
            breaker=BreakerStub(),
            provider_availability={"anthropic": False, "openai": True},
            telemetry=session,
        )
        response = await gateway.call(_request())
        await session.drain(timeout=5.0)
        await session.close()

        self.assertFalse(response.error)
        kinds = [record.invocation_kind for record in sink.invocation_starts]
        self.assertIn(INVOCATION_SKIPPED_CANDIDATE, kinds)
        self.assertIn(INVOCATION_PROVIDER_CALL, kinds)

        skipped = [e for e in sink.attempt_events if e.event_kind == EVENT_SKIPPED]
        skipped_starts = [
            record for record in sink.invocation_starts
            if record.invocation_kind == INVOCATION_SKIPPED_CANDIDATE
        ]
        # Every skipped candidate gets its own start and exactly one terminal.
        self.assertEqual(len(skipped), len(skipped_starts))
        self.assertEqual(
            {event.subject_id for event in skipped},
            {record.invocation_id for record in skipped_starts},
        )
        for event in skipped:
            # A skipped candidate never reached a provider, so it can carry
            # nothing a provider could have supplied.
            self.assertTrue(event.observation.is_empty)
            self.assertEqual(event.transport_outcome, "")

    async def test_ordinals_are_one_based_and_unique_per_call(self):
        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(
            session,
            anthropic_executor=_failing("rate_limited"),
            openai_executor=_failing("rate_limited"),
            max_retries=2,
        )
        await gateway.call(_request())
        await session.drain(timeout=5.0)
        await session.close()

        ordinals = [record.attempt_ordinal for record in sink.invocation_starts]
        self.assertEqual(ordinals, sorted(ordinals))
        self.assertEqual(len(ordinals), len(set(ordinals)))
        self.assertTrue(all(value >= 1 for value in ordinals))
        for record in sink.invocation_starts:
            self.assertGreaterEqual(record.candidate_ordinal, 1)
            self.assertGreaterEqual(record.retry_ordinal, 1)


class StrictModeFailClosedTests(unittest.IsolatedAsyncioTestCase):
    """Strict posture is deliberately NOT behavior-neutral. Assert that."""

    async def test_a_strict_start_failure_prevents_the_provider_call(self):
        called = []

        async def executor(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            called.append(model)
            return LLMResponse(text="ok", ok=True, model_used=model)

        session = await _TelemetrySession(FailingSink(), posture=POSTURE_STRICT).start()
        gateway = _gateway(session, anthropic_executor=executor, openai_executor=executor)

        with self.assertRaises(TelemetryStartUnavailable):
            await gateway.call(_request())
        await session.close()

        # The whole point: no provider request was made.
        self.assertEqual(called, [])

    async def test_observational_start_failure_does_not_prevent_the_call(self):
        called = []

        async def executor(model, system, prompt, max_tokens, temperature, thinking_budget=0):
            called.append(model)
            return LLMResponse(text="ok", ok=True, model_used=model)

        session = await _TelemetrySession(
            FailingSink(), posture=POSTURE_OBSERVATIONAL
        ).start()
        gateway = _gateway(session, anthropic_executor=executor, openai_executor=executor)
        response = await gateway.call(_request())
        await session.close()

        self.assertFalse(response.error)
        self.assertEqual(len(called), 1)


class RecordedContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_prompt_or_response_text_reaches_any_record(self):
        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session)
        await gateway.call(
            _request()
            .__class__(
                phase="classify",
                system_prompt="SYSTEM_SENTINEL_DO_NOT_LEAK",
                user_prompt="PROMPT_SENTINEL_DO_NOT_LEAK sk-ant-SECRETSECRET",
                routing_context=RoutingContext(phase="classify"),
            )
        )
        await session.drain(timeout=5.0)
        await session.close()

        blob = repr(sink.rows)
        for sentinel in (
            "SYSTEM_SENTINEL_DO_NOT_LEAK",
            "PROMPT_SENTINEL_DO_NOT_LEAK",
            "sk-ant-SECRETSECRET",
        ):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, blob)

    async def test_routing_decision_is_frozen_into_the_invocation_record(self):
        sink = RecordingSink()
        session = await _TelemetrySession(sink).start()
        gateway = _gateway(session)
        await gateway.call(_request("classify"))
        await session.close()

        invocation = sink.invocation_starts[0]
        self.assertEqual(invocation.provider, "anthropic")
        self.assertEqual(invocation.requested_model, MODEL_ROUTING["classify"].model)
        self.assertRegex(invocation.routing_decision_fingerprint, r"^[0-9a-f]{64}$")
        self.assertRegex(invocation.request_config_fingerprint, r"^[0-9a-f]{64}$")
        # The start is written before the call, so it exists even though the
        # terminal event has not been delivered yet.
        self.assertTrue(sink.start_calls)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
