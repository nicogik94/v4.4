"""The historical Gate B posture now fails closed, exercised with fakes only.

Gate B's FAIL remains historical evidence for a deferred capability. The final
V7 product boundary must prove three different facts:

  1. Anthropic really was unavailable (the harness posture, not an outage);
  2. an OpenAI credential did not make OpenAI eligible;
  3. no response-shape evidence was fabricated for the skipped provider.

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


# ════════════════ 2. OpenAI is present but release-ineligible ════════════════


def test_gate_b_posture_fails_closed_without_calling_openai():
    result, completions, records = _run_gate_b_phase([_usable()])

    assert result.ok is False
    assert result.error_type == "provider_unavailable"
    openai_records = [r for r in records if r["provider"] == "openai"]
    assert openai_records == [], "deferred OpenAI entered the supported candidate set"
    assert completions.models == [], "an OpenAI SDK call was made"


def test_a_deferred_openai_candidate_never_claims_an_effective_model():
    _, _, records = _run_gate_b_phase([_usable()])

    assert [r for r in records if r["provider"] == "openai"] == []


def test_provider_response_fixture_cannot_bypass_release_eligibility():
    response = _completion(_message(content="ok", refusal=None), finish_reason="stop",
                           usage=_Usage(_Details(reasoning_tokens=1)))
    del response.model

    result, completions, records = _run_gate_b_phase([response])

    assert result.ok is False
    assert completions.models == []
    assert all(r["provider"] == "anthropic" for r in records)


# ════════════════ 3. Skips never fabricate provider evidence ════════════════


def test_the_historical_v7_failure_fixture_is_never_sent_to_openai():
    result, completions, records = _run_gate_b_phase([_v7_signature()])

    assert result.ok is False
    assert completions.models == []
    openai_records = [r for r in records if r["provider"] == "openai"]
    assert openai_records == []


def test_reasoning_tokens_are_observed_never_guessed():
    """A provider that reports no reasoning tokens must not yield a `0`."""

    response = _completion(_message(content="ok", refusal=None), finish_reason="stop",
                           usage=_Usage(None))

    _, completions, records = _run_gate_b_phase([response])

    assert completions.models == []
    openai_records = [r for r in records if r["provider"] == "openai"]
    assert openai_records == []


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
    openai_records = [r for r in records if r["provider"] == "openai"]
    assert openai_records == []


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
