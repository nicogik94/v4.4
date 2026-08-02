"""Strict posture is a property of the worker, and every entry point proves it.

The finding: strict fail-closed behavior was enforced only where a capture scope
existed. ``handle_async_request`` read ``current_capture()``, found ``None`` for
any call made outside a scope, and took the transparent path — so a strict
experiment could make real, unrecorded provider requests from any code path that
never opened one, and the run would reconcile clean because it had nothing to
reconcile them against.

A paired experiment does not need "calls inside this block are recorded". It
needs "this worker made no provider call outside the experiment". These tests
assert the second thing, at both enforcement points — the entry point, where a
caller gets a clear failure, and the transport, which catches anything that
never went through an entry point at all.

No test here reaches a network: the refusals happen strictly before transport,
and the one case that is *allowed* through uses a mock transport.
"""
import asyncio
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

from provider_telemetry import posture, service  # noqa: E402
from provider_telemetry.identity import (  # noqa: E402
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_DIRECT_GATEWAY,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_T1A_VALIDATION,
)
from provider_telemetry.models import POSTURE_OBSERVATIONAL, POSTURE_STRICT  # noqa: E402
from provider_telemetry.posture import (  # noqa: E402
    NON_EXPERIMENT_ENV,
    StrictPostureMisconfigured,
    StrictPostureViolation,
)

POSTURE_ENV = config.PROVIDER_TELEMETRY_POSTURE_ENV
COMPAT_ENV = config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV

# Every supported entry point, so "which ones are covered" is a list rather than
# a claim. The ninth — direct gateway — is the one with no scope of its own,
# which is exactly why it needs the process-wide guard.
SUPPORTED_ENTRY_POINTS = (
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_T1A_VALIDATION,
    ENTRY_POINT_DIRECT_GATEWAY,
)


@contextmanager
def environment(**values):
    """Set exactly these telemetry variables, clearing the rest."""
    names = (POSTURE_ENV, COMPAT_ENV, NON_EXPERIMENT_ENV)
    saved = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    for name, value in values.items():
        if value is not None:
            os.environ[name] = value
    try:
        yield
    finally:
        for name in names:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


@contextmanager
def strict_worker():
    with environment(**{POSTURE_ENV: "strict"}):
        yield


@contextmanager
def bound_session(posture_name=POSTURE_STRICT, run_id="00000000-0000-4000-8000-000000000031"):
    """Bind a session the way ``run_session`` binds one, without a database."""

    class _Session:
        def __init__(self):
            self.posture = posture_name
            self.telemetry_run_id = run_id

        @property
        def strict(self):
            return self.posture == POSTURE_STRICT

    token = service._session.set(_Session())
    try:
        yield
    finally:
        service._session.reset(token)


class ConfigurationTests(unittest.TestCase):
    """A configuration that cannot mean one thing fails, it does not resolve."""

    def test_strict_posture_makes_the_worker_strict_required(self):
        with strict_worker():
            self.assertTrue(posture.strict_required())
            self.assertFalse(posture.non_experiment())

    def test_observational_and_off_are_not_strict_required(self):
        for value in ("observational", "off", None):
            with self.subTest(posture=value):
                with environment(**{POSTURE_ENV: value}):
                    self.assertFalse(posture.strict_required())

    def test_an_unknown_posture_is_a_configuration_problem(self):
        with environment(**{POSTURE_ENV: "STRICTISH"}):
            self.assertIn("unknown_posture", posture.configuration_problems())
            with self.assertRaises(StrictPostureMisconfigured):
                posture.require_valid_configuration()

    def test_strict_with_the_compat_flag_disabled_is_refused(self):
        """Two operators disagreeing is not a precedence question."""
        with environment(**{POSTURE_ENV: "strict", COMPAT_ENV: "0"}):
            problems = posture.configuration_problems()
            self.assertIn("strict_posture_with_disabled_compat_flag", problems)
            with self.assertRaises(StrictPostureMisconfigured):
                posture.require_valid_configuration()

    def test_a_malformed_compat_flag_is_refused(self):
        with environment(**{POSTURE_ENV: "strict", COMPAT_ENV: "maybe"}):
            self.assertIn("unknown_compat_flag", posture.configuration_problems())

    def test_the_opt_out_must_not_be_combined_with_a_strict_posture(self):
        with environment(**{POSTURE_ENV: "strict", NON_EXPERIMENT_ENV: "1"}):
            self.assertIn(
                "strict_posture_with_non_experiment_optout",
                posture.configuration_problems(),
            )

    def test_a_valid_configuration_has_no_problems(self):
        for values in (
            {POSTURE_ENV: "strict"},
            {POSTURE_ENV: "observational"},
            {POSTURE_ENV: "off"},
            {},
        ):
            with self.subTest(values=values):
                with environment(**values):
                    self.assertEqual(posture.configuration_problems(), [])

    def test_the_opt_out_is_explicit_and_disarms_the_guard(self):
        with environment(**{NON_EXPERIMENT_ENV: "1"}):
            self.assertTrue(posture.non_experiment())
            self.assertFalse(posture.strict_required())
            posture.enforce_provider_call("test")  # must not raise


class EntryPointRefusalTests(unittest.TestCase):
    """Every supported entry point verifies the worker posture."""

    def test_an_unscoped_call_is_refused_at_every_entry_point(self):
        with strict_worker():
            for entry_point in SUPPORTED_ENTRY_POINTS:
                with self.subTest(entry_point=entry_point):
                    with self.assertRaises(StrictPostureViolation) as caught:
                        posture.enforce_provider_call(entry_point)
                    self.assertIn(entry_point, str(caught.exception))
                    self.assertIn("was not sent", str(caught.exception))

    def test_a_call_inside_a_valid_strict_scope_is_permitted(self):
        with strict_worker(), bound_session(POSTURE_STRICT):
            for entry_point in SUPPORTED_ENTRY_POINTS:
                with self.subTest(entry_point=entry_point):
                    posture.enforce_provider_call(entry_point)

    def test_an_observational_scope_inside_a_strict_worker_does_not_satisfy_it(self):
        """A scope of the wrong posture is not a scope for this purpose."""
        with strict_worker(), bound_session(POSTURE_OBSERVATIONAL):
            with self.assertRaises(StrictPostureViolation):
                posture.enforce_provider_call(ENTRY_POINT_DIRECT_GATEWAY)

    def test_a_session_without_a_run_id_does_not_satisfy_it(self):
        with strict_worker(), bound_session(POSTURE_STRICT, run_id=""):
            with self.assertRaises(StrictPostureViolation):
                posture.enforce_provider_call(ENTRY_POINT_DIRECT_GATEWAY)

    def test_a_non_strict_worker_is_unaffected(self):
        for value in ("observational", "off", None):
            with self.subTest(posture=value):
                with environment(**{POSTURE_ENV: value}):
                    for entry_point in SUPPORTED_ENTRY_POINTS:
                        posture.enforce_provider_call(entry_point)


class ScopeDowngradeTests(unittest.IsolatedAsyncioTestCase):
    """Nested scopes cannot weaken a strict-required worker."""

    async def test_an_observational_scope_cannot_be_opened(self):
        with strict_worker():
            with self.assertRaises(StrictPostureViolation):
                async with service.run_session(posture=POSTURE_OBSERVATIONAL):
                    pass  # pragma: no cover - the context must not open

    async def test_an_off_scope_cannot_be_opened(self):
        with strict_worker():
            with self.assertRaises(StrictPostureViolation):
                async with service.run_session(posture="off"):
                    pass  # pragma: no cover - the context must not open

    async def test_a_nested_downgrade_inside_a_strict_scope_is_refused(self):
        with strict_worker(), bound_session(POSTURE_STRICT):
            with self.assertRaises(StrictPostureViolation):
                async with service.run_session(posture=POSTURE_OBSERVATIONAL):
                    pass  # pragma: no cover - the context must not open

    async def test_an_off_scope_is_still_fine_in_a_non_strict_worker(self):
        with environment(**{POSTURE_ENV: "off"}):
            async with service.run_session(posture="off") as session:
                self.assertEqual(session.posture, "off")


class TransportRefusalTests(unittest.IsolatedAsyncioTestCase):
    """The wire is the last place the invariant can still be true."""

    def _client(self, transport):
        from provider_telemetry.transport import build_telemetry_transport

        import httpx

        return httpx.AsyncClient(
            transport=build_telemetry_transport(transport, provider="anthropic")
        )

    async def test_an_unscoped_request_never_reaches_the_inner_transport(self):
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200])
        client = self._client(inner)
        try:
            with strict_worker():
                with self.assertRaises(StrictPostureViolation):
                    await client.get("https://api.anthropic.com/v1/messages")
            self.assertEqual(
                inner.requests, [],
                "the request must be refused before transport, not after",
            )
        finally:
            await client.aclose()

    async def test_a_direct_sdk_use_that_skipped_every_entry_point_is_refused(self):
        """The path MAJ-3 describes: nobody called an entry point at all."""
        from tests.provider_telemetry_support import (
            anthropic_client_with,
            make_mock_transport,
        )

        inner = make_mock_transport([200])
        client = anthropic_client_with(inner, max_retries=0)
        try:
            with strict_worker():
                with self.assertRaises(Exception) as caught:
                    await client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=16,
                        messages=[{"role": "user", "content": "hi"}],
                    )
            # The SDK wraps transport errors; the refusal is the cause either way.
            chain = []
            error = caught.exception
            while error is not None and error not in chain:
                chain.append(error)
                error = error.__cause__ or error.__context__
            self.assertTrue(
                any(isinstance(item, StrictPostureViolation) for item in chain),
                f"expected a posture violation in {chain}",
            )
            self.assertEqual(inner.requests, [])
        finally:
            await client.close()

    async def test_a_scoped_request_still_goes_through(self):
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200])
        client = self._client(inner)
        try:
            with strict_worker(), bound_session(POSTURE_STRICT):
                response = await client.get("https://api.anthropic.com/v1/messages")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(inner.requests), 1)
        finally:
            await client.aclose()

    async def test_a_non_strict_worker_is_unaffected_at_the_transport(self):
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200])
        client = self._client(inner)
        try:
            with environment(**{POSTURE_ENV: "observational"}):
                response = await client.get("https://api.anthropic.com/v1/messages")
            self.assertEqual(response.status_code, 200)
        finally:
            await client.aclose()

    async def test_concurrent_unscoped_calls_are_each_refused(self):
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200])
        client = self._client(inner)
        try:
            with strict_worker():
                results = await asyncio.gather(
                    *(
                        client.get("https://api.anthropic.com/v1/messages")
                        for _ in range(8)
                    ),
                    return_exceptions=True,
                )
            self.assertEqual(len(results), 8)
            for result in results:
                self.assertIsInstance(result, StrictPostureViolation)
            self.assertEqual(inner.requests, [])
        finally:
            await client.aclose()

    async def test_a_scope_does_not_leak_into_a_concurrent_task(self):
        """Two tasks, one scoped and one not: the unscoped one is still refused.

        The scope is a context variable, so a task started *outside* it does not
        inherit it — which is precisely the situation where a per-scope guard
        gives the wrong answer and a per-worker guard gives the right one.
        """
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200, 200])
        client = self._client(inner)
        started = asyncio.Event()

        async def unscoped():
            await started.wait()
            return await client.get("https://api.anthropic.com/v1/messages")

        try:
            with strict_worker():
                task = asyncio.create_task(unscoped())
                with bound_session(POSTURE_STRICT):
                    started.set()
                    scoped = await client.get("https://api.anthropic.com/v1/messages")
                with self.assertRaises(StrictPostureViolation):
                    await task
            self.assertEqual(scoped.status_code, 200)
            self.assertEqual(len(inner.requests), 1)
        finally:
            await client.aclose()

    async def test_cancellation_leaves_the_worker_posture_intact(self):
        """A cancelled scoped call must not leave the scope bound behind it."""
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport(["cancel"])
        client = self._client(inner)
        try:
            with strict_worker():
                with bound_session(POSTURE_STRICT):
                    with self.assertRaises(asyncio.CancelledError):
                        await client.get("https://api.anthropic.com/v1/messages")
                # Out of the scope again: the guard is armed exactly as before.
                with self.assertRaises(StrictPostureViolation):
                    await client.get("https://api.anthropic.com/v1/messages")
        finally:
            await client.aclose()


class GatewayEntryPointTests(unittest.IsolatedAsyncioTestCase):
    """The gateway refuses at the top of the stack, before any work."""

    def _gateway(self):
        from llm_client import LLMResponse
        from runtime.cache import NoOpSemanticCache
        from runtime.provider_gateway import DefaultProviderGateway
        from tests.provider_telemetry_support import BreakerStub

        self.executed = []

        async def executor(model, system, prompt, max_tokens, temperature,
                           thinking_budget=0):
            self.executed.append(model)
            return LLMResponse(text="ok", ok=True, model_used=model)

        return DefaultProviderGateway(
            anthropic_executor=executor,
            openai_executor=executor,
            cache=NoOpSemanticCache(),
            breaker=BreakerStub(),
            max_retries=1,
            telemetry=None,
        )

    def _request(self):
        from extensions.runtime import GatewayRequest, RoutingContext

        return GatewayRequest(
            phase="classify",
            system_prompt="system",
            user_prompt="prompt",
            routing_context=RoutingContext(phase="classify"),
        )

    async def test_a_direct_gateway_call_without_a_scope_is_refused(self):
        gateway = self._gateway()
        with strict_worker():
            with self.assertRaises(StrictPostureViolation):
                await gateway.call(self._request())
        self.assertEqual(
            self.executed, [], "no executor may run for a refused call"
        )

    async def test_a_direct_gateway_call_inside_a_scope_proceeds(self):
        """A real session, because the gateway persists starts through it."""
        from provider_telemetry.delivery import EventDelivery
        from provider_telemetry.models import TelemetryRunRecord
        from tests.provider_telemetry_support import RecordingSink

        sink = RecordingSink()
        run = TelemetryRunRecord(
            posture=POSTURE_STRICT, telemetry_required=True, entry_point="cli_workflow"
        )
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=64)
        await delivery.start()
        session = service.TelemetrySession(
            run_record=run, sink=sink, delivery=delivery
        )
        token = service._session.set(session)
        gateway = self._gateway()
        try:
            with strict_worker():
                response = await gateway.call(self._request())
        finally:
            service._session.reset(token)
            await delivery.aclose(drain_timeout=5.0)
        self.assertEqual(response.text, "ok")
        self.assertEqual(len(self.executed), 1)
        self.assertTrue(sink.attempt_starts or sink.invocation_starts)

    async def test_a_non_strict_worker_calls_the_gateway_normally(self):
        gateway = self._gateway()
        with environment(**{POSTURE_ENV: "observational"}):
            response = await gateway.call(self._request())
        self.assertEqual(response.text, "ok")


class StrictPreflightTests(unittest.IsolatedAsyncioTestCase):
    """A strict run refuses to start in a worker that is not strict-required."""

    class _Sink:
        async def _pool(self):
            return None

    async def test_a_non_experiment_worker_cannot_start_a_strict_run(self):
        with environment(**{POSTURE_ENV: "strict", NON_EXPERIMENT_ENV: "1"}):
            result = await service.strict_preflight(self._Sink())
        self.assertFalse(result.healthy)
        self.assertIn("worker_is_not_strict_required", result.reasons)
        self.assertTrue(
            any(reason.startswith("configuration:") for reason in result.reasons)
        )

    async def test_a_misconfigured_worker_cannot_start_a_strict_run(self):
        with environment(**{POSTURE_ENV: "strict", COMPAT_ENV: "0"}):
            result = await service.strict_preflight(self._Sink())
        self.assertFalse(result.healthy)
        self.assertIn(
            "configuration:strict_posture_with_disabled_compat_flag", result.reasons
        )

    async def test_the_scope_state_reports_the_worker_truthfully(self):
        with strict_worker():
            state = posture.scope_state()
            self.assertEqual(state["posture"], "strict")
            self.assertTrue(state["strict_required"])
            self.assertFalse(state["in_strict_scope"])
            with bound_session(POSTURE_STRICT):
                self.assertTrue(posture.scope_state()["in_strict_scope"])


class ClientConstructionTests(unittest.TestCase):
    """A strict worker never receives an uninstrumented client."""

    def test_instrumentation_failure_raises_instead_of_being_swallowed(self):
        import llm_client
        from provider_telemetry.transport import TelemetryTransportUnsupported

        original = llm_client.telemetry_transport.instrument_sdk_client

        class _Sdk:
            """Stands in for a constructed SDK client. Never sends anything."""

        def explode(sdk_client, **_kwargs):
            raise TelemetryTransportUnsupported("cannot instrument this build")

        llm_client.telemetry_transport.instrument_sdk_client = explode
        try:
            sdk = _Sdk()
            with strict_worker():
                with self.assertRaises(TelemetryTransportUnsupported):
                    llm_client._instrument_provider_client(sdk, "anthropic")
            with environment(**{POSTURE_ENV: "observational"}):
                # Observational still fails open: telemetry never changes what
                # the runtime does in that posture. The SDK keeps the client it
                # built for itself — the same one a telemetry-off build gets —
                # rather than receiving a substitute.
                self.assertIs(
                    llm_client._instrument_provider_client(sdk, "anthropic"), sdk
                )
        finally:
            llm_client.telemetry_transport.instrument_sdk_client = original


class WorkerPostureMutationTests(unittest.IsolatedAsyncioTestCase):
    """Remove the process-wide guard and an unscoped request goes through."""

    async def test_without_the_transport_guard_an_unscoped_request_is_sent(self):
        from provider_telemetry import transport as telemetry_transport
        from tests.provider_telemetry_support import make_mock_transport

        inner = make_mock_transport([200])
        from provider_telemetry.transport import build_telemetry_transport

        import httpx

        client = httpx.AsyncClient(
            transport=build_telemetry_transport(inner, provider="anthropic")
        )
        original = telemetry_transport._enforce_worker_posture
        telemetry_transport._enforce_worker_posture = lambda: None
        try:
            with strict_worker():
                response = await client.get("https://api.anthropic.com/v1/messages")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                len(inner.requests), 1,
                "the mutation must reproduce the finding: an unscoped, "
                "unrecorded provider request reaching the wire",
            )
        finally:
            telemetry_transport._enforce_worker_posture = original
            await client.aclose()

    async def test_without_the_gateway_guard_an_unscoped_call_executes(self):
        from runtime import provider_gateway

        original = provider_gateway.telemetry_posture.enforce_provider_call
        provider_gateway.telemetry_posture.enforce_provider_call = lambda _entry: None
        try:
            tests = GatewayEntryPointTests("test_a_direct_gateway_call_without_a_scope_is_refused")
            gateway = tests._gateway()
            with strict_worker():
                response = await gateway.call(tests._request())
            self.assertEqual(response.text, "ok")
            self.assertEqual(len(tests.executed), 1)
        finally:
            provider_gateway.telemetry_posture.enforce_provider_call = original


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
