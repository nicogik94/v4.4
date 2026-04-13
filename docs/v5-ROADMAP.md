# v5.0 Roadmap (ARVV-corrected)

**Status:** draft · subject to evidence · revise every 4 weeks
**Supersedes:** the v5 plan delivered in conversation on April 7, 2026
**Audit that produced this:** `archive/audits/v4.1-ARVV-v5-Plan-Audit.md`

---

## What changed from the original v5 plan

The original plan argued "don't pick v5 yet; wait 8–12 weeks." The direction survives the ARVV audit; the details do not. Eight specific revisions are folded in below.

| # | Original | Revised |
|---|---|---|
| 1 | Two themes (Productize, Close-the-loop) | **Three themes** — vertical specialization added as a first-class option |
| 2 | "5 paying clients asking" | **Measurement protocol** — tracked conversations by theme in a single Notion doc |
| 3 | Theme B = closed-loop monitor | **Theme B + kill-switch requirement** — agentic execution requires human approval gates by default |
| 4 | "Wait 8–12 weeks and see" | **Week-4 acquisition check** — if < 2 active v4.1 engagements, the v5 question is moot; pivot to acquisition |
| 5 | "Themes are mutually constraining" | **"Themes are sequenceable"** — Theme B's prior consumption fits inside Theme A's multi-tenant shell |
| 6 | Implicit operator capacity | **Explicit floor** — minimum 4 hrs/week on v4.1 operations, or the feedback-loop premise is moot |
| 7 | Specific timelines and thresholds | **Labeled as judgment calls** — numbers are re-evaluated every 4 weeks |
| 8 | "LangGraph 1.0 persistence" for long-running agentic execution | **Temporal** (or equivalent) — LangGraph 1.0 persists graph state, not arbitrary background workflows |

---

## The three themes

| | **A — Productize** | **B — Close-the-loop** | **C — Vertical specialize** |
|---|---|---|---|
| **One-line pitch** | Other consultants run the engine themselves | The engine becomes a live decision system, not a one-shot analysis | One engine per high-value vertical (SEO/GEO, M&A, hiring, product pivots) with pre-tuned prompts, frameworks, golden cases, sample dossiers |
| **What breaks from v4.2** | `store.py` schema (workspace_id), `api.py` auth, prompt loader (per-tenant overrides), eval harness (per-tenant golden sets) | `orchestrator.py` (priors consumed at run-time, already scaffolded in v4.2), monitor phase becomes long-running subgraph, state schema (`current_phase` no longer linear) | Prompt modules fork per vertical, eval suite forks per vertical, pricing forks per vertical; core orchestrator is unchanged |
| **New infra** | Auth (Clerk/Stack/WorkOS), billing (Stripe), tenant isolation review, Postgres RLS | Temporal (or equivalent), webhook ingress for live signals, explicit kill-switch architecture | None — it's a content + positioning effort, not an infra effort |
| **Mandatory gate** | — | **Kill-switch architecture MUST exist before any autonomous mode ships.** Autonomous = opt-in per circuit breaker per project. Global autonomous is forbidden. | — |
| **Effort estimate** | 12–16 weeks | 10–14 weeks | 4–8 weeks per vertical |
| **What it makes possible** | Self-serve onboarding, consultant-as-distribution-channel, cross-operator calibration network effect | Defensible moat — a competitor can copy multi-tenant SaaS in 6 weeks but cannot copy 18 months of calibrated priors | Solves the positioning problem the original audit caught ("what is this FOR?"). Each vertical has a named buyer, named pain, named price. |
| **What it fails at** | Demand is unproven — the deck's productization claim preceded any paying customer asking for self-serve | Requires clients first. Can't close loops on deals that don't exist. | Dilutes operator focus across verticals; each new vertical is a new content investment |
| **When it's right** | Friendly consultants are asking to run it themselves | A paying client asks "can this monitor for me continuously?" with a budget attached | Positioning is the binding constraint on close rate |

**None of the three is obviously correct right now.** The whole point of the roadmap is that the correct answer depends on evidence that doesn't exist yet.

---

## Week-4 acquisition bottleneck check (HARD GATE)

Before any theme work begins, **run this check at the end of week 4**:

```
active_engagements = count(projects in v4.1 with > 1 phase completed in last 30 days)
```

- **active_engagements < 2** → the v5 question is moot. Pause this roadmap. The bottleneck is not architecture; it is acquisition. Re-read `pitch/launch/v4-90Day-MasterPlan-CORRECTED.docx` and execute it. Return to this roadmap when active_engagements ≥ 2.
- **active_engagements ≥ 2** → proceed to the measurement phase below.

This gate exists because the original plan assumed v4.1 would automatically generate evidence. It won't — it needs clients feeding it for that to happen.

---

## Measurement protocol (replacing "5 paying clients asking")

Track every client conversation in a single Notion doc with this schema:

| Date | Client | Conversation type | Theme signal (A / B / C / none) | Quoted ask | Would pay? | Budget hint |
|---|---|---|---|---|---|---|

**Theme signal rules:**
- **A** → anything like "could my team use this themselves," "can I run this without you," "white-label," "train my associates"
- **B** → anything like "can this track over time," "can it alert me," "continuous monitoring," "early warning"
- **C** → anything like "do you specialize in X," "is this for M&A / hiring / product," "we need someone who knows our industry"
- **none** → general interest without a specific ask

**Decision criterion (re-evaluated every 4 weeks):**
A theme wins if **3 or more independent conversations in an 8-week window** carry the same theme signal AND at least one has a specific budget attached. Below 3 signals, the theme is a hypothesis, not a direction.

This is still a judgment call. The protocol makes it auditable; it doesn't make it algorithmic.

---

## Operator capacity floor

**Minimum:** 4 hours/week on v4.1 operations (project intake, phase execution, outcome recording, dashboard review).

Below 4 hrs/week:
- `update_priors.py` has insufficient signal to calibrate (fewer than ~5 resolved outcomes per phase per quarter)
- The dashboard stays empty
- The feedback-loop premise collapses
- v4.1 is a delivery tool, not a learning tool
- v5 decisions should be deferred indefinitely

This is an honest constraint, not a pep talk. If competing commitments (chemistry research, AI strategy applications, other client work) make 4 hrs/week unrealistic, the correct move is to shrink v5 ambition, not grind.

---

## Technical debt v5 must clear (regardless of theme)

These are deferred in v4.2 and accumulate until they block something. Every v5 theme benefits from clearing them first.

1. **Consolidate `orchestrator.py` prompt builders with `prompts/loader.py`.** Currently `orchestrator.py` has inline `build_*_prompt` functions and `prompts/loader.py` is unused. Pick one. The loader is the future because it's prompt-cache-friendly. Migration requires running the eval harness to confirm no behavior drift.
2. **Replace in-process `running: set[str]` with a Redis (or Postgres advisory lock).** Required the moment you scale to two worker processes.
3. **Alembic migration tooling.** SQL changes are currently applied with `psql -f`. First v5 theme that adds a column will need this.
4. **Rewrite `archive/legacy-docs/v4-Multi-Agent-System-Prompt.md` (930 lines) as a derivation of `router.md` + `phases/*.md`.** Or delete it. Right now it's archived documentation that shadows the real prompts and will drift out of sync.
5. **Decide the fate of `archive/gpt-builder-integration/`.** These are for operators who run the engine via Claude Projects or Custom GPTs rather than the Python stack. Keep as a separate integration target, or retire as duplicative.

Total effort: 1–2 weeks of focused work, no theme choice required.

---

## What v4.2 already ships that v5 depends on

- **Prior consumption wired.** `mas/priors.py` reads `prior_snapshots` at phase start and injects a calibration hint into the system prompt. Empty by default → identical to v4.1. First time there is data → behavior shifts. This is what makes the Bayesian story non-decorative.
- **Operator dashboard.** `dashboards/index.html` renders projects, priors, calibration deltas, and framework usage from the API. Refreshes every 30s. No separate server required.
- **Dashboard API endpoints.** `GET /calibration/priors`, `GET /calibration/framework-performance`, `GET /calibration/deltas`. All fail-soft when the database is empty.
- **`prompts/templates.py` deleted.** Three prompt code paths → two. The final merge is a v5 decision because it changes behavior.

v5 inherits a working feedback loop. The question is what to put around it.

---

## Update — v4.3 has shipped (April 2026)

The v4.3 release integrated the v2.1 enterprise AI agent strategy into the Decision Engine. This rewrites several assumptions in the v5 plan above. Read this section before treating any of the v5 themes as unchanged.

### What v4.3 moved out of v5 scope

- **Deterministic enforcement layer.** `mas/policy.py` ships in v4.3 with reversibility classification, per-project budget caps, kill switch, and a three-state phase circuit breaker. The v5 themes no longer need to invent this — they inherit it. Theme B (close-the-loop monitoring) in particular gets this for free; the kill-switch architecture requirement that the ARVV audit flagged is now implemented.
- **Prompt injection defenses.** `mas/security/intake_sanitizer.py` ships in v4.3 with five categories of patterns plus structural checks. v5 work that adds external data sources (Theme A productize, Theme B monitor) inherits the sanitizer; it does not need to be rebuilt.
- **EU AI Act risk classification.** `compliance/eu-ai-act-classification.md` ships in v4.3 with the operator decision tree, the three-position framing (not high-risk by default; per-project classification mandatory; Annex III requires separate compliance track), and the seven-step compliance track for high-risk projects. v5 themes that target Annex III verticals (employment screening, credit decisions, insurance) inherit the classification framework.
- **Multi-provider routing scaffold.** `mas/llm_client.py` has a `route_model()` function and the existing primary+fallback chain. The full LiteLLM/OpenRouter integration is still v5 work, but the v5 effort is now "deploy a managed gateway against an existing scaffold," not "build the scaffold and the gateway from scratch."
- **Governance and security documentation.** `compliance/governance-checklist.md`, `docs/security/threat-model.md`, `docs/security/prompt-injection-defenses.md`, and `docs/v4.3-MATURITY-SELF-ASSESSMENT.md` all ship in v4.3. The v5 themes inherit a documented compliance posture.

### What this means for each v5 theme

**Theme A — Productize.** v4.3 makes this more feasible by removing the security and compliance burden that productization would have created. The remaining v5 work is multi-tenant authentication, billing, per-tenant prompts and golden cases, and Postgres row-level security. These are still substantial, but they are no longer blocked on "we don't have the security envelope" — v4.3 ships the envelope.

**Theme B — Close-the-loop monitor.** v4.3 ships the kill-switch architecture the ARVV audit flagged as a non-negotiable for autonomous monitoring. Theme B can now proceed against a real safety baseline. The remaining v5 work is the Temporal (or equivalent) workflow runtime, the long-running monitor phase as a subgraph, and webhook ingress for live signals. Theme B's autonomous mode remains opt-in per circuit breaker per project, never default.

**Theme C — Vertical specialize.** v4.3's EU AI Act classification framework is the prerequisite for entering any Annex III vertical (employment, credit, insurance). Without it, vertical specialization in those categories was a compliance liability. With it, the vertical can be pursued on a defensible compliance track. The remaining Theme C work is per-vertical prompts, golden cases, and pricing — no architectural blockers.

### What the week-4 acquisition gate looks like with v4.3

Unchanged. The acquisition bottleneck is still the binding constraint for any v5 theme. v4.3 makes the engine more defensible to pitch but does not generate clients. The honest meta-finding from the ARVV audit still applies: the next move with the most leverage is not revising this roadmap. It is booking three intake calls with people who have strategic decisions and budget.

### Technical debt status

The v5 technical debt list is mostly unchanged, but two items are partially addressed:

- **Consolidate `orchestrator.py` prompt builders with `prompts/loader.py`** — still pending. v4.3 did not touch this.
- **Replace in-process `running: set[str]` with Redis or Postgres advisory lock** — still pending. v4.3 added kill-switch state to Postgres so operator-triggered halts persist, but the runtime concurrency gate is still in-process.
- **Alembic migration tooling** — still pending. v4.3 added new fields to ProjectState; these are JSONB and don't require schema migration, but the next column-level addition will trip on this.
- **Rewrite `archive/legacy-docs/v4-Multi-Agent-System-Prompt.md` (930 lines)** — still pending.
- **Decide the fate of `archive/gpt-builder-integration/`** — still pending.

### Re-evaluation cadence

Per the original v5 plan, this roadmap is re-evaluated every 4 weeks. v4.3 is the first major change since the original; the next re-evaluation is due 4 weeks from the v4.3 release date. The week-4 acquisition gate runs at the same cadence.

---

## What this roadmap cannot decide

Whether any of this matters more than three customer conversations this week. The ARVV audit of the previous plan surfaced a meta-finding that applies here too: **the operator probably needs fewer plans and more conversations**. A roadmap is useful precisely to the degree that it does not become a substitute for talking to paying clients.

**The next move with the most leverage is not revising this document.** It is booking three intake calls with people who have strategic decisions and budget.
