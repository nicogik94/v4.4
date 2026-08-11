import io
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai

from evals import provider_preflight


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "evals.yml"


class RecordingCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client_with_outcomes(*outcomes):
    completions = RecordingCompletions(outcomes)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=completions,
        )
    )
    return client, completions


def _choice(content, *, finish_reason="stop", refusal=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=refusal),
        finish_reason=finish_reason,
    )


def _response(*choices):
    return SimpleNamespace(choices=list(choices))


def _usable_response(text="OK"):
    """A response carrying usable visible text.

    Preflight now inspects the response, so a bare sentinel object no longer
    stands in for success.
    """

    return _response(_choice(text))


def _workflow_text():
    return WORKFLOW_PATH.read_text()


def _job_block(workflow: str, job_name: str) -> str:
    start_match = re.search(rf"(?m)^  {re.escape(job_name)}:\n", workflow)
    assert start_match, job_name
    next_match = re.search(
        r"(?m)^  [a-zA-Z0-9_-]+:\n",
        workflow[start_match.end() :],
    )
    end = (
        start_match.end() + next_match.start()
        if next_match
        else len(workflow)
    )
    return workflow[start_match.start() : end]


def _job_directive(job_block: str, directive: str) -> str:
    prefix = f"    {directive}:"
    matches = [
        line.strip()
        for line in job_block.splitlines()
        if line.startswith(prefix)
    ]
    assert len(matches) == 1, (directive, matches)
    return matches[0]


def test_probe_order_and_both_success():
    client, completions = _client_with_outcomes(
        _usable_response(),
        _usable_response(),
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    assert provider_preflight.run_preflight(
        client,
        stdout=stdout,
        stderr=stderr,
    )

    assert [call["model"] for call in completions.calls] == [
        "gpt-5-mini",
        "gpt-5",
    ]
    assert stderr.getvalue() == ""
    assert stdout.getvalue().splitlines() == [
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5-mini",
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5",
        "OPENAI_PROVIDER_PREFLIGHT=PASS",
    ]


def test_first_model_failure_stops_before_gpt5():
    client, completions = _client_with_outcomes(RuntimeError("not rendered"))
    stderr = io.StringIO()

    assert not provider_preflight.run_preflight(
        client,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert [call["model"] for call in completions.calls] == ["gpt-5-mini"]
    assert "model=gpt-5-mini" in stderr.getvalue()
    assert "type=RuntimeError" in stderr.getvalue()


def test_second_model_failure_occurs_only_after_first_success():
    client, completions = _client_with_outcomes(
        _usable_response(),
        RuntimeError("not rendered"),
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    assert not provider_preflight.run_preflight(
        client,
        stdout=stdout,
        stderr=stderr,
    )

    assert [call["model"] for call in completions.calls] == [
        "gpt-5-mini",
        "gpt-5",
    ]
    assert stdout.getvalue().splitlines() == [
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5-mini"
    ]
    assert "model=gpt-5 " in stderr.getvalue()


def test_probe_request_matches_runtime_gpt5_contract():
    request = provider_preflight.probe_request("gpt-5-mini")

    assert request == {
        "model": "gpt-5-mini",
        "messages": [
            {
                "role": "system",
                "content": "You are a concise assistant.",
            },
            {"role": "user", "content": "Reply with OK."},
        ],
        "max_completion_tokens": 512,
        "temperature": 1,
    }
    assert 0 < request["max_completion_tokens"] <= 1024
    assert "reasoning_effort" not in request
    assert "max_tokens" not in request


def test_probe_uses_chat_completions_create_with_contract_for_each_model():
    client, completions = _client_with_outcomes(
        _usable_response(),
        _usable_response(),
    )

    assert provider_preflight.run_preflight(
        client,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert completions.calls == [
        provider_preflight.probe_request("gpt-5-mini"),
        provider_preflight.probe_request("gpt-5"),
    ]


def test_client_configuration_disables_retries_timeout_and_sdk_debug_logging(
    monkeypatch,
):
    captured = {}
    sentinel = object()

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("OPENAI_LOG", "debug")
    client = provider_preflight.create_openai_client(
        "test-key",
        client_factory=fake_factory,
    )

    assert client is sentinel
    assert captured == {
        "api_key": "test-key",
        "timeout": 30.0,
        "max_retries": 0,
    }
    assert "OPENAI_LOG" not in provider_preflight.os.environ


def test_structured_bad_request_diagnostics_preserve_allowlisted_fields():
    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/chat/completions",
    )
    response = httpx.Response(
        400,
        request=request,
        headers={"x-request-id": "req_example123"},
    )
    exc = openai.BadRequestError(
        "wrapper includes raw body and must not be rendered",
        response=response,
        body={
            "message": "Unsupported value for temperature.",
            "type": "invalid_request_error",
            "code": "unsupported_value",
            "param": "temperature",
            "unapproved_debug": "must-not-appear",
        },
    )

    assert provider_preflight.diagnostic_fields(exc) == {
        "status": "400",
        "error_type": "invalid_request_error",
        "code": "unsupported_value",
        "param": "temperature",
        "request_id": "req_example123",
        "message": "Unsupported value for temperature.",
    }
    diagnostic = provider_preflight.format_failure("gpt-5-mini", exc)
    assert "type=BadRequestError" in diagnostic
    assert "status=400" in diagnostic
    assert "error_type=invalid_request_error" in diagnostic
    assert "code=unsupported_value" in diagnostic
    assert "param=temperature" in diagnostic
    assert "request_id=req_example123" in diagnostic
    assert 'message="Unsupported value for temperature."' in diagnostic
    assert "wrapper includes raw body" not in diagnostic
    assert "unapproved_debug" not in diagnostic
    assert "must-not-appear" not in diagnostic


def test_nested_api_status_body_is_supported_without_rendering_extra_fields():
    class APIStatusError(Exception):
        status_code = 422
        request_id = "req_nested"
        body = {
            "error": {
                "message": "Nested provider message.",
                "type": "invalid_request_error",
                "code": "nested_code",
                "param": "messages",
                "debug": "ignored",
            }
        }

    fields = provider_preflight.diagnostic_fields(APIStatusError("ignored"))

    assert fields == {
        "status": "422",
        "error_type": "invalid_request_error",
        "code": "nested_code",
        "param": "messages",
        "request_id": "req_nested",
        "message": "Nested provider message.",
    }


def test_missing_diagnostic_fields_remain_empty_and_exception_text_is_not_used():
    class EmptyAPIStatusError(Exception):
        message = "unsafe fallback must not appear"
        body = None

    exc = EmptyAPIStatusError("unsafe string form must not appear")

    assert provider_preflight.diagnostic_fields(exc) == {
        "status": "",
        "error_type": "",
        "code": "",
        "param": "",
        "request_id": "",
        "message": "",
    }
    diagnostic = provider_preflight.format_failure("gpt-5", exc)
    assert "status= error_type= code= param= request_id=" in diagnostic
    assert 'message=""' in diagnostic
    assert "unsafe" not in diagnostic


def test_diagnostic_redacts_secrets_headers_tokens_env_values_and_prompts():
    openai_secret = "sk-" + "redaction.example123"
    bearer_secret = "bearer-example-token"
    authorization_secret = "authorization example credential"
    signature_secret = "signature-example-value"
    api_key_secret = "api key example value"
    env_secret = "environment example value"
    unix_path = "/home/runner/work/private/config.json"
    generic_path = "/root/private/provider/config.json"
    windows_path = "C:\\Users\\runner\\private.txt"
    file_uri = "file:///tmp/private-provider-diagnostic.txt"

    class BadRequestError(Exception):
        status_code = 400
        request_id = openai_secret
        body = {
            "message": (
                f"secret={openai_secret}; Bearer {bearer_secret}\n"
                f"Authorization: Basic {authorization_secret}; trailing-secret\n"
                "Authorization: AWS4-HMAC-SHA256 "
                f"Credential={authorization_secret}, "
                f"Signature={signature_secret}; trailing-signature-secret\n"
                f'OPENAI_API_KEY="{api_key_secret}"; '
                f"OTHER_SECRET='{env_secret}'\n"
                f"unix={unix_path}; generic={generic_path}; "
                f"windows={windows_path}; uri={file_uri}\n"
                f"prompt={provider_preflight.PROBE_USER_PROMPT}"
            ),
            "type": "invalid_request_error",
            "code": None,
            "param": None,
        }

    diagnostic = provider_preflight.format_failure(
        "gpt-5-mini",
        BadRequestError("ignored"),
    )

    for secret in (
        openai_secret,
        bearer_secret,
        authorization_secret,
        api_key_secret,
        env_secret,
        signature_secret,
        "trailing-secret",
        "trailing-signature-secret",
        unix_path,
        generic_path,
        windows_path,
        file_uri,
        provider_preflight.PROBE_USER_PROMPT,
    ):
        assert secret not in diagnostic
    assert "REDACTED" in diagnostic
    assert "REDACTED_PROMPT" in diagnostic
    assert "REDACTED_PATH" in diagnostic


def test_diagnostic_message_and_emitted_json_are_hard_bounded():
    class BadRequestError(Exception):
        body = {"message": ("\U0001f600\"\\" * 2000)}

    exc = BadRequestError("ignored")
    message = provider_preflight.diagnostic_fields(exc)["message"]
    diagnostic = provider_preflight.format_failure("gpt-5-mini", exc)
    encoded_message = diagnostic.split(" message=", 1)[1]

    assert len(message) == provider_preflight.DIAGNOSTIC_MESSAGE_MAX_CHARS
    assert message.endswith("...")
    assert len(encoded_message) <= provider_preflight.DIAGNOSTIC_MESSAGE_MAX_CHARS + 2
    assert json.loads(encoded_message) == message


def test_hostile_body_accessor_cannot_escape_sanitized_fallback():
    class HostileMapping(Mapping):
        def __getitem__(self, key):
            raise RuntimeError("raw-body-sentinel")

        def __iter__(self):
            return iter(())

        def __len__(self):
            return 0

        def get(self, key, default=None):
            raise RuntimeError("raw-body-sentinel")

    class BadRequestError(Exception):
        status_code = 400
        body = HostileMapping()

        @property
        def type(self):
            raise RuntimeError("raw-body-sentinel")

    exc = BadRequestError("original-exception-sentinel")
    client, _ = _client_with_outcomes(exc)
    stderr = io.StringIO()

    assert not provider_preflight.run_preflight(
        client,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    diagnostic = stderr.getvalue()
    assert "OPENAI_PROVIDER_PREFLIGHT=FAIL" in diagnostic
    assert "type=BadRequestError" in diagnostic
    assert "status=400" in diagnostic
    assert "raw-body-sentinel" not in diagnostic
    assert "original-exception-sentinel" not in diagnostic
    assert "Traceback" not in diagnostic


def test_last_resort_formatter_uses_only_constant_exception_identity(monkeypatch):
    monkeypatch.setattr(
        provider_preflight,
        "diagnostic_fields",
        lambda exc: (_ for _ in ()).throw(RuntimeError("formatter-sentinel")),
    )

    diagnostic = provider_preflight.format_failure(
        "gpt-5-mini",
        RuntimeError("original-exception-sentinel"),
    )

    assert diagnostic == (
        "OPENAI_PROVIDER_PREFLIGHT=FAIL model=gpt-5-mini type=Exception "
        'status= error_type= code= param= request_id= message=""'
    )
    assert "formatter-sentinel" not in diagnostic
    assert "original-exception-sentinel" not in diagnostic


# ─── M3: usable-output contract ──────────────────────────────────────────────
#
# PASS means the configured model produced usable visible text under the
# preflight request shape.  "The SDK did not raise" is not sufficient.


def _run(*outcomes):
    """Run the preflight against injected responses; return (ok, out, err, calls)."""

    client, completions = _client_with_outcomes(*outcomes)
    stdout = io.StringIO()
    stderr = io.StringIO()
    ok = provider_preflight.run_preflight(
        client,
        stdout=stdout,
        stderr=stderr,
    )
    return ok, stdout.getvalue(), stderr.getvalue(), completions.calls


def _assert_first_model_rejected(stderr, calls, category, response=None):
    """Assert the first model failed with ``category`` and stopped the probe.

    Checking the emitted category (not merely the boolean) keeps these tests
    honest: an incidental harness error could otherwise satisfy a bare
    ``not ok``.
    """

    assert f"reason={category}" in stderr, response
    assert "model=gpt-5-mini" in stderr, response
    assert [call["model"] for call in calls] == ["gpt-5-mini"], response


def test_p1_normal_non_empty_response_passes():
    ok, stdout, stderr, calls = _run(_usable_response(), _usable_response())

    assert ok
    assert stderr == ""
    assert stdout.splitlines()[-1] == "OPENAI_PROVIDER_PREFLIGHT=PASS"
    assert len(calls) == 2


def test_p2_none_content_fails():
    ok, _, stderr, calls = _run(_response(_choice(None)))

    assert not ok
    _assert_first_model_rejected(stderr, calls, "empty_provider_output")
    assert "content=none" in stderr


def test_p3_empty_string_content_fails():
    ok, _, stderr, calls = _run(_response(_choice("")))

    assert not ok
    _assert_first_model_rejected(stderr, calls, "empty_provider_output")
    assert "content=empty" in stderr


def test_p4_whitespace_only_content_fails():
    ok, _, stderr, calls = _run(_response(_choice("  \n\t  ")))

    assert not ok
    _assert_first_model_rejected(stderr, calls, "empty_provider_output")
    assert "content=whitespace" in stderr


def test_p5_missing_or_empty_choices_fails():
    for response in (
        SimpleNamespace(),
        SimpleNamespace(choices=None),
        SimpleNamespace(choices=[]),
    ):
        ok, _, stderr, calls = _run(response)

        assert not ok, response
        _assert_first_model_rejected(
            stderr,
            calls,
            "malformed_response",
            response,
        )


def test_p6_malformed_message_or_content_fails():
    missing_message = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop")]
    )
    missing_content = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(),
                finish_reason="stop",
            )
        ]
    )
    non_string_content = _response(_choice(12345))

    for response in (missing_message, missing_content, non_string_content):
        ok, _, stderr, calls = _run(response)

        assert not ok, response
        _assert_first_model_rejected(
            stderr,
            calls,
            "malformed_response",
            response,
        )


def test_p7_empty_output_at_token_limit_reports_exhaustion():
    for content in (None, ""):
        ok, _, stderr, calls = _run(
            _response(_choice(content, finish_reason="length"))
        )

        assert not ok, content
        _assert_first_model_rejected(
            stderr,
            calls,
            "output_token_exhausted",
            content,
        )
        assert "finish_reason=length" in stderr


def test_p8_non_empty_output_at_token_limit_still_passes():
    partial = _response(_choice("OK but truncat", finish_reason="length"))
    ok, stdout, stderr, calls = _run(partial, partial)

    assert ok
    assert stderr == ""
    assert stdout.splitlines()[-1] == "OPENAI_PROVIDER_PREFLIGHT=PASS"
    assert len(calls) == 2


def test_p9_refusal_without_visible_text_fails_without_leaking_refusal():
    refusal_text = "refusal-sentinel-must-not-appear"
    ok, stdout, stderr, calls = _run(
        _response(_choice(None, refusal=refusal_text))
    )

    assert not ok
    _assert_first_model_rejected(stderr, calls, "empty_provider_output")
    assert "refusal=present" in stderr
    assert refusal_text not in stderr
    assert refusal_text not in stdout


def test_p9b_refusal_alongside_visible_text_is_usable_capability():
    # Pinned semantics: this probe measures output capability, not moderation.
    # Visible text proves the model can emit output, so a populated refusal
    # field alongside it does not fail the probe.
    response = _response(_choice("OK", refusal="also-refused-sentinel"))
    ok, stdout, stderr, _ = _run(response, response)

    assert ok
    assert stderr == ""
    assert "also-refused-sentinel" not in stdout

    assessment = provider_preflight.assess_output(response)
    assert assessment.usable
    assert assessment.refusal_status == provider_preflight.REFUSAL_PRESENT


def test_p10_unusable_first_model_stops_before_second():
    ok, stdout, stderr, calls = _run(
        _response(_choice("")),
        _usable_response(),
    )

    assert not ok
    assert [call["model"] for call in calls] == ["gpt-5-mini"]
    assert "model=gpt-5-mini" in stderr
    assert "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS" not in stdout
    assert "OPENAI_PROVIDER_PREFLIGHT=PASS" not in stdout


def test_p10b_unusable_second_model_fails_overall_after_a_usable_first():
    # A later model's failure must not be hidden behind the earlier success.
    ok, stdout, stderr, calls = _run(
        _usable_response(),
        _response(_choice(None, finish_reason="length")),
    )

    assert not ok
    assert [call["model"] for call in calls] == ["gpt-5-mini", "gpt-5"]
    assert stdout.splitlines() == [
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5-mini"
    ]
    assert "OPENAI_PROVIDER_PREFLIGHT=PASS" not in stdout
    assert "model=gpt-5 " in stderr
    assert "reason=output_token_exhausted" in stderr


def test_p11_both_models_usable_passes_overall():
    ok, stdout, stderr, calls = _run(_usable_response(), _usable_response())

    assert ok
    assert stderr == ""
    assert [call["model"] for call in calls] == ["gpt-5-mini", "gpt-5"]
    assert stdout.splitlines() == [
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5-mini",
        "OPENAI_PROVIDER_PREFLIGHT_MODEL=PASS model=gpt-5",
        "OPENAI_PROVIDER_PREFLIGHT=PASS",
    ]


def test_p12_sdk_exception_diagnostics_remain_sanitized():
    class BadRequestError(Exception):
        status_code = 400
        body = {"message": "exception-body-sentinel", "code": "bad_request"}

    ok, _, stderr, calls = _run(BadRequestError("exception-arg-sentinel"))

    assert not ok
    assert len(calls) == 1
    assert "OPENAI_PROVIDER_PREFLIGHT=FAIL" in stderr
    assert "type=BadRequestError" in stderr
    assert "status=400" in stderr
    assert "code=bad_request" in stderr
    # The exception path is untouched by M3: allowlisted body message still
    # renders, while the exception's own args never do.
    assert "exception-body-sentinel" in stderr
    assert "exception-arg-sentinel" not in stderr
    assert "Traceback" not in stderr


def test_p13_response_content_never_reaches_stdout_or_stderr():
    sentinel = "response-content-sentinel"
    hostile_finish_reason = "finish-reason-sentinel"
    responses = (
        _response(_choice(f"   {sentinel}   ".replace(sentinel, ""))),
        _response(_choice(None, refusal=sentinel)),
        _response(
            _choice("", finish_reason=hostile_finish_reason, refusal=sentinel)
        ),
        _response(_choice(sentinel.encode())),
    )

    for response in responses:
        ok, stdout, stderr, calls = _run(response)

        assert not ok, response
        assert len(calls) == 1, response
        assert sentinel not in stdout, response
        assert sentinel not in stderr, response
        assert hostile_finish_reason not in stdout, response
        assert hostile_finish_reason not in stderr, response

    # An unrecognized finish reason is normalized rather than echoed.
    assessment = provider_preflight.assess_output(
        _response(_choice("", finish_reason=hostile_finish_reason))
    )
    assert assessment.finish_reason == provider_preflight.FINISH_REASON_OTHER


def test_p14_hostile_response_accessors_cannot_crash_the_diagnostic_path():
    class ExplodingChoices:
        @property
        def choices(self):
            raise RuntimeError("choices-accessor-sentinel")

    class ExplodingMessage:
        @property
        def message(self):
            raise RuntimeError("message-accessor-sentinel")

        finish_reason = "stop"

    class ExplodingContent:
        @property
        def content(self):
            raise RuntimeError("content-accessor-sentinel")

        @property
        def refusal(self):
            raise RuntimeError("refusal-accessor-sentinel")

    hostile = (
        ExplodingChoices(),
        SimpleNamespace(choices=[ExplodingMessage()]),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=ExplodingContent(),
                    finish_reason="stop",
                )
            ]
        ),
    )

    for response in hostile:
        ok, stdout, stderr, calls = _run(response)

        assert not ok, response
        _assert_first_model_rejected(
            stderr,
            calls,
            "malformed_response",
            response,
        )
        assert "sentinel" not in stdout, response
        assert "sentinel" not in stderr, response
        assert "Traceback" not in stderr, response


def test_probe_request_is_unchanged_by_the_usable_output_contract():
    # M3 is response validation only; the request shape is pinned elsewhere in
    # this module and must not drift here.
    assert provider_preflight.PROBE_MODELS == ("gpt-5-mini", "gpt-5")
    assert provider_preflight.PROBE_MAX_COMPLETION_TOKENS == 512
    assert provider_preflight.PROBE_TEMPERATURE == 1
    assert provider_preflight.PROBE_MAX_RETRIES == 0
    assert provider_preflight.PROBE_TIMEOUT_SECONDS == 30.0
    assert provider_preflight.probe_request("gpt-5") == {
        "model": "gpt-5",
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Reply with OK."},
        ],
        "max_completion_tokens": 512,
        "temperature": 1,
    }


def test_usable_output_does_not_require_any_particular_reply_text():
    # The probe validates output capability, not judge quality.
    for text in ("OK", "no", "42", "¡hola!", "x" * 400):
        assert provider_preflight.assess_output(
            _response(_choice(text))
        ).usable, text


def test_workflow_self_path_can_trigger_evals_without_broadening_dispatch():
    workflow = _workflow_text()
    triggers = workflow.split("concurrency:", 1)[0]
    expected_dispatch = (
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      threshold:\n"
        "        description: 'Pass rate threshold (0.0-1.0)'\n"
        "        required: false\n"
        "        default: '0.75'"
    )

    assert "      - '.github/workflows/evals.yml'" in triggers
    assert triggers.count("  workflow_dispatch:\n") == 1
    dispatch_start = triggers.index("  workflow_dispatch:\n")
    assert triggers[dispatch_start:].strip() == expected_dispatch.strip()


def test_workflow_provider_preflight_pull_request_auth_is_non_draft_paid_eval():
    provider_job = _job_block(_workflow_text(), "provider-preflight")

    assert _job_directive(provider_job, "if") == (
        "if: ${{ github.event_name == 'workflow_dispatch' || "
        "(github.event.pull_request.draft == false && "
        "contains(github.event.pull_request.labels.*.name, 'paid-eval')) }}"
    )
    assert _job_directive(provider_job, "needs") == "needs: smoke"


def test_workflow_real_shards_and_aggregate_require_successful_preflight():
    workflow = _workflow_text()
    real_job = _job_block(workflow, "real-eval-shard")
    aggregate_job = _job_block(workflow, "aggregate")

    assert _job_directive(real_job, "needs") == (
        "needs: [smoke, provider-preflight]"
    )
    assert _job_directive(real_job, "if") == (
        "if: ${{ needs.provider-preflight.result == 'success' && "
        "(github.event_name == 'workflow_dispatch' || "
        "(github.event.pull_request.draft == false && "
        "contains(github.event.pull_request.labels.*.name, 'paid-eval'))) }}"
    )
    assert _job_directive(aggregate_job, "needs") == (
        "needs: [provider-preflight, real-eval-shard]"
    )
    assert _job_directive(aggregate_job, "if") == (
        "if: ${{ always() && needs.provider-preflight.result == 'success' && "
        "(github.event_name == 'workflow_dispatch' || "
        "(github.event.pull_request.draft == false && "
        "contains(github.event.pull_request.labels.*.name, 'paid-eval'))) }}"
    )


def test_workflow_paid_provider_steps_are_openai_only_with_secret_injection():
    workflow = _workflow_text()
    provider_job = _job_block(workflow, "provider-preflight")
    real_job = _job_block(workflow, "real-eval-shard")

    assert provider_job.count("ANTHROPIC_API_KEY: ''") == 1
    assert provider_job.count("OPENAI_LOG: ''") == 1
    assert real_job.count("ANTHROPIC_API_KEY: ''") == 2
    assert provider_job.count(
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"
    ) == 1
    assert real_job.count(
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"
    ) == 2
    assert "ANTHROPIC_API_KEY: ${{ secrets." not in workflow

    key_assignments = [
        line.strip()
        for line in workflow.splitlines()
        if line.strip().startswith("OPENAI_API_KEY:")
    ]
    assert key_assignments
    assert set(key_assignments) == {
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"
    }


def test_workflow_executes_tested_module_without_substantive_inline_python():
    provider_job = _job_block(_workflow_text(), "provider-preflight")

    assert "uses: actions/checkout@v4" in provider_job
    assert "working-directory: mas" in provider_job
    assert "run: python -m evals.provider_preflight" in provider_job
    assert "python - <<" not in provider_job
    assert "chat.completions.create" not in provider_job
    assert "max_completion_tokens" not in provider_job


# ═════════ eval failure provenance: bounded, and paid posture unchanged ═════════
#
# The provenance wave is observational. These pin the two things that make that
# claim checkable from the workflow alone: the release posture it was added
# beside is byte-for-byte the posture that ran before it, and the mechanism is
# switched on in exactly one place.

EVAL_PROVENANCE_FLAG = "MAS_EVAL_PROVENANCE"


def _job_names(workflow: str) -> list[str]:
    body = workflow.split("\njobs:\n", 1)[1]
    return [
        line[2:-1]
        for line in body.splitlines()
        if re.fullmatch(r"  [a-zA-Z0-9_-]+:", line)
    ]


def test_workflow_declares_no_new_job_and_no_new_provider_bearing_job():
    workflow = _workflow_text()

    assert _job_names(workflow) == [
        "smoke",
        "provider-preflight",
        "real-eval-shard",
        "aggregate",
    ]
    # A provider-bearing job is one the OpenAI secret is injected into. The set
    # is unchanged: preflight and the shard, and nothing else.
    provider_bearing = [
        name
        for name in _job_names(workflow)
        if "secrets.OPENAI_API_KEY" in _job_block(workflow, name)
    ]
    assert provider_bearing == ["provider-preflight", "real-eval-shard"]


def test_real_eval_shard_count_and_threshold_are_unchanged():
    real_job = _job_block(_workflow_text(), "real-eval-shard")

    assert "shard: [0, 1, 2, 3, 4, 5]" in real_job
    assert "fail-fast: false" in real_job
    assert "--shard-count 6" in real_job
    assert "--threshold ${{ github.event.inputs.threshold || '0.75' }}" in real_job


def test_aggregate_threshold_is_unchanged():
    aggregate_job = _job_block(_workflow_text(), "aggregate")

    assert "--threshold ${{ github.event.inputs.threshold || '0.75' }}" in aggregate_job
    assert "--aggregate evals/shards/eval-report-shard-*" in aggregate_job


def test_provenance_is_enabled_in_exactly_one_place_and_nowhere_else():
    workflow = _workflow_text()

    assert workflow.count(EVAL_PROVENANCE_FLAG) == 1
    assert f"{EVAL_PROVENANCE_FLAG}: '1'" in _job_block(workflow, "real-eval-shard")
    for job in ("smoke", "provider-preflight", "aggregate"):
        assert EVAL_PROVENANCE_FLAG not in _job_block(workflow, job)


def test_provenance_is_scoped_to_the_shard_run_step_only():
    real_job = _job_block(_workflow_text(), "real-eval-shard")
    steps = real_job.split("      - name: ")

    carrying = [step.splitlines()[0] for step in steps if EVAL_PROVENANCE_FLAG in step]

    assert carrying == ["Run eval shard"]


def test_provenance_adds_no_provider_call_shard_judge_or_model_invocation():
    before = subprocess.run(
        ["git", "show", f"HEAD:{WORKFLOW_PATH.relative_to(REPO_ROOT).as_posix()}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    after = _workflow_text()

    def significant(text: str) -> list[str]:
        """Every line that could cause work, comments and blanks removed."""
        return [
            line.rstrip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    added = [line for line in significant(after) if line not in significant(before)]
    removed = [line for line in significant(before) if line not in significant(after)]

    # The whole executable delta of this wave against its own base: one env
    # assignment. No step, no job, no matrix entry, no run command.
    assert added == [f"          {EVAL_PROVENANCE_FLAG}: '1'"]
    assert removed == []


def test_smoke_remains_zero_provider():
    smoke_job = _job_block(_workflow_text(), "smoke")

    assert "python -m evals.run_evals --mock" in smoke_job
    assert "secrets." not in smoke_job
    assert "OPENAI_API_KEY" not in smoke_job
    assert "ANTHROPIC_API_KEY" not in smoke_job
    assert EVAL_PROVENANCE_FLAG not in smoke_job
