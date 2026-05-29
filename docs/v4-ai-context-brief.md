# v4 AI Context Brief

This brief is the canonical AI-readable description of the current v4 repository. It is written for future AI agents and operators who need to understand what v4 is, what is implemented, what is partial or scaffolded, and what must not be overclaimed. It is grounded in the repo as of v4.4 on branch `v44-expansion-wave`, including the recent expansion-wave commits through `fae348b` (`Add dashboard export profile controls`).

Status vocabulary used here:

- Implemented and runnable means active code paths, docs, and tests support the claim.
- Partial means meaningful code exists, but the capability is bounded, data-dependent, review-only, or missing an end-to-end product contract.
- Scaffolded means interfaces or future-facing shells exist, but no mature runtime behavior should be claimed.
- Planned means docs such as `docs/v5-ROADMAP.md` or tranche plans describe intent, but current v4.4 code should not be described as already shipping it.
- Known gap / do not overclaim means the repo either lacks the capability or explicitly warns against relying on it.

## 1. One-paragraph summary

v4 is a controlled decision-analysis engine that turns vague strategic, operational, product, or business questions into structured decision work: classification, hypotheses, stress testing, evidence audit, strategy, quality review, monitoring, and final report. It is operator-led, not autonomous: the code controls workflow order, deterministic gates, policy checks, persistence, retrieval eligibility, export boundaries, and report/citation guardrails, while LLM calls generate structured phase outputs inside those constraints. The strongest current shape is a single-operator, localhost-first Strategic Decision Audit workflow surfaced through `START_HERE.md`, the canonical v5 dashboard at `dashboards/index.html`, and the FastAPI backend in `mas/api.py`.

## 2. What v4 is

- An operator-led decision-analysis system. The daily path starts with `START_HERE.md`, runs the API with `cd mas && docker-compose up`, and uses `dashboards/index.html` to create, run, watch, kill, review, and export projects.
- A structured multi-phase reasoning engine. `mas/config.py` defines the workflow phases and framework routing; `mas/orchestrator.py` executes phase prompt builders and the sequential workflow; `mas/state.py` carries the shared project state.
- A hybrid deterministic + LLM system. The code enforces phase order, policy gates, budget caps, kill switch state, schema parsing, scoring, re-entry, retrieval eligibility, and persistence. LLMs generate classify/hypotheses/gauntlet/audit/strategy/SQI/monitor/report content.
- A tool for traceable, evidence-bounded strategic analysis. Decision objects, workspace summaries, explainability traces, controlled retrieval, uploaded evidence, and citation-resolvability checks live in `mas/decision_objects.py`, `mas/workspace.py`, `mas/explainability.py`, `mas/knowledge/*`, and `mas/cdp/*`.
- A profile-based export system. The legacy report export still exists, and newer profile exports in `mas/exporters.py` / `mas/api.py` support `report`, `client_dossier`, `operator_dossier`, and `machine_archive` with explicit audience boundaries and sanitizer/redaction rules.

## 3. What v4 is not

- It is not a generic chatbot.
- It is not a generic autonomous agent framework.
- It is not a BI dashboard or general analytics platform.
- It is not a replacement for human judgment.
- It is not a fully autonomous decision-maker.
- It is not enterprise-ready, multi-tenant, or public SaaS in the current v4.4 form.
- It is not a live external-action system. `mas/policy.py` defines irreversible external actions as a future category, but the core v4.4 flow produces analysis only.
- It is not a guarantee that cited evidence semantically proves every report claim. CDP v0.1 is a deterministic marker/locator resolvability pass, not a full claim-defensibility system (`mas/cdp/README.md`).
- It is not an "export everything to the client" system. Client dossier exports are intentionally bounded; machine archives are internal/operator-developer artifacts and are sanitized rather than raw state dumps.

## 4. Core workflow

The current operator-visible workflow is:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> SQI -> monitor -> report`

`mas/config.py` lists the 8 runtime phases in `PHASES`. `mas/orchestrator.py` uses the same 8-phase `WORKFLOW_PHASE_SEQUENCE`. Older docs and the phase table in `README.md` sometimes describe the engine as 6 main phases because `gauntlet` and `sqi` are support phases in `PHASE_ORDER` / `SUPPORT_PHASES`; the v4.4 console shows all 8 steps.

| Phase | Purpose | Expected output | Gate or downstream dependency |
|---|---|---|---|
| `classify` | Frame the decision domain and problem structure. Uses Cynefin, Bayes Factor, Requisite Variety, OODA, RPD, and Sensemaking. | `ClassifyOutput` with domain, BF, DQ fields, variety gaps, OODA, reference class, justification. | Supports depth selection, prompt context for all later phases, and classify gate checks in `mas/tools/scoring.py`. |
| `hypotheses` | Generate falsifiable hypotheses with priors and tests. | 8-12 `Hypothesis` records with `alpha`, `beta`, confirm/reject criteria, EVOI, portfolio cluster, and evidence IDs where available. | Feeds gauntlet, audit, strategy, Bayesian scoring, decision objects, and calibration-related surfaces. |
| `gauntlet` | Stress-test the riskiest hypotheses adversarially. | `GauntletOutput` with ranked hypothesis results, cruxes, FMEA/FTA findings, portfolio correlation, MECE gaps, Thompson/EVOI ranking. | Challenges hypothesis quality before audit and strategy. It is a support phase but appears in the v4.4 phase strip. |
| `audit` | Identify evidence gaps, failure modes, operational risks, and observation needs. | `AuditOutput` with FMEA items, top findings, data-backed vs predicted labeling, observation needs, and risk findings. | Feeds strategy, SQI, Workspace risks, report evidence, and controlled retrieval usage for the audit phase. |
| `strategy` | Synthesize a recommendation and action plan from hypotheses, gauntlet, and audit. | `StrategyOutput` with executive strategy, preliminary verdicts, actions, evidence chains, impacts, and monitoring plans. | Feeds SQI, monitor, report, decision actions, and strategy evidence explanations. |
| `SQI` | Score and stress-check strategy quality. | `SQIOutput` with strategy quality index fields and evaluative checks. | Helps detect weak strategy quality before monitor/report and appears in workspace score summaries. |
| `monitor` | Define how to watch the decision after the report. | `MonitorOutput` with OODA schedule, canaries, circuit breakers, chaos drills, HRO principles, re-entry watch, commitment score. | Feeds report, re-entry watch, and operator monitoring plans. |
| `report` | Produce the final operator-facing markdown report. Report Clarity T1 now asks for a client-facing decision memo with fixed headings such as Executive Summary, The Decision, Recommended Path, Evidence Used, Roadmap, Next Steps, Monitoring and Kill Criteria, and Appendix: Technical Analysis. | `ProjectState.report` markdown with evidence/citation discipline instructions. | Final artifact for human review; DOCX/PDF export uses `mas/exporters.py`. Existing reports are not rewritten automatically; only newly generated report-phase output uses the current prompt. CDP can review citation marker resolvability after generation. |

## 5. Architecture

### Orchestrator and state machine

The runtime center is `mas/orchestrator.py`. It imports LangGraph and can build a graph via `build_workflow_graph()`, but the v4.4 API path uses the sequential runner `run_workflow_sequence()` from `mas/api.py` background tasks. The sequential runner resumes from the first unfinished or failed phase, saves state after each phase, checks gates, and invalidates downstream phases when upstream content changes.

### Shared blackboard state

`ProjectState` in `mas/state.py` is the shared state carrier. It stores project input, phase outputs, phase status, confidence, DQ/deterministic scores, report text, decision objects, imported evidence/signals, knowledge layer, uploaded file manifests, policy state, risk classification, budget usage, approvals, calibration snapshots, predictions, and audit logs. Backward-compatible defaults are used heavily, and tests such as `mas/tests/test_decision_objects.py` and `mas/tests/scenarios/test_models.py` validate old payload loading.

### Phase-specific prompts

There are two prompt-related paths:

- Inline prompt builders in `mas/orchestrator.py` (`build_classify_prompt`, `build_hypotheses_prompt`, `build_gauntlet_prompt`, `build_audit_prompt`, `build_strategy_prompt`, `build_sqi_prompt`, `build_monitor_prompt`, `build_report_prompt`) are the active runtime builders.
- Prompt files in `mas/prompts/router.md` and `mas/prompts/phases/*` exist as the documented phase prompt library. `CHANGELOG.md` notes that full consolidation of inline builders with `mas/prompts/loader.py` is deferred because it can change behavior and needs a full eval sweep.
- `build_report_prompt` contains Report Clarity T1. The active report prompt instructs the LLM to write a client-facing decision memo with exact section headings, plain-language recommendations, explicit assumptions/open questions, 7/30/60/90 roadmap structure, next steps, monitoring/stop-change-course thresholds, and strict citation discipline. It also says evidence markers identify source material but do not by themselves prove the recommendation.

Do not claim prompt construction is fully consolidated.

### Deterministic gates and re-entry logic

`mas/tools/scoring.py` implements gate checks, deterministic scoring, Bayesian math helpers, and re-entry trigger evaluation. `mas/config.py` declares `GATE_CONFIGS`, `REENTRY_TRIGGERS` R1-R8, and `INVALIDATION_MAP`. Re-entry can send the state back to earlier phases when assumptions, domain classification, portfolio correlation, futility, SLOs, or commitment scores indicate drift.

### Policy enforcement outside the LLM

`mas/policy.py` is deterministic and runs outside the model. It includes reversibility classification, budget caps, kill switch checks, phase circuit breakers, HITL approvals, and policy audit logging. `mas/orchestrator.py` calls `policy_gate()` before LLM calls. The module is explicitly fail-soft because v4.4 is read-only by default; that posture would need re-evaluation before any irreversible external action surface.

### What is code-controlled versus LLM-generated

Code-controlled:

- phase order and resume behavior (`mas/orchestrator.py`)
- model routing config and framework lists (`mas/config.py`)
- policy gates, budget checks, kill switch, breaker state (`mas/policy.py`)
- retrieval eligibility and prompt projection (`mas/knowledge/retrieval.py`)
- upload parsing limits and sidecar file storage (`mas/knowledge/files.py`, `mas/knowledge/file_parsers.py`)
- parsing/validation into Pydantic state models (`mas/state.py`, `mas/orchestrator.py`)
- deterministic gates, scoring, re-entry, invalidation (`mas/tools/scoring.py`)
- decision object derivation (`mas/decision_objects.py`)
- workspace, overview, and trace derivation (`mas/workspace.py`, `mas/overview.py`, `mas/explainability.py`)
- persistence and fallback behavior (`mas/store.py`)
- report export mechanics (`mas/exporters.py`)
- citation marker/locator review (`mas/cdp/*`)

LLM-generated:

- structured phase outputs for classify, hypotheses, gauntlet, audit, strategy, SQI, monitor
- report markdown
- model judgment fields such as justifications, findings, preliminary verdicts, action rationales, and evidence chains

### Persistence layer

`mas/store.py` persists `ProjectState` snapshots as JSONB in Postgres when `DATABASE_URL` and `asyncpg` are available. It also ensures a parent row in `projects` exists for outcomes/events/approvals. If there is no database or connection fails, it falls back to process memory. That fallback is useful for local development but loses projects on restart.

### Observability and tracing

`mas/observability.py` is a thin Langfuse wrapper. If Langfuse keys are absent, it is a no-op. The runtime gateway in `mas/runtime/provider_gateway.py` emits summary metadata without dumping raw prompts/responses. `mas/explainability.py` builds backend-derived decision trace views without exposing raw chain-of-thought.

### Dashboard/operator console

`dashboards/index.html` is the canonical/default local operator dashboard. It defaults to `http://localhost:8000` for local file use, supports query-param/localStorage/manual API base overrides, uses the API directly, and surfaces project creation, portfolio summary, project workstation, phase strip, run/kill controls, Overview/Workspace/trace-style views, clarifications, Bayesian advisory display, uploads, report, export profiles, budget, calibration, and runtime readiness details. `dashboards/index-v5.html` is a compatibility/explicit v5 entry point that redirects to `dashboards/index.html`.

### API surface

`mas/api.py` is the FastAPI layer. It exposes project CRUD, run/phase execution, state, queue, overview, workspace, trace/explain, knowledge, uploads, CSV import, gates, report, export, outcomes, calibration, policy controls, approvals, breaker reset, kill switch, risk classification, deterministic clarifications, and scenario-shadow read routes. It does not implement API authentication in v4.4; CORS is configured with `allow_origins=["*"]`.

Export routes now have two shapes:

- Legacy route, unchanged: `GET /projects/{project_id}/export/{fmt}` with `fmt=pdf|docx`, exporting the current final report.
- Profile route: `GET /projects/{project_id}/export?profile={profile}&format={format}`. Supported combinations are `report` PDF/DOCX, `client_dossier` PDF/DOCX, `operator_dossier` PDF/DOCX, `machine_archive` ZIP, `client_monitoring_template` XLSX, and `operator_monitoring_template` XLSX. Invalid profiles or invalid profile/format pairs return HTTP 400.

## 6. Analytical framework library

v4 uses a 30-framework analytical library, but it should not run all frameworks blindly. Framework routing is phase-specific in `mas/config.py` and documented in `mas/shared_knowledge/v4-framework-encyclopedia.md`. `mas/tests/test_core.py::TestFrameworkDistribution` checks that all 30 frameworks are assigned and no phase has more than 10.

High-level routing:

- Classification frameworks: Cynefin, Requisite Variety, OODA, RPD, Sensemaking, BAYES_LITE.
- Hypothesis frameworks: HDD, BAYES_LITE, EVOI, Thompson Sampling, Information Gain, DOUBLE_CRUX.
- Risk/stress frameworks: STEELMAN, PREMORTEM, DOUBLE_CRUX, BAYES_LITE, SISTEMICO, LADDER, FMEA, HAZOP, FTA, Red Teaming.
- Audit frameworks: FMEA, HAZOP, FTA, Swiss Cheese, STPA, Mental Models, ODD, Chaos Engineering, Circuit Breaker, Canary.
- Strategy frameworks: Prospect Theory, PREMORTEM, SISTEMICO, LADDER, EVOI, STEELMAN.
- Monitoring frameworks: OODA, Chaos Engineering, Circuit Breaker, Canary, HRO.
- Report/verification frameworks: Causal Inference, Swiss Cheese, HRO, Red Teaming, Ablation.

The right claim is: v4 routes relevant framework subsets by phase. The wrong claim is: v4 exhaustively applies all 30 frameworks to every decision.

## 7. Current v4.4 operating status

v4.4 is primarily a UX/operator-surface release, not a new reasoning-engine redesign. `CHANGELOG.md` says v4.4 adds no new engine capability and changes the operator experience. `README.md` and `START_HERE.md` point operators to a single-screen console instead of a curl-first workflow.

The underlying v4.3 architecture remains the core engine: deterministic policy enforcement, prompt-injection intake sanitizer, risk classification, budget caps, kill switch, circuit breakers, persistence, prior consumption, and observability.

Recent `v44-expansion-wave` commits add bounded increments on top of that core: deterministic clarification storage/display, an internal Bayesian scenario adapter, Report Clarity T1 for newly generated reports, profile-based backend exports, monitoring template XLSX exports, and canonical v5 dashboard controls. These are additive surfaces, not an autonomous-agent redesign.

The canonical operator path is:

`START_HERE.md -> dashboards/index.html -> create project -> classify/risk rationale -> run -> watch/kill -> read Overview/Workspace/Decision trace/report -> export/review`

The older curl/API-driven workflow still exists and is useful for debugging and automation, but `START_HERE.md` makes the console the default daily-use surface.

## 8. Current boundaries and non-overclaim rules

- No API authentication in v4.4. `mas/api.py` has no auth dependency for routes and uses CORS `allow_origins=["*"]`. `START_HERE.md` and `CHANGELOG.md` explicitly call the console/API localhost-only and single-operator.
- No multi-tenancy. `ProjectState` and `store.py` are project-scoped, not workspace/org/tenant-scoped. There is no row-level tenant isolation or tenant auth layer.
- Assume localhost/single-operator unless hardened. Public deployment requires auth, network controls, rate limits, secret management, and proxy/hardening first.
- Kill switch and policy controls exist, but realistic operational testing still matters. `CHANGELOG.md` recommends kill-switch drills; `docs/v4.3-MATURITY-SELF-ASSESSMENT.md` marks security maturity as dependent on drills and governance work.
- Some template/vertical surfaces are less mature than the core Strategic Decision Audit path. `mas/extensions/packs.py` is scaffolding, and no concrete first-class Automation ROI or AI Readiness runtime pack was found in the active backend.
- Controlled retrieval is bounded. It is prompt-facing only for audit and strategy in `mas/knowledge/retrieval.py` and `mas/orchestrator.py`. It does not auto-rerun phases.
- Scenario shadow and Bayesian Scenarios T1 are internal/experimental adapters, not buyer-facing probability products. `docs/v4.4-SCENARIO-SHADOW.md` and `mas/scenarios/models.py` explicitly avoid workflow control and product surfaces.
- Deterministic clarifications are storage/display support, not a mature blocker-only halt loop. They do not control workflow routing or gates.
- Profile exports do not change access control. Without API auth, anyone who can reach the API can request report, dossier, or machine archive exports.
- Machine archives are sanitized internal ZIP artifacts. They should not be described as raw forensics, full database backups, or client deliverables.
- Every output requires human review before client-facing use. The engine drafts analysis; the operator owns delivery and judgment.

Note on doc/runtime drift: `docs/security/threat-model.md` contains mitigation language saying API auth is required for certain endpoints. Current v4.4 runtime and `START_HERE.md` indicate auth is not implemented. Treat auth as a required public-deployment gap, not as a shipped control.

## 9. Product/runtime differentiators

| Item | Status | Evidence | Notes |
|---|---|---|---|
| Uploaded evidence handling | Implemented | `mas/knowledge/files.py`, `mas/knowledge/file_parsers.py`, `mas/api.py`, `docs/v4.4-FILE-UPLOADS-OVERVIEW.md`, `mas/tests/test_file_uploads.py` | Supports PDF, DOCX, TXT, MD, CSV, XLSX with bounded parsing. Uploads do not auto-rerun analysis. |
| Deterministic clarifications | Implemented as storage/display support | `mas/clarifications.py`, `mas/api.py`, `dashboards/index.html`, `mas/tests/test_clarifications.py`, `mas/tests/test_dashboard_workspace_markup.py` | Generates deterministic follow-up questions, records answers/unavailable status, and displays them in the canonical dashboard. It is not a workflow-control loop and should not be sold as mature blocker-only clarification. |
| Automatic KPI inference | Partial | `mas/state.py` monitor/strategy models, `mas/orchestrator.py` monitor prompt, `mas/overview.py` metric cards | LLM-generated strategy metrics and monitor schedules exist. There is no separate deterministic KPI inference engine. |
| Resumable workflow execution | Implemented | `mas/orchestrator.py::run_workflow_sequence`, `mas/tests/test_workflow_runner.py` | Runner starts from first unfinished/failed phase and persists after phases. |
| Phase-specific LLM routing | Implemented with runtime hooks | `mas/config.py`, `mas/runtime/provider_gateway.py`, `mas/llm_client.py`, `mas/tests/test_runtime_gateway.py` | Phase defaults and override hooks exist. Full broader provider productization is still bounded. |
| Deeper reasoning budgets for key phases | Implemented in config | `mas/config.py` `MODEL_ROUTING` | Higher budgets are configured for hypotheses, strategy, report, audit, SQI. |
| Report Clarity T1 | Implemented for newly generated reports | `mas/orchestrator.py::build_report_prompt`, `mas/tests/test_report_evidence_register.py`, `mas/tests/test_workflow_runner.py` | The active report prompt asks for a client-facing decision memo with fixed headings and citation discipline. It does not rewrite old reports or PDFs. |
| Analysis quality fields | Implemented | `mas/state.py`, `mas/tools/scoring.py`, `mas/workspace.py`, `mas/tests/test_core.py` | DQ, deterministic scores, SQI, phase confidence, Brier fields, and gates exist. |
| Evidence sufficiency fields | Partial | `mas/state.py`, `mas/decision_objects.py`, `mas/explainability.py`, `mas/cdp/*` | Evidence IDs, provenance, observation needs, retrieval visibility, and citation locator checks exist. No full semantic evidence-strength gauge yet. |
| Template runtime status | Scaffolded | `mas/extensions/packs.py`, `docs/v4.4-TRANCHE-2-PLAN.md`, `dashboards/index.html` | Pack registry exists; dashboard demo-framing labels include template ideas. Active v4.4 backend does not show concrete template packs. |
| Workspace/trace/report/export surfaces | Implemented | `mas/workspace.py`, `mas/overview.py`, `mas/explainability.py`, `mas/exporters.py`, `mas/api.py`, `dashboards/index.html`, `mas/tests/test_exporters.py` | Backend-derived Workspace, Overview, trace/explain, markdown report, legacy DOCX/PDF report export, profile-based dossier exports, monitoring XLSX exports, and sanitized machine archive ZIP. The canonical dashboard has profile export controls. |
| Profile-based project exports | Implemented | `mas/exporters.py`, `mas/api.py`, `mas/tests/test_exporters.py` | Supports `report`, `client_dossier`, `operator_dossier`, and `machine_archive`. Client/operator dossiers render PDF/DOCX; machine archive returns sanitized ZIP. Client exports are bounded and do not dump raw ProjectState. |
| Strategic Decision Audit | Implemented as the core strongest path | `START_HERE.md`, `README.md`, `mas/orchestrator.py`, `mas/prompts/phases/*`, `lead-magnets/sample-decision-audit.md` | Best described as the mature default use case, not necessarily a separate template object. |
| Automation ROI | Not found as first-class runtime template | `lead-magnets/roi-calculator.html`, `dashboards/index.html` | Can be analyzed as a generic decision brief. Do not claim a hardened Automation ROI backend template in v4.4. |
| AI Readiness | Not found as first-class runtime template | `dashboards/index.html` mentions an option; active backend pack registry has no concrete pack | Can be analyzed generically, but do not overclaim dedicated runtime support. |
| Bayesian priors | Implemented, plus internal scenario adapter | `mas/state.py` `Hypothesis.alpha/beta`, `mas/priors.py`, `mas/tools/scoring.py`, `mas/scenarios/*`, `mas/tests/scenarios/*` | Priors feed prompts when snapshots exist. Bayesian Scenarios T1 is internal-only and deterministic. |
| Calibration/meta-learning | Partial / data-dependent | `mas/sql/outcomes.sql`, `mas/jobs/update_priors.py`, `mas/priors.py`, `mas/tools/calibration.py`, `mas/api.py` calibration endpoints | Brier/ECE/outcomes/prior snapshots exist. Framework value scoring is an honest stub until enough standardized resolved data exists. |
| Policy/risk classification | Implemented | `mas/policy.py`, `mas/security/intake_sanitizer.py`, `compliance/eu-ai-act-classification.md`, `mas/api.py` | Operator sets/owns classification. API will not magically enforce every Annex III judgment. |
| CDP citation review | Partial, review-only | `mas/cdp/README.md`, `mas/cdp/citation_resolvability.py`, `mas/tests/test_citation_resolvability.py` | Resolves citation markers to registered locator metadata; does not prove semantic support. |
| Scenario shadow | Implemented as shadow/internal | `docs/v4.4-SCENARIO-SHADOW.md`, `mas/scenarios/*`, `mas/tests/test_scenario_shadow.py` | Additive shadow behavior, SQLite sidecar; not workflow control. |

## 10. Strongest current use case

The strongest current use case is Strategic Decision Audit: an operator gives the engine a hard strategic, operational, product, or business decision; v4 classifies it, generates hypotheses, stress-tests them, audits risks/evidence gaps, synthesizes strategy, scores quality, defines monitoring, and drafts a report for review.

Evidence: `START_HERE.md` explains this daily workflow; `README.md` frames v4.4 as a single-screen operator release for the same engine; `mas/orchestrator.py`, `mas/config.py`, and `mas/state.py` implement the phase pipeline; `mas/evals/golden_cases.jsonl` contains realistic decision cases; `mas/tests/*` cover workflow, gates, trace, decision objects, retrieval, report evidence registers, and scenario adapters.

Automation ROI and AI Readiness may be valid topics to feed into the general Strategic Decision Audit workflow, but the repo evidence does not support claiming they are hardened first-class v4.4 templates. `lead-magnets/roi-calculator.html` is a marketing/lead-magnet artifact, and the dashboard treats these as demo-framing labels rather than backend runtime packs.

## 11. Human responsibility boundary

v4 assists judgment; it does not own the decision. Every report, recommendation, risk classification, EU AI Act classification, citation interpretation, monitoring plan, and client-facing conclusion requires human review before delivery. The system can be confidently wrong, stale, under-evidenced, or context-incomplete. The operator is responsible for deciding what to trust, what to rerun, what to revise, and what to send.

## 12. Known gaps / risks / open implementation issues

| Gap | Evidence | Why it matters |
|---|---|---|
| No API authentication | `mas/api.py`, `START_HERE.md`, `CHANGELOG.md` | Anyone who can reach the API can create/run/kill projects. Keep localhost/private or add auth before exposure. |
| No multi-tenancy | `mas/state.py`, `mas/store.py`, `README.md`, `CHANGELOG.md` | There is no tenant/org/workspace isolation, billing, or per-user permissions. |
| Process-local job state | `mas/api.py` uses `running: set[str]` and FastAPI `BackgroundTasks` | One worker/process assumption. Restarts or horizontal scaling can lose in-flight job status or duplicate execution protection. |
| In-memory store fallback | `mas/store.py`, `START_HERE.md` troubleshooting | Useful for local dev, but projects vanish on restart if Postgres is unavailable. |
| Redis is configured but not a durable job/lock layer | `mas/config.py` has `REDIS_URL`; active job control is `api.running` | Do not claim Redis-backed queueing, locks, or durable jobs. |
| Prompt builder drift | `mas/orchestrator.py`, `mas/prompts/loader.py`, `mas/prompts/phases/*`, `CHANGELOG.md` | Inline active prompts and file prompts coexist. Changes must verify which path is runtime-active. |
| Controlled retrieval is narrow | `mas/knowledge/retrieval.py`, `docs/v4.4-CONTROLLED-RETRIEVAL.md`, `START_HERE.md` | Only audit/strategy consume prompt-facing projections; no auto-rerun; no raw prompt dumps. |
| Knowledge sync is not broad live current-awareness | `mas/knowledge/sync.py`, `docs/v4.4-TRANCHE-3A-KNOWLEDGE-FOUNDATION.md` | Manual/offline and uploaded-file sources are supported; scheduled live connectors remain deferred. |
| Template/vertical parity is not mature | `mas/extensions/packs.py`, `docs/v4.4-TRANCHE-2-PLAN.md`, `dashboards/index.html` | Avoid selling v4.4 as a complete suite of vertical products. |
| Calibration loop is data-dependent | `mas/sql/outcomes.sql`, `mas/jobs/update_priors.py`, `mas/priors.py` | Priors need resolved outcomes and a running DB/job. Framework value scoring is explicitly stubbed until enough standardized data exists. |
| Policy is fail-soft | `mas/policy.py` | Appropriate for read-only analysis, but unsafe for future irreversible external actions without redesign. |
| CDP is not full claim defensibility | `mas/cdp/README.md`, `mas/cdp/citation_resolvability.py` | Resolved evidence markers mean locator metadata matches. They do not prove the cited material supports the claim. |
| Scenario shadow/T1 are internal, not live control | `docs/v4.4-SCENARIO-SHADOW.md`, `mas/scenarios/models.py`, `mas/tests/scenarios/*` | Do not expose or describe buyer-facing posterior probabilities from these adapters. |
| Clarifications are not workflow blockers | `mas/clarifications.py`, `mas/api.py`, `dashboards/index.html` | Deterministic questions/answers are useful operator context but do not halt, route, or gate workflow execution. |
| Export profiles are not access control | `mas/api.py`, `mas/exporters.py` | Dossier and archive export boundaries reduce accidental overexposure, but public deployment still needs auth/network controls before any export endpoint is exposed. |
| Dashboard deployment hardening and mobile polish remain gaps | `START_HERE.md`, `CHANGELOG.md`, `dashboards/index.html` | The console is a local operator surface, not a hardened hosted frontend. |
| Security docs contain some drift | `docs/security/threat-model.md` versus `START_HERE.md` / `mas/api.py` | The threat model describes desired auth mitigations that are not implemented in v4.4. Runtime evidence should win. |
| Human review is mandatory | `START_HERE.md`, `mas/cdp/README.md`, `mas/overview.py` next-action copy | Reports and recommendations are drafts for review, not autonomous deliverables. |

## 13. How another AI should use this context

- Preserve the distinction between implemented, partial, scaffolded, planned, and missing.
- Inspect repo files before making claims, especially `START_HERE.md`, `CHANGELOG.md`, `README.md`, `mas/orchestrator.py`, `mas/state.py`, `mas/api.py`, `mas/config.py`, `mas/policy.py`, and `mas/tests/*`.
- Treat `dashboards/index.html` as the canonical/default dashboard for daily operation. Treat `dashboards/index-v5.html` only as a compatibility/explicit v5 entry point that redirects to the canonical dashboard.
- Do not invent product features such as auth, multi-tenancy, public SaaS readiness, autonomous action execution, broad live connectors, or full claim defensibility.
- Do not claim Report Clarity T1 rewrites old reports. It changes newly generated report-phase output only.
- Do not describe CDP citation locator review as evidence validation, proof, semantic support, or a defensibility score.
- Do not expose Bayesian Scenario internals, raw provider payloads, raw prompts, chain-of-thought, or secrets in export descriptions.
- For export changes, inspect `mas/exporters.py`, `mas/api.py`, and `mas/tests/test_exporters.py`. Preserve legacy `/projects/{project_id}/export/{fmt}` behavior unless explicitly changing it.
- Recommend tests/evals for changes that touch prompts, orchestration, parsing, scoring, gates, policy, retrieval, persistence, exports, CDP, or scenario adapters.
- Prioritize the Strategic Decision Audit workflow unless the user explicitly asks for another use case.
- Keep v4 narrow and high-quality before expanding it into templates, vertical packs, or agentic execution.
- Avoid turning v4 into a generic chatbot or generic agent platform.
- Maintain traceability from claims to file paths and tests.
- When docs and runtime disagree, cite the disagreement and prefer active code/tests for implementation status.

## 14. Short reusable descriptions

One-sentence description:

v4 is a single-operator decision-analysis engine that uses deterministic workflow controls and LLM phase outputs to turn hard business questions into traceable Strategic Decision Audit reports.

One-paragraph description:

v4 takes a strategic, operational, product, or business decision brief and runs it through a controlled pipeline: classify, hypotheses, gauntlet, audit, strategy, SQI, monitor, and report. The code owns workflow order, gates, policy, retrieval eligibility, persistence, export boundaries, and trace surfaces, while LLMs generate structured analysis within phase-specific prompts. It is strongest today as a local operator-led Strategic Decision Audit tool, not as a public SaaS, autonomous agent, or fully hardened multi-tenant product.

Non-technical description:

v4 is a structured thinking tool for difficult decisions. You describe the decision, run the workflow, and get a draft analysis that lays out hypotheses, risks, evidence gaps, strategy, monitoring, and a report. You can export the report, a stakeholder dossier, an internal operator dossier, or a sanitized machine archive. A human still reviews and owns the final decision.

Technical description:

v4 is a Python/FastAPI decision engine built around `ProjectState`, deterministic scoring/policy gates, phase-specific LLM calls, Postgres JSONB snapshots with in-memory fallback, optional Langfuse tracing, controlled knowledge retrieval, derived decision objects, profile-based exporters, and single-file dashboard surfaces. It uses Pydantic models, phase routers, framework assignments, calibration/prior snapshots, report locator registers, CDP citation resolvability, deterministic clarification records, and internal scenario adapters to keep analysis structured and inspectable.

What makes it different:

v4 is not a chat transcript wrapped in a dashboard. Its value is the combination of fixed decision phases, explicit analytical framework routing, falsifiable hypotheses with priors, stress testing, deterministic gates, policy controls outside the model, evidence/provenance surfaces, export audience boundaries, calibration hooks, and human review boundaries.

## 15. Final status table

| Area | Current status | Evidence | Notes |
|---|---|---|---|
| Core workflow | Implemented and runnable | `mas/orchestrator.py`, `mas/config.py`, `START_HERE.md`, `mas/tests/test_workflow_runner.py` | 8 visible phases; 6 main phases plus gauntlet/SQI support phases in older framing. |
| Orchestrator | Implemented | `mas/orchestrator.py` | Sequential API runner is active; LangGraph graph builder also exists. |
| Phase prompts | Implemented with consolidation caveat | `mas/orchestrator.py`, `mas/prompts/phases/*`, `CHANGELOG.md` | Inline builders are active; file prompt loader exists; full consolidation deferred. Report Clarity T1 is active in `build_report_prompt` for newly generated reports. |
| ProjectState/state | Implemented | `mas/state.py`, `mas/tests/test_decision_objects.py` | Pydantic blackboard with additive compatibility fields. |
| Dashboard/operator console | Implemented for local operator use | `dashboards/index.html`, `dashboards/index-v5.html`, `START_HERE.md`, `mas/tests/test_dashboard_workspace_markup.py` | `index.html` is the canonical/default v5 dashboard. `index-v5.html` is a compatibility entry point. No separate build step; not hardened hosted frontend. |
| API | Implemented, unauthenticated | `mas/api.py` | Broad local REST surface; no auth; wildcard CORS. Includes legacy report export, profile export query route, deterministic clarification routes, and scenario-shadow read routes. |
| Policy layer | Implemented, fail-soft | `mas/policy.py`, `mas/orchestrator.py`, `CHANGELOG.md` | Outside LLM; kill switch/budgets/breakers/approvals; needs operational drills. |
| Persistence | Implemented with fallback caveat | `mas/store.py`, `mas/sql/outcomes.sql` | Postgres JSONB when configured; in-memory fallback loses state on restart. |
| Tracing/observability | Implemented optional | `mas/observability.py`, `mas/explainability.py`, `mas/runtime/provider_gateway.py` | Langfuse no-op if unset; backend trace surfaces avoid raw chain-of-thought. |
| Deterministic clarifications | Implemented as storage/display support | `mas/clarifications.py`, `mas/api.py`, `dashboards/index.html`, `mas/tests/test_clarifications.py` | Deterministic questions/answers/unavailable status; not workflow gating or routing. |
| Profile exports | Implemented | `mas/exporters.py`, `mas/api.py`, `mas/tests/test_exporters.py`, `dashboards/index.html` | Legacy report export preserved. Query route supports report, client dossier, operator dossier, monitoring XLSX templates, and sanitized machine archive. The canonical dashboard has profile controls. |
| Strategic Decision Audit | Strongest implemented path | `START_HERE.md`, `README.md`, `mas/orchestrator.py`, `lead-magnets/sample-decision-audit.md` | Core workflow is best described this way. |
| Automation ROI | Not first-class backend template | `lead-magnets/roi-calculator.html`, `dashboards/index.html` | Generic analysis possible; dedicated v4.4 runtime template not proven. |
| AI Readiness | Not first-class backend template | `dashboards/index.html`, `mas/extensions/packs.py` | Future-facing/scaffolded evidence only. |
| Calibration/meta-learning | Partial and data-dependent | `mas/sql/outcomes.sql`, `mas/jobs/update_priors.py`, `mas/priors.py`, `mas/tools/calibration.py`, `mas/api.py` | Brier/ECE/prior loop exists; needs resolved outcomes/DB/job; framework value score is stubbed. |
| Auth/multi-tenancy | Not implemented | `mas/api.py`, `START_HERE.md`, `CHANGELOG.md` | Single-operator localhost assumption. |
| Deployment readiness | Local/operator-ready, not public-hardened | `START_HERE.md`, `mas/docker-compose.yml`, `mas/api.py` | Public deployment requires auth/proxy/rate limits/ops hardening. |
| Human review | Mandatory | `START_HERE.md`, `mas/cdp/README.md`, `mas/overview.py` | v4 assists judgment; it does not own the decision. |
