"""Feature-gated R1.6 writes and separate read-only binding inputs."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import config

from . import binding_repository as repo
from .binding_models import (
    BindingReviewStatus,
    ConsumerDisposition,
    ResearchEvidenceConsumerInputBindingCreate,
    ResearchEvidenceConsumerInputBindingRecord,
)
from .claim_support_models import (
    EvidenceLinkage,
    LocatorResolution,
    SemanticRelationship,
)
from .freshness_models import DriftStatus, FreshnessStatus


class ResearchEvidenceBindingDisabled(RuntimeError):
    """Raised when consumer-input binding access is feature-disabled."""


class ResearchEvidenceBindingTransactionError(RuntimeError):
    """Raised when caller-owned binding atomicity cannot be preserved."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceBindingDisabled(
            "Research evidence binding is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _binding_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceBindingTransactionError(
            "research-evidence binding writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_binding_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_binding_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_binding_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_binding_write")


def record_consumer_input_binding(
    conn,
    binding: ResearchEvidenceConsumerInputBindingCreate,
) -> ResearchEvidenceConsumerInputBindingRecord:
    """Append or idempotently return one consumer-input evaluation."""
    binding = ResearchEvidenceConsumerInputBindingCreate.model_validate(
        binding.model_dump()
        if isinstance(binding, ResearchEvidenceConsumerInputBindingCreate)
        else binding
    )
    _require_enabled()
    with _binding_write(conn):
        existing = repo.get_binding_by_request_id(
            conn,
            project_id=binding.project_id,
            consumer_contract=binding.consumer_contract,
            binding_set_id=binding.binding_set_id,
            input_key=binding.input_key,
            request_id=binding.request_id,
        )
        if existing is not None:
            return repo.ensure_retry_matches(existing, binding)
        return repo.insert_binding(conn, binding)


def get_effective_consumer_input_binding(
    conn,
    *,
    project_id: str,
    consumer_contract: str,
    binding_set_id: str,
    input_key: str,
) -> Optional[ResearchEvidenceConsumerInputBindingRecord]:
    """Return latest history for one input without invoking its consumer."""
    _require_enabled()
    return repo.get_effective_binding(
        conn,
        project_id=project_id,
        consumer_contract=consumer_contract,
        binding_set_id=binding_set_id,
        input_key=input_key,
    )


def _effective(conn, **identity) -> Optional[ResearchEvidenceConsumerInputBindingRecord]:
    return get_effective_consumer_input_binding(conn, **identity)


def binding_availability_status(conn, **identity) -> Optional[bool]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.availability_status


def binding_retention_basis(conn, **identity) -> tuple[dict, ...]:
    binding = _effective(conn, **identity)
    return () if binding is None else binding.retention_basis


def binding_lineage_is_current(conn, **identity) -> Optional[bool]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.lineage_is_current


def binding_review_status(
    conn, **identity
) -> Optional[BindingReviewStatus]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.review_status


def binding_freshness_status(
    conn, **identity
) -> Optional[FreshnessStatus]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.freshness_status


def binding_drift_status(conn, **identity) -> Optional[DriftStatus]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.drift_status


def binding_locator_resolution(
    conn, **identity
) -> Optional[LocatorResolution]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.locator_resolution


def binding_evidence_linkage(
    conn, **identity
) -> Optional[EvidenceLinkage]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.evidence_linkage


def binding_semantic_relationship(
    conn, **identity
) -> Optional[SemanticRelationship]:
    binding = _effective(conn, **identity)
    return None if binding is None else binding.semantic_relationship


def binding_consumer_disposition(
    conn, **identity
) -> Optional[ConsumerDisposition]:
    """Return only the recorded input-contract disposition; authorize nothing."""
    binding = _effective(conn, **identity)
    return None if binding is None else binding.consumer_disposition
