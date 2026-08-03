"""Shared builders for R3 Research Evidence provenance carry-through tests.

These assemble REAL ``ResearchEvidencePackAggregate`` values and project them
through the real A-3 entry, so every provenance test exercises production
construction rather than a hand-made stand-in. No database and no network.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.pack_models import (  # noqa: E402
    ResearchEvidencePackAggregate,
    ResearchEvidencePackAuthorizedClaim,
    ResearchEvidencePackAuthorizedEvidence,
    ResearchEvidencePackAuthorizedRelationship,
    ResearchEvidencePackAuthorizedSource,
    ResearchEvidencePackClaimAnnotation,
    ResearchEvidencePackContext,
    ResearchEvidencePackCounts,
)
from research_evidence.presentation_projection_service import (  # noqa: E402
    project_research_evidence_pack,
)


def uid() -> str:
    return str(uuid4())


def stable_uid(namespace: str, value: str) -> str:
    """Deterministic identity for replay fixtures built from frozen inputs."""
    return str(uuid5(NAMESPACE_URL, f"mas-test:{namespace}:{value}"))


@dataclass
class SourceSpec:
    key: str
    citation_label: str = "Example 2026"
    publisher: str = "Example Org"
    author: str = "A. Author"
    source_kind: str = "url"
    canonical_source_locator: str = "https://example.org/document"
    declared_quality_tier: str = "high"
    declared_quality_rationale: str = "peer reviewed"


@dataclass
class ClaimSpec:
    key: str
    claim_text: str = "Authorized claim"
    claim_category: str = "fact"
    epistemic_status: str = "estimate"
    confidence_label: str = "medium"
    supports_statement: str = "supports the claim"
    does_not_prove: str = "does not prove causality"
    limitations: tuple[str, ...] = ("one limit",)
    decision_relevance: str = "relevant to the decision"


@dataclass
class LinkSpec:
    """One authorized claim→evidence→source relationship."""

    claim_key: str
    source_key: str
    fact_key: str
    semantic_relationship: str = "support"


@dataclass
class PackSpec:
    sources: list[SourceSpec] = field(default_factory=list)
    claims: list[ClaimSpec] = field(default_factory=list)
    links: list[LinkSpec] = field(default_factory=list)
    project_id: str = ""
    research_question: str = "What should be decided?"
    project_limitations: tuple[str, ...] = ("limited data",)
    unresolved_gaps: tuple[str, ...] = ("open gap",)
    deterministic_ids: bool = False


def _identity(spec: PackSpec, namespace: str, key: str) -> str:
    return stable_uid(namespace, key) if spec.deterministic_ids else uid()


def build_aggregate(spec: PackSpec) -> ResearchEvidencePackAggregate:
    """Assemble one authorized internal_analysis pack from a declarative spec."""
    project_id = spec.project_id or _identity(spec, "project", "project")

    source_ids = {item.key: _identity(spec, "source", item.key) for item in spec.sources}
    claim_ids = {item.key: _identity(spec, "claim", item.key) for item in spec.claims}
    annotation_ids = {
        item.key: _identity(spec, "annotation", item.key) for item in spec.claims
    }
    fact_keys = list(dict.fromkeys(link.fact_key for link in spec.links))
    fact_ids = {key: _identity(spec, "fact", key) for key in fact_keys}
    # Every fact belongs to exactly one source, taken from its first linkage.
    fact_source = {}
    for link in spec.links:
        fact_source.setdefault(link.fact_key, link.source_key)

    claims = tuple(sorted(
        (
            ResearchEvidencePackAuthorizedClaim(
                claim_draft_id=claim_ids[item.key],
                claim_text=item.claim_text,
                claim_category=item.claim_category,
                annotation=ResearchEvidencePackClaimAnnotation(
                    annotation_revision_id=annotation_ids[item.key],
                    claim_draft_id=claim_ids[item.key],
                    annotation_sequence=2,
                    epistemic_status=item.epistemic_status,
                    confidence_label=item.confidence_label,
                    decision_relevance=item.decision_relevance,
                    supports_statement=item.supports_statement,
                    does_not_prove=item.does_not_prove,
                    limitations=item.limitations,
                    related_claim_draft_ids=(),
                    recorded_at=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
                ),
            )
            for item in spec.claims
        ),
        key=lambda item: item.claim_draft_id,
    ))

    sources = tuple(sorted(
        (
            ResearchEvidencePackAuthorizedSource(
                source_snapshot_id=source_ids[item.key],
                source_blob_id=_identity(spec, "blob", item.key),
                source_metadata_revision_id=_identity(spec, "meta", item.key),
                source_kind=item.source_kind,
                source_locator="https://internal.capture/x",
                captured_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                canonical_source_locator=item.canonical_source_locator,
                publisher=item.publisher,
                author=item.author,
                published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                citation_label=item.citation_label,
                declared_quality_tier=item.declared_quality_tier,
                declared_quality_rationale=item.declared_quality_rationale,
            )
            for item in spec.sources
        ),
        key=lambda item: item.source_snapshot_id,
    ))

    evidence = tuple(sorted(
        (
            ResearchEvidencePackAuthorizedEvidence(
                candidate_fact_revision_id=fact_ids[key],
                source_snapshot_id=source_ids[fact_source[key]],
                fact_metadata_revision_id=_identity(spec, "factmeta", key),
                fact_type="count",
                numeric_value=Decimal("11"),
                counted_entity="records",
                stable_fact_key=f"fact-key-{key}",
                source_char_range="10-20",
                citation_locator="section 2",
                unit="records",
            )
            for key in fact_keys
        ),
        key=lambda item: (item.source_snapshot_id, item.candidate_fact_revision_id),
    ))

    relationships = tuple(sorted(
        (
            ResearchEvidencePackAuthorizedRelationship(
                authorization_decision_id=uid(),
                claim_intake_item_id=uid(),
                evidence_intake_item_id=uid(),
                claim_support_assessment_id=uid(),
                claim_draft_id=claim_ids[link.claim_key],
                candidate_fact_revision_id=fact_ids[link.fact_key],
                source_snapshot_id=source_ids[link.source_key],
                claim_annotation_revision_id=annotation_ids[link.claim_key],
                claim_review_decision_id=uid(),
                evidence_review_decision_id=uid(),
                usage_scope="internal_analysis",
                authorization_sequence=1,
                authorized_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                locator_resolution="resolvable",
                evidence_linkage="linked",
                semantic_relationship=link.semantic_relationship,
            )
            for link in spec.links
        ),
        key=lambda item: (
            item.claim_draft_id, item.source_snapshot_id,
            item.candidate_fact_revision_id,
        ),
    ))

    return ResearchEvidencePackAggregate(
        project_id=project_id,
        usage_scope="internal_analysis",
        context=ResearchEvidencePackContext(
            context_revision_id=_identity(spec, "context", "context"),
            context_sequence=1,
            research_question=spec.research_question,
            project_limitations=spec.project_limitations,
            unresolved_gaps=spec.unresolved_gaps,
            recorded_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        ),
        claims=claims,
        sources=sources,
        evidence=evidence,
        relationships=relationships,
        counts=ResearchEvidencePackCounts(
            source_count=len(sources), claim_count=len(claims),
            evidence_count=len(evidence), relationship_count=len(relationships),
        ),
    )


def build_projection(spec: PackSpec):
    """Project a spec through the real A-3 presentation projection."""
    return project_research_evidence_pack(build_aggregate(spec))


def single_claim_spec(**overrides) -> PackSpec:
    """The smallest authorized shape: one claim, one record, one source."""
    return PackSpec(
        sources=[SourceSpec(key="s1")],
        claims=[ClaimSpec(key="c1", **overrides)],
        links=[LinkSpec(claim_key="c1", source_key="s1", fact_key="f1")],
    )
