"""Deterministic clarification helpers for missing decision inputs.

T1 clarifications are storage/display only. They do not call LLMs and do not
feed prompts, retrieval, reports, exports, gates, workflow routing, scenarios,
or calibration.
"""
from __future__ import annotations

from datetime import datetime
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
