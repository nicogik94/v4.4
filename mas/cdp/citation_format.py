"""Shared evidence citation marker format."""
from __future__ import annotations

from typing import Any

EVIDENCE_CITATION_MARKER_FORMAT = "[Evidence: {evidence_id} | {locator}]"
EVIDENCE_CITATION_MARKER_LOCATOR_UNAVAILABLE = "locator unavailable"
EVIDENCE_CITATION_MARKER_REGEX = r"\[Evidence:\s+(?P<evidence_id>[^\s|]+)\s+\|\s+(?P<locator>[^\]]+)\]"

_LOCATOR_FRAGMENT_PREFIXES = ("chunk=", "rows=", "row=", "page=", "sheet=")


def derive_knowledge_item_locator(item: Any) -> str:
    """Return a concrete locator string for a KnowledgeItem, or "" if none derivable.

    Probes, in order:
      1. explicit `.locator` attribute (legacy / test fixtures)
      2. structured_payload["locator"]
      3. structured_payload anchor fields:
           - chunk_index               -> "chunk=N"
           - row_start + row_end       -> "row=N-M" (or "sheet=S;row=N-M" with sheet_name)
           - page                      -> "page=P"
      4. source_ref `#fragment` if it starts with chunk=, rows=, row=, page=, sheet=
      5. "" (caller renders as "locator unavailable")

    Both the orchestrator's report evidence locator register and the CDP T1b
    resolver registry call this helper so register-emitted markers and resolver
    expectations stay byte-identical.
    """
    explicit = getattr(item, "locator", "")
    if explicit:
        text = str(explicit).strip()
        if text:
            return text

    payload = getattr(item, "structured_payload", {}) or {}
    if isinstance(payload, dict):
        explicit_payload = payload.get("locator")
        if explicit_payload:
            text = str(explicit_payload).strip()
            if text:
                return text

        chunk_index = payload.get("chunk_index")
        if chunk_index not in (None, ""):
            return f"chunk={chunk_index}"

        row_start = payload.get("row_start")
        row_end = payload.get("row_end")
        if row_start not in (None, "") and row_end not in (None, ""):
            sheet = payload.get("sheet_name")
            sheet_text = str(sheet).strip() if sheet not in (None, "") else ""
            row_range = f"{row_start}-{row_end}"
            return f"sheet={sheet_text};row={row_range}" if sheet_text else f"row={row_range}"

        page = payload.get("page")
        if page not in (None, ""):
            return f"page={page}"

    source_ref = str(getattr(item, "source_ref", "") or "")
    if "#" in source_ref:
        fragment = source_ref.rsplit("#", 1)[1].strip()
        if fragment and any(fragment.startswith(prefix) for prefix in _LOCATOR_FRAGMENT_PREFIXES):
            return fragment

    return ""
