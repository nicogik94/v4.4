"""Focused deterministic tests for W8.1 decision-state identity."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from clarifications import (
    ClarificationAnswer,
    ClarificationStatus,
    current_authoritative_answers,
)
from state import PhaseStatus, ProjectState
from state_coherence import (
    AnalysisGenerationIdentity,
    effective_input_identity,
    effective_input_payload,
    effective_input_sha256,
    is_complete_analysis,
    primary_decision_id,
    workflow_fingerprint,
)
import api
from tests.test_workflow_runner import make_completed_state


def _state() -> ProjectState:
    return ProjectState(
        project_id="11111111-1111-4111-8111-111111111111",
        project_name="Decision context",
        brief="Choose the controlled launch option.",
        data="Budget cap is 100000 USD.",
        ingestion_contract_version="2",
        ingestion_source="operator_api",
        ingestion_external_case_id="case-7",
        ingestion_metadata={"portfolio": "north"},
        output_language="en",
        report_mode="standard",
        risk_classification="limited_risk",
        risk_classification_rationale="Operator classified the decision.",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _answer(answer_id: str, text: str, status: ClarificationStatus, at: datetime):
    return ClarificationAnswer(
        answer_id=answer_id,
        question_id="q-budget",
        answer_text=text,
        status=status,
        answered_at=at,
    )


def test_effective_input_fingerprint_is_deterministic_and_identity_is_stable():
    state = _state()
    first = effective_input_identity(state)
    second = effective_input_identity(deepcopy(state))

    assert first == second
    assert len(first.effective_input_sha256) == 64
    assert first.decision_id == primary_decision_id(state)


def test_material_authoritative_input_change_changes_fingerprint():
    state = _state()
    before = effective_input_sha256(state)
    state.brief = "Choose the controlled launch option before Q4."
    assert effective_input_sha256(state) != before


def test_derived_and_volatile_state_do_not_change_effective_input_fingerprint():
    state = _state()
    before = effective_input_sha256(state)

    state.report = "A derived report"
    state.phase_status["classify"] = PhaseStatus.COMPLETED
    state.phase_confidence["classify"] = 0.91
    state.budget_consumed["total_tokens"] = 999
    state.policy_audit_log.append({"event": "telemetry", "ts": "later"})
    state.created_at = datetime(2030, 1, 1, tzinfo=timezone.utc)

    assert effective_input_sha256(state) == before


def test_consumed_knowledge_projection_changes_effective_input_identity():
    state = _state()
    state.analysis_input_attestations = {
        "audit": {
            "knowledge": {
                "status": "used",
                "projection_fingerprint": "1" * 64,
                "policy_fingerprint": "2" * 64,
                "items": [
                    {
                        "item_id": "item-1",
                        "source_id": "source-1",
                        "projection_sha256": "3" * 64,
                    }
                ],
            }
        }
    }
    before = effective_input_sha256(state)
    state.analysis_input_attestations["audit"]["knowledge"][
        "projection_fingerprint"
    ] = "4" * 64

    assert effective_input_sha256(state) != before


def test_non_admitted_knowledge_and_research_evidence_do_not_enter_identity():
    state = _state()
    before = effective_input_sha256(state)
    for status in ("empty", "blocked", "unavailable", "disabled", "not_applicable"):
        state.analysis_input_attestations = {
            "audit": {
                "knowledge": {
                    "status": status,
                    "projection_fingerprint": status,
                },
                "research_evidence": {
                    "status": status,
                    "projection_fingerprint": status,
                },
            }
        }
        assert effective_input_sha256(state) == before


def test_authorized_research_evidence_projection_is_referenced_not_copied():
    state = _state()
    state.analysis_input_attestations = {
        "strategy": {
            "research_evidence": {
                "status": "used",
                "usage_scope": "internal_analysis",
                "projection_fingerprint": "a" * 64,
                "policy_identifier": "consumer-policy",
                "policy_version": "1",
                "policy_fingerprint": "b" * 64,
                "sources": [{"source_snapshot_id": "source-snapshot-1"}],
                "claims": [{"claim_text": "must not be copied"}],
            }
        }
    }
    governed = effective_input_payload(state)["consumed_governed_evidence"]

    assert governed == [
        {
            "phase": "strategy",
            "authority": "research_evidence_internal_analysis",
            "usage_scope": "internal_analysis",
            "projection_fingerprint": "a" * 64,
            "policy_identifier": "consumer-policy",
            "policy_version": "1",
            "policy_fingerprint": "b" * 64,
            "source_snapshot_ids": ["source-snapshot-1"],
        }
    ]
    assert "claim_text" not in str(governed)


def test_only_current_authoritative_answer_contributes_to_effective_input():
    state = _state()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = _answer("a-old", "Budget is 80k", ClarificationStatus.ANSWERED, now)
    current = _answer(
        "a-current", "Budget is 100k", ClarificationStatus.ANSWERED, now + timedelta(seconds=1)
    )
    state.clarification_answers = [current, old]

    assert current_authoritative_answers(state) == [current]
    assert effective_input_payload(state)["authoritative_clarifications"] == [
        {
            "question_id": "q-budget",
            "answer_id": "a-current",
            "answer_text": "Budget is 100k",
        }
    ]


def test_unavailable_or_superseded_latest_answer_removes_older_authority():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for terminal_status in (ClarificationStatus.UNAVAILABLE, ClarificationStatus.SUPERSEDED):
        state = _state()
        state.clarification_answers = [
            _answer("a-old", "Budget is 80k", ClarificationStatus.ANSWERED, now),
            _answer("a-terminal", "", terminal_status, now + timedelta(seconds=1)),
        ]
        assert current_authoritative_answers(state) == []
        assert effective_input_payload(state)["authoritative_clarifications"] == []


def test_open_question_without_answer_never_becomes_effective_input():
    state = _state()
    assert effective_input_payload(state)["authoritative_clarifications"] == []


def test_equal_time_conflicting_answers_fail_closed():
    state = _state()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state.clarification_answers = [
        _answer("a-1", "Budget is 80k", ClarificationStatus.ANSWERED, now),
        _answer("a-2", "Budget is 100k", ClarificationStatus.ANSWERED, now),
    ]
    assert current_authoritative_answers(state) == []


def test_authoritative_answer_order_respects_timezone_offsets():
    state = _state()
    older_local = _answer(
        "a-local",
        "Budget is 80k",
        ClarificationStatus.ANSWERED,
        datetime.fromisoformat("2026-01-01T08:00:00-06:00"),
    )
    newer_utc = _answer(
        "a-utc",
        "Budget is 100k",
        ClarificationStatus.ANSWERED,
        datetime.fromisoformat("2026-01-01T14:30:00+00:00"),
    )
    state.clarification_answers = [newer_utc, older_local]

    assert current_authoritative_answers(state) == [newer_utc]


def test_workflow_fingerprint_is_deterministic_and_version_sensitive():
    state = _state()
    assert workflow_fingerprint(state, code_version="4.4.0") == workflow_fingerprint(
        deepcopy(state), code_version="4.4.0"
    )
    assert workflow_fingerprint(state, code_version="4.4.0") != workflow_fingerprint(
        state, code_version="4.4.1"
    )


def test_incomplete_working_state_is_not_an_accepted_analysis():
    assert is_complete_analysis(_state()) is False


def test_pre_w8_project_state_deserializes_with_empty_compatibility_bindings():
    restored = ProjectState.model_validate(
        {
            "project_id": "11111111-1111-4111-8111-111111111111",
            "project_name": "Historical",
            "brief": "Historical brief",
        }
    )
    assert restored.effective_input_snapshot_id == ""
    assert restored.analysis_generation_id == ""


def test_completed_operator_run_is_validated_then_promoted_before_persistence():
    state = make_completed_state("22222222-2222-4222-8222-222222222222")
    candidate = AnalysisGenerationIdentity(
        generation_id="33333333-3333-4333-8333-333333333333",
        project_id=state.project_id,
        decision_id=primary_decision_id(state),
        snapshot_id="44444444-4444-4444-8444-444444444444",
        analysis_state_sha256="a" * 64,
        workflow_fingerprint="workflow-v1",
        status="candidate",
    )
    calls: list[str] = []

    async def validated(*args, **kwargs):
        calls.append("validated")

    async def promoted(*args, **kwargs):
        calls.append("promoted")

    async def saved(*args, **kwargs):
        calls.append("saved")

    with (
        patch("api.store._get_pool", new=AsyncMock(return_value=object())),
        patch("api.state_coherence.schema_available", new=AsyncMock(return_value=True)),
        patch("api.state_coherence.create_candidate", new=AsyncMock(return_value=candidate)),
        patch("api.state_coherence.validate_candidate", new=validated),
        patch("api.state_coherence.promote_candidate", new=promoted),
        patch("api.store.save", new=saved),
    ):
        asyncio.run(api._accept_completed_analysis(state, None))

    assert calls == ["validated", "promoted", "saved"]
    assert state.analysis_generation_id == candidate.generation_id
    assert state.effective_input_snapshot_id == candidate.snapshot_id


def test_durable_runtime_fails_closed_when_v64_is_missing():
    state = make_completed_state("22222222-2222-4222-8222-222222222222")
    with (
        patch("api.store._get_pool", new=AsyncMock(return_value=object())),
        patch("api.store.DATABASE_URL", "postgresql://configured"),
        patch("api.state_coherence.schema_available", new=AsyncMock(return_value=False)),
    ):
        with pytest.raises(Exception, match="v64 decision-state coherence migration is required"):
            asyncio.run(api._accept_completed_analysis(state, None))


def test_manual_strategy_rerun_cannot_promote_stale_downstream_then_can_complete():
    state = make_completed_state("22222222-2222-4222-8222-222222222223")
    durable_current = "33333333-3333-4333-8333-333333333334"
    old_outputs = (state.sqi, state.monitor, state.report)
    fresh_sqi = deepcopy(state.sqi)
    fresh_sqi.sqi_overall = 76
    fresh_monitor = deepcopy(state.monitor)
    fresh_monitor.commitment_score = 81
    durable_binding = {"generation_id": durable_current}
    promoted_states: list[ProjectState] = []

    async def run_requested_phase(working: ProjectState, phase: str) -> ProjectState:
        if phase == "strategy":
            # The endpoint must invalidate before it hands the state to the
            # phase runner; otherwise this is the mixed-generation incident.
            assert working.sqi is None
            assert working.monitor is None
            assert working.report is None
            working.strategy.executive_strategy = "new coherent strategy"
        elif phase == "sqi":
            assert working.strategy.executive_strategy == "new coherent strategy"
            working.sqi = fresh_sqi
        elif phase == "monitor":
            assert working.strategy.executive_strategy == "new coherent strategy"
            working.monitor = fresh_monitor
        elif phase == "report":
            assert working.sqi is fresh_sqi
            assert working.monitor is fresh_monitor
            working.report = "report recomputed from the new strategy"
        working.phase_status[phase] = PhaseStatus.COMPLETED
        return working

    async def promote(completed: ProjectState, expected_base: str | None) -> None:
        assert expected_base == durable_current
        assert completed.strategy.executive_strategy == "new coherent strategy"
        assert completed.report == "report recomputed from the new strategy"
        promoted_states.append(deepcopy(completed))
        durable_binding["generation_id"] = "44444444-4444-4444-8444-444444444445"

    async def current_generation(_state: ProjectState) -> str:
        return durable_binding["generation_id"]

    async def exercise() -> None:
        with (
            patch("api.store.load", new=AsyncMock(side_effect=lambda _project_id: state)),
            patch("api.store.save", new=AsyncMock()),
            patch("api._current_generation_id", new=AsyncMock(side_effect=current_generation)),
            patch("api.run_phase_node", new=AsyncMock(side_effect=run_requested_phase)),
            patch("api._accept_completed_analysis", new=AsyncMock(side_effect=promote)),
        ):
            await api.run_single_phase_endpoint(
                state.project_id, api.RunPhaseRequest(phase="strategy")
            )

            assert promoted_states == []
            assert durable_binding["generation_id"] == durable_current
            assert state.analysis_generation_id == ""
            for phase in ("sqi", "monitor", "report"):
                assert state.phase_status[phase] == PhaseStatus.STALE
                assert getattr(state, phase) is None

            for phase in ("sqi", "monitor", "report"):
                await api.run_single_phase_endpoint(
                    state.project_id, api.RunPhaseRequest(phase=phase)
                )

    asyncio.run(exercise())

    assert len(promoted_states) == 1
    assert durable_binding["generation_id"] != durable_current
    assert api.is_workflow_complete(promoted_states[0])
    assert promoted_states[0].sqi == fresh_sqi
    assert promoted_states[0].sqi != old_outputs[0]
    assert promoted_states[0].monitor == fresh_monitor
    assert promoted_states[0].monitor != old_outputs[1]


def test_failed_manual_partial_rerun_leaves_durable_current_unpromoted():
    state = make_completed_state("22222222-2222-4222-8222-222222222224")
    durable_current = "33333333-3333-4333-8333-333333333335"
    promote = AsyncMock()

    async def fail_strategy(working: ProjectState, phase: str) -> ProjectState:
        assert phase == "strategy"
        working.strategy = None
        working.phase_status[phase] = PhaseStatus.FAILED
        return working

    async def exercise() -> None:
        with (
            patch("api.store.load", new=AsyncMock(return_value=state)),
            patch("api.store.save", new=AsyncMock()),
            patch("api._current_generation_id", new=AsyncMock(return_value=durable_current)),
            patch("api.run_phase_node", new=AsyncMock(side_effect=fail_strategy)),
            patch("api._accept_completed_analysis", new=promote),
        ):
            await api.run_single_phase_endpoint(
                state.project_id, api.RunPhaseRequest(phase="strategy")
            )

    asyncio.run(exercise())

    promote.assert_not_awaited()
    assert state.analysis_generation_id == ""
    assert state.phase_status["strategy"] == PhaseStatus.FAILED
    for phase in ("sqi", "monitor", "report"):
        assert state.phase_status[phase] == PhaseStatus.STALE
        assert getattr(state, phase) is None


def test_manual_report_rerun_preserves_upstream_analysis():
    state = make_completed_state("22222222-2222-4222-8222-222222222225")
    upstream = {
        phase: getattr(state, phase)
        for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor")
    }

    async def replace_report(working: ProjectState, phase: str) -> ProjectState:
        assert phase == "report"
        working.report = "replacement report"
        working.phase_status[phase] = PhaseStatus.COMPLETED
        return working

    async def exercise() -> None:
        with (
            patch("api.store.load", new=AsyncMock(return_value=state)),
            patch("api.store.save", new=AsyncMock()),
            patch("api._current_generation_id", new=AsyncMock(return_value=None)),
            patch("api.run_phase_node", new=AsyncMock(side_effect=replace_report)),
            patch("api._accept_completed_analysis", new=AsyncMock()),
        ):
            await api.run_single_phase_endpoint(
                state.project_id, api.RunPhaseRequest(phase="report")
            )

    asyncio.run(exercise())

    assert state.report == "replacement report"
    for phase, output in upstream.items():
        assert getattr(state, phase) is output
        assert state.phase_status[phase] == PhaseStatus.COMPLETED
