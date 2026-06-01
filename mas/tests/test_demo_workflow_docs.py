import ast
import subprocess
import sys
from pathlib import Path


MAS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAS_ROOT.parent
DOCS_ROOT = REPO_ROOT / "docs"


REQUIRED_DOCS = [
    DOCS_ROOT / "v5-DEMO-WORKFLOW.md",
    DOCS_ROOT / "v5-DEMO-SCRIPT.md",
    DOCS_ROOT / "v5-CLIENT-EXPLANATION.md",
    DOCS_ROOT / "v5-DEMO-READINESS-CHECKLIST.md",
]

STRATEGIC_DECISION_AUDIT_DOCS = [
    DOCS_ROOT / "v5-STRATEGIC-DECISION-AUDIT.md",
    DOCS_ROOT / "templates" / "strategic-decision-audit-intake.md",
    DOCS_ROOT / "examples" / "strategic-decision-audit-brief.md",
    DOCS_ROOT / "v5-STRATEGIC-DECISION-DEMO-SCRIPT.md",
]

AUTOMATION_ROI_AUDIT_DOCS = [
    DOCS_ROOT / "v5-AUTOMATION-ROI-AUDIT.md",
    DOCS_ROOT / "templates" / "automation-roi-audit-intake.md",
    DOCS_ROOT / "examples" / "automation-roi-audit-brief.md",
    DOCS_ROOT / "v5-AUTOMATION-ROI-DEMO-SCRIPT.md",
]

BRIEF_HEADINGS = [
    "# ",
    "## Decision Question",
    "## Context",
    "## Constraints",
    "## Known Evidence",
    "## Unknowns",
    "## What A Good Recommendation Should Resolve",
    "## Suggested Files / Evidence To Upload If Available",
    "## Expected Output Types",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized_lower(text: str) -> str:
    return " ".join(text.lower().split())


def test_demo_docs_exist_in_repo_root_docs():
    for path in REQUIRED_DOCS:
        assert path.exists(), path
        assert MAS_ROOT / "docs" not in path.parents


def test_strategic_decision_audit_package_docs_exist_in_repo_root_docs():
    for path in STRATEGIC_DECISION_AUDIT_DOCS:
        assert path.exists(), path
        assert MAS_ROOT / "docs" not in path.parents


def test_automation_roi_audit_package_docs_exist_in_repo_root_docs():
    for path in AUTOMATION_ROI_AUDIT_DOCS:
        assert path.exists(), path
        assert MAS_ROOT / "docs" not in path.parents


def test_strategic_decision_audit_entrypoint_has_required_offer_sections():
    text = _read(DOCS_ROOT / "v5-STRATEGIC-DECISION-AUDIT.md")
    lower = _normalized_lower(text)

    for heading in (
        "# Strategic Decision Audit Packaged Offer",
        "## What This Offer Is",
        "## What This Offer Is Not",
        "## Intake Template",
        "## Example Brief",
        "## Recommended Upload Files",
        "## Operator Runbook",
        "## Sample Project Framing",
        "## Expected Export Checklist",
        "## Client-Safe Positioning Language",
        "## Boundaries And Disclaimers",
    ):
        assert heading in text

    for required in (
        "local operator workflow",
        "human review is required",
        "not legal advice",
        "not financial advice",
        "not public saas",
        "not a guaranteed recommendation engine",
        "not a new reasoning mode",
        "strategic_audit",
        "classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report",
        "client-safe after review",
        "operator-only",
        "internal archive",
    ):
        assert required in lower


def test_automation_roi_audit_entrypoint_has_required_offer_sections():
    text = _read(DOCS_ROOT / "v5-AUTOMATION-ROI-AUDIT.md")
    lower = _normalized_lower(text)

    for heading in (
        "# Automation ROI Audit Packaged Offer",
        "## What This Offer Is",
        "## What This Offer Is Not",
        "## Intake Template",
        "## Example Brief",
        "## Recommended Upload Files",
        "## Operator Runbook",
        "## Sample Project Framing",
        "## Expected Export Checklist",
        "## Client-Safe Positioning Language",
        "## Boundaries And Disclaimers",
    ):
        assert heading in text

    for required in (
        "docs/templates/tests-only",
        "local operator workflow",
        "human review is required",
        "roi assumptions are estimates, not guarantees",
        "not legal advice",
        "not financial advice",
        "not public saas",
        "not a guaranteed automation recommendation engine",
        "not a new reasoning mode",
        "not a first-class backend runtime template",
        "automation roi example framing",
        "automation_roi",
        "classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report",
        "client-safe after review",
        "operator-only",
        "internal archive",
    ):
        assert required in lower


def test_strategic_decision_audit_template_and_example_are_complete():
    template = _read(DOCS_ROOT / "templates" / "strategic-decision-audit-intake.md")
    example = _read(DOCS_ROOT / "examples" / "strategic-decision-audit-brief.md")
    combined = _normalized_lower(template + "\n" + example)

    for heading in (
        "## Decision Question",
        "## Alternatives Being Compared",
        "## Context",
        "## Constraints",
        "## Known Evidence",
        "## Unknowns",
        "## Success Criteria",
        "## Risk Classification",
        "## Recommended Upload Files",
        "## Expected Output Types",
    ):
        assert heading in template

    for heading in BRIEF_HEADINGS:
        assert heading in example

    assert "example brief" in combined
    assert "not a first-class vertical template or" in combined
    assert "runtime pack" in combined
    assert "human review is required" in combined
    assert "not legal advice" in combined
    assert "not financial advice" in combined
    assert "not public saas" in combined
    assert "not a guaranteed recommendation engine" in combined
    assert "not a new reasoning mode" in combined


def test_automation_roi_audit_template_and_example_are_complete():
    template = _read(DOCS_ROOT / "templates" / "automation-roi-audit-intake.md")
    example = _read(DOCS_ROOT / "examples" / "automation-roi-audit-brief.md")
    combined = _normalized_lower(template + "\n" + example)

    for heading in (
        "## Automation Question",
        "## Candidate Workflows",
        "## Current Workflow Baseline",
        "## Volume And Time Assumptions",
        "## Cost And ROI Assumptions",
        "## Implementation Constraints",
        "## Known Evidence",
        "## Unknowns",
        "## Success Criteria",
        "## Risk Classification",
        "## Recommended Upload Files",
        "## Expected Output Types",
        "## Human Review Notes",
    ):
        assert heading in template

    for heading in (
        "# Automation ROI Audit Example Brief",
        "## Automation Question",
        "## Candidate Workflows",
        "## Current Workflow Baseline",
        "## Volume And Time Assumptions",
        "## Cost And ROI Assumptions",
        "## Implementation Constraints",
        "## Known Evidence",
        "## Unknowns",
        "## What A Good Recommendation Should Resolve",
        "## Suggested Files / Evidence To Upload If Available",
        "## Expected Output Types",
        "## Human Review Reminder",
    ):
        assert heading in example

    assert "paste-ready example brief" in combined
    assert "not a first-class vertical template" in combined
    assert "not a first-class backend runtime template" in combined
    assert "runtime pack" in combined
    assert "human review is required" in combined
    assert "roi assumptions are estimates, not guarantees" in combined
    assert "not legal advice" in combined
    assert "not financial advice" in combined
    assert "not public saas" in combined
    assert "not a guaranteed automation recommendation engine" in combined
    assert "not a new reasoning mode" in combined


def test_strategic_decision_audit_demo_script_uses_existing_local_flow():
    text = _read(DOCS_ROOT / "v5-STRATEGIC-DECISION-DEMO-SCRIPT.md")
    lower = _normalized_lower(text)

    for required in (
        "docker compose port app 8000",
        "scripts\\demo_smoke_check.py",
        "dashboards/index.html",
        "strategic decision audit framing",
        "classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report",
        "client-safe after review",
        "operator-only",
        "internal archive",
        "human review required",
        "not legal advice",
        "not financial advice",
        "not public saas",
        "not a guaranteed recommendation engine",
        "not a new reasoning mode",
    ):
        assert required in lower


def test_automation_roi_audit_demo_script_uses_existing_local_flow():
    text = _read(DOCS_ROOT / "v5-AUTOMATION-ROI-DEMO-SCRIPT.md")
    lower = _normalized_lower(text)

    for required in (
        "docker compose port app 8000",
        "scripts\\demo_smoke_check.py",
        "dashboards/index.html",
        "automation roi example framing",
        "classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report",
        "client-safe after review",
        "operator-only",
        "internal archive",
        "human review required",
        "roi assumptions are estimates, not guarantees",
        "not legal advice",
        "not financial advice",
        "not public saas",
        "not a guaranteed automation recommendation engine",
        "not a new reasoning mode",
        "not a first-class backend runtime template",
    ):
        assert required in lower


def test_strategic_decision_audit_docs_do_not_overclaim_or_change_scope():
    combined = _normalized_lower("\n".join(_read(path) for path in STRATEGIC_DECISION_AUDIT_DOCS))

    for required_boundary in (
        "no auth",
        "no auth, tenancy, public deployment hardening",
        "provider routing changes",
        "queue/runtime changes",
        "report generation changes",
        "export schema changes",
        "regulated vertical logic",
    ):
        assert required_boundary in combined

    for prohibited in (
        "is a guaranteed recommendation engine",
        "public saas ready",
        "autonomous decision-maker",
        "adds a new workflow phase",
        "creates a new prompt path",
        "new provider routing",
        "is legal advice",
        "is financial advice",
    ):
        assert prohibited not in combined


def test_automation_roi_audit_docs_do_not_overclaim_or_change_scope():
    combined = _normalized_lower("\n".join(_read(path) for path in AUTOMATION_ROI_AUDIT_DOCS))

    for required_boundary in (
        "docs/templates/tests-only",
        "local operator workflow",
        "human review is required",
        "roi assumptions are estimates, not guarantees",
        "not legal advice",
        "not financial advice",
        "not public saas",
        "not a guaranteed automation recommendation engine",
        "not a new reasoning mode",
        "not a first-class backend runtime template",
        "no auth",
        "no auth, tenancy, public deployment hardening",
        "provider routing changes",
        "queue/runtime changes",
        "export schema changes",
        "automation-specific backend execution",
    ):
        assert required_boundary in combined

    for prohibited in (
        "guaranteed roi",
        "guarantees roi",
        "guarantees savings",
        "public saas ready",
        "autonomous implementation engine",
        "finance-approved by the engine",
        "legally approved by the engine",
        "new automation runtime",
        "first-class automation runtime",
        "safe to share before review",
    ):
        assert prohibited not in combined


def test_demo_docs_use_runtime_foundation_and_non_overclaim_language():
    combined = _normalized_lower("\n".join(_read(path) for path in REQUIRED_DOCS))

    assert "v5 runtime foundation demo workflow" in combined
    assert "not a fully released v5 product" in combined
    assert "not public saas" in combined
    assert "no public saas readiness" in combined
    assert "no autonomous decision-making" in combined
    assert "no guaranteed causal truth" in combined
    assert "human review remains required" in combined


def test_docker_discovered_port_guidance_comes_before_localhost_8000_fallback():
    for path in (DOCS_ROOT / "v5-DEMO-WORKFLOW.md", DOCS_ROOT / "v5-DEMO-READINESS-CHECKLIST.md"):
        text = _read(path)
        discovered = text.find("docker compose port app 8000")
        fallback = text.find("http://localhost:8000")
        assert discovered != -1
        assert fallback != -1
        assert discovered < fallback


def test_demo_briefs_are_examples_not_runtime_packs_and_have_required_headings():
    brief_dir = DOCS_ROOT / "demo-briefs"
    briefs = [
        brief_dir / "b2b-saas-pilot-expansion.md",
        brief_dir / "ai-automation-roi-prioritization.md",
    ]

    for path in briefs:
        text = _read(path)
        lower = text.lower()
        assert "example brief" in lower
        assert "not a first-class vertical template or" in lower
        assert "runtime pack" in lower
        for heading in BRIEF_HEADINGS:
            assert heading in text


def test_start_here_has_exactly_one_short_demo_workflow_pointer():
    text = _read(REPO_ROOT / "START_HERE.md")
    assert text.count("docs/v5-DEMO-WORKFLOW.md") == 2
    assert text.count("For a v5 runtime foundation demo workflow") == 1


def test_start_here_has_exactly_one_strategic_decision_audit_pointer():
    text = _read(REPO_ROOT / "START_HERE.md")
    assert text.count("docs/v5-STRATEGIC-DECISION-AUDIT.md") == 2
    assert text.count("For the first packaged offer") == 1


def test_start_here_has_exactly_one_automation_roi_audit_pointer():
    text = _read(REPO_ROOT / "START_HERE.md")
    assert text.count("docs/v5-AUTOMATION-ROI-AUDIT.md") == 2
    assert text.count("For the Automation ROI Audit packaged offer") == 1


def test_ingestion_contract_doc_covers_contract_and_caveats():
    text = _normalized_lower(_read(DOCS_ROOT / "v5-INGESTION-CONTRACT.md"))

    assert "case.v1" in text
    assert "legacy compatibility" in text
    assert "conflict rejection" in text
    assert "x-request-id" in text
    assert "run_id" in text
    assert "no api authentication" in text
    assert "no tenancy" in text
    assert "no public deployment hardening" in text
    assert "does not change prompts" in text
    assert "report semantics" in text
    assert "queue/runtime architecture" in text
    assert "export_schema_version" in text


def test_ingestion_contract_has_short_references_from_entry_docs():
    start_here = _read(REPO_ROOT / "START_HERE.md")
    context_brief = _read(DOCS_ROOT / "v4-ai-context-brief.md")
    runtime_smoke = _read(DOCS_ROOT / "local-runtime-smoke.md")

    assert start_here.count("docs/v5-INGESTION-CONTRACT.md") == 1
    assert context_brief.count("docs/v5-INGESTION-CONTRACT.md") == 1
    assert runtime_smoke.count("docs/v5-INGESTION-CONTRACT.md") == 1


def test_demo_checklist_includes_artifact_and_compose_exclusions():
    text = _read(DOCS_ROOT / "v5-DEMO-READINESS-CHECKLIST.md").lower()

    assert "docker-compose.yml not committed" in text
    assert "no generated artifacts committed" in text
    assert "exports/" in text
    assert "upload_store/" in text
    assert "scenario_shadow.sqlite3" in text


def test_demo_smoke_check_script_is_parseable_and_helpful():
    script = REPO_ROOT / "scripts" / "demo_smoke_check.py"
    ast.parse(script.read_text(encoding="utf-8"))

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--base-url" in completed.stdout
    assert "Read-only" in completed.stdout


def test_demo_smoke_check_rejects_non_local_base_url_without_network_call():
    script = REPO_ROOT / "scripts" / "demo_smoke_check.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--base-url", "https://example.com"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "localhost" in completed.stderr
