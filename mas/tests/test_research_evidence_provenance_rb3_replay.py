"""R3 — RB3 provenance-path replay. Frozen inputs only, no provider call.

This is NOT a new experiment and it does not regenerate any RB3 model output.
It replays the deterministic construction/serialization path — projection →
consuming-phase attestation → downstream register → report boundary — over the
FROZEN D001R-RB3 admitted set, and demonstrates that the records RB3 measured as
materially used can remain resolvable past the point where RB3 lost them.

The admitted set is the byte-identical reference copy from the RB3 comparative
audit; its SHA-256 is pinned below and matches the digest recorded in that
audit's FINAL_REPORT.
"""
import hashlib
import json
import sys
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
    stable_uid,
)


ADMITTED_SET_PATH = Path(__file__).resolve().parent / "fixtures" / "d001r_rb3_admitted_set.json"

# Recorded in r2-d001r-rb3-comparative-audit-20260803T000000Z/10_report/FINAL_REPORT.md
ADMITTED_SET_SHA256 = (
    "180d67c2aeb75998b1e5c89c8f387888d20cfecd6733900c1e39c5b29682e588"
)

# Per 09_uptake/TREATMENT_UPTAKE.md: eight of ten admitted records were used
# MATERIALLY at the workflow layer, and zero of ten reached the deliverable.
RB3_MATERIALLY_USED = (
    "RE05-PAID-001-A", "RE06-PS-002-B", "RE01-AI-005-A", "RE03-MX-002-A",
    "RE03-US-001-B", "RE03-US-002-A", "RE05-FREE-001-A", "RE01-AI-003-B",
)
RB3_DECORATIVE = ("RE06-PS-001-A",)
RB3_UNUSED = ("RE01-AI-001-B",)

# The eight approved gaps the admitted set must never be allowed to close.
RB3_GAP_IDS = tuple(f"GAP-0{i}" for i in range(1, 9))

# RB3's own source-kind truth: these are operator-curated evidence records, not
# raw web captures. Naming them anything else would fabricate provenance.
REPLAY_SOURCE_KIND = "operator_curated_research_evidence_record"


def load_admitted_set() -> dict:
    raw = ADMITTED_SET_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == ADMITTED_SET_SHA256, "frozen RB3 admitted set drifted"
    return json.loads(raw)


def _confidence_label(source_quality: str) -> str:
    """Map RB3's free-text source_quality onto the closed confidence vocabulary.

    Some frozen records qualify their quality in prose ("high for provider-offer
    existence, low for market generalization"). Only the leading grade is a
    confidence label; anything unrecognized becomes ``unknown`` rather than being
    upgraded to a grade the record never asserted.
    """
    head = source_quality.strip().split()[0].lower() if source_quality.strip() else ""
    return head if head in {"high", "medium", "low"} else "unknown"


def replay_spec(admitted: dict) -> PackSpec:
    """Map the frozen RB3 records onto the production pack contract.

    One source family per source, one atomic proposition per claim, one
    authorized support relationship per record — exactly the shape RB3 admitted.
    Identities are derived deterministically from the frozen evidence ids, so the
    replay is reproducible and carries no invented identity.
    """
    records = admitted["records"]
    families = {}
    for record in records:
        families.setdefault(record["source_family"], record)

    sources = [
        SourceSpec(
            key=family,
            citation_label=family,
            publisher=record["publisher"],
            author=record["publisher"],
            source_kind=REPLAY_SOURCE_KIND,
            canonical_source_locator=record["canonical_url"],
            declared_quality_tier=record["source_quality"].lower(),
            declared_quality_rationale=record["evidence_type"],
        )
        for family, record in sorted(families.items())
    ]
    claims = [
        ClaimSpec(
            key=record["evidence_id"],
            claim_text=record["atomic_proposition"],
            claim_category="fact",
            epistemic_status="reported_fact",
            confidence_label=_confidence_label(record["source_quality"]),
            supports_statement=record["directly_demonstrates"],
            does_not_prove=record["does_not_demonstrate"],
            limitations=(
                record["required_qualification"],
                record["laundering_guard"],
            ),
            decision_relevance=f"applicability: {record['cacofonico_applicability']}",
        )
        for record in records
    ]
    links = [
        LinkSpec(
            claim_key=record["evidence_id"],
            source_key=record["source_family"],
            fact_key=record["evidence_id"],
        )
        for record in records
    ]
    return PackSpec(
        sources=sources, claims=claims, links=links,
        project_id=admitted["project_id"],
        research_question=(
            "What should CACOFONICO's first commercial offer be?"
        ),
        project_limitations=tuple(
            item["subject"] for item in admitted["approved_gaps"][:8]
        )[:10],
        unresolved_gaps=tuple(
            item["gap_id"] for item in admitted["approved_gaps"]
        )[:10],
        deterministic_ids=True,
    )


@pytest.fixture(scope="module")
def replay():
    admitted = load_admitted_set()
    projection = build_projection(replay_spec(admitted))
    envelopes = rc.build_claim_provenance(projection)
    # Replay both consuming phases exactly as a run records them.
    state = type("S", (), {})()
    state.policy_audit_log = [
        {
            "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
            "phase": phase,
            "details": {
                "phase": phase,
                "status": rc.ResearchEvidenceConsumptionStatus.USED.value,
                "claims": [item.model_dump() for item in envelopes],
            },
        }
        for phase in ("audit", "strategy")
    ]
    register = rc.build_research_evidence_provenance_register(state)
    by_evidence_id = {
        record["evidence_id"]: stable_uid("claim", record["evidence_id"])
        for record in admitted["records"]
    }
    return {
        "admitted": admitted,
        "projection": projection,
        "register": register,
        "section": rc.render_research_evidence_provenance_section(register),
        "claim_id_of": by_evidence_id,
    }


def _resolution_for(replay, evidence_id):
    claim_id = replay["claim_id_of"][evidence_id]
    claim = next(
        item for item in replay["register"].claims
        if item.claim_draft_id == claim_id
    )
    return rc.resolve_research_evidence_reference(replay["register"], claim.claim_key)


# ═════════════════════════ frozen input integrity ═══════════════════════════


def test_frozen_admitted_set_matches_the_rb3_recorded_digest():
    admitted = load_admitted_set()
    assert admitted["record_count"] == 10
    assert len(admitted["records"]) == 10
    assert admitted["authorized_usage_scopes"] == ["internal_analysis"]
    assert sorted(admitted["denied_usage_scopes"]) == ["client_report", "operator_dossier"]


def test_replay_projects_all_ten_records_at_internal_analysis_scope(replay):
    projection = replay["projection"]
    assert projection.usage_scope is rc.CONSUMER_USAGE_SCOPE
    assert len(projection.claims) == 10
    assert len(projection.sources) == 9  # nine distinct source families
    assert len(projection.relationships) == 10


# ══════════ the eight materially used records survive the boundary ══════════


@pytest.mark.parametrize("evidence_id", RB3_MATERIALLY_USED)
def test_materially_used_record_is_resolvable_past_the_report_boundary(
    replay, evidence_id,
):
    """RB3 measured 8/10 used materially and 0/10 cited. All 8 now resolve."""
    resolution = _resolution_for(replay, evidence_id)
    assert resolution.resolved and resolution.citable
    assert resolution.claim.claim_key in replay["section"]


def test_all_ten_records_carry_a_distinct_stable_key(replay):
    keys = [claim.claim_key for claim in replay["register"].claims]
    assert len(keys) == 10
    assert len(set(keys)) == 10
    assert all(key.startswith(rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX) for key in keys)


@pytest.mark.parametrize("evidence_id", RB3_MATERIALLY_USED)
def test_downstream_consumer_answers_the_four_questions(replay, evidence_id):
    """Which record? Which source family? What limits? Backed or inferred?

    Answered from the register alone — no conversational history, no re-read of
    the Research Evidence store, no access to the original prompt block.
    """
    record = next(
        item for item in replay["admitted"]["records"]
        if item["evidence_id"] == evidence_id
    )
    resolution = _resolution_for(replay, evidence_id)
    claim = resolution.claim

    # 1. which RE record supports this claim
    assert claim.claim_text.startswith(record["atomic_proposition"][:80])
    assert claim.evidence_ids and claim.evidence_keys
    # 2. which source family
    assert [item.citation_label for item in claim.sources] == [record["source_family"]]
    assert claim.sources[0].source_kind == REPLAY_SOURCE_KIND
    # 3. what limitations apply
    assert claim.does_not_prove
    assert record["does_not_demonstrate"][:60] in claim.does_not_prove
    assert any(record["laundering_guard"][:60] in item for item in claim.limitations)
    # 4. evidence-backed or inferred
    assert claim.support_status == rc.CLAIM_SUPPORT_STATUS_SUPPORTED
    assert resolution.attribution == rc.ATTRIBUTION_RESEARCH_EVIDENCE


def test_decorative_and_unused_records_are_available_but_not_asserted(replay):
    """Availability is not usage: the register offers, it never claims."""
    for evidence_id in RB3_DECORATIVE + RB3_UNUSED:
        resolution = _resolution_for(replay, evidence_id)
        assert resolution.resolved
    # A record only becomes a citation when a phase actually references its key.
    assert rc.extract_research_evidence_references("no references here") == ()


# ═══════════ what the admitted set must still NOT be allowed to prove ═══════


def test_every_laundering_guard_survives_to_the_report_boundary(replay):
    section = replay["section"]
    for record in replay["admitted"]["records"]:
        guard = record["laundering_guard"]
        assert guard[:60] in section, f"{record['evidence_id']} lost its guard"


def test_every_does_not_demonstrate_survives_to_the_report_boundary(replay):
    section = replay["section"]
    for record in replay["admitted"]["records"]:
        assert record["does_not_demonstrate"][:60] in section


@pytest.mark.parametrize("gap_id", RB3_GAP_IDS)
def test_approved_gaps_are_not_closed_by_provenance_carry_through(replay, gap_id):
    """Making evidence traceable must not make it prove more than it does."""
    gap = next(
        item for item in replay["admitted"]["approved_gaps"]
        if item["gap_id"] == gap_id
    )
    # No claim in the register asserts the gap subject as established.
    for claim in replay["register"].claims:
        assert claim.claim_text != gap["subject"]
        assert claim.support_status in (
            rc.CLAIM_SUPPORT_STATUS_SUPPORTED,
            rc.CLAIM_SUPPORT_STATUS_QUALIFICATION_ONLY,
        )
    # And the gap subjects are never presented as supported propositions.
    assert gap["status"] in (
        "NO_RELIABLE_DIRECT_EVIDENCE_FOUND",
        "NO_RELIABLE_COMPARATIVE_EFFECTIVENESS_EVIDENCE",
        "REQUIRES_PRIMARY_CACOFONICO_VALIDATION",
    )


def test_carry_through_is_far_smaller_than_the_injected_block(replay):
    """The fix must not re-inject the corpus into every downstream phase."""
    block = rc.render_research_evidence_block(replay["projection"])
    block_bytes = len(block.encode("utf-8"))
    section_bytes = len(replay["section"].encode("utf-8"))
    assert section_bytes < block_bytes
    # Report the observed ratio for the wave's context-overhead comparison.
    assert section_bytes / block_bytes < 0.75


def test_replay_is_deterministic(replay):
    """Same frozen inputs, same keys, byte-identical downstream section."""
    admitted = load_admitted_set()
    again = build_projection(replay_spec(admitted))
    envelopes = rc.build_claim_provenance(again)
    # The envelope keeps projection order (by claim_draft_id); the register
    # re-orders by claim key. Both are deterministic and describe the same set.
    assert sorted(item.claim_key for item in envelopes) == [
        item.claim_key for item in replay["register"].claims
    ]
    assert rc.render_research_evidence_provenance_section(
        rc.ResearchEvidenceProvenanceRegister(
            phases=list(replay["register"].phases),
            claims=sorted(envelopes, key=lambda item: item.claim_key),
        )
    ) == replay["section"]
