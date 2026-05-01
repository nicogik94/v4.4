"""Deterministic citation resolvability pass for raw report text.

This module is intentionally not wired into ProjectState, the workflow graph,
API, renderer, exporter, database, or persistence. It derives an in-memory
review-only result from the current ProjectState snapshot.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cdp.citation_format import (
    EVIDENCE_CITATION_MARKER_LOCATOR_UNAVAILABLE,
    EVIDENCE_CITATION_MARKER_REGEX,
)

CANONICAL_MARKER_PATTERN = r"\[Evidence:\s+[^\s|]+\s+\|\s+[^\]]+\]"
CANONICAL_MARKER_RE = re.compile(CANONICAL_MARKER_PATTERN)
MARKER_DETAIL_RE = re.compile(EVIDENCE_CITATION_MARKER_REGEX)
EVIDENCE_CANDIDATE_RE = re.compile(r"\[Evidence:[^\]]*\]")
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")

ResolverStatus = Literal[
    "resolved_exact",
    "resolved_id_only",
    "unknown_evidence_id",
    "locator_mismatch",
    "malformed",
]

LOAD_BEARING_SECTIONS = {
    "executive summary",
    "decision logic",
    "evidence strength",
    "final verdicts",
    "strategy results",
    "monitoring and kill criteria",
}
SUPPORT_LABELS = ("[Inference]", "[Hypothesis]", "[Unknown]", "citation unavailable")
GENERIC_EVIDENCE_IDS = {"evidence_id"}
GENERIC_LOCATORS = {"locator"}
NON_CONCRETE_LOCATORS = {"", EVIDENCE_CITATION_MARKER_LOCATOR_UNAVAILABLE}
EMPIRICAL_CUE_RE = re.compile(
    r"(%|\$|\b\d+(?:\.\d+)?\b|\b("
    r"increased|decreased|grew|declined|reduced|improved|worsened|"
    r"observed|measured|reported|validated|confirmed|indicates|shows|supports|"
    r"market|customer|revenue|cost|margin|capacity|demand|supply|risk|latency|"
    r"conversion|churn|failure|defect|delay|throughput"
    r")\b)",
    re.I,
)


class EvidenceLocatorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    locators: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    external_uris: list[str] = Field(default_factory=list)
    has_concrete_locator: bool = False


class CitationMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: str
    evidence_id: str
    locator: str
    start: int
    end: int
    line_number: int
    section: str = ""


class CitationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: str
    status: ResolverStatus
    evidence_id: str = ""
    locator: str = ""
    registered_locators: list[str] = Field(default_factory=list)
    line_number: int = 0
    section: str = ""
    review_eligible: bool = False
    review_reason: str = ""


class LoadBearingReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str
    line_number: int
    text_excerpt: str
    reason: str
    marker_count: int = 0
    support_label_present: bool = False
    review_only: bool = True


class DefensePassResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "cdp.v0.1"
    source: str = "ProjectState.report"
    extraction_method: str = "deterministic_canonical_marker_post_pass"
    canonical_marker_pattern: str = CANONICAL_MARKER_PATTERN
    report_text_preserved: bool = True
    source_report_sha256: str = ""
    source_report_length: int = 0
    registry_entries: list[EvidenceLocatorEntry] = Field(default_factory=list)
    markers: list[CitationMarker] = Field(default_factory=list)
    resolutions: list[CitationResolution] = Field(default_factory=list)
    malformed_candidates: list[str] = Field(default_factory=list)
    load_bearing_reviews: list[LoadBearingReviewFinding] = Field(default_factory=list)
    claims_requiring_review: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    extraction_limitations: list[str] = Field(default_factory=list)
    summary_counts: dict[str, int] = Field(default_factory=dict)


def build_defense_pass_result(state: Any) -> DefensePassResult:
    """Return an in-memory citation resolvability result without mutating state."""
    report_text = str(getattr(state, "report", "") or "")
    registry_entries = build_evidence_locator_registry(state)
    registry = {entry.evidence_id: entry for entry in registry_entries}
    markers = parse_citation_markers(report_text)
    malformed_resolutions = _malformed_resolutions(report_text)
    resolutions = [_resolve_marker(marker, registry) for marker in markers] + malformed_resolutions
    malformed_candidates = [resolution.marker for resolution in malformed_resolutions]
    load_bearing_reviews = detect_load_bearing_review_findings(report_text)
    missing_inputs = _missing_inputs(report_text, registry_entries, markers)
    claims_requiring_review = _claims_requiring_review(resolutions, load_bearing_reviews)

    return DefensePassResult(
        source_report_sha256=hashlib.sha256(report_text.encode("utf-8")).hexdigest(),
        source_report_length=len(report_text),
        registry_entries=registry_entries,
        markers=markers,
        resolutions=resolutions,
        malformed_candidates=malformed_candidates,
        load_bearing_reviews=load_bearing_reviews,
        claims_requiring_review=claims_requiring_review,
        missing_inputs=missing_inputs,
        extraction_limitations=[
            "Raw ProjectState.report markdown only; no structured claim extraction.",
            "Line-level load-bearing review triage only; not proof of claim defensibility.",
            "No renderer, export, API, graph, database, or persistence integration.",
        ],
        summary_counts=_summary_counts(markers, malformed_resolutions, resolutions, load_bearing_reviews),
    )


def run_citation_resolvability_pass(state: Any) -> DefensePassResult:
    """Alias for callers that prefer pass-oriented naming."""
    return build_defense_pass_result(state)


def build_evidence_locator_registry(state: Any) -> list[EvidenceLocatorEntry]:
    """Build a deterministic evidence locator registry from existing state metadata."""
    entries: dict[str, dict[str, set[str]]] = {}

    for item in list(getattr(getattr(state, "knowledge_layer", None), "items", []) or []):
        provenance = getattr(item, "provenance", None)
        _add_registry_entry(
            entries,
            evidence_id=getattr(item, "evidence_id", "") or getattr(item, "item_id", ""),
            locator=getattr(item, "locator", ""),
            source_ref=getattr(item, "source_ref", "") or getattr(provenance, "source_ref", ""),
            source_id=getattr(item, "source_id", ""),
            title=getattr(item, "title", ""),
            external_uri=getattr(provenance, "external_uri", ""),
        )

    for evidence in list(getattr(state, "imported_evidence", []) or []):
        provenance = getattr(evidence, "provenance", None)
        _add_registry_entry(
            entries,
            evidence_id=getattr(evidence, "evidence_id", ""),
            source_ref=getattr(provenance, "source_ref", ""),
            title=getattr(evidence, "title", ""),
            external_uri=getattr(provenance, "external_uri", ""),
        )

    decision_objects = getattr(state, "decision_objects", None)
    for evidence in list(getattr(decision_objects, "evidences", []) or []):
        provenance = getattr(evidence, "provenance", None)
        _add_registry_entry(
            entries,
            evidence_id=getattr(evidence, "evidence_id", ""),
            source_ref=getattr(provenance, "source_ref", ""),
            title=getattr(evidence, "title", ""),
            external_uri=getattr(provenance, "external_uri", ""),
        )

    for hypothesis in list(getattr(state, "hypotheses", []) or []):
        for evidence_id in list(getattr(hypothesis, "evidence_ids", []) or []):
            _add_registry_entry(entries, evidence_id=evidence_id)

    registry = []
    for evidence_id in sorted(entries):
        entry = entries[evidence_id]
        locators = sorted(entry["locators"])
        registry.append(
            EvidenceLocatorEntry(
                evidence_id=evidence_id,
                locators=locators,
                source_refs=sorted(entry["source_refs"]),
                source_ids=sorted(entry["source_ids"]),
                titles=sorted(entry["titles"]),
                external_uris=sorted(entry["external_uris"]),
                has_concrete_locator=bool(locators),
            )
        )
    return registry


def parse_citation_markers(report_text: str) -> list[CitationMarker]:
    """Parse strict, non-placeholder canonical markers from raw report text."""
    markers: list[CitationMarker] = []
    for line_number, line, section, offset in _line_contexts(report_text):
        for match in CANONICAL_MARKER_RE.finditer(line):
            marker = match.group(0)
            if _is_placeholder_candidate(marker):
                continue
            detail = MARKER_DETAIL_RE.fullmatch(marker)
            if not detail:
                continue
            markers.append(
                CitationMarker(
                    marker=marker,
                    evidence_id=detail.group("evidence_id").strip(),
                    locator=detail.group("locator").strip(),
                    start=offset + match.start(),
                    end=offset + match.end(),
                    line_number=line_number,
                    section=section,
                )
            )
    return markers


def detect_load_bearing_review_findings(report_text: str) -> list[LoadBearingReviewFinding]:
    """Return conservative line-level review findings for load-bearing sections."""
    findings: list[LoadBearingReviewFinding] = []
    for line_number, line, section, _offset in _line_contexts(report_text):
        if section not in LOAD_BEARING_SECTIONS:
            continue
        stripped = line.strip()
        if not stripped or MARKDOWN_HEADING_RE.match(stripped):
            continue
        marker_count = len([match for match in CANONICAL_MARKER_RE.findall(line) if not _is_placeholder_candidate(match)])
        support_label_present = _has_support_label(line)
        if marker_count or support_label_present or not _has_empirical_cue(line):
            continue
        findings.append(
            LoadBearingReviewFinding(
                section=section,
                line_number=line_number,
                text_excerpt=_clip(stripped),
                reason=(
                    "review_only: load-bearing line has empirical cues but no canonical "
                    "evidence marker or support label"
                ),
                marker_count=marker_count,
                support_label_present=support_label_present,
            )
        )
    return findings


def _add_registry_entry(
    entries: dict[str, dict[str, set[str]]],
    *,
    evidence_id: Any,
    locator: Any = "",
    source_ref: Any = "",
    source_id: Any = "",
    title: Any = "",
    external_uri: Any = "",
) -> None:
    clean_id = _clean(evidence_id)
    if not clean_id:
        return
    entry = entries.setdefault(
        clean_id,
        {
            "locators": set(),
            "source_refs": set(),
            "source_ids": set(),
            "titles": set(),
            "external_uris": set(),
        },
    )
    clean_locator = _clean(locator)
    if _is_concrete_locator(clean_locator):
        entry["locators"].add(clean_locator)
    for key, value in (
        ("source_refs", source_ref),
        ("source_ids", source_id),
        ("titles", title),
        ("external_uris", external_uri),
    ):
        clean_value = _clean(value)
        if clean_value:
            entry[key].add(clean_value)


def _resolve_marker(marker: CitationMarker, registry: dict[str, EvidenceLocatorEntry]) -> CitationResolution:
    entry = registry.get(marker.evidence_id)
    if entry is None:
        return CitationResolution(
            marker=marker.marker,
            status="unknown_evidence_id",
            evidence_id=marker.evidence_id,
            locator=marker.locator,
            line_number=marker.line_number,
            section=marker.section,
            review_eligible=True,
            review_reason="Evidence ID is absent from the evidence locator registry.",
        )
    if _is_locator_unavailable(marker.locator) or not entry.has_concrete_locator:
        reason = (
            "Marker uses locator unavailable and is resolved by ID only."
            if _is_locator_unavailable(marker.locator)
            else "Evidence ID is known but no concrete locator is registered."
        )
        return CitationResolution(
            marker=marker.marker,
            status="resolved_id_only",
            evidence_id=marker.evidence_id,
            locator=marker.locator,
            registered_locators=entry.locators,
            line_number=marker.line_number,
            section=marker.section,
            review_eligible=True,
            review_reason=reason,
        )
    if marker.locator in entry.locators:
        return CitationResolution(
            marker=marker.marker,
            status="resolved_exact",
            evidence_id=marker.evidence_id,
            locator=marker.locator,
            registered_locators=entry.locators,
            line_number=marker.line_number,
            section=marker.section,
            review_eligible=False,
            review_reason="Evidence ID and locator exactly match the registry.",
        )
    return CitationResolution(
        marker=marker.marker,
        status="locator_mismatch",
        evidence_id=marker.evidence_id,
        locator=marker.locator,
        registered_locators=entry.locators,
        line_number=marker.line_number,
        section=marker.section,
        review_eligible=True,
        review_reason="Evidence ID is known, but locator does not match registered concrete locators.",
    )


def _malformed_resolutions(report_text: str) -> list[CitationResolution]:
    malformed: list[CitationResolution] = []
    for line_number, line, section, _offset in _line_contexts(report_text):
        for match in EVIDENCE_CANDIDATE_RE.finditer(line):
            candidate = match.group(0)
            if CANONICAL_MARKER_RE.fullmatch(candidate) and not _is_placeholder_candidate(candidate):
                continue
            detail = MARKER_DETAIL_RE.fullmatch(candidate)
            malformed.append(
                CitationResolution(
                    marker=candidate,
                    status="malformed",
                    evidence_id=detail.group("evidence_id").strip() if detail else "",
                    locator=detail.group("locator").strip() if detail else "",
                    line_number=line_number,
                    section=section,
                    review_eligible=True,
                    review_reason=(
                        "Evidence marker candidate is non-canonical, escaped, placeholder-shaped, "
                        "or otherwise unsupported."
                    ),
                )
            )
    return malformed


def _is_placeholder_candidate(candidate: str) -> bool:
    detail = MARKER_DETAIL_RE.fullmatch(candidate)
    content = candidate[len("[Evidence:"):-1].strip() if candidate.startswith("[Evidence:") else ""
    if content == "..." or "\\|" in candidate or "<" in candidate or ">" in candidate:
        return True
    if not detail:
        return False
    evidence_id = detail.group("evidence_id").strip().lower()
    locator = detail.group("locator").strip().lower()
    return (
        not evidence_id
        or not locator
        or evidence_id in GENERIC_EVIDENCE_IDS
        or locator in GENERIC_LOCATORS
        or evidence_id == "..."
        or locator == "..."
    )


def _line_contexts(report_text: str):
    current_section = ""
    offset = 0
    for line_number, raw_line in enumerate((report_text or "").splitlines(keepends=True), start=1):
        line = raw_line.rstrip("\r\n")
        heading = _normalize_heading(line)
        if heading:
            current_section = heading
        yield line_number, line, current_section, offset
        offset += len(raw_line)


def _normalize_heading(line: str) -> str:
    heading = MARKDOWN_HEADING_RE.match(line.strip())
    if not heading:
        return ""
    normalized = heading.group(1).strip().lower()
    normalized = re.sub(r"\s+\[#\d+\]\s*$", "", normalized).strip()
    normalized = re.sub(r"\s*\([^)]*\)\s*$", "", normalized).strip()
    return normalized


def _has_support_label(line: str) -> bool:
    lowered = line.lower()
    return any(label.lower() in lowered for label in SUPPORT_LABELS)


def _has_empirical_cue(line: str) -> bool:
    return bool(EMPIRICAL_CUE_RE.search(line or ""))


def _is_concrete_locator(locator: str) -> bool:
    return _clean(locator).lower() not in NON_CONCRETE_LOCATORS


def _is_locator_unavailable(locator: str) -> bool:
    return _clean(locator).lower() == EVIDENCE_CITATION_MARKER_LOCATOR_UNAVAILABLE


def _missing_inputs(
    report_text: str,
    registry_entries: list[EvidenceLocatorEntry],
    markers: list[CitationMarker],
) -> list[str]:
    missing = []
    if not report_text:
        missing.append("raw_report_missing")
    if not registry_entries:
        missing.append("evidence_locator_registry_empty")
    if report_text and not markers:
        missing.append("canonical_evidence_markers_missing")
    return missing


def _claims_requiring_review(
    resolutions: list[CitationResolution],
    findings: list[LoadBearingReviewFinding],
) -> list[str]:
    review_items = [
        f"{resolution.status}: line {resolution.line_number}: {resolution.marker}"
        for resolution in resolutions
        if resolution.review_eligible
    ]
    review_items.extend(
        f"load_bearing_review: line {finding.line_number}: {finding.text_excerpt}"
        for finding in findings
    )
    return review_items


def _summary_counts(
    markers: list[CitationMarker],
    malformed_resolutions: list[CitationResolution],
    resolutions: list[CitationResolution],
    load_bearing_reviews: list[LoadBearingReviewFinding],
) -> dict[str, int]:
    counts = {
        "canonical_marker_count": len(markers),
        "malformed_marker_count": len(malformed_resolutions),
        "load_bearing_review_count": len(load_bearing_reviews),
        "review_eligible_resolution_count": sum(1 for item in resolutions if item.review_eligible),
        "resolved_exact": 0,
        "resolved_id_only": 0,
        "unknown_evidence_id": 0,
        "locator_mismatch": 0,
        "malformed": 0,
    }
    for resolution in resolutions:
        counts[resolution.status] += 1
    return counts


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clip(value: str, limit: int = 240) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "..."
