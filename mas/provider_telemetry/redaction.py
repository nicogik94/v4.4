"""The one sanitizing seam between untrusted provider data and durable storage.

Everything a provider returns is untrusted input. Blocker 7 of the audit —
"provider-controlled metadata and structured exception fields can persist secrets
or sensitive content" — is not solved by a denylist, because a provider is free
to echo any part of the request back inside a field typed as an identifier. It is
solved by *positive grammars*: a value is stored only if it matches an explicit
pattern for its field, and is refused otherwise.

The validation order is load-bearing and is asserted by
``test_provider_attempt_telemetry_redaction``:

1. **Type.** Only ``str`` (and ``int`` where a field is numeric). A ``dict``,
   list or arbitrary object is refused without being stringified, so structured
   exception bodies can never be flattened into a column.
2. **Raw length bound**, before any transformation, so a megabyte of input is
   never normalized or scanned.
3. **Unicode normalization (NFKC).** Fullwidth and compatibility forms are folded
   so ``ｓｋ-ant-…`` cannot evade the credential scan below.
4. **Dangerous-codepoint rejection.** Control characters, line breaks, and the
   Unicode format/invisible class (``Cf``: ZWSP, ZWNJ, RLO, word joiner …) cause
   an outright refusal. They are *rejected, never stripped* — stripping is what
   turns ``sk-<ZWSP>ant-secret`` into a grammar-conformant credential.
5. **Credential-shape scan** on the normalized text.
6. **Positive grammar** for the specific field.

There is no code path in this module that reads ``str(exc)``, ``exc.args``,
``exc.message``, a response body, or a header collection. That is pinned by a
static test over this file's own source.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from .values import (
    ABSENT,
    MISSING,
    NULL,
    VALUE_UNSUPPORTED,
    ProviderValue,
    invalid,
    redacted,
    unknown_value,
    valid,
)

# ─────────────────────────── bounds ───────────────────────────

MAX_RAW_CHARS = 512
MAX_RESPONSE_ID_CHARS = 128
MAX_MODEL_CHARS = 128
MAX_STOP_REASON_CHARS = 64
MAX_REQUEST_ID_CHARS = 128
MAX_ERROR_CATEGORY_CHARS = 64
MAX_ERROR_IDENTITY_CHARS = 256
MAX_RETRY_AFTER_CHARS = 32

# ─────────────────────────── positive grammars ───────────────────────────
#
# Each begins with an alphanumeric so a value cannot lead with a separator, and
# each admits only characters that appear in the providers' documented formats.
# None of them admit whitespace, quotes, slashes into a scheme, or "@", which is
# what keeps URL userinfo (`https://user:pass@host`) out by construction.

_RE_RESPONSE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_\-]{0,127}\Z")
_RE_MODEL = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._\-]{0,127}\Z")
_RE_STOP_REASON = re.compile(r"\A[A-Za-z][A-Za-z0-9_\-]{0,63}\Z")
_RE_REQUEST_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_\-]{0,127}\Z")
_RE_ERROR_CATEGORY = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_RE_ERROR_TOKEN = re.compile(r"\A[A-Za-z][A-Za-z0-9_.\-]{0,127}\Z")
_RE_RETRY_AFTER = re.compile(r"\A[0-9]{1,10}(\.[0-9]{1,6})?\Z")

# ─────────────────────────── credential shapes ───────────────────────────

# Each entry is ``(name, pattern)``. The names are load-bearing: they are what
# ``test_provider_attempt_telemetry_credentials`` enumerates to prove each rule
# is doing work, and what the PostgreSQL constraint mirrors — so a rule deleted
# here fails a test that names it rather than quietly widening what can be
# stored. The scan runs on NFKC-normalized text, after the invisible-codepoint
# refusal, so compatibility and zero-width evasions are already gone.
CREDENTIAL_SHAPES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # ── Provider and platform key prefixes ──
    ("anthropic_key", re.compile(r"(?i)sk-ant-")),
    ("sk_key", re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{8,}")),
    ("stripe_key", re.compile(r"(?i)\b[rs]k_(live|test)_[A-Za-z0-9]{8,}")),
    ("github_token", re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{8,}")),
    ("slack_token", re.compile(r"(?i)\bxox[baprs]-")),
    ("aws_access_key_id", re.compile(r"\b(AKIA|ASIA|AROA|AIDA)[A-Z0-9]{12,}")),
    ("google_key", re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}")),
    # ── Authorization schemes, with or without a separator run ──
    # This is the rule the audit's `Bearer_abcdefghijkl` example turns on: the
    # separator class is starred, so `Bearer_x`, `Bearer-x`, `Bearer.x`,
    # `Bearer:x`, `Bearer=x` and `Bearerx` are all caught, in any case.
    ("bearer_scheme", re.compile(r"(?i)bearer[\s_.:=\-]*[A-Za-z0-9+/=_\-]{4,}")),
    ("basic_scheme", re.compile(r"(?i)basic[\s_.:=\-]*[A-Za-z0-9+/=_\-]{8,}")),
    ("authorization_header", re.compile(r"(?i)authoriz(ation|ed?)[\s_.:=\-]")),
    # ── Credential *names*, whether or not an `=` follows ──
    # `api_key=…` was covered; `api_key_abcdef` and `sessionid-9f8a…` were not,
    # and a provider echoing a credential into an identifier field is exactly
    # as likely to use one spelling as the other.
    (
        "credential_assignment",
        re.compile(
            r"(?i)(api[_.\-]?key|access[_.\-]?token|auth[_.\-]?token|id[_.\-]?token"
            r"|refresh[_.\-]?token|session[_.\-]?id|session|secret|passwd|password"
            r"|credential|cookie|private[_.\-]?key)"
            r"[\s_.\-]*[:=]"
        ),
    ),
    (
        "credential_name_prefix",
        re.compile(
            r"(?i)\b(api[_.\-]?key|access[_.\-]?token|auth[_.\-]?token|id[_.\-]?token"
            r"|refresh[_.\-]?token|session[_.\-]?id|secret|passwd|password"
            r"|credential|cookie|private[_.\-]?key)"
            r"[\s_.\-]+[A-Za-z0-9+/=_\-]{6,}"
        ),
    ),
    # ── URL userinfo, and any URL at all in an identifier field ──
    ("url_scheme", re.compile(r"(?i)[a-z][a-z0-9+.\-]*://")),
    ("userinfo_at", re.compile(r"@")),
    # A percent-encoded separator is how a token survives being put in a URL.
    # `Bearer%20abc` and `Bearer%3Aabc` are refused by the grammars anyway —
    # `%` is in none of them — but naming the shape keeps the reason truthful.
    ("percent_encoded_separator", re.compile(r"(?i)%(20|3a|3d|2f|2b)")),
    # ── JWTs ──
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
)

# The scan itself only needs the patterns.
_CREDENTIAL_SHAPES = tuple(pattern for _, pattern in CREDENTIAL_SHAPES)


def credential_shape(text: str) -> Optional[str]:
    """The name of the first credential rule ``text`` matches, or ``None``.

    Exposed so a test can assert *which* rule refused a value, which is what
    makes "this rule is load-bearing" checkable rather than asserted.
    """
    for name, pattern in CREDENTIAL_SHAPES:
        if pattern.search(text):
            return name
    return None

# Codepoint categories that are refused outright rather than stripped.
#   Cc control, Cf format/invisible, Cs surrogate, Co private use, Cn unassigned
#   Zl/Zp line/paragraph separators, Zs any space (grammars admit none anyway)
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})


class _Refusal(Exception):
    """Internal: a value did not survive the shared pre-checks."""

    def __init__(self, value: ProviderValue) -> None:
        super().__init__(value.status)
        self.value = value


def _normalize(raw: Any, *, max_chars: int) -> str:
    """Run the shared pre-checks and return the normalized text, or refuse."""
    if raw is MISSING:
        raise _Refusal(ABSENT)
    if raw is None:
        raise _Refusal(NULL)
    if isinstance(raw, bool) or not isinstance(raw, str):
        # An int is not accepted here either: every field this function guards is
        # textual, and accepting a bare int would mean stringifying provider data
        # that never claimed to be a string.
        raise _Refusal(invalid(type(raw).__name__[:16]))
    if len(raw) > MAX_RAW_CHARS:
        raise _Refusal(invalid("oversized"))

    text = unicodedata.normalize("NFKC", raw)
    if not text:
        raise _Refusal(invalid("empty"))
    for char in text:
        if unicodedata.category(char) in _FORBIDDEN_CATEGORIES:
            raise _Refusal(redacted("nonprintable"))
    for shape in _CREDENTIAL_SHAPES:
        if shape.search(text):
            raise _Refusal(redacted("credential_shape"))
    if len(text) > max_chars:
        raise _Refusal(invalid("oversized"))
    return text


def _grammar(raw: Any, pattern: re.Pattern[str], *, max_chars: int) -> ProviderValue:
    try:
        text = _normalize(raw, max_chars=max_chars)
    except _Refusal as refusal:
        return refusal.value
    if not pattern.match(text):
        return invalid("grammar")
    return valid(text)


# ─────────────────────────── field validators ───────────────────────────


def provider_response_id(raw: Any) -> ProviderValue:
    return _grammar(raw, _RE_RESPONSE_ID, max_chars=MAX_RESPONSE_ID_CHARS)


def provider_model(raw: Any) -> ProviderValue:
    return _grammar(raw, _RE_MODEL, max_chars=MAX_MODEL_CHARS)


def provider_request_id(raw: Any) -> ProviderValue:
    return _grammar(raw, _RE_REQUEST_ID, max_chars=MAX_REQUEST_ID_CHARS)


def error_category(raw: Any) -> ProviderValue:
    return _grammar(raw, _RE_ERROR_CATEGORY, max_chars=MAX_ERROR_CATEGORY_CHARS)


# The closed stop-reason vocabularies the two providers document. A value
# outside them is not an error — providers add reasons — but it is explicitly
# *unknown*, and is kept only as a bounded token that already passed the grammar.
KNOWN_STOP_REASONS = frozenset(
    {
        # Anthropic Messages
        "end_turn",
        "max_tokens",
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
        # OpenAI Chat Completions (finish_reason)
        "stop",
        "length",
        "tool_calls",
        "content_filter",
        "function_call",
    }
)


def stop_reason(raw: Any) -> ProviderValue:
    result = _grammar(raw, _RE_STOP_REASON, max_chars=MAX_STOP_REASON_CHARS)
    if not result.is_valid:
        return result
    if result.value not in KNOWN_STOP_REASONS:
        # Bounded safe representation: the token survived the grammar, so it is
        # an identifier of at most 64 safe characters. It is carried as a detail
        # under an explicit unknown status, never as a valid value.
        return unknown_value(result.value)
    return result


def retry_after(raw: Any) -> ProviderValue:
    """A ``retry-after`` header value, accepted only in delta-seconds form.

    HTTP defines two representations for this header and this package supports
    one. The HTTP-date form is reported ``unsupported`` rather than parsed:
    parsing it would mean accepting free-form text out of a header into a stored
    field, and delta-seconds is what both SDKs actually emit. ``unsupported`` is
    the truthful answer — the header was present and well-formed, and we chose
    not to represent it — and it stores nothing either way.
    """
    if raw is MISSING:
        return ABSENT
    if raw is None:
        return NULL
    if isinstance(raw, bool):
        return invalid("bool")
    if isinstance(raw, int):
        return valid(str(raw)) if 0 <= raw <= 10**10 else invalid("oversized")
    if not isinstance(raw, str):
        return invalid(type(raw).__name__[:16])
    if len(raw) > MAX_RAW_CHARS:
        return invalid("oversized")

    text = unicodedata.normalize("NFKC", raw)
    # A credential in a retry-after header is a provider defect worth recording
    # as such, and is distinguished from a merely unsupported representation.
    for shape in _CREDENTIAL_SHAPES:
        if shape.search(text):
            return redacted("credential_shape")
    if _RE_RETRY_AFTER.match(text):
        return valid(text)
    return ProviderValue(VALUE_UNSUPPORTED)


def http_status(raw: Any) -> ProviderValue:
    """An HTTP status code, accepted only in the range HTTP defines."""
    if raw is MISSING:
        return ABSENT
    if raw is None:
        return NULL
    if isinstance(raw, bool):
        return invalid("bool")
    if not isinstance(raw, int):
        return invalid(type(raw).__name__[:16])
    if 100 <= raw <= 599:
        return valid(raw)
    return invalid("range")


# ─────────────────────────── exception identity ───────────────────────────


def _safe_token(raw: Any, *, max_chars: int = 128) -> Optional[str]:
    """A structured exception field, or ``None`` if it cannot be trusted."""
    try:
        text = _normalize(raw, max_chars=max_chars)
    except _Refusal:
        return None
    return text if _RE_ERROR_TOKEN.match(text) else None


def _typed_status_code(exc: Any) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status if 100 <= status <= 599 else None


def _typed_error_type(exc: Any) -> Optional[str]:
    """Read ``error.type`` only where the SDKs actually type it as a string.

    ``exc.body`` is provider-controlled JSON. Only a *string* at exactly
    ``body["error"]["type"]`` or ``body["type"]`` is considered; any other shape
    (nested object, list, number) is ignored rather than coerced, so a provider
    cannot smuggle a structure into a token column.
    """
    direct = getattr(exc, "type", None)
    if isinstance(direct, str):
        return _safe_token(direct)
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("type"), str):
        return _safe_token(error["type"])
    if isinstance(body.get("type"), str):
        return _safe_token(body["type"])
    return None


def _typed_request_id(exc: Any) -> Optional[str]:
    """The SDK's own typed ``request_id`` attribute. Headers are never scanned."""
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str):
        return _safe_token(request_id, max_chars=MAX_REQUEST_ID_CHARS)
    return None


def exception_identity(exc: BaseException) -> str:
    """A structured, message-free identity for a provider exception.

    The exception's own free-text message is never read, on any path. What is
    kept is the class name plus up to three typed, grammar-validated tokens::

        exception=RateLimitError status_code=429 error_type=rate_limit_error

    A field that fails validation is simply omitted; there is no fallback that
    stores the raw value.
    """
    fields: list[str] = []

    name = _safe_token(type(exc).__name__, max_chars=64)
    if name:
        fields.append(f"exception={name}")

    status = _typed_status_code(exc)
    if status is not None:
        fields.append(f"status_code={status}")

    error_type = _typed_error_type(exc)
    if error_type:
        fields.append(f"error_type={error_type}")

    request_id = _typed_request_id(exc)
    if request_id:
        fields.append(f"request_id={request_id}")

    return " ".join(fields)[:MAX_ERROR_IDENTITY_CHARS]


def response_failure_identity(category: Any, response_id: Any = None) -> str:
    """Identity for a non-exception failure (a response refused as malformed)."""
    fields: list[str] = []
    token = _safe_token(category, max_chars=MAX_ERROR_CATEGORY_CHARS)
    if token:
        fields.append(f"error_type={token}")
    identifier = _safe_token(response_id, max_chars=MAX_REQUEST_ID_CHARS)
    if identifier:
        fields.append(f"response_id={identifier}")
    return " ".join(fields)[:MAX_ERROR_IDENTITY_CHARS]


def failure_class(exc: BaseException) -> str:
    """A short, grammar-safe classification of a transport failure."""
    token = _safe_token(type(exc).__name__, max_chars=64)
    return token or "unclassified"
