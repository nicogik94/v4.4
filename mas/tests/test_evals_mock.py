import asyncio

from config import Provider
from evals.run_evals import (
    JUDGE_MODEL,
    JUDGE_SYSTEM_PROMPT,
    judge_case,
    load_cases,
    pass_fail,
    run_case_mock,
    score_deterministic,
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
