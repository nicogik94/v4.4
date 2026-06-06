# v5 Runtime Foundation Demo Readiness Checklist

Use this before a local demo of the v5 runtime foundation demo workflow. This
does not certify a fully released v5 product or public SaaS readiness.

## Repo And Tests

- [ ] Branch or tag is correct, for example `v5-runtime-foundation`.
- [ ] `git status --short` has no unintended source changes.
- [ ] `docker-compose.yml not committed`.
- [ ] No generated artifacts committed: `exports/`, `upload_store/`,
      `scenario_shadow.sqlite3`, `__pycache__/`, `.pyc`, DOCX, XLSX, PDF, or
      smoke output.
- [ ] Targeted pytest suites pass.
- [ ] Full `python -m pytest -q` passes.

## Docker And Runtime

Use Docker-discovered host port first:

```powershell
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"
curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
python ..\scripts\demo_smoke_check.py --base-url "$base"
```

`http://localhost:8000` is only the default or fallback when Docker publishes
the app on host port 8000.

- [ ] `/health` returns ok.
- [ ] `/runtime/preflight` is ok, or only degraded for an explainable local
      operator limitation.
- [ ] `/runtime/release-readiness` passes, or a blocker is understood before
      demo.

## Demo Project

- [ ] Fresh project created from an example brief.
- [ ] Optional files uploaded only when useful for the demo.
- [ ] Workflow run starts and returns a run identifier.
- [ ] Duplicate active run returns controlled `409`.
- [ ] Report phase completes.
- [ ] Client output reviewed for evidence maturity and safety.
- [ ] Operator output reviewed for diagnostics and trace.
- [ ] Monitoring XLSX opens and has stable headers.
- [ ] Client delivery package, if used, is generated outside tracked repo
      artifacts.

## Human Review And Non-Claims

- [ ] Human review remains required.
- [ ] No public SaaS readiness claim.
- [ ] No autonomous decision-making claim.
- [ ] No guaranteed causal truth claim.
- [ ] No guaranteed semantic evidence proof claim.
- [ ] Demo briefs described as examples, not first-class vertical runtime packs.
