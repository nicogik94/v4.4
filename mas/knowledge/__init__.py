"""Knowledge-layer foundation for tranche 3A."""

from .freshness import build_knowledge_health, refresh_knowledge_items
from .files import (
    UploadStorageError,
    UploadStoreHealth,
    check_upload_store_writable,
    delete_project_uploads,
    delete_uploaded_file,
    describe_uploaded_file,
    get_uploaded_file_manifest,
    ingest_uploaded_file,
    list_uploaded_files,
)
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
    RetrievalPhaseImpactSummary,
    ProjectKnowledgeRetrievalSummary,
    build_phase_retrieval_impact,
    build_prompt_facing_retrieval_impact,
    build_project_retrieval_summary,
    evaluate_phase_retrieval,
    get_retrieval_policy,
)
from .sync import sync_multiple_sources, sync_offline_source

__all__ = [
    "DEFAULT_FRESHNESS_POLICY",
    "PhaseKnowledgeRetrievalView",
    "RetrievalPhaseImpactSummary",
    "ProjectKnowledgeRetrievalSummary",
    "build_phase_retrieval_impact",
    "build_prompt_facing_retrieval_impact",
    "ProjectedKnowledgeItem",
    "UploadStorageError",
    "UploadStoreHealth",
    "build_knowledge_health",
    "check_upload_store_writable",
    "build_project_retrieval_summary",
    "delete_project_uploads",
    "delete_uploaded_file",
    "describe_uploaded_file",
    "ensure_knowledge_layer",
    "evaluate_phase_retrieval",
    "get_freshness_policy",
    "get_uploaded_file_manifest",
    "get_retrieval_policy",
    "get_source_entry",
    "ingest_uploaded_file",
    "list_jobs",
    "list_sources",
    "list_uploaded_files",
    "project_knowledge_item",
    "refresh_knowledge_items",
    "sync_multiple_sources",
    "sync_offline_source",
    "upsert_source_entry",
]
