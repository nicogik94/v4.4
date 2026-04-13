"""Focused tests for tranche-3A knowledge layer foundations."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from knowledge.freshness import build_knowledge_health, evaluate_knowledge_item_status  # noqa: E402
from knowledge.registry import ensure_knowledge_layer, upsert_source_entry  # noqa: E402
from knowledge.sync import sync_offline_source  # noqa: E402
from state import (  # noqa: E402
    KnowledgeItem,
    KnowledgeItemStatus,
    ProjectState,
    Provenance,
    SourceRegistryEntry,
)
from tests.test_decision_objects import make_state  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


class TestKnowledgeModels(unittest.TestCase):
    def test_project_state_loads_without_knowledge_layer_field(self):
        state = make_state("legacy-knowledge")
        payload = state.model_dump(mode="json")
        payload.pop("knowledge_layer", None)

        loaded = ProjectState.model_validate(payload)

        self.assertIsNone(loaded.knowledge_layer)

    def test_knowledge_item_freshness_transitions(self):
        now = datetime(2026, 4, 12, 12, 0, 0)
        recent = KnowledgeItem(
            item_id="K1",
            source_id="S1",
            source_ref="ref-1",
            title="Recent",
            observed_at=(now - timedelta(hours=2)).isoformat(),
            captured_at=(now - timedelta(hours=2)).isoformat(),
            provenance=Provenance(source_type="connector_import", source_ref="ref-1", captured_at=(now - timedelta(hours=2)).isoformat(), captured_by="operator"),
        )
        stale = recent.model_copy(update={"item_id": "K2", "observed_at": (now - timedelta(hours=80)).isoformat(), "captured_at": (now - timedelta(hours=80)).isoformat()})
        expired = recent.model_copy(update={"item_id": "K3", "observed_at": (now - timedelta(hours=200)).isoformat(), "captured_at": (now - timedelta(hours=200)).isoformat()})

        self.assertEqual(
            evaluate_knowledge_item_status(recent, stale_after_hours=72, expire_after_hours=168, now=now),
            KnowledgeItemStatus.FRESH,
        )
        self.assertEqual(
            evaluate_knowledge_item_status(stale, stale_after_hours=72, expire_after_hours=168, now=now),
            KnowledgeItemStatus.STALE,
        )
        self.assertEqual(
            evaluate_knowledge_item_status(expired, stale_after_hours=72, expire_after_hours=168, now=now),
            KnowledgeItemStatus.EXPIRED,
        )


class TestKnowledgeSync(unittest.TestCase):
    def test_manual_offline_sync_updates_knowledge_layer(self):
        state = make_state("knowledge-sync")
        upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id="src-1",
                name="Local fixture",
                source_kind="offline_fixture",
                connector_type="offline_fixture",
                owner="operator",
                access_mode="manual",
                sensitivity="internal",
            ),
        )

        job = sync_offline_source(
            state,
            "src-1",
            [
                {
                    "source_ref": "fixture://briefs/1",
                    "title": "Fresh market note",
                    "summary": "Demand changed last week.",
                    "observed_at": datetime.now().isoformat(),
                    "structured_payload": {"score": 0.8},
                }
            ],
            actor="operator",
        )

        self.assertEqual(job.status.value, "completed")
        self.assertEqual(len(state.knowledge_layer.items), 1)
        self.assertFalse(state.knowledge_layer.items[0].eligible_for_retrieval)
        health = build_knowledge_health(state)
        self.assertEqual(health["status"], "current")

    def test_sensitive_source_fails_closed(self):
        state = make_state("knowledge-sensitive")
        ensure_knowledge_layer(state)
        upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id="src-2",
                name="Restricted source",
                source_kind="offline_fixture",
                connector_type="offline_fixture",
                owner="operator",
                access_mode="manual",
                sensitivity="restricted",
            ),
        )

        job = sync_offline_source(
            state,
            "src-2",
            [{"source_ref": "fixture://secret/1", "title": "Secret note"}],
            actor="operator",
        )

        self.assertEqual(job.status.value, "failed")
        self.assertEqual(len(state.knowledge_layer.items), 0)
        self.assertEqual(build_knowledge_health(state)["status"], "sync_failed")


class TestKnowledgeApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_register_source_sync_fixture_and_expose_workspace_status(self):
        state = make_state("knowledge-api")
        before_phase_status = dict(state.phase_status)

        register_req = api.KnowledgeSourceUpsertRequest(
            source_id="src-api",
            name="Analyst fixture",
        )
        sync_req = api.KnowledgeSourceSyncRequest(
            actor="operator",
            items=[
                api.KnowledgeFixtureItemRequest(
                    source_ref="fixture://signals/1",
                    title="Current awareness note",
                    summary="This is recent external context.",
                    observed_at=datetime.now().isoformat(),
                    structured_payload={"region": "mx"},
                )
            ],
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            register_response = await api.upsert_knowledge_source(state.project_id, register_req)
            sync_response = await api.sync_project_knowledge_source(state.project_id, "src-api", sync_req)
            workspace = await api.get_workspace(state.project_id)
            knowledge_view = await api.get_knowledge(state.project_id)
            sources_view = await api.get_knowledge_sources(state.project_id)
            jobs_view = await api.get_knowledge_jobs(state.project_id)
        with patch("api.store.list_all", new=AsyncMock(return_value=[state])):
            queue_rows = await api.get_project_queue()

        self.assertEqual(register_response["status"], "registered")
        self.assertEqual(sync_response["status"], "completed")
        self.assertEqual(workspace.knowledge_health.status, "current")
        self.assertEqual(workspace.knowledge_health.item_count, 1)
        self.assertEqual(knowledge_view["summary"]["status"], "current")
        self.assertEqual(len(sources_view), 1)
        self.assertEqual(sources_view[0]["source_id"], "src-api")
        self.assertEqual(len(jobs_view), 1)
        self.assertEqual(jobs_view[0]["status"], "completed")
        self.assertEqual(queue_rows[0].knowledge_status, "current")
        self.assertEqual(dict(state.phase_status), before_phase_status)
        save_mock.assert_awaited()

    async def test_batch_sync_route_returns_jobs(self):
        state = make_state("knowledge-batch")
        upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id="src-batch",
                name="Batch fixture",
                source_kind="offline_fixture",
                connector_type="offline_fixture",
                owner="operator",
                access_mode="manual",
                sensitivity="internal",
            ),
        )
        request = api.KnowledgeSyncRequest(
            actor="operator",
            sources=[
                api.KnowledgeMultiSyncSourceRequest(
                    source_id="src-batch",
                    items=[
                        api.KnowledgeFixtureItemRequest(
                            source_ref="fixture://batch/1",
                            title="Batch item",
                            observed_at=datetime.now().isoformat(),
                        )
                    ],
                )
            ],
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()):
            response = await api.sync_project_knowledge(state.project_id, request)

        self.assertEqual(response["status"], "completed")
        self.assertEqual(len(response["jobs"]), 1)
        self.assertEqual(response["jobs"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
