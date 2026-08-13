"""Governed lifecycle for direct authoritative ProjectState input revisions.

Proposals are inert.  PostgreSQL application locks the exact decision scope and
commits the revision transition, ProjectState, and W8.1 snapshot lineage in one
transaction.  The in-memory fallback preserves local development behavior but
is explicitly reported as non-durable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

import store
from state import ProjectState, normalize_output_language, normalize_report_mode
from state_coherence import (
    effective_input_identity,
    is_legacy_effective_input_payload,
    primary_decision_id,
)


PROPOSED = "proposed"
APPLIED = "applied"
REJECTED = "rejected"
SCHEMA_VERSION = "input-revision.v1"


class InputRevisionError(RuntimeError):
    pass


class InputRevisionNotFound(InputRevisionError):
    pass


class InputRevisionValidationError(InputRevisionError):
    pass


class InputRevisionConflict(InputRevisionError):
    pass


class StaleInputRevision(InputRevisionConflict):
    pass


class InputRevisionSchemaRequired(InputRevisionError):
    pass


class DirectInputPatch(BaseModel):
    """The deliberately narrow W8.2 direct-input domain."""

    model_config = ConfigDict(extra="forbid")

    project_name: str | None = None
    brief: str | None = None
    data: str | None = None
    output_language: str | None = None
    report_mode: str | None = None
    observations: dict[str, str] | None = None
    timer_logs: list[dict[str, Any]] | None = None


class InputRevisionRecord(BaseModel):
    revision_id: str
    project_id: str
    decision_id: str
    expected_base_snapshot_id: str
    patch: dict[str, Any]
    patch_fingerprint: str
    affected_field_paths: list[str]
    rationale: str
    source_kind: str
    source_reference: str = ""
    proposed_by: str
    proposed_at: datetime
    status: str
    applied_by: str = ""
    applied_at: datetime | None = None
    rejected_by: str = ""
    rejected_at: datetime | None = None
    resulting_snapshot_id: str = ""
    rejection_rationale: str = ""
    stale: bool = False
    durable: bool = False


@dataclass(frozen=True)
class RevisionEffects:
    changed_fields: list[str]
    invalidated_phases: list[str]
    next_phase: str


@dataclass(frozen=True)
class InputRevisionApplication:
    revision: InputRevisionRecord
    state: ProjectState
    effects: RevisionEffects


StateTransform = Callable[
    [ProjectState, dict[str, Any], str], RevisionEffects | Awaitable[RevisionEffects]
]


_mem_revisions: dict[str, InputRevisionRecord] = {}
_mem_project_revisions: dict[str, list[str]] = {}
_mem_locks: dict[str, asyncio.Lock] = {}


def normalize_patch(
    patch: Mapping[str, Any],
    state: ProjectState,
    *,
    require_change: bool = True,
) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise InputRevisionValidationError("patch must be a JSON object")
    try:
        validated = DirectInputPatch.model_validate(dict(patch))
    except ValidationError as exc:
        raise InputRevisionValidationError(str(exc)) from exc
    updates = validated.model_dump(exclude_unset=True)
    if not updates:
        raise InputRevisionValidationError("patch must contain at least one supported field")
    if any(value is None for value in updates.values()):
        raise InputRevisionValidationError("revision fields cannot be null")
    try:
        if "output_language" in updates:
            updates["output_language"] = normalize_output_language(updates["output_language"])
        if "report_mode" in updates:
            updates["report_mode"] = normalize_report_mode(updates["report_mode"])
    except ValueError as exc:
        raise InputRevisionValidationError(str(exc)) from exc
    candidate = state.model_copy(deep=True)
    for field, value in updates.items():
        setattr(candidate, field, value)
    try:
        ProjectState.model_validate(candidate.model_dump(mode="python"))
    except ValidationError as exc:
        raise InputRevisionValidationError(str(exc)) from exc
    if require_change and _is_noop(state, updates):
        raise InputRevisionValidationError("revision patch is a deterministic no-op")
    return {field: updates[field] for field in sorted(updates)}


def patch_fingerprint(patch: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(patch)).encode("utf-8")).hexdigest()


async def propose_revision(
    project_id: str,
    patch: Mapping[str, Any],
    *,
    rationale: str,
    source_kind: str,
    source_reference: str = "",
    proposed_by: str = "operator",
    state: ProjectState | None = None,
) -> InputRevisionRecord:
    metadata = _normalize_metadata(rationale, source_kind, source_reference, proposed_by)
    pool = await _revision_pool()
    if pool is None:
        if state is None:
            raise InputRevisionNotFound("project not found")
        lock = _mem_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            normalized = normalize_patch(patch, state)
            record = _new_record(state, normalized, durable=False, **metadata)
            _store_mem(record)
            return record.model_copy(deep=True)

    committed_state: ProjectState | None = None
    record: InputRevisionRecord | None = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_schema(conn)
            await _lock_scope(conn, project_id)
            current = await store.load_conn(conn, project_id, for_update=True)
            if current is None:
                raise InputRevisionNotFound("project not found")
            await _prepare_bound_identity_conn(conn, current)
            normalized = normalize_patch(patch, current)
            record = await _insert_revision_conn(conn, current, normalized, **metadata)
            committed_state = current
    assert record is not None and committed_state is not None
    store.cache_state(committed_state)
    return record


async def create_and_apply_revision(
    project_id: str,
    patch: Mapping[str, Any],
    *,
    rationale: str,
    source_kind: str,
    source_reference: str = "",
    proposed_by: str = "operator",
    applied_by: str = "operator",
    transform: StateTransform,
    state: ProjectState | None = None,
    allow_noop: bool = False,
    expected_base_snapshot_id: str | None = None,
) -> InputRevisionApplication | None:
    metadata = _normalize_metadata(rationale, source_kind, source_reference, proposed_by)
    pool = await _revision_pool()
    if pool is None:
        if state is None:
            raise InputRevisionNotFound("project not found")
        lock = _mem_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            actual = effective_input_identity(state).snapshot_id
            if expected_base_snapshot_id and actual != expected_base_snapshot_id:
                raise StaleInputRevision(
                    "direct input patch expected base no longer matches current input"
                )
            normalized = normalize_patch(patch, state, require_change=not allow_noop)
            if _is_noop(state, normalized):
                return None
            record = _new_record(state, normalized, durable=False, **metadata)
            return await _apply_mem(record, state, applied_by, transform, persist_proposal=False)

    committed_state: ProjectState | None = None
    application: InputRevisionApplication | None = None
    no_change = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_schema(conn)
            await _lock_scope(conn, project_id)
            current = await store.load_conn(conn, project_id, for_update=True)
            if current is None:
                raise InputRevisionNotFound("project not found")
            await _prepare_bound_identity_conn(conn, current)
            actual = _require_bound_identity(current)
            if (
                expected_base_snapshot_id
                and actual.snapshot_id != expected_base_snapshot_id
            ):
                raise StaleInputRevision(
                    "direct input patch expected base no longer matches current input"
                )
            normalized = normalize_patch(
                patch, current, require_change=not allow_noop
            )
            if _is_noop(current, normalized):
                no_change = True
                committed_state = current
            else:
                record = await _insert_revision_conn(conn, current, normalized, **metadata)
                application = await _apply_conn(
                    conn, record, current, applied_by=applied_by, transform=transform
                )
                committed_state = application.state
    assert committed_state is not None
    store.cache_state(committed_state)
    if no_change:
        return None
    assert application is not None
    return application


async def apply_revision(
    project_id: str,
    revision_id: str,
    *,
    applied_by: str,
    transform: StateTransform,
    state: ProjectState | None = None,
) -> InputRevisionApplication:
    pool = await _revision_pool()
    if pool is None:
        if state is None:
            raise InputRevisionNotFound("project not found")
        lock = _mem_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            record = _mem_revisions.get(revision_id)
            if record is None or record.project_id != project_id:
                raise InputRevisionNotFound("input revision not found")
            return await _apply_mem(record, state, applied_by, transform, persist_proposal=True)

    application: InputRevisionApplication | None = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_schema(conn)
            await _lock_scope(conn, project_id)
            current = await store.load_conn(conn, project_id, for_update=True)
            if current is None:
                raise InputRevisionNotFound("project not found")
            row = await conn.fetchrow(
                "SELECT * FROM input_revisions WHERE id=$1::uuid AND project_id=$2::uuid FOR UPDATE",
                revision_id,
                project_id,
            )
            if row is None:
                raise InputRevisionNotFound("input revision not found")
            record = _row_to_record(row, durable=True)
            if record.status != PROPOSED:
                raise InputRevisionConflict(
                    f"input revision is already {record.status}; resulting_snapshot_id={record.resulting_snapshot_id or 'none'}"
                )
            application = await _apply_conn(
                conn, record, current, applied_by=applied_by, transform=transform
            )
    assert application is not None
    store.cache_state(application.state)
    return application


async def reject_revision(
    project_id: str,
    revision_id: str,
    *,
    rejected_by: str,
    rejection_rationale: str,
) -> InputRevisionRecord:
    actor = _required_text(rejected_by, "rejected_by", 200)
    rationale = _required_text(rejection_rationale, "rejection_rationale", 4000)
    pool = await _revision_pool()
    if pool is None:
        lock = _mem_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            record = _mem_revisions.get(revision_id)
            if record is None or record.project_id != project_id:
                raise InputRevisionNotFound("input revision not found")
            if record.status != PROPOSED:
                raise InputRevisionConflict(f"input revision is already {record.status}")
            rejected = record.model_copy(
                update={
                    "status": REJECTED,
                    "rejected_by": actor,
                    "rejected_at": datetime.now(timezone.utc),
                    "rejection_rationale": rationale,
                }
            )
            _mem_revisions[revision_id] = rejected
            return rejected.model_copy(deep=True)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_schema(conn)
            row = await conn.fetchrow(
                "SELECT * FROM input_revisions WHERE id=$1::uuid AND project_id=$2::uuid FOR UPDATE",
                revision_id,
                project_id,
            )
            if row is None:
                raise InputRevisionNotFound("input revision not found")
            record = _row_to_record(row, durable=True)
            if record.status != PROPOSED:
                raise InputRevisionConflict(f"input revision is already {record.status}")
            updated = await conn.fetchrow(
                """
                UPDATE input_revisions
                SET lifecycle_status='rejected', rejected_by=$3,
                    rejected_at=NOW(), rejection_rationale=$4
                WHERE id=$1::uuid AND project_id=$2::uuid
                RETURNING *
                """,
                revision_id,
                project_id,
                actor,
                rationale,
            )
            return _row_to_record(updated, durable=True)


async def get_revision(
    project_id: str, revision_id: str, *, state: ProjectState | None = None
) -> InputRevisionRecord:
    pool = await _revision_pool()
    if pool is None:
        record = _mem_revisions.get(revision_id)
        if record is None or record.project_id != project_id:
            raise InputRevisionNotFound("input revision not found")
        return _with_stale(record, state)
    async with pool.acquire() as conn:
        await _require_schema(conn)
        row = await conn.fetchrow(
            "SELECT * FROM input_revisions WHERE id=$1::uuid AND project_id=$2::uuid",
            revision_id,
            project_id,
        )
        if row is None:
            raise InputRevisionNotFound("input revision not found")
        current = await store.load_conn(conn, project_id)
        return _with_stale(_row_to_record(row, durable=True), current)


async def list_revisions(
    project_id: str, *, state: ProjectState | None = None
) -> list[InputRevisionRecord]:
    pool = await _revision_pool()
    if pool is None:
        return [
            _with_stale(_mem_revisions[item], state)
            for item in _mem_project_revisions.get(project_id, [])
        ]
    async with pool.acquire() as conn:
        await _require_schema(conn)
        rows = await conn.fetch(
            "SELECT * FROM input_revisions WHERE project_id=$1::uuid ORDER BY proposed_at, id",
            project_id,
        )
        current = await store.load_conn(conn, project_id)
        return [_with_stale(_row_to_record(row, durable=True), current) for row in rows]


async def _apply_conn(
    conn,
    record: InputRevisionRecord,
    current: ProjectState,
    *,
    applied_by: str,
    transform: StateTransform,
) -> InputRevisionApplication:
    actor = _required_text(applied_by, "applied_by", 200)
    actual = _require_bound_identity(current)
    if actual.snapshot_id != record.expected_base_snapshot_id:
        raise StaleInputRevision("input revision expected base no longer matches current input")
    normalized = normalize_patch(record.patch, current)
    if patch_fingerprint(normalized) != record.patch_fingerprint:
        raise InputRevisionConflict("stored revision patch fingerprint mismatch")
    updated = current.model_copy(deep=True)
    effects = transform(updated, normalized, actor)
    if hasattr(effects, "__await__"):
        effects = await effects
    if not isinstance(effects, RevisionEffects):
        raise InputRevisionValidationError("revision transform returned invalid effects")
    updated = ProjectState.model_validate(updated.model_dump(mode="python"))
    resulting = effective_input_identity(updated)
    if resulting.snapshot_id == actual.snapshot_id:
        raise InputRevisionValidationError("revision application would not change effective input")
    row = await store.save_conn(
        conn,
        updated,
        predecessor_snapshot_id=actual.snapshot_id,
        revision_change=store.RevisionChange(
            revision_id=record.revision_id,
            applied_by=actor,
        ),
    )
    await _application_fault_point()
    if row is None:
        raise InputRevisionConflict("input revision is no longer proposed")
    return InputRevisionApplication(
        revision=_row_to_record(row, durable=True), state=updated, effects=effects
    )


async def _apply_mem(
    record: InputRevisionRecord,
    current: ProjectState,
    applied_by: str,
    transform: StateTransform,
    *,
    persist_proposal: bool,
) -> InputRevisionApplication:
    actor = _required_text(applied_by, "applied_by", 200)
    if record.status != PROPOSED:
        raise InputRevisionConflict(
            f"input revision is already {record.status}; resulting_snapshot_id={record.resulting_snapshot_id or 'none'}"
        )
    actual = effective_input_identity(current)
    if actual.snapshot_id != record.expected_base_snapshot_id:
        raise StaleInputRevision("input revision expected base no longer matches current input")
    normalized = normalize_patch(record.patch, current)
    if patch_fingerprint(normalized) != record.patch_fingerprint:
        raise InputRevisionConflict("stored revision patch fingerprint mismatch")
    updated = current.model_copy(deep=True)
    effects = transform(updated, normalized, actor)
    if hasattr(effects, "__await__"):
        effects = await effects
    if not isinstance(effects, RevisionEffects):
        raise InputRevisionValidationError("revision transform returned invalid effects")
    updated = ProjectState.model_validate(updated.model_dump(mode="python"))
    resulting = effective_input_identity(updated)
    if updated.effective_input_snapshot_id != resulting.snapshot_id:
        updated.analysis_generation_id = ""
    updated.effective_input_snapshot_id = resulting.snapshot_id
    prior = current.model_copy(deep=True)
    _replace_state(current, updated)
    try:
        await store.save(current)
    except Exception:
        _replace_state(current, prior)
        raise
    applied = record.model_copy(
        update={
            "status": APPLIED,
            "applied_by": actor,
            "applied_at": datetime.now(timezone.utc),
            "resulting_snapshot_id": resulting.snapshot_id,
        }
    )
    if persist_proposal:
        _mem_revisions[record.revision_id] = applied
    else:
        _store_mem(applied)
    return InputRevisionApplication(revision=applied, state=current, effects=effects)


async def _insert_revision_conn(
    conn,
    state: ProjectState,
    patch: dict[str, Any],
    *,
    rationale: str,
    source_kind: str,
    source_reference: str,
    proposed_by: str,
) -> InputRevisionRecord:
    identity = _require_bound_identity(state)
    revision_id = str(uuid.uuid4())
    row = await conn.fetchrow(
        """
        INSERT INTO input_revisions (
            id, project_id, decision_id, expected_base_snapshot_id,
            patch_json, patch_sha256, affected_field_paths, rationale,
            source_kind, source_reference, proposed_by
        ) VALUES (
            $1::uuid, $2::uuid, $3, $4::uuid,
            $5::jsonb, $6, $7::text[], $8, $9, NULLIF($10, ''), $11
        ) RETURNING *
        """,
        revision_id,
        state.project_id,
        identity.decision_id,
        identity.snapshot_id,
        _canonical_json(patch),
        patch_fingerprint(patch),
        [f"input.{field}" for field in patch],
        rationale,
        source_kind,
        source_reference,
        proposed_by,
    )
    return _row_to_record(row, durable=True)


def _new_record(
    state: ProjectState,
    patch: dict[str, Any],
    *,
    rationale: str,
    source_kind: str,
    source_reference: str,
    proposed_by: str,
    durable: bool,
) -> InputRevisionRecord:
    identity = effective_input_identity(state)
    return InputRevisionRecord(
        revision_id=str(uuid.uuid4()),
        project_id=state.project_id,
        decision_id=identity.decision_id,
        expected_base_snapshot_id=identity.snapshot_id,
        patch=patch,
        patch_fingerprint=patch_fingerprint(patch),
        affected_field_paths=[f"input.{field}" for field in patch],
        rationale=rationale,
        source_kind=source_kind,
        source_reference=source_reference,
        proposed_by=proposed_by,
        proposed_at=datetime.now(timezone.utc),
        status=PROPOSED,
        durable=durable,
    )


def _row_to_record(row: Any, *, durable: bool) -> InputRevisionRecord:
    patch = row["patch_json"]
    if isinstance(patch, str):
        patch = json.loads(patch)
    return InputRevisionRecord(
        revision_id=str(row["id"]),
        project_id=str(row["project_id"]),
        decision_id=row["decision_id"],
        expected_base_snapshot_id=str(row["expected_base_snapshot_id"]),
        patch=dict(patch),
        patch_fingerprint=row["patch_sha256"],
        affected_field_paths=list(row["affected_field_paths"]),
        rationale=row["rationale"],
        source_kind=row["source_kind"],
        source_reference=row["source_reference"] or "",
        proposed_by=row["proposed_by"],
        proposed_at=row["proposed_at"],
        status=row["lifecycle_status"],
        applied_by=row["applied_by"] or "",
        applied_at=row["applied_at"],
        rejected_by=row["rejected_by"] or "",
        rejected_at=row["rejected_at"],
        resulting_snapshot_id=(str(row["resulting_snapshot_id"]) if row["resulting_snapshot_id"] else ""),
        rejection_rationale=row["rejection_rationale"] or "",
        durable=durable,
    )


def _with_stale(
    record: InputRevisionRecord, state: ProjectState | None
) -> InputRevisionRecord:
    stale = False
    if record.status == PROPOSED and state is not None:
        stale = effective_input_identity(state).snapshot_id != record.expected_base_snapshot_id
    return record.model_copy(update={"stale": stale}, deep=True)


def _store_mem(record: InputRevisionRecord) -> None:
    _mem_revisions[record.revision_id] = record
    _mem_project_revisions.setdefault(record.project_id, []).append(record.revision_id)


def _require_bound_identity(state: ProjectState):
    identity = effective_input_identity(state)
    if state.effective_input_snapshot_id != identity.snapshot_id:
        raise InputRevisionConflict(
            "ProjectState effective-input binding does not match current authoritative input"
        )
    if identity.decision_id != primary_decision_id(state):
        raise InputRevisionConflict("decision scope mismatch")
    return identity


async def _prepare_bound_identity_conn(conn, state: ProjectState):
    """Bind an unbound project or perform the exact W8.1 -> W8.2 rebind.

    A proposal may durably materialize the unchanged v2 base identity, but it
    never changes the authoritative input payload. Any other mismatch fails
    closed as possible state corruption or an unsupported contract transition.
    """
    identity = effective_input_identity(state)
    if state.effective_input_snapshot_id == identity.snapshot_id:
        return identity

    predecessor = state.effective_input_snapshot_id or None
    if predecessor is not None:
        row = await conn.fetchrow(
            """
            SELECT effective_input_json
            FROM decision_input_snapshots
            WHERE id=$1::uuid AND project_id=$2::uuid AND decision_id=$3
            """,
            predecessor,
            state.project_id,
            identity.decision_id,
        )
        if row is None:
            raise InputRevisionConflict(
                "ProjectState effective-input binding has no same-scope snapshot"
            )
        payload = row["effective_input_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not is_legacy_effective_input_payload(state, payload):
            raise InputRevisionConflict(
                "ProjectState effective-input binding does not match current authoritative input"
            )

    await store.save_conn(
        conn,
        state,
        predecessor_snapshot_id=predecessor,
    )
    return _require_bound_identity(state)


async def _require_schema(conn) -> None:
    row = await conn.fetchrow(
        """
        SELECT to_regclass('input_revisions') IS NOT NULL AS revisions,
               to_regclass('decision_input_snapshots') IS NOT NULL AS snapshots
        """
    )
    if not row or not row["revisions"] or not row["snapshots"]:
        raise InputRevisionSchemaRequired("v65 governed input revision migration is required")


async def _revision_pool():
    pool = await store._get_pool()
    if pool is None and store.DATABASE_URL:
        raise InputRevisionSchemaRequired(
            "durable PostgreSQL input-revision service is unavailable"
        )
    return pool


async def _lock_scope(conn, project_id: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        f"{project_id}|input-revision",
    )


async def _application_fault_point() -> None:
    """Test injection point inside, and immediately before, the single commit."""


def _normalize_metadata(
    rationale: str, source_kind: str, source_reference: str, proposed_by: str
) -> dict[str, str]:
    return {
        "rationale": _required_text(rationale, "rationale", 4000),
        "source_kind": _required_text(source_kind, "source_kind", 100),
        "source_reference": _optional_text(source_reference, "source_reference", 1000),
        "proposed_by": _required_text(proposed_by, "proposed_by", 200),
    }


def _required_text(value: str, field: str, maximum: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise InputRevisionValidationError(f"{field} is required")
    if len(clean) > maximum:
        raise InputRevisionValidationError(f"{field} exceeds {maximum} characters")
    return clean


def _optional_text(value: str, field: str, maximum: int) -> str:
    clean = str(value or "").strip()
    if len(clean) > maximum:
        raise InputRevisionValidationError(f"{field} exceeds {maximum} characters")
    return clean


def _is_noop(state: ProjectState, patch: Mapping[str, Any]) -> bool:
    return not any(getattr(state, field) != value for field, value in patch.items())


def _replace_state(target: ProjectState, source: ProjectState) -> None:
    for field in ProjectState.model_fields:
        setattr(target, field, getattr(source, field))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
