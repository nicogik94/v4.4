"""Read-only local runtime diagnostics for the demo workflow."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
CHECKS = ("/health", "/runtime/preflight", "/runtime/release-readiness")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local demo diagnostics.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Local API base URL.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        base_url = _validated_local_base_url(args.base_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: dict[str, Any] = {}
    exit_code = 0
    for path in CHECKS:
        url = urllib.parse.urljoin(base_url + "/", path.lstrip("/"))
        ok, payload = _get_json(url, timeout=args.timeout)
        results[path] = payload
        if not ok:
            exit_code = 1

    print(json.dumps(results, indent=2, sort_keys=True))
    return exit_code


def _validated_local_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base URL must use http or https")
    if parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("base URL must be localhost, 127.0.0.1, or ::1")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not include credentials")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _get_json(url: str, *, timeout: float) -> tuple[bool, dict[str, Any]]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"raw": body[:500]}
            status = int(getattr(response, "status", 0) or 0)
            ok = 200 <= status < 300
            return ok, {"ok": ok, "http_status": status, "response": parsed}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, {"ok": False, "http_status": exc.code, "error": body[:500]}
    except Exception as exc:
        return False, {"ok": False, "error": exc.__class__.__name__}


if __name__ == "__main__":
    raise SystemExit(main())
