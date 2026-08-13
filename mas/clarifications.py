"""Deterministic clarification helpers for missing decision inputs.

T1 clarifications are storage/display only. They do not call LLMs and do not
feed prompts, retrieval, reports, exports, gates, workflow routing, scenarios,
or calibration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field


class ClarificationPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClarificationStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"
    SUPERSEDED = "superseded"


class ClarificationQuestion(BaseModel):
    question_id: str
    text: str
    why_it_matters: str
    priority: ClarificationPriority
    affected_phase: str
    source_gap: str
    status: ClarificationStatus = ClarificationStatus.OPEN
    created_at: datetime = Field(default_factory=datetime.now)


class ClarificationAnswer(BaseModel):
    answer_id: str
    question_id: str
    answer_text: str = ""
    status: ClarificationStatus
    answered_at: datetime = Field(default_factory=datetime.now)


class ClarificationCycle(BaseModel):
    cycle_id: str
    project_id: str
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    summary: str = ""


class ClarificationReviewRow(BaseModel):
    question_id: str = ""
    text: str = ""
    why_it_matters: str = ""
    priority: str = ""
    affected_phase: str = ""
    source_gap: str = ""
    status: str = ""
    required: bool = False
    answer_preview: str = ""
    answered_at: str = ""
    created_at: str = ""
    refresh_candidate: bool = False
    refresh_reason: str = ""


class ClarificationSummary(BaseModel):
    total_cycles: int = 0
    latest_cycle_id: str = ""
    total_questions: int = 0
    open_count: int = 0
    answered_count: int = 0
    unavailable_count: int = 0
    superseded_count: int = 0
    open_required_count: int = 0
    resolution_rate: float = 1.0
    affected_phases: list[str] = Field(default_factory=list)
    latest_cycle_status: str = "not_generated"
    next_action: str = "Generate missing-information questions before treating the analysis as fully reviewed."
    refresh_candidate_phases: list[str] = Field(default_factory=list)
    review_rows: list[ClarificationReviewRow] = Field(default_factory=list)


_QUESTION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source_gap": "decision_deadline",
        "priority": ClarificationPriority.CRITICAL,
        "affected_phase": "classify",
        "text": "What is the decision deadline or time window?",
        "why_it_matters": "The engine needs timing context to separate urgent tradeoffs from decisions that can wait for more evidence.",
        "pattern": re.compile(
            r"\b(deadline|due|time\s*window|timeline|eod|today|tomorrow|this\s+week|"
            r"this\s+(month|quarter)|next\s+(week|month|quarter)|"
            r"within\s+\d+\s*(days?|weeks?|months?|quarters?)|"
            r"q[1-4]|fy\d{2,4}|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|"
            r"jul(y)?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|"
            r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2})\b",
            re.I,
        ),
    },
    {
        "source_gap": "success_metric",
        "priority": ClarificationPriority.CRITICAL,
        "affected_phase": "strategy",
        "text": "What success metric or target outcome should define a good decision?",
        "why_it_matters": "Without a success measure, later strategy and monitoring outputs can optimize for the wrong result.",
        "pattern": re.compile(
            r"(%|\$|\b\d+(?:\.\d+)?\b|\b(metric|kpi|outcome|target|threshold|revenue|"
            r"cost|conversion|churn|risk|margin|retention|growth|savings|roi|success)\b)",
            re.I,
        ),
    },
    {
        "source_gap": "alternatives_options",
        "priority": ClarificationPriority.HIGH,
        "affected_phase": "hypotheses",
        "text": "What alternatives or options are being compared?",
        "why_it_matters": "Hypothesis generation is stronger when the choice set is explicit instead of inferred.",
        "pattern": re.compile(
            r"\b(option|options|alternative|alternatives|vs|versus|between|or|pivot|"
            r"continue|build|buy|launch|delay|keep|switch)\b",
            re.I,
        ),
    },
    {
        "source_gap": "constraints",
        "priority": ClarificationPriority.HIGH,
        "affected_phase": "audit",
        "text": "What hard constraints or dependencies limit the decision?",
        "why_it_matters": "Audit and stress testing need constraints so they do not recommend actions the operator cannot take.",
        "pattern": re.compile(
            r"\b(legal|regulatory|compliance|timeline|dependency|dependencies|capacity|"
            r"constraint|constraints|must|cannot|can't|blocked|approval|required)\b",
            re.I,
        ),
    },
    {
        "source_gap": "stakeholder_audience",
        "priority": ClarificationPriority.MEDIUM,
        "affected_phase": "classify",
        "text": "Who is the target stakeholder, audience, or decision owner?",
        "why_it_matters": "Stakeholder context changes the framing, risk tolerance, and level of explanation required.",
        "pattern": re.compile(
            r"\b(stakeholder|customer|client|user|audience|team|board|executive|"
            r"operator|market|owner|buyer|leadership)\b",
            re.I,
        ),
    },
    {
        "source_gap": "evidence_source_material",
        "priority": ClarificationPriority.HIGH,
        "affected_phase": "audit",
        "text": "What evidence or source material should the analysis consider?",
        "why_it_matters": "The audit phase needs source material to distinguish measured evidence from assumptions.",
        "pattern": re.compile(
            r"\b(evidence|source|sources|report|survey|interview|analytics|dashboard|"
            r"data|dataset|spreadsheet|document|research)\b",
            re.I,
        ),
    },
    {
        "source_gap": "monitoring_kill_criteria",
        "priority": ClarificationPriority.MEDIUM,
        "affected_phase": "monitor",
        "text": "What monitoring signal, trigger, or kill criterion should be watched?",
        "why_it_matters": "Monitoring criteria help define when the decision should continue, pause, roll back, or be revisited.",
        "pattern": re.compile(
            r"\b(monitor|watch|threshold|kill|stop|rollback|canary|trigger|alert|"
            r"trip|signal|circuit\s*breaker)\b",
            re.I,
        ),
    },
    {
        "source_gap": "budget_resource_constraints",
        "priority": ClarificationPriority.HIGH,
        "affected_phase": "strategy",
        "text": "What budget, resource, or capacity constraint applies?",
        "why_it_matters": "Strategy recommendations need resource bounds to stay actionable.",
        "pattern": re.compile(
            r"(\$|\b(budget|cost|resource|resources|team|headcount|runway|capacity|"
            r"fte|hours|staff|funding|spend)\b)",
            re.I,
        ),
    },
)


def generate_clarification_cycle(state: Any) -> ClarificationCycle:
    """Generate a deterministic clarification cycle from current project state."""
    project_id = str(getattr(state, "project_id", "") or "")
    text = _state_text(state)
    questions: list[ClarificationQuestion] = []

    for spec in _QUESTION_SPECS:
        source_gap = spec["source_gap"]
        has_signal = bool(spec["pattern"].search(text))
        if source_gap == "evidence_source_material" and _has_source_material(state):
            has_signal = True
        if has_signal:
            continue
        questions.append(
            ClarificationQuestion(
                question_id=_stable_question_id(project_id, source_gap),
                text=spec["text"],
                why_it_matters=spec["why_it_matters"],
                priority=spec["priority"],
                affected_phase=spec["affected_phase"],
                source_gap=source_gap,
            )
        )

    gap_key = "|".join(question.source_gap for question in questions) or "none"
    cycle = ClarificationCycle(
        cycle_id=f"clarification_cycle_{_digest(project_id, gap_key)}",
        project_id=project_id,
        questions=questions,
        summary=_cycle_summary(questions),
    )
    return cycle


def record_clarification_answer(
    state: Any,
    question_id: str,
    answer_text: str,
) -> ClarificationAnswer:
    """Record an operator answer and mark the referenced question answered."""
    clean = (answer_text or "").strip()
    if not clean:
        raise ValueError("answer_text is required when status is answered")
    question = _find_question(state, question_id)
    if question.status == ClarificationStatus.SUPERSEDED:
        raise ValueError("cannot answer a superseded clarification question")

    question.status = ClarificationStatus.ANSWERED
    answer = ClarificationAnswer(
        answer_id=_next_answer_id(state, question_id),
        question_id=question_id,
        answer_text=clean,
        status=ClarificationStatus.ANSWERED,
    )
    _answers(state).append(answer)
    return answer


def mark_clarification_unavailable(state: Any, question_id: str) -> ClarificationAnswer:
    """Mark a question unavailable when the operator cannot provide an answer."""
    question = _find_question(state, question_id)
    if question.status == ClarificationStatus.SUPERSEDED:
        raise ValueError("cannot mark a superseded clarification question unavailable")

    question.status = ClarificationStatus.UNAVAILABLE
    answer = ClarificationAnswer(
        answer_id=_next_answer_id(state, question_id),
        question_id=question_id,
        answer_text="Unavailable",
        status=ClarificationStatus.UNAVAILABLE,
    )
    _answers(state).append(answer)
    return answer


def open_clarification_questions(state: Any) -> list[ClarificationQuestion]:
    return [
        question
        for cycle in _cycles(state)
        for question in cycle.questions
        if question.status == ClarificationStatus.OPEN
    ]


def latest_clarification_cycle(state: Any) -> ClarificationCycle | None:
    cycles = _cycles(state)
    return cycles[-1] if cycles else None


def same_gap_set(first: ClarificationCycle | None, second: ClarificationCycle | None) -> bool:
    if first is None or second is None:
        return False
    return [q.source_gap for q in first.questions] == [q.source_gap for q in second.questions]


def supersede_open_questions(state: Any) -> None:
    for question in open_clarification_questions(state):
        question.status = ClarificationStatus.SUPERSEDED


def current_authoritative_answers(state: Any) -> list[Any]:
    """Return one unambiguous current ANSWERED record per question.

    Authority is determined from the existing clarification lifecycle.  List
    order is never authority: the newest ``answered_at`` wins, unavailable or
    superseded terminal records remove an older answer, and equal-time
    conflicts fail closed.  Exact equal-time ANSWERED duplicates collapse by
    stable identity.
    """
    grouped: dict[str, list[Any]] = {}
    for answer in _read_answers(state):
        question_id = _normalized_text(_get(answer, "question_id", ""))
        if question_id:
            grouped.setdefault(question_id, []).append(answer)

    selected: list[Any] = []
    for records in grouped.values():
        timestamped = [
            (answer, _authoritative_answer_datetime(_get(answer, "answered_at")))
            for answer in records
        ]
        if any(timestamp is None for _, timestamp in timestamped):
            if len(records) != 1:
                continue
            answer = records[0]
        else:
            latest_timestamp = max(timestamp for _, timestamp in timestamped)
            latest = [answer for answer, timestamp in timestamped if timestamp == latest_timestamp]
            if len(latest) > 1:
                statuses = {_normalized_status(_get(answer, "status", "")) for answer in latest}
                texts = {_normalized_text(_get(answer, "answer_text", "")) for answer in latest}
                if statuses != {ClarificationStatus.ANSWERED.value} or len(texts) != 1:
                    continue
                latest.sort(key=_authoritative_answer_sort_key)
            answer = latest[0]

        if (
            _normalized_status(_get(answer, "status", "")) == ClarificationStatus.ANSWERED.value
            and _normalized_text(_get(answer, "answer_text", ""))
        ):
            selected.append(answer)

    return sorted(selected, key=_authoritative_answer_sort_key)


def _authoritative_answer_sort_key(answer: Any) -> tuple[str, str, str, str]:
    timestamp = _authoritative_answer_datetime(_get(answer, "answered_at"))
    timestamp_text = timestamp.isoformat() if timestamp is not None else ""
    return (
        _normalized_text(_get(answer, "question_id", "")),
        _normalized_text(_get(answer, "answer_id", "")),
        timestamp_text,
        _normalized_text(_get(answer, "answer_text", "")),
    )


def _authoritative_answer_datetime(value: Any) -> datetime | None:
    """Normalize authority timestamps to UTC without discarding offsets."""
    raw = _value(value)
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_clarification_summary(state: Any) -> ClarificationSummary:
    """Build a read-only operator summary for clarification review state."""
    cycles = _read_cycles(state)
    answers = _read_answers(state)
    answer_by_question = _latest_answers_by_question(answers)
    latest = cycles[-1] if cycles else None
    latest_cycle_id = str(_value(_get(latest, "cycle_id", "")) or "") if latest else ""
    questions = [
        question
        for cycle in cycles
        for question in _iter_values(_get(cycle, "questions", []))
    ]
    latest_questions = list(_iter_values(_get(latest, "questions", []))) if latest else []

    rows: list[ClarificationReviewRow] = []
    status_counts = {status.value: 0 for status in ClarificationStatus}
    status_counts["open"] = 0
    refresh_phases: list[str] = []
    affected_phases: list[str] = []

    for question in questions:
        question_id = str(_value(_get(question, "question_id", "")) or "")
        status = _normalized_status(_get(question, "status", ClarificationStatus.OPEN))
        if status not in status_counts:
            status_counts[status] = 0
        status_counts[status] += 1

        priority = _normalized_text(_get(question, "priority", ""))
        affected_phase = _normalized_text(_get(question, "affected_phase", ""))
        if affected_phase and affected_phase not in affected_phases:
            affected_phases.append(affected_phase)
        answer = answer_by_question.get(question_id)
        answer_preview = _answer_preview(_get(answer, "answer_text", "")) if answer is not None else ""
        answered_at = _datetime_text(_get(answer, "answered_at", "")) if answer is not None else ""
        created_at = _datetime_text(_get(question, "created_at", ""))
        required = priority in {ClarificationPriority.CRITICAL.value, ClarificationPriority.HIGH.value}
        refresh_candidate = _answer_requires_refresh(state, affected_phase, answer)
        refresh_reason = (
            f"Answer saved after {affected_phase} last completed; review or rerun that phase when ready."
            if refresh_candidate and affected_phase
            else ""
        )
        if refresh_candidate and affected_phase and affected_phase not in refresh_phases:
            refresh_phases.append(affected_phase)
        rows.append(
            ClarificationReviewRow(
                question_id=question_id,
                text=str(_value(_get(question, "text", "")) or ""),
                why_it_matters=str(_value(_get(question, "why_it_matters", "")) or ""),
                priority=priority,
                affected_phase=affected_phase,
                source_gap=_normalized_text(_get(question, "source_gap", "")),
                status=status,
                required=required,
                answer_preview=answer_preview,
                answered_at=answered_at,
                created_at=created_at,
                refresh_candidate=refresh_candidate,
                refresh_reason=refresh_reason,
            )
        )

    latest_open_required = sum(
        1
        for question in latest_questions
        if _normalized_status(_get(question, "status", ClarificationStatus.OPEN)) == ClarificationStatus.OPEN.value
        and _normalized_text(_get(question, "priority", "")) in {ClarificationPriority.CRITICAL.value, ClarificationPriority.HIGH.value}
    )
    total_open = int(status_counts.get(ClarificationStatus.OPEN.value, 0) or 0)
    total_answered = int(status_counts.get(ClarificationStatus.ANSWERED.value, 0) or 0)
    total_unavailable = int(status_counts.get(ClarificationStatus.UNAVAILABLE.value, 0) or 0)
    total_superseded = int(status_counts.get(ClarificationStatus.SUPERSEDED.value, 0) or 0)
    actionable_total = total_open + total_answered + total_unavailable
    resolution_rate = 1.0 if actionable_total == 0 else round((total_answered + total_unavailable) / actionable_total, 4)

    affected_phases = _phase_ordered(affected_phases)
    refresh_phases = _phase_ordered(refresh_phases)
    latest_cycle_status = _latest_cycle_status(latest, latest_questions, latest_open_required)
    next_action = _clarification_next_action(
        total_cycles=len(cycles),
        total_questions=len(questions),
        open_count=total_open,
        open_required_count=latest_open_required,
        refresh_candidate_phases=refresh_phases,
    )

    return ClarificationSummary(
        total_cycles=len(cycles),
        latest_cycle_id=latest_cycle_id,
        total_questions=len(questions),
        open_count=total_open,
        answered_count=total_answered,
        unavailable_count=total_unavailable,
        superseded_count=total_superseded,
        open_required_count=latest_open_required,
        resolution_rate=resolution_rate,
        affected_phases=affected_phases,
        latest_cycle_status=latest_cycle_status,
        next_action=next_action,
        refresh_candidate_phases=refresh_phases,
        review_rows=rows,
    )


def _state_text(state: Any) -> str:
    parts: list[str] = [
        str(getattr(state, "brief", "") or ""),
        str(getattr(state, "data", "") or ""),
    ]
    observations = getattr(state, "observations", {}) or {}
    if isinstance(observations, dict):
        parts.extend(str(value) for value in observations.values())
    parts.extend(_evidence_texts(getattr(state, "imported_evidence", []) or []))
    parts.extend(_evidence_texts(getattr(state, "imported_signals", []) or []))
    layer = getattr(state, "knowledge_layer", None)
    if layer is not None:
        parts.extend(_evidence_texts(getattr(layer, "items", []) or []))
        parts.extend(_evidence_texts(getattr(layer, "uploaded_files", []) or []))
    return "\n".join(part for part in parts if part).lower()


def _evidence_texts(items: list[Any]) -> list[str]:
    texts: list[str] = []
    for item in items:
        for attr in ("title", "summary", "name", "description", "filename", "source_ref"):
            value = getattr(item, attr, "")
            if value:
                texts.append(str(value))
    return texts


def _has_source_material(state: Any) -> bool:
    if str(getattr(state, "data", "") or "").strip():
        return True
    if len(getattr(state, "imported_evidence", []) or []) > 0:
        return True
    if len(getattr(state, "imported_signals", []) or []) > 0:
        return True
    layer = getattr(state, "knowledge_layer", None)
    if layer is None:
        return False
    return bool((getattr(layer, "items", []) or []) or (getattr(layer, "uploaded_files", []) or []))


def _find_question(state: Any, question_id: str) -> ClarificationQuestion:
    for cycle in reversed(_cycles(state)):
        for question in cycle.questions:
            if question.question_id == question_id:
                return question
    raise KeyError(question_id)


def _cycles(state: Any) -> list[ClarificationCycle]:
    cycles = getattr(state, "clarification_cycles", None)
    if cycles is None:
        cycles = []
        setattr(state, "clarification_cycles", cycles)
    return cycles


def _answers(state: Any) -> list[ClarificationAnswer]:
    answers = getattr(state, "clarification_answers", None)
    if answers is None:
        answers = []
        setattr(state, "clarification_answers", answers)
    return answers


def _read_cycles(state: Any) -> list[Any]:
    value = getattr(state, "clarification_cycles", []) if state is not None else []
    return _iter_values(value)


def _read_answers(state: Any) -> list[Any]:
    value = getattr(state, "clarification_answers", []) if state is not None else []
    return _iter_values(value)


def _latest_answers_by_question(answers: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for answer in answers:
        question_id = str(_value(_get(answer, "question_id", "")) or "").strip()
        if question_id:
            result[question_id] = answer
    return result


def _answer_requires_refresh(state: Any, phase: str, answer: Any) -> bool:
    if not phase or answer is None:
        return False
    answer_dt = _parse_datetime(_get(answer, "answered_at", ""))
    completed_raw = (getattr(state, "phase_run_completed_at", {}) or {}).get(phase, "")
    completed_dt = _parse_datetime(completed_raw)
    if answer_dt is None or completed_dt is None:
        return False
    return answer_dt > completed_dt


def _latest_cycle_status(latest: Any, latest_questions: list[Any], open_required_count: int) -> str:
    if latest is None:
        return "not_generated"
    if not latest_questions:
        return "no_questions"
    statuses = [_normalized_status(_get(question, "status", ClarificationStatus.OPEN)) for question in latest_questions]
    if open_required_count:
        return "required_open"
    if any(status == ClarificationStatus.OPEN.value for status in statuses):
        return "optional_open"
    if statuses and all(status == ClarificationStatus.SUPERSEDED.value for status in statuses):
        return "superseded"
    return "resolved"


def _clarification_next_action(
    *,
    total_cycles: int,
    total_questions: int,
    open_count: int,
    open_required_count: int,
    refresh_candidate_phases: list[str],
) -> str:
    if total_cycles == 0:
        return "Generate missing-information questions before treating the analysis as fully reviewed."
    if total_questions == 0:
        return "No clarification action needed right now."
    if open_required_count:
        return "Answer critical/high clarification questions before regenerating or sharing outputs."
    if open_count:
        return "Answer remaining clarification questions or mark them unavailable."
    if refresh_candidate_phases:
        return f"Review saved clarification answers and rerun affected phase(s): {', '.join(refresh_candidate_phases)}."
    return "Review saved clarification answers before final delivery."


def _phase_ordered(phases: list[str]) -> list[str]:
    order = ["classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"]
    seen = {phase for phase in phases if phase}
    ordered = [phase for phase in order if phase in seen]
    ordered.extend(sorted(phase for phase in seen if phase not in order))
    return ordered


def _normalized_status(value: Any) -> str:
    normalized = _normalized_text(value)
    return normalized or ClarificationStatus.OPEN.value


def _normalized_text(value: Any) -> str:
    return str(_value(value) or "").strip().lower()


def _answer_preview(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(_value(value) or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _datetime_text(value: Any) -> str:
    raw = _value(value)
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw or "")


def _parse_datetime(value: Any) -> datetime | None:
    raw = _value(value)
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _get(item: Any, field_name: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(field_name, default)
    return getattr(item, field_name, default)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iter_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _next_answer_id(state: Any, question_id: str) -> str:
    ordinal = 1 + sum(1 for answer in _answers(state) if answer.question_id == question_id)
    return f"clarification_answer_{_digest(question_id, str(ordinal))}"


def _stable_question_id(project_id: str, source_gap: str) -> str:
    return f"clarification_question_{_digest(project_id, source_gap)}"


def _cycle_summary(questions: list[ClarificationQuestion]) -> str:
    if not questions:
        return "No deterministic missing-information questions right now."
    critical_count = sum(1 for question in questions if question.priority == ClarificationPriority.CRITICAL)
    return f"{len(questions)} deterministic missing-information question(s), {critical_count} critical."


def _digest(*parts: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]
