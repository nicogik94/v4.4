"""Immutable, content-addressed storage for evidence-only source bytes (R2.0A-4C).

``source_snapshot.storage_ref`` is a *truthful* durable reference: a committed
snapshot must never point at bytes that were never persisted. This module owns
that durability for the evidence-only ingress path, and nothing else.

Why a dedicated root instead of the Knowledge upload store
----------------------------------------------------------
``knowledge.files._store_upload_bytes`` writes into ``UPLOAD_LAYER.storage_dir``
and its references are consumed by Knowledge upload semantics (manifest,
registry entry, parsed items, the upload deletion path). Evidence-only capture
creates none of those. Reusing that root would make an evidence-only blob
indistinguishable from a Knowledge upload's backing file and would couple this
path to upload lifecycle behaviour it does not participate in. The root is
therefore separate and explicitly configurable
(``config.evidence_source_storage_dir``).

Guarantees
----------
* **Content-addressed** — the reference is derived from the project id and the
  SHA-256 of the bytes, so it is deterministic and collision-safe: the same bytes
  in the same project always resolve to the same reference.
* **Traversal-safe** — the project id and digest are validated against strict
  patterns and the resolved path is proven to stay inside the resolved root, so
  no caller-supplied value can escape the store.
* **Atomic** — bytes are written to a temporary file in the destination
  directory, flushed and ``fsync``-ed, then ``os.replace``-d into place, so a
  reader never observes a partially written artifact.
* **Verified** — the bytes are re-read from the final path and re-hashed after
  the write; a mismatch is a hard failure and the reference is not returned.
* **Immutable** — an existing reference is never overwritten. If the target
  already holds bytes with the expected digest the write is a verified no-op
  (which is what makes a capture retry safe). If it holds *different* bytes the
  operation fails: this module never replaces the content of an existing
  immutable reference.
* **Owner-only** — every reference this module returns names a file restricted to
  mode ``0600``, whether it was just written or was already present.

Orphaned bytes
--------------
This module deliberately ships no garbage collector. A stored artifact is
"referenced" exactly when some ``source_snapshot.storage_ref`` names it, and
referenced evidence bytes must never be deleted — a committed snapshot that
pointed at absent bytes would break the durability guarantee above. The bounded
operator procedure for identifying genuinely unreferenced artifacts is documented
in ``docs/v4.4-R2.0A-4C-EVIDENCE-ONLY-SOURCE-SNAPSHOT-INGRESS.md``;
:func:`stored_bytes_present` is the only diagnostic this module offers.

This module performs no database work, no Knowledge work, and no network access.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Optional

import config

HASH_ALGORITHM = "sha256"

# Read-back chunk size for post-write verification.
_VERIFY_CHUNK_BYTES = 1024 * 1024

# Strict shapes for every caller-supplied path component. A project id is a UUID
# and a digest is 64 lowercase hex characters; nothing else may become a path
# segment, so `..`, separators, NUL bytes, and absolute paths are all impossible.
_UUID_PATTERN = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")


class EvidenceSourceStorageError(RuntimeError):
    """Evidence-only source bytes could not be durably and verifiably stored.

    The message is bounded and never echoes source content. It may name the
    failing operation but not the private absolute storage path.
    """


class EvidenceSourceImmutabilityError(EvidenceSourceStorageError):
    """An existing immutable reference already holds different bytes.

    Content addressing makes this practically unreachable for genuine SHA-256
    inputs, so it signals a corrupted or tampered store rather than an ordinary
    collision. It is raised instead of overwriting.
    """


# Mode every stored artifact ends at. ``tempfile.mkstemp`` already creates the
# incoming file 0600 and ``os.replace`` preserves it, so a fresh write is
# restricted by construction; an artifact that was already present is brought to
# the same mode before it is accepted.
_STORED_FILE_MODE = 0o600


def content_digest(content: bytes) -> str:
    """SHA-256 of the exact bytes, lowercase hex."""
    return hashlib.new(HASH_ALGORITHM, content).hexdigest()


def is_canonical_uuid(value: str) -> bool:
    """True when ``value`` has the canonical 8-4-4-4-12 hexadecimal UUID shape.

    This is the ONLY project-id shape the store accepts as a path segment.
    Callers validating a project id before they reach a database (or this
    module) should use it rather than a parser of their own, so a project id can
    never be accepted upstream and then rejected here.
    """
    return bool(_UUID_PATTERN.match(value or ""))


def _storage_root() -> Path:
    """The configured store root, resolved.

    Resolution happens up front so the containment check below compares two
    fully resolved paths (a symlinked root is legitimate; a symlink *inside* the
    store that points outside it is not, and cannot be created by this module
    because it only ever creates directories and regular files).
    """
    configured = (config.evidence_source_storage_dir() or "").strip()
    if not configured:
        raise EvidenceSourceStorageError(
            "evidence-only source storage root is not configured"
        )
    root = Path(configured)
    if not root.is_absolute():
        raise EvidenceSourceStorageError(
            "evidence-only source storage root must be an absolute server-side path"
        )
    return root


def storage_reference(project_id: str, content_sha256: str) -> str:
    """Return the deterministic content-addressed reference for these bytes.

    ``<root>/<project_id>/sha256/<digest[:2]>/<digest>`` — project-scoped because
    v47 ``source_blob`` deduplicates content identity per project, so bytes are
    never shared across project boundaries by the store either. The two-character
    fan-out keeps directory sizes reasonable.
    """
    if not is_canonical_uuid(project_id):
        raise EvidenceSourceStorageError("project id must be a UUID")
    if not _SHA256_PATTERN.match(content_sha256 or ""):
        raise EvidenceSourceStorageError(
            "content digest must be 64 lowercase hexadecimal characters"
        )
    root = _storage_root()
    target = (
        root
        / project_id.lower()
        / HASH_ALGORITHM
        / content_sha256[:2]
        / content_sha256
    )
    # Containment proof. The components above are already pattern-restricted, so
    # this is belt-and-braces against a future change to the layout: the resolved
    # target must remain inside the resolved root.
    resolved_root = os.path.realpath(str(root))
    resolved_target = os.path.realpath(str(target))
    if (
        resolved_target != resolved_root
        and not resolved_target.startswith(resolved_root + os.sep)
    ):
        raise EvidenceSourceStorageError(
            "refusing a storage reference outside the configured store root"
        )
    return str(target)


def stored_bytes_present(storage_ref: str, *, expected_sha256: str) -> bool:
    """True when ``storage_ref`` holds a regular file whose digest matches.

    Used to verify durability before a snapshot is trusted (including on an
    idempotent retry, where the bytes must still be present and intact).
    """
    path = Path(storage_ref)
    try:
        if not path.is_file():
            return False
        return _digest_file(path) == expected_sha256
    except OSError:
        return False


def _restrict_stored_file_mode(path: Path) -> None:
    """Bring an accepted stored artifact to ``0600`` without touching its bytes.

    Only the permission bits change. A file that is already restricted is left
    alone (no needless syscall, and no spurious failure when the artifact is
    owned by another server-side identity but already private). A genuine
    failure to restrict is a bounded storage error rather than a silent accept:
    the store must not hand back a reference to evidence bytes it could not
    confine.
    """
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != _STORED_FILE_MODE:
            os.chmod(path, _STORED_FILE_MODE)
    except OSError as exc:
        raise EvidenceSourceStorageError(
            "stored source bytes could not be restricted to owner-only access"
        ) from exc


def _digest_file(path: Path) -> str:
    digest = hashlib.new(HASH_ALGORITHM)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_VERIFY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def persist_source_bytes(
    *,
    project_id: str,
    content: bytes,
    content_sha256: Optional[str] = None,
) -> str:
    """Durably store ``content`` and return its verified immutable reference.

    On return the bytes are on disk at the reference and have been re-read and
    re-hashed from that final path. Raises :class:`EvidenceSourceStorageError` on
    any failure — in which case callers must not persist a snapshot.
    """
    if not content:
        raise EvidenceSourceStorageError(
            "evidence-only capture requires genuine non-empty source bytes"
        )
    digest = content_sha256 or content_digest(content)
    if digest != content_digest(content):
        raise EvidenceSourceStorageError(
            "supplied content digest does not match the supplied bytes"
        )
    target = Path(storage_reference(project_id, digest))

    # Already present: verify rather than rewrite. This is the retry path.
    if target.exists():
        if not target.is_file():
            raise EvidenceSourceStorageError(
                "storage reference exists but is not a regular file"
            )
        try:
            existing = _digest_file(target)
        except OSError as exc:
            raise EvidenceSourceStorageError(
                "existing stored source bytes could not be read for verification"
            ) from exc
        if existing != digest:
            raise EvidenceSourceImmutabilityError(
                "storage reference already holds different bytes; refusing to "
                "overwrite an immutable evidence reference"
            )
        # Accepting an existing artifact must not accept weaker permissions than
        # this store writes itself.
        _restrict_stored_file_mode(target)
        return str(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvidenceSourceStorageError(
            "evidence-only source storage directory could not be created"
        ) from exc

    # Atomic publish: write + fsync a temporary file in the destination
    # directory, then rename it into place. A crash mid-write leaves the
    # temporary file, never a truncated artifact at the reference.
    handle = None
    temp_name = ""
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".incoming-", suffix=".tmp"
        )
        handle = os.fdopen(descriptor, "wb")
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp_name, str(target))
        temp_name = ""
    except OSError as exc:
        raise EvidenceSourceStorageError(
            "evidence-only source bytes could not be written durably"
        ) from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover - best effort
                pass
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover - best effort
                pass

    # fsync the containing directory so the rename itself is durable, not just
    # the file contents.
    try:
        directory_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:  # pragma: no cover - platform/filesystem dependent
        pass

    # Verify from the final path, not from the in-memory bytes.
    try:
        written = _digest_file(target)
    except OSError as exc:
        raise EvidenceSourceStorageError(
            "stored source bytes could not be re-read for verification"
        ) from exc
    if written != digest:
        raise EvidenceSourceStorageError(
            "stored source bytes failed post-write digest verification"
        )
    return str(target)
