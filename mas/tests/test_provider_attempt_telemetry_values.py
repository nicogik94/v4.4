"""Truthful provider values and the redaction grammars.

Covers the audit's required regressions 11, 13 and 14 directly:

* float / bool / numeric-string usage values are never coerced into valid counts;
* secret-shaped provider metadata is rejected or redacted;
* Unicode invisible-separator credential bypasses fail.

Every case here is a *counterexample the audit reproduced*, restated as an
assertion about behavior rather than about implementation.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import redaction  # noqa: E402
from provider_telemetry.values import (  # noqa: E402
    MAX_USAGE_VALUE,
    MISSING,
    VALUE_ABSENT,
    VALUE_INVALID,
    VALUE_NULL,
    VALUE_REDACTED,
    VALUE_UNKNOWN_VALUE,
    VALUE_UNSUPPORTED,
    VALUE_VALID,
    ProviderValue,
    ProviderValueError,
    exact_nonnegative_int,
)


class UsageValueTruthfulnessTests(unittest.TestCase):
    """Requirement 11: a count is an exact nonnegative int, or it is not a count."""

    def test_exact_integers_are_valid(self):
        for raw in (0, 1, 12, MAX_USAGE_VALUE):
            with self.subTest(raw=raw):
                value = exact_nonnegative_int(raw)
                self.assertEqual(value.status, VALUE_VALID)
                self.assertEqual(value.value, raw)

    def test_float_is_never_coerced(self):
        # The audit's exact counterexample: 1.9 must not become 1.
        for raw in (1.9, 1.0, 0.5, -0.0):
            with self.subTest(raw=raw):
                value = exact_nonnegative_int(raw)
                self.assertEqual(value.status, VALUE_INVALID)
                self.assertEqual(value.detail, "float")
                self.assertIsNone(value.stored)

    def test_bool_is_not_an_integer(self):
        # bool subclasses int in Python, so `isinstance(True, int)` is True and a
        # naive implementation stores True as the count 1.
        for raw in (True, False):
            with self.subTest(raw=raw):
                value = exact_nonnegative_int(raw)
                self.assertEqual(value.status, VALUE_INVALID)
                self.assertEqual(value.detail, "bool")
                self.assertIsNone(value.stored)

    def test_numeric_string_is_not_a_number(self):
        for raw in ("12", "0", " 7 ", "1e3"):
            with self.subTest(raw=raw):
                value = exact_nonnegative_int(raw)
                self.assertEqual(value.status, VALUE_INVALID)
                self.assertEqual(value.detail, "string")

    def test_negative_and_oversized_are_refused(self):
        self.assertEqual(exact_nonnegative_int(-1).detail, "negative")
        self.assertEqual(exact_nonnegative_int(MAX_USAGE_VALUE + 1).detail, "oversized")

    def test_absent_null_and_unsupported_stay_distinct(self):
        self.assertEqual(exact_nonnegative_int(MISSING).status, VALUE_ABSENT)
        self.assertEqual(exact_nonnegative_int(None).status, VALUE_NULL)
        self.assertNotEqual(VALUE_ABSENT, VALUE_NULL)
        self.assertNotEqual(VALUE_ABSENT, VALUE_UNSUPPORTED)

    def test_a_non_valid_value_can_never_carry_a_value(self):
        for status in (VALUE_ABSENT, VALUE_NULL, VALUE_INVALID, VALUE_REDACTED):
            with self.subTest(status=status):
                with self.assertRaises(ProviderValueError):
                    ProviderValue(status, 5)

    def test_a_valid_value_must_carry_a_value(self):
        with self.assertRaises(ProviderValueError):
            ProviderValue(VALUE_VALID, None)


class SecretSafetyTests(unittest.TestCase):
    """Requirement 13: secret-shaped provider metadata is rejected or redacted."""

    CREDENTIALS = (
        "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
        "sk-proj-abcdefghijklmnopqrstuvwxyz",
        "Bearer abcdefghijklmnop",
        "bearer:abcdefghijklmnop",
        "Basic dXNlcjpwYXNzd29yZA==",
        "api_key=supersecret",
        "api-key: supersecret",
        "authorization=Bearer x1234567",
        "session=abc123def456",
        "password:hunter2hunter2",
        "https://user:password@example.com/v1",
        "ghp_AAAAAAAAAAAAAAAAAAAA",
        "xoxb-1234-5678-abcdefg",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",
    )

    def test_credentials_never_survive_any_field_validator(self):
        validators = (
            redaction.provider_response_id,
            redaction.provider_model,
            redaction.provider_request_id,
            redaction.stop_reason,
            redaction.error_category,
        )
        for raw in self.CREDENTIALS:
            for validator in validators:
                with self.subTest(raw=raw, validator=validator.__name__):
                    value = validator(raw)
                    self.assertNotEqual(value.status, VALUE_VALID)
                    self.assertIsNone(value.stored)
                    # Nothing recognisable from the credential may survive into
                    # the diagnostic detail either.
                    self.assertNotIn("secret", value.detail)
                    self.assertNotIn("hunter2", value.detail)

    def test_exception_identity_never_reads_the_message(self):
        class HostileError(Exception):
            status_code = 401
            request_id = "req_ok123"
            type = "authentication_error"

            def __str__(self) -> str:  # pragma: no cover - must never be called
                raise AssertionError("redaction must never stringify an exception")

        identity = redaction.exception_identity(HostileError())
        self.assertIn("exception=HostileError", identity)
        self.assertIn("status_code=401", identity)
        self.assertIn("error_type=authentication_error", identity)
        self.assertIn("request_id=req_ok123", identity)

    def test_structured_exception_body_is_never_flattened(self):
        class BodyError(Exception):
            # A provider is free to echo the request into its error body. A
            # nested object must be ignored, not stringified into a token.
            body = {"error": {"type": {"nested": "sk-ant-leak"}, "message": "sk-ant-leak"}}

        identity = redaction.exception_identity(BodyError())
        self.assertNotIn("sk-ant", identity)
        self.assertNotIn("nested", identity)
        self.assertEqual(identity, "exception=BodyError")

    def test_error_body_with_a_credential_typed_as_a_string_is_redacted(self):
        class LeakyError(Exception):
            body = {"error": {"type": "sk-ant-api03-LEAKED"}}

        self.assertEqual(redaction.exception_identity(LeakyError()), "exception=LeakyError")

    def test_multiline_and_control_characters_are_refused(self):
        for raw in ("msg_01\nmsg_02", "msg\t01", "msg\r\n01", "msg\x00id"):
            with self.subTest(raw=raw):
                value = redaction.provider_response_id(raw)
                self.assertEqual(value.status, VALUE_REDACTED)
                self.assertEqual(value.detail, "nonprintable")


class UnicodeBypassTests(unittest.TestCase):
    """Requirement 14: invisible-separator credential bypasses fail."""

    INVISIBLES = (
        "​",  # zero width space
        "‌",  # zero width non-joiner
        "‍",  # zero width joiner
        "⁠",  # word joiner
        "﻿",  # zero width no-break space
        "‮",  # right-to-left override
        "­",  # soft hyphen
    )

    def test_invisible_separators_are_rejected_not_stripped(self):
        # Stripping is the bug: `sk-<ZWSP>ant-secret` would become a
        # grammar-conformant `sk-ant-secret` and be stored verbatim.
        for invisible in self.INVISIBLES:
            candidate = f"sk-{invisible}ant-api03-SECRETSECRET"
            with self.subTest(codepoint=hex(ord(invisible))):
                value = redaction.provider_response_id(candidate)
                self.assertEqual(value.status, VALUE_REDACTED)
                self.assertEqual(value.detail, "nonprintable")
                self.assertIsNone(value.stored)

    def test_fullwidth_compatibility_forms_are_normalized_before_scanning(self):
        # NFKC folds fullwidth Latin to ASCII; without normalization the
        # credential scan below would not match.
        fullwidth = "ｓｋ-ant-api03-SECRET"
        value = redaction.provider_response_id(fullwidth)
        self.assertEqual(value.status, VALUE_REDACTED)
        self.assertEqual(value.detail, "credential_shape")

    def test_ordinary_identifiers_still_pass(self):
        value = redaction.provider_response_id("msg_01ABCdef-234")
        self.assertEqual(value.status, VALUE_VALID)
        self.assertEqual(value.value, "msg_01ABCdef-234")


class GrammarTests(unittest.TestCase):
    def test_stop_reason_vocabulary(self):
        self.assertEqual(redaction.stop_reason("end_turn").status, VALUE_VALID)
        self.assertEqual(redaction.stop_reason("stop").status, VALUE_VALID)

    def test_unknown_stop_reason_is_bounded_and_explicitly_unknown(self):
        value = redaction.stop_reason("some_future_reason")
        self.assertEqual(value.status, VALUE_UNKNOWN_VALUE)
        self.assertIsNone(value.stored)
        # A bounded safe representation is kept, and it is exactly the token that
        # already passed the positive grammar.
        self.assertEqual(value.detail, "some_future_reason")

    def test_unknown_stop_reason_that_is_not_grammar_safe_is_invalid(self):
        value = redaction.stop_reason("a" * 200)
        self.assertEqual(value.status, VALUE_INVALID)
        self.assertIsNone(value.stored)

    def test_model_identifier_grammar(self):
        self.assertEqual(redaction.provider_model("claude-sonnet-4-6").status, VALUE_VALID)
        self.assertEqual(redaction.provider_model("gpt-4.1-2025-04-14").status, VALUE_VALID)
        # A slash would let a URL-shaped value through an identifier column.
        self.assertNotEqual(redaction.provider_model("http://evil/x").status, VALUE_VALID)

    def test_retry_after_accepts_only_delta_seconds(self):
        self.assertEqual(redaction.retry_after("30").value, "30")
        self.assertEqual(redaction.retry_after("1.5").value, "1.5")
        self.assertEqual(redaction.retry_after(30).value, "30")
        # The HTTP-date form is unsupported rather than parsed.
        self.assertEqual(
            redaction.retry_after("Wed, 21 Oct 2026 07:28:00 GMT").status,
            VALUE_UNSUPPORTED,
        )

    def test_http_status_range(self):
        self.assertEqual(redaction.http_status(200).value, 200)
        self.assertEqual(redaction.http_status(599).value, 599)
        self.assertEqual(redaction.http_status(99).status, VALUE_INVALID)
        self.assertEqual(redaction.http_status(True).detail, "bool")
        self.assertEqual(redaction.http_status(MISSING).status, VALUE_ABSENT)
        self.assertEqual(redaction.http_status(None).status, VALUE_NULL)

    def test_oversized_input_is_bounded_before_any_work(self):
        value = redaction.provider_response_id("a" * 100_000)
        self.assertEqual(value.status, VALUE_INVALID)
        self.assertEqual(value.detail, "oversized")

    def test_non_string_types_are_refused_without_stringification(self):
        class Hostile:
            def __str__(self) -> str:  # pragma: no cover - must never be called
                raise AssertionError("redaction must not stringify arbitrary objects")

        value = redaction.provider_response_id(Hostile())
        self.assertEqual(value.status, VALUE_INVALID)
        self.assertEqual(value.detail, "Hostile")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
