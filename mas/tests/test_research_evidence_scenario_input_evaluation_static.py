"""Static boundary tests for the additive R1.7 foundation."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.scenario_input_evaluation_models import (  # noqa: E402
    EVALUATION_POLICY_CANONICAL_JSON,
    EVALUATION_POLICY_FINGERPRINT,
)


SQL_PATH = (
    ROOT
    / "sql"
    / "v58_research_evidence_scenario_input_evaluation_foundation.sql"
)
MODEL_PATH = (
    ROOT / "research_evidence" / "scenario_input_evaluation_models.py"
)
REPOSITORY_PATH = (
    ROOT / "research_evidence" / "scenario_input_evaluation_repository.py"
)
SERVICE_PATH = (
    ROOT / "research_evidence" / "scenario_input_evaluation_service.py"
)
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
MODEL_TEXT = MODEL_PATH.read_text(encoding="utf-8")
REPOSITORY_TEXT = REPOSITORY_PATH.read_text(encoding="utf-8")
SERVICE_TEXT = SERVICE_PATH.read_text(encoding="utf-8")
PACKAGE_TEXT = MODEL_TEXT + REPOSITORY_TEXT + SERVICE_TEXT


def _without_comments(text):
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _function_bodies():
    pattern = re.compile(
        r"CREATE OR REPLACE FUNCTION\s+([a-zA-Z0-9_]+)\s*\("
        r"[\s\S]*?\)\s+RETURNS[\s\S]*?\bAS \$(\w+)\$\n"
        r"([\s\S]*?)\n\$\2\$;",
        re.MULTILINE,
    )
    return {
        match.group(1): match.group(3)
        for match in pattern.finditer(SQL_RAW)
    }


def test_v58_is_one_manual_additive_transaction():
    sql = _without_comments(SQL_RAW)
    assert sql.count("BEGIN;") == 1
    assert sql.count("COMMIT;") == 1
    assert "Apply manually" in SQL_RAW
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 5
    for forbidden in (
        "DROP TABLE",
        "DROP SCHEMA",
        "CREATE VIEW",
        "SET TRANSACTION",
        "LOCK TABLE",
    ):
        assert forbidden not in sql


def test_v58_uses_only_v57_exact_bindings_and_never_latest_resolution():
    assert "research_evidence_consumer_input_binding" in SQL_RAW
    assert "selected_binding_id" in SQL_RAW
    assert "binding_id" in REPOSITORY_TEXT
    for text in (SQL_RAW, REPOSITORY_TEXT, SERVICE_TEXT):
        assert "get_effective_binding" not in text
        assert "get_effective_consumer_input_binding" not in text
        assert "ORDER BY binding_sequence DESC" not in text


def test_policy_json_fingerprint_and_order_are_exact_in_sql_and_python():
    assert EVALUATION_POLICY_CANONICAL_JSON in SQL_RAW
    assert EVALUATION_POLICY_FINGERPRINT in SQL_RAW
    assert hashlib.sha256(
        EVALUATION_POLICY_CANONICAL_JSON.encode("utf-8")
    ).hexdigest() == EVALUATION_POLICY_FINGERPRINT
    parameters = json.loads(EVALUATION_POLICY_CANONICAL_JSON)
    assert parameters["status_precedence"] == [
        "does_not_satisfy",
        "indeterminate",
        "qualified",
        "satisfies",
    ]
    assert parameters["satisfies_nonempty_manifest_reachable"] is False
    for ordinal, reason in enumerate(parameters["reason_order"], 1):
        assert f"('{reason}', {ordinal}," in SQL_RAW


def test_manifest_and_request_fingerprints_are_database_derived():
    assert "sha256(convert_to(NEW.structural_descriptor, 'UTF8'))" in SQL_RAW
    assert "sha256(convert_to(v_descriptor_json::text, 'UTF8'))" in SQL_RAW
    assert "sha256(convert_to(NEW.request_payload_json::text, 'UTF8'))" in SQL_RAW
    assert "COLLATE \"C\"" in SQL_RAW
    assert "scenario-input-manifest-v1" in SQL_RAW
    for prohibited in (
        "caller_manifest_fingerprint",
        "caller_descriptor_fingerprint",
        "caller_request_fingerprint",
    ):
        assert prohibited not in SQL_RAW


def test_all_immutable_tables_use_always_derivation_and_mutation_guards():
    for trigger in (
        "trg_resim_prepare_insert",
        "trg_resim_link_items",
        "trg_resim_no_mutation",
        "trg_resim_complete",
        "trg_resimi_prepare_insert",
        "trg_resimi_no_mutation",
        "trg_resimi_complete",
        "trg_resie_prepare_insert",
        "trg_resie_link_inputs",
        "trg_resie_no_mutation",
        "trg_resie_complete",
        "trg_resiei_prepare_insert",
        "trg_resiei_no_mutation",
        "trg_resiei_complete",
    ):
        assert f"ENABLE ALWAYS TRIGGER {trigger}" in SQL_RAW
    assert SQL_RAW.count("slicea_reject_mutation()") >= 4
    assert "evaluation derived fields are server-owned" in SQL_RAW
    assert "evaluation input derived fields are server-owned" in SQL_RAW


def test_lock_order_is_v57_allocation_rows_before_r17_allocator():
    body = _function_bodies()[
        "research_evidence_prepare_scenario_input_evaluation"
    ]
    v57_lock = body.index(
        "research_evidence_consumer_input_binding_sequence_allocator"
    )
    v57_for_update = body.index("FOR UPDATE OF allocator", v57_lock)
    r17_allocator = body.index(
        "research_evidence_scenario_input_evaluation_sequence_allocator"
    )
    assert v57_lock < v57_for_update < r17_allocator
    assert (
        "ORDER BY allocator.project_id, allocator.consumer_contract,"
        in body
    )
    for forbidden in (
        "pg_trigger_depth",
        "current_setting(",
        "set_config(",
        "session_replication_role",
        "magic",
    ):
        assert forbidden not in body.lower()


def test_uuid_history_uses_ordered_single_row_lookup_not_uuid_aggregation():
    assert not re.search(
        r"\bmax\s*\(\s*(?:[a-z_][a-z0-9_]*\.)?id\s*\)",
        SQL_RAW,
        re.IGNORECASE,
    )
    assert len(re.findall(
        r"ORDER BY latest\.evaluation_sequence DESC,\s+"
        r"latest\.id DESC\s+LIMIT 1",
        SQL_RAW,
    )) == 2
    body = _function_bodies()[
        "research_evidence_prepare_scenario_input_evaluation"
    ]
    assert "min(evaluation_sequence)" in body
    assert "max(evaluation_sequence)" in body
    assert "v_history.last_id IS DISTINCT FROM v_last_id" in body


def test_contract_a_allocator_lifecycle_is_explicit_and_bidirectional():
    assert "Contract A (no preallocation)" in SQL_RAW
    assert "evaluation history has no allocator" in SQL_RAW
    assert "a.last_sequence < 1" in SQL_RAW
    assert "a.last_evaluation_id IS NULL" in SQL_RAW
    assert (
        "ORDER BY latest.evaluation_sequence DESC, latest.id DESC"
        in SQL_RAW
    )


def test_reapply_checks_same_name_definition_and_integrity_drift():
    for sentinel in (
        "divergent column counts",
        "divergent columns",
        "divergent column defaults",
        "missing constraints",
        "divergent constraint backing indexes",
        "divergent indexes",
        "missing/divergent functions",
        "divergent triggers",
        "tables have non-owner ACL privileges",
        "functions have non-owner ACL privileges",
        "divergent table ownership",
        "malformed manifest history",
        "request membership/child policy drift",
        "header policy/aggregate integrity drift",
        "allocator/history divergence",
        "aclexplode(",
        "acl.grantee <> c.relowner",
        "acl.grantee <> p.proowner",
        "md5(regexp_replace(",
        "tgenabled = 'A'",
        "tgqual IS NULL",
        "t.tgoldtable IS NULL",
        "t.tgnewtable IS NULL",
        "t.tgnargs = 0",
        "indpred IS NULL",
        "indnkeyatts",
        "indclass::oid[]",
        "indcollation::oid[]",
        "indoption::smallint[]",
    ):
        assert sentinel in SQL_RAW


def test_v58_acl_boundary_is_owner_admin_only_and_has_no_runtime_role_claim():
    assert "owner/admin-only schema foundation" in SQL_RAW
    assert "No runtime role is granted" in SQL_RAW
    assert "later authorized deployment/DBA wave" in SQL_RAW
    assert "REVOKE ALL ON TABLE" in SQL_RAW
    assert SQL_RAW.count("REVOKE ALL ON FUNCTION") == 11
    assert not re.search(r"\bGRANT\b", _without_comments(SQL_RAW))


def test_no_connection_ownership_or_sqlite_path_exists():
    for forbidden in (
        ".commit(",
        ".rollback(",
        ".close(",
        "sqlite",
        "scenario_shadow",
        "upload_store",
    ):
        assert forbidden not in PACKAGE_TEXT.lower()
    assert "SAVEPOINT" in SERVICE_TEXT
    assert "MAS_RESEARCH_EVIDENCE_ENABLED" in SERVICE_TEXT


def test_no_prohibited_product_or_bayesian_surface_is_implemented():
    parsed = ast.parse(PACKAGE_TEXT)
    identifiers = {
        node.name
        for node in ast.walk(parsed)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    prohibited = {
        "create_observation",
        "calculate_prior",
        "calculate_likelihood",
        "update_posterior",
        "authorize_run",
        "execute_scenario",
        "generate_report",
        "render_citation",
        "export_result",
        "retrieve_evidence",
        "project_product",
    }
    assert identifiers.isdisjoint(prohibited)
    for directory in ("api", "ui", "scenarios", "reports", "retrieval"):
        assert f"/{directory}/" not in "\n".join(
            str(path) for path in (
                MODEL_PATH, REPOSITORY_PATH, SERVICE_PATH, SQL_PATH
            )
        )


def test_function_hash_constants_match_normalized_bodies():
    bodies = _function_bodies()
    expected_pairs = re.findall(
        r"\('([a-z0-9_]+)'(?:\:\:text)?,\s*\d+,\s*"
        r"'([0-9a-f]{32})'(?:\:\:text)?\)",
        SQL_RAW,
    )
    expected = dict(expected_pairs)
    assert set(expected) == set(bodies)
    for name, body in bodies.items():
        actual = hashlib.md5(
            re.sub(r"\s+", "", body).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        assert expected[name] == actual
