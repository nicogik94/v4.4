"""Deterministic pre-finalization integrity checks (V4.4 pilot integrity P0-1/2/3).

Architecture
────────────
The model reasons, structured state carries its outputs, and this module is
deterministic code that reads *structured fields only*. It contains no natural
language understanding: no tokenizer, no stemmer, no similarity measure, no
inference from word overlap. Every decision below is an integer comparison, an
identifier lookup, or an enum check over ``ProjectState``.

P0-1 — a material SQI objection must never disappear silently
    ``material_sqi_findings`` reads the structured SQI fields (weakest link,
    scored dimensions, Rumelt sub-tests, conflicts, opposite test, must-be-true
    entries). Every material finding is carried into the finalized report. It is
    never asked whether some prose "already covered" it — the explicit
    structured resolution is that a re-run SQI phase no longer reports it, which
    is exactly what happens when the strategy is revised (editing strategy
    invalidates sqi/monitor/report, so SQI re-runs). Redundant surfacing is
    accepted; silent loss is not.

P0-2 — an impossible measurable criterion must not reach a gate
    ``assess_measurable_criteria`` compares declared integers:
    ``required_successes`` against ``population``, ``eligible_observations`` and
    ``observations_by_deadline``. Every candidate ends in exactly one of
    ``feasible`` / ``impossible`` / ``not_machine_checkable``; the third is
    reported, never silently dropped, and a criterion marked ``hard_gate`` that
    cannot be checked requires operator review. The one concession to prose is a
    single ratio regex over legacy ``success_metrics`` strings — anything it
    cannot read is ``not_machine_checkable``, never guessed at.

P0-3 — a hard control threshold must carry verifiable provenance
    ``classify_control_provenance`` reads the control object's own
    ``threshold_provenance`` record: an operator reference, an evidence
    identifier that must *resolve* against state, or declared inputs plus an
    operation that must *recompute* to the declared result. Numeric coincidence
    with some other text proves nothing and is not consulted. Anything else is
    ``unsupported_model_generated`` and is demoted to an explicit unverified
    proposal wherever the control surface is rendered.

Everything here is pure and offline; ``state`` is duck-typed so no workflow
module is imported.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "decision_integrity.v1"

# ══════════════════════════════════════════════════════════════════════════
# P0-1 — material SQI findings
# ══════════════════════════════════════════════════════════════════════════

KIND_WEAKEST_LINK = "weakest_link"
KIND_DIMENSION_FAILURE = "dimension_failure"
KIND_RUMELT_FAILURE = "rumelt_failure"
KIND_CONFLICT = "conflict"
KIND_OPPOSITE_TEST = "opposite_test"
KIND_MUST_BE_TRUE = "must_be_true"

SQI_FINDING_KINDS = (
    KIND_WEAKEST_LINK,
    KIND_DIMENSION_FAILURE,
    KIND_RUMELT_FAILURE,
    KIND_CONFLICT,
    KIND_OPPOSITE_TEST,
    KIND_MUST_BE_TRUE,
)

# A dimension is material when it scores as failing *and* says what is wrong;
# a failing score with no finding text carries nothing to reconcile.
MATERIAL_DIMENSION_SCORE = 60.0
MATERIAL_DIMENSION_GRADES = frozenset({"d", "e", "f"})

_RUMELT_TESTS = ("consistency", "consonance", "advantage", "feasibility")

# ``wwhtbt`` entries whose declared status is one of these are not objections.
_SUPPORTED_MUST_BE_TRUE_STATUSES = frozenset(
    {"likely true", "true", "supported", "confirmed", "validated"}
)


@dataclass(frozen=True)
class MaterialSQIFinding:
    finding_id: str
    kind: str
    source: str
    statement: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "kind": self.kind,
            "source": self.source,
            "statement": self.statement,
        }


def material_sqi_findings(state: Any) -> tuple[MaterialSQIFinding, ...]:
    """Material objections, read straight out of the structured SQI output."""
    sqi = getattr(state, "sqi", None)
    if sqi is None:
        return ()

    raw: list[tuple[str, str, str]] = []  # (kind, source, statement)

    weakest_link = _text(_get(sqi, "weakest_link")).strip()
    if weakest_link:
        raw.append((KIND_WEAKEST_LINK, "sqi.weakest_link", weakest_link))

    for index, dimension in enumerate(list(_get(sqi, "dimensions") or [])):
        statement = _text(_get(dimension, "finding")).strip()
        if not statement or not _dimension_is_failing(dimension):
            continue
        name = _text(_get(dimension, "name")).strip() or f"dimension {index}"
        raw.append((KIND_DIMENSION_FAILURE, f"sqi.dimensions[{index}]", f"{name}: {statement}"))

    rumelt = _get(sqi, "rumelt_test")
    for test_name in _RUMELT_TESTS:
        entry = _get(rumelt, test_name)
        if entry is None or _truthy(_get(entry, "pass")):
            continue
        # ``RumeltTest`` defaults every sub-test to ``{"pass": False, "note": ""}``,
        # so a test that never ran is indistinguishable from one that ran and
        # said nothing. Absent is not failed: without a note there is nothing to
        # carry forward.
        note = _text(_get(entry, "note")).strip()
        if note:
            raw.append((KIND_RUMELT_FAILURE, f"sqi.rumelt_test.{test_name}", f"{test_name}: {note}"))

    for index, conflict in enumerate(list(_get(sqi, "conflicts") or [])):
        statement = _flatten(conflict)
        if statement:
            raw.append((KIND_CONFLICT, f"sqi.conflicts[{index}]", statement))

    for index, entry in enumerate(list(_get(sqi, "opposite_test") or [])):
        if _truthy(_get(entry, "is_stupid")):
            continue
        statement = _flatten(entry)
        if statement:
            raw.append((KIND_OPPOSITE_TEST, f"sqi.opposite_test[{index}]", statement))

    for index, entry in enumerate(list(_get(sqi, "wwhtbt") or [])):
        status = " ".join(_text(_get(entry, "current_status")).strip().casefold().split())
        if status in _SUPPORTED_MUST_BE_TRUE_STATUSES:
            continue
        statement = _flatten(entry)
        if statement:
            raw.append((KIND_MUST_BE_TRUE, f"sqi.wwhtbt[{index}]", statement))

    return tuple(
        MaterialSQIFinding(
            finding_id=_stable_id("sqi", kind, source, statement),
            kind=kind,
            source=source,
            statement=_excerpt(statement, 600),
        )
        for kind, source, statement in raw
    )


def _dimension_is_failing(dimension: Any) -> bool:
    grade = _text(_get(dimension, "grade")).strip().casefold()
    if grade and grade[0] in MATERIAL_DIMENSION_GRADES:
        return True
    try:
        return float(_get(dimension, "score")) < MATERIAL_DIMENSION_SCORE
    except (TypeError, ValueError):
        return False


# ══════════════════════════════════════════════════════════════════════════
# P0-2 — measurable criteria feasibility
# ══════════════════════════════════════════════════════════════════════════

STATUS_FEASIBLE = "feasible"
STATUS_IMPOSSIBLE = "impossible"
STATUS_NOT_MACHINE_CHECKABLE = "not_machine_checkable"
CRITERION_STATUSES = (STATUS_FEASIBLE, STATUS_IMPOSSIBLE, STATUS_NOT_MACHINE_CHECKABLE)

CODE_REQUIRED_EXCEEDS_POPULATION = "required_exceeds_population"
CODE_REQUIRED_EXCEEDS_ELIGIBLE = "required_exceeds_eligible_observations"
CODE_ELIGIBLE_EXCEEDS_POPULATION = "eligible_exceeds_population"
CODE_DEADLINE_UNREACHABLE = "required_exceeds_observations_by_deadline"

IMPOSSIBILITY_CODES = (
    CODE_REQUIRED_EXCEEDS_POPULATION,
    CODE_REQUIRED_EXCEEDS_ELIGIBLE,
    CODE_ELIGIBLE_EXCEEDS_POPULATION,
    CODE_DEADLINE_UNREACHABLE,
)

# The only prose this module reads: an explicit "N of M" ratio in a legacy
# free-text success metric. Anything else is not_machine_checkable.
_RATIO_RX = re.compile(r"\b(\d{1,4})\s*(?:/|of|out\s+of|de)\s*(\d{1,4})\b", re.I)


@dataclass(frozen=True)
class CriterionAssessment:
    criterion_id: str
    source: str
    statement: str
    status: str
    hard_gate: bool = False
    code: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "source": self.source,
            "statement": self.statement,
            "status": self.status,
            "hard_gate": self.hard_gate,
            "code": self.code,
            "message": self.message,
        }


def assess_measurable_criteria(state: Any) -> tuple[CriterionAssessment, ...]:
    """Give every candidate criterion exactly one explicit status."""
    strategy = getattr(state, "strategy", None)
    if strategy is None:
        return ()

    assessments: list[CriterionAssessment] = []

    for index, criterion in enumerate(list(_get(strategy, "measurable_criteria") or [])):
        source = f"strategy.measurable_criteria[{index}]"
        assessments.append(
            _assess_numbers(
                source=source,
                criterion_id=_text(_get(criterion, "criterion_id")).strip()
                or _stable_id("crit", source, _text(_get(criterion, "statement"))),
                statement=_excerpt(_get(criterion, "statement"), 300),
                required=_intish(_get(criterion, "required_successes")),
                population=_intish(_get(criterion, "population")),
                eligible=_intish(_get(criterion, "eligible_observations")),
                by_deadline=_intish(_get(criterion, "observations_by_deadline")),
                hard_gate=bool(_get(criterion, "hard_gate")),
            )
        )

    for index, metric in enumerate(list(_get(strategy, "success_metrics") or [])):
        statement = _text(metric).strip()
        if not statement:
            continue
        source = f"strategy.success_metrics[{index}]"
        required, population = _parse_ratio(statement)
        assessments.append(
            _assess_numbers(
                source=source,
                criterion_id=_stable_id("crit", source, statement),
                statement=_excerpt(statement, 300),
                required=required,
                population=population,
                eligible=None,
                by_deadline=None,
                # A legacy free-text metric declares no gate status, so it stays
                # advisory rather than being promoted to a control.
                hard_gate=False,
            )
        )

    return tuple(assessments)


def _assess_numbers(
    *,
    source: str,
    criterion_id: str,
    statement: str,
    required: int | None,
    population: int | None,
    eligible: int | None,
    by_deadline: int | None,
    hard_gate: bool,
) -> CriterionAssessment:
    def impossible(code: str, message: str) -> CriterionAssessment:
        return CriterionAssessment(
            criterion_id=criterion_id,
            source=source,
            statement=statement,
            status=STATUS_IMPOSSIBLE,
            hard_gate=hard_gate,
            code=code,
            message=message,
        )

    if eligible is not None and population is not None and eligible > population:
        return impossible(
            CODE_ELIGIBLE_EXCEEDS_POPULATION,
            f"{eligible} eligible observations declared over a population of {population}.",
        )
    if required is not None:
        if population is not None and required > population:
            return impossible(
                CODE_REQUIRED_EXCEEDS_POPULATION,
                f"{required} successes required from a population of {population}.",
            )
        if eligible is not None and required > eligible:
            return impossible(
                CODE_REQUIRED_EXCEEDS_ELIGIBLE,
                f"{required} successes required from only {eligible} eligible observation(s).",
            )
        if by_deadline is not None and required > by_deadline:
            return impossible(
                CODE_DEADLINE_UNREACHABLE,
                f"{required} successes required by the deadline, but only "
                f"{by_deadline} observation(s) occur by then.",
            )
        if population is not None or eligible is not None or by_deadline is not None:
            return CriterionAssessment(
                criterion_id=criterion_id,
                source=source,
                statement=statement,
                status=STATUS_FEASIBLE,
                hard_gate=hard_gate,
            )

    return CriterionAssessment(
        criterion_id=criterion_id,
        source=source,
        statement=statement,
        status=STATUS_NOT_MACHINE_CHECKABLE,
        hard_gate=hard_gate,
        message="No declared counts to check; feasibility was not established.",
    )


def _parse_ratio(statement: str) -> tuple[int | None, int | None]:
    match = _RATIO_RX.search(statement)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def impossible_criteria(state: Any) -> tuple[CriterionAssessment, ...]:
    return tuple(c for c in assess_measurable_criteria(state) if c.status == STATUS_IMPOSSIBLE)


def unchecked_hard_gates(state: Any) -> tuple[CriterionAssessment, ...]:
    return tuple(
        c
        for c in assess_measurable_criteria(state)
        if c.status == STATUS_NOT_MACHINE_CHECKABLE and c.hard_gate
    )


# ══════════════════════════════════════════════════════════════════════════
# P0-3 — hard control threshold provenance
# ══════════════════════════════════════════════════════════════════════════

PROVENANCE_OPERATOR_STATED = "operator_stated"
PROVENANCE_SOURCE_EVIDENCE = "source_evidence_backed"
PROVENANCE_REPRODUCIBLE_DERIVED = "reproducible_derived"
PROVENANCE_UNSUPPORTED = "unsupported_model_generated"
# A citation that resolves but whose linkage to the numeric value this build
# cannot verify. Distinct from ``unsupported_model_generated``: the model did
# cite something real, and an operator can close the gap. Not authoritative.
PROVENANCE_REQUIRES_OPERATOR_REVIEW = "requires_operator_review"

PROVENANCE_CLASSES = (
    PROVENANCE_OPERATOR_STATED,
    PROVENANCE_SOURCE_EVIDENCE,
    PROVENANCE_REPRODUCIBLE_DERIVED,
    PROVENANCE_REQUIRES_OPERATOR_REVIEW,
    PROVENANCE_UNSUPPORTED,
)

# Only these may act as an authoritative hard control.
AUTHORITATIVE_PROVENANCE = (
    PROVENANCE_OPERATOR_STATED,
    PROVENANCE_SOURCE_EVIDENCE,
    PROVENANCE_REPRODUCIBLE_DERIVED,
)

ADVISORY_THRESHOLD_PREFIX = "Proposed threshold (unverified, requires operator confirmation)"

DERIVATION_OPERATIONS = ("sum", "product", "difference", "quotient", "mean")
_DERIVATION_TOLERANCE = 1e-6

_DIGIT_RX = re.compile(r"\d")


@dataclass(frozen=True)
class ControlThresholdAssessment:
    control: str
    source: str
    statement: str
    provenance_class: str
    reason: str = ""

    @property
    def is_authoritative(self) -> bool:
        return self.provenance_class in AUTHORITATIVE_PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "source": self.source,
            "statement": self.statement,
            "provenance_class": self.provenance_class,
            "reason": self.reason,
        }


def classify_control_provenance(control: Any, state: Any) -> tuple[str, str]:
    """Classify a control object's declared provenance, verifying its data."""
    record = _get(control, "threshold_provenance")
    if record is None:
        return PROVENANCE_UNSUPPORTED, "No threshold provenance was declared."

    declared = _text(_get(record, "provenance_class")).strip()
    if declared == PROVENANCE_OPERATOR_STATED:
        reference = _text(_get(record, "operator_reference")).strip()
        if reference:
            return PROVENANCE_OPERATOR_STATED, f"Operator reference: {_excerpt(reference, 120)}"
        return PROVENANCE_UNSUPPORTED, "operator_stated declared without an operator reference."

    if declared == PROVENANCE_SOURCE_EVIDENCE:
        return _verify_source_evidence(record, state)

    if declared == PROVENANCE_REPRODUCIBLE_DERIVED:
        return _verify_derivation(_get(record, "derivation"))

    if declared:
        return PROVENANCE_UNSUPPORTED, f"Unrecognised provenance class {declared!r}."
    return PROVENANCE_UNSUPPORTED, "No threshold provenance class was declared."


def _verify_source_evidence(record: Any, state: Any) -> tuple[str, str]:
    """A resolving citation is identity, not support.

    Authoritative only when the *existing* structured state proves the link
    between the exact control value and the cited claim: the record must name
    the numeric ``threshold_value`` it is claiming, and the cited knowledge item
    must carry that same value under the ``evidence_value_key`` the record
    points at. That is a declared pointer into structured evidence checked by
    equality — not digit coincidence, token overlap or any semantic judgment.

    Everything short of that verifies as ``requires_operator_review``: the
    citation may well be correct, but this build cannot prove it, and an
    unprovable number must not act as a hard control. Today's evidence schema
    carries structured claim values only on ``KnowledgeItem.structured_payload``,
    so source-backed thresholds fail closed more often than they will once
    richer structured claims exist.
    """
    evidence_id = _text(_get(record, "evidence_id")).strip()
    if not evidence_id:
        return PROVENANCE_UNSUPPORTED, "source_evidence_backed declared without an evidence id."
    if evidence_id not in known_evidence_ids(state):
        # A citation that resolves to nothing is a fabrication, not a gap.
        return (
            PROVENANCE_UNSUPPORTED,
            f"Evidence id {evidence_id!r} does not resolve to supplied evidence.",
        )

    threshold_value = _floatish(_get(record, "threshold_value"))
    value_key = _text(_get(record, "evidence_value_key")).strip()
    if threshold_value is None or not value_key:
        return (
            PROVENANCE_REQUIRES_OPERATOR_REVIEW,
            f"Evidence id {evidence_id} resolves, but the record does not name the "
            "threshold value and the evidence field that carries it, so the citation "
            "proves traceability only.",
        )

    claimed = _structured_claim_value(state, evidence_id, value_key)
    if claimed is None:
        return (
            PROVENANCE_REQUIRES_OPERATOR_REVIEW,
            f"Evidence {evidence_id} carries no structured value at {value_key!r}, "
            "so the threshold cannot be verified against it.",
        )
    if abs(claimed - threshold_value) > _DERIVATION_TOLERANCE:
        return (
            PROVENANCE_UNSUPPORTED,
            f"Evidence {evidence_id} states {claimed:g} at {value_key!r}, "
            f"not the claimed threshold {threshold_value:g}.",
        )
    return (
        PROVENANCE_SOURCE_EVIDENCE,
        f"Evidence {evidence_id} states {claimed:g} at {value_key!r}.",
    )


def _structured_claim_value(state: Any, evidence_id: str, value_key: str) -> float | None:
    """The numeric claim a cited knowledge item carries under ``value_key``."""
    knowledge = getattr(state, "knowledge_layer", None)
    for item in list(_get(knowledge, "items") or []):
        identifiers = {
            _text(_get(item, "evidence_id")).strip(),
            _text(_get(item, "item_id")).strip(),
        }
        if evidence_id not in identifiers:
            continue
        payload = _get(item, "structured_payload")
        if not isinstance(payload, dict) or value_key not in payload:
            continue
        value = _floatish(payload[value_key])
        if value is not None:
            return value
    return None


def _verify_derivation(derivation: Any) -> tuple[str, str]:
    if derivation is None:
        return PROVENANCE_UNSUPPORTED, "reproducible_derived declared without a derivation."
    inputs = [value for value in (_floatish(item) for item in list(_get(derivation, "inputs") or [])) if value is not None]
    operation = _text(_get(derivation, "operation")).strip().casefold()
    result = _floatish(_get(derivation, "result"))
    if not inputs or result is None or operation not in DERIVATION_OPERATIONS:
        return PROVENANCE_UNSUPPORTED, "Derivation is missing inputs, operation, or result."
    computed = _compute(operation, inputs)
    if computed is None:
        return PROVENANCE_UNSUPPORTED, f"Derivation operation {operation!r} could not be evaluated."
    if abs(computed - result) > _DERIVATION_TOLERANCE:
        return (
            PROVENANCE_UNSUPPORTED,
            f"Derivation does not reproduce the stated value ({operation} of inputs is {computed:g}, not {result:g}).",
        )
    return PROVENANCE_REPRODUCIBLE_DERIVED, f"{operation} of {inputs} = {result:g}"


def _compute(operation: str, inputs: list[float]) -> float | None:
    if operation == "sum":
        return float(sum(inputs))
    if operation == "mean":
        return float(sum(inputs)) / len(inputs)
    if operation == "product":
        total = 1.0
        for value in inputs:
            total *= value
        return total
    if operation == "difference":
        total = inputs[0]
        for value in inputs[1:]:
            total -= value
        return total
    if operation == "quotient":
        total = inputs[0]
        for value in inputs[1:]:
            if value == 0:
                return None
            total /= value
        return total
    return None


def known_evidence_ids(state: Any) -> frozenset[str]:
    """Identifiers a threshold may cite, collected from structured state."""
    ids: set[str] = set()
    for evidence in list(getattr(state, "imported_evidence", None) or []):
        ids.add(_text(_get(evidence, "evidence_id")).strip())
    knowledge = getattr(state, "knowledge_layer", None)
    for item in list(_get(knowledge, "items") or []):
        ids.add(_text(_get(item, "evidence_id")).strip())
        ids.add(_text(_get(item, "item_id")).strip())
    objects = getattr(state, "decision_objects", None)
    for evidence in list(_get(objects, "evidences") or []):
        ids.add(_text(_get(evidence, "evidence_id")).strip())
    ids.discard("")
    return frozenset(ids)


def assess_control_thresholds(state: Any) -> tuple[ControlThresholdAssessment, ...]:
    """Classify every monitor-proposed hard control."""
    monitor = getattr(state, "monitor", None)
    if monitor is None:
        return ()
    assessments: list[ControlThresholdAssessment] = []
    for index, breaker in enumerate(list(_get(monitor, "circuit_breakers") or [])):
        statement = _text(_get(breaker, "trip")).strip()
        if not carries_numeric_threshold(statement):
            continue
        provenance_class, reason = classify_control_provenance(breaker, state)
        assessments.append(
            ControlThresholdAssessment(
                control="circuit_breaker",
                source=f"monitor.circuit_breakers[{index}].trip",
                statement=_excerpt(statement, 240),
                provenance_class=provenance_class,
                reason=reason,
            )
        )
    for index, canary in enumerate(list(_get(monitor, "canaries") or [])):
        statement = " ".join(
            part for part in (_text(_get(canary, "signal")), _text(_get(canary, "meaning"))) if part
        ).strip()
        if not carries_numeric_threshold(statement):
            continue
        provenance_class, reason = classify_control_provenance(canary, state)
        assessments.append(
            ControlThresholdAssessment(
                control="canary",
                source=f"monitor.canaries[{index}]",
                statement=_excerpt(statement, 240),
                provenance_class=provenance_class,
                reason=reason,
            )
        )
    return tuple(assessments)


def unverified_control_thresholds(state: Any) -> tuple[ControlThresholdAssessment, ...]:
    """Every control threshold that may not act as an authoritative gate."""
    return tuple(a for a in assess_control_thresholds(state) if not a.is_authoritative)


def carries_numeric_threshold(value: Any) -> bool:
    """A threshold is a numeric trigger; text without a digit states none."""
    return bool(_DIGIT_RX.search(_text(value)))


def control_surface_threshold(value: Any, provenance_class: str) -> str:
    """The string that may occupy a hard control cell.

    The figure is preserved and relabelled rather than deleted: an unsourced
    proposal is still a proposal an operator may want to confirm.
    """
    text = _text(value)
    if provenance_class in AUTHORITATIVE_PROVENANCE:
        return text
    if not carries_numeric_threshold(text) or ADVISORY_THRESHOLD_PREFIX in text:
        return text
    return f"{ADVISORY_THRESHOLD_PREFIX}: {text.strip()}"


# ══════════════════════════════════════════════════════════════════════════
# Finalization, gates and certification
# ══════════════════════════════════════════════════════════════════════════

SURFACED_SECTION_MARKER = "<!-- mas:deterministic-integrity-findings -->"

_HEADINGS = {
    "en": (
        "Deterministic integrity findings",
        "Unresolved strategy objections",
        "Measurable criteria that cannot be satisfied as written",
        "Measurable criteria that could not be checked automatically",
    ),
    "es-MX": (
        "Hallazgos deterministas de integridad",
        "Objeciones de estrategia sin resolver",
        "Criterios medibles que no se pueden cumplir tal como están escritos",
        "Criterios medibles que no se pudieron verificar automáticamente",
    ),
}


class DecisionIntegrityError(RuntimeError):
    """Raised by certification paths when a run cannot be certified."""


@dataclass(frozen=True)
class DecisionIntegrityReport:
    schema_version: str
    sqi_findings: tuple[MaterialSQIFinding, ...] = ()
    impossible_criteria: tuple[CriterionAssessment, ...] = ()
    unchecked_hard_gates: tuple[CriterionAssessment, ...] = ()
    unverified_thresholds: tuple[ControlThresholdAssessment, ...] = ()
    surfaced: bool = False

    @property
    def clean(self) -> bool:
        return not (
            self.sqi_findings
            or self.impossible_criteria
            or self.unchecked_hard_gates
            or self.unverified_thresholds
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "surfaced": self.surfaced,
            "sqi_findings": [item.as_dict() for item in self.sqi_findings],
            "impossible_criteria": [item.as_dict() for item in self.impossible_criteria],
            "unchecked_hard_gates": [item.as_dict() for item in self.unchecked_hard_gates],
            "unverified_thresholds": [item.as_dict() for item in self.unverified_thresholds],
        }


def build_decision_integrity_report(state: Any) -> DecisionIntegrityReport:
    """Read-only projection over structured state."""
    return DecisionIntegrityReport(
        schema_version=SCHEMA_VERSION,
        sqi_findings=material_sqi_findings(state),
        impossible_criteria=impossible_criteria(state),
        unchecked_hard_gates=unchecked_hard_gates(state),
        unverified_thresholds=unverified_control_thresholds(state),
    )


def apply_decision_integrity(state: Any) -> DecisionIntegrityReport:
    """Surface the findings on the finalized report; idempotent.

    This is a state transition performed by the orchestrator/API at report
    finalization, never by an exporter.
    """
    original = _text(getattr(state, "report", ""))
    stripped = strip_surfaced_section(original)
    if stripped != original:
        state.report = stripped

    report = build_decision_integrity_report(state)
    body = _render_section(report, _report_language(state))
    if body and _text(getattr(state, "report", "")).strip():
        state.report = _text(getattr(state, "report", "")).rstrip() + "\n\n" + body
        report = DecisionIntegrityReport(
            schema_version=report.schema_version,
            sqi_findings=report.sqi_findings,
            impossible_criteria=report.impossible_criteria,
            unchecked_hard_gates=report.unchecked_hard_gates,
            unverified_thresholds=report.unverified_thresholds,
            surfaced=True,
        )
        _log_event(state, "decision_integrity_surfaced", report.as_dict())
    return report


def strip_surfaced_section(report: Any) -> str:
    text = _text(report)
    index = text.find(SURFACED_SECTION_MARKER)
    if index < 0:
        return text
    head = text[:index]
    return head.rstrip() + "\n" if head.strip() else ""


def _render_section(report: DecisionIntegrityReport, language: str) -> str:
    title, objections, impossible, unchecked = _HEADINGS.get(language, _HEADINGS["en"])
    blocks: list[str] = []
    if report.sqi_findings:
        blocks.append(
            f"### {objections}\n"
            + "\n".join(f"- {finding.statement}" for finding in report.sqi_findings)
        )
    if report.impossible_criteria:
        blocks.append(
            f"### {impossible}\n"
            + "\n".join(
                f"- {item.message} ({item.statement})" if item.statement else f"- {item.message}"
                for item in report.impossible_criteria
            )
        )
    if report.unchecked_hard_gates:
        blocks.append(
            f"### {unchecked}\n"
            + "\n".join(f"- {item.statement or item.criterion_id}" for item in report.unchecked_hard_gates)
        )
    if not blocks:
        return ""
    return f"{SURFACED_SECTION_MARKER}\n## {title}\n\n" + "\n\n".join(blocks) + "\n"


def delivery_blocking_reasons(state: Any) -> list[str]:
    """A criterion no run of the plan can satisfy cannot be reviewed against."""
    return [
        f"Impossible measurable criterion ({item.code}): {item.message}"
        for item in impossible_criteria(state)
    ]


def delivery_review_warnings(state: Any) -> list[str]:
    warnings = [
        "Hard gate criterion could not be checked automatically and needs operator "
        f"review before it is treated as a gate: {item.statement or item.criterion_id}"
        for item in unchecked_hard_gates(state)
    ]
    warnings.extend(
        f"Control threshold is not authoritative ({item.source}): {item.reason}"
        for item in unverified_control_thresholds(state)
    )
    return warnings


def require_decision_integrity(state: Any) -> DecisionIntegrityReport:
    """Fail closed for callers that must not certify an unsound plan.

    A hard gate the system cannot evaluate is not certifiable: an unevaluable
    control is indistinguishable from an unmet one, so it blocks certification
    pending operator review rather than passing silently. A criterion that is
    merely advisory does not block — nothing is gating on it.
    """
    report = build_decision_integrity_report(state)
    problems = [
        f"Impossible measurable criterion ({item.code}): {item.message}"
        for item in report.impossible_criteria
    ]
    problems.extend(
        "Hard gate criterion is not machine-checkable and cannot be certified "
        f"({item.source}): {item.statement or item.criterion_id}"
        for item in report.unchecked_hard_gates
    )
    problems.extend(
        f"Control threshold is not authoritative ({item.source}): {item.reason}"
        for item in report.unverified_thresholds
    )
    if problems:
        raise DecisionIntegrityError("; ".join(problems))
    return report


# ══════════════════════════════════════════════════════════════════════════
# small shared helpers
# ══════════════════════════════════════════════════════════════════════════


def _report_language(state: Any) -> str:
    for name in ("report_output_language", "output_language"):
        value = _text(getattr(state, name, "")).strip()
        if value in _HEADINGS:
            return value
    return "en"


def _log_event(state: Any, event_type: str, details: dict[str, Any]) -> None:
    try:
        from policy import log_policy_event

        log_policy_event(state, event_type, details)
    except Exception:  # pragma: no cover - audit logging is best effort
        pass


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _get(container: Any, name: str, default: Any = None) -> Any:
    if container is None:
        return default
    if isinstance(container, dict):
        return container.get(name, default)
    return getattr(container, name, default)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in sorted(value.items()) if _text(item))
    return str(value)


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return _excerpt(
            "; ".join(
                f"{key}: {_text(item)}"
                for key, item in sorted(value.items())
                if _text(item) and not isinstance(item, bool)
            ),
            600,
        )
    return _excerpt(_text(value), 600)


def _excerpt(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", _text(value)).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "si", "sí"}
    return bool(value)


def _intish(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _floatish(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
