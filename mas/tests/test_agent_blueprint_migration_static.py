"""Agent Blueprint Studio S1 — static safety checks on the v50 migration (no DB).

These assert the migration's Wave-1 safety properties by inspecting the SQL text,
so they run without a PostgreSQL instance.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _strip_sql_comments(text: str) -> str:
    """Drop ``--`` line comments so assertions check real DDL, not explanatory prose."""
    out = []
    for line in text.splitlines():
        idx = line.find("--")
        out.append(line if idx == -1 else line[:idx])
    return "\n".join(out)


_V50_RAW = (ROOT / "sql" / "v50_agent_blueprint_studio_foundation.sql").read_text(encoding="utf-8")
V50 = _strip_sql_comments(_V50_RAW)

STUDIO_TABLES = (
    "blueprint_project",
    "blueprint_config_revision",
    "blueprint_source_item",
    "blueprint_source_extract",
    "blueprint_artifact",
    "blueprint_artifact_input_binding",
    "blueprint_lint_result",
    "blueprint_lint_finding",
    "blueprint_eval_case",
    "blueprint_eval_run",
    "blueprint_draft_export",
)


def test_single_transaction_boundary():
    assert V50.count("BEGIN;") == 1
    assert V50.count("COMMIT;") == 1


def test_no_append_only_triggers_in_wave_1():
    assert "CREATE TRIGGER" not in V50
    assert "CREATE OR REPLACE FUNCTION" not in V50


def test_additive_only_no_alter_or_drop_of_existing():
    assert "ALTER TABLE" not in V50
    assert "DROP TABLE" not in V50
    assert "DROP SCHEMA" not in V50
    assert "CREATE INDEX CONCURRENTLY" not in V50


def test_does_not_touch_decision_engine_state():
    assert "state_snapshots" not in V50
    assert "ProjectState" not in V50


def test_all_studio_tables_created_idempotently():
    # Every Studio table is created with IF NOT EXISTS (re-apply no-op).
    assert V50.count("CREATE TABLE ") == V50.count("CREATE TABLE IF NOT EXISTS ")
    for table in STUDIO_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in V50, table
    assert V50.count("CREATE TABLE IF NOT EXISTS ") == len(STUDIO_TABLES)


def test_preflight_dependency_and_divergence_messages_present():
    assert "requires the base schema" in V50
    assert "partial/divergent" in V50


def test_default_deny_rights_columns_present():
    for col in ("use_allowed", "quote_allowed", "export_allowed", "external_processing_allowed"):
        assert col in V50
    # Booleans default FALSE; categorical rights default to the most restrictive value.
    assert "BOOLEAN NOT NULL DEFAULT FALSE" in V50
    assert "DEFAULT 'operator_only'" in V50
    assert "DEFAULT 'restricted'" in V50
    assert "DEFAULT 'undeclared_restricted'" in V50


def test_many_row_binding_table_present():
    assert "CREATE TABLE IF NOT EXISTS blueprint_artifact_input_binding (" in V50
    assert "uq_baib_artifact_extract" in V50
    assert "fk_baib_artifact_project" in V50
    assert "fk_baib_extract_project" in V50


def test_draft_only_assurance_and_curation_semantics():
    assert "ck_ba_assurance_draft_only" in V50
    assert "assurance_status = 'draft_unvalidated'" in V50
    # operator-declared curation only (no authenticated/verified review wording)
    assert "curated_by" in V50
    assert "reviewed_by" not in V50
    assert "review_status" not in V50


def test_optional_non_coupling_link_to_projects():
    assert "linked_project_id" in V50
    assert "REFERENCES projects(id) ON DELETE SET NULL" in V50
