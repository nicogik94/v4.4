from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_evidence import presentation_projection_models as models
from research_evidence import presentation_projection_policy as policy
from research_evidence.presentation_projection_models import (
    ResearchEvidencePresentationClaim,
    ResearchEvidencePresentationContext,
    ResearchEvidencePresentationEvidence,
    ResearchEvidencePresentationProbability,
    ResearchEvidencePresentationProjection,
    ResearchEvidencePresentationRelationship,
    ResearchEvidencePresentationSource,
    canonical_presentation_projection_descriptor,
    presentation_projection_fingerprint,
)
from research_evidence.presentation_projection_service import (
    project_research_evidence_pack,
)
from research_evidence.pack_models import (
    ResearchEvidencePackAggregate,
    ResearchEvidencePackAuthorizedClaim,
    ResearchEvidencePackAuthorizedEvidence,
    ResearchEvidencePackAuthorizedRelationship,
    ResearchEvidencePackAuthorizedSource,
    ResearchEvidencePackClaimAnnotation,
    ResearchEvidencePackContext,
    ResearchEvidencePackCounts,
    ResearchEvidencePackExplicitProbability,
    UsageScope,
)


SCOPES = tuple(UsageScope)
MEMBER_MODELS = {
    "context": ResearchEvidencePresentationContext,
    "claim": ResearchEvidencePresentationClaim,
    "probability": ResearchEvidencePresentationProbability,
    "source": ResearchEvidencePresentationSource,
    "evidence": ResearchEvidencePresentationEvidence,
    "relationship": ResearchEvidencePresentationRelationship,
}


def uid() -> str:
    return str(uuid4())


def pack_ids():
    return {
        "project": uid(), "claim": uid(), "source": uid(), "fact": uid(),
        "annotation": uid(), "context": uid(),
    }


def build_pack(scope, *, ids=None, claim_text="Authorized claim",
               probability_value=Decimal("0.250000")):
    ids = ids or pack_ids()
    annotation = ResearchEvidencePackClaimAnnotation(
        annotation_revision_id=ids["annotation"], claim_draft_id=ids["claim"],
        annotation_sequence=2, epistemic_status="estimate",
        confidence_label="medium", decision_relevance="relevant to the decision",
        supports_statement="supports the claim",
        does_not_prove="does not prove causality", limitations=("one limit",),
        related_claim_draft_ids=(),
        recorded_at=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        explicit_probability=ResearchEvidencePackExplicitProbability(
            value=probability_value, provided_by="operator",
            provenance_reference="probability workbook",
            provenance_note="entered after review",
        ),
    )
    claim = ResearchEvidencePackAuthorizedClaim(
        claim_draft_id=ids["claim"], claim_text=claim_text,
        claim_category="fact", annotation=annotation,
    )
    source = ResearchEvidencePackAuthorizedSource(
        source_snapshot_id=ids["source"], source_blob_id=uid(),
        source_metadata_revision_id=uid(), source_kind="url",
        source_locator="https://internal.capture/x",
        captured_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        canonical_source_locator="https://example.org/document",
        publisher="Example Org", author="A. Author",
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        citation_label="Example 2026", declared_quality_tier="high",
        declared_quality_rationale="peer reviewed",
    )
    evidence = ResearchEvidencePackAuthorizedEvidence(
        candidate_fact_revision_id=ids["fact"], source_snapshot_id=ids["source"],
        fact_metadata_revision_id=uid(), fact_type="count",
        numeric_value=Decimal("11"), counted_entity="records",
        stable_fact_key="fact-key-1", source_char_range="10-20",
        citation_locator="section 2", unit="records",
    )
    relationship = ResearchEvidencePackAuthorizedRelationship(
        authorization_decision_id=uid(), claim_intake_item_id=uid(),
        evidence_intake_item_id=uid(), claim_support_assessment_id=uid(),
        claim_draft_id=ids["claim"], candidate_fact_revision_id=ids["fact"],
        source_snapshot_id=ids["source"],
        claim_annotation_revision_id=ids["annotation"],
        claim_review_decision_id=uid(), evidence_review_decision_id=uid(),
        usage_scope=scope, authorization_sequence=1,
        authorized_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        locator_resolution="resolvable", evidence_linkage="linked",
        semantic_relationship="support",
    )
    context = ResearchEvidencePackContext(
        context_revision_id=ids["context"], context_sequence=1,
        research_question="What should be decided?",
        project_limitations=("limited data",), unresolved_gaps=("open gap",),
        recorded_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    return ResearchEvidencePackAggregate(
        project_id=ids["project"], usage_scope=scope, context=context,
        claims=(claim,), sources=(source,), evidence=(evidence,),
        relationships=(relationship,),
        counts=ResearchEvidencePackCounts(
            source_count=1, claim_count=1, evidence_count=1, relationship_count=1,
        ),
    )


def pack_value(pack, member_kind, field):
    if member_kind == "context":
        return getattr(pack.context, field)
    if member_kind == "claim":
        claim = pack.claims[0]
        if field in ("claim_draft_id", "claim_text", "claim_category"):
            return getattr(claim, field)
        if field == "annotation_recorded_at":
            return claim.annotation.recorded_at
        if field == "explicit_probability":
            return claim.annotation.explicit_probability
        return getattr(claim.annotation, field)
    if member_kind == "probability":
        return getattr(pack.claims[0].annotation.explicit_probability, field)
    if member_kind == "source":
        return getattr(pack.sources[0], field)
    if member_kind == "evidence":
        return getattr(pack.evidence[0], field)
    return getattr(pack.relationships[0], field)


def projected_member(projection, member_kind):
    if member_kind == "context":
        return projection.context
    if member_kind == "claim":
        return projection.claims[0]
    if member_kind == "probability":
        return projection.claims[0].explicit_probability
    if member_kind == "source":
        return projection.sources[0]
    if member_kind == "evidence":
        return projection.evidence[0]
    return projection.relationships[0]


def remodel(member, **overrides):
    values = {name: getattr(member, name) for name in type(member).model_fields}
    values.update(overrides)
    return type(member)(**values)


def refingerprint(values) -> str:
    return presentation_projection_fingerprint(
        canonical_presentation_projection_descriptor(
            project_id=values["project_id"], usage_scope=values["usage_scope"],
            context=values["context"], claims=values["claims"],
            sources=values["sources"], evidence=values["evidence"],
            relationships=values["relationships"], counts=values["counts"],
        )
    )


def rebuild(projection, **overrides):
    values = {
        name: getattr(projection, name)
        for name in type(projection).model_fields
    }
    values.update(overrides)
    if "projection_fingerprint" not in overrides:
        values["projection_fingerprint"] = refingerprint(values)
    return ResearchEvidencePresentationProjection(**values)


def test_projection_models_are_immutable_forbid_extras_and_use_tuples():
    projection = project_research_evidence_pack(build_pack("client_report"))
    with pytest.raises(ValidationError):
        projection.claims = ()
    with pytest.raises(ValidationError):
        projection.claims[0].claim_text = "changed"
    with pytest.raises(ValidationError):
        rebuild(projection, unexpected="value")
    with pytest.raises(ValidationError):
        remodel(projection.claims[0], unexpected="value")
    assert isinstance(projection.claims, tuple)
    assert isinstance(projection.sources, tuple)
    assert isinstance(projection.evidence, tuple)
    assert isinstance(projection.relationships, tuple)
    assert isinstance(projection.claims[0].limitations, tuple)


@pytest.mark.parametrize("scope", SCOPES)
def test_empty_projection_is_typed_pinned_and_fingerprinted(scope):
    project_id = uid()
    empty = project_research_evidence_pack(
        ResearchEvidencePackAggregate(project_id=project_id, usage_scope=scope)
    )
    assert empty.project_id == project_id
    assert empty.usage_scope is scope
    assert empty.context is None
    assert empty.claims == empty.sources == empty.evidence == empty.relationships == ()
    assert empty.counts == ResearchEvidencePackCounts()
    assert empty.policy_identifier == policy.PRESENTATION_POLICY_IDENTIFIER
    assert empty.policy_version == policy.PRESENTATION_POLICY_VERSION
    assert empty.policy_fingerprint == policy.PRESENTATION_POLICY_FINGERPRINT
    assert empty.projection_fingerprint == refingerprint(
        {name: getattr(empty, name) for name in type(empty).model_fields}
    )


def test_empty_projection_fingerprints_are_scope_specific():
    project_id = uid()
    fingerprints = {
        project_research_evidence_pack(
            ResearchEvidencePackAggregate(project_id=project_id, usage_scope=scope)
        ).projection_fingerprint
        for scope in SCOPES
    }
    assert len(fingerprints) == len(SCOPES)


def test_policy_fingerprint_is_recomputable_and_canonical():
    import hashlib
    import json

    canonical = json.dumps(
        policy.PRESENTATION_POLICY_PARAMETERS,
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    assert canonical == policy.PRESENTATION_POLICY_CANONICAL_JSON
    assert policy.PRESENTATION_POLICY_FINGERPRINT == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert policy.PRESENTATION_POLICY_PARAMETERS["default"] == "deny"
    assert policy.PRESENTATION_POLICY_IDENTIFIER == (
        "research_evidence_presentation_disclosure"
    )
    assert policy.PRESENTATION_POLICY_VERSION == "1.0.0"


@pytest.mark.parametrize("member_kind", sorted(MEMBER_MODELS))
def test_policy_is_default_deny_with_internal_full_fidelity(member_kind):
    model_fields = set(MEMBER_MODELS[member_kind].model_fields)
    internal = policy.allowed_presentation_fields(
        UsageScope.INTERNAL_ANALYSIS, member_kind,
    )
    operator = policy.allowed_presentation_fields(
        UsageScope.OPERATOR_DOSSIER, member_kind,
    )
    client = policy.allowed_presentation_fields(
        UsageScope.CLIENT_REPORT, member_kind,
    )
    assert internal == model_fields
    assert client <= operator <= internal
    for scope in SCOPES:
        allowed = policy.allowed_presentation_fields(scope, member_kind)
        required = policy.required_presentation_fields(scope, member_kind)
        assert required <= allowed <= model_fields


def test_operator_dossier_is_not_full_fidelity_and_client_is_strictest():
    for member_kind in ("claim", "source", "evidence", "relationship", "context"):
        internal = policy.allowed_presentation_fields(
            UsageScope.INTERNAL_ANALYSIS, member_kind,
        )
        operator = policy.allowed_presentation_fields(
            UsageScope.OPERATOR_DOSSIER, member_kind,
        )
        assert operator < internal
    assert policy.allowed_presentation_fields(
        UsageScope.CLIENT_REPORT, "source",
    ) < policy.allowed_presentation_fields(UsageScope.OPERATOR_DOSSIER, "source")
    assert "source_locator" not in policy.allowed_presentation_fields(
        UsageScope.OPERATOR_DOSSIER, "source",
    )


def test_policy_rejects_unknown_member_kind_and_scope():
    with pytest.raises(ValueError):
        policy.allowed_presentation_fields(UsageScope.CLIENT_REPORT, "mystery")
    with pytest.raises(ValueError):
        policy.required_presentation_fields("not_a_scope", "claim")


@pytest.mark.parametrize("scope", SCOPES)
def test_disclosure_matrix_presence_omission_and_verbatim(scope):
    pack = build_pack(scope)
    projection = project_research_evidence_pack(pack)
    assert projection.project_id == pack.project_id
    assert projection.usage_scope is pack.usage_scope
    assert projection.counts == pack.counts
    for member_kind, model_cls in MEMBER_MODELS.items():
        member = projected_member(projection, member_kind)
        allowed = policy.allowed_presentation_fields(scope, member_kind)
        required = policy.required_presentation_fields(scope, member_kind)
        for field in model_cls.model_fields:
            value = getattr(member, field)
            if field not in allowed:
                assert value is None, (scope, member_kind, field)
                continue
            if field in required:
                assert value is not None, (scope, member_kind, field)
            if member_kind == "claim" and field == "explicit_probability":
                assert (value is None) == (
                    pack.claims[0].annotation.explicit_probability is None
                )
                continue
            assert value == pack_value(pack, member_kind, field), (
                scope, member_kind, field,
            )


def leak_cases():
    donor_pack = build_pack("internal_analysis")
    donor = project_research_evidence_pack(donor_pack)
    cases = []
    for scope in (UsageScope.OPERATOR_DOSSIER, UsageScope.CLIENT_REPORT):
        for member_kind, model_cls in MEMBER_MODELS.items():
            disallowed = (
                set(model_cls.model_fields)
                - policy.allowed_presentation_fields(scope, member_kind)
            )
            for field in sorted(disallowed):
                value = getattr(projected_member(donor, member_kind), field)
                assert value is not None, (member_kind, field)
                cases.append(pytest.param(
                    scope, member_kind, field, value,
                    id=f"{scope.value}-{member_kind}-{field}",
                ))
    return cases


@pytest.mark.parametrize("scope,member_kind,field,donor_value", leak_cases())
def test_default_deny_rejects_every_disallowed_field(
    scope, member_kind, field, donor_value,
):
    projection = project_research_evidence_pack(build_pack(scope))
    if member_kind == "context":
        override = {"context": remodel(projection.context, **{field: donor_value})}
    elif member_kind == "claim":
        override = {"claims": (
            remodel(projection.claims[0], **{field: donor_value}),
        )}
    elif member_kind == "probability":
        probability = remodel(
            projection.claims[0].explicit_probability, **{field: donor_value},
        )
        override = {"claims": (
            remodel(projection.claims[0], explicit_probability=probability),
        )}
    elif member_kind == "source":
        override = {"sources": (
            remodel(projection.sources[0], **{field: donor_value}),
        )}
    elif member_kind == "evidence":
        override = {"evidence": (
            remodel(projection.evidence[0], **{field: donor_value}),
        )}
    else:
        override = {"relationships": (
            remodel(projection.relationships[0], **{field: donor_value}),
        )}
    with pytest.raises(ValidationError, match="is not allowed for scope"):
        rebuild(projection, **override)


def required_cases():
    cases = []
    for scope in SCOPES:
        for member_kind in sorted(MEMBER_MODELS):
            for field in sorted(
                policy.required_presentation_fields(scope, member_kind)
            ):
                cases.append(pytest.param(
                    scope, member_kind, field,
                    id=f"{scope.value}-{member_kind}-{field}",
                ))
    return cases


@pytest.mark.parametrize("scope,member_kind,field", required_cases())
def test_required_presentation_fields_cannot_be_dropped(scope, member_kind, field):
    projection = project_research_evidence_pack(build_pack(scope))
    with pytest.raises(ValidationError):
        if member_kind == "context":
            rebuild(projection, context=remodel(projection.context, **{field: None}))
        elif member_kind == "claim":
            rebuild(projection, claims=(
                remodel(projection.claims[0], **{field: None}),
            ))
        elif member_kind == "probability":
            probability = remodel(
                projection.claims[0].explicit_probability, **{field: None},
            )
            rebuild(projection, claims=(
                remodel(projection.claims[0], explicit_probability=probability),
            ))
        elif member_kind == "source":
            rebuild(projection, sources=(
                remodel(projection.sources[0], **{field: None}),
            ))
        elif member_kind == "evidence":
            rebuild(projection, evidence=(
                remodel(projection.evidence[0], **{field: None}),
            ))
        else:
            rebuild(projection, relationships=(
                remodel(projection.relationships[0], **{field: None}),
            ))


def test_policy_naming_unknown_model_fields_fails_closed(monkeypatch):
    projection = project_research_evidence_pack(build_pack("client_report"))
    original = policy.allowed_presentation_fields

    def patched(scope, member_kind):
        allowed = original(scope, member_kind)
        return allowed | {"mystery_field"} if member_kind == "claim" else allowed

    monkeypatch.setattr(policy, "allowed_presentation_fields", patched)
    with pytest.raises(ValidationError, match="unknown claim fields"):
        rebuild(projection)


def test_policy_identity_is_pinned_and_scope_mismatch_rejected():
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    with pytest.raises(ValidationError, match="frozen presentation policy identity"):
        rebuild(projection, policy_version="9.9.9")
    with pytest.raises(ValidationError, match="frozen presentation policy identity"):
        rebuild(projection, policy_fingerprint="0" * 64)
    with pytest.raises(ValidationError, match="frozen presentation policy identity"):
        rebuild(projection, policy_identifier="other_policy")
    with pytest.raises(ValidationError, match="scope must match the projection"):
        rebuild(projection, relationships=(
            remodel(
                projection.relationships[0],
                usage_scope=UsageScope.CLIENT_REPORT,
            ),
        ))


def test_counts_and_fingerprint_must_match_content():
    projection = project_research_evidence_pack(build_pack("client_report"))
    with pytest.raises(ValidationError, match="counts must match"):
        rebuild(projection, counts=ResearchEvidencePackCounts(
            source_count=1, claim_count=1, evidence_count=1, relationship_count=0,
        ), relationships=projection.relationships)
    with pytest.raises(ValidationError, match="fingerprint must match"):
        rebuild(projection, projection_fingerprint="0" * 64)


def test_empty_projection_admits_no_partial_state():
    projection = project_research_evidence_pack(build_pack("client_report"))
    with pytest.raises(ValidationError, match="empty projection"):
        rebuild(
            projection,
            relationships=(),
            counts=ResearchEvidencePackCounts(
                source_count=1, claim_count=1, evidence_count=1,
                relationship_count=0,
            ),
        )


def test_orphan_members_and_absent_targets_are_rejected():
    projection = project_research_evidence_pack(build_pack("client_report"))
    stranger = uid()
    extra_claim = remodel(projection.claims[0], claim_draft_id=stranger)
    with pytest.raises(ValidationError):
        rebuild(
            projection,
            claims=tuple(sorted(
                projection.claims + (extra_claim,),
                key=lambda item: item.claim_draft_id,
            )),
            counts=ResearchEvidencePackCounts(
                source_count=1, claim_count=2, evidence_count=1,
                relationship_count=1,
            ),
        )
    extra_source = remodel(projection.sources[0], source_snapshot_id=stranger)
    with pytest.raises(ValidationError):
        rebuild(
            projection,
            sources=tuple(sorted(
                projection.sources + (extra_source,),
                key=lambda item: item.source_snapshot_id,
            )),
            counts=ResearchEvidencePackCounts(
                source_count=2, claim_count=1, evidence_count=1,
                relationship_count=1,
            ),
        )
    extra_evidence = remodel(
        projection.evidence[0], candidate_fact_revision_id=stranger,
    )
    with pytest.raises(ValidationError):
        rebuild(
            projection,
            evidence=tuple(sorted(
                projection.evidence + (extra_evidence,),
                key=lambda item: (
                    item.source_snapshot_id, item.candidate_fact_revision_id,
                ),
            )),
            counts=ResearchEvidencePackCounts(
                source_count=1, claim_count=1, evidence_count=2,
                relationship_count=1,
            ),
        )
    with pytest.raises(ValidationError, match="absent claim|relationship-reachable"):
        rebuild(projection, relationships=(
            remodel(projection.relationships[0], claim_draft_id=stranger),
        ))


def test_relationship_evidence_must_use_canonical_source():
    projection = project_research_evidence_pack(build_pack("client_report"))
    other_source_id = uid()
    with pytest.raises(ValidationError):
        rebuild(projection, evidence=(
            remodel(projection.evidence[0], source_snapshot_id=other_source_id),
        ))


def test_internal_relationship_annotation_binding_must_match_claim():
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    with pytest.raises(ValidationError, match="non-current claim annotation"):
        rebuild(projection, relationships=(
            remodel(
                projection.relationships[0],
                claim_annotation_revision_id=uid(),
            ),
        ))


def build_two_member_pack(scope):
    first, second = sorted((pack_ids(), pack_ids()), key=lambda ids: ids["claim"])
    project_id = first["project"]
    second = dict(second, project=project_id)
    packs = [build_pack(scope, ids=first), build_pack(scope, ids=second)]
    claims = tuple(sorted(
        (packs[0].claims[0], packs[1].claims[0]),
        key=lambda item: item.claim_draft_id,
    ))
    sources = tuple(sorted(
        (packs[0].sources[0], packs[1].sources[0]),
        key=lambda item: item.source_snapshot_id,
    ))
    evidence = tuple(sorted(
        (packs[0].evidence[0], packs[1].evidence[0]),
        key=lambda item: (item.source_snapshot_id, item.candidate_fact_revision_id),
    ))
    relationships = tuple(sorted(
        (packs[0].relationships[0], packs[1].relationships[0]),
        key=lambda item: (
            item.claim_draft_id, item.source_snapshot_id,
            item.candidate_fact_revision_id,
        ),
    ))
    return ResearchEvidencePackAggregate(
        project_id=project_id, usage_scope=scope, context=packs[0].context,
        claims=claims, sources=sources, evidence=evidence,
        relationships=relationships,
        counts=ResearchEvidencePackCounts(
            source_count=2, claim_count=2, evidence_count=2, relationship_count=2,
        ),
    )


@pytest.mark.parametrize(
    "collection", ["claims", "sources", "evidence", "relationships"],
)
def test_projection_ordering_must_be_canonical(collection):
    projection = project_research_evidence_pack(
        build_two_member_pack("client_report")
    )
    reordered = tuple(reversed(getattr(projection, collection)))
    with pytest.raises(ValidationError, match="ordered|ordering"):
        rebuild(projection, **{collection: reordered})


def test_duplicate_projected_members_are_rejected():
    projection = project_research_evidence_pack(
        build_two_member_pack("client_report")
    )
    with pytest.raises(ValidationError):
        rebuild(projection, claims=(projection.claims[0], projection.claims[0]))
    with pytest.raises(ValidationError):
        rebuild(projection, relationships=(
            projection.relationships[0], projection.relationships[0],
        ))


def test_projection_fingerprint_stability_and_sensitivity(monkeypatch):
    ids = pack_ids()
    baseline = project_research_evidence_pack(
        build_pack("client_report", ids=ids)
    )
    repeated = project_research_evidence_pack(
        build_pack("client_report", ids=ids)
    )
    assert baseline == repeated
    assert baseline.projection_fingerprint == repeated.projection_fingerprint

    changed_text = project_research_evidence_pack(
        build_pack("client_report", ids=ids, claim_text="A different claim")
    )
    assert changed_text.projection_fingerprint != baseline.projection_fingerprint

    other_scope = project_research_evidence_pack(
        build_pack("operator_dossier", ids=ids)
    )
    assert other_scope.projection_fingerprint != baseline.projection_fingerprint

    rescaled = project_research_evidence_pack(
        build_pack("client_report", ids=ids, probability_value=Decimal("0.25"))
    )
    assert rescaled.projection_fingerprint != baseline.projection_fingerprint

    monkeypatch.setattr(policy, "PRESENTATION_POLICY_FINGERPRINT", "f" * 64)
    repoliced = project_research_evidence_pack(
        build_pack("client_report", ids=ids)
    )
    assert repoliced.policy_fingerprint == "f" * 64
    assert repoliced.projection_fingerprint != baseline.projection_fingerprint


def test_descriptor_binds_policy_scope_and_content():
    import json

    projection = project_research_evidence_pack(build_pack("operator_dossier"))
    descriptor = canonical_presentation_projection_descriptor(
        project_id=projection.project_id, usage_scope=projection.usage_scope,
        context=projection.context, claims=projection.claims,
        sources=projection.sources, evidence=projection.evidence,
        relationships=projection.relationships, counts=projection.counts,
    )
    payload = json.loads(descriptor)
    assert payload["descriptor"] == models.PRESENTATION_PROJECTION_DESCRIPTOR_VERSION
    assert payload["policy_identifier"] == policy.PRESENTATION_POLICY_IDENTIFIER
    assert payload["policy_version"] == policy.PRESENTATION_POLICY_VERSION
    assert payload["policy_fingerprint"] == policy.PRESENTATION_POLICY_FINGERPRINT
    assert payload["content"]["usage_scope"] == "operator_dossier"
    assert payload["content"]["project_id"] == projection.project_id
    assert payload["content"]["claims"][0]["annotation_revision_id"] is None
    assert projection.projection_fingerprint == presentation_projection_fingerprint(
        descriptor
    )


def test_canonical_value_normalizes_utc_and_rejects_unsafe_types():
    aware = datetime(2026, 7, 1, 10, 30, tzinfo=timezone(timedelta(hours=2)))
    assert models._canonical_value(aware) == "2026-07-01T08:30:00+00:00"
    assert models._canonical_value(Decimal("0.250000")) == "0.250000"
    assert models._canonical_value(Decimal("0.25")) == "0.25"
    assert models._canonical_value(UsageScope.CLIENT_REPORT) == "client_report"
    with pytest.raises(ValueError, match="timezone-aware"):
        models._canonical_value(datetime(2026, 7, 1, 10, 30))
    with pytest.raises(ValueError, match="unsupported"):
        models._canonical_value(1.5)
    with pytest.raises(ValueError, match="unsupported"):
        models._canonical_value(object())


def test_projected_relationship_narrows_to_authorized_assessment_values():
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    with pytest.raises(ValidationError, match="support or qualification"):
        remodel(projection.relationships[0], semantic_relationship="contradiction")
    with pytest.raises(ValidationError, match="resolvable"):
        remodel(projection.relationships[0], locator_resolution="unresolvable")
    with pytest.raises(ValidationError, match="linked"):
        remodel(projection.relationships[0], evidence_linkage="not_linked")
    assert remodel(
        projection.relationships[0], semantic_relationship="qualification",
    ).semantic_relationship == "qualification"


def test_projected_evidence_mirrors_pack_fact_shape_rules():
    projection = project_research_evidence_pack(build_pack("client_report"))
    evidence = projection.evidence[0]
    with pytest.raises(ValidationError, match="numeric fact types"):
        remodel(evidence, numeric_value=None)
    with pytest.raises(ValidationError, match="integral"):
        remodel(evidence, numeric_value=Decimal("11.5"))
    with pytest.raises(ValidationError, match="currency_code and as_of_date"):
        remodel(evidence, fact_type="money")
    with pytest.raises(ValidationError, match="require text_value"):
        remodel(evidence, fact_type="text", numeric_value=None)


def test_projected_probability_and_claim_mirror_pack_bounds():
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    probability = projection.claims[0].explicit_probability
    with pytest.raises(ValidationError, match="between 0 and 1"):
        remodel(probability, value=Decimal("1.5"))
    with pytest.raises(ValidationError, match="six decimal places"):
        remodel(probability, value=Decimal("0.1234567"))
    with pytest.raises(ValidationError, match="must not be blank"):
        remodel(probability, provenance_reference="   ")
    claim = projection.claims[0]
    with pytest.raises(ValidationError, match="cannot include the projected claim"):
        remodel(claim, related_claim_draft_ids=(claim.claim_draft_id,))
