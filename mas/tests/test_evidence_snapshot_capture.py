"""PostgreSQL-backed Slice A capture-seam tests: upload durability, CSV/raw-bytes
boundary, no automatic fact extraction, and the deletion guard.

Skipped when no disposable PostgreSQL database is provided (TEST_EVIDENCE_PG_DSN).
"""
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from config import UPLOAD_LAYER  # noqa: E402
from knowledge import files as knowledge_files  # noqa: E402
from knowledge.evidence_snapshot import capture, repository as repo  # noqa: E402
from knowledge.files import delete_uploaded_file, ingest_uploaded_file  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema(conn):
    with pg.fresh_schema(conn) as s:
        yield s


def _enable_capture_on_schema(monkeypatch, schema):
    monkeypatch.setattr(capture, "_runtime_connection", lambda: pg.connect(schema=schema, autocommit=False))


def _new_project(conn):
    pid = str(uuid.uuid4())
    pg.insert_project(conn, project_id=pid, name="capture")
    conn.commit()
    return pid


def _count(conn, table):
    conn.rollback()  # fresh read snapshot so we see capture's committed rows
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ── 8. Capture durability: DB failure does not break the upload ──────────────

def test_upload_succeeds_when_capture_fails(conn, schema, monkeypatch):
    pid = _new_project(conn)
    state = make_state(project_id=pid)

    def boom():
        raise RuntimeError("authoritative database unreachable")

    monkeypatch.setattr(capture, "_runtime_connection", boom)

    with tempfile.TemporaryDirectory() as tempdir, \
            pytest.MonkeyPatch().context() as mp:
        mp.setattr(UPLOAD_LAYER, "storage_dir", tempdir)
        result = ingest_uploaded_file(
            state, filename="brief.txt", media_type="text/plain",
            content=b"Durable upload, failed capture.", actor="operator",
        )
        # Upload still succeeded …
        assert result.manifest.filename == "brief.txt"
        assert Path(result.manifest.storage_ref).exists()
    # … but produced no durable Slice A evidence.
    assert _count(conn, "source_snapshot") == 0
    assert _count(conn, "source_blob") == 0


# ── Happy-path capture through the real upload seam ──────────────────────────

def test_upload_captures_blob_and_snapshot(conn, schema, monkeypatch):
    pid = _new_project(conn)
    state = make_state(project_id=pid)
    _enable_capture_on_schema(monkeypatch, schema)

    content = b"Genuine raw bytes for capture."
    with tempfile.TemporaryDirectory() as tempdir, pytest.MonkeyPatch().context() as mp:
        mp.setattr(UPLOAD_LAYER, "storage_dir", tempdir)
        result = ingest_uploaded_file(
            state, filename="brief.txt", media_type="text/plain",
            content=content, actor="operator",
        )
        storage_ref = result.manifest.storage_ref

    assert _count(conn, "source_blob") == 1
    assert _count(conn, "source_snapshot") == 1
    row = conn.execute(
        "SELECT byte_size FROM source_blob LIMIT 1"
    ).fetchone()
    assert row[0] == len(content)
    snap = conn.execute("SELECT storage_ref FROM source_snapshot LIMIT 1").fetchone()
    assert snap[0] == storage_ref


# ── 7. No automatic fact extraction from uploaded file content ───────────────

def test_upload_does_not_auto_create_candidate_facts(conn, schema, monkeypatch):
    pid = _new_project(conn)
    state = make_state(project_id=pid)
    _enable_capture_on_schema(monkeypatch, schema)

    csv_bytes = b"metric,value\nctr,0.42\nrevenue,1000\n"
    with tempfile.TemporaryDirectory() as tempdir, pytest.MonkeyPatch().context() as mp:
        mp.setattr(UPLOAD_LAYER, "storage_dir", tempdir)
        ingest_uploaded_file(
            state, filename="table.csv", media_type="text/csv",
            content=csv_bytes, actor="operator",
        )
    # A snapshot of the genuine file bytes is fine; CSV cells must NOT become facts.
    assert _count(conn, "source_snapshot") == 1
    assert _count(conn, "candidate_fact_revision") == 0


# ── 9. CSV / raw-material boundary: parsed rows are not a snapshot ───────────

def test_no_snapshot_without_raw_bytes_and_storage_ref(conn, schema):
    pid = _new_project(conn)
    # Simulates the CSV-text path: derived row data, no stable storage reference.
    result = capture.capture_upload(
        project_id=pid, content=b"ctr,0.42", storage_ref="", operation_id="csv-rows", connection=conn,
    )
    assert result is None
    assert _count(conn, "source_snapshot") == 0
    op = repo.get_ingest_operation(conn, project_id=pid, operation_id="csv-rows")
    assert op is not None and op.status == "skipped_not_capturable"


# ── 10. Deletion guard ───────────────────────────────────────────────────────

def test_delete_refused_for_snapshot_linked_storage(conn, schema, monkeypatch):
    pid = _new_project(conn)
    state = make_state(project_id=pid)
    _enable_capture_on_schema(monkeypatch, schema)

    with tempfile.TemporaryDirectory() as tempdir, pytest.MonkeyPatch().context() as mp:
        mp.setattr(UPLOAD_LAYER, "storage_dir", tempdir)
        result = ingest_uploaded_file(
            state, filename="brief.txt", media_type="text/plain",
            content=b"linked bytes", actor="operator",
        )
        file_id = result.manifest.file_id
        storage_ref = result.manifest.storage_ref
        assert _count(conn, "source_snapshot") == 1

        with pytest.raises(capture.DeletionBlockedError):
            delete_uploaded_file(state, file_id)
        # File must not have been hard-unlinked.
        assert Path(storage_ref).exists()


def test_delete_fails_closed_when_linkage_unverifiable(conn, schema, monkeypatch):
    def explode(_conn, _ref):
        raise RuntimeError("cannot verify linkage")

    monkeypatch.setattr(capture, "_runtime_connection", lambda: pg.connect(schema=schema, autocommit=False))
    monkeypatch.setattr(repo, "find_snapshots_by_storage_ref", explode)

    with pytest.raises(capture.DeletionBlockedError):
        capture.assert_safe_to_delete_storage_ref("/some/storage/ref")
