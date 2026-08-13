"""End-to-end run_phase_node integration for the R2.0A-4A consumer.

Covers the ratified failure matrix at the orchestrator boundary: feature off,
not-applicable project types, used/empty attestation, fail-closed blocking with
NO LLM call, knowledge coexistence, and Decision Trace exposure. The A-3 entry
and the connection seam are injected; no database is required.
"""
import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_evidence_context as rc  # noqa: E402
from explainability import build_phase_trace  # noqa: E402
from orchestrator import run_phase_node  # noqa: E402
from research_evidence.pack_service import ResearchEvidencePackLimitError  # noqa: E402
from research_evidence.presentation_projection_service import (  # noqa: E402
    ResearchEvidencePresentationProjectionIntegrityError,
)
from state import PhaseStatus  # noqa: E402
from tests.test_audit_retrieval_integration import (  # noqa: E402
    make_audit_payload,
    make_audit_state,
    make_llm_response,
    sync_audit_fixture,
)
from tests.test_research_evidence_context import (  # noqa: E402
    FakeConn,
    empty_projection,
    internal_projection,
)


@pytest.fixture(autouse=True)
def _isolate_connection_seam(monkeypatch):
    """Default: opening a connection is a hard error unless a test allows it."""
    def forbidden():
        raise AssertionError("consumer opened a DB connection unexpectedly")

    monkeypatch.setattr(rc, "open_consumer_connection", forbidden)
    yield


@contextmanager
def _consumer(monkeypatch, *, connect=None, returns=None, raises=None):
    if connect is not None:
        monkeypatch.setattr(rc, "open_consumer_connection", connect)

    def fake(conn, *, project_id, usage_scope):
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(rc, "project_research_evidence_presentation", fake)
    yield


def _run_audit(state):
    response = make_llm_response(json.dumps(make_audit_payload()))
    call_llm = AsyncMock(return_value=response)
    with patch("orchestrator.call_llm", new=call_llm):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "audit"))
    return updated, call_llm


def _prompt_of(call_llm):
    assert call_llm.await_count == 1
    # call_llm(phase, system, prompt, ...)
    return call_llm.await_args.args[2]


def _consumption_events(state):
    return [
        event for event in state.policy_audit_log
        if event.get("event_type") == rc.RESEARCH_EVIDENCE_EVENT_TYPE
    ]


# ─────────────────────────── (A) feature flag off ───────────────────────────


def test_flag_off_no_db_no_event_and_byte_stable_prompt(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "false")
    state = make_audit_state("re-off")
    # connection seam stays forbidden; A-3 must not run either
    monkeypatch.setattr(
        rc, "project_research_evidence_presentation",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("A-3 must not run")),
    )
    updated, call_llm = _run_audit(state)
    assert updated.phase_status["audit"] == PhaseStatus.COMPLETED
    assert _consumption_events(updated) == []
    assert rc.RESEARCH_EVIDENCE_BLOCK_LABEL not in _prompt_of(call_llm)


# ─────────────────────────── project-type / phase gate ──────────────────────


@pytest.mark.parametrize("project_type", ["ai_readiness", "automation_roi"])
def test_shared_sequence_non_strategic_types_do_not_consume(monkeypatch, project_type):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state(f"re-{project_type}")
    state.project_type = project_type
    # connection seam remains forbidden -> proves no DB access
    updated, call_llm = _run_audit(state)
    assert updated.phase_status["audit"] == PhaseStatus.COMPLETED
    assert _consumption_events(updated) == []
    assert rc.RESEARCH_EVIDENCE_BLOCK_LABEL not in _prompt_of(call_llm)


# ─────────────────────────── (used) ─────────────────────────────────────────


def test_used_injects_block_logs_event_and_exposes_trace(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-used")
    projection = internal_projection()
    conn = FakeConn()
    with _consumer(monkeypatch, connect=lambda: conn, returns=projection):
        updated, call_llm = _run_audit(state)

    assert updated.phase_status["audit"] == PhaseStatus.COMPLETED
    prompt = _prompt_of(call_llm)
    assert rc.RESEARCH_EVIDENCE_BLOCK_LABEL in prompt
    assert projection.claims[0].claim_text in prompt

    events = _consumption_events(updated)
    assert len(events) == 1
    assert events[0]["details"]["status"] == "used"
    assert events[0]["details"]["projection_fingerprint"] == (
        projection.projection_fingerprint
    )
    attestation = updated.analysis_input_attestations["audit"]["research_evidence"]
    assert attestation["status"] == "used"
    assert attestation["projection_fingerprint"] == projection.projection_fingerprint
    assert attestation["policy_fingerprint"] == projection.policy_fingerprint
    assert "claims" not in attestation

    # read-only, no-commit posture held through the real orchestrator path
    assert conn.commits == 0
    assert conn.read_only is True
    assert conn.closed is True

    trace = build_phase_trace(updated, "audit")
    assert trace.research_evidence_impact is not None
    assert trace.research_evidence_impact.status == "used"
    assert trace.research_evidence_impact.consumed is True


# ─────────────────────────── (B) empty projection ───────────────────────────


def test_empty_projection_proceeds_without_block_and_attests(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-empty")
    with _consumer(monkeypatch, connect=FakeConn, returns=empty_projection()):
        updated, call_llm = _run_audit(state)

    assert updated.phase_status["audit"] == PhaseStatus.COMPLETED
    assert rc.RESEARCH_EVIDENCE_BLOCK_LABEL not in _prompt_of(call_llm)
    events = _consumption_events(updated)
    assert len(events) == 1
    assert events[0]["details"]["status"] == "empty"
    assert (
        updated.analysis_input_attestations["audit"]["research_evidence"]["status"]
        == "empty"
    )
    trace = build_phase_trace(updated, "audit")
    assert trace.research_evidence_impact.status == "empty"


# ─────────────────────────── fail-closed: no LLM call ───────────────────────


def _assert_blocked(updated, call_llm, reason):
    assert updated.phase_status["audit"] == PhaseStatus.FAILED
    assert call_llm.await_count == 0  # no LLM call on fail-closed
    events = _consumption_events(updated)
    assert len(events) == 1
    assert events[0]["details"]["status"] == "blocked"
    assert events[0]["details"]["blocked_reason"] == reason
    diag = updated.phase_failure_details["audit"]
    assert diag.category == reason
    assert "postgresql://" not in diag.message


def _run_audit_expecting_block(state):
    call_llm = AsyncMock()
    with patch("orchestrator.call_llm", new=call_llm):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "audit"))
    return updated, call_llm


def test_db_unavailable_blocks_phase_without_llm(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-db-down")

    def failing_connect():
        raise ConnectionError("postgresql://secret@host unreachable")

    monkeypatch.setattr(rc, "open_consumer_connection", failing_connect)
    updated, call_llm = _run_audit_expecting_block(state)
    _assert_blocked(updated, call_llm, rc.ResearchEvidenceBlockReason.UNAVAILABLE.value)
    trace = build_phase_trace(updated, "audit")
    assert trace.research_evidence_impact.status == "blocked"


def test_corrupt_state_blocks_phase_without_llm(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-corrupt")
    with _consumer(
        monkeypatch, connect=FakeConn,
        raises=ResearchEvidencePresentationProjectionIntegrityError("corrupt"),
    ):
        updated, call_llm = _run_audit_expecting_block(state)
    _assert_blocked(updated, call_llm, rc.ResearchEvidenceBlockReason.INTEGRITY.value)


def test_capacity_overflow_blocks_phase_without_llm(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-capacity")
    with _consumer(
        monkeypatch, connect=FakeConn,
        raises=ResearchEvidencePackLimitError("pack exceeds capacity"),
    ):
        updated, call_llm = _run_audit_expecting_block(state)
    _assert_blocked(
        updated, call_llm, rc.ResearchEvidenceBlockReason.CAPACITY_OVERFLOW.value,
    )


def test_prompt_overflow_blocks_phase_without_llm(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-overflow")
    monkeypatch.setattr(rc, "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES", 100)
    with _consumer(monkeypatch, connect=FakeConn, returns=internal_projection()):
        updated, call_llm = _run_audit_expecting_block(state)
    _assert_blocked(
        updated, call_llm, rc.ResearchEvidenceBlockReason.PROMPT_OVERFLOW.value,
    )


# ─────────────────────────── knowledge coexistence ──────────────────────────


def test_retrieval_knowledge_and_research_evidence_coexist_as_two_blocks(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    state = make_audit_state("re-coexist")
    sync_audit_fixture(state)  # eligible retrieval knowledge
    projection = internal_projection()
    with _consumer(monkeypatch, connect=FakeConn, returns=projection):
        updated, call_llm = _run_audit(state)

    prompt = _prompt_of(call_llm)
    # two separately labeled blocks, neither suppressing the other
    assert "RETRIEVAL-APPROVED KNOWLEDGE FOR AUDIT" in prompt
    assert rc.RESEARCH_EVIDENCE_BLOCK_LABEL in prompt
    assert "Fresh audit note" in prompt
    assert projection.claims[0].claim_text in prompt
    # both subsystems attested independently
    event_types = {event.get("event_type") for event in updated.policy_audit_log}
    assert "knowledge_retrieval_used" in event_types
    assert rc.RESEARCH_EVIDENCE_EVENT_TYPE in event_types
