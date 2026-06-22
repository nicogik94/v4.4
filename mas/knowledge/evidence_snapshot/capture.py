"""Upload-path capture seam and deletion guard for Slice A.

Both entry points are inert unless the feature is enabled. When enabled, the
authoritative MAS PostgreSQL database is used (no separate evidence database, no
in-memory fallback). Capture failure never breaks the host upload and never
claims durable evidence; the deletion guard fails closed when linkage cannot be
verified.

``_runtime_connection`` is the single seam tests monkeypatch to inject a
disposable database connection.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import config

from . import repository as repo

logger = logging.getLogger(__name__)

HASH_ALGORITHM = "sha256"


class CaptureError(RuntimeError):
    """A Slice A capture attempt failed; the host upload is unaffected."""


class DeletionBlockedError(RuntimeError):
    """Hard deletion refused because storage is (or may be) snapshot-linked."""


def _runtime_connection():
    """Return a new connection to the authoritative MAS database, or None if disabled.

    Tests monkeypatch this to inject a disposable-database connection. Production
    callers never pass a connection explicitly, so the feature flag governs here.
    """
    if not config.evidence_snapshot_enabled():
        return None
    import psycopg

    return psycopg.connect(config.DATABASE_URL)


def _stable_operation_id(project_id: str, storage_ref: str) -> str:
    """Project-scoped stable fingerprint for an upload capture event.

    The per-upload storage_ref is unique and stable, so reusing it as the
    operation id makes capture retries for the same stored file idempotent while a
    distinct upload (distinct storage_ref) is always a new capture event.
    """
    digest = hashlib.sha256(f"{project_id}:{storage_ref}".encode("utf-8")).hexdigest()
    return f"upload:{digest}"


def capture_upload(
    *,
    project_id: str,
    content: bytes,
    storage_ref: str,
    source_kind: str = "uploaded_file",
    source_locator: str = "",
    actor: str = "",
    operation_id: Optional[str] = None,
    connection=None,
) -> Optional[str]:
    """Capture a Blob + Snapshot for a genuine raw-bytes upload.

    Returns the snapshot id on success, or None when capture is disabled or the
    material is not capturable. Raises CaptureError on a genuine persistence
    failure (callers in the upload path swallow this so the upload still succeeds).
    """
    own_conn = False
    conn = connection
    if conn is None:
        conn = _runtime_connection()
        if conn is None:
            return None  # feature disabled — no-op
        own_conn = True

    op_id = operation_id or _stable_operation_id(project_id, storage_ref)
    capturable = bool(content) and bool(storage_ref)

    try:
        operation = repo.create_or_get_ingest_operation(
            conn, project_id=project_id, operation_id=op_id,
            detail=("" if capturable else "missing raw bytes or stable storage_ref"),
        )

        # Idempotent retry: a committed operation already produced its snapshot.
        if operation.existed and operation.status == "committed" and operation.source_snapshot_id:
            conn.commit()
            return operation.source_snapshot_id

        if not capturable:
            # Genuine raw bytes plus a stable storage_ref are required. Do not
            # invent a snapshot from parsed rows, checksums, or free text.
            repo.set_ingest_status(
                conn, operation_pk=operation.id, status="skipped_not_capturable",
            )
            conn.commit()
            return None

        # The IngestOperation row now exists (created or fetched) within this
        # transaction. Wrap the Blob/Snapshot work in a savepoint so a later
        # failure rolls back only the blob/snapshot/status changes — never
        # leaving partial rows — while the operation row survives to be marked
        # failed and committed as durable operational state.
        conn.execute("SAVEPOINT slicea_capture")
        try:
            content_hash = hashlib.new(HASH_ALGORITHM, content).hexdigest()
            blob_id = repo.insert_or_get_blob(
                conn, project_id=project_id, content_hash=content_hash,
                byte_size=len(content), hash_algorithm=HASH_ALGORITHM, created_by=actor,
            )
            snapshot_id = repo.insert_snapshot(
                conn, source_blob_id=blob_id, project_id=project_id, storage_ref=storage_ref,
                source_kind=source_kind, source_locator=source_locator,
                ingest_operation_id=op_id, captured_by=actor,
            )
            repo.set_ingest_status(
                conn, operation_pk=operation.id, status="committed", source_snapshot_id=snapshot_id,
            )
        except Exception as exc:
            # Undo only the blob/snapshot/status work; keep the operation row.
            conn.execute("ROLLBACK TO SAVEPOINT slicea_capture")
            repo.set_ingest_status(
                conn, operation_pk=operation.id, status="failed", detail=str(exc)[:500],
            )
            conn.commit()
            raise CaptureError(str(exc)) from exc
        conn.execute("RELEASE SAVEPOINT slicea_capture")
        conn.commit()
        return snapshot_id
    except CaptureError:
        raise
    except Exception as exc:
        # Failure before/at operation creation: nothing durable to annotate.
        try:
            conn.rollback()
        except Exception:  # pragma: no cover - rollback best effort
            pass
        raise CaptureError(str(exc)) from exc
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass


def assert_safe_to_delete_storage_ref(storage_ref: str, *, connection=None) -> None:
    """Refuse ordinary hard deletion of storage that is (or may be) snapshot-linked.

    No-op when the feature is disabled (preserving current deletion behavior).
    When enabled, refuses if the exact storage_ref is linked to a SourceSnapshot,
    and fails closed if linkage cannot be verified.
    """
    own_conn = False
    conn = connection
    if conn is None:
        conn = _runtime_connection()
        if conn is None:
            return  # feature disabled — preserve current behavior
        own_conn = True

    try:
        try:
            linked = repo.find_snapshots_by_storage_ref(conn, storage_ref)
        except Exception as exc:
            # Linkage required but unverifiable → fail closed.
            raise DeletionBlockedError(
                "Cannot verify snapshot linkage for this storage reference; refusing deletion."
            ) from exc
        if linked:
            raise DeletionBlockedError(
                "Storage reference is linked to retained evidence snapshot(s); "
                "ordinary hard deletion is refused."
            )
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass
