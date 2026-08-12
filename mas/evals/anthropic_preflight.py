"""Bounded Anthropic provider preflight for Gate A (primary release validation).

This is the Gate A counterpart to ``provider_preflight`` (Gate B / OpenAI).  It
holds the same contract, restated for the Anthropic Messages response shape:

``ANTHROPIC_PROVIDER_PREFLIGHT_MODEL=PASS`` means that model returned **usable
visible text** under a production-representative request shape -- not merely
that the SDK call returned without raising, and not merely that a credential
exists.  Usable is deliberately narrow: at least one ``text`` content block
whose ``text`` is a ``str`` with non-empty ``strip()``.

Semantic correctness, quality, policy compliance and release readiness are
explicitly **not** claimed.  A refusal delivered as visible text PASSes: the
probe asserts output capability, and adjudicating refusals would turn a
capability gate into a moderation oracle keyed on a fixed probe prompt.

The probed model set is *derived* from the certified routing tables via
``release_gates.required_models`` rather than restated here, so a routing change
cannot leave this probe checking the wrong models.  Diagnostics reuse the
allowlisted sanitizers from ``provider_preflight`` -- that module is imported
and never modified, so there remains exactly one implementation of the
redaction rules.

Tests inject fake clients.  Nothing in this module is imported by a certified
product path, and nothing here makes a call unless a caller passes a client.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evals.provider_preflight import (
    _MISSING,
    _field,
    _sanitized_token,
    format_failure,
)
from evals.release_gates import required_models


PROBE_MODELS = required_models("anthropic")
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MAX_RETRIES = 0
PROBE_MAX_TOKENS = 512
PROBE_TEMPERATURE = 0.2
PROBE_SYSTEM_PROMPT = "You are a concise assistant."
PROBE_USER_PROMPT = "Reply with OK."

# Closed failure vocabulary, mirroring the Gate B probe so the two gates'
# diagnostics read the same way.  A local restatement is deliberate: importing
# the product's classifier would couple this eval-only harness to a certified
# module, and this probe classifies a raw SDK response, not a GatewayResponse.
CATEGORY_OUTPUT_TOKEN_EXHAUSTED = "output_token_exhausted"
CATEGORY_EMPTY_PROVIDER_OUTPUT = "empty_provider_output"
CATEGORY_MALFORMED_RESPONSE = "malformed_response"

CONTENT_MISSING = "missing"
CONTENT_NONE = "none"
CONTENT_EMPTY = "empty"
CONTENT_WHITESPACE = "whitespace"
CONTENT_NONEMPTY = "nonempty"
CONTENT_MALFORMED = "malformed"
CONTENT_NO_TEXT_BLOCK = "no_text_block"

# Anthropic signals output-budget exhaustion with exactly ``max_tokens``.  No
# alias is accepted; inventing one would assert semantics this probe has not
# observed.
STOP_REASON_MAX_TOKENS = "max_tokens"
STOP_REASON_ABSENT = ""
STOP_REASON_OTHER = "other"
KNOWN_STOP_REASONS = frozenset(
    {
        "end_turn",
        STOP_REASON_MAX_TOKENS,
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
    }
)

BLOCK_TYPE_TEXT = "text"


@dataclass(frozen=True)
class OutputAssessment:
    """Bounded verdict on a preflight response.

    Every field is drawn from a closed vocabulary, so an assessment renders to
    the workflow log without any provider-controlled bytes.
    """

    usable: bool
    category: str
    stop_reason: str
    content_status: str
    text_block_count: int


def _normalized_stop_reason(value: object) -> str:
    if value is _MISSING or value is None:
        return STOP_REASON_ABSENT
    if not isinstance(value, str):
        return STOP_REASON_OTHER
    normalized = value.strip().lower()
    if not normalized:
        return STOP_REASON_ABSENT
    if normalized in KNOWN_STOP_REASONS:
        return normalized
    return STOP_REASON_OTHER


def _iter_blocks(content: object) -> list[Any] | None:
    """Return the content blocks, or None when `content` is not a block sequence.

    A ``str`` is rejected on purpose.  Anthropic returns a *list of blocks*; a
    bare string would be a different response shape than the one the adapter
    parses, and silently accepting it would let this probe PASS a shape the
    product cannot consume.
    """

    if isinstance(content, (str, bytes, Mapping)):
        return None
    try:
        return list(content)  # type: ignore[arg-type]
    except Exception:
        return None


def _visible_text_status(blocks: list[Any]) -> tuple[str, int]:
    """Classify the visible text across all text blocks.

    Mirrors the product adapter, which joins every ``type == "text"`` block and
    uses the result.  The worst-case status across blocks is not what matters --
    what matters is whether the *joined* visible text is usable, which is the
    value a phase would actually receive.
    """

    text_blocks = 0
    malformed = False
    joined: list[str] = []
    for block in blocks:
        block_type = _field(block, "type")
        if block_type != BLOCK_TYPE_TEXT:
            continue
        text_blocks += 1
        text = _field(block, "text")
        if text is _MISSING or text is None:
            malformed = True
            continue
        if not isinstance(text, str):
            malformed = True
            continue
        joined.append(text)

    if text_blocks == 0:
        return CONTENT_NO_TEXT_BLOCK, 0
    combined = "".join(joined)
    if combined.strip():
        return CONTENT_NONEMPTY, text_blocks
    if malformed:
        return CONTENT_MALFORMED, text_blocks
    if combined == "":
        return CONTENT_EMPTY, text_blocks
    return CONTENT_WHITESPACE, text_blocks


def _malformed(stop_reason: str, content_status: str, text_blocks: int = 0) -> OutputAssessment:
    return OutputAssessment(
        usable=False,
        category=CATEGORY_MALFORMED_RESPONSE,
        stop_reason=stop_reason,
        content_status=content_status,
        text_block_count=text_blocks,
    )


def _assess_output(response: object) -> OutputAssessment:
    stop_reason = _normalized_stop_reason(_field(response, "stop_reason"))
    content = _field(response, "content")
    if content is _MISSING:
        return _malformed(stop_reason, CONTENT_MISSING)
    if content is None:
        return _malformed(stop_reason, CONTENT_NONE)

    blocks = _iter_blocks(content)
    if blocks is None:
        return _malformed(stop_reason, CONTENT_MALFORMED)

    content_status, text_blocks = _visible_text_status(blocks)

    if content_status == CONTENT_NONEMPTY:
        # Visible text proves output capability even when truncated, and even
        # when the model refused in prose.  See the module docstring.
        return OutputAssessment(
            usable=True,
            category="",
            stop_reason=stop_reason,
            content_status=content_status,
            text_block_count=text_blocks,
        )

    if content_status == CONTENT_MALFORMED:
        # A structurally broken block outranks the stop reason: reporting
        # exhaustion here would assert a cause the evidence does not support.
        return _malformed(stop_reason, content_status, text_blocks)

    category = (
        CATEGORY_OUTPUT_TOKEN_EXHAUSTED
        if stop_reason == STOP_REASON_MAX_TOKENS
        else CATEGORY_EMPTY_PROVIDER_OUTPUT
    )
    return OutputAssessment(
        usable=False,
        category=category,
        stop_reason=stop_reason,
        content_status=content_status,
        text_block_count=text_blocks,
    )


def assess_output(response: object) -> OutputAssessment:
    """Classify a preflight response, failing closed on any inspection error."""

    try:
        return _assess_output(response)
    except Exception:
        return _malformed(STOP_REASON_ABSENT, CONTENT_MISSING)


def format_unusable_output(model: str, assessment: OutputAssessment) -> str:
    try:
        safe_model = _sanitized_token(model, max_chars=80)
        try:
            blocks = int(assessment.text_block_count)
        except Exception:
            blocks = 0
        return " ".join(
            (
                "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL",
                f"model={safe_model}",
                f"reason={assessment.category}",
                f"stop_reason={assessment.stop_reason}",
                f"content={assessment.content_status}",
                f"text_blocks={blocks}",
            )
        )
    except Exception:
        safe_model = model if type(model) is str and model in PROBE_MODELS else ""
        return (
            "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL "
            f"model={safe_model} reason={CATEGORY_MALFORMED_RESPONSE} "
            "stop_reason= content=missing text_blocks=0"
        )


def probe_request(model: str) -> dict[str, object]:
    """A bounded request carrying the material shape the adapter sends.

    Mirrors ``llm_client._call_anthropic``: the system prompt is a text block
    with ephemeral cache control, the user turn is a plain string, and
    ``max_tokens``/``temperature`` are explicit.

    Extended thinking is deliberately **not** enabled.  A thinking-enabled probe
    at a bounded ``max_tokens`` can legitimately return thinking blocks and no
    visible text, which would fail a model that works fine in production -- the
    exact false failure this gate must not invent.  Thinking-path behavior is
    therefore an acknowledged gap in this probe, recorded rather than guessed.
    """

    return {
        "model": model,
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": PROBE_TEMPERATURE,
        "system": [
            {
                "type": "text",
                "text": PROBE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": PROBE_USER_PROMPT}],
    }


def run_preflight(client: object, *, stdout=None, stderr=None) -> bool:
    """Probe every required model in order, stopping at the first failure.

    Stopping early is intentional: a later success must never be able to mask an
    earlier failure, and there is no reason to spend a second call once the gate
    is already going to fail.
    """

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    for model in PROBE_MODELS:
        try:
            response = client.messages.create(**probe_request(model))
        except Exception as exc:
            print(
                format_failure(model, exc).replace(
                    "OPENAI_PROVIDER_PREFLIGHT=FAIL",
                    "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL",
                    1,
                ),
                file=stderr,
            )
            return False

        assessment = assess_output(response)
        if not assessment.usable:
            print(format_unusable_output(model, assessment), file=stderr)
            return False

        print(
            f"ANTHROPIC_PROVIDER_PREFLIGHT_MODEL=PASS model={model}",
            file=stdout,
        )

    print("ANTHROPIC_PROVIDER_PREFLIGHT=PASS", file=stdout)
    return True


def create_anthropic_client(api_key: str, *, client_factory=None):
    # The SDK's debug mode logs request options, including message content.
    # Preflight diagnostics are intentionally allowlisted, so never inherit it.
    os.environ.pop("ANTHROPIC_LOG", None)
    if client_factory is None:
        from anthropic import Anthropic

        client_factory = Anthropic
    return client_factory(
        api_key=api_key,
        timeout=PROBE_TIMEOUT_SECONDS,
        max_retries=PROBE_MAX_RETRIES,
    )


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print(
            "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL reason=missing_key",
            file=sys.stderr,
        )
        return 1

    try:
        client = create_anthropic_client(api_key)
    except Exception as exc:
        print(
            format_failure("", exc).replace(
                "OPENAI_PROVIDER_PREFLIGHT=FAIL",
                "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL",
                1,
            ),
            file=sys.stderr,
        )
        return 1
    return 0 if run_preflight(client) else 1


if __name__ == "__main__":
    raise SystemExit(main())
