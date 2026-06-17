#!/usr/bin/env bash
set -euo pipefail

DEFAULT_BASE_URL="http://127.0.0.1:8000"
BASE_URL="${MAS_SMOKE_BASE_URL:-$DEFAULT_BASE_URL}"
BASE_URL="${BASE_URL%/}"
PYTHON_BIN="${PYTHON:-python3}"
OPERATOR_AUTH_HEADER="X-MAS-Operator-Key"
INVALID_OPERATOR_KEY="invalid-operator-key"

die() {
  echo "operator_auth_smoke: $*" >&2
  exit 1
}

need_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    die "$command_name is required."
  fi
}

need_command curl
need_command "$PYTHON_BIN"

if [[ -z "${MAS_REQUIRE_OPERATOR_AUTH:-}" ]]; then
  die "MAS_REQUIRE_OPERATOR_AUTH=true is required in this shell."
fi

case "${MAS_REQUIRE_OPERATOR_AUTH,,}" in
  1|true|yes|on)
    ;;
  *)
    die "MAS_REQUIRE_OPERATOR_AUTH must be true for this smoke."
    ;;
esac

if [[ -z "${MAS_OPERATOR_API_KEY:-}" ]]; then
  die "MAS_OPERATOR_API_KEY is required in this shell."
fi

case "$MAS_OPERATOR_API_KEY" in
  *$'\n'*|*$'\r'*|*'"'*)
    die "MAS_OPERATOR_API_KEY contains a newline, carriage return, or double quote; use a simple local test key for this smoke."
    ;;
esac

"$PYTHON_BIN" - "$BASE_URL" <<'PY' || die "MAS_SMOKE_BASE_URL must be http(s)://localhost or loopback with no path."
import ipaddress
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
if parsed.scheme not in {"http", "https"}:
    raise SystemExit(1)
if parsed.username or parsed.password:
    raise SystemExit(1)
if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
    raise SystemExit(1)
host = parsed.hostname
if not host:
    raise SystemExit(1)
try:
    port = parsed.port
except ValueError:
    raise SystemExit(1)
if port is not None and not (1 <= port <= 65535):
    raise SystemExit(1)
try:
    address = ipaddress.ip_address(host)
except ValueError:
    local = host == "localhost" or host.endswith(".localhost")
else:
    local = address.is_loopback
if not local:
    raise SystemExit(1)
PY

SMOKE_PROJECT_ID="${MAS_SMOKE_PROJECT_ID:-}"
if [[ -z "$SMOKE_PROJECT_ID" ]]; then
  SMOKE_PROJECT_ID="$("$PYTHON_BIN" -c 'import uuid; print(uuid.uuid4())')" || die "Unable to generate smoke project id."
fi

"$PYTHON_BIN" - "$SMOKE_PROJECT_ID" <<'PY' || die "MAS_SMOKE_PROJECT_ID must be UUID-shaped."
import sys
import uuid

uuid.UUID(sys.argv[1])
PY

TMP_DIR="$(mktemp -d)" || die "Unable to create temporary directory."
trap 'rm -rf "$TMP_DIR"' EXIT

curl_status() {
  local method="$1"
  local url="$2"
  local output_path="$3"
  shift 3
  local status

  if ! status="$(curl --silent --show-error --output "$output_path" --write-out "%{http_code}" --request "$method" "$@" "$url")"; then
    return 1
  fi
  printf '%s' "$status"
}

curl_status_with_operator_key() {
  local method="$1"
  local url="$2"
  local output_path="$3"
  local status

  if ! status="$(printf 'header = "%s: %s"\n' "$OPERATOR_AUTH_HEADER" "$MAS_OPERATOR_API_KEY" |
    curl --config - --silent --show-error --output "$output_path" --write-out "%{http_code}" --request "$method" "$url")"; then
    return 1
  fi
  printf '%s' "$status"
}

expect_status() {
  local label="$1"
  local actual="$2"
  local expected="$3"

  if [[ "$actual" != "$expected" ]]; then
    die "$label expected HTTP $expected, got $actual."
  fi
  echo "$label: HTTP $actual"
}

expect_json_detail() {
  local label="$1"
  local response_path="$2"
  local expected_detail="$3"

  "$PYTHON_BIN" - "$response_path" "$expected_detail" <<'PY' || die "$label returned unexpected JSON detail."
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("detail") != sys.argv[2]:
    raise SystemExit(1)
PY
}

echo "operator_auth_smoke: using local base URL $BASE_URL"

HEALTH_BODY="$TMP_DIR/health.json"
if ! HEALTH_STATUS="$(curl_status GET "$BASE_URL/health" "$HEALTH_BODY")"; then
  die "curl failed to reach /health; is the local server running?"
fi
expect_status "GET /health" "$HEALTH_STATUS" "200"
"$PYTHON_BIN" - "$HEALTH_BODY" <<'PY' || die "GET /health did not return expected JSON status=ok."
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("status") != "ok":
    raise SystemExit(1)
print(
    "GET /health: status={status}, persistence={persistence}, tracing={tracing}".format(
        status=payload.get("status", ""),
        persistence=payload.get("persistence", ""),
        tracing=payload.get("tracing", ""),
    )
)
PY

PREFLIGHT_BODY="$TMP_DIR/preflight.json"
if ! PREFLIGHT_STATUS="$(curl_status GET "$BASE_URL/runtime/preflight" "$PREFLIGHT_BODY")"; then
  die "curl failed to reach /runtime/preflight."
fi
expect_status "GET /runtime/preflight" "$PREFLIGHT_STATUS" "200"
"$PYTHON_BIN" - "$PREFLIGHT_BODY" "$OPERATOR_AUTH_HEADER" <<'PY' || die "runtime preflight does not show operator auth required and configured."
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
auth = payload.get("checks", {}).get("operator_auth", {})
expected_header = sys.argv[2]
errors = []
if auth.get("status") != "ok":
    errors.append("operator_auth.status")
if auth.get("required") is not True:
    errors.append("operator_auth.required")
if auth.get("configured") is not True:
    errors.append("operator_auth.configured")
if auth.get("header") != expected_header:
    errors.append("operator_auth.header")
if errors:
    print("failed fields: " + ", ".join(errors), file=sys.stderr)
    raise SystemExit(1)
print(
    "GET /runtime/preflight: status={runtime_status}, operator_auth.status={auth_status}, required={required}, configured={configured}, header={header}".format(
        runtime_status=payload.get("status", ""),
        auth_status=auth.get("status", ""),
        required=auth.get("required", ""),
        configured=auth.get("configured", ""),
        header=auth.get("header", ""),
    )
)
PY

SMOKE_PATH="/projects/$SMOKE_PROJECT_ID/run"
MISSING_BODY="$TMP_DIR/missing.json"
INVALID_BODY="$TMP_DIR/invalid.json"
VALID_BODY="$TMP_DIR/valid.json"

if ! MISSING_STATUS="$(curl_status POST "$BASE_URL$SMOKE_PATH" "$MISSING_BODY")"; then
  die "curl failed during missing-key protected endpoint smoke."
fi
expect_status "POST $SMOKE_PATH without key" "$MISSING_STATUS" "401"
expect_json_detail "missing-key response" "$MISSING_BODY" "Operator authentication required"

if ! INVALID_STATUS="$(curl_status POST "$BASE_URL$SMOKE_PATH" "$INVALID_BODY" --header "$OPERATOR_AUTH_HEADER: $INVALID_OPERATOR_KEY")"; then
  die "curl failed during invalid-key protected endpoint smoke."
fi
expect_status "POST $SMOKE_PATH with invalid key" "$INVALID_STATUS" "401"
expect_json_detail "invalid-key response" "$INVALID_BODY" "Operator authentication required"

if ! VALID_STATUS="$(curl_status_with_operator_key POST "$BASE_URL$SMOKE_PATH" "$VALID_BODY")"; then
  die "curl failed during valid-key protected endpoint smoke."
fi
expect_status "POST $SMOKE_PATH with valid key" "$VALID_STATUS" "404"
expect_json_detail "valid-key response" "$VALID_BODY" "Project not found"

echo "operator_auth_smoke: valid key passed auth and reached application-level missing-project 404."
echo "operator_auth_smoke: complete."
