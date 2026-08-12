"""Deterministic matrix for the Gate A Anthropic preflight.

Fake clients only. No provider call, no credential, no network.

The contract under test is the Gate A restatement of the audited M3 contract:
`ANTHROPIC_PROVIDER_PREFLIGHT_MODEL=PASS` means that model returned usable
visible text -- at least one `text` block whose `text` is a `str` with non-empty
`strip()` -- under a production-representative request shape.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

MAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAS_ROOT))

from evals import anthropic_preflight as ap  # noqa: E402
from evals import release_gates  # noqa: E402


class Obj:
    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


class Exploding:
    def __getattr__(self, name):
        raise RuntimeError("boom sk-ant-secret /home/nicolas/private")


def text_block(text):
    return Obj(type="text", text=text)


def thinking_block(text="internal reasoning that must never be read"):
    return Obj(type="thinking", thinking=text)


def response(content, stop_reason="end_turn"):
    return Obj(content=content, stop_reason=stop_reason)


class FakeClient:
    """Records every request; returns a scripted response or raises."""

    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.requests = []
        self.messages = Obj(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        behaviour = self.behaviours[len(self.requests) - 1]
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour


USABLE = response([text_block("OK")])


# ════════════════════ probed model set ════════════════════


def test_probe_models_are_derived_not_restated():
    assert ap.PROBE_MODELS == release_gates.required_models("anthropic")
    assert ap.PROBE_MODELS  # a preflight over zero models would prove nothing


def test_probe_models_cover_every_anthropic_phase_default_the_eval_reaches():
    from config import MODEL_ROUTING, Provider

    for phase in release_gates.EVAL_PHASES:
        config = MODEL_ROUTING[phase]
        if config.provider is Provider.ANTHROPIC:
            assert config.model in ap.PROBE_MODELS, phase


def test_the_judge_model_is_probed():
    assert release_gates.EVAL_JUDGE_MODEL in ap.PROBE_MODELS


# ════════════════════ PASS matrix ════════════════════

PASS_CASES = {
    "single text block": response([text_block("OK")]),
    "text truncated at max_tokens": response([text_block("partial")], "max_tokens"),
    "refusal prose as visible text": response([text_block("I cannot help with that")], "refusal"),
    "thinking block then text block": response([thinking_block(), text_block("OK")]),
    "text split across blocks": response([text_block("O"), text_block("K")]),
    "text with surrounding whitespace": response([text_block("  OK  ")]),
    "unknown stop reason but visible text": response([text_block("OK")], "some_new_reason"),
    "tuple content": response((text_block("OK"),)),
}


@pytest.mark.parametrize("label", list(PASS_CASES), ids=list(PASS_CASES))
def test_usable_output_passes(label):
    assessment = ap.assess_output(PASS_CASES[label])
    assert assessment.usable is True, assessment
    assert assessment.category == ""


# ════════════════════ FAIL matrix ════════════════════

FAIL_CASES = {
    "missing content": Obj(stop_reason="end_turn"),
    "content None": response(None),
    "content empty list": response([]),
    "content is a bare string": response("OK"),
    "content is a mapping": response({"text": "OK"}),
    "only thinking blocks": response([thinking_block()]),
    "only thinking blocks at max_tokens": response([thinking_block()], "max_tokens"),
    "text block with empty text": response([text_block("")]),
    "text block with whitespace": response([text_block("   \t\n ")]),
    "text block with None text": response([text_block(None)]),
    "text block missing text": response([Obj(type="text")]),
    "text block with non-string text": response([text_block(["OK"])]),
    "text block with int text": response([text_block(7)]),
    "empty text at max_tokens": response([text_block("")], "max_tokens"),
    "exploding response": Exploding(),
    "exploding block": response([Exploding()]),
    "response is None": None,
    "response is a string": "OK",
}


@pytest.mark.parametrize("label", list(FAIL_CASES), ids=list(FAIL_CASES))
def test_unusable_output_fails(label):
    assessment = ap.assess_output(FAIL_CASES[label])
    assert assessment.usable is False, assessment
    assert assessment.category in {
        ap.CATEGORY_MALFORMED_RESPONSE,
        ap.CATEGORY_EMPTY_PROVIDER_OUTPUT,
        ap.CATEGORY_OUTPUT_TOKEN_EXHAUSTED,
    }


def test_a_bare_string_content_is_malformed_not_usable():
    """Anthropic returns a block list; a bare string is a shape the adapter
    cannot parse, so accepting it would PASS a model the product cannot use."""

    assessment = ap.assess_output(response("OK"))
    assert assessment.usable is False
    assert assessment.content_status == ap.CONTENT_MALFORMED


def test_exhaustion_and_emptiness_are_distinguished():
    exhausted = ap.assess_output(response([text_block("")], "max_tokens"))
    empty = ap.assess_output(response([text_block("")], "end_turn"))
    assert exhausted.category == ap.CATEGORY_OUTPUT_TOKEN_EXHAUSTED
    assert empty.category == ap.CATEGORY_EMPTY_PROVIDER_OUTPUT


def test_a_structurally_broken_block_outranks_the_stop_reason():
    """Same J2 precedence as M3: report the strongest *verified* claim."""

    assessment = ap.assess_output(response([text_block(["parts"])], "max_tokens"))
    assert assessment.category == ap.CATEGORY_MALFORMED_RESPONSE
    assert assessment.stop_reason == "max_tokens"


def test_no_text_block_is_distinguished_from_an_empty_one():
    assert ap.assess_output(response([thinking_block()])).content_status == ap.CONTENT_NO_TEXT_BLOCK
    assert ap.assess_output(response([text_block("")])).content_status == ap.CONTENT_EMPTY


# ════════════════════ leak resistance ════════════════════

LEAK_MARKERS = (
    "sk-ant",
    "/home/nicolas",
    "internal reasoning",
    "I cannot help",
    "hunter2",
    "You are a concise assistant",
    "Reply with OK",
)


@pytest.mark.parametrize("label", list(FAIL_CASES), ids=list(FAIL_CASES))
def test_diagnostic_line_carries_no_provider_bytes(label):
    assessment = ap.assess_output(FAIL_CASES[label])
    line = ap.format_unusable_output("claude-sonnet-4-6", assessment)
    for marker in LEAK_MARKERS:
        assert marker not in line, line


def test_unknown_stop_reason_never_reaches_the_diagnostic():
    assessment = ap.assess_output(response([text_block("")], "hunter2_secret_reason"))
    assert assessment.stop_reason == ap.STOP_REASON_OTHER
    assert "hunter2" not in ap.format_unusable_output("claude-sonnet-4-6", assessment)


@pytest.mark.parametrize(
    "value",
    ["max_tokens ", "MAX_TOKENS", " End_Turn ", "", "  ", None, 42, ["max_tokens"], b"end_turn"],
)
def test_stop_reason_is_always_in_the_closed_vocabulary(value):
    assessment = ap.assess_output(response([text_block("")], value))
    assert assessment.stop_reason in (
        ap.KNOWN_STOP_REASONS | {ap.STOP_REASON_ABSENT, ap.STOP_REASON_OTHER}
    )


def test_sdk_exception_diagnostics_are_redacted():
    client = FakeClient([RuntimeError("failed for key sk-ant-abcdefgh at /home/nicolas/x")])
    err = io.StringIO()

    assert ap.run_preflight(client, stdout=io.StringIO(), stderr=err) is False

    assert "ANTHROPIC_PROVIDER_PREFLIGHT=FAIL" in err.getvalue()
    assert "OPENAI" not in err.getvalue()
    for marker in ("sk-ant-abcdefgh", "/home/nicolas"):
        assert marker not in err.getvalue()


# ════════════════════ order / short-circuit ════════════════════


def test_all_required_models_are_probed_in_order():
    client = FakeClient([USABLE] * len(ap.PROBE_MODELS))
    out = io.StringIO()

    assert ap.run_preflight(client, stdout=out, stderr=io.StringIO()) is True

    assert [request["model"] for request in client.requests] == list(ap.PROBE_MODELS)
    assert "ANTHROPIC_PROVIDER_PREFLIGHT=PASS" in out.getvalue()


def test_first_model_failure_stops_before_the_second():
    client = FakeClient([response([text_block("")]), USABLE])

    assert ap.run_preflight(client, stdout=io.StringIO(), stderr=io.StringIO()) is False
    assert len(client.requests) == 1


def test_a_later_failure_is_never_hidden_by_an_earlier_success():
    behaviours = [USABLE] * (len(ap.PROBE_MODELS) - 1) + [response([text_block("")])]
    client = FakeClient(behaviours)
    out = io.StringIO()

    assert ap.run_preflight(client, stdout=out, stderr=io.StringIO()) is False
    assert len(client.requests) == len(ap.PROBE_MODELS)
    assert "ANTHROPIC_PROVIDER_PREFLIGHT=PASS" not in out.getvalue()


def test_missing_key_fails_without_constructing_a_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)

    assert ap.main() == 1
    assert "reason=missing_key" in err.getvalue()


# ════════════════════ request shape ════════════════════


def test_request_mirrors_the_production_adapter_shape():
    request = ap.probe_request("claude-sonnet-4-6")

    assert request["model"] == "claude-sonnet-4-6"
    assert request["max_tokens"] == ap.PROBE_MAX_TOKENS
    assert request["system"] == [
        {
            "type": "text",
            "text": ap.PROBE_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert request["messages"] == [{"role": "user", "content": ap.PROBE_USER_PROMPT}]


def test_request_is_bounded_and_carries_no_thinking_block():
    request = ap.probe_request("claude-sonnet-4-6")

    # Extended thinking is deliberately absent: see `probe_request`.
    assert "thinking" not in request
    assert request["max_tokens"] <= 512
    assert ap.PROBE_MAX_RETRIES == 0
    assert ap.PROBE_TIMEOUT_SECONDS == 30.0


def test_client_is_built_with_zero_retries_and_no_sdk_logging(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    ap.create_anthropic_client("test-key-not-a-credential", client_factory=factory)

    assert captured["max_retries"] == 0
    assert captured["timeout"] == ap.PROBE_TIMEOUT_SECONDS
    assert "ANTHROPIC_LOG" not in ap.os.environ


def test_preflight_does_not_require_any_particular_reply_text():
    assert ap.assess_output(response([text_block("banana")])).usable is True
