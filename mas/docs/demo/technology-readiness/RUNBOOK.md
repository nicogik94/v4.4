# Technology Readiness Demo Runbook

Synthetic demo data only. No private or client data is included.

This runbook exercises the `technology_readiness` workflow and its deliverables. The output is an operator-reviewed readiness assessment, not TRL certification, legal patentability advice, investment readiness, production readiness, or a guarantee of commercial transfer.

## Start Locally

From the repository root, start the local app:

```powershell
docker compose up --build
```

Use the dashboard at `dashboards/index.html`. If Docker publishes a non-default port, set the dashboard API base URL to the local API. The usual local setup uses docker compose port app 8000.

## Verify Template Registration

Confirm the backend lists the Technology Readiness template:

```powershell
Invoke-RestMethod http://localhost:8001/templates
```

Expected signal:

- `template_id`: `technology_readiness`
- `project_type`: `technology_readiness`
- `label`: `Technology Readiness & Transfer Audit`

## Create The Demo Project

In the dashboard:

1. Click **New project**.
2. Select `Technology Readiness & Transfer Audit`.
3. Use `docs/demo/technology-readiness/brief.md` as the brief.
4. Add `docs/demo/technology-readiness/supporting-data.md` as supporting data.
5. Upload or paste evidence from `docs/demo/technology-readiness/evidence-pack/`.
6. Run the workflow and review each phase output before relying on it.

## Expected Rough Output

The exact output depends on the local model response and supplied evidence, but the demo is intended to produce an evidence-backed estimate close to:

- Current TRL likely 3.
- Next target likely TRL 4.
- Why not higher:
  - reproducibility not demonstrated;
  - controlled validation missing;
  - IP review incomplete;
  - cost/scalability unknown;
  - no industrial partner feedback.

Recommended actions should include:

- controlled validation protocol;
- repeatability tests;
- benchmark against alternatives;
- IP review before disclosure;
- cost/scalability estimate;
- partner validation brief.

## Export The Workbook

After the technology-readiness phases run, export the workbook from the dashboard report/export panel:

- Profile: `technology_readiness_workbook`
- Format: `xlsx`

Direct API form:

```powershell
Invoke-WebRequest "http://localhost:8001/projects/<project_id>/export?profile=technology_readiness_workbook&format=xlsx" -OutFile technology-readiness-workbook.xlsx
```

Do not commit generated XLSX files, ZIP archives, PDFs, SQLite files, `.pytest_cache`, `__pycache__`, `upload_store`, or local runtime artifacts.

## Interpret The Decision Layer

Stage-gate decision:

- `proceed` means the deterministic evidence requirements for the next TRL gate are present.
- `proceed_with_conditions` means technical evidence may be sufficient, but a condition such as missing IP review remains.
- `hold` means required evidence is missing and advancement is not supported.
- `stop` is reserved for cases where the operator determines the pathway should not continue.

Claim ledger:

- `fact` claims require supplied evidence IDs.
- `inference` claims are evidence-backed but still operator-reviewed.
- `hypothesis` claims need validation before external reliance.
- "Why not higher" limitations explain why the next TRL is not justified.
- IP-related claims remain preliminary unless explicit `ip_review` evidence is present; specialist review is required.

Go/no-go checklist:

- Treat it as a review aid, not an autonomous decision.
- Resolve blocking evidence gaps before claiming advancement.
- Keep legal, IP, safety, regulatory, and transfer decisions with the responsible specialists and operator.
