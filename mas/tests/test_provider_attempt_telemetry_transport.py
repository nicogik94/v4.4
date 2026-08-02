"""Transport-level instrumentation against the real Anthropic/OpenAI SDKs.

Covers required regression 9 — *every SDK-internal HTTP retry has a separate
attempt identity* — using the genuine SDK retry loop driven by a no-network mock
transport. Nothing here monkeypatches the SDK: the clients are constructed
normally with ``max_retries`` at a real value, and the only customization is the
``httpx`` transport, which is precisely the boundary under test.
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
    EVENT_PROVIDER_FAILURE,
    POSTURE_STRICT,
    SUBJECT_HTTP_ATTEMPT,
    TRANSPORT_CANCELLED,
    TRANSPORT_ERROR,
    TRANSPORT_RESPONSE,
)
from provider_telemetry.transport import TelemetryStartUnavailable  # noqa: E402
from provider_telemetry.values import VALUE_VALID  # noqa: E402
from tests.provider_telemetry_support import (  # noqa: E402
    anthropic_client_with,
    make_mock_transport,
    new_capture,
    openai_client_with,
)


class _Session:
    """A session double that records the fail-closed start writes."""

    def __init__(self, posture="observational", fail=False):
        self.posture = posture
        self.telemetry_run_id = "00000000-0000-4000-8000-000000000009"
        self.starts = []
        self.fail = fail

    @property
    def strict(self):
        return self.posture == POSTURE_STRICT

    async def persist_attempt_start(self, record):
        if self.fail:
            raise TelemetryStartUnavailable("sink refused the attempt start")
        self.starts.append(record)


class _SessionScope:
    """Bind a session double for the duration of a block."""

    def __init__(self, session):
        from provider_telemetry import service

        self._service = service
        self._session = session
        self._token = None

    def __enter__(self):
        self._token = self._service._session.set(self._session)  # noqa: SLF001
        return self._session

    def __exit__(self, *exc):
        self._service._session.reset(self._token)  # noqa: SLF001
        return False


class AnthropicRetryInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_sdk_internal_retry_gets_its_own_attempt_identity(self):
        """Requirement 9."""
        transport = make_mock_transport([429, 429, 200])
        client = anthropic_client_with(transport, max_retries=2)
        session = _Session()
        buffer = new_capture(None)

        with _SessionScope(session), capture.capture_scope(buffer):
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )

        # The SDK really did retry: three actual HTTP requests.
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(response.id, "msg_01TESTIDENTITY")

        # …and telemetry saw all three, each with its own identity and ordinal.
        self.assertEqual(len(buffer.http_attempts), 3)
        self.assertEqual(
            [record.http_retry_ordinal for record in buffer.http_attempts], [1, 2, 3]
        )
        self.assertEqual(
            len({record.attempt_id for record in buffer.http_attempts}), 3
        )
        # All three belong to the same SDK invocation.
        self.assertEqual(
            len({record.invocation_id for record in buffer.http_attempts}), 1
        )
        # Every start was persisted before the bytes left.
        self.assertEqual(len(session.starts), 3)

        http_events = [
            event for event in buffer.events if event.subject_kind == SUBJECT_HTTP_ATTEMPT
        ]
        self.assertEqual(len(http_events), 3)
        self.assertEqual(
            [event.http_status.value for event in http_events], [429, 429, 200]
        )
        self.assertEqual(
            [event.provider_request_id.value for event in http_events],
            ["req_1", "req_2", "req_3"],
        )
        for event in http_events:
            self.assertEqual(event.transport_outcome, TRANSPORT_RESPONSE)
            self.assertEqual(event.event_kind, EVENT_COMPLETED)

    async def test_retry_after_is_captured_only_from_the_named_header(self):
        transport = make_mock_transport([429, 200])
        client = anthropic_client_with(transport, max_retries=1)
        buffer = new_capture(None)
        with _SessionScope(_Session()), capture.capture_scope(buffer):
            await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )
        http_events = [
            event for event in buffer.events if event.subject_kind == SUBJECT_HTTP_ATTEMPT
        ]
        self.assertEqual(http_events[0].retry_after.status, VALUE_VALID)
        self.assertEqual(http_events[0].retry_after.value, "0")
        # The 200 carried no retry-after; that is absence, not zero.
        self.assertNotEqual(http_events[1].retry_after.status, VALUE_VALID)

    async def test_a_connection_error_is_recorded_for_every_attempt(self):
        transport = make_mock_transport(["connect"])
        client = anthropic_client_with(transport, max_retries=2)
        buffer = new_capture(None)

        with _SessionScope(_Session()), capture.capture_scope(buffer):
            with self.assertRaises(Exception):
                await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16,
                    messages=[{"role": "user", "content": "hi"}],
                )

        # A connection error produces no response object at all, which is exactly
        # why the instrumentation is a transport wrapper and not an event hook.
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(len(buffer.http_attempts), 3)
        http_events = [
            event for event in buffer.events if event.subject_kind == SUBJECT_HTTP_ATTEMPT
        ]
        self.assertEqual(len(http_events), 3)
        for event in http_events:
            self.assertEqual(event.transport_outcome, TRANSPORT_ERROR)
            self.assertEqual(event.event_kind, EVENT_PROVIDER_FAILURE)
            self.assertEqual(event.failure_class, "ConnectError")
            self.assertIsNone(event.http_status.stored)

    async def test_cancellation_at_the_transport_is_recorded_as_cancellation(self):
        transport = make_mock_transport(["cancel"])
        client = anthropic_client_with(transport, max_retries=0)
        buffer = new_capture(None)

        with _SessionScope(_Session()), capture.capture_scope(buffer):
            with self.assertRaises(asyncio.CancelledError):
                await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16,
                    messages=[{"role": "user", "content": "hi"}],
                )

        http_events = [
            event for event in buffer.events if event.subject_kind == SUBJECT_HTTP_ATTEMPT
        ]
        self.assertEqual(len(http_events), 1)
        self.assertEqual(http_events[0].transport_outcome, TRANSPORT_CANCELLED)
        self.assertEqual(http_events[0].event_kind, EVENT_CANCELLED)

    async def test_the_sdk_retry_policy_is_not_altered(self):
        # max_retries stays exactly where the caller set it: instrumentation
        # observes the policy, it never rewrites it.
        for retries, expected in ((0, 1), (1, 2), (3, 4)):
            with self.subTest(max_retries=retries):
                transport = make_mock_transport([429])
                client = anthropic_client_with(transport, max_retries=retries)
                buffer = new_capture(None)
                with _SessionScope(_Session()), capture.capture_scope(buffer):
                    with self.assertRaises(Exception):
                        await client.messages.create(
                            model="claude-sonnet-4-6",
                            max_tokens=16,
                            messages=[{"role": "user", "content": "hi"}],
                        )
                self.assertEqual(len(transport.requests), expected)
                self.assertEqual(len(buffer.http_attempts), expected)


class OpenAIRetryInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_internal_retries_are_separately_identified(self):
        transport = make_mock_transport([500, 200], provider="openai")
        client = openai_client_with(transport, max_retries=2)
        buffer = new_capture(None, provider="openai", model="gpt-4.1")

        with _SessionScope(_Session()), capture.capture_scope(buffer):
            response = await client.chat.completions.create(
                model="gpt-4.1", messages=[{"role": "user", "content": "hi"}]
            )

        self.assertEqual(response.id, "chatcmpl-TESTIDENTITY")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(len(buffer.http_attempts), 2)
        self.assertEqual(
            [record.http_retry_ordinal for record in buffer.http_attempts], [1, 2]
        )
        http_events = [
            event for event in buffer.events if event.subject_kind == SUBJECT_HTTP_ATTEMPT
        ]
        self.assertEqual([event.http_status.value for event in http_events], [500, 200])


class StrictFailClosedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_strict_start_failure_stops_the_request_leaving_the_wire(self):
        transport = make_mock_transport([200])
        client = anthropic_client_with(transport, max_retries=0)
        session = _Session(posture=POSTURE_STRICT, fail=True)
        buffer = new_capture(None)
        buffer.posture = POSTURE_STRICT

        with _SessionScope(session), capture.capture_scope(buffer):
            with self.assertRaises(Exception):
                await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16,
                    messages=[{"role": "user", "content": "hi"}],
                )

        # The guarantee strict posture sells: no start, no request.
        self.assertEqual(len(transport.requests), 0)

    async def test_observational_start_failure_still_sends_the_request(self):
        # Driven through the *real* session, not a double: the fail-open decision
        # is the session's, and this is the assertion that it makes it.
        from provider_telemetry.delivery import NullDelivery
        from provider_telemetry.models import POSTURE_OBSERVATIONAL, TelemetryRunRecord
        from provider_telemetry.service import TelemetrySession

        from tests.provider_telemetry_support import FailingSink

        transport = make_mock_transport([200])
        client = anthropic_client_with(transport, max_retries=0)
        session = TelemetrySession(
            run_record=TelemetryRunRecord(posture=POSTURE_OBSERVATIONAL),
            sink=FailingSink(),
            delivery=NullDelivery(),
        )
        buffer = new_capture(None)

        with _SessionScope(session), capture.capture_scope(buffer):
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(response.id, "msg_01TESTIDENTITY")
        self.assertEqual(len(transport.requests), 1)
        # The failure is counted, never hidden.
        self.assertEqual(session.local_start_failures, 1)

    async def test_a_misbehaving_session_cannot_stop_an_observational_request(self):
        transport = make_mock_transport([200])
        client = anthropic_client_with(transport, max_retries=0)

        class Misbehaving(_Session):
            async def persist_attempt_start(self, record):
                raise RuntimeError("session double is broken")

        buffer = new_capture(None)
        with _SessionScope(Misbehaving()), capture.capture_scope(buffer):
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )
        self.assertEqual(response.id, "msg_01TESTIDENTITY")
        self.assertEqual(len(transport.requests), 1)


class NoCaptureTransparencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_wrapper_is_transparent_when_nobody_is_capturing(self):
        transport = make_mock_transport([200])
        client = anthropic_client_with(transport, max_retries=0)
        # No capture scope: the transport delegates and records nothing at all.
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(response.id, "msg_01TESTIDENTITY")
        self.assertEqual(len(transport.requests), 1)


class RequestMetadataSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_path_is_recorded_never_the_query_string(self):
        transport = make_mock_transport([200])
        client = anthropic_client_with(transport, max_retries=0)
        buffer = new_capture(None)
        with _SessionScope(_Session()), capture.capture_scope(buffer):
            await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": "PROMPT_SENTINEL"}],
            )
        record = buffer.http_attempts[0]
        self.assertEqual(record.request_path, "/v1/messages")
        self.assertNotIn("?", record.request_path)
        blob = repr(buffer.http_attempts) + repr(buffer.events)
        self.assertNotIn("PROMPT_SENTINEL", blob)
        self.assertNotIn("sk-ant-test", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
