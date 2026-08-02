"""The identity model and the entry-point matrix.

Covers required regression 21 — *every supported execution entry point carries
truthful identity* — in two complementary ways:

* **structurally**, by reading the source of each entry point and asserting it
  opens a telemetry scope with its own entry-point constant, so a new entry point
  cannot be added without appearing here;
* **behaviorally**, by driving a gateway call under each identity and asserting
  the recorded rows carry it.

It also pins the type discipline that blocker 8 turned on: a non-UUID evaluation
identifier is never cast into a PostgreSQL ``UUID`` column.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extensions.runtime import GatewayRequest, RoutingContext  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from provider_telemetry import repository  # noqa: E402
from provider_telemetry.delivery import NullDelivery  # noqa: E402
from provider_telemetry.identity import (  # noqa: E402
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_T1A_VALIDATION,
    ENTRY_POINT_UNKNOWN,
    ENTRY_POINTS,
    IDENTITY_BEARING_ENTRY_POINTS,
    TelemetryIdentity,
    TelemetryIdentityError,
    as_project_uuid,
    bind_identity,
    current_identity,
    worker_id,
)
from provider_telemetry.models import POSTURE_OBSERVATIONAL, TelemetryRunRecord  # noqa: E402
from provider_telemetry.service import TelemetrySession  # noqa: E402
from runtime.cache import NoOpSemanticCache  # noqa: E402
from runtime.provider_gateway import DefaultProviderGateway  # noqa: E402
from tests.provider_telemetry_support import BreakerStub, RecordingSink  # noqa: E402

# The authoritative matrix: every supported entry point, the module that owns it,
# and the constant that module must bind. A new supported entry point that is not
# added here fails `test_the_matrix_covers_every_identity_bearing_entry_point`.
ENTRY_POINT_MATRIX = (
    (ENTRY_POINT_API_WORKFLOW_RUN, "api.py", "ENTRY_POINT_API_WORKFLOW_RUN"),
    (ENTRY_POINT_API_MANUAL_PHASE, "api.py", "ENTRY_POINT_API_MANUAL_PHASE"),
    (ENTRY_POINT_CLI_WORKFLOW, "main.py", "ENTRY_POINT_CLI_WORKFLOW"),
    (ENTRY_POINT_CLI_SINGLE_PHASE, "main.py", "ENTRY_POINT_CLI_SINGLE_PHASE"),
    (ENTRY_POINT_EVALUATION_PHASE, "evals/run_evals.py", "ENTRY_POINT_EVALUATION_PHASE"),
    (ENTRY_POINT_EVALUATION_JUDGE, "evals/run_evals.py", "ENTRY_POINT_EVALUATION_JUDGE"),
    (ENTRY_POINT_CDP_REPORT_GATEWAY, "tools/cdp_review.py", "ENTRY_POINT_CDP_REPORT_GATEWAY"),
    (ENTRY_POINT_T1A_VALIDATION, "tools/validate_t1a_gate2.py", "ENTRY_POINT_T1A_VALIDATION"),
)


def _telemetry_scope_entry_points(path: Path) -> set[str]:
    """Every constant passed as ``entry_point=`` to ``telemetry_scope`` in a file.

    Read from the AST rather than by substring match, so a mention inside a
    docstring or a comment cannot satisfy the assertion.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = getattr(callee, "attr", None) or getattr(callee, "id", None)
        if name != "telemetry_scope":
            continue
        for keyword in node.keywords:
            if keyword.arg == "entry_point" and isinstance(keyword.value, ast.Name):
                found.add(keyword.value.id)
    return found


class EntryPointMatrixTests(unittest.TestCase):
    def test_every_entry_point_binds_its_own_constant(self):
        """Requirement 21, structurally."""
        by_file: dict[str, set[str]] = {}
        for _value, filename, constant in ENTRY_POINT_MATRIX:
            by_file.setdefault(filename, set()).add(constant)

        for filename, expected in sorted(by_file.items()):
            with self.subTest(module=filename):
                found = _telemetry_scope_entry_points(ROOT / filename)
                missing = expected - found
                self.assertFalse(
                    missing,
                    f"{filename} does not open a telemetry scope for {sorted(missing)}",
                )

    def test_the_matrix_covers_every_identity_bearing_entry_point(self):
        covered = {value for value, _file, _constant in ENTRY_POINT_MATRIX}
        self.assertEqual(covered, set(IDENTITY_BEARING_ENTRY_POINTS))

    def test_the_vocabulary_is_closed(self):
        with self.assertRaises(TelemetryIdentityError):
            TelemetryIdentity(entry_point="not_a_real_entry_point")
        # Every documented entry point is constructible.
        for value in ENTRY_POINTS:
            with self.subTest(entry_point=value):
                self.assertEqual(TelemetryIdentity(entry_point=value).entry_point, value)


class ProjectIdentityTypeTests(unittest.TestCase):
    """Blocker 8: a non-UUID identifier never reaches a UUID column."""

    def test_a_uuid_is_recognised_and_canonicalised(self):
        self.assertEqual(
            as_project_uuid("2A5A2E1C-0000-4000-8000-000000000001"),
            "2a5a2e1c-0000-4000-8000-000000000001",
        )

    def test_a_non_uuid_is_not_a_project_uuid(self):
        for raw in ("eval-brand-01", "", "   ", "12345", None, object()):
            with self.subTest(raw=raw):
                self.assertIsNone(as_project_uuid(raw))

    def test_an_evaluation_identifier_is_carried_as_an_external_identity(self):
        with bind_identity(entry_point=ENTRY_POINT_EVALUATION_PHASE, project_id="eval-7"):
            identity = current_identity()
        self.assertIsNone(identity.project_uuid)
        self.assertEqual(identity.external_project_id, "eval-7")

    def test_a_relational_uuid_is_carried_as_a_project_uuid(self):
        project = "2a5a2e1c-0000-4000-8000-000000000001"
        with bind_identity(entry_point=ENTRY_POINT_API_WORKFLOW_RUN, project_id=project):
            identity = current_identity()
        self.assertEqual(identity.project_uuid, project)
        self.assertEqual(identity.external_project_id, "")

    def test_identity_labels_cannot_carry_a_line_break(self):
        with bind_identity(entry_point=ENTRY_POINT_CLI_WORKFLOW, run_id="a\nINJECTED"):
            identity = current_identity()
        self.assertNotIn("\n", identity.run_id)

    def test_binding_extends_rather_than_replaces(self):
        with bind_identity(entry_point=ENTRY_POINT_API_WORKFLOW_RUN, run_id="run-1", job_id="job-1"):
            with bind_identity(phase="audit"):
                inner = current_identity()
        # An inner scope that knows only the phase must not blank the run.
        self.assertEqual(inner.run_id, "run-1")
        self.assertEqual(inner.job_id, "job-1")
        self.assertEqual(inner.phase, "audit")
        self.assertEqual(inner.entry_point, ENTRY_POINT_API_WORKFLOW_RUN)

    def test_identity_is_restored_after_a_scope_exits(self):
        with bind_identity(entry_point=ENTRY_POINT_CLI_WORKFLOW, run_id="r"):
            pass
        self.assertEqual(current_identity().entry_point, ENTRY_POINT_UNKNOWN)
        self.assertEqual(current_identity().run_id, "")

    def test_worker_identity_is_stable_within_a_process(self):
        self.assertEqual(worker_id(), worker_id())
        self.assertTrue(worker_id())


async def _ok(model, system, prompt, max_tokens, temperature, thinking_budget=0):
    return LLMResponse(text="ok", ok=True, model_used=model)


class RecordedIdentityTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 21, behaviorally: the identity actually lands in the rows."""

    async def _record_under(self, *, entry_point, project_id, run_id, job_id=""):
        sink = RecordingSink()
        session = TelemetrySession(
            run_record=TelemetryRunRecord(posture=POSTURE_OBSERVATIONAL),
            sink=sink,
            delivery=NullDelivery(),
        )
        gateway = DefaultProviderGateway(
            anthropic_executor=_ok,
            openai_executor=_ok,
            cache=NoOpSemanticCache(),
            breaker=BreakerStub(),
            telemetry=session,
        )
        with bind_identity(
            entry_point=entry_point, project_id=project_id, run_id=run_id, job_id=job_id
        ):
            await gateway.call(
                GatewayRequest(
                    phase="classify",
                    system_prompt="s",
                    user_prompt="p",
                    routing_context=RoutingContext(phase="classify"),
                )
            )
        return sink

    async def test_every_entry_point_records_its_own_identity(self):
        for entry_point, _file, _constant in ENTRY_POINT_MATRIX:
            with self.subTest(entry_point=entry_point):
                sink = await self._record_under(
                    entry_point=entry_point,
                    project_id="eval-42",
                    run_id=f"run-{entry_point}",
                    job_id="job-9",
                )
                call = sink.table(repository.CALL_TABLE)[0]
                invocation = sink.table(repository.INVOCATION_TABLE)[0]
                for record in (call, invocation):
                    self.assertEqual(record.identity.entry_point, entry_point)
                    self.assertEqual(record.identity.run_id, f"run-{entry_point}")
                    self.assertEqual(record.identity.job_id, "job-9")
                    # Non-UUID: external, never the relational column.
                    self.assertIsNone(record.identity.project_uuid)
                    self.assertEqual(record.identity.external_project_id, "eval-42")

    async def test_a_relational_project_lands_in_the_uuid_column(self):
        project = "2a5a2e1c-0000-4000-8000-000000000001"
        sink = await self._record_under(
            entry_point=ENTRY_POINT_API_WORKFLOW_RUN,
            project_id=project,
            run_id="run-1",
        )
        row = repository.call_row(sink.table(repository.CALL_TABLE)[0])
        columns = dict(zip(repository.CALL_COLUMNS, row))
        self.assertEqual(columns["project_id"], project)
        self.assertEqual(columns["external_project_id"], "")

    async def test_an_external_project_never_reaches_the_uuid_column(self):
        sink = await self._record_under(
            entry_point=ENTRY_POINT_EVALUATION_PHASE,
            project_id="eval-brand-01",
            run_id="eval-brand-01",
        )
        row = repository.call_row(sink.table(repository.CALL_TABLE)[0])
        columns = dict(zip(repository.CALL_COLUMNS, row))
        # `project_id` is a PostgreSQL UUID column; a non-UUID must arrive as
        # NULL there and be preserved beside it, never cast.
        self.assertIsNone(columns["project_id"])
        self.assertEqual(columns["external_project_id"], "eval-brand-01")
        self.assertEqual(columns["external_run_id"], "eval-brand-01")

    async def test_truthful_absence_is_recorded_when_there_is_no_identity(self):
        sink = await self._record_under(
            entry_point=ENTRY_POINT_UNKNOWN, project_id="", run_id=""
        )
        call = sink.table(repository.CALL_TABLE)[0]
        self.assertEqual(call.identity.run_id, "")
        self.assertIsNone(call.identity.project_uuid)
        self.assertEqual(call.identity.external_project_id, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
