"""M4.0 — what V4 actually sent to OpenAI, proved rather than derived.

The Gate B forensic analysis established the failing request shape by reading
certified source code. These tests pin the properties that make the *record*
sufficient on its own, so the next paid certification never has to repeat that:

* the exact ``max_completion_tokens`` supplied, per model;
* ``reasoning_effort`` recorded as **absent** — the positive finding that V4 did
  not send it — and never as the provider's documented default;
* absence (observed, not carried) kept distinct from missing (never observed),
  so no path can forge the absence proof;
* nothing about the prompt, the messages, or a credential in the record;
* and, throughout, that observing the request cannot change it.

No provider call is made anywhere in this file.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import sys
from pathlib import Path

import pytest

MAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAS_ROOT))
sys.path.insert(0, str(MAS_ROOT / "tests"))

from evals import provenance  # noqa: E402
from provider_telemetry import (  # noqa: E402
    ENTRY_POINT_EVALUATION_PHASE,
    POSTURE_OBSERVATIONAL,
    capture,
    telemetry_scope,
)
from provider_telemetry import request_shape as rs  # noqa: E402
from test_provider_attempt_telemetry_capture import (  # noqa: E402
    _completion,
    _Details,
    _message,
    _Usage,
)

# Strings that must never appear in any record produced here.
PROMPT_SENTINEL = "system-sentinel"
USER_SENTINEL = "user-sentinel"
CREDENTIAL_SENTINEL = "sk-ant-notarealcredential000"
SENTINELS = (PROMPT_SENTINEL, USER_SENTINEL, CREDENTIAL_SENTINEL, "hunter2")


def _gpt5_kwargs(model="gpt-5", budget=6000):
    """Exactly the mapping `llm_client._call_openai` builds on the gpt-5 branch."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_SENTINEL},
            {"role": "user", "content": f"{USER_SENTINEL} {CREDENTIAL_SENTINEL}"},
        ],
        "max_completion_tokens": budget,
        "temperature": 1,
    }


def _legacy_kwargs(model="gpt-4o", budget=4000, temperature=0.7):
    """The mapping built on the non-gpt-5 branch."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": USER_SENTINEL}],
        "max_tokens": budget,
        "temperature": temperature,
    }


class _Recorder:
    """A minimal observer, used where the full provenance recorder is overkill."""

    def __init__(self):
        self.payloads = []

    def record_request_shape(self, payload):
        self.payloads.append(payload)


# ════════════════════ 1. the exact budget actually supplied ════════════════════


@pytest.mark.parametrize(
    ("model", "budget"),
    [("gpt-5", 6000), ("gpt-5", 8000), ("gpt-5-mini", 6000), ("gpt-5-mini", 4000)],
)
def test_the_exact_max_completion_tokens_supplied_is_recorded(model, budget):
    """The number itself, not a fingerprint of it and not a config lookup."""
    shape = rs.openai_request_shape(_gpt5_kwargs(model, budget))

    assert shape["request_max_completion_tokens"]["status"] == rs.SHAPE_VALID
    assert shape["request_max_completion_tokens"]["value"] == budget
    assert shape["request_model"]["value"] == model


def test_the_branch_that_ran_is_provable_from_the_record_alone():
    """`max_tokens` absent on the gpt-5 branch; `max_completion_tokens` absent off it.

    This is what makes the record self-supporting: a reader does not have to
    know `startswith("gpt-5")` exists to tell which parameter carried the budget.
    """
    gpt5 = rs.openai_request_shape(_gpt5_kwargs("gpt-5", 6000))
    legacy = rs.openai_request_shape(_legacy_kwargs())

    assert gpt5["request_max_completion_tokens"]["value"] == 6000
    assert gpt5["request_max_tokens"]["status"] == rs.SHAPE_ABSENT
    assert gpt5["request_max_tokens"]["value"] is None

    assert legacy["request_max_tokens"]["value"] == 4000
    assert legacy["request_max_completion_tokens"]["status"] == rs.SHAPE_ABSENT
    assert legacy["request_max_completion_tokens"]["value"] is None


def test_the_gpt5_temperature_override_is_recorded_as_sent():
    """The literal `1` the branch forces, not whatever the config asked for."""
    assert rs.openai_request_shape(_gpt5_kwargs())["request_temperature"]["value"] == 1
    assert rs.openai_request_shape(_legacy_kwargs())["request_temperature"]["value"] == 0.7


# ════════════════════ 2. reasoning_effort: absence is the finding ════════════════════


def test_reasoning_effort_is_recorded_absent_and_never_as_the_provider_default():
    """The single most important assertion in this file.

    OpenAI defaults an unsent `reasoning_effort` to `medium`. That is a fact
    about OpenAI, not about the request V4 emitted, and writing it here would
    have made the Gate B forensic diagnosis unreachable from the evidence.
    """
    shape = rs.openai_request_shape(_gpt5_kwargs())

    assert shape["request_reasoning_effort"]["status"] == rs.SHAPE_ABSENT
    assert shape["request_reasoning_effort"]["value"] is None
    assert "medium" not in json.dumps(shape)


@pytest.mark.parametrize("effort", sorted(rs.KNOWN_REASONING_EFFORTS))
def test_an_explicitly_supplied_reasoning_effort_is_recorded_as_a_value(effort):
    kwargs = _gpt5_kwargs()
    kwargs["reasoning_effort"] = effort

    shape = rs.openai_request_shape(kwargs)

    assert shape["request_reasoning_effort"]["status"] == rs.SHAPE_VALID
    assert shape["request_reasoning_effort"]["value"] == effort


def test_absence_and_an_explicit_value_are_never_the_same_record():
    absent = rs.openai_request_shape(_gpt5_kwargs())["request_reasoning_effort"]
    kwargs = _gpt5_kwargs()
    kwargs["reasoning_effort"] = "medium"
    explicit = rs.openai_request_shape(kwargs)["request_reasoning_effort"]

    assert absent != explicit
    assert absent["status"] != explicit["status"]
    # The distinction survives serialization into an uploaded artifact.
    assert json.dumps(absent) != json.dumps(explicit)


def test_an_explicit_null_is_distinct_from_both_absence_and_a_value():
    kwargs = _gpt5_kwargs()
    kwargs["reasoning_effort"] = None

    shape = rs.openai_request_shape(kwargs)

    assert shape["request_reasoning_effort"]["status"] == rs.SHAPE_NULL
    assert shape["request_reasoning_effort"]["value"] is None


def test_an_unrecognized_effort_level_is_unknown_rather_than_valid_or_invalid():
    """A provider may add levels; this build must not vouch for one it lacks."""
    kwargs = _gpt5_kwargs()
    kwargs["reasoning_effort"] = "extreme"

    field = rs.openai_request_shape(kwargs)["request_reasoning_effort"]

    assert field["status"] == rs.SHAPE_UNKNOWN
    assert field["value"] is None
    assert field["detail"] == "extreme"


# ════════════════════ 3. malformed observation fails safely ════════════════════


@pytest.mark.parametrize(
    "bad",
    [None, "not-a-mapping", 42, [], object()],
    ids=["none", "str", "int", "list", "object"],
)
def test_an_unreadable_mapping_reports_missing_and_never_forges_absence(bad):
    """`missing` means "not looked at". Reporting `absent` here would forge the
    reasoning-effort proof out of a failure to observe anything at all."""
    shape = rs.openai_request_shape(bad)

    for name, _ in rs.OPENAI_REQUEST_ALLOWLIST:
        assert shape[name]["status"] == rs.SHAPE_MISSING, name
        assert shape[name]["value"] is None, name
    assert rs.SHAPE_ABSENT not in json.dumps(shape)


def test_a_hostile_mapping_costs_the_record_and_nothing_else():
    class Hostile(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("hostile mapping")

    shape = rs.openai_request_shape(Hostile())

    # It is a dict, so each field was attempted individually and each failed.
    for name, _ in rs.OPENAI_REQUEST_ALLOWLIST:
        assert shape[name]["status"] in (rs.SHAPE_MISSING, rs.SHAPE_ABSENT, rs.SHAPE_INVALID)
        assert shape[name]["value"] is None


@pytest.mark.parametrize(
    ("raw", "detail"),
    [(True, "bool"), (6000.0, "float"), ("6000", "string"), (-1, "negative")],
)
def test_a_budget_that_is_not_an_exact_count_is_refused_not_coerced(raw, detail):
    kwargs = _gpt5_kwargs()
    kwargs["max_completion_tokens"] = raw

    field = rs.openai_request_shape(kwargs)["request_max_completion_tokens"]

    assert field["status"] == rs.SHAPE_INVALID
    assert field["value"] is None, "a non-count was coerced into a number"
    assert field["detail"] == detail


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_temperature_is_refused_because_it_is_not_json(raw):
    kwargs = _gpt5_kwargs()
    kwargs["temperature"] = raw

    field = rs.openai_request_shape(kwargs)["request_temperature"]

    assert field["status"] == rs.SHAPE_INVALID
    assert field["detail"] == "non_finite"
    # The record must survive the artifact it travels in.
    json.dumps(field)


def test_a_structured_value_in_a_scalar_field_is_refused_without_stringification():
    kwargs = _gpt5_kwargs()
    kwargs["reasoning_effort"] = {"level": USER_SENTINEL}
    kwargs["model"] = {"name": PROMPT_SENTINEL}

    shape = rs.openai_request_shape(kwargs)

    payload = json.dumps(shape)
    for sentinel in (PROMPT_SENTINEL, USER_SENTINEL):
        assert sentinel not in payload
    assert shape["request_reasoning_effort"]["status"] == rs.SHAPE_INVALID
    assert shape["request_model"]["status"] == rs.SHAPE_INVALID


# ════════════════════ 4. nothing but the allowlist ════════════════════


def test_the_allowlist_is_exactly_these_five_keys():
    """A mutation guard. Widening what is captured must fail this test first."""
    assert rs.OPENAI_REQUEST_ALLOWLIST == (
        ("request_model", "model"),
        ("request_max_completion_tokens", "max_completion_tokens"),
        ("request_max_tokens", "max_tokens"),
        ("request_reasoning_effort", "reasoning_effort"),
        ("request_temperature", "temperature"),
    )


def test_no_prompt_message_or_credential_text_reaches_the_record():
    kwargs = _gpt5_kwargs()
    kwargs["tools"] = [{"name": "leak", "description": PROMPT_SENTINEL}]
    kwargs["metadata"] = {"note": CREDENTIAL_SENTINEL}
    kwargs["extra_headers"] = {"authorization": f"Bearer {CREDENTIAL_SENTINEL}"}

    payload = json.dumps(rs.openai_request_shape(kwargs))

    for sentinel in SENTINELS:
        assert sentinel not in payload, sentinel
    for leaked in ("messages", "Bearer", "authorization", "tools", "metadata"):
        assert leaked not in payload, leaked


def test_a_credential_shaped_value_in_an_allowlisted_field_is_refused():
    """Even an allowlisted key cannot smuggle a credential through."""
    kwargs = _gpt5_kwargs()
    kwargs["model"] = CREDENTIAL_SENTINEL

    field = rs.openai_request_shape(kwargs)["request_model"]

    assert field["status"] == rs.SHAPE_INVALID
    assert field["value"] is None
    assert CREDENTIAL_SENTINEL not in json.dumps(field)


def test_the_record_carries_only_the_declared_fields():
    shape = rs.openai_request_shape(_gpt5_kwargs())

    assert set(shape) == set(rs.OUTBOUND_REQUEST_FIELDS) | {"observation_point"}


# ════════════════════ 6. publication is inert and cannot escape ════════════════════


def test_nothing_is_published_when_no_observer_is_bound():
    assert rs.current_request_shape_observer() is None
    rs.publish_request_shape(lambda: rs.openai_request_shape(_gpt5_kwargs()))


def test_a_builder_that_raises_costs_the_record_and_nothing_else():
    recorder = _Recorder()

    def explode():
        raise RuntimeError("builder defect")

    with rs.request_shape_scope(recorder):
        rs.publish_request_shape(explode)

    assert recorder.payloads == []


def test_an_observer_that_raises_never_reaches_the_caller():
    class Hostile:
        def record_request_shape(self, payload):
            raise RuntimeError("observer defect")

    with rs.request_shape_scope(Hostile()):
        rs.publish_request_shape(lambda: rs.openai_request_shape(_gpt5_kwargs()))


def test_cancellation_still_propagates_through_the_guard():
    """`guard` absorbs Exception and re-raises BaseException. Swallowing a
    cancellation would itself be the behavioral change telemetry must avoid."""

    def cancel():
        raise asyncio.CancelledError()

    with rs.request_shape_scope(_Recorder()):
        with pytest.raises(asyncio.CancelledError):
            rs.publish_request_shape(cancel)


def test_publication_cannot_mutate_the_mapping_it_observes():
    kwargs = _gpt5_kwargs()
    snapshot = copy.deepcopy(kwargs)
    messages = kwargs["messages"]

    with rs.request_shape_scope(_Recorder()):
        rs.publish_request_shape(lambda: rs.openai_request_shape(kwargs))

    assert kwargs == snapshot
    assert kwargs["messages"] is messages
    assert set(kwargs) == set(snapshot)


# ════════════════════ 6c. the adapter-boundary wrapper ════════════════════


class _Owner:
    """Stands in for the SDK's completions object."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result


def test_the_wrapper_publishes_and_delegates_unchanged():
    sentinel = object()
    owner = _Owner(result=sentinel)
    recorder = _Recorder()
    kwargs = _gpt5_kwargs()

    with rs.request_shape_scope(recorder), rs.observe_openai_create(owner):
        returned = owner.create(**kwargs)

    assert returned is sentinel, "the SDK's return value was not passed through"
    assert owner.seen == [kwargs]
    assert len(recorder.payloads) == 1
    assert recorder.payloads[0]["request_max_completion_tokens"]["value"] == 6000


def test_the_wrapper_does_not_await_or_wrap_the_result():
    """`create` returns an awaitable; the wrapper must return it untouched so
    streaming and `await` semantics are exactly what they were."""

    async def coro():
        return "answer"

    awaitable = coro()
    owner = _Owner(result=awaitable)

    with rs.request_shape_scope(_Recorder()), rs.observe_openai_create(owner):
        returned = owner.create(**_gpt5_kwargs())

    assert returned is awaitable
    assert asyncio.run(_consume(returned)) == "answer"


async def _consume(awaitable):
    return await awaitable


def test_an_exception_from_the_sdk_propagates_unchanged():
    boom = RuntimeError("provider exploded")
    owner = _Owner(raises=boom)
    recorder = _Recorder()

    with rs.request_shape_scope(recorder), rs.observe_openai_create(owner):
        with pytest.raises(RuntimeError) as caught:
            owner.create(**_gpt5_kwargs())

    assert caught.value is boom
    # Published *before* delegating, so a call that fails still leaves evidence
    # of what it tried to send. A record that only exists for calls that
    # succeeded cannot describe the failures anyone would want to diagnose.
    assert len(recorder.payloads) == 1
    assert recorder.payloads[0]["request_max_completion_tokens"]["value"] == 6000


def test_the_original_create_is_restored_on_exit():
    owner = _Owner()
    original = owner.create

    with rs.observe_openai_create(owner):
        assert owner.create is not original

    assert owner.create == original
    assert "create" not in vars(owner)


def test_nesting_the_wrapper_cannot_double_record():
    owner = _Owner()
    recorder = _Recorder()

    with rs.request_shape_scope(recorder), rs.observe_openai_create(owner):
        with rs.observe_openai_create(owner):
            owner.create(**_gpt5_kwargs())

    assert len(recorder.payloads) == 1
    assert len(owner.seen) == 1


def test_an_owner_without_create_is_a_silent_no_op():
    owner = object()

    with rs.observe_openai_create(owner) as yielded:
        assert yielded is owner


def test_the_wrapper_binds_to_the_installed_sdk_class():
    """The path the eval harness actually uses."""
    cls = rs.openai_completions_class()
    assert cls is not None, "the installed OpenAI SDK lost its pinned shape"

    import openai

    client = openai.AsyncOpenAI(api_key="test-key-not-a-credential")
    assert type(client.chat.completions) is cls

    with rs.observe_openai_sdk_requests() as owner:
        assert owner is cls
        assert isinstance(vars(cls).get("create"), rs._Wrapped)
    assert not isinstance(vars(cls).get("create"), rs._Wrapped), "the class was left patched"


def test_an_unresolvable_sdk_shape_observes_nothing_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(rs, "openai_completions_class", lambda: None)
    recorder = _Recorder()

    with rs.request_shape_scope(recorder), rs.observe_openai_sdk_requests() as owner:
        assert owner is None

    assert recorder.payloads == []


# ════════════════════ 7. end to end, through the real gateway ════════════════════


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        # A deep copy taken *before* the adapter can observe anything, so the
        # test can prove the mapping the SDK received was never modified.
        self.calls.append(copy.deepcopy(kwargs))
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def _client(responses):
    completions = _Completions(responses)
    client = type("Client", (), {})()
    client.chat = type("Chat", (), {})()
    client.chat.completions = completions
    return client, completions


def _run_phase(responses, phase="strategy", *, observe=True):
    """Drive one phase under the Gate B posture: Anthropic blank, OpenAI set."""
    import llm_client

    client, completions = _client(responses)
    recorder = provenance.EvalProvenanceRecorder(case_id="M40")
    previous = (
        llm_client._openai,
        llm_client.OPENAI_API_KEY,
        llm_client.ANTHROPIC_API_KEY,
    )
    llm_client._openai = client
    llm_client.OPENAI_API_KEY = "test-key-not-a-credential"
    llm_client.ANTHROPIC_API_KEY = ""

    async def drive():
        import contextlib

        with contextlib.ExitStack() as stack:
            stack.enter_context(capture.response_shape_scope(recorder))
            if observe:
                stack.enter_context(rs.request_shape_scope(recorder))
                # Exactly what the eval harness binds, against the fake SDK's
                # own completions object rather than the installed SDK class.
                stack.enter_context(rs.observe_openai_create(completions))
            async with telemetry_scope(
                entry_point=ENTRY_POINT_EVALUATION_PHASE,
                project_id="eval-M40",
                run_id="eval-M40",
                expected_phases=(phase,),
                posture=POSTURE_OBSERVATIONAL,
                sink=recorder,
            ):
                return await llm_client.call_llm(
                    phase, PROMPT_SENTINEL, USER_SENTINEL, project_id="eval-M40"
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
    return _completion(
        _message(content=text, refusal=None),
        finish_reason="stop",
        usage=_Usage(_Details(reasoning_tokens=128)),
    )


def _exhausted():
    """The V7 signature: empty visible text, stopped at length."""
    return _completion(
        _message(content="", refusal=None),
        finish_reason="length",
        usage=_Usage(_Details(reasoning_tokens=4000)),
    )


def _openai_attempts(records):
    return [
        r
        for r in records
        if r["provider"] == "openai" and r["terminal_event_kind"] != "skipped"
    ]


def test_a_live_gpt5_attempt_records_the_budget_it_actually_sent():
    result, completions, records = _run_phase([_usable()])

    assert result.ok
    attempts = _openai_attempts(records)
    assert attempts, "no OpenAI attempt recorded"
    record = attempts[0]

    assert record["request_observation_point"] == provenance.REQUEST_POINT_ADAPTER
    sent = completions.calls[0]
    # The record matches the mapping the fake SDK actually received.
    assert record["request_max_completion_tokens"]["value"] == sent["max_completion_tokens"]
    assert record["request_model"]["value"] == sent["model"]
    assert record["request_reasoning_effort"]["status"] == provenance.STATUS_ABSENT
    assert "reasoning_effort" not in sent


def test_the_ledger_proves_reasoning_effort_was_unsent_on_every_openai_attempt():
    _, completions, records = _run_phase([_exhausted(), _usable()])

    attempts = _openai_attempts(records)
    assert len(attempts) >= 2, "the fallback candidate did not run"
    for record in attempts:
        assert record["request_reasoning_effort"]["status"] == provenance.STATUS_ABSENT
        assert record["request_reasoning_effort"]["value"] is None
    for sent in completions.calls:
        assert "reasoning_effort" not in sent


def test_each_fallback_candidate_records_its_own_request():
    """gpt-5 exhausts, gpt-5-mini answers: two candidates, two request records."""
    _, completions, records = _run_phase([_exhausted(), _usable()])

    attempts = _openai_attempts(records)
    models = [r["request_model"]["value"] for r in attempts]
    assert models == [call["model"] for call in completions.calls]
    assert len(set(models)) > 1, "the fallback never changed model"
    for record, sent in zip(attempts, completions.calls):
        assert record["request_max_completion_tokens"]["value"] == sent["max_completion_tokens"]


def test_a_skipped_candidate_never_claims_a_request_was_sent():
    _, _, records = _run_phase([_usable()])

    skipped = [r for r in records if r["terminal_event_kind"] == "skipped"]
    assert skipped, "no candidate was skipped"
    for record in skipped:
        assert record["request_observation_point"] == ""
        for name in provenance.OUTBOUND_REQUEST_FIELDS:
            assert record[name]["status"] == provenance.STATUS_UNKNOWN, name
            assert record[name]["value"] is None, name
            assert record[name]["detail"] == "no_request_shape_record"


def test_an_anthropic_candidate_skipped_before_transport_reports_no_budget():
    """The Gate B posture skips Anthropic pre-HTTP. That must not read as a request."""
    _, _, records = _run_phase([_usable()])

    anthropic = [r for r in records if r["provider"] == "anthropic"]
    assert anthropic
    for record in anthropic:
        assert record["request_max_completion_tokens"]["value"] is None
        assert record["request_observation_point"] == ""


def _cache_gateway(openai_executor):
    """A gateway with caching genuinely on, so a second call is really served."""
    from extensions.runtime import RoutingConfig
    from runtime.cache import InMemorySemanticCache
    from runtime.provider_gateway import DefaultProviderGateway

    from test_runtime_gateway import _BreakerStub

    async def _unused_anthropic(*args, **kwargs):  # pragma: no cover - never reached
        raise AssertionError("the Anthropic executor must not run")

    return DefaultProviderGateway(
        anthropic_executor=_unused_anthropic,
        openai_executor=openai_executor,
        cache=InMemorySemanticCache(),
        breaker=_BreakerStub(),
        provider_availability={"anthropic": False, "openai": True},
        routing_config=RoutingConfig(cache_enabled=True, cache_ttl_seconds=60),
        max_retries=1,
    )


def test_a_cache_hit_fabricates_no_provider_request_evidence():
    """A served cache entry makes no HTTP request, so it must claim none.

    The cache returns before ``_call_with_fallbacks``, so neither the adapter nor
    the transport runs. The assertion that matters is that the second call
    publishes *nothing* — a cached response must never inherit the request record
    of the call that populated it.
    """
    from extensions.runtime import GatewayRequest, RoutingContext

    calls = []
    recorder = _Recorder()

    async def executor(model, system, prompt, max_tokens, temperature):
        calls.append(model)
        with rs.request_shape_scope(recorder):
            rs.publish_request_shape(
                lambda: rs.openai_request_shape(
                    {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_completion_tokens": max_tokens,
                        "temperature": temperature,
                    }
                )
            )
        import llm_client

        return llm_client.LLMResponse(
            text="cached-answer", ok=True, model_used=model, input_tokens=1,
            output_tokens=1, cost_usd=0.0, latency_ms=1.0,
        )

    gateway = _cache_gateway(executor)
    request = GatewayRequest(
        phase="strategy",
        system_prompt=PROMPT_SENTINEL,
        user_prompt=USER_SENTINEL,
        routing_context=RoutingContext(phase="strategy"),
        allow_cache=True,
    )

    async def drive():
        with rs.request_shape_scope(recorder):
            first = await gateway.call(request)
            published_after_first = len(recorder.payloads)
            second = await gateway.call(request)
            return first, second, published_after_first

    first, second, published_after_first = asyncio.run(drive())

    assert not first.error and not second.error
    assert second.text == first.text == "cached-answer"
    assert second.cache_hit is True, "the second call was not served from cache"
    assert len(calls) == 1, "the cache hit still reached the adapter"
    # The decisive assertion: the cache hit added no request-shape record.
    assert len(recorder.payloads) == published_after_first
    assert published_after_first == 1


def test_no_prompt_or_credential_text_reaches_the_invocation_ledger():
    _, _, records = _run_phase([_usable(), _exhausted()])

    payload = json.dumps(records)
    for sentinel in SENTINELS:
        assert sentinel not in payload, sentinel
    assert "a usable strategy answer" not in payload


def test_request_and_response_evidence_stay_separate_fields():
    """`request_*` describes what V4 sent; `effective_model` what came back."""
    _, _, records = _run_phase([_usable()])

    record = _openai_attempts(records)[0]

    assert record["request_model"]["value"] == "gpt-5"
    assert record["effective_model"]["value"] == "gpt-5-2026-01-01"
    assert record["request_model"] != record["effective_model"]
    for name in provenance.OUTBOUND_REQUEST_FIELDS:
        assert name.startswith("request_")
        assert "effective_" not in name
        assert "provider_observed" not in name


# ════════════════════ 8. behavior neutrality ════════════════════


def test_observing_changes_nothing_the_runtime_does():
    """The differential proof: identical behavior with and without the observer.

    Same result, same models, same candidate order, same mapping handed to the
    SDK — and every field of the invocation ledger identical except the M4.0
    additions, which are the only thing the observer is allowed to affect.
    """
    observed_result, observed_calls, observed_records = _run_phase(
        [_exhausted(), _usable()], observe=True
    )
    plain_result, plain_calls, plain_records = _run_phase(
        [_exhausted(), _usable()], observe=False
    )

    assert observed_result.ok == plain_result.ok
    assert observed_result.text == plain_result.text
    assert observed_result.model_used == plain_result.model_used
    assert observed_calls.calls == plain_calls.calls, "the SDK received a different request"

    m4_fields = set(provenance.OUTBOUND_REQUEST_FIELDS) | {"request_observation_point"}
    volatile = {"logical_call_id", "invocation_id"}
    assert len(observed_records) == len(plain_records)
    for observed, plain in zip(observed_records, plain_records):
        left = {k: v for k, v in observed.items() if k not in m4_fields | volatile}
        right = {k: v for k, v in plain.items() if k not in m4_fields | volatile}
        assert left == right


def test_the_unobserved_run_still_produces_the_m4_fields_as_unknown():
    """Absent an observer the fields exist and are honest, rather than missing."""
    _, _, records = _run_phase([_usable()], observe=False)

    for record in records:
        assert record["request_observation_point"] == ""
        assert record["request_reasoning_effort"]["status"] == provenance.STATUS_UNKNOWN


def test_the_cache_key_payload_carries_no_m4_field():
    """Cache identity is unchanged: `build_cache_key` never saw these names."""
    import inspect

    from runtime import provider_gateway

    source = inspect.getsource(provider_gateway.build_cache_key)
    for name in provenance.OUTBOUND_REQUEST_FIELDS:
        assert name not in source
    for name in ("request_shape", "reasoning_effort", "max_completion_tokens"):
        assert name not in source


def test_routing_selection_is_untouched_by_the_observer():
    from runtime.provider_gateway import select_model_candidates

    def fingerprint():
        return [
            (
                config.provider,
                config.model,
                config.max_tokens,
                config.thinking_budget,
                config.min_response_tokens,
                config.temperature,
                selection.provider,
                selection.model,
                selection.task_profile,
            )
            for config, selection in select_model_candidates("strategy")
        ]

    plain = fingerprint()
    with rs.request_shape_scope(_Recorder()):
        observed = fingerprint()

    assert observed == plain


def test_the_adapter_publishes_before_the_call_and_only_the_allowlist():
    """The mapping the SDK received is byte-identical to the one observed."""
    _, completions, records = _run_phase([_usable()])

    sent = completions.calls[0]
    record = _openai_attempts(records)[0]

    assert set(sent) >= {"model", "messages", "max_completion_tokens", "temperature"}
    assert record["request_temperature"]["value"] == sent["temperature"]
    # The messages the SDK received are intact and untouched by observation.
    assert sent["messages"][0]["content"] == PROMPT_SENTINEL


# ════════════════════ 9. vocabulary and reader contract ════════════════════


def test_the_runtime_and_harness_vocabularies_agree():
    """A rename on either side must fail here rather than silently degrade."""
    assert set(provenance.REQUEST_OBSERVATION_POINTS) == set(rs.OBSERVATION_POINTS)
    assert set(provenance.OUTBOUND_REQUEST_FIELDS) == set(rs.OUTBOUND_REQUEST_FIELDS)
    assert provenance.REQUEST_POINT_ADAPTER == rs.POINT_ADAPTER_KWARGS


def test_the_first_observation_for_an_invocation_wins():
    """First-wins, exactly like the response shape beside it: the first record
    describes the request the adapter composed; a later one describes something
    else and must not overwrite it."""
    recorder = provenance.EvalProvenanceRecorder(case_id="M40")
    first = rs.openai_request_shape(_gpt5_kwargs("gpt-5", 6000))
    first["invocation_id"] = "inv-1"
    second = rs.openai_request_shape(_gpt5_kwargs("gpt-5", 8000))
    second["invocation_id"] = "inv-1"

    recorder.record_request_shape(first)
    recorder.record_request_shape(second)

    stored = recorder.request_shapes["inv-1"]
    assert stored["request_max_completion_tokens"]["value"] == 6000
    assert stored["request_observation_point"] == provenance.REQUEST_POINT_ADAPTER


@pytest.mark.parametrize("bad", [None, "x", 42, {}, {"invocation_id": "i"}])
def test_the_recorder_drops_a_malformed_request_payload_without_raising(bad):
    recorder = provenance.EvalProvenanceRecorder(case_id="M40")

    recorder.record_request_shape(bad)

    assert recorder.request_shapes == {}


def test_an_unrecognized_observation_point_is_refused():
    recorder = provenance.EvalProvenanceRecorder(case_id="M40")
    payload = rs.openai_request_shape(_gpt5_kwargs())
    payload["invocation_id"] = "inv-1"
    payload["observation_point"] = "somewhere_else"

    recorder.record_request_shape(payload)

    assert recorder.request_shapes == {}


def test_a_hostile_payload_is_counted_rather_than_raised():
    class Hostile(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("hostile payload")

    recorder = provenance.EvalProvenanceRecorder(case_id="M40")

    recorder.record_request_shape(Hostile())

    assert recorder.request_shapes == {}
    assert provenance.NOTE_RECORDER_FAULT in recorder.notes


def test_the_aggregate_counts_request_evidence_without_scoring_it():
    _, _, records = _run_phase([_usable()])
    case = {
        "case_id": "M40",
        "provenance": {"captured": True, "counters": {}, "invocations": records},
    }

    summary = provenance.aggregate_provenance([case])

    assert summary["invocations_with_request_evidence"] >= 1
    assert summary["request_observation_point_counts"][provenance.REQUEST_POINT_ADAPTER] >= 1
    assert summary["request_reasoning_effort_status_counts"][provenance.STATUS_ABSENT] >= 1
    assert summary["informational_only"] is True


# ════════════════════ 10. mutation guards on the epistemic controls ════════════════════


def test_mutating_absent_into_the_provider_default_would_fail_a_test():
    """If a future edit substituted OpenAI's documented default, this is what
    would catch it: `absent` carries no value, and `medium` is not reachable."""
    field = rs.openai_request_shape(_gpt5_kwargs())["request_reasoning_effort"]

    assert field["status"] == rs.SHAPE_ABSENT
    assert field["value"] not in rs.KNOWN_REASONING_EFFORTS
    assert field["value"] is None


def test_mutating_missing_into_absent_would_fail_a_test():
    """The two are never interchangeable: one is an observation, one is a gap."""
    unreadable = rs.openai_request_shape(None)["request_reasoning_effort"]
    observed = rs.openai_request_shape(_gpt5_kwargs())["request_reasoning_effort"]

    assert unreadable["status"] == rs.SHAPE_MISSING
    assert observed["status"] == rs.SHAPE_ABSENT
    assert unreadable["status"] != observed["status"]


def test_mutating_the_budget_reader_to_use_config_would_fail_a_test():
    """The record must follow the mapping, not the routing table.

    A budget the routing table never contains still has to be recorded exactly,
    which is only true if the value is read from the request itself.
    """
    odd = 12345
    shape = rs.openai_request_shape(_gpt5_kwargs("gpt-5", odd))

    assert shape["request_max_completion_tokens"]["value"] == odd


def test_every_status_used_is_json_serializable_and_bounded():
    shapes = [
        rs.openai_request_shape(_gpt5_kwargs()),
        rs.openai_request_shape(None),
        rs.openai_request_shape({"model": "x" * 5000}),
    ]

    for shape in shapes:
        encoded = json.dumps(shape)
        assert len(encoded) < 4096
        for name, _ in rs.OPENAI_REQUEST_ALLOWLIST:
            assert len(shape[name]["detail"]) <= 32


def test_a_float_budget_is_never_silently_rounded():
    """`1.9` is not 1, and `6000.0` is not 6000: rounding invents a number."""
    for raw in (6000.0, 5999.9, 0.5):
        kwargs = _gpt5_kwargs()
        kwargs["max_completion_tokens"] = raw
        field = rs.openai_request_shape(kwargs)["request_max_completion_tokens"]
        assert field["value"] is None
        assert field["status"] == rs.SHAPE_INVALID
        assert not math.isclose(field["value"] or -1, raw)
