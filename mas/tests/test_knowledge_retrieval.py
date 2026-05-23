"""Focused tests for controlled knowledge retrieval eligibility and projection."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from knowledge.registry import ensure_knowledge_layer, upsert_source_entry  # noqa: E402
from knowledge.retrieval import build_project_retrieval_summary, evaluate_phase_retrieval  # noqa: E402
from knowledge.sync import sync_offline_source  # noqa: E402
from state import KnowledgeItemStatus, SourceRegistryEntry  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


def make_synced_state(project_id: str = "knowledge-retrieval", base_time: datetime | None = None):
    base_time = base_time or datetime.now().replace(microsecond=0)
    state = make_state(project_id)
    ensure_knowledge_layer(state)
    upsert_source_entry(
        state,
        SourceRegistryEntry(
            source_id="src-knowledge",
            name="Offline analyst fixture",
            source_kind="offline_fixture",
            connector_type="offline_fixture",
            owner="operator",
            access_mode="manual",
            sensitivity="internal",
            trust_tier="operator_curated",
        ),
    )
    sync_offline_source(
        state,
        "src-knowledge",
        [
            {
                "source_ref": "fixture://current/1",
                "title": "Recent current-awareness note",
                "summary": "Recent change in demand signal for the current project.",
                "observed_at": (base_time - timedelta(hours=2)).isoformat(),
                "structured_payload": {
                    "region": "mx",
                    "score": 0.82,
                    "nested": {"ignore": True},
                },
            }
        ],
        actor="operator",
        requested_at=base_time,
    )
    return state


class TestKnowledgeRetrievalEligibility(unittest.TestCase):
    def test_fresh_internal_item_is_eligible_for_strategy(self):
        fixed_now = datetime(2026, 4, 12, 12, 0, 0)
        state = make_synced_state("retrieval-eligible", base_time=fixed_now)

        view = evaluate_phase_retrieval(state, "strategy", now=fixed_now)

        self.assertEqual(len(view.eligible_items), 1)
        self.assertEqual(len(view.blocked_items), 0)
        eligible = view.eligible_items[0]
        self.assertEqual(eligible.projection.title, "Recent current-awareness note")
        self.assertTrue(any(fact.key == "region" for fact in eligible.projection.facts))
        self.assertTrue(any(fact.key == "score" for fact in eligible.projection.facts))
        self.assertFalse(any(fact.key == "nested" for fact in eligible.projection.facts))

    def test_stale_expired_and_quarantined_items_are_blocked(self):
        fixed_now = datetime(2026, 4, 12, 12, 0, 0)
        state = make_synced_state("retrieval-freshness", base_time=fixed_now)
        base = state.knowledge_layer.items[0]
        stale_item = base.model_copy(update={
            "item_id": "stale-item",
            "source_ref": "fixture://stale/1",
            "observed_at": (datetime(2026, 4, 8, 0, 0, 0)).isoformat(),
            "captured_at": (datetime(2026, 4, 8, 0, 0, 0)).isoformat(),
        })
        expired_item = base.model_copy(update={
            "item_id": "expired-item",
            "source_ref": "fixture://expired/1",
            "observed_at": (datetime(2026, 4, 1, 0, 0, 0)).isoformat(),
            "captured_at": (datetime(2026, 4, 1, 0, 0, 0)).isoformat(),
        })
        quarantined_item = base.model_copy(update={
            "item_id": "quarantined-item",
            "source_ref": "fixture://quarantined/1",
            "freshness_status": KnowledgeItemStatus.QUARANTINED,
        })
        state.knowledge_layer.items.extend([stale_item, expired_item, quarantined_item])

        view = evaluate_phase_retrieval(state, "strategy", now=fixed_now)

        blocked = {item.item_id: item.blocked_reasons for item in view.blocked_items}
        self.assertIn("freshness_stale", blocked["stale-item"])
        self.assertIn("freshness_expired", blocked["expired-item"])
        self.assertIn("freshness_quarantined", blocked["quarantined-item"])

    def test_trust_tier_and_sensitivity_rules_block_items(self):
        fixed_now = datetime(2026, 4, 12, 12, 0, 0)
        state = make_synced_state("retrieval-trust-sensitivity", base_time=fixed_now)
        low_trust = state.knowledge_layer.items[0].model_copy(update={
            "item_id": "low-trust",
            "source_ref": "fixture://low/1",
            "trust_tier": "external_unknown",
        })
        restricted = state.knowledge_layer.items[0].model_copy(update={
            "item_id": "restricted",
            "source_ref": "fixture://restricted/1",
            "sensitivity": "restricted",
        })
        state.knowledge_layer.items.extend([low_trust, restricted])

        view = evaluate_phase_retrieval(state, "strategy", now=fixed_now)

        blocked = {item.item_id: item.blocked_reasons for item in view.blocked_items}
        self.assertIn("trust_tier_below_minimum", blocked["low-trust"])
        self.assertIn("sensitivity_disallowed", blocked["restricted"])

    def test_project_summary_reports_eligible_and_blocked_counts(self):
        fixed_now = datetime(2026, 4, 12, 12, 0, 0)
        state = make_synced_state("retrieval-summary", base_time=fixed_now)
        blocked_item = state.knowledge_layer.items[0].model_copy(update={
            "item_id": "blocked",
            "source_ref": "fixture://blocked/1",
            "sensitivity": "restricted",
        })
        state.knowledge_layer.items.append(blocked_item)

        summary = build_project_retrieval_summary(state, now=fixed_now)

        self.assertEqual(summary.project_id, state.project_id)
        self.assertEqual(len(summary.phases), 8)
        self.assertGreater(summary.total_eligible_count, 0)
        self.assertGreater(summary.total_blocked_count, 0)
        self.assertIn("backend-derived", summary.overview)


class TestKnowledgeRetrievalApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_knowledge_routes_expose_retrieval_summary_and_phase_view_without_rerun(self):
        state = make_synced_state("retrieval-api")
        before_phase_status = dict(state.phase_status)

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            knowledge = await api.get_knowledge(state.project_id)
            summary = await api.get_knowledge_retrieval_summary(state.project_id)
            phase_view = await api.get_knowledge_retrieval_phase(state.project_id, "strategy")

        self.assertIn("retrieval_summary", knowledge)
        self.assertEqual(knowledge["retrieval_summary"]["project_id"], state.project_id)
        self.assertEqual(summary.project_id, state.project_id)
        self.assertEqual(phase_view.phase, "strategy")
        self.assertTrue(phase_view.eligible_items)
        self.assertEqual(dict(state.phase_status), before_phase_status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
