"""Bounded OpenAI provider preflight for paid evaluation jobs.

The probe mirrors the material GPT-5-family request shape used by the runtime,
while keeping credentials in the workflow environment and diagnostics narrowly
allowlisted.  Tests inject fake clients; this module is the only executable
preflight implementation used by the workflow.

``OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS`` means the configured model produced
**usable visible text** under the preflight request shape -- not merely that the
SDK call returned without raising.  Usable is deliberately narrow: a visible
content value that is a ``str`` whose ``strip()`` is non-empty.  The probe
validates provider/model output capability, so it never inspects semantic
correctness and never requires any particular reply text.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


PROBE_MODELS = ("gpt-5-mini", "gpt-5")
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MAX_RETRIES = 0
PROBE_MAX_COMPLETION_TOKENS = 512
PROBE_TEMPERATURE = 1
PROBE_SYSTEM_PROMPT = "You are a concise assistant."
PROBE_USER_PROMPT = "Reply with OK."
DIAGNOSTIC_MESSAGE_MAX_CHARS = 500

# Closed failure vocabulary.  A local equivalent of the runtime's taxonomy is
# used deliberately: importing the product's classifier would couple this
# eval-only harness to a certified product module for two string constants, and
# the preflight classifies a raw SDK response rather than a GatewayResponse.
CATEGORY_OUTPUT_TOKEN_EXHAUSTED = "output_token_exhausted"
CATEGORY_EMPTY_PROVIDER_OUTPUT = "empty_provider_output"
CATEGORY_MALFORMED_RESPONSE = "malformed_response"

# Closed content-status vocabulary.  ``malformed`` covers a present but
# non-string content value, which would otherwise have to be misreported.
CONTENT_MISSING = "missing"
CONTENT_NONE = "none"
CONTENT_EMPTY = "empty"
CONTENT_WHITESPACE = "whitespace"
CONTENT_NONEMPTY = "nonempty"
CONTENT_MALFORMED = "malformed"

REFUSAL_PRESENT = "present"
REFUSAL_ABSENT = "absent"
REFUSAL_UNKNOWN = "unknown"

# Chat Completions signals token exhaustion with exactly ``length``.  No alias
# is accepted; inventing one would assert provider semantics this probe has not
# observed.
FINISH_REASON_LENGTH = "length"
FINISH_REASON_ABSENT = ""
FINISH_REASON_OTHER = "other"
KNOWN_FINISH_REASONS = frozenset(
    {
        "stop",
        FINISH_REASON_LENGTH,
        "content_filter",
        "tool_calls",
        "function_call",
    }
)

_MISSING = object()

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


@dataclass(frozen=True)
class OutputAssessment:
    """Bounded verdict on a preflight response.

    Every field is drawn from a closed vocabulary, so an assessment can be
    rendered to the workflow log without any provider-controlled bytes.
    """

    usable: bool
    category: str
    finish_reason: str
    content_status: str
    refusal_status: str


def _field(container: object, name: str) -> Any:
    """Attribute-or-key lookup that never raises.

    Returns ``_MISSING`` when the field is absent or unreadable, which keeps
    "absent" distinguishable from a genuine ``None`` value.
    """

    try:
        value = getattr(container, name, _MISSING)
    except Exception:
        # A property that raises reaches here rather than returning the default.
        return _MISSING
    if value is not _MISSING:
        return value
    if isinstance(container, Mapping):
        try:
            if name in container:
                return container[name]
        except Exception:
            return _MISSING
    return _MISSING


def _normalized_finish_reason(value: object) -> str:
    """Collapse the finish reason onto a closed vocabulary.

    Any unrecognized value becomes ``other`` so no provider-controlled text can
    reach the diagnostic line.
    """

    if value is _MISSING or value is None:
        return FINISH_REASON_ABSENT
    if not isinstance(value, str):
        return FINISH_REASON_OTHER
    normalized = value.strip().lower()
    if not normalized:
        return FINISH_REASON_ABSENT
    if normalized in KNOWN_FINISH_REASONS:
        return normalized
    return FINISH_REASON_OTHER


def _content_status(content: object) -> str:
    if content is _MISSING:
        return CONTENT_MISSING
    if content is None:
        return CONTENT_NONE
    if not isinstance(content, str):
        return CONTENT_MALFORMED
    if content == "":
        return CONTENT_EMPTY
    if content.strip() == "":
        return CONTENT_WHITESPACE
    return CONTENT_NONEMPTY


def _refusal_status(message: object) -> str:
    refusal = _field(message, "refusal")
    if refusal is _MISSING:
        return REFUSAL_UNKNOWN
    if refusal is None:
        return REFUSAL_ABSENT
    if isinstance(refusal, str):
        return REFUSAL_PRESENT if refusal.strip() else REFUSAL_ABSENT
    return REFUSAL_PRESENT


def _malformed(
    finish_reason: str,
    content_status: str,
    refusal_status: str,
) -> OutputAssessment:
    return OutputAssessment(
        usable=False,
        category=CATEGORY_MALFORMED_RESPONSE,
        finish_reason=finish_reason,
        content_status=content_status,
        refusal_status=refusal_status,
    )


def _assess_output(response: object) -> OutputAssessment:
    choices = _field(response, "choices")
    if choices is _MISSING or choices is None:
        return _malformed(
            FINISH_REASON_ABSENT,
            CONTENT_MISSING,
            REFUSAL_UNKNOWN,
        )

    try:
        # Covers empty sequences and non-subscriptable values alike.
        first_choice = choices[0]
    except Exception:
        return _malformed(
            FINISH_REASON_ABSENT,
            CONTENT_MISSING,
            REFUSAL_UNKNOWN,
        )

    finish_reason = _normalized_finish_reason(_field(first_choice, "finish_reason"))
    message = _field(first_choice, "message")
    if message is _MISSING or message is None:
        return _malformed(finish_reason, CONTENT_MISSING, REFUSAL_UNKNOWN)

    refusal_status = _refusal_status(message)
    content_status = _content_status(_field(message, "content"))

    if content_status == CONTENT_NONEMPTY:
        # Visible text proves output capability even when truncated, and even
        # when a refusal field is also populated.  This probe deliberately does
        # not adjudicate refusals; see the module docstring.
        return OutputAssessment(
            usable=True,
            category="",
            finish_reason=finish_reason,
            content_status=content_status,
            refusal_status=refusal_status,
        )

    if content_status in (CONTENT_MISSING, CONTENT_MALFORMED):
        # A structurally broken response is reported as such even when the
        # finish reason would otherwise suggest exhaustion.
        return _malformed(finish_reason, content_status, refusal_status)

    category = (
        CATEGORY_OUTPUT_TOKEN_EXHAUSTED
        if finish_reason == FINISH_REASON_LENGTH
        else CATEGORY_EMPTY_PROVIDER_OUTPUT
    )
    return OutputAssessment(
        usable=False,
        category=category,
        finish_reason=finish_reason,
        content_status=content_status,
        refusal_status=refusal_status,
    )


def assess_output(response: object) -> OutputAssessment:
    """Classify a preflight response, failing closed on any inspection error."""

    try:
        return _assess_output(response)
    except Exception:
        return _malformed(
            FINISH_REASON_ABSENT,
            CONTENT_MISSING,
            REFUSAL_UNKNOWN,
        )


def format_unusable_output(model: str, assessment: OutputAssessment) -> str:
    try:
        safe_model = _sanitized_token(model, max_chars=80)
        return " ".join(
            (
                "OPENAI_PROVIDER_PREFLIGHT=FAIL",
                f"model={safe_model}",
                f"reason={assessment.category}",
                f"finish_reason={assessment.finish_reason}",
                f"content={assessment.content_status}",
                f"refusal={assessment.refusal_status}",
            )
        )
    except Exception:
        safe_model = model if type(model) is str and model in PROBE_MODELS else ""
        return (
            "OPENAI_PROVIDER_PREFLIGHT=FAIL "
            f"model={safe_model} reason={CATEGORY_MALFORMED_RESPONSE} "
            "finish_reason= content=missing refusal=unknown"
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
            response = client.chat.completions.create(**probe_request(model))
        except Exception as exc:
            print(format_failure(model, exc), file=stderr)
            return False

        assessment = assess_output(response)
        if not assessment.usable:
            print(format_unusable_output(model, assessment), file=stderr)
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
