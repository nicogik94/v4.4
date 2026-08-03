"""R3 — end-to-end Research Evidence provenance carry-through, no network.

Drives production phase construction (``run_phase_node`` + the real prompt
builders) across audit → strategy → synthesis → report and asserts that an
authorized evidence-backed claim stays resolvable at the report boundary,
including the exact RB3 failure mode as a before/after regression pair.

Every provider call is mocked. No database, no network, no real model.
"""
import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import research_evidence_context as rc  # noqa: E402
from explainability import build_phase_trace  # noqa: E402
from orchestrator import build_report_prompt, run_phase_node  # noqa: E402
from research_evidence.pack_service import ResearchEvidencePackLimitError  # noqa: E402
from state import PhaseStatus  # noqa: E402
from tests.research_evidence_provenance_fixtures import (  # noqa: E402
    ClaimSpec,
    LinkSpec,
    PackSpec,
    SourceSpec,
    build_projection,
)
from tests.test_audit_retrieval_integration import (  # noqa: E402
    make_audit_payload,
    make_audit_state,
    make_llm_response,
)
from tests.test_strategy_retrieval_integration import (  # noqa: E402
    make_strategy_payload,
)
from tests.test_workflow_runner import make_monitor_payload  # noqa: E402


CLAIM_TEXT = "Paid fixed-price diagnostic entry offers exist in this reference class"
DOES_NOT_PROVE = "does not prove superiority over a free discovery call"
LIMITATION = "offer-existence evidence only — not a benchmark"
CITATION_LABEL = "Highstella 2026"


def treatment_spec() -> PackSpec:
    """Two authorized records: one evidence-backed, one qualification-only."""
    return PackSpec(
        sources=[
            SourceSpec(key="s1", citation_label=CITATION_LABEL, source_kind="url"),
            SourceSpec(key="s2", citation_label="Reference Panel 2026"),
        ],
        claims=[
            ClaimSpec(
                key="c1", claim_text=CLAIM_TEXT, does_not_prove=DOES_NOT_PROVE,
                limitations=(LIMITATION,),
            ),
            ClaimSpec(
                key="c2", claim_text="Close cycles run up to six months",
                does_not_prove="does not prove this project's cycle length",
                limitations=("reference class only",),
            ),
        ],
        links=[
            LinkSpec(claim_key="c1", source_key="s1", fact_key="f1"),
            LinkSpec(
                claim_key="c2", source_key="s2", fact_key="f2",
                semantic_relationship="qualification",
            ),
        ],
    )


@pytest.fixture(autouse=True)
def _forbid_real_connections(monkeypatch):
    def forbidden():
        raise AssertionError("consumer opened a DB connection unexpectedly")

    monkeypatch.setattr(rc, "open_consumer_connection", forbidden)
    yield


@contextmanager
def _consumer(monkeypatch, *, returns=None, raises=None):
    monkeypatch.setattr(rc, "open_consumer_connection", lambda: object())
    monkeypatch.setattr(rc, "_enforce_read_only_posture", lambda conn: None)
    monkeypatch.setattr(rc, "_safe_close", lambda conn: None)

    def fake(conn, *, project_id, usage_scope):
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(rc, "project_research_evidence_presentation", fake)
    yield


def _run(state, phase, payload_text):
    call_llm = AsyncMock(return_value=make_llm_response(payload_text))
    with patch("orchestrator.call_llm", new=call_llm):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, phase))
    return updated, call_llm


def _prompt_of(call_llm):
    assert call_llm.await_count == 1
    return call_llm.await_args.args[2]


def _audit_payload_citing(key):
    """An audit output that references the record by its stable key."""
    payload = make_audit_payload()
    payload["top_findings"] = [
        f"Entry-offer architecture is available in the reference class [{key}]",
        "Archive load time remains the largest operational risk",
    ]
    return json.dumps(payload)


def _strategy_payload_citing(key):
    payload = make_strategy_payload()
    payload["executive_strategy"] = (
        f"Lead with a bounded paid diagnostic offer [{key}] before any retainer."
    )
    return json.dumps(payload)


def _drive_to_report_boundary(monkeypatch, projection):
    """Run audit and strategy as consuming phases, then monitor."""
    state = make_audit_state("re-provenance-e2e")
    state.project_type = "strategic_audit"
    keys = {
        claim.claim_draft_id: claim.claim_key
        for claim in rc.build_claim_provenance(projection)
    }
    primary = next(
        key for claim_id, key in keys.items()
        if claim_id == _claim_id_for(projection, CLAIM_TEXT)
    )

    with _consumer(monkeypatch, returns=projection):
        state, audit_call = _run(state, "audit", _audit_payload_citing(primary))
        assert state.phase_status["audit"] == PhaseStatus.COMPLETED
        state.current_phase = "strategy"
        state, strategy_call = _run(state, "strategy", _strategy_payload_citing(primary))
        assert state.phase_status["strategy"] == PhaseStatus.COMPLETED

    state.current_phase = "monitor"
    state, _ = _run(state, "monitor", json.dumps(make_monitor_payload()))
    return state, primary, audit_call, strategy_call


def _claim_id_for(projection, claim_text):
    return next(
        claim.claim_draft_id
        for claim in projection.claims
        if claim.claim_text == claim_text
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(config, "research_evidence_enabled", lambda: True)


# ═══════════════ the RB3 failure mode, before and after the fix ═════════════


def test_rb3_failure_mode_reproduced_when_provenance_is_not_carried(
    enabled, monkeypatch,
):
    """Pre-fix behaviour: report inherits a reference it cannot resolve.

    This is RB3 F-M2 exactly — the report phase receives the consuming phases'
    output, and therefore their reference keys, but nothing that defines them.
    """
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    # Simulate the pre-fix report builder: no provenance carried across.
    monkeypatch.setattr(rc, "build_downstream_provenance_section", lambda state: "")
    prompt = build_report_prompt(state)

    assert key in prompt, "the opaque reference is inherited from upstream context"
    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL not in prompt
    # Nothing in the prompt lets the synthesis phase resolve that reference.
    assert CLAIM_TEXT not in prompt
    assert CITATION_LABEL not in prompt
    assert DOES_NOT_PROVE not in prompt


def test_provenance_survives_audit_strategy_synthesis_and_report(
    enabled, monkeypatch,
):
    """Post-fix: the same reference resolves to record, source and limitation."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    prompt = build_report_prompt(state)

    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL in prompt
    assert key in prompt
    assert CLAIM_TEXT in prompt
    assert CITATION_LABEL in prompt
    assert DOES_NOT_PROVE in prompt
    assert LIMITATION in prompt


def test_report_consumer_answers_the_four_provenance_questions(enabled, monkeypatch):
    """Which record, which source family, what limits, backed or inferred."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    register = rc.build_research_evidence_provenance_register(state)
    resolution = rc.resolve_research_evidence_reference(register, key)

    assert resolution.claim.claim_draft_id == _claim_id_for(projection, CLAIM_TEXT)
    assert resolution.claim.sources[0].citation_label == CITATION_LABEL
    assert resolution.claim.does_not_prove == DOES_NOT_PROVE
    assert resolution.claim.limitations == [LIMITATION]
    assert resolution.attribution == rc.ATTRIBUTION_RESEARCH_EVIDENCE


def test_provenance_survives_a_state_snapshot_round_trip(enabled, monkeypatch):
    """Durability without a migration: the carrier is the persisted state."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    restored = type(state).model_validate(
        json.loads(state.model_dump_json())
    )

    register = rc.build_research_evidence_provenance_register(restored)
    assert rc.resolve_research_evidence_reference(register, key).citable
    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL in build_report_prompt(restored)


def test_report_phase_records_a_clean_attribution_attestation(enabled, monkeypatch):
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    state.current_phase = "report"
    state, _ = _run(
        state, "report",
        f"# Executive Summary\nA bounded paid diagnostic is recommended "
        f"[{key}]. Supported by research evidence.\n",
    )

    events = [
        event for event in state.policy_audit_log
        if event["event_type"] == rc.RESEARCH_EVIDENCE_ATTRIBUTION_EVENT_TYPE
    ]
    assert len(events) == 1
    details = events[0]["details"]
    assert details["cited_claim_keys"] == [key]
    assert details["findings"] == []


def test_report_phase_flags_the_rb3_misattribution(enabled, monkeypatch):
    """RB3 F-M3: claiming no evidence exists while authorized records are held."""
    projection = build_projection(treatment_spec())
    state, _, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    state.current_phase = "report"
    state, _ = _run(
        state, "report",
        "# Executive Summary\nAI substitution risk [Inference] — no direct "
        "project evidence; cited as structural risk only.\n",
    )

    events = [
        event for event in state.policy_audit_log
        if event["event_type"] == rc.RESEARCH_EVIDENCE_ATTRIBUTION_EVENT_TYPE
    ]
    codes = [item["code"] for item in events[0]["details"]["findings"]]
    assert rc.ATTRIBUTION_FINDING_EVIDENCE_DENIED in codes
    # Observation only: the report itself is untouched and the phase completed.
    assert state.phase_status["report"] == PhaseStatus.COMPLETED
    assert "no direct project evidence" in state.report


def test_report_phase_flags_a_reference_that_resolves_to_nothing(
    enabled, monkeypatch,
):
    projection = build_projection(treatment_spec())
    state, _, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    state.current_phase = "report"
    state, _ = _run(
        state, "report",
        "# Executive Summary\nThe recommendation rests on REC-00000000.\n",
    )

    events = [
        event for event in state.policy_audit_log
        if event["event_type"] == rc.RESEARCH_EVIDENCE_ATTRIBUTION_EVENT_TYPE
    ]
    codes = [item["code"] for item in events[0]["details"]["findings"]]
    assert rc.ATTRIBUTION_FINDING_UNRESOLVED_REFERENCE in codes


def test_downstream_trace_exposes_the_resolution_register(enabled, monkeypatch):
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)

    report_trace = build_phase_trace(state, "report")
    audit_trace = build_phase_trace(state, "audit")

    assert report_trace.research_evidence_provenance is not None
    assert key in report_trace.research_evidence_provenance.by_key()
    # Consuming phases keep their own impact summary; the register is the
    # downstream view and is not duplicated onto them.
    assert audit_trace.research_evidence_provenance is None
    assert audit_trace.research_evidence_impact is not None


# ═══════════════════════ governance boundaries preserved ════════════════════


def test_research_evidence_off_creates_no_provenance_surface(monkeypatch):
    """RE OFF: no register, no section, no attestation, no citations."""
    monkeypatch.setattr(config, "research_evidence_enabled", lambda: False)
    state = make_audit_state("re-provenance-off")
    state.project_type = "strategic_audit"
    state, call_llm = _run(state, "audit", json.dumps(make_audit_payload()))

    assert rc.build_research_evidence_provenance_register(state).empty
    prompt = build_report_prompt(state)
    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL not in prompt
    assert rc.RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX not in prompt
    assert not [
        event for event in state.policy_audit_log
        if event["event_type"] == rc.RESEARCH_EVIDENCE_EVENT_TYPE
    ]


def test_report_prompt_is_byte_identical_when_nothing_was_consumed(monkeypatch):
    """The carry-through must not perturb Knowledge-only / RE-off prompts."""
    monkeypatch.setattr(config, "research_evidence_enabled", lambda: False)
    state = make_audit_state("re-provenance-byte-stable")
    state.project_type = "strategic_audit"
    state, _ = _run(state, "audit", json.dumps(make_audit_payload()))

    with_carry_through = build_report_prompt(state)
    monkeypatch.setattr(rc, "build_downstream_provenance_section", lambda state: "")
    legacy = build_report_prompt(state)
    assert with_carry_through == legacy


@pytest.mark.parametrize("project_type", ["ai_readiness", "automation_roi"])
def test_excluded_project_types_gain_no_provenance(enabled, monkeypatch, project_type):
    """Modes intentionally excluded from RE stay excluded downstream too."""
    state = make_audit_state(f"re-provenance-{project_type}")
    state.project_type = project_type
    projection = build_projection(treatment_spec())
    with _consumer(monkeypatch, returns=projection):
        state, _ = _run(state, "audit", json.dumps(make_audit_payload()))

    assert rc.build_research_evidence_provenance_register(state).empty
    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL not in build_report_prompt(state)


def test_unauthorized_or_inactive_evidence_cannot_be_cited(enabled, monkeypatch):
    """Only what A-2/A-3 admitted is in the projection, therefore in the register."""
    admitted = build_projection(treatment_spec())
    # A record that was never authorized (or is revoked/inactive) is simply
    # absent from the projection, so it has no key anywhere downstream.
    withheld = build_projection(PackSpec(
        sources=[SourceSpec(key="sx", citation_label="Withheld Source")],
        claims=[ClaimSpec(key="cx", claim_text="Withheld proposition")],
        links=[LinkSpec(claim_key="cx", source_key="sx", fact_key="fx")],
    ))
    withheld_key = rc.build_claim_provenance(withheld)[0].claim_key

    state, _, _, _ = _drive_to_report_boundary(monkeypatch, admitted)
    register = rc.build_research_evidence_provenance_register(state)

    assert not rc.resolve_research_evidence_reference(register, withheld_key).citable
    prompt = build_report_prompt(state)
    assert withheld_key not in prompt
    assert "Withheld proposition" not in prompt
    assert "Withheld Source" not in prompt


def test_blocked_consumption_leaves_no_provenance_and_fails_the_phase(
    enabled, monkeypatch,
):
    """Missing provenance fails safely: no phase output, no partial register."""
    state = make_audit_state("re-provenance-blocked")
    state.project_type = "strategic_audit"
    call_llm = AsyncMock()
    with _consumer(monkeypatch, raises=ResearchEvidencePackLimitError("too large")):
        with patch("orchestrator.call_llm", new=call_llm):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                state = asyncio.run(run_phase_node(state, "audit"))

    assert call_llm.await_count == 0
    assert state.phase_status["audit"] == PhaseStatus.FAILED
    assert rc.build_research_evidence_provenance_register(state).empty
    assert rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL not in build_report_prompt(state)


def test_research_evidence_stays_separate_from_project_evidence_locators(
    enabled, monkeypatch,
):
    """Two governed systems, side by side, never merged into one register."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    prompt = build_report_prompt(state)

    assert "PROJECT EVIDENCE LOCATORS:" in prompt
    locator_section = prompt[
        prompt.index("PROJECT EVIDENCE LOCATORS:"):
        prompt.index(rc.RESEARCH_EVIDENCE_PROVENANCE_SECTION_LABEL)
    ]
    assert key not in locator_section
    assert "Do not use a REC- key inside an [Evidence: ...] marker" in prompt


# ═══════════════════════════ anti-laundering ════════════════════════════════


def test_rewriting_a_claim_without_its_key_retains_no_citation(enabled, monkeypatch):
    """evidence-backed claim → unsupported rewrite → citation does not follow."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)

    rewrite = (
        "A paid diagnostic entry offer is the superior route for this business."
    )
    assert rc.extract_research_evidence_references(rewrite) == ()
    assert rc.check_research_evidence_attribution(rewrite, register) == []
    # And no API exists that would attach the record to that sentence.
    assert not rc.resolve_research_evidence_reference(register, rewrite).citable


def test_verbatim_claim_text_alone_does_not_recreate_a_citation(enabled, monkeypatch):
    """citation removed upstream → downstream cannot rebuild it from similarity."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)

    quoted = f"The report states: {CLAIM_TEXT}."
    assert rc.extract_research_evidence_references(quoted) == ()
    assert not rc.resolve_research_evidence_reference(register, CLAIM_TEXT).resolved


def test_merged_claims_do_not_pool_their_sources(enabled, monkeypatch):
    """two claims merged → union must not imply every source supports each part."""
    projection = build_projection(treatment_spec())
    state, _, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)

    first, second = register.claims[0], register.claims[1]
    first_sources = {item.source_snapshot_id for item in first.sources}
    second_sources = {item.source_snapshot_id for item in second.sources}
    assert first_sources.isdisjoint(second_sources)
    for key in (first.claim_key, second.claim_key):
        resolution = rc.resolve_research_evidence_reference(register, key)
        assert {item.source_snapshot_id for item in resolution.claim.sources} == (
            first_sources if key == first.claim_key else second_sources
        )


def test_inference_from_an_evidence_backed_claim_stays_an_inference(
    enabled, monkeypatch,
):
    """A derived statement is never upgraded by its parent's support."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)
    resolution = rc.resolve_research_evidence_reference(register, key)

    derived = "Therefore this business can charge a premium for bilingual delivery."
    # Resolving the parent yields the parent's proposition, not the derivation.
    assert resolution.claim.claim_text == CLAIM_TEXT
    assert derived not in resolution.claim.claim_text
    # And the prompt requires the derivation to be labelled as an inference.
    prompt = build_report_prompt(state)
    assert "An inference drawn from an evidence-backed claim is still an" in prompt
    assert rc.SUPPORT_LABEL_INFERENCE in prompt


def test_source_scope_limitations_remain_visible_downstream(enabled, monkeypatch):
    """source scope exceeded → the limitation travels with the record."""
    projection = build_projection(treatment_spec())
    state, key, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)
    section = rc.render_research_evidence_provenance_section(register)

    assert DOES_NOT_PROVE in section
    assert LIMITATION in section
    assert "does not prove this project's cycle length" in section
    assert "reference class only" in section


def test_qualification_only_record_is_not_presented_as_support(enabled, monkeypatch):
    projection = build_projection(treatment_spec())
    state, _, _, _ = _drive_to_report_boundary(monkeypatch, projection)
    register = rc.build_research_evidence_provenance_register(state)

    qualification = next(
        claim for claim in register.claims
        if claim.support_status == rc.CLAIM_SUPPORT_STATUS_QUALIFICATION_ONLY
    )
    assert not rc.resolve_research_evidence_reference(
        register, qualification.claim_key,
    ).citable
    section = rc.render_research_evidence_provenance_section(register)
    assert f"{qualification.claim_key} support_status=" \
           f"{rc.CLAIM_SUPPORT_STATUS_QUALIFICATION_ONLY}" in section
