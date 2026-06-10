# CHANGELOG

## v4.4.0 — Wave 10.1 hardening (2026-06-10)

v5-track hardening and productization work has landed inside the v4.4 repository. This entry records additive trust-hardening changes only; no runtime behavior, no prompt changes, no eval threshold changes.

### Added (Wave 10.1)

- **pytest CI gate** — `.github/workflows/tests.yml` runs `python -m pytest tests -q` on every pull request and push to `main`. Uses ubuntu-latest, Python 3.12, and a postgres:16 service container. 682 tests pass on the existing suite.
- **Provenance foundation** — `mas/version.py` is now the single canonical source for `APP_VERSION` and exposes `get_git_sha()`. Resolution order: `V4_GIT_SHA` env → `GIT_SHA` env → `GITHUB_SHA` env (set automatically in GitHub Actions) → `git rev-parse --short HEAD` → `"unknown"`.
- **Version + SHA surfaced** — `git_sha` is now returned in `/health`, `/runtime/preflight`, and the machine-readable `export_manifest.json` inside archive exports.

### Not changed (Wave 10.1)

- No prompt or eval changes. No golden-case changes. No eval thresholds. No runtime recovery or drain behavior. No auth, CORS, or exposure interlocks. No schema migrations. No client-facing report prose. Wave 10.2 will address runtime recovery and exposure interlocks.

---

## v4.4.0 — 2026-04-09

The UX release. v4.3 added a lot of capability (deterministic enforcement, prompt injection defenses, EU AI Act compliance, six new API endpoints) but the surface for using all of that was still curl commands and reading files. v4.4 collapses the daily-use surface to **one screen with buttons** and **one document that explains the workflow**.

This release adds no new engine capability. It changes the operator's experience.

### Added

- **`START_HERE.md`** — one-page operator entry point at the bundle root. Answers "I want to use this, what do I do?" Covers prerequisites, one-time setup, daily workflow, troubleshooting, and where to find everything else. New operators read this and nothing else; experienced operators reference the API docs and compliance guides directly.
- **New operator console at `dashboards/index.html`** — single-file HTML replacement for the v4.2 dashboard. Two tabs:
  - **Console tab (default landing)**: full daily workflow. New project form with name, brief, risk classification dropdown, and rationale. Projects table with click-to-drill-in. Drill-in panel shows phase strip (8-phase visual progress), four budget meters (tokens, cost, calls, failures, with fill bars and warn/danger states), sanitization findings banner when present, three sub-tabs (Report, Audit log, Raw state). Run button, kill switch button (with confirmation modal requiring a reason), close button. Auto-refresh every 15 seconds. Markdown rendering for the report. Color-coded risk badges (minimal/limited/high/prohibited). First-run help panel that dismisses to localStorage.
  - **System tab**: the v4.2 dashboard content (priors grid, calibration deltas, framework usage). Same UI, just no longer the default landing.
- **`docs/v4.4-FILE-CLEANUP-PROPOSAL.md`** — proposal to move 25 files (historical audits, legacy docs, pitch material, alternate integration targets, superseded UIs) to `archive/` and `pitch/` subdirectories. Not executed; awaits operator sign-off. Includes the proposed final tree structure and the verification plan before moving anything.

### Changed

- **`mas/api.py`** — version bumped from 4.3.0 → 4.4.0.
  - `POST /projects` accepts three new optional fields: `risk_classification`, `risk_rationale`, `risk_set_by`. When present, the project is created with the classification already set, eliminating the need for a separate `POST /projects/{id}/risk-classification` call. Backward-compatible: omitting these fields produces v4.3 behavior (default `minimal_risk`, must set later).
  - The console uses this combined payload by default; the standalone `POST /projects/{id}/risk-classification` endpoint still works for re-classification after creation.
- **`README.md`** — header bumped to v4.4 with the UX release framing and a prominent "Read this first" pointer to `START_HERE.md`. Repo map updated with `START_HERE.md` at the top and `docs/v4.4-FILE-CLEANUP-PROPOSAL.md` in the docs section. File-map table now points the "Use the engine (any task)" entry to the START_HERE → console flow.
- **`dashboards/index.html`** — replaced entirely. The v4.2 file is overwritten because v4.4's console covers a strict superset of the v4.2 dashboard's capabilities (Console tab is new; System tab is the v4.2 dashboard content).

### Architectural properties unchanged from v4.3

- The deterministic enforcement layer (`mas/policy.py`) is unchanged.
- The intake sanitizer (`mas/security/intake_sanitizer.py`) is unchanged.
- The orchestrator (`mas/orchestrator.py`) is unchanged.
- The state schema (`mas/state.py`) is unchanged.
- The eval harness, prior consumption, observability, and persistence are all unchanged.
- The risk classification API endpoint (`POST /projects/{id}/risk-classification`) is unchanged and still works for re-classification after creation.

### Upgrade notes from v4.3 → v4.4

1. **No SQL migration required.** No schema changes.
2. **No environment variable changes.**
3. **Default behavior is unchanged for existing projects.** A v4.3 project loaded under v4.4 displays exactly the same way in the new console — the console reads the same state structure.
4. **Existing v4.3 callers continue to work.** `POST /projects` without `risk_classification` produces v4.3 default behavior. Existing scripts and integrations need no changes.
5. **The file cleanup proposal is opt-in.** Reading `docs/v4.4-FILE-CLEANUP-PROPOSAL.md` does not execute it. Move files only when you've reviewed the plan.
6. **The console is single-operator and localhost-only.** It assumes no API authentication. If you ever expose the API to anyone other than yourself, you need to add auth before doing so. That is a v4.5 conversation.

### Open follow-ups (not blocking v4.4 ship)

- **Execute the file cleanup proposal** when you're ready. One-shot operation, reversible.
- **API authentication for multi-user deployments** — v4.5.
- **Per-project `fail_hard` flag for the sanitizer in the console UI** — currently the sanitizer is fail-soft only via the API. v4.5 cosmetic addition.
- **Dashboard panel for the policy audit log across all projects** — the per-project audit log is in the drill-in; a system-wide view would be useful for compliance reviews. v4.5.
- **Spanish-language sanitizer patterns** — known gap from v4.3, still pending.
- **Console mobile responsiveness** — works but not optimized for narrow screens.

### What v4.4 does NOT add (and why)

- **A CLI tool** — the console covers the daily workflow. A CLI would be redundant for the operator and add a maintenance surface.
- **API authentication** — the engine is single-operator localhost-only. Adding auth without a clear multi-user requirement adds complexity that buys nothing for the current operator. Defer to v4.5.
- **New phases or new analytical frameworks** — v4.4 is purely a UX release. The engine is unchanged.
- **A rebuild of the v4.3 architecture** — the v4.3 architecture is fine. The problem v4.4 solved was the surface, not the substance.

---

## v4.3.0 — 2026-04-09

Integration of the v2.1 Enterprise AI Agent Upgrade Strategy bundle into the Decision Engine. This release adds the deterministic enforcement layer, prompt injection defenses, EU AI Act risk classification, and compliance documentation that the v2.1 audit established as mandatory for production-grade agent deployments. It is fully backward-compatible with v4.2: default behavior is unchanged for existing projects (they run at `risk_classification=minimal_risk` with conservative budget caps), and the new enforcement primitives are opt-in per project via API.

### Added

- **`mas/policy.py`** — new module. The deterministic enforcement layer sits outside the LLM's control: reversibility classification for every action (read_only / reversible_internal / irreversible_internal / irreversible_external), per-project budget caps (tokens, cost, wall-clock, LLM calls, reentries, consecutive failures), kill switch primitives with audit logging, three-state phase circuit breaker (CLOSED / DEGRADED / OPEN), and the unified `policy_gate()` entry point the orchestrator calls before every LLM call. Includes dict ↔ dataclass adapters for Pydantic-compatible state storage. ~440 lines.
- **`mas/security/intake_sanitizer.py`** — new module. Scans every project brief at first phase entry for prompt injection content. Five pattern categories (instruction override, role manipulation, output hijacking, tool hijacking, exfiltration) plus structural checks (max length, max line length, repeated character runs, Unicode normalization, control characters). Each finding is severity-tagged (`info` / `low` / `medium` / `high` / `critical`) with a recommendation (`allow` / `review` / `block`). Fail-soft by default; `fail_hard=True` mode available for high-risk deployments. ~330 lines. Smoke-tested: benign brief → 0 findings; obvious injection → 3 findings with CRITICAL severity; 100K-character DOS → truncated and flagged.
- **`mas/security/__init__.py`** — module entry point exporting `sanitize_brief`, `SanitizationResult`, `SanitizationFinding`, `Severity`.
- **`compliance/eu-ai-act-classification.md`** — Decision Engine-specific EU AI Act guidance with the operator decision tree, four risk tiers (minimal_risk, limited_risk, high_risk, prohibited), the seven-step separate compliance track for high-risk projects, and three explicit positions: (1) not high-risk by default, (2) per-project classification is mandatory, (3) Annex III use cases require a separate compliance track. Reflects the phased EU AI Act timeline (Feb 2 2025 prohibited practices, Aug 2 2025 GPAI obligations, Aug 2 2026 Commission enforcement powers for GPAI). ~280 lines.
- **`compliance/governance-checklist.md`** — adapted from the v2.1 enterprise bundle to the Decision Engine's solo-operator posture. Ten sections (identity, kill switch, prompt injection, blast radius, EU AI Act, observability, ISO/IEC 42001, data residency, incident response, deferred to v5) with severity-tagged items. ~200 lines.
- **`docs/security/threat-model.md`** — STRIDE analysis plus OWASP LLM Top 10 mapping. Asset inventory, trust boundaries diagram, per-threat mitigations tagged with v4.3 implementation status, and the cross-cutting lesson from documented production failures (Air Canada, Replit Agent, Amazon Kiro, recursive loops): deterministic enforcement, never trust the model to follow instructions. ~280 lines.
- **`docs/security/prompt-injection-defenses.md`** — operator guide for the intake sanitizer. How it works, what it catches, what it misses, how to read findings, when to switch to `fail_hard` mode, how to extend the pattern library, quarterly review cadence. ~170 lines.
- **`docs/v4.3-MATURITY-SELF-ASSESSMENT.md`** — honest scoring against the v2.1 10-dimension rubric. **Total: 23/30 (Production bucket)**. Three Level-3 dimensions (architecture, observability, blast radius, evaluation), two Level-1 dimensions (protocols, memory) explicitly deferred to v5, five Level-2 dimensions with a clear path to Level 3 within one quarter of operator work. ~100 lines.
- **Six new policy API endpoints** in `mas/api.py`:
  - `POST /projects/{id}/kill` — trigger kill switch, idempotent, persists to Postgres
  - `POST /projects/{id}/risk-classification` — set EU AI Act tier with rationale
  - `GET /projects/{id}/budget` — current consumption vs caps with headroom
  - `POST /projects/{id}/budget` — update caps (cannot set below current consumption)
  - `POST /projects/{id}/approvals` — grant HITL approval for a specific action
  - `GET /projects/{id}/policy-audit` — full audit log for compliance review

### Changed

- **`mas/state.py`** — `ProjectState` extended with ten new fields: `risk_classification`, `risk_classification_rationale`, `risk_classification_set_by`, `budget_caps`, `budget_consumed`, `kill_switch_active` + reason + triggered_by + triggered_at, `approvals_granted`, `phase_breakers`, `intake_sanitization_findings`, `policy_audit_log`. All fields have safe defaults so existing v4.2 projects continue to work unchanged.
- **`mas/orchestrator.py`** — `run_phase_node` runs the policy gate **before** the LLM call. On first phase entry (`classify`), also runs intake sanitization on the brief and starts the wall-clock budget meter. After every LLM call, records token and cost consumption via `record_consumption_to_state()` regardless of success. If the gate denies, the phase is logged as `POLICY_BLOCKED` in the audit log and the function returns without calling the LLM.
- **`mas/llm_client.py`** — added `route_model(phase, complexity, risk_classification)` scaffold for complexity-aware routing. Currently delegates to the existing `MODEL_ROUTING` table with logging for future routing decisions. Full LiteLLM/OpenRouter integration remains a v5 theme.
- **`mas/api.py`** — version bumped from 4.2.0 → 4.3.0 in both the FastAPI constructor and `/health` response. Header comment updated to "REST API (v4.3)".
- **`docs/v5-ROADMAP.md`** — added "Update — v4.3 has shipped" section explaining what v4.3 moved out of v5 scope (deterministic enforcement layer, prompt injection defenses, EU AI Act risk classification framework, multi-provider routing scaffold, governance and security documentation) and what each of the three v5 themes looks like with the v4.3 baseline in place.
- **`README.md`** — header bumped to v4.3 with full changelog entry, repo map updated to include `mas/policy.py`, `mas/security/`, `compliance/`, and `docs/security/` directories, file-map table extended with v4.3 operational entries (classify under EU AI Act, review compliance posture, kill runaway project, check budget, review policy audit log).

### Architectural properties unchanged from v4.2

- PostgreSQL persistence via `store.py` — v4.3 policy state uses the same JSONB storage pattern
- Langfuse tracing via `observability.py` — policy events ride the existing trace infrastructure
- Prior consumption via `priors.py` — v4.3 adds the policy gate before `priors.get_prior_hint()` runs
- Eval harness (`evals/run_evals.py`, `evals/run_evals_batch.py`) — unchanged; golden cases should be extended with adversarial briefs in a follow-up
- Dashboard (`dashboards/index.html`) — unchanged; a v4.3.x release could add a policy-audit panel

### Upgrade notes from v4.2 → v4.3

1. **No SQL migration required.** All new fields are stored in the JSONB state blob via `store.py`.
2. **No environment variable changes.** The sanitizer and policy layer work against the existing configuration.
3. **Default behavior is unchanged for existing projects.** A v4.2 project loaded under v4.3 will have `risk_classification=minimal_risk` (default), conservative budget caps (2M tokens / $25 / 1 hour / 100 LLM calls), and an empty sanitization result (the sanitizer runs on first phase entry of new projects). Existing projects do not need to be re-classified unless they are resumed with new phases.
4. **Operator actions required before high-risk deployments.** Any project intended for an Annex III use case (employment, credit, insurance, education, law enforcement, migration, justice, essential services) must be classified `high_risk` via `POST /projects/{id}/risk-classification` and routed through the seven-step separate compliance track documented in `compliance/eu-ai-act-classification.md`. The API will not prevent a minimal_risk classification on an Annex III use case — the operator is the enforcement layer.
5. **Recommended kill-switch drill.** Test the kill switch on a non-production project before relying on it. Untested kill switches have failed in real incidents (see `docs/security/threat-model.md`).
6. **Recommended eval harness extension.** Add 3–5 adversarial briefs to `mas/evals/golden_cases.jsonl` covering each of the five sanitizer categories. Ensures regression coverage when the pattern library is extended.

### Open follow-ups (not blocking v4.3 ship)

- **Kill switch drill** — operator action, not a code change
- **Spanish-language sanitizer patterns** — the current pattern library is English-only; the operator's bilingual practice creates a known gap. Add in v4.3.x.
- **Dashboard policy-audit panel** — v4.3.x enhancement; not blocking for v4.3
- **`fail_hard` per-project configuration** — v4.3 supports it at the function level but does not yet wire it into the API endpoint as a project config flag. Add in v4.3.x.
- **ISO/IEC 42001 gap analysis** — operator work, not a code change. Target Q2 2026.
- **Pattern library quarterly review** — recurring operator discipline

### What remains explicitly deferred to v5

Per `docs/v5-ROADMAP.md` (updated in v4.3): MCP gateway with security envelope, hybrid vector + graph memory, full LiteLLM/OpenRouter multi-provider routing, A2A multi-agent coordination, causal inference observability tooling, dry-run mode for new agent capabilities, behavioral monitoring beyond the policy gate (sycophancy probes, goal-fidelity scoring, behavioral baselines).

---

## v4.2.0 — 2026-04-08

Non-breaking increments on top of v4.1. The headline items are (1) the operator dashboard that was explicitly deferred to v4.2, (2) prior consumption wired into the orchestrator at run-time — the smallest change that makes the Bayesian story non-decorative, and (3) the corrected v5 roadmap folding in all 8 revisions from the ARVV audit of the original v5 plan.

### Added

- **`mas/priors.py`** — reads the latest `prior_snapshots` row for a phase and returns an injectable calibration hint. Fail-soft: empty string if no database, no snapshot, or fewer than 5 resolved outcomes. 5-minute in-memory cache with `invalidate_cache(phase)` for `update_priors.py` to call after a write. First real snapshot → the orchestrator's system prompt shifts. Empty snapshots → identical to v4.1 behavior.
- **`dashboards/index.html`** — single-file operator dashboard. Reads `/health`, `/projects`, `/calibration/priors`, `/calibration/framework-performance`, `/calibration/deltas` from the v4.2 API. Auto-refreshes every 30s. No build step, no separate server. Open it in a browser and point the `API base URL` field at your running FastAPI instance (CORS is already `*`). Renders projects with domain badges, a phase-priors grid (8 cards with Brier / ECE / α/β / direction), calibration deltas table, and framework usage grouped by phase with explicit `pending` styling for NULL Brier columns per the fail-soft contract.
- **Three new API endpoints** in `mas/api.py`:
    - `GET /calibration/framework-performance` — surfaces the fail-soft stub rows with the Brier columns left NULL-but-present
    - `GET /calibration/deltas` — surfaces the `calibration_deltas` SQL view for resolved projects
    - (The existing `GET /calibration/priors` from v4.1 completes the dashboard data contract)
- **`docs/v5-ROADMAP.md`** — the ARVV-corrected v5 plan. Three themes instead of two (vertical specialization added as a first-class option), hard week-4 acquisition-bottleneck gate, explicit measurement protocol replacing the invented "5 clients asking" threshold, mandatory kill-switch architecture for Theme B's agentic execution, LangGraph-vs-Temporal correction, explicit operator capacity floor (4 hrs/week minimum), labeled judgment calls re-evaluated every 4 weeks. The meta-finding is preserved at the bottom of the document: the highest-leverage move is not revising the roadmap but booking three intake calls.

### Changed

- **`mas/orchestrator.py`** — two surgical edits:
    - `build_system_prompt(phase, json_mode=True, calibration_hint="")` now accepts an optional calibration hint appended to the system prompt tail
    - `run_phase_node()` lazy-imports `priors` and fetches the hint before calling `build_system_prompt()`; failures are caught and logged at debug level, never blocking the phase
  - Net effect: zero behavior change until the first real `prior_snapshots` row exists, then gradual calibration drift as the meta-learner accumulates data
- **`mas/api.py`** — version bumped from 4.1.0 → 4.2.0 in both the FastAPI constructor and the `/health` endpoint response
- **`mas/README.md`** — file map updated to reflect `prompts/` contents (loader.py, router.md, phases/) replacing the deleted `templates.py` row
- **`CHANGELOG.md`** and **`README.md`** — this file + version bump to 4.2 in the top-level README

### Removed

- **`mas/prompts/templates.py`** — deleted. Orphaned since v4.1; no code path imported it. Three prompt code paths → two. Final consolidation of `orchestrator.py`'s inline builders with `prompts/loader.py` is deferred to v5 because it changes behavior and needs to land alongside a full eval suite regression run.

### Upgrade notes from v4.1 → v4.2

1. **No SQL migration required.** v4.2 reads from tables and views that already exist in v4.1's `outcomes.sql`.
2. **No environment variable changes.** Priors consumption works against the existing `DATABASE_URL`. No new secrets, no new services.
3. **Behavior-drift warning.** Prior consumption is non-breaking at the API surface but behavior-changing at the output surface the moment real snapshots exist. **Recommended procedure:**
    - Run the eval suite once against `--mock` to validate plumbing after upgrading
    - Let `update_priors.py` run for ≥ 5 days with real resolved outcomes to produce actual snapshots
    - Run the eval suite again (real, not mock) immediately before and immediately after the first snapshot write, and compare the reports
    - If the drift is in the direction you want (e.g., better-calibrated domain classification, tighter Brier scores on resolved hypotheses), keep it
    - If the drift breaks eval cases, either tune the hint format in `priors.py` or extend the golden case frameworks to tolerate the new calibration context
4. **Dashboard requires the API to be running and accessible.** Default URL in the dashboard is `http://localhost:8000`; change the field to your staging/prod host as needed.
5. **Technical debt deferred to v5** is catalogued in the new `docs/v5-ROADMAP.md` under "Technical debt v5 must clear (regardless of theme)."

### Open follow-ups (not blocking v4.2 ship)

- `orchestrator.py` inline prompt builders + `prompts/loader.py` still coexist. Merging them is a v5 decision because it changes behavior and needs to land with a full eval regression.
- In-process `running: set[str]` in `api.py` is still the concurrency gate. Scales to one worker process. v5 needs a Redis-backed or Postgres-advisory-lock replacement before horizontal scaling.
- Alembic migration tooling still absent. First v5 column add needs this.
- `docs/v4-Multi-Agent-System-Prompt.md` (930 lines) still shadows the real prompts in `mas/prompts/router.md` + `phases/`. Rewrite as a derivation or delete in v5.

---

## v4.1.1 — 2026-04-07 (same-day patch)

Two follow-on items decided immediately after the v4.1.0 ship review.

### Added

- **Batch eval runner.** `mas/evals/run_evals_batch.py` — same scoring logic as `run_evals.py` but routes the 12 LLM-judge calls through Anthropic's Message Batches API. ~50% cheaper, async (typically minutes; 24h max). Supports `--submit-only` and `--resume <batch_id>` for very long suites. Use it for nightly runs and post-rewrite regression sweeps; keep `run_evals.py` real-time on PRs where latency dominates cost.
- **Nightly batch workflow.** `.github/workflows/evals-nightly-batch.yml` — runs the batch suite at 06:00 UTC daily. On regression, opens a GitHub issue tagged `eval-regression` with the failing case IDs in the body. Uses `actions/upload-artifact@v4` so the per-case JSON report is always inspectable.
- **Framework performance fail-soft stub.** `jobs/update_priors.py` now actually writes `framework_performance` rows on every nightly run. For each `(framework, phase)` pair from `config.FRAMEWORKS_BY_PHASE`, it counts how many `phase_outputs` rows in the rolling window mention the framework name and writes:
    - `n_uses` — populated (real count)
    - `n_verdict_changes` — `0` (placeholder)
    - `avg_brier_when_used`, `avg_brier_when_absent`, `value_score` — `NULL`
  - The `outcomes.sql` schema now carries an explicit FAIL-SOFT CONTRACT comment block: dashboards MUST treat NULL as "data not yet available," not as "framework had no value." This way the table is never empty (no 404s) and we never fabricate Brier deltas the system cannot honestly support. Implement the Brier-weighted columns once you have ≥10 resolved projects with framework-tagged outputs.
- **Eval README expanded** with the two-runner table and the cost/latency tradeoff rationale.

### Open question still on the table

- **Lead-magnet pricing A/B.** Not actioned in code; this is a funnel goal question, not a code question. Address inline in the response, not in v4.1.1.

---

## v4.1.0 — 2026-04-07

The v4.1 release closes every HIGH-severity item in `v4-Comprehensive-Analysis-Report.md` and adds five architectural upgrades (the "Job B" track). Most of the audit's HIGH items were already addressed in the source bundle; see `JOB_A_COMPLETION.md` for the reconciliation.

### Added (Job B — architectural upgrades)

- **Persistent project store.** `mas/store.py` — PostgreSQL JSONB-backed state persistence with transparent in-memory fallback for local dev. Projects now survive process restarts. Schema extension in `mas/sql/outcomes.sql`.
- **Langfuse observability.** `mas/observability.py` — thin wrapper around Langfuse that is a no-op unless `LANGFUSE_PUBLIC_KEY` is set. Wired into `api.py` around every phase execution. Zero overhead when disabled.
- **Router prompt refactor.** 930-line monolith (`v4-Multi-Agent-System-Prompt.md`) split into:
    - `mas/prompts/router.md` — 200-line orchestrator prompt (always loaded)
    - `mas/prompts/phases/00-classify.md` through `05-report.md` — 6 lazy-loaded phase modules (40–80 lines each)
    - `mas/prompts/loader.py` — Python loader with `@lru_cache`, designed to be prompt-cache-friendly (router chunk is bitwise-identical across all calls)
  - **Measured impact:** input tokens per call dropped from ~8,500 (monolith) to ~1,750–2,000 (router + one phase). ~4× reduction — larger than the 60–80% estimated in the Job B plan.
- **Eval harness.** `mas/evals/` — 12 golden decision cases + deterministic scoring + Sonnet 4.6 LLM judge + GitHub Actions CI gate (`.github/workflows/evals.yml`). Pass threshold: 75%. Cost per full run: ~$2–4.
  - Golden cases include real engagement contexts (HappyHiller GEO pivot, First Choice SC market entry, MOF Hamilton-syringe method, solo-consultant wedge offer).
- **Feedback loop closure — the Bayesian story, made real.**
    - `mas/sql/outcomes.sql` — `outcomes`, `prior_snapshots`, `framework_performance` tables + `calibration_deltas` view
    - `mas/tools/calibration.py` — Brier, ECE, Beta-prior update, reliability curves
    - `mas/jobs/update_priors.py` — nightly job that ingests resolved outcomes and updates recommended priors per phase
    - Three new API endpoints: `POST /projects/{id}/outcomes`, `GET /projects/{id}/calibration`, `GET /calibration/priors`
  - This is the piece that turns "we use Bayesian gates" from a decorative claim into an operationally live loop.
- **GTM lead magnets.** `lead-magnets/`:
    - `sample-decision-audit.md` — redacted 10-section sample dossier demonstrating full v4 output
    - `roi-calculator.html` — single-file interactive calculator comparing traditional consulting vs Decision Engine for any decision size
- **Top-level `README.md`** — getting-started, repo map, 5-minute run path, "which file do I use for what?" table. Closes CROSS-3 from the audit ("no documentation connecting the pieces").

### Changed (Job A — audit reconciliation)

- **`mas/config.py`.** OpenAI fallback chain updated from `gpt-4.1`/`gpt-4.1-mini` to `gpt-5`/`gpt-5-mini` family aliases (future-proof against version bumps).
- **`mas/requirements.txt`.** `langgraph>=1.0.0` pinned (GA on Oct 22, 2025). `langgraph-checkpoint-postgres>=2.0.0` bumped for 1.0 compatibility.
- **`v4-workflow-app.jsx`** (frontend). `API_MODEL` updated from `claude-sonnet-4-20250514` (Sonnet 4) to `claude-sonnet-4-6` (current generation).
- **`mas/api.py`.** Version bumped to `4.1.0`. In-memory `projects: dict` replaced with `store` module calls throughout. Added endpoints for outcomes, calibration, calibration/priors, and DELETE /projects/{id}. `/health` now reports persistence and tracing status.

### Verified unchanged (audit items already at fixed state in the source bundle)

- Excel `Agent Routing` tab Opus 4.6 pricing: already $5/$25, sourced to Anthropic April 2026 pricing.
- PPTX slide 5: already shows CrewAI at $18M (PitchBook's $24.5M includes unconfirmed pre-seed). Hebbia already flagged as "July 2024, last reported".
- PPTX slide 7: already uses realistic 65–85% net margin scenarios with API-cost column, not the misleading 97% figure.
- Master Plan §1 "Your ideal client": persona added.
- Master Plan §3.4 "Monthly cost budget": cost stack added.
- Master Plan §3.5: channel prioritization (LinkedIn → warm network → cold email wk 2–3 → others mo 2+).
- `v4-pitch.jsx`: hero already rewritten outcome-first ("Analyze any strategic decision from every angle — in days, not weeks").
- HTML dashboard cold email volume: 30–50/inbox × 2–3 inboxes = 60–90 total, within the safe 25–50/inbox range. Audit's flag was a misread.

### Migration notes from v4.0

1. **Database.** Run `sql/init.sql` if you haven't, then `sql/outcomes.sql`. `state_snapshots` will be created automatically by `store.py` on first use.
2. **Environment.** New optional env vars: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. All observability is off unless both keys are set.
3. **Prompts.** The legacy `prompts/templates.py` remains in place for backward compatibility with `orchestrator.py`. Code paths that want the new loader should `from prompts.loader import build_prompt`. A follow-on cleanup will consolidate prompt construction through the loader.
4. **Cron.** Schedule `python -m jobs.update_priors` once daily (e.g., 03:00 local). The job is idempotent — running twice the same day just overwrites that day's snapshot.
5. **CI.** Enable `.github/workflows/evals.yml` and set the `ANTHROPIC_API_KEY` repo secret. Evals skip on PRs that don't touch `mas/prompts/**` or orchestration code.

### Non-breaking omissions (deferred to v4.2)

- **Full prompt-builder consolidation.** `orchestrator.py` still contains the per-phase builder functions from v4.0 alongside the new `prompts/loader.py` path. Both work; picking one is a follow-on PR.
- **Framework performance aggregation.** `update_priors.py` has the schema and hook but the framework-level Brier computation is a stub (see `compute_framework_performance`). Implementing it requires joining `phase_outputs.output_json` with `outcomes` in a deployment-specific way.
- **Multi-tenant workspace isolation.** `store.py` is tenant-scoped by `project_id` but has no workspace/org layer. Adequate for current single-operator usage.

### Open questions for the next iteration

1. Should the eval harness move to the Anthropic Batch API (50% cheaper, 24-hour turnaround) or stay real-time for faster PR feedback?
2. Should `update_priors.py` write framework_performance even in stub form so downstream dashboards have something to read?
3. Is the $500 Decision Audit Lite wedge offer worth A/B testing against the $1,500 price in the lead magnets, or stick with $1,500 as the anchor?

---

## v4.0.0 — April 2026

Initial release. See `v4-Comprehensive-Analysis-Report.md` for the pre-v4.1 state.
