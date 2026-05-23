"""Tests for deterministic clarification cycles."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from clarifications import (  # noqa: E402
    ClarificationStatus,
    generate_clarification_cycle,
    mark_clarification_unavailable,
    record_clarification_answer,
)
from state import (  # noqa: E402
    Evidence,
    KnowledgeItem,
    KnowledgeLayerState,
    PhaseStatus,
    ProjectState,
    Signal,
    UploadedFileManifest,
)


EXPECTED_GAPS = [
    "decision_deadline",
    "success_metric",
    "alternatives_options",
    "constraints",
    "stakeholder_audience",
    "evidence_source_material",
    "monitoring_kill_criteria",
    "budget_resource_constraints",
]


def gap_names(state: ProjectState) -> list[str]:
    return [question.source_gap for question in generate_clarification_cycle(state).questions]


class TestClarificationGeneration(unittest.TestCase):
    def test_incomplete_brief_generates_expected_gaps_in_fixed_order(self):
        state = ProjectState(project_id="clarity-incomplete", brief="Evaluate the initiative.")

        cycle = generate_clarification_cycle(state)

        self.assertEqual([question.source_gap for question in cycle.questions], EXPECTED_GAPS)
        self.assertEqual(cycle.project_id, state.project_id)
        self.assertEqual(
            [question.question_id for question in cycle.questions],
            [question.question_id for question in generate_clarification_cycle(state).questions],
        )

    def test_complete_brief_generates_no_open_questions(self):
        state = ProjectState(
            project_id="clarity-complete",
            brief=(
                "By Q3, choose launch or delay for the customer segment. Target a 15% "
                "conversion lift with a regulatory constraint, analytics dashboard evidence, "
                "monitor threshold, rollback trigger, and $80k budget for 2 FTE."
            ),
        )

        cycle = generate_clarification_cycle(state)

        self.assertEqual(cycle.questions, [])
        self.assertEqual(cycle.summary, "No deterministic missing-information questions right now.")

    def test_evidence_source_question_is_suppressed_by_data(self):
        state = ProjectState(project_id="clarity-data", brief="Evaluate the initiative.", data="Analyst notes")

        self.assertNotIn("evidence_source_material", gap_names(state))

    def test_evidence_source_question_is_suppressed_by_imported_records(self):
        evidence_state = ProjectState(project_id="clarity-evidence", brief="Evaluate the initiative.")
        evidence_state.imported_evidence.append(Evidence(evidence_id="E1", title="Market report"))

        signal_state = ProjectState(project_id="clarity-signal", brief="Evaluate the initiative.")
        signal_state.imported_signals.append(Signal(signal_id="S1", name="Demand signal"))

        self.assertNotIn("evidence_source_material", gap_names(evidence_state))
        self.assertNotIn("evidence_source_material", gap_names(signal_state))

    def test_evidence_source_question_is_suppressed_by_uploads_and_knowledge_items(self):
        upload_state = ProjectState(
            project_id="clarity-upload",
            brief="Evaluate the initiative.",
            knowledge_layer=KnowledgeLayerState(
                uploaded_files=[UploadedFileManifest(file_id="F1", filename="market.csv")]
            ),
        )
        knowledge_state = ProjectState(
            project_id="clarity-knowledge",
            brief="Evaluate the initiative.",
            knowledge_layer=KnowledgeLayerState(
                items=[KnowledgeItem(item_id="K1", title="Customer interview")]
            ),
        )

        self.assertNotIn("evidence_source_material", gap_names(upload_state))
        self.assertNotIn("evidence_source_material", gap_names(knowledge_state))


class TestClarificationAnswers(unittest.TestCase):
    def test_record_answer_updates_status_and_preserves_phase_status(self):
        state = ProjectState(project_id="clarity-answer", brief="Evaluate the initiative.")
        state.phase_status["classify"] = PhaseStatus.COMPLETED
        state.clarification_cycles.append(generate_clarification_cycle(state))
        before_phase_status = dict(state.phase_status)
        question = state.clarification_cycles[-1].questions[0]

        answer = record_clarification_answer(state, question.question_id, "End of Q3.")

        self.assertEqual(answer.status, ClarificationStatus.ANSWERED)
        self.assertEqual(question.status, ClarificationStatus.ANSWERED)
        self.assertEqual(state.clarification_answers[-1], answer)
        self.assertEqual(state.phase_status, before_phase_status)

    def test_mark_unavailable_updates_status_and_preserves_phase_status(self):
        state = ProjectState(project_id="clarity-unavailable", brief="Evaluate the initiative.")
        state.phase_status["strategy"] = PhaseStatus.FAILED
        state.clarification_cycles.append(generate_clarification_cycle(state))
        before_phase_status = dict(state.phase_status)
        question = state.clarification_cycles[-1].questions[1]

        answer = mark_clarification_unavailable(state, question.question_id)

        self.assertEqual(answer.status, ClarificationStatus.UNAVAILABLE)
        self.assertEqual(question.status, ClarificationStatus.UNAVAILABLE)
        self.assertEqual(answer.answer_text, "Unavailable")
        self.assertEqual(state.phase_status, before_phase_status)

    def test_legacy_project_state_loads_without_clarification_fields(self):
        state = ProjectState(project_id="clarity-legacy", brief="Evaluate the initiative.")
        payload = state.model_dump(mode="json")
        payload.pop("clarification_cycles", None)
        payload.pop("clarification_answers", None)

        loaded = ProjectState.model_validate(payload)

        self.assertEqual(loaded.clarification_cycles, [])
        self.assertEqual(loaded.clarification_answers, [])


class TestClarificationApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_get_cycle_answer_and_unavailable_routes(self):
        state = ProjectState(project_id="clarity-api", brief="Evaluate the initiative.")

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            empty = await api.get_clarifications(state.project_id)
            self.assertEqual(empty["latest_cycle"], None)

            cycle_response = await api.create_clarification_cycle(state.project_id)
            self.assertEqual(len(state.clarification_cycles), 1)
            self.assertEqual(len(cycle_response["open_questions"]), len(EXPECTED_GAPS))
            save_mock.assert_awaited()

            repeated_response = await api.create_clarification_cycle(state.project_id)
            self.assertEqual(len(state.clarification_cycles), 1)
            self.assertEqual(repeated_response["cycle"]["cycle_id"], cycle_response["cycle"]["cycle_id"])

            answer_question = state.clarification_cycles[-1].questions[0]
            answer_response = await api.answer_clarification(
                state.project_id,
                api.ClarificationAnswerRequest(
                    question_id=answer_question.question_id,
                    answer_text="End of Q3.",
                    status="answered",
                ),
            )
            self.assertEqual(answer_response["answer"]["status"], "answered")
            self.assertEqual(answer_question.status, ClarificationStatus.ANSWERED)

            unavailable_question = state.clarification_cycles[-1].questions[1]
            unavailable_response = await api.answer_clarification(
                state.project_id,
                api.ClarificationAnswerRequest(
                    question_id=unavailable_question.question_id,
                    status="unavailable",
                ),
            )
            self.assertEqual(unavailable_response["answer"]["status"], "unavailable")
            self.assertEqual(unavailable_question.status, ClarificationStatus.UNAVAILABLE)

    async def test_get_route_does_not_mutate_or_save_state(self):
        state = ProjectState(project_id="clarity-api-get", brief="Evaluate the initiative.")

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            response = await api.get_clarifications(state.project_id)

        self.assertEqual(response["latest_cycle"], None)
        self.assertEqual(state.clarification_cycles, [])
        save_mock.assert_not_awaited()

    async def test_answer_route_rejects_invalid_requests(self):
        state = ProjectState(project_id="clarity-api-invalid", brief="Evaluate the initiative.")
        state.clarification_cycles.append(generate_clarification_cycle(state))
        question = state.clarification_cycles[-1].questions[0]

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()):
            with self.assertRaises(api.HTTPException) as empty_answer:
                await api.answer_clarification(
                    state.project_id,
                    api.ClarificationAnswerRequest(question_id=question.question_id, status="answered"),
                )
            self.assertEqual(empty_answer.exception.status_code, 400)

            with self.assertRaises(api.HTTPException) as missing_question:
                await api.answer_clarification(
                    state.project_id,
                    api.ClarificationAnswerRequest(
                        question_id="missing",
                        answer_text="Answer",
                        status="answered",
                    ),
                )
            self.assertEqual(missing_question.exception.status_code, 404)

            with self.assertRaises(api.HTTPException) as invalid_status:
                await api.answer_clarification(
                    state.project_id,
                    api.ClarificationAnswerRequest(
                        question_id=question.question_id,
                        answer_text="Answer",
                        status="closed",
                    ),
                )
            self.assertEqual(invalid_status.exception.status_code, 400)

            question.status = ClarificationStatus.SUPERSEDED
            with self.assertRaises(api.HTTPException) as superseded_question:
                await api.answer_clarification(
                    state.project_id,
                    api.ClarificationAnswerRequest(
                        question_id=question.question_id,
                        answer_text="Answer",
                        status="answered",
                    ),
                )
            self.assertEqual(superseded_question.exception.status_code, 400)
