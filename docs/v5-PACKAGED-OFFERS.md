# v5 Packaged Offers

This selector helps a local operator choose the right packaged workflow before
creating a project. These packages are packaged workflows over the existing
Decision Engine, not separate backend products, not new reasoning modes, and not
first-class backend runtime templates.

Every package uses the same local operator workflow:

`dashboards/index.html -> create project -> run -> review -> export`

Human review is required before any output is shared or acted on. Client-safe
means after review.

## Quick Comparison

| Package | Best for | Start with | Framing | Boundaries |
|---|---|---|---|---|
| [Strategic Decision Audit](v5-STRATEGIC-DECISION-AUDIT.md) | General high-stakes decision audit | [Intake](templates/strategic-decision-audit-intake.md), [example](examples/strategic-decision-audit-brief.md), [demo](v5-STRATEGIC-DECISION-DEMO-SCRIPT.md) | `Strategic Decision Audit framing` / `strategic_audit` | Not public SaaS, not autonomous decision-making, not guaranteed recommendations. |
| [Automation ROI Audit](v5-AUTOMATION-ROI-AUDIT.md) | Automation prioritization and ROI assumption review | [Intake](templates/automation-roi-audit-intake.md), [example](examples/automation-roi-audit-brief.md), [demo](v5-AUTOMATION-ROI-DEMO-SCRIPT.md) | `Automation ROI example framing` / `automation_roi` | ROI assumptions are estimates, not guarantees; not guaranteed recommendations. |
| [AI Readiness Audit](v5-AI-READINESS-AUDIT.md) | Directional readiness assessment, not certification | [Intake](templates/ai-readiness-audit-intake.md), [example](examples/ai-readiness-audit-brief.md), [demo](v5-AI-READINESS-DEMO-SCRIPT.md) | `AI readiness example framing` / `ai_readiness` | Readiness findings are directional, not guarantees; not security certification and not compliance certification. |
| [Real Estate Decision Audit](v5-REAL-ESTATE-DECISION-AUDIT.md) | Real-estate decision framing | [Intake](templates/real-estate-decision-audit-intake.md), [example](examples/real-estate-decision-audit-brief.md), [demo](v5-REAL-ESTATE-DECISION-DEMO-SCRIPT.md) | Use `Strategic Decision Audit framing` / `strategic_audit`; real-estate framing lives in the brief/docs only | Not investment advice, not legal advice, not tax advice, not appraisal or valuation certification, and not lending or credit underwriting. |

## When To Use Each Package

- Use **Strategic Decision Audit** for a general high-stakes decision with
  multiple paths, unclear evidence, meaningful downside, or stakeholders who
  need a reviewed recommendation.
- Use **Automation ROI Audit** for automation prioritization, workflow
  comparison, baseline assumption review, implementation burden, and ROI
  estimate review.
- Use **AI Readiness Audit** for directional readiness assessment across
  business goals, process maturity, data quality, tool-stack constraints, team
  adoption, governance, privacy, security, human oversight, and 30/60/90-day
  action planning.
- Use **Real Estate Decision Audit** for buy/sell/hold, acquisition screening,
  market-entry, lease, development, renovation, capex, portfolio risk, or
  diligence-framing decisions.

## When Not To Use Each Package

- Do not use **Strategic Decision Audit** to decide on its own or to guarantee
  outcomes.
- Do not use **Automation ROI Audit** as guaranteed savings, guaranteed ROI, or
  finance-approved automation approval.
- Use **AI Readiness Audit** only for directional readiness; it is not security
  certification, not compliance certification, not legal sign-off, not privacy
  sign-off, and not a guaranteed AI transformation plan.
- Use **Real Estate Decision Audit** only for decision framing; it is not
  investment advice, not financial advice, not legal advice, not tax advice,
  not appraisal or valuation certification, not lending or credit underwriting,
  and not regulated real-estate decision automation.

## Package Links

| Package | Main doc | Intake template | Example brief | Demo script |
|---|---|---|---|---|
| Strategic Decision Audit | [Main](v5-STRATEGIC-DECISION-AUDIT.md) | [Intake](templates/strategic-decision-audit-intake.md) | [Example](examples/strategic-decision-audit-brief.md) | [Demo](v5-STRATEGIC-DECISION-DEMO-SCRIPT.md) |
| Automation ROI Audit | [Main](v5-AUTOMATION-ROI-AUDIT.md) | [Intake](templates/automation-roi-audit-intake.md) | [Example](examples/automation-roi-audit-brief.md) | [Demo](v5-AUTOMATION-ROI-DEMO-SCRIPT.md) |
| AI Readiness Audit | [Main](v5-AI-READINESS-AUDIT.md) | [Intake](templates/ai-readiness-audit-intake.md) | [Example](examples/ai-readiness-audit-brief.md) | [Demo](v5-AI-READINESS-DEMO-SCRIPT.md) |
| Real Estate Decision Audit | [Main](v5-REAL-ESTATE-DECISION-AUDIT.md) | [Intake](templates/real-estate-decision-audit-intake.md) | [Example](examples/real-estate-decision-audit-brief.md) | [Demo](v5-REAL-ESTATE-DECISION-DEMO-SCRIPT.md) |

## Canonical Demo Bundle

For a reproducible Automation ROI demo bundle and operator runbook, use
[`Automation ROI canonical demo runbook`](demo/AUTOMATION-ROI-DEMO-RUNBOOK.md).

## Export/Profile Reminder

Use [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md) before sending any
client-facing artifact. The current export profiles are:

- `report`: client-safe after review.
- `client_dossier`: client-safe after review.
- `client_monitoring_template`: client-safe after review.
- `operator_dossier`: operator-only.
- `operator_monitoring_template`: operator-only.
- `machine_archive`: internal archive only.

Client-safe means after review; it does not mean correct, complete, legally
approved, confidential by access control, or ready for public distribution.

## Shared Boundaries

All packaged offers share these boundaries:

- Local operator workflow only.
- Human review required.
- Not public SaaS.
- Not autonomous decision-making.
- Not guaranteed recommendations.
- Not new reasoning modes.
- Not first-class backend runtime templates.
- Not legal advice.
- Not financial advice.
- Not tax advice.
- Not investment advice.
- Not security certification.
- Not compliance certification.
- No backend workflow phases, prompt routes, provider routing changes,
  queue/runtime architecture changes, report rewrites, export schema changes,
  dashboard redesign, auth, tenancy, public deployment hardening, new package
  runtime types, or regulated vertical-specific runtime logic.
