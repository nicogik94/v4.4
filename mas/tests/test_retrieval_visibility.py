"""Focused tests for derived retrieval visibility in workspace/trace/explain surfaces."""
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from explainability import build_explainability_report, build_phase_trace  # noqa: E402
from knowledge.registry import ensure_knowledge_layer, upsert_source_entry  # noqa: E402
from knowledge.sync import sync_offline_source  # noqa: E402
from state import SourceRegistryEntry  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


def make_visibility_state(project_id: str = "retrieval-visibility"):
    now = datetime.now()
    state = make_state(project_id)
    ensure_knowledge_layer(state)
    upsert_source_entry(
        state,
        SourceRegistryEntry(
            source_id="src-visibility",
            name="Visibility fixture",
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
        "src-visibility",
        [
            {
                "source_ref": "fixture://visibility/eligible",
                "title": "Fresh visibility note",
                "summary": "Current signal supports closer audit and strategy review.",
                "observed_at": now.isoformat(),
                "structured_payload": {"region": "mx", "score": 0.81},
            }
        ],
        actor="operator",
        requested_at=now,
    )
    blocked = state.knowledge_layer.items[0].model_copy(
        update={
            "item_id": "visibility-blocked",
            "source_ref": "fixture://visibility/blocked",
            "title": "Blocked visibility note",
            "sensitivity": "restricted",
        }
    )
    state.knowledge_layer.items.append(blocked)

    used_item = {
        "item_id": state.knowledge_layer.items[0].item_id,
        "source_id": "src-visibility",
        "source_name": "Visibility fixture",
        "title": "Fresh visibility note",
        "observed_at": now.isoformat(),
        "freshness_status": "fresh",
        "trust_tier": "operator_curated",
        "sensitivity": "internal",
        "fact_keys": ["region", "score"],
    }
    state.policy_audit_log.append(
        {
            "ts": now.timestamp(),
            "event_type": "knowledge_retrieval_used",
            "phase": "audit",
            "details": {
                "phase": "audit",
                "used_item_count": 1,
                "used_item_ids": [used_item["item_id"]],
                "used_items": [used_item],
            },
        }
    )
    state.policy_audit_log.append(
        {
            "ts": now.timestamp() + 1,
            "event_type": "knowledge_retrieval_used",
            "phase": "strategy",
            "details": {
                "phase": "strategy",
                "used_item_count": 1,
                "used_item_ids": [used_item["item_id"]],
                "used_items": [used_item],
            },
        }
    )
    return state


class TestRetrievalVisibility(unittest.TestCase):
    def test_workspace_summary_exposes_retrieval_visibility(self):
        state = make_visibility_state("retrieval-workspace")

        workspace = build_workspace_summary(state)

        self.assertEqual(len(workspace.retrieval_visibility), 2)
        audit_summary = next(item for item in workspace.retrieval_visibility if item.phase == "audit")
        strategy_summary = next(item for item in workspace.retrieval_visibility if item.phase == "strategy")
        self.assertTrue(audit_summary.retrieval_used)
        self.assertEqual(audit_summary.eligible_count, 1)
        self.assertEqual(audit_summary.blocked_count, 1)
        self.assertEqual(audit_summary.used_item_count, 1)
        self.assertEqual(audit_summary.used_items[0].title, "Fresh visibility note")
        self.assertIn("sensitivity disallowed x1", audit_summary.blocked_reason_summary)
        self.assertTrue(strategy_summary.retrieval_used)

    def test_phase_trace_exposes_retrieval_impact_and_usage(self):
        state = make_visibility_state("retrieval-trace")

        audit_trace = build_phase_trace(state, "audit")
        strategy_trace = build_phase_trace(state, "strategy")

        self.assertIsNotNone(audit_trace.retrieval_impact)
        self.assertEqual(audit_trace.retrieval_impact.eligible_count, 1)
        self.assertEqual(audit_trace.retrieval_impact.blocked_count, 1)
        self.assertIn("sensitivity disallowed x1", audit_trace.retrieval_impact.blocked_reason_summary)
        self.assertEqual(audit_trace.knowledge_usage[0].title, "Fresh visibility note")
        self.assertTrue(
            any("Audit prompt used retrieval-approved knowledge item" in item for item in audit_trace.logic_separation.knowledge_inputs)
        )
        self.assertEqual(strategy_trace.knowledge_usage[0].title, "Fresh visibility note")

    def test_explainability_overview_and_logic_include_retrieval_visibility(self):
        state = make_visibility_state("retrieval-explain")

        report = build_explainability_report(state)

        self.assertIn("Audit used 1 retrieval-approved knowledge item(s)", report.overview)
        self.assertIn("Strategy used 1 retrieval-approved knowledge item(s)", report.overview)
        self.assertTrue(
            any("Audit used retrieval-approved knowledge item" in item for item in report.logic_separation.knowledge_inputs)
        )
        self.assertTrue(
            any("Strategy used retrieval-approved knowledge item" in item for item in report.logic_separation.knowledge_inputs)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
