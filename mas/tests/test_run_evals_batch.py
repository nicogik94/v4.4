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

import asyncio
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


def _healthy_phase_status(phases=batch.REAL_CASE_PHASES) -> dict:
    """`phase_status` as the orchestrator leaves it after a clean run."""

    return {phase: "completed" for phase in phases}


def _passing_output(case: dict) -> dict:
    """A pipeline output that clears every deterministic check for `case`.

    Carries the phase bookkeeping a real `run_case_real` state carries, because
    completion is now proven from that bookkeeping rather than assumed from the
    absence of a recorded exception.
    """

    return {
        "classify": {"domain": case["expected_domain"]},
        "hypotheses": [{"id": f"H{index + 1}"} for index in range(case["min_hypotheses"])],
        "strategy": {
            "frameworks": list(case.get("must_contain_frameworks", [])),
            "notes": list(case.get("strategy_must_mention", [])),
        },
        "phase_status": _healthy_phase_status(),
        "phase_failure_details": {},
    }


def _failed_phase_output(case: dict, failures: dict, *, completed=()) -> dict:
    """A pipeline output whose `failures` phases failed with a typed category.

    Mirrors what the orchestrator records: a failed phase gets
    `phase_status="failed"` and a `phase_failure_details` entry, and it does
    *not* raise, so nothing lands in `ingestion_metadata.eval_errors`.
    """

    output = dict(_passing_output(case))
    status = {}
    details = {}
    for phase in batch.REAL_CASE_PHASES:
        if phase in failures:
            status[phase] = "failed"
            details[phase] = {
                "phase": phase,
                "category": failures[phase],
                "message": f"diagnostic for {phase}",
                "captured_at": "2026-08-16T00:00:00",
            }
        elif phase in completed:
            status[phase] = "completed"
        else:
            status[phase] = "pending"
    output["phase_status"] = status
    output["phase_failure_details"] = details
    return output


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
    execution_mode: str = batch.EXECUTION_MODE_BATCH,
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
        execution_mode=execution_mode,
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
    assert record.category == batch.UNTYPED_PHASE_FAILURE

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is False
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE


def test_confused_halt_is_a_measured_outcome_not_a_failure():
    """The product deliberately stops after a Confused classification.

    The state this leaves behind is the halt marker plus a completed `classify`
    and four phases that were never run. That must stay a *measured* outcome:
    the phases are missing because the product decided to stop, not because
    anything failed.
    """

    output = dict(_passing_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]]))
    output["phase_status"] = {
        "classify": "completed",
        "hypotheses": "pending",
        "gauntlet": "pending",
        "audit": "pending",
        "strategy": "pending",
    }
    output["ingestion_metadata"] = {"eval_errors": [batch.EVAL_ERROR_CONFUSED_HALT]}
    record = batch.pipeline_record_for_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]], output)

    assert record.status == batch.PIPELINE_COMPLETE

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is True
    assert observation.result_class == release_gates.RESULT_PASS


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


# ═══════════════ MAJOR-1 — resume source-SHA attribution ═══════════════
#
# A batch's pipeline outputs are produced at the *submitting* checkout. A
# `--resume` may run days later from any checkout at all, so the submit-time
# commit is persisted with the outputs and the resume binds to it. Every
# variant below proves the same thing from a different angle: a resume that
# cannot prove it is scoring outputs from the commit it claims must not be able
# to produce valid evidence.

SHA_A = "a1b2c3d4" * 5
SHA_B = "f9e8d7c6" * 5
FAKE_BATCH_ID = "msgbatch_deterministic"


class _ProviderCalls:
    """Counts every provider entry point the CLI could reach. All must stay 0
    except where a test deliberately exercises a successful collection."""

    def __init__(self) -> None:
        self.submits = 0
        self.waits = 0
        self.collects = 0
        # What the CLI actually handed the scheduling/budget seams.
        self.concurrency = None
        self.max_wait = None

    @property
    def total(self) -> int:
        return self.submits + self.waits + self.collects


def _install_fake_batch_api(
    monkeypatch, tracker: _ProviderCalls, *, judges=None, wait_budget_exhausted: bool = False
):
    """Replace the three provider-touching coroutines with counters.

    Nothing here opens a socket or reads a credential; the batch API is never
    reached, so these tests can assert on the resume contract without spend.
    """

    async def fake_pipeline(cases, mock, concurrency=batch.DEFAULT_CASE_CONCURRENCY):
        tracker.concurrency = concurrency
        return _complete_records([case["id"] for case in cases])

    async def fake_submit(requests):
        tracker.submits += 1
        return FAKE_BATCH_ID

    async def fake_wait(batch_id, poll_interval=30, max_wait=batch.DEFAULT_BATCH_WAIT_SECONDS):
        tracker.waits += 1
        tracker.max_wait = max_wait
        if wait_budget_exhausted:
            raise batch.BatchWaitBudgetExhausted(batch_id, max_wait)
        return {}

    async def fake_collect(batch_id):
        tracker.collects += 1
        return _judges() if judges is None else judges

    monkeypatch.setattr(batch, "run_pipeline_for_all_cases", fake_pipeline)
    monkeypatch.setattr(batch, "submit_batch", fake_submit)
    monkeypatch.setattr(batch, "wait_for_batch", fake_wait)
    monkeypatch.setattr(batch, "collect_batch_results", fake_collect)


def _run_cli(monkeypatch, argv: list[str]) -> int:
    """Drive `main()` end to end and return the process exit code."""

    monkeypatch.setattr(sys, "argv", ["run_evals_batch", *argv])
    try:
        asyncio.run(batch.main())
    except SystemExit as exc:  # noqa: PERF203 - the CLI's own exit path
        return int(exc.code or 0)
    return 0


def _cache_path(tmp_path: Path, batch_id: str = FAKE_BATCH_ID) -> Path:
    return tmp_path / f"batch_inputs_{batch_id}.json"


def _submit_at(tmp_path, monkeypatch, tracker, sha: str) -> dict:
    """Submit a batch at `sha` and return the cache it persisted."""

    monkeypatch.setenv("GITHUB_SHA", sha)
    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--submit-only"]) == 0
    return json.loads(_cache_path(tmp_path).read_text())


def _summary_on_disk(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "summary_batch.json").read_text())


def _seed_cache(tmp_path: Path, payload: object, batch_id: str = FAKE_BATCH_ID) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _cache_path(tmp_path, batch_id).write_text(json.dumps(payload, default=str))


def _cached_entries() -> list[dict]:
    return [
        {"case": CASES_BY_ID[cid], "output": _passing_output(CASES_BY_ID[cid])}
        for cid in GOLDEN_CASE_IDS
    ]


# ── 1. submit persists the exact commit it ran at ──


def test_submit_persists_the_exact_source_sha_it_ran_at(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)

    cached = _submit_at(tmp_path, monkeypatch, tracker, SHA_A)

    assert cached["schema_version"] == batch.BATCH_INPUTS_SCHEMA_VERSION
    assert cached["source_sha"] == SHA_A
    assert cached["source_sha_origin"] == "github_sha"
    assert [entry["case"]["id"] for entry in cached["cases"]] == GOLDEN_CASE_IDS
    # Submission alone must not collect anything.
    assert (tracker.waits, tracker.collects) == (0, 0)


# ── 2. a same-commit resume uses the persisted commit ──


def test_resume_at_the_submitting_commit_uses_the_cached_sha(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    _submit_at(tmp_path, monkeypatch, tracker, SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert summary["source_sha"] == SHA_A
    assert summary["source_sha_origin"] == batch.SOURCE_SHA_ORIGIN_RESUME_CACHE
    assert summary["valid_observation"] is True
    assert summary["quality_measured"] is True
    assert summary["result_class"] == release_gates.RESULT_PASS
    assert summary["ok"] is True
    assert summary["mode"] == batch.EXECUTION_MODE_BATCH
    # Denominator, provider and parser semantics are untouched by the binding.
    assert summary["passed"] == len(GOLDEN_CASE_IDS)
    assert summary["total"] == len(GOLDEN_CASE_IDS)
    assert summary["expected_case_ids"] == GOLDEN_CASE_IDS
    assert exit_code == 0
    assert tracker.collects == 1


# ── 3. a cross-commit resume cannot produce evidence ──


def test_resuming_from_a_different_commit_cannot_produce_valid_evidence(tmp_path, monkeypatch):
    """The exact defect: outputs made at A, resumed at B, reported as B."""

    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    _submit_at(tmp_path, monkeypatch, tracker, SHA_A)

    monkeypatch.setenv("GITHUB_SHA", SHA_B)
    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    # The artifact never relabels A's outputs with B.
    assert summary["source_sha"] == SHA_A
    assert summary["source_sha"] != SHA_B
    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["ok"] is False
    assert summary["result_class"] == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert batch.VALIDITY_SOURCE_SHA_MISMATCH in {
        entry["code"] for entry in summary["validity_errors"]
    }
    assert exit_code == 1
    # And no provider work is done chasing results that can never be evidence.
    assert (tracker.waits, tracker.collects) == (0, 0)


# ── 4. a pre-wave cache carries no commit ──


def test_a_cache_without_a_source_sha_cannot_produce_valid_evidence(tmp_path, monkeypatch):
    """The old cache layout was a bare list. It is readable, never valid."""

    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    _seed_cache(tmp_path, _cached_entries())
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert summary["source_sha"] == ""
    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["ok"] is False
    assert summary["result_class"] == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert batch.VALIDITY_SOURCE_SHA_MISSING in {
        entry["code"] for entry in summary["validity_errors"]
    }
    assert exit_code == 1
    assert (tracker.waits, tracker.collects) == (0, 0)
    # Still fully diagnosable: the operator sees which cases were in the cache.
    assert summary["observed_case_ids"] == GOLDEN_CASE_IDS


def test_a_cache_with_an_empty_source_sha_is_treated_as_missing(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    _seed_cache(tmp_path, {"source_sha": "   ", "cases": _cached_entries()})
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID]) == 1
    summary = _summary_on_disk(tmp_path)

    assert summary["valid_observation"] is False
    assert batch.VALIDITY_SOURCE_SHA_MISSING in {
        entry["code"] for entry in summary["validity_errors"]
    }


# ── 5. a malformed cached commit ──


@pytest.mark.parametrize(
    "malformed",
    [
        "HEAD",
        "main",
        "refs/heads/stabilization",
        "a1b2c3d",            # short sha
        "a" * 39,
        "a" * 41,
        "z" * 40,             # not hex
        f"{SHA_A} {SHA_B}",   # ambiguous: two commits in one field
        12345,
        [SHA_A],
        {},
    ],
)
def test_a_malformed_cached_sha_cannot_produce_valid_evidence(tmp_path, monkeypatch, malformed):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    _seed_cache(tmp_path, {"source_sha": malformed, "cases": _cached_entries()})
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert summary["source_sha"] == ""
    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["ok"] is False
    assert summary["result_class"] == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert exit_code == 1
    assert (tracker.waits, tracker.collects) == (0, 0)
    # The malformed value is untrusted on-disk content and is never echoed.
    assert str(malformed) not in json.dumps(summary["validity_errors"])


def test_an_unreadable_resume_cache_fails_closed(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    tmp_path.mkdir(parents=True, exist_ok=True)
    _cache_path(tmp_path).write_text("{not json at all")
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert exit_code == 1
    assert summary["valid_observation"] is False
    assert summary["result_class"] == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert (tracker.waits, tracker.collects) == (0, 0)


# ── 6. a mismatch reaches neither quality verdict ──


@pytest.mark.parametrize("score", [95, 55, 0])
def test_a_resume_mismatch_reaches_neither_pass_nor_quality_failure(score):
    """Whatever the judges said, an unattributable resume is not a verdict."""

    _, _, error = batch.resolve_resume_source_sha({"source_sha": SHA_A}, SHA_B)
    observation = _observe(harness_errors=[error], judges=_judges(score=score))

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert observation.result_class != release_gates.RESULT_PASS
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
    assert observation.result_class in release_gates.GATE_RESULTS


@pytest.mark.parametrize(
    "payload,checkout,expected_sha,expected_code",
    [
        ({"source_sha": SHA_A}, SHA_A, SHA_A, None),
        ({"source_sha": SHA_A.upper()}, SHA_A, SHA_A, None),
        ({"source_sha": SHA_A}, SHA_B, SHA_A, batch.VALIDITY_SOURCE_SHA_MISMATCH),
        ({"source_sha": SHA_A}, "", SHA_A, batch.VALIDITY_SOURCE_SHA_MISSING),
        ({"source_sha": SHA_A}, "HEAD", SHA_A, batch.VALIDITY_SOURCE_SHA_MISSING),
        ({"cases": []}, SHA_A, "", batch.VALIDITY_SOURCE_SHA_MISSING),
        ([], SHA_A, "", batch.VALIDITY_SOURCE_SHA_MISSING),
        (None, SHA_A, "", batch.VALIDITY_SOURCE_SHA_MISSING),
        ({"source_sha": "HEAD"}, SHA_A, "", batch.VALIDITY_SOURCE_SHA_MALFORMED),
    ],
)
def test_resume_attribution_table(payload, checkout, expected_sha, expected_code):
    sha, origin, error = batch.resolve_resume_source_sha(payload, checkout)

    assert sha == expected_sha
    if expected_code is None:
        assert error is None
        assert origin == batch.SOURCE_SHA_ORIGIN_RESUME_CACHE
    else:
        assert error is not None
        assert error["code"] == expected_code
        assert error["code"] in batch.ATTRIBUTION_AND_HARNESS_CODES


def test_every_resume_attribution_failure_classifies_as_non_quality():
    for code in (
        batch.VALIDITY_SOURCE_SHA_MISSING,
        batch.VALIDITY_SOURCE_SHA_MALFORMED,
        batch.VALIDITY_SOURCE_SHA_MISMATCH,
    ):
        classification = batch.classify_result(
            [{"code": code, "case_ids": [], "detail": ""}],
            pass_rate=1.0,
            threshold=PASS_THRESHOLD,
        )
        assert classification == release_gates.RESULT_INFRASTRUCTURE_FAILURE
        assert classification in release_gates.GATE_RESULTS


def test_mismatch_diagnostics_are_a_closed_pair_of_commit_identities():
    _, _, error = batch.resolve_resume_source_sha({"source_sha": SHA_A}, SHA_B)

    assert error["detail"] == f"submitted_at={SHA_A} resumed_at={SHA_B}"
    # Nothing but validated hex can reach this field.
    assert set(error["detail"]) <= set("submited_arn=0123456789abcdef ")


# ── 7. the ordinary non-resume nightly is unchanged ──


def test_a_standard_non_resume_nightly_is_unchanged(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path)])
    summary = _summary_on_disk(tmp_path)

    assert exit_code == 0
    assert summary["mode"] == batch.EXECUTION_MODE_BATCH
    assert summary["source_sha"] == SHA_A
    assert summary["source_sha_origin"] == "github_sha"
    assert summary["valid_observation"] is True
    assert summary["quality_measured"] is True
    assert summary["result_class"] == release_gates.RESULT_PASS
    assert summary["ok"] is True
    assert summary["passed"] == summary["total"] == len(GOLDEN_CASE_IDS)
    assert summary["validity_errors"] == []
    # It still caches an attributable input set for a later resume.
    assert json.loads(_cache_path(tmp_path).read_text())["source_sha"] == SHA_A


# ═══════════════ MAJOR-2 — mock artifact truthfulness ═══════════════
#
# `--mock` fabricates its judge scores. Before this wave its artifact was
# field-for-field indistinguishable from a real measured pass. The discriminator
# is now the declared `mode` and the typed `JUDGE_SYNTHETIC` status — never the
# judge's prose.

REAL_PASS_CONTRACT = {
    "mode": batch.EXECUTION_MODE_BATCH,
    "valid_observation": True,
    "quality_measured": True,
    "result_class": release_gates.RESULT_PASS,
    "ok": True,
}


def _mock_summary(tmp_path, monkeypatch, sha: str = SHA_A) -> tuple[dict, int]:
    monkeypatch.setenv("GITHUB_SHA", sha)
    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--mock"])
    return _summary_on_disk(tmp_path), exit_code


def _synthetic_judges(score: int = 99, rationale: str = "mock"):
    return {
        cid: batch.JudgeObservation(batch.JUDGE_SYNTHETIC, score=score, rationale=rationale)
        for cid in GOLDEN_CASE_IDS
    }


def test_mock_summary_declares_an_explicit_mock_mode(tmp_path, monkeypatch):
    summary, _ = _mock_summary(tmp_path, monkeypatch)

    assert summary["mode"] == batch.EXECUTION_MODE_MOCK
    assert summary["mode"] in batch.EXECUTION_MODES
    assert all(
        entry["judge_status"] == batch.JUDGE_SYNTHETIC for entry in summary["cases"]
    )


def test_mock_is_not_a_valid_observation(tmp_path, monkeypatch):
    summary, _ = _mock_summary(tmp_path, monkeypatch)

    assert summary["valid_observation"] is False


def test_mock_never_claims_measured_quality(tmp_path, monkeypatch):
    summary, _ = _mock_summary(tmp_path, monkeypatch)

    assert summary["quality_measured"] is False
    assert all(entry["quality_measured"] is False for entry in summary["cases"])


def test_mock_is_never_ok(tmp_path, monkeypatch):
    summary, _ = _mock_summary(tmp_path, monkeypatch)

    assert summary["ok"] is False


def test_mock_result_class_is_a_non_quality_classification(tmp_path, monkeypatch):
    summary, _ = _mock_summary(tmp_path, monkeypatch)

    assert summary["result_class"] in release_gates.GATE_RESULTS
    assert summary["result_class"] != release_gates.RESULT_PASS
    assert summary["result_class"] != release_gates.RESULT_QUALITY_FAILURE
    assert batch.VALIDITY_SYNTHETIC_JUDGE_SCORES in {
        entry["code"] for entry in summary["validity_errors"]
    }


def test_no_mock_artifact_can_be_contract_equivalent_to_a_real_valid_pass(tmp_path, monkeypatch):
    """The authoritative fields must differ on every single one."""

    mock_summary, _ = _mock_summary(tmp_path, monkeypatch)

    for field, real_value in REAL_PASS_CONTRACT.items():
        assert mock_summary[field] != real_value, field

    # And the numerator itself never absorbs a fabricated score.
    assert mock_summary["passed"] == 0
    assert mock_summary["pass_rate"] == 0.0
    assert all(entry["passed"] is None for entry in mock_summary["cases"])
    assert all(entry["judge_overall"] is None for entry in mock_summary["cases"])


def test_a_perfect_synthetic_run_still_reaches_neither_quality_verdict():
    """Synthetic 12/12, scored 99, and still not evidence."""

    observation = _observe(judges=_synthetic_judges(score=99))

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert observation.passed == 0
    assert observation.pass_rate == 0.0
    assert observation.result_class != release_gates.RESULT_PASS
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
    assert observation.result_class in release_gates.GATE_RESULTS


def test_real_batch_mode_still_reports_the_normal_mode(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    _run_cli(monkeypatch, ["--report", str(tmp_path)])

    assert _summary_on_disk(tmp_path)["mode"] == batch.EXECUTION_MODE_BATCH


def test_a_genuine_measured_pass_is_unchanged(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path)])
    summary = _summary_on_disk(tmp_path)

    for field, real_value in REAL_PASS_CONTRACT.items():
        assert summary[field] == real_value, field
    assert summary["synthetic_smoke_ok"] is None
    assert all(entry["quality_measured"] is True for entry in summary["cases"])
    assert all(entry["synthetic_passed"] is None for entry in summary["cases"])
    assert exit_code == 0


def test_a_measured_quality_failure_is_still_reachable(tmp_path, monkeypatch):
    """The one verdict mock must never reach must stay reachable for real runs."""

    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker, judges=_judges(score=20))
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path)])
    summary = _summary_on_disk(tmp_path)

    assert summary["mode"] == batch.EXECUTION_MODE_BATCH
    assert summary["valid_observation"] is True
    assert summary["quality_measured"] is True
    assert summary["result_class"] == release_gates.RESULT_QUALITY_FAILURE
    assert summary["ok"] is False
    assert exit_code == 1


def test_judge_rationale_alone_is_not_the_evidence_discriminator():
    """Both halves: prose cannot disqualify, and prose cannot qualify."""

    # A real, measured judge result that happens to say "mock" is still evidence.
    real = _observe(
        judges={
            cid: batch.JudgeObservation(batch.JUDGE_VALID, score=90, rationale="mock")
            for cid in GOLDEN_CASE_IDS
        }
    )
    assert real.valid_observation is True
    assert real.result_class == release_gates.RESULT_PASS

    # A synthetic result dressed in convincing prose is still not evidence.
    synthetic = _observe(
        judges=_synthetic_judges(score=99, rationale="thorough, well-evidenced analysis")
    )
    assert synthetic.valid_observation is False
    assert batch.VALIDITY_SYNTHETIC_JUDGE_SCORES in _codes(synthetic)


def test_a_synthetic_result_smuggled_into_a_batch_run_still_fails_closed():
    """Defence in depth: the judge statuses invalidate on their own."""

    observation = _observe(
        judges={**_judges(), GOLDEN_CASE_IDS[0]: batch.JudgeObservation(
            batch.JUDGE_SYNTHETIC, score=100, rationale="ok"
        )},
        execution_mode=batch.EXECUTION_MODE_BATCH,
    )

    assert observation.valid_observation is False
    assert batch.VALIDITY_SYNTHETIC_JUDGE_SCORES in _codes(observation)
    assert observation.result_class != release_gates.RESULT_PASS
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE


def test_an_unknown_execution_mode_fails_closed():
    observation = _observe(execution_mode="totally_new_mode")

    assert observation.execution_mode in batch.EXECUTION_MODES
    assert observation.valid_observation is False
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE


# ── the provider-free smoke is preserved ──


def test_the_synthetic_smoke_keeps_its_own_separate_verdict(tmp_path, monkeypatch):
    """Mock still exits 0 when the harness worked — evidence is a separate axis."""

    summary, exit_code = _mock_summary(tmp_path, monkeypatch)

    assert summary["synthetic_smoke_ok"] is True
    assert exit_code == 0
    # Every case still ran the deterministic plumbing end to end.
    assert summary["observed_case_ids"] == GOLDEN_CASE_IDS
    assert all(entry["synthetic_passed"] is True for entry in summary["cases"])
    # ...but none of that is evidence.
    assert summary["ok"] is False
    assert summary["valid_observation"] is False


def test_a_broken_synthetic_harness_still_exits_nonzero():
    observation = batch.build_observation(
        pipeline_records=[
            batch.PipelineRecord(
                CASES_BY_ID[GOLDEN_CASE_IDS[0]], {}, batch.PIPELINE_HARNESS_FAILURE, "boom"
            ),
            *_complete_records(GOLDEN_CASE_IDS[1:]),
        ],
        judge_observations=_synthetic_judges(),
        expected_case_ids=GOLDEN_CASE_IDS,
        golden_case_ids=GOLDEN_CASE_IDS,
        threshold=PASS_THRESHOLD,
        source_sha=SOURCE_SHA,
        source_sha_origin="github_sha",
        execution_mode=batch.EXECUTION_MODE_MOCK,
    )

    assert observation.synthetic_smoke_ok is False
    assert observation.valid_observation is False


def test_the_mock_smoke_verdict_never_leaks_into_the_evidence_fields(tmp_path):
    observation = batch.build_observation(
        pipeline_records=_complete_records(),
        judge_observations=_synthetic_judges(),
        expected_case_ids=GOLDEN_CASE_IDS,
        golden_case_ids=GOLDEN_CASE_IDS,
        threshold=PASS_THRESHOLD,
        source_sha=SOURCE_SHA,
        source_sha_origin="github_sha",
        execution_mode=batch.EXECUTION_MODE_MOCK,
    )
    summary = batch.write_report(observation, tmp_path, batch_id=None)

    assert summary["synthetic_smoke_ok"] is True
    assert summary["ok"] is False
    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["passed"] == 0


def test_the_cli_makes_no_provider_call_on_any_invalid_resume(tmp_path, monkeypatch):
    """One assertion over every fail-closed resume shape: zero provider calls."""

    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    caches = [
        _cached_entries(),                                        # pre-wave layout
        {"cases": _cached_entries()},                             # no commit
        {"source_sha": "HEAD", "cases": _cached_entries()},       # malformed
        {"source_sha": SHA_B, "cases": _cached_entries()},        # different commit
    ]
    for index, payload in enumerate(caches):
        report_dir = tmp_path / f"run{index}"
        _seed_cache(report_dir, payload)
        exit_code = _run_cli(
            monkeypatch, ["--report", str(report_dir), "--resume", FAKE_BATCH_ID]
        )
        summary = _summary_on_disk(report_dir)

        assert exit_code == 1, payload
        assert summary["valid_observation"] is False
        assert summary["quality_measured"] is False
        assert summary["ok"] is False
        assert summary["result_class"] != release_gates.RESULT_PASS
        assert summary["result_class"] != release_gates.RESULT_QUALITY_FAILURE

    assert tracker.total == 0


# ═══════════ MAJOR-3 — real pipeline phase failures are not completions ═══════════
#
# `run_case_real` drives the real orchestrator, and the orchestrator does not
# raise on an ordinary `LLMResponse(ok=False)`: it marks the phase failed,
# records a typed category in `phase_failure_details`, and returns the state.
# Nothing reaches `ingestion_metadata.eval_errors`, so a nightly that consulted
# `eval_errors` alone declared a case whose every phase was provider-dead
# `complete` — and a run of twelve such cases could be reported as a measured
# 0/12 quality failure, or, with eleven healthy cases beside it, as a pass.
#
# The tests below are provider-free: `call_llm` is replaced with a stub, so the
# state under test is produced by the real product code with no network, no
# credential and no spend.


def _dead_provider_stub(counter: list, *, error_type: str = "quota_exceeded", text: str = ""):
    """A `call_llm` replacement that fails the way a real provider outage does."""

    import llm_client

    async def stub(phase, system, prompt, **kwargs):
        counter.append(phase)
        return llm_client.LLMResponse(
            ok=False,
            text=text,
            error_type=error_type,
            error="your credit balance is too low; org_id=org-123 key=sk-ant-LEAKED",
        )

    return stub


def _real_state_with_dead_provider(case: dict, monkeypatch, **kwargs) -> tuple[dict, list]:
    """Drive the REAL `run_case_real` with a stubbed provider. Zero network."""

    import orchestrator
    from evals.run_evals import run_case_real

    calls: list = []
    monkeypatch.setattr(orchestrator, "call_llm", _dead_provider_stub(calls, **kwargs))
    state = asyncio.run(run_case_real(case))
    return state.model_dump(mode="json"), calls


# ── A. the shape the real product emits ──


def test_real_pipeline_provider_failure_is_not_a_completion(monkeypatch):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, calls = _real_state_with_dead_provider(case, monkeypatch)

    # The product recorded the failure where it actually records it...
    assert "failed" in set(output["phase_status"].values())
    assert output["phase_failure_details"]
    assert any(
        entry["category"] in batch.PROVIDER_PHASE_FAILURE_KINDS
        for entry in output["phase_failure_details"].values()
    )
    # ...and deliberately not as a raised eval-harness exception.
    assert not (output.get("ingestion_metadata") or {}).get("eval_errors")

    record = batch.pipeline_record_for_output(case, output)

    assert record.status != batch.PIPELINE_COMPLETE
    assert record.complete is False
    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category == "quota_exceeded"
    # The stub answered locally; nothing left the process.
    assert calls


def test_a_generic_provider_error_is_also_a_provider_failure(monkeypatch):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, _ = _real_state_with_dead_provider(case, monkeypatch, error_type="api_error")

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category in batch.PROVIDER_PHASE_FAILURE_KINDS


# ── B. one dead case among valid judges ──


def test_a_real_dead_pipeline_case_cannot_be_a_quality_failure(monkeypatch):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, _ = _real_state_with_dead_provider(case, monkeypatch)
    record = batch.pipeline_record_for_output(case, output)

    observation = _observe(
        records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])], judges=_judges()
    )

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert batch.VALIDITY_PIPELINE_PROVIDER_FAILURE in _codes(observation)
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert observation.result_class != release_gates.RESULT_PASS
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
    assert _case_entry(observation, GOLDEN_CASE_IDS[0])["quality_measured"] is False


# ── C. every case provider-dead, judges otherwise fine ──


def test_twelve_dead_pipeline_cases_report_provider_unavailable(monkeypatch):
    """The exact defect: 12 dead cases + good judges used to read 0/12 quality."""

    records = []
    for case_id in GOLDEN_CASE_IDS:
        case = CASES_BY_ID[case_id]
        output, _ = _real_state_with_dead_provider(case, monkeypatch)
        records.append(batch.pipeline_record_for_output(case, output))

    assert all(record.status == batch.PIPELINE_PROVIDER_FAILURE for record in records)

    observation = _observe(records=records, judges=_judges())

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
    assert observation.passed == 0
    # No product quality verdict was reached for any case.
    assert all(entry["quality_measured"] is False for entry in observation.cases)
    assert all(entry["passed"] is None for entry in observation.cases)
    assert all(entry["judge_overall"] is None for entry in observation.cases)


# ── D. one dead case, eleven healthy, good judges: never a pass ──


def test_one_dead_case_among_eleven_healthy_ones_can_never_pass(monkeypatch):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, _ = _real_state_with_dead_provider(case, monkeypatch)
    records = [
        batch.pipeline_record_for_output(case, output),
        *_complete_records(GOLDEN_CASE_IDS[1:]),
    ]

    observation = _observe(records=records, judges=_judges(score=100))

    assert observation.valid_observation is False
    assert observation.result_class != release_gates.RESULT_PASS
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE
    # The denominator stays the full expected universe...
    assert observation.total == len(GOLDEN_CASE_IDS)
    assert observation.expected_case_ids == GOLDEN_CASE_IDS
    # ...and the dead case is not counted as measured quality.
    assert observation.passed == len(GOLDEN_CASE_IDS) - 1
    assert _case_entry(observation, GOLDEN_CASE_IDS[0])["quality_measured"] is False


# ── E. policy_blocked is a non-provider failure ──


def test_policy_blocked_phase_failure_is_not_attributed_to_a_provider():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = _failed_phase_output(case, {"classify": "policy_blocked"})

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.status != batch.PIPELINE_COMPLETE
    assert record.category == "policy_blocked"

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    assert observation.valid_observation is False
    assert batch.VALIDITY_PIPELINE_HARNESS_FAILURE in _codes(observation)
    assert batch.VALIDITY_PIPELINE_PROVIDER_FAILURE not in _codes(observation)
    assert observation.result_class == release_gates.RESULT_INFRASTRUCTURE_FAILURE
    assert observation.result_class != release_gates.RESULT_QUALITY_FAILURE


@pytest.mark.parametrize(
    "category",
    ["phase_configuration", "prerequisite_failed", "json_parse", "schema_validation"],
)
def test_other_known_non_provider_categories_are_harness_failures(category):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    record = batch.pipeline_record_for_output(
        case, _failed_phase_output(case, {"strategy": category}, completed=("classify", "hypotheses", "gauntlet", "audit"))
    )

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == category


# ── F. the raised-exception path is untouched ──


def test_a_raised_phase_exception_still_invalidates_the_case():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["ingestion_metadata"] = {
        "eval_errors": [
            "classify: Provider call failed: category=quota_exceeded, provider=anthropic"
        ]
    }

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category == "quota_exceeded"


def test_a_raised_exception_outranks_an_otherwise_healthy_phase_state():
    """`eval_errors` still counts even when `phase_status` says everything ran."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["ingestion_metadata"] = {"eval_errors": ["audit: RuntimeError('boom')"]}

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == batch.UNTYPED_PHASE_FAILURE


def test_an_unattributable_eval_error_still_invalidates_the_case():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["ingestion_metadata"] = {"eval_errors": ["something went wrong with no phase prefix"]}

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.complete is False


# ── G/H. the Confused halt and the healthy pipeline ──


def test_the_confused_halt_survives_the_phase_state_check():
    """Only `classify` was expected to run, so four pending phases are fine."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = {
        "classify": "completed",
        "hypotheses": "pending",
        "gauntlet": "pending",
        "audit": "pending",
        "strategy": "pending",
    }
    output["ingestion_metadata"] = {"eval_errors": [batch.EVAL_ERROR_CONFUSED_HALT]}

    assert batch.expected_real_phases(output) == batch.REAL_CASE_PHASES[:1]

    record = batch.pipeline_record_for_output(case, output)
    assert record.status == batch.PIPELINE_COMPLETE
    assert record.complete is True


def test_a_confused_halt_whose_classify_failed_is_still_not_complete():
    """The halt marker is not a licence to skip proving `classify` ran."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = _failed_phase_output(case, {"classify": "quota_exceeded"})
    output["ingestion_metadata"] = {"eval_errors": [batch.EVAL_ERROR_CONFUSED_HALT]}

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE


def test_a_fully_healthy_pipeline_state_stays_complete():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    record = batch.pipeline_record_for_output(case, _passing_output(case))

    assert record.status == batch.PIPELINE_COMPLETE
    assert record.category == ""

    observation = _observe()
    assert observation.valid_observation is True
    assert observation.result_class == release_gates.RESULT_PASS


# ── completion is proven, never assumed ──


@pytest.mark.parametrize("status", ["pending", "running", "stale"])
def test_a_phase_that_never_finished_is_not_a_completion(status):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = {**_healthy_phase_status(), "strategy": status}

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == f"{batch.PHASE_STATE_CATEGORY_PREFIX}{status}"


@pytest.mark.parametrize(
    "phase_status, expected_detail",
    [
        (None, "phase_state_unreadable"),
        ("not a dict", "phase_state_unreadable"),
        ({}, "phase_state_missing"),
        ({"classify": "invented_status"}, "phase_state_unknown"),
    ],
)
def test_missing_or_ambiguous_phase_state_fails_closed(phase_status, expected_detail):
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = phase_status

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == expected_detail


def test_a_pre_wave_output_without_phase_state_is_not_assumed_complete():
    """A `--resume` cache from before phase bookkeeping cannot prove completion."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = {
        "classify": {"domain": case["expected_domain"]},
        "hypotheses": [{"id": "H1"}],
    }

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.complete is False


# ── adversarial probes ──


def test_the_first_failure_in_canonical_order_is_the_reported_cause():
    """Probe 4: the circuit breaker opens after a quota outage.

    `audit`/`strategy` then fail `policy_blocked` as a *consequence*. Reporting
    the consequence would hide the outage that caused it.
    """

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = _failed_phase_output(
        case,
        {
            "classify": "quota_exceeded",
            "hypotheses": "quota_exceeded",
            "gauntlet": "quota_exceeded",
            "audit": "policy_blocked",
            "strategy": "policy_blocked",
        },
    )

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category == "quota_exceeded"


def test_the_real_circuit_breaker_cascade_reports_the_outage(monkeypatch):
    """The same probe, against state the real orchestrator produced."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, _ = _real_state_with_dead_provider(case, monkeypatch)
    categories = {
        entry["category"] for entry in output["phase_failure_details"].values()
    }

    assert "policy_blocked" in categories  # the breaker really did open
    assert batch.pipeline_record_for_output(case, output).category == "quota_exceeded"


def test_a_provider_failure_after_completed_phases_is_still_a_failure():
    """Probe 3: three phases genuinely completed before the provider died."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = _failed_phase_output(
        case,
        {"audit": "provider_error", "strategy": "prerequisite_failed"},
        completed=("classify", "hypotheses", "gauntlet"),
    )

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_PROVIDER_FAILURE
    assert record.category == "provider_error"


def test_a_failed_phase_with_a_perfect_judge_score_is_still_not_measured():
    """Probe 5: the judge scoring 100 on a dead pipeline changes nothing."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    record = batch.pipeline_record_for_output(
        case, _failed_phase_output(case, {"classify": "quota_exceeded"})
    )

    observation = _observe(
        records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])], judges=_judges(score=100)
    )
    entry = _case_entry(observation, GOLDEN_CASE_IDS[0])

    assert entry["quality_measured"] is False
    assert entry["judge_overall"] is None
    assert entry["passed"] is None
    assert observation.result_class == release_gates.RESULT_PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "details",
    [
        "not a dict",
        {"classify": "not a dict either"},
        {"classify": {}},
        {"classify": {"category": ""}},
        {"classify": {"category": None}},
        {"classify": {"category": ["quota_exceeded"]}},
    ],
)
def test_corrupt_phase_failure_details_never_become_a_completion(details):
    """Probe 8: a failed phase stays failed however unreadable its diagnostic."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = {**_healthy_phase_status(), "classify": "failed"}
    output["phase_failure_details"] = details

    record = batch.pipeline_record_for_output(case, output)

    assert record.complete is False
    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category in (
        batch.UNCATEGORIZED_PHASE_FAILURE,
        batch.UNKNOWN_PHASE_FAILURE_CATEGORY,
    )


def test_an_unknown_failure_category_is_bounded_and_never_provider():
    """Probe 9: a category this build does not know is not a provider outage."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    record = batch.pipeline_record_for_output(
        case, _failed_phase_output(case, {"classify": "some_future_category"})
    )

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == batch.UNKNOWN_PHASE_FAILURE_CATEGORY


def test_a_hostile_category_cannot_claim_a_provider_outage():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = {**_healthy_phase_status(), "classify": "failed"}
    output["phase_failure_details"] = {
        "classify": {"category": "quota_exceeded_but_actually_a_product_bug"}
    }

    record = batch.pipeline_record_for_output(case, output)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.category == batch.UNKNOWN_PHASE_FAILURE_CATEGORY


# ── I. no prose, prompt or credential reaches the artifact ──


SECRET_MARKERS = ("sk-ant", "org-123", "credit balance", "LEAKED", "diagnostic for")


def test_no_failure_prose_or_secret_reaches_the_pipeline_record():
    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output = dict(_passing_output(case))
    output["phase_status"] = {**_healthy_phase_status(), "classify": "failed"}
    output["phase_failure_details"] = {
        "classify": {
            "phase": "classify",
            "category": "quota_exceeded",
            "message": "credit balance too low; key=sk-ant-LEAKED org-123",
            "captured_at": "2026-08-16T00:00:00",
        }
    }

    record = batch.pipeline_record_for_output(case, output)

    assert record.category == "quota_exceeded"
    for marker in SECRET_MARKERS:
        assert marker not in record.category


def test_no_failure_prose_reaches_the_summary_artifact(tmp_path, monkeypatch):
    """End to end, from the real orchestrator's own recorded diagnostics."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    output, _ = _real_state_with_dead_provider(case, monkeypatch)
    record = batch.pipeline_record_for_output(case, output)

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])
    summary = batch.write_report(observation, tmp_path, batch_id=None)

    dead = next(item for item in summary["cases"] if item["case_id"] == GOLDEN_CASE_IDS[0])
    assert dead["pipeline_detail"] == "quota_exceeded"

    # The whole artifact, not just the fields we happen to have named.
    serialized = json.dumps(summary)
    for marker in ("sk-ant", "org-123", "credit balance", "org_id"):
        assert marker not in serialized
    for error in summary["validity_errors"]:
        assert error["code"] in batch.VALIDITY_CODES


def test_every_pipeline_detail_is_a_bounded_token():
    """No pipeline classification detail is free text."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    outputs = [
        _failed_phase_output(case, {"classify": "quota_exceeded"}),
        _failed_phase_output(case, {"classify": "policy_blocked"}),
        _failed_phase_output(case, {"classify": "some_future_category"}),
        {**_passing_output(case), "phase_status": {}},
        "not a dict at all",
    ]

    for output in outputs:
        category = batch.pipeline_record_for_output(case, output).category
        assert category == category.strip()
        assert len(category) <= 64
        assert " " not in category


def test_the_pipeline_failure_vocabulary_stays_closed():
    assert batch.PROVIDER_PHASE_FAILURE_KINDS <= batch.KNOWN_PHASE_FAILURE_KINDS
    assert "quota_exceeded" in batch.PROVIDER_PHASE_FAILURE_KINDS
    assert "provider_error" in batch.PROVIDER_PHASE_FAILURE_KINDS
    assert "policy_blocked" not in batch.PROVIDER_PHASE_FAILURE_KINDS
    assert "phase_configuration" not in batch.PROVIDER_PHASE_FAILURE_KINDS
    assert "policy_blocked" in batch.KNOWN_PHASE_FAILURE_KINDS
    assert "phase_configuration" in batch.KNOWN_PHASE_FAILURE_KINDS


def test_the_expected_phase_universe_is_the_canonical_one():
    from evals.run_evals import REAL_CASE_PHASES as canonical

    assert batch.REAL_CASE_PHASES == canonical
    assert batch.expected_real_phases({}) == canonical


def test_the_real_pipeline_tests_reach_no_provider(monkeypatch):
    """The stub answers every call locally; nothing opens a socket."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    output, calls = _real_state_with_dead_provider(case, monkeypatch)

    assert calls  # the stub, not a provider, produced every response
    assert batch.pipeline_record_for_output(case, output).complete is False


# ═══════════════════════════════════════════════════════════════════════════
# nightly completion reliability
# ═══════════════════════════════════════════════════════════════════════════
#
# Fourteen historical nightlies were killed at the workflow's 90-minute job
# timeout, twelve of them before a single judge request had been submitted. A
# SIGKILL writes no artifact, so those runs could not report even that they had
# measured nothing — the truth contract proved above is worthless in a process
# that never reaches its own `write_report`.
#
# Two bounds now keep the run inside the cap: independent cases are scheduled a
# bounded number at a time, and the batch wait is budgeted below the job
# timeout. Both are *scheduling* changes, and the tests below exist to prove
# they are only that: the same cases, the same phases, the same order in the
# report, the same product calls, and the same verdicts.
#
# Everything here is provider-free. The scheduling is driven by a stubbed
# `run_case_real`, the clock is fake, and nothing sleeps for real.


class _FakeState:
    """The `.model_dump(mode="json")` surface `run_pipeline_for_all_cases` uses."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return self._payload


class _Scheduler:
    """An instrumented `run_case_real` that records how cases were scheduled.

    "Duration" is a count of event-loop ticks rather than seconds: it makes
    completion order fully deterministic and means no test ever waits on a
    wall clock.
    """

    def __init__(self, *, ticks=None, raises=(), outputs=None) -> None:
        self.ticks = dict(ticks or {})
        self.raises = dict(raises or {})
        self.outputs = outputs or (lambda case: _passing_output(case))
        self.in_flight = 0
        self.peak_in_flight = 0
        self.started: list[str] = []
        self.finished: list[str] = []

    def install(self, monkeypatch) -> "_Scheduler":
        monkeypatch.setattr(batch, "run_case_real", self._run_case)
        return self

    async def _run_case(self, case: dict):
        case_id = case["id"]
        self.started.append(case_id)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            for _ in range(self.ticks.get(case_id, 1)):
                await asyncio.sleep(0)
            if case_id in self.raises:
                raise self.raises[case_id]
            return _FakeState(self.outputs(case))
        finally:
            self.in_flight -= 1
            self.finished.append(case_id)


def _schedule(monkeypatch, case_ids, *, concurrency, **kwargs) -> tuple[list, _Scheduler]:
    scheduler = _Scheduler(**kwargs).install(monkeypatch)
    cases = [CASES_BY_ID[cid] for cid in case_ids]
    records = asyncio.run(
        batch.run_pipeline_for_all_cases(cases, False, concurrency=concurrency)
    )
    return records, scheduler


def _inverse_ticks(case_ids) -> dict:
    """The first case takes longest, the last finishes first."""

    return {cid: len(case_ids) - index for index, cid in enumerate(case_ids)}


def _truth_fields(observation) -> dict:
    return {
        "expected_case_ids": observation.expected_case_ids,
        "observed_case_ids": observation.observed_case_ids,
        "total": observation.total,
        "passed": observation.passed,
        "pass_rate": observation.pass_rate,
        "validity_errors": observation.validity_errors,
        "valid_observation": observation.valid_observation,
        "quality_measured": observation.quality_measured,
        "result_class": observation.result_class,
    }


def _tuples(records) -> list[tuple]:
    return [(record.case_id, record.status, record.category) for record in records]


# ── 1. the report is in input order, whatever order the cases finished in ──


def test_record_order_is_input_order_not_completion_order(monkeypatch):
    records, scheduler = _schedule(
        monkeypatch, GOLDEN_CASE_IDS, concurrency=12, ticks=_inverse_ticks(GOLDEN_CASE_IDS)
    )

    # The cases genuinely completed in reverse...
    assert scheduler.finished == list(reversed(GOLDEN_CASE_IDS))
    # ...and the report is still in input order.
    assert [record.case_id for record in records] == GOLDEN_CASE_IDS


def test_inverse_completion_order_does_not_disturb_the_observation(monkeypatch):
    records, _ = _schedule(
        monkeypatch, GOLDEN_CASE_IDS, concurrency=6, ticks=_inverse_ticks(GOLDEN_CASE_IDS)
    )

    observation = _observe(records=records, judges=_judges())

    assert observation.valid_observation is True
    assert observation.observed_case_ids == GOLDEN_CASE_IDS
    assert observation.result_class == release_gates.RESULT_PASS


# ── 2. serial and concurrent produce the same records and the same truth ──


@pytest.mark.parametrize("concurrency", [2, 3, 6, 12, 50])
def test_concurrent_scheduling_is_equivalent_to_the_serial_one(monkeypatch, concurrency):
    ticks = _inverse_ticks(GOLDEN_CASE_IDS)

    serial, _ = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=1, ticks=ticks)
    concurrent, _ = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=concurrency, ticks=ticks)

    assert _tuples(concurrent) == _tuples(serial)
    assert _truth_fields(_observe(records=concurrent)) == _truth_fields(_observe(records=serial))


def test_denominator_and_validity_are_identical_under_either_schedule(monkeypatch):
    """The same mixed universe: one dead case, one blocked, the rest healthy."""

    outputs = {
        GOLDEN_CASE_IDS[0]: _failed_phase_output(CASES_BY_ID[GOLDEN_CASE_IDS[0]], {"classify": "quota_exceeded"}),
        GOLDEN_CASE_IDS[1]: _failed_phase_output(CASES_BY_ID[GOLDEN_CASE_IDS[1]], {"classify": "policy_blocked"}),
    }

    def build(case):
        return outputs.get(case["id"], _passing_output(case))

    serial, _ = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=1, outputs=build)
    concurrent, _ = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=6, outputs=build)

    assert _tuples(concurrent) == _tuples(serial)

    truth = _truth_fields(_observe(records=concurrent))
    assert truth == _truth_fields(_observe(records=serial))
    assert truth["valid_observation"] is False
    assert truth["total"] == len(GOLDEN_CASE_IDS)
    assert truth["result_class"] not in (
        release_gates.RESULT_PASS,
        release_gates.RESULT_QUALITY_FAILURE,
    )


# ── 3. the bound is real ──


@pytest.mark.parametrize("concurrency", [1, 2, 3, 6, 12])
def test_peak_cases_in_flight_never_exceeds_the_configured_bound(monkeypatch, concurrency):
    _, scheduler = _schedule(
        monkeypatch, GOLDEN_CASE_IDS, concurrency=concurrency, ticks=_inverse_ticks(GOLDEN_CASE_IDS)
    )

    assert scheduler.peak_in_flight <= concurrency
    # And the bound is actually reached: this is genuine concurrency, not a
    # serial loop that happens to satisfy an upper bound vacuously.
    assert scheduler.peak_in_flight == min(concurrency, len(GOLDEN_CASE_IDS))


def test_a_bound_larger_than_the_case_count_runs_every_case_at_once(monkeypatch):
    records, scheduler = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=100)

    assert scheduler.peak_in_flight == len(GOLDEN_CASE_IDS)
    assert [record.case_id for record in records] == GOLDEN_CASE_IDS


def test_the_default_bound_is_the_measured_six(monkeypatch):
    """The default is evidence-based: this corpus has run 6-way under Gate A."""

    assert batch.DEFAULT_CASE_CONCURRENCY == 6

    scheduler = _Scheduler(ticks=_inverse_ticks(GOLDEN_CASE_IDS)).install(monkeypatch)
    asyncio.run(batch.run_pipeline_for_all_cases([CASES_BY_ID[cid] for cid in GOLDEN_CASE_IDS], False))

    assert scheduler.peak_in_flight == batch.DEFAULT_CASE_CONCURRENCY


# ── 4. `--concurrency 1` is the old schedule, exactly ──


def test_concurrency_one_reproduces_the_serial_schedule(monkeypatch):
    _, scheduler = _schedule(
        monkeypatch, GOLDEN_CASE_IDS, concurrency=1, ticks=_inverse_ticks(GOLDEN_CASE_IDS)
    )

    # One case at a time...
    assert scheduler.peak_in_flight == 1
    # ...each finished before the next began...
    assert scheduler.started == scheduler.finished
    # ...in input order.
    assert scheduler.started == GOLDEN_CASE_IDS


# ── 5. one case's failure costs exactly one case ──


def test_a_raising_case_does_not_cancel_its_peers(monkeypatch):
    victim = GOLDEN_CASE_IDS[0]
    records, scheduler = _schedule(
        monkeypatch,
        GOLDEN_CASE_IDS,
        concurrency=6,
        raises={victim: RuntimeError("harness bug")},
    )

    assert [record.case_id for record in records] == GOLDEN_CASE_IDS
    assert len(records) == len(GOLDEN_CASE_IDS)
    # Every peer ran to completion — no gather-wide cancellation.
    assert set(scheduler.finished) == set(GOLDEN_CASE_IDS)

    failed = [record for record in records if not record.complete]
    assert [record.case_id for record in failed] == [victim]
    assert failed[0].status == batch.PIPELINE_HARNESS_FAILURE
    assert failed[0].category == "RuntimeError"


def test_a_case_that_raises_immediately_still_leaves_eleven_records(monkeypatch):
    """The failure lands before any peer has had a chance to start."""

    victim = GOLDEN_CASE_IDS[0]
    records, _ = _schedule(
        monkeypatch,
        GOLDEN_CASE_IDS,
        concurrency=6,
        ticks={victim: 0},
        raises={victim: ValueError("immediate")},
    )

    assert len(records) == len(GOLDEN_CASE_IDS)
    assert sum(1 for record in records if record.complete) == len(GOLDEN_CASE_IDS) - 1


def test_a_case_that_outlives_its_peers_still_gets_its_own_record(monkeypatch):
    straggler = GOLDEN_CASE_IDS[-1]
    ticks = {cid: 1 for cid in GOLDEN_CASE_IDS}
    ticks[straggler] = 200

    records, scheduler = _schedule(monkeypatch, GOLDEN_CASE_IDS, concurrency=6, ticks=ticks)

    assert scheduler.finished[-1] == straggler
    assert [record.case_id for record in records] == GOLDEN_CASE_IDS
    assert all(record.complete for record in records)


@pytest.mark.parametrize(
    "exc, expected_status",
    [
        (TimeoutError("transport"), batch.PIPELINE_PROVIDER_FAILURE),
        (ConnectionError("transport"), batch.PIPELINE_PROVIDER_FAILURE),
        (RuntimeError("harness"), batch.PIPELINE_HARNESS_FAILURE),
    ],
)
def test_several_simultaneously_failing_cases_stay_typed_and_non_quality(
    monkeypatch, exc, expected_status
):
    victims = GOLDEN_CASE_IDS[:3]
    records, _ = _schedule(
        monkeypatch,
        GOLDEN_CASE_IDS,
        concurrency=6,
        raises={cid: exc for cid in victims},
    )

    for record in records[:3]:
        assert record.status == expected_status
        assert record.complete is False
    assert all(record.complete for record in records[3:])

    observation = _observe(records=records, judges=_judges(score=100))

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert observation.result_class not in (
        release_gates.RESULT_PASS,
        release_gates.RESULT_QUALITY_FAILURE,
    )
    # The healthy peers keep their own records and are not blamed for the
    # failures beside them.
    for case_id in GOLDEN_CASE_IDS[3:]:
        assert _case_entry(observation, case_id)["quality_measured"] is True
    for case_id in victims:
        assert _case_entry(observation, case_id)["quality_measured"] is False


def test_an_escaped_exception_is_still_only_one_case(monkeypatch):
    """The `return_exceptions=True` backstop, exercised directly.

    `_run_one_case` classifies its own exceptions, so nothing should ever reach
    `gather`'s error path — but if anything ever did, the default behaviour
    would cancel every peer, and one case would erase eleven records.
    """

    victim = GOLDEN_CASE_IDS[0]

    async def escaping(case: dict, mock: bool):
        if case["id"] == victim:
            raise ConnectionError("escaped classification entirely")
        await asyncio.sleep(0)
        return batch.PipelineRecord(case, _passing_output(case), batch.PIPELINE_COMPLETE)

    monkeypatch.setattr(batch, "_run_one_case", escaping)
    records = asyncio.run(
        batch.run_pipeline_for_all_cases(
            [CASES_BY_ID[cid] for cid in GOLDEN_CASE_IDS], False, concurrency=6
        )
    )

    assert [record.case_id for record in records] == GOLDEN_CASE_IDS
    assert records[0].status == batch.PIPELINE_PROVIDER_FAILURE
    assert all(record.complete for record in records[1:])


# ── 6. identity isolation, through the real product pipeline ──


def _identity_probe(monkeypatch, case_ids, *, concurrency):
    """Drive the REAL `run_case_real` concurrently with a stubbed provider.

    Every phase call records the telemetry identity bound at the moment of the
    call together with the prompt it was given. The prompt carries the case's
    own brief, so the identity's claim can be checked against the case whose
    work is actually being done — which is the only way to detect a leak.
    """

    import llm_client
    import orchestrator
    from provider_telemetry.identity import current_identity

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    seen: list[tuple[str, str, str]] = []

    async def stub(phase, system, prompt, **kwargs):
        identity = current_identity()
        seen.append((identity.external_project_id, identity.run_id, prompt))
        # Guarantees a yield inside every case, so the cases genuinely interleave.
        await asyncio.sleep(0)
        return llm_client.LLMResponse(ok=False, text="", error_type="api_error", error="stub")

    monkeypatch.setattr(orchestrator, "call_llm", stub)
    records = asyncio.run(
        batch.run_pipeline_for_all_cases(
            [CASES_BY_ID[cid] for cid in case_ids], False, concurrency=concurrency
        )
    )
    return records, seen


def test_concurrent_cases_never_borrow_each_others_identity(monkeypatch):
    case_ids = GOLDEN_CASE_IDS[:4]
    briefs = {cid: CASES_BY_ID[cid]["brief"] for cid in case_ids}
    assert len(set(briefs.values())) == len(case_ids)  # the tie must be unique

    records, seen = _identity_probe(monkeypatch, case_ids, concurrency=4)

    assert seen  # the stub, not a provider, answered every call
    for external_id, run_id, prompt in seen:
        # Which case's work is this, really?
        working_on = [cid for cid in case_ids if briefs[cid] in prompt]
        assert len(working_on) == 1
        assert external_id == f"eval-{working_on[0]}"
        assert run_id == f"eval-{working_on[0]}"

    assert {entry[0] for entry in seen} == {f"eval-{cid}" for cid in case_ids}
    assert [record.case_id for record in records] == case_ids


def test_the_identity_probe_really_ran_the_cases_concurrently(monkeypatch):
    """Otherwise the isolation above would be proving nothing."""

    case_ids = GOLDEN_CASE_IDS[:4]
    _, concurrent = _identity_probe(monkeypatch, case_ids, concurrency=4)
    _, serial = _identity_probe(monkeypatch, case_ids, concurrency=1)

    concurrent_ids = [entry[0] for entry in concurrent]
    serial_ids = [entry[0] for entry in serial]

    # Serial: every case's calls in one uninterrupted block.
    assert serial_ids == sorted(serial_ids, key=lambda cid: case_ids.index(cid[len("eval-"):]))
    # Concurrent: genuinely interleaved, so not that.
    assert concurrent_ids != serial_ids
    assert len(set(concurrent_ids[:2])) == 2


# ── 7. shared process state cannot manufacture quality evidence ──
#
# `llm_client._breaker` and `llm_client._semantic_cache` are process-global and
# are deliberately not redesigned in this wave. What must be shown is narrower
# and sufficient: no consequence of sharing them across concurrent cases can put
# a case into the *measured quality* column that did not earn its way there.


def test_a_breaker_blocked_case_is_never_measured_quality(monkeypatch):
    """`policy_blocked` is the shape the breaker produces downstream."""

    case = CASES_BY_ID[GOLDEN_CASE_IDS[0]]
    blocked = _failed_phase_output(
        case, {"audit": "policy_blocked"}, completed=("classify", "hypotheses", "gauntlet")
    )
    record = batch.pipeline_record_for_output(case, blocked)

    assert record.status == batch.PIPELINE_HARNESS_FAILURE
    assert record.status != batch.PIPELINE_PROVIDER_FAILURE
    assert record.complete is False

    observation = _observe(records=[record, *_complete_records(GOLDEN_CASE_IDS[1:])])

    assert observation.valid_observation is False
    assert _case_entry(observation, case["id"])["quality_measured"] is False
    assert observation.result_class not in (
        release_gates.RESULT_PASS,
        release_gates.RESULT_QUALITY_FAILURE,
    )


def test_concurrent_dead_cases_leave_the_survivors_truthful(monkeypatch):
    """The real product pipeline, several cases at once, one dead provider.

    This is the shape the shared breaker actually produces: the first phases
    fail on the provider, the per-case budget gate then blocks the rest. Root
    cause is what gets reported, and nothing here is quality.
    """

    case_ids = GOLDEN_CASE_IDS[:4]
    records, seen = _identity_probe(monkeypatch, case_ids, concurrency=4)

    assert seen
    assert [record.case_id for record in records] == case_ids
    for record in records:
        assert record.complete is False
        assert record.status in (batch.PIPELINE_PROVIDER_FAILURE, batch.PIPELINE_HARNESS_FAILURE)

    observation = _observe(
        records=records,
        judges=_judges(case_ids, score=100),
        expected_case_ids=case_ids,
        golden_case_ids=GOLDEN_CASE_IDS,
    )

    assert observation.valid_observation is False
    assert observation.quality_measured is False
    assert observation.passed == 0
    assert observation.result_class not in (
        release_gates.RESULT_PASS,
        release_gates.RESULT_QUALITY_FAILURE,
    )
    assert all(entry["quality_measured"] is False for entry in observation.cases)


# ── 8. a malformed bound fails before anything is scheduled ──


@pytest.mark.parametrize("value", [0, -1, -6, "0", "-3", "", "  ", "six", "2.5", "6.0", None, 1.5, True, [6]])
def test_an_invalid_concurrency_is_rejected(value):
    with pytest.raises(ValueError):
        batch.normalize_concurrency(value)


@pytest.mark.parametrize("value", [1, 2, 6, 12, "6", " 6 "])
def test_a_valid_concurrency_normalizes_to_an_int(value):
    assert batch.normalize_concurrency(value) == int(str(value).strip())


def test_an_invalid_concurrency_schedules_nothing(monkeypatch):
    scheduler = _Scheduler().install(monkeypatch)

    with pytest.raises(ValueError):
        asyncio.run(
            batch.run_pipeline_for_all_cases(
                [CASES_BY_ID[cid] for cid in GOLDEN_CASE_IDS], False, concurrency=0
            )
        )

    assert scheduler.started == []


@pytest.mark.parametrize("value", ["0", "-1", "abc", "2.5", ""])
def test_a_malformed_concurrency_flag_exits_without_a_provider_call(
    tmp_path, monkeypatch, value
):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)

    monkeypatch.setenv("GITHUB_SHA", SHA_A)
    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--concurrency", value]) == 2
    assert tracker.total == 0
    assert not (tmp_path / "summary_batch.json").exists()


@pytest.mark.parametrize("value", ["0", "-30", "thirty", "1.5"])
def test_a_malformed_batch_wait_flag_exits_without_a_provider_call(
    tmp_path, monkeypatch, value
):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)

    monkeypatch.setenv("GITHUB_SHA", SHA_A)
    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--batch-wait-minutes", value]) == 2
    assert tracker.total == 0


def test_the_cli_passes_its_bounds_through_to_the_seams(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    _run_cli(monkeypatch, ["--report", str(tmp_path), "--concurrency", "3", "--batch-wait-minutes", "7"])

    assert tracker.concurrency == 3
    assert tracker.max_wait == 7 * 60


def test_the_cli_defaults_are_the_documented_ones(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    _run_cli(monkeypatch, ["--report", str(tmp_path)])

    assert tracker.concurrency == batch.DEFAULT_CASE_CONCURRENCY
    assert tracker.max_wait == batch.DEFAULT_BATCH_WAIT_SECONDS


# ── 9. mock mode and odd universes are unaffected by scheduling ──


def test_mock_mode_runs_under_concurrency_and_is_still_never_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", SHA_A)
    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--mock", "--concurrency", "6"])
    summary = _summary_on_disk(tmp_path)

    assert exit_code == 0
    assert summary["mode"] == batch.EXECUTION_MODE_MOCK
    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["ok"] is False
    assert [entry["case_id"] for entry in summary["cases"]] == GOLDEN_CASE_IDS


def test_an_empty_case_universe_schedules_nothing_and_stays_invalid(monkeypatch):
    scheduler = _Scheduler().install(monkeypatch)

    records = asyncio.run(batch.run_pipeline_for_all_cases([], False, concurrency=6))

    assert records == []
    assert scheduler.started == []

    observation = _observe(records=[], judges={}, expected_case_ids=[], golden_case_ids=[])
    assert observation.valid_observation is False
    assert batch.VALIDITY_EXPECTED_UNIVERSE_EMPTY in _codes(observation)


def test_a_subset_universe_is_scheduled_but_still_structurally_invalid(monkeypatch):
    subset = GOLDEN_CASE_IDS[:3]
    records, scheduler = _schedule(monkeypatch, subset, concurrency=6)

    assert [record.case_id for record in records] == subset
    assert scheduler.peak_in_flight == len(subset)

    observation = _observe(
        records=records, judges=_judges(subset), expected_case_ids=subset
    )
    assert observation.valid_observation is False
    assert batch.VALIDITY_INCOMPLETE_CASE_SELECTION in _codes(observation)
    assert observation.result_class != release_gates.RESULT_PASS


# ═══════════════ the batch wait budget ═══════════════


class _FakeClock:
    """A monotonic clock that only ever advances when a fake sleep is awaited."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def install(self, monkeypatch) -> "_FakeClock":
        async def fake_sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self.now += seconds

        monkeypatch.setattr(batch, "_now", lambda: self.now)
        monkeypatch.setattr(batch, "_sleep", fake_sleep)
        return self


class _FakeBatchClient:
    """The two attributes `wait_for_batch` reads off a retrieved batch."""

    def __init__(self, statuses: list[str], recorder: list) -> None:
        self._statuses = list(statuses)
        self._recorder = recorder
        self.messages = self

    @property
    def batches(self):
        return self

    async def retrieve(self, batch_id: str):
        self._recorder.append(batch_id)
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return type(
            "FakeBatch",
            (),
            {
                "id": batch_id,
                "processing_status": status,
                "request_counts": type(
                    "Counts", (),
                    {"succeeded": 0, "errored": 0, "processing": 12, "canceled": 0, "expired": 0},
                )(),
            },
        )()


def _install_fake_anthropic(monkeypatch, statuses: list[str]) -> list:
    """Stand in for the `anthropic` module `wait_for_batch` imports locally."""

    retrievals: list = []
    fake_module = type(
        "FakeAnthropic",
        (),
        {"AsyncAnthropic": staticmethod(lambda api_key=None: _FakeBatchClient(statuses, retrievals))},
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return retrievals


def _wait(monkeypatch, statuses, *, max_wait, poll_interval=30):
    clock = _FakeClock().install(monkeypatch)
    retrievals = _install_fake_anthropic(monkeypatch, statuses)
    return clock, retrievals, batch.wait_for_batch(
        FAKE_BATCH_ID, poll_interval, max_wait=max_wait
    )


# ── 10. the budget is respected, on a fake clock ──


def test_a_batch_that_never_ends_reaches_a_clean_budget_exhaustion(monkeypatch):
    clock, retrievals, coro = _wait(monkeypatch, ["in_progress"], max_wait=1800)

    with pytest.raises(batch.BatchWaitBudgetExhausted) as raised:
        asyncio.run(coro)

    assert raised.value.batch_id == FAKE_BATCH_ID
    assert raised.value.max_wait == 1800
    # Bounded by the budget, on the fake clock, with no real sleeping at all.
    assert clock.now == 1800
    assert sum(clock.slept) == 1800
    assert len(retrievals) == 1800 // 30 + 1


def test_the_budget_is_wall_clock_not_a_count_of_polls(monkeypatch):
    """A poll interval larger than the remaining budget is truncated, not
    overshot: the bound exists to fit inside a job timeout."""

    clock, _, coro = _wait(monkeypatch, ["in_progress"], max_wait=45, poll_interval=30)

    with pytest.raises(batch.BatchWaitBudgetExhausted):
        asyncio.run(coro)

    assert clock.slept == [30, 15]
    assert clock.now == 45


def test_a_batch_that_ends_before_the_budget_is_collected(monkeypatch):
    clock, retrievals, coro = _wait(
        monkeypatch, ["in_progress", "in_progress", "ended"], max_wait=1800
    )

    result = asyncio.run(coro)

    assert result.processing_status == "ended"
    assert len(retrievals) == 3
    assert clock.now == 60


def test_a_batch_that_ends_exactly_at_the_budget_boundary_is_collected(monkeypatch):
    """Two polls' worth of budget, ending on the second: still a collection."""

    clock, _, coro = _wait(monkeypatch, ["in_progress", "ended"], max_wait=30)

    assert asyncio.run(coro).processing_status == "ended"
    assert clock.now == 30


def test_a_batch_that_ends_one_tick_after_the_budget_is_not(monkeypatch):
    clock, _, coro = _wait(monkeypatch, ["in_progress", "in_progress", "ended"], max_wait=30)

    with pytest.raises(batch.BatchWaitBudgetExhausted):
        asyncio.run(coro)

    assert clock.now == 30


def test_an_already_ended_batch_is_collected_even_on_a_tiny_budget(monkeypatch):
    """A status is always retrieved at least once."""

    clock, retrievals, coro = _wait(monkeypatch, ["ended"], max_wait=1)

    assert asyncio.run(coro).processing_status == "ended"
    assert retrievals == [FAKE_BATCH_ID]
    assert clock.slept == []


def test_budget_exhaustion_is_not_a_timeout_error(monkeypatch):
    """`classify_exception` reads `TimeoutError` as provider transport, and this
    condition is the opposite of a provider fact."""

    assert not issubclass(batch.BatchWaitBudgetExhausted, TimeoutError)
    assert not issubclass(batch.BatchWaitBudgetExhausted, OSError)

    status, _ = batch.classify_exception(batch.BatchWaitBudgetExhausted(FAKE_BATCH_ID, 1800))
    assert status == batch.PIPELINE_HARNESS_FAILURE


# ── 11. the default budget fits inside the real workflow's job timeout ──


def _nightly_job_timeout_minutes() -> int:
    """Read the real workflow. Parsed, never modified."""

    import yaml

    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "evals-nightly-batch.yml"
    )
    parsed = yaml.safe_load(workflow_path.read_text())
    return int(parsed["jobs"]["nightly-batch"]["timeout-minutes"])


def test_the_default_wait_budget_is_strictly_inside_the_job_timeout():
    timeout = _nightly_job_timeout_minutes()

    assert timeout == 90  # asserted, not assumed; this wave does not move it
    assert batch.DEFAULT_BATCH_WAIT_MINUTES < timeout
    # And with room to spare: setup plus a bounded-concurrency pipeline pass has
    # to fit alongside it, and the run still has to write its artifact.
    assert batch.DEFAULT_BATCH_WAIT_MINUTES <= timeout // 2
    assert batch.DEFAULT_BATCH_WAIT_SECONDS == batch.DEFAULT_BATCH_WAIT_MINUTES * 60


def test_the_old_unreachable_ceiling_is_gone():
    """86400s inside a 90-minute job could only ever end as a SIGKILL."""

    import inspect

    default = inspect.signature(batch.wait_for_batch).parameters["max_wait"].default

    assert default == batch.DEFAULT_BATCH_WAIT_SECONDS
    assert default < _nightly_job_timeout_minutes() * 60


# ── 12. what an exhausted budget leaves behind ──


def _exhausted_run(tmp_path, monkeypatch, sha: str = SHA_A) -> tuple[dict, int, _ProviderCalls]:
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker, wait_budget_exhausted=True)
    monkeypatch.setenv("GITHUB_SHA", sha)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path)])
    return _summary_on_disk(tmp_path), exit_code, tracker


def test_wait_budget_exhaustion_still_writes_a_summary(tmp_path, monkeypatch):
    summary, exit_code, tracker = _exhausted_run(tmp_path, monkeypatch)

    assert (tmp_path / "summary_batch.json").exists()
    assert exit_code == 1
    # Submitted once, waited once, and deliberately never collected.
    assert (tracker.submits, tracker.waits, tracker.collects) == (1, 1, 0)


def test_the_exhausted_summary_names_the_batch_it_gave_up_on(tmp_path, monkeypatch):
    summary, _, _ = _exhausted_run(tmp_path, monkeypatch)

    assert summary["batch_id"] == FAKE_BATCH_ID


def test_an_exhausted_wait_is_not_a_measured_quality_verdict(tmp_path, monkeypatch):
    summary, _, _ = _exhausted_run(tmp_path, monkeypatch)

    assert summary["valid_observation"] is False
    assert summary["quality_measured"] is False
    assert summary["ok"] is False
    assert summary["result_class"] != release_gates.RESULT_PASS
    assert summary["result_class"] != release_gates.RESULT_QUALITY_FAILURE
    assert summary["result_class"] == release_gates.RESULT_INFRASTRUCTURE_FAILURE


def test_an_exhausted_wait_is_not_blamed_on_the_provider(tmp_path, monkeypatch):
    """The provider accepted the batch and may still be working on it."""

    summary, _, _ = _exhausted_run(tmp_path, monkeypatch)

    assert summary["result_class"] != release_gates.RESULT_PROVIDER_UNAVAILABLE
    codes = {entry["code"] for entry in summary["validity_errors"]}
    assert batch.VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE not in codes
    assert batch.VALIDITY_PIPELINE_PROVIDER_FAILURE not in codes


def test_the_exhaustion_diagnostic_is_a_fixed_closed_token(tmp_path, monkeypatch):
    summary, _, _ = _exhausted_run(tmp_path, monkeypatch)

    errors = [
        entry
        for entry in summary["validity_errors"]
        if entry["code"] == batch.VALIDITY_BATCH_WAIT_BUDGET_EXHAUSTED
    ]

    assert len(errors) == 1
    assert errors[0]["detail"] == f"batch_collect:{batch.WAIT_BUDGET_EXHAUSTED_DETAIL}"
    assert errors[0]["case_ids"] == GOLDEN_CASE_IDS
    # In the module's own closed vocabulary, and classified.
    assert batch.VALIDITY_BATCH_WAIT_BUDGET_EXHAUSTED in batch.VALIDITY_CODES
    assert batch.VALIDITY_BATCH_WAIT_BUDGET_EXHAUSTED in batch.ATTRIBUTION_AND_HARNESS_CODES


def test_no_exception_or_provider_prose_reaches_the_exhausted_artifact(tmp_path, monkeypatch):
    summary, _, _ = _exhausted_run(tmp_path, monkeypatch)

    serialized = json.dumps(summary)
    for marker in ("BatchWaitBudgetExhausted", "Traceback", "elapsed before", "sk-ant", "api_key"):
        assert marker not in serialized
    for entry in summary["validity_errors"]:
        assert entry["code"] in batch.VALIDITY_CODES
        assert " " not in entry["detail"]


def test_a_hostile_batch_id_cannot_smuggle_prose_into_the_diagnostic(tmp_path, monkeypatch):
    """The diagnostic is built from fixed tokens, not from anything observed."""

    hostile = 'msgbatch_"><script>  key=sk-ant-LEAKED'

    async def fake_pipeline(cases, mock, concurrency=batch.DEFAULT_CASE_CONCURRENCY):
        return _complete_records([case["id"] for case in cases])

    async def fake_submit(requests):
        return hostile

    async def fake_wait(batch_id, poll_interval=30, max_wait=batch.DEFAULT_BATCH_WAIT_SECONDS):
        raise batch.BatchWaitBudgetExhausted(batch_id, max_wait)

    monkeypatch.setattr(batch, "run_pipeline_for_all_cases", fake_pipeline)
    monkeypatch.setattr(batch, "submit_batch", fake_submit)
    monkeypatch.setattr(batch, "wait_for_batch", fake_wait)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    assert _run_cli(monkeypatch, ["--report", str(tmp_path)]) == 1
    summary = _summary_on_disk(tmp_path)

    for entry in summary["validity_errors"]:
        assert "sk-ant" not in entry["detail"]
        assert "script" not in entry["detail"]
    # `batch_id` is its own field and is the operator's resume handle; the
    # *diagnostics* never quote it.
    assert summary["batch_id"] == hostile


def test_the_submitted_inputs_survive_an_exhausted_wait(tmp_path, monkeypatch):
    _exhausted_run(tmp_path, monkeypatch)

    cached = json.loads(_cache_path(tmp_path).read_text())

    assert cached["source_sha"] == SHA_A
    assert [entry["case"]["id"] for entry in cached["cases"]] == GOLDEN_CASE_IDS


# ── 13. and what can be recovered from it ──


def test_an_exhausted_wait_can_be_resumed_at_the_same_commit(tmp_path, monkeypatch):
    """The whole point of stopping the wait cleanly: the batch is still there."""

    _exhausted_run(tmp_path, monkeypatch)

    resume_tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, resume_tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert exit_code == 0
    assert summary["valid_observation"] is True
    assert summary["quality_measured"] is True
    assert summary["ok"] is True
    assert summary["result_class"] == release_gates.RESULT_PASS
    assert summary["source_sha"] == SHA_A
    assert summary["validity_errors"] == []
    # No second batch was ever created.
    assert resume_tracker.submits == 0
    assert (resume_tracker.waits, resume_tracker.collects) == (1, 1)


def test_resuming_an_exhausted_wait_at_another_commit_still_fails_closed(tmp_path, monkeypatch):
    _exhausted_run(tmp_path, monkeypatch, sha=SHA_A)

    resume_tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, resume_tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_B)

    exit_code = _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID])
    summary = _summary_on_disk(tmp_path)

    assert exit_code == 1
    assert summary["valid_observation"] is False
    assert summary["result_class"] != release_gates.RESULT_PASS
    assert summary["result_class"] != release_gates.RESULT_QUALITY_FAILURE
    assert batch.VALIDITY_SOURCE_SHA_MISMATCH in {
        entry["code"] for entry in summary["validity_errors"]
    }
    # Failed closed *before* touching the provider.
    assert resume_tracker.total == 0


def test_an_exhausted_wait_never_resubmits_by_itself(tmp_path, monkeypatch):
    """Budget exhaustion is a recoverable observation, not authorization to buy
    a second paid batch."""

    _, _, tracker = _exhausted_run(tmp_path, monkeypatch)

    assert tracker.submits == 1
    caches = sorted(path.name for path in tmp_path.glob("batch_inputs_*.json"))
    assert caches == [f"batch_inputs_{FAKE_BATCH_ID}.json"]


def test_a_resume_whose_budget_also_expires_is_typed_the_same_way(tmp_path, monkeypatch):
    _exhausted_run(tmp_path, monkeypatch)

    resume_tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, resume_tracker, wait_budget_exhausted=True)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--resume", FAKE_BATCH_ID]) == 1
    summary = _summary_on_disk(tmp_path)

    errors = [
        entry
        for entry in summary["validity_errors"]
        if entry["code"] == batch.VALIDITY_BATCH_WAIT_BUDGET_EXHAUSTED
    ]
    assert len(errors) == 1
    assert errors[0]["detail"] == f"batch_resume:{batch.WAIT_BUDGET_EXHAUSTED_DETAIL}"
    assert summary["valid_observation"] is False
    assert summary["batch_id"] == FAKE_BATCH_ID
    assert resume_tracker.collects == 0


# ── 14. --submit-only is untouched by any of this ──


def test_submit_only_is_still_exactly_one_submit_and_nothing_else(tmp_path, monkeypatch):
    tracker = _ProviderCalls()
    _install_fake_batch_api(monkeypatch, tracker)
    monkeypatch.setenv("GITHUB_SHA", SHA_A)

    assert _run_cli(monkeypatch, ["--report", str(tmp_path), "--submit-only"]) == 0

    assert (tracker.submits, tracker.waits, tracker.collects) == (1, 0, 0)
    assert _cache_path(tmp_path).exists()
    assert not (tmp_path / "summary_batch.json").exists()
