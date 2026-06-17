# Operator Auth Local Smoke Runbook

This is a local-only startup and smoke procedure for the operator-authenticated API path. It does not change app behavior, auth behavior, endpoints, Docker, or runtime preflight.

## Evidence

- Local API startup command comes from `README.md`: `uvicorn api:app --reload`.
- `GET /health` is open and returns `status`, `version`, `git_sha`, `persistence`, and `tracing` in `api.py`.
- `GET /runtime/preflight` is open and returns the runtime preflight payload from `api.py`.
- The operator auth header is `X-MAS-Operator-Key` from `config.py`.
- The protected control-plane endpoint is `POST /projects/{project_id}/run` from `api.py`.
- `tests/test_operator_auth.py` proves the protected route returns:
  - missing key: `401`
  - invalid key: `401`
  - valid key with a dummy missing project: auth passes, then app logic returns `404`
- `tests/test_runtime_preflight.py` proves preflight reports `checks.operator_auth.required`, `checks.operator_auth.configured`, and `checks.operator_auth.header` without serializing the raw key.

## Start Local Runtime

Run from the repository root. Bind uvicorn to loopback only.

```bash
export MAS_REQUIRE_OPERATOR_AUTH=true
export MAS_OPERATOR_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BASE_URL="http://127.0.0.1:8000"

uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

If you use the repo virtualenv directly, the equivalent startup is:

```bash
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Do not echo `MAS_OPERATOR_API_KEY`, do not use `set -x`, and do not bind this smoke run to a public interface.

## Health Check Without Auth

In a shell that can reach the local server:

```bash
curl -fsS "$BASE_URL/health" | python3 -m json.tool
```

Expected:

- HTTP `200`
- JSON `status` is `ok`
- `persistence` is `memory` for local fallback or `postgres` when Postgres persistence is active
- no operator key is required

## Runtime Preflight

Call preflight without auth and inspect only the auth posture fields:

```bash
curl -fsS "$BASE_URL/runtime/preflight" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
auth = payload["checks"]["operator_auth"]
print({
    "runtime_status": payload["status"],
    "operator_only": payload["operator_only"],
    "operator_auth_status": auth["status"],
    "required": auth["required"],
    "configured": auth["configured"],
    "header": auth["header"],
})
assert auth["status"] == "ok"
assert auth["required"] is True
assert auth["configured"] is True
assert auth["header"] == "X-MAS-Operator-Key"
'
```

The overall preflight `status` can be `degraded` in a minimal local environment if optional dependencies are not configured. For this smoke, the required check is that `checks.operator_auth` is `ok`, `required`, and `configured`.

## Protected Endpoint Auth Smoke

Use the protected control-plane endpoint `POST /projects/{project_id}/run`. Use a UUID-shaped project id that does not exist so a valid key reaches application logic and returns the safe missing-project result.

```bash
SMOKE_PROJECT_ID="00000000-0000-4000-8000-000000000001"
SMOKE_PATH="/projects/$SMOKE_PROJECT_ID/run"
```

Missing key should fail before application logic:

```bash
curl -sS -o /tmp/mas-auth-missing.json -w "%{http_code}\n" \
  -X POST "$BASE_URL$SMOKE_PATH"
```

Expected: `401`.

Invalid key should also fail before application logic:

```bash
curl -sS -o /tmp/mas-auth-invalid.json -w "%{http_code}\n" \
  -H "X-MAS-Operator-Key: invalid-operator-key" \
  -X POST "$BASE_URL$SMOKE_PATH"
```

Expected: `401`.

Valid key should pass auth and reach the expected missing-project result. This command passes the key through curl config stdin so the raw key is not printed by the command:

```bash
printf 'header = "X-MAS-Operator-Key: %s"\n' "$MAS_OPERATOR_API_KEY" | \
  curl --config - -sS -o /tmp/mas-auth-valid.json -w "%{http_code}\n" \
    -X POST "$BASE_URL$SMOKE_PATH"
```

Expected: `404` with JSON detail `Project not found`. The `404` proves the valid key passed auth and the request reached application-level project lookup.

Remove temporary response files when done:

```bash
rm -f /tmp/mas-auth-missing.json /tmp/mas-auth-invalid.json /tmp/mas-auth-valid.json
```

## Optional Script

If `scripts/operator_auth_smoke.sh` is present, it automates the checks above against a running local server:

```bash
MAS_SMOKE_BASE_URL="${BASE_URL:-http://127.0.0.1:8000}" scripts/operator_auth_smoke.sh
```

The script requires `curl`, `python3`, `MAS_REQUIRE_OPERATOR_AUTH=true`, and a non-empty `MAS_OPERATOR_API_KEY` in the invoking shell. It refuses non-local base URLs and does not print the raw key.

## Stop Runtime Cleanly

If uvicorn is running in the foreground, press `Ctrl-C` once and wait for shutdown to complete.

If uvicorn was started in the background, stop the recorded process id:

```bash
kill "$SERVER_PID"
wait "$SERVER_PID" || true
```

Clear the local smoke key after shutdown:

```bash
unset MAS_OPERATOR_API_KEY MAS_REQUIRE_OPERATOR_AUTH BASE_URL SMOKE_PROJECT_ID SMOKE_PATH
```
