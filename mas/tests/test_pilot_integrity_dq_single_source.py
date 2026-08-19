"""V4.4 pilot integrity P0-4 — one authoritative DQ value per run.

Observed defect: the same run exported ``DQ=40`` in classify/report while
``project_state`` and ``calibration_predictions`` carried ``DQ=0``.
``state.classify.dq`` (measured) and ``state.dq`` (a six-link container never
assigned anywhere in the codebase, whose ``sum(...)`` was published as
``dq_total``) were two disjoint answers.

These tests pin the contract, not an implementation: every surface agrees,
absent DQ stays unavailable rather than a synthetic zero, and no exporter
mutates ``ProjectState``.

Deterministic and offline: no provider, no network, no database.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_quality  # noqa: E402
import exporters  # noqa: E402
from decision_objects import build_decision_objects  # noqa: E402
from state import ClassifyOutput, OODALoop, PhaseStatus, ProjectState  # noqa: E402
from tools.scoring import check_gate, summarize_phase_output  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


DQ_COMPONENTS = [20.0, 8.0, 7.0, 5.0]
DQ_TOTAL = 40.0


def _state() -> ProjectState:
    state = ProjectState(
        project_id="pilot-integrity-dq",
        project_name="DQ single source",
        brief="Decide whether to run a bounded pilot.",
    )
    state.classify = ClassifyOutput(
        domain="Complicated",
        justification="Expert-discoverable structure.",
        bf=42.0,
        variety_gaps="1. gap",
        ooda=OODALoop(observe="o", orient="o", decide="d", act="a", freq="Weekly"),
        dq=list(DQ_COMPONENTS),
    )
    state.report = "# Executive Summary\nRun the bounded pilot.\n"
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
    return state


class TestAuthoritativeValue(unittest.TestCase):
    def test_derives_from_the_single_declared_source(self):
        assessment = decision_quality.authoritative_dq(_state())
        self.assertTrue(assessment.available)
        self.assertEqual(assessment.total, DQ_TOTAL)
        self.assertEqual(list(assessment.components), DQ_COMPONENTS)
        self.assertEqual(assessment.source, decision_quality.AUTHORITATIVE_DQ_SOURCE)

    def test_absent_classify_output_is_unavailable_not_zero(self):
        state = _state()
        state.classify = None
        assessment = decision_quality.authoritative_dq(state)
        self.assertFalse(assessment.available)
        self.assertIsNone(assessment.total)

    def test_empty_component_list_is_unavailable_not_zero(self):
        state = _state()
        state.classify.dq = []
        self.assertIsNone(decision_quality.authoritative_dq_total(state))

    def test_all_zero_components_are_available_and_zero(self):
        state = _state()
        state.classify.dq = [0.0, 0.0, 0.0, 0.0]
        assessment = decision_quality.authoritative_dq(state)
        self.assertTrue(assessment.available)
        self.assertEqual(assessment.total, 0.0)

    def test_deriving_the_value_does_not_mutate_state(self):
        state = _state()
        before = state.model_dump(mode="json")
        decision_quality.authoritative_dq(state)
        decision_quality.dq_export_projection(state)
        decision_quality.dq_display_text(state)
        self.assertEqual(state.model_dump(mode="json"), before)


class TestEverySurfaceAgrees(unittest.TestCase):
    """The observed inconsistency: report/classify vs project_state/calibration."""

    def test_classify_summary_project_state_calibration_and_workspace_agree(self):
        state = _state()

        self.assertIn(f"DQ={DQ_TOTAL:g}", summarize_phase_output("classify", state))

        archive = exporters.build_machine_archive_payload(state)
        self.assertEqual(archive["project_state.json"]["dq"]["total"], DQ_TOTAL)

        snapshots = archive["calibration_predictions.json"]["calibration_snapshots"]
        self.assertTrue(snapshots)
        self.assertEqual(snapshots[0]["dq_total"], DQ_TOTAL)

        self.assertEqual(build_workspace_summary(state).score_summary.dq_total, DQ_TOTAL)

    def test_calibration_snapshot_matches_classify(self):
        objects = build_decision_objects(_state())
        self.assertTrue(objects.calibration_snapshots)
        self.assertEqual(objects.calibration_snapshots[0].dq_total, DQ_TOTAL)

    def test_operator_export_renders_the_same_value(self):
        summary = exporters.operator_classification_summary(_state())
        self.assertIn("40 total", summary)

    def test_unavailable_dq_is_not_rendered_as_zero(self):
        state = _state()
        state.classify.dq = []
        self.assertIn("Not measured", exporters.operator_classification_summary(state))
        self.assertIsNone(build_workspace_summary(state).score_summary.dq_total)
        archive = exporters.build_machine_archive_payload(state)
        self.assertFalse(archive["project_state.json"]["dq"]["available"])
        self.assertIsNone(archive["project_state.json"]["dq"]["total"])


class TestExportersDoNotMutateState(unittest.TestCase):
    """Exporters are observational with respect to ProjectState."""

    def test_machine_archive_is_observational(self):
        state = _state()
        before = state.model_dump(mode="json")
        exporters.build_machine_archive_payload(state)
        self.assertEqual(state.model_dump(mode="json"), before)

    def test_every_export_profile_is_observational(self):
        for profile, formats in exporters.EXPORT_PROFILE_FORMATS.items():
            if profile == "technology_readiness_workbook":
                continue  # requires a different project_type; covered by its own suite
            for fmt in sorted(formats):
                with self.subTest(profile=profile, format=fmt):
                    state = _state()
                    before = state.model_dump(mode="json")
                    try:
                        exporters.export_project_profile_bytes(state, profile, fmt)
                    except exporters.ExportProfileConflict:
                        continue
                    self.assertEqual(state.model_dump(mode="json"), before)

    def test_readonly_projections_are_observational(self):
        state = _state()
        before = state.model_dump(mode="json")
        exporters.operator_classification_summary(state)
        exporters.build_export_manifest(state, "machine_archive", "zip")
        self.assertEqual(state.model_dump(mode="json"), before)

    def test_legacy_dq_container_is_left_untouched_on_state(self):
        state = _state()
        exporters.build_machine_archive_payload(state)
        self.assertEqual(state.dq.model_dump(), {"frame": 0.0, "alt": 0.0, "info": 0.0, "val": 0.0, "reas": 0.0, "commit": 0.0})


class TestGateUsesTheAuthoritativeValue(unittest.TestCase):
    def test_gate_reads_the_same_number(self):
        from config import GATE_CONFIGS

        state = _state()
        state.phase_confidence["classify"] = 1.0
        blocking = " ".join(check_gate(state, "classify")["blocking"])
        minimum = GATE_CONFIGS["classify"].dq_minimum
        if minimum and DQ_TOTAL < minimum:
            self.assertIn(f"DQ={DQ_TOTAL:.0f}%", blocking)
        else:
            self.assertNotIn("DQ=", blocking)

    def test_gate_fails_closed_when_dq_is_required_but_unavailable(self):
        state = _state()
        state.classify.dq = []
        self.assertIn("DQ unavailable", " ".join(check_gate(state, "classify")["blocking"]))


if __name__ == "__main__":
    unittest.main()
