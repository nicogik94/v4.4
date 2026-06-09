import asyncio

from config import Provider
from state import ClassifyOutput
from evals.run_evals import (
    CaseResult,
    JUDGE_MODEL,
    JUDGE_SYSTEM_PROMPT,
    aggregate_summaries,
    judge_case,
    load_cases,
    pass_fail,
    run_case_real,
    run_case_mock,
    score_deterministic,
    shard_cases,
    summarize_results,
    write_summary,
)
from llm_client import LLMResponse


def _mock_score(case):
    output = asyncio.run(run_case_mock(case))
    result = score_deterministic(case, output)
    result.judge_overall = 70 if result.domain_match else 40
    result.judge_rationale = "mock"
    result.passed = pass_fail(result)
    return output, result


def test_mock_eval_cases_use_explicit_mock_expectations():
    cases = load_cases()

    assert cases
    for case in cases:
        output, result = _mock_score(case)

        assert output["mock"] is True
        assert output["mock_expected_frameworks"] == case.get("must_contain_frameworks", [])
        assert output["mock_expected_strategy_terms"] == case.get("strategy_must_mention", [])
        assert result.passed, case["id"]


def test_shard_selection_covers_every_case_exactly_once():
    cases = load_cases()
    shard_count = 4
    shard_ids = [
        [case["id"] for case in shard_cases(cases, shard_index, shard_count)]
        for shard_index in range(shard_count)
    ]
    flattened = [case_id for ids in shard_ids for case_id in ids]

    assert sorted(flattened) == sorted(case["id"] for case in cases)
    assert len(flattened) == len(set(flattened))
    assert all(shard_ids)


def _fake_case_result(case_id, passed=True, rationale="mock"):
    return CaseResult(
        case_id=case_id,
        passed=passed,
        domain_match=passed,
        hypothesis_count_ok=passed,
        frameworks_covered=1.0 if passed else 0.0,
        must_mention_hits=1.0 if passed else 0.0,
        data_labeling_correct=True,
        judge_overall=70 if passed else 0,
        judge_rationale=rationale,
    )


def _write_sharded_summaries(tmp_path, results_by_id, shard_count=4):
    cases = load_cases()
    shard_dirs = []
    for shard_index in range(shard_count):
        shard_dir = tmp_path / f"eval-shard-{shard_index}"
        shard_cases_for_index = shard_cases(cases, shard_index, shard_count)
        shard_results = [results_by_id[case["id"]] for case in shard_cases_for_index]
        write_summary(
            shard_dir,
            summarize_results(
                shard_results,
                threshold=0.75,
                mode="mock",
                case_ids=[case["id"] for case in shard_cases_for_index],
                shard_index=shard_index,
                shard_count=shard_count,
            ),
        )
        shard_dirs.append(str(shard_dir))
    return shard_dirs


def test_aggregate_summary_matches_non_sharded_pass_rate(tmp_path):
    cases = load_cases()
    results = [
        _fake_case_result(case["id"], passed=(index % 3 != 0))
        for index, case in enumerate(cases)
    ]
    results_by_id = {result.case_id: result for result in results}
    monolithic = summarize_results(
        results,
        threshold=0.75,
        mode="mock",
        case_ids=[case["id"] for case in cases],
    )
    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    assert aggregate["total"] == monolithic["total"] == len(cases)
    assert aggregate["passed"] == monolithic["passed"]
    assert aggregate["pass_rate"] == monolithic["pass_rate"]
    assert aggregate["case_ids"] == [case["id"] for case in cases]
    assert aggregate["aggregation_errors"] == []


def test_aggregate_fails_when_global_pass_rate_below_threshold(tmp_path):
    cases = load_cases()
    results_by_id = {
        case["id"]: _fake_case_result(case["id"], passed=(index < 8))
        for index, case in enumerate(cases)
    }

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    assert aggregate["total"] == len(cases)
    assert aggregate["passed"] == 8
    assert aggregate["pass_rate"] < 0.75
    assert aggregate["ok"] is False


def test_aggregate_preserves_provider_failure_rationale(tmp_path):
    cases = load_cases()
    provider_rationale = (
        "judge error: Provider call failed: category=invalid_request, "
        "provider=anthropic, model=claude-sonnet-4-6; "
        'provider_detail=status_code=400 error_type=invalid_request_error message="billing blocked"'
    )
    results_by_id = {
        case["id"]: _fake_case_result(case["id"], passed=True)
        for case in cases
    }
    first_id = cases[0]["id"]
    results_by_id[first_id] = _fake_case_result(
        first_id,
        passed=False,
        rationale=provider_rationale,
    )

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    first_case = next(case for case in aggregate["cases"] if case["case_id"] == first_id)
    assert first_case["judge_rationale"] == provider_rationale


def test_judge_case_calls_current_call_llm_interface(monkeypatch):
    case = load_cases()[0]
    output = {"classify": {"domain": case["expected_domain"]}}
    calls = []

    async def fake_call_llm(
        phase,
        system,
        prompt,
        config_override=None,
        *,
        project_id="",
        before_attempt=None,
    ):
        calls.append(
            {
                "phase": phase,
                "system": system,
                "prompt": prompt,
                "config_override": config_override,
                "project_id": project_id,
                "before_attempt": before_attempt,
            }
        )
        return LLMResponse(
            text='{"score": 82, "rationale": "valid judge parse", "critical_failures": []}',
            ok=True,
        )

    monkeypatch.setattr("evals.run_evals.call_llm", fake_call_llm)

    score, rationale = asyncio.run(judge_case(case, output))

    assert (score, rationale) == (82, "valid judge parse")
    assert len(calls) == 1
    call = calls[0]
    assert call["phase"] == "eval_judge"
    assert call["system"] == JUDGE_SYSTEM_PROMPT
    assert "ACTUAL OUTPUT" in call["prompt"]
    assert call["config_override"].model == JUDGE_MODEL
    assert call["config_override"].provider == Provider.ANTHROPIC
    assert call["project_id"] == f"eval-{case['id']}"
    assert call["before_attempt"] is None


def _run_judge_case_with_text(monkeypatch, text):
    case = load_cases()[0]

    async def fake_call_llm(
        phase,
        system,
        prompt,
        config_override=None,
        *,
        project_id="",
        before_attempt=None,
    ):
        return LLMResponse(text=text, ok=True)

    monkeypatch.setattr("evals.run_evals.call_llm", fake_call_llm)
    return asyncio.run(judge_case(case, {"classify": {"domain": case["expected_domain"]}}))


def test_judge_case_parses_fenced_json_response(monkeypatch):
    score, rationale = _run_judge_case_with_text(
        monkeypatch,
        """```json
{"score": 82, "rationale": "valid fenced judge parse", "critical_failures": []}
```""",
    )

    assert (score, rationale) == (82, "valid fenced judge parse")


def test_judge_case_parses_prose_wrapped_json_response(monkeypatch):
    score, rationale = _run_judge_case_with_text(
        monkeypatch,
        'Here is the score:\n{"score": 81, "rationale": "valid prose-wrapped judge parse", "critical_failures": []}\nDone.',
    )

    assert (score, rationale) == (81, "valid prose-wrapped judge parse")


def test_judge_case_reports_malformed_json_response(monkeypatch):
    score, rationale = _run_judge_case_with_text(
        monkeypatch,
        "I cannot provide a JSON score for this output.",
    )

    assert score == 0
    assert rationale.startswith("judge parse error:")


def test_judge_case_preserves_enriched_provider_failure_rationale(monkeypatch):
    case = load_cases()[0]
    provider_error = (
        "Provider call failed: category=invalid_request, provider=anthropic, "
        "model=claude-sonnet-4-6; provider_detail=status_code=400 "
        "exception=BadRequestError error_type=invalid_request_error "
        'request_id=req_123 message="model is not available"'
    )

    async def fake_call_llm(
        phase,
        system,
        prompt,
        config_override=None,
        *,
        project_id="",
        before_attempt=None,
    ):
        return LLMResponse(text="", ok=False, error=provider_error, error_type="invalid_request")

    monkeypatch.setattr("evals.run_evals.call_llm", fake_call_llm)

    score, rationale = asyncio.run(
        judge_case(case, {"classify": {"domain": case["expected_domain"]}})
    )

    assert score == 0
    assert rationale.startswith("judge error:")
    assert "Provider call failed: category=invalid_request" in rationale
    assert "; provider_detail=status_code=400" in rationale
    assert "error_type=invalid_request_error" in rationale
    assert "request_id=req_123" in rationale


def test_real_pass_fail_still_rejects_missing_framework_coverage():
    case = next(case for case in load_cases() if case.get("must_contain_frameworks"))
    output = {
        "mock": True,
        "classify": {"domain": case["expected_domain"]},
        "hypotheses": [{"id": f"H{i+1}"} for i in range(case["min_hypotheses"])],
        "mock_expected_strategy_terms": case.get("strategy_must_mention", []),
    }

    result = score_deterministic(case, output)
    result.judge_overall = 70

    assert result.frameworks_covered == 0.0
    assert not pass_fail(result)


def test_deterministic_scoring_normalizes_framework_and_phrase_text():
    case = {
        "id": "NORM",
        "expected_domain": "Complicated",
        "min_hypotheses": 1,
        "max_hypotheses": 1,
        "must_contain_frameworks": ["SISTÉMICO", "Red Teaming"],
        "strategy_must_mention": ["Stern-Volmer", "LinkedIn primary"],
        "strategy_must_not_mention": ["trust your gut"],
        "data_based_expected": False,
    }
    output = {
        "classify": {"domain": "Complicated"},
        "hypotheses": [{"id": "H1"}],
        "gauntlet": {"results": [{"frameworks": [{"fw": "Sistemico"}, {"fw": "Red–Teaming"}]}]},
        "strategy": {
            "executive_strategy": "Use Stern–Volmer controls and make LinkedIn-primary outreach the acquisition path."
        },
        "audit": {"data_based": False},
    }

    result = score_deterministic(case, output)

    assert result.frameworks_covered == 1.0
    assert result.must_mention_hits == 1.0
    assert result.must_not_mention_violations == 0


def test_real_eval_runner_halts_after_confused_classification(monkeypatch):
    calls = []

    async def fake_run_phase_node(state, phase):
        calls.append(phase)
        if phase == "classify":
            state.classify = ClassifyOutput(domain="Confused", justification="Brief too short.")
        return state

    monkeypatch.setattr("evals.run_evals.run_phase_node", fake_run_phase_node)

    state = asyncio.run(run_case_real({"id": "GXX", "brief": "hi"}))

    assert calls == ["classify"]
    assert state.classify.domain == "Confused"
    assert state.hypotheses is None
    assert (
        "workflow halted after Confused classification"
        in state.ingestion_metadata["eval_errors"]
    )
