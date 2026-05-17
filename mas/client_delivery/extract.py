"""Tolerant extraction from ProjectState-like payloads into DeliveryPackage."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    CriticalAssumption,
    DeliveryPackage,
    ExecutionAction,
    KPI,
    Recommendation,
    ReviewBlock,
)
from .utils import safe_join, safe_text


FALLBACK_REENTRY_TRIGGERS = [
    "Critical assumption is contradicted.",
    "KPI remains red for two review cycles.",
    "New evidence materially changes the recommendation.",
]


def build_delivery_package(state: Any) -> DeliveryPackage:
    warnings: list[str] = []
    project_id = safe_text(_get(state, "project_id"), default="").strip() or "unknown-project"
    report = safe_text(_get(state, "report"), default="")

    decision_statement = _first_text(
        _get(state, "decision_statement"),
        _get(state, "decision", "statement"),
        _get(state, "decision_objects", "primary_decision", "summary"),
        _get(state, "decision_objects", "primary_decision", "title"),
        _report_section(report, ("The Decision", "Decision")),
        _get(state, "brief"),
    )
    if not decision_statement:
        warnings.append("Decision statement could not be extracted; left blank.")

    recommendation = _extract_recommendation(state, report, warnings)
    execution_plan = _extract_execution_plan(state, warnings)
    critical_assumptions = _extract_critical_assumptions(state, report)
    kpis = _extract_kpis(state, warnings)
    review = _extract_review(state)

    if not review.reentry_triggers:
        review.reentry_triggers = list(FALLBACK_REENTRY_TRIGGERS)

    source_report_excerpt = _first_text(_get(state, "source_report_excerpt"), _excerpt(report, 1800))
    if not source_report_excerpt:
        warnings.append("Source report excerpt could not be extracted; left blank.")

    return DeliveryPackage(
        project_id=project_id,
        decision_statement=decision_statement,
        recommendation=recommendation,
        execution_plan=execution_plan,
        critical_assumptions=critical_assumptions,
        kpis=kpis,
        review=review,
        source_report_excerpt=source_report_excerpt,
        extraction_warnings=_dedupe(warnings),
    )


def normalize_phase_tag(value: Any, warnings: list[str] | None = None) -> str:
    raw = safe_text(value, default="").strip()
    normalized = re.sub(r"\s+", " ", raw.lower())
    compact = re.sub(r"[^a-z0-9]+", "", normalized)

    if re.search(r"(^|[^0-9])30([^0-9]|$)", normalized) or compact in {"30", "30d", "30day", "30days"}:
        return "30d"
    if re.search(r"\b(0|1)\s*[-/]\s*30\b", normalized) or re.search(r"\bday\s*(0|1)\s*[-/]\s*30\b", normalized):
        return "30d"
    if "first 30" in normalized:
        return "30d"

    if re.search(r"(^|[^0-9])60([^0-9]|$)", normalized) or compact in {"60", "60d", "60day", "60days"}:
        return "60d"
    if re.search(r"\b31\s*[-/]\s*60\b", normalized):
        return "60d"

    if re.search(r"(^|[^0-9])90([^0-9]|$)", normalized) or compact in {"90", "90d", "90day", "90days"}:
        return "90d"
    if re.search(r"\b61\s*[-/]\s*90\b", normalized):
        return "90d"

    if warnings is not None:
        label = raw or "missing"
        warnings.append(f"Unknown execution phase '{label}'; defaulted to 90d.")
    return "90d"


def _extract_recommendation(state: Any, report: str, warnings: list[str]) -> Recommendation:
    direct = _get(state, "recommendation")
    selected = _first_text(
        _get(direct, "selected_option"),
        _get(direct, "selected"),
        _get(direct, "option"),
        _get(state, "recommendation", "selected_option"),
        _report_section(report, ("Recommended Path", "Recommendation")),
        _get(state, "strategy", "executive_strategy"),
    )
    if not selected:
        warnings.append("Recommendation selected_option could not be extracted; left blank.")

    confidence = _first_text(
        _get(direct, "confidence"),
        _get(state, "strategy", "confidence"),
        _get(state, "phase_confidence", "strategy"),
        "unknown",
    ) or "unknown"
    evidence_strength = _first_text(
        _get(direct, "evidence_strength"),
        _get(state, "evidence_strength"),
        "unknown",
    ) or "unknown"
    evidence = _as_list(_get(direct, "evidence"))

    return Recommendation(
        selected_option=selected,
        rationale=_first_text(
            _get(direct, "rationale"),
            _report_section(report, ("Why This Is Recommended", "Executive Summary")),
            _get(state, "strategy", "implementation_sequence"),
        ),
        confidence=confidence,
        evidence_strength=evidence_strength,
        evidence=evidence,
    )


def _extract_execution_plan(state: Any, warnings: list[str]) -> list[ExecutionAction]:
    direct_items = _as_list(_get(state, "execution_plan"))
    if direct_items:
        return [_action_from_item(item, warnings) for item in direct_items]

    strategy_items = _as_list(_get(state, "strategy", "strategies"))
    actions = [_action_from_item(item, warnings) for item in strategy_items]
    if actions:
        return actions

    object_actions = _as_list(_get(state, "decision_objects", "actions"))
    return [_action_from_decision_object(item, warnings) for item in object_actions]


def _action_from_item(item: Any, warnings: list[str]) -> ExecutionAction:
    return ExecutionAction(
        phase=normalize_phase_tag(_first_raw(_get(item, "phase"), _get(item, "timeline")), warnings),
        action=_first_text(_get(item, "action"), _get(item, "title"), _get(item, "summary"), item if _is_scalar(item) else ""),
        owner=_extract_owner(item),
        dependencies=_as_list(_first_raw(_get(item, "dependencies"), _get(item, "dependency"))),
        evidence=_as_list(_first_raw(_get(item, "evidence"), _get(item, "evidence_chain"), _get(item, "evidence_ids"))),
        notes=_first_text(_get(item, "notes"), _get(item, "justification"), _get(item, "risk_if_ignored")),
        success_criteria=_first_text(_get(item, "success_criteria"), _get(item, "expected_impact")),
        status=_first_text(_get(item, "status"), "proposed"),
    )


def _action_from_decision_object(item: Any, warnings: list[str]) -> ExecutionAction:
    return ExecutionAction(
        phase=normalize_phase_tag(_get(item, "phase"), warnings),
        action=_first_text(_get(item, "title"), _get(item, "summary")),
        owner=_extract_owner(item),
        evidence=_as_list(_get(item, "evidence_ids")),
        notes=_first_text(_get(item, "source_phase")),
        status=_first_text(_get(item, "status"), "proposed"),
    )


def _extract_owner(item: Any) -> str:
    return _first_text(
        _get(item, "owner"),
        _get(item, "owner_role"),
        _get(item, "responsible"),
        _get(item, "responsible_role"),
        _get(item, "assignee"),
        _get(item, "accountable"),
    )


def _extract_critical_assumptions(state: Any, report: str) -> list[CriticalAssumption]:
    direct_items = _as_list(_get(state, "critical_assumptions"))
    if not direct_items:
        direct_items = _as_list(_get(state, "assumptions"))
    if direct_items:
        return [_assumption_from_item(item) for item in direct_items]

    section = _report_section(report, ("Assumptions and Open Questions", "Open assumptions / questions"))
    lines = _bulletish_lines(section)
    return [
        CriticalAssumption(assumption=line, confidence="unknown")
        for line in lines
        if line
    ]


def _assumption_from_item(item: Any) -> CriticalAssumption:
    return CriticalAssumption(
        assumption=_first_text(_get(item, "assumption"), _get(item, "title"), _get(item, "summary"), item if _is_scalar(item) else ""),
        falsification_trigger=_first_text(_get(item, "falsification_trigger"), _get(item, "trigger"), _get(item, "reject")),
        owner=_first_text(_get(item, "owner"), _get(item, "responsible")),
        confidence=_first_text(_get(item, "confidence"), "unknown") or "unknown",
        evidence=_as_list(_first_raw(_get(item, "evidence"), _get(item, "evidence_ids"))),
        notes=_first_text(_get(item, "notes")),
    )


def _extract_kpis(state: Any, warnings: list[str]) -> list[KPI]:
    direct_items = _as_list(_get(state, "kpis"))
    if direct_items:
        return [_kpi_from_item(item, warnings) for item in direct_items]

    metrics = _as_list(_get(state, "strategy", "success_metrics"))
    kpis = [_kpi_from_metric(metric, warnings) for metric in metrics if safe_text(metric).strip()]

    for cadence in ("daily", "weekly", "monthly"):
        for item in _as_list(_get(state, "monitor", "ooda_schedule", cadence)):
            name = _first_text(_get(item, "metric"), _get(item, "name"))
            if not name:
                continue
            if any(existing.name == name for existing in kpis):
                continue
            warnings.append(f"KPI indicator_type is unknown for '{name}'; set to unknown.")
            kpis.append(
                KPI(
                    name=name,
                    indicator_type="unknown",
                    owner=_first_text(_get(item, "owner")),
                    cadence=cadence,
                    notes=_first_text(_get(item, "source")),
                )
            )
    return kpis


def _kpi_from_metric(metric: Any, warnings: list[str]) -> KPI:
    name = safe_text(metric).strip()
    warnings.append(f"KPI indicator_type is unknown for '{name}'; set to unknown.")
    return KPI(name=name, indicator_type="unknown")


def _kpi_from_item(item: Any, warnings: list[str]) -> KPI:
    name = _first_text(_get(item, "name"), _get(item, "metric"), item if _is_scalar(item) else "")
    indicator_type = _normalize_indicator_type(_get(item, "indicator_type"), name, warnings)
    return KPI(
        name=name,
        indicator_type=indicator_type,
        threshold_red=_first_raw(_get(item, "threshold_red"), _get(item, "red_threshold")),
        threshold_amber=_first_raw(_get(item, "threshold_amber"), _get(item, "amber_threshold")),
        actual_value=_first_raw(_get(item, "actual_value"), _get(item, "actual")),
        status=_first_text(_get(item, "status")),
        owner=_first_text(_get(item, "owner")),
        cadence=_first_text(_get(item, "cadence"), _get(item, "review_cadence")),
        notes=_first_text(_get(item, "notes"), _get(item, "source")),
    )


def _normalize_indicator_type(value: Any, name: str, warnings: list[str]) -> str:
    raw = safe_text(value, default="").strip().lower()
    if raw in {"leading", "lead"}:
        return "leading"
    if raw in {"lagging", "lag"}:
        return "lagging"
    label = name or "unnamed KPI"
    warnings.append(f"KPI indicator_type is unknown for '{label}'; set to unknown.")
    return "unknown"


def _extract_review(state: Any) -> ReviewBlock:
    direct = _get(state, "review")
    triggers = _as_list(_get(direct, "reentry_triggers"))
    if not triggers:
        triggers = _as_list(_get(state, "monitor", "reentry_watch"))
    if not triggers:
        triggers = _reentry_trigger_items(_get(state, "reentry_triggers_fired"))
    if not triggers:
        triggers = _as_list(_get(state, "strategy", "reentry_check"))
    if not triggers:
        triggers = [
            _first_text(_get(item, "trip"), _get(item, "strategy_ref"))
            for item in _as_list(_get(state, "monitor", "circuit_breakers"))
            if _first_text(_get(item, "trip"), _get(item, "strategy_ref"))
        ]

    return ReviewBlock(
        cadence=_first_text(_get(direct, "cadence"), _get(state, "strategy", "review_date"), "operator-defined"),
        owner=_first_text(_get(direct, "owner"), "operator"),
        reentry_triggers=triggers,
        notes=_first_text(_get(direct, "notes"), _get(state, "monitor", "commitment_rationale")),
    )


def _reentry_trigger_items(value: Any) -> list[Any]:
    items = []
    for item in _as_list(value):
        if isinstance(item, dict):
            items.append(_first_text(item.get("trigger"), item.get("reason"), item.get("phase"), item))
        else:
            items.append(item)
    return [item for item in items if safe_text(item).strip()]


def _get(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for key in path:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key, default)
            continue
        try:
            current = getattr(current, key)
        except Exception:
            return default
    return current


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if safe_text(value).strip() == "":
        return []
    return [value]


def _first_text(*values: Any) -> str:
    for value in values:
        text = safe_text(value, default="").strip()
        if text:
            return text
    return ""


def _first_raw(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if safe_text(value, default="").strip():
            return value
    return None


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _excerpt(text: str, limit: int) -> str:
    value = safe_text(text, default="").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _report_section(report: str, headings: tuple[str, ...]) -> str:
    if not report:
        return ""
    wanted = {_normalize_heading(heading) for heading in headings}
    current = ""
    sections: dict[str, list[str]] = {}
    for line in report.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            current = _normalize_heading(match.group(1))
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    for heading in wanted:
        body = "\n".join(sections.get(heading, [])).strip()
        if body:
            return body
    return ""


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", safe_text(value).lower()).strip()


def _bulletish_lines(text: str) -> list[str]:
    lines = []
    for line in safe_text(text).splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = safe_text(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
