# Wave 11.8 - Workflow Runner Consolidation Discovery

## Goal

Map the current workflow runner and execution surfaces, then decide whether a future Wave 11.9 consolidation patch is safe, useful, and small enough.

This wave is discovery-only. It does not implement runner consolidation, refactor workflow execution, add tests, or change runtime behavior.

## Scope

- Inspect current operator workflow execution paths.
- Identify active, scaffolded, legacy, test-only, eval-only, and future-facing runner surfaces.
- Map phase-order sources of truth and drift risks.
- Map resume, downstream invalidation, persistence, policy gates, kill switch, circuit breakers, and budget gates.
- Produce a decision-useful recommendation for Wave 11.9 with file/path evidence.

## Non-goals

- No workflow runner consolidation.
- No workflow routing redesign.
- No source behavior changes.
- No new tests.
- No eval semantic changes.
- No report/export behavior changes.
- No readiness semantic changes.
- No autonomous monitoring or action behavior.
- No public SaaS, multi-tenant, chatbot, BI, or public-user account behavior.

## Source files inspected

- `ai_context/v4_v5_current_progress.md`
- `ai_context/waves/wave_11_6_support_phase_test_hardening.md`
- `ai_context/waves/wave_11_7_repo_hygiene_generated_artifacts.md`
- `config.py`
- `orchestrator.py`
- `api.py`
- `main.py`
- `state.py`
- `policy.py`
- `store.py`
- `workspace.py`
- `overview.py`
- `delivery_readiness.py`
- `workflow_templates.py`
- `runtime/run_state.py`
- `runtime/work_queue.py`
- `tools/scoring.py`
- `evals/run_evals.py`
- `evals/run_evals_batch.py`
- Relevant workflow/API/support tests discovered by tracked-file search.

## Discovery commands run

Primary discovery:

```bash
git grep -n -E "run_workflow_sequence|build_workflow_graph|BackgroundTasks|background|running|resume|invalidate|kill_switch|circuit|budget|phase_order|WORKFLOW|PHASE" -- '*.py' '*.md' ':!ai_context/waves/*'
git ls-files 'tests/*workflow*' 'tests/*orchestrator*' 'tests/*api*' 'tests/*workspace*' 'tests/*delivery*' 'tests/*support*'
git ls-files '*runner*' '*workflow*' '*queue*' '*jobs*'
```

Focused follow-up discovery:

```bash
git grep -n "compile_workflow\|build_workflow_graph\|run_workflow_sequence\|run_full_workflow\|run_single_phase_endpoint" -- '*.py'
git grep -n "invalidate_downstream\|_invalidate_from_phase\|_clear_phase_output\|_mark_phase_stale" -- '*.py'
git grep -n "workflow_phase_sequence_for_state\|WORKFLOW_PHASE_SEQUENCE\|STRATEGIC_AUDIT_PHASE_SEQUENCE\|PHASE_ORDER\|PHASES" -- '*.py' 'tests/*.py'
git grep -n "projects/.*/run\|/run\"\|/run'\|run_full_workflow\|run workflow\|Run Workflow\|run-workflow" -- '*.py' '*.js' '*.ts' '*.tsx' '*.html' '*.md' ':!ai_context/waves/*'
```

The broader `grep -R` fallback was not needed because tracked-file search and focused follow-up searches found the material references.

## Current execution surfaces

### API full-workflow operator path

The operator-facing full workflow path is `POST /projects/{project_id}/run` in `api.run_full_workflow`. It loads persisted project state, rejects duplicate active runs, recovers stale runs, creates durable run-state metadata, then either:

- enqueues a durable workflow job through `runtime.work_queue.enqueue_workflow_job` and schedules `_drain_workflow_queue`, or
- schedules `_run_workflow` directly through FastAPI `BackgroundTasks` when durable queue mode is not active.

Both branches ultimately execute `api._run_workflow`, which calls `orchestrator.run_workflow_sequence`.

Evidence:

- `api.py:948` `run_full_workflow`
- `api.py:1604` `_ensure_project_not_running`
- `api.py:1901` `_drain_workflow_queue`
- `api.py:1913` `_execute_workflow_job`
- `api.py:1950` `_run_workflow`
- `api.py:1983` call to `run_workflow_sequence`
- `runtime/run_state.py`
- `runtime/work_queue.py`
- `tests/test_workflow_runner.py:2321`, `tests/test_workflow_runner.py:2342`, `tests/test_workflow_runner.py:2358`, `tests/test_workflow_runner.py:2375`, `tests/test_workflow_runner.py:2390`, `tests/test_workflow_runner.py:2420`, `tests/test_workflow_runner.py:2443`

### Sequential runner

`orchestrator.run_workflow_sequence` is the active full-workflow runner for the API path. It resumes at the first unfinished phase, invalidates downstream phases from that point, persists before and after phase execution, runs `run_phase_node` for each phase in the project template sequence, applies deterministic scoring/re-entry behavior, and halts on failed phases or structural gate blockers.

Evidence:

- `orchestrator.py:1743` `run_workflow_sequence`
- `orchestrator.py:1757` downstream invalidation from start phase
- `orchestrator.py:1764` template-driven sequence
- `orchestrator.py:1771` persist before phase
- `orchestrator.py:1778` call to `run_phase_node`
- `orchestrator.py:1816` quality-gate force-proceed handling
- `orchestrator.py:1826` structural gate halt
- `tests/test_workflow_runner.py:1691`, `tests/test_workflow_runner.py:1729`, `tests/test_workflow_runner.py:1750`, `tests/test_workflow_runner.py:1800`

### Single-phase/manual operator path

`api.run_single_phase_endpoint` runs one phase through `run_phase_node`, saves state, and is guarded against concurrent active full-workflow runs.

Evidence:

- `api.py:993` `run_single_phase_endpoint`
- `api.py:997` `_ensure_project_not_running`
- `api.py:999` `run_phase_node`
- `tests/test_workflow_runner.py:2530` manual phase rejects while workflow is running

### LangGraph builder and CLI/demo path

`orchestrator.build_workflow_graph` and `compile_workflow` still exist. `main.py` compiles the graph and invokes it for a CLI/demo-style `run_project` path, and support-phase tests call `build_workflow_graph` directly. The API operator path does not call `compile_workflow` or `build_workflow_graph`.

This path is not test-only because `main.py` can invoke it, but it is separate from the operator API runtime path.

Evidence:

- `main.py:12` imports `compile_workflow`
- `main.py:36` calls `compile_workflow`
- `orchestrator.py:2000` `build_workflow_graph`
- `orchestrator.py:2092` `compile_workflow`
- `tests/test_support_phases.py:219`, `tests/test_support_phases.py:238` direct graph-node coverage
- Focused grep showed no `api.py` call to `compile_workflow` or `build_workflow_graph`

### Eval/mock runner paths

`evals/run_evals.py` and `evals/run_evals_batch.py` are eval-only surfaces. Real eval cases manually run a subset of phases and do not use the full operator workflow runner. They must stay separate from operator runner consolidation because eval semantics and provider diagnostics are distinct from runtime workflow execution.

Evidence:

- `evals/run_evals.py:412` `run_case_real`
- `evals/run_evals.py:418` manual phase loop over `["classify", "hypotheses", "gauntlet", "audit", "strategy"]`
- `evals/run_evals_batch.py` delegates to eval case runners and batch judge paths
- Wave 11.5A context says provider-quota aggregate diagnostics do not change eval semantics

### Dashboard-triggered path

The dashboard-triggered run path is discoverable through tracked tests rather than a tracked dashboard bundle in this checkout. `tests/test_dashboard_workspace_markup.py` asserts the canonical dashboard contains `/projects/${state.selectedProjectId}/run`, which maps to the API full-workflow operator path.

Evidence:

- `tests/test_dashboard_workspace_markup.py:82`
- `api.py:948` `POST /projects/{project_id}/run`

## Active operator path

The active operator full-workflow path is:

1. `POST /projects/{project_id}/run`
2. `api.run_full_workflow`
3. `runtime.run_state.create_workflow_run`
4. durable queue path through `runtime.work_queue` or direct FastAPI background task
5. `api._run_workflow`
6. `orchestrator.run_workflow_sequence`
7. `orchestrator.run_phase_node`
8. `store.save`

Confidence: high. The API imports `run_workflow_sequence`, `_run_workflow` calls it directly, and workflow-runner tests patch `api.run_workflow_sequence` to exercise run-state and queue behavior.

## Scaffolded, test, legacy, and future-facing paths

- `orchestrator.build_workflow_graph` / `compile_workflow`: secondary CLI/demo and direct-test path, not the API operator path.
- `main.py.run_project`: CLI/demo path using LangGraph. It can execute workflow behavior if invoked, but is not the API background workflow runner.
- `api.run_single_phase_endpoint`: active manual operator path, not a full-workflow runner.
- `evals/run_evals.py` and `evals/run_evals_batch.py`: eval-only paths. Keep separate from runtime runner consolidation.
- `route_after_gate` / `route_after_reentry`: graph routing helpers tied to `PHASE_ORDER`, not used by the sequential API runner.

## Phase-order source of truth and drift

The practical runtime source of truth is `workflow_templates.get_workflow_phase_sequence(project_type)`.

Evidence:

- `workflow_templates.py:15` `STRATEGIC_AUDIT_PHASE_SEQUENCE`
- `workflow_templates.py:26` `TECHNOLOGY_READINESS_PHASE_SEQUENCE`
- `workflow_templates.py:133` `get_workflow_phase_sequence`
- `orchestrator.py:894` `workflow_phase_sequence_for_state`
- `orchestrator.py:1764` sequential runner uses `workflow_phase_sequence_for_state`
- `state.py:931` default `phase_status` uses `STRATEGIC_AUDIT_PHASE_SEQUENCE`
- `state.py:1041` model post-init backfills template phase status/re-entry state
- `tools/scoring.py:137` downstream invalidation first asks `get_downstream_phases`

Drift risks:

- `orchestrator.WORKFLOW_PHASE_SEQUENCE` duplicates the strategic-audit sequence instead of importing the template constant.
- `config.PHASES` duplicates the full strategic phase list.
- `config.PHASE_ORDER` is a main-six list used by graph routing helpers, not the active sequential runner.
- `config.INVALIDATION_MAP` remains as a fallback for invalidation, while template-driven downstream ordering is used first.

Existing tests already assert part of this alignment:

- `tests/test_technology_readiness.py:226` asserts `WORKFLOW_PHASE_SEQUENCE == STRATEGIC_AUDIT_PHASE_SEQUENCE`
- `tests/test_workflow_runner.py:2426` and `tests/test_workflow_runner.py:2448` assert workflow run phase order behavior

## Gate, persistence, resume, and invalidation enforcement map

| Concern | Current enforcement | Evidence | Notes |
| --- | --- | --- | --- |
| Full-run concurrency | `api.running` process-local set and durable active-run checks | `api.py:126`, `api.py:1604`, `runtime/run_state.py` | API protects both direct background and durable queue paths. |
| Stale active run recovery | Run endpoint calls durable recovery before starting | `api.py:959`, `runtime/run_state.py:199` | Existing tests cover stale recovery. |
| Durable queue | API enqueues and drains jobs through `runtime.work_queue` | `api.py:970`, `api.py:1901`, `runtime/work_queue.py` | Queue is API/background infrastructure, not runner logic. |
| Runner resume | Sequential runner starts at first unfinished phase | `orchestrator.py:924`, `orchestrator.py:1756` | Resume behavior is in the runner, not queue. |
| Downstream invalidation | Sequential runner calls `invalidate_downstream`; API edit endpoints call `_invalidate_from_phase`, which calls `invalidate_downstream` | `orchestrator.py:1757`, `api.py:1672`, `tools/scoring.py:134` | API has extra clear/stale helper logic for edited phase itself. |
| Per-phase persistence | API passes `persist_workflow_state` into `run_workflow_sequence` | `api.py:1961`, `orchestrator.py:1771`, `orchestrator.py:1798` | Run state and project state are both updated. |
| Store persistence | `store.save` persists project state | `store.py:52` | Store supports memory and Postgres paths. |
| Policy gates | `run_phase_node` calls `policy_gate` before LLM phase execution | `orchestrator.py:1331`, `policy.py:363` | Applies kill switch, budget caps, breakers, HITL. |
| Kill switch | `policy_gate` blocks when `state.kill_switch_active` is true | `policy.py:272`, `policy.py:377` | Readiness/workspace also report it. |
| Budget gates | `policy_gate` calls `check_budget` | `policy.py:157`, `policy.py:383` | Workspace/readiness summarize budget breaker signals. |
| Phase circuit breakers | `policy_gate` checks `PhaseBreaker` state | `policy.py:302`, `policy.py:393` | Workspace/readiness report open breakers. |
| Structural quality gates | Sequential runner calls `check_gate` after phases | `orchestrator.py:1807`, `tools/scoring.py:19` | Quality shortfalls may force proceed; structural blockers halt. |
| Workspace blocker projection | Workspace summary reports kill switch, failed phases, budget breaker, open breakers | `workspace.py:283` | Projection only; not an execution surface. |
| Delivery readiness blocker projection | Delivery readiness reports phase/blocker signals | `delivery_readiness.py:221`, `delivery_readiness.py:313` | Projection only; does not mutate or approve. |

## Duplication and hidden coupling findings

1. Phase order has multiple names and lists.
   `workflow_templates.py` is the template source, but `orchestrator.WORKFLOW_PHASE_SEQUENCE`, `config.PHASES`, and `config.PHASE_ORDER` still exist. The active runner uses templates; graph routing helpers use `PHASE_ORDER`; scenarios use `PHASES`.

2. There are two full-workflow execution styles.
   The API uses the sequential runner. `main.py` uses compiled LangGraph. Consolidating them would touch execution semantics and should not be treated as a small patch.

3. API run infrastructure is coupled to durable run state and queue posture.
   `_run_workflow` updates durable run-state metadata around `run_workflow_sequence`. A consolidation that moves runner ownership without preserving these updates risks breaking queue/run observability.

4. Invalidation has shared and API-specific layers.
   `tools.scoring.invalidate_downstream` is shared; `api._invalidate_from_phase` adds self-stale/edit handling. Removing that layering without tests risks changing operator edit behavior.

5. Eval runners are deliberately separate.
   Evals run a limited subset and have provider/quota aggregate semantics. They should not be folded into operator runtime execution.

## Evidence table

| Finding | Evidence path / function / test | Why it matters | Confidence | Implication for Wave 11.9 |
| --- | --- | --- | --- | --- |
| API run endpoint is the active operator full-run entrypoint. | `api.py:948` `run_full_workflow`; `tests/test_dashboard_workspace_markup.py:82`; README route table | Operator and dashboard runs converge on the same API path. | High | Any consolidation must preserve this endpoint contract. |
| API full-run path executes `run_workflow_sequence`. | `api.py:1950` `_run_workflow`; `api.py:1983`; `tests/test_workflow_runner.py:2321` patches `api.run_workflow_sequence` | Confirms active runner implementation. | High | Do not replace this with graph execution without a dedicated behavior wave. |
| Durable queue and run-state are API infrastructure around the runner. | `runtime/run_state.py`; `runtime/work_queue.py`; `api.py:1901`; `tests/test_workflow_runner.py:2353` | Runner consolidation could break queued/current/running/failed posture. | High | Keep queue/run-state wrapper intact in any small patch. |
| Sequential runner owns resume and full-run halt behavior. | `orchestrator.py:924`; `orchestrator.py:1743`; `tests/test_workflow_runner.py:1691`, `1729`, `1800` | Resume and failure semantics are behavioral contracts. | High | Required tests before changing runner internals. |
| Manual phase execution is active but not a full-run runner. | `api.py:993`; `tests/test_workflow_runner.py:2530` | It shares `run_phase_node` but not sequencing/resume logic. | High | Do not merge manual phase execution with full-run orchestration unless explicitly scoped. |
| LangGraph path is not API-active. | `orchestrator.py:2000`; `orchestrator.py:2092`; `main.py:36`; focused grep showed no API call; `tests/test_support_phases.py:219` | It still exists through CLI/demo and tests, so it is not dead code. | Medium-high | Do not remove or route API through it in a tiny consolidation. |
| Active phase sequence is template-driven. | `workflow_templates.py:133`; `orchestrator.py:894`; `orchestrator.py:1764`; `state.py:1041` | Reduces project-type drift when using sequential runner. | High | Best small cleanup is constant alignment, not runner rewrite. |
| `PHASE_ORDER` is graph/main-six routing state, not sequential-runner state. | `config.py:193`; `orchestrator.py:1970`; `orchestrator.py:1989` | Misreading it as the active full-run order would drop support phases. | High | Avoid centralizing everything onto `PHASE_ORDER`. |
| `config.PHASES` is still used outside the runner. | `config.py:192`; `scenarios/eval.py:6`, `scenarios/eval.py:26` | It may be scenario-shadow/reporting support, not runner order. | Medium | Do not remove without scenario tests. |
| Shared invalidation is template-first with config fallback. | `tools/scoring.py:134`; `workflow_templates.py:138`; `config.py:300` | Keeps project-type phase order while retaining fallback. | High | API invalidation cleanup needs focused edit tests. |
| API edit invalidation adds local self-clear/stale handling. | `api.py:1629`; `api.py:1656`; `api.py:1672` | Hidden coupling with operator edit endpoints. | Medium-high | Defer unless tests cover input and phase-output patching. |
| Policy gates run per phase inside `run_phase_node`. | `orchestrator.py:1331`; `policy.py:363` | Kill switch, budget, breakers, and HITL checks are phase-level. | High | Runner consolidation must preserve `run_phase_node` as the gate boundary. |
| Workspace/readiness report blockers but do not execute workflow. | `workspace.py:283`; `delivery_readiness.py:221`, `delivery_readiness.py:313` | They are projections, not runner surfaces. | High | Do not fold monitoring/readiness projection into runner logic. |
| Evals are a separate execution surface with separate failure semantics. | `evals/run_evals.py:412`; `evals/run_evals.py:418`; Wave 11.5A context | Eval provider failures must remain distinct from runtime failures. | High | Do not consolidate eval runners with operator workflow execution. |

## Risk table for possible consolidation

| Area | Risk | Evidence | Risk level | Mitigation |
| --- | --- | --- | --- | --- |
| Replacing sequential API runner with LangGraph | Changes resume, persistence, queue, and gate behavior | `api.py:1950`; `orchestrator.py:1743`; `orchestrator.py:2000` | High | Do not do this in Wave 11.9. |
| Removing LangGraph path | Breaks `main.py` CLI/demo path and support-phase graph tests | `main.py:36`; `tests/test_support_phases.py:219` | Medium-high | Leave graph path intact unless a later deprecation wave is approved. |
| Centralizing on `PHASE_ORDER` | Drops gauntlet/SQI support phases from full-run sequence | `config.py:193`; `workflow_templates.py:15` | High | Use template sequence as the active full-run source. |
| Removing `config.PHASES` | Could break scenario-shadow phase listing | `scenarios/eval.py:6` | Medium | Add scenario tests before touching it. |
| Deduplicating API invalidation helpers | Could change edited-phase stale/clear behavior | `api.py:1629`; `tools/scoring.py:134` | Medium | Add edit endpoint tests first. |
| Moving queue/run-state logic into orchestrator | Blurs infrastructure and workflow semantics | `runtime/run_state.py`; `runtime/work_queue.py`; `api.py:1901` | High | Keep API/runtime wrapper separate. |
| Folding eval runners into operator runner | Breaks eval scope and provider-diagnostic semantics | `evals/run_evals.py`; Wave 11.5A | High | Leave evals separate. |

## Candidate options for Wave 11.9

### Option A: no consolidation

Leave code as-is and document the active runner map only.

Pros:

- Lowest behavior risk.
- Avoids touching duplicate but working execution surfaces.

Cons:

- Phase-order duplication and runner confusion remain.
- Future contributors may still confuse `PHASE_ORDER`, `WORKFLOW_PHASE_SEQUENCE`, templates, and LangGraph routing.

### Option B: tiny docs/test-only cleanup

Add tests and documentation that lock the current active path, phase-order source, and non-active runner boundaries.

Pros:

- Safest next step.
- Improves confidence before source cleanup.

Cons:

- Does not reduce duplication.
- Leaves repeated constants in place.

### Option C: small source consolidation

Apply a narrow source cleanup that does not change execution semantics. The smallest candidate is to make `orchestrator.WORKFLOW_PHASE_SEQUENCE` an alias of `workflow_templates.STRATEGIC_AUDIT_PHASE_SEQUENCE`, preserving the public import used by tests while removing one duplicated literal list.

Possible follow-up only if tests exist: clarify comments around `config.PHASE_ORDER` as graph/main-six routing rather than active full-run order.

Pros:

- Reduces phase-order drift risk.
- Small and evidence-backed.

Cons:

- Still touches orchestrator source.
- Does not consolidate runner execution.
- Must be test-first because `WORKFLOW_PHASE_SEQUENCE` is imported in tests.

### Option D: defer larger refactor

Defer any consolidation that changes how API, queue, LangGraph, manual phase execution, or eval runners invoke workflow logic.

Pros:

- Avoids mixing infrastructure, execution semantics, and eval semantics.
- Preserves local/operator-first behavior.

Cons:

- Does not simplify the system immediately.

## Recommended decision

Proceed with Wave 11.9 only as a small, test-first phase-order and runner-boundary cleanup. Do not consolidate the actual workflow runners yet.

Recommended shape:

1. Add or adjust tests that prove the API full-run path still uses `run_workflow_sequence`, preserves durable queue/run-state behavior, and preserves template phase order.
2. Make the smallest source cleanup: alias `orchestrator.WORKFLOW_PHASE_SEQUENCE` to `workflow_templates.STRATEGIC_AUDIT_PHASE_SEQUENCE` instead of keeping a duplicated literal.
3. Optionally clarify comments/docs around `config.PHASE_ORDER` being graph/main-six routing state, not the active full-run sequence.
4. Stop before touching `run_workflow_sequence`, `run_phase_node`, queue/run-state behavior, eval runners, or API execution semantics.

If Wave 11.9 is expected to merge the API sequential runner and LangGraph runner, defer or split it. That is not a small safe patch based on the current evidence.

## Smallest safe patch, if any

The smallest safe source patch is constant alignment only:

- Change `orchestrator.WORKFLOW_PHASE_SEQUENCE` from a duplicated tuple literal to an alias of `workflow_templates.STRATEGIC_AUDIT_PHASE_SEQUENCE`.
- Keep `workflow_phase_sequence_for_state` template-driven.
- Keep `config.PHASE_ORDER`, `config.PHASES`, `INVALIDATION_MAP`, `run_workflow_sequence`, `build_workflow_graph`, `compile_workflow`, API run handling, runtime queue, and eval runners unchanged.

This should be paired with tests in Wave 11.9. It should not be done as a behavior refactor.

## Tests required before implementing

- API full-run path test proving `api._run_workflow` delegates to `run_workflow_sequence`.
- Durable queue path tests covering queued, running, succeeded, failed, duplicate active run, and stale recovery behavior.
- Phase-order tests proving `WORKFLOW_PHASE_SEQUENCE`, `STRATEGIC_AUDIT_PHASE_SEQUENCE`, and `get_workflow_phase_sequence("strategic_audit")` stay aligned.
- Template tests proving technology-readiness projects still use `TECHNOLOGY_READINESS_PHASE_SEQUENCE`.
- Manual phase endpoint test proving active full-run guard remains enforced.
- Edit invalidation tests before touching `_invalidate_from_phase` or `invalidate_downstream`.
- Existing support-phase graph tests if any `build_workflow_graph` code is touched.
- Existing eval mock tests if eval execution surfaces are mentioned or imported.

## Stop conditions for implementation

Stop before implementation if Wave 11.9 requires:

- replacing `run_workflow_sequence` with LangGraph execution;
- merging API full-run, manual phase, CLI/demo, and eval runners;
- changing workflow phase order;
- changing `run_phase_node` policy-gate behavior;
- moving queue or durable run-state semantics into orchestrator code;
- changing downstream invalidation semantics;
- changing report/export/readiness/eval semantics;
- creating autonomous monitoring or action behavior;
- touching external provider calls or real LLM execution;
- weakening the human review boundary.

## Behavior intentionally unchanged

This wave changes no runtime behavior. It does not change workflow routing, phase order, queue behavior, run-state behavior, manual phase execution, readiness projection, workspace projection, report/export behavior, eval semantics, auth, preflight, tests, scripts, or dashboards.

Wave 11.5A fixed provider-quota aggregate diagnostics. This discovery wave does not change eval semantics or provider failure classification.

## Human review boundary

MAS remains a local/operator-first decision-analysis engine. It is not a chatbot, BI product, public SaaS, multi-tenant system, public-user account system, or autonomous action system. Human review remains mandatory before client-facing use, delivery decisions, public exposure, auth/security changes, deployment, or runtime-control changes.

## Claude Design handoff

Decision question: Should Wave 11.9 exist?

Options:

1. No Wave 11.9 consolidation: leave code as-is and rely on this discovery note.
2. Test/docs-only Wave 11.9: add runner-boundary and phase-order tests, then defer source cleanup.
3. Small source Wave 11.9: test-first alias `orchestrator.WORKFLOW_PHASE_SEQUENCE` to `STRATEGIC_AUDIT_PHASE_SEQUENCE`, clarify `PHASE_ORDER`, and avoid runner execution changes.
4. Larger consolidation: merge API sequential, LangGraph, manual phase, and eval runners.

Recommended option: Option 3 only if scoped narrowly and test-first. Otherwise choose Option 2. Do not choose Option 4 for Wave 11.9.

Evidence summary:

- API full-run path: `api.py:948` -> `api.py:1950` -> `api.py:1983` -> `orchestrator.py:1743`.
- Durable queue/run-state wrapper: `runtime/run_state.py`, `runtime/work_queue.py`, `api.py:1901`.
- LangGraph path is separate: `main.py:36`, `orchestrator.py:2000`, `orchestrator.py:2092`, `tests/test_support_phases.py:219`.
- Phase templates are current runtime source: `workflow_templates.py:15`, `workflow_templates.py:133`, `orchestrator.py:894`, `orchestrator.py:1764`.
- `PHASE_ORDER` is graph/main-six routing state: `config.py:193`, `orchestrator.py:1970`.
- Eval runner is separate: `evals/run_evals.py:412`, `evals/run_evals.py:418`.

Likely files touched if implementation proceeds:

- `orchestrator.py` for constant alias only.
- `tests/test_workflow_runner.py` and/or `tests/test_technology_readiness.py` for phase-order/API runner-boundary tests.
- Optional context note update for Wave 11.9.

Files that should not be touched:

- `api.py`, unless tests reveal a clear tiny bug.
- `runtime/run_state.py` and `runtime/work_queue.py`.
- `evals/`.
- `store.py`.
- `workspace.py`.
- `delivery_readiness.py`.
- report/export/client-delivery code.
- auth/preflight code.
- scripts.
- generated files, database files, cache files, dashboard bundles.

Required tests:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_workflow_runner.py tests/test_support_phases.py tests/test_technology_readiness.py tests/test_templates_api_and_surfaces.py -q`
- Include focused new tests before source cleanup if source is touched.
- Run `scripts/wave_verify.sh`.

Implementation stop conditions:

- Any need to change `run_workflow_sequence`, `run_phase_node`, API queue/run-state flow, eval semantics, report/export/readiness behavior, or monitoring autonomy.
- Any test failure that implies workflow semantics are inconsistent enough to need a behavior redesign.

Unresolved questions:

- Is `main.py` still a supported CLI/demo execution surface or only legacy scaffolding?
- Should `config.PHASES` remain the scenario-shadow phase enumeration, or should it eventually be derived from templates with explicit tests?
- Should API edit invalidation helpers be consolidated with `tools.scoring.invalidate_downstream`, or are their self-stale semantics intentionally endpoint-specific?
