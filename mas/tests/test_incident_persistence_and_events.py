"""Incident regression coverage for project parent rows and decision events."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
import decision_events
import store
from state import ProjectState


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _IncidentConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.projects: dict[str, dict] = {}
        self.snapshots: dict[str, str] = {}
        self.outcomes: dict[tuple[str, str], dict] = {}
        self.events_by_project: dict[str, list[dict]] = {}

    async def execute(self, query: str, *args):
        self.calls.append((query, args))
        normalized = " ".join(query.split()).lower()

        if "insert into projects" in normalized:
            project_id = args[0]
            self.projects[project_id] = {
                "id": project_id,
                "name": args[1],
                "brief": args[2],
                "data": args[3],
                "current_phase": args[4],
                "created_at": args[5],
            }
            return "INSERT 0 1"

        if "insert into state_snapshots" in normalized:
            self.snapshots[args[0]] = args[1]
            return "INSERT 0 1"

        if "delete from state_snapshots" in normalized:
            self.snapshots.pop(args[0], None)
            return "DELETE 1"

        if "delete from projects" in normalized:
            existed = args[0] in self.projects
            if existed:
                self.projects.pop(args[0], None)
            return "DELETE 1" if existed else "DELETE 0"

        if "insert into outcomes" in normalized:
            project_id = args[0]
            if project_id not in self.projects:
                raise RuntimeError("fk_violation: outcomes.project_id missing parent in projects")
            hypothesis_id = args[1]
            self.outcomes[(project_id, hypothesis_id)] = {
                "project_id": project_id,
                "hypothesis_id": hypothesis_id,
                "phase": args[2],
                "predicted_probability": args[3],
                "realized": args[4],
            }
            return "INSERT 0 1"

        if "insert into decision_events" in normalized:
            event = {
                "event_id": args[0],
                "project_id": args[1],
                "event_type": args[2],
                "event_time": args[3],
                "actor_type": args[4],
                "actor_id": args[5],
                "payload": json.loads(args[6]),
                "trace_id": args[7],
                "phase": args[8],
                "model_provider": args[9],
                "model_name": args[10],
                "cost_usd": args[11],
                "latency_ms": args[12],
                "prev_event_id": args[13],
                "event_hash": args[14],
            }
            self.events_by_project.setdefault(args[1], []).append(event)
            return "INSERT 0 1"

        return "OK"

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split()).lower()

        if "select state_json from state_snapshots" in normalized:
            snapshot = self.snapshots.get(args[0])
            if snapshot is None:
                return None
            return {"state_json": snapshot}

        if "from decision_events" in normalized and "order by event_time desc" in normalized:
            events = self.events_by_project.get(args[0], [])
            if not events:
                return None
            last = events[-1]
            return {"event_id": last["event_id"], "event_hash": last["event_hash"]}

        return None


class _IncidentPool:
    def __init__(self):
        self.conn = _IncidentConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


class TestIncidentPersistenceAndEvents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store._mem.clear()
        decision_events._reset_memory_for_tests()
        api.running.clear()
        api.auto_refresh_jobs.clear()

    async def asyncTearDown(self):
        store._mem.clear()
        decision_events._reset_memory_for_tests()
        api.running.clear()
        api.auto_refresh_jobs.clear()

    async def test_store_save_creates_parent_and_rejects_ungoverned_name_update(self):
        pool = _IncidentPool()
        state = ProjectState(
            project_id="incident-parent-1",
            project_name="Initial Name",
            brief="brief",
            data="data",
            created_at=datetime.now(timezone.utc),
        )
        with patch("store._get_pool", new=AsyncMock(return_value=pool)):
            await store.save(state)
            state.project_name = "Updated Name"
            with self.assertRaises(store.DirectInputAuthorityError):
                await store.save(state)
            state.project_name = "Initial Name"
            state.current_phase = "audit"
            await store.save(state)

        self.assertIn(state.project_id, pool.conn.projects)
        self.assertEqual(pool.conn.projects[state.project_id]["name"], "Initial Name")
        self.assertEqual(pool.conn.projects[state.project_id]["current_phase"], "audit")
        first_insert = next(call for call in pool.conn.calls if "INSERT INTO projects" in call[0])
        self.assertIsInstance(first_insert[1][5], datetime)

    async def test_store_delete_keeps_parent_delete_consistent(self):
        pool = _IncidentPool()
        state = ProjectState(
            project_id="incident-delete-1",
            project_name="Delete Test",
            brief="brief",
            data="data",
            created_at=datetime.now(timezone.utc),
        )
        with patch("store._get_pool", new=AsyncMock(return_value=pool)):
            await store.save(state)
            deleted = await store.delete(state.project_id)

        self.assertTrue(deleted)
        delete_queries = [q for (q, _) in pool.conn.calls if q.strip().startswith("DELETE FROM")]
        self.assertIn("DELETE FROM state_snapshots", delete_queries[0])
        self.assertIn("DELETE FROM projects", delete_queries[1])
        self.assertNotIn(state.project_id, pool.conn.projects)

    async def test_decision_events_append_binds_datetime_for_timestamptz(self):
        pool = _IncidentPool()
        with patch("decision_events._get_pool", new=AsyncMock(return_value=pool)):
            event = await decision_events.append(
                "incident-event-bind-1",
                "project.created",
                payload={"project_type": "strategic_audit"},
            )

        self.assertIsNotNone(event)
        insert_call = next(call for call in pool.conn.calls if "INSERT INTO decision_events" in call[0])
        self.assertIsInstance(insert_call[1][3], datetime)
        self.assertNotIsInstance(insert_call[1][3], str)

    async def test_create_then_outcome_does_not_violate_fk(self):
        pool = _IncidentPool()
        with patch("store._get_pool", new=AsyncMock(return_value=pool)):
            created = await api.create_project(
                api.CreateProjectRequest(
                    name="FK Regression",
                    brief="fk-safe flow",
                    data="seed",
                    project_type="strategic_audit",
                )
            )
            response = await api.record_outcome(
                created.project_id,
                api.OutcomeRecord(
                    hypothesis_id="H1",
                    phase="classify",
                    predicted_probability=0.73,
                    realized=True,
                    realized_value=1.0,
                    notes="fk check",
                    recorded_by="test",
                ),
            )

        self.assertEqual(response["status"], "recorded")
        self.assertIn((created.project_id, "H1"), pool.conn.outcomes)

    async def test_record_outcome_appends_outcome_recorded_for_fresh_project(self):
        pool = _IncidentPool()
        with patch("store._get_pool", new=AsyncMock(return_value=pool)):
            created = await api.create_project(
                api.CreateProjectRequest(
                    name="Outcome Event Regression",
                    brief="event append check",
                    data="seed",
                    project_type="strategic_audit",
                )
            )
            await api.record_outcome(
                created.project_id,
                api.OutcomeRecord(
                    hypothesis_id="H1",
                    phase="classify",
                    predicted_probability=0.60,
                    realized=False,
                    notes="event check",
                    recorded_by="test",
                ),
            )

        event_types = [e["event_type"] for e in pool.conn.events_by_project.get(created.project_id, [])]
        self.assertIn("project.created", event_types)
        self.assertIn("outcome.recorded", event_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
