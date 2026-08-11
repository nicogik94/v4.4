"""What a Gate B run must be able to prove, exercised with fakes only.

Gate B claims OpenAI fallback compatibility. That claim is only meaningful if
the artifact can show three things a bare pass rate cannot:

  1. Anthropic really was unavailable (the harness posture, not an outage);
  2. OpenAI really was selected and really answered;
  3. when it failed, *why* -- effective model, stop reason, visible-output
     shape and reasoning-token behaviour.

No provider call is made here: `llm_client` is pointed at a fake SDK client and
both credentials are controlled by the test.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

MAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAS_ROOT))
sys.path.insert(0, str(MAS_ROOT / "tests"))

from evals import provenance, release_gates  # noqa: E402
from provider_telemetry import (  # noqa: E402
    ENTRY_POINT_EVALUATION_PHASE,
    POSTURE_OBSERVATIONAL,
    capture,
    telemetry_scope,
)
from test_provider_attempt_telemetry_capture import (  # noqa: E402
    _completion,
    _Details,
    _message,
    _Usage,
)

SENTINELS = ("system-sentinel", "user-sentinel", "test-key-not-a-credential")


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.models = []

    async def create(self, **kwargs):
        self.models.append(kwargs.get("model"))
        response = self.responses[min(len(self.models) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def _client(responses):
    completions = _Completions(responses)
    client = type("Client", (), {})()
    client.chat = type("Chat", (), {})()
    client.chat.completions = completions
    return client, completions


def _run_gate_b_phase(responses, phase="strategy"):
    """Drive one phase under the Gate B posture: Anthropic blank, OpenAI set."""

    import llm_client

    client, completions = _client(responses)
    recorder = provenance.EvalProvenanceRecorder(case_id="G01")
    previous = (
        llm_client._openai,
        llm_client.OPENAI_API_KEY,
        llm_client.ANTHROPIC_API_KEY,
    )
    llm_client._openai = client
    llm_client.OPENAI_API_KEY = "test-key-not-a-credential"
    llm_client.ANTHROPIC_API_KEY = ""  # the Gate B posture

    async def drive():
        with capture.response_shape_scope(recorder):
            async with telemetry_scope(
                entry_point=ENTRY_POINT_EVALUATION_PHASE,
                project_id="eval-G01",
                run_id="eval-G01",
                expected_phases=(phase,),
                posture=POSTURE_OBSERVATIONAL,
                sink=recorder,
            ):
                return await llm_client.call_llm(
                    phase, "system-sentinel", "user-sentinel", project_id="eval-G01"
                )

    try:
        result = asyncio.run(drive())
    finally:
        (
            llm_client._openai,
            llm_client.OPENAI_API_KEY,
            llm_client.ANTHROPIC_API_KEY,
        ) = previous
    return result, completions, recorder.invocation_records()


def _usable(text="a usable strategy answer"):
    return _completion(_message(content=text, refusal=None), finish_reason="stop",
                       usage=_Usage(_Details(reasoning_tokens=128)))


def _v7_signature():
    """Empty visible text, truncated at length, large reasoning spend."""

    return _completion(_message(content="", refusal=None), finish_reason="length",
                       usage=_Usage(_Details(reasoning_tokens=4000)))


# ════════════════ 1. Anthropic unavailable by intentional posture ════════════════


def test_gate_b_posture_makes_anthropic_candidates_unavailable():
    _, completions, records = _run_gate_b_phase([_usable()])

    anthropic = [r for r in records if r["provider"] == "anthropic"]
    assert anthropic, "no Anthropic candidate was considered at all"
    assert all(r["terminal_event_kind"] == "skipped" for r in anthropic)
    # Skipped, not failed: the harness removed the credential, the provider did
    # not have an outage. Conflating those would make Gate B unreadable.
    assert not any(r["terminal_event_kind"] == "provider_failure" for r in anthropic)
    assert "anthropic" not in [m for m in completions.models if m]


# ════════════════ 2. OpenAI really was selected and answered ════════════════


def test_gate_b_can_prove_openai_was_actually_selected():
    result, completions, records = _run_gate_b_phase([_usable()])

    assert result.ok
    openai_records = [r for r in records if r["provider"] == "openai"]
    assert openai_records, "no OpenAI attempt recorded"
    answered = [r for r in openai_records if r["terminal_event_kind"] != "skipped"]
    assert answered, "OpenAI was never actually attempted"
    assert completions.models, "no SDK call was made"
    assert answered[0]["effective_model"]["status"] in provenance.VALUE_STATUSES


def test_a_gate_b_artifact_distinguishes_requested_from_effective_model():
    _, _, records = _run_gate_b_phase([_usable()])

    answered = [
        r for r in records if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]
    record = answered[0]
    # The effective model is what the provider reported, not an echo of the
    # request: the fake reports a dated snapshot the request never named.
    assert record["effective_model"]["value"] == "gpt-5-2026-01-01"
    assert record["requested_model"] != record["effective_model"]["value"]


def test_effective_model_is_never_claimed_when_the_provider_did_not_report_one():
    response = _completion(_message(content="ok", refusal=None), finish_reason="stop",
                           usage=_Usage(_Details(reasoning_tokens=1)))
    del response.model

    _, _, records = _run_gate_b_phase([response])

    answered = [
        r for r in records if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]
    status = answered[0]["effective_model"]["status"]
    assert status in (provenance.STATUS_MISSING, provenance.STATUS_ABSENT, provenance.STATUS_UNKNOWN)
    assert answered[0]["effective_model"].get("value") in (None, "")


# ════════════════ 3. Diagnosable failure ════════════════


def test_the_v7_failure_signature_is_fully_described_not_merely_counted():
    result, _, records = _run_gate_b_phase([_v7_signature()])

    assert result.ok is False
    answered = [
        r for r in records if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]
    assert answered
    record = answered[0]
    # Every fact needed to attribute the failure, none of them the response text.
    assert record["content_status"]["status"] == capture.SHAPE_EMPTY
    assert record["visible_content_length"]["value"] == 0
    assert record["stop_reason"]["value"] == "length"
    assert record["reasoning_tokens"]["value"] == 4000
    assert record["output_tokens"]["value"] == 3


def test_reasoning_tokens_are_observed_never_guessed():
    """A provider that reports no reasoning tokens must not yield a `0`."""

    response = _completion(_message(content="ok", refusal=None), finish_reason="stop",
                           usage=_Usage(None))

    _, _, records = _run_gate_b_phase([response])

    answered = [
        r for r in records if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]
    reasoning = answered[0]["reasoning_tokens"]
    assert reasoning["status"] in (
        provenance.STATUS_MISSING,
        provenance.STATUS_ABSENT,
        provenance.STATUS_UNSUPPORTED,
        provenance.STATUS_UNKNOWN,
        provenance.STATUS_NULL,
    )
    assert reasoning.get("value") is None, "an unreported count was invented"


def test_a_skipped_candidate_never_reports_a_response_shape():
    _, _, records = _run_gate_b_phase([_usable()])

    for record in records:
        if record["terminal_event_kind"] == "skipped":
            assert record["content_status"]["status"] == provenance.STATUS_UNKNOWN
            assert record["reasoning_tokens"].get("value") is None


# ════════════════ raw-data exclusion ════════════════


@pytest.mark.parametrize(
    "responses",
    [[_usable()], [_v7_signature()]],
    ids=["usable", "v7-signature"],
)
def test_no_prompt_response_or_credential_text_reaches_the_artifact(responses):
    _, _, records = _run_gate_b_phase(responses)

    payload = json.dumps(records)
    for sentinel in SENTINELS:
        assert sentinel not in payload, sentinel
    assert "a usable strategy answer" not in payload


def test_refusal_text_is_recorded_as_a_status_not_as_text():
    response = _completion(
        _message(content="", refusal="I will not comply, hunter2"),
        finish_reason="stop",
        usage=_Usage(_Details(reasoning_tokens=5)),
    )

    _, _, records = _run_gate_b_phase([response])

    payload = json.dumps(records)
    assert "hunter2" not in payload
    assert "I will not comply" not in payload
    answered = [
        r for r in records if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]
    assert answered[0]["refusal_status"]["status"] == capture.SHAPE_NONEMPTY


# ════════════════ gate identity on the evidence ════════════════


def test_a_gate_b_summary_states_gate_b_and_never_gate_a(monkeypatch):
    from evals.run_evals import summarize_results

    monkeypatch.setenv(release_gates.GATE_ENV_VAR, release_gates.GATE_B)
    summary = summarize_results([], threshold=0.75, mode="real", case_ids=[])

    assert summary["provider_gate"] == release_gates.GATE_B
    assert release_gates.GATE_A not in json.dumps(summary)


def test_an_unset_gate_records_none_rather_than_a_guess(monkeypatch):
    from evals.run_evals import summarize_results

    monkeypatch.delenv(release_gates.GATE_ENV_VAR, raising=False)
    summary = summarize_results([], threshold=0.75, mode="real", case_ids=[])

    assert summary["provider_gate"] == release_gates.GATE_NONE


@pytest.mark.parametrize("junk", ["gate_a", "GATE-A", "gate_c", "", "  ", "true"])
def test_an_unrecognized_gate_never_resolves_to_a_real_gate(junk, monkeypatch):
    from evals.run_evals import summarize_results

    monkeypatch.setenv(release_gates.GATE_ENV_VAR, junk)
    summary = summarize_results([], threshold=0.75, mode="real", case_ids=[])

    assert summary["provider_gate"] == release_gates.GATE_NONE
