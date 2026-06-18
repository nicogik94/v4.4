"""Shared CDP review caveats for read-only operator surfaces."""
from __future__ import annotations


RESOLVER_STATUSES = (
    "resolved_exact",
    "resolved_id_only",
    "unknown_evidence_id",
    "locator_mismatch",
    "malformed",
)

RESOLVER_STATUS_DESCRIPTIONS = {
    "resolved_exact": "Marker-to-registered-locator metadata matched.",
    "resolved_id_only": "Evidence-ID traceability only; weaker than resolved_exact.",
    "unknown_evidence_id": "Marker evidence ID is absent from the registry and requires operator review.",
    "locator_mismatch": "Marker evidence ID is known, but locator metadata conflicts and requires operator review.",
    "malformed": "Evidence marker text is non-canonical or unsupported and requires operator review.",
}

ANTI_OVERCLAIMING_LABELS = [
    "CDP v0.1 is review-only citation resolvability.",
    "resolved_id_only means evidence-ID traceability only.",
    "resolved_id_only is weaker than resolved_exact.",
    "This does not verify semantic support.",
    "This does not prove full claim defensibility.",
    "This does not rewrite, strip, or correct report text.",
    "Load-bearing findings are line-level review prompts, not claim cards.",
]

CDP_REVIEW_CAVEATS = [
    "CDP v0.1 is review-only citation resolvability.",
    "Resolved markers are traceability aids only.",
    "resolved_exact means marker-to-registered-locator metadata matched.",
    "resolved_id_only means evidence-ID traceability only and is weaker than resolved_exact.",
    "Unresolved or malformed markers require operator review.",
    "CDP does not verify semantic support.",
    "CDP does not prove full claim defensibility.",
    "CDP does not approve delivery.",
    "CDP does not rewrite, strip, or correct report text.",
]

LOCATOR_PRECISION_CAVEAT = (
    "Locator precision caveat: this project traces primarily to evidence IDs without specific "
    "locator anchors. resolved_id_only is weaker than resolved_exact and does not prove "
    "page/chunk/row-level support."
)
ALL_ID_ONLY_CAVEAT = (
    "All resolved markers are ID-only. This shows evidence-ID traceability, not locator-level "
    "precision or semantic support."
)
