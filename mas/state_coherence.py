"""Durable decision-state identity and atomic currentness primitives.

``ProjectState`` remains the mutable workflow working copy.  This module owns
the immutable effective-input identity and immutable accepted analysis copy
needed to distinguish candidate work from the one authoritative current
analysis.  It deliberately does not trigger or rerun workflow phases.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from clarifications import current_authoritative_answers
from decision_objects import stable_object_id
from state import PhaseStatus, ProjectState
from workflow_templates import get_workflow_phase_sequence


STATE_COHERENCE_SCHEMA_VERSION = "decision-state.v1"
EFFECTIVE_INPUT_CONTRACT_VERSION = "effective-decision-input.v1"
ANALYSIS_STATE_CONTRACT_VERSION = "analysis-generation.v1"
LEGACY_BASELINE_WORKFLOW_FINGERPRINT = "legacy-baseline.v1"
_IDENTITY_NAMESPACE = uuid.UUID("c956f954-d100-4d4f-a2a0-afd687c4dfef")


class StateCoherenceError(RuntimeError):
    """Base fail-closed state-coherence error."""


class CandidateNotValidatedError(StateCoherenceError):
    pass


class StalePromotionError(StateCoherenceError):
    pass


class ScopeMismatchError(StateCoherenceError):
    pass


@dataclass(frozen=True)
class EffectiveInputIdentity:
    snapshot_id: str
    project_id: str
    decision_id: str
    effective_input_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class AnalysisGenerationIdentity:
    generation_id: str
    project_id: str
    decision_id: str
    snapshot_id: str
    analysis_state_sha256: str
    workflow_fingerprint: str
    status: str
    expected_base_generation_id: str | None = None


def primary_decision_id(state: ProjectState) -> str:
    """Reuse the existing stable primary-decision identity."""
    return stable_object_id("decision", state.project_id, "primary")


def effective_input_payload(state: ProjectState) -> dict[str, Any]:
    """Project only authoritative, material decision inputs.

    Derived phase outputs, report text, workflow status, telemetry, timestamps,
    caches, policy consumption counters, and open/unavailable/superseded
    clarification records are intentionally absent.
    """
    clarifications = [
        {
            "question_id": str(getattr(answer, "question_id", "") or ""),
            "answer_id": str(getattr(answer, "answer_id", "") or ""),
            "answer_text": str(getattr(answer, "answer_text", "") or "").strip(),
        }
        for answer in current_authoritative_answers(state)
    ]
    clarifications.sort(
        key=lambda item: (item["question_id"], item["answer_id"], item["answer_text"])
    )
    return {
        "contract_version": EFFECTIVE_INPUT_CONTRACT_VERSION,
        "decision_scope": {
            "project_id": state.project_id,
            "decision_id": primary_decision_id(state),
        },
        "question": {
            "brief": state.brief,
            "operator_data": state.data,
            "project_type": state.project_type,
        },
        "ingestion": {
            "contract_version": state.ingestion_contract_version,
            "source": state.ingestion_source,
            "external_case_id": state.ingestion_external_case_id,
            "metadata": _jsonable(state.ingestion_metadata),
        },
        "operator_configuration": {
            "output_language": state.output_language,
            "report_mode": state.report_mode,
            "risk_classification": state.risk_classification,
            "risk_classification_rationale": state.risk_classification_rationale,
        },
        "operator_supplied_evidence": sorted(
            (_effective_evidence(item) for item in state.imported_evidence),
            key=_canonical_json,
        ),
        "operator_supplied_signals": sorted(
            (_effective_signal(item) for item in state.imported_signals),
            key=_canonical_json,
        ),
        "authoritative_clarifications": clarifications,
        "consumed_governed_evidence": _consumed_governed_evidence(state),
    }


def effective_input_sha256(state: ProjectState) -> str:
    return _sha256(effective_input_payload(state))


def effective_input_identity(state: ProjectState) -> EffectiveInputIdentity:
    payload = effective_input_payload(state)
    digest = _sha256(payload)
    decision_id = primary_decision_id(state)
    snapshot_id = str(
        uuid.uuid5(
            _IDENTITY_NAMESPACE,
            f"{state.project_id}|{decision_id}|{EFFECTIVE_INPUT_CONTRACT_VERSION}|{digest}",
        )
    )
    return EffectiveInputIdentity(
        snapshot_id=snapshot_id,
        project_id=state.project_id,
        decision_id=decision_id,
        effective_input_sha256=digest,
        payload=payload,
    )


def analysis_state_payload(state: ProjectState) -> dict[str, Any]:
    state_payload = state.model_dump(mode="json")
    # The generation row is the authority for its own identity. Excluding the
    # compatibility binding avoids a circular content hash while preserving the
    # effective-input snapshot attribution inside the immutable state copy.
    state_payload.pop("analysis_generation_id", None)
    return {
        "contract_version": ANALYSIS_STATE_CONTRACT_VERSION,
        "state": state_payload,
    }


def analysis_state_sha256(state: ProjectState) -> str:
    return _sha256(analysis_state_payload(state))


def workflow_fingerprint(state: ProjectState, *, code_version: str) -> str:
    return _sha256(
        {
            "project_type": state.project_type,
            "phase_sequence": get_workflow_phase_sequence(state.project_type),
            "code_version": str(code_version or "unknown"),
        }
    )


def is_complete_analysis(state: ProjectState) -> bool:
    for phase in get_workflow_phase_sequence(state.project_type):
        status = state.phase_status.get(phase)
        normalized = status.value if hasattr(status, "value") else str(status or "")
        if normalized != PhaseStatus.COMPLETED.value or not _phase_has_output(state, phase):
            return False
    return True


async def create_candidate(
    pool: Any,
    state: ProjectState,
    *,
    workflow_identity: str,
    expected_base_generation_id: str | None,
) -> AnalysisGenerationIdentity:
    snapshot = effective_input_identity(state)
    generation_id = str(uuid.uuid4())
    previous_snapshot_id = state.effective_input_snapshot_id
    previous_generation_id = state.analysis_generation_id
    state.effective_input_snapshot_id = snapshot.snapshot_id
    state.analysis_generation_id = generation_id
    state_payload = analysis_state_payload(state)
    state_digest = _sha256(state_payload)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _insert_snapshot(conn, snapshot)
                await conn.execute(
                    """
                    INSERT INTO analysis_generations (
                        id, project_id, decision_id, effective_input_snapshot_id,
                        workflow_fingerprint, lifecycle_status,
                        expected_base_generation_id, analysis_state_sha256,
                        analysis_state_json
                    ) VALUES (
                        $1::uuid, $2::uuid, $3, $4::uuid,
                        $5, 'candidate', $6::uuid, $7, $8::jsonb
                    )
                    """,
                    generation_id,
                    state.project_id,
                    snapshot.decision_id,
                    snapshot.snapshot_id,
                    workflow_identity,
                    expected_base_generation_id,
                    state_digest,
                    _canonical_json(state_payload),
                )
    except Exception:
        state.effective_input_snapshot_id = previous_snapshot_id
        state.analysis_generation_id = previous_generation_id
        raise
    return AnalysisGenerationIdentity(
        generation_id=generation_id,
        project_id=state.project_id,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        analysis_state_sha256=state_digest,
        workflow_fingerprint=workflow_identity,
        status="candidate",
        expected_base_generation_id=expected_base_generation_id,
    )


async def validate_candidate(pool: Any, generation_id: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT lifecycle_status, analysis_state_json
                FROM analysis_generations
                WHERE id = $1::uuid
                FOR UPDATE
                """,
                generation_id,
            )
            if row is None or row["lifecycle_status"] != "candidate":
                raise StateCoherenceError("generation is not a candidate")
            payload = _load_json(row["analysis_state_json"])
            state = ProjectState.model_validate(payload.get("state"))
            if not is_complete_analysis(state):
                raise CandidateNotValidatedError("candidate analysis is incomplete")
            await conn.execute(
                """
                UPDATE analysis_generations
                SET validated_at = NOW()
                WHERE id = $1::uuid
                """,
                generation_id,
            )


async def abandon_candidate(pool: Any, generation_id: str, *, failed: bool) -> None:
    terminal = "failed" if failed else "aborted"
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """
                UPDATE analysis_generations
                SET lifecycle_status = $2, terminal_at = NOW()
                WHERE id = $1::uuid AND lifecycle_status = 'candidate'
                """,
                generation_id,
                terminal,
            )
            if result != "UPDATE 1":
                raise StateCoherenceError("generation is not an active candidate")


async def promote_candidate(
    pool: Any,
    generation_id: str,
    *,
    expected_base_generation_id: str | None,
) -> None:
    """Atomically promote a validated candidate using compare-and-swap currentness."""
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT promote_analysis_generation($1::uuid, $2::uuid)",
                    generation_id,
                    expected_base_generation_id,
                )
    except Exception as exc:
        message = str(exc)
        if "expected-base" in message or "current analysis changed" in message:
            raise StalePromotionError(message) from exc
        if "has not been validated" in message:
            raise CandidateNotValidatedError(message) from exc
        raise StateCoherenceError(message) from exc


async def bootstrap_current_analysis(pool: Any, state: ProjectState) -> str:
    """Lazily create one deterministic baseline for a completed pre-W8 project."""
    if not is_complete_analysis(state):
        raise CandidateNotValidatedError("cannot bootstrap an incomplete analysis")
    snapshot = effective_input_identity(state)
    state.effective_input_snapshot_id = snapshot.snapshot_id
    state_payload = analysis_state_payload(state)
    state_digest = _sha256(state_payload)
    generation_id = str(
        uuid.uuid5(
            _IDENTITY_NAMESPACE,
            f"baseline|{state.project_id}|{snapshot.decision_id}|{snapshot.snapshot_id}|{state_digest}",
        )
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.fetchval(
                """
                SELECT bootstrap_analysis_generation(
                    $1::uuid, $2::uuid, $3, $4, $5::jsonb, $6,
                    $7::uuid, $8, $9::jsonb
                )
                """,
                snapshot.snapshot_id,
                state.project_id,
                snapshot.decision_id,
                snapshot.effective_input_sha256,
                _canonical_json(snapshot.payload),
                EFFECTIVE_INPUT_CONTRACT_VERSION,
                generation_id,
                state_digest,
                _canonical_json(state_payload),
            )
    return _uuid_text(result) or ""


async def current_generation(pool: Any, project_id: str, decision_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT generation.id, generation.project_id, generation.decision_id,
                   generation.effective_input_snapshot_id,
                   generation.workflow_fingerprint, generation.lifecycle_status,
                   generation.analysis_state_sha256, generation.analysis_state_json,
                   generation.validated_at, generation.promoted_at,
                   generation.bootstrap_kind
            FROM current_analysis_generations current_generation
            JOIN analysis_generations generation
              ON generation.id = current_generation.generation_id
             AND generation.project_id = current_generation.project_id
             AND generation.decision_id = current_generation.decision_id
            WHERE current_generation.project_id = $1::uuid
              AND current_generation.decision_id = $2
            """,
            project_id,
            decision_id,
        )
    return dict(row) if row is not None else None


async def schema_available(pool: Any) -> bool:
    """Return whether the complete W8.1 relation set is installed."""
    async with pool.acquire() as conn:
        return await schema_available_conn(conn)


async def schema_available_conn(conn: Any) -> bool:
    row = await conn.fetchrow(
        """
        SELECT to_regclass('decision_input_snapshots') IS NOT NULL AS snapshots,
               to_regclass('analysis_generations') IS NOT NULL AS generations,
               to_regclass('current_analysis_generations') IS NOT NULL AS currentness
        """
    )
    if not row:
        return False
    try:
        return bool(row["snapshots"] and row["generations"] and row["currentness"])
    except (KeyError, TypeError):
        # Compatibility with bounded test/dry-run connections that do not
        # model catalog probes. A configured durable runtime still fails
        # closed at the caller when this returns False.
        return False


async def _insert_snapshot(conn: Any, snapshot: EffectiveInputIdentity) -> None:
    await conn.execute(
        """
        INSERT INTO decision_input_snapshots (
            id, project_id, decision_id, effective_input_sha256,
            effective_input_json, contract_version
        ) VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6)
        ON CONFLICT (project_id, decision_id, effective_input_sha256) DO NOTHING
        """,
        snapshot.snapshot_id,
        snapshot.project_id,
        snapshot.decision_id,
        snapshot.effective_input_sha256,
        _canonical_json(snapshot.payload),
        EFFECTIVE_INPUT_CONTRACT_VERSION,
    )


async def bind_effective_input(conn: Any, state: ProjectState) -> EffectiveInputIdentity:
    """Persist and bind the immutable input identity inside an existing transaction."""
    snapshot = effective_input_identity(state)
    if state.effective_input_snapshot_id and state.effective_input_snapshot_id != snapshot.snapshot_id:
        # A changed input cannot continue claiming attribution to the accepted
        # analysis produced from the old snapshot.
        state.analysis_generation_id = ""
    state.effective_input_snapshot_id = snapshot.snapshot_id
    await _insert_snapshot(conn, snapshot)
    return snapshot


def _consumed_governed_evidence(state: ProjectState) -> list[dict[str, Any]]:
    """Reference only projections actually admitted by existing authorities."""
    admitted: list[dict[str, Any]] = []
    for phase, phase_attestations in sorted((state.analysis_input_attestations or {}).items()):
        if not isinstance(phase_attestations, dict):
            continue
        knowledge = phase_attestations.get("knowledge") or {}
        if isinstance(knowledge, dict) and knowledge.get("status") == "used":
            admitted.append(
                {
                    "phase": phase,
                    "authority": "knowledge_retrieval",
                    "projection_fingerprint": str(knowledge.get("projection_fingerprint") or ""),
                    "policy_fingerprint": str(knowledge.get("policy_fingerprint") or ""),
                    "items": [
                        {
                            "item_id": str(item.get("item_id") or ""),
                            "source_id": str(item.get("source_id") or ""),
                            "projection_sha256": str(item.get("projection_sha256") or ""),
                        }
                        for item in (knowledge.get("items") or [])
                        if isinstance(item, dict)
                    ],
                }
            )
        research = phase_attestations.get("research_evidence") or {}
        if isinstance(research, dict) and research.get("status") == "used":
            admitted.append(
                {
                    "phase": phase,
                    "authority": "research_evidence_internal_analysis",
                    "usage_scope": str(research.get("usage_scope") or ""),
                    "projection_fingerprint": str(research.get("projection_fingerprint") or ""),
                    "policy_identifier": str(research.get("policy_identifier") or ""),
                    "policy_version": str(research.get("policy_version") or ""),
                    "policy_fingerprint": str(research.get("policy_fingerprint") or ""),
                    "source_snapshot_ids": sorted(
                        str(item.get("source_snapshot_id") or "")
                        for item in (research.get("sources") or [])
                        if isinstance(item, dict) and item.get("source_snapshot_id")
                    ),
                }
            )
    return admitted


def _effective_evidence(item: Any) -> dict[str, Any]:
    provenance = getattr(item, "provenance", None)
    return {
        "evidence_id": str(getattr(item, "evidence_id", "") or ""),
        "title": str(getattr(item, "title", "") or ""),
        "summary": str(getattr(item, "summary", "") or ""),
        "category": str(getattr(item, "category", "") or ""),
        "untrusted_source": bool(getattr(item, "untrusted_source", False)),
        "provenance": {
            "source_type": str(getattr(provenance, "source_type", "") or ""),
            "source_ref": str(getattr(provenance, "source_ref", "") or ""),
            "external_uri": str(getattr(provenance, "external_uri", "") or ""),
            "checksum": str(getattr(provenance, "checksum", "") or ""),
        },
    }


def _effective_signal(item: Any) -> dict[str, Any]:
    provenance = getattr(item, "provenance", None)
    return {
        "signal_id": str(getattr(item, "signal_id", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "description": str(getattr(item, "description", "") or ""),
        "kind": str(getattr(item, "kind", "") or ""),
        "confidence": getattr(item, "confidence", None),
        "cadence": str(getattr(item, "cadence", "") or ""),
        "provenance": {
            "source_type": str(getattr(provenance, "source_type", "") or ""),
            "source_ref": str(getattr(provenance, "source_ref", "") or ""),
            "external_uri": str(getattr(provenance, "external_uri", "") or ""),
            "checksum": str(getattr(provenance, "checksum", "") or ""),
        },
    }


def _phase_has_output(state: ProjectState, phase: str) -> bool:
    if phase == "audit":
        return state.audit is not None
    if phase == "strategy":
        return state.strategy is not None
    if phase == "report":
        return bool(state.report)
    value = getattr(state, phase, None)
    return bool(value) if isinstance(value, list) else value is not None


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


def _uuid_text(value: Any) -> str | None:
    return str(value) if value is not None else None
