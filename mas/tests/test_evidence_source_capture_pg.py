"""Disposable-PostgreSQL integration for evidence-only source ingress (R2.0A-4C).

Proves against a genuine database that an authenticated local operator can create
canonical v47 ``SourceBlob`` + ``SourceSnapshot`` records from operator-supplied
immutable local bytes WITHOUT Knowledge ingestion, and that:

* exactly one ``IngestOperation`` + one ``SourceBlob`` + one ``SourceSnapshot``
  are created, and ZERO Knowledge sources / Knowledge items / uploaded-file
  manifests / ``imported_evidence`` / ``imported_signals`` — no ``ProjectState``
  is required or touched;
* the bytes are durably present at ``storage_ref`` before any committed snapshot,
  and a storage failure commits nothing;
* dry-run (the default) commits no database state, and ``--commit`` persists
  exactly the intended rows;
* the idempotency contract holds exactly: same operation id + same bytes + same
  metadata returns the existing capture; different bytes or different metadata
  fail; a new operation id for the same bytes deduplicates the blob but appends a
  distinct immutable snapshot;
* ``source-list`` sees the result and ``fact-create`` consumes it with no
  Knowledge object in existence for that source;
* retention/availability semantics are unchanged;
* the bridge emits no raw source content and no private storage path;
* the complete canonical Research Evidence chain (source-capture → source-list →
  fact-create → source-metadata → intake → review → claim-support → annotation →
  context → internal_analysis authorization) is reachable from an evidence-only
  local file, on a disposable project only.

Skips unless TEST_EVIDENCE_PG_DSN is set.
"""
import hashlib
import io
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tests.evidence_snapshot_pg as pg  # noqa: E402
import tools.research_evidence_bridge as bridge  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot import source_service, source_storage  # noqa: E402
from research_evidence import UsageScope  # noqa: E402

CURATED = source_service.OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD
DOCUMENT = source_service.OPERATOR_SUPPLIED_DOCUMENT

# Bridge write commands that require the pinned runtime fingerprint.
WRITE_COMMANDS = frozenset({
    "source-capture", "source-metadata-create", "fact-create", "claim-create",
    "intake-create", "intake-item-create", "review-record", "freshness-record",
    "claim-support-record", "annotation-record", "context-record",
    "authorize-internal-analysis", "revoke-internal-analysis",
})

# Knowledge state that evidence-only capture must never create. `state_snapshots`
# is where ProjectState (which owns the Knowledge layer, uploaded-file manifests,
# imported_evidence and imported_signals) is persisted, so its emptiness is the
# ProjectState-non-mutation proof at the database boundary.
KNOWLEDGE_STATE_TABLE = "state_snapshots"


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture
def evidence_store(tmp_path, monkeypatch):
    root = tmp_path / "evidence_source_store"
    monkeypatch.setenv(config.EVIDENCE_SOURCE_STORAGE_DIR_ENV, str(root))
    return root


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", os.environ.get("TEST_EVIDENCE_PG_DSN", ""))


@pytest.fixture
def full_schema():
    """The complete approved runtime topology the bridge's write preflight needs."""
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_through_v60_research_topology(conn, schema)
        pg.apply_v61_research_evidence_pack(conn)
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        try:
            yield conn, schema
        finally:
            pg.drop_schema(conn, "research_evidence_automation_roi")
    conn.close()


@pytest.fixture
def slice_a_schema():
    """Only v47 — enough for direct service tests that never touch the bridge."""
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        yield conn, schema
    conn.close()


def _service_connection(schema):
    """A caller-owned, non-autocommit, explicitly pinned READ COMMITTED session."""
    conn = pg.connect(schema=schema, autocommit=False)
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    return conn


def _artifact(tmp_path, name, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _runtime_fingerprint_for(schema):
    conn = pg.connect(schema=schema, autocommit=True)
    try:
        return bridge._runtime_fingerprint(conn)
    finally:
        conn.close()


def _bridge(monkeypatch, schema, argv, *, recorder=None):
    def factory():
        real = pg.connect(schema=schema, autocommit=True)
        if recorder is not None:
            return _RecordingConn(real, recorder)
        return real

    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    argv = list(argv)
    if argv and argv[0] in WRITE_COMMANDS and "--expect-runtime-fingerprint" not in argv:
        argv += ["--expect-runtime-fingerprint", _runtime_fingerprint_for(schema)]
    out = io.StringIO()
    code = bridge.main(argv, stream=out)
    return code, json.loads(out.getvalue())


class _RecordingConn:
    """Proxy recording commits and write statements."""

    def __init__(self, real, log):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_log", log)
        log["commits"] = 0
        log["writes"] = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def autocommit(self):
        return self._real.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._real.autocommit = value

    @property
    def isolation_level(self):
        return self._real.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self._real.isolation_level = value

    def execute(self, query, params=None):
        text = str(query).strip().upper()
        if text.startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE")
        ):
            self._log["writes"].append(str(query))
        return self._real.execute(query, params)

    def commit(self):
        self._log["commits"] += 1
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()


def _count(conn, table, **filters):
    conn.rollback()  # fresh read snapshot: see other sessions' committed rows
    where = ""
    params: list = []
    if filters:
        where = " WHERE " + " AND ".join(f"{key} = %s" for key in filters)
        params = list(filters.values())
    return conn.execute(
        f"SELECT count(*) FROM {table}{where}", params
    ).fetchone()[0]


def _new_project(conn, name="evidence-only"):
    project_id = pg.insert_project(conn, name=name)
    conn.commit()
    return project_id


# ═══════════════════════ service: happy path + record shape ═══════════════════


def test_capture_creates_exactly_one_operation_blob_and_snapshot(
    slice_a_schema, evidence_store
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"D-TEST curated evidence record v1\n"

    session = _service_connection(schema)
    try:
        result = source_service.capture_evidence_source_bytes(
            session,
            project_id=project_id,
            content=content,
            source_kind=CURATED,
            source_locator="operator-curated:record-1",
            operation_id="capture-1",
            actor="operator",
        )
        session.commit()
    finally:
        session.close()

    assert result["reused"] is False
    assert result["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert result["byte_size"] == len(content)
    assert result["source_kind"] == CURATED
    assert result["operation_id"] == "evidence-source:capture-1"

    assert _count(conn, "ingest_operation", project_id=project_id) == 1
    assert _count(conn, "source_blob", project_id=project_id) == 1
    assert _count(conn, "source_snapshot", project_id=project_id) == 1
    assert _count(conn, "candidate_fact_revision", project_id=project_id) == 0
    assert _count(conn, "evidence_retention_event", project_id=project_id) == 0

    operation = conn.execute(
        "SELECT status, source_snapshot_id::text, operation_id FROM ingest_operation "
        "WHERE project_id = %s",
        (project_id,),
    ).fetchone()
    assert operation[0] == "committed"
    assert operation[1] == result["source_snapshot_id"]
    assert operation[2] == "evidence-source:capture-1"

    snapshot = conn.execute(
        "SELECT source_kind, source_locator, storage_ref, ingest_operation_id, "
        "captured_by FROM source_snapshot WHERE id = %s",
        (result["source_snapshot_id"],),
    ).fetchone()
    assert snapshot[0] == CURATED
    assert snapshot[1] == "operator-curated:record-1"
    assert snapshot[3] == "evidence-source:capture-1"
    assert snapshot[4] == "operator"

    # Requirement: the bytes really are at storage_ref, verified by digest.
    assert Path(snapshot[2]).is_file()
    assert Path(snapshot[2]).read_bytes() == content
    assert source_storage.stored_bytes_present(
        snapshot[2], expected_sha256=result["content_sha256"]
    )
    assert Path(snapshot[2]).resolve().is_relative_to(evidence_store.resolve())


def test_bytes_exist_before_the_snapshot_row_is_created(
    slice_a_schema, evidence_store, monkeypatch
):
    """Storage strictly precedes the database — observed, not merely documented."""
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"ordering probe\n"
    digest = hashlib.sha256(content).hexdigest()
    observed: list[str] = []

    real_persist = source_storage.persist_source_bytes
    real_insert = source_service.repo.insert_snapshot

    def spy_persist(**kwargs):
        ref = real_persist(**kwargs)
        observed.append("stored")
        return ref

    def spy_insert(conn_, **kwargs):
        # At the moment the snapshot row is written, the bytes are already there.
        assert source_storage.stored_bytes_present(
            kwargs["storage_ref"], expected_sha256=digest
        )
        observed.append("snapshot")
        return real_insert(conn_, **kwargs)

    monkeypatch.setattr(source_storage, "persist_source_bytes", spy_persist)
    monkeypatch.setattr(source_service.repo, "insert_snapshot", spy_insert)

    session = _service_connection(schema)
    try:
        source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=content, source_kind=DOCUMENT,
            source_locator="operator-supplied:doc", operation_id="order-1",
        )
        session.commit()
    finally:
        session.close()

    assert observed == ["stored", "snapshot"]


def test_storage_failure_commits_no_snapshot(
    slice_a_schema, evidence_store, monkeypatch
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)

    def boom(**kwargs):
        raise source_storage.EvidenceSourceStorageError("storage unavailable")

    monkeypatch.setattr(source_storage, "persist_source_bytes", boom)

    session = _service_connection(schema)
    try:
        with pytest.raises(source_storage.EvidenceSourceStorageError):
            source_service.capture_evidence_source_bytes(
                session, project_id=project_id, content=b"never stored",
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="storage-fail",
            )
        session.commit()  # commit whatever the service left behind: nothing
    finally:
        session.close()

    assert _count(conn, "source_snapshot", project_id=project_id) == 0
    assert _count(conn, "source_blob", project_id=project_id) == 0
    assert _count(conn, "ingest_operation", project_id=project_id) == 0


def test_database_failure_leaves_no_committed_snapshot(
    slice_a_schema, evidence_store, monkeypatch
):
    """A DB failure after the bytes land commits nothing; the blob is orphaned.

    This is the documented filesystem/PostgreSQL boundary: an unreferenced
    immutable blob may remain on disk, but no committed snapshot may point at
    absent or corrupt bytes — and here no snapshot is committed at all.
    """
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"db failure probe\n"
    digest = hashlib.sha256(content).hexdigest()
    real_insert = source_service.repo.insert_snapshot

    def boom(conn_, **kwargs):
        real_insert(conn_, **kwargs)  # the row is written…
        raise RuntimeError("authoritative database failure")  # …then the DB fails

    monkeypatch.setattr(source_service.repo, "insert_snapshot", boom)

    session = _service_connection(schema)
    try:
        with pytest.raises(RuntimeError):
            source_service.capture_evidence_source_bytes(
                session, project_id=project_id, content=content,
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="db-fail",
            )
        session.commit()
    finally:
        session.close()

    assert _count(conn, "source_snapshot", project_id=project_id) == 0
    assert _count(conn, "source_blob", project_id=project_id) == 0
    # The orphaned immutable blob remains — and is reusable, never dangling
    # evidence, because nothing references it.
    orphan = source_storage.storage_reference(project_id, digest)
    assert source_storage.stored_bytes_present(orphan, expected_sha256=digest)


def test_missing_project_fails_before_storage(slice_a_schema, evidence_store):
    conn, schema = slice_a_schema
    absent = str(uuid.uuid4())
    session = _service_connection(schema)
    try:
        with pytest.raises(source_service.EvidenceSourceProjectNotFound):
            source_service.capture_evidence_source_bytes(
                session, project_id=absent, content=b"orphan bytes",
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="no-project",
            )
        session.rollback()
    finally:
        session.close()
    assert not evidence_store.exists()


def test_malformed_project_id_never_reaches_sql_and_leaves_the_caller_usable(
    slice_a_schema, evidence_store
):
    """A malformed project id must not abort the CALLER'S transaction.

    ``projects.id`` is a ``uuid`` column, so comparing it against malformed text
    raises ``invalid input syntax for type uuid`` and puts the caller's
    transaction into the aborted state — a caller-visible failure the caller did
    not cause. The service validates the shape before any SQL instead.
    """
    conn, schema = slice_a_schema
    project_id = _new_project(conn)

    session = _service_connection(schema)
    try:
        with pytest.raises(source_service.EvidenceSourceCaptureValidationError):
            source_service.capture_evidence_source_bytes(
                session, project_id="not-a-uuid", content=b"malformed project",
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="malformed-1",
            )
        # The connection is still usable: not aborted, no rollback needed.
        assert session.execute("SELECT 1").fetchone()[0] == 1
        # ...and the same transaction can still complete a genuine capture.
        result = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"good bytes after rejection",
            source_kind=CURATED, source_locator="operator-curated:after",
            operation_id="malformed-recovery",
        )
        session.commit()
    finally:
        session.close()

    assert result["reused"] is False
    assert _count(conn, "source_snapshot", project_id=project_id) == 1
    # Nothing was stored for the rejected request.
    assert not (
        evidence_store / "not-a-uuid"
    ).exists()


def test_raw_malformed_uuid_comparison_really_aborts_a_transaction(
    slice_a_schema, evidence_store
):
    """Evidence for the guard above: the hazard it prevents is real."""
    conn, schema = slice_a_schema
    _new_project(conn)
    session = _service_connection(schema)
    try:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            session.execute(
                "SELECT 1 FROM projects WHERE id = %s LIMIT 1", ("not-a-uuid",)
            )
        # The caller's transaction is now aborted: every further statement fails
        # until a rollback. This is exactly what the pre-SQL validation avoids.
        with pytest.raises(psycopg.errors.InFailedSqlTransaction):
            session.execute("SELECT 1")
        session.rollback()
        assert session.execute("SELECT 1").fetchone()[0] == 1
    finally:
        session.close()


def test_live_isolation_violation_fails_closed(slice_a_schema, evidence_store):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    session = pg.connect(schema=schema, autocommit=False)
    try:
        # Driver attribute says READ COMMITTED; the live transaction is not.
        session.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        session.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        with pytest.raises(source_service.EvidenceSourceCaptureTransactionError):
            source_service.capture_evidence_source_bytes(
                session, project_id=project_id, content=b"wrong isolation",
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="iso-1",
            )
        session.rollback()
    finally:
        session.close()
    assert not evidence_store.exists()


# ═══════════════════════ idempotency matrix ═══════════════════════


def test_same_operation_same_bytes_same_metadata_returns_existing(
    slice_a_schema, evidence_store
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"idempotent capture\n"
    common = dict(
        project_id=project_id, content=content, source_kind=CURATED,
        source_locator="operator-curated:same", operation_id="retry-1",
    )

    session = _service_connection(schema)
    try:
        first = source_service.capture_evidence_source_bytes(session, **common)
        session.commit()
        second = source_service.capture_evidence_source_bytes(session, **common)
        session.commit()
    finally:
        session.close()

    assert second["source_snapshot_id"] == first["source_snapshot_id"]
    assert second["source_blob_id"] == first["source_blob_id"]
    assert second["reused"] is True and first["reused"] is False
    assert _count(conn, "source_snapshot", project_id=project_id) == 1
    assert _count(conn, "ingest_operation", project_id=project_id) == 1


def test_same_operation_different_bytes_fails(slice_a_schema, evidence_store):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    session = _service_connection(schema)
    try:
        source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"original bytes",
            source_kind=CURATED, source_locator="operator-curated:x",
            operation_id="conflict-1",
        )
        session.commit()
        with pytest.raises(source_service.EvidenceSourceCaptureConflict) as excinfo:
            source_service.capture_evidence_source_bytes(
                session, project_id=project_id, content=b"DIFFERENT bytes",
                source_kind=CURATED, source_locator="operator-curated:x",
                operation_id="conflict-1",
            )
        session.commit()
    finally:
        session.close()
    assert "different source bytes" in str(excinfo.value)
    assert _count(conn, "source_snapshot", project_id=project_id) == 1


@pytest.mark.parametrize(
    "changed",
    ({"source_kind": DOCUMENT}, {"source_locator": "operator-curated:changed"}),
)
def test_same_operation_different_metadata_fails(
    slice_a_schema, evidence_store, changed
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    base = dict(
        project_id=project_id, content=b"stable bytes for metadata drift",
        source_kind=CURATED, source_locator="operator-curated:base",
        operation_id="meta-1",
    )
    session = _service_connection(schema)
    try:
        source_service.capture_evidence_source_bytes(session, **base)
        session.commit()
        with pytest.raises(source_service.EvidenceSourceCaptureConflict) as excinfo:
            source_service.capture_evidence_source_bytes(
                session, **{**base, **changed}
            )
        session.commit()
    finally:
        session.close()
    assert "different source metadata" in str(excinfo.value)
    assert _count(conn, "source_snapshot", project_id=project_id) == 1


def test_new_operation_same_bytes_dedupes_blob_but_appends_snapshot(
    slice_a_schema, evidence_store
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"same bytes, two legitimate capture events\n"
    session = _service_connection(schema)
    try:
        first = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=content, source_kind=CURATED,
            source_locator="operator-curated:evt", operation_id="evt-1",
        )
        session.commit()
        second = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=content, source_kind=CURATED,
            source_locator="operator-curated:evt", operation_id="evt-2",
        )
        session.commit()
    finally:
        session.close()

    assert second["source_blob_id"] == first["source_blob_id"]  # blob deduplicated
    assert second["source_snapshot_id"] != first["source_snapshot_id"]
    assert second["reused"] is False
    assert _count(conn, "source_blob", project_id=project_id) == 1
    assert _count(conn, "source_snapshot", project_id=project_id) == 2
    assert _count(conn, "ingest_operation", project_id=project_id) == 2


def test_retry_with_absent_stored_bytes_fails_closed(
    slice_a_schema, evidence_store
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    content = b"bytes that will vanish\n"
    common = dict(
        project_id=project_id, content=content, source_kind=DOCUMENT,
        source_locator="operator-supplied:vanishing", operation_id="vanish-1",
    )
    session = _service_connection(schema)
    try:
        first = source_service.capture_evidence_source_bytes(session, **common)
        session.commit()
        stored = conn.execute(
            "SELECT storage_ref FROM source_snapshot WHERE id = %s",
            (first["source_snapshot_id"],),
        ).fetchone()[0]
        Path(stored).unlink()
        with pytest.raises(source_service.EvidenceSourceCaptureStorageMissing):
            source_service.capture_evidence_source_bytes(session, **common)
        session.rollback()
    finally:
        session.close()


def test_append_only_guarantees_are_not_weakened(slice_a_schema, evidence_store):
    """The created rows remain immutable: v47 triggers still reject mutation."""
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    session = _service_connection(schema)
    try:
        result = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"append only\n",
            source_kind=CURATED, source_locator="operator-curated:append",
            operation_id="append-1",
        )
        session.commit()
    finally:
        session.close()

    conn.rollback()
    for statement, params in (
        (
            "UPDATE source_snapshot SET source_kind = 'raw_web_capture' "
            "WHERE id = %s",
            (result["source_snapshot_id"],),
        ),
        ("DELETE FROM source_snapshot WHERE id = %s", (result["source_snapshot_id"],)),
        ("DELETE FROM source_blob WHERE id = %s", (result["source_blob_id"],)),
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(statement, params)
        conn.rollback()


# ═══════════════════════ Knowledge non-mutation ═══════════════════════


def test_capture_creates_zero_knowledge_state(slice_a_schema, evidence_store):
    """No Knowledge source, item, manifest, ProjectState, evidence or signals.

    ProjectState (which owns the Knowledge layer, the uploaded-file manifests,
    ``imported_evidence`` and ``imported_signals``) is persisted in
    ``state_snapshots``. Evidence-only capture must not create that relation, let
    alone a row in it — so its complete absence after a committed capture is the
    proof at the database boundary.
    """
    conn, schema = slice_a_schema
    project_id = _new_project(conn)

    session = _service_connection(schema)
    try:
        source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"no knowledge here\n",
            source_kind=CURATED, source_locator="operator-curated:none",
            operation_id="nok-1",
        )
        session.commit()
    finally:
        session.close()

    conn.rollback()
    present = conn.execute(
        "SELECT to_regclass(%s)", (f'"{schema}".{KNOWLEDGE_STATE_TABLE}',)
    ).fetchone()[0]
    assert present is None, "evidence-only capture must not create ProjectState storage"

    # The capture is visible only through v47 relations.
    assert _count(conn, "source_snapshot", project_id=project_id) == 1

    # And the evidence store holds only the raw bytes: no manifest, no parsed
    # artifact, no Knowledge sidecar file of any kind.
    stored = [p for p in evidence_store.rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].name == hashlib.sha256(b"no knowledge here\n").hexdigest()


def test_knowledge_upload_store_is_never_written(slice_a_schema, evidence_store, tmp_path):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    upload_root = tmp_path / "upload_store_probe"

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(config.UPLOAD_LAYER, "storage_dir", str(upload_root))
        session = _service_connection(schema)
        try:
            source_service.capture_evidence_source_bytes(
                session, project_id=project_id, content=b"not an upload\n",
                source_kind=DOCUMENT, source_locator="operator-supplied:doc",
                operation_id="not-upload-1",
            )
            session.commit()
        finally:
            session.close()

    assert not upload_root.exists()


# ═══════════════════════ retention / availability ═══════════════════════


def test_retention_and_availability_semantics_are_intact(
    slice_a_schema, evidence_store
):
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    session = _service_connection(schema)
    try:
        captured = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"retention probe\n",
            source_kind=CURATED, source_locator="operator-curated:retention",
            operation_id="ret-1",
        )
        session.commit()
    finally:
        session.close()
    snapshot_id = captured["source_snapshot_id"]

    conn.rollback()
    assert ev_repo.snapshot_available(conn, snapshot_id) is True

    # legal_hold never affects availability…
    ev_repo.insert_retention_event(
        conn, project_id=project_id, event_type="legal_hold",
        source_snapshot_id=snapshot_id, reason="hold",
    )
    conn.commit()
    assert ev_repo.snapshot_available(conn, snapshot_id) is True

    # …tombstone does.
    ev_repo.insert_retention_event(
        conn, project_id=project_id, event_type="tombstone",
        source_snapshot_id=snapshot_id, reason="retire",
    )
    conn.commit()
    assert ev_repo.snapshot_available(conn, snapshot_id) is False


def test_deletion_guard_protects_captured_evidence_storage(
    slice_a_schema, evidence_store, monkeypatch
):
    """The existing Slice A deletion guard covers the new storage namespace too."""
    from knowledge.evidence_snapshot import capture as upload_capture

    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    session = _service_connection(schema)
    try:
        captured = source_service.capture_evidence_source_bytes(
            session, project_id=project_id, content=b"guarded bytes\n",
            source_kind=CURATED, source_locator="operator-curated:guard",
            operation_id="guard-1",
        )
        session.commit()
    finally:
        session.close()

    conn.rollback()
    storage_ref = conn.execute(
        "SELECT storage_ref FROM source_snapshot WHERE id = %s",
        (captured["source_snapshot_id"],),
    ).fetchone()[0]

    monkeypatch.setattr(
        upload_capture, "_runtime_connection",
        lambda: pg.connect(schema=schema, autocommit=False),
    )
    with pytest.raises(upload_capture.DeletionBlockedError):
        upload_capture.assert_safe_to_delete_storage_ref(storage_ref)


# ═══════════════════════ bridge: gates and posture ═══════════════════════


def test_bridge_capture_requires_both_feature_gates(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"gate probe\n")
    argv = [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:gate",
        "--actor", "op", "--operation-id", "gate-1", "--commit",
    ]

    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    code, payload = _bridge(monkeypatch, schema, argv)
    assert code == 1 and payload["status"] == "error"
    assert "Research Evidence is disabled" in payload["error"]

    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.delenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", raising=False)
    code, payload = _bridge(monkeypatch, schema, argv)
    assert code == 1 and payload["status"] == "error"
    assert "Evidence snapshot capture is disabled" in payload["error"]

    assert _count(conn, "source_snapshot", project_id=project_id) == 0
    assert not evidence_store.exists()


def test_bridge_capture_requires_explicit_database_url(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"dsn probe\n")
    monkeypatch.setenv("DATABASE_URL", "")
    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:dsn",
        "--actor", "op", "--operation-id", "dsn-1", "--commit",
    ])
    assert code == 1 and payload["status"] == "error"
    assert "DATABASE_URL" in payload["error"]
    assert _count(conn, "source_snapshot", project_id=project_id) == 0


def test_bridge_capture_requires_matching_runtime_fingerprint(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"fingerprint probe\n")
    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:fp",
        "--actor", "op", "--operation-id", "fp-1", "--commit",
        "--expect-runtime-fingerprint", "0" * 64,
    ])
    assert code == 1 and payload["status"] == "error"
    assert "fingerprint mismatch" in payload["error"]
    assert _count(conn, "source_snapshot", project_id=project_id) == 0


def test_bridge_capture_blocked_on_partial_topology(
    evidence_store, tmp_path, monkeypatch
):
    conn = pg.connect()
    try:
        with pg.fresh_schema(conn) as schema:
            pg.apply_v51_research(conn)
            pg.apply_v52_research(conn)
            project_id = _new_project(conn)
            artifact = _artifact(tmp_path, "record.txt", b"partial topology\n")
            code, payload = _bridge(monkeypatch, schema, [
                "source-capture", "--project-id", project_id, "--file",
                str(artifact), "--source-kind", CURATED,
                "--source-locator", "operator-curated:partial",
                "--actor", "op", "--operation-id", "partial-1", "--commit",
            ])
            assert code == 1 and payload["status"] == "error"
            assert "write blocked" in payload["error"]
            assert _count(conn, "source_snapshot", project_id=project_id) == 0
    finally:
        conn.close()


def test_bridge_capture_dry_run_persists_no_database_state(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    content = b"dry run record\n"
    artifact = _artifact(tmp_path, "record.txt", content)
    recorder: dict = {}

    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:dry",
        "--actor", "op", "--operation-id", "dry-1",
    ], recorder=recorder)

    assert code == 0
    assert payload["dry_run"] is True and payload["committed"] is False
    assert payload["status"] == "dry_run"
    assert recorder["commits"] == 0
    assert _count(conn, "source_snapshot", project_id=project_id) == 0
    assert _count(conn, "source_blob", project_id=project_id) == 0
    assert _count(conn, "ingest_operation", project_id=project_id) == 0
    # The documented non-transactional boundary: the immutable bytes ARE on disk
    # (a dry run is a genuine storage rehearsal), unreferenced by any snapshot.
    # The payload states BOTH halves of that contract, so the operator is not
    # left to infer it from the design document (audit MINOR-2).
    assert payload["source_bytes_persisted"] is True
    assert payload["source_bytes_retained_on_rollback"] is True
    digest = hashlib.sha256(content).hexdigest()
    reference = source_storage.storage_reference(project_id, digest)
    assert source_storage.stored_bytes_present(reference, expected_sha256=digest)
    # ...and the same disclosure is in the command's own help text.
    capture_parser = (
        bridge.build_parser()._subparsers._group_actions[0].choices["source-capture"]
    )
    help_text = capture_parser.format_help().lower()
    assert "database state only" in help_text
    assert "may remain" in help_text and "evidence source store" in help_text
    # Nothing references the retained bytes, so they are unreachable as evidence.
    assert conn.execute(
        "SELECT count(*) FROM source_snapshot WHERE storage_ref = %s", (reference,)
    ).fetchone()[0] == 0


def test_bridge_capture_commit_persists_exactly_the_intended_state(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    content = b"committed record\n"
    artifact = _artifact(tmp_path, "record.txt", content)

    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:commit",
        "--actor", "op", "--operation-id", "commit-1", "--commit",
    ])
    assert code == 0 and payload["committed"] is True
    assert payload["status"] == "committed"
    assert payload["capture_reused"] is False
    assert payload["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["byte_size"] == len(content)
    assert payload["capture_operation_id"] == "evidence-source:commit-1"

    assert _count(conn, "source_snapshot", project_id=project_id) == 1
    assert _count(conn, "source_blob", project_id=project_id) == 1
    assert _count(conn, "ingest_operation", project_id=project_id) == 1
    # Nothing else in the Research Evidence chain was created.
    for table in (
        "research_source_metadata_revision", "research_fact_metadata_revision",
        "research_claim_draft", "research_evidence_intake",
        "research_evidence_intake_item", "candidate_fact_revision",
    ):
        assert _count(conn, table, project_id=project_id) == 0


def test_bridge_capture_idempotent_retry_returns_same_snapshot(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"retry via bridge\n")
    argv = [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:retry",
        "--actor", "op", "--operation-id", "bridge-retry-1", "--commit",
    ]
    code, first = _bridge(monkeypatch, schema, argv)
    assert code == 0 and first["capture_reused"] is False
    code, second = _bridge(monkeypatch, schema, argv)
    assert code == 0 and second["capture_reused"] is True
    assert second["source_snapshot_id"] == first["source_snapshot_id"]
    assert _count(conn, "source_snapshot", project_id=project_id) == 1


def test_bridge_capture_operation_content_mismatch_fails(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    first_file = _artifact(tmp_path, "first.txt", b"first bytes\n")
    second_file = _artifact(tmp_path, "second.txt", b"second bytes\n")

    def argv(path):
        return [
            "source-capture", "--project-id", project_id, "--file", str(path),
            "--source-kind", CURATED,
            "--source-locator", "operator-curated:mismatch",
            "--actor", "op", "--operation-id", "bridge-mismatch-1", "--commit",
        ]

    code, _payload = _bridge(monkeypatch, schema, argv(first_file))
    assert code == 0
    code, payload = _bridge(monkeypatch, schema, argv(second_file))
    assert code == 1 and payload["status"] == "error"
    assert payload["error_type"] == "EvidenceSourceCaptureConflict"
    assert _count(conn, "source_snapshot", project_id=project_id) == 1


def test_bridge_capture_metadata_mismatch_for_reused_operation_fails(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"stable metadata bytes\n")

    def argv(locator):
        return [
            "source-capture", "--project-id", project_id, "--file", str(artifact),
            "--source-kind", CURATED, "--source-locator", locator,
            "--actor", "op", "--operation-id", "bridge-meta-1", "--commit",
        ]

    code, _payload = _bridge(monkeypatch, schema, argv("operator-curated:a"))
    assert code == 0
    code, payload = _bridge(monkeypatch, schema, argv("operator-curated:b"))
    assert code == 1 and payload["error_type"] == "EvidenceSourceCaptureConflict"
    assert _count(conn, "source_snapshot", project_id=project_id) == 1


def test_bridge_expected_sha_mismatch_fails_before_any_write(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"sha bound record\n")
    recorder: dict = {}
    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:sha",
        "--actor", "op", "--operation-id", "sha-1", "--commit",
        "--expected-sha256", "a" * 64,
    ], recorder=recorder)
    assert code == 1 and payload["status"] == "error"
    assert "--expected-sha256" in payload["error"]
    assert recorder.get("commits", 0) == 0
    assert _count(conn, "source_snapshot", project_id=project_id) == 0
    # Rejected before storage: no bytes were written.
    assert not evidence_store.exists()


def test_bridge_expected_sha_match_succeeds(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    content = b"bound to its digest\n"
    artifact = _artifact(tmp_path, "record.txt", content)
    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:shaok",
        "--actor", "op", "--operation-id", "sha-ok-1", "--commit",
        "--expected-sha256", hashlib.sha256(content).hexdigest().upper(),
    ])
    assert code == 0 and payload["committed"] is True


@pytest.mark.parametrize("kind", sorted(source_service.RESERVED_SOURCE_KINDS))
def test_bridge_refuses_reserved_provenance_kinds(
    full_schema, evidence_store, tmp_path, monkeypatch, kind
):
    """A reserved kind is rejected by argparse: it is not even offered."""
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"provenance probe\n")
    with pytest.raises(SystemExit) as excinfo:
        _bridge(monkeypatch, schema, [
            "source-capture", "--project-id", project_id, "--file", str(artifact),
            "--source-kind", kind, "--source-locator", "operator-curated:kind",
            "--actor", "op", "--operation-id", "kind-1", "--commit",
        ])
    assert excinfo.value.code == 2
    assert _count(conn, "source_snapshot", project_id=project_id) == 0


def test_bridge_refuses_a_url_and_never_fetches(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    for url in ("https://example.org/page", "http://example.org", "file:///etc/passwd"):
        code, payload = _bridge(monkeypatch, schema, [
            "source-capture", "--project-id", project_id, "--file", url,
            "--source-kind", DOCUMENT, "--source-locator", "operator-supplied:url",
            "--actor", "op", "--operation-id", "url-1", "--commit",
        ])
        assert code == 1 and payload["status"] == "error"
        assert "never fetches" in payload["error"]
    assert _count(conn, "source_snapshot", project_id=project_id) == 0


def test_bridge_rejects_empty_and_absent_files(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    empty = _artifact(tmp_path, "empty.txt", b"")

    def run(path):
        return _bridge(monkeypatch, schema, [
            "source-capture", "--project-id", project_id, "--file", str(path),
            "--source-kind", DOCUMENT, "--source-locator", "operator-supplied:x",
            "--actor", "op", "--operation-id", "file-1", "--commit",
        ])

    code, payload = run(empty)
    assert code == 1 and "empty" in payload["error"]
    code, payload = run(tmp_path / "does-not-exist.txt")
    assert code == 1 and "does not exist" in payload["error"]
    code, payload = run(tmp_path)  # a directory
    assert code == 1 and "regular file" in payload["error"]
    assert _count(conn, "source_snapshot", project_id=project_id) == 0


def test_bridge_capture_emits_no_content_or_private_storage_path(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    secret = b"CONFIDENTIAL-EVIDENCE-BODY-do-not-emit\n"
    artifact = _artifact(tmp_path, "secret-record.txt", secret)
    locator = "operator-curated:PRIVATE-CAPTURE-LOCATOR"
    code, payload = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", locator,
        "--actor", "op", "--operation-id", "secret-1", "--commit",
    ])
    assert code == 0
    emitted = json.dumps(payload)
    assert "CONFIDENTIAL" not in emitted
    assert str(evidence_store) not in emitted
    assert str(artifact) not in emitted
    assert "storage_ref" not in payload
    assert os.environ["DATABASE_URL"] not in emitted
    # The raw capture locator stays private (the tool's existing posture); only a
    # boolean confirms it was recorded — and it really was persisted.
    assert locator not in emitted
    assert payload["source_locator_recorded"] is True
    conn.rollback()
    assert conn.execute(
        "SELECT source_locator FROM source_snapshot WHERE id = %s",
        (payload["source_snapshot_id"],),
    ).fetchone()[0] == locator


# ═══════════════════════ downstream consumption ═══════════════════════


def test_source_list_sees_the_captured_snapshot(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"listed record\n")
    code, captured = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:listed",
        "--actor", "op", "--operation-id", "list-1", "--commit",
    ])
    assert code == 0

    code, payload = _bridge(
        monkeypatch, schema, ["source-list", "--project-id", project_id]
    )
    assert code == 0
    assert payload["counts"]["source_count"] == 1
    listed = payload["sources"][0]
    assert listed["source_snapshot_id"] == captured["source_snapshot_id"]
    assert listed["source_kind"] == CURATED
    # No metadata revision exists yet, and source-list never leaks storage.
    assert listed["citation_label"] == ""
    assert "storage_ref" not in json.dumps(payload)


def test_fact_create_consumes_the_capture_without_any_knowledge_object(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    conn, schema = full_schema
    project_id = _new_project(conn)
    artifact = _artifact(tmp_path, "record.txt", b"fact source record\n")
    code, captured = _bridge(monkeypatch, schema, [
        "source-capture", "--project-id", project_id, "--file", str(artifact),
        "--source-kind", CURATED, "--source-locator", "operator-curated:fact",
        "--actor", "op", "--operation-id", "fact-src-1", "--commit",
    ])
    assert code == 0
    snapshot_id = captured["source_snapshot_id"]

    code, fact = _bridge(monkeypatch, schema, [
        "fact-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot_id, "--actor", "op",
        "--fact-type", "count", "--value", "7", "--counted-entity", "records",
        "--citation-locator", "section 1", "--commit",
    ])
    assert code == 0 and fact["committed"] is True
    assert fact["source_snapshot_id"] == snapshot_id

    conn.rollback()
    assert _count(conn, "candidate_fact_revision", project_id=project_id) == 1
    # Still no ProjectState / Knowledge storage anywhere.
    assert conn.execute(
        "SELECT to_regclass(%s)", (f'"{schema}".{KNOWLEDGE_STATE_TABLE}',)
    ).fetchone()[0] is None


# ═══════════════════════ D001R-shaped functional probe ═══════════════════════


def test_evidence_only_capture_reaches_internal_analysis_authorization(
    full_schema, evidence_store, tmp_path, monkeypatch
):
    """The complete canonical RE chain from an evidence-only local file.

    Disposable project + disposable database only. This is a capability probe: it
    drives the existing canonical chain and asserts the pack/authorization
    outcome. It runs no audit/strategy phase and no LLM consumer.
    """
    from research_evidence.pack_service import assemble_research_evidence_pack

    conn, schema = full_schema
    project_id = _new_project(conn, name="evidence-only-probe")
    tag = uuid.uuid4().hex[:8]

    evidence_file = _artifact(
        tmp_path,
        "curated-evidence-record.txt",
        (
            "Operator-curated research evidence record\n"
            "normalized-from: frozen admission artifact\n"
            f"probe: {tag}\n"
        ).encode("utf-8"),
    )

    def run(argv):
        code, payload = _bridge(monkeypatch, schema, argv + ["--commit"])
        assert code == 0, payload
        assert payload["committed"] is True, payload
        return payload

    # 1. evidence-only source capture (no Knowledge ingestion anywhere)
    captured = run([
        "source-capture", "--project-id", project_id,
        "--file", str(evidence_file), "--source-kind", CURATED,
        "--source-locator", f"operator-curated:probe/{tag}",
        "--actor", "op", "--operation-id", f"probe-{tag}",
        "--expected-sha256", hashlib.sha256(
            evidence_file.read_bytes()
        ).hexdigest(),
    ])
    snapshot_id = captured["source_snapshot_id"]

    # 2. source-list can see it
    code, listed = _bridge(
        monkeypatch, schema, ["source-list", "--project-id", project_id]
    )
    assert code == 0
    assert [s["source_snapshot_id"] for s in listed["sources"]] == [snapshot_id]

    # 3. source metadata (the citation identity the pack projects)
    metadata = run([
        "source-metadata-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot_id, "--actor", "op",
        "--citation-label", f"Operator-Curated Record {tag}",
        "--canonical-source-locator", f"operator-curated:probe/{tag}",
        "--declared-quality-tier", "operator_curated",
        "--declared-quality-rationale", "normalized from a frozen artifact",
        "--publisher", "operator",
    ])
    source_metadata_id = metadata["source_metadata_revision_id"]

    # 4. candidate fact bound to the evidence-only snapshot
    fact = run([
        "fact-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot_id, "--actor", "op",
        "--fact-type", "count", "--value", "3", "--counted-entity", "records",
        "--citation-locator", "line 1", "--stable-fact-key", f"probe-{tag}",
    ])

    # 5. claim draft
    claim = run([
        "claim-create", "--project-id", project_id, "--actor", "op",
        "--claim-text", f"Evidence-only ingress supports claim {tag}",
        "--claim-category", "capability",
    ])

    # 6. intakes + items (evidence and claim endpoints)
    evidence_intake = run([
        "intake-create", "--project-id", project_id, "--actor", "op",
        "--source-snapshot-id", snapshot_id,
        "--source-metadata-revision-id", source_metadata_id,
        "--selection-reason", "evidence-only curated record",
    ])
    evidence_item = run([
        "intake-item-create", "--project-id", project_id, "--actor", "op",
        "--research-evidence-intake-id",
        evidence_intake["research_evidence_intake_id"],
        "--item-kind", "candidate_fact",
        "--candidate-fact-revision-id", fact["candidate_fact_revision_id"],
        "--fact-metadata-revision-id", fact["fact_metadata_revision_id"],
    ])
    claim_intake = run([
        "intake-create", "--project-id", project_id, "--actor", "op",
        "--source-snapshot-id", snapshot_id,
        "--source-metadata-revision-id", source_metadata_id,
        "--selection-reason", "claim intake",
    ])
    claim_item = run([
        "intake-item-create", "--project-id", project_id, "--actor", "op",
        "--research-evidence-intake-id",
        claim_intake["research_evidence_intake_id"],
        "--item-kind", "claim_draft",
        "--claim-draft-id", claim["claim_draft_id"],
    ])
    evidence_item_id = evidence_item["research_evidence_intake_item_id"]
    claim_item_id = claim_item["research_evidence_intake_item_id"]

    # 7. reviews (approved on both endpoints)
    for item_id, label in ((evidence_item_id, "ev"), (claim_item_id, "cl")):
        run([
            "review-record", "--project-id", project_id,
            "--research-evidence-intake-item-id", item_id,
            "--decision-type", "approved",
            "--decision-reason", "operator reviewed the curated record",
            "--actor", "op", "--request-id", f"review-{label}-{tag}",
        ])

    # 8. claim support assessment
    run([
        "claim-support-record", "--project-id", project_id,
        "--claim-intake-item-id", claim_item_id,
        "--evidence-intake-item-id", evidence_item_id,
        "--request-id", f"support-{tag}",
        "--locator-resolution", "resolvable",
        "--locator-rationale", "the curated record locator resolves",
        "--evidence-linkage", "linked",
        "--evidence-linkage-rationale", "the fact comes from this record",
        "--semantic-relationship", "support",
        "--semantic-relationship-rationale", "the record supports the claim",
        "--actor", "op",
    ])

    # 9. claim annotation + project context
    run([
        "annotation-record", "--project-id", project_id,
        "--claim-draft-id", claim["claim_draft_id"],
        "--request-id", f"annotation-{tag}",
        "--epistemic-status", "reported_fact",
        "--confidence-label", "medium",
        "--decision-relevance", "supports the ingress capability decision",
        "--supports-statement", "an evidence-only record can back a claim",
        "--does-not-prove", "it does not prove external retrieval works",
        "--actor", "op",
    ])
    run([
        "context-record", "--project-id", project_id,
        "--request-id", f"context-{tag}",
        "--research-question", "can evidence-only ingress back internal analysis?",
        "--project-limitations", "operator-curated records only",
        "--unresolved-gaps", "no external evidence ingress",
        "--actor", "op",
    ])

    # 10. internal_analysis authorization eligibility is REACHED
    confirmation = f"{project_id} {claim_item_id} {evidence_item_id}"
    authorization = run([
        "authorize-internal-analysis", "--project-id", project_id,
        "--claim-intake-item-id", claim_item_id,
        "--evidence-intake-item-id", evidence_item_id,
        "--request-id", f"authorize-{tag}",
        "--reason", "evidence-only ingress capability probe",
        "--actor", "op", "--confirm", confirmation,
    ])
    assert authorization["usage_scope"] == "internal_analysis"

    # The canonical A-2 pack now contains the evidence-only source.
    conn.rollback()
    pack = assemble_research_evidence_pack(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert pack.counts.claim_count == 1
    assert pack.counts.source_count == 1
    assert pack.counts.relationship_count == 1
    assert pack.sources[0].source_snapshot_id == snapshot_id
    assert pack.sources[0].source_kind == CURATED

    # …and NOT ONE Knowledge object exists for that source.
    assert conn.execute(
        "SELECT to_regclass(%s)", (f'"{schema}".{KNOWLEDGE_STATE_TABLE}',)
    ).fetchone()[0] is None
    assert _count(conn, "source_snapshot", project_id=project_id) == 1
    assert _count(conn, "ingest_operation", project_id=project_id) == 1
    operation_ids = [
        row[0] for row in conn.execute(
            "SELECT operation_id FROM ingest_operation WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    ]
    assert operation_ids == [f"evidence-source:probe-{tag}"]
    assert not any(op.startswith("upload:") for op in operation_ids)

    # 11. The truthful provenance category survives A-3 disclosure AND both A-4A
    #     boundaries (audit MAJOR-1). Renderer + attestation only: still no
    #     audit/strategy phase, no orchestrator, and no LLM call.
    import research_evidence_context as rc
    from research_evidence import project_research_evidence_presentation

    projection = project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert projection.sources[0].source_kind == CURATED

    block = rc.render_research_evidence_block(projection)
    assert f"     source_kind: {CURATED}" in block.splitlines()
    assert block.count("     source_kind: ") == len(projection.sources) == 1
    assert len(block.encode("utf-8")) <= rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES
    # The model-facing block still carries no private storage identity.
    storage_ref = conn.execute(
        "SELECT storage_ref FROM source_snapshot WHERE id = %s", (snapshot_id,)
    ).fetchone()[0]
    assert storage_ref
    assert storage_ref not in block
    assert str(evidence_store) not in block

    attestation = rc._attestation_from_projection(projection)
    assert [item.source_kind for item in attestation["sources"]] == [CURATED]


def test_capture_operation_uniqueness_is_enforced_by_the_database(
    slice_a_schema, evidence_store
):
    """The idempotency contract's load-bearing constraint really exists."""
    conn, schema = slice_a_schema
    project_id = _new_project(conn)
    conn.rollback()
    state = bridge._constraint_state(
        conn, schema, "ingest_operation", "uq_ingest_operation_project_op"
    )
    assert bridge._constraint_problem(
        state, expected_columns=("project_id", "operation_id")
    ) is None
