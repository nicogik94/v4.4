"""Provider-free proof of the nightly batch harness's oracle truth.

Every assertion here is static or fake-driven. No provider call is made, no
secret is read, no batch is submitted and no workflow is dispatched.

The property under test is a single one, approached from many directions:

    a nightly run may be reported as a *quality* failure only if it actually
    obtained a complete, attributable, structurally valid quality observation
    over the whole expected case universe.

Everything else — a judge response that would not parse, a batch request the
provider errored, a case whose judge result never arrived, a pipeline that
crashed, a shrunken or contaminated denominator — must invalidate the
observation, classify as a non-quality failure, and still exit non-zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import evals.run_evals_batch as batch
from evals import release_gates
from evals.run_evals import PASS_THRESHOLD, load_cases

GOLDEN_CASE_IDS = [case["id"] for case in load_cases()]
CASES_BY_ID = {case["id"]: case for case in load_cases()}
SOURCE_SHA = "e8160077cc91a2eed3569fe4a2c9c57dfcca3209"


# ═══════════════════════ fixtures / builders ═══════════════════════


def _passing_output(case: dict) -> dict:
    """A pipeline output that clears every deterministic check for `case`."""

    return {
        "classify": {"domain": case["expected_domain"]},
        "hypotheses": [{"id": f"H{index + 1}"} for index in range(case["min_hypotheses"])],
        "strategy": {
            "frameworks": list(case.get("must_contain_frameworks", [])),
            "notes": list(case.get("strategy_must_mention", [])),
        },
    }


def _complete_records(case_ids=None) -> list[batch.PipelineRecord]:
    ids = GOLDEN_CASE_IDS if case_ids is None else list(case_ids)
    return [
        batch.PipelineRecord(CASES_BY_ID[cid], _passing_output(CASES_BY_ID[cid]), batch.PIPELINE_COMPLETE)
        for cid in ids
    ]


def _judges(case_ids=None, score: int = 90) -> dict[str, batch.JudgeObservation]:
    ids = GOLDEN_CASE_IDS if case_ids is None else list(case_ids)
    return {cid: batch.JudgeObservation(batch.JUDGE_VALID, score=score, rationale="ok") for cid in ids}


def _observe(
    *,
    records=None,
    judges=None,
    expected_case_ids=None,
    golden_case_ids=None,
    source_sha: str = SOURCE_SHA,
    source_sha_origin: str = "github_sha",
    harness_errors=None,
    threshold: float = PASS_THRESHOLD,
) -> batch.NightlyObservation:
    records = _complete_records() if records is None else records
    judges = _judges() if judges is None else judges
    return batch.build_observation(
        pipeline_records=records,
        judge_observations=judges,
        expected_case_ids=GOLDEN_CASE_IDS if expected_case_ids is None else list(expected_case_ids),
        golden_case_ids=GOLDEN_CASE_IDS if golden_case_ids is None else list(golden_case_ids),
        threshold=threshold,
        source_sha=source_sha,
        source_sha_origin=source_sha_origin,
        harness_errors=harness_errors,
    )


def _codes(observation: batch.NightlyObservation) -> set[str]:
    return {entry["code"] for entry in observation.validity_errors}


def _case_entry(observation: batch.NightlyObservation, case_id: str) -> dict:
    return next(entry for entry in observation.cases if entry["case_id"] == case_id)


# ═══════════════════════ the healthy baseline ═══════════════════════


def test_complete_universe_with_valid_judges_is_a_valid_pass():
    observation = _observe()

    assert observation.valid_observation is True
    assert observation.validity_errors == []
    assert observation.result_class == release_gates.RESULT_PASS
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.passed == len(GOLDEN_CASE_IDS)
    assert observation.pass_rate == 1.0
    assert observation.expected_case_ids == GOLDEN_CASE_IDS
    assert observation.observed_case_ids == GOLDEN_CASE_IDS


def test_valid_observation_below_threshold_is_a_quality_failure():
    # A real, parsed judge score of 55 is a measurement. It is *supposed* to
    # produce a quality failure, and that path must stay reachable.
    observation = _observe(judges=_judges(score=55))

    assert observation.valid_observation is True
    assert observation.validity_errors == []
    assert observation.result_class == release_gates.RESULT_QUALITY_FAILURE
    assert observation.pass_rate == 0.0


def test_quality_failure_requires_a_valid_observation():
    """The central invariant, asserted directly over every invalid variant."""

    invalid_variants = [
        _observe(source_sha="", source_sha_origin="unavailable"),
        _observe(judges=_judges(GOLDEN_CASE_IDS[1:])),
        _observe(judges={**_judges(), GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_MALFORMED)}),
        _observe(
            judges={
                **_judges(),
                GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_PROVIDER_OR_BATCH_FAILURE),
            }
        ),
        _observe(records=_complete_records(GOLDEN_CASE_IDS[1:])),
        _observe(
            records=[
                batch.PipelineRecord(CASES_BY_ID[GOLDEN_CASE_IDS[0]], {}, batch.PIPELINE_PROVIDER_FAILURE, "quota_exceeded"),
                *_complete_records(GOLDEN_CASE_IDS[1:]),
            ]
        ),
    ]

    for observation in invalid_variants:
        assert observation.valid_observation is False
        assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
        assert observation.result_class != release_gates.RESULT_PASS
        assert observation.result_class in release_gates.GATE_RESULTS


# ═══════════════════════ DEFECT A — the judge JSON parser ═══════════════════════


def test_plain_json_judge_response_parses():
    observation = batch.parse_judge_payload('{"score": 81, "rationale": "fine"}')

    assert observation.status == batch.JUDGE_VALID
    assert observation.score == 81
    assert observation.rationale == "fine"


def test_json_fenced_judge_response_parses():
    """The exact base-harness defect: ```json left a literal `json` behind."""

    fenced = '```json\n{"score": 92, "rationale": "valid"}\n```'
    observation = batch.parse_judge_payload(fenced)

    assert observation.status == batch.JUDGE_VALID
    assert observation.score == 92
    assert observation.rationale == "valid"


def test_base_parser_expression_still_fails_on_the_same_payload():
    """Differential proof that the reused canonical parser is what fixed it."""

    fenced = '```json\n{"score": 92, "rationale": "valid"}\n```'
    with pytest.raises(json.JSONDecodeError):
        json.loads(fenced.strip().strip("`"))  # the base expression, verbatim


def test_generic_fenced_and_prose_wrapped_judge_responses_parse():
    for payload in (
        '```\n{"score": 70, "rationale": "generic fence"}\n```',
        'Here is the result:\n{"score": 70, "rationale": "prose intro"}',
    ):
        observation = batch.parse_judge_payload(payload)
        assert observation.status == batch.JUDGE_VALID, payload
        assert observation.score == 70


def test_malformed_judge_response_is_structural_not_a_zero():
    for payload in ("", "not json at all", "{{{", "[1, 2, 3]", '{"rationale": "no score"}'):
        observation = batch.parse_judge_payload(payload)
        assert observation.status == batch.JUDGE_MALFORMED, payload
        assert observation.score is None, payload
        assert observation.rationale == "", payload


def test_non_numeric_and_out_of_range_scores_are_malformed():
    assert batch.parse_judge_payload('{"score": "92"}').status == batch.JUDGE_MALFORMED
    assert batch.parse_judge_payload('{"score": true}').status == batch.JUDGE_MALFORMED
    assert batch.parse_judge_payload('{"score": null}').status == batch.JUDGE_MALFORMED
    assert batch.parse_judge_payload('{"score": 101}').status == batch.JUDGE_MALFORMED
    assert batch.parse_judge_payload('{"score": -1}').status == batch.JUDGE_MALFORMED


def test_a_genuinely_parsed_zero_is_a_valid_measured_score():
    """VALID `{"score": 0}` and INVALID non-parseable output are not the same."""

    measured = batch.parse_judge_payload('{"score": 0, "rationale": "genuinely bad"}')
    unmeasured = batch.parse_judge_payload("the model refused")

    assert measured.status == batch.JUDGE_VALID
    assert measured.score == 0
    assert unmeasured.status == batch.JUDGE_MALFORMED
    assert unmeasured.score is None
    assert measured.status != unmeasured.status


def test_parsed_zero_scores_flow_into_a_valid_quality_failure():
    observation = _observe(judges=_judges(score=0))

    assert observation.valid_observation is True
    assert observation.result_class == release_gates.RESULT_QUALITY_FAILURE
    for case_id in GOLDEN_CASE_IDS:
        entry = _case_entry(observation, case_id)
        assert entry["quality_measured"] is True
        assert entry["judge_overall"] == 0
        assert entry["passed"] is False


def test_parser_diagnostics_never_echo_the_malformed_payload():
    secret = "sk-ant-SUPER-SECRET-TOKEN and a prompt fragment"
    observation = batch.parse_judge_payload(f"absolutely not json: {secret}")

    assert observation.status == batch.JUDGE_MALFORMED
    serialized = json.dumps(observation.to_dict())
    assert secret not in serialized
    assert "sk-ant" not in serialized
    assert observation.detail in {
        "judge_response_not_a_json_object",
        "judge_response_missing_score",
    }


# ═══════════════════════ DEFECT B — invalid never becomes score zero ═══════════════════════


def test_malformed_judge_result_invalidates_the_whole_observation():
    observation = _observe(
        judges={**_judges(), GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_MALFORMED)}
    )

    assert observation.valid_observation is False
    assert batch.VALIDITY_JUDGE_RESULT_MALFORMED in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE

    entry = _case_entry(observation, GOLDEN_CASE_IDS[0])
    assert entry["quality_measured"] is False
    assert entry["judge_overall"] is None
    assert entry["judge_score"] is None
    assert entry["passed"] is None


def test_missing_judge_result_invalidates_and_does_not_shrink_the_denominator():
    observation = _observe(judges=_judges(GOLDEN_CASE_IDS[1:]))

    assert observation.valid_observation is False
    assert batch.VALIDITY_JUDGE_RESULT_MISSING in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE
    # 11 measured cases, denominator still 12.
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.passed == len(GOLDEN_CASE_IDS) - 1

    entry = _case_entry(observation, GOLDEN_CASE_IDS[0])
    assert entry["judge_status"] == batch.JUDGE_MISSING
    assert entry["judge_overall"] is None
    assert entry["passed"] is None


def test_batch_provider_failure_on_one_case_invalidates_the_observation():
    observation = _observe(
        judges={
            **_judges(),
            GOLDEN_CASE_IDS[0]: batch.JudgeObservation(
                batch.JUDGE_PROVIDER_OR_BATCH_FAILURE, detail="batch_result_errored"
            ),
        }
    )

    assert observation.valid_observation is False
    assert batch.VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE in _codes(observation)
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert _case_entry(observation, GOLDEN_CASE_IDS[0])["judge_overall"] is None


@pytest.mark.parametrize("result_type", ["errored", "canceled", "expired"])
def test_each_unsuccessful_batch_result_type_invalidates_the_observation(result_type):
    failed = batch.JudgeObservation(
        batch.JUDGE_PROVIDER_OR_BATCH_FAILURE, detail=batch.batch_result_detail(result_type)
    )
    observation = _observe(judges={**_judges(), GOLDEN_CASE_IDS[3]: failed})

    assert observation.valid_observation is False
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert failed.detail == f"batch_result_{result_type}"
    assert _case_entry(observation, GOLDEN_CASE_IDS[3])["quality_measured"] is False


def test_unknown_batch_result_type_is_recorded_without_echoing_it():
    detail = batch.batch_result_detail("something the SDK invented; sk-ant-LEAK")

    assert detail == "batch_result_unknown"
    assert "sk-ant" not in detail


def test_no_failed_or_missing_result_can_reach_the_passed_count():
    judges = {
        **_judges(),
        GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_MALFORMED),
        GOLDEN_CASE_IDS[1]: batch.JudgeObservation(batch.JUDGE_PROVIDER_OR_BATCH_FAILURE),
    }
    del judges[GOLDEN_CASE_IDS[2]]
    observation = _observe(judges=judges)

    unmeasured = {GOLDEN_CASE_IDS[0], GOLDEN_CASE_IDS[1], GOLDEN_CASE_IDS[2]}
    for entry in observation.cases:
        if entry["case_id"] in unmeasured:
            assert entry["quality_measured"] is False
            assert entry["judge_overall"] is None
            assert entry["passed"] is None
    assert observation.passed == len(GOLDEN_CASE_IDS) - 3
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.valid_observation is False


# ═══════════════════════ pipeline failure classification ═══════════════════════


def test_typed_provider_phase_failure_invalidates_the_observation():
    """The 43 historical credit-balance nightlies, reproduced from the real message."""

    output = dict(_passing_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]]))
    output["ingestion_metadata"] = {
        "eval_errors": [
            "classify: Provider call failed: category=quota_exceeded, "
            "provider=anthropic, model=claude-sonnet-4-6"
        ]
    }
    record = batch.pipeline_record_for_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]], output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category == "quota_exceeded"

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is False
    assert batch.VALIDITY_PIPELINE_PROVIDER_FAILURE in _codes(observation)
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE


def test_untyped_phase_failure_fails_closed_to_a_harness_classification():
    output = dict(_passing_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]]))
    output["ingestion_metadata"] = {"eval_errors": ["strategy: KeyError('missing')"]}
    record = batch.pipeline_record_for_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]], output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == "untyped_phase_failure"

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is False
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE


def test_confused_halt_is_a_measured_outcome_not_a_failure():
    """The product deliberately stops after a Confused classification."""

    output = dict(_passing_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]]))
    output["ingestion_metadata"] = {"eval_errors": [batch.EVAL_ERROR_CONFUSED_HALT]}
    record = batch.pipeline_record_for_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]], output)

    assert record.status == batch.PIPELINE_COMPLETE

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is True


def test_pipeline_exception_cannot_create_an_artificial_quality_score():
    record = batch.PipelineRecord(
        CASES_BY_ID[GOLDEN_CASE_IDS[0]], {}, batch.PIPELINE_PROVIDER_FAILURE, "quota_exceeded"
    )
    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])

    entry = _case_entry(observation, GOLDEN_CASE_IDS[0])
    assert entry["quality_measured"] is False
    assert entry["judge_overall"] is None
    assert entry["passed"] is None
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE


def test_exception_classification_separates_transport_from_harness():
    class FakeProviderError(Exception):
        status_code = 400

    class HarnessBug(Exception):
        pass

    status, category = batch.classify_exception(FakeProviderError("credit balance is too low"))
    assert status == batch.PIPELINE_PROVIDER_FAILURE
    assert category == "quota_exceeded"

    status, category = batch.classify_exception(HarnessBug("a local bug"))
    assert status == batch.PIPELINE_HARNESS_FAILURE
    assert category == "HarnessBug"


def test_harness_exception_detail_never_carries_the_exception_message():
    class HarnessBug(Exception):
        pass

    error = _validity_error_for(HarnessBug("ANTHROPIC_API_KEY=sk-ant-LEAKED"))

    assert "sk-ant" not in json.dumps(error)
    assert error["code"] == batch.VALIDITY_HARNESS_FAILURE
    assert error["detail"] == "batch_submit:HarnessBug"


def _validity_error_for(exc: BaseException) -> dict:
    return batch._batch_stage_error("submit", exc, list(GOLDEN_CASE_IDS))


def test_provider_error_detail_is_a_category_not_raw_provider_text():
    class FakeProviderError(Exception):
        status_code = 429

    error = _validity_error_for(FakeProviderError("your credit balance is too low; org_id=abc"))

    assert error["code"] == batch.VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE
    assert error["detail"] == "batch_submit:quota_exceeded"
    assert "org_id" not in json.dumps(error)


def test_provider_detail_marker_cannot_smuggle_a_category_or_leak_text():
    smuggled = (
        "classify: Provider call failed: category=timeout, provider=anthropic, model=x"
        f"{batch.PROVIDER_DETAIL_MARKER}category=quota_exceeded and sk-ant-LEAK"
    )

    assert batch.typed_provider_category(smuggled) == "timeout"
    assert batch.typed_provider_category(f"x{batch.PROVIDER_DETAIL_MARKER}category=timeout") == ""


# ═══════════════════════ denominator integrity ═══════════════════════


def test_eleven_of_twelve_cases_cannot_produce_valid_quality_evidence():
    subset = GOLDEN_CASE_IDS[:-1]
    observation = _observe(records=_complete_records(subset), judges=_judges(subset))

    assert observation.valid_observation is False
    assert batch.VALIDITY_MISSING_CASE_IDS in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.pass_rate < 1.0


def test_a_narrowed_expected_universe_is_itself_invalid():
    """A `--cases` subset run is not a nightly observation, however green."""

    subset = GOLDEN_CASE_IDS[:3]
    observation = _observe(
        records=_complete_records(subset), judges=_judges(subset), expected_case_ids=subset
    )

    assert observation.valid_observation is False
    assert batch.VALIDITY_INCOMPLETE_CASE_SELECTION in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE
    assert observation.pass_rate == 1.0  # green, and still not evidence


def test_duplicate_case_id_cannot_produce_valid_quality_evidence():
    records = [*_complete_records(), _complete_records([GOLDEN_CASE_IDS[0]])[0]]
    observation = _observe(records=records)

    assert observation.valid_observation is False
    assert batch.VALIDITY_DUPLICATE_CASE_IDS in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.passed <= len(GOLDEN_CASE_IDS)


def test_unknown_case_id_cannot_enter_the_denominator():
    stranger = dict(CASES_BY_ID[GOLDEN_CASE_IDS[0]], id="G99-not-a-golden-case")
    records = [
        *_complete_records(),
        batch.PipelineRecord(stranger, _passing_output(stranger), batch.PIPELINE_COMPLETE),
    ]
    judges = {**_judges(), "G99-not-a-golden-case": batch.JudgeObservation(batch.JUDGE_VALID, score=100)}
    observation = _observe(records=records, judges=judges)

    assert observation.valid_observation is False
    assert batch.VALIDITY_UNKNOWN_CASE_IDS in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert "G99-not-a-golden-case" not in observation.expected_case_ids
    # The stranger reaches neither the denominator nor the numerator, so the
    # pass rate cannot exceed 1.0 by contamination.
    assert observation.passed == len(GOLDEN_CASE_IDS)
    assert observation.pass_rate <= 1.0


def test_a_judge_result_for_an_unexpected_case_contaminates_the_universe():
    judges = {**_judges(), "G99-stray": batch.JudgeObservation(batch.JUDGE_VALID, score=100)}
    observation = _observe(judges=judges)

    assert observation.valid_observation is False
    assert batch.VALIDITY_UNKNOWN_CASE_IDS in _codes(observation)


def test_an_empty_expected_universe_is_invalid():
    observation = _observe(records=[], judges={}, expected_case_ids=[])

    assert observation.valid_observation is False
    assert batch.VALIDITY_EXPECTED_UNIVERSE_EMPTY in _codes(observation)
    assert observation.result_class == release_gates.RESULT_STRUCTURAL_FAILURE


# ═══════════════════════ source attribution ═══════════════════════


def test_github_sha_is_the_preferred_source_identity():
    sha, origin = batch.resolve_source_sha({"GITHUB_SHA": SOURCE_SHA})

    assert sha == SOURCE_SHA
    assert origin == "github_sha"


@pytest.mark.parametrize(
    "candidate",
    ["main", "refs/heads/main", "", "not-a-sha", "e816007", "zzzz" * 10, SOURCE_SHA + "0"],
)
def test_a_mutable_ref_is_never_accepted_as_source_identity(candidate, tmp_path):
    sha, origin = batch.resolve_source_sha({"GITHUB_SHA": candidate}, repo_root=tmp_path)

    assert sha == ""
    assert origin == "unavailable"


def test_local_git_fallback_records_an_exact_commit(tmp_path):
    if not _git_available():
        pytest.skip("git is unavailable in this environment")
    _init_git_repo(tmp_path)

    sha, origin = batch.resolve_source_sha({}, repo_root=tmp_path)

    assert origin == "git_rev_parse"
    assert len(sha) == 40 and all(char in "0123456789abcdef" for char in sha)


def test_missing_source_identity_fails_the_observation_closed():
    observation = _observe(source_sha="", source_sha_origin="unavailable")

    assert observation.valid_observation is False
    assert batch.VALIDITY_SOURCE_SHA_MISSING in _codes(observation)
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=True)
    except Exception:  # noqa: BLE001
        return False
    return True


def _init_git_repo(root: Path) -> None:
    env_args = [
        "-c", "user.email=eval@example.invalid",
        "-c", "user.name=eval",
        "-c", "commit.gpgsign=false",
    ]
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, timeout=30)
    (root / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(root), *env_args, "commit", "-q", "-m", "seed"], check=True, timeout=30
    )


# ═══════════════════════ result classification ═══════════════════════


def test_every_result_class_is_a_member_of_the_existing_closed_vocabulary():
    for code_group in (
        batch.ATTRIBUTION_AND_HARNESS_CODES,
        batch.PROVIDER_CODES,
        batch.STRUCTURAL_CODES,
    ):
        for code in code_group:
            classification = batch.classify_result(
                [{"code": code, "case_ids": [], "detail": ""}],
                pass_rate=1.0,
                threshold=PASS_THRESHOLD,
            )
            assert classification in release_gates.GATE_RESULTS
            assert classification != release_gates.RESULT_PASS
            assert classification != release_gates.RESULT_QUALITY_FAILURE


def test_no_validity_code_is_left_unclassified():
    """Every declared code belongs to exactly one precedence group."""

    groups = (
        batch.ATTRIBUTION_AND_HARNESS_CODES,
        batch.PROVIDER_CODES,
        batch.STRUCTURAL_CODES,
    )
    for code in batch.VALIDITY_CODES:
        assert sum(1 for group in groups if code in group) == 1, code


def test_provider_inability_outranks_the_structural_symptoms_it_causes():
    """A provider outage also loses the judge results; the cause is reported."""

    observation = _observe(
        records=[
            batch.PipelineRecord(
                CASES_BY_ID[cid], {}, batch.PIPELINE_PROVIDER_FAILURE, "quota_exceeded"
            )
            for cid in GOLDEN_CASE_IDS
        ],
        judges={},
    )

    assert _codes(observation) >= {
        batch.VALIDITY_PIPELINE_PROVIDER_FAILURE,
        batch.VALIDITY_JUDGE_RESULT_MISSING,
    }
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE


def test_an_unattributable_run_outranks_a_provider_outage():
    observation = _observe(
        source_sha="",
        source_sha_origin="unavailable",
        judges={
            **_judges(),
            GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_PROVIDER_OR_BATCH_FAILURE),
        },
    )

    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE


def test_a_harness_stage_failure_classifies_as_infrastructure():
    class HarnessBug(Exception):
        pass

    observation = _observe(harness_errors=[_validity_error_for(HarnessBug("boom"))])

    assert observation.valid_observation is False
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE


# ═══════════════════════ report / exit semantics ═══════════════════════


def _summary(tmp_path: Path, observation: batch.NightlyObservation, batch_id="msgbatch_test") -> dict:
    return batch.write_report(observation, tmp_path, batch_id=batch_id)


def test_summary_exposes_the_full_machine_readable_contract(tmp_path):
    summary = _summary(tmp_path, _observe())

    for key in (
        "schema_version",
        "source_sha",
        "source_sha_origin",
        "valid_observation",
        "result_class",
        "validity_errors",
        "quality_measured",
        "expected_case_ids",
        "observed_case_ids",
        "golden_case_ids",
        "passed",
        "total",
        "pass_rate",
        "threshold",
        "ok",
        "cases",
    ):
        assert key in summary, key

    on_disk = json.loads((tmp_path / "summary_batch.json").read_text())
    assert on_disk["source_sha"] == SOURCE_SHA
    assert on_disk["expected_case_ids"] == GOLDEN_CASE_IDS
    assert on_disk["observed_case_ids"] == GOLDEN_CASE_IDS
    assert on_disk["valid_observation"] is True
    assert on_disk["result_class"] == release_gates.RESULT_PASS
    assert on_disk["validity_errors"] == []
    assert on_disk["batch_id"] == "msgbatch_test"


def test_validity_errors_are_machine_readable_records(tmp_path):
    summary = _summary(tmp_path, _observe(judges=_judges(GOLDEN_CASE_IDS[1:])))

    assert summary["validity_errors"]
    for entry in summary["validity_errors"]:
        assert set(entry) == {"code", "case_ids", "detail"}
        assert entry["code"] in batch.VALIDITY_CODES
        assert isinstance(entry["case_ids"], list)
        assert isinstance(entry["detail"], str)


def test_ok_is_true_only_for_a_valid_measured_pass(tmp_path):
    assert _summary(tmp_path, _observe())["ok"] is True
    assert _summary(tmp_path, _observe(judges=_judges(score=10)))["ok"] is False
    assert _summary(tmp_path, _observe(source_sha=""))["ok"] is False
    assert _summary(tmp_path, _observe(records=_complete_records(GOLDEN_CASE_IDS[1:])))["ok"] is False


def test_no_non_pass_result_class_ever_reports_ok(tmp_path):
    variants = [
        _observe(judges=_judges(score=10)),
        _observe(source_sha=""),
        _observe(judges={**_judges(), GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_MALFORMED)}),
        _observe(
            judges={
                **_judges(),
                GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_PROVIDER_OR_BATCH_FAILURE),
            }
        ),
        _observe(records=_complete_records(GOLDEN_CASE_IDS[1:])),
    ]
    for observation in variants:
        summary = _summary(tmp_path, observation)
        assert summary["ok"] is False, summary["result_class"]
        assert summary["result_class"] != release_gates.RESULT_PASS


def test_no_raw_provider_or_prompt_text_leaks_into_the_report(tmp_path):
    poisoned = dict(CASES_BY_ID[GOLDEN_CASE_IDS[0]])
    output = {
        "ingestion_metadata": {
            "eval_errors": [
                "classify: Provider call failed: category=quota_exceeded, provider=anthropic, "
                f"model=claude-sonnet-4-6{batch.PROVIDER_DETAIL_MARKER}"
                "raw provider body: sk-ant-LEAKED-KEY / Authorization: Bearer XYZ"
            ]
        }
    }
    record = batch.pipeline_record_for_output(poisoned, output)
    observation = _observe(
        records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])],
        judges={**_judges(), GOLDEN_CASE_IDS[0]: batch.JudgeObservation(batch.JUDGE_MALFORMED)},
    )
    summary = _summary(tmp_path, observation)

    # `cases[*].pipeline_detail`, `judge_detail` and `validity_errors` are the
    # diagnostic surfaces; none of them may carry provider or credential text.
    diagnostics = json.dumps(
        {
            "validity_errors": summary["validity_errors"],
            "diagnostics": [
                {
                    "pipeline_detail": entry["pipeline_detail"],
                    "judge_detail": entry["judge_detail"],
                    "judge_rationale": entry["judge_rationale"],
                }
                for entry in summary["cases"]
            ],
        }
    )
    for forbidden in ("sk-ant", "Bearer", "Authorization", "raw provider body"):
        assert forbidden not in diagnostics, forbidden


def test_judge_rationale_is_bounded_and_whitespace_collapsed():
    observation = batch.parse_judge_payload(
        json.dumps({"score": 90, "rationale": "a\n\nb\t" + "x" * 5000})
    )

    assert observation.status == batch.JUDGE_VALID
    assert len(observation.rationale) <= batch.JUDGE_RATIONALE_MAX_CHARS
    assert "\n" not in observation.rationale


def test_every_case_entry_carries_its_own_observation_status(tmp_path):
    summary = _summary(tmp_path, _observe())

    for entry in summary["cases"]:
        assert entry["pipeline_status"] in batch.PIPELINE_STATUSES
        assert entry["judge_status"] in batch.JUDGE_STATUSES
        assert isinstance(entry["quality_measured"], bool)


# ═══════════════════════ zero-provider guarantees ═══════════════════════


def test_building_an_observation_makes_no_provider_call(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the nightly validity contract made a provider call")

    import llm_client

    monkeypatch.setattr(llm_client, "call_llm", explode)
    observation = _observe()

    assert observation.valid_observation is True


def test_module_declares_no_second_provider_credential():
    source = Path(batch.__file__).read_text()

    assert "OPENAI_API_KEY" not in source
    assert source.count("ANTHROPIC_API_KEY") == 3  # submit, wait, collect clients
