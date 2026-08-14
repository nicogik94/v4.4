# Universal Project Workflow v4.4

A multi-agent decision engine that applies 30 analytical frameworks across 6 phases with mathematical convergence gates, Bayesian priors, a meta-learner that closes the loop on calibration, deterministic policy enforcement (v4.3), and a single-screen operator console (v4.4).

**Status:** v4.4 — v5-track hardening and productization work underway inside this repository
**Author:** Nicolás Grinberg
**Date:** April 2026

> **Wave 10 (in progress):** CI pytest gate and provenance reporting have landed. A pytest workflow runs on every PR and push to main. Version and git SHA are stamped in `/health`, `/runtime/preflight`, and machine-readable export manifests. This is hardening work; it does not change runtime behavior or claim public SaaS or v5 production readiness.

---

## ⚠️ Read this first

If you want to use the engine, **read [`START_HERE.md`](START_HERE.md)**. It is the only document you need to start operating. Everything else in this bundle is reference material.

For tranche-1 baseline review and acceptance, start with:

- [`docs/v4.4-TRANCHE-1-BASELINE.md`](docs/v4.4-TRANCHE-1-BASELINE.md)
- [`docs/v4.4-TRANCHE-1-QA-CHECKLIST.md`](docs/v4.4-TRANCHE-1-QA-CHECKLIST.md)
- [`docs/v4.4-TRANCHE-2-PLAN.md`](docs/v4.4-TRANCHE-2-PLAN.md)
- [`docs/v4.4-TRANCHE-2-CSV-RUNTIME.md`](docs/v4.4-TRANCHE-2-CSV-RUNTIME.md)
- [`docs/v4.4-SCENARIO-SHADOW.md`](docs/v4.4-SCENARIO-SHADOW.md)
- [`docs/v4.4-FILE-UPLOADS-OVERVIEW.md`](docs/v4.4-FILE-UPLOADS-OVERVIEW.md)

---

## What changed in v4.4 vs v4.3

See [CHANGELOG.md](CHANGELOG.md) for the full diff. The headline items:

1. **Canonical operator dashboard** — `dashboards/index.html` is the default v5 dashboard. It handles the daily workflow (create, classify, run, watch, kill, review, export), shows runtime readiness details, and keeps calibration/portfolio views available for operators.
2. **Combined create + classify** — `POST /projects` now accepts `risk_classification` and `risk_rationale` in the same payload. Backward-compatible with v4.3 callers.
3. **`START_HERE.md`** — one-page operator entry point at the bundle root. The 80 other files become reference material instead of homework.
4. **File cleanup proposal** — `docs/v4.4-FILE-CLEANUP-PROPOSAL.md` lists 25 files that could move to `archive/` and `pitch/` subdirectories without breaking anything functional. Not executed until you sign off.

## What changed in v4.3 vs v4.2

See [CHANGELOG.md](CHANGELOG.md) for the full diff. The headline items:

1. **Deterministic enforcement layer** — `mas/policy.py` adds reversibility classification for every action, per-project budget caps (tokens, cost, wall-clock, LLM calls, reentries), kill switch with audit logging, and a three-state phase circuit breaker. The orchestrator runs the policy gate **before every LLM call**, in code, where the LLM cannot bypass it.
2. **Prompt injection defenses at brief intake** — `mas/security/intake_sanitizer.py` scans every project brief at first phase entry. Five categories of patterns plus structural checks. Defense in depth, fail-soft by default.
3. **EU AI Act risk classification** — every project is classified at intake (default `minimal_risk`, must be affirmed). Annex III use cases trigger the separate compliance track in [`compliance/eu-ai-act-classification.md`](compliance/eu-ai-act-classification.md).
4. **Six new policy API endpoints** — `POST /projects/{id}/kill`, `POST /projects/{id}/risk-classification`, `GET /projects/{id}/budget`, `POST /projects/{id}/budget`, `POST /projects/{id}/approvals`, `GET /projects/{id}/policy-audit`.
5. **Compliance and security documentation** — [`compliance/eu-ai-act-classification.md`](compliance/eu-ai-act-classification.md), [`compliance/governance-checklist.md`](compliance/governance-checklist.md), [`docs/security/threat-model.md`](docs/security/threat-model.md), [`docs/security/prompt-injection-defenses.md`](docs/security/prompt-injection-defenses.md), [`docs/v4.3-MATURITY-SELF-ASSESSMENT.md`](docs/v4.3-MATURITY-SELF-ASSESSMENT.md).
6. **Multi-provider routing scaffold** — `route_model()` in `mas/llm_client.py` adds complexity-aware routing on top of the existing phase-based primary+fallback chain.
7. **v5 roadmap updated** — `docs/v5-ROADMAP.md` now reflects what v4.3 moved out of v5 scope.

## What changed in v4.2 vs v4.1

See [CHANGELOG.md](CHANGELOG.md) for the full diff. The headline items:

1. **Operator dashboard** — single-file HTML that reads projects, priors, calibration deltas, and framework usage from the API (`dashboards/index.html`)
2. **Prior consumption wired** — `orchestrator.py` now reads the latest `prior_snapshots` at phase start and injects a calibration hint into the system prompt (`mas/priors.py`). Empty snapshots → identical to v4.1 behavior. Real snapshots → behavior shifts based on realized outcomes. This is what makes the Bayesian story non-decorative.
3. **Three new dashboard API endpoints** — `GET /calibration/priors`, `GET /calibration/framework-performance`, `GET /calibration/deltas`
4. **`prompts/templates.py` deleted** — was orphaned since v4.1; three prompt code paths → two
5. **v5 roadmap, ARVV-corrected** — three themes (Productize, Close-the-loop, Vertical specialize), hard week-4 acquisition gate, explicit measurement protocol, kill-switch requirement for autonomous execution (`docs/v5-ROADMAP.md`)

## What changed in v4.1 vs v4.0

See [CHANGELOG.md](CHANGELOG.md) for the full diff. The headline items:

1. **PostgreSQL persistence** — projects survive restarts (`mas/store.py`)
2. **Langfuse tracing** — per-phase cost, latency, and token accounting (`mas/observability.py`)
3. **Router prompt** — the 930-line monolith split into a 200-line router + 8 lazy-loaded phase modules (`mas/prompts/router.md`, `mas/prompts/phases/`)
4. **Eval harness** — 12 golden decision cases + LLM-judge scoring + GitHub Actions CI gate (`mas/evals/`)
5. **Feedback loop** — client outcomes schema + nightly `update_priors` job that updates phase priors from realized Brier/ECE (`mas/sql/outcomes.sql`, `mas/jobs/update_priors.py`)
6. **Lead magnets** — sample decision audit + interactive ROI calculator for the pitch page (`lead-magnets/`)
7. **API pricing + model strings** — Opus 4.6 at $5/$25, Sonnet 4.6 primary, GPT-5 family as fallback, `langgraph>=1.0.0` pinned

---

## Repository map

```
v4.4/
├── START_HERE.md                      # ⟵ READ THIS FIRST
├── README.md                          # you are here
├── CHANGELOG.md                       # v4.0 → v4.4 diff
│
├── dashboards/
│   ├── index.html                     # ⟵ canonical/default operator dashboard
│   └── index-v5.html                  # compatibility entry point to index.html
│
├── compliance/
│   ├── eu-ai-act-classification.md    # per-project risk classification guide (v4.3)
│   └── governance-checklist.md        # security + compliance checklist (v4.3)
│
├── mas/                               # the running engine
│   ├── api.py                         # FastAPI REST layer (v4.4)
│   ├── orchestrator.py                # LangGraph state machine + policy gate (v4.3)
│   ├── llm_client.py                  # model-agnostic LLM wrapper + routing scaffold (v4.3)
│   ├── state.py                       # Pydantic state schema + policy fields (v4.3)
│   ├── config.py                      # model routing + gates + frameworks
│   ├── store.py                       # persistent project store (v4.1)
│   ├── observability.py               # Langfuse wrapper (v4.1)
│   ├── priors.py                      # prior_snapshots → prompt hint (v4.2)
│   ├── policy.py                      # deterministic enforcement layer (v4.3)
│   ├── security/                      # prompt injection defenses (v4.3)
│   │   ├── __init__.py
│   │   └── intake_sanitizer.py
│   ├── requirements.txt                # direct dependency intent
│   ├── requirements.lock.txt           # exact Python 3.12 Linux closure
│   ├── Dockerfile + docker-compose.yml + .env.example
│   ├── sql/
│   │   ├── init.sql
│   │   └── outcomes.sql
│   ├── prompts/
│   │   ├── router.md
│   │   ├── phases/ (00-classify through 05-report)
│   │   └── loader.py
│   ├── evals/
│   │   ├── golden_cases.jsonl
│   │   ├── run_evals.py + run_evals_batch.py
│   │   └── README.md
│   ├── jobs/
│   │   └── update_priors.py
│   ├── shared_knowledge/
│   │   └── v4-framework-encyclopedia.md
│   ├── tools/
│   │   ├── calibration.py
│   │   └── scoring.py
│   └── tests/
│
├── docs/                              # reference docs (read when needed)
│   ├── v4-Decision-Playbook.md
│   ├── v4-Implementation-Guide.md
│   ├── v4-Project-Blueprint-Template.md
│   ├── v4.3-MATURITY-SELF-ASSESSMENT.md
│   ├── v4.4-FILE-CLEANUP-PROPOSAL.md
│   ├── v5-ROADMAP.md
│   └── security/
│       ├── threat-model.md
│       └── prompt-injection-defenses.md
│
├── excel/                             # offline analysis
│   ├── v4-MAS-Command-Center.xlsx
│   ├── v4-Command-Center.xlsx
│   └── v4-Meta-Learner-Database.xlsx
│
├── .github/workflows/
│   ├── evals.yml
│   └── evals-nightly-batch.yml
│
├── pitch/                             # sales and marketing material
│   ├── deck/v4-AI-Decision-Engine-Opportunity.pptx
│   ├── launch/v4-90Day-MasterPlan-CORRECTED.docx
│   ├── launch/v4-90Day-MasterPlan-Interactive.html
│   ├── frontend/v4-pitch.jsx
│   └── lead-magnets/
│       ├── roi-calculator.html
│       └── sample-decision-audit.md
│
└── archive/                           # historical, not in daily flow
    ├── audits/                        # v4.0 → v4.1 audit trail
    ├── legacy-docs/                   # superseded documentation
    ├── gpt-builder-integration/       # alternate GPT Builder target
    └── superseded-frontends/          # old React workflow UI
```

---

## Getting started in 5 minutes

### 1. Clone and install

```bash
cd mas
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip==25.3
python scripts/validate_requirements_lock.py
python -m pip install -r requirements.lock.txt
```

Edit `requirements.txt` to change direct dependency intent, then regenerate
`requirements.lock.txt` in a clean Python 3.12 Linux environment. The validator
rejects a stale lock before installation.

### 2. Set credentials

```bash
cp .env.example .env
# edit .env and set at minimum:
#   ANTHROPIC_API_KEY
#   DATABASE_URL (postgres://user:pass@localhost:5432/workflow_v4)
# optional:
#   LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY  (enables tracing)
```

### 3. Bring up Postgres and run migrations

```bash
docker-compose up -d postgres
psql $DATABASE_URL -f sql/init.sql
psql $DATABASE_URL -f sql/outcomes.sql
psql $DATABASE_URL -f sql/v64_decision_state_coherence_foundation.sql
```

### 4. Run the API

```bash
uvicorn api:app --reload
```

### 5. Run a project end-to-end

```bash
# Create a project
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","brief":"Should we launch product X in Q3?"}'
# Returns {"project_id": "..."}

# Run the full workflow
curl -X POST http://localhost:8000/projects/<project_id>/run

# Check status
curl http://localhost:8000/projects/<project_id>

# Fetch the final report
curl http://localhost:8000/projects/<project_id>/report
```

### 6. Open the dashboard

```bash
# Just open the file in any browser — no server needed
open dashboards/index.html
```

The `API base URL` field at the top defaults to `http://localhost:8000` for local file use. Change it when Docker publishes the API on a different host port. The dashboard refreshes automatically and shows project workflow state, runtime health/preflight/release readiness, portfolio signals, calibration, report, and export controls. Empty cards mean "no data yet," never "broken."

---

## The 6 phases at a glance

| Phase | Agent | Primary model | What it produces | Gate |
|---|---|---|---|---|
| 0 · Classify | ClassifyAgent | Haiku 4.5 | Cynefin domain, Bayes Factor, DQ frame | BF > 10, DQ ≥ 60 |
| 1 · Hypotheses | HypothesesAgent (+ Gauntlet) | Opus 4.6 | 8–12 testable hypotheses with priors | H_norm < 0.15 |
| 2 · Audit | AuditAgent | Sonnet 4.6 | FMEA, HAZOP, FTA, Swiss Cheese | Top findings ≥ 3 |
| 3 · Strategy | StrategyAgent (+ SQI) | Opus 4.6 | Ranked strategies + preliminary verdicts | SQI ≥ 70 |
| 4 · Monitor | MonitorAgent | Sonnet 4.6 | OODA loop, circuit breakers, canaries | Human review |
| 5 · Report | ReportAgent | Sonnet 4.6 | Final dossier + lessons + calibration | Brier logged |

Every gate is a hard stop. Re-entry triggers (R1–R8) route the state back to a prior phase if downstream evidence invalidates upstream assumptions.

---

## Which file do I use for what?

| If you want to... | Open... |
|---|---|
| Sell the system to a client | `deck/v4-AI-Decision-Engine-Opportunity.pptx` + `lead-magnets/roi-calculator.html` |
| Run a project manually in the chat UI | `frontend/v4-workflow-app.jsx` |
| Show a prospect what the output looks like | `lead-magnets/sample-decision-audit.md` |
| Design a new client engagement | `docs/v4-Project-Blueprint-Template.md` |
| Track project quality across engagements | `excel/v4-Meta-Learner-Database.xlsx` |
| Understand why a phase failed a gate | `docs/v4-Decision-Playbook.md` |
| Extend the system with a new framework | `mas/shared_knowledge/v4-framework-encyclopedia.md` + `mas/config.py` `FRAMEWORKS_BY_PHASE` |
| Launch RegexSEO as a practice | `launch/v4-90Day-MasterPlan-CORRECTED.docx` |
| See live project state, priors, calibration | `dashboards/index.html` (point at running API) |
| Plan what v5 should be | `docs/v5-ROADMAP.md` |
| Debug prompt regressions | `mas/evals/run_evals.py` |
| **Use the engine (any task)** | **`START_HERE.md` → open `dashboards/index.html` in your browser** |
| Classify a project under EU AI Act | `compliance/eu-ai-act-classification.md` |
| Review security + compliance posture | `compliance/governance-checklist.md` |
| Understand the threat model | `docs/security/threat-model.md` |
| Operate the intake sanitizer | `docs/security/prompt-injection-defenses.md` |
| Self-assess maturity | `docs/v4.3-MATURITY-SELF-ASSESSMENT.md` |
| Kill a runaway project | `POST /projects/{id}/kill` (see `mas/api.py`) |
| Set a project's risk tier | `POST /projects/{id}/risk-classification` |
| Check budget consumption | `GET /projects/{id}/budget` |
| Review policy audit log | `GET /projects/{id}/policy-audit` |

---

## Non-goals

- This is **not** a general agentic framework. Use CrewAI or LangGraph directly for that. v4 is a prescriptive workflow with opinionated gates.
- This is **not** a replacement for domain expertise. The frameworks catch blind spots; they do not substitute judgment.
- This is **not** a black box. Every claim in the final report is traceable to a named framework, a hypothesis, and an evidence chain.

---

## License

Proprietary. © 2026 Nicolás Grinberg. All rights reserved.
"# v4.4" 
