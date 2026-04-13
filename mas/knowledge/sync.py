"""Manual offline sync helpers for tranche 3A knowledge sources."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from decision_objects import stable_object_id
from state import (
    KnowledgeItem,
    KnowledgeItemStatus,
    KnowledgeSyncJob,
    KnowledgeSyncJobStatus,
    ProjectState,
    Provenance,
)

from .freshness import evaluate_knowledge_item_status
from .registry import ensure_knowledge_layer, get_freshness_policy, get_source_entry


def sync_multiple_sources(
    state: ProjectState,
    source_payloads: list[dict[str, Any]],
    *,
    actor: str,
    requested_at: datetime | None = None,
) -> list[KnowledgeSyncJob]:
    jobs: list[KnowledgeSyncJob] = []
    for payload in source_payloads:
        jobs.append(
            sync_offline_source(
                state,
                payload.get("source_id", ""),
                payload.get("items", []),
                actor=actor,
                requested_at=requested_at,
            )
        )
    return jobs


def sync_offline_source(
    state: ProjectState,
    source_id: str,
    fixture_items: list[dict[str, Any]],
    *,
    actor: str,
    requested_at: datetime | None = None,
) -> KnowledgeSyncJob:
    requested_at = requested_at or datetime.now()
    requested_at_iso = requested_at.isoformat()
    layer = ensure_knowledge_layer(state)
    source = get_source_entry(state, source_id)
    if source is None:
        raise KeyError(source_id)

    job = KnowledgeSyncJob(
        job_id=stable_object_id("knowledge_job", state.project_id, source_id, requested_at_iso, actor),
        source_id=source_id,
        requested_at=requested_at_iso,
        requested_by=actor,
        status=KnowledgeSyncJobStatus.PENDING,
        mode="manual",
    )

    try:
        _validate_source_for_manual_sync(source)
        normalized_items = _normalize_fixture_items(
            state,
            source_id,
            fixture_items,
            actor=actor,
            captured_at=requested_at_iso,
        )
        existing_by_id = {
            item.item_id: item
            for item in layer.items
            if item.source_id == source_id
        }
        new_by_id = {item.item_id: item for item in normalized_items}

        job.item_count = len(normalized_items)
        job.inserted_count = sum(1 for item_id in new_by_id if item_id not in existing_by_id)
        job.updated_count = sum(1 for item_id in new_by_id if item_id in existing_by_id)
        job.removed_count = sum(1 for item_id in existing_by_id if item_id not in new_by_id)
        job.checksum_sha256 = _aggregate_checksum(normalized_items)
        job.status = KnowledgeSyncJobStatus.COMPLETED
        job.completed_at = datetime.now().isoformat()

        layer.items = [
            item for item in layer.items
            if item.source_id != source_id
        ] + normalized_items
        source.last_sync_at = job.completed_at
        source.last_success_at = job.completed_at
        source.last_error = ""
        source.last_checksum_sha256 = job.checksum_sha256
        layer.sync_state.status = "current"
        layer.sync_state.last_sync_at = job.completed_at
        layer.sync_state.last_success_at = job.completed_at
        layer.sync_state.last_error = ""
        layer.sync_state.last_job_id = job.job_id
    except Exception as exc:
        message = str(exc)
        job.status = KnowledgeSyncJobStatus.FAILED
        job.error = message
        job.completed_at = datetime.now().isoformat()
        source.last_sync_at = job.completed_at
        source.last_error = message
        layer.sync_state.status = "failed"
        layer.sync_state.last_sync_at = job.completed_at
        layer.sync_state.last_error = message
        layer.sync_state.last_job_id = job.job_id

    layer.sync_state.jobs.append(job)
    layer.sync_state.jobs = layer.sync_state.jobs[-50:]
    return job


def _validate_source_for_manual_sync(source) -> None:
    if not source.enabled:
        raise ValueError("Source is disabled")
    if source.connector_type != "offline_fixture" or source.source_kind != "offline_fixture":
        raise ValueError("Only offline_fixture sources are syncable in tranche 3A")
    if source.access_mode != "manual":
        raise ValueError("Only manual access_mode sources are syncable in tranche 3A")
    if (source.sensitivity or "").lower() not in {"public", "internal"}:
        raise ValueError("Sensitive sources fail closed in tranche 3A")


def _normalize_fixture_items(
    state: ProjectState,
    source_id: str,
    fixture_items: list[dict[str, Any]],
    *,
    actor: str,
    captured_at: str,
) -> list[KnowledgeItem]:
    source = get_source_entry(state, source_id)
    if source is None:
        raise ValueError(f"Unknown source: {source_id}")
    policy = get_freshness_policy(state, source.freshness_policy_id)
    items: list[KnowledgeItem] = []
    for raw in fixture_items:
        source_ref = str(raw.get("source_ref") or "").strip()
        if not source_ref:
            raise ValueError("Each knowledge fixture item must include source_ref")
        title = str(raw.get("title") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if not title and not summary:
            raise ValueError("Each knowledge fixture item must include title or summary")

        canonical = {
            "source_ref": source_ref,
            "title": title,
            "summary": summary,
            "structured_payload": raw.get("structured_payload") or {},
            "observed_at": str(raw.get("observed_at") or ""),
            "effective_at": str(raw.get("effective_at") or ""),
            "expires_at": str(raw.get("expires_at") or ""),
            "trust_tier": str(raw.get("trust_tier") or source.trust_tier or "operator_curated"),
            "sensitivity": str(raw.get("sensitivity") or source.sensitivity or "internal"),
        }
        checksum = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        item = KnowledgeItem(
            item_id=stable_object_id("knowledge", state.project_id, source_id, source_ref, checksum),
            source_id=source_id,
            source_ref=source_ref,
            title=title[:240],
            summary=summary[:2000],
            structured_payload=dict(raw.get("structured_payload") or {}),
            observed_at=str(raw.get("observed_at") or captured_at),
            captured_at=captured_at,
            effective_at=str(raw.get("effective_at") or ""),
            expires_at=str(raw.get("expires_at") or ""),
            checksum_sha256=checksum,
            provenance=Provenance(
                source_type="connector_import",
                source_ref=source_ref,
                captured_at=captured_at,
                captured_by=actor,
                connector="offline_fixture",
                external_uri=source_ref,
                checksum=checksum,
                notes="offline_fixture;untrusted_source=true",
            ),
            freshness_status=KnowledgeItemStatus.FRESH,
            trust_tier=canonical["trust_tier"],
            sensitivity=canonical["sensitivity"],
            untrusted_source=True,
            eligible_for_retrieval=False,
        )
        item.freshness_status = evaluate_knowledge_item_status(
            item,
            stale_after_hours=policy.stale_after_hours,
            expire_after_hours=policy.expire_after_hours,
            now=datetime.fromisoformat(captured_at),
        )
        items.append(item)
    items.sort(key=lambda item: (item.observed_at, item.item_id), reverse=True)
    return items


def _aggregate_checksum(items: list[KnowledgeItem]) -> str:
    material = "|".join(item.checksum_sha256 for item in items)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
