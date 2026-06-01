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


def test_demo_docs_exist_in_repo_root_docs():
    for path in REQUIRED_DOCS:
        assert path.exists(), path
        assert MAS_ROOT / "docs" not in path.parents


def test_demo_docs_use_runtime_foundation_and_non_overclaim_language():
    combined = " ".join("\n".join(_read(path) for path in REQUIRED_DOCS).lower().split())

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


def test_ingestion_contract_doc_covers_contract_and_caveats():
    text = " ".join(_read(DOCS_ROOT / "v5-INGESTION-CONTRACT.md").lower().split())

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
