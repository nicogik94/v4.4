"""
v4 MAS — Persistent Project Store
Replaces the in-memory `projects: dict` in api.py with a PostgreSQL-backed store.
Uses the existing `projects` table from sql/init.sql plus a new `state_snapshots` table
that holds the full ProjectState as JSONB. Existing PostgreSQL rows fail closed
when a whole-state save would change W8.2-governed direct inputs without applying
the exact durable revision in the same transaction.

If DATABASE_URL is not set or asyncpg is unavailable, falls back to the in-memory dict
so local development still works. Production sets DATABASE_URL and persistence is automatic.
"""
import hashlib
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from decision_objects import ensure_decision_objects
from state import ProjectState
from state_coherence import (
    bind_effective_input,
    bootstrap_current_analysis,
    direct_input_projection,
    effective_input_identity,
    is_complete_analysis,
    primary_decision_id,
    schema_available,
    schema_available_conn,
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool = None
_mem: dict[str, ProjectState] = {}  # fallback + cache


class DirectInputAuthorityError(RuntimeError):
    """An ordinary durable save attempted to bypass W8.2 input authority."""


@dataclass(frozen=True)
class RevisionChange:
    """Exact durable revision application context; never a generic bypass."""

    revision_id: str
    applied_by: str


async def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    if not DATABASE_URL:
        return None
    try:
        import asyncpg
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        async with _pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS state_snapshots (
                    project_id UUID PRIMARY KEY,
                    state_json JSONB NOT NULL,
                    version INT NOT NULL DEFAULT 1,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        logger.info("Project store: PostgreSQL persistence active")
        return _pool
    except Exception as e:
        logger.warning(f"Project store: falling back to in-memory ({e})")
        return None


async def save(state: ProjectState) -> None:
    pool = await _get_pool()
    if pool is None:
        _mem[state.project_id] = state
        return
    async with pool.acquire() as conn:
        async with _transaction(conn):
            await save_conn(conn, state)
    _mem[state.project_id] = state


async def save_conn(
    conn,
    state: ProjectState,
    *,
    predecessor_snapshot_id: str | None = None,
    revision_change: RevisionChange | None = None,
) -> Any | None:
    """Persist state inside a caller-owned transaction.

    Callers own commit/rollback and must update the process cache only after a
    successful commit.  This is the composable durability boundary used by
    governed input revision application.
    """
    if revision_change is not None:
        transaction_probe = getattr(conn, "is_in_transaction", None)
        if callable(transaction_probe) and not transaction_probe():
            raise DirectInputAuthorityError(
                "authorized W8.2 revision persistence requires an explicit transaction"
            )
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"{state.project_id}|input-revision",
        )
    persisted = await load_conn(conn, state.project_id, for_update=True)
    authorized_revision = await _guard_direct_input_change(
        conn,
        persisted=persisted,
        incoming=state,
        predecessor_snapshot_id=predecessor_snapshot_id,
        revision_change=revision_change,
    )
    await conn.execute("""
        INSERT INTO projects (id, name, brief, data, current_phase, status, created_at, updated_at)
        VALUES ($1::uuid, $2, $3, $4, $5, 'active', $6::timestamptz, NOW())
        ON CONFLICT (id) DO UPDATE
        SET name          = EXCLUDED.name,
            brief         = EXCLUDED.brief,
            data          = EXCLUDED.data,
            current_phase = EXCLUDED.current_phase,
            updated_at    = NOW()
    """, state.project_id, state.project_name, state.brief,
         state.data, state.current_phase, state.created_at)
    coherence_ready = await schema_available_conn(conn)
    if DATABASE_URL and not coherence_ready:
        raise RuntimeError("v64 decision-state coherence migration is required")
    if coherence_ready:
        bound_snapshot = await bind_effective_input(
            conn,
            state,
            predecessor_snapshot_id=predecessor_snapshot_id,
            change_cause_id=(
                authorized_revision.revision_id if authorized_revision else None
            ),
        )
    else:
        bound_snapshot = None
    payload = state.model_dump(mode="json")
    await conn.execute("""
        INSERT INTO state_snapshots (project_id, state_json, version, updated_at)
        VALUES ($1::uuid, $2::jsonb, 1, NOW())
        ON CONFLICT (project_id) DO UPDATE
        SET state_json = EXCLUDED.state_json,
            version = state_snapshots.version + 1,
            updated_at = NOW()
    """, state.project_id, json.dumps(payload))
    if authorized_revision is None:
        return None
    if bound_snapshot is None:
        raise DirectInputAuthorityError(
            "v64 decision-state coherence is required for revision application"
        )
    row = await conn.fetchrow(
        """
        UPDATE input_revisions
        SET lifecycle_status='applied', applied_by=$3, applied_at=NOW(),
            resulting_snapshot_id=$4::uuid
        WHERE id=$1::uuid AND project_id=$2::uuid AND lifecycle_status='proposed'
        RETURNING *
        """,
        authorized_revision.revision_id,
        state.project_id,
        authorized_revision.applied_by,
        bound_snapshot.snapshot_id,
    )
    if row is None:
        raise DirectInputAuthorityError("W8.2 revision is no longer proposed")
    return row


async def _guard_direct_input_change(
    conn,
    *,
    persisted: ProjectState | None,
    incoming: ProjectState,
    predecessor_snapshot_id: str | None,
    revision_change: RevisionChange | None,
) -> RevisionChange | None:
    """Fail closed unless a durable proposed revision authorizes the delta."""
    if persisted is None:
        if revision_change is not None:
            raise DirectInputAuthorityError(
                "a revision change cause cannot authorize initial project persistence"
            )
        return None

    before = direct_input_projection(persisted)
    after = direct_input_projection(incoming)
    if before == after:
        if revision_change is not None:
            raise DirectInputAuthorityError(
                "a revision change cause requires an exact direct-input change"
            )
        return None

    if revision_change is None or not predecessor_snapshot_id:
        raise DirectInputAuthorityError(
            "governed direct inputs may change only through an authorized W8.2 revision"
        )
    actor = str(revision_change.applied_by or "").strip()
    if not actor or len(actor) > 200:
        raise DirectInputAuthorityError("invalid W8.2 revision application actor")
    try:
        revision_id = str(uuid.UUID(revision_change.revision_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DirectInputAuthorityError("invalid W8.2 revision change cause") from exc

    row = await conn.fetchrow(
        """
        SELECT id, project_id, decision_id, expected_base_snapshot_id,
               patch_json, patch_sha256, affected_field_paths, lifecycle_status
        FROM input_revisions
        WHERE id=$1::uuid
        FOR UPDATE
        """,
        revision_id,
    )
    if row is None:
        raise DirectInputAuthorityError("W8.2 revision change cause does not exist")

    decision_id = primary_decision_id(persisted)
    actual_base = effective_input_identity(persisted).snapshot_id
    if (
        str(row["project_id"]) != persisted.project_id
        or incoming.project_id != persisted.project_id
        or row["decision_id"] != decision_id
        or primary_decision_id(incoming) != decision_id
        or row["lifecycle_status"] != "proposed"
        or str(row["expected_base_snapshot_id"]) != predecessor_snapshot_id
        or persisted.effective_input_snapshot_id != predecessor_snapshot_id
        or actual_base != predecessor_snapshot_id
    ):
        raise DirectInputAuthorityError(
            "W8.2 revision change cause does not match the persisted decision base"
        )

    patch = row["patch_json"]
    if isinstance(patch, str):
        patch = json.loads(patch)
    if not isinstance(patch, dict) or not patch:
        raise DirectInputAuthorityError("W8.2 revision change cause has an invalid patch")
    if set(patch) - set(before):
        raise DirectInputAuthorityError("W8.2 revision patch exceeds direct-input authority")

    canonical_patch = json.dumps(
        patch, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    expected_paths = [f"input.{field}" for field in sorted(patch)]
    intended = dict(before)
    intended.update(patch)
    if (
        hashlib.sha256(canonical_patch.encode("utf-8")).hexdigest()
        != row["patch_sha256"]
        or list(row["affected_field_paths"]) != expected_paths
        or intended != after
    ):
        raise DirectInputAuthorityError(
            "W8.2 revision change cause does not authorize the exact direct-input delta"
        )
    return RevisionChange(revision_id=revision_id, applied_by=actor)


async def load_conn(conn, project_id: str, *, for_update: bool = False) -> Optional[ProjectState]:
    suffix = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        "SELECT state_json FROM state_snapshots WHERE project_id = $1::uuid" + suffix,
        project_id,
    )
    if not row:
        return None
    raw = row["state_json"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    state = ProjectState.model_validate(raw)
    ensure_decision_objects(state, trigger="store.load_conn")
    return state


def cache_state(state: ProjectState) -> None:
    """Publish a committed state to the process-local read-through cache."""
    _mem[state.project_id] = state


async def load(project_id: str) -> Optional[ProjectState]:
    if project_id in _mem:
        ensure_decision_objects(_mem[project_id], trigger="store.load:mem")
        return _mem[project_id]
    pool = await _get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state_json FROM state_snapshots WHERE project_id = $1::uuid",
            project_id,
        )
    if not row:
        return None
    coherence_ready = await schema_available(pool)
    if DATABASE_URL and not coherence_ready:
        raise RuntimeError("v64 decision-state coherence migration is required")
    state = ProjectState.model_validate(json.loads(row["state_json"]))
    ensure_decision_objects(state, trigger="store.load:db")
    if (
        is_complete_analysis(state)
        and not state.analysis_generation_id
        and coherence_ready
    ):
        generation_id = await bootstrap_current_analysis(pool, state)
        if generation_id:
            state.analysis_generation_id = generation_id
            await save(state)
    _mem[project_id] = state
    return state


async def list_all() -> list[ProjectState]:
    pool = await _get_pool()
    if pool is None:
        states = list(_mem.values())
        for state in states:
            ensure_decision_objects(state, trigger="store.list_all:mem")
        return states
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT state_json FROM state_snapshots ORDER BY updated_at DESC LIMIT 200"
        )
    coherence_ready = await schema_available(pool)
    if DATABASE_URL and not coherence_ready:
        raise RuntimeError("v64 decision-state coherence migration is required")
    states = [ProjectState.model_validate(json.loads(r["state_json"])) for r in rows]
    for s in states:
        ensure_decision_objects(s, trigger="store.list_all:db")
        if (
            is_complete_analysis(s)
            and not s.analysis_generation_id
            and coherence_ready
        ):
            generation_id = await bootstrap_current_analysis(pool, s)
            if generation_id:
                s.analysis_generation_id = generation_id
                await save(s)
        _mem[s.project_id] = s
    return states


async def delete(project_id: str) -> bool:
    _mem.pop(project_id, None)
    pool = await _get_pool()
    if pool is None:
        return True
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM state_snapshots WHERE project_id = $1::uuid", project_id
        )
        result = await conn.execute(
            "DELETE FROM projects WHERE id = $1::uuid", project_id
        )
    return "DELETE 1" in result


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def _transaction(conn):
    """Use a real database transaction; retain compatibility with test fakes."""
    transaction_factory = getattr(conn, "transaction", None)
    if callable(transaction_factory):
        async with transaction_factory():
            yield
    else:
        yield
