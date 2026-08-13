"""Static architecture guards for evidence-only source ingress (R2.0A-4C).

These assert the bounded contract in source: the wave adds ONE production service
plus ONE bridge write command, adds no migration and no API route, never reaches
Knowledge, never fetches from the network, and leaves the Knowledge upload path
byte-for-byte unchanged.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAS = ROOT / "mas"
BRIDGE = MAS / "tools/research_evidence_bridge.py"
SOURCE_SERVICE = MAS / "knowledge/evidence_snapshot/source_service.py"
SOURCE_STORAGE = MAS / "knowledge/evidence_snapshot/source_storage.py"
FILES = MAS / "knowledge/files.py"
CAPTURE = MAS / "knowledge/evidence_snapshot/capture.py"
API = MAS / "api.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """Source with comment lines stripped (docstrings remain)."""
    return "\n".join(
        line for line in _text(path).splitlines()
        if not line.lstrip().startswith("#")
    )


# ─────────────────────── no migration / no new schema ───────────────────────


def test_no_new_sql_migration_was_added():
    sql_dir = MAS / "sql"
    # Discovery concluded the existing v47 schema already represents evidence-only
    # capture, so this wave adds no migration at all (v62 stays unused).
    assert not list(sql_dir.glob("v62_*.sql"))
    # The manifest below is the whole v6* series, not this wave's contribution.
    # v63 belongs to the R2 provider-attempt telemetry wave and is unrelated to
    # evidence-only source ingress; the assertion that matters for R2.0A-4C is
    # the v62 one above, plus test_no_ddl_anywhere_in_the_new_modules below.
    assert sorted(p.name for p in sql_dir.glob("v6*.sql")) == [
        "v60_research_evidence_automation_roi_execution.sql",
        "v61_research_evidence_pack_foundation.sql",
        "v63_provider_attempt_telemetry_foundation.sql",
        "v64_decision_state_coherence_foundation.sql",
    ]


def test_no_ddl_anywhere_in_the_new_modules():
    for path in (SOURCE_SERVICE, SOURCE_STORAGE):
        text = _text(path)
        for ddl in (
            "CREATE TABLE", "ALTER TABLE", "CREATE INDEX", "ADD CONSTRAINT",
            "CREATE SCHEMA", "CREATE TRIGGER", "CREATE FUNCTION",
        ):
            assert ddl not in text, (path.name, ddl)


# ─────────────────────── no API route ───────────────────────


def _route_decorators(path: Path) -> list[str]:
    tree = ast.parse(_text(path))
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr in (
                "get", "post", "put", "patch", "delete"
            ):
                arguments = decorator.args if isinstance(decorator, ast.Call) else []
                literal = (
                    arguments[0].value
                    if arguments and isinstance(arguments[0], ast.Constant)
                    else ""
                )
                routes.append(f"{target.attr}:{literal}")
    return routes


def test_no_api_route_was_added_for_evidence_source_capture():
    routes = _route_decorators(API)
    for route in routes:
        assert "source-capture" not in route, route
        assert "source_capture" not in route, route
        assert "evidence-source" not in route, route
    text = _text(API)
    for symbol in (
        "capture_evidence_source_bytes", "source_service", "source_storage",
        "evidence_source_storage_dir",
    ):
        assert symbol not in text, symbol


# ─────────────────────── one bounded service boundary ───────────────────────


def test_service_is_gated_caller_owned_and_never_commits():
    text = _text(SOURCE_SERVICE)
    # Feature gated on the v47 snapshot flag (it writes v47 rows).
    assert "config.evidence_snapshot_enabled()" in text
    assert "def _require_enabled(" in text
    # Caller owns the transaction: autocommit rejected, READ COMMITTED explicitly
    # pinned AND live-verified, work wrapped in a savepoint, never committed.
    assert "def _require_caller_owned_read_committed(" in text
    assert "def _verify_live_read_committed(" in text
    assert "SHOW transaction_isolation" in text
    assert "isolation is None" in text
    assert "READ_COMMITTED" in text
    assert "SAVEPOINT evidence_source_capture" in text
    assert ".commit(" not in _code(SOURCE_SERVICE)


def test_service_creates_only_v47_capture_records():
    """Blob + Snapshot + IngestOperation, and nothing else."""
    names = set()
    for node in ast.walk(ast.parse(_text(SOURCE_SERVICE))):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    assert {
        "create_or_get_ingest_operation", "insert_or_get_blob",
        "insert_snapshot", "set_ingest_status",
    } <= names
    # No fact, claim, retention, authorization, or intake write.
    for forbidden in (
        "insert_fact", "insert_retention_event", "create_claim_draft",
        "create_intake", "create_intake_item",
        "record_usage_authorization_decision", "create_source_metadata_revision",
    ):
        assert forbidden not in names, forbidden


def test_service_orders_storage_before_the_database():
    """A committed snapshot can never reference bytes that were never persisted."""
    body = _text(SOURCE_SERVICE).split("def capture_evidence_source_bytes(")[1]
    assert body.index("persist_source_bytes(") < body.index("_capture_write(conn)")
    assert body.index("persist_source_bytes(") < body.index("insert_snapshot(")
    # …and the durability of the reference is asserted, not assumed.
    assert "stored_bytes_present(" in body


def test_service_rejects_everything_semantic_before_any_sql():
    body = _text(SOURCE_SERVICE).split("def capture_evidence_source_bytes(")[1]
    first_statement = body.index("_verify_live_read_committed(conn)")
    for guard in (
        "_require_enabled()",
        "_require_caller_owned_read_committed(conn)",
        # A malformed project id must never reach the projects.id uuid
        # comparison, which would abort the caller's transaction.
        "_require_project_uuid(project_id)",
        "_validate_source_kind(source_kind)",
        "namespaced_operation_id(operation_id)",
        "if not content:",
        "expected != content_sha256",
    ):
        assert body.index(guard) < first_statement, guard
    # Storage happens after the live-isolation check too, so a rejected request
    # never leaves bytes behind.
    assert first_statement < body.index("persist_source_bytes(")


def test_project_id_shape_is_validated_by_the_storage_layer_definition():
    """One project-id shape, defined once, used by both layers."""
    service = _text(SOURCE_SERVICE)
    storage = _text(SOURCE_STORAGE)
    assert "def _require_project_uuid(" in service
    assert "source_storage.is_canonical_uuid(" in service
    assert "def is_canonical_uuid(" in storage
    # The store's own reference builder uses the same predicate, so the two can
    # never diverge into contradictory parsers.
    assert "if not is_canonical_uuid(project_id):" in storage
    # No second UUID parser was introduced in the service.
    for competing in ("import uuid", "from uuid import", "re.compile"):
        assert competing not in service, competing


def test_provenance_vocabulary_is_closed_and_refuses_reserved_kinds():
    import knowledge.evidence_snapshot.source_service as service

    assert service.EVIDENCE_SOURCE_KINDS == frozenset({
        "operator_curated_research_evidence_record",
        "operator_supplied_document",
    })
    assert service.RESERVED_SOURCE_KINDS == frozenset({
        "uploaded_file", "raw_web_capture",
    })
    assert not (service.EVIDENCE_SOURCE_KINDS & service.RESERVED_SOURCE_KINDS)
    # The upload path's kind is exactly the one this ingress refuses to claim.
    assert "uploaded_file" in service.RESERVED_SOURCE_KINDS
    assert '"uploaded_file"' in _text(CAPTURE)


def test_storage_is_a_separate_immutable_content_addressed_namespace():
    text = _text(SOURCE_STORAGE)
    # Explicitly configurable root, absolute-only, traversal-proofed.
    assert "config.evidence_source_storage_dir()" in text
    assert "must be an absolute server-side path" in text
    assert "_UUID_PATTERN" in text and "_SHA256_PATTERN" in text
    assert "resolved_target.startswith(resolved_root + os.sep)" in text
    # Atomic + verified + immutable.
    assert "os.replace(" in text
    assert "os.fsync(" in text
    assert "def _digest_file(" in text
    assert "EvidenceSourceImmutabilityError" in text
    assert "overwrite an immutable evidence reference" in text
    # It is NOT the Knowledge upload store. Checked against referenced names, so
    # the module may explain in prose why it avoids that root without tripping.
    referenced = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            referenced.add(node.value)
    assert "UPLOAD_LAYER" not in referenced
    assert "storage_dir" not in referenced
    assert not any("upload_store" in name for name in referenced)


def test_stored_artifacts_are_restricted_to_owner_only_access():
    text = _text(SOURCE_STORAGE)
    assert "_STORED_FILE_MODE = 0o600" in text
    assert "def _restrict_stored_file_mode(" in text
    # Applied on the accept-an-existing-artifact path, before the reference is
    # returned; a fresh write is already 0600 by mkstemp construction.
    accept_path = text.split("# Already present: verify rather than rewrite.")[1]
    assert accept_path.index("_restrict_stored_file_mode(target)") < accept_path.index(
        "return str(target)"
    )


def test_no_garbage_collection_subsystem_was_added():
    """Orphan identification is a documented operator procedure, not code."""
    storage = _text(SOURCE_STORAGE)
    service = _text(SOURCE_SERVICE)
    for destructive in (
        "os.unlink(str(target", "os.remove(", "shutil.rmtree", "rmdir(",
        "def collect_garbage", "def purge", "def prune", "def cleanup_orphans",
    ):
        assert destructive not in storage, destructive
        assert destructive not in service, destructive
    # The only unlink is the temporary-file cleanup on a failed atomic write.
    assert storage.count("os.unlink(") == 1
    assert "os.unlink(temp_name)" in storage


def test_orphan_and_dry_run_storage_semantics_are_documented():
    doc = _text(ROOT / "docs/v4.4-R2.0A-4C-EVIDENCE-ONLY-SOURCE-SNAPSHOT-INGRESS.md")
    # A bounded orphan rule keyed on snapshot references, and the prohibition.
    assert "storage_ref" in doc
    assert "must not delete" in doc.lower() or "never delete" in doc.lower()
    assert "orphan" in doc.lower()
    # The dry-run storage side effect is stated, not implied.
    assert "source_bytes_persisted" in doc
    assert "dry" in doc.lower()


# ─────────────────────── bridge command contract ───────────────────────


def test_bridge_exposes_exactly_one_new_write_command_via_the_service():
    text = _text(BRIDGE)
    assert "def cmd_source_capture(" in text
    assert "from knowledge.evidence_snapshot.source_service import" in text
    assert "capture_evidence_source_bytes(" in text
    assert 'sub.add_parser(\n        "source-capture"' in text
    # It uses the shared write runner (gates + preflight + dry-run default) and
    # requires the v47 snapshot flag as well as the Research Evidence flag.
    assert '_run_write(\n        "source-capture", args, stream, build, ' \
           "requires_snapshot_flag=True\n    )" in text


def test_bridge_capture_never_fetches_and_takes_no_url():
    text = _text(BRIDGE)
    body = text.split("def cmd_source_capture(")[1].split("\ndef ", 1)[0]
    reader = text.split("def _read_local_source_bytes(")[1].split("\ndef ", 1)[0]
    for forbidden in (
        "requests", "urllib", "httpx", "urlopen", "http.client", "socket",
        "--url", "--uri", "download",
    ):
        assert forbidden not in body, forbidden
        assert forbidden not in reader, forbidden
    # A pasted URL is refused explicitly rather than treated as a filename.
    assert "_REFUSED_FILE_SCHEMES" in text
    assert "this command never " in text
    assert "http://" in text and "https://" in text


def test_bridge_binds_the_intended_artifact_before_any_write():
    text = _text(BRIDGE)
    body = text.split("def cmd_source_capture(")[1].split("\ndef ", 1)[0]
    assert "--expected-sha256" in text
    # The digest comparison happens in the command body, before `build` (which is
    # the only thing the write runner executes on a connection).
    assert body.index("does not match the digest of --file") < body.index("def build(")
    assert body.index("_read_local_source_bytes(file_path)") < body.index("def build(")


def test_bridge_capture_payload_is_metadata_safe():
    text = _text(BRIDGE)
    body = text.split("def cmd_source_capture(")[1].split("\ndef ", 1)[0]
    # Never the private storage reference, the operator's local path, or content.
    payload = body.split("return {")[1].split("}")[0]
    emitted_keys = {
        segment.split('"')[1]
        for segment in payload.split("\n")
        if segment.strip().startswith('"')
    }
    for leaked in (
        "storage_ref", "file", "file_path", "content", "source_bytes",
        # The RAW capture locator stays private, matching the tool's existing
        # posture; only a boolean confirms it was recorded.
        "source_locator",
    ):
        assert leaked not in emitted_keys, leaked
    for leaked in ("storage_ref", "file_path", '"--file"'):
        assert leaked not in payload, leaked
    # The safe identities ARE reported.
    for field in (
        "source_snapshot_id", "source_blob_id", "capture_operation_id",
        "content_sha256", "byte_size", "source_kind", "source_locator_recorded",
        "capture_reused", "source_bytes_persisted",
    ):
        assert f'"{field}"' in payload, field


def test_capture_help_discloses_the_dry_run_storage_side_effect():
    """`--commit` governs database state; the bytes are not rolled back with it.

    The design is intentional (a dry run that "passed" while storage would have
    failed at commit time would be a lie), so the contract has to be unmistakable
    in the command's own help rather than only in a design document.
    """
    import tools.research_evidence_bridge as bridge

    parser = bridge.build_parser()
    capture = parser._subparsers._group_actions[0].choices["source-capture"]
    help_text = capture.format_help()
    lowered = help_text.lower()

    # It names the scope of the rollback...
    assert "database state only" in lowered
    # ...and that the verified immutable bytes may remain in the store.
    assert "may remain" in lowered
    assert "evidence source store" in lowered
    for token in ("dry run", "immutable", "source_bytes_persisted"):
        assert token in lowered, token

    # The shared `--commit` help for every other write command is untouched.
    assert "persist the write (default: dry-run / rollback)" in help_text
    for command in ("fact-create", "claim-create", "authorize-internal-analysis"):
        other = parser._subparsers._group_actions[0].choices[command].format_help()
        assert "persist the write (default: dry-run / rollback)" in other
        assert "may remain" not in other.lower()


def test_capture_payload_states_the_rollback_scope():
    text = _text(BRIDGE)
    body = text.split("def cmd_source_capture(")[1].split("\ndef ", 1)[0]
    payload = body.split("return {")[1].split("}")[0]
    assert '"source_bytes_persisted": result["bytes_persisted"]' in payload
    assert '"source_bytes_retained_on_rollback": True' in payload


def test_malformed_max_bytes_configuration_fails_closed_in_the_bridge():
    text = _text(BRIDGE)
    reader = text.split("def _read_local_source_bytes(")[1].split("\ndef ", 1)[0]
    # The bridge surfaces the bounded configuration message instead of silently
    # accepting a fallback bound.
    assert "config.EvidenceSourceConfigurationError" in reader
    config_text = _text(MAS / "config.py")
    assert "class EvidenceSourceConfigurationError(" in config_text
    assert "refusing to fall back to the default size bound" in config_text


def test_capture_operation_uniqueness_is_a_frozen_catalog_fact():
    import tools.research_evidence_bridge as bridge

    assert bridge.CATALOG_CAPTURE_CONSTRAINTS == (
        ("ingest_operation", "uq_ingest_operation_project_op",
         ("project_id", "operation_id")),
    )
    # Folded into the SAME constraints_ready verdict the other load-bearing
    # uniqueness checks feed, so a drifted constraint blocks the write.
    text = _text(BRIDGE)
    assert "CATALOG_CONSTRAINTS + CATALOG_CAPTURE_CONSTRAINTS" in text
    assert '"constraints_ready": not bad_constraints' in text


def test_capture_constraint_matches_the_ratified_v47_migration():
    import re

    import tools.research_evidence_bridge as bridge

    sql = _text(MAS / "sql/v47_evidence_snapshot_foundation.sql")
    for _relation, conname, columns in bridge.CATALOG_CAPTURE_CONSTRAINTS:
        match = re.search(
            r"CONSTRAINT\s+" + conname + r"\s+UNIQUE\s*\(([^)]*)\)", sql
        )
        assert match is not None, conname
        declared = tuple(
            part.strip() for part in match.group(1).split(",") if part.strip()
        )
        assert declared == columns, (conname, declared, columns)


def test_bridge_source_kind_choices_come_from_the_service():
    """The CLI vocabulary is read from the service, never duplicated as literals."""
    text = _text(BRIDGE)
    assert "def _evidence_source_kind_choices(" in text
    assert "choices=_evidence_source_kind_choices()" in text
    import tools.research_evidence_bridge as bridge
    from knowledge.evidence_snapshot.source_service import (
        EVIDENCE_SOURCE_KINDS,
        RESERVED_SOURCE_KINDS,
    )

    assert bridge._evidence_source_kind_choices() == tuple(
        sorted(EVIDENCE_SOURCE_KINDS)
    )
    # The refused kinds named in the CLI help are read from the service too, so
    # neither list can drift into offering something the service rejects.
    assert bridge._refused_evidence_source_kinds() == tuple(
        sorted(RESERVED_SOURCE_KINDS)
    )
    # The parser really accepts every service-supported kind and really rejects
    # every reserved one (argparse `choices`), so the CLI cannot offer a kind the
    # service would refuse — nor withhold one it accepts.
    import pytest

    def parse(kind):
        return bridge.build_parser().parse_args([
            "source-capture", "--project-id", "p", "--file", "f",
            "--source-kind", kind, "--source-locator", "l", "--actor", "a",
            "--operation-id", "o",
        ])

    for kind in sorted(EVIDENCE_SOURCE_KINDS):
        assert parse(kind).source_kind == kind
    for kind in sorted(RESERVED_SOURCE_KINDS):
        with pytest.raises(SystemExit):
            parse(kind)


# ─────────────────────── Knowledge path unchanged ───────────────────────


def test_knowledge_upload_path_is_untouched_by_this_wave():
    """`knowledge/files.py` still owns Knowledge ingestion, unchanged.

    The evidence-only ingress is additive: it does not call, wrap, reuse, or
    modify the Knowledge upload flow, and the upload flow does not know it exists.
    """
    text = _text(FILES)
    for symbol in (
        "capture_evidence_source_bytes", "source_service", "source_storage",
        "evidence_source_storage_dir", "EVIDENCE_SOURCE_KINDS",
    ):
        assert symbol not in text, symbol
    # The upload path still performs its own Knowledge work and its own Slice A
    # capture through the unchanged upload seam.
    assert "_evidence_capture.capture_upload(" in text
    assert 'source_kind="uploaded_file"' in text
    assert "_upsert_file_manifest(state, manifest)" in text


def test_upload_capture_seam_is_unchanged_by_this_wave():
    text = _text(CAPTURE)
    for symbol in ("source_storage", "source_service", "EVIDENCE_SOURCE_KINDS"):
        assert symbol not in text, symbol
    # Its own operation-id namespace is intact and distinct.
    assert 'return f"upload:{digest}"' in text
