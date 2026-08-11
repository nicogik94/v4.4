"""Eval failure provenance: the phase ledger, aggregation and judge record.

The V7 live observation scored 4/12 and could not be read: the artifact carried
no evidence about whether a case failed because the analysis was poor or because
the model returned nothing to analyse. These tests pin the ledger that makes the
difference visible — and, just as hard, pin that it changes nothing about the
release verdict beside it.

Everything here is deterministic and offline. Provider records are built by hand
in the shape the runtime hands to a sink, so no provider, no network and no
database is involved.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evals.run_evals as eval_runner  # noqa: E402
from evals import provenance  # noqa: E402
from evals.run_evals import (  # noqa: E402
    JUDGE_INPUT_MAX_CHARS,
    JUDGE_MODEL,
    JUDGE_SYSTEM_PROMPT,
    PASS_THRESHOLD,
    REAL_CASE_PHASES,
    CaseResult,
    aggregate_exit_code,
    aggregate_summaries,
    load_cases,
    pass_fail,
    shard_cases,
    summarize_results,
    write_summary,
)


# ─────────────────────────── record fakes ───────────────────────────
#
# Shaped exactly like the records the runtime hands to a telemetry sink. Built
# by hand so this suite needs no runtime, no provider and no database.


def _identity(phase):
    return SimpleNamespace(phase=phase, external_project_id="eval-G01")


def _call(call_id, phase):
    return SimpleNamespace(
        call_id=call_id,
        identity=_identity(phase),
        requested_provider="openai",
        requested_model="gpt-5",
        candidate_count=3,
    )


def _invocation(invocation_id, call_id, phase, *, candidate=1, retry=1):
    return SimpleNamespace(
        invocation_id=invocation_id,
        call_id=call_id,
        identity=_identity(phase),
        invocation_kind="provider_call",
        provider="openai",
        requested_model="gpt-5",
        candidate_ordinal=candidate,
        retry_ordinal=retry,
        attempt_ordinal=candidate,
        fallback_candidate=candidate > 1,
        request_config_fingerprint="a" * 64,
        routing_decision_fingerprint="b" * 64,
    )


class _Value:
    def __init__(self, status, stored=None, detail=""):
        self.status = status
        self.stored = stored
        self.detail = detail


def _observation(*, model="gpt-5-2026", stop="stop", inp=100, out=50):
    return SimpleNamespace(
        effective_model=_Value("valid", model),
        stop_reason=_Value("valid", stop),
        input_tokens=_Value("valid", inp),
        output_tokens=_Value("valid", out),
        cache_read_tokens=_Value("absent"),
    )


def _event(subject_id, kind, *, ordinal=1, terminal=False, observation=None, category=""):
    return SimpleNamespace(
        subject_kind="sdk_invocation",
        subject_id=subject_id,
        event_kind=kind,
        event_ordinal=ordinal,
        is_terminal=terminal,
        error_category=category,
        failure_class="",
        observation=observation if observation is not None else _observation(),
    )


def _shape(invocation_id, *, content="nonempty", length=42, refusal="absent", reasoning=None):
    payload = {
        "invocation_id": invocation_id,
        "call_id": "call",
        "provider": "openai",
        "requested_model": "gpt-5",
        "content_status": {"status": content, "value": None, "detail": ""},
        "visible_content_length": {
            "status": "valid" if length is not None else "missing",
            "value": length,
            "detail": "",
        },
        "refusal_status": {"status": refusal, "value": None, "detail": ""},
        "reasoning_tokens": {
            "status": "valid" if reasoning is not None else "missing",
            "value": reasoning,
            "detail": "" if reasoning is not None else "details_missing",
        },
    }
    return payload


def _recorder(case_id="G01"):
    return provenance.EvalProvenanceRecorder(case_id=case_id)


def _feed(recorder, call_id, invocation_id, phase, *, kind="completed", observation=None,
          shape=None, category=""):
    asyncio.run(recorder.append_start("call", _call(call_id, phase)))
    asyncio.run(recorder.append_start("invocation", _invocation(invocation_id, call_id, phase)))
    asyncio.run(
        recorder.append_event(
            _event(invocation_id, "observation", ordinal=1, observation=observation)
        )
    )
    asyncio.run(
        recorder.append_event(
            _event(
                invocation_id,
                kind,
                ordinal=2,
                terminal=True,
                observation=SimpleNamespace(
                    effective_model=_Value("absent"),
                    stop_reason=_Value("absent"),
                    input_tokens=_Value("absent"),
                    output_tokens=_Value("absent"),
                    cache_read_tokens=_Value("absent"),
                ),
                category=category,
            )
        )
    )
    if shape is not None:
        recorder.record_response_shape(shape)


def _output(statuses, *, failures=None, eval_errors=None):
    return {
        "phase_status": dict(statuses),
        "phase_failure_details": {
            phase: {"phase": phase, "category": category, "message": "diagnostic text"}
            for phase, category in (failures or {}).items()
        },
        "ingestion_metadata": {"eval_errors": list(eval_errors or [])},
    }


_ALL_PENDING = {phase: "pending" for phase in REAL_CASE_PHASES}


def _statuses(**overrides):
    statuses = dict(_ALL_PENDING)
    statuses.update(overrides)
    return statuses


def _phase(ledger, name):
    return next(record for record in ledger if record["phase"] == name)


# ─────────────────────────── the phase ledger ───────────────────────────


class PhaseLedgerTests(unittest.TestCase):
    """Regressions 29-37."""

    def test_a_clean_first_pass_phase_is_completed_with_one_logical_call(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "classify", shape=_shape("i1", reasoning=0))
        ledger = provenance.phase_ledger(
            output=_output(_statuses(classify="completed")),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )
        record = _phase(ledger, "classify")

        self.assertEqual(record["phase_final_status"], provenance.PHASE_COMPLETED)
        self.assertTrue(record["phase_started"])
        self.assertEqual(record["logical_call_count"], 1)
        self.assertFalse(record["structured_repair_attempted"])
        self.assertEqual(record["first_parse_result"], provenance.PARSE_PARSED)
        self.assertEqual(
            record["structured_repair_result"], provenance.OUTCOME_NOT_ATTEMPTED
        )
        self.assertEqual(record["structural_failure_kind"], "none")
        self.assertFalse(record["continued_after_structural_failure"])

    def test_first_empty_then_successful_repair_is_recorded_as_a_repair(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "gauntlet", shape=_shape("i1", content="empty", length=0))
        _feed(recorder, "c2", "i2", "gauntlet", shape=_shape("i2", content="nonempty", length=900))
        ledger = provenance.phase_ledger(
            output=_output(_statuses(gauntlet="completed")),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )
        record = _phase(ledger, "gauntlet")

        self.assertEqual(record["logical_call_count"], 2)
        self.assertTrue(record["structured_repair_attempted"])
        self.assertEqual(record["first_parse_result"], provenance.PARSE_FAILED)
        self.assertEqual(record["structured_repair_result"], provenance.OUTCOME_SUCCEEDED)
        self.assertEqual(record["phase_final_status"], provenance.PHASE_COMPLETED)
        self.assertEqual(record["first_response_status"]["status"], "empty")
        self.assertEqual(record["first_visible_content_length"]["value"], 0)
        self.assertEqual(record["empty_visible_output_count"], 1)

    def test_two_empty_responses_end_in_a_terminal_structural_failure(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "audit", shape=_shape("i1", content="empty", length=0))
        _feed(recorder, "c2", "i2", "audit", shape=_shape("i2", content="empty", length=0))
        ledger = provenance.phase_ledger(
            output=_output(
                _statuses(audit="failed"), failures={"audit": "json_parse"}
            ),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )
        record = _phase(ledger, "audit")

        self.assertEqual(record["phase_final_status"], provenance.PHASE_STRUCTURAL_FAILURE)
        self.assertEqual(record["structural_failure_kind"], "json_parse")
        self.assertEqual(record["structured_repair_result"], provenance.OUTCOME_FAILED)
        self.assertEqual(record["empty_visible_output_count"], 2)

    def test_a_nonempty_response_that_fails_to_parse_is_still_a_parse_failure(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "strategy", shape=_shape("i1", content="nonempty", length=4000))
        _feed(recorder, "c2", "i2", "strategy", shape=_shape("i2", content="nonempty", length=3800))
        ledger = provenance.phase_ledger(
            output=_output(
                _statuses(strategy="failed"), failures={"strategy": "json_shape"}
            ),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )
        record = _phase(ledger, "strategy")

        self.assertEqual(record["first_parse_result"], provenance.PARSE_FAILED)
        self.assertEqual(record["structural_failure_kind"], "json_shape")
        self.assertEqual(record["empty_visible_output_count"], 0)

    def test_a_length_stop_is_counted_and_recovery_is_not_claimed_either_way(self):
        recorder = _recorder()
        _feed(
            recorder,
            "c1",
            "i1",
            "strategy",
            observation=_observation(stop="length", out=8000),
            shape=_shape("i1", content="empty", length=0, reasoning=4000),
        )
        ledger = provenance.phase_ledger(
            output=_output(_statuses(strategy="completed")),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )
        record = _phase(ledger, "strategy")

        self.assertEqual(record["first_stop_reason"]["value"], "length")
        self.assertEqual(record["length_stop_count"], 1)
        # The orchestrator's deterministic Strategy repair leaves no signal
        # outside a certified file. It is reported as unobserved, never guessed.
        self.assertEqual(record["strategy_recovery_attempted"]["status"], "unknown")
        self.assertEqual(
            record["strategy_recovery_attempted"]["detail"],
            provenance.UNOBSERVABLE_IN_ORCHESTRATOR,
        )
        self.assertEqual(record["strategy_recovery_result"]["status"], "unknown")

    def test_the_eval_is_recorded_as_continuing_after_a_terminal_failure(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "classify")
        _feed(recorder, "c2", "i2", "hypotheses")
        _feed(recorder, "c3", "i3", "gauntlet")
        ledger = provenance.phase_ledger(
            output=_output(
                _statuses(classify="completed", hypotheses="failed", gauntlet="completed"),
                failures={"hypotheses": "json_parse"},
            ),
            phases=REAL_CASE_PHASES,
            recorder=recorder,
        )

        self.assertTrue(_phase(ledger, "hypotheses")["continued_after_structural_failure"])
        self.assertFalse(_phase(ledger, "classify")["continued_after_structural_failure"])
        self.assertTrue(_phase(ledger, "gauntlet")["phase_started"])

    def test_a_confused_halt_is_an_expected_halt_and_not_a_structural_failure(self):
        recorder = _recorder()
        _feed(recorder, "c1", "i1", "classify")
        output = _output(
            _statuses(classify="completed"),
            eval_errors=["workflow halted after Confused classification"],
        )
        ledger = provenance.phase_ledger(
            output=output, phases=REAL_CASE_PHASES, recorder=recorder
        )

        self.assertEqual(
            _phase(ledger, "classify")["phase_final_status"], provenance.PHASE_COMPLETED
        )
        for phase in ("hypotheses", "gauntlet", "audit", "strategy"):
            with self.subTest(phase=phase):
                record = _phase(ledger, phase)
                self.assertEqual(
                    record["phase_final_status"], provenance.PHASE_EXPECTED_HALT
                )
                self.assertFalse(record["phase_started"])
        self.assertEqual(provenance._halt_reason(output), provenance.HALT_CONFUSED)

    def test_missing_provenance_becomes_an_explicit_unknown_not_a_clean_record(self):
        ledger = provenance.phase_ledger(
            output=_output(_statuses(classify="completed")),
            phases=REAL_CASE_PHASES,
            recorder=None,
        )
        record = _phase(ledger, "classify")

        self.assertEqual(record["first_parse_result"], provenance.PARSE_UNKNOWN)
        self.assertEqual(record["structured_repair_result"], provenance.OUTCOME_UNKNOWN)
        self.assertEqual(record["first_response_status"]["status"], "unknown")
        self.assertEqual(record["first_parse_result_derivation"], "not_observed")
        self.assertEqual(record["invocation_count"], 0)

    def test_phase_order_is_the_declared_order(self):
        ledger = provenance.phase_ledger(
            output=_output(_statuses()), phases=REAL_CASE_PHASES, recorder=None
        )
        self.assertEqual(
            [record["phase"] for record in ledger], list(REAL_CASE_PHASES)
        )

    def test_an_unrecognised_failure_category_is_bounded_not_copied(self):
        ledger = provenance.phase_ledger(
            output=_output(
                _statuses(audit="failed"),
                failures={"audit": "something_a_future_build_invented"},
            ),
            phases=REAL_CASE_PHASES,
            recorder=None,
        )
        self.assertEqual(_phase(ledger, "audit")["structural_failure_kind"], "other")

    def test_the_ledger_never_carries_a_failure_diagnostic_message(self):
        ledger = provenance.phase_ledger(
            output=_output(
                _statuses(audit="failed"),
                failures={"audit": "schema_validation"},
                eval_errors=["audit: ValueError('secret-exception-text')"],
            ),
            phases=REAL_CASE_PHASES,
            recorder=None,
        )
        payload = json.dumps(ledger)
        self.assertNotIn("diagnostic text", payload)
        self.assertNotIn("secret-exception-text", payload)
        self.assertTrue(_phase(ledger, "audit")["harness_exception_recorded"])


class RecorderIsolationTests(unittest.TestCase):
    def test_the_recorder_absorbs_a_hostile_record_without_raising(self):
        recorder = _recorder()

        class Hostile:
            @property
            def invocation_id(self):
                raise RuntimeError("hostile-record-sentinel")

            @property
            def candidate_ordinal(self):
                raise RuntimeError("hostile-record-sentinel")

        asyncio.run(recorder.append_start("invocation", Hostile()))
        asyncio.run(recorder.append_event(Hostile()))
        recorder.record_response_shape(Hostile())

        self.assertIn(provenance.NOTE_RECORDER_FAULT, recorder.notes)
        self.assertEqual(recorder.invocations, [])

    def test_collection_is_bounded(self):
        recorder = _recorder()
        for index in range(provenance.MAX_INVOCATIONS + 5):
            asyncio.run(
                recorder.append_start(
                    "invocation", _invocation(f"i{index}", "c1", "classify")
                )
            )
        self.assertEqual(len(recorder.invocations), provenance.MAX_INVOCATIONS)
        self.assertIn(provenance.NOTE_INVOCATION_CAP, recorder.notes)

    def test_run_level_events_are_not_mistaken_for_attempt_evidence(self):
        recorder = _recorder()
        asyncio.run(
            recorder.append_event(
                SimpleNamespace(event_kind="reconciliation", drain_status="drained")
            )
        )
        self.assertEqual(recorder.events, {})

    def test_a_second_shape_for_one_invocation_never_replaces_the_first(self):
        recorder = _recorder()
        recorder.record_response_shape(_shape("i1", content="empty", length=0))
        recorder.record_response_shape(_shape("i1", content="nonempty", length=999))
        self.assertEqual(recorder.shapes["i1"]["content_status"]["status"], "empty")


# ─────────────────────────── aggregation ───────────────────────────


def _case_result(case_id, *, passed=True, provenance_payload=None):
    return CaseResult(
        case_id=case_id,
        passed=passed,
        domain_match=passed,
        hypothesis_count_ok=passed,
        frameworks_covered=1.0 if passed else 0.0,
        must_mention_hits=1.0 if passed else 0.0,
        data_labeling_correct=True,
        judge_overall=70 if passed else 40,
        judge_rationale="mock",
        provenance=provenance_payload or {},
    )


def _captured_provenance(case_id, *, structural_phase=None, empty=0, length_stops=0,
                         reasoning=0, continued=False, judge=None):
    phases = []
    for phase in REAL_CASE_PHASES:
        failed = phase == structural_phase
        phases.append(
            {
                "phase": phase,
                "phase_started": True,
                "phase_final_status": (
                    provenance.PHASE_STRUCTURAL_FAILURE if failed else provenance.PHASE_COMPLETED
                ),
                "structural_failure_kind": "json_parse" if failed else "none",
                "continued_after_structural_failure": failed and continued,
            }
        )
    return {
        "schema_version": provenance.SCHEMA_VERSION,
        "captured": True,
        "capture_mode": provenance.CAPTURE_MODE_TELEMETRY,
        "case_id": case_id,
        "halt_reason": "",
        "phases": phases,
        "invocations": [
            {
                "content_status": {"status": "empty" if empty else "nonempty"},
                "refusal_status": {"status": "absent"},
                "stop_reason": {"status": "valid", "value": "length" if length_stops else "stop"},
                "reasoning_tokens": {"status": "valid" if reasoning else "missing"},
            }
        ],
        "judge": judge or {},
        "notes": [],
        "counters": {
            "phase_count": len(phases),
            "invocation_count": 1,
            "structural_failure_phases": [structural_phase] if structural_phase else [],
            "empty_visible_output_event_count": empty,
            "explicit_length_stop_event_count": length_stops,
            "reasoning_token_evidence_available_count": reasoning,
            "continued_after_structural_failure": bool(structural_phase and continued),
        },
    }


def _write_shards(tmp_path, results_by_id, shard_count=4):
    cases = load_cases()
    dirs = []
    for index in range(shard_count):
        shard_dir = tmp_path / f"eval-shard-{index}"
        selected = shard_cases(cases, index, shard_count)
        write_summary(
            shard_dir,
            summarize_results(
                [results_by_id[case["id"]] for case in selected],
                threshold=PASS_THRESHOLD,
                mode="real",
                case_ids=[case["id"] for case in selected],
                shard_index=index,
                shard_count=shard_count,
            ),
        )
        dirs.append(str(shard_dir))
    return dirs


class AggregateProvenanceTests(unittest.TestCase):
    """Regressions 38-46."""

    def test_a_historical_summary_without_provenance_still_aggregates(self):
        cases = load_cases()
        results_by_id = {case["id"]: _case_result(case["id"]) for case in cases}
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            dirs = _write_shards(tmp_path, results_by_id)
            # Strip the provenance key entirely: this is what a report written
            # before this wave looks like on disk.
            for shard_dir in dirs:
                path = Path(shard_dir) / "summary.json"
                data = json.loads(path.read_text())
                data.pop("eval_provenance", None)
                for case in data["cases"]:
                    case.pop("provenance", None)
                path.write_text(json.dumps(data))

            aggregate = aggregate_summaries(dirs, threshold=PASS_THRESHOLD)

        self.assertEqual(aggregate["total"], len(cases))
        self.assertEqual(aggregate["aggregation_errors"], [])
        self.assertTrue(aggregate["ok"])
        self.assertEqual(aggregate["eval_provenance"]["cases_with_provenance"], 0)
        self.assertEqual(
            aggregate["eval_provenance"]["structural_failure_case_ids"], []
        )

    def test_a_summary_with_provenance_aggregates_its_counters(self):
        cases = load_cases()
        failing = cases[0]["id"]
        results_by_id = {
            case["id"]: _case_result(
                case["id"],
                provenance_payload=_captured_provenance(case["id"], reasoning=1),
            )
            for case in cases
        }
        results_by_id[failing] = _case_result(
            failing,
            passed=False,
            provenance_payload=_captured_provenance(
                failing,
                structural_phase="hypotheses",
                empty=1,
                length_stops=1,
                reasoning=1,
                continued=True,
            ),
        )
        with tempfile.TemporaryDirectory() as raw:
            aggregate = aggregate_summaries(
                _write_shards(Path(raw), results_by_id), threshold=PASS_THRESHOLD
            )

        block = aggregate["eval_provenance"]
        self.assertEqual(block["cases_with_provenance"], len(cases))
        self.assertEqual(block["cases_with_structural_failure"], 1)
        self.assertEqual(block["structural_failure_case_ids"], [failing])
        self.assertEqual(block["structural_failure_phases"], {"hypotheses": 1})
        self.assertEqual(block["empty_visible_output_event_count"], 1)
        self.assertEqual(block["explicit_length_stop_event_count"], 1)
        self.assertEqual(block["reasoning_token_evidence_available_count"], len(cases))
        self.assertEqual(block["cases_continued_after_structural_failure"], [failing])
        self.assertEqual(block["content_status_counts"]["empty"], 1)
        self.assertEqual(block["refusal_status_counts"]["absent"], len(cases))
        self.assertEqual(
            block["failure_provenance_counts"][provenance.FAILURE_STRUCTURAL], 1
        )
        self.assertTrue(block["informational_only"])

    def test_provenance_never_changes_pass_fail_pass_rate_threshold_or_ok(self):
        cases = load_cases()
        bare = [
            _case_result(case["id"], passed=(index % 3 != 0))
            for index, case in enumerate(cases)
        ]
        annotated = [
            _case_result(
                case["id"],
                passed=(index % 3 != 0),
                provenance_payload=_captured_provenance(
                    case["id"], structural_phase="strategy", empty=3, continued=True
                ),
            )
            for index, case in enumerate(cases)
        ]

        for before, after in zip(bare, annotated):
            with self.subTest(case_id=before.case_id):
                self.assertEqual(pass_fail(before), pass_fail(after))

        without = summarize_results(
            bare, threshold=PASS_THRESHOLD, mode="real", case_ids=[c["id"] for c in cases]
        )
        with_provenance = summarize_results(
            annotated,
            threshold=PASS_THRESHOLD,
            mode="real",
            case_ids=[c["id"] for c in cases],
        )

        for key in (
            "passed",
            "total",
            "pass_rate",
            "threshold",
            "ok",
            "aggregate_failure_kind",
            "quality_ok",
            "quality_failure_count",
            "quality_failure_case_ids",
            "provider_failure_count",
            "provider_unavailable",
        ):
            with self.subTest(key=key):
                self.assertEqual(without[key], with_provenance[key])
        self.assertEqual(aggregate_exit_code(without), aggregate_exit_code(with_provenance))

    def test_no_corrected_or_counterfactual_pass_rate_is_ever_produced(self):
        cases = load_cases()
        results = [
            _case_result(
                case["id"],
                passed=False,
                provenance_payload=_captured_provenance(
                    case["id"], structural_phase="gauntlet", empty=2, continued=True
                ),
            )
            for case in cases
        ]
        summary = summarize_results(
            results, threshold=PASS_THRESHOLD, mode="real", case_ids=[c["id"] for c in cases]
        )
        text = json.dumps(summary)

        self.assertEqual(summary["pass_rate"], 0.0)
        self.assertFalse(summary["ok"])
        for forbidden in (
            "corrected_pass_rate",
            "adjusted_pass_rate",
            "counterfactual_pass_rate",
            "would_have_passed",
            "structural_pass_rate",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_duplicate_and_missing_cases_still_fail_closed_with_provenance(self):
        cases = load_cases()
        results_by_id = {
            case["id"]: _case_result(
                case["id"], provenance_payload=_captured_provenance(case["id"])
            )
            for case in cases
        }
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            dirs = _write_shards(tmp_path, results_by_id)

            duplicated = json.loads((Path(dirs[0]) / "summary.json").read_text())
            duplicate_dir = tmp_path / "duplicate"
            duplicate_dir.mkdir()
            (duplicate_dir / "summary.json").write_text(json.dumps(duplicated))
            with_duplicate = aggregate_summaries(
                dirs + [str(duplicate_dir)], threshold=PASS_THRESHOLD
            )

            missing = aggregate_summaries(dirs[:-1], threshold=PASS_THRESHOLD)

        self.assertFalse(with_duplicate["ok"])
        self.assertTrue(
            any("duplicate" in error for error in with_duplicate["aggregation_errors"])
        )
        self.assertEqual(aggregate_exit_code(with_duplicate), 1)

        self.assertFalse(missing["ok"])
        self.assertTrue(any("missing" in error for error in missing["aggregation_errors"]))
        self.assertEqual(
            missing["eval_provenance"]["failure_provenance_counts"][
                provenance.FAILURE_AGGREGATION
            ],
            len(missing["aggregation_errors"]),
        )

    def test_a_corrupt_shard_artifact_cannot_make_the_aggregate_unbounded(self):
        # Aggregation reads shard reports that arrived as downloaded artifacts.
        # A corrupt one may make a counter wrong; it must not be able to decide
        # the aggregate's key set or the length of anything in it.
        payload = _captured_provenance("G01")
        payload["counters"]["structural_failure_phases"] = ["p" * 5000]
        payload["invocations"] = [
            {
                "content_status": {"status": "x" * 5000},
                "refusal_status": {"status": "not-a-declared-status"},
                "stop_reason": {"status": "valid", "value": "stop"},
                "reasoning_tokens": {"status": "missing"},
            }
        ]
        case = _case_result("C" * 5000, passed=False, provenance_payload=payload)

        block = provenance.aggregate_provenance([case])

        self.assertTrue(all(len(key) <= 64 for key in block["structural_failure_phases"]))
        self.assertTrue(all(len(cid) <= 64 for cid in block["structural_failure_case_ids"]))
        self.assertEqual(
            set(block["content_status_counts"]) - set(provenance.VALUE_STATUSES), set()
        )
        self.assertEqual(block["refusal_status_counts"], {provenance.STATUS_UNKNOWN: 1})

    def test_a_failed_case_with_no_provenance_is_unknown_not_analytical(self):
        attribution = provenance.failure_provenance(
            _case_result("G01", passed=False)
        )
        self.assertEqual(attribution["primary"], provenance.FAILURE_UNKNOWN)

    def test_a_failed_case_with_clean_provenance_is_attributed_to_analysis(self):
        attribution = provenance.failure_provenance(
            _case_result(
                "G01", passed=False, provenance_payload=_captured_provenance("G01")
            )
        )
        self.assertEqual(attribution["primary"], provenance.FAILURE_ANALYTICAL)

    def test_a_provider_failure_outranks_a_structural_one_in_attribution(self):
        payload = _captured_provenance("G01", structural_phase="audit")
        payload["phases"][0]["structural_failure_kind"] = "quota_exceeded"
        attribution = provenance.failure_provenance(
            _case_result("G01", passed=False, provenance_payload=payload)
        )
        self.assertEqual(attribution["primary"], provenance.FAILURE_PROVIDER)
        self.assertIn(provenance.FAILURE_STRUCTURAL, attribution["categories"])


# ─────────────────────────── judge provenance ───────────────────────────


class JudgeProvenanceTests(unittest.TestCase):
    """Regressions 47-50."""

    def _judge_with_output(self, output, *, recorder=None, response=None):
        case = load_cases()[0]
        captured = {}

        async def fake_call_llm(phase, system, prompt, config_override=None, *,
                                project_id="", before_attempt=None):
            captured["config_override"] = config_override
            captured["system"] = system
            captured["prompt"] = prompt
            return response or eval_runner.LLMResponse(
                text='{"score": 82, "rationale": "ok", "critical_failures": []}',
                ok=True,
                model_used="claude-sonnet-4-6-20260101",
                provider_used="anthropic",
                input_tokens=900,
                output_tokens=60,
            )

        original = eval_runner.call_llm
        eval_runner.call_llm = fake_call_llm
        try:
            result = asyncio.run(eval_runner.judge_case(case, output, recorder))
        finally:
            eval_runner.call_llm = original
        return result, captured

    def test_pre_and_post_truncation_lengths_and_the_flag_are_recorded(self):
        recorder = _recorder()
        oversized = {"classify": {"domain": "Complicated", "padding": "x" * 40000}}
        (score, _), _ = self._judge_with_output(oversized, recorder=recorder)
        record = recorder.judge_record

        self.assertEqual(score, 82)
        self.assertGreater(record["input_chars_pre_truncation"], JUDGE_INPUT_MAX_CHARS)
        self.assertEqual(record["input_chars_post_truncation"], JUDGE_INPUT_MAX_CHARS)
        self.assertTrue(record["input_truncated"])

    def test_an_untruncated_judge_input_is_reported_as_untruncated(self):
        recorder = _recorder()
        self._judge_with_output({"classify": {"domain": "Complicated"}}, recorder=recorder)
        record = recorder.judge_record

        self.assertEqual(
            record["input_chars_pre_truncation"], record["input_chars_post_truncation"]
        )
        self.assertFalse(record["input_truncated"])
        self.assertLess(record["input_chars_post_truncation"], JUDGE_INPUT_MAX_CHARS)

    def test_the_judge_configuration_itself_is_observed_and_unchanged(self):
        recorder = _recorder()
        _, captured = self._judge_with_output(
            {"classify": {"domain": "Complicated"}}, recorder=recorder
        )
        config = captured["config_override"]
        record = recorder.judge_record

        self.assertEqual(config.model, JUDGE_MODEL)
        self.assertEqual(config.provider.value, "anthropic")
        self.assertEqual(config.max_tokens, 1000)
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(captured["system"], JUDGE_SYSTEM_PROMPT)
        self.assertEqual(record["requested_model"], JUDGE_MODEL)
        self.assertEqual(record["requested_max_tokens"], 1000)
        self.assertEqual(record["requested_temperature"], 0.0)
        # `used_provider` is the gateway's routing record, which is a real fact
        # and is reported as one. What it is NOT is provider evidence, so it no
        # longer carries the `effective_` name.
        self.assertEqual(record["used_provider"], "anthropic")
        self.assertNotIn("effective_provider", record)
        # This fixture drives the response directly and supplies no telemetry
        # observation, so there is no provider-observed model. Before the F-2
        # fix this asserted `"claude-sonnet-4-6-20260101"` -- the value the
        # *gateway* put on `model_used`, reported as though the provider had
        # confirmed it.
        self.assertIsInstance(record["effective_model"], dict)
        self.assertNotEqual(record["effective_model"]["status"], provenance.STATUS_VALID)
        self.assertIsNone(record["effective_model"]["value"])
        self.assertTrue(record["response_ok"])

    def test_the_judge_record_stores_no_prompt_and_no_response_text(self):
        recorder = _recorder()
        output = {"classify": {"domain": "secret-domain-sentinel"}}
        self._judge_with_output(
            output,
            recorder=recorder,
            response=eval_runner.LLMResponse(
                text="secret-judge-response-sentinel",
                ok=True,
            ),
        )
        payload = json.dumps(recorder.judge_record)

        self.assertNotIn("secret-domain-sentinel", payload)
        self.assertNotIn("secret-judge-response-sentinel", payload)
        self.assertNotIn("You are a harsh but fair evaluator", payload)

    def test_a_judge_provider_failure_is_recorded_without_the_error_text(self):
        recorder = _recorder()
        (score, rationale), _ = self._judge_with_output(
            {"classify": {"domain": "Complicated"}},
            recorder=recorder,
            response=eval_runner.LLMResponse(
                text="",
                ok=False,
                error="Provider call failed: category=quota_exceeded, provider=anthropic",
                error_type="quota_exceeded",
            ),
        )

        # The rationale the harness already retained is unchanged; the ledger
        # keeps only the category, and does not duplicate the message.
        self.assertEqual(score, 0)
        self.assertTrue(rationale.startswith("judge error:"))
        self.assertFalse(recorder.judge_record["response_ok"])
        self.assertEqual(recorder.judge_record["error_type"], "quota_exceeded")
        self.assertNotIn("Provider call failed", json.dumps(recorder.judge_record))

    def test_judge_provenance_is_absent_rather_than_invented_without_a_recorder(self):
        (score, _), _ = self._judge_with_output({"classify": {"domain": "Complicated"}})
        self.assertEqual(score, 82)


# ─────────────────────── harness wiring and defaults ───────────────────────


class HarnessWiringTests(unittest.TestCase):
    def test_provenance_is_off_unless_explicitly_enabled(self):
        self.assertFalse(provenance.provenance_enabled({}))
        self.assertFalse(provenance.provenance_enabled({provenance.PROVENANCE_ENV: ""}))
        self.assertFalse(provenance.provenance_enabled({provenance.PROVENANCE_ENV: "0"}))
        for enabled in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=enabled):
                self.assertTrue(
                    provenance.provenance_enabled({provenance.PROVENANCE_ENV: enabled})
                )

    def test_a_disabled_run_opens_the_same_telemetry_scope_as_before(self):
        self.assertEqual(eval_runner._provenance_session_kwargs(None), {})

    def test_an_enabled_run_binds_an_eval_local_sink_and_an_explicit_posture(self):
        recorder = _recorder()
        kwargs = eval_runner._provenance_session_kwargs(recorder)
        self.assertIs(kwargs["sink"], recorder)
        self.assertEqual(kwargs["posture"], "observational")

    def test_the_case_ledger_is_never_able_to_break_a_case(self):
        class Exploding:
            calls = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        payload = eval_runner.build_case_provenance("G01", {}, Exploding())
        self.assertFalse(payload["captured"])
        self.assertEqual(payload["schema_version"], provenance.SCHEMA_VERSION)

    def test_the_mock_path_records_that_it_captured_nothing(self):
        payload = provenance.empty_case_provenance(
            case_id="G01", capture_mode=provenance.CAPTURE_MODE_MOCK
        )
        self.assertFalse(payload["captured"])
        self.assertEqual(payload["capture_mode"], provenance.CAPTURE_MODE_MOCK)
        self.assertEqual(payload["phases"], [])

    def test_a_case_result_round_trips_through_a_summary_with_its_provenance(self):
        payload = _captured_provenance("G01", structural_phase="audit")
        summary = summarize_results(
            [_case_result("G01", provenance_payload=payload)],
            threshold=PASS_THRESHOLD,
            mode="real",
            case_ids=["G01"],
        )
        restored = eval_runner._case_result_from_dict(summary["cases"][0])
        self.assertEqual(restored.provenance["counters"]["structural_failure_phases"], ["audit"])

    def test_a_case_result_from_a_pre_wave_summary_defaults_to_empty_provenance(self):
        restored = eval_runner._case_result_from_dict(
            {"case_id": "G01", "passed": True, "judge_overall": 70}
        )
        self.assertEqual(restored.provenance, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ════════════════════ F-2: provider-observed identity ════════════════════
#
# Three identities, three sources, and the artifact must not blur them:
#
#   requested_*   configuration          always known
#   selected_*    gateway routing        known once routing ran
#   used_provider gateway routing        known once a call was attempted
#   effective_model  PROVIDER OBSERVATION  known only if the provider said so
#
# `LLMResponse.model_used` is set from the REQUESTED model at every construction
# site in both adapters and is re-asserted from `config.model` in the gateway. It
# is therefore never evidence of what ran, and reading it as `effective_model`
# reported the request back as though the provider had confirmed it.


def _judge_response(**overrides):
    base = dict(
        ok=True,
        model_used="claude-sonnet-4-6",
        provider_used="anthropic",
        selected_model="claude-sonnet-4-6",
        selected_provider="anthropic",
        selection_reason="config_override",
        task_profile="judge",
        fallback_used=False,
        attempt_count=1,
        error_type="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _judge_record(recorder=None, **overrides):
    return provenance.judge_provenance(
        requested_provider="anthropic",
        requested_model="claude-sonnet-4-6",
        requested_max_tokens=1000,
        requested_temperature=0.0,
        input_chars_pre_truncation=10,
        input_chars_post_truncation=10,
        response=_judge_response(**overrides),
        recorder=recorder,
    )


def _judge_recorder(observation, *, invocation_id="j1"):
    recorder = _recorder()
    _feed(recorder, "jc", invocation_id, provenance.JUDGE_PHASE, observation=observation)
    return recorder


class JudgeProviderIdentityTests(unittest.TestCase):
    def test_p1_a_requested_model_never_becomes_a_provider_observation(self):
        """P1/P11 -- the F-2 finding itself."""

        record = _judge_record(recorder=None, model_used="gpt-5", selected_model="gpt-5")

        self.assertEqual(record["requested_model"], "claude-sonnet-4-6")
        self.assertEqual(record["selected_model"], "gpt-5")
        self.assertIsInstance(record["effective_model"], dict)
        self.assertNotEqual(record["effective_model"]["status"], provenance.STATUS_VALID)
        self.assertIsNone(record["effective_model"]["value"])
        # The decisive assertion: no requested or selected identity leaked into
        # the provider-observed field.
        self.assertNotIn("gpt-5", json.dumps(record["effective_model"]))

    def test_p2_a_selected_model_never_becomes_a_provider_observation(self):
        recorder = _judge_recorder(
            SimpleNamespace(
                effective_model=_Value("absent"),
                stop_reason=_Value("valid", "stop"),
                input_tokens=_Value("valid", 10),
                output_tokens=_Value("valid", 5),
                cache_read_tokens=_Value("absent"),
            )
        )
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["effective_model"]["status"], provenance.STATUS_ABSENT)
        self.assertIsNone(record["effective_model"]["value"])
        self.assertEqual(record["selected_model"], "claude-sonnet-4-6")

    def test_p3_a_matching_observed_model_is_preserved(self):
        recorder = _judge_recorder(_observation(model="claude-sonnet-4-6"))
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["effective_model"]["status"], provenance.STATUS_VALID)
        self.assertEqual(record["effective_model"]["value"], "claude-sonnet-4-6")

    def test_p4_an_observed_model_that_differs_is_preserved_truthfully(self):
        recorder = _judge_recorder(_observation(model="claude-sonnet-4-6-20260101"))
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["effective_model"]["value"], "claude-sonnet-4-6-20260101")
        self.assertNotEqual(record["effective_model"]["value"], record["requested_model"])

    def test_p5_p9_every_non_valid_provider_status_is_carried_not_replaced(self):
        """P5 missing · P6 absent · P7 null · P8 unsupported · P9 invalid."""

        for status, expected in (
            ("missing", provenance.STATUS_INVALID),  # no runtime counterpart
            ("absent", provenance.STATUS_ABSENT),
            ("null", provenance.STATUS_NULL),
            ("unsupported", provenance.STATUS_UNSUPPORTED),
            ("invalid", provenance.STATUS_INVALID),
        ):
            with self.subTest(status=status):
                recorder = _judge_recorder(
                    SimpleNamespace(
                        effective_model=_Value(status),
                        stop_reason=_Value("absent"),
                        input_tokens=_Value("absent"),
                        output_tokens=_Value("absent"),
                        cache_read_tokens=_Value("absent"),
                    )
                )
                record = _judge_record(recorder=recorder)
                self.assertEqual(record["effective_model"]["status"], expected)
                self.assertIsNone(record["effective_model"]["value"])

    def test_p10_a_fallback_reports_the_invocation_that_actually_answered(self):
        """The judge falls back to OpenAI whenever Anthropic is blank -- the
        Gate B posture. Reading the FIRST invocation would describe the failed
        Anthropic attempt as though it had produced the answer."""

        recorder = _recorder()
        _feed(
            recorder, "jc", "j1", provenance.JUDGE_PHASE,
            kind="provider_failure", category="authentication_error",
            observation=SimpleNamespace(
                effective_model=_Value("absent"),
                stop_reason=_Value("absent"),
                input_tokens=_Value("absent"),
                output_tokens=_Value("absent"),
                cache_read_tokens=_Value("absent"),
            ),
        )
        _feed(
            recorder, "jc", "j2", provenance.JUDGE_PHASE,
            observation=_observation(model="gpt-5-2026-01-01", inp=900, out=120),
        )
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["invocation_count"], 2)
        self.assertEqual(record["effective_model"]["value"], "gpt-5-2026-01-01")
        self.assertEqual(record["input_tokens"]["value"], 900)

    def test_p12_no_field_claims_a_provider_observed_provider(self):
        """No provider echoes its own identity, so nothing may be named as if
        one had. The gateway's routing record is reported as `used_provider`."""

        recorder = _judge_recorder(_observation())
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["used_provider"], "anthropic")
        self.assertNotIn("effective_provider", record)
        for key in record:
            self.assertFalse(
                key.startswith("effective_") and key != "effective_model",
                f"{key} claims provider-observed truth with no provider evidence",
            )

    def test_p13_usage_is_an_envelope_so_zero_is_not_mistaken_for_unobserved(self):
        """`LLMResponse` defaults every counter to 0, so a call that never
        reached a provider used to report a confident `0`."""

        record = _judge_record(recorder=None)

        for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
            with self.subTest(field=field):
                self.assertIsInstance(record[field], dict)
                self.assertNotEqual(record[field]["status"], provenance.STATUS_VALID)
                self.assertIsNone(record[field]["value"])

    def test_p13_an_observed_zero_is_still_reported_as_observed(self):
        recorder = _judge_recorder(_observation(inp=0, out=0))
        record = _judge_record(recorder=recorder)

        self.assertEqual(record["input_tokens"]["status"], provenance.STATUS_VALID)
        self.assertEqual(record["input_tokens"]["value"], 0)

    def test_p14_every_provider_observed_field_is_a_well_formed_envelope(self):
        recorder = _judge_recorder(_observation())
        record = _judge_record(recorder=recorder)

        for field in (
            "effective_model", "stop_reason", "input_tokens", "output_tokens",
            "cache_read_tokens", "reasoning_tokens", "content_status",
            "visible_content_length", "refusal_status",
        ):
            with self.subTest(field=field):
                envelope = record[field]
                self.assertIsInstance(envelope, dict, field)
                self.assertIn(envelope["status"], provenance.VALUE_STATUSES)
                self.assertIn("value", envelope)
                self.assertIn("detail", envelope)

    def test_the_judge_record_still_carries_no_raw_provider_text(self):
        recorder = _judge_recorder(_observation(model="gpt-5-2026"))
        record = _judge_record(recorder=recorder, error_type="refusal")
        payload = json.dumps(record)

        self.assertNotIn("system-sentinel", payload)
        self.assertNotIn("user-sentinel", payload)
