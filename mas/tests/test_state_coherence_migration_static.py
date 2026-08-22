"""Static contract checks for the additive W8.1 migration."""
from pathlib import Path
import re


MAS = Path(__file__).resolve().parents[1]
MIGRATION = MAS / "sql" / "v64_decision_state_coherence_foundation.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_v64_is_the_next_used_migration_number():
    numbered = sorted(
        int(match.group(1))
        for path in (MAS / "sql").glob("v*.sql")
        if (match := re.match(r"v(\d+)_", path.name))
    )
    assert numbered[-3:-1] == [63, 64]


def test_migration_is_additive_transactional_and_provider_independent():
    sql = _sql().lower()
    assert sql.count("begin;") == 1
    assert sql.count("commit;") == 1
    assert "alter table projects" not in sql
    assert "drop table" not in sql
    assert "anthropic" not in sql
    assert "openai" not in sql
    assert sql.count("set search_path from current") == 5


def test_fresh_docker_and_documented_setup_apply_v64():
    compose = (MAS / "docker-compose.yml").read_text(encoding="utf-8")
    readme = (MAS.parent / "README.md").read_text(encoding="utf-8")
    migration_name = "v64_decision_state_coherence_foundation.sql"

    assert migration_name in compose
    assert f"psql $DATABASE_URL -f sql/{migration_name}" in readme


def test_schema_enforces_scope_immutability_and_single_current_binding():
    sql = _sql()
    assert "PRIMARY KEY (project_id, decision_id)" in sql
    assert "fk_ag_snapshot_same_scope" in sql
    assert "fk_ag_expected_base_same_scope" in sql
    assert "trg_dis_immutable" in sql
    assert "trg_ag_immutable" in sql
    assert "trg_cag_guard" in sql


def test_promotion_is_database_atomic_and_expected_base_guarded():
    sql = _sql()
    function = sql[sql.index("CREATE OR REPLACE FUNCTION promote_analysis_generation") :]
    assert "pg_advisory_xact_lock" in function
    assert "expected_base_generation_id IS DISTINCT FROM expected_base_id" in function
    assert "actual_base IS DISTINCT FROM expected_base_id" in function
    assert "ON CONFLICT (project_id, decision_id) DO UPDATE" in function
