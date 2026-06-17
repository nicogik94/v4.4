# Release Readiness Checklist

This checklist is for local/operator-first release gates for v4/v5 MAS. MAS is a controlled, operator-led decision-analysis engine. It is not a chatbot, not BI, not public SaaS, not multi-tenant, and it does not have a public-user account system.

Use this checklist before treating a repo state as ready for:

- local demo readiness
- internal release review readiness
- future deployment or security review readiness

This checklist is not deployment automation and does not prove public SaaS readiness.

## Source Evidence

- `README.md` documents local API startup with `uvicorn api:app --reload`.
- `api.py` defines unauthenticated `GET /health` and `GET /runtime/preflight`.
- `runtime/preflight.py` returns `operator_only`, `checks.public_exposure`, and `checks.operator_auth`.
- `docs/runbooks/operator_auth_smoke_runbook.md` documents the protected control-plane smoke path and expected auth outcomes.
- `scripts/operator_auth_smoke.sh` is an optional operator-invoked localhost smoke helper. Do not run it unless the operator explicitly chooses that check.
- `scripts/wave_verify.sh` runs `git diff --check`, pytest, and `scripts/wave_clean_artifacts.sh`.
- `ai_context/v4_v5_current_progress.md` records the current local/operator-first posture and the current verified test baseline.

## Preconditions

Purpose: confirm the review is being run from the intended branch, at a known commit, with a local/operator-first posture.

Command:

```bash
git branch --show-current
git log -1 --oneline
git status --short
```

Expected pass signal:

- Branch is the expected release-review branch.
- `git log -1 --oneline` records the commit being reviewed.
- `git status --short` is empty before release evidence capture, or contains only explicitly intended release docs before final cleanup.
- The operator confirms this is a local/operator-first review, not a public deployment approval.

Fail action:

- Stop the release review.
- Resolve branch mismatch, unknown commit state, or unexpected working tree changes before continuing.

Release gate impact:

- Unexpected branch, unknown commit, or dirty state is `NO-GO`.

## Repository And Git Hygiene Checks

Purpose: prevent accidental release review with forbidden files, generated artifacts, or local-only edits.

Command:

```bash
git status --short
git branch --show-current
git log --oneline -5
```

Expected pass signal:

- Status is clean, or only explicitly reviewed docs-only release-readiness files are present.
- Recent history matches the intended review base.

Fail action:

- Stop and inspect every unexpected path.
- Do not use `git clean`.
- Do not proceed with source, runtime, auth, Docker, workflow, prompt, exporter, generated artifact, or local compose edits unless they are part of a separate approved wave.

Release gate impact:

- Unexpected modified or untracked files are `NO-GO` until resolved.

Forbidden-file check:

```bash
git status --short | awk '
  /scenario_shadow\.sqlite3/ ||
  /__pycache__\// ||
  /\.pyc$/ ||
  /(^|[[:space:]])upload_store\// ||
  /\.zip$/ ||
  /(generated|exports?|artifact).*\.(pdf|docx|xlsx|json|zip)$/ ||
  /docker-compose(\.override)?\.yml$/ ||
  /(^|[[:space:]])Dockerfile$/ {
    print
  }
'
```

Expected pass signal:

- The command prints nothing.

Fail action:

- Stop the review.
- For tracked test artifacts only, run `scripts/wave_clean_artifacts.sh` and re-check status.
- For any other path, inspect and resolve deliberately; do not delete untracked files with `git clean`.
- Local Docker Compose edits are allowed only when explicitly intended and reviewed for that release gate.

Release gate impact:

- Any forbidden-file hit is `NO-GO` until cleared or formally accepted as an explicit, reviewed caveat.

## Test And Cleanup Checks

Purpose: verify the current repo state with the standard local test gate and artifact cleanup.

Command:

```bash
scripts/wave_verify.sh
```

Expected pass signal:

- `git diff --check` passes.
- Full pytest suite passes.
- Expected current baseline shape is similar to:

```text
696 passed, 1 warning, 67 subtests passed
```

- `scripts/wave_verify.sh` runs `scripts/wave_clean_artifacts.sh` automatically on exit and prints final `git status --short`.

Fail action:

- Stop the release review.
- Keep the failing output as evidence.
- Fix the failure in a separate scoped change or send it to the owner for triage.
- Re-run the full verification after any fix.

Release gate impact:

- Test failure is `NO-GO`.
- Cleanup failure is `NO-GO` unless a human reviewer explicitly accepts a known non-release artifact caveat.

## Local Runtime Startup Check

Purpose: confirm the API can be started for local review without public binding.

Command:

```bash
export BASE_URL="http://127.0.0.1:8000"
uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

If using the repo virtualenv directly:

```bash
export BASE_URL="http://127.0.0.1:8000"
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Expected pass signal:

- Runtime starts on loopback.
- No public host binding is required.
- No Docker deployment changes are required for this checklist.

Fail action:

- Stop runtime review.
- Capture the startup error.
- Do not switch to public binding to make the smoke pass.

Release gate impact:

- Local startup failure is `NO-GO` for local demo readiness.
- It can be `GO WITH CAVEATS` for docs-only internal review only if the failure is recorded and runtime demo is out of scope.

## Health Check

Purpose: verify the lightweight health endpoint is reachable without auth.

Command:

```bash
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
curl -sS -o /tmp/mas-health.json -w "%{http_code}\n" "$BASE_URL/health"
python3 - <<'PY'
import json

with open("/tmp/mas-health.json", "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print({
    "status": payload.get("status"),
    "version": payload.get("version"),
    "git_sha": payload.get("git_sha"),
    "persistence": payload.get("persistence"),
    "tracing": payload.get("tracing"),
})
assert payload.get("status") == "ok"
PY
```

Expected pass signal:

- HTTP status is `200`.
- JSON includes `status`, `version`, `git_sha`, `persistence`, and `tracing`.
- `status` is `ok`.
- No operator key is required.

Fail action:

- Stop local runtime review.
- Capture the HTTP status and sanitized response.
- Check that the server is running on the expected loopback URL.

Release gate impact:

- Health failure is `NO-GO` for local demo readiness.

## Runtime Preflight Check

Purpose: verify the operator-local runtime diagnostic and understand any degraded local dependencies.

Command:

```bash
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
curl -fsS "$BASE_URL/runtime/preflight" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
public_exposure = payload["checks"]["public_exposure"]
operator_auth = payload["checks"]["operator_auth"]
print({
    "status": payload["status"],
    "operator_only": payload["operator_only"],
    "public_exposure_status": public_exposure["status"],
    "public_exposure_intent": public_exposure["public_exposure_intent"],
    "host_binding": public_exposure["host_binding"]["classification"],
    "operator_auth_status": operator_auth["status"],
    "operator_auth_required": operator_auth["required"],
    "operator_auth_configured": operator_auth["configured"],
    "operator_auth_header": operator_auth["header"],
})
assert payload["operator_only"] is True
'
```

Expected pass signal:

- HTTP status is `200`.
- `operator_only` is `true`.
- `checks.public_exposure.status` is understood and reviewed.
- `checks.operator_auth.status`, `required`, `configured`, and `header` are understood and reviewed.
- Overall preflight may be `degraded` in a minimal local environment when optional local dependencies are missing. That can be acceptable only when the degraded checks are understood, documented, and not related to public exposure or auth misconfiguration.

Fail action:

- If preflight status is `fail`, stop runtime release review.
- If public exposure is flagged, follow the public exposure interlock below.
- If operator auth is required but not configured, set the key safely or mark auth smoke as `NO-GO`.

Release gate impact:

- Public exposure failure is `NO-GO`.
- Auth misconfiguration is `NO-GO` for authenticated runtime review.
- Known optional dependency degradation can be `GO WITH CAVEATS` only when documented.

## Operator Auth Posture Check

Purpose: verify protected write/control-plane auth posture without printing raw secrets.

References:

- `docs/runbooks/operator_auth_smoke_runbook.md`
- `scripts/operator_auth_smoke.sh`

Optional operator-invoked command:

```bash
export MAS_REQUIRE_OPERATOR_AUTH=true
export MAS_OPERATOR_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
MAS_SMOKE_BASE_URL="http://127.0.0.1:8000" scripts/operator_auth_smoke.sh
```

Expected pass signal:

- `MAS_REQUIRE_OPERATOR_AUTH=true` is set.
- `MAS_OPERATOR_API_KEY` is set but never echoed.
- Base URL is localhost or loopback only.
- Missing key returns `401`.
- Invalid key returns `401`.
- Valid key passes auth and reaches the expected application-level missing-project `404`.

Fail action:

- Stop authenticated runtime review.
- Do not print the raw key.
- Do not use `set -x`.
- Re-check the env, localhost URL, and running server.

Release gate impact:

- Required auth smoke failure is `NO-GO` for authenticated local demo readiness.
- If auth smoke is not run, release review can be `GO WITH CAVEATS` only when the evidence packet states it was not run.

## Public Exposure Interlock Check

Purpose: prevent treating this local/operator-first system as public SaaS or multi-tenant infrastructure.

Command:

```bash
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
curl -fsS "$BASE_URL/runtime/preflight" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
public_exposure = payload["checks"]["public_exposure"]
print({
    "status": public_exposure["status"],
    "operator_only": public_exposure["operator_only"],
    "public_exposure_intent": public_exposure["public_exposure_intent"],
    "host_binding": public_exposure["host_binding"],
    "message": public_exposure["message"],
})
assert public_exposure["operator_only"] is True
'
```

Expected pass signal:

- `operator_only` is `true`.
- Local/default or reviewed loopback posture is confirmed.
- Public exposure intent is absent for local demo or internal release review.

Fail action:

- Stop release review on public exposure intent, public host binding, or unexpected non-local binding.
- Require human review before any public exposure, deployment, auth, security, runtime-control, CORS, host binding, or multi-tenant change.
- Do not reinterpret operator auth as full public hardening.

Release gate impact:

- Any unreviewed public exposure signal is `NO-GO`.
- Any proposed public deployment requires a separate security/design review and is outside this checklist.

## Release Evidence Packet

Purpose: capture enough evidence for a human reviewer without overclaiming readiness.

Command:

```bash
{
  echo "commit:"
  git log -1 --oneline
  echo
  echo "branch:"
  git branch --show-current
  echo
  echo "status:"
  git status --short
} > /tmp/mas-release-evidence.txt
```

Capture manually in the review notes:

- commit SHA from `git log -1 --oneline`
- `git status --short`
- `scripts/wave_verify.sh` result summary
- `/health` result summary
- `/runtime/preflight` result summary
- operator auth smoke result, if run
- known caveats, including optional dependency degradation or skipped smoke checks
- explicit statement that this is local/operator-first readiness, not public SaaS readiness

Expected pass signal:

- Evidence packet identifies the exact commit and all gate results.
- No raw `MAS_OPERATOR_API_KEY` appears in evidence.

Fail action:

- Redact secrets immediately if accidentally captured.
- Rebuild the evidence packet from sanitized command output.

Release gate impact:

- Missing evidence is `GO WITH CAVEATS` for internal review and `NO-GO` for local demo signoff.
- Secret leakage in evidence is `NO-GO`.

## Go / No-Go Decision Table

| Decision | Exact conditions | Fail action and escalation | Release gate impact |
| --- | --- | --- | --- |
| `GO` | Clean Git state; expected branch and commit captured; no forbidden files; `scripts/wave_verify.sh` passes; local loopback startup works; `/health` returns unauthenticated `200` with `status=ok`; `/runtime/preflight` is reviewed with no public exposure failure; operator auth smoke passes if auth is in scope; evidence packet is complete and sanitized. | Proceed to human release review notes. Keep scope local/operator-first. | Ready for local demo or internal release review. Not public SaaS readiness. |
| `GO WITH CAVEATS` | Tests pass and no forbidden files are present, but a non-blocking local caveat exists, such as optional dependency degradation, skipped operator auth smoke, or runtime startup out of scope for docs-only review. Caveat is documented in the evidence packet and accepted by the reviewer. | Record caveat, owner, and follow-up. Do not expand scope. Escalate if the caveat touches auth, public exposure, runtime control, security, or deployment. | Allowed only for internal review or limited local demo where the caveat is visible and accepted. |
| `NO-GO` | Wrong branch; unknown commit; unexpected dirty state; forbidden files changed; test failure; cleanup failure; local runtime cannot start for a local demo; `/health` fails; preflight `fail`; unreviewed public exposure; operator auth misconfigured when auth is required; raw secret appears in output or evidence. | Stop. Capture sanitized failure details. Assign owner. Fix in a scoped wave or request security/design review for auth, public exposure, deployment, or runtime-control changes. | Not ready for local demo, internal release signoff, or future deployment/security review package. |

## Final Human Review Reminder

This checklist supports an operator review gate. It does not change behavior, does not deploy anything, and does not certify public hardening. Any public exposure, account system, multi-tenancy, security boundary, or deployment path needs a separate design and security review before implementation or release.
