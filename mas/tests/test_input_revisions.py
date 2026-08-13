"""Deterministic W8.2 lifecycle and compatibility tests."""
from __future__ import annotations

import unittest
from copy import deepcopy
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import api
import input_revisions
import store
from state import PhaseStatus, ProjectState
from state_coherence import (
    DIRECT_INPUT_FIELDS,
    direct_input_projection,
    effective_input_identity,
    effective_input_payload,
)


def _state() -> ProjectState:
    state = ProjectState(
        project_id=str(uuid4()),
        project_name="Governed input",
        brief="Choose A or B",
        data="Baseline evidence",
    )
    state.effective_input_snapshot_id = effective_input_identity(state).snapshot_id
    return state


class TestInputRevisionNormalization(unittest.TestCase):
    def test_patch_is_narrow_normalized_sorted_and_deterministic(self):
        state = _state()
        first = input_revisions.normalize_patch(
            {"timer_logs": [{"z": 2, "a": 1}], "brief": "Choose B"}, state
        )
        second = input_revisions.normalize_patch(
            {"brief": "Choose B", "timer_logs": [{"a": 1, "z": 2}]}, state
        )

        self.assertEqual(list(first), ["brief", "timer_logs"])
        self.assertEqual(
            input_revisions.patch_fingerprint(first),
            input_revisions.patch_fingerprint(second),
        )

    def test_risk_ice_knowledge_and_research_fields_are_not_revision_fields(self):
        state = _state()
        for field in (
            "risk_classification",
            "clarification_answers",
            "knowledge_layer",
            "analysis_input_attestations",
            "imported_evidence",
        ):
            with self.subTest(field=field), self.assertRaises(
                input_revisions.InputRevisionValidationError
            ):
                input_revisions.normalize_patch({field: "unsupported"}, state)

    def test_all_existing_direct_patch_fields_change_effective_identity(self):
        base = _state()
        cases = {
            "project_name": "Renamed",
            "brief": "Choose B",
            "data": "New evidence",
            "output_language": "es-MX",
            "report_mode": "decision_memo_pilot_plan",
            "observations": {"owner": "confirmed"},
            "timer_logs": [{"at": "09:00"}],
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                changed = deepcopy(base)
                setattr(changed, field, value)
                self.assertNotEqual(
                    effective_input_identity(base).snapshot_id,
                    effective_input_identity(changed).snapshot_id,
                )

        payload = effective_input_payload(base)
        self.assertEqual(payload["contract_version"], "effective-decision-input.v2")

    def test_direct_input_projection_is_exactly_the_w8_2_domain(self):
        state = _state()
        state.risk_classification = "limited_risk"
        state.analysis_input_attestations = {"audit": {"knowledge": {"status": "used"}}}

        projection = direct_input_projection(state)

        self.assertEqual(tuple(projection), DIRECT_INPUT_FIELDS)
        self.assertEqual(
            set(projection),
            {
                "project_name",
                "brief",
                "data",
                "output_language",
                "report_mode",
                "observations",
                "timer_logs",
            },
        )


class TestInputRevisionMemoryLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store._mem.clear()
        input_revisions._mem_revisions.clear()
        input_revisions._mem_project_revisions.clear()
        input_revisions._mem_locks.clear()
        self.state = _state()
        store._mem[self.state.project_id] = self.state

    async def asyncTearDown(self):
        store._mem.clear()
        input_revisions._mem_revisions.clear()
        input_revisions._mem_project_revisions.clear()
        input_revisions._mem_locks.clear()

    async def test_proposal_and_rejection_are_inert_and_terminal(self):
        before = self.state.model_dump(mode="json")
        proposal = await input_revisions.propose_revision(
            self.state.project_id,
            {"brief": "Choose B"},
            rationale="New operator constraint",
            source_kind="operator_api",
            state=self.state,
        )
        self.assertEqual(self.state.model_dump(mode="json"), before)
        self.assertEqual(proposal.status, input_revisions.PROPOSED)

        rejected = await input_revisions.reject_revision(
            self.state.project_id,
            proposal.revision_id,
            rejected_by="operator",
            rejection_rationale="Not approved",
        )
        self.assertEqual(rejected.status, input_revisions.REJECTED)
        self.assertEqual(self.state.model_dump(mode="json"), before)
        with self.assertRaises(input_revisions.InputRevisionConflict):
            await input_revisions.apply_revision(
                self.state.project_id,
                proposal.revision_id,
                applied_by="operator",
                transform=api._apply_direct_input_revision,
                state=self.state,
            )

    async def test_same_base_proposals_coexist_then_only_one_can_apply(self):
        first = await input_revisions.propose_revision(
            self.state.project_id,
            {"brief": "Choose B"},
            rationale="First",
            source_kind="operator_api",
            state=self.state,
        )
        second = await input_revisions.propose_revision(
            self.state.project_id,
            {"data": "Updated evidence"},
            rationale="Second",
            source_kind="operator_api",
            state=self.state,
        )
        self.assertEqual(first.expected_base_snapshot_id, second.expected_base_snapshot_id)

        applied = await input_revisions.apply_revision(
            self.state.project_id,
            first.revision_id,
            applied_by="operator",
            transform=api._apply_direct_input_revision,
            state=self.state,
        )
        self.assertEqual(applied.revision.status, input_revisions.APPLIED)
        self.assertEqual(self.state.brief, "Choose B")
        with self.assertRaises(input_revisions.StaleInputRevision):
            await input_revisions.apply_revision(
                self.state.project_id,
                second.revision_id,
                applied_by="operator",
                transform=api._apply_direct_input_revision,
                state=self.state,
            )
        with self.assertRaises(input_revisions.InputRevisionConflict):
            await input_revisions.apply_revision(
                self.state.project_id,
                first.revision_id,
                applied_by="operator",
                transform=api._apply_direct_input_revision,
                state=self.state,
            )

    async def test_compatibility_patch_creates_one_revision_and_retry_is_noop(self):
        first = await api.patch_project_input(
            self.state.project_id, api.PatchProjectInputRequest(brief="Choose B")
        )
        second = await api.patch_project_input(
            self.state.project_id, api.PatchProjectInputRequest(brief="Choose B")
        )

        self.assertEqual(first["status"], "updated")
        self.assertTrue(first["revision_id"])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["revision_id"], "")
        self.assertEqual(
            len(input_revisions._mem_project_revisions[self.state.project_id]), 1
        )

    async def test_noop_revision_proposal_is_rejected_without_fake_history(self):
        with self.assertRaises(input_revisions.InputRevisionValidationError):
            await input_revisions.propose_revision(
                self.state.project_id,
                {"brief": self.state.brief},
                rationale="No change",
                source_kind="operator_api",
                state=self.state,
            )
        self.assertEqual(input_revisions._mem_project_revisions, {})

    async def test_configured_postgresql_never_falls_back_to_memory(self):
        with (
            patch.object(store, "DATABASE_URL", "postgresql://configured"),
            patch("store._get_pool", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(input_revisions.InputRevisionSchemaRequired):
                await input_revisions.propose_revision(
                    self.state.project_id,
                    {"brief": "Must remain durable"},
                    rationale="Fail closed proof",
                    source_kind="operator_api",
                    state=self.state,
                )
        self.assertEqual(input_revisions._mem_project_revisions, {})


class TestInputRevisionApiSurface(unittest.TestCase):
    def test_write_routes_reuse_operator_authorization(self):
        protected = {
            route.path
            for route in api.app.routes
            if getattr(route, "path", "").startswith("/projects/{project_id}/input")
            and any(
                getattr(dependency.call, "__name__", "") == "require_operator_auth"
                for dependency in route.dependant.dependencies
            )
        }
        self.assertIn("/projects/{project_id}/input", protected)
        self.assertIn("/projects/{project_id}/input-revisions", protected)
        self.assertIn(
            "/projects/{project_id}/input-revisions/{revision_id}/apply", protected
        )
        self.assertIn(
            "/projects/{project_id}/input-revisions/{revision_id}/reject", protected
        )
