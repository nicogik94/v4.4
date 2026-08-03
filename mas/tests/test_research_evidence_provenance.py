"""R3 — Research Evidence provenance carry-through: identity, envelope, resolver.

RB3 established that Research Evidence materially improved audit/strategy while
zero records reached the operator-facing deliverable, because the consuming
phases emitted phase-local ``C1``-``C10`` keys that the report phase had no way
to resolve. These tests cover the identity and envelope layer that fixes it.

No database, no network, no provider call.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_evidence_context as rc  # noqa: E402
from tests.research_evidence_provenance_fixtures import (  # noqa: E402
    ClaimSpec,
    LinkSpec,
    PackSpec,
    SourceSpec,
    build_projection,
    single_claim_spec,
    uid,
)


# ═══════════════ stable, non-positional cross-phase identity ═══════════════


def test_reference_keys_are_content_derived_not_positional():
    identities = [uid() for _ in range(5)]
    forward = rc.build_reference_keys(identities, rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX)
    reversed_order = rc.build_reference_keys(
        list(reversed(identities)), rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX,
    )
    # Reordering the input cannot change what any key means. This is the
    # property C1/C2 positional labels do not have.
    assert forward == reversed_order


def test_removing_one_record_does_not_renumber_the_others():
    identities = [uid() for _ in range(6)]
    full = rc.build_reference_keys(identities, rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX)
    without_second = rc.build_reference_keys(
        identities[:1] + identities[2:], rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX,
    )
    for identity in identities[2:]:
        assert without_second[identity] == full[identity]


def test_reference_keys_are_prefixed_per_member_kind():
    projection = build_projection(single_claim_spec())
    block = rc.render_research_evidence_block(projection)
    assert rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX in block
    assert rc.RESEARCH_EVIDENCE_SOURCE_KEY_PREFIX in block
    assert rc.RESEARCH_EVIDENCE_EVIDENCE_KEY_PREFIX in block


def test_positional_claim_labels_are_gone_from_the_model_facing_block():
    """The exact RB3 mechanism: opaque C1..Cn keys crossing a phase boundary."""
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key=f"s{i}") for i in range(1, 4)],
        claims=[ClaimSpec(key=f"c{i}") for i in range(1, 4)],
        links=[
            LinkSpec(claim_key=f"c{i}", source_key=f"s{i}", fact_key=f"f{i}")
            for i in range(1, 4)
        ],
    ))
    block = rc.render_research_evidence_block(projection)
    claims_section = block[block.index("CLAIMS:"):block.index("EVIDENCE:")]
    for ordinal in range(1, 4):
        assert f"  C{ordinal} " not in claims_section


def test_colliding_short_keys_widen_deterministically(monkeypatch):
    """A short-prefix collision must widen, never alias two identities."""
    first, second = uid(), uid()
    digests = {
        first: "aaaaaaaa" + "1" * 56,
        second: "aaaaaaaa" + "2" * 56,
    }
    monkeypatch.setattr(rc, "_reference_key_digest", lambda value: digests[value])
    keys = rc.build_reference_keys([first, second], rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX)
    assert keys[first] != keys[second]
    assert len(keys[first]) > len(rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX) + 8


def test_unresolvable_key_collision_fails_closed(monkeypatch):
    """Two identities that cannot be told apart must never get one key."""
    first, second = uid(), uid()
    monkeypatch.setattr(rc, "_reference_key_digest", lambda value: "b" * 64)
    with pytest.raises(rc.ResearchEvidenceReferenceKeyCollision):
        rc.build_reference_keys([first, second], rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX)


def test_key_collision_blocks_the_phase_before_any_model_call(monkeypatch):
    """Fail closed: an ambiguous reference must not reach a model."""
    monkeypatch.setattr(rc, "_reference_key_digest", lambda value: "c" * 64)
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key="s1"), SourceSpec(key="s2")],
        claims=[ClaimSpec(key="c1"), ClaimSpec(key="c2")],
        links=[
            LinkSpec(claim_key="c1", source_key="s1", fact_key="f1"),
            LinkSpec(claim_key="c2", source_key="s2", fact_key="f2"),
        ],
    ))
    with pytest.raises(rc.ResearchEvidenceReferenceKeyCollision):
        rc.render_research_evidence_block(projection)


# ═══════════════════════════ provenance envelope ═══════════════════════════


def test_envelope_carries_identity_status_limitations_and_source_family():
    projection = build_projection(single_claim_spec(
        claim_text="Paid diagnostic entry offers exist in this reference class",
        does_not_prove="does not prove superiority over a free discovery call",
        limitations=("offer-existence evidence only", "single vendor"),
    ))
    claim = rc.build_claim_provenance(projection)[0]

    assert claim.claim_key.startswith(rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX)
    assert claim.claim_draft_id == projection.claims[0].claim_draft_id
    assert claim.claim_text == projection.claims[0].claim_text
    assert claim.epistemic_status == projection.claims[0].epistemic_status.value
    assert claim.support_status == rc.CLAIM_SUPPORT_STATUS_SUPPORTED
    assert claim.evidence_backed
    assert claim.does_not_prove == "does not prove superiority over a free discovery call"
    assert claim.limitations == ["offer-existence evidence only", "single vendor"]
    assert claim.evidence_ids == [projection.evidence[0].candidate_fact_revision_id]
    assert claim.sources[0].citation_label == projection.sources[0].citation_label
    assert claim.sources[0].source_kind == projection.sources[0].source_kind


def test_envelope_is_far_smaller_than_the_rendered_block():
    """Provenance is carried by resolution, not by re-injecting the corpus."""
    spec = PackSpec(
        sources=[SourceSpec(key=f"s{i}") for i in range(1, 11)],
        claims=[ClaimSpec(key=f"c{i}", claim_text="X" * 900) for i in range(1, 11)],
        links=[
            LinkSpec(claim_key=f"c{i}", source_key=f"s{i}", fact_key=f"f{i}")
            for i in range(1, 11)
        ],
    )
    projection = build_projection(spec)
    block_bytes = len(rc.render_research_evidence_block(projection).encode("utf-8"))
    register = rc.ResearchEvidenceProvenanceRegister(
        phases=["audit", "strategy"], claims=list(rc.build_claim_provenance(projection)),
    )
    section_bytes = len(
        rc.render_research_evidence_provenance_section(register).encode("utf-8")
    )
    assert section_bytes < block_bytes


def test_long_values_are_truncated_with_an_explicit_marker():
    projection = build_projection(single_claim_spec(claim_text="Y" * 4000))
    claim = rc.build_claim_provenance(projection)[0]
    assert claim.claim_text.endswith(rc._PROVENANCE_TRUNCATION_MARKER)
    assert len(claim.claim_text) < 4000


def test_one_claim_supported_by_multiple_records():
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key="s1"), SourceSpec(key="s2")],
        claims=[ClaimSpec(key="c1")],
        links=[
            LinkSpec(claim_key="c1", source_key="s1", fact_key="f1"),
            LinkSpec(claim_key="c1", source_key="s2", fact_key="f2"),
        ],
    ))
    claim = rc.build_claim_provenance(projection)[0]
    assert len(claim.evidence_ids) == 2
    assert len(claim.sources) == 2


def test_one_record_supporting_multiple_claims():
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key="s1")],
        claims=[ClaimSpec(key="c1"), ClaimSpec(key="c2")],
        links=[
            LinkSpec(claim_key="c1", source_key="s1", fact_key="f1"),
            LinkSpec(claim_key="c2", source_key="s1", fact_key="f1"),
        ],
    ))
    claims = rc.build_claim_provenance(projection)
    assert len(claims) == 2
    assert claims[0].evidence_ids == claims[1].evidence_ids
    assert claims[0].claim_key != claims[1].claim_key


def test_duplicate_linkage_cannot_inflate_apparent_support():
    """Two authorized links to the same record are one supporting record."""
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key="s1")],
        claims=[ClaimSpec(key="c1")],
        links=[
            LinkSpec(claim_key="c1", source_key="s1", fact_key="f1"),
            LinkSpec(
                claim_key="c1", source_key="s1", fact_key="f2",
                semantic_relationship="qualification",
            ),
        ],
    ))
    claim = rc.build_claim_provenance(projection)[0]
    assert claim.source_keys == list(dict.fromkeys(claim.source_keys))
    assert len(claim.source_keys) == 1
    assert len(claim.evidence_ids) == 2


def test_qualification_only_claim_is_not_reported_as_supported():
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key="s1")],
        claims=[ClaimSpec(key="c1")],
        links=[LinkSpec(
            claim_key="c1", source_key="s1", fact_key="f1",
            semantic_relationship="qualification",
        )],
    ))
    claim = rc.build_claim_provenance(projection)[0]
    assert claim.support_status == rc.CLAIM_SUPPORT_STATUS_QUALIFICATION_ONLY
    assert not claim.evidence_backed


# ══════════════════════ register: merge and read-back ══════════════════════


def _state(log=None):
    return types.SimpleNamespace(
        project_id=uid(), project_type="strategic_audit",
        policy_audit_log=[] if log is None else log, report="",
    )


def _used_event(phase, claims):
    return {
        "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
        "phase": phase,
        "details": {
            "phase": phase,
            "status": rc.ResearchEvidenceConsumptionStatus.USED.value,
            "claims": [claim.model_dump() for claim in claims],
        },
    }


def test_register_merges_consuming_phases_and_orders_by_key():
    projection = build_projection(PackSpec(
        sources=[SourceSpec(key=f"s{i}") for i in range(1, 4)],
        claims=[ClaimSpec(key=f"c{i}") for i in range(1, 4)],
        links=[
            LinkSpec(claim_key=f"c{i}", source_key=f"s{i}", fact_key=f"f{i}")
            for i in range(1, 4)
        ],
    ))
    claims = rc.build_claim_provenance(projection)
    state = _state([_used_event("audit", claims), _used_event("strategy", claims)])

    register = rc.build_research_evidence_provenance_register(state)

    assert register.phases == ["audit", "strategy"]
    assert len(register.claims) == 3  # merged, not duplicated
    assert [item.claim_key for item in register.claims] == sorted(
        item.claim_key for item in register.claims
    )


def test_register_is_empty_when_nothing_was_consumed():
    assert rc.build_research_evidence_provenance_register(_state()).empty
    assert rc.render_research_evidence_provenance_section(
        rc.build_research_evidence_provenance_register(_state())
    ) == ""


@pytest.mark.parametrize(
    "status",
    [
        rc.ResearchEvidenceConsumptionStatus.EMPTY.value,
        rc.ResearchEvidenceConsumptionStatus.BLOCKED.value,
    ],
)
def test_register_ignores_non_used_attestations(status):
    projection = build_projection(single_claim_spec())
    event = _used_event("audit", rc.build_claim_provenance(projection))
    event["details"]["status"] = status
    assert rc.build_research_evidence_provenance_register(_state([event])).empty


def test_register_refuses_a_key_that_means_two_different_claims():
    """An ambiguous key is dropped, never resolved to an arbitrary winner."""
    first = build_projection(single_claim_spec(claim_text="first claim"))
    second = build_projection(single_claim_spec(claim_text="second claim"))
    claim_a = rc.build_claim_provenance(first)[0]
    claim_b = rc.build_claim_provenance(second)[0].model_copy(
        update={"claim_key": claim_a.claim_key},
    )
    state = _state([_used_event("audit", [claim_a]), _used_event("strategy", [claim_b])])

    register = rc.build_research_evidence_provenance_register(state)

    assert register.empty
    assert not rc.resolve_research_evidence_reference(
        register, claim_a.claim_key,
    ).citable


def test_malformed_envelope_is_dropped_not_guessed_at():
    event = _used_event("audit", [])
    event["details"]["claims"] = [{"claim_key": ["not", "a", "string"]}, "junk"]
    assert rc.build_research_evidence_provenance_register(_state([event])).empty


def test_register_ignores_attestations_from_non_consuming_phases():
    projection = build_projection(single_claim_spec())
    event = _used_event("report", rc.build_claim_provenance(projection))
    assert rc.build_research_evidence_provenance_register(_state([event])).empty


# ═════════════════════════════ resolution ══════════════════════════════════


def _register_with(spec=None):
    projection = build_projection(spec or single_claim_spec())
    claims = rc.build_claim_provenance(projection)
    return rc.build_research_evidence_provenance_register(
        _state([_used_event("audit", claims)])
    ), claims


def test_known_key_resolves_to_its_record_source_family_and_limitations():
    register, claims = _register_with(single_claim_spec(
        claim_text="reference-class price bound",
        does_not_prove="does not establish willingness to pay",
        limitations=("existence bounds — not benchmarks",),
    ))
    resolution = rc.resolve_research_evidence_reference(register, claims[0].claim_key)

    assert resolution.resolved and resolution.citable
    assert resolution.attribution == rc.ATTRIBUTION_RESEARCH_EVIDENCE
    assert resolution.claim.claim_text == "reference-class price bound"
    assert resolution.claim.sources[0].citation_label
    assert resolution.claim.does_not_prove == "does not establish willingness to pay"
    assert resolution.claim.limitations == ["existence bounds — not benchmarks"]


def test_unknown_key_resolves_unresolved_and_is_not_citable():
    register, _ = _register_with()
    resolution = rc.resolve_research_evidence_reference(register, "REC-deadbeef")
    assert not resolution.resolved
    assert not resolution.citable
    assert resolution.attribution == rc.ATTRIBUTION_UNRESOLVED
    assert resolution.claim is None


def test_opaque_positional_reference_is_never_resolvable():
    """The RB3 failure mode, stated as a contract: C7 resolves to nothing."""
    register, _ = _register_with()
    for opaque in ("C1", "C7", "C10", "S1", ""):
        assert not rc.resolve_research_evidence_reference(register, opaque).citable


def test_qualification_only_key_resolves_but_is_not_evidence_backed():
    register, claims = _register_with(PackSpec(
        sources=[SourceSpec(key="s1")],
        claims=[ClaimSpec(key="c1")],
        links=[LinkSpec(
            claim_key="c1", source_key="s1", fact_key="f1",
            semantic_relationship="qualification",
        )],
    ))
    resolution = rc.resolve_research_evidence_reference(register, claims[0].claim_key)
    assert resolution.resolved
    assert not resolution.citable
    assert resolution.attribution == rc.ATTRIBUTION_QUALIFICATION_ONLY


def test_reference_extraction_finds_keys_and_ignores_prose():
    register, claims = _register_with()
    key = claims[0].claim_key
    text = f"The offer is bounded {key}. Unrelated wording mentions C3 and RES-… only."
    assert rc.extract_research_evidence_references(text) == (key,)


# ═════════════════════ rendered downstream section ═════════════════════════


def test_section_renders_keys_support_status_sources_and_limitations():
    register, claims = _register_with(single_claim_spec(
        claim_text="78% of firms close within six months",
        does_not_prove="does not prove this project's cycle length",
        limitations=("reference class only",),
    ))
    section = rc.render_research_evidence_provenance_section(register)

    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL in section
    assert claims[0].claim_key in section
    assert "78% of firms close within six months" in section
    assert "source_family:" in section
    assert "does not prove this project's cycle length" in section
    assert "reference class only" in section
    assert section.startswith("\n\n")


def test_section_states_every_support_label_and_forbids_denying_evidence():
    register, _ = _register_with()
    section = rc.render_research_evidence_provenance_section(register)
    for label in (
        rc.SUPPORT_LABEL_RESEARCH_EVIDENCE, rc.SUPPORT_LABEL_PROJECT_EVIDENCE,
        rc.SUPPORT_LABEL_INFERENCE, rc.SUPPORT_LABEL_ASSUMPTION,
        rc.SUPPORT_LABEL_UNKNOWN,
    ):
        assert label in section
    assert "no direct project evidence" in section
    assert "does not make either one support the whole" in section


def test_section_does_not_leak_internal_identifiers():
    """Downstream needs resolution and attribution, not persistence mechanics."""
    spec = single_claim_spec()
    projection = build_projection(spec)
    claims = rc.build_claim_provenance(projection)
    register = rc.build_research_evidence_provenance_register(
        _state([_used_event("audit", claims)])
    )
    section = rc.render_research_evidence_provenance_section(register)
    assert projection.claims[0].annotation_revision_id not in section
    assert projection.sources[0].source_blob_id not in section
    assert projection.evidence[0].stable_fact_key not in section


# ═════════════════════════ attribution checking ════════════════════════════


def test_attribution_check_reports_an_unresolvable_reference():
    register, _ = _register_with()
    findings = rc.check_research_evidence_attribution(
        "The plan rests on REC-00000000 as its anchor.", register,
    )
    codes = [item["code"] for item in findings]
    assert rc.ATTRIBUTION_FINDING_UNRESOLVED_REFERENCE in codes


def test_attribution_check_reports_the_rb3_evidence_denial():
    """RB3 F-M3: the report claimed no evidence existed while it did."""
    register, _ = _register_with()
    findings = rc.check_research_evidence_attribution(
        "AI substitution risk [Inference] — no direct project evidence; "
        "cited as structural risk only.",
        register,
    )
    codes = [item["code"] for item in findings]
    assert rc.ATTRIBUTION_FINDING_EVIDENCE_DENIED in codes


def test_attribution_check_is_silent_on_a_correctly_attributed_report():
    register, claims = _register_with()
    findings = rc.check_research_evidence_attribution(
        f"Offer architecture is supported by research evidence {claims[0].claim_key}.",
        register,
    )
    assert findings == []


def test_attribution_check_does_not_fire_when_no_evidence_was_consumed():
    """RE-off reports may legitimately say no project evidence exists."""
    empty = rc.build_research_evidence_provenance_register(_state())
    findings = rc.check_research_evidence_attribution(
        "[Inference] — no direct project evidence.", empty,
    )
    assert findings == []
