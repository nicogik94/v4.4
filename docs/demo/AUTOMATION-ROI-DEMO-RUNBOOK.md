# Automation ROI Canonical Demo Runbook

This runbook defines the canonical local demo for the Automation ROI Audit
packaged offer. It uses synthetic demo evidence only. It does not change the
workflow, prompts, runtime, dashboard, provider routing, queue, export profiles,
risk semantics, or evidence semantics.

The demo is a hypothesis-driven diagnostic, not a measured audit. Human review
is required for high-risk/finance automation decisions, and every client-facing
artifact must be checked with the rule: Validate before client delivery.

## Canonical Project

- Project name: `TEST Aut-ROI Canonical Demo`
- Dashboard framing: `Automation ROI example framing`
- Risk classification: `high_risk`
- Brief path: `docs/demo/automation-roi/brief.md`
- Supporting data path: `docs/demo/automation-roi/supporting-data.md`
- Evidence pack path: `docs/demo/automation-roi/evidence-pack/`
- Fixed workflow order: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

## Local Setup

From the repo root:

```powershell
cd mas
docker compose up -d --build db redis app
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"
curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
python ..\scripts\demo_smoke_check.py --base-url "$base"
```

Use the Docker-discovered `$base`. Treat `http://localhost:8000` only as a
fallback when Docker publishes the app on that host port.

## Create The Project

1. Open `dashboards/index.html`.
2. Set the API base URL to the discovered local `$base`.
3. Click **New**.
4. Select `Automation ROI example framing`.
5. Set the project name to `TEST Aut-ROI Canonical Demo`.
6. Paste `docs/demo/automation-roi/brief.md` into the brief field.
7. Select `high_risk` and enter this rationale:
   `High-risk finance automation candidate with operational, compliance, and
   human-review dependencies.`
8. Upload the five evidence-pack files:
   - `automation_candidate_metrics.csv`
   - `current_process_notes.txt`
   - `tool_budget_assumptions.txt`
   - `risk_and_compliance_notes.txt`
   - `sprint0_evidence_pack_notes.txt`
9. Confirm uploaded files parsed before running the workflow.

If a prior project with this name has stale state or a failed classify result,
create a fresh project with a timestamp suffix instead of reusing the old row.

## Run And Review

1. Open the project row and click **Run**.
2. Watch the phase strip through all phases:
   `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
3. Confirm evidence maturity is `Partial evidence`.
4. Review the report for citation markers and evidence limitations.
5. Review client-facing language for raw markdown table artifacts or rough
   wording before sharing.
6. Review operator-only diagnostics, SQI findings, and monitoring thresholds.
7. Keep evidence gaps visible; do not treat the output as implementation proof.

## Expected Exports

Generate these export profiles after the `report` phase completes:

- `report` PDF: client-safe after review.
- `client_dossier` PDF: client-safe after review.
- `operator_dossier` PDF: operator-only.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_monitoring_template` XLSX: operator-only.

Do not commit generated PDFs, XLSX files, ZIP archives, machine archives, or
files copied from `upload_store/`.

## Two-Minute Talk Track

"This is the canonical Automation ROI Audit demo. We use one synthetic project,
`TEST Aut-ROI Canonical Demo`, and run it through the existing local Decision
Engine with `Automation ROI example framing` and `high_risk` classification.
The goal is to compare four automation candidates and decide which one deserves
a Sprint 0 validation, not to approve automation or promise savings.

The output is a hypothesis-driven diagnostic. It organizes the assumptions,
evidence gaps, review burden, risks, and monitoring conditions that a human
decision owner should inspect. The client-facing report and dossier are
client-safe after review, while the operator dossier and operator monitoring
template remain internal."

## Ten-Minute Operator Walkthrough

1. Show the root bundle files under `docs/demo/automation-roi/`.
2. Explain that all evidence is synthetic demo data with no private or client
   data.
3. Start the local runtime and show `/runtime/preflight`.
4. Open `dashboards/index.html` and set the discovered API base.
5. Create `TEST Aut-ROI Canonical Demo` with `Automation ROI example framing`.
6. Paste the canonical brief and select `high_risk`.
7. Upload the evidence pack and confirm parse status.
8. Run the workflow and call out each fixed phase as it completes.
9. Open the report and client dossier, then verify citation markers, evidence
   maturity, and the Validate before client delivery posture.
10. Export the client and operator monitoring XLSX files and inspect sheet names,
    metrics, thresholds, owners, and client-safe wording.
11. Close by pointing to the Sprint 0 evidence gaps that remain before any
    implementation decision.

## Known Boundaries

- Local operator workflow only.
- Hypothesis-driven diagnostic, not measured audit.
- Validate before client delivery.
- Evidence gaps still required before implementation.
- Human review required for high-risk/finance automation.
- ROI assumptions are estimates, not guarantees.
- Not legal advice.
- Not financial advice.
- Not compliance certification.
- Not security certification.
- Not public SaaS.
- Not autonomous automation approval.
- Not a new reasoning mode.
- Not a first-class backend runtime template.

## Troubleshooting

- upload_store not writable: check the configured upload path, Docker volume
  mount, and local filesystem permissions before rerunning uploads.
- Docker Desktop / WSL mount issue: restart Docker Desktop, confirm the repo
  path is shared with WSL, and rerun `docker compose ps`.
- Stale project wall-clock budget cap: create a fresh project row with a
  timestamp suffix and rerun from classify.
- Failed classify due to reused old project: do not rerun the stale row; create
  a fresh project with the canonical brief and uploads.
- Generated dirt cleanup: before commit, run
  `git restore scenario_shadow.sqlite3 scenarios/__pycache__/__init__.cpython-312.pyc tools/__pycache__/scoring.cpython-312.pyc`.

## Git Hygiene

- Do not commit `docker-compose.yml` local dirt.
- Do not commit `upload_store/`.
- Do not commit SQLite files.
- Do not commit pycache files.
- Do not commit generated PDFs, XLSX files, ZIP archives, machine archives, or
  export artifacts.
- Do not commit unrelated CSV artifacts outside the repo.
