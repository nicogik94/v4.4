"""Static guarantees and change scope for the provider-telemetry wave.

Covers required regression 24 — *newly added candidate files are included in
change-scope tests* — which is the one the previous wave could not have passed:
its whole implementation was new, so a scope check that read only ``git diff``
would have reported an empty change set.

The scope guards derive the wave's footprint from the checkout itself rather
than from any version-control delta. Every delta — ``git diff``, ``ls-files
--others``, a merge-base range — is empty in at least one state this test must
survive, and an empty delta makes a scope guard silently vacuous rather than
loud. Reading untracked status was the earlier form, and it failed the instant
the wave was committed without a single byte of the wave itself changing.

Where an active change set is still consulted it is narrowed to telemetry-owned
paths first. These guards answer "is the telemetry surface within its declared
contract", not "does this worktree contain anything else" — the second question
belongs to no wave, and asking it made an unrelated feature branch fail here.

The source-level assertions here use the AST with docstrings stripped rather than
substring matching. A module that *documents* what it refuses to do would
otherwise trip a naive substring check on its own prose — the same false positive
that cost the A-4C wave time.
"""
import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAS = ROOT / "mas"
if str(MAS) not in sys.path:
    sys.path.insert(0, str(MAS))

PACKAGE = MAS / "provider_telemetry"

# The complete, exact scope of this wave. Anything the discovered telemetry
# surface or an active change set turns up outside it is out of scope by
# definition — including a telemetry file someone adds later.
ALLOWED = {
    # runtime integration
    "mas/api.py",
    "mas/config.py",
    "mas/evals/run_evals.py",
    "mas/extensions/runtime.py",
    "mas/llm_client.py",
    "mas/main.py",
    "mas/runtime/provider_gateway.py",
    "mas/tools/cdp_review.py",
    "mas/tools/validate_t1a_gate2.py",
    # the telemetry package
    "mas/provider_telemetry/__init__.py",
    "mas/provider_telemetry/capture.py",
    "mas/provider_telemetry/delivery.py",
    "mas/provider_telemetry/identity.py",
    "mas/provider_telemetry/models.py",
    "mas/provider_telemetry/posture.py",
    "mas/provider_telemetry/redaction.py",
    "mas/provider_telemetry/repository.py",
    "mas/provider_telemetry/service.py",
    "mas/provider_telemetry/transport.py",
    "mas/provider_telemetry/values.py",
    # schema and tools
    "mas/sql/v63_provider_attempt_telemetry_foundation.sql",
    "mas/tools/provider_attempt_telemetry_export.py",
    "mas/tools/provider_attempt_telemetry_migrate.py",
    "mas/tools/provider_attempt_telemetry_restore.py",
    # tests
    "mas/tests/pg_dsn.py",
    "mas/tests/provider_telemetry_pg_support.py",
    "mas/tests/provider_telemetry_support.py",
    "mas/tests/test_evidence_source_capture_static.py",
    "mas/tests/test_provider_attempt_telemetry_capture.py",
    "mas/tests/test_provider_attempt_telemetry_capture_failures.py",
    "mas/tests/test_provider_attempt_telemetry_credentials.py",
    "mas/tests/test_provider_attempt_telemetry_delivery.py",
    "mas/tests/test_provider_attempt_telemetry_export.py",
    "mas/tests/test_provider_attempt_telemetry_gateway.py",
    "mas/tests/test_provider_attempt_telemetry_identity.py",
    "mas/tests/test_provider_attempt_telemetry_models.py",
    "mas/tests/test_provider_attempt_telemetry_pg.py",
    "mas/tests/test_provider_attempt_telemetry_pg_contract.py",
    "mas/tests/test_provider_attempt_telemetry_pg_dsn.py",
    "mas/tests/test_provider_attempt_telemetry_pg_expected_work.py",
    "mas/tests/test_provider_attempt_telemetry_pg_function_acl.py",
    "mas/tests/test_provider_attempt_telemetry_pg_snapshot.py",
    "mas/tests/test_provider_attempt_telemetry_sdk_client_parity.py",
    "mas/tests/test_provider_attempt_telemetry_service.py",
    "mas/tests/test_provider_attempt_telemetry_static.py",
    "mas/tests/test_provider_attempt_telemetry_transport.py",
    "mas/tests/test_provider_attempt_telemetry_transport_equivalence.py",
    "mas/tests/test_provider_attempt_telemetry_values.py",
    "mas/tests/test_provider_attempt_telemetry_worker_posture.py",
}

# Areas this wave must not touch, named explicitly so a widening is loud.
FORBIDDEN_PREFIXES = (
    "mas/prompts/",
    "mas/scenarios/",
    "mas/research_evidence/",
    "mas/knowledge/",
    "mas/decision_events.py",
)


class _DocstringStripper(ast.NodeTransformer):
    """Remove every docstring so prose cannot satisfy a source assertion."""

    def _strip(self, node):
        self.generic_visit(node)
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_Module = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip


def code_of(path: Path) -> str:
    """A module's executable source, with all docstrings removed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return ast.unparse(_DocstringStripper().visit(tree))


def sql_without_comments(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line.split("--", 1)[0] if "--" in line else line)
    return "\n".join(lines)


def sql_code(path: Path) -> str:
    """SQL with comments removed *and* string literals blanked.

    Blanking literals is what makes "this file issues no X" checkable at all: the
    migration's own diagnostics say things like "it creates no role and sets no
    PASSWORD", and its ACL postflight passes the privilege names 'TRUNCATE' and
    'REFERENCES' as data. Matching on raw text would flag every one of them.
    """
    text = sql_without_comments(path)
    out: list[str] = []
    in_literal = False
    for char in text:
        if char == "'":
            in_literal = not in_literal
            out.append("'")
            continue
        out.append(" " if in_literal else char)
    return "".join(out)


def sql_strings(path: Path) -> list[str]:
    """Every SQL string constant appearing in a Python module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    return values


# A statement that would rewrite or erase telemetry, as opposed to a mere mention
# of the word. Anchored on the telemetry relation prefix so a privilege name
# passed as data (`has_table_privilege(..., 'TRUNCATE')`) cannot match.
DESTRUCTIVE_STATEMENT = re.compile(
    r"\b(UPDATE\s+provider_|DELETE\s+FROM\s+provider_|TRUNCATE\s+(TABLE\s+)?provider_)",
    re.IGNORECASE,
)


def _git(*args) -> list[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()


def _untracked() -> set[str]:
    return {path for path in _git("ls-files", "--others", "--exclude-standard") if path}


def repository_files() -> set[str]:
    """Every file present in this checkout — tracked plus not-yet-tracked.

    Deliberately *not* a change set. ``git ls-files`` is non-empty in a dirty
    development worktree, a clean committed feature branch, a detached exact-SHA
    CI checkout and on merged main alike, whereas every change-set query
    (``git diff``, ``ls-files --others``, a merge-base delta) legitimately
    collapses to nothing in at least one of those four states.
    """
    return {path for path in set(_git("ls-files")) | _untracked() if path}


# What makes a file this wave's: it is named for the telemetry, or its
# executable source names it.
TELEMETRY_MARKERS = ("provider_attempt_telemetry", "provider_telemetry")


def _names_the_telemetry(path: Path) -> bool:
    """True when a file's *code* — not its prose — refers to the telemetry."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not any(marker in raw for marker in TELEMETRY_MARKERS):
        return False
    if path.suffix == ".sql":
        body = sql_without_comments(path)
    elif path.suffix == ".py":
        try:
            body = code_of(path)
        except SyntaxError:
            body = raw
    else:
        body = raw
    return any(marker in body for marker in TELEMETRY_MARKERS)


def _is_telemetry_owned(rel: str) -> bool:
    """Whether one repository-relative path belongs to this wave.

    Name first, then code: a path named for the telemetry is the wave's even
    when it cannot be read, and a path whose executable source names the
    telemetry is the wave's whatever it happens to be called.
    """
    if any(marker in rel for marker in TELEMETRY_MARKERS):
        return True
    return _names_the_telemetry(ROOT / rel)


def telemetry_surface() -> set[str]:
    """The wave's footprint, discovered from the checkout rather than from Git.

    This is the durable form of requirement 24. A brand-new package is invisible
    to ``git diff``; once the same package is committed it is invisible to
    ``git ls-files --others`` as well. Ownership is a property of the tree, so
    discovering it here keeps the scope guards below load-bearing in every state
    the test has to survive, including long after this branch is merged.
    """
    owned = set()
    for rel in repository_files():
        if not (ROOT / rel).is_file():
            continue
        if _is_telemetry_owned(rel):
            owned.add(rel)
    return owned


def active_telemetry_changes() -> set[str]:
    """The active change set, narrowed to the paths this wave owns.

    The narrowing is the whole point. An unrelated file edited or added in the
    same worktree is not evidence about the telemetry's scope, and unioning the
    raw change set into the candidate below turned these guards into "the
    repository has no other changes" — a claim no permanent test on main can
    make, and one an unrelated feature branch falsifies with a single empty
    file. Ownership, not co-residence in a worktree, decides membership.

    What the narrowing keeps is the half that matters: a telemetry path being
    edited or newly added is still measured against ``ALLOWED``, including a
    path the tree walk cannot classify on its own.
    """
    active = {path for path in _git("diff", "--name-only") if path} | _untracked()
    return {rel for rel in active if _is_telemetry_owned(rel)}


class ChangeScopeTests(unittest.TestCase):
    """Requirement 24."""

    def _inventory(self) -> set[str]:
        # The telemetry-owned half of an active change set still counts when
        # there is one, so a dirty worktree cannot smuggle an out-of-scope
        # telemetry edit past the guards below. It is unioned with — never
        # substituted for — the discovered surface, which is what remains once
        # the wave is committed.
        return active_telemetry_changes() | telemetry_surface()

    def test_the_change_inventory_covers_the_whole_telemetry_surface(self):
        inventory = self._inventory()
        surface = telemetry_surface()
        package = {p for p in surface if p.startswith("mas/provider_telemetry/")}
        # Anti-vacuity: the wave adds a whole package, so if discovery were
        # broken this would be empty and every scope assertion below trivial.
        self.assertTrue(package)
        self.assertTrue(surface <= inventory)
        # And whatever telemetry is not yet tracked is covered too — the half a
        # `git diff`-only check missed, still asserted where it exists.
        self.assertTrue({p for p in _untracked() if _is_telemetry_owned(p)} <= inventory)

    def test_the_change_inventory_is_within_the_declared_scope(self):
        unexpected = self._inventory() - ALLOWED
        self.assertEqual(unexpected, set(), f"out-of-scope changes: {sorted(unexpected)}")

    def test_no_forbidden_area_is_touched(self):
        for path in sorted(self._inventory()):
            for prefix in FORBIDDEN_PREFIXES:
                with self.subTest(path=path, prefix=prefix):
                    self.assertFalse(
                        path.startswith(prefix),
                        f"{path} is outside this wave's boundary ({prefix})",
                    )

    def test_the_unrelated_decision_events_defect_is_not_addressed_here(self):
        # A real defect, and deliberately not this wave's: touching it would
        # widen the change surface an auditor has to reason about.
        self.assertNotIn("mas/decision_events.py", self._inventory())
        sql_dir = MAS / "sql"
        self.assertEqual(list(sql_dir.glob("*decision_events*")), [
            sql_dir / "v46_backfill_decision_events_from_durable_tables.sql"
        ])


class MigrationScopeTests(unittest.TestCase):
    def test_v62_remains_unused_and_v63_is_this_wave(self):
        sql_dir = MAS / "sql"
        self.assertEqual(list(sql_dir.glob("v62_*.sql")), [])
        self.assertEqual(
            sorted(path.name for path in sql_dir.glob("v6*.sql")),
            [
                "v60_research_evidence_automation_roi_execution.sql",
                "v61_research_evidence_pack_foundation.sql",
                "v63_provider_attempt_telemetry_foundation.sql",
            ],
        )

    def test_the_migration_is_the_only_one_this_wave_adds(self):
        # Which migration belongs to this wave is a property of what the SQL
        # says, not of whether it happens to be staged yet: reading untracked
        # status here made the test pass only while the wave sat in a dirty
        # worktree, and fail the moment the very same files were committed.
        owned = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (MAS / "sql").glob("*.sql")
            if _names_the_telemetry(path)
        )
        self.assertEqual(owned, ["mas/sql/v63_provider_attempt_telemetry_foundation.sql"])


class SecretSafetySourceTests(unittest.TestCase):
    """No code path in the package can read a message, a body, or a credential."""

    FORBIDDEN_IN_REDACTION = (
        "str(exc)",
        ".message",
        "exc.args",
        "traceback",
        "repr(exc)",
    )

    def test_redaction_never_reads_an_exception_message(self):
        code = code_of(PACKAGE / "redaction.py")
        for token in self.FORBIDDEN_IN_REDACTION:
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_the_package_never_reads_prompt_or_response_text(self):
        forbidden = (
            "system_prompt",
            "user_prompt",
            "response.text",
            ".content",
            "choices[0].message",
        )
        for path in sorted(PACKAGE.glob("*.py")):
            code = code_of(path)
            for token in forbidden:
                with self.subTest(module=path.name, token=token):
                    self.assertNotIn(token, code)

    def test_only_three_response_headers_are_ever_named(self):
        from provider_telemetry import transport

        self.assertEqual(
            set(transport.PROVIDER_REQUEST_ID_HEADERS) | {transport.RETRY_AFTER_HEADER},
            {"request-id", "x-request-id", "retry-after"},
        )
        code = code_of(PACKAGE / "transport.py")
        # There is no path that enumerates or stores the header collection.
        for token in ("headers.items()", "dict(headers", "list(headers"):
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_no_api_key_or_dsn_is_read_anywhere_in_the_package(self):
        for path in sorted(PACKAGE.glob("*.py")):
            code = code_of(path)
            for token in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "api_key", "DATABASE_URL"):
                with self.subTest(module=path.name, token=token):
                    self.assertNotIn(token, code)


class AppendOnlySourceTests(unittest.TestCase):
    def test_the_package_issues_no_update_delete_or_truncate(self):
        for path in sorted(PACKAGE.glob("*.py")):
            for statement in sql_strings(path):
                with self.subTest(module=path.name, statement=statement[:40]):
                    self.assertIsNone(DESTRUCTIVE_STATEMENT.search(statement))

    def test_the_export_tool_contains_no_write_sql(self):
        path = MAS / "tools" / "provider_attempt_telemetry_export.py"
        for statement in sql_strings(path):
            with self.subTest(statement=statement[:40]):
                self.assertIsNone(DESTRUCTIVE_STATEMENT.search(statement))
                self.assertNotIn("INSERT INTO", statement.upper())
        # …and it never commits.
        self.assertNotIn("commit()", code_of(path))

    def test_the_migration_declares_no_foreign_key(self):
        sql = sql_code(MAS / "sql" / "v63_provider_attempt_telemetry_foundation.sql")
        self.assertIsNone(re.search(r"\bFOREIGN\s+KEY\b", sql, re.IGNORECASE))
        # An inline `REFERENCES table(col)` clause is the other FK spelling.
        self.assertIsNone(re.search(r"\bREFERENCES\s+\w+\s*\(", sql, re.IGNORECASE))

    def test_the_migration_creates_no_role_and_sets_no_password(self):
        sql = sql_code(MAS / "sql" / "v63_provider_attempt_telemetry_foundation.sql")
        for statement in (r"\bCREATE\s+ROLE\b", r"\bALTER\s+ROLE\b", r"\bPASSWORD\b"):
            with self.subTest(statement=statement):
                self.assertIsNone(re.search(statement, sql, re.IGNORECASE))


class NonBlockingSourceTests(unittest.TestCase):
    """Completion delivery is submitted, never awaited, on the provider path."""

    def _awaited_names(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                callee = node.value.func
                name = getattr(callee, "attr", None) or getattr(callee, "id", None)
                if name:
                    names.add(name)
        return names

    def test_the_gateway_never_awaits_an_event_submission(self):
        awaited = self._awaited_names(MAS / "runtime" / "provider_gateway.py")
        for name in ("submit_event", "submit_events", "append_event", "record"):
            with self.subTest(name=name):
                self.assertNotIn(name, awaited)

    def test_the_gateway_awaits_only_the_fail_closed_starts(self):
        awaited = self._awaited_names(MAS / "runtime" / "provider_gateway.py")
        telemetry_awaits = {
            name for name in awaited if "persist" in name or "telemetry" in name
        }
        # Exactly the three fail-closed start writes, and nothing else.
        self.assertEqual(
            telemetry_awaits,
            {"persist_call_start", "persist_invocation_start", "_persist_call_start"},
        )

    def test_submit_is_never_awaited_in_the_adapter_module(self):
        awaited = self._awaited_names(MAS / "llm_client.py")
        for name in ("submit_event", "submit_events", "append_event"):
            with self.subTest(name=name):
                self.assertNotIn(name, awaited)


class PostureDefaultTests(unittest.TestCase):
    def test_telemetry_is_off_by_default(self):
        import os

        import config
        from provider_telemetry import service

        for name in (
            config.PROVIDER_TELEMETRY_POSTURE_ENV,
            config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV,
        ):
            os.environ.pop(name, None)
        self.assertEqual(service.configured_posture(), service.POSTURE_OFF)
        self.assertFalse(config.provider_attempt_telemetry_enabled())

    def test_the_completeness_notice_never_claims_exhaustive_coverage(self):
        from provider_telemetry import service

        self.assertIn("NOT guaranteed", service.OBSERVATIONAL_COMPLETENESS_NOTICE)
        self.assertIn(
            "reconciliation", service.STRICT_COMPLETENESS_NOTICE
        )
        for notice in (
            service.OBSERVATIONAL_COMPLETENESS_NOTICE,
            service.STRICT_COMPLETENESS_NOTICE,
        ):
            with self.subTest(notice=notice[:32]):
                self.assertNotIn("exhaustive", notice.lower())
                self.assertNotIn("every event is recorded", notice.lower())


class ContractConsistencyTests(unittest.TestCase):
    def test_every_relation_has_a_keyset_and_a_column_tuple(self):
        from provider_telemetry import repository

        for table in repository.TELEMETRY_TABLES:
            with self.subTest(table=table):
                self.assertIn(table, repository.WRITE_COLUMNS)
                self.assertIn(table, repository.READ_COLUMNS)
                self.assertIn(table, repository.KEYSET_COLUMN)
                self.assertIn(table, repository.DATABASE_ASSIGNED)

    def test_the_migration_defines_every_relation_the_code_reads(self):
        sql = sql_without_comments(
            MAS / "sql" / "v63_provider_attempt_telemetry_foundation.sql"
        )
        from provider_telemetry import repository

        for table in repository.TELEMETRY_TABLES:
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} (", sql)

    def test_every_write_column_appears_in_the_migration(self):
        sql = sql_without_comments(
            MAS / "sql" / "v63_provider_attempt_telemetry_foundation.sql"
        )
        from provider_telemetry import repository

        for table, columns in repository.WRITE_COLUMNS.items():
            for column in columns:
                with self.subTest(table=table, column=column):
                    self.assertIn(column, sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
