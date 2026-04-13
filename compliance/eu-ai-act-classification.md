# EU AI Act Risk Classification — Decision Engine

**Version:** 4.3
**Date:** April 2026
**Audience:** Operators classifying individual Decision Engine deployments
**Status:** Operational guidance, not legal advice. For legally binding determinations, consult qualified EU AI Act counsel.

---

## Three positions you must internalize before classifying any project

1. **The Decision Engine is not high-risk by default.** It is a decision-support tool that produces analysis and recommendations. Humans retain review authority and final decision-making. By the literal text of the EU AI Act, that posture keeps the engine itself outside Annex III.

2. **Per-project classification is mandatory.** Default does not mean "ignore." Every deployment must be classified by the operator at intake, with the classification recorded in `state.risk_classification` via `POST /projects/{id}/risk-classification`. Skipping the classification is a compliance failure even if the eventual classification would have been minimal_risk.

3. **Annex III use cases require a separate compliance track, not ordinary deployment.** The moment a project is intended to support employment, creditworthiness, life/health insurance, education, essential public services, law enforcement, justice, migration, or critical infrastructure decisions, it falls into the high-risk regime — or, at minimum, triggers high-risk operator obligations that the operator inherits regardless of how the tool itself is classified. These deployments cannot ride the standard intake flow. They require: a documented conformity assessment, additional human-oversight controls beyond the default, transparency notices to affected persons, and a separate sign-off chain.

These three positions are encoded in the API contract: every project starts at `risk_classification = "minimal_risk"`, and the operator must affirmatively call `POST /projects/{id}/risk-classification` to either confirm minimal_risk or escalate. The orchestrator records who set the classification, when, and why.

---

## The legal landscape (April 2026)

The EU AI Act is **already partially in force**. The relevant phased timeline:

| Date | What applies | Status |
|---|---|---|
| August 1, 2024 | Act entered into force | Done |
| **February 2, 2025** | Prohibited AI practices (Article 5) and AI literacy obligations | **In force** |
| **August 2, 2025** | General-Purpose AI (GPAI) model obligations, governance bodies, penalty provisions | **In force** |
| **August 2, 2026** | Commission enforcement powers for GPAI providers; high-risk system rules for newly developed systems | **Imminent** |
| August 2, 2027 | Provisions for legacy high-risk systems already on the market when the Act entered into force | Future |

Penalties: up to **€35M or 7% of global turnover**, whichever is higher.

Two implications for the Decision Engine:

- **GPAI obligations are already in force.** Whatever foundation model the Decision Engine uses (Claude Opus 4.6, Sonnet 4.6, GPT-5.4) is itself subject to the GPAI provisions in force since August 2, 2025. The Decision Engine inherits no obligation here directly, but you should verify that every model in use is GPAI-compliant — the model provider's compliance posture is your supply chain.
- **High-risk system rules apply from August 2, 2026** for newly-developed systems. If a Decision Engine deployment is classified as high-risk, the conformity assessment must be complete by that date or the deployment must be paused.

---

## The four risk tiers

The Decision Engine API uses four classification values, mapping to the EU AI Act's four risk tiers. Each tier has different obligations and a different operational track.

### `prohibited`

Use cases that are prohibited under Article 5. These include:
- Subliminal techniques to materially distort behavior in harmful ways
- Exploiting vulnerabilities of specific groups (age, disability, social/economic situation)
- Social scoring by public authorities
- Real-time remote biometric identification in public spaces by law enforcement (with narrow exceptions)
- Untargeted scraping of facial images for facial recognition databases
- Inferring emotions in workplaces and educational institutions (with narrow exceptions)
- Biometric categorization systems inferring sensitive attributes (race, political opinion, religion)

**The Decision Engine has no capability that supports any of these use cases. If a project's intended purpose falls into this category, the operator must reject the engagement entirely.** Setting `risk_classification = "prohibited"` is not a configuration — it is a record that the engagement was rejected at intake and should never run.

### `high_risk`

Use cases listed in Annex III. The relevant categories for a decision-support tool:

- **Employment, workers management, and access to self-employment** — recruitment screening, promotion decisions, task allocation, performance evaluation, monitoring of workers
- **Access to essential private and public services** — creditworthiness, credit scoring, dispatching of emergency services, eligibility for public benefits
- **Insurance** — risk assessment and pricing for life and health insurance
- **Education and vocational training** — admission decisions, evaluation of learning outcomes, monitoring of prohibited behavior during tests
- **Law enforcement** — risk assessment of natural persons becoming victims or offenders, polygraph or emotion detection, evaluation of evidence reliability, profiling
- **Migration, asylum, border control** — polygraph, risk assessment, examination of applications, detection of irregular migration
- **Administration of justice and democratic processes** — assisting judicial authorities in researching and interpreting facts and law, influencing election outcomes

**If a Decision Engine deployment is intended to support a decision in any of the above categories, the project must be classified high_risk and routed to the separate compliance track described below.** This is a mandatory escalation, not a judgment call.

### `limited_risk`

Use cases that interact with natural persons in ways requiring transparency obligations (the system must inform users they are interacting with an AI), or that generate or manipulate content in ways requiring disclosure (deepfakes, synthetic media). The Decision Engine produces analysis documents — these are AI-generated output and should be disclosed to any reader who is not the operator. Many Decision Engine deployments will fall here in practice, particularly when a client receives the output as a deliverable.

### `minimal_risk`

Default. Internal analysis and decision-support that does not influence consequential decisions about identifiable people, does not generate content for external audiences, and is not used in any Annex III context. Minimal-risk deployments have no substantive obligations under the EU AI Act beyond the GPAI compliance of the underlying foundation model.

---

## The operator decision tree

Run this at project intake. Set the classification via API before running any phase.

```
START → Is the project's intended purpose in Article 5 (prohibited)?
        ├── YES → Reject the engagement. Record rejection.
        └── NO → continue
                 │
                 Is the project's intended purpose in any Annex III category?
                 (employment, credit, insurance, education, law enforcement,
                  migration, justice, essential services)
                 ├── YES → Classify "high_risk".
                 │         Route to the SEPARATE COMPLIANCE TRACK below.
                 │         Do NOT proceed with ordinary intake.
                 └── NO → continue
                          │
                          Will the output be delivered to a person who is
                          not the operator (e.g., a client receives the report)?
                          ├── YES → Classify "limited_risk".
                          │         Add AI-generated content disclosure to the
                          │         report header and to any preview shown to
                          │         the recipient.
                          └── NO → Classify "minimal_risk".
                                   No additional obligations beyond verifying
                                   GPAI compliance of the foundation model.
```

---

## The separate compliance track for high_risk projects

**A high_risk classification cannot ride the ordinary intake flow.** The operator must complete each of the following before running any phase:

1. **Conformity assessment.** A documented assessment of how the deployment meets the high-risk system requirements in Articles 9–15: risk management system, data and data governance, technical documentation, record-keeping, transparency, human oversight, accuracy, robustness, cybersecurity. Template forthcoming in `compliance/high-risk-conformity-template.md` (v4.3.x).

2. **Risk management documentation.** An identified, analyzed, evaluated, and mitigated set of risks specific to the deployment. Reference: `docs/security/threat-model.md` provides the engine-level baseline; the operator must extend it for the deployment-specific risk surface.

3. **Data governance evidence.** Documentation of the data sources used (the brief, any uploaded files, retrieved memory), with provenance, quality, and bias assessments. The Decision Engine logs every input automatically via the policy audit log; the operator must add provenance metadata at intake.

4. **Human oversight design.** A documented description of how a human reviews each phase output before it influences any consequential decision. Default policy gate already requires HITL approval for `irreversible_internal` actions on high-risk projects (see `mas/policy.py`); the operator must verify this is sufficient for the specific use case and add additional review steps if not.

5. **Transparency notice to affected persons.** When the deployment supports a decision about an identifiable person, that person must be informed that an AI system is involved. The operator is responsible for delivering this notice; the Decision Engine does not communicate with end users directly.

6. **Separate sign-off chain.** A high-risk deployment cannot be approved by a single operator. It requires sign-off from at least one additional reviewer (compliance officer, legal counsel, or designated safety lead). Record the sign-off in the `risk_classification_set_by` field as a comma-separated list of reviewers, plus a reference to the conformity assessment document.

7. **Post-market monitoring.** High-risk deployments require ongoing monitoring against the risks identified in step 2. Use `GET /projects/{id}/policy-audit` and the dashboard to track every action; review monthly.

**No high-risk deployment may run without all seven steps complete.** The orchestrator does not enforce this — it cannot, because it cannot tell whether a conformity assessment exists. The operator is the enforcement layer. Treat any high-risk deployment that runs without these steps as a compliance incident worth a post-mortem.

---

## What the API enforces automatically

The deterministic enforcement layer in `mas/policy.py` does what it can without operator intervention:

- **Default classification is `minimal_risk`.** No project can run without an explicit or default classification.
- **HITL approval is required for `irreversible_internal` actions on high-risk projects.** This means that, for example, sealing a project's gauntlet output will be blocked on a high-risk project until an operator grants approval via `POST /projects/{id}/approvals`.
- **Every classification change is logged** to the policy audit log with a timestamp and the operator who set it.
- **Every kill-switch trigger is logged.** Every budget breach is logged. Every blocked action is logged. The audit log is the source of truth for any compliance review.

The API does not enforce the operator's compliance obligations themselves. It cannot verify that a conformity assessment exists. It cannot tell whether the human-oversight design is adequate. It cannot validate the data governance evidence. Those are operator responsibilities, and the consequences of skipping them are operator-borne.

---

## Common confusions

**"The Decision Engine is just a tool — the EU AI Act doesn't apply."** Wrong. The EU AI Act applies to providers and deployers of AI systems. The operator running the Decision Engine on behalf of clients is a provider (or a deployer, depending on contract structure). The classification of the engine does not exempt the operator from the obligations attached to the use case.

**"We're in Mexico, the EU AI Act doesn't apply to us."** Wrong. The Act has extraterritorial reach. If the output is used to make decisions affecting persons in the EU, or if the operator places the system on the EU market, the Act applies regardless of where the operator is physically located.

**"High-risk just means we add a disclaimer."** Wrong. High-risk classification triggers the seven-step compliance track above. A disclaimer is part of the transparency obligation but is nowhere near sufficient on its own.

**"If we never tell the engine that the project is high-risk, the obligations don't apply."** Wrong, and dangerous. The classification must reflect the project's actual intended purpose. Misclassifying a high-risk use case as minimal_risk to avoid the compliance track is a violation in itself, separate from the underlying obligations. The audit log will surface the discrepancy in any future review.

**"The operator has discretion on classification."** Partial. The operator has discretion at the boundary between minimal_risk and limited_risk (transparency obligations). The operator does not have discretion on whether a clearly Annex III use case is high-risk. That determination is governed by the Act's text, not by operator preference.

---

## What to do this quarter

If you have not yet classified the engine's existing or planned deployments, do this now:

1. **Inventory** every existing Decision Engine project and every planned deployment in Q2/Q3 2026.
2. **Run the decision tree** above on each one. Record the classification.
3. **Set classifications via API** (`POST /projects/{id}/risk-classification`) for every existing project. The default is `minimal_risk`; you must affirm this by setting it explicitly.
4. **Identify any high-risk projects.** Pause them. Begin the seven-step compliance track for each one. Plan for completion by **June 2026**, two months ahead of the August 2 enforcement milestone.
5. **Verify GPAI compliance of foundation models in use.** Document which models you call, which provider, and confirm the provider has published their GPAI compliance posture.
6. **Begin ISO/IEC 42001 alignment** as the cross-jurisdictional compliance umbrella. See `compliance/governance-checklist.md`.

This is operational, not theoretical. Penalties are €35M or 7% of global turnover, and the August 2, 2026 milestone is approximately four months away.

---

## References

- EU AI Act (Regulation 2024/1689). Phased enforcement: prohibited practices Feb 2, 2025; GPAI obligations Aug 2, 2025; Commission enforcement powers for GPAI providers and high-risk system rules Aug 2, 2026; legacy high-risk Aug 2, 2027.
- Article 5 — Prohibited AI practices.
- Annex III — High-risk AI systems.
- Articles 9–15 — High-risk system requirements.
- Enterprise AI Agent Upgrade Strategy v2.1, Section 6.1 — The phased timeline and the "build to compliance now" rationale.
- ISO/IEC 42001:2023 — AI management system standard. Cross-jurisdictional alignment.

---

*This document is operational guidance, not legal advice. The operator is responsible for obtaining qualified EU AI Act counsel for any specific deployment classification, particularly for any project that might fall into the high-risk regime. Penalties for misclassification or non-compliance are substantial and personal liability is possible.*
