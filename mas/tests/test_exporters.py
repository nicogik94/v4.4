"""Tests for profile-based project exports."""
import json
import os
import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
import report_freshness  # noqa: E402
from clarifications import (  # noqa: E402
    ClarificationCycle,
    ClarificationPriority,
    ClarificationQuestion,
    ClarificationStatus,
)
from exporters import (  # noqa: E402
    build_client_dossier_markdown,
    build_export_manifest,
    build_machine_archive_payload,
    build_operator_dossier_markdown,
    export_project_profile_bytes,
    sanitize_for_export,
)
from state import (  # noqa: E402
    AuditOutput,
    FMEAItem,
    KnowledgeItem,
    KnowledgeLayerState,
    MonitorCanary,
    MonitorCircuitBreaker,
    MonitorOODASchedule,
    MonitorOutput,
    MonitorScheduleItem,
    PhaseStatus,
    ProjectState,
    StrategyOutput,
    UploadedFileManifest,
)
from tests.test_decision_objects import make_state  # noqa: E402


def _docx_text(payload: bytes) -> str:
    document = Document(BytesIO(payload))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _docx_table_count(payload: bytes) -> int:
    return len(Document(BytesIO(payload)).tables)


def _attach_report_generation_metadata(
    state: ProjectState,
    *,
    code_version: str,
    generated_at: str = "2026-05-01T00:00:00Z",
) -> None:
    state.policy_audit_log.append(
        {
            "ts": 1770000000.0,
            "event_type": "report_generated",
            "phase": "report",
            "details": report_freshness.build_report_generation_metadata(
                state.report,
                code_version=code_version,
                generated_at=generated_at,
            ),
        }
    )


CLIENT_HEADINGS = [
    "What decision we reviewed",
    "Recommended path",
    "Why this is recommended",
    "What evidence was used",
    "What should happen next",
    "Timeline / 7-30-60-90 roadmap",
    "Key risks",
    "What to monitor",
    "Open assumptions / questions",
    "Human review note",
]


OPERATOR_HEADINGS = [
    "Cover / project metadata",
    "Executive summary",
    "Current recommendation",
    "Decision snapshot",
    "Phase completion status",
    "Dashboard overview",
    "Original input",
    "Classification summary",
    "Hypotheses table",
    "Gauntlet / stress-test summary",
    "Audit findings",
    "Evidence and source summary",
    "Strategy plan",
    "SQI / quality review",
    "Monitoring plan",
    "Workspace summary",
    "Risks and open questions",
    "Decision trace / explainability",
    "Clarifications / assumptions",
    "Report appendix",
    "Technical appendix",
]


def _assert_in_order(testcase: unittest.TestCase, text: str, headings: list[str]) -> None:
    position = -1
    for heading in headings:
        next_position = text.find(heading)
        testcase.assertGreater(next_position, position, heading)
        position = next_position


def make_export_state(project_id: str = "export-profile"):
    state = make_state(project_id)
    state.report = """# Executive Summary
Proceed with a bounded audit pilot [Evidence: ev-market | chunk=1].

# The Decision
Decide whether to fund the pilot.

# Recommended Path
Run the 30-day pilot before the full program.

# Why This Is Recommended
The pilot limits commitment while validating demand.

# Options Considered
| Option | Verdict |
|---|---|
| Pilot | Recommended |

# Evidence Used
| Evidence | What it suggests |
|---|---|
| Market note | There is a concrete source marker. |

# Key Risks
Scope creep and unclear ownership.

# Assumptions and Open Questions
Who owns the pilot remains open.

# Roadmap
Day 1-30: run the audit.

# Next Steps
- Confirm owner.
- Confirm data access.

# Monitoring and Kill Criteria
Stop if data access is unavailable.

# Appendix: Technical Analysis
Technical notes stay here.
"""
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
        state.phase_summaries[phase] = f"{phase} summary"
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ev-market",
                source_id="src-1",
                title="Market note",
                source_ref="upload:file-1:market.pdf#chunk=1",
                structured_payload={"locator": "chunk=1"},
            )
        ],
        uploaded_files=[
            UploadedFileManifest(
                file_id="file-1",
                source_id="src-1",
                filename="market.pdf",
                media_type="application/pdf",
                size_bytes=321,
                storage_ref=r"C:\Users\nicoc\private\market.pdf",
            )
        ],
    )
    state.clarification_cycles = [
        ClarificationCycle(
            cycle_id="cycle-1",
            project_id=state.project_id,
            questions=[
                ClarificationQuestion(
                    question_id="q1",
                    text="Who owns the pilot?",
                    why_it_matters="Ownership controls follow-through.",
                    priority=ClarificationPriority.CRITICAL,
                    affected_phase="strategy",
                    source_gap="stakeholder_audience",
                    status=ClarificationStatus.OPEN,
                )
            ],
        )
    ]
    state.policy_audit_log = [
        {
            "event_type": "policy_gate_blocked",
            "phase": "strategy",
            "details": {
                "api_key": "sk-test-secret",
                "raw_prompt": "hidden prompt",
                "raw_provider_payload": {"token": "provider-token"},
                "chain_of_thought": "hidden reasoning",
                "scenario_probability": 0.91,
                "local_path": r"C:\Users\nicoc\private\payload.json",
            },
        }
    ]
    state.data = r"Local notes at C:\Users\nicoc\private\data.csv with api_key=sk-test-secret"
    return state


class TestCodeVersionFreshness(unittest.TestCase):
    def test_current_code_version_uses_safe_env_var_first(self):
        env = {
            "V4_CODE_VERSION": "abc123",
            "GIT_COMMIT": "",
            "COMMIT_SHA": "",
            "SOURCE_VERSION": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("report_freshness.subprocess.run") as run_mock:
                self.assertEqual(report_freshness.current_code_version(), "abc123")

        run_mock.assert_not_called()

    def test_current_code_version_ignores_unsafe_env_and_uses_git_root(self):
        repo_root = ROOT.parent
        env = {
            "V4_CODE_VERSION": r"abc123 C:\Users\example\secret.txt",
            "GIT_COMMIT": "",
            "COMMIT_SHA": "",
            "SOURCE_VERSION": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("report_freshness._detect_repo_root", return_value=repo_root):
                with patch(
                    "report_freshness.subprocess.run",
                    return_value=SimpleNamespace(stdout="git789\n"),
                ) as run_mock:
                    self.assertEqual(report_freshness.current_code_version(), "git789")

        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(repo_root))

    def test_current_code_version_returns_unknown_only_when_env_and_git_fail(self):
        env = {name: "" for name in report_freshness.CODE_VERSION_ENV_VARS}
        with patch.dict(os.environ, env, clear=False):
            with patch("report_freshness._detect_repo_root", return_value=ROOT.parent):
                with patch("report_freshness.subprocess.run", side_effect=FileNotFoundError):
                    self.assertEqual(report_freshness.current_code_version(), report_freshness.UNKNOWN_CODE_VERSION)

    def test_export_manifest_uses_code_version_helper_for_manifest_and_freshness(self):
        state = make_export_state("manifest-version")
        _attach_report_generation_metadata(state, code_version="same999")

        with patch("report_freshness.current_code_version", return_value="same999"):
            manifest = build_export_manifest(state, "machine_archive", "zip")

        self.assertEqual(manifest["code_version"], "same999")
        self.assertEqual(manifest["report_freshness"]["current_code_version"], "same999")
        self.assertEqual(manifest["report_freshness"]["generated_code_version"], "same999")
        self.assertEqual(manifest["report_freshness"]["status"], "fresh")


class TestProfileExporterHelpers(unittest.TestCase):
    def test_report_profile_exports_report_only(self):
        state = make_export_state("report-only")
        state.report = "Standalone report only."

        payload, media_type, filename = export_project_profile_bytes(state, "report", "docx")
        text = _docx_text(payload)

        self.assertEqual(media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("report-only-report-", filename)
        self.assertIn("Standalone report only.", text)
        self.assertNotIn("Executive Summary", text)
        self.assertNotIn("classify summary", text)

    def test_all_profiles_export_valid_formats(self):
        state = make_export_state("all-profiles")
        expected = {
            ("report", "docx"): "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ("report", "pdf"): "application/pdf",
            ("client_dossier", "docx"): "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ("client_dossier", "pdf"): "application/pdf",
            ("operator_dossier", "docx"): "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ("operator_dossier", "pdf"): "application/pdf",
            ("machine_archive", "zip"): "application/zip",
        }

        for (profile, fmt), media_type in expected.items():
            with self.subTest(profile=profile, format=fmt):
                payload, actual_media_type, filename = export_project_profile_bytes(state, profile, fmt)
                self.assertGreater(len(payload), 100)
                self.assertEqual(actual_media_type, media_type)
                self.assertIn(f"{profile}-", filename)
                if fmt == "pdf":
                    self.assertTrue(payload.startswith(b"%PDF"))
                if fmt == "zip":
                    with zipfile.ZipFile(BytesIO(payload)) as archive:
                        self.assertNotIn("raw_project_state.json", archive.namelist())

    def test_report_profile_includes_versioned_freshness_warning_when_stale(self):
        state = make_export_state("stale-report-profile")
        _attach_report_generation_metadata(state, code_version="old123")

        with patch("report_freshness.current_code_version", return_value="new456"):
            payload, _, _ = export_project_profile_bytes(state, "report", "docx")

        text = _docx_text(payload)
        self.assertIn(
            "Report freshness warning: this report was generated with code version old123; "
            "current code version is new456. Regenerate the report before validation or client delivery.",
            text,
        )

    def test_client_dossier_includes_concise_freshness_warning_when_stale(self):
        state = make_export_state("stale-client-profile")
        _attach_report_generation_metadata(state, code_version="old123")

        with patch("report_freshness.current_code_version", return_value="new456"):
            markdown = build_client_dossier_markdown(state)

        self.assertIn("Report freshness warning:", markdown)
        self.assertIn("old123", markdown)
        self.assertIn("new456", markdown)
        self.assertNotIn("Freshness metadata", markdown)

    def test_operator_dossier_includes_detailed_freshness_warning_when_stale(self):
        state = make_export_state("stale-operator-profile")
        _attach_report_generation_metadata(state, code_version="old123")

        with patch("report_freshness.current_code_version", return_value="new456"):
            markdown = build_operator_dossier_markdown(state)

        self.assertIn("Report freshness warning:", markdown)
        self.assertIn("Freshness metadata", markdown)
        self.assertIn("Generated code version", markdown)
        self.assertIn("old123", markdown)
        self.assertIn("Current code version", markdown)
        self.assertIn("new456", markdown)

    def test_matching_report_generation_version_suppresses_freshness_warning(self):
        state = make_export_state("fresh-report-profile")
        _attach_report_generation_metadata(state, code_version="same123")

        with patch("report_freshness.current_code_version", return_value="same123"):
            report_payload, _, _ = export_project_profile_bytes(state, "report", "docx")
            client_markdown = build_client_dossier_markdown(state)
            operator_markdown = build_operator_dossier_markdown(state)

        self.assertNotIn("Report freshness warning", _docx_text(report_payload))
        self.assertNotIn("Report freshness warning", client_markdown)
        self.assertNotIn("Report freshness warning", operator_markdown)

    def test_legacy_missing_generation_version_triggers_generic_freshness_warning(self):
        state = make_export_state("legacy-report-profile")

        with patch("report_freshness.current_code_version", return_value="new456"):
            payload, _, _ = export_project_profile_bytes(state, "report", "docx")

        self.assertIn(
            "Report freshness warning: this report may have been generated by an older code version. "
            "Regenerate the report before using this export for validation or client delivery.",
            _docx_text(payload),
        )

    def test_client_dossier_includes_expected_sections_and_safe_cdp_wording(self):
        markdown = build_client_dossier_markdown(make_export_state("client-profile"))

        _assert_in_order(self, markdown, CLIENT_HEADINGS)
        for expected in (
            "Run the 30-day pilot before the full program.",
            "Day 1-30: run the audit.",
            "Stop if data access is unavailable.",
            "Who owns the pilot?",
            "This export is intended to support human review and decision-making.",
        ):
            self.assertIn(expected, markdown)
        for forbidden in (
            "policy_audit_log",
            "raw_prompt",
            "raw_provider_payload",
            "sk-test-secret",
            "chain_of_thought",
            "scenario_probability",
            "semantic proof",
            "defensibility score",
            "evidence validated",
            "claim proven",
        ):
            self.assertNotIn(forbidden, markdown)

    def test_operator_dossier_includes_summaries_and_excludes_unsafe_detail(self):
        markdown = build_operator_dossier_markdown(make_export_state("operator-profile"))

        _assert_in_order(self, markdown, OPERATOR_HEADINGS)
        for expected in (
            "Phase completion status",
            "Dashboard overview",
            "Evidence and source summary",
            "Decision trace / explainability",
            "Technical appendix",
            "policy_gate_blocked=1",
            "Market note",
        ):
            self.assertIn(expected, markdown)
        for forbidden in (
            "sk-test-secret",
            "raw_provider_payload",
            "raw_prompt",
            "chain_of_thought",
            "ProjectState",
            r"C:\Users\nicoc",
        ):
            self.assertNotIn(forbidden, markdown)

    def test_sparse_dossiers_include_explicit_empty_states(self):
        sparse = ProjectState(
            project_id="sparse-export",
            project_name="Sparse export",
            brief="Sparse decision brief",
        )
        client_markdown = build_client_dossier_markdown(sparse)
        operator_markdown = build_operator_dossier_markdown(sparse)
        combined = client_markdown + "\n\n" + operator_markdown

        _assert_in_order(self, client_markdown, CLIENT_HEADINGS)
        _assert_in_order(self, operator_markdown, OPERATOR_HEADINGS)
        for expected in (
            "No hypotheses have been generated yet.",
            "No uploaded files were attached.",
            "No monitoring plan is available yet.",
            "This section will populate after the strategy phase runs.",
            "No imported evidence is available yet.",
            "No decision trace is available yet.",
            "No clarification answers have been submitted yet.",
            "No audit findings are available yet.",
        ):
            self.assertIn(expected, combined)
        self.assertIn("This is a structured hypothesis map, not a measured audit.", combined)
        self.assertIn("Provisional report: clarification questions have not been answered.", combined)
        self.assertIn("Sprint 0 evidence collection should validate", client_markdown)

    def test_client_dossier_simplifies_internal_jargon_but_operator_keeps_detail(self):
        state = make_export_state("client-jargon")
        state.report = """# Executive Summary
H1 has BF 12, DQ 65, H_norm 0.12, rho 0.45, FMEA RPN 336 [#10], citation unavailable.
H2 has Jaccard index 0.42, Brier score 0.20, ECE 0.12, probability 70%, and failure probability 0.70.

# The Decision
Choose whether to productize the dashboard.

# Recommended Path
Run Sprint 0 before productization.

# Evidence Used
H1 BF 12, DQ 65, H_norm 0.12, rho 0.45, FMEA row citation unavailable. H2 Jaccard index 0.42, Brier score 0.20, ECE 0.12, citation unavailable.

# Key Risks
RPN 336 and FMEA gaps remain.

# Appendix: Technical Analysis
FMEA RPN 336 BF 12 DQ 65 H_norm 0.12 rho 0.45 [#24]
"""
        state.audit = AuditOutput(
            fmea=[FMEAItem(component="dashboard", failure_mode="unclear owner", rpn=336, action="assign owner")],
            top_findings=["FMEA RPN 336 needs review"],
        )

        client_markdown = build_client_dossier_markdown(state)
        operator_markdown = build_operator_dossier_markdown(state)

        for forbidden in ("RPN", "FMEA", "BF", "DQ", "H_norm", "rho", "Jaccard", "Brier score", "ECE", "[#10]", "[#24]"):
            self.assertNotIn(forbidden, client_markdown)
        for expected in (
            "risk priority",
            "structured risk review",
            "internal confidence diagnostic",
            "evidence quality diagnostic",
            "uncertainty diagnostic",
            "related-hypothesis risk",
            "user-value hypothesis",
            "schema overlap score",
            "forecast accuracy check",
            "calibration check",
            "No concrete citation locators were available for this project",
        ):
            self.assertIn(expected, client_markdown)
        self.assertIn("FMEA", operator_markdown)
        self.assertIn("RPN", operator_markdown)

    def test_monitor_signals_fill_success_metrics_when_strategy_metrics_empty(self):
        state = make_export_state("monitor-success")
        state.strategy.success_metrics = []
        state.monitor = MonitorOutput(
            ooda_schedule=MonitorOODASchedule(
                daily=[MonitorScheduleItem(metric="Pilot activation", owner="Product Owner", source="product telemetry")]
            ),
            canaries=[MonitorCanary(signal="Regeneration failure rate", direction="down", window="7d", meaning="workflow quality")],
            circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="failure rate more than 15%", reset="two stable weeks")],
            commitment_score=70,
            commitment_rationale="Owner and cadence confirmed.",
        )

        operator_markdown = build_operator_dossier_markdown(state)
        client_markdown = build_client_dossier_markdown(state)

        self.assertIn("Success metrics are captured in the monitoring plan below.", operator_markdown)
        self.assertIn("Regeneration failure rate", operator_markdown)
        self.assertIn("Pilot activation", client_markdown)
        self.assertNotIn("No success metrics saved.", operator_markdown)

    def test_unavailable_commitment_score_renders_not_scored(self):
        state = make_export_state("commitment-unscored")
        state.monitor.commitment_score = 0
        state.monitor.commitment_rationale = ""

        combined = build_client_dossier_markdown(state) + "\n\n" + build_operator_dossier_markdown(state)

        self.assertIn("Commitment score: Not scored — requires operator confirmation.", combined)
        self.assertNotIn("Commitment score: 0", combined)

    def test_numeric_commitment_with_placeholder_rationale_renders_not_scored(self):
        state = make_export_state("commitment-placeholder")
        state.monitor.commitment_score = 71
        state.monitor.commitment_rationale = "TBD / operator confirmation"

        combined = build_client_dossier_markdown(state) + "\n\n" + build_operator_dossier_markdown(state)

        self.assertIn("Commitment score: Not scored — requires operator confirmation.", combined)
        self.assertNotIn("Commitment score: 71", combined)

    def test_substantive_commitment_rationale_preserves_numeric_score(self):
        state = make_export_state("commitment-substantive")
        state.monitor.commitment_score = 61
        state.monitor.commitment_rationale = "Executive sponsor, owner, budget review, and weekly cadence are confirmed."

        combined = build_client_dossier_markdown(state) + "\n\n" + build_operator_dossier_markdown(state)

        self.assertIn("Commitment score: 61", combined)
        self.assertIn("Executive sponsor, owner, budget review, and weekly cadence are confirmed.", combined)

    def test_threshold_conflicts_warn_and_consistent_thresholds_do_not(self):
        conflict = make_export_state("threshold-conflict")
        conflict.report = (
            "# Executive Summary\n"
            "Use rho target 0.45 for portfolio risk.\n"
            "# Monitoring and Kill Criteria\n"
            "Canary success more than 5/20 in one table and more than 15% elsewhere. rho less than 0.50.\n"
        )
        consistent = make_export_state("threshold-consistent")
        consistent.report = (
            "# Executive Summary\n"
            "Use rho target 0.45 for portfolio risk.\n"
            "# Monitoring and Kill Criteria\n"
            "Canary success more than 5/20 in each table. rho target 0.45.\n"
        )

        self.assertIn("Threshold consistency warning", build_operator_dossier_markdown(conflict))
        self.assertIn("Confirm one decision matrix in Sprint 0", build_client_dossier_markdown(conflict))
        self.assertNotIn("Threshold consistency warning", build_operator_dossier_markdown(consistent))

    def test_exact_dollar_estimate_without_budget_evidence_is_caveated(self):
        state = ProjectState(
            project_id="dollar-without-budget",
            project_name="Dollar without budget",
            brief="Decide productization direction for a product pilot.",
            report="# Executive Summary\nExpected value is $125,000 with high confidence.",
        )

        operator_markdown = build_operator_dossier_markdown(state)

        self.assertIn("Exact dollar estimates appear without budget or spend evidence", operator_markdown)
        self.assertIn("High confidence only that evidence collection is required", operator_markdown)

    def test_sparse_client_dossier_replaces_exact_probabilities_but_keeps_provisional_gates(self):
        state = ProjectState(
            project_id="sparse-probabilities",
            project_name="Sparse probabilities",
            brief="Improve growth performance across sales, retention, and pipeline.",
            report="""# Executive Summary
H1 probability 70% and predicted failure probability 0.70.

# Monitoring and Kill Criteria
Stop threshold is more than 15% churn.
The proposed planning gate is more than 20% activation.
""",
        )

        markdown = build_client_dossier_markdown(state)

        self.assertNotIn("probability 70%", markdown)
        self.assertNotIn("failure probability 0.70", markdown)
        self.assertIn("model-generated prior", markdown)
        self.assertIn("high provisional failure risk", markdown)
        self.assertIn("operator-confirmed threshold required", markdown)
        self.assertIn("proposed planning gate is more than 20% activation", markdown)

    def test_growth_client_dossier_avoids_generated_web_language_without_explicit_context(self):
        state = ProjectState(
            project_id="growth-client-no-web",
            project_name="Growth performance",
            brief="Improve growth performance across revenue operations, retention, and pipeline.",
            report="""# Executive Summary
Generated text says Search Console, GA4, crawl, CMS/schema capability, SEO Lead, and Web/CMS Owner.

# Evidence Used
Generated text says editorial evidence and CMS/schema capability.
""",
        )

        markdown = build_client_dossier_markdown(state)

        self.assertIn("cohort retention", markdown)
        for forbidden in ("Search Console", "GA4", "CMS/schema capability", "SEO Lead", "Web/CMS Owner"):
            self.assertNotIn(forbidden, markdown)

    def test_productization_client_and_operator_include_wave2_matrix_without_cms_leakage(self):
        state = ProjectState(
            project_id="productization-wave2",
            project_name="Productization direction",
            brief="Choose the v4 productization direction, Wave 2 roadmap, template abstraction, ROI engine, and pilot sessions.",
            report="# Executive Summary\nProceed with Wave 0 and Wave 1 before Wave 2.",
        )

        client_markdown = build_client_dossier_markdown(state)
        operator_markdown = build_operator_dossier_markdown(state)

        for markdown in (client_markdown, operator_markdown):
            self.assertIn("Wave 2 Graduation Matrix", markdown)
            self.assertIn("Proceed to Wave 2 if", markdown)
            self.assertIn("operator-set threshold", markdown)
            self.assertNotIn("70%", markdown)
            self.assertNotIn("80%", markdown)
        self.assertNotIn("CMS/schema capability", client_markdown)

    def test_telemetry_privacy_caveat_appears_for_logging_recommendations(self):
        state = make_export_state("telemetry-export")
        state.report = "# Executive Summary\nRecommend dashboard telemetry, product analytics, session replay, and regeneration-event logging."

        client_markdown = build_client_dossier_markdown(state)
        operator_markdown = build_operator_dossier_markdown(state)

        for markdown in (client_markdown, operator_markdown):
            self.assertIn("Log event metadata by default.", markdown)
            self.assertIn("Do not log raw briefs, uploaded content, report text, provider payloads, secrets, local paths, API keys, or sensitive user text", markdown)
        self.assertNotIn("raw_provider_payload", client_markdown)
        self.assertNotIn("raw_prompt", client_markdown)
        self.assertNotIn("sk-test-secret", client_markdown)
        self.assertIn("This export is intended to support human review and decision-making.", client_markdown)

    def test_profile_docx_outputs_include_headings_and_tables(self):
        state = make_export_state("docx-profile")

        client_payload, _, _ = export_project_profile_bytes(state, "client_dossier", "docx")
        operator_payload, _, _ = export_project_profile_bytes(state, "operator_dossier", "docx")
        client_text = _docx_text(client_payload)
        operator_text = _docx_text(operator_payload)

        self.assertIn("What decision we reviewed", client_text)
        self.assertIn("Human review note", client_text)
        self.assertIn("Cover / project metadata", operator_text)
        self.assertIn("Technical appendix", operator_text)
        self.assertGreaterEqual(_docx_table_count(client_payload), 1)
        self.assertGreaterEqual(_docx_table_count(operator_payload), 1)

    def test_profile_pdfs_build_for_report_and_dossiers(self):
        state = make_export_state("pdf-profile")

        for profile in ("report", "client_dossier", "operator_dossier"):
            with self.subTest(profile=profile):
                payload, media_type, filename = export_project_profile_bytes(state, profile, "pdf")
                self.assertEqual(media_type, "application/pdf")
                self.assertIn(f"{profile}-", filename)
                self.assertTrue(payload.startswith(b"%PDF"))

    def test_report_docx_normalizes_blockquoted_at_a_glance_table(self):
        state = make_export_state("blockquote-report")
        state.report = """# Executive Summary
> **At a Glance**
>
> | Field | Detail |
> |---|---|
> | Decision | Run Sprint 0 first |
> | Recommendation | Validate before implementation |

---

# Evidence Used
| Evidence | What It Suggests |
|---|---|
| Supplied context | Structural analysis only |

Stop if >2 critical assumptions remain unknown.
"""

        payload, _, _ = export_project_profile_bytes(state, "report", "docx")
        text = _docx_text(payload)

        self.assertIn("At a Glance", text)
        self.assertIn("Decision", text)
        self.assertIn("Run Sprint 0 first", text)
        self.assertNotIn("| Field | Detail |", text)
        self.assertNotIn("|---|---|", text)
        self.assertNotIn("---", text)
        self.assertNotIn(">", text)
        self.assertIn("greater than 2 critical assumptions", text)
        for line in text.splitlines():
            self.assertFalse(line.strip().startswith(">"), line)

    def test_report_pdf_builds_with_blockquoted_table_and_wide_table_cards(self):
        state = make_export_state("wide-table-report")
        state.report = """# Executive Summary
> **At a Glance**
>
> | Field | Detail |
> |---|---|
> | Decision | Run Sprint 0 first |
> | Recommendation | Validate before implementation |

# Options Considered
| Option | Upside | Downside | Best Use Case | Verdict |
|---|---|---|---|---|
| Sprint 0 | Validates assumptions | Adds discovery time | Evidence-light SEO work | Recommended |
"""

        payload, media_type, filename = export_project_profile_bytes(state, "report", "pdf")

        self.assertEqual(media_type, "application/pdf")
        self.assertIn("wide-table-report-report-", filename)
        self.assertTrue(payload.startswith(b"%PDF"))

    def test_machine_archive_contains_expected_sanitized_files(self):
        state = make_export_state("archive-profile")
        payload, media_type, filename = export_project_profile_bytes(state, "machine_archive", "zip")

        self.assertEqual(media_type, "application/zip")
        self.assertIn("archive-profile-machine_archive-", filename)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertNotIn("raw_project_state.json", names)
            self.assertTrue(
                {
                    "export_manifest.json",
                    "project_state.json",
                    "report.md",
                    "phase_outputs.json",
                    "clarifications.json",
                    "evidence_locator_register.json",
                    "uploaded_file_manifest.json",
                    "policy_summary.json",
                }.issubset(names)
            )
            combined = "\n".join(archive.read(name).decode("utf-8") for name in names)
            self.assertNotIn("sk-test-secret", combined)
            self.assertNotIn("hidden reasoning", combined)
            self.assertNotIn("hidden prompt", combined)
            self.assertNotIn("provider-token", combined)
            self.assertNotIn(r"C:\Users\nicoc", combined)

            manifest = json.loads(archive.read("export_manifest.json").decode("utf-8"))
            self.assertEqual(manifest["export_schema_version"], "1.0")
            self.assertEqual(manifest["export_profile"], "machine_archive")
            self.assertEqual(manifest["export_format"], "zip")
            self.assertIn("code_version", manifest)
            self.assertIn("report_freshness", manifest)
            self.assertIn("status", manifest["report_freshness"])
            self.assertIn("is_fresh", manifest["report_freshness"])
            self.assertIn("project_state.json", manifest["included_files"])

            uploads = json.loads(archive.read("uploaded_file_manifest.json").decode("utf-8"))
            self.assertEqual(uploads[0]["file_id"], "file-1")
            self.assertEqual(uploads[0]["original_filename"], "market.pdf")
            self.assertEqual(uploads[0]["content_type"], "application/pdf")
            self.assertEqual(uploads[0]["size_bytes"], 321)
            self.assertEqual(uploads[0]["storage_ref"], "[REDACTED]")

    def test_invalid_profile_and_format_combinations_raise(self):
        state = make_export_state("invalid-profile")

        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "unknown", "pdf")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "client_dossier", "zip")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "machine_archive", "pdf")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "report", "exe")

    def test_sanitizer_redacts_sensitive_keys_and_paths(self):
        payload = {
            "api_key": "sk-test-secret",
            "nested": {
                "raw_provider_payload": {"token": "provider-token"},
                "note": r"See C:\Users\nicoc\private\file.txt",
            },
        }

        sanitized = sanitize_for_export(payload, "machine_archive", mode="redact")

        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["raw_provider_payload"], "[REDACTED]")
        self.assertNotIn(r"C:\Users\nicoc", sanitized["nested"]["note"])

    def test_build_machine_archive_payload_has_no_raw_project_state_file(self):
        payload = build_machine_archive_payload(make_export_state("payload-profile"))

        self.assertIn("project_state.json", payload)
        self.assertNotIn("raw_project_state.json", payload)


class TestProfileExportApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_legacy_export_route_still_returns_attachments(self):
        state = make_export_state("legacy-route")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            docx_response = await api.export_project(state.project_id, "docx")
            pdf_response = await api.export_project(state.project_id, "pdf")

        self.assertEqual(docx_response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(pdf_response.media_type, "application/pdf")
        self.assertIn("attachment;", docx_response.headers["content-disposition"])
        self.assertIn("attachment;", pdf_response.headers["content-disposition"])
        self.assertIn(".docx", docx_response.headers["content-disposition"])
        self.assertIn(".pdf", pdf_response.headers["content-disposition"])

    async def test_query_route_defaults_profile_to_report(self):
        state = make_export_state("query-default")
        state.report = "Only this report content."
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.export_project_profile(state.project_id, format="docx")

        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("query-default-report-", response.headers["content-disposition"])
        text = _docx_text(response.body)
        self.assertIn("Only this report content.", text)
        self.assertNotIn("classify summary", text)

    async def test_query_route_rejects_missing_or_invalid_format(self):
        state = make_export_state("query-invalid")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as missing:
                await api.export_project_profile(state.project_id)
            with self.assertRaises(HTTPException) as unknown:
                await api.export_project_profile(state.project_id, profile="bogus", format="pdf")
            with self.assertRaises(HTTPException) as invalid_combo:
                await api.export_project_profile(state.project_id, profile="client_dossier", format="zip")

        self.assertEqual(missing.exception.status_code, 400)
        self.assertEqual(unknown.exception.status_code, 400)
        self.assertEqual(invalid_combo.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
