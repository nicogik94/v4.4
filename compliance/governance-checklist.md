# Governance Checklist — Decision Engine v4.3

A working checklist for the operator (and any compliance reviewer) reviewing a Decision Engine deployment. Adapted from the v2.1 enterprise strategy bundle to the Decision Engine's actual posture: mostly read-only, single-operator, internal analysis tool with no external action surface in the default configuration.

Each item is **[CRITICAL]**, **[HIGH]**, **[MEDIUM]**, or **[LOW]** by production-readiness impact.

Use this as a gate before promoting any project to a paying client engagement and as a recurring quarterly review.

---

## Section A — Identity and access (lighter than the v2.1 enterprise checklist)

The Decision Engine is a single-operator tool by default. The full Non-Human Identity discipline from the v2.1 enterprise strategy applies in spirit but not in scale.

- [ ] **[HIGH]** The operator running the engine has a named human owner (yourself, in the solo-operator case). Recorded in `risk_classification_set_by` for each project.
- [ ] **[HIGH]** API access to the engine is authenticated. Even a single-operator deployment should not expose `/projects/{id}/kill` or `/projects/{id}/budget` to unauthenticated callers.
- [ ] **[MEDIUM]** Foundation model API keys are stored in `.env` and excluded from git. Verify `.gitignore` includes `mas/.env`.
- [ ] **[MEDIUM]** API keys are rotated when an operator leaves or when key exposure is suspected.
- [ ] **[LOW]** If the engine is exposed to multiple operators, each operator has a separate identity and the audit log distinguishes them.

---

## Section B — Kill switch

- [ ] **[CRITICAL]** Kill switch is implemented in `mas/policy.py` and exposed via `POST /projects/{id}/kill`. ✅ v4.3.
- [ ] **[CRITICAL]** Kill switch state is persisted to Postgres via `store.py` so it survives orchestrator restarts. ✅ v4.3.
- [ ] **[CRITICAL]** The orchestrator checks the kill switch before every phase via `policy_gate()`. ✅ v4.3.
- [ ] **[HIGH]** Kill switch has been **tested under realistic failure conditions**. Untested kill switches have failed in real incidents (see v2.1 strategy doc, §9). Test before relying on it for any high-risk project.
- [ ] **[HIGH]** Kill switch activation triggers an entry in `policy_audit_log` with reason and triggered_by. ✅ v4.3.
- [ ] **[MEDIUM]** Operator runbook documents the kill switch invocation: when to fire, how to verify activation, how to investigate root cause.

---

## Section C — Prompt injection defenses

The Decision Engine ingests untrusted user briefs. This is the largest attack surface.

- [ ] **[CRITICAL]** Intake sanitization runs on every brief at first phase entry. ✅ v4.3 (`mas/security/intake_sanitizer.py`).
- [ ] **[CRITICAL]** Sanitization findings are recorded on `state.intake_sanitization_findings` and in the policy audit log. ✅ v4.3.
- [ ] **[HIGH]** The sanitizer has been smoke-tested against benign briefs (zero findings expected) and obvious injection attempts (CRITICAL findings expected). ✅ v4.3 — see test output in policy.py docstring.
- [ ] **[HIGH]** Operator reviews findings flagged as `recommendation = "review"` before allowing the project to proceed. The default is fail-soft; the operator is the second line.
- [ ] **[MEDIUM]** Continuous adversarial testing: golden_cases.jsonl includes briefs with embedded injection attempts; the eval harness catches regressions in detection rate.
- [ ] **[MEDIUM]** Pattern library (`_INSTRUCTION_OVERRIDE_PATTERNS`, `_ROLE_MANIPULATION_PATTERNS`, etc.) is reviewed quarterly and updated based on new attack patterns published by OWASP, Lakera, or the CSA Agentic AI Red Teaming Guide.
- [ ] **[LOW]** Long-term: integrate a dedicated prompt-injection scanner like Lakera Guard or Promptfoo policy engine for higher-confidence detection.

---

## Section D — Blast radius containment

The Decision Engine is read-only by design. The blast radius is naturally small. The defense is keeping it small.

- [ ] **[CRITICAL]** Reversibility classification is defined for every action via `PHASE_ACTION_MAP` in `mas/policy.py`. ✅ v4.3. All phases default to `reversible_internal`. Sealing and writing calibration snapshots are `irreversible_internal`.
- [ ] **[CRITICAL]** No phase produces an `irreversible_external` action in the default configuration. ✅ v4.3 — verified in PHASE_ACTION_MAP.
- [ ] **[CRITICAL]** Per-project budget caps are enforced before every LLM call. ✅ v4.3 (`policy_gate()` + `record_consumption_to_state()`).
- [ ] **[CRITICAL]** Default budget caps are conservative (2M tokens / $25 / 1 hour / 100 LLM calls / 3 reentries). ✅ v4.3.
- [ ] **[HIGH]** Per-phase circuit breaker tracks consecutive failures and opens after 3. ✅ v4.3 (`evaluate_breaker()`).
- [ ] **[HIGH]** Operator runbook documents how to reset a circuit breaker after investigating root cause.
- [ ] **[HIGH]** When new phases or tools are added in v5+, they are classified by reversibility before being wired into the orchestrator. This is a code-review checklist item, not just a runtime check.
- [ ] **[MEDIUM]** Idempotency: `POST /projects/{id}/kill` is idempotent. ✅ v4.3. `POST /projects/{id}/risk-classification` and `/budget` are also idempotent updates.
- [ ] **[MEDIUM]** Dry-run mode: TODO. v4.3 does not yet have a dry-run mode for new agent capabilities. Add when introducing any new write-side action.

---

## Section E — EU AI Act compliance per project

See `compliance/eu-ai-act-classification.md` for the full operator decision tree.

- [ ] **[CRITICAL]** Every project has a `risk_classification` set explicitly via `POST /projects/{id}/risk-classification`. Default `minimal_risk` is acceptable but must be affirmed, not skipped. ✅ v4.3 enforces default + records overrides.
- [ ] **[CRITICAL]** Every Annex III use case (employment, credit, insurance, education, law enforcement, migration, justice, essential services) is classified `high_risk` and routed to the separate compliance track.
- [ ] **[CRITICAL]** No `high_risk` project runs without all seven steps of the separate compliance track complete. See `compliance/eu-ai-act-classification.md` §"The separate compliance track for high_risk projects".
- [ ] **[HIGH]** GPAI compliance of every foundation model in use is verified and documented. The Decision Engine inherits the model provider's compliance posture.
- [ ] **[HIGH]** Limited-risk projects (output delivered to a non-operator recipient) include AI-generated content disclosure in the report header.
- [ ] **[HIGH]** Conformity assessments for any high-risk project are complete by **June 2026**, two months ahead of the August 2 enforcement milestone.
- [ ] **[MEDIUM]** Quarterly review: re-classify any project whose intended use case has changed.

---

## Section F — Observability and audit

The Decision Engine has a working observability foundation in v4.3. The next layer (causal diagnosis, replay) is a v5 theme.

- [ ] **[CRITICAL]** Every phase run is traced via Langfuse (`mas/observability.py`). ✅ v4.1+.
- [ ] **[CRITICAL]** Every policy event lands in `state.policy_audit_log`. ✅ v4.3.
- [ ] **[HIGH]** Every LLM call records token count and cost via `record_consumption_to_state()`. ✅ v4.3.
- [ ] **[HIGH]** Operator can answer "show me the trace for the failed run yesterday at 3 PM" via the dashboard. ✅ v4.2+ dashboard.
- [ ] **[MEDIUM]** Loop detection: phase circuit breaker catches consecutive failures. Doesn't yet catch stuck loops within a phase. Add in v5 if a real incident demonstrates the gap.
- [ ] **[MEDIUM]** Quality regression tracking: eval harness runs on every PR and nightly. ✅ v4.1+ (`evals.yml`, `evals-nightly-batch.yml`).
- [ ] **[LOW]** Causal inference / replay / semi-automated remediation: deferred to v5 or later. Not the 2026 baseline.

---

## Section G — ISO/IEC 42001 and standards alignment

ISO/IEC 42001 is the cross-jurisdictional compliance umbrella. It does not specifically address AI agents but provides a framework that aligns with EU AI Act, NIST AI RMF, and most sectoral rules.

- [ ] **[HIGH]** Gap analysis against ISO/IEC 42001 complete. Target: Q2 2026.
- [ ] **[HIGH]** AI management system documented (context, leadership, planning, support, operation, performance evaluation, improvement). The Decision Engine bundle's `docs/`, `compliance/`, and policy audit log together cover most of this.
- [ ] **[MEDIUM]** Certification target date set. Recommended: Q4 2026 / Q1 2027.
- [ ] **[MEDIUM]** Tracking the NIST AI Agent Standards Initiative (launched February 17, 2026).

---

## Section H — Data residency and sovereignty

Currently the Decision Engine calls foundation models hosted by Anthropic and OpenAI. Data flows out of the operator's jurisdiction by default.

- [ ] **[HIGH]** For any project that touches EU residents' personal data, document the data flow: brief → foundation model API → response. Confirm the model provider has appropriate data processing agreements in place.
- [ ] **[HIGH]** For sensitive client data, consider on-premise or in-region open-weight models (e.g., Llama 3.1 405B served via vLLM) rather than cloud APIs. v4.3 supports this via the existing provider routing in `mas/llm_client.py`; full configuration is a v5 theme.
- [ ] **[MEDIUM]** Cross-border data flows are tracked in the project's intake metadata.
- [ ] **[MEDIUM]** Tracking French CNIL and Austrian DSB enforcement precedents (€20M+ in fines for cross-border AI violations to date) for any deployment serving EU-adjacent clients.

---

## Section I — Incident response

- [ ] **[HIGH]** Operator runbook for agent incidents exists. Covers: how to trigger the kill switch, how to investigate via the policy audit log, how to recover state, how to file a post-incident report.
- [ ] **[HIGH]** Post-incident review process produces concrete action items, tracked to closure. Lessons feed back into the pattern library (Section C) and the eval harness (Section F).
- [ ] **[HIGH]** Failure drill executed at least once per quarter: simulate misbehavior, verify policy gates and kill switch work, verify audit logs are complete.
- [ ] **[MEDIUM]** Lessons from public failures (Air Canada, Replit Agent, Amazon Kiro) reviewed and applied. See `docs/security/threat-model.md` for the cross-cutting principle: deterministic enforcement, never trust the model to follow instructions.

---

## Section J — What v4.3 does not yet have (deferred to v5)

These are explicitly out of scope for v4.3 and tracked in `docs/v5-ROADMAP.md`. They are listed here so they are not forgotten and so any compliance reviewer knows what's missing.

- [ ] **[v5]** MCP gateway pattern with security envelope. The Decision Engine currently uses internal LangGraph nodes; if v5 adds external tool integration, the gateway becomes mandatory.
- [ ] **[v5]** Hybrid vector + graph memory (Cognee or equivalent). Currently the engine has Postgres state only.
- [ ] **[v5]** Multi-agent coordination via A2A. Currently there is one orchestrator and a sequence of phase agents within it.
- [ ] **[v5]** Full LiteLLM/OpenRouter multi-provider routing. v4.3 has the routing scaffold (`route_model()` in `llm_client.py`) and the existing primary+fallback chain, but no managed gateway.
- [ ] **[v5]** Causal inference and replay observability tooling. v4.3 has the foundation (traces, evals, alerts); the next layer is deferred.
- [ ] **[v5]** Dry-run mode for new agent capabilities. Add when the first new write-side action is introduced.
- [ ] **[v5]** Behavioral monitoring beyond the policy gate: sycophancy probes, goal-fidelity scoring, behavioral baselines. Useful when the engine starts running fully autonomous workflows.

---

## Quick scoring

Count the unchecked **[CRITICAL]** items. If > 0, do not run any client-facing project until they are checked. Count the unchecked **[HIGH]** items. If > 5, the deployment is at high risk and should be reviewed before any expansion.

The checklist is a recurring instrument, not a one-time pass. Re-review quarterly at minimum. Re-review immediately after any major change to phases, tools, foundation models, or deployment scope.

**Current v4.3 baseline:** the **[CRITICAL]** items are all green by virtue of the v4.3 release itself. The HIGH items split between green (kill switch implemented, sanitizer deployed, budget caps enforced) and operator-action-required (kill switch tested, GPAI compliance verified, conformity assessments started for any high-risk projects). Walking through the checklist on first deployment is a half-day exercise.
