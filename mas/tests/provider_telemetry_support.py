"""Shared, test-only doubles for the provider-telemetry regression suite.

Everything here is a *double*, never a reimplementation: the sinks record what
the production code hands them, and the mock transports answer with bytes the
real Anthropic and OpenAI SDKs parse. No test in this suite reaches a network,
and no test asserts against a value this module invented.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import models, repository  # noqa: E402
from provider_telemetry.delivery import AmbiguousWrite  # noqa: E402


class RecordingSink:
    """An in-memory sink that records everything, in order."""

    def __init__(self) -> None:
        self.rows: dict[str, list[Any]] = {}
        self.start_calls: list[tuple[str, Any]] = []
        self.event_calls: list[Any] = []

    async def _pool(self):
        return None

    async def append_start(self, table: str, record: Any) -> None:
        self.start_calls.append((table, record))
        self.rows.setdefault(table, []).append(record)

    async def append_event(self, event: Any) -> None:
        self.event_calls.append(event)
        table = (
            repository.RUN_EVENT_TABLE
            if isinstance(event, models.RunEvent)
            else repository.EVENT_TABLE
        )
        self.rows.setdefault(table, []).append(event)

    # ── convenience accessors ──

    def table(self, table: str) -> list[Any]:
        return list(self.rows.get(table, []))

    @property
    def attempt_starts(self) -> list[Any]:
        return self.table(repository.ATTEMPT_TABLE)

    @property
    def invocation_starts(self) -> list[Any]:
        return self.table(repository.INVOCATION_TABLE)

    @property
    def attempt_events(self) -> list[Any]:
        return self.table(repository.EVENT_TABLE)

    def events_of_kind(self, kind: str) -> list[Any]:
        return [event for event in self.attempt_events if event.event_kind == kind]


class FailingSink(RecordingSink):
    """A sink whose writes always fail, definitively."""

    def __init__(self, *, fail_starts: bool = True, fail_events: bool = True) -> None:
        super().__init__()
        self.fail_starts = fail_starts
        self.fail_events = fail_events

    async def append_start(self, table: str, record: Any) -> None:
        if self.fail_starts:
            raise ValueError("sink refused the start")
        await super().append_start(table, record)

    async def append_event(self, event: Any) -> None:
        if self.fail_events:
            raise ValueError("sink refused the event")
        await super().append_event(event)


class AmbiguousSink(RecordingSink):
    """A sink whose writes may or may not have landed."""

    async def append_start(self, table: str, record: Any) -> None:
        raise AmbiguousWrite("ConnectionDoesNotExistError")

    async def append_event(self, event: Any) -> None:
        raise AmbiguousWrite("ConnectionDoesNotExistError")


class SlowSink(RecordingSink):
    """A sink that blocks for a controllable time before recording.

    Used to prove the audit's central claim: that a slow telemetry write cannot
    delay a retry, a fallback, a breaker transition, a cache write, a caller
    timeout, or a cancellation.
    """

    def __init__(self, delay: float = 0.25) -> None:
        super().__init__()
        self.delay = delay
        self.gate = asyncio.Event()

    async def append_start(self, table: str, record: Any) -> None:
        await super().append_start(table, record)

    async def append_event(self, event: Any) -> None:
        await asyncio.sleep(self.delay)
        await super().append_event(event)


class BlockingSink(RecordingSink):
    """A sink whose event writes never return until released."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.entered = 0

    async def append_event(self, event: Any) -> None:
        self.entered += 1
        await self.release.wait()
        await super().append_event(event)


# ─────────────────────────── breaker double ───────────────────────────


class BreakerStub:
    """Mirrors the production breaker's surface: is_open / record_failure / reset."""

    def __init__(self, open_keys: Optional[set] = None) -> None:
        self.open = set(open_keys or ())
        self.failures: dict[str, list] = {}
        self.reset_calls: list[str] = []
        self.failure_calls: list[str] = []

    def is_open(self, key: str) -> bool:
        return key in self.open

    def record_failure(self, key: str) -> None:
        self.failure_calls.append(key)
        self.failures.setdefault(key, []).append(1)

    def reset(self, key: str) -> None:
        self.reset_calls.append(key)
        self.failures.pop(key, None)


class ExplodingBreaker:
    """A breaker whose state cannot be read at all.

    Used only against the telemetry snapshot helper: a production breaker whose
    ``is_open`` raises would break routing long before telemetry sees it, so this
    double is deliberately not driven through a whole gateway call.
    """

    failures: dict = {}

    def is_open(self, key: str) -> bool:
        raise RuntimeError("breaker state is unavailable")

    def record_failure(self, key: str) -> None:  # pragma: no cover - unused
        pass

    def reset(self, key: str) -> None:  # pragma: no cover - unused
        pass


class HalfBrokenBreaker:
    """A breaker whose state reads but whose failure counter does not.

    The exact shape that produced the audit's fabricated "closed with zero
    failures" reading: the state read succeeded, the count read failed, and the
    two independent `except` clauses assembled a snapshot nobody ever took.
    Routing works perfectly against this breaker — only telemetry is affected.
    """

    def __init__(self) -> None:
        self._open: set = set()
        self.reset_calls: list = []
        self.failure_calls: list = []

    @property
    def failures(self):
        raise RuntimeError("failure counter is unavailable")

    def is_open(self, key: str) -> bool:
        return key in self._open

    def record_failure(self, key: str) -> None:
        self.failure_calls.append(key)

    def reset(self, key: str) -> None:
        self.reset_calls.append(key)


# ─────────────────────────── SDK mock transports ───────────────────────────


ANTHROPIC_BODY = {
    "id": "msg_01TESTIDENTITY",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "ok"}],
    "usage": {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2,
    },
}

OPENAI_BODY = {
    "id": "chatcmpl-TESTIDENTITY",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4.1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 3},
    },
}


def make_mock_transport(statuses, *, body=None, provider: str = "anthropic"):
    """An httpx transport that answers with real, parseable provider bytes.

    ``statuses`` is consumed one entry per **actual HTTP request**, so a list of
    ``[429, 429, 200]`` exercises the SDK's genuine internal retry loop with no
    network and no monkeypatching of the SDK itself. The entry ``"connect"``
    raises a real ``httpx.ConnectError``, which is how a transport failure with
    no response object is reproduced.
    """
    import httpx

    payload = json.dumps(body if body is not None else (
        ANTHROPIC_BODY if provider == "anthropic" else OPENAI_BODY
    )).encode("utf-8")

    class MockTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def handle_async_request(self, request):
            index = len(self.requests)
            self.requests.append(request)
            status = statuses[min(index, len(statuses) - 1)]
            if status == "connect":
                raise httpx.ConnectError("no network in tests", request=request)
            if status == "cancel":
                raise asyncio.CancelledError()
            headers = {
                "content-type": "application/json",
                "request-id": f"req_{index + 1}",
            }
            if status == 429:
                headers["retry-after"] = "0"
            return httpx.Response(
                status, headers=headers, content=payload, request=request
            )

    return MockTransport()


def anthropic_client_with(transport, *, max_retries: int = 2):
    """A real ``AsyncAnthropic`` whose only customization is the transport."""
    import anthropic
    import httpx

    from provider_telemetry.transport import build_telemetry_transport

    instrumented = build_telemetry_transport(transport, provider="anthropic")
    return anthropic.AsyncAnthropic(
        api_key="sk-ant-test-not-a-real-key",
        max_retries=max_retries,
        http_client=httpx.AsyncClient(
            transport=instrumented, base_url="https://api.anthropic.com"
        ),
    )


def openai_client_with(transport, *, max_retries: int = 2):
    """A real ``AsyncOpenAI`` whose only customization is the transport."""
    import httpx
    import openai

    from provider_telemetry.transport import build_telemetry_transport

    instrumented = build_telemetry_transport(transport, provider="openai")
    return openai.AsyncOpenAI(
        api_key="sk-test-not-a-real-key",
        max_retries=max_retries,
        http_client=httpx.AsyncClient(
            transport=instrumented, base_url="https://api.openai.com/v1"
        ),
    )


def new_capture(session, *, provider: str = "anthropic", model: str = "claude-sonnet-4-6"):
    from provider_telemetry.capture import InvocationCapture

    return InvocationCapture(
        invocation_id=models.new_identity(),
        call_id=models.new_identity(),
        telemetry_run_id=getattr(session, "telemetry_run_id", "") or models.new_identity(),
        posture=getattr(session, "posture", models.POSTURE_OBSERVATIONAL),
        worker_id="test-worker",
        provider=provider,
        requested_model=model,
    )
