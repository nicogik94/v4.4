"""
v4 MAS — Persistent Project Store
Replaces the in-memory `projects: dict` in api.py with a PostgreSQL-backed store.
Uses the existing `projects` table from sql/init.sql plus a new `state_snapshots` table
that holds the full ProjectState as JSONB. Last-write-wins, ACID via single-row upsert.

If DATABASE_URL is not set or asyncpg is unavailable, falls back to the in-memory dict
so local development still works. Production sets DATABASE_URL and persistence is automatic.
"""
import os
import json
import logging
from typing import Optional
from datetime import datetime

from state import ProjectState
from decision_objects import ensure_decision_objects

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool = None
_mem: dict[str, ProjectState] = {}  # fallback + cache


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
    _mem[state.project_id] = state
    pool = await _get_pool()
    if pool is None:
        return
    payload = state.model_dump(mode="json")
    async with pool.acquire() as conn:
        # Ensure the FK-parent row in projects exists so that outcomes,
        # decision_events, approvals, and policy_decisions INSERTs succeed.
        await conn.execute("""
            INSERT INTO projects (id, name, brief, data, current_phase, status, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, 'active', $6::timestamptz, NOW())
            ON CONFLICT (id) DO UPDATE
            SET name          = EXCLUDED.name,
                brief         = EXCLUDED.brief,
                current_phase = EXCLUDED.current_phase,
                updated_at    = NOW()
        """, state.project_id, state.project_name, state.brief,
             state.data, state.current_phase,
             state.created_at)
        await conn.execute("""
            INSERT INTO state_snapshots (project_id, state_json, version, updated_at)
            VALUES ($1::uuid, $2::jsonb, 1, NOW())
            ON CONFLICT (project_id) DO UPDATE
            SET state_json = EXCLUDED.state_json,
                version = state_snapshots.version + 1,
                updated_at = NOW()
        """, state.project_id, json.dumps(payload))


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
    state = ProjectState.model_validate(json.loads(row["state_json"]))
    ensure_decision_objects(state, trigger="store.load:db")
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
    states = [ProjectState.model_validate(json.loads(r["state_json"])) for r in rows]
    for s in states:
        ensure_decision_objects(s, trigger="store.list_all:db")
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
