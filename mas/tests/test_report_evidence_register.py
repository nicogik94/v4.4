"""Focused tests for orchestrator._build_report_evidence_locator_register.

These tests cover the prompt-side register path that feeds the report-phase
LLM. They confirm KnowledgeItems with concrete locators are included, that
entries without a derivable locator are filtered out (so the model is not
tempted to cite analytical conclusions as `[Evidence: <id> | locator
unavailable]`), and that the empty-state message is the honest fallback.
"""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import _build_report_evidence_locator_register  # noqa: E402
from state import (  # noqa: E402
    DecisionObjects,
    Evidence,
    KnowledgeItem,
    KnowledgeLayerState,
    ProjectState,
    Provenance,
)


def _state_with_items(*items: KnowledgeItem) -> ProjectState:
    state = ProjectState(
        project_id="register-test",
        project_name="Register",
        brief="Report register coverage.",
    )
    state.knowledge_layer = KnowledgeLayerState(items=list(items))
    return state


class TestReportEvidenceRegister(unittest.TestCase):
    def test_knowledge_items_included_via_item_id_fallback(self):
        state = _state_with_items(
            KnowledgeItem(item_id="ev-x", source_id="src", source_ref="upload:f1:doc.pdf#chunk=1"),
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: ev-x", register_text)
        self.assertNotIn(
            "No project evidence locators supplied",
            register_text,
            msg="register should not fall back to the empty-state message when item_id is present with a derivable locator",
        )

    def test_concrete_locator_rendered_from_chunk_index(self):
        state = _state_with_items(
            KnowledgeItem(item_id="ev-doc", source_id="src", structured_payload={"chunk_index": 5}),
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: ev-doc | chunk=5]", register_text)
        self.assertNotIn("ev-doc | locator unavailable", register_text)

    def test_concrete_locator_rendered_from_row_range_with_sheet(self):
        state = _state_with_items(
            KnowledgeItem(
                item_id="ev-rows",
                source_id="src",
                structured_payload={"row_start": 2, "row_end": 17, "sheet_name": "Q3"},
            ),
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: ev-rows | sheet=Q3;row=2-17]", register_text)

    def test_locator_unavailable_entries_are_filtered_to_empty_state(self):
        state = _state_with_items(
            KnowledgeItem(
                item_id="ev-bare",
                source_id="src",
                structured_payload={"category": "uploaded_document", "filename": "x.pdf"},
            ),
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertNotIn(
            "[Evidence: ev-bare",
            register_text,
            msg="entries without a derivable locator must be filtered out, not rendered as 'locator unavailable'",
        )
        self.assertNotIn(
            "locator unavailable",
            register_text,
            msg="filtered register must not surface the locator-unavailable literal",
        )
        self.assertIn(
            "No project evidence locators supplied",
            register_text,
            msg="all-filtered register must fall through to the honest empty-state message",
        )

    def test_register_includes_concrete_entries_only_filtering_locator_unavailable(self):
        state = _state_with_items(
            KnowledgeItem(item_id="ev-a", source_id="src", structured_payload={"chunk_index": 1}),
            KnowledgeItem(item_id="ev-b", source_id="src", structured_payload={"row_start": 4, "row_end": 9}),
            KnowledgeItem(item_id="ev-c", source_id="src"),
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: ev-a | chunk=1]", register_text)
        self.assertIn("[Evidence: ev-b | row=4-9]", register_text)
        self.assertNotIn(
            "ev-c",
            register_text,
            msg="locator-unavailable entries must be filtered, not rendered",
        )
        self.assertNotIn("locator unavailable", register_text)

    def test_register_filters_decision_objects_derived_evidence_without_locators(self):
        state = _state_with_items(
            KnowledgeItem(item_id="knowledge_concrete", source_id="src", structured_payload={"chunk_index": 1}),
        )
        state.decision_objects = DecisionObjects(
            evidences=[
                Evidence(
                    evidence_id="evidence_derived",
                    title="Derived analytical evidence",
                    provenance=Provenance(source_ref="audit:fmea:0"),
                ),
            ]
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: knowledge_concrete | chunk=1]", register_text)
        self.assertNotIn(
            "evidence_derived",
            register_text,
            msg="derived analytical Evidence entries must not appear without a concrete locator",
        )
        self.assertNotIn("locator unavailable", register_text)

    def test_register_emits_empty_state_when_only_derived_entries_exist(self):
        state = ProjectState(project_id="derived-only", project_name="Derived", brief="No source-backed evidence.")
        state.decision_objects = DecisionObjects(
            evidences=[
                Evidence(
                    evidence_id="evidence_a",
                    title="Audit derived",
                    provenance=Provenance(source_ref="audit:top_finding:0"),
                ),
                Evidence(
                    evidence_id="evidence_b",
                    title="Strategy derived",
                    provenance=Provenance(source_ref="strategy:verdict:H1"),
                ),
            ]
        )

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("PROJECT EVIDENCE LOCATORS:", register_text)
        self.assertIn(
            "No project evidence locators supplied",
            register_text,
            msg="register populated only by derived entries must fall through to the empty-state message",
        )
        self.assertNotIn("evidence_a", register_text)
        self.assertNotIn("evidence_b", register_text)

    def test_empty_knowledge_layer_emits_empty_register_message(self):
        state = ProjectState(project_id="empty", project_name="Empty", brief="No evidence.")

        register_text = _build_report_evidence_locator_register(state)

        self.assertIn("PROJECT EVIDENCE LOCATORS:", register_text)
        self.assertIn("No project evidence locators supplied", register_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
