# v5 Runtime Foundation Demo Script

This is a talk track for a local v5 runtime foundation demo workflow. It is not
a script for presenting a fully released v5 product or public SaaS.

## Two-Minute Explanation

The Decision Engine is an operator-led decision audit system. We give it a brief
about a hard decision, optionally add source files, and run a fixed workflow:
`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.

The output is not an autonomous decision. It is a structured draft for human
review: hypotheses, stress tests, evidence maturity, risks, strategy, monitoring
controls, and client/operator exports.

## Ten-Minute Live Demo Flow

1. Start with Docker-discovered port commands:

   ```powershell
   docker compose ps
   $appPort = (docker compose port app 8000).Split(":")[-1]
   $base = "http://localhost:$appPort"
   curl.exe "$base/health"
   curl.exe "$base/runtime/preflight"
   curl.exe "$base/runtime/release-readiness"
   ```

   Mention `http://localhost:8000` only as the fallback when Docker publishes
   the container on that host port.

2. Open `dashboards/index.html` and set the API base URL to `$base`.
3. Create a project using an example brief from `docs/demo-briefs/`.
4. Explain that uploads are optional and additive; they do not automatically
   rerun the workflow.
5. Click the project row and click **Run**.
6. While the run executes, explain the phase strip and fixed workflow order.
7. When hypotheses appear, say: "These are testable candidate explanations.
   They guide what evidence we need next; they are not conclusions."
8. If evidence maturity is Hypothesis-only, say: "This is useful planning
   structure, but it needs a Sprint 0 evidence pack before client reliance."
9. Open the report and explain the recommendation, why, next actions, risks,
   and monitoring.
10. Export client and operator outputs.

## What To Click

- **New project**: paste the brief and create the project.
- **Project row**: opens the drill-in panel.
- **Overview**: high-level recommendation and state.
- **Dossier**: editable project inputs and phase outputs.
- **Workspace**: operational status, evidence state, blockers, and controls.
- **Decision trace**: explainability without raw chain-of-thought.
- **Run**: starts the full workflow.
- **Report**: review and export profiles.

## Explaining Outputs

- Client dossier: clean stakeholder-facing decision review.
- Operator dossier: internal review with trace, diagnostics, and evidence
  accounting.
- Monitoring XLSX: a spreadsheet for owners, cadence, signals, thresholds, and
  actions after the decision review.
- Client delivery package: script/service-generated board memo and execution
  tracker for local demo packaging.

## Explaining Limitations

Use direct language:

- This is not public SaaS ready.
- This is not autonomous decision-making.
- This does not guarantee causal truth.
- This does not guarantee semantic evidence proof for every claim.
- Human review remains required.
- Demo briefs are examples, not first-class vertical runtime packs.

## Closing With Next Steps

Close by asking what evidence would make the recommendation safe to rely on.
The practical next step is usually a Sprint 0 evidence pack, a reviewed client
dossier, and a monitoring template with named owners and confirmed thresholds.
