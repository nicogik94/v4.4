# Automation ROI Canonical Demo Canary Checklist

Use this checklist after running `TEST Aut-ROI Canonical Demo` and generating
the expected exports. The demo uses synthetic demo data only and includes no
private or client data.

## Runtime And Workflow

- [ ] Runtime/preflight pass.
- [ ] All phases complete: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- [ ] Evidence maturity is `Partial evidence`.
- [ ] Uploaded files parsed.
- [ ] Citation markers resolved.

## Client-Facing Export Canaries

- [ ] No raw markdown table artifacts.
- [ ] No rough client wording.
- [ ] `report` PDF generated and reviewed.
- [ ] `client_dossier` PDF generated and reviewed.
- [ ] Validate before client delivery.

## Monitoring XLSX Canaries

- [ ] Client XLSX expected sheets exist.
- [ ] Operator XLSX expected sheets exist.
- [ ] Client XLSX does not expose `hypothesis 5`.
- [ ] Client XLSX does not expose `hypothesis 9`.
- [ ] Client XLSX does not expose `architecture hypothesis`.
- [ ] Monitoring metric present.
- [ ] Monitoring threshold present.
- [ ] Monitoring owner present.

## Operator Boundary

- [ ] `operator_dossier` PDF remains operator-only.
- [ ] `operator_monitoring_template` XLSX remains operator-only.
- [ ] Evidence gaps are still visible before implementation.
- [ ] Human review required for high-risk/finance automation.
