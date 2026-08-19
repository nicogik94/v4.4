"""The one authoritative Decision Quality value for a run (V4.4 P0-4).

Why this exists
───────────────
A real pre-pilot run exported ``DQ=40`` in classify/report and ``DQ=0`` in
``project_state`` and ``calibration_predictions``. Two disjoint representations
existed: ``state.classify.dq`` (four numbers the classify phase emits, read by
the gate, the classify phase summary and the operator exports) and
``state.dq`` — a six-link ``DQScores`` container **never assigned anywhere in
the codebase**, whose ``sum(...)`` was published as ``dq_total`` by the
workspace summary, the calibration snapshot and the machine archive. One
surface published a measurement, the other a structural zero.

Contract
────────
``classify.dq`` is the single declared source. Every surface derives from
:func:`authoritative_dq`, which is **pure**: it never writes to ``ProjectState``,
so exporters stay observational and a state persisted before this fix still
exports the right number. Absent DQ is ``available=False`` — a different fact
from zero, and never rendered as one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "decision_quality.v1"

# The single declared source of DQ for a strategic-audit run.
AUTHORITATIVE_DQ_SOURCE = "classify.dq"


@dataclass(frozen=True)
class DQAssessment:
    available: bool
    total: float | None = None
    components: tuple[float, ...] = ()
    source: str = AUTHORITATIVE_DQ_SOURCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "available": self.available,
            "total": self.total,
            "components": list(self.components),
            "source": self.source if self.available else "",
        }


def authoritative_dq(state: Any) -> DQAssessment:
    """Derive the one DQ value for this run. Pure — never mutates ``state``."""
    classify = getattr(state, "classify", None)
    components = _components(getattr(classify, "dq", None)) if classify is not None else None
    if components is None:
        return DQAssessment(available=False)
    return DQAssessment(
        available=True,
        total=float(sum(components)),
        components=tuple(components),
    )


def authoritative_dq_total(state: Any) -> float | None:
    """The DQ total, or ``None`` when the run has not measured one."""
    return authoritative_dq(state).total


def dq_export_projection(state: Any) -> dict[str, Any]:
    """Authoritative DQ record for exported state, computed without mutation."""
    return authoritative_dq(state).as_dict()


def dq_display_text(state: Any) -> str:
    """One rendering of the value, used by every human-facing export."""
    assessment = authoritative_dq(state)
    if not assessment.available:
        return "Not measured"
    components = ", ".join(f"{value:g}" for value in assessment.components)
    total = f"{assessment.total:g}"
    return f"{total} total ({components}); source: {assessment.source}" if components else f"{total} total"


def _components(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    components: list[float] = []
    for item in value:
        try:
            components.append(float(item))
        except (TypeError, ValueError):
            return None
    return components
