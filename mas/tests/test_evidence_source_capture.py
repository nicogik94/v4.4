"""Unit coverage for evidence-only v47 source ingress (R2.0A-4C).

No database. These prove:

* the immutable content-addressed store's guarantees (determinism, traversal
  safety, atomic + digest-verified write, refusal to overwrite different bytes at
  an existing reference);
* the capture service's feature gate, caller-transaction-ownership guard, byte
  and provenance validation, and ``expected_sha256`` binding — all of which reject
  BEFORE any SQL and before any byte is stored;
* the provenance vocabulary really refuses the kinds this ingress cannot attest
  to (``uploaded_file``, ``raw_web_capture``).

Repository binding, idempotency, rollback, Knowledge non-mutation, and the bridge
command live in ``test_evidence_source_capture_pg.py``.
"""
import hashlib
import os
import stat
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from knowledge.evidence_snapshot import source_service, source_storage  # noqa: E402

RC = psycopg.IsolationLevel.READ_COMMITTED
PROJECT = "11111111-2222-3333-4444-555555555555"
CONTENT = b"operator-curated research evidence record\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class FakeConn:
    """A connection whose every ``execute`` fails — proves no SQL runs.

    Any rejection that reached ``execute`` would raise AssertionError, so a
    passing gating test proves the rejection happened before any SQL.
    """

    def __init__(self, *, autocommit=False, isolation_level=RC):
        self.autocommit = autocommit
        self.isolation_level = isolation_level
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append(str(query))
        raise AssertionError("no SQL should run in these gating tests")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the evidence-only store at a disposable root."""
    root = tmp_path / "evidence_source_store"
    monkeypatch.setenv(config.EVIDENCE_SOURCE_STORAGE_DIR_ENV, str(root))
    return root


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")


def _capture(conn, **overrides):
    kwargs = {
        "project_id": PROJECT,
        "content": CONTENT,
        "source_kind": source_service.OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD,
        "source_locator": "operator-curated:D-TEST/record-1",
        "operation_id": "op-1",
        "actor": "operator",
    }
    kwargs.update(overrides)
    return source_service.capture_evidence_source_bytes(conn, **kwargs)


# ═══════════════════════ configuration ═══════════════════════


def test_store_root_is_configurable_and_distinct_from_knowledge_uploads(monkeypatch):
    monkeypatch.delenv(config.EVIDENCE_SOURCE_STORAGE_DIR_ENV, raising=False)
    default_root = config.evidence_source_storage_dir()
    assert default_root
    # The evidence-only store is never the Knowledge upload store.
    assert Path(default_root) != Path(config.UPLOAD_LAYER.storage_dir)
    assert "upload_store" not in Path(default_root).name

    monkeypatch.setenv(config.EVIDENCE_SOURCE_STORAGE_DIR_ENV, "/srv/evidence")
    assert config.evidence_source_storage_dir() == "/srv/evidence"


def test_max_bytes_is_configurable_with_a_documented_default(monkeypatch):
    monkeypatch.delenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, raising=False)
    assert config.evidence_source_max_bytes() == config.EVIDENCE_SOURCE_DEFAULT_MAX_BYTES
    # An explicitly empty variable is still "use the documented default".
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, "   ")
    assert config.evidence_source_max_bytes() == config.EVIDENCE_SOURCE_DEFAULT_MAX_BYTES
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, "128")
    assert config.evidence_source_max_bytes() == 128
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, " 128 ")
    assert config.evidence_source_max_bytes() == 128


@pytest.mark.parametrize(
    "malformed",
    ("not-a-number", "5MB", "1.5", "5_000_000.0", "1e6", "0x10", "", "  x  "),
)
def test_malformed_max_bytes_fails_closed_instead_of_defaulting(
    monkeypatch, malformed
):
    """A malformed bound must never silently become the larger 5 MB default."""
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, malformed)
    if not malformed.strip():
        # Blank is the documented "unset" case, not a malformed value.
        assert (
            config.evidence_source_max_bytes()
            == config.EVIDENCE_SOURCE_DEFAULT_MAX_BYTES
        )
        return
    with pytest.raises(config.EvidenceSourceConfigurationError) as excinfo:
        config.evidence_source_max_bytes()
    assert config.EVIDENCE_SOURCE_MAX_BYTES_ENV in str(excinfo.value)
    assert str(config.EVIDENCE_SOURCE_DEFAULT_MAX_BYTES) not in str(excinfo.value)


def test_malformed_max_bytes_blocks_capture_before_any_sql(
    store, enabled, monkeypatch
):
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, "five-megabytes")
    conn = FakeConn()
    with pytest.raises(config.EvidenceSourceConfigurationError):
        _capture(conn)
    assert conn.executed == []
    assert not store.exists()


@pytest.mark.parametrize("configured,expected", (("0", 1), ("-1", 1), ("1", 1)))
def test_max_bytes_lower_bound_is_preserved(monkeypatch, configured, expected):
    """The intentional lower clamp is unchanged: it never disables capture."""
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, configured)
    assert config.evidence_source_max_bytes() == expected
    assert config.EVIDENCE_SOURCE_MIN_MAX_BYTES == 1


# ═══════════════════════ storage guarantees ═══════════════════════


def test_reference_is_deterministic_and_content_addressed(store):
    first = source_storage.storage_reference(PROJECT, DIGEST)
    second = source_storage.storage_reference(PROJECT, DIGEST)
    assert first == second
    assert first.startswith(str(store))
    assert DIGEST in first
    # Different bytes ⇒ a different reference.
    other = hashlib.sha256(b"different").hexdigest()
    assert source_storage.storage_reference(PROJECT, other) != first


def test_reference_rejects_traversal_and_malformed_components(store):
    for bad_project in ("../../etc", "..", "a/b", PROJECT + "/..", "", "not-a-uuid"):
        with pytest.raises(source_storage.EvidenceSourceStorageError):
            source_storage.storage_reference(bad_project, DIGEST)
    for bad_digest in ("../secret", DIGEST[:-1], DIGEST.upper(), "", "z" * 64):
        with pytest.raises(source_storage.EvidenceSourceStorageError):
            source_storage.storage_reference(PROJECT, bad_digest)


def test_relative_or_empty_root_is_refused(monkeypatch):
    monkeypatch.setenv(config.EVIDENCE_SOURCE_STORAGE_DIR_ENV, "relative/store")
    with pytest.raises(source_storage.EvidenceSourceStorageError):
        source_storage.storage_reference(PROJECT, DIGEST)


def test_persist_writes_verifies_and_is_idempotent(store):
    ref = source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    assert Path(ref).is_file()
    assert Path(ref).read_bytes() == CONTENT
    assert source_storage.stored_bytes_present(ref, expected_sha256=DIGEST)
    # No leftover temporary artifacts.
    assert [p.name for p in Path(ref).parent.iterdir()] == [DIGEST]
    # Re-persisting the same bytes verifies rather than rewrites.
    again = source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    assert again == ref


def test_persisted_bytes_are_owner_only(store):
    ref = source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    assert stat.S_IMODE(Path(ref).stat().st_mode) == 0o600


def test_accepting_an_existing_matching_file_restricts_it_to_0600(store):
    ref = source_storage.storage_reference(PROJECT, DIGEST)
    # A pre-existing artifact with exactly the expected bytes but a permissive
    # mode (e.g. written by an earlier tool or restored from a backup).
    Path(ref).parent.mkdir(parents=True, exist_ok=True)
    Path(ref).write_bytes(CONTENT)
    os.chmod(ref, 0o644)
    assert stat.S_IMODE(Path(ref).stat().st_mode) == 0o644

    accepted = source_storage.persist_source_bytes(
        project_id=PROJECT, content=CONTENT
    )

    assert accepted == ref
    assert stat.S_IMODE(Path(ref).stat().st_mode) == 0o600
    # Acceptance must not have altered the content.
    assert Path(ref).read_bytes() == CONTENT
    assert source_storage.stored_bytes_present(ref, expected_sha256=DIGEST)


def test_failure_to_restrict_an_existing_file_is_a_bounded_storage_error(
    store, monkeypatch
):
    ref = source_storage.storage_reference(PROJECT, DIGEST)
    Path(ref).parent.mkdir(parents=True, exist_ok=True)
    Path(ref).write_bytes(CONTENT)
    os.chmod(ref, 0o644)

    def refuse(*args, **kwargs):
        raise OSError("operation not permitted")

    monkeypatch.setattr(source_storage.os, "chmod", refuse)
    with pytest.raises(source_storage.EvidenceSourceStorageError) as excinfo:
        source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    assert CONTENT.decode() not in str(excinfo.value)
    # Content untouched by the failed acceptance.
    assert Path(ref).read_bytes() == CONTENT


def test_persist_refuses_to_overwrite_different_bytes_at_a_reference(store):
    ref = source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    # Simulate a corrupted/tampered store: the reference now holds other bytes.
    Path(ref).write_bytes(b"tampered")
    with pytest.raises(source_storage.EvidenceSourceImmutabilityError):
        source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    # The existing content was NOT replaced.
    assert Path(ref).read_bytes() == b"tampered"


def test_persist_rejects_empty_bytes_and_digest_mismatch(store):
    with pytest.raises(source_storage.EvidenceSourceStorageError):
        source_storage.persist_source_bytes(project_id=PROJECT, content=b"")
    with pytest.raises(source_storage.EvidenceSourceStorageError):
        source_storage.persist_source_bytes(
            project_id=PROJECT, content=CONTENT, content_sha256="0" * 64
        )


def test_stored_bytes_present_is_false_for_absent_or_wrong_bytes(store):
    ref = source_storage.storage_reference(PROJECT, DIGEST)
    assert source_storage.stored_bytes_present(ref, expected_sha256=DIGEST) is False
    Path(ref).parent.mkdir(parents=True, exist_ok=True)
    Path(ref).write_bytes(b"wrong")
    assert source_storage.stored_bytes_present(ref, expected_sha256=DIGEST) is False


def test_storage_failure_surfaces_as_a_bounded_error(store, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("device full")

    monkeypatch.setattr(source_storage.tempfile, "mkstemp", boom)
    with pytest.raises(source_storage.EvidenceSourceStorageError) as excinfo:
        source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    # The bounded message never echoes the source bytes.
    assert CONTENT.decode() not in str(excinfo.value)


def test_storage_errors_never_echo_source_content(store):
    secret = b"CONFIDENTIAL-EVIDENCE-PAYLOAD"
    with pytest.raises(source_storage.EvidenceSourceStorageError) as excinfo:
        source_storage.persist_source_bytes(
            project_id=PROJECT, content=secret, content_sha256="1" * 64
        )
    assert "CONFIDENTIAL" not in str(excinfo.value)


# ═══════════════════════ service gating (no SQL, no bytes stored) ═════════════


def test_disabled_feature_fails_closed(store, monkeypatch):
    monkeypatch.delenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", raising=False)
    with pytest.raises(source_service.EvidenceSourceCaptureDisabled):
        _capture(FakeConn())
    assert not store.exists()


def test_autocommit_connection_is_rejected(store, enabled):
    with pytest.raises(source_service.EvidenceSourceCaptureTransactionError):
        _capture(FakeConn(autocommit=True))
    assert not store.exists()


def test_unpinned_isolation_is_rejected(store, enabled):
    with pytest.raises(source_service.EvidenceSourceCaptureTransactionError):
        _capture(FakeConn(isolation_level=None))
    assert not store.exists()


@pytest.mark.parametrize(
    "level",
    (psycopg.IsolationLevel.REPEATABLE_READ, psycopg.IsolationLevel.SERIALIZABLE),
)
def test_stronger_isolation_is_rejected(store, enabled, level):
    with pytest.raises(source_service.EvidenceSourceCaptureTransactionError):
        _capture(FakeConn(isolation_level=level))
    assert not store.exists()


def test_empty_bytes_are_rejected_before_any_sql(store, enabled):
    conn = FakeConn()
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(conn, content=b"")
    assert conn.executed == []
    assert not store.exists()


def test_oversized_bytes_are_rejected_before_any_sql(store, enabled, monkeypatch):
    monkeypatch.setenv(config.EVIDENCE_SOURCE_MAX_BYTES_ENV, "8")
    conn = FakeConn()
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(conn, content=b"0123456789")
    assert conn.executed == []
    assert not store.exists()


@pytest.mark.parametrize("field", ("source_locator", "operation_id"))
def test_blank_required_metadata_is_rejected_before_any_sql(
    store, enabled, field
):
    conn = FakeConn()
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(conn, **{field: "   "})
    assert conn.executed == []
    assert not store.exists()


@pytest.mark.parametrize(
    "malformed",
    (
        "   ",
        "not-a-uuid",
        # A malformed value that would reach a PostgreSQL uuid comparison and
        # abort the caller's transaction with "invalid input syntax for type uuid".
        "11111111-2222-3333-4444-55555555555",       # one digit short
        "11111111-2222-3333-4444-5555555555555",     # one digit long
        PROJECT + "'; SELECT 1 --",
        PROJECT.replace("-", ""),                     # unhyphenated: not canonical
        "{" + PROJECT + "}",
        "urn:uuid:" + PROJECT,
        "../../etc/passwd",
    ),
)
def test_malformed_project_id_is_rejected_before_any_sql(store, enabled, malformed):
    conn = FakeConn()
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(conn, project_id=malformed)
    assert conn.executed == []
    assert not store.exists()


def test_project_id_validation_matches_the_storage_layer_exactly(store, enabled):
    """No contradictory parser: what the service accepts, the store accepts.

    A shape the service admitted but the store rejected would fail *after* the
    project read and the idempotency probe — the very ordering this wave promises
    not to break.
    """
    accepted = (PROJECT, PROJECT.upper())
    for candidate in accepted:
        assert source_storage.is_canonical_uuid(candidate)
        # Validation is not what stops it: the FakeConn's AssertionError proves
        # the service got past every pre-SQL rejection.
        with pytest.raises(AssertionError):
            _capture(FakeConn(), project_id=candidate)
        assert source_storage.storage_reference(candidate, DIGEST)

    for candidate in ("not-a-uuid", PROJECT.replace("-", ""), "{" + PROJECT + "}"):
        assert not source_storage.is_canonical_uuid(candidate)
        with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
            _capture(FakeConn(), project_id=candidate)
        with pytest.raises(source_storage.EvidenceSourceStorageError):
            source_storage.storage_reference(candidate, DIGEST)


def test_expected_sha_mismatch_is_rejected_before_any_sql_or_storage(store, enabled):
    conn = FakeConn()
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(conn, expected_sha256="0" * 64)
    assert conn.executed == []
    assert not store.exists()


def test_expected_sha_match_passes_validation_and_reaches_sql(store, enabled):
    # A matching digest must NOT be the thing that rejects: the FakeConn's
    # AssertionError proves validation passed and the service reached its first
    # statement (the live-isolation verification).
    with pytest.raises(AssertionError):
        _capture(FakeConn(), expected_sha256=DIGEST.upper())


# ═══════════════════════ truthful provenance ═══════════════════════


def test_reserved_kinds_this_ingress_cannot_attest_to_are_refused(store, enabled):
    assert source_service.RESERVED_SOURCE_KINDS == frozenset(
        {"uploaded_file", "raw_web_capture"}
    )
    for kind in sorted(source_service.RESERVED_SOURCE_KINDS):
        conn = FakeConn()
        with pytest.raises(
            source_service.EvidenceSourceCaptureValidationError
        ) as excinfo:
            _capture(conn, source_kind=kind)
        assert "reserved" in str(excinfo.value)
        assert conn.executed == []
    assert not store.exists()


def test_unknown_kind_is_refused(store, enabled):
    with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
        _capture(FakeConn(), source_kind="totally_made_up")
    assert not store.exists()


def test_supported_kinds_distinguish_curated_records_from_raw_artifacts():
    assert source_service.EVIDENCE_SOURCE_KINDS == frozenset({
        "operator_curated_research_evidence_record",
        "operator_supplied_document",
    })
    # The curated-record kind is available, so a normalized operator-authored
    # record never has to claim to be a raw original webpage or an upload.
    assert (
        source_service.OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD
        in source_service.EVIDENCE_SOURCE_KINDS
    )
    assert not (
        source_service.EVIDENCE_SOURCE_KINDS
        & source_service.RESERVED_SOURCE_KINDS
    )


def test_operation_ids_are_namespaced_away_from_the_upload_path():
    assert source_service.OPERATION_ID_PREFIX == "evidence-source:"
    assert source_service.namespaced_operation_id("op-1") == "evidence-source:op-1"
    # The Knowledge upload seam uses the `upload:` namespace; the two can never
    # collide on one ingest_operation row.
    from knowledge.evidence_snapshot import capture as upload_capture

    assert upload_capture._stable_operation_id("p", "/ref").startswith("upload:")
    assert not source_service.namespaced_operation_id("x").startswith("upload:")


# ═══════════════════════ no Knowledge / no network in the service ═════════════


def _referenced_identifiers(path: Path) -> set[str]:
    """Every identifier the module's *executable code* references.

    Docstrings and comments are excluded by construction (they are not names), so
    a module may legitimately *describe* the Knowledge objects it refuses to
    create while never referencing one. Substring matching over the raw text
    cannot make that distinction; this can.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.update(node.module.split("."))
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names the module imports (absolute and relative)."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                for alias in node.names:
                    modules.add("." * node.level + alias.name)
            elif node.module:
                modules.add(node.module)
    return modules


def test_service_module_references_no_knowledge_or_network_surface():
    path = ROOT / "knowledge/evidence_snapshot/source_service.py"
    names = _referenced_identifiers(path)
    for forbidden in (
        "ProjectState", "imported_evidence", "imported_signals",
        "UploadedFileManifest", "SourceRegistryEntry", "KnowledgeItem",
        "ensure_knowledge_layer", "upsert_source_entry", "parse_upload_bytes",
        "ingest_uploaded_file", "requests", "urllib", "httpx", "llm_client",
        "record_usage_authorization_decision", "create_candidate_fact_revision",
        "insert_fact", "capture_upload",
    ):
        assert forbidden not in names, forbidden
    # It never commits: transaction ownership stays with the caller.
    assert "commit" not in names
    # It does use the canonical v47 append seams.
    for expected in (
        "create_or_get_ingest_operation", "insert_or_get_blob", "insert_snapshot",
        "set_ingest_status",
    ):
        assert expected in names, expected
    # A closed import surface: config, the v47 repository seam, and this wave's
    # storage helper. No Knowledge module, no state module, no HTTP client.
    assert _imported_modules(path) == {
        "__future__", "contextlib", "typing", "config", ".repository",
        ".source_storage",
    }


def test_storage_module_is_filesystem_only():
    names = _referenced_identifiers(
        ROOT / "knowledge/evidence_snapshot/source_storage.py"
    )
    for forbidden in (
        "psycopg", "requests", "urllib", "httpx", "ProjectState", "socket",
        "subprocess",
    ):
        assert forbidden not in names, forbidden
    # Atomic publish + verification are actually present.
    assert {"replace", "fsync", "mkstemp"} <= names


def test_stored_bytes_stay_inside_the_configured_root(store):
    # The store creates only directories and regular files; the resolved artifact
    # path never escapes the resolved configured root.
    ref = source_storage.persist_source_bytes(project_id=PROJECT, content=CONTENT)
    assert os.path.realpath(ref).startswith(os.path.realpath(str(store)))
