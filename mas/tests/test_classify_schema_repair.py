"""Focused tests for classify schema-repair hardening."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import LLMResponse, parse_json
from orchestrator import (
    _parse_phase_json,
    _phase_json_retry_instruction,
    _repair_strategy_payload,
    _store_phase_output,
    build_classify_prompt,
    build_gauntlet_prompt,
    run_phase_node,
)
from state import ClassifyOutput, PhaseStatus, ProjectState


def make_classify_payload() -> dict:
    return {
        "domain": "Complicated",
        "justification": "Expert-discoverable cause-effect.",
        "bf": 85,
        "variety_env": "3 user types",
        "variety_sys": "Tutorial system",
        "variety_gaps": "1. No offline mode",
        "variety_decision": "Amplify",
        "ooda": {
            "observe": "Usage analytics",
            "orient": "FMEA",
            "decide": "Gate review",
            "act": "Fix",
            "freq": "Weekly",
        },
        "rpd_pattern": "SaaS adoption",
        "sensemaking_anchors": "confusion patterns",
        "expectancy_violations": "if experts also struggle",
        "reference_class": "30-40% adoption in 1 month",
        "dq": [20, 15, 18, 12],
        "maturity_assessment": "Level 2",
        "spiral_depth": "Spiral 1",
    }


def make_strategy_contract_payload() -> dict:
    return {
        "preliminary_verdicts": [
            {
                "id": "H1",
                "verdict": "NEEDS_MONITORING",
                "evidence": "bounded",
                "monitoring_plan": "observe",
            }
        ],
        "executive_strategy": "Run one bounded pilot.",
        "strategies": [
            {
                "priority": "HIGH",
                "action": "Run pilot",
                "justification": "It is reversible.",
                "evidence_chain": "H1 + audit -> pilot",
                "expected_impact": "Learn",
                "effort": "Low",
                "timeline": "2 weeks",
                "risk_if_ignored": "Uncertainty persists",
                "framework_source": "FMEA",
            }
        ],
        "implementation_sequence": "Pilot, review, decide",
        "success_metrics": ["One measured outcome"],
        "monitoring_plan": "Review weekly",
        "review_date": "2026-09-01",
        "confidence": "Medium",
        "reentry_check": "none",
    }


def make_state() -> ProjectState:
    state = ProjectState(project_id="test-classify", project_name="Test", brief="Test brief")
    # Skip the security intake pass in unit tests so they stay scoped to
    # classify JSON handling.
    state.intake_sanitization_findings = {}
    return state


def make_response(text: str, input_tokens: int = 10, output_tokens: int = 5,
                  cost_usd: float = 0.01) -> LLMResponse:
    return LLMResponse(
        text=text,
        ok=True,
        model_used="claude-haiku-4-5-20251001",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


class TestClassifyPromptAndParser(unittest.TestCase):
    def test_parse_json_handles_fenced_json_with_trailing_prose(self):
        text = "```json\n{\"a\": 1}\n```\n\nblah"
        self.assertEqual(parse_json(text), {"a": 1})

    def test_dict_required_parser_does_not_return_nested_list_from_unclosed_outer_object(self):
        text = '{"preliminary_verdicts":[{"id":"H1"}],"executive_strategy":"cut'

        self.assertIsNone(parse_json(text))
        self.assertIsNone(parse_json(text, expected_root_type=dict))

    def test_object_phase_preserves_complete_list_root_with_trailing_prose(self):
        payload = [{"id": "H1"}, {"id": "H2"}]
        text = json.dumps(payload) + " Done."

        self.assertEqual(_parse_phase_json("strategy", text), payload)
        self.assertNotIsInstance(_parse_phase_json("strategy", text), dict)

    def test_object_phase_preserves_list_before_second_json_object(self):
        payload = [{"id": "H1"}]
        text = json.dumps(payload) + ' {"executive_strategy":"later"}'

        self.assertEqual(_parse_phase_json("strategy", text), payload)

    def test_object_phase_does_not_promote_nested_dict_from_incomplete_dict(self):
        text = '{"outer":{"id":"nested"},"unfinished":"cut'

        self.assertIsNone(_parse_phase_json("strategy", text))

    def test_prose_prefixed_complete_dict_preserves_root(self):
        self.assertEqual(
            _parse_phase_json("strategy", 'Here is the result: {"ok":true} Done.'),
            {"ok": True},
        )
        self.assertEqual(
            _parse_phase_json("strategy", 'Result [JSON follows]: {"ok":true}'),
            {"ok": True},
        )

    def test_fenced_complete_dict_preserves_root(self):
        self.assertEqual(
            _parse_phase_json("strategy", '```json\n{"ok":true}\n```'),
            {"ok": True},
        )

    def test_parser_ignores_quoted_delimiters_and_handles_escaped_text(self):
        payload = {
            "text": 'quoted { brace } and [ bracket ] plus "quote" and \\path',
            "nested": [{"value": "still valid"}],
        }
        text = "Result: " + json.dumps(payload) + " trailing"

        self.assertEqual(_parse_phase_json("strategy", text), payload)

    def test_wrong_shaped_root_remains_observable_for_diagnostics(self):
        wrong_root = [{"id": "H1", "verdict": "NEEDS_MONITORING"}]

        parsed = _parse_phase_json(
            "strategy",
            json.dumps(wrong_root) + ' {"executive_strategy":"not selected"}',
        )

        self.assertEqual(parsed, wrong_root)
        self.assertIsInstance(parsed, list)

    def test_first_wrong_dict_is_not_replaced_by_later_strategy_fragment(self):
        verdict = {"id": "H1", "verdict": "NEEDS_MONITORING"}
        later = {"executive_strategy": "must not replace the first root"}

        parsed = _parse_phase_json(
            "strategy",
            json.dumps(verdict) + " " + json.dumps(later),
        )

        self.assertEqual(parsed, verdict)

    def test_permissive_parser_still_supports_legitimate_hypotheses_list(self):
        payload = [{"id": "H1"}, {"id": "H2"}]

        self.assertEqual(parse_json(json.dumps(payload)), payload)
        self.assertEqual(parse_json(f"```json\n{json.dumps(payload)}\n```"), payload)
        self.assertEqual(_parse_phase_json("hypotheses", json.dumps(payload)), payload)

    def test_first_structural_root_identity_never_promotes_nested_or_later_strategy(self):
        payload = make_strategy_contract_payload()
        encoded = json.dumps(payload)
        rejected = {
            "malformed_object_before_nested": "{\\" + encoded,
            "malformed_array_before_nested": "[\\" + encoded,
            "malformed_first_then_later": "{oops] " + encoded,
            "incomplete_object_with_nested": '{"wrapper":' + encoded,
            "incomplete_array_with_nested": "[" + encoded,
        }
        for label, text in rejected.items():
            with self.subTest(label=label):
                self.assertIsNone(_parse_phase_json("strategy", text))
                self.assertIsNone(_repair_strategy_payload(text))

        wrong_array = [{"id": "not-a-strategy"}]
        array_then_strategy = json.dumps(wrong_array) + " " + encoded
        self.assertEqual(_parse_phase_json("strategy", array_then_strategy), wrong_array)
        self.assertIsNone(_repair_strategy_payload(array_then_strategy))

    def test_strategy_root_identity_preserves_supported_wrappers_and_escaped_strings(self):
        payload = make_strategy_contract_payload()
        special = 'C:\\ops\\plan says "quoted" with {braces} and [brackets].'
        payload["executive_strategy"] = special
        encoded = json.dumps(payload)

        self.assertEqual(_parse_phase_json("strategy", "Result: " + encoded), payload)
        self.assertEqual(_parse_phase_json("strategy", f"```json\n{encoded}\n```"), payload)

        truncated = encoded[:-1] + ',"appendix":"cut'
        repaired = _repair_strategy_payload(truncated)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["executive_strategy"], special)

    def test_build_classify_prompt_requires_single_object(self):
        prompt = build_classify_prompt(make_state())
        self.assertIn("Return ONE JSON object", prompt)
        self.assertIn("Do NOT return an array", prompt)
        self.assertIn("ooda", prompt)
        self.assertIn("dq", prompt)

    def test_build_gauntlet_prompt_requires_compact_exact_schema(self):
        prompt = build_gauntlet_prompt(make_state())
        self.assertIn("Return ONE compact JSON object", prompt)
        self.assertIn("`results` must contain exactly 3 objects", prompt)
        self.assertIn("Do NOT include extra keys", prompt)
        self.assertIn("Each `frameworks` array must contain exactly 10 objects", prompt)

    def test_gauntlet_retry_instruction_forbids_extra_keys(self):
        instruction = _phase_json_retry_instruction("gauntlet")
        self.assertIn("results must contain exactly 3 objects", instruction)
        self.assertIn("Do NOT include prior, confidence, summary, rationale, notes", instruction)
        self.assertIn("Start with { and end with }", instruction)

    def test_ooda_list_fields_are_normalized(self):
        payload = make_classify_payload()
        payload["ooda"] = {
            "observe": ["one", "two"],
            "orient": ["three"],
            "decide": ["four"],
            "act": ["five"],
            "freq": ["weekly"],
        }
        output = ClassifyOutput(**payload)
        self.assertEqual(output.ooda.observe, "one; two")
        self.assertEqual(output.ooda.orient, "three")
        self.assertEqual(output.ooda.decide, "four")
        self.assertEqual(output.ooda.act, "five")
        self.assertEqual(output.ooda.freq, "weekly")

    def test_store_phase_output_accepts_single_dict_wrapped_list(self):
        state = make_state()
        _store_phase_output(state, "classify", [make_classify_payload()])
        self.assertIsNotNone(state.classify)
        self.assertEqual(state.classify.domain, "Complicated")


class TestClassifySchemaRepair(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = make_state()

    async def test_valid_dict_completes_without_retry(self):
        response = make_response(json.dumps(make_classify_payload()), 11, 7, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)) as call_llm_mock:
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(self.state, "classify")

        self.assertEqual(call_llm_mock.await_count, 1)
        self.assertEqual(updated.phase_status["classify"], PhaseStatus.COMPLETED)
        self.assertIsNotNone(updated.classify)
        self.assertEqual(updated.classify.domain, "Complicated")
        self.assertEqual(updated.budget_consumed["llm_call_count"], 1)
        self.assertEqual(updated.budget_consumed["total_tokens"], 18)
        self.assertAlmostEqual(updated.budget_consumed["total_cost_usd"], 0.02, places=6)

    async def test_list_response_triggers_repair_retry_and_succeeds(self):
        first = make_response(json.dumps(["wrong", "shape"]), 12, 4, 0.01)
        second = make_response(json.dumps(make_classify_payload()), 13, 5, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(side_effect=[first, second])) as call_llm_mock:
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                with self.assertLogs("orchestrator", level="WARNING") as logs:
                    updated = await run_phase_node(self.state, "classify")

        self.assertEqual(call_llm_mock.await_count, 2)
        self.assertEqual(updated.phase_status["classify"], PhaseStatus.COMPLETED)
        self.assertIsNotNone(updated.classify)
        self.assertEqual(updated.classify.domain, "Complicated")
        self.assertEqual(updated.budget_consumed["llm_call_count"], 2)
        self.assertEqual(updated.budget_consumed["total_tokens"], 34)
        self.assertAlmostEqual(updated.budget_consumed["total_cost_usd"], 0.03, places=6)
        self.assertTrue(
            any("parsed JSON had invalid top-level shape list" in line for line in logs.output)
        )

    async def test_list_response_fails_cleanly_after_bad_repair(self):
        first = make_response(json.dumps(["wrong", "shape"]), 8, 4, 0.01)
        second = make_response(json.dumps(["still", "wrong"]), 9, 5, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(side_effect=[first, second])) as call_llm_mock:
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                with self.assertLogs("orchestrator", level="WARNING") as logs:
                    updated = await run_phase_node(self.state, "classify")

        self.assertEqual(call_llm_mock.await_count, 2)
        self.assertEqual(updated.phase_status["classify"], PhaseStatus.FAILED)
        self.assertIsNone(updated.classify)
        self.assertTrue(
            any("retry returned invalid JSON shape list" in line for line in logs.output)
        )
        self.assertFalse(
            any("argument after ** must be a mapping" in line for line in logs.output)
        )
        self.assertFalse(any("Failed to parse classify output:" in line for line in logs.output))

    async def test_malformed_retry_marks_classify_failed(self):
        first = make_response(json.dumps(["wrong", "shape"]), 7, 3, 0.01)
        second = make_response("not json", 6, 4, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(side_effect=[first, second])) as call_llm_mock:
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                with self.assertLogs("orchestrator", level="WARNING") as logs:
                    updated = await run_phase_node(self.state, "classify")

        self.assertEqual(call_llm_mock.await_count, 2)
        self.assertEqual(updated.phase_status["classify"], PhaseStatus.FAILED)
        self.assertIsNone(updated.classify)
        self.assertTrue(any("JSON parse failed on retry too" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main(verbosity=2)
