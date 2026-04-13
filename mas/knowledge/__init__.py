"""Knowledge-layer foundation for tranche 3A."""

from .freshness import build_knowledge_health, refresh_knowledge_items
from .projection import ProjectedKnowledgeItem, project_knowledge_item
from .registry import (
    DEFAULT_FRESHNESS_POLICY,
    ensure_knowledge_layer,
    get_freshness_policy,
    get_source_entry,
    list_jobs,
    list_sources,
    upsert_source_entry,
)
from .retrieval import (
    PhaseKnowledgeRetrievalView,
    ProjectKnowledgeRetrievalSummary,
    build_project_retrieval_summary,
    evaluate_phase_retrieval,
    get_retrieval_policy,
)
from .sync import sync_multiple_sources, sync_offline_source

__all__ = [
    "DEFAULT_FRESHNESS_POLICY",
    "PhaseKnowledgeRetrievalView",
    "ProjectKnowledgeRetrievalSummary",
    "ProjectedKnowledgeItem",
    "build_knowledge_health",
    "build_project_retrieval_summary",
    "ensure_knowledge_layer",
    "evaluate_phase_retrieval",
    "get_freshness_policy",
    "get_retrieval_policy",
    "get_source_entry",
    "list_jobs",
    "list_sources",
    "project_knowledge_item",
    "refresh_knowledge_items",
    "sync_multiple_sources",
    "sync_offline_source",
    "upsert_source_entry",
]
