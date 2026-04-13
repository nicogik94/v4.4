"""Knowledge-layer registry helpers."""
from __future__ import annotations

from state import FreshnessPolicy, KnowledgeLayerState, ProjectState, SourceRegistryEntry


DEFAULT_FRESHNESS_POLICY = FreshnessPolicy(
    policy_id="default_offline",
    name="Offline fixture default",
    stale_after_hours=72,
    expire_after_hours=168,
    manual_review_required=False,
    allow_stale_read=True,
    notes="Default policy for offline/manual knowledge sync sources.",
)


def ensure_knowledge_layer(state: ProjectState) -> KnowledgeLayerState:
    if state.knowledge_layer is None:
        state.knowledge_layer = KnowledgeLayerState(
            freshness_policies=[DEFAULT_FRESHNESS_POLICY.model_copy(deep=True)],
        )
    elif not state.knowledge_layer.freshness_policies:
        state.knowledge_layer.freshness_policies = [DEFAULT_FRESHNESS_POLICY.model_copy(deep=True)]
    return state.knowledge_layer


def get_freshness_policy(state: ProjectState, policy_id: str) -> FreshnessPolicy:
    layer = ensure_knowledge_layer(state)
    requested = (policy_id or "").strip()
    for policy in layer.freshness_policies:
        if policy.policy_id == requested:
            return policy
    return layer.freshness_policies[0]


def get_source_entry(state: ProjectState, source_id: str) -> SourceRegistryEntry | None:
    layer = state.knowledge_layer
    if layer is None:
        return None
    target = (source_id or "").strip()
    for source in layer.sources:
        if source.source_id == target:
            return source
    return None


def list_sources(state: ProjectState) -> list[SourceRegistryEntry]:
    layer = state.knowledge_layer
    if layer is None:
        return []
    return list(layer.sources)


def list_jobs(state: ProjectState):
    layer = state.knowledge_layer
    if layer is None:
        return []
    return list(layer.sync_state.jobs)


def upsert_source_entry(state: ProjectState, source: SourceRegistryEntry) -> SourceRegistryEntry:
    layer = ensure_knowledge_layer(state)
    incoming = source.model_copy(deep=True)
    for index, existing in enumerate(layer.sources):
        if existing.source_id != incoming.source_id:
            continue
        if not incoming.last_sync_at:
            incoming.last_sync_at = existing.last_sync_at
        if not incoming.last_success_at:
            incoming.last_success_at = existing.last_success_at
        if not incoming.last_error:
            incoming.last_error = existing.last_error
        if not incoming.last_checksum_sha256:
            incoming.last_checksum_sha256 = existing.last_checksum_sha256
        layer.sources[index] = incoming
        return incoming

    layer.sources.append(incoming)
    layer.sources.sort(key=lambda item: (item.name.lower(), item.source_id))
    return incoming
