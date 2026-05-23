"""Quality warnings for generated client delivery packages."""

from __future__ import annotations

import re
from typing import Any

from .models import DeliveryPackage
from .utils import safe_text


NON_NUMERIC_THRESHOLD_WARNING = (
    "KPI thresholds are non-numeric; conditional formatting could not be applied deterministically."
)
MISSING_ACTION_OWNER_WARNING = "One or more execution actions are missing owners."


def delivery_quality_warnings(package: DeliveryPackage) -> list[str]:
    warnings: list[str] = []
    if not safe_text(package.decision_statement).strip():
        warnings.append("Delivery package has missing or empty decision statement.")
    if not safe_text(package.recommendation.selected_option).strip():
        warnings.append("Delivery package has missing or empty recommendation.selected_option.")
    if len(package.execution_plan) < 3:
        warnings.append("Delivery package has fewer than 3 execution actions.")
    if package.execution_plan and any(not safe_text(action.owner).strip() for action in package.execution_plan):
        warnings.append(MISSING_ACTION_OWNER_WARNING)
    if len(package.critical_assumptions) == 0:
        warnings.append("Delivery package has zero critical assumptions.")
    if len(package.kpis) == 0:
        warnings.append("Delivery package has zero KPIs.")
    if len(package.review.reentry_triggers) == 0:
        warnings.append("Delivery package has zero re-entry triggers.")
    if _has_non_numeric_thresholds(package):
        warnings.append(NON_NUMERIC_THRESHOLD_WARNING)
    return _dedupe(warnings)


def _has_non_numeric_thresholds(package: DeliveryPackage) -> bool:
    for kpi in package.kpis:
        for threshold in (kpi.threshold_red, kpi.threshold_amber):
            text = safe_text(threshold).strip()
            if not text:
                continue
            if _parse_number(threshold) is None:
                return True
    return False


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = safe_text(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
