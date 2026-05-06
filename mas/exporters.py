"""Project dossier exporters for DOCX and PDF downloads."""
from __future__ import annotations

import json
import subprocess
import zipfile
from io import BytesIO
from datetime import datetime, timezone
from xml.sax.saxutils import escape
import re
from typing import Any

from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from state import ProjectState


def build_export_filename(state: ProjectState, ext: str) -> str:
    name = (state.project_name or "decision-dossier").strip().lower()
    safe = re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "decision-dossier"
    return f"{safe}-dossier.{ext}"


def export_project_docx_bytes(state: ProjectState) -> bytes:
    document = Document()
    document.core_properties.title = f"{state.project_name or 'Decision Dossier'}"
    document.core_properties.subject = "Decision Engine dossier export"

    normal_style = document.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(10.5)

    for block in _build_dossier_blocks(state):
        if block["type"] == "heading":
            document.add_heading(block["text"], level=min(block["level"], 4))
        elif block["type"] == "paragraph":
            document.add_paragraph(block["text"])
        elif block["type"] == "bullets":
            for item in block["items"]:
                document.add_paragraph(item, style="List Bullet")

    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def export_project_pdf_bytes(state: ProjectState) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=state.project_name or "Decision Dossier",
    )

    styles = getSampleStyleSheet()
    heading1 = ParagraphStyle(
        "DossierHeading1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#0A1628"),
        spaceAfter=8,
    )
    heading2 = ParagraphStyle(
        "DossierHeading2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=8,
        spaceAfter=6,
    )
    heading3 = ParagraphStyle(
        "DossierHeading3",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=6,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "DossierBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#0A1628"),
        spaceAfter=4,
    )

    story = []
    for block in _build_dossier_blocks(state):
        if block["type"] == "heading":
            style = heading1 if block["level"] == 1 else heading2 if block["level"] == 2 else heading3
            story.append(Paragraph(_as_pdf_text(block["text"]), style))
        elif block["type"] == "paragraph":
            story.append(Paragraph(_as_pdf_text(block["text"]), body))
        elif block["type"] == "bullets":
            items = [
                ListItem(Paragraph(_as_pdf_text(item), body), leftIndent=10)
                for item in block["items"]
            ]
            story.append(ListFlowable(items, bulletType="bullet", leftIndent=12))
        story.append(Spacer(1, 2))

    doc.build(story)
    return buf.getvalue()


REPORT_CLARITY_HEADINGS = [
    "Executive Summary",
    "The Decision",
    "Recommended Path",
    "Why This Is Recommended",
    "Options Considered",
    "Evidence Used",
    "Key Risks",
    "Assumptions and Open Questions",
    "Roadmap",
    "Next Steps",
    "Monitoring and Kill Criteria",
    "Appendix: Technical Analysis",
]

EXPORT_PROFILE_FORMATS = {
    "report": {"pdf", "docx"},
    "client_dossier": {"pdf", "docx"},
    "operator_dossier": {"pdf", "docx"},
    "machine_archive": {"zip"},
}

PROFILE_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
}

SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "api_key",
    "apikey",
    "token",
    "password",
    "credential",
    "authorization",
    "cookie",
    "session",
    "provider",
    "raw_payload",
    "payload_raw",
    "raw_response",
    "raw_request",
    "raw_llm",
    "llm_response",
    "chain_of_thought",
    "chain-of-thought",
    "scratchpad",
    "debug",
    "prompt",
    "storage_ref",
    "file_path",
    "local_path",
)

_DROP = object()
REDACTED = "[REDACTED]"


def export_project_profile_bytes(state: ProjectState, profile: str, format: str) -> tuple[bytes, str, str]:
    profile_name = (profile or "report").strip().lower()
    fmt = (format or "").strip().lower()
    if not fmt:
        raise ValueError("format is required")
    if profile_name not in EXPORT_PROFILE_FORMATS:
        raise ValueError(f"profile must be one of {sorted(EXPORT_PROFILE_FORMATS)}")
    if fmt not in PROFILE_MEDIA_TYPES:
        raise ValueError("format must be pdf, docx, or zip")
    if fmt not in EXPORT_PROFILE_FORMATS[profile_name]:
        raise ValueError(f"profile={profile_name} does not support format={fmt}")

    filename = _profile_filename(state, profile_name, fmt)
    if profile_name == "machine_archive":
        archive_payload = build_machine_archive_payload(state)
        return _zip_archive_bytes(archive_payload), PROFILE_MEDIA_TYPES[fmt], filename

    if profile_name == "report":
        markdown = _safe_report_markdown(state)
    elif profile_name == "client_dossier":
        markdown = build_client_dossier_markdown(state)
    else:
        markdown = build_operator_dossier_markdown(state)

    if fmt == "docx":
        return _export_markdown_docx_bytes(markdown, title=filename), PROFILE_MEDIA_TYPES[fmt], filename
    return _export_markdown_pdf_bytes(markdown, title=filename), PROFILE_MEDIA_TYPES[fmt], filename


def build_client_dossier_markdown(state: ProjectState) -> str:
    sections = _extract_report_sections(state.report or "")
    lines = [
        "# Client Dossier",
        _project_metadata_line(state),
        "## Source Report",
        _safe_report_markdown(state),
    ]
    for heading in REPORT_CLARITY_HEADINGS:
        lines.extend([
            f"## {heading}",
            _section_or_fallback(sections, heading),
        ])

    citation_summary = _citation_locator_summary_markdown(state, include_registry=True)
    if citation_summary:
        lines.extend(["## Citation Locator Review Summary", citation_summary])

    clarification_summary = _client_clarifications_markdown(state)
    if clarification_summary:
        lines.extend(["## Clarification Open Questions", clarification_summary])

    return "\n\n".join(part for part in lines if str(part).strip())


def build_operator_dossier_markdown(state: ProjectState) -> str:
    lines = [
        "# Operator Dossier",
        _project_metadata_line(state),
        build_client_dossier_markdown(state),
        "## Project Overview",
        _operator_overview_markdown(state),
        "## Phase Summaries",
        summarize_phase_outputs(state),
        "## Hypotheses",
        summarize_hypotheses(state),
        "## Gauntlet Risks and Cruxes",
        _summarize_gauntlet(state),
        "## Audit Findings and Observation Needs",
        _summarize_audit(state),
        "## Strategy Actions and Success Metrics",
        _summarize_strategy(state),
        "## Monitoring Plan and Circuit Breakers",
        summarize_monitoring(state),
        "## Clarifications",
        _operator_clarifications_markdown(state),
        "## Workspace Summary",
        _workspace_summary_markdown(state),
        "## Decision Trace Summary",
        summarize_trace(state),
        "## Evidence Locator Register",
        _evidence_locator_register_markdown(state),
        "## CDP Citation Locator Review Summary",
        _citation_locator_summary_markdown(state, include_registry=False) or "No citation locator review summary available.",
        "## Risk and Policy Summary",
        summarize_policy(state),
        "## Budget Summary",
        _budget_summary_markdown(state),
        "## Approvals Summary",
        _approvals_summary_markdown(state),
        "## Calibration and Prediction Summary",
        _calibration_prediction_summary_markdown(state),
    ]
    return "\n\n".join(part for part in lines if str(part).strip())


def build_machine_archive_payload(state: ProjectState) -> dict[str, Any]:
    decision_objects = _decision_objects_payload(state)
    files: dict[str, Any] = {
        "project_state.json": sanitize_for_export(state, "machine_archive", mode="redact"),
        "report.md": _safe_report_markdown(state),
        "phase_outputs.json": sanitize_for_export(_phase_outputs_payload(state), "machine_archive", mode="redact"),
        "decision_objects.json": sanitize_for_export(decision_objects, "machine_archive", mode="redact"),
        "clarifications.json": sanitize_for_export(_clarifications_payload(state), "machine_archive", mode="redact"),
        "evidence_locator_register.json": sanitize_for_export(_evidence_locator_payload(state), "machine_archive", mode="redact"),
        "uploaded_file_manifest.json": sanitize_for_export(_uploaded_file_manifest_payload(state), "machine_archive", mode="redact"),
        "policy_summary.json": sanitize_for_export(_policy_summary_payload(state), "machine_archive", mode="redact"),
    }

    calibration = _calibration_predictions_payload(state)
    if calibration:
        files["calibration_predictions.json"] = sanitize_for_export(calibration, "machine_archive", mode="redact")
    approvals = _approvals_payload(state)
    if approvals:
        files["approvals_summary.json"] = sanitize_for_export(approvals, "machine_archive", mode="redact")
    risks = _risk_summary_payload(state)
    if risks:
        files["risk_summary.json"] = sanitize_for_export(risks, "machine_archive", mode="redact")

    included_files = ["export_manifest.json", *sorted(files.keys())]
    files["export_manifest.json"] = build_export_manifest(
        state,
        "machine_archive",
        "zip",
        included_files=included_files,
    )
    return files


def build_export_manifest(
    state: ProjectState,
    profile: str,
    format: str,
    *,
    included_files: list[str] | None = None,
) -> dict[str, Any]:
    return sanitize_for_export(
        {
            "export_schema_version": "1.0",
            "project_id": state.project_id,
            "project_name": state.project_name,
            "export_profile": profile,
            "export_format": format,
            "generated_at": _utc_now(),
            "code_version": _code_version(),
            "redaction_policy": "Recursive sensitive-key, unsafe-path, prompt/debug, provider-payload, and chain-of-thought redaction.",
            "included_files": included_files or [],
        },
        profile,
        mode="redact",
    )


def sanitize_for_export(value, profile, mode: str = "redact"):
    sanitized = _sanitize_for_export(value, profile=profile, mode=mode)
    if sanitized is _DROP:
        return None if mode == "redact" else {}
    return sanitized


def summarize_phase_outputs(state: ProjectState) -> str:
    rows = [["Phase", "Status", "Confidence", "Completed at", "Summary"]]
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        status = state.phase_status.get(phase, "")
        rows.append([
            phase,
            _enum_value(status),
            _fmt_value(state.phase_confidence.get(phase)),
            state.phase_run_completed_at.get(phase, ""),
            state.phase_summaries.get(phase, _short_text(_phase_output_text(state, phase), 240)),
        ])
    return _markdown_table(rows)


def summarize_hypotheses(state: ProjectState) -> str:
    rows = [["ID", "Hypothesis", "Alpha/Beta", "Status", "Confirm", "Reject", "EVOI", "Cluster"]]
    for hypothesis in state.hypotheses or []:
        rows.append([
            hypothesis.id,
            hypothesis.text,
            f"{hypothesis.alpha}/{hypothesis.beta}",
            hypothesis.status,
            hypothesis.confirm,
            hypothesis.reject,
            hypothesis.evoi,
            hypothesis.portfolio_cluster,
        ])
    return _markdown_table(rows) if len(rows) > 1 else "No hypotheses saved."


def summarize_monitoring(state: ProjectState) -> str:
    if not state.monitor:
        return "No monitor output saved."
    lines = [
        f"Commitment score: {_fmt_value(state.monitor.commitment_score)}",
        f"Commitment rationale: {state.monitor.commitment_rationale or 'TBD — requires operator confirmation.'}",
        "### OODA Schedule",
        _markdown_table(
            [["Cadence", "Metric", "Owner", "Source"]]
            + [["Daily", item.metric, item.owner, item.source] for item in state.monitor.ooda_schedule.daily]
            + [["Weekly", item.metric, item.owner, item.source] for item in state.monitor.ooda_schedule.weekly]
            + [["Monthly", item.metric, item.owner, item.source] for item in state.monitor.ooda_schedule.monthly]
        ),
        "### Circuit Breakers",
        _markdown_table(
            [["Strategy", "Trip", "Reset"]]
            + [[item.strategy_ref, item.trip, item.reset] for item in state.monitor.circuit_breakers]
        ),
        "### Canaries",
        _markdown_table(
            [["Signal", "Direction", "Window", "Meaning"]]
            + [[item.signal, item.direction, item.window, item.meaning] for item in state.monitor.canaries]
        ),
    ]
    return "\n\n".join(lines)


def summarize_policy(state: ProjectState) -> str:
    event_counts: dict[str, int] = {}
    for event in state.policy_audit_log or []:
        event_type = str((event or {}).get("event_type", "unknown"))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    rows = [
        ["Field", "Value"],
        ["Risk classification", state.risk_classification],
        ["Risk rationale", state.risk_classification_rationale],
        ["Set by", state.risk_classification_set_by],
        ["Kill switch active", str(bool(state.kill_switch_active))],
        ["Kill switch reason", state.kill_switch_reason],
        ["Policy event count", str(len(state.policy_audit_log or []))],
        ["Policy event types", ", ".join(f"{key}={value}" for key, value in sorted(event_counts.items()))],
    ]
    return _markdown_table(rows)


def summarize_trace(state: ProjectState) -> str:
    try:
        from explainability import build_project_trace

        trace = build_project_trace(state)
        rows = [["Phase", "Status", "Purpose", "Output summary"]]
        for phase in trace.phases:
            rows.append([
                phase.phase,
                phase.status,
                phase.purpose,
                _short_text(phase.output_summary, 260),
            ])
        return _markdown_table(rows)
    except Exception as exc:
        return f"Decision trace summary unavailable: {_short_text(str(exc), 180)}"


def _as_pdf_text(text: str) -> str:
    return escape((text or "").replace("\n", "<br/>"))


def _build_dossier_blocks(state: ProjectState) -> list[dict]:
    blocks: list[dict] = []

    def heading(text: str, level: int = 1):
        blocks.append({"type": "heading", "text": text, "level": level})

    def paragraph(text: str):
        clean = (text or "").strip()
        if clean:
            blocks.append({"type": "paragraph", "text": clean})

    def bullets(items: list[str]):
        clean_items = [str(item).strip() for item in items if str(item).strip()]
        if clean_items:
            blocks.append({"type": "bullets", "items": clean_items})

    heading(state.project_name or "Decision Dossier", 1)
    paragraph(
        " | ".join(filter(None, [
            f"Project ID: {state.project_id}",
            f"Created: {_fmt_datetime(state.created_at)}",
            f"Risk: {state.risk_classification}",
            f"Current phase: {state.current_phase}",
        ]))
    )

    heading("Input", 2)
    paragraph("Brief")
    paragraph(state.brief or "No brief provided.")
    if state.data:
        paragraph("Supporting data")
        paragraph(state.data)
    if state.observations:
        bullets([f"Observation - {k}: {v}" for k, v in state.observations.items()])
    if state.timer_logs:
        bullets([f"Timer - {entry.get('time', '')}: {entry.get('label', '')}" for entry in state.timer_logs])

    heading("Classify", 2)
    if state.classify:
        c = state.classify
        paragraph(f"Domain: {c.domain}")
        paragraph(f"Justification: {c.justification}")
        paragraph(f"Bayes factor: {c.bf}")
        bullets([
            f"Variety environment: {c.variety_env}",
            f"Variety system: {c.variety_sys}",
            f"Variety gaps: {c.variety_gaps}",
            f"Variety decision: {c.variety_decision}",
            f"RPD pattern: {c.rpd_pattern}",
            f"Sensemaking anchors: {c.sensemaking_anchors}",
            f"Expectancy violations: {c.expectancy_violations}",
            f"Reference class: {c.reference_class}",
            f"DQ: {', '.join(str(v) for v in c.dq)}",
            f"Maturity assessment: {c.maturity_assessment}",
            f"Spiral depth: {c.spiral_depth}",
        ])
        heading("OODA", 3)
        bullets([
            f"Observe: {c.ooda.observe}",
            f"Orient: {c.ooda.orient}",
            f"Decide: {c.ooda.decide}",
            f"Act: {c.ooda.act}",
            f"Frequency: {c.ooda.freq}",
        ])
    else:
        paragraph("No classify output saved.")

    heading("Hypotheses", 2)
    if state.hypotheses:
        for hypothesis in state.hypotheses:
            heading(f"{hypothesis.id}: {hypothesis.text}", 3)
            paragraph(f"Justification: {hypothesis.justification}")
            bullets([
                f"Signal: {hypothesis.signal}",
                f"Alpha/Beta: {hypothesis.alpha}/{hypothesis.beta}",
                f"Confirm: {hypothesis.confirm}",
                f"Reject: {hypothesis.reject}",
                f"EVOI: {hypothesis.evoi}",
                f"Portfolio cluster: {hypothesis.portfolio_cluster}",
                f"Status: {hypothesis.status}",
            ])
    else:
        paragraph("No hypotheses saved.")

    heading("Gauntlet", 2)
    if state.gauntlet:
        paragraph(f"Portfolio correlation: {state.gauntlet.portfolio_correlation}")
        paragraph(f"MECE gaps: {state.gauntlet.mece_gaps}")
        paragraph(f"Thompson priority: {state.gauntlet.thompson_priority}")
        paragraph(f"EVOI ranking: {state.gauntlet.evoi_ranking}")
        for result in state.gauntlet.results:
            heading(f"{result.id} - risk rank {result.risk_rank}", 3)
            bullets([
                f"Crux: {result.crux}",
                f"Top FMEA: {result.top_fmea}",
                f"FTA cut set: {result.fta_cut_set}",
            ])
            if result.frameworks:
                bullets([
                    f"{fw.get('fw', 'FW')}: {fw.get('finding', '')} | action={fw.get('action', '')}"
                    for fw in result.frameworks
                ])
    else:
        paragraph("No gauntlet output saved.")

    heading("Audit", 2)
    if state.audit:
        paragraph(f"Data based: {state.audit.data_based}")
        bullets([f"Top finding: {item}" for item in state.audit.top_findings])
        bullets([f"Observation need: {item}" for item in state.audit.observation_needs])
        bullets([
            f"FMEA {item.component}: {item.failure_mode} | RPN {item.rpn} | action {item.action}"
            for item in state.audit.fmea
        ])
    elif state.audit_raw:
        paragraph(state.audit_raw)
    else:
        paragraph("No audit output saved.")

    heading("Strategy", 2)
    if state.strategy:
        paragraph(state.strategy.executive_strategy)
        heading("Preliminary verdicts", 3)
        bullets([
            f"{item.id}: {item.verdict} | evidence: {item.evidence} | monitoring: {item.monitoring_plan}"
            for item in state.strategy.preliminary_verdicts
        ])
        heading("Actions", 3)
        for action in state.strategy.strategies:
            heading(f"{action.priority}: {action.action}", 4)
            bullets([
                f"Justification: {action.justification}",
                f"Evidence chain: {action.evidence_chain}",
                f"Expected impact: {action.expected_impact}",
                f"Effort: {action.effort}",
                f"Timeline: {action.timeline}",
                f"Risk if ignored: {action.risk_if_ignored}",
                f"Framework source: {action.framework_source}",
            ])
        bullets([f"Success metric: {metric}" for metric in state.strategy.success_metrics])
        paragraph(f"Implementation sequence: {state.strategy.implementation_sequence}")
        paragraph(f"Monitoring plan: {state.strategy.monitoring_plan}")
        paragraph(f"Review date: {state.strategy.review_date}")
        paragraph(f"Confidence: {state.strategy.confidence}")
        paragraph(f"Re-entry check: {state.strategy.reentry_check}")
    elif state.strategy_raw:
        paragraph(state.strategy_raw)
    else:
        paragraph("No strategy output saved.")

    heading("Monitor", 2)
    if state.monitor:
        heading("OODA schedule", 3)
        bullets(_schedule_lines("Daily", state.monitor.ooda_schedule.daily))
        bullets(_schedule_lines("Weekly", state.monitor.ooda_schedule.weekly))
        bullets(_schedule_lines("Monthly", state.monitor.ooda_schedule.monthly))
        bullets([
            f"Circuit breaker - {item.strategy_ref}: trip {item.trip} | reset {item.reset}"
            for item in state.monitor.circuit_breakers
        ])
        bullets([
            f"Canary - {item.signal}: {item.direction} over {item.window} | {item.meaning}"
            for item in state.monitor.canaries
        ])
        bullets([
            f"Chaos drill - {item.what}: when {item.when} | measure {item.measure}"
            for item in state.monitor.chaos_drills
        ])
        bullets([f"HRO principle: {item}" for item in state.monitor.hro_principles_active])
        bullets([f"Re-entry watch: {item}" for item in state.monitor.reentry_watch])
        paragraph(f"Commitment score: {state.monitor.commitment_score}")
        paragraph(f"Commitment rationale: {state.monitor.commitment_rationale}")
    else:
        paragraph("No monitor output saved.")

    heading("Report", 2)
    if state.report:
        _append_markdownish_report(blocks, state.report)
    else:
        paragraph("No report saved.")

    return blocks


def _schedule_lines(label: str, entries: list) -> list[str]:
    return [
        f"{label} - {entry.metric} | owner: {entry.owner} | source: {entry.source}"
        for entry in entries
    ]


def _append_markdownish_report(blocks: list[dict], report: str) -> None:
    current_bullets: list[str] = []

    def flush_bullets():
        nonlocal current_bullets
        if current_bullets:
            blocks.append({"type": "bullets", "items": current_bullets})
            current_bullets = []

    for raw_line in (report or "").splitlines():
        line = raw_line.strip()
        if not line:
            flush_bullets()
            continue
        if line.startswith("### "):
            flush_bullets()
            blocks.append({"type": "heading", "text": line[4:].strip(), "level": 3})
        elif line.startswith("## "):
            flush_bullets()
            blocks.append({"type": "heading", "text": line[3:].strip(), "level": 2})
        elif line.startswith("# "):
            flush_bullets()
            blocks.append({"type": "heading", "text": line[2:].strip(), "level": 2})
        elif line.startswith("- "):
            current_bullets.append(line[2:].strip())
        else:
            flush_bullets()
            blocks.append({"type": "paragraph", "text": line})
    flush_bullets()


def _fmt_datetime(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _profile_filename(state: ProjectState, profile: str, fmt: str) -> str:
    project_id = re.sub(r"[^A-Za-z0-9-]+", "-", state.project_id or "project").strip("-") or "project"
    return f"{project_id}-{profile}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{fmt}"


def _safe_report_markdown(state: ProjectState) -> str:
    return _redact_unsafe_string(state.report or "No report available.")


def _project_metadata_line(state: ProjectState) -> str:
    return " | ".join(
        part
        for part in [
            f"Project ID: {state.project_id}",
            f"Project name: {_redact_unsafe_string(state.project_name or '')}",
            f"Generated: {_utc_now()}",
            f"Risk: {_redact_unsafe_string(state.risk_classification or '')}",
        ]
        if part.strip()
    )


def _extract_report_sections(report: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key = ""
    current_level = 0
    for raw_line in (report or "").splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw_line.strip())
        if match:
            heading = match.group(2).strip()
            key = _normalize_heading(heading)
            if key in {_normalize_heading(item) for item in REPORT_CLARITY_HEADINGS}:
                current_key = key
                current_level = len(match.group(1))
                sections.setdefault(current_key, [])
                continue
            if current_key and len(match.group(1)) <= current_level:
                current_key = ""
                current_level = 0
        if current_key:
            sections[current_key].append(raw_line)
    return {key: _redact_unsafe_string("\n".join(lines).strip()) for key, lines in sections.items()}


def _section_or_fallback(sections: dict[str, str], heading: str) -> str:
    value = sections.get(_normalize_heading(heading), "").strip()
    return value or "Not available in current report output."


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _citation_locator_summary_markdown(state: ProjectState, *, include_registry: bool) -> str:
    try:
        from cdp.citation_resolvability import build_defense_pass_result

        result = build_defense_pass_result(state)
    except Exception:
        return ""

    has_summary = bool(result.registry_entries or result.markers or result.summary_counts)
    if not has_summary:
        return ""

    lines = [
        "Citation locator review summary — confirms marker/locator availability only, not semantic support.",
    ]
    if result.summary_counts:
        rows = [["Metric", "Count"]]
        for key in (
            "canonical_marker_count",
            "resolved_exact",
            "resolved_id_only",
            "unknown_evidence_id",
            "locator_mismatch",
            "malformed",
            "load_bearing_review_count",
        ):
            if key in result.summary_counts:
                rows.append([key, str(result.summary_counts.get(key, 0))])
        if len(rows) > 1:
            lines.append(_markdown_table(rows))

    if include_registry and result.registry_entries:
        rows = [["Evidence ID", "Locator availability", "Title"]]
        for entry in result.registry_entries[:12]:
            locators = ", ".join(entry.locators or [])
            rows.append([
                entry.evidence_id,
                _redact_unsafe_string(locators or "No concrete locator registered"),
                _redact_unsafe_string(", ".join(entry.titles or [])),
            ])
        lines.append(_markdown_table(rows))
    return "\n\n".join(lines)


def _evidence_locator_register_markdown(state: ProjectState) -> str:
    entries = _evidence_locator_payload(state)
    if not entries:
        return "No evidence locator registry entries available."
    rows = [["Evidence ID", "Locators", "Source", "Title"]]
    for entry in entries[:30]:
        rows.append([
            entry.get("evidence_id", ""),
            ", ".join(entry.get("locators", []) or []) or "No concrete locator registered",
            entry.get("source_id", ""),
            entry.get("title", ""),
        ])
    return _markdown_table(rows)


def _client_clarifications_markdown(state: ProjectState) -> str:
    questions = []
    for cycle in state.clarification_cycles or []:
        for question in cycle.questions or []:
            status = _enum_value(question.status)
            if status in {"open", "unavailable"}:
                questions.append([
                    question.text,
                    question.why_it_matters,
                    question.affected_phase,
                    status,
                ])
    if not questions:
        return ""
    return _markdown_table([["Open question", "Why it matters", "Affected phase", "Status"], *questions])


def _operator_clarifications_markdown(state: ProjectState) -> str:
    rows = [["Question ID", "Question", "Affected phase", "Status", "Answer"]]
    answers = {answer.question_id: answer for answer in state.clarification_answers or []}
    for cycle in state.clarification_cycles or []:
        for question in cycle.questions or []:
            answer = answers.get(question.question_id)
            rows.append([
                question.question_id,
                question.text,
                question.affected_phase,
                _enum_value(question.status),
                answer.answer_text if answer else "",
            ])
    return _markdown_table(rows) if len(rows) > 1 else "No clarification questions or answers saved."


def _operator_overview_markdown(state: ProjectState) -> str:
    try:
        from overview import build_operator_overview

        overview = build_operator_overview(state)
        rows = [
            ["Field", "Value"],
            ["Project status", overview.project_status],
            ["Current recommendation", _short_text(overview.current_recommendation, 260)],
            ["Decision summary", _short_text(overview.decision_summary, 260)],
            ["Sources/files", _short_text(overview.sources_and_files_message, 260)],
            ["Next operator action", _short_text(overview.next_operator_action, 260)],
        ]
        return _markdown_table(rows)
    except Exception as exc:
        return f"Operator overview unavailable: {_short_text(str(exc), 180)}"


def _workspace_summary_markdown(state: ProjectState) -> str:
    try:
        from workspace import build_workspace_summary

        workspace = build_workspace_summary(state)
        rows = [
            ["Field", "Value"],
            ["Project status", workspace.project_status],
            ["Current phase", workspace.current_phase],
            ["Active risks", str(workspace.active_risk_count)],
            ["Decision object status", workspace.decision_object_health.status],
            ["Knowledge status", workspace.knowledge_health.status],
            ["SQI", _fmt_value(workspace.score_summary.sqi_overall)],
            ["Deterministic score", _fmt_value(workspace.score_summary.det_score_overall)],
            ["Brier score", _fmt_value(workspace.score_summary.brier_score)],
        ]
        if workspace.blocking_reasons:
            rows.append(["Blocking reasons", "; ".join(workspace.blocking_reasons)])
        return _markdown_table(rows)
    except Exception as exc:
        return f"Workspace summary unavailable: {_short_text(str(exc), 180)}"


def _summarize_gauntlet(state: ProjectState) -> str:
    if not state.gauntlet:
        return "No gauntlet output saved."
    rows = [["ID", "Risk rank", "Crux", "Top FMEA", "FTA cut set"]]
    for result in state.gauntlet.results or []:
        rows.append([
            result.id,
            _fmt_value(result.risk_rank),
            result.crux,
            str(result.top_fmea),
            str(result.fta_cut_set),
        ])
    return "\n\n".join(
        [
            f"Portfolio correlation: {_fmt_value(state.gauntlet.portfolio_correlation)}",
            f"MECE gaps: {state.gauntlet.mece_gaps or 'TBD — requires operator confirmation.'}",
            _markdown_table(rows),
        ]
    )


def _summarize_audit(state: ProjectState) -> str:
    if not state.audit:
        return _redact_unsafe_string(state.audit_raw or "No audit output saved.")
    rows = [["Type", "Summary"]]
    rows.extend([["Top finding", item] for item in state.audit.top_findings or []])
    rows.extend([["Observation need", item] for item in state.audit.observation_needs or []])
    for item in state.audit.fmea or []:
        rows.append(["FMEA", f"{item.component}: {item.failure_mode} | RPN {item.rpn} | action {item.action}"])
    return _markdown_table(rows)


def _summarize_strategy(state: ProjectState) -> str:
    if not state.strategy:
        return _redact_unsafe_string(state.strategy_raw or "No strategy output saved.")
    rows = [["Priority", "Action", "Expected impact", "Timeline", "Risk if ignored"]]
    for action in state.strategy.strategies or []:
        rows.append([
            _enum_value(action.priority),
            action.action,
            action.expected_impact,
            action.timeline,
            action.risk_if_ignored,
        ])
    metrics = "\n".join(f"- {metric}" for metric in state.strategy.success_metrics or [])
    return "\n\n".join([
        state.strategy.executive_strategy or "No executive strategy saved.",
        _markdown_table(rows),
        "### Success Metrics",
        metrics or "No success metrics saved.",
    ])


def _budget_summary_markdown(state: ProjectState) -> str:
    rows = [["Budget field", "Value"]]
    for key, value in (state.budget_caps or {}).items():
        rows.append([f"cap.{key}", _fmt_value(value)])
    for key, value in (state.budget_consumed or {}).items():
        rows.append([f"consumed.{key}", _fmt_value(value)])
    return _markdown_table(rows)


def _approvals_summary_markdown(state: ProjectState) -> str:
    approvals = _approvals_payload(state)
    if not approvals:
        return "No approvals saved."
    rows = [["Scope", "Status", "Requested by", "Resolved by", "Reason"]]
    for approval in approvals:
        rows.append([
            approval.get("scope", ""),
            approval.get("status", ""),
            approval.get("requested_by", ""),
            approval.get("resolved_by", ""),
            approval.get("reason", ""),
        ])
    return _markdown_table(rows)


def _calibration_prediction_summary_markdown(state: ProjectState) -> str:
    payload = _calibration_predictions_payload(state)
    if not payload:
        return "No calibration or prediction records saved."
    rows = [["Type", "ID/Phase", "Probability/Score", "Outcome/Notes"]]
    for prediction in payload.get("predictions", []):
        rows.append([
            "Prediction",
            f"{prediction.get('hypothesis_id', '')}/{prediction.get('phase', '')}",
            _fmt_value(prediction.get("predicted_probability")),
            _fmt_value(prediction.get("actual_outcome")),
        ])
    for snapshot in payload.get("calibration_snapshots", []):
        rows.append([
            "Calibration",
            snapshot.get("snapshot_id", ""),
            _fmt_value(snapshot.get("brier_score")),
            snapshot.get("notes", ""),
        ])
    return _markdown_table(rows)


def _phase_outputs_payload(state: ProjectState) -> dict[str, Any]:
    return {
        "classify": _model_dump(state.classify),
        "hypotheses": _model_dump(state.hypotheses),
        "gauntlet": _model_dump(state.gauntlet),
        "audit": _model_dump(state.audit),
        "audit_raw": state.audit_raw,
        "strategy": _model_dump(state.strategy),
        "strategy_raw": state.strategy_raw,
        "sqi": _model_dump(state.sqi),
        "monitor": _model_dump(state.monitor),
        "phase_summaries": dict(state.phase_summaries or {}),
        "phase_status": _model_dump(state.phase_status),
        "phase_confidence": dict(state.phase_confidence or {}),
        "phase_run_completed_at": dict(state.phase_run_completed_at or {}),
    }


def _decision_objects_payload(state: ProjectState) -> dict[str, Any]:
    if state.decision_objects is None:
        try:
            from decision_objects import ensure_decision_objects

            return _model_dump(ensure_decision_objects(state, trigger="export_profile"))
        except Exception:
            return {}
    return _model_dump(state.decision_objects)


def _clarifications_payload(state: ProjectState) -> dict[str, Any]:
    return {
        "cycles": _model_dump(state.clarification_cycles),
        "answers": _model_dump(state.clarification_answers),
    }


def _evidence_locator_payload(state: ProjectState) -> list[dict[str, Any]]:
    try:
        from cdp.citation_resolvability import build_evidence_locator_registry

        return [_model_dump(entry) for entry in build_evidence_locator_registry(state)]
    except Exception:
        return []


def _uploaded_file_manifest_payload(state: ProjectState) -> list[dict[str, Any]]:
    manifests = list(getattr(getattr(state, "knowledge_layer", None), "uploaded_files", []) or [])
    rows = []
    for manifest in manifests:
        storage_ref = getattr(manifest, "storage_ref", "")
        rows.append(
            {
                "file_id": getattr(manifest, "file_id", ""),
                "source_id": getattr(manifest, "source_id", ""),
                "original_filename": getattr(manifest, "filename", ""),
                "content_type": getattr(manifest, "media_type", ""),
                "size_bytes": getattr(manifest, "size_bytes", 0),
                "evidence_id": getattr(manifest, "evidence_id", ""),
                "uploaded_at": getattr(manifest, "uploaded_at", ""),
                "uploaded_by": getattr(manifest, "uploaded_by", ""),
                "parser_kind": getattr(manifest, "parser_kind", ""),
                "import_mode": getattr(manifest, "import_mode", ""),
                "storage_ref": REDACTED if _looks_like_unsafe_path(str(storage_ref)) else storage_ref,
                "parse_summary": _model_dump(getattr(manifest, "parse_summary", None)),
            }
        )
    return rows


def _policy_summary_payload(state: ProjectState) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    for event in state.policy_audit_log or []:
        event_type = str((event or {}).get("event_type", "unknown"))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    return {
        "risk_classification": state.risk_classification,
        "risk_classification_rationale": state.risk_classification_rationale,
        "risk_classification_set_by": state.risk_classification_set_by,
        "kill_switch_active": state.kill_switch_active,
        "kill_switch_reason": state.kill_switch_reason,
        "kill_switch_triggered_by": state.kill_switch_triggered_by,
        "kill_switch_triggered_at": state.kill_switch_triggered_at,
        "phase_breaker_count": len(state.phase_breakers or {}),
        "policy_audit_event_count": len(state.policy_audit_log or []),
        "policy_audit_event_types": event_counts,
        "intake_sanitization_summary": _sanitization_summary(state.intake_sanitization_findings),
    }


def _calibration_predictions_payload(state: ProjectState) -> dict[str, Any]:
    payload = {
        "brier_score": state.brier_score,
        "predictions": _model_dump(state.predictions),
        "calibration_snapshots": _model_dump(
            getattr(getattr(state, "decision_objects", None), "calibration_snapshots", [])
            or []
        ),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _approvals_payload(state: ProjectState) -> list[dict[str, Any]]:
    approvals = []
    decision_objects = state.decision_objects
    for approval in list(getattr(decision_objects, "approvals", []) or []):
        approvals.append(_model_dump(approval))
    for action, approval in (state.approvals_granted or {}).items():
        row = _model_dump(approval)
        if isinstance(row, dict):
            row.setdefault("scope", action)
            approvals.append(row)
    return approvals


def _risk_summary_payload(state: ProjectState) -> dict[str, Any]:
    risks = list(getattr(getattr(state, "decision_objects", None), "risks", []) or [])
    return {
        "risk_classification": state.risk_classification,
        "risk_classification_rationale": state.risk_classification_rationale,
        "risks": _model_dump(risks),
    }


def _sanitization_summary(findings: dict | None) -> dict[str, Any]:
    if not findings:
        return {}
    return {
        "highest_severity": findings.get("highest_severity"),
        "recommendation": findings.get("recommendation"),
        "brief_length": findings.get("brief_length"),
        "truncated": findings.get("truncated"),
        "finding_count_by_severity": findings.get("finding_count_by_severity", {}),
    }


def _zip_archive_bytes(files: dict[str, Any]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(files.keys()):
            if filename == "raw_project_state.json":
                continue
            payload = files[filename]
            if filename.endswith(".json"):
                content = json.dumps(
                    sanitize_for_export(payload, "machine_archive", mode="redact"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            else:
                content = _redact_unsafe_string(str(payload or ""))
            archive.writestr(filename, content.encode("utf-8"))
    return buf.getvalue()


def _export_markdown_docx_bytes(markdown: str, *, title: str) -> bytes:
    document = Document()
    document.core_properties.title = title
    document.core_properties.subject = "Decision Engine profile export"
    normal_style = document.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(10.5)

    for block in _markdown_to_blocks(markdown):
        if block["type"] == "heading":
            document.add_heading(block["text"], level=min(max(block["level"], 1), 4))
        elif block["type"] == "paragraph":
            document.add_paragraph(_strip_inline_markdown(block["text"]))
        elif block["type"] == "bullets":
            for item in block["items"]:
                document.add_paragraph(_strip_inline_markdown(item), style="List Bullet")
        elif block["type"] == "table":
            _add_docx_table(document, block["rows"])

    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _export_markdown_pdf_bytes(markdown: str, *, title: str) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    heading1 = ParagraphStyle("ProfileHeading1", parent=styles["Heading1"], fontSize=17, leading=21, spaceAfter=8)
    heading2 = ParagraphStyle("ProfileHeading2", parent=styles["Heading2"], fontSize=13, leading=17, spaceBefore=8, spaceAfter=6)
    heading3 = ParagraphStyle("ProfileHeading3", parent=styles["Heading3"], fontSize=11, leading=14, spaceBefore=6, spaceAfter=4)
    body = ParagraphStyle("ProfileBody", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=4)

    story = []
    for block in _markdown_to_blocks(markdown):
        if block["type"] == "heading":
            style = heading1 if block["level"] == 1 else heading2 if block["level"] == 2 else heading3
            story.append(Paragraph(_as_pdf_text(_strip_inline_markdown(block["text"])), style))
        elif block["type"] == "paragraph":
            story.append(Paragraph(_as_pdf_text(_strip_inline_markdown(block["text"])), body))
        elif block["type"] == "bullets":
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_as_pdf_text(_strip_inline_markdown(item)), body), leftIndent=10) for item in block["items"]],
                    bulletType="bullet",
                    leftIndent=12,
                )
            )
        elif block["type"] == "table":
            story.append(_pdf_table(block["rows"], body, doc.width))
        story.append(Spacer(1, 2))
    doc.build(story)
    return buf.getvalue()


def _markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = (markdown or "").splitlines()
    idx = 0
    bullets: list[str] = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            blocks.append({"type": "bullets", "items": bullets})
            bullets = []

    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            flush_bullets()
            idx += 1
            continue
        table = _consume_table(lines, idx)
        if table:
            flush_bullets()
            rows, next_idx = table
            blocks.append({"type": "table", "rows": rows})
            idx = next_idx
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_bullets()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2).strip()})
        elif re.match(r"^(-|\*)\s+", line):
            bullets.append(re.sub(r"^(-|\*)\s+", "", line).strip())
        elif re.match(r"^\d+\.\s+", line):
            bullets.append(re.sub(r"^\d+\.\s+", "", line).strip())
        else:
            flush_bullets()
            blocks.append({"type": "paragraph", "text": line})
        idx += 1
    flush_bullets()
    return blocks


def _consume_table(lines: list[str], start: int) -> tuple[list[list[str]], int] | None:
    if start + 1 >= len(lines):
        return None
    first = lines[start].strip()
    second = lines[start + 1].strip()
    if "|" not in first or not _is_markdown_separator_row(second):
        return None
    rows = [_split_table_row(first)]
    idx = start + 2
    while idx < len(lines) and "|" in lines[idx].strip():
        rows.append(_split_table_row(lines[idx].strip()))
        idx += 1
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return rows, idx


def _is_markdown_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [_strip_inline_markdown(cell.strip()) for cell in stripped.split("|")]


def _add_docx_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
    table.style = "Table Grid"
    for row_idx, row in enumerate(rows):
        for col_idx, value in enumerate(row):
            table.cell(row_idx, col_idx).text = _strip_inline_markdown(str(value))


def _pdf_table(rows: list[list[str]], body_style: ParagraphStyle, available_width: float) -> Table:
    column_count = max(len(row) for row in rows) if rows else 1
    col_widths = [available_width / column_count] * column_count
    table = Table(
        [[Paragraph(_as_pdf_text(str(cell)), body_style) for cell in row] for row in rows],
        colWidths=col_widths,
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _markdown_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    clean_rows = [[_table_cell(value) for value in row] for row in rows]
    width = max(len(row) for row in clean_rows)
    clean_rows = [row + [""] * (width - len(row)) for row in clean_rows]
    header = clean_rows[0]
    separator = ["---"] * width
    body = clean_rows[1:]
    return "\n".join(
        ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
        + ["| " + " | ".join(row) + " |" for row in body]
    )


def _table_cell(value: Any) -> str:
    return _redact_unsafe_string(str(value if value is not None else "")).replace("\n", " ").replace("|", "/").strip()


def _strip_inline_markdown(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = value.replace("**", "").replace("__", "")
    return value


def _sanitize_for_export(value, *, profile: str, mode: str):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, datetime):
        return value.isoformat()
    elif hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return getattr(value, "value")

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                if mode == "drop":
                    continue
                result[key_text] = REDACTED
                continue
            sanitized = _sanitize_for_export(item, profile=profile, mode=mode)
            if sanitized is not _DROP:
                result[key_text] = sanitized
        return result
    if isinstance(value, (list, tuple, set)):
        items = []
        for item in value:
            sanitized = _sanitize_for_export(item, profile=profile, mode=mode)
            if sanitized is not _DROP:
                items.append(sanitized)
        return items
    if isinstance(value, str):
        return _redact_unsafe_string(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower())
    return any(fragment in normalized for fragment in SENSITIVE_KEY_SUBSTRINGS)


def _redact_unsafe_string(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret|credential)\s*[:=]\s*[^,\s|;]+", r"\1=" + REDACTED, text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer " + REDACTED, text)
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", REDACTED, text)
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\s|]+", REDACTED, text)
    text = re.sub(r"\\\\[A-Za-z0-9_.-]+\\[^\s|]+", REDACTED, text)
    text = re.sub(r"file://[^\s|]+", REDACTED, text)
    text = re.sub(r"(?<!https:)(?<!http:)\b/(Users|home|mnt|var|tmp|root|workspace|Volumes|opt)/[^\s|]+", REDACTED, text)
    return text


def _looks_like_unsafe_path(value: str) -> bool:
    return _redact_unsafe_string(value) != str(value or "")


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_dump(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_dump(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _phase_output_text(state: ProjectState, phase: str) -> str:
    if phase == "audit" and state.audit_raw and not state.audit:
        return state.audit_raw
    if phase == "strategy" and state.strategy_raw and not state.strategy:
        return state.strategy_raw
    if phase == "report":
        return state.report or ""
    return json.dumps(_model_dump(getattr(state, phase, None)), ensure_ascii=False, default=str)


def _short_text(value: str, limit: int = 180) -> str:
    text = _redact_unsafe_string(str(value or "")).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fmt_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return _redact_unsafe_string(str(_enum_value(value)))


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _code_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"
