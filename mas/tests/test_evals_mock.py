import asyncio

from evals.run_evals import load_cases, pass_fail, run_case_mock, score_deterministic


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
