"""Isolated, non-blocking delivery of telemetry completion events.

Blocker 1 of the audit: awaited telemetry writes can change provider/model
selection, cache behavior, timeout results and cancellation behavior. The
previous design awaited a database INSERT *inside* the routing loop, between the
provider returning and the gateway deciding whether to retry — so a slow sink
delayed a fallback, and a sink that hung changed a caller's timeout outcome.

The fix is not "make the write faster". It is to take the write off the caller's
path entirely:

* completion events are **submitted**, never awaited, at every site listed in
  the audit (retry selection, fallback selection, breaker transitions, cache
  population, successful provider return, response transformation);
* a single dedicated worker task owns the sink and drains a bounded queue;
* the run cannot be certified unless that worker drained successfully.

Asynchrony is safe *here specifically* because an unmatched start is
independently detectable: the start row is written synchronously before
transport, so a completion that never lands leaves a start with no terminal
event, which reconciliation reports as an incomplete run rather than as a clean
one. The reverse arrangement — async starts, sync completions — would have no
such property, which is why starts are not delivered through this queue.

Backpressure is explicit and has no silent branch:

``observational``
    A full queue drops the event and increments ``dropped_events``. The run is
    already documented as not guaranteeing completeness.
``strict``
    A full queue *also* drops — blocking would reintroduce blocker 1 — but the
    drop is recorded as an ambiguity, and any ambiguity makes the run
    **uncertified**. A strict run either proves it delivered everything or says
    plainly that it did not.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import (
    DRAIN_DRAINED,
    DRAIN_FAILED,
    DRAIN_TIMEOUT,
    DRAIN_UNKNOWN,
    POSTURE_STRICT,
)

logger = logging.getLogger(__name__)

DEFAULT_CAPACITY = 4096
DEFAULT_DRAIN_TIMEOUT = 30.0


class AmbiguousWrite(Exception):
    """A durable write whose result could not be determined.

    Raised (or wrapped) when the connection failed at a point where the row may
    or may not have been committed. It is deliberately *not* a failure: a run
    that treats "maybe written" as "not written" would under-report, and one
    that treats it as "written" would over-report. It is its own state, and it
    makes a strict run uncertified.
    """


@dataclass
class DeliveryCounters:
    """Durable accounting for everything that entered or left the queue."""

    submitted: int = 0
    delivered: int = 0
    failed: int = 0
    ambiguous: int = 0
    dropped: int = 0
    failures_by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def undurable(self) -> int:
        """Events known not to have reached durable storage."""
        return self.failed + self.dropped

    @property
    def outstanding(self) -> int:
        """Submitted events not yet accounted for in any terminal category."""
        return self.submitted - (
            self.delivered + self.failed + self.ambiguous + self.dropped
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "submitted": self.submitted,
            "delivered": self.delivered,
            "failed": self.failed,
            "ambiguous": self.ambiguous,
            "dropped": self.dropped,
            "undurable": self.undurable,
            "outstanding": self.outstanding,
            "failures_by_reason": dict(self.failures_by_reason),
        }


@dataclass(frozen=True)
class DrainResult:
    """What a flush barrier actually established."""

    status: str
    counters: dict[str, Any]

    @property
    def drained(self) -> bool:
        return self.status == DRAIN_DRAINED

    @property
    def certifiable(self) -> bool:
        """A drain that proves nothing was lost and nothing was ambiguous."""
        return (
            self.status == DRAIN_DRAINED
            and self.counters.get("undurable", 1) == 0
            and self.counters.get("ambiguous", 1) == 0
            and self.counters.get("outstanding", 1) == 0
        )


class EventDelivery:
    """A bounded queue, one dedicated worker, and honest accounting."""

    def __init__(
        self,
        sink: Any,
        *,
        posture: str,
        capacity: int = DEFAULT_CAPACITY,
        owner: str = "",
    ) -> None:
        self._sink = sink
        self._posture = posture
        self._capacity = max(1, int(capacity))
        self._owner = owner
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = False
        self.counters = DeliveryCounters()

    # ─────────────────────────── lifecycle ───────────────────────────

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self) -> None:
        """Bind the queue and worker to the running loop.

        Process/run ownership is recorded: a delivery created on one loop is
        never reused from another, because a queue shared across loops is the
        classic source of silently lost tasks.
        """
        if self._worker is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._capacity)
        self._closed = False
        self._worker = asyncio.create_task(
            self._run(), name=f"provider-telemetry-delivery:{self._owner or 'process'}"
        )

    def submit(self, event: Any) -> bool:
        """Hand an event to the worker. Never blocks, never awaits, never raises.

        Returns True when the event entered the queue. A False return is always
        accompanied by a counter increment, so a dropped event is visible in the
        run attestation rather than silently absent.
        """
        queue = self._queue
        if queue is None or self._closed:
            self.counters.dropped += 1
            self._note("delivery_not_running")
            return False
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self.counters.dropped += 1
            self._note("queue_full")
            if self._posture == POSTURE_STRICT:
                logger.warning(
                    "provider-telemetry strict run dropped a completion event: the "
                    "delivery queue (capacity=%s) was full. The run will be reported "
                    "as uncertified; provider routing, retry, fallback, breaker and "
                    "cache behavior are unaffected.",
                    self._capacity,
                )
            return False
        self.counters.submitted += 1
        return True

    async def drain(self, timeout: float = DEFAULT_DRAIN_TIMEOUT) -> DrainResult:
        """The durable flush barrier.

        Waits for every queued event to be accounted for, bounded by ``timeout``
        so a wedged sink cannot hang a run's shutdown forever. A timeout is
        reported as a timeout, not as success.
        """
        if self._queue is None or self._worker is None:
            return DrainResult(DRAIN_UNKNOWN, self.counters.snapshot())
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return DrainResult(DRAIN_TIMEOUT, self.counters.snapshot())
        except asyncio.CancelledError:
            # Cancellation of the *drain* is not cancellation of the run's
            # accounting; report it truthfully and re-raise so the caller's
            # cancellation semantics are preserved exactly.
            raise
        if self._worker.done():
            # `.exception()` re-raises for a cancelled task, so cancellation is
            # checked first; either way a dead worker means the drain did not
            # establish what a drain is supposed to establish.
            if self._worker.cancelled() or self._worker.exception() is not None:
                return DrainResult(DRAIN_FAILED, self.counters.snapshot())
        counters = self.counters.snapshot()
        status = DRAIN_DRAINED if counters["outstanding"] == 0 else DRAIN_FAILED
        return DrainResult(status, counters)

    async def aclose(self, *, drain_timeout: float = DEFAULT_DRAIN_TIMEOUT) -> DrainResult:
        """Drain, then stop the worker. Safe to call from a cancelled scope."""
        self._closed = True
        result = DrainResult(DRAIN_UNKNOWN, self.counters.snapshot())
        if self._worker is None:
            return result
        try:
            result = await asyncio.shield(self.drain(timeout=drain_timeout))
        except asyncio.CancelledError:
            # Shutdown is still completed: an event left in the queue must be
            # counted, never abandoned unrecorded.
            result = DrainResult(DRAIN_TIMEOUT, self.counters.snapshot())
            raise
        finally:
            self._stop_worker()
            self._count_abandoned()
        return result

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()

    def _count_abandoned(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.counters.dropped += 1
            self._note("abandoned_at_shutdown")
            queue.task_done()

    # ─────────────────────────── the worker ───────────────────────────

    async def _run(self) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            event = await queue.get()
            try:
                await self._deliver(event)
            finally:
                queue.task_done()

    async def _deliver(self, event: Any) -> None:
        try:
            await self._sink.append_event(event)
        except asyncio.CancelledError:
            # The write may or may not have committed, so the ambiguity is
            # recorded before anything else happens.
            self.counters.ambiguous += 1
            self._note("cancelled_during_write")
            # Then: was *this task* actually cancelled, or did the sink merely
            # raise CancelledError on its own? Only the former is a shutdown, and
            # only the former may propagate — swallowing a real cancellation
            # would change shutdown semantics, while propagating a spurious one
            # would kill the worker and silently strand every later event.
            task = asyncio.current_task()
            if task is not None and task.cancelling() == 0:
                logger.warning(
                    "provider-telemetry sink raised CancelledError without this "
                    "worker being cancelled; the event is recorded as ambiguous "
                    "and the worker continues"
                )
                return
            raise
        except AmbiguousWrite as exc:
            self.counters.ambiguous += 1
            self._note(f"ambiguous:{type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - telemetry failures never escape
            self.counters.failed += 1
            self._note(type(exc).__name__)
        else:
            self.counters.delivered += 1

    def _note(self, reason: str) -> None:
        key = str(reason)[:64]
        self.counters.failures_by_reason[key] = (
            self.counters.failures_by_reason.get(key, 0) + 1
        )


class NullDelivery:
    """What a disabled build holds: submissions are counted and discarded."""

    def __init__(self) -> None:
        self.counters = DeliveryCounters()

    @property
    def running(self) -> bool:
        return False

    async def start(self) -> None:
        return None

    def submit(self, event: Any) -> bool:
        return False

    async def drain(self, timeout: float = DEFAULT_DRAIN_TIMEOUT) -> DrainResult:
        return DrainResult(DRAIN_DRAINED, self.counters.snapshot())

    async def aclose(self, *, drain_timeout: float = DEFAULT_DRAIN_TIMEOUT) -> DrainResult:
        return DrainResult(DRAIN_DRAINED, self.counters.snapshot())
