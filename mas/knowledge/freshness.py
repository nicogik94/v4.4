"""Freshness evaluation for the additive knowledge layer."""
from __future__ import annotations

from datetime import datetime, timedelta

from state import KnowledgeItem, KnowledgeItemStatus, ProjectState

from .registry import ensure_knowledge_layer, get_freshness_policy


def refresh_knowledge_items(state: ProjectState, *, now: datetime | None = None) -> list[KnowledgeItem]:
    layer = state.knowledge_layer
    if layer is None:
        return []
    now = now or datetime.now()
    for item in layer.items:
        policy = get_freshness_policy(state, _source_policy_id(state, item.source_id))
        item.freshness_status = evaluate_knowledge_item_status(item, stale_after_hours=policy.stale_after_hours, expire_after_hours=policy.expire_after_hours, now=now)
    return list(layer.items)


def evaluate_knowledge_item_status(
    item: KnowledgeItem,
    *,
    stale_after_hours: int,
    expire_after_hours: int,
    now: datetime | None = None,
) -> KnowledgeItemStatus:
    if item.freshness_status == KnowledgeItemStatus.QUARANTINED:
        return KnowledgeItemStatus.QUARANTINED

    now = now or datetime.now()
    explicit_expiry = _parse_dt(item.expires_at)
    if explicit_expiry is not None and now >= explicit_expiry:
        return KnowledgeItemStatus.EXPIRED

    anchor = (
        _parse_dt(item.observed_at)
        or _parse_dt(item.effective_at)
        or _parse_dt(item.captured_at)
        or now
    )
    age = now - anchor
    if expire_after_hours > 0 and age >= timedelta(hours=expire_after_hours):
        return KnowledgeItemStatus.EXPIRED
    if stale_after_hours > 0 and age >= timedelta(hours=stale_after_hours):
        return KnowledgeItemStatus.STALE
    return KnowledgeItemStatus.FRESH


def build_knowledge_health(state: ProjectState, *, now: datetime | None = None) -> dict:
    layer = state.knowledge_layer
    if layer is None or not layer.sources:
        return {
            "status": "unconfigured",
            "message": "No knowledge sources configured.",
            "source_count": 0,
            "enabled_source_count": 0,
            "item_count": 0,
            "fresh_item_count": 0,
            "stale_item_count": 0,
            "expired_item_count": 0,
            "quarantined_item_count": 0,
            "due_source_count": 0,
            "failed_source_count": 0,
            "last_sync_at": "",
            "last_success_at": "",
        }

    now = now or datetime.now()
    refresh_knowledge_items(state, now=now)
    layer = ensure_knowledge_layer(state)

    enabled_sources = [source for source in layer.sources if source.enabled]
    item_status_counts = {
        "fresh": 0,
        "stale": 0,
        "expired": 0,
        "quarantined": 0,
    }
    for item in layer.items:
        key = item.freshness_status.value if hasattr(item.freshness_status, "value") else str(item.freshness_status)
        item_status_counts[key] = item_status_counts.get(key, 0) + 1

    due_source_count = 0
    failed_source_count = 0
    expired_source_count = 0
    stale_source_count = 0
    for source in enabled_sources:
        source_status = evaluate_source_status(
            state,
            source.source_id,
            now=now,
        )
        if source_status == "due":
            due_source_count += 1
        elif source_status == "failed":
            failed_source_count += 1
        elif source_status == "expired":
            expired_source_count += 1
        elif source_status == "stale":
            stale_source_count += 1

    if failed_source_count:
        status = "sync_failed"
        message = "One or more knowledge source syncs failed."
    elif expired_source_count or item_status_counts["expired"]:
        status = "expired"
        message = "One or more knowledge sources or items are expired."
    elif stale_source_count or due_source_count or item_status_counts["stale"]:
        status = "stale"
        message = "Knowledge sources need manual sync or review."
    else:
        status = "current"
        message = "Knowledge sources are current."

    return {
        "status": status,
        "message": message,
        "source_count": len(layer.sources),
        "enabled_source_count": len(enabled_sources),
        "item_count": len(layer.items),
        "fresh_item_count": item_status_counts["fresh"],
        "stale_item_count": item_status_counts["stale"],
        "expired_item_count": item_status_counts["expired"],
        "quarantined_item_count": item_status_counts["quarantined"],
        "due_source_count": due_source_count,
        "failed_source_count": failed_source_count,
        "last_sync_at": layer.sync_state.last_sync_at,
        "last_success_at": layer.sync_state.last_success_at,
    }


def evaluate_source_status(state: ProjectState, source_id: str, *, now: datetime | None = None) -> str:
    layer = state.knowledge_layer
    if layer is None:
        return "unconfigured"
    source = next((item for item in layer.sources if item.source_id == source_id), None)
    if source is None:
        return "missing"
    if not source.enabled:
        return "disabled"
    if source.last_error:
        return "failed"
    if not source.last_success_at:
        return "due"

    now = now or datetime.now()
    policy = get_freshness_policy(state, source.freshness_policy_id)
    last_success = _parse_dt(source.last_success_at)
    if last_success is None:
        return "due"
    age = now - last_success
    if policy.expire_after_hours > 0 and age >= timedelta(hours=policy.expire_after_hours):
        return "expired"
    if policy.stale_after_hours > 0 and age >= timedelta(hours=policy.stale_after_hours):
        return "stale"
    return "current"


def _source_policy_id(state: ProjectState, source_id: str) -> str:
    layer = state.knowledge_layer
    if layer is None:
        return "default_offline"
    for source in layer.sources:
        if source.source_id == source_id:
            return source.freshness_policy_id or "default_offline"
    return "default_offline"


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
