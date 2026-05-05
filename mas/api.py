"""
v4 Multi-Agent System — REST API (v4.4)
Endpoints: create project, run workflow, get status, run single phase, get phase output.

v4.1: state persisted via store.py (PostgreSQL JSONB) — falls back to in-memory
if DATABASE_URL is unset. Langfuse tracing wired via observability.py.
"""
import asyncio
import json
import uuid
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks, Body, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from connectors import CONNECTOR_REGISTRY
from explainability import (
    ExplainabilityReport,
    PhaseTraceSummary,
    ProjectTrace,
    build_explainability_report,
    build_phase_trace,
    build_project_trace,
)
from knowledge import (
    delete_project_uploads,
    PhaseKnowledgeRetrievalView,
    ProjectKnowledgeRetrievalSummary,
    build_project_retrieval_summary,
    describe_uploaded_file,
    delete_uploaded_file,
    ensure_knowledge_layer,
    evaluate_phase_retrieval,
    get_uploaded_file_manifest,
    ingest_uploaded_file,
    list_uploaded_files,
    list_jobs as list_knowledge_jobs,
    list_sources as list_knowledge_sources,
    sync_multiple_sources,
    sync_offline_source,
    upsert_source_entry,
)
from knowledge.file_parsers import UploadParseError
from knowledge.freshness import build_knowledge_health
from overview import OperatorOverviewSummary, build_operator_overview
from scenarios import (
    ProjectScenarioShadowView,
    ScenarioPhaseShadowView,
    build_phase_shadow_view,
    build_project_shadow_view,
)
from state import (
    ProjectState, PhaseStatus,
    ClassifyOutput, Hypothesis, GauntletOutput,
    AuditOutput, StrategyOutput, MonitorOutput,
    KnowledgeLayerState, SourceRegistryEntry,
)
from decision_objects import ensure_decision_objects
from extensions.connectors import (
    CSVColumnMapping as CSVColumnMappingSpec,
    ConnectorImportRequest,
)
from ingestion import merge_imported_records
from orchestrator import is_workflow_complete, run_phase_node, run_workflow_sequence
from tools.scoring import (
    check_gate, compute_det_scores, invalidate_downstream, summarize_phase_output,
)
from workspace import QueueItem, WorkspaceSummary, build_queue_item, build_workspace_summary
from exporters import (
    build_export_filename,
    export_project_docx_bytes,
    export_project_pdf_bytes,
)
import store
import observability

logger = logging.getLogger("v4-api")

running: set[str] = set()
auto_refresh_jobs: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 v4 Multi-Agent Workflow API starting")
    if observability.enabled():
        logger.info("📊 Langfuse tracing: ON")
    await store._get_pool()  # warm pool
    yield
    await store.close()
    observability.flush()
    logger.info("🛑 Shutting down")


app = FastAPI(
    title="v4 Universal Project Workflow",
    description="Multi-agent decision engine with 30 frameworks",
    version="4.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateProjectRequest(BaseModel):
    name: str = "New Project"
    brief: str
    data: str = ""
    # v4.4 — optional combined classification at create time. Backward-compatible:
    # if omitted, project defaults to minimal_risk and operator can set later via
    # POST /projects/{id}/risk-classification.
    risk_classification: str | None = None
    risk_rationale: str = ""
    risk_set_by: str = "operator"

class RunPhaseRequest(BaseModel):
    phase: str

class PatchProjectInputRequest(BaseModel):
    project_name: str | None = None
    brief: str | None = None
    data: str | None = None
    observations: dict[str, str] | None = None
    timer_logs: list[dict] | None = None


class CSVImportMappingRequest(BaseModel):
    column: str
    target_type: str = "ignore"
    target_field: str = ""
    value_type: str = "string"
    required: bool = False
    transform: list[str] = []
    signal_kind: str | None = None
    confidence: float | None = None
    drop_if_empty: bool = False
    default_value: Any = None


class CSVImportRequest(BaseModel):
    filename: str = "import.csv"
    csv_text: str
    mapping: list[CSVImportMappingRequest]
    actor: str = "operator"
    dry_run: bool = False
    source_ref: str | None = None


class KnowledgeSourceUpsertRequest(BaseModel):
    source_id: str | None = None
    name: str
    source_kind: str = "offline_fixture"
    connector_type: str = "offline_fixture"
    owner: str = "operator"
    domain_tags: list[str] = []
    sensitivity: str = "internal"
    trust_tier: str = "operator_curated"
    enabled: bool = True
    access_mode: str = "manual"
    freshness_policy_id: str = "default_offline"
    secret_ref: str = ""
    notes: str = ""


class KnowledgeFixtureItemRequest(BaseModel):
    source_ref: str
    title: str = ""
    summary: str = ""
    structured_payload: dict[str, Any] = {}
    observed_at: str = ""
    effective_at: str = ""
    expires_at: str = ""
    trust_tier: str = "operator_curated"
    sensitivity: str = "internal"


class KnowledgeSourceSyncRequest(BaseModel):
    actor: str = "operator"
    items: list[KnowledgeFixtureItemRequest] = []


class KnowledgeMultiSyncSourceRequest(BaseModel):
    source_id: str
    items: list[KnowledgeFixtureItemRequest] = []


class KnowledgeSyncRequest(BaseModel):
    actor: str = "operator"
    sources: list[KnowledgeMultiSyncSourceRequest]

class ProjectResponse(BaseModel):
    project_id: str
    name: str
    current_phase: str
    phase_status: dict
    classify_domain: str | None = None
    hypothesis_count: int = 0
    strategy_count: int = 0
    sqi_score: float | None = None
    det_score: float | None = None
    brier_score: float | None = None
    reentry_count: int = 0


# v4.3 — policy layer request models
class KillSwitchRequest(BaseModel):
    reason: str
    triggered_by: str = "operator"

class RiskClassificationRequest(BaseModel):
    classification: str  # minimal_risk | limited_risk | high_risk | prohibited
    rationale: str
    set_by: str = "operator"

class BudgetCapsRequest(BaseModel):
    max_total_tokens: int | None = None
    max_total_cost_usd: float | None = None
    max_wall_clock_seconds: int | None = None
    max_llm_calls: int | None = None
    max_phase_reentries: int | None = None
    max_consecutive_failures: int | None = None

class ApprovalRequest(BaseModel):
    action: str
    approved_by: str = "operator"
    rationale: str = ""


class ResetBreakersRequest(BaseModel):
    phase: str | None = None
    reset_budget_failures: bool = True
    reset_phase_breakers: bool = True
    reset_by: str = "operator"
    rationale: str = ""


EDITABLE_PHASES = {"classify", "hypotheses", "gauntlet", "audit", "strategy", "monitor", "report"}


@app.get("/health")
async def health():
    pool = await store._get_pool()
    return {
        "status": "ok",
        "version": "4.4.0",
        "persistence": "postgres" if pool else "memory",
        "tracing": "langfuse" if observability.enabled() else "off",
    }


@app.post("/projects", response_model=ProjectResponse)
async def create_project(req: CreateProjectRequest):
    state = ProjectState(
        project_id=str(uuid.uuid4()),
        project_name=req.name,
        brief=req.brief,
        data=req.data,
        created_at=datetime.now(),
    )
    # v4.4 — apply optional classification at create time
    if req.risk_classification:
        valid = ("minimal_risk", "limited_risk", "high_risk", "prohibited")
        if req.risk_classification not in valid:
            raise HTTPException(400, f"risk_classification must be one of {valid}")
        state.risk_classification = req.risk_classification
        state.risk_classification_rationale = req.risk_rationale
        state.risk_classification_set_by = req.risk_set_by
        try:
            from policy import log_policy_event
            log_policy_event(state, "risk_classification_set", {
                "classification": req.risk_classification,
                "rationale": req.risk_rationale,
                "set_by": req.risk_set_by,
                "set_at": "create_time",
            })
        except Exception as e:
            logger.debug(f"policy log skipped at create ({e})")
    ensure_decision_objects(state, trigger="api.create_project")
    await store.save(state)
    try:
        import decision_events

        await decision_events.append(
            state.project_id,
            "project.created",
            actor_type="operator",
            actor_id=req.risk_set_by or "operator",
            payload={
                "project_name": state.project_name,
                "project_type": getattr(req, "project_type", ""),
                "risk_classification": state.risk_classification,
            },
        )
    except Exception as e:
        logger.debug(f"decision event append skipped at create ({e})")
    return _to_response(state)


@app.get("/projects/queue", response_model=list[QueueItem])
async def get_project_queue():
    states = await store.list_all()
    return [
        build_queue_item(state, workflow_running=state.project_id in running)
        for state in states
    ]


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return _to_response(state)


@app.get("/projects/{project_id}/state")
async def get_full_state(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    ensure_decision_objects(state, trigger="api.state")
    return state.model_dump(mode="json")


@app.get("/projects/{project_id}/workspace", response_model=WorkspaceSummary)
async def get_workspace(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_workspace_summary(state, workflow_running=project_id in running)


@app.get("/projects/{project_id}/overview", response_model=OperatorOverviewSummary)
async def get_overview(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_operator_overview(state)


@app.get("/projects/{project_id}/files")
async def get_uploaded_files(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return {
        "project_id": project_id,
        "files": [manifest.model_dump(mode="json") for manifest in list_uploaded_files(state)],
    }


@app.get("/projects/{project_id}/files/{file_id}")
async def get_uploaded_file(project_id: str, file_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    try:
        return describe_uploaded_file(state, file_id)
    except KeyError:
        raise HTTPException(404, "Uploaded file not found") from None


@app.get("/projects/{project_id}/trace", response_model=ProjectTrace)
async def get_project_trace(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_project_trace(state)


@app.get("/projects/{project_id}/trace/{phase}", response_model=PhaseTraceSummary)
async def get_phase_trace(project_id: str, phase: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    try:
        return build_phase_trace(state, phase)
    except KeyError:
        raise HTTPException(404, "Phase trace not found") from None


@app.get("/projects/{project_id}/explain", response_model=ExplainabilityReport)
async def get_explainability(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_explainability_report(state)


@app.get("/projects/{project_id}/scenarios/shadow", response_model=ProjectScenarioShadowView)
async def get_project_scenario_shadow(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_project_shadow_view(project_id)


@app.get("/projects/{project_id}/scenarios/shadow/{phase}", response_model=ScenarioPhaseShadowView)
async def get_project_scenario_shadow_phase(project_id: str, phase: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_phase_shadow_view(project_id, phase)


@app.get("/projects/{project_id}/knowledge")
async def get_knowledge(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    layer = state.knowledge_layer or KnowledgeLayerState()
    return {
        "project_id": project_id,
        "summary": build_knowledge_health(state),
        "retrieval_summary": build_project_retrieval_summary(state).model_dump(mode="json"),
        "knowledge_layer": layer.model_dump(mode="json"),
    }


@app.get("/projects/{project_id}/knowledge/sources")
async def get_knowledge_sources(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return [source.model_dump(mode="json") for source in list_knowledge_sources(state)]


@app.get("/projects/{project_id}/knowledge/jobs")
async def get_knowledge_jobs(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return [job.model_dump(mode="json") for job in list_knowledge_jobs(state)]


@app.get("/projects/{project_id}/knowledge/retrieval", response_model=ProjectKnowledgeRetrievalSummary)
async def get_knowledge_retrieval_summary(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return build_project_retrieval_summary(state)


@app.get("/projects/{project_id}/knowledge/retrieval/{phase}", response_model=PhaseKnowledgeRetrievalView)
async def get_knowledge_retrieval_phase(project_id: str, phase: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    try:
        return evaluate_phase_retrieval(state, phase)
    except KeyError:
        raise HTTPException(404, "Knowledge retrieval phase not found") from None


@app.post("/projects/{project_id}/knowledge/sources")
async def upsert_knowledge_source(project_id: str, req: KnowledgeSourceUpsertRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    ensure_knowledge_layer(state)
    source = upsert_source_entry(
        state,
        SourceRegistryEntry(
            source_id=req.source_id or str(uuid.uuid4()),
            name=req.name,
            source_kind=req.source_kind,
            connector_type=req.connector_type,
            owner=req.owner,
            domain_tags=list(req.domain_tags),
            sensitivity=req.sensitivity,
            trust_tier=req.trust_tier,
            enabled=req.enabled,
            access_mode=req.access_mode,
            freshness_policy_id=req.freshness_policy_id,
            secret_ref=req.secret_ref,
            notes=req.notes,
        ),
    )
    _log_knowledge_event(
        state,
        "knowledge_source_upserted",
        {
            "source_id": source.source_id,
            "name": source.name,
            "source_kind": source.source_kind,
            "connector_type": source.connector_type,
            "enabled": source.enabled,
            "access_mode": source.access_mode,
            "sensitivity": source.sensitivity,
        },
    )
    await store.save(state)
    return {
        "status": "registered",
        "project_id": project_id,
        "source": source.model_dump(mode="json"),
        "summary": build_knowledge_health(state),
    }


@app.post("/projects/{project_id}/knowledge/sync")
async def sync_project_knowledge(project_id: str, req: KnowledgeSyncRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)
    if not req.sources:
        raise HTTPException(400, "sources must contain at least one source sync payload")

    try:
        jobs = sync_multiple_sources(
            state,
            [
                {
                    "source_id": source.source_id,
                    "items": [item.model_dump(mode="json") for item in source.items],
                }
                for source in req.sources
            ],
            actor=req.actor,
        )
    except KeyError as exc:
        raise HTTPException(404, f"Knowledge source not found: {exc.args[0]}") from None
    _log_knowledge_event(
        state,
        "knowledge_sync_batch",
        {
            "actor": req.actor,
            "source_ids": [job.source_id for job in jobs],
            "statuses": [job.status.value if hasattr(job.status, "value") else str(job.status) for job in jobs],
        },
    )
    await store.save(state)
    return {
        "status": "completed",
        "project_id": project_id,
        "jobs": [job.model_dump(mode="json") for job in jobs],
        "summary": build_knowledge_health(state),
    }


@app.post("/projects/{project_id}/knowledge/sync/{source_id}")
async def sync_project_knowledge_source(project_id: str, source_id: str, req: KnowledgeSourceSyncRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    try:
        job = sync_offline_source(
            state,
            source_id,
            [item.model_dump(mode="json") for item in req.items],
            actor=req.actor,
        )
    except KeyError:
        raise HTTPException(404, "Knowledge source not found") from None

    _log_knowledge_event(
        state,
        "knowledge_sync",
        {
            "actor": req.actor,
            "source_id": source_id,
            "job_id": job.job_id,
            "job_status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "item_count": job.item_count,
        },
    )
    await store.save(state)
    return {
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "project_id": project_id,
        "job": job.model_dump(mode="json"),
        "summary": build_knowledge_health(state),
    }


@app.post("/projects/{project_id}/files")
async def upload_project_file(
    project_id: str,
    file: UploadFile = File(...),
    actor: str = Form("operator"),
    role: str = Form("context"),
    import_mode: str = Form("knowledge"),
    sheet_name: str = Form(""),
    mapping_json: str = Form(""),
):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    normalized_import_mode = (import_mode or "knowledge").strip().lower()
    if normalized_import_mode not in {"knowledge", "structured_import"}:
        raise HTTPException(400, "import_mode must be knowledge or structured_import")

    mapping = _parse_upload_mapping_json(mapping_json)
    try:
        content = await file.read()
        result = ingest_uploaded_file(
            state,
            filename=file.filename or "upload.bin",
            media_type=file.content_type or "",
            content=content,
            actor=actor,
            role=role,
            import_mode=normalized_import_mode,
            sheet_name=sheet_name,
            mapping=mapping,
        )
    except UploadParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    _log_uploaded_file_event(
        state,
        result=result,
        actor=actor,
        import_mode=normalized_import_mode,
    )
    await store.save(state)
    return {
        "status": "uploaded",
        "project_id": project_id,
        "manifest": result.manifest.model_dump(mode="json"),
        "parse_summary": result.manifest.parse_summary.model_dump(mode="json"),
        "source": result.source.model_dump(mode="json"),
        "knowledge_summary": result.knowledge_summary,
        "structured_import_summary": result.import_summary,
    }


@app.delete("/projects/{project_id}/files/{file_id}")
async def delete_project_file(project_id: str, file_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)
    manifest = get_uploaded_file_manifest(state, file_id)
    if manifest is None:
        raise HTTPException(404, "Uploaded file not found")
    result = delete_uploaded_file(state, file_id)
    _log_knowledge_event(
        state,
        "uploaded_file_deleted",
        {
            "file_id": file_id,
            "filename": manifest.filename,
            "source_id": manifest.source_id,
            "deleted_by": "operator",
        },
    )
    await store.save(state)
    return result


@app.post("/projects/{project_id}/run")
async def run_full_workflow(project_id: str, background_tasks: BackgroundTasks):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    if project_id in running:
        raise HTTPException(409, "Workflow already running")
    if is_workflow_complete(state):
        return {"status": "already_complete", "project_id": project_id}

    running.add(project_id)
    background_tasks.add_task(_run_workflow, project_id)
    return {"status": "started", "project_id": project_id}


@app.post("/projects/{project_id}/phase")
async def run_single_phase_endpoint(project_id: str, req: RunPhaseRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    if project_id in running:
        raise HTTPException(409, "Workflow already running")

    with observability.trace_phase(project_id, req.phase, {"trigger": "manual"}):
        updated = await run_phase_node(state, req.phase)
    await store.save(updated)
    phase_status = updated.phase_status.get(req.phase)
    return {
        "status": phase_status.value if hasattr(phase_status, "value") else str(phase_status),
        "phase": req.phase,
    }


@app.patch("/projects/{project_id}/input")
async def patch_project_input(project_id: str, req: PatchProjectInputRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No input fields provided")

    changed_keys: list[str] = []
    changed_field_paths: list[str] = []
    for field, value in updates.items():
        if getattr(state, field) != value:
            setattr(state, field, value)
            changed_keys.append(field)
            changed_field_paths.append(f"input.{field}")

    if not changed_keys:
        return {
            "status": "unchanged",
            "project_id": project_id,
            "section": "input",
            "changed_fields": [],
            "invalidated_phases": [],
            "next_phase": state.current_phase,
        }

    invalidated: list[str] = []
    if any(key in ("brief", "data") for key in changed_keys):
        invalidated = _invalidate_from_phase(state, "classify", include_self=True)
        state.current_phase = invalidated[0] if invalidated else "classify"
    elif any(key in ("observations", "timer_logs") for key in changed_keys):
        invalidated = _invalidate_from_phase(state, "monitor", include_self=True)
        state.current_phase = invalidated[0] if invalidated else "monitor"

    _log_operator_edit(state, "input", changed_field_paths, invalidated)
    ensure_decision_objects(state, trigger="api.patch_input")
    await store.save(state)
    return {
        "status": "updated",
        "project_id": project_id,
        "section": "input",
        "changed_fields": changed_field_paths,
        "invalidated_phases": invalidated,
        "next_phase": state.current_phase,
    }


@app.patch("/projects/{project_id}/phase-output/{phase}")
async def patch_phase_output(project_id: str, phase: str, payload: Any = Body(...)):
    phase = phase.strip().lower()
    if phase not in EDITABLE_PHASES:
        raise HTTPException(400, f"phase must be one of {sorted(EDITABLE_PHASES)}")

    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    validated, changed_field_paths = _validate_phase_payload(phase, payload)
    _apply_phase_output(state, phase, validated)
    _finalize_phase_output_edit(state, phase)

    invalidated = _invalidate_from_phase(state, phase, include_self=False)
    state.current_phase = invalidated[0] if invalidated else phase

    _log_operator_edit(state, phase, changed_field_paths, invalidated)
    ensure_decision_objects(state, trigger=f"api.patch_phase:{phase}")
    await store.save(state)
    return {
        "status": "updated",
        "project_id": project_id,
        "section": phase,
        "changed_fields": changed_field_paths,
        "invalidated_phases": invalidated,
        "next_phase": state.current_phase,
    }


@app.post("/projects/{project_id}/imports/csv")
async def import_csv(project_id: str, req: CSVImportRequest):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    _ensure_project_not_running(project_id)

    connector = CONNECTOR_REGISTRY.get("csv")
    if connector is None:
        raise HTTPException(503, "CSV connector is not available")

    mapping = [
        CSVColumnMappingSpec(**item.model_dump())
        for item in req.mapping
    ]
    source_ref = req.source_ref or f"{project_id}:{req.filename}"
    result = connector.ingest(
        ConnectorImportRequest(
            source_ref=source_ref,
            raw_text=req.csv_text,
            initiated_by=req.actor,
            dry_run=req.dry_run,
            filename=req.filename,
            mapping=mapping,
        )
    )

    totals: dict[str, int] = {
        "evidence_total": len(state.imported_evidence or []),
        "signal_total": len(state.imported_signals or []),
    }
    persisted = False
    if not req.dry_run:
        totals = merge_imported_records(
            state,
            evidence=result.evidence,
            signals=result.signals,
        )
        _log_connector_import(state, req, result)
        ensure_decision_objects(state, trigger="api.import_csv")
        await store.save(state)
        persisted = True

    return {
        "status": "imported" if persisted else "validated",
        "project_id": project_id,
        "connector": result.connector_name,
        "dry_run": req.dry_run,
        "persisted": persisted,
        "checksum": result.checksum,
        "row_count": result.row_count,
        "imported_rows": result.imported_rows,
        "skipped_rows": result.skipped_rows,
        "evidence_count": len(result.evidence),
        "signal_count": len(result.signals),
        "warnings": list(result.warnings),
        "unknown_columns": list(result.unknown_columns),
        "mapped_columns": list(result.mapped_columns),
        "row_issues": [vars(issue) for issue in result.row_issues],
        "totals": totals,
    }


@app.get("/projects/{project_id}/gate/{phase}")
async def check_phase_gate(project_id: str, phase: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    return check_gate(state, phase)


@app.get("/projects/{project_id}/report")
async def get_report(project_id: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")
    if not state.report:
        raise HTTPException(404, "Report not generated yet")
    return {"report": state.report}


@app.get("/projects/{project_id}/export/{fmt}")
async def export_project(project_id: str, fmt: str):
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "Project not found")

    fmt = fmt.strip().lower()
    if fmt == "docx":
        payload = export_project_docx_bytes(state)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "pdf":
        payload = export_project_pdf_bytes(state)
        media_type = "application/pdf"
    else:
        raise HTTPException(400, "fmt must be pdf or docx")

    filename = build_export_filename(state, fmt)
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/projects")
async def list_projects():
    states = await store.list_all()
    return [_to_response(s) for s in states]


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    ok = await store.delete(project_id)
    if ok:
        delete_project_uploads(project_id)
    return {"deleted": ok}


# ═══ Outcomes (feedback loop) ═══

class OutcomeRecord(BaseModel):
    hypothesis_id: str
    phase: str
    predicted_probability: float
    realized: bool
    realized_value: float | None = None
    notes: str = ""
    recorded_by: str = "client"


@app.post("/projects/{project_id}/outcomes")
async def record_outcome(project_id: str, outcome: OutcomeRecord):
    """Record a realized outcome. Feeds jobs/update_priors.py the next time it runs."""
    pool = await store._get_pool()
    if pool is None:
        raise HTTPException(503, "Outcomes require database persistence (set DATABASE_URL)")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO outcomes (
                project_id, hypothesis_id, phase, predicted_probability,
                realized, realized_value, notes, recorded_by, resolution_date
            ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (project_id, hypothesis_id) DO UPDATE
            SET realized = EXCLUDED.realized,
                realized_value = EXCLUDED.realized_value,
                notes = EXCLUDED.notes,
                resolution_date = NOW()
            """,
            project_id, outcome.hypothesis_id, outcome.phase,
            outcome.predicted_probability, outcome.realized,
            outcome.realized_value, outcome.notes, outcome.recorded_by,
        )
    try:
        import decision_events

        await decision_events.append(
            project_id,
            "outcome.recorded",
            actor_type="operator",
            actor_id=outcome.recorded_by,
            phase=outcome.phase,
            payload={
                "hypothesis_id": outcome.hypothesis_id,
                "phase": outcome.phase,
                "predicted_probability": outcome.predicted_probability,
                "realized": outcome.realized,
                "realized_value": outcome.realized_value,
                "notes": outcome.notes,
            },
        )
    except Exception as e:
        logger.debug(f"decision event append skipped for outcome ({e})")
    return {"status": "recorded", "project_id": project_id, "hypothesis_id": outcome.hypothesis_id}


@app.get("/projects/{project_id}/calibration")
async def get_calibration(project_id: str):
    """Return the calibration_delta view for a project: Brier, mean_pred, mean_realized."""
    pool = await store._get_pool()
    if pool is None:
        raise HTTPException(503, "Calibration requires database persistence")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM calibration_deltas WHERE project_id = $1::uuid", project_id
        )
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


@app.get("/calibration/priors")
async def latest_priors():
    """Return the latest prior_snapshots row per phase (what the orchestrator should seed with)."""
    pool = await store._get_pool()
    if pool is None:
        return {"priors": [], "note": "database not configured"}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (phase)
                phase, snapshot_date, n_outcomes, brier_score, ece,
                recommended_alpha, recommended_beta, direction
            FROM prior_snapshots
            ORDER BY phase, snapshot_date DESC
            """
        )
    return {"priors": [dict(r) for r in rows]}


@app.get("/calibration/framework-performance")
async def framework_performance(days: int = 90):
    """Return framework usage stub from jobs/update_priors.py (v4.2).

    Brier-weighted columns stay NULL until the operator populates them — dashboards
    should treat NULL as 'data not yet available', not 'framework had no value'.
    """
    pool = await store._get_pool()
    if pool is None:
        return {"rows": [], "note": "database not configured"}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (framework_name, phase)
                framework_name, phase, snapshot_date, n_uses, n_verdict_changes,
                avg_brier_when_used, avg_brier_when_absent, value_score
            FROM framework_performance
            ORDER BY framework_name, phase, snapshot_date DESC
            """
        )
    return {"rows": [dict(r) for r in rows]}


@app.get("/calibration/deltas")
async def calibration_deltas_endpoint():
    """Return calibration_deltas view rows — Brier per project across resolved outcomes."""
    pool = await store._get_pool()
    if pool is None:
        return {"deltas": [], "note": "database not configured"}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT project_id, name, completed_at,
                   n_predictions, n_resolved, mean_brier,
                   mean_predicted, mean_realized, calibration_delta
            FROM calibration_deltas
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 100
            """
        )
    return {"deltas": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════
# v4.3 — POLICY LAYER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/projects/{project_id}/kill")
async def kill_project(project_id: str, req: KillSwitchRequest):
    """Trigger the kill switch on a project. The orchestrator will halt
    on its next phase or LLM call check. Persisted to the store so it
    survives orchestrator restarts.

    This is the deterministic enforcement primitive — the LLM cannot
    disarm or bypass it. Returns 200 even if the project is already
    halted (idempotent)."""
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    from policy import trigger_kill_switch, log_policy_event
    trigger_kill_switch(state, reason=req.reason, triggered_by=req.triggered_by)
    log_policy_event(state, "kill_switch_triggered", {
        "reason": req.reason,
        "triggered_by": req.triggered_by,
    })
    await store.save(state)

    return {
        "project_id": project_id,
        "kill_switch_active": True,
        "reason": req.reason,
        "triggered_by": req.triggered_by,
        "triggered_at": state.kill_switch_triggered_at,
    }


@app.post("/projects/{project_id}/risk-classification")
async def set_risk_classification(project_id: str, req: RiskClassificationRequest):
    """Set the EU AI Act risk classification for a project. Default for
    every new project is minimal_risk; the operator MUST override if the
    use case is in Annex III (employment, creditworthiness, life/health
    insurance, etc.).

    See compliance/eu-ai-act-classification.md for the decision tree.
    """
    valid = ("minimal_risk", "limited_risk", "high_risk", "prohibited")
    if req.classification not in valid:
        raise HTTPException(400, f"classification must be one of {valid}")

    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    state.risk_classification = req.classification
    state.risk_classification_rationale = req.rationale
    state.risk_classification_set_by = req.set_by

    from policy import log_policy_event
    log_policy_event(state, "risk_classification_set", {
        "classification": req.classification,
        "rationale": req.rationale,
        "set_by": req.set_by,
    })
    ensure_decision_objects(state, trigger="api.risk_classification")
    await store.save(state)

    return {
        "project_id": project_id,
        "risk_classification": req.classification,
        "rationale": req.rationale,
        "set_by": req.set_by,
    }


@app.get("/projects/{project_id}/budget")
async def get_budget(project_id: str):
    """Return current budget consumption against caps. Operators check
    this before scaling up or running additional phases."""
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    consumed = state.budget_consumed
    caps = state.budget_caps

    return {
        "project_id": project_id,
        "caps": caps,
        "consumed": {
            "total_tokens": consumed.get("total_tokens", 0),
            "total_cost_usd": round(consumed.get("total_cost_usd", 0.0), 4),
            "llm_call_count": consumed.get("llm_call_count", 0),
            "consecutive_failures": consumed.get("consecutive_failures", 0),
        },
        "headroom": {
            "tokens": caps.get("max_total_tokens", 0) - consumed.get("total_tokens", 0),
            "cost_usd": round(caps.get("max_total_cost_usd", 0.0) - consumed.get("total_cost_usd", 0.0), 4),
            "llm_calls": caps.get("max_llm_calls", 0) - consumed.get("llm_call_count", 0),
        },
    }


@app.post("/projects/{project_id}/budget")
async def set_budget_caps(project_id: str, req: BudgetCapsRequest):
    """Update budget caps for a project. Only fields provided in the
    request are updated; omitted fields keep their existing values.
    Caps cannot be set below current consumption."""
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    caps = state.budget_caps
    consumed = state.budget_consumed

    updates = req.dict(exclude_unset=True)
    for key, value in updates.items():
        if value is None:
            continue
        # Sanity check: cannot set cap below current consumption
        if key == "max_total_tokens" and value < consumed.get("total_tokens", 0):
            raise HTTPException(400, f"cannot set max_total_tokens below current consumption {consumed['total_tokens']}")
        if key == "max_total_cost_usd" and value < consumed.get("total_cost_usd", 0.0):
            raise HTTPException(400, f"cannot set max_total_cost_usd below current consumption {consumed['total_cost_usd']}")
        caps[key] = value

    from policy import log_policy_event
    log_policy_event(state, "budget_caps_updated", updates)
    await store.save(state)

    return {"project_id": project_id, "caps": caps}


@app.post("/projects/{project_id}/approvals")
async def grant_approval(project_id: str, req: ApprovalRequest):
    """Grant HITL approval for a specific action on a project. Required
    for irreversible_internal actions on high-risk projects and for any
    irreversible_external action."""
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    if state.approvals_granted is None:
        state.approvals_granted = {}
    state.approvals_granted[req.action] = {
        "approved_by": req.approved_by,
        "rationale": req.rationale,
        "granted_at": datetime.now().isoformat(),
    }

    from policy import log_policy_event
    log_policy_event(state, "approval_granted", {
        "action": req.action,
        "approved_by": req.approved_by,
        "rationale": req.rationale,
    })
    ensure_decision_objects(state, trigger=f"api.approval:{req.action}")
    await store.save(state)

    return {"project_id": project_id, "action": req.action, "approved": True}


@app.post("/projects/{project_id}/breakers/reset")
async def reset_breakers(project_id: str, req: ResetBreakersRequest):
    """Operator reset for stuck policy breakers on a single project.

    This clears the project-wide consecutive failure counter used by the
    budget gate and/or closes one or more persisted phase breakers.
    """
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")

    budget_before = 0
    budget_after = 0
    reset_phases: list[str] = []

    if req.reset_budget_failures:
        if state.budget_consumed is None:
            state.budget_consumed = {}
        budget_before = state.budget_consumed.get("consecutive_failures", 0)
        state.budget_consumed["consecutive_failures"] = 0
        budget_after = 0
    else:
        budget_after = (state.budget_consumed or {}).get("consecutive_failures", 0)

    if req.reset_phase_breakers:
        if state.phase_breakers is None:
            state.phase_breakers = {}
        targets = [req.phase] if req.phase else list(state.phase_breakers.keys())
        for phase_name in targets:
            if not phase_name:
                continue
            state.phase_breakers[phase_name] = {
                "state": "closed",
                "failure_count": 0,
                "last_failure_at": None,
                "opened_at": None,
            }
            reset_phases.append(phase_name)

    from policy import log_policy_event
    log_policy_event(state, "circuit_breaker_reset", {
        "phase": req.phase or "all_persisted",
        "reset_budget_failures": req.reset_budget_failures,
        "reset_phase_breakers": req.reset_phase_breakers,
        "budget_consecutive_failures_before": budget_before,
        "budget_consecutive_failures_after": budget_after,
        "reset_phases": reset_phases,
        "reset_by": req.reset_by,
        "rationale": req.rationale,
    })
    await store.save(state)

    return {
        "project_id": project_id,
        "budget_consecutive_failures": (state.budget_consumed or {}).get("consecutive_failures", 0),
        "phase_breakers_reset": reset_phases,
        "reset_by": req.reset_by,
    }


@app.get("/projects/{project_id}/policy-audit")
async def get_policy_audit(project_id: str):
    """Return the full policy audit log for a project. This is the
    source of truth for any compliance review or post-incident
    investigation."""
    state = await store.load(project_id)
    if not state:
        raise HTTPException(404, "project not found")
    return {
        "project_id": project_id,
        "audit_log": state.policy_audit_log,
        "intake_sanitization": state.intake_sanitization_findings,
        "risk_classification": state.risk_classification,
        "kill_switch_active": state.kill_switch_active,
    }


def _ensure_project_not_running(project_id: str) -> None:
    if project_id in running:
        raise HTTPException(409, "Workflow already running")


def _phase_has_material_output(state: ProjectState, phase: str) -> bool:
    if phase == "audit":
        return state.audit is not None or bool(state.audit_raw)
    if phase == "strategy":
        return state.strategy is not None or bool(state.strategy_raw)
    if phase == "report":
        return bool(state.report)
    value = getattr(state, phase, None)
    if isinstance(value, list):
        return len(value) > 0
    return value is not None


def _clear_phase_output(state: ProjectState, phase: str) -> None:
    if phase == "classify":
        state.classify = None
    elif phase == "hypotheses":
        state.hypotheses = None
        state.sealed = False
        state.seal_date = None
    elif phase == "gauntlet":
        state.gauntlet = None
    elif phase == "audit":
        state.audit = None
        state.audit_raw = None
    elif phase == "strategy":
        state.strategy = None
        state.strategy_raw = None
        state.det_scores = None
    elif phase == "monitor":
        state.monitor = None
    elif phase == "sqi":
        state.sqi = None
    elif phase == "report":
        state.report = None


def _mark_phase_stale(state: ProjectState, phase: str) -> bool:
    had_output = _phase_has_material_output(state, phase)
    had_status = state.phase_status.get(phase, PhaseStatus.PENDING) != PhaseStatus.PENDING
    had_summary = phase in state.phase_summaries
    had_confidence = phase in state.phase_confidence

    _clear_phase_output(state, phase)

    if had_output or had_status or had_summary or had_confidence:
        state.phase_status[phase] = PhaseStatus.STALE
        state.phase_confidence.pop(phase, None)
        state.phase_summaries.pop(phase, None)
        return True
    return False


def _invalidate_from_phase(
    state: ProjectState, phase: str, include_self: bool = False
) -> list[str]:
    invalidated: list[str] = []
    if include_self and _mark_phase_stale(state, phase):
        invalidated.append(phase)
    invalidated.extend(invalidate_downstream(state, phase))
    return invalidated


def _field_paths_for_payload(section: str, payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [f"{section}.{key}" for key in sorted(payload.keys())]
    if isinstance(payload, list):
        paths: list[str] = []
        for index, item in enumerate(payload):
            label = item.get("id", index) if isinstance(item, dict) else index
            if isinstance(item, dict):
                for key in sorted(item.keys()):
                    paths.append(f"{section}[{label}].{key}")
            else:
                paths.append(f"{section}[{index}]")
        return paths or [section]
    return [section]


def _require_dict_payload(phase: str, payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(400, f"{phase} payload must be a JSON object")
    return payload


def _validate_phase_payload(phase: str, payload: Any):
    try:
        if phase == "classify":
            data = _require_dict_payload(phase, payload)
            return ClassifyOutput(**data), _field_paths_for_payload(phase, data)

        if phase == "hypotheses":
            items = payload.get("hypotheses") if isinstance(payload, dict) and "hypotheses" in payload else payload
            if not isinstance(items, list):
                raise HTTPException(400, "hypotheses payload must be a JSON array or an object with a hypotheses array")
            return [Hypothesis(**item) for item in items], _field_paths_for_payload("hypotheses", items)

        if phase == "gauntlet":
            data = _require_dict_payload(phase, payload)
            return GauntletOutput(**data), _field_paths_for_payload(phase, data)

        if phase == "audit":
            data = _require_dict_payload(phase, payload)
            return AuditOutput(**data), _field_paths_for_payload(phase, data)

        if phase == "strategy":
            data = _require_dict_payload(phase, payload)
            return StrategyOutput(**data), _field_paths_for_payload(phase, data)

        if phase == "monitor":
            data = _require_dict_payload(phase, payload)
            return MonitorOutput(**data), _field_paths_for_payload(phase, data)

        if phase == "report":
            if isinstance(payload, dict):
                report_text = payload.get("report")
                if not isinstance(report_text, str):
                    raise HTTPException(400, "report payload must contain a string field named report")
                return report_text, ["report"]
            if not isinstance(payload, str):
                raise HTTPException(400, "report payload must be a string or an object with a report field")
            return payload, ["report"]
    except HTTPException:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid {phase} payload: {exc}") from exc

    raise HTTPException(400, f"Unsupported editable phase: {phase}")


def _apply_phase_output(state: ProjectState, phase: str, validated) -> None:
    if phase == "classify":
        state.classify = validated
    elif phase == "hypotheses":
        state.hypotheses = validated
    elif phase == "gauntlet":
        state.gauntlet = validated
    elif phase == "audit":
        state.audit = validated
        state.audit_raw = None
    elif phase == "strategy":
        state.strategy = validated
        state.strategy_raw = None
        state.det_scores = compute_det_scores(state.strategy)
    elif phase == "monitor":
        state.monitor = validated
    elif phase == "report":
        state.report = validated


def _finalize_phase_output_edit(state: ProjectState, phase: str) -> None:
    state.phase_status[phase] = PhaseStatus.COMPLETED
    state.phase_confidence[phase] = 1.0
    if phase == "hypotheses":
        state.sealed = True
        state.seal_date = datetime.now().date().isoformat()
    state.phase_summaries[phase] = summarize_phase_output(phase, state)


def _log_operator_edit(
    state: ProjectState,
    section: str,
    changed_field_paths: list[str],
    invalidated_phases: list[str],
) -> None:
    from policy import log_policy_event

    log_policy_event(state, "operator_state_edit", {
        "section": section,
        "changed_field_paths": changed_field_paths,
        "invalidated_phases": invalidated_phases,
        "edited_by": "operator",
    })


def _analysis_pending_phase_for_import(state: ProjectState) -> str:
    for phase in ("report", "monitor", "sqi", "strategy", "audit", "gauntlet", "hypotheses", "classify"):
        if _phase_has_material_output(state, phase):
            return phase
    return ""


def _log_connector_import(state: ProjectState, req: CSVImportRequest, result) -> None:
    from policy import log_policy_event

    pending_phase = _analysis_pending_phase_for_import(state)
    log_policy_event(state, "connector_import", {
        "connector": "csv",
        "filename": req.filename,
        "source_ref": req.source_ref or f"{state.project_id}:{req.filename}",
        "imported_by": req.actor,
        "dry_run": req.dry_run,
        "row_count": result.row_count,
        "imported_rows": result.imported_rows,
        "skipped_rows": result.skipped_rows,
        "evidence_count": len(result.evidence),
        "signal_count": len(result.signals),
        "warning_count": len(result.warnings),
        "row_issue_count": len(result.row_issues),
        "checksum": result.checksum,
        "unknown_columns": list(result.unknown_columns),
        "mapped_columns": list(result.mapped_columns),
        "analysis_pending": bool(pending_phase),
        "analysis_pending_phase": pending_phase,
    })


def _parse_upload_mapping_json(mapping_json: str) -> list[CSVColumnMappingSpec]:
    raw = (mapping_json or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"mapping_json is invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise HTTPException(400, "mapping_json must be a JSON array")
    try:
        return [CSVColumnMappingSpec(**item) for item in payload]
    except (TypeError, ValidationError, ValueError) as exc:
        raise HTTPException(400, f"mapping_json is invalid: {exc}") from exc


def _log_uploaded_file_event(state: ProjectState, *, result, actor: str, import_mode: str) -> None:
    pending_phase = _analysis_pending_phase_for_import(state)
    _log_knowledge_event(
        state,
        "uploaded_file_ingested",
        {
            "file_id": result.manifest.file_id,
            "source_id": result.manifest.source_id,
            "filename": result.manifest.filename,
            "uploaded_by": actor,
            "role": result.manifest.role.value if hasattr(result.manifest.role, "value") else str(result.manifest.role),
            "parser_kind": result.manifest.parser_kind,
            "import_mode": import_mode,
            "knowledge_item_count": result.manifest.parse_summary.knowledge_item_count,
            "evidence_count": result.manifest.parse_summary.evidence_count,
            "signal_count": result.manifest.parse_summary.signal_count,
            "analysis_pending": bool(pending_phase),
            "analysis_pending_phase": pending_phase,
        },
    )


def _log_knowledge_event(state: ProjectState, event_type: str, details: dict[str, Any]) -> None:
    state.policy_audit_log.append(
        {
            "ts": datetime.now().timestamp(),
            "event_type": event_type,
            "phase": state.current_phase,
            "details": details,
        }
    )


async def _run_workflow(project_id: str):
    try:
        state = await store.load(project_id)
        if not state:
            logger.error(f"Project {project_id} vanished before run")
            return
        with observability.trace_phase(project_id, "full_workflow", {"trigger": "api"}):
            final_state = await run_workflow_sequence(state, persist_state=store.save)
        await store.save(final_state)
        if is_workflow_complete(final_state):
            logger.info(f"✅ Workflow complete: {project_id}")
        else:
            logger.warning(
                f"Workflow stopped before completion: {project_id} "
                f"(current_phase={final_state.current_phase}, "
                f"status={final_state.phase_status.get(final_state.current_phase)})"
            )
    except Exception as e:
        logger.error(f"❌ Workflow failed: {e}", exc_info=True)
    finally:
        running.discard(project_id)


def _to_response(s: ProjectState) -> ProjectResponse:
    return ProjectResponse(
        project_id=s.project_id,
        name=s.project_name,
        current_phase=s.current_phase,
        phase_status={k: v.value if hasattr(v, 'value') else str(v) for k, v in s.phase_status.items()},
        classify_domain=s.classify.domain if s.classify else None,
        hypothesis_count=len(s.hypotheses or []),
        strategy_count=len(s.strategy.strategies) if s.strategy else 0,
        sqi_score=s.sqi.sqi_overall if s.sqi else None,
        det_score=s.det_scores.overall if s.det_scores else None,
        brier_score=s.brier_score,
        reentry_count=len(s.reentry_triggers_fired),
    )
