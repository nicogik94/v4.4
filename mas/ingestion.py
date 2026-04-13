"""Helpers for merging connector imports into canonical ProjectState fields."""
from __future__ import annotations

from state import Evidence, ProjectState, Signal


def merge_imported_records(
    state: ProjectState,
    *,
    evidence: list[Evidence],
    signals: list[Signal],
) -> dict[str, int]:
    existing_evidence = {item.evidence_id: item for item in (state.imported_evidence or [])}
    existing_signals = {item.signal_id: item for item in (state.imported_signals or [])}

    for item in evidence:
        existing_evidence[item.evidence_id] = item
    for item in signals:
        existing_signals[item.signal_id] = item

    state.imported_evidence = sorted(
        existing_evidence.values(),
        key=lambda item: (item.provenance.captured_at, item.evidence_id),
    )
    state.imported_signals = sorted(
        existing_signals.values(),
        key=lambda item: (item.provenance.captured_at, item.signal_id),
    )

    return {
        "evidence_total": len(state.imported_evidence),
        "signal_total": len(state.imported_signals),
    }
