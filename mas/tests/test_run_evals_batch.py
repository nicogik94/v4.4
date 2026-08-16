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

    @property
    def total(self) -> int:
        return self.submits + self.waits + self.collects


def _install_fake_batch_api(monkeypatch, tracker: _ProviderCalls, *, judges=None):
    """Replace the three provider-touching coroutines with counters.

    Nothing here opens a socket or reads a credential; the batch API is never
    reached, so these tests can assert on the resume contract without spend.
    """

    async def fake_pipeline(cases, mock):
        return _complete_records([case["id"] for case in cases])

    async def fake_submit(requests):
        tracker.submits += 1
        return FAKE_BATCH_ID

    async def fake_wait(batch_id, poll_interval=30, max_wait=86400):
        tracker.waits += 1
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
