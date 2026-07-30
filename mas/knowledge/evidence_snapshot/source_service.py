"""Bounded production service for evidence-only v47 source capture (R2.0A-4C).

Discovery for this wave found no existing validated public service that creates a
canonical v47 ``SourceBlob`` + ``SourceSnapshot`` from operator-supplied local
bytes *without* passing through Knowledge ingestion:

* ``knowledge.evidence_snapshot.capture.capture_upload`` is the **upload-path**
  seam. It owns its own connection and COMMIT, derives its operation id from an
  upload ``storage_ref`` (``upload:<digest>``), and its only production caller is
  ``knowledge.files.ingest_uploaded_file`` — which creates a Knowledge source
  registry entry, Knowledge items, an uploaded-file manifest, and mutates
  ``ProjectState`` (and, in structured-import mode, ``imported_evidence`` /
  ``imported_signals``). Capture there is a side effect of Knowledge ingestion,
  not an independent ingress.
* ``knowledge.evidence_snapshot.repository.insert_snapshot`` /
  ``insert_or_get_blob`` are low-level append seams: no feature gate, no
  transaction-ownership guard, no project validation, no provenance vocabulary,
  and — critically — no proof that ``storage_ref`` names bytes that actually
  exist.

This module is the smallest bounded service that closes that gap. It:

* is inert unless ``MAS_EVIDENCE_SNAPSHOT_ENABLED`` is set (it writes v47 rows);
* requires genuine non-empty caller-supplied bytes;
* requires a **truthful** provenance kind from a closed vocabulary that this
  ingress can actually attest to (see :data:`EVIDENCE_SOURCE_KINDS`), and refuses
  the reserved kinds this path cannot honestly claim
  (:data:`RESERVED_SOURCE_KINDS`);
* stores the bytes in the dedicated immutable, content-addressed evidence-only
  store (:mod:`knowledge.evidence_snapshot.source_storage`) **before** any
  snapshot row is created, and never returns a snapshot whose bytes are absent;
* preserves caller transaction ownership — it never commits, rejects an
  autocommit connection, rejects any isolation level other than an explicitly
  pinned READ COMMITTED, verifies the *live* isolation, and wraps its writes in a
  savepoint;
* reuses the existing v47 ``IngestOperation`` / ``SourceBlob`` / ``SourceSnapshot``
  architecture through :mod:`knowledge.evidence_snapshot.repository`;
* creates **no** Knowledge source, Knowledge item, uploaded-file manifest,
  ``ProjectState`` mutation, ``imported_evidence``, ``imported_signals``,
  candidate fact, claim, authorization, or parallel evidence database;
* performs no parsing, no network access, and no LLM calls.

Filesystem/database boundary
----------------------------
A POSIX filesystem and PostgreSQL cannot participate in one atomic transaction,
and this module does not pretend otherwise. The ordering is deliberate and
one-directional:

1. the bytes are made durable and digest-verified on disk;
2. only then are the ``IngestOperation`` / ``SourceBlob`` / ``SourceSnapshot``
   rows created on the caller's transaction.

Consequences, stated explicitly:

* a **committed** ``SourceSnapshot`` can never reference absent or corrupt bytes;
* a database failure (or a deliberate rollback, e.g. the operator bridge's
  dry-run default) may leave an unreferenced immutable blob on disk. That blob is
  content-addressed, so a later genuine capture of the same bytes reuses it
  rather than duplicating it, and it is never reachable as evidence because no
  snapshot references it.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import config

from . import repository as repo
from . import source_storage

HASH_ALGORITHM = source_storage.HASH_ALGORITHM

# Operation-id namespace. The upload path uses ``upload:<digest>``
# (``capture.capture_upload``); evidence-only capture uses its own prefix so an
# evidence-only capture can never adopt — or be adopted by — an upload's
# ``ingest_operation`` row, even if an operator supplies a colliding identifier.
OPERATION_ID_PREFIX = "evidence-source:"

# Provenance kinds this ingress can truthfully attest to.
#
# The kind describes HOW THIS SYSTEM OBTAINED THE BYTES, which for this path is
# always "an operator supplied local bytes". ``source_locator`` separately carries
# the operator's assertion about where the material originally came from, so an
# operator-curated record never has to masquerade as a raw original artifact.
OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD = (
    "operator_curated_research_evidence_record"
)
OPERATOR_SUPPLIED_DOCUMENT = "operator_supplied_document"

EVIDENCE_SOURCE_KINDS: frozenset[str] = frozenset({
    # A normalised, operator-authored evidence record (not a copy of an original
    # artifact). It must be able to say so rather than claim to be a raw capture.
    OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD,
    # A document artifact the operator supplied verbatim as local bytes.
    OPERATOR_SUPPLIED_DOCUMENT,
})

# Kinds this ingress must never claim, because it cannot attest to them:
# ``uploaded_file`` belongs to the Knowledge upload path (which really does
# create a Knowledge manifest), and ``raw_web_capture`` would assert that this
# system fetched the material from the network — this path performs no fetching
# whatsoever. Refusing them here is what keeps provenance truthful instead of
# fabricated.
RESERVED_SOURCE_KINDS: frozenset[str] = frozenset({
    "uploaded_file",
    "raw_web_capture",
})


class EvidenceSourceCaptureDisabled(RuntimeError):
    """Raised when evidence-only capture is attempted while Slice A is disabled."""


class EvidenceSourceCaptureTransactionError(RuntimeError):
    """Raised when caller-owned atomicity cannot be preserved."""


class EvidenceSourceCaptureValidationError(ValueError):
    """A capture argument is missing, empty, oversized, or not truthful."""


class EvidenceSourceProjectNotFound(ValueError):
    """The parent project does not exist."""


class EvidenceSourceCaptureConflict(ValueError):
    """A reused capture operation id does not describe this capture.

    A capture operation is an immutable event: the same
    ``(project_id, operation_id)`` must always mean the same bytes and the same
    source metadata. Reusing it for different content or different provenance is
    refused rather than silently appending a second, divergent capture under one
    operation identity.
    """


class EvidenceSourceCaptureStorageMissing(RuntimeError):
    """A previously committed capture's bytes are absent or corrupt.

    Raised on an idempotent retry so the service never hands back a snapshot id
    whose durable bytes can no longer be verified.
    """


def _require_enabled() -> None:
    if not config.evidence_snapshot_enabled():
        raise EvidenceSourceCaptureDisabled(
            "Evidence snapshot capture is disabled "
            "(set MAS_EVIDENCE_SNAPSHOT_ENABLED to enable it)"
        )


def _require_caller_owned_read_committed(conn) -> None:
    """Reject autocommit and require an EXPLICITLY pinned READ COMMITTED level.

    Mirrors :func:`knowledge.evidence_snapshot.fact_service._require_caller_owned_read_committed`
    exactly, and for the same reason: it inspects only the driver's Python-side
    attributes so it can reject before any SQL is issued. ``isolation_level is
    None`` delegates to the server/session default and therefore does not *prove*
    READ COMMITTED, so it is rejected; REPEATABLE READ and SERIALIZABLE are
    rejected.
    """
    if conn.autocommit:
        raise EvidenceSourceCaptureTransactionError(
            "evidence-only source capture requires a non-autocommit connection"
        )
    isolation = getattr(conn, "isolation_level", None)
    if isolation is None:
        raise EvidenceSourceCaptureTransactionError(
            "evidence-only source capture requires an explicitly pinned READ "
            "COMMITTED isolation level (isolation_level=None delegates to the "
            "server default and does not prove READ COMMITTED)"
        )
    name = getattr(isolation, "name", str(isolation)).upper()
    if name != "READ_COMMITTED":
        raise EvidenceSourceCaptureTransactionError(
            "evidence-only source capture requires READ COMMITTED isolation "
            f"(got {name})"
        )


def _verify_live_read_committed(conn) -> None:
    """Verify the LIVE transaction isolation, not merely the driver attribute.

    The driver attribute governs what is requested for a *new* transaction; a
    caller can pin the active transaction differently with a raw ``SET
    TRANSACTION ISOLATION LEVEL``. This is the first statement the service
    issues, so every pre-SQL rejection above still precedes any SQL.
    """
    row = conn.execute("SHOW transaction_isolation").fetchone()
    level = (row[0] if row else "").strip().lower()
    if level != "read committed":
        raise EvidenceSourceCaptureTransactionError(
            "evidence-only source capture requires a live READ COMMITTED "
            f"transaction (got {level!r})"
        )


def _require_text(value: Optional[str], label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise EvidenceSourceCaptureValidationError(f"{label} is required")
    return text


def _require_project_uuid(project_id: Optional[str]) -> str:
    """Validate the project id's canonical UUID shape BEFORE any SQL.

    A malformed project id must never reach the ``projects.id`` UUID comparison:
    PostgreSQL would raise ``invalid input syntax for type uuid``, which aborts
    the CALLER'S transaction and turns an argument mistake into a broken
    transaction the caller did not cause. The accepted shape is exactly the one
    :func:`source_storage.is_canonical_uuid` enforces for the store, so this
    check can never accept an id the storage layer would later reject.
    """
    project = _require_text(project_id, "project_id")
    if not source_storage.is_canonical_uuid(project):
        raise EvidenceSourceCaptureValidationError(
            "project_id must be a canonical UUID"
        )
    return project


def _validate_source_kind(source_kind: str) -> str:
    kind = _require_text(source_kind, "source_kind")
    if kind in RESERVED_SOURCE_KINDS:
        raise EvidenceSourceCaptureValidationError(
            f"source_kind {kind!r} is reserved for a path that can attest to it; "
            "evidence-only capture must declare a kind it can truthfully claim "
            f"({', '.join(sorted(EVIDENCE_SOURCE_KINDS))})"
        )
    if kind not in EVIDENCE_SOURCE_KINDS:
        raise EvidenceSourceCaptureValidationError(
            f"unsupported evidence-only source_kind {kind!r} "
            f"(expected one of: {', '.join(sorted(EVIDENCE_SOURCE_KINDS))})"
        )
    return kind


def namespaced_operation_id(operation_id: str) -> str:
    """Return the evidence-only capture operation id for an operator identifier."""
    return f"{OPERATION_ID_PREFIX}{_require_text(operation_id, 'operation_id')}"


@contextmanager
def _capture_write(conn):
    conn.execute("SAVEPOINT evidence_source_capture")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT evidence_source_capture")
        conn.execute("RELEASE SAVEPOINT evidence_source_capture")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT evidence_source_capture")


def _project_exists(conn, project_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM projects WHERE id = %s LIMIT 1", (project_id,)
    ).fetchone()
    return row is not None


def _capture_state(conn, *, project_id: str, source_snapshot_id: str) -> Optional[dict]:
    """Read the committed capture facts of one same-project snapshot.

    Returns the snapshot's storage reference and declared provenance plus its
    blob's content identity, so an idempotent retry can be checked against what
    was actually committed.
    """
    row = conn.execute(
        """
        SELECT snapshot.storage_ref, snapshot.source_kind, snapshot.source_locator,
               blob.id::text, blob.hash_algorithm, blob.content_hash, blob.byte_size
        FROM source_snapshot snapshot
        JOIN source_blob blob
          ON blob.id = snapshot.source_blob_id
         AND blob.project_id = snapshot.project_id
        WHERE snapshot.id = %s AND snapshot.project_id = %s
        """,
        (source_snapshot_id, project_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "storage_ref": row[0],
        "source_kind": row[1] or "",
        "source_locator": row[2] or "",
        "source_blob_id": row[3],
        "hash_algorithm": row[4],
        "content_hash": row[5],
        "byte_size": int(row[6]),
    }


def _reuse_committed_capture(
    conn,
    *,
    project_id: str,
    operation_id: str,
    source_snapshot_id: str,
    content_sha256: str,
    source_kind: str,
    source_locator: str,
) -> dict:
    """Validate and return an already-committed capture for a reused operation id.

    Raises :class:`EvidenceSourceCaptureConflict` when the committed capture does
    not describe this request (different bytes, or materially different source
    metadata), and :class:`EvidenceSourceCaptureStorageMissing` when its durable
    bytes can no longer be verified.
    """
    state = _capture_state(
        conn, project_id=project_id, source_snapshot_id=source_snapshot_id
    )
    if state is None:
        # The operation claims a committed snapshot that is not in this project.
        raise EvidenceSourceCaptureConflict(
            "capture operation references a snapshot that does not exist for "
            "this project"
        )
    if (
        state["hash_algorithm"] != HASH_ALGORITHM
        or state["content_hash"] != content_sha256
    ):
        raise EvidenceSourceCaptureConflict(
            "capture operation id was already committed for different source "
            "bytes; a capture event is immutable"
        )
    if (
        state["source_kind"] != source_kind
        or state["source_locator"] != source_locator
    ):
        raise EvidenceSourceCaptureConflict(
            "capture operation id was already committed with different source "
            "metadata; a capture event is immutable"
        )
    if not source_storage.stored_bytes_present(
        state["storage_ref"], expected_sha256=content_sha256
    ):
        raise EvidenceSourceCaptureStorageMissing(
            "previously committed capture bytes are absent or fail digest "
            "verification; refusing to reuse the snapshot"
        )
    return {
        "source_snapshot_id": source_snapshot_id,
        "source_blob_id": state["source_blob_id"],
        "operation_id": operation_id,
        "content_sha256": content_sha256,
        "byte_size": state["byte_size"],
        "source_kind": source_kind,
        "source_locator": source_locator,
        "reused": True,
        "bytes_persisted": True,
    }


def capture_evidence_source_bytes(
    conn,
    *,
    project_id: str,
    content: bytes,
    source_kind: str,
    source_locator: str,
    operation_id: str,
    actor: str = "",
    expected_sha256: Optional[str] = None,
) -> dict:
    """Capture one immutable evidence-only ``SourceBlob`` + ``SourceSnapshot``.

    Returns the capture result (snapshot id, blob id, content identity, declared
    provenance, whether an existing committed capture was reused). The caller owns
    the connection lifecycle and the COMMIT/ROLLBACK decision; this service never
    commits.

    Every semantic rejection — disabled feature, autocommit/isolation, malformed
    project id, empty or oversized bytes, untruthful provenance kind, missing
    locator/operation id, ``expected_sha256`` mismatch — happens before any SQL
    and before any byte is written, so a rejected request also leaves the
    caller's transaction untouched and usable. The only statements preceding the
    storage write are the live-isolation verification, the project-existence
    read, and the idempotency probe; none of them mutates anything.
    """
    _require_enabled()
    _require_caller_owned_read_committed(conn)

    project = _require_project_uuid(project_id)
    kind = _validate_source_kind(source_kind)
    locator = _require_text(source_locator, "source_locator")
    namespaced_operation = namespaced_operation_id(operation_id)

    if not content:
        raise EvidenceSourceCaptureValidationError(
            "evidence-only capture requires genuine non-empty source bytes"
        )
    max_bytes = config.evidence_source_max_bytes()
    if len(content) > max_bytes:
        raise EvidenceSourceCaptureValidationError(
            f"source bytes exceed the configured maximum of {max_bytes} bytes"
        )

    content_sha256 = source_storage.content_digest(content)
    if expected_sha256 is not None:
        expected = (expected_sha256 or "").strip().lower()
        if not expected:
            raise EvidenceSourceCaptureValidationError(
                "expected_sha256 was supplied but is empty"
            )
        if expected != content_sha256:
            # Binding failure: the operator did not supply the artifact they
            # intended. Reject before any byte is stored and before any SQL.
            raise EvidenceSourceCaptureValidationError(
                "supplied source bytes do not match the expected SHA-256 digest"
            )

    # First (and only) statement before storage; opens the caller's transaction
    # at the pinned level so every rejection above precedes any SQL.
    _verify_live_read_committed(conn)

    if not _project_exists(conn, project):
        raise EvidenceSourceProjectNotFound(
            "project parent not found for evidence-only source capture"
        )

    # Fast idempotent path: an already-committed capture under this operation id
    # is validated and returned without storing anything. Doing this before the
    # storage write means a rejected (mismatched) retry never leaves bytes behind.
    existing = repo.get_ingest_operation(
        conn, project_id=project, operation_id=namespaced_operation
    )
    if (
        existing is not None
        and existing.status == "committed"
        and existing.source_snapshot_id
    ):
        return _reuse_committed_capture(
            conn,
            project_id=project,
            operation_id=namespaced_operation,
            source_snapshot_id=existing.source_snapshot_id,
            content_sha256=content_sha256,
            source_kind=kind,
            source_locator=locator,
        )
    if existing is not None and existing.status == "committed":
        # Committed with no snapshot is an inconsistent operational state; never
        # repair it silently by appending a second capture under the same id.
        raise EvidenceSourceCaptureConflict(
            "capture operation is marked committed but carries no snapshot; "
            "refusing to append a second capture under the same operation id"
        )

    # Storage precedes the database: on return the bytes are durable and their
    # digest has been re-verified from the final path.
    storage_ref = source_storage.persist_source_bytes(
        project_id=project, content=content, content_sha256=content_sha256
    )

    with _capture_write(conn):
        operation = repo.create_or_get_ingest_operation(
            conn, project_id=project, operation_id=namespaced_operation,
        )
        # Authoritative recheck. Between the probe above and this insert another
        # transaction may have committed the same operation id; the unique
        # constraint uq_ingest_operation_project_op makes that visible here.
        if (
            operation.existed
            and operation.status == "committed"
            and operation.source_snapshot_id
        ):
            return _reuse_committed_capture(
                conn,
                project_id=project,
                operation_id=namespaced_operation,
                source_snapshot_id=operation.source_snapshot_id,
                content_sha256=content_sha256,
                source_kind=kind,
                source_locator=locator,
            )
        if operation.existed and operation.status == "committed":
            raise EvidenceSourceCaptureConflict(
                "capture operation is marked committed but carries no snapshot; "
                "refusing to append a second capture under the same operation id"
            )

        blob_id = repo.insert_or_get_blob(
            conn,
            project_id=project,
            content_hash=content_sha256,
            byte_size=len(content),
            hash_algorithm=HASH_ALGORITHM,
            created_by=actor,
        )
        snapshot_id = repo.insert_snapshot(
            conn,
            source_blob_id=blob_id,
            project_id=project,
            storage_ref=storage_ref,
            source_kind=kind,
            source_locator=locator,
            ingest_operation_id=namespaced_operation,
            captured_by=actor,
        )
        repo.set_ingest_status(
            conn,
            operation_pk=operation.id,
            status="committed",
            source_snapshot_id=snapshot_id,
        )

    # Final durability assertion on the caller's behalf: never return a snapshot
    # id whose bytes are not verifiably present at the referenced location.
    if not source_storage.stored_bytes_present(
        storage_ref, expected_sha256=content_sha256
    ):  # pragma: no cover - persist_source_bytes already verified this
        raise EvidenceSourceCaptureStorageMissing(
            "captured source bytes are no longer verifiable at their storage "
            "reference; refusing to report a durable capture"
        )

    return {
        "source_snapshot_id": snapshot_id,
        "source_blob_id": blob_id,
        "operation_id": namespaced_operation,
        "content_sha256": content_sha256,
        "byte_size": len(content),
        "source_kind": kind,
        "source_locator": locator,
        "reused": False,
        "bytes_persisted": True,
    }
