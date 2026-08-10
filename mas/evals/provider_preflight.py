"""Bounded OpenAI provider preflight for paid evaluation jobs.

The probe mirrors the material GPT-5-family request shape used by the runtime,
while keeping credentials in the workflow environment and diagnostics narrowly
allowlisted.  Tests inject fake clients; this module is the only executable
preflight implementation used by the workflow.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from typing import Any


PROBE_MODELS = ("gpt-5-mini", "gpt-5")
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MAX_RETRIES = 0
PROBE_MAX_COMPLETION_TOKENS = 512
PROBE_TEMPERATURE = 1
PROBE_SYSTEM_PROMPT = "You are a concise assistant."
PROBE_USER_PROMPT = "Reply with OK."
DIAGNOSTIC_MESSAGE_MAX_CHARS = 500

_AUTHORIZATION_RE = re.compile(
    r"(?i)\bauthorization\b\s*[\"']?\s*[:=]\s*[^\r\n]+"
)
_API_KEY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:openai[_ -]?api[_ -]?key|api[_ -]?key)\b"
    r"\s*[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>[A-Z][A-Z0-9_]{2,})\s*[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;\"']+")
_OPENAI_SECRET_RE = re.compile(r"(?i)\bsk-[a-z0-9._-]{4,}\b")
_FILE_URI_RE = re.compile(r"(?i)\bfile://[^\s,;\"']+")
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:[\\/][^\s,;\"']+")
_UNC_PATH_RE = re.compile(r"\\\\[^\\\s]+\\[^\s,;\"']+")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![:/A-Za-z0-9])/(?:[^/\s,;\"']+/)*[^/\s,;\"']+"
)
_TILDE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])~[\\/][^\s,;\"']+")
_RELATIVE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])\.\.?[\\/][^\s,;\"']+"
)
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._:/-]+")


def _safe_getattr(value: object, name: str) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        return None


def _safe_mapping_get(mapping: Mapping[str, Any], key: str) -> Any:
    try:
        return mapping.get(key)
    except Exception:
        return None


def _error_body(exc: BaseException) -> Mapping[str, Any]:
    body = _safe_getattr(exc, "body")
    if not isinstance(body, Mapping):
        return {}
    nested = _safe_mapping_get(body, "error")
    if isinstance(nested, Mapping):
        return nested
    return body


def _redact(text: str) -> str:
    for prompt in (PROBE_SYSTEM_PROMPT, PROBE_USER_PROMPT):
        text = text.replace(prompt, "[REDACTED_PROMPT]")
    text = _AUTHORIZATION_RE.sub("Authorization=[REDACTED]", text)
    text = _API_KEY_ASSIGNMENT_RE.sub("API_KEY=[REDACTED]", text)
    text = _ENV_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('name')}=[REDACTED]",
        text,
    )
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _OPENAI_SECRET_RE.sub("[REDACTED]", text)
    text = _FILE_URI_RE.sub("[REDACTED_PATH]", text)
    text = _WINDOWS_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _UNC_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _TILDE_PATH_RE.sub("[REDACTED_PATH]", text)
    return _RELATIVE_PATH_RE.sub("[REDACTED_PATH]", text)


def _sanitized_scalar(value: object, *, max_chars: int) -> str:
    if value is None or not isinstance(value, (str, int, float, bool)):
        return ""
    try:
        text = _redact(str(value))
        text = text.encode("ascii", errors="replace").decode("ascii")
        text = "".join(
            character if 32 <= ord(character) <= 126 else " "
            for character in text
        )
        text = _WHITESPACE_RE.sub(" ", text).strip()
        # Keep JSON rendering one character per retained message character.
        text = text.replace("\\", "/").replace('"', "'")
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _sanitized_token(value: object, *, max_chars: int) -> str:
    try:
        text = _sanitized_scalar(value, max_chars=max_chars)
        return _TOKEN_UNSAFE_RE.sub("_", text).strip("_")
    except Exception:
        return ""


def _empty_diagnostic_fields() -> dict[str, str]:
    return {
        "status": "",
        "error_type": "",
        "code": "",
        "param": "",
        "request_id": "",
        "message": "",
    }


def diagnostic_fields(exc: BaseException) -> dict[str, str]:
    """Extract only known scalar SDK fields from an exception.

    ``openai-python`` flattens the provider's outer ``error`` object into
    ``exc.body`` today.  The nested form is also accepted for compatibility,
    but the raw body, response, request, headers, and exception rendering are
    never emitted.
    """

    try:
        body = _error_body(exc)
        return {
            "status": _sanitized_token(
                _safe_getattr(exc, "status_code"),
                max_chars=16,
            ),
            "error_type": _sanitized_token(
                _safe_getattr(exc, "type")
                or _safe_mapping_get(body, "type"),
                max_chars=120,
            ),
            "code": _sanitized_token(
                _safe_getattr(exc, "code")
                or _safe_mapping_get(body, "code"),
                max_chars=120,
            ),
            "param": _sanitized_token(
                _safe_getattr(exc, "param")
                or _safe_mapping_get(body, "param"),
                max_chars=120,
            ),
            "request_id": _sanitized_token(
                _safe_getattr(exc, "request_id"),
                max_chars=160,
            ),
            "message": _sanitized_scalar(
                _safe_mapping_get(body, "message"),
                max_chars=DIAGNOSTIC_MESSAGE_MAX_CHARS,
            ),
        }
    except Exception:
        return _empty_diagnostic_fields()


def format_failure(model: str, exc: BaseException) -> str:
    try:
        fields = diagnostic_fields(exc)
        exception_name = _sanitized_token(type(exc).__name__, max_chars=80)
        safe_model = _sanitized_token(model, max_chars=80)
        return " ".join(
            (
                "OPENAI_PROVIDER_PREFLIGHT=FAIL",
                f"model={safe_model}",
                f"type={exception_name}",
                f"status={fields['status']}",
                f"error_type={fields['error_type']}",
                f"code={fields['code']}",
                f"param={fields['param']}",
                f"request_id={fields['request_id']}",
                f"message={json.dumps(fields['message'], ensure_ascii=True)}",
            )
        )
    except Exception:
        safe_model = model if type(model) is str and model in PROBE_MODELS else ""
        return (
            "OPENAI_PROVIDER_PREFLIGHT=FAIL "
            f"model={safe_model} type=Exception "
            "status= error_type= code= param= request_id= message=\"\""
        )


def probe_request(model: str) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": PROBE_SYSTEM_PROMPT},
            {"role": "user", "content": PROBE_USER_PROMPT},
        ],
        "max_completion_tokens": PROBE_MAX_COMPLETION_TOKENS,
        "temperature": PROBE_TEMPERATURE,
    }


def run_preflight(client: object, *, stdout=None, stderr=None) -> bool:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    for model in PROBE_MODELS:
        try:
            client.chat.completions.create(**probe_request(model))
        except Exception as exc:
            print(format_failure(model, exc), file=stderr)
            return False

        print(
            f"OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model={model}",
            file=stdout,
        )

    print("OPENAI_PROVIDER_PREFLIGHT=PASS", file=stdout)
    return True


def create_openai_client(api_key: str, *, client_factory=None):
    # The SDK's debug mode logs request options, including message content.
    # Preflight diagnostics are intentionally allowlisted, so never inherit it.
    os.environ.pop("OPENAI_LOG", None)
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    return client_factory(
        api_key=api_key,
        timeout=PROBE_TIMEOUT_SECONDS,
        max_retries=PROBE_MAX_RETRIES,
    )


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print(
            "OPENAI_PROVIDER_PREFLIGHT=FAIL reason=missing_key",
            file=sys.stderr,
        )
        return 1

    try:
        client = create_openai_client(api_key)
    except Exception as exc:
        print(format_failure("", exc), file=sys.stderr)
        return 1
    return 0 if run_preflight(client) else 1


if __name__ == "__main__":
    raise SystemExit(main())
