# START HERE

If you want to use the Decision Engine, you only need to know about **two things**:

1. **The console** — `dashboards/index.html`. Open it in your browser. This is where you create projects, run them, watch them work, and read reports.
2. **The API** — `cd mas && docker-compose up`. Start it once, leave it running, the console talks to it.

Everything else in this bundle is reference material. You don't have to read it to use the engine.

For a v5 runtime foundation demo workflow, see [`docs/v5-DEMO-WORKFLOW.md`](docs/v5-DEMO-WORKFLOW.md).

For local launch checks, Docker Desktop / WSL recovery, and smoke export scanning, see [`docs/local-runtime-smoke.md`](docs/local-runtime-smoke.md).

---

## The 30-second tour

```
1. cd mas && docker-compose up           ← starts the engine
2. open dashboards/index.html             ← opens the console
3. fill in the "New project" form         ← describes your decision
4. click the project row, click "Run"     ← runs the workflow
5. read the report when it finishes       ← review before delivering
```

That's it. The rest of this document explains what to do if any step is unclear.

---

## What the Decision Engine is (one paragraph)

You give it a brief — a paragraph or two describing a hard decision. It walks through 8 phases (classify the problem, generate hypotheses, stress-test them, audit the analysis, build a strategy, score it for quality, build monitoring, and write the report) and produces a structured report. It is a thinking tool, not a decision-maker. Humans review every report before anything happens with it.

---

## Setting it up the first time

### Prerequisites

- Docker and Docker Compose installed
- API keys for at least one of: Anthropic (for Claude), OpenAI (for GPT)
- A text editor

### One-time setup

```bash
cd mas
cp .env.example .env
# Edit .env, add your ANTHROPIC_API_KEY or OPENAI_API_KEY
docker-compose up
```

The first start takes a minute (downloading images, creating the database). Subsequent starts are fast.

You'll see logs from three services: `postgres`, `mas-api`, and `langfuse` (if enabled). When you see `Uvicorn running on http://0.0.0.0:8000`, the API is ready.

**Leave that terminal alone.** It's the engine. Open a new terminal or just go to your browser.

### Open the console

Open `dashboards/index.html` in any browser. The default API URL is `http://localhost:8000`, which is what `docker-compose up` exposes. If your engine is running somewhere else, change the URL field at the top right of the console.

You should see compact status pills at the top for API, persistence, tracing, preflight, and release readiness. If preflight or release readiness has blockers or warnings, the dashboard shows concise operator-facing details.

If any pill is red, the engine isn't reachable. Check the terminal where Docker is running.

---

## Daily use

### Creating a project

1. Open the console (`dashboards/index.html`).
2. Click **New**.
3. Fill in the **New project** form:
   - **Project name** — short, descriptive. "Knoxville expansion" not "Project 14".
   - **Brief** — a paragraph or two describing the decision. Be specific. Include numbers if you have them. The engine reads this directly.
   - **Risk classification** — almost always `minimal_risk` for internal analysis. Read the warnings on the form for the other options. If you're not sure, read [`compliance/eu-ai-act-classification.md`](compliance/eu-ai-act-classification.md).
   - **Rationale** — one line explaining the classification choice.
4. Click **Create project**.

The new project appears in the left project list and opens in the main workstation.

### Running a project

Open the project and click **Run**. Confirm the dialog. The engine queues the workflow and starts executing.

Watch the **phase strip** for progress. The 8 phases turn green as they complete:
- `classify` → understand the problem
- `hypotheses` → generate possibilities
- `gauntlet` → stress-test them
- `audit` → check for risks
- `strategy` → build the recommendation
- `sqi` → score the strategy quality
- `monitor` → set up observation plan
- `report` → write the final document

Total runtime is usually 2–8 minutes depending on complexity and model choice. The console auto-refreshes every 15 seconds.

### Reading the report

When `report` turns green, click the **Report** sub-tab in the drill-in panel. The report renders in formatted markdown.

**Read it carefully before sending it anywhere.** The engine can be confidently wrong. The report is a draft for your review, not a deliverable to forward without thought.

### Dossier vs Workspace

The drill-in now has four distinct surfaces:

- **Overview** — the summary-first read surface. Start here. It is designed to answer, in order: the current recommendation, why the system reached it, what to do next, what sources are in play, and only then the control/system detail.
- **Dossier** — the edit surface. Use it to update the brief, supporting data, and structured phase outputs.
- **Workspace** — the operational command center. Use it to understand what is blocked, stale, review-required, or complete, and to inspect risks, evidence, approvals, and re-entry history.
- **Decision trace** — the explainability surface. Use it to inspect per-phase purpose, inputs, frameworks, gate results, uncertainty, and strategy evidence chains without exposing raw chain-of-thought.

If you need to change project content, use **Dossier**. If you need to understand project state, use **Workspace**.
If you need to understand how the current result was reached, use **Decision trace**.
If you need the fastest high-level read, start in **Overview**.

You can now upload `PDF`, `DOCX`, `TXT`, `MD`, `CSV`, and `XLSX` files from **Overview** or the **Dossier** input section. Uploads are additive:

- documents become bounded knowledge/context items
- tables become bounded table knowledge items by default
- `CSV` and `XLSX` can optionally use structured evidence/signal import

Uploads do **not** auto-rerun the workflow. They update files, knowledge visibility, and import status immediately, but a manual rerun is still required before new uploaded information affects downstream analysis or recommendations.

Imported CSV evidence/signals appear in **Workspace** immediately, but they do **not** automatically rewrite prior analysis. If Workspace shows a notice that imported evidence is pending analysis, rerun the relevant analysis/report path before treating recommendations as updated.

Knowledge sources and synced knowledge items also appear as a separate **knowledge freshness** signal in Workspace and Queue. That layer tells you whether current-awareness inputs are configured, current, stale, expired, or sync-failed. It does **not** automatically rerun phases. Prompt-facing use is still tightly bounded: only the **audit** and **strategy** phases may consume approved structured retrieval projections.

Controlled retrieval visibility is now available through the knowledge API routes. Eligibility is backend-computed, phase-specific, and whitelist-based. In the current bounded slice, only the **audit** and **strategy** phases may consume approved structured projections, and **Decision trace** will show which approved knowledge items were used. This still does **not** auto-rerun phases or expose raw prompt dumps.

Workspace and **Decision trace** also show simple retrieval visibility for audit/strategy: whether retrieval was used, current eligible vs blocked counts, and the top blocked reasons. Those are operator visibility signals, not proof that retrieval changed the result in a measurable way.

Three status axes stay separate on purpose:

- **Decision object health** means whether the derived `decision_objects` layer is synchronized with the current stored project state.
- **Imported evidence pending analysis** means imported evidence/signals exist, but a successful rerun has not yet incorporated them into downstream analysis outputs.
- **Knowledge freshness** means whether synced external knowledge is current, stale, expired, or sync-failed. It is independent of decision-object freshness.

If they disagree, treat them literally:

- objects `fresh` + imports pending = Workspace evidence is current in state, but recommendations have not been updated yet; rerun the relevant analysis path
- objects `fresh` + knowledge `stale` = current-awareness inputs need refresh, even if the current derived objects are synchronized
- knowledge `current` + imports not pending = the external knowledge layer is operationally current, but it still does not feed prompts automatically in this version

### Watching the budget

The drill-in panel shows four budget meters: tokens, cost (USD), LLM calls, consecutive failures. Each has a bar that turns yellow at 50% and red at 80% of the cap. Default caps are 2M tokens / $25 / 100 calls / 3 failures per project.

If a project hits a cap, the engine halts the next phase and logs a `policy_gate_blocked` event. You can adjust caps via the API (`POST /projects/{id}/budget`) — see `mas/api.py`.

### Firing the kill switch

If anything looks wrong — output suspicious, costs climbing, taking too long, sanitization warnings you don't trust — click **■ Kill switch** in the drill-in panel. A modal asks for a reason (required). Enter it, click **Fire kill switch**, done.

The halt is immediate, persisted to the database, and survives orchestrator restarts. The engine cannot bypass it.

### The Control log

The **Control log** sub-tab shows every policy event for the project: classification changes, kill switch triggers, budget breaches, sanitization findings, approval grants. If anyone asks "what happened on this project and when," this is the source of truth.

### Sanitization warnings

If a brief contains content that looks like a prompt injection attempt — instructions trying to override the engine's behavior — the intake sanitizer flags it. A warning banner appears in the drill-in panel before the phase strip.

Most warnings on briefs you wrote are false positives (the sanitizer is permissive on purpose). Briefs from external sources deserve more scrutiny. Read [`docs/security/prompt-injection-defenses.md`](docs/security/prompt-injection-defenses.md) for details.

---

## The system tab

Click the **System** tab at the top to switch from the operator console to the engineering view. This shows:

- **Phase priors** — the rolling Brier and ECE per phase. Empty cards mean fewer than 5 resolved outcomes; the meta-learner is waiting for data.
- **Calibration deltas** — per-project Brier scores and over/under-confidence gaps.
- **Framework usage** — which of the 30 analytical frameworks were used in the last 90 days.

This is the v4.2 dashboard content, kept because it's useful, but no longer the default landing.

---

## Where to look when you have a question

Most operators never need anything below this line. But when you do, here's the map:

| You want to… | Read |
|---|---|
| Classify a project under EU AI Act | [`compliance/eu-ai-act-classification.md`](compliance/eu-ai-act-classification.md) |
| Review your security and compliance posture | [`compliance/governance-checklist.md`](compliance/governance-checklist.md) |
| Understand the threat model | [`docs/security/threat-model.md`](docs/security/threat-model.md) |
| Know how the prompt-injection sanitizer works | [`docs/security/prompt-injection-defenses.md`](docs/security/prompt-injection-defenses.md) |
| See where v4.4 sits on the maturity scale | [`docs/v4.3-MATURITY-SELF-ASSESSMENT.md`](docs/v4.3-MATURITY-SELF-ASSESSMENT.md) |
| Plan v5 work | [`docs/v5-ROADMAP.md`](docs/v5-ROADMAP.md) |
| Understand the framework library | [`mas/shared_knowledge/v4-framework-encyclopedia.md`](mas/shared_knowledge/v4-framework-encyclopedia.md) |
| Read the API contract | [`mas/api.py`](mas/api.py) — endpoint docstrings are accurate |
| Read the policy enforcement code | [`mas/policy.py`](mas/policy.py) |
| Read the intake sanitizer code | [`mas/security/intake_sanitizer.py`](mas/security/intake_sanitizer.py) |
| See the full repository structure | [`README.md`](README.md) |
| See the change history | [`CHANGELOG.md`](CHANGELOG.md) |

---

## Troubleshooting

**The console says "API unreachable"**
The engine isn't running, or the URL in the console is wrong. Check the terminal where you ran `docker-compose up`. Confirm port 8000 isn't blocked.

**Status pill says "store: in_memory"**
Postgres isn't reachable. The engine falls back to in-memory state, which means projects vanish when you restart it. Check that the postgres container started and that `DATABASE_URL` is set in `mas/.env`.

**A project is stuck on a phase**
1. Check the budget meters — did it hit a cap?
2. Check the audit log sub-tab — was there a `policy_gate_blocked` event?
3. Fire the kill switch and start a fresh project with a clearer brief.

**Sanitization warning on a brief I wrote**
Read the matched text. If it's a false positive (the brief uses instruction-like language for legitimate reasons), proceed with the project. If it's a real warning, fix the brief and create a new project.

**The report looks wrong**
That's your job to catch. Read it. Fix it. Don't send bad analysis to a client. The Decision Engine is a thinking tool, not an oracle.

**I want to do something the console doesn't expose**
The full API is in `mas/api.py`. Every endpoint is documented in its docstring. The console only exposes the daily-use subset; the API has more (manual single-phase runs, calibration management, approvals, etc.).

---

## What v4.4 added (and what it didn't)

### Added in v4.4

- This `START_HERE.md` document
- The canonical v5 operator dashboard (`dashboards/index.html`) with portfolio summary, project workstation, runtime readiness details, and full project workflow
- `risk_classification` and `risk_rationale` accepted at project create time (no separate API call needed)

### Not added in v4.4

- A CLI tool — the console covers it
- Authentication on the API — still localhost-only single-operator
- Multi-tenancy — single-operator only
- New phases or new analytical frameworks — same engine as v4.3
- Anything that changes the v4.3 architecture — v4.4 is purely a UX release

If you ever need to expose the engine to anyone other than yourself (clients, contractors, anyone on a network), you need to add API authentication first. That's a v4.5 conversation.

---

## The one principle to remember

**The Decision Engine is a thinking tool. Humans decide.**

Every report needs human review before delivery. Every classification needs operator judgment. Every kill switch trigger needs your call. The architecture protects you against the engine doing something stupid; it does not absolve you of responsibility for the outputs.

That's the whole thing. Now go run a project.
