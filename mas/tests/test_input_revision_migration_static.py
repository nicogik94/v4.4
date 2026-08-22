"""Static contract checks for the additive W8.2 migration."""
from pathlib import Path
import re


MAS = Path(__file__).resolve().parents[1]
ROOT = MAS.parent
MIGRATION = MAS / "sql" / "v65_governed_input_revision_lifecycle.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_v65_is_the_next_unused_migration_number():
    numbered = sorted(
        int(match.group(1))
        for path in (MAS / "sql").glob("v*.sql")
        if (match := re.match(r"v(\d+)_", path.name))
    )
    assert numbered[-3:] == [63, 64, 65]


def test_migration_is_transactional_provider_free_and_does_not_rewrite_w8_1():
    sql = _sql().lower()
    assert sql.count("begin;") == 1
    assert sql.count("commit;") == 1
    assert "alter table decision_input_snapshots" not in sql
    assert "alter table analysis_generations" not in sql
    assert "anthropic" not in sql
    assert "openai" not in sql


def test_schema_guards_scope_lineage_and_terminal_lifecycle():
    sql = _sql()
    assert "fk_ir_expected_base_same_scope" in sql
    assert "fk_ir_result_same_scope" in sql
    assert "trg_ir_lifecycle_guard" in sql
    assert "terminal input revision lifecycle is immutable" in sql
    assert "applied revision requires exact resulting snapshot lineage" in sql
    assert "snapshot_cause IS DISTINCT FROM OLD.id::text" in sql


def test_existing_patch_domain_is_the_only_json_patch_domain():
    sql = _sql()
    for field in (
        "project_name",
        "brief",
        "data",
        "output_language",
        "report_mode",
        "observations",
        "timer_logs",
    ):
        assert f"'{field}'" in sql
    assert "'risk_classification'" not in sql
    assert "'clarification_answers'" not in sql
    assert "'knowledge_layer'" not in sql
    assert "'imported_evidence'" not in sql


def test_fresh_docker_and_documented_setup_apply_v65():
    migration_name = MIGRATION.name
    compose = (MAS / "docker-compose.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert migration_name in compose
    assert f"psql $DATABASE_URL -f sql/{migration_name}" in readme
