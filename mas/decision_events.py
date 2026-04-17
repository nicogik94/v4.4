"""v5 alpha — Decision Event Writer (Fases 1 + 3).

Append-only writer for the `decision_events` log, plus formal persistence
helpers for `approvals` and `policy_decisions`.

Contract (docs/v5-ALPHA-MODULE-BOUNDARY.md §3):
  - snapshot write is authoritative; event write is fail-soft
  - every exception is caught, logged at WARNING, returns None
  - callers MUST NOT branch on the return value for control flow
  - in-memory fallback is used when DATABASE_URL is unset, so tests
    and dev runs still exercise the append/read path end-to-end

Readers here are bounded utilities for Fase 2 endpoints; they do NOT
own presentation logic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

# In-memory fallback stores. Keyed by project_id.
_mem_events: dict[str, list[dict]] = {}
_mem_approvals: dict[str, list[dict]] = {}
_mem_policy_decisions: dict[str, list[dict]] = {}

# Last event per project, for prev_event_id chaining in both modes.
_last_event: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Event envelope dataclass (for documentation / typed construction)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionEventEnvelope:
    """Typed envelope matching docs/v5-ALPHA-EVENT-TAXONOMY.md §2."""
    project_id: str
    event_type: str
    actor_type: str = "system"
    actor_id: str = ""
    payload: dict = field(default_factory=dict)
    trace_id: str = ""
    phase: str = ""
    model_provider: str = ""
    model_name: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Hash chaining
# ═══════════════════════════════════════════════════════════════════════════

def _canonical_body(event: dict) -> str:
    """Canonical JSON for hashing. Stable key order, no whitespace."""
    body = {
        "event_id": event.get("event_id", ""),
        "project_id": event.get("project_id", ""),
        "event_type": event.get("event_type", ""),
        "event_time": event.get("event_time", ""),
        "actor_type": event.get("actor_type", ""),
        "actor_id": event.get("actor_id", ""),
        "payload": event.get("payload", {}) or {},
        "trace_id": event.get("trace_id", ""),
        "phase": event.get("phase", ""),
        "model_provider": event.get("model_provider", ""),
        "model_name": event.get("model_name", ""),
        "cost_usd": float(event.get("cost_usd", 0.0) or 0.0),
        "latency_ms": float(event.get("latency_ms", 0.0) or 0.0),
        "prev_event_id": event.get("prev_event_id") or "",
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(event: dict, prev_hash: str) -> str:
    return hashlib.sha256(
        (prev_hash + "|" + _canonical_body(event)).encode("utf-8")
    ).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Pool (shared with store.py where possible)
# ═══════════════════════════════════════════════════════════════════════════

async def _get_pool():
    """Return the shared store pool if available, else None (fallback to memory)."""
    try:
        from store import _get_pool as _store_pool
        return await _store_pool()
    except Exception as exc:
        logger.debug(f"decision_events: store pool unavailable ({exc})")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Previous-event lookup (for prev_event_id / chain continuity)
# ═══════════════════════════════════════════════════════════════════════════

async def _fetch_last_event(project_id: str) -> Optional[dict]:
    cached = _last_event.get(project_id)
    if cached is not None:
        return cached
    pool = await _get_pool()
    if pool is None:
        events = _mem_events.get(project_id) or []
        return events[-1] if events else None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT event_id, event_hash
                FROM decision_events
                WHERE project_id = $1::uuid
                ORDER BY event_time DESC, created_at DESC
                LIMIT 1
                """,
                project_id,
            )
        if not row:
            return None
        cached = {"event_id": str(row["event_id"]), "event_hash": row["event_hash"] or ""}
        _last_event[project_id] = cached
        return cached
    except Exception as exc:
        logger.warning(f"decision_events: last-event lookup failed ({exc})")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Core append
# ═══════════════════════════════════════════════════════════════════════════

async def append(
    project_id: str,
    event_type: str,
    *,
    actor_type: str = "system",
    actor_id: str = "",
    payload: Optional[dict] = None,
    trace_id: str = "",
    phase: str = "",
    model_provider: str = "",
    model_name: str = "",
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> Optional[dict]:
    """Append one event to the decision log. Fail-soft.

    Returns the stored event dict on success, None on failure.
    The caller MUST NOT use the return value for control flow — the
    snapshot write is authoritative.
    """
    if not project_id or not event_type:
        logger.warning("decision_events.append: missing project_id or event_type")
        return None

    prev = await _fetch_last_event(project_id)
    prev_event_id = prev["event_id"] if prev else None
    prev_hash = prev["event_hash"] if prev else ""

    now = datetime.now(timezone.utc)
    event = {
        "event_id": str(uuid.uuid4()),
        "project_id": project_id,
        "event_type": event_type,
        "event_time": now.isoformat(),
        "_event_time_dt": now,  # datetime object for asyncpg
        "actor_type": actor_type or "system",
        "actor_id": actor_id or "",
        "payload": payload or {},
        "trace_id": trace_id or "",
        "phase": phase or "",
        "model_provider": model_provider or "",
        "model_name": model_name or "",
        "cost_usd": float(cost_usd or 0.0),
        "latency_ms": float(latency_ms or 0.0),
        "prev_event_id": prev_event_id,
    }
    event["event_hash"] = _compute_hash(event, prev_hash)
    event_dt = event.pop("_event_time_dt")  # extract for asyncpg, remove from dict

    pool = await _get_pool()
    if pool is None:
        _mem_events.setdefault(project_id, []).append(event)
        _last_event[project_id] = {"event_id": event["event_id"], "event_hash": event["event_hash"]}
        return event

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO decision_events (
                    event_id, project_id, event_type, event_time,
                    actor_type, actor_id, payload, trace_id, phase,
                    model_provider, model_name, cost_usd, latency_ms,
                    prev_event_id, event_hash
                ) VALUES (
                    $1::uuid, $2::uuid, $3, $4::timestamptz,
                    $5, $6, $7::jsonb, $8, $9,
                    $10, $11, $12, $13,
                    $14, $15
                )
                """,
                event["event_id"], project_id, event_type, event_dt,
                event["actor_type"], event["actor_id"],
                json.dumps(event["payload"]),
                event["trace_id"], event["phase"],
                event["model_provider"], event["model_name"],
                event["cost_usd"], event["latency_ms"],
                prev_event_id, event["event_hash"],
            )
        _last_event[project_id] = {"event_id": event["event_id"], "event_hash": event["event_hash"]}
        return event
    except Exception as exc:
        logger.warning(f"decision_events.append: DB write failed, falling back to memory ({exc})")
        _mem_events.setdefault(project_id, []).append(event)
        _last_event[project_id] = {"event_id": event["event_id"], "event_hash": event["event_hash"]}
        return event


# ═══════════════════════════════════════════════════════════════════════════
# Readers for Fase 2 (timeline, events, state-at, diff)
# ═══════════════════════════════════════════════════════════════════════════

async def list_events(
    project_id: str,
    *,
    event_type: Optional[str] = None,
    phase: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """Return events for a project, oldest-first. Bounded by `limit`."""
    pool = await _get_pool()
    if pool is None:
        events = list(_mem_events.get(project_id) or [])
        if event_type:
            events = [e for e in events if e["event_type"] == event_type]
        if phase:
            events = [e for e in events if e.get("phase") == phase]
        return events[:limit]

    try:
        clauses = ["project_id = $1::uuid"]
        args: list[Any] = [project_id]
        if event_type:
            args.append(event_type)
            clauses.append(f"event_type = ${len(args)}")
        if phase:
            args.append(phase)
            clauses.append(f"phase = ${len(args)}")
        args.append(int(limit))
        where = " AND ".join(clauses)
        query = (
            "SELECT event_id, project_id, event_type, event_time, "
            "actor_type, actor_id, payload, trace_id, phase, "
            "model_provider, model_name, cost_usd, latency_ms, "
            "prev_event_id, event_hash "
            f"FROM decision_events WHERE {where} "
            f"ORDER BY event_time ASC, created_at ASC LIMIT ${len(args)}"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [_row_to_event(r) for r in rows]
    except Exception as exc:
        logger.warning(f"decision_events.list_events: {exc}")
        return list(_mem_events.get(project_id) or [])[:limit]


def _row_to_event(row) -> dict:
    d = dict(row)
    for key in ("event_id", "prev_event_id", "project_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    payload = d.get("payload")
    if isinstance(payload, str):
        try:
            d["payload"] = json.loads(payload)
        except Exception:
            d["payload"] = {}
    event_time = d.get("event_time")
    if isinstance(event_time, datetime):
        d["event_time"] = event_time.isoformat()
    return d


async def build_timeline(project_id: str, *, limit: int = 500) -> list[dict]:
    """Compact timeline: one row per event with only the fields an operator
    needs to scan the history quickly.
    """
    events = await list_events(project_id, limit=limit)
    return [
        {
            "event_id": e["event_id"],
            "event_type": e["event_type"],
            "event_time": e["event_time"],
            "actor_type": e.get("actor_type", ""),
            "actor_id": e.get("actor_id", ""),
            "phase": e.get("phase", ""),
            "trace_id": e.get("trace_id", ""),
            "cost_usd": e.get("cost_usd", 0.0),
            "latency_ms": e.get("latency_ms", 0.0),
            "payload_keys": sorted(list((e.get("payload") or {}).keys())),
        }
        for e in events
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Replay-lite: project a bounded "state at event N" from the snapshot + events
# ═══════════════════════════════════════════════════════════════════════════

def _apply_event_to_projection(projection: dict, event: dict) -> None:
    """Apply one event to the bounded projection.

    Only fields we model cleanly from events are projected here. For anything
    else, the caller should fall back to the snapshot.
    """
    etype = event.get("event_type", "")
    payload = event.get("payload") or {}

    if etype == "project.created":
        projection["project_type"] = payload.get("project_type", projection.get("project_type"))
        projection["project_name"] = payload.get("project_name", projection.get("project_name"))
        if "risk_classification" in payload:
            projection["risk_classification"] = payload["risk_classification"]
    elif etype == "phase.started":
        phase = payload.get("phase") or event.get("phase") or ""
        if phase:
            projection.setdefault("phase_status", {})[phase] = "running"
            projection["current_phase"] = phase
    elif etype == "phase.completed":
        phase = payload.get("phase") or event.get("phase") or ""
        if phase:
            projection.setdefault("phase_status", {})[phase] = "completed"
            if "confidence" in payload:
                projection.setdefault("phase_confidence", {})[phase] = payload["confidence"]
    elif etype == "phase.failed":
        phase = payload.get("phase") or event.get("phase") or ""
        if phase:
            projection.setdefault("phase_status", {})[phase] = "failed"
    elif etype == "policy.kill_switch_triggered":
        projection["kill_switch_active"] = True
        projection["kill_switch_reason"] = payload.get("reason", "")
        projection["kill_switch_triggered_by"] = payload.get("triggered_by", "")
    elif etype == "policy.risk_classification_set":
        projection["risk_classification"] = payload.get("classification", projection.get("risk_classification"))
        projection["risk_classification_rationale"] = payload.get("rationale", "")
        projection["risk_classification_set_by"] = payload.get("set_by", "")
    elif etype == "policy.approval_granted":
        action = payload.get("action", "")
        if action:
            approvals = projection.setdefault("approvals_granted", {})
            approvals[action] = {
                "approved_by": payload.get("approved_by", ""),
                "rationale": payload.get("rationale", ""),
                "granted_at": event.get("event_time", ""),
            }
    elif etype == "policy.budget_caps_updated":
        updates = payload.get("updates") or {}
        caps = projection.setdefault("budget_caps", {})
        for key, value in updates.items():
            if value is not None:
                caps[key] = value
    elif etype == "outcome.recorded":
        # Outcomes are stored in a separate table; the projection tracks
        # a compact count for visibility.
        projection["outcome_events_count"] = int(projection.get("outcome_events_count", 0)) + 1


REPLAY_LITE_FIELDS = (
    "project_id", "project_name", "project_type", "current_phase",
    "phase_status", "phase_confidence",
    "kill_switch_active", "kill_switch_reason", "kill_switch_triggered_by",
    "risk_classification", "risk_classification_rationale", "risk_classification_set_by",
    "approvals_granted", "budget_caps", "outcome_events_count",
)


async def state_at(
    project_id: str,
    *,
    event_id: Optional[str] = None,
    at_time: Optional[str] = None,
    limit: int = 500,
) -> dict:
    """Replay-lite: return the bounded projection of project state at a given
    event (by event_id) or timestamp (ISO-8601). Only fields the event log
    models are included; callers needing other fields must fall back to the
    snapshot. If no boundary is given, returns the projection after ALL events.
    """
    events = await list_events(project_id, limit=limit)

    if event_id:
        truncated: list[dict] = []
        for e in events:
            truncated.append(e)
            if e["event_id"] == event_id:
                break
        events = truncated
    elif at_time:
        events = [e for e in events if e["event_time"] <= at_time]

    projection: dict = {"project_id": project_id}
    for event in events:
        _apply_event_to_projection(projection, event)

    # Carry envelope metadata for the latest applied event
    if events:
        last = events[-1]
        projection["_as_of_event_id"] = last["event_id"]
        projection["_as_of_event_time"] = last["event_time"]
        projection["_events_applied"] = len(events)
    else:
        projection["_events_applied"] = 0

    # Only surface the fields we model cleanly.
    return {k: projection[k] for k in projection if k in REPLAY_LITE_FIELDS or k.startswith("_")}


async def diff_states(
    project_id: str,
    *,
    from_event_id: Optional[str] = None,
    to_event_id: Optional[str] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    limit: int = 500,
) -> dict:
    """Diff the replay-lite projection at two points.

    Returns {added, removed, changed, events_between}. `events_between` is a
    compact summary of every event that contributed to the diff so the caller
    can answer "what changed AND when."
    """
    before = await state_at(project_id, event_id=from_event_id, at_time=from_time, limit=limit)
    after = await state_at(project_id, event_id=to_event_id, at_time=to_time, limit=limit)

    before_keys = {k for k in before if not k.startswith("_")}
    after_keys = {k for k in after if not k.startswith("_")}

    added = {k: after[k] for k in after_keys - before_keys}
    removed = {k: before[k] for k in before_keys - after_keys}
    changed: dict[str, dict] = {}
    for k in before_keys & after_keys:
        if before[k] != after[k]:
            changed[k] = {"before": before[k], "after": after[k]}

    # Pull every event strictly after `before` cutoff and up to `after` cutoff
    all_events = await list_events(project_id, limit=limit)
    before_cutoff_time = before.get("_as_of_event_time")
    after_cutoff_time = after.get("_as_of_event_time")

    def _between(e: dict) -> bool:
        t = e["event_time"]
        if before_cutoff_time and t <= before_cutoff_time:
            return False
        if after_cutoff_time and t > after_cutoff_time:
            return False
        return True

    events_between = [
        {
            "event_id": e["event_id"],
            "event_type": e["event_type"],
            "event_time": e["event_time"],
            "phase": e.get("phase", ""),
            "actor_type": e.get("actor_type", ""),
            "actor_id": e.get("actor_id", ""),
        }
        for e in all_events if _between(e)
    ]

    return {
        "project_id": project_id,
        "from": {
            "event_id": before.get("_as_of_event_id"),
            "event_time": before_cutoff_time,
            "events_applied": before.get("_events_applied", 0),
        },
        "to": {
            "event_id": after.get("_as_of_event_id"),
            "event_time": after_cutoff_time,
            "events_applied": after.get("_events_applied", 0),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "events_between": events_between,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Fase 3 — formal approvals / policy decisions persistence
# ═══════════════════════════════════════════════════════════════════════════

async def record_approval(
    project_id: str,
    *,
    action: str,
    decision: str = "granted",
    approved_by: str = "",
    rationale: str = "",
    policy_context: Optional[dict] = None,
    event_id: Optional[str] = None,
) -> Optional[dict]:
    """Append a formal approval row. Fail-soft.

    The caller is responsible for also writing the compatibility record in
    ProjectState.approvals_granted (kept for v4.5 clients).
    """
    if not project_id or not action:
        logger.warning("decision_events.record_approval: missing project_id/action")
        return None

    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "action": action,
        "decision": decision,
        "approved_by": approved_by or "",
        "rationale": rationale or "",
        "policy_context": policy_context or {},
        "event_id": event_id,
        "decided_at": now.isoformat(),
    }

    pool = await _get_pool()
    if pool is None:
        _mem_approvals.setdefault(project_id, []).append(row)
        return row
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO approvals (
                    id, project_id, action, decision, approved_by,
                    rationale, policy_context, event_id, decided_at
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb, $8::uuid, $9::timestamptz)
                """,
                row["id"], project_id, action, decision, approved_by,
                rationale, json.dumps(row["policy_context"]),
                event_id,
                now,
            )
        return row
    except Exception as exc:
        logger.warning(f"decision_events.record_approval: DB write failed ({exc})")
        _mem_approvals.setdefault(project_id, []).append(row)
        return row


async def list_approvals(project_id: str, *, action: Optional[str] = None) -> list[dict]:
    pool = await _get_pool()
    if pool is None:
        rows = list(_mem_approvals.get(project_id) or [])
        if action:
            rows = [r for r in rows if r["action"] == action]
        return rows
    try:
        query = (
            "SELECT id, project_id, action, decision, approved_by, "
            "rationale, policy_context, event_id, decided_at "
            "FROM approvals WHERE project_id = $1::uuid"
        )
        args: list[Any] = [project_id]
        if action:
            query += " AND action = $2"
            args.append(action)
        query += " ORDER BY decided_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [_approval_row(r) for r in rows]
    except Exception as exc:
        logger.warning(f"decision_events.list_approvals: {exc}")
        return list(_mem_approvals.get(project_id) or [])


def _approval_row(row) -> dict:
    d = dict(row)
    for key in ("id", "project_id", "event_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    ctx = d.get("policy_context")
    if isinstance(ctx, str):
        try:
            d["policy_context"] = json.loads(ctx)
        except Exception:
            d["policy_context"] = {}
    decided = d.get("decided_at")
    if isinstance(decided, datetime):
        d["decided_at"] = decided.isoformat()
    return d


async def record_policy_decision(
    project_id: str,
    *,
    decision_type: str,
    decided_by: str = "",
    rationale: str = "",
    context: Optional[dict] = None,
    event_id: Optional[str] = None,
) -> Optional[dict]:
    """Append a formal policy decision row. Fail-soft.

    Complements the embedded ProjectState.policy_audit_log for compliance.
    """
    if not project_id or not decision_type:
        logger.warning("decision_events.record_policy_decision: missing project_id/decision_type")
        return None

    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "decision_type": decision_type,
        "decided_by": decided_by or "",
        "rationale": rationale or "",
        "context": context or {},
        "event_id": event_id,
        "decided_at": now.isoformat(),
    }

    pool = await _get_pool()
    if pool is None:
        _mem_policy_decisions.setdefault(project_id, []).append(row)
        return row
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO policy_decisions (
                    id, project_id, decision_type, decided_by,
                    rationale, context, event_id, decided_at
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7::uuid, $8::timestamptz)
                """,
                row["id"], project_id, decision_type, decided_by,
                rationale, json.dumps(row["context"]),
                event_id,
                now,
            )
        return row
    except Exception as exc:
        logger.warning(f"decision_events.record_policy_decision: DB write failed ({exc})")
        _mem_policy_decisions.setdefault(project_id, []).append(row)
        return row


async def list_policy_decisions(project_id: str, *, decision_type: Optional[str] = None) -> list[dict]:
    pool = await _get_pool()
    if pool is None:
        rows = list(_mem_policy_decisions.get(project_id) or [])
        if decision_type:
            rows = [r for r in rows if r["decision_type"] == decision_type]
        return rows
    try:
        query = (
            "SELECT id, project_id, decision_type, decided_by, "
            "rationale, context, event_id, decided_at "
            "FROM policy_decisions WHERE project_id = $1::uuid"
        )
        args: list[Any] = [project_id]
        if decision_type:
            query += " AND decision_type = $2"
            args.append(decision_type)
        query += " ORDER BY decided_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [_policy_row(r) for r in rows]
    except Exception as exc:
        logger.warning(f"decision_events.list_policy_decisions: {exc}")
        return list(_mem_policy_decisions.get(project_id) or [])


def _policy_row(row) -> dict:
    d = dict(row)
    for key in ("id", "project_id", "event_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    ctx = d.get("context")
    if isinstance(ctx, str):
        try:
            d["context"] = json.loads(ctx)
        except Exception:
            d["context"] = {}
    decided = d.get("decided_at")
    if isinstance(decided, datetime):
        d["decided_at"] = decided.isoformat()
    return d


# ═══════════════════════════════════════════════════════════════════════════
# Test / ops helpers
# ═══════════════════════════════════════════════════════════════════════════

def _reset_memory_for_tests() -> None:
    """Clear the in-memory fallback. Tests only."""
    _mem_events.clear()
    _mem_approvals.clear()
    _mem_policy_decisions.clear()
    _last_event.clear()


def verify_chain(project_id: str) -> dict:
    """Recompute the hash chain against the stored values (in-memory only —
    intended for tests and in-process audit scans). Returns
    {'ok': bool, 'break_at': Optional[int]}.
    """
    events = list(_mem_events.get(project_id) or [])
    prev_hash = ""
    for i, event in enumerate(events):
        expected = _compute_hash(
            {**event, "event_hash": ""},  # hash excludes itself
            prev_hash,
        )
        if event["event_hash"] != expected:
            return {"ok": False, "break_at": i}
        prev_hash = event["event_hash"]
    return {"ok": True, "break_at": None}
