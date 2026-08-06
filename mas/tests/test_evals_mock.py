import asyncio
import json

from cdp.review_caveats import CDP_REVIEW_CAVEATS
from config import Provider
from state import ClassifyOutput, ProjectState
from evals.run_evals import (
    CaseResult,
    JUDGE_MODEL,
    JUDGE_SYSTEM_PROMPT,
    _case_data_payload,
    _compact_output_for_judge,
    _normalize_eval_text,
    _term_present,
    _term_present_exact,
    aggregate_exit_code,
    aggregate_summaries,
    judge_case,
    load_cases,
    pass_fail,
    run_case_real,
    run_case_mock,
    score_citation_resolvability,
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


def _provider_failure_case_result(case_id, rationale):
    return CaseResult(
        case_id=case_id,
        passed=False,
        domain_match=True,
        hypothesis_count_ok=True,
        frameworks_covered=1.0,
        must_mention_hits=1.0,
        must_not_mention_violations=0,
        data_labeling_correct=True,
        citation_resolvability_ok=False,
        citation_resolvability={"status": "fail", "unresolved_count": 1},
        judge_overall=0,
        judge_rationale=rationale,
    )


def _quality_failure_case_result(case_id):
    return CaseResult(
        case_id=case_id,
        passed=False,
        domain_match=False,
        hypothesis_count_ok=True,
        frameworks_covered=1.0,
        must_mention_hits=1.0,
        must_not_mention_violations=0,
        data_labeling_correct=True,
        citation_resolvability_ok=True,
        judge_overall=70,
        judge_rationale="mock quality failure",
    )


def _claim_traceability_failure_case_result(case_id):
    return CaseResult(
        case_id=case_id,
        passed=False,
        domain_match=True,
        hypothesis_count_ok=True,
        frameworks_covered=1.0,
        must_mention_hits=1.0,
        must_not_mention_violations=0,
        data_labeling_correct=True,
        citation_resolvability_ok=False,
        citation_resolvability={"status": "fail", "unresolved_count": 1},
        judge_overall=70,
        judge_rationale="mock",
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


def test_provider_quota_only_aggregate_failure_is_provider_unavailable(tmp_path):
    cases = load_cases()
    provider_rationale = (
        "judge error: Provider call failed: category=quota_exceeded, "
        "provider=anthropic, model=claude-sonnet-4-6"
    )
    results_by_id = {
        case["id"]: _provider_failure_case_result(case["id"], provider_rationale)
        for case in cases
    }

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    assert aggregate["ok"] is False
    assert aggregate["aggregate_failure_kind"] == "provider_unavailable"
    assert aggregate["provider_unavailable"] is True
    assert aggregate["provider_failure_only"] is True
    assert aggregate["provider_failure_detected"] is True
    assert aggregate["provider_failure_count"] == len(cases)
    assert aggregate["provider_failure_categories"] == ["quota_exceeded"]
    assert aggregate["quality_failure_count"] == 0
    assert aggregate["quality_failure_case_ids"] == []
    assert aggregate["quality_ok"] == "unknown"
    assert all(case["citation_resolvability_ok"] is False for case in aggregate["cases"])
    assert aggregate_exit_code(aggregate) == 0


def test_provider_quota_only_aggregate_is_not_eval_quality_failure(tmp_path):
    cases = load_cases()
    provider_rationale = (
        "judge error: Provider call failed: category=quota_exceeded, "
        "provider=anthropic, model=claude-sonnet-4-6"
    )
    results_by_id = {
        case["id"]: _provider_failure_case_result(case["id"], provider_rationale)
        for case in cases
    }

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    assert aggregate["aggregate_failure_kind"] != "eval_quality_failure"
    assert aggregate["quality_failure_case_ids"] == []


def test_mixed_provider_and_real_quality_failure_still_fails(tmp_path):
    cases = load_cases()
    provider_rationale = (
        "judge error: Provider call failed: category=quota_exceeded, "
        "provider=anthropic, model=claude-sonnet-4-6"
    )
    results_by_id = {
        case["id"]: _provider_failure_case_result(case["id"], provider_rationale)
        for case in cases
    }
    results_by_id[cases[1]["id"]] = _quality_failure_case_result(cases[1]["id"])

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )

    assert aggregate["aggregate_failure_kind"] == "mixed_failure"
    assert aggregate["provider_failure_count"] == len(cases) - 1
    assert aggregate["quality_failure_count"] == 1
    assert aggregate["provider_failure_only"] is False
    assert aggregate["provider_unavailable"] is False
    assert aggregate["quality_ok"] is False
    assert aggregate_exit_code(aggregate) == 1


def test_aggregation_errors_remain_failing(tmp_path):
    aggregate = aggregate_summaries(
        [str(tmp_path / "missing-summary-dir")],
        threshold=0.75,
    )

    assert aggregate["aggregate_failure_kind"] == "aggregation_error"
    assert aggregate["aggregation_errors"]
    assert aggregate["provider_failure_detected"] is False
    assert aggregate_exit_code(aggregate) == 1


def test_claim_traceability_failure_remains_real_eval_failure(tmp_path):
    cases = load_cases()
    results_by_id = {case["id"]: _fake_case_result(case["id"], passed=True) for case in cases}
    results_by_id[cases[0]["id"]] = _claim_traceability_failure_case_result(cases[0]["id"])

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.99,
    )

    assert aggregate["aggregate_failure_kind"] == "eval_quality_failure"
    assert aggregate["provider_failure_count"] == 0
    assert aggregate["quality_failure_count"] == 1
    assert aggregate["quality_failure_case_ids"] == [cases[0]["id"]]
    assert aggregate_exit_code(aggregate) == 1


def test_aggregate_provider_diagnostics_avoid_overclaiming_language(tmp_path):
    cases = load_cases()
    provider_rationale = (
        "judge error: Provider call failed: category=quota_exceeded, "
        "provider=anthropic, model=claude-sonnet-4-6"
    )
    results_by_id = {
        case["id"]: _provider_failure_case_result(case["id"], provider_rationale)
        for case in cases
    }

    aggregate = aggregate_summaries(
        _write_sharded_summaries(tmp_path, results_by_id),
        threshold=0.75,
    )
    payload = {key: value for key, value in aggregate.items() if key != "cases"}
    text = json.dumps(payload, sort_keys=True).lower()

    for phrase in (
        "semantic support",
        "semantic-support",
        "semantic_support",
        "claim_truth",
        "defensibility_proven",
        "delivery approval",
        "delivery-approval",
        "delivery_approved",
        "delivery_gate_passed",
        "safe_to_send",
    ):
        assert phrase not in text


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


def _citation_case(report, knowledge_items=None, expected_status=None):
    fixture = {
        "report": report,
        "knowledge_items": knowledge_items or [],
    }
    if expected_status:
        fixture["expected_status"] = expected_status
    return {
        "id": "CDP-EVAL",
        "brief": "Evaluate citation resolvability.",
        "expected_domain": "Complicated",
        "min_hypotheses": 0,
        "max_hypotheses": 10,
        "must_contain_frameworks": [],
        "strategy_must_mention": [],
        "strategy_must_not_mention": [],
        "data_based_expected": False,
        "citation_resolvability_fixture": fixture,
    }


def _knowledge_item(evidence_id="ev1", locator="chunk=1", source_ref="fixture://source#chunk=1"):
    return {
        "item_id": evidence_id,
        "evidence_id": evidence_id,
        "source_id": "eval",
        "source_ref": source_ref,
        "locator": locator,
        "title": "Eval evidence",
    }


def _runtime_citation_case():
    return {
        "id": "CDP-RUNTIME-EVAL",
        "brief": "Evaluate runtime citation applicability.",
        "expected_domain": "Complicated",
        "min_hypotheses": 0,
        "max_hypotheses": 10,
        "must_contain_frameworks": [],
        "strategy_must_mention": [],
        "strategy_must_not_mention": [],
        "data_based_expected": False,
    }


def test_citation_without_fixture_and_without_report_key_is_not_applicable():
    result = score_deterministic(_runtime_citation_case(), {})

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_citation_without_fixture_and_report_none_is_not_applicable():
    result = score_deterministic(_runtime_citation_case(), {"report": None})

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_citation_without_fixture_and_empty_report_is_not_applicable():
    result = score_deterministic(_runtime_citation_case(), {"report": ""})

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_citation_without_fixture_and_whitespace_report_is_not_applicable():
    result = score_deterministic(_runtime_citation_case(), {"report": " \n\t "})

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_citation_without_fixture_and_non_dict_output_is_not_applicable():
    summary = score_citation_resolvability(_runtime_citation_case(), None)

    assert summary["status"] == "not_applicable"


def test_serialized_project_state_without_report_is_not_applicable():
    output = ProjectState(
        project_id="citation-serialization-regression",
        project_name="Citation serialization regression",
        brief="Strategy-only eval output.",
    ).model_dump(mode="json")

    assert output["report"] is None
    assert output["knowledge_layer"] is None
    assert output["imported_evidence"] == []
    assert output["decision_objects"] is None

    result = score_deterministic(_runtime_citation_case(), output)

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_empty_runtime_containers_without_report_are_not_applicable():
    output = {
        "report": None,
        "knowledge_layer": {"items": []},
        "imported_evidence": [],
        "decision_objects": None,
    }

    result = score_deterministic(_runtime_citation_case(), output)

    assert result.citation_resolvability["status"] == "not_applicable"
    assert result.citation_resolvability_ok is True


def test_real_report_without_resolvable_evidence_remains_fail_closed():
    output = {
        "report": "# Executive Summary\nA claim [Evidence: ev-missing | chunk=1].",
    }

    result = score_deterministic(_runtime_citation_case(), output)

    assert result.citation_resolvability["status"] != "not_applicable"
    assert result.citation_resolvability["status"] == "fail"
    assert result.citation_resolvability_ok is False


def test_explicit_citation_fixtures_preserve_expected_status_behavior():
    cases = [
        _citation_case(
            "# Executive Summary\nA traceable claim [Evidence: ev1 | chunk=1].",
            [_knowledge_item()],
            expected_status="pass",
        ),
        _citation_case(
            "# Executive Summary\nID-only claim [Evidence: ev1 | locator unavailable].",
            [_knowledge_item(locator="", source_ref="fixture://source")],
            expected_status="partial",
        ),
        _citation_case(
            "# Executive Summary\nUnknown claim [Evidence: ev-missing | chunk=1].",
            [],
            expected_status="fail",
        ),
        _citation_case(
            "# Executive Summary\nNo evidence markers appear in this report.",
            [],
            expected_status="no_markers",
        ),
    ]

    for case in cases:
        result = score_deterministic(case, {})
        expected_status = case["citation_resolvability_fixture"]["expected_status"]
        assert result.citation_resolvability["status"] == expected_status
        assert result.citation_resolvability_ok is True


def test_citation_resolvability_dimension_appears_in_eval_output():
    case = _citation_case(
        "# Executive Summary\nA traceable claim [Evidence: ev1 | chunk=1].",
        [_knowledge_item()],
        expected_status="pass",
    )
    output = {"classify": {"domain": "Complicated"}, "hypotheses": []}

    result = score_deterministic(case, output)

    assert result.citation_resolvability_ok is True
    assert result.citation_resolvability["schema_version"] == "citation_resolvability_eval.v0.1"
    assert result.citation_resolvability["status"] == "pass"
    assert result.citation_resolvability["marker_count"] == 1
    assert result.citation_resolvability["resolved_exact_count"] == 1


def test_exact_resolved_markers_improve_dimension_score():
    case = _citation_case(
        "# Executive Summary\nA traceable claim [Evidence: ev1 | chunk=1].",
        [_knowledge_item()],
    )

    summary = score_citation_resolvability(case, {})

    assert summary["status"] == "pass"
    assert summary["score"] == 1.0
    assert summary["resolved_exact_count"] == 1
    assert summary["unresolved_count"] == 0


def test_id_only_resolution_is_partial_warning_not_semantic_support():
    case = _citation_case(
        "# Executive Summary\nID-only claim [Evidence: ev1 | locator unavailable].",
        [_knowledge_item(locator="", source_ref="fixture://source")],
        expected_status="partial",
    )

    summary = score_citation_resolvability(case, {})

    assert summary["status"] == "partial"
    assert summary["score"] == 0.6
    assert summary["resolved_id_only_count"] == 1
    assert any("ID-only" in warning for warning in summary["warnings"])
    assert "CDP does not verify semantic support." in summary["caveats"]


def test_unknown_evidence_id_and_malformed_marker_degrade_dimension():
    cases = [
        (
            _citation_case(
                "# Executive Summary\nUnknown claim [Evidence: ev-missing | chunk=1].",
                [],
                expected_status="fail",
            ),
            "unknown_evidence_id_count",
        ),
        (
            _citation_case(
                "# Executive Summary\nMalformed claim [Evidence: ev1 \\| chunk=1].",
                [_knowledge_item()],
                expected_status="fail",
            ),
            "malformed_marker_count",
        ),
    ]

    for case, count_key in cases:
        summary = score_citation_resolvability(case, {})
        assert summary["status"] == "fail"
        assert summary["score"] == 0.0
        assert summary[count_key] == 1
        assert summary["unresolved_count"] == 1


def test_no_marker_case_does_not_overclaim_success():
    case = _citation_case(
        "# Executive Summary\nNo evidence markers appear in this report.",
        [],
        expected_status="no_markers",
    )

    summary = score_citation_resolvability(case, {})

    assert summary["status"] == "no_markers"
    assert summary["score"] == 0.0
    assert summary["marker_count"] == 0
    assert any("not evidence of semantic support" in warning for warning in summary["warnings"])


def test_golden_citation_fixtures_cover_required_statuses_and_mock_path_still_passes():
    expected_statuses = {
        (case.get("citation_resolvability_fixture") or {}).get("expected_status")
        for case in load_cases()
        if case.get("citation_resolvability_fixture")
    }

    assert {"pass", "partial", "fail", "no_markers"}.issubset(expected_statuses)
    for case in load_cases():
        _, result = _mock_score(case)
        assert result.passed, case["id"]


def test_citation_resolvability_caveats_are_not_lost():
    case = _citation_case(
        "# Executive Summary\nA traceable claim [Evidence: ev1 | chunk=1].",
        [_knowledge_item()],
    )

    summary = score_citation_resolvability(case, {})

    assert summary["caveats"] == list(CDP_REVIEW_CAVEATS)


def test_citation_resolvability_output_avoids_overclaiming_field_language():
    case = _citation_case(
        "# Executive Summary\nA traceable claim [Evidence: ev1 | chunk=1].",
        [_knowledge_item()],
    )

    payload = score_citation_resolvability(case, {})
    text = json.dumps({key: value for key, value in payload.items() if key != "caveats"}).lower()

    forbidden = [
        "semantic_support",
        "claim_truth",
        "defensibility_proven",
        "evidence_gauge",
        "defense_index",
        "claim_cards",
        "safe_to_send",
        "delivery_approved",
        "delivery_gate_passed",
    ]
    for phrase in forbidden:
        assert phrase not in text


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


def test_term_matching_allows_non_contiguous_multiword_concepts():
    text = _normalize_eval_text(
        "Make LinkedIn the primary channel, track reputational costs, "
        "segment customers, and run conversion-rate controls."
    )

    assert _term_present("LinkedIn primary", text)
    assert _term_present("reputation cost", text)
    assert _term_present("customer segmentation", text)
    assert _term_present("conversion rate", text)


def test_real_eval_data_payload_uses_brief_facts_only_when_case_expects_data():
    case_with_data = {
        "id": "DATA",
        "brief": "MRR is $4K and growth is 4% MoM.",
        "data_based_expected": True,
    }
    case_without_data = {
        "id": "NODATA",
        "brief": "Choose a positioning strategy.",
        "data_based_expected": False,
    }

    assert "MRR is $4K" in _case_data_payload(case_with_data)
    assert _case_data_payload(case_without_data) == ""


def test_judge_output_view_keeps_only_completed_eval_phases():
    compact = _compact_output_for_judge(
        {
            "project_id": "eval-GXX",
            "brief": "long brief",
            "classify": {"domain": "Complicated"},
            "hypotheses": [{"id": "H1"}],
            "strategy": {"executive_strategy": "act"},
            "report": {"unused": True},
        }
    )

    assert set(compact) == {"classify", "hypotheses", "strategy"}
    assert "project_id" not in compact
    assert "report" not in compact


def test_must_not_mention_scattered_tokens_not_a_violation():
    # "outright ban" and "no data" appear separately — not the exact forbidden phrase.
    # _term_present_exact must return False; score_deterministic must report 0 violations.
    forbidden_term = "outright ban with no data"
    text = _normalize_eval_text(
        "We recommend against an outright ban. There is simply no data yet to support "
        "a sweeping policy change."
    )
    assert not _term_present_exact(forbidden_term, text), (
        "scattered tokens must not trigger a must-not-mention violation"
    )

    case = {
        "id": "SCATTER",
        "expected_domain": "Complex",
        "min_hypotheses": 1,
        "max_hypotheses": 5,
        "must_contain_frameworks": [],
        "strategy_must_not_mention": [forbidden_term],
        "data_based_expected": False,
    }
    output = {
        "classify": {"domain": "Complex"},
        "hypotheses": [{"id": "H1"}],
        "strategy": {
            "executive_strategy": (
                "We recommend against an outright ban. There is simply no data yet "
                "to support a sweeping policy change."
            )
        },
        "audit": {"data_based": False},
    }
    result = score_deterministic(case, output)
    assert result.must_not_mention_violations == 0


def test_must_not_mention_genuine_forbidden_phrase_is_violation():
    # The literal phrase "just ignore bugs" appears — must still be detected.
    forbidden_term = "just ignore bugs"
    text = _normalize_eval_text(
        "The simplest path forward is to just ignore bugs that do not affect revenue."
    )
    assert _term_present_exact(forbidden_term, text), (
        "literal forbidden phrase must still trigger a violation"
    )

    case = {
        "id": "GENUINE",
        "expected_domain": "Simple",
        "min_hypotheses": 1,
        "max_hypotheses": 5,
        "must_contain_frameworks": [],
        "strategy_must_not_mention": [forbidden_term],
        "data_based_expected": False,
    }
    output = {
        "classify": {"domain": "Simple"},
        "hypotheses": [{"id": "H1"}],
        "strategy": {
            "executive_strategy": (
                "The simplest path forward is to just ignore bugs that do not affect revenue."
            )
        },
        "audit": {"data_based": False},
    }
    result = score_deterministic(case, output)
    assert result.must_not_mention_violations == 1


def test_must_not_mention_apostrophe_normalization_still_matches():
    # _normalize_eval_text strips apostrophes to spaces: "you're" → "you re".
    # Both the forbidden term and the text go through the same normalization, so when
    # the text uses the same apostrophe form, exact-substring matching still works.
    # Note: "you're" and "you are" do NOT produce the same normalized form ("you re"
    # vs "you are"), so _term_present_exact does not expand contractions — it detects
    # the literal phrase in whatever surface form normalization produces.
    forbidden_term = "charge more because you're worth it"
    text_same_form = _normalize_eval_text(
        "A premium pricing strategy is justified: charge more because you're worth it."
    )
    assert _term_present_exact(forbidden_term, text_same_form), (
        "forbidden phrase with apostrophe must be detected when text uses the same form"
    )

    case = {
        "id": "APOSTROPHE",
        "expected_domain": "Complicated",
        "min_hypotheses": 1,
        "max_hypotheses": 5,
        "must_contain_frameworks": [],
        "strategy_must_not_mention": [forbidden_term],
        "data_based_expected": False,
    }
    output = {
        "classify": {"domain": "Complicated"},
        "hypotheses": [{"id": "H1"}],
        "strategy": {
            "executive_strategy": (
                "A premium pricing strategy is justified: "
                "charge more because you're worth it."
            )
        },
        "audit": {"data_based": False},
    }
    result = score_deterministic(case, output)
    assert result.must_not_mention_violations == 1


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
