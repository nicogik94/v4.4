"""The bounded delivery queue: backpressure, drain, and honest accounting.

Supports required regressions 7 and 23 — cancellation during completion
persistence is detectable, and the run-end drain detects missing and ambiguous
events. The contract asserted here is that *nothing is ever lost silently*: every
event that enters the queue leaves it through exactly one accounted-for
category.
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry.delivery import (  # noqa: E402
    DeliveryCounters,
    EventDelivery,
    NullDelivery,
)
from provider_telemetry.models import (  # noqa: E402
    DRAIN_DRAINED,
    DRAIN_TIMEOUT,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
)
from tests.provider_telemetry_support import (  # noqa: E402
    AmbiguousSink,
    BlockingSink,
    FailingSink,
    RecordingSink,
)


class _CancellingSink(RecordingSink):
    """A sink whose write is cancelled mid-flight."""

    async def append_event(self, event):
        raise asyncio.CancelledError()


class CountersTests(unittest.TestCase):
    def test_every_submitted_event_lands_in_exactly_one_category(self):
        counters = DeliveryCounters()
        counters.submitted = 10
        counters.delivered = 4
        counters.failed = 2
        counters.ambiguous = 1
        counters.dropped = 3
        self.assertEqual(counters.outstanding, 0)
        self.assertEqual(counters.undurable, 5)

    def test_outstanding_exposes_unaccounted_events(self):
        counters = DeliveryCounters()
        counters.submitted = 5
        counters.delivered = 2
        self.assertEqual(counters.outstanding, 3)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_clean_run_drains_and_is_certifiable(self):
        sink = RecordingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT)
        await delivery.start()
        for index in range(5):
            self.assertTrue(delivery.submit(f"event-{index}"))
        result = await delivery.drain(timeout=5.0)
        await delivery.aclose()

        self.assertEqual(result.status, DRAIN_DRAINED)
        self.assertTrue(result.certifiable)
        self.assertEqual(result.counters["delivered"], 5)
        self.assertEqual(result.counters["undurable"], 0)
        self.assertEqual(len(sink.event_calls), 5)

    async def test_a_definite_write_failure_is_undurable_and_not_certifiable(self):
        delivery = EventDelivery(FailingSink(), posture=POSTURE_STRICT)
        await delivery.start()
        delivery.submit("event")
        result = await delivery.drain(timeout=5.0)
        await delivery.aclose()

        self.assertEqual(result.status, DRAIN_DRAINED)
        self.assertFalse(result.certifiable)
        self.assertEqual(result.counters["failed"], 1)
        self.assertEqual(result.counters["undurable"], 1)

    async def test_an_ambiguous_write_is_neither_delivered_nor_failed(self):
        delivery = EventDelivery(AmbiguousSink(), posture=POSTURE_STRICT)
        await delivery.start()
        delivery.submit("event")
        result = await delivery.drain(timeout=5.0)
        await delivery.aclose()

        # "Maybe written" is its own state: counting it as written would
        # over-report, and counting it as lost would under-report.
        self.assertEqual(result.counters["ambiguous"], 1)
        self.assertEqual(result.counters["delivered"], 0)
        self.assertEqual(result.counters["failed"], 0)
        self.assertFalse(result.certifiable)

    async def test_cancellation_during_a_write_is_recorded_as_ambiguous(self):
        """Requirement 7: cancellation during completion persistence is detectable."""
        delivery = EventDelivery(_CancellingSink(), posture=POSTURE_STRICT)
        await delivery.start()
        delivery.submit("event")
        # The worker's own task dies from the cancellation; the counter is set
        # before it propagates, so the loss is visible rather than silent.
        await asyncio.sleep(0.05)
        self.assertEqual(delivery.counters.ambiguous, 1)
        self.assertIn("cancelled_during_write", delivery.counters.failures_by_reason)
        await delivery.aclose(drain_timeout=0.2)

    async def test_a_full_queue_drops_and_counts_rather_than_blocking(self):
        sink = BlockingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=2)
        await delivery.start()
        # The worker takes the first event and blocks in the sink; the queue then
        # holds two before it is full.
        accepted = [delivery.submit(f"event-{index}") for index in range(10)]
        await asyncio.sleep(0)

        self.assertIn(False, accepted)  # backpressure engaged
        self.assertGreater(delivery.counters.dropped, 0)
        self.assertIn("queue_full", delivery.counters.failures_by_reason)

        sink.release.set()
        result = await delivery.aclose(drain_timeout=2.0)
        # A strict run that dropped anything is not certifiable, by construction.
        self.assertFalse(result.certifiable)

    async def test_submitting_never_raises_even_when_delivery_is_not_running(self):
        delivery = EventDelivery(RecordingSink(), posture=POSTURE_OBSERVATIONAL)
        # Never started.
        self.assertFalse(delivery.submit("event"))
        self.assertEqual(delivery.counters.dropped, 1)
        self.assertIn("delivery_not_running", delivery.counters.failures_by_reason)

    async def test_a_wedged_sink_produces_a_timeout_not_a_success(self):
        sink = BlockingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=8)
        await delivery.start()
        delivery.submit("event")
        result = await delivery.drain(timeout=0.2)
        self.assertEqual(result.status, DRAIN_TIMEOUT)
        self.assertFalse(result.certifiable)
        sink.release.set()
        await delivery.aclose(drain_timeout=1.0)

    async def test_shutdown_counts_abandoned_events(self):
        sink = BlockingSink()
        delivery = EventDelivery(sink, posture=POSTURE_STRICT, capacity=16)
        await delivery.start()
        for index in range(5):
            delivery.submit(f"event-{index}")
        await asyncio.sleep(0)
        result = await delivery.aclose(drain_timeout=0.1)

        self.assertNotEqual(result.status, DRAIN_DRAINED)
        # Every abandoned event is counted; none simply disappears.
        self.assertGreater(delivery.counters.dropped, 0)
        self.assertIn("abandoned_at_shutdown", delivery.counters.failures_by_reason)
        sink.release.set()

    async def test_null_delivery_is_inert_but_still_reports_drained(self):
        delivery = NullDelivery()
        await delivery.start()
        self.assertFalse(delivery.submit("event"))
        result = await delivery.drain()
        self.assertEqual(result.status, DRAIN_DRAINED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
