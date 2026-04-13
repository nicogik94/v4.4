"""Project dossier exporters for DOCX and PDF downloads."""
from __future__ import annotations

from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
import re

from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

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
