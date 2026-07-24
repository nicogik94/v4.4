"""Unit / no-database behavior for the R2.0A-4B operator bridge.

Covers argument validation, deterministic JSON output, dry-run vs commit at the
transaction-runner level, feature-disabled failure, missing-migration
diagnostics, typed-confirmation matching, secret/DSN redaction, and read-only
posture — all with injected fakes so no PostgreSQL is required. The full service
chain, byte-budget boundaries, and authorization/commit live in the _pg suite.
"""
import io
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.research_evidence_bridge as bridge  # noqa: E402
from research_evidence.presentation_projection_service import (  # noqa: E402
    project_research_evidence_pack,
)
from tests.test_research_evidence_presentation_models import build_pack  # noqa: E402


# ─────────────────────────── fakes ───────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """A minimal psycopg-like stand-in for read commands and the write runner."""

    def __init__(self, responder=None):
        self.autocommit = True
        self.read_only = False
        self.isolation_level = None
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.executed = []
        self._responder = responder or (lambda q, p: [])

    def execute(self, query, params=None):
        self.executed.append((str(query), params))
        return _Result(self._responder(str(query), params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


# ─────────────────────────── ready-topology responder ───────────────────────────
#
# The full catalog manifest is verified against real PostgreSQL in the _pg suite.
# At unit level we monkeypatch `_collect_catalog` (see `_ready_catalog`) and this
# lightweight responder only answers the non-catalog write-preflight probes:
# READ COMMITTED, the runtime identity, and the parent project.

# 7-column runtime identity, in the exact SELECT order of `_runtime_identity`:
# (db, inet_addr, inet_port, configured_port, system_identifier, user,
# current_schema). The cluster discriminators (configured_port,
# system_identifier) are what keep two socket-backed clusters — where inet addr
# and port are NULL/empty — from sharing a runtime fingerprint.
READY_IDENTITY = (
    "disposable_db", "10.9.8.7", "5432", "5432",
    "7300000000000000001", "operator", "public",
)
# Positional indices into READY_IDENTITY, for the bare 1-column probes.
_ID_DATABASE = 0
_ID_SYSTEM_IDENTIFIER = 4
_ID_SCHEMA = 6


def _ready_catalog():
    """An all-ready catalog dict matching `_collect_catalog`'s shape."""
    return {
        "current_schema": "public",
        "missing_relations": [], "relations_ready": True,
        "bad_functions": [], "functions_ready": True,
        "bad_triggers": [], "triggers_ready": True,
        "bad_constraints": [], "constraints_ready": True,
        "missing_roles": [], "roles_ready": True,
        "security_findings": [], "topology_security_ready": True,
        "namespace_findings": [], "namespace_ready": True,
    }


def ready_responder(query, params, *, isolation="read committed"):
    q = str(query)
    if "transaction_isolation" in q:
        return [(isolation,)]
    if "inet_server" in q:  # _runtime_identity (7-column)
        return [READY_IDENTITY]
    if "current_schema" in q:  # bare _current_schema
        return [(READY_IDENTITY[_ID_SCHEMA],)]
    if "current_database" in q:  # bare _current_database (1-column)
        return [(READY_IDENTITY[_ID_DATABASE],)]
    if "FROM projects" in q:  # parent project present
        return [("p", "Acme")]
    return []


def ready_conn(**overrides):
    """A FakeConn wired to the ready responder, with optional probe overrides."""
    isolation = overrides.pop("isolation", "read committed")
    return FakeConn(lambda q, p: ready_responder(q, p, isolation=isolation))


def _ready_fingerprint():
    return bridge._runtime_fingerprint(ready_conn())


def _patch_catalog(monkeypatch, catalog=None):
    """Monkeypatch `_collect_catalog` to a ready (or custom) catalog dict."""
    monkeypatch.setattr(
        bridge, "_collect_catalog", lambda conn: catalog or _ready_catalog()
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")


@pytest.fixture
def write_ready(monkeypatch):
    """Feature flags on AND an explicitly configured DATABASE_URL for writes."""
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://disposable-test/db")


def _run(argv, monkeypatch, conn=None, *, forbid_db=False):
    out = io.StringIO()
    if forbid_db:
        def factory():
            raise AssertionError("database opened unexpectedly")
    elif conn is not None:
        def factory():
            return conn
    else:
        def factory():
            raise AssertionError("no connection provided")
    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    code = bridge.main(argv, stream=out)
    return code, out.getvalue()


# ─────────────────────────── argument validation ───────────────────────────


def test_missing_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        bridge.build_parser().parse_args([])
    assert exc.value.code == bridge.EXIT_USAGE


@pytest.mark.parametrize(
    "argv",
    [
        ["claim-create", "--actor", "op", "--claim-text", "x"],  # no project
        ["fact-create", "--project-id", "p", "--actor", "op"],  # no fact-type
        ["authorize-internal-analysis", "--project-id", "p"],  # missing ids
        ["review-record", "--project-id", "p"],  # missing item/request
    ],
)
def test_missing_required_arguments_exit_usage(argv):
    with pytest.raises(SystemExit) as exc:
        bridge.build_parser().parse_args(argv)
    assert exc.value.code == bridge.EXIT_USAGE


def test_no_command_exposes_a_usage_scope_flag():
    parser = bridge.build_parser()
    # Argparse never learned a usage-scope option on any subcommand.
    help_text = parser.format_help()
    for action in parser._subparsers._group_actions[0].choices.values():
        assert "--usage-scope" not in action.format_help()
        assert "--scope" not in action.format_help()


# ─────────────────────────── helpers ───────────────────────────


def test_parse_string_array_json_and_delimited_and_empty():
    assert bridge._parse_string_array(None) == ()
    assert bridge._parse_string_array("  ") == ()
    assert bridge._parse_string_array('["a", "b"]') == ("a", "b")
    assert bridge._parse_string_array("a || b || c") == ("a", "b", "c")
    # An explicit empty string element in the JSON form is preserved as-is.
    assert bridge._parse_string_array('["a", ""]') == ("a", "")


@pytest.mark.parametrize(
    "value", ["[null]", "[1]", '[{"x": 1}]', '["ok", 2]', "[true]", "[[1]]"]
)
def test_parse_string_array_rejects_non_string_json_items(value):
    # Non-string JSON elements must be rejected, not coerced via str() into
    # "None"/"1"/"{'x': 1}" that would slip past the services' string-array
    # validators and persist malformed operator metadata.
    with pytest.raises(bridge.BridgeError):
        bridge._parse_string_array(value)


def test_parse_aware_datetime_requires_timezone():
    assert bridge._parse_aware_datetime("2026-01-01T00:00:00Z", "--x").tzinfo is not None
    with pytest.raises(bridge.BridgeError):
        bridge._parse_aware_datetime("2026-01-01T00:00:00", "--x")


def test_expected_confirmation_and_check():
    args = SimpleNamespace(confirm="proj claim ev")
    bridge._check_confirmation(args, "proj", "claim", "ev")  # exact match: no raise
    with pytest.raises(bridge.BridgeConfirmationError):
        bridge._check_confirmation(
            SimpleNamespace(confirm="proj claim wrong"), "proj", "claim", "ev"
        )
    with pytest.raises(bridge.BridgeConfirmationError):
        bridge._check_confirmation(SimpleNamespace(confirm=None), "proj", "claim", "ev")


def test_json_default_serializes_expected_types_and_rejects_others():
    assert bridge._json_default(Decimal("1.5")) == "1.5"
    assert bridge._json_default({3, 1, 2}) == [1, 2, 3]
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert bridge._json_default(dt).startswith("2026-01-01")
    with pytest.raises(TypeError):
        bridge._json_default(object())


def test_readonly_posture_is_pinned():
    conn = FakeConn()
    bridge._configure_readonly_connection(conn)
    assert conn.autocommit is False and conn.read_only is True


def test_write_posture_is_non_autocommit():
    conn = FakeConn()
    bridge._configure_write_connection(conn)
    assert conn.autocommit is False


# ─────────────────────────── dry-run vs commit runner ───────────────────────────


def _write_args(**kwargs):
    kwargs.setdefault("expect_runtime_fingerprint", _ready_fingerprint())
    return SimpleNamespace(**kwargs)


def test_run_write_dry_run_rolls_back(monkeypatch, write_ready):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    _patch_catalog(monkeypatch)
    out = io.StringIO()
    code = bridge._run_write(
        "demo", _write_args(commit=False), out, lambda c: {"widget": 1}
    )
    assert code == bridge.EXIT_OK
    assert conn.commits == 0 and conn.rollbacks >= 1
    payload = json.loads(out.getvalue())
    assert payload["dry_run"] is True and payload["committed"] is False
    assert payload["status"] == "dry_run" and payload["widget"] == 1


def test_run_write_commit_persists(monkeypatch, write_ready):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    _patch_catalog(monkeypatch)
    out = io.StringIO()
    code = bridge._run_write(
        "demo", _write_args(commit=True), out, lambda c: {"widget": 2}
    )
    assert code == bridge.EXIT_OK
    assert conn.commits == 1
    payload = json.loads(out.getvalue())
    assert payload["committed"] is True and payload["status"] == "committed"


def test_run_write_build_failure_rolls_back_without_commit(monkeypatch, write_ready):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    _patch_catalog(monkeypatch)

    def boom(c):
        raise bridge.BridgeError("bad input")

    with pytest.raises(bridge.BridgeError):
        bridge._run_write("demo", _write_args(commit=True), io.StringIO(), boom)
    assert conn.commits == 0
    assert conn.closed is True


# ─────────────────────── write preflight enforcement (unit) ──────────────────


def test_write_blocks_when_database_url_unconfigured(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # forbid_db proves the block happens before any connection is opened.
    code, text = _run(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", "whatever"],
        monkeypatch, forbid_db=True,
    )
    assert code == bridge.EXIT_FAILURE
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert payload["error_type"] == "BridgePreflightError"
    assert "postgresql://" not in text


def test_write_blocks_without_runtime_fingerprint(monkeypatch, write_ready):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    _patch_catalog(monkeypatch)
    out = io.StringIO()
    # No --expect-runtime-fingerprint supplied.
    code = bridge.main(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit"],
        stream=out,
    )
    assert code == bridge.EXIT_FAILURE
    assert json.loads(out.getvalue())["error_type"] == "BridgePreflightError"
    assert conn.commits == 0


def test_write_blocks_on_fingerprint_mismatch(monkeypatch, write_ready):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    _patch_catalog(monkeypatch)
    out = io.StringIO()
    code = bridge.main(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", "0" * 64],
        stream=out,
    )
    assert code == bridge.EXIT_FAILURE
    assert json.loads(out.getvalue())["error_type"] == "BridgePreflightError"
    assert conn.commits == 0


@pytest.mark.parametrize(
    "unready_flag",
    ["relations_ready", "functions_ready", "triggers_ready", "constraints_ready",
     "roles_ready", "topology_security_ready", "namespace_ready"],
)
def test_write_blocks_on_any_unready_catalog_category(monkeypatch, write_ready, unready_flag):
    conn = ready_conn()
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    catalog = _ready_catalog()
    catalog[unready_flag] = False
    _patch_catalog(monkeypatch, catalog)
    out = io.StringIO()
    code = bridge.main(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", _ready_fingerprint()],
        stream=out,
    )
    assert code == bridge.EXIT_FAILURE
    assert json.loads(out.getvalue())["error_type"] == "BridgePreflightError"
    assert conn.commits == 0


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable"])
def test_write_blocks_on_non_read_committed(monkeypatch, write_ready, isolation):
    conn = ready_conn(isolation=isolation)
    monkeypatch.setattr(bridge, "open_bridge_connection", lambda: conn)
    out = io.StringIO()
    code = bridge.main(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", _ready_fingerprint()],
        stream=out,
    )
    assert code == bridge.EXIT_FAILURE
    assert json.loads(out.getvalue())["error_type"] == "BridgePreflightError"
    assert conn.commits == 0


# ─────────────────────────── feature-disabled failure ───────────────────────────


def test_write_requires_research_evidence_flag(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    code, text = _run(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "hi", "--commit"],
        monkeypatch, forbid_db=True,
    )
    assert code == bridge.EXIT_FAILURE
    payload = json.loads(text)
    assert payload["status"] == "error" and payload["committed"] is False


def test_fact_create_requires_snapshot_flag(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.delenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", raising=False)
    code, text = _run(
        ["fact-create", "--project-id", "p", "--source-snapshot-id", "s",
         "--actor", "op", "--fact-type", "count", "--value", "1",
         "--counted-entity", "records", "--commit"],
        monkeypatch, forbid_db=True,
    )
    assert code == bridge.EXIT_FAILURE
    assert json.loads(text)["status"] == "error"


# ─────────────────────────── deterministic read output ───────────────────────────


def test_source_list_deterministic_json_is_metadata_safe(monkeypatch, enabled):
    def responder(query, params):
        if "FROM source_snapshot" in query:
            return [
                ("snap-1", "uploaded_file", "Doc A", "https://x/a", "primary"),
                ("snap-2", "uploaded_file", "Doc B", "https://x/b", "secondary"),
            ]
        return []

    conn = FakeConn(responder)
    code, text = _run(
        ["source-list", "--project-id", "proj-1"], monkeypatch, conn=conn
    )
    assert code == bridge.EXIT_OK
    payload = json.loads(text)
    assert payload["command"] == "source-list"
    assert payload["counts"]["source_count"] == 2
    assert payload["sources"][0]["source_snapshot_id"] == "snap-1"
    # metadata-safe: raw capture keys never appear (canonical_source_locator,
    # a citation field, is fine; the raw storage_ref / source_locator are not).
    assert '"storage_ref"' not in text
    assert '"source_locator"' not in text
    # read-only posture held
    assert conn.commits == 0 and conn.read_only is True and conn.closed is True


def test_project_show_reports_presence(monkeypatch, enabled):
    def responder(query, params):
        if "FROM projects" in query:
            return [("proj-1", "Acme")]
        return []

    conn = FakeConn(responder)
    code, text = _run(
        ["project-show", "--project-id", "proj-1"], monkeypatch, conn=conn
    )
    payload = json.loads(text)
    assert payload["project_present"] is True and payload["project_name"] == "Acme"


# ─────────────────────────── preflight readiness diagnostics ───────────────────────────
#
# The real catalog manifest is verified against PostgreSQL in the _pg suite. At
# unit level we monkeypatch `_collect_catalog` and verify how cmd_preflight
# aggregates its seven readiness flags into the write-eligibility verdicts.


def preflight_responder(query, params, *, project=True, snapshot=True):
    q = str(query)
    if "inet_server" in q:
        return [READY_IDENTITY]
    if "current_schema" in q:
        return [(READY_IDENTITY[_ID_SCHEMA],)]
    if "current_database" in q:
        return [(READY_IDENTITY[_ID_DATABASE],)]
    if "FROM projects" in q:
        return [(1,)] if project else []
    if "source_snapshot snapshot" in q:
        return [("snap", "k", "label", "loc", "tier", "pub")] if snapshot else []
    return []


def _preflight(monkeypatch, argv, *, catalog=None, responder=None):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://disposable-test/db")
    _patch_catalog(monkeypatch, catalog)
    conn = FakeConn(responder or (lambda q, p: preflight_responder(q, p)))
    code, text = _run(argv, monkeypatch, conn=conn)
    return code, text, json.loads(text)


def test_preflight_all_ready_permits_writes(monkeypatch):
    code, text, payload = _preflight(monkeypatch, ["preflight"])
    assert code == bridge.EXIT_OK
    for flag in ("relations_ready", "functions_ready", "triggers_ready",
                 "constraints_ready", "roles_ready", "topology_security_ready",
                 "namespace_ready"):
        assert payload[flag] is True
    assert payload["research_writes_allowed"] is True
    assert payload["fact_writes_allowed"] is True
    assert payload["writes_allowed"] is True
    # bounded, non-secret runtime fingerprint reported; never a DSN
    assert len(payload["runtime_fingerprint"]) == 64
    assert "postgresql://" not in text


@pytest.mark.parametrize(
    "unready_flag",
    ["relations_ready", "functions_ready", "triggers_ready", "constraints_ready",
     "roles_ready", "topology_security_ready", "namespace_ready"],
)
def test_preflight_any_unready_category_blocks(monkeypatch, unready_flag):
    catalog = _ready_catalog()
    catalog[unready_flag] = False
    _, _, payload = _preflight(monkeypatch, ["preflight"], catalog=catalog)
    assert payload[unready_flag] is False
    assert payload["research_writes_allowed"] is False
    assert payload["writes_allowed"] is False
    assert payload["status"] == "degraded"


def test_preflight_blocks_writes_when_database_url_unconfigured(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _patch_catalog(monkeypatch)
    conn = FakeConn(lambda q, p: preflight_responder(q, p))
    code, text = _run(["preflight"], monkeypatch, conn=conn)
    payload = json.loads(text)
    assert payload["database_url_configured"] is False
    assert payload["research_writes_allowed"] is False
    assert payload["writes_allowed"] is False


# ─────────────── MINOR 2: preflight readiness fidelity (supplied targets) ─────


def test_preflight_snapshot_flag_off_flips_fact_writes_allowed(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.delenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://disposable-test/db")
    _patch_catalog(monkeypatch)
    conn = FakeConn(lambda q, p: preflight_responder(q, p))
    code, text = _run(["preflight"], monkeypatch, conn=conn)
    payload = json.loads(text)
    # research writes still allowed; fact writes gated off by the snapshot flag
    assert payload["research_writes_allowed"] is True
    assert payload["fact_writes_allowed"] is False


def test_preflight_missing_project_flips_requested_target_ready(monkeypatch):
    _, _, payload = _preflight(
        monkeypatch, ["preflight", "--project-id", "p"],
        responder=lambda q, p: preflight_responder(q, p, project=False),
    )
    assert payload["project_present"] is False
    assert payload["requested_target_ready"] is False
    assert payload["writes_allowed"] is False
    # whole-topology eligibility remains true; only the supplied target failed
    assert payload["research_writes_allowed"] is True


def test_preflight_missing_snapshot_flips_requested_target_ready(monkeypatch):
    _, _, payload = _preflight(
        monkeypatch,
        ["preflight", "--project-id", "p", "--source-snapshot-id", "s"],
        responder=lambda q, p: preflight_responder(q, p, snapshot=False),
    )
    assert payload["source_snapshot_present"] is False
    assert payload["requested_target_ready"] is False
    assert payload["writes_allowed"] is False


def test_preflight_expect_database_mismatch_flips_requested_target_ready(monkeypatch):
    _, _, payload = _preflight(
        monkeypatch, ["preflight", "--expect-database", "some_other_db"],
    )
    assert payload["same_runtime_database"] is False
    assert payload["requested_target_ready"] is False
    assert payload["writes_allowed"] is False


def test_preflight_connection_unavailable_is_diagnostic_not_crash(monkeypatch, enabled):
    def factory():
        raise ConnectionError("postgresql://secret@host/db down")

    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    out = io.StringIO()
    code = bridge.main(["preflight"], stream=out)
    assert code == bridge.EXIT_OK
    text = out.getvalue()
    payload = json.loads(text)
    assert payload["connection_available"] is False
    assert payload["writes_allowed"] is False
    # redaction: the DSN never appears
    assert "postgresql://" not in text and "secret" not in text


# ─────────────────────────── redaction ───────────────────────────


def test_unexpected_error_redacts_dsn_and_secrets(monkeypatch, write_ready):
    def factory():
        raise ConnectionError("postgresql://user:pw@host:5432/workflow_v4")

    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    out = io.StringIO()
    code = bridge.main(
        ["claim-create", "--project-id", "p", "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", "any"],
        stream=out,
    )
    assert code == bridge.EXIT_FAILURE
    text = out.getvalue()
    assert "postgresql://" not in text
    assert "workflow_v4" not in text and "pw" not in text
    payload = json.loads(text)
    assert payload["status"] == "error" and payload["error_type"] == "ConnectionError"


# ─────────────────────────── byte-budget boundaries ───────────────────────────


def _block_bytes(projection):
    """Measure the complete rendered block size with the budget guard lifted."""
    import research_evidence_context as rc

    saved = rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES
    rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES = 1_000_000_000
    try:
        return len(rc.render_research_evidence_block(projection).encode("utf-8"))
    finally:
        rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES = saved


def _build_merged(claim_lengths):
    """Merge one internal_analysis member per requested ASCII claim length."""
    from research_evidence.pack_models import (
        ResearchEvidencePackAggregate,
        ResearchEvidencePackCounts,
    )

    packs = []
    project_id = None
    for length in claim_lengths:
        from tests.test_research_evidence_presentation_models import pack_ids

        ids = pack_ids()
        if project_id is None:
            project_id = ids["project"]
        ids["project"] = project_id
        packs.append(
            build_pack("internal_analysis", ids=ids, claim_text="A" * length)
        )
    claims = tuple(sorted((p.claims[0] for p in packs), key=lambda i: i.claim_draft_id))
    sources = tuple(
        sorted((p.sources[0] for p in packs), key=lambda i: i.source_snapshot_id)
    )
    evidence = tuple(
        sorted(
            (p.evidence[0] for p in packs),
            key=lambda i: (i.source_snapshot_id, i.candidate_fact_revision_id),
        )
    )
    relationships = tuple(
        sorted(
            (p.relationships[0] for p in packs),
            key=lambda i: (
                i.claim_draft_id, i.source_snapshot_id, i.candidate_fact_revision_id
            ),
        )
    )
    aggregate = ResearchEvidencePackAggregate(
        project_id=project_id, usage_scope="internal_analysis",
        context=packs[0].context, claims=claims, sources=sources,
        evidence=evidence, relationships=relationships,
        counts=ResearchEvidencePackCounts(
            source_count=len(packs), claim_count=len(packs),
            evidence_count=len(packs), relationship_count=len(packs),
        ),
    )
    return project_research_evidence_pack(aggregate)


def _sized_internal_projection(target_bytes):
    """Return an internal_analysis projection rendering to exactly ``target_bytes``.

    Uses uniform minimal members plus one member padded with ASCII claim text
    (rendered verbatim, one byte per character) to solve the residual exactly.
    """
    count = 1
    while True:
        size_min = _block_bytes(_build_merged([1] * count))
        gap = target_bytes - size_min
        if 0 <= gap <= 9999:
            break
        assert size_min <= target_bytes, "overshot the exact-byte window"
        count += 1
    lengths = [1] * count
    lengths[0] = 1 + gap
    projection = _build_merged(lengths)
    assert _block_bytes(projection) == target_bytes
    return projection


def test_block_at_65535_is_within_limit():
    projection = _sized_internal_projection(65535)
    status, size = bridge._classify_projection_block(projection)
    assert status == "WITHIN_LIMIT" and size == 65535


def test_block_at_exactly_65536_is_within_limit():
    projection = _sized_internal_projection(65536)
    status, size = bridge._classify_projection_block(projection)
    assert status == "WITHIN_LIMIT" and size == 65536


def test_block_at_65537_would_block_prompt_overflow_with_no_partial():
    projection = _sized_internal_projection(65537)
    status, size = bridge._classify_projection_block(projection)
    assert status == "WOULD_BLOCK_PROMPT_OVERFLOW" and size is None


def test_empty_projection_classifies_empty_zero_bytes():
    projection = project_research_evidence_pack(
        __import__("research_evidence").pack_models.ResearchEvidencePackAggregate(
            project_id="00000000-0000-0000-0000-000000000000",
            usage_scope="internal_analysis",
        )
    )
    status, size = bridge._classify_projection_block(projection)
    assert status == "EMPTY" and size == 0


def test_projection_preview_capacity_overflow_omits_rendered_bytes(monkeypatch, enabled):
    # MINOR 1: on a capacity overflow no projection is rendered, so
    # rendered_utf8_bytes is OMITTED (not fabricated as 0).
    import research_evidence.presentation_projection_service as pps
    from research_evidence.pack_service import ResearchEvidencePackLimitError

    def boom(conn, *, project_id, usage_scope):
        raise ResearchEvidencePackLimitError("capacity exceeded")

    monkeypatch.setattr(pps, "project_research_evidence_presentation", boom)
    conn = FakeConn()
    code, text = _run(
        ["projection-preview", "--project-id", "p"], monkeypatch, conn=conn
    )
    assert code == bridge.EXIT_OK
    payload = json.loads(text)
    assert payload["block_status"] == "WOULD_BLOCK_CAPACITY_OVERFLOW"
    assert "rendered_utf8_bytes" not in payload


# ═══════ ITERATION 5: load-bearing request-id uniqueness (unit logic) ═══════
#
# `_constraint_problem` is pure, so every drift shape is discriminated here; the
# catalog *queries* that feed it are proven against real PostgreSQL in the _pg
# suite (including that each drift blocks the corresponding real write command).


def _canonical_constraint_state(**overrides):
    """A state dict as `_constraint_state` returns it for a healthy constraint."""
    state = {
        "contype": "u",
        "convalidated": True,
        "condeferrable": False,
        "condeferred": False,
        "conrelid": 4242,
        "conindid": 9001,
        "columns": ("project_id", "research_evidence_intake_item_id", "request_id"),
        "index_relation": 4242,
        "index_unique": True,
        "index_valid": True,
        "index_ready": True,
        "index_live": True,
        "index_immediate": True,
        "index_has_expressions": False,
        "index_has_predicate": False,
    }
    state.update(overrides)
    return state


REIRD_COLUMNS = ("project_id", "research_evidence_intake_item_id", "request_id")


def test_canonical_request_constraint_has_no_problem():
    assert bridge._constraint_problem(
        _canonical_constraint_state(), expected_columns=REIRD_COLUMNS
    ) is None


def test_absent_request_constraint_is_missing():
    assert bridge._constraint_problem(
        None, expected_columns=REIRD_COLUMNS
    ) == "missing"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        # A CHECK/FK/PK constraint wearing the protected name is not a UNIQUE.
        ({"contype": "c"}, "contype"),
        ({"contype": "p"}, "contype"),
        # Wrong column set, missing the request key entirely.
        ({"columns": ("project_id", "research_evidence_intake_item_id")}, "columns"),
        # Same columns, PERMUTED: ordering is frozen as ratified catalog exactness.
        ({"columns": ("research_evidence_intake_item_id", "project_id",
                      "request_id")}, "columns"),
        # An extra column widens the key, so a repeated request_id no longer collides.
        ({"columns": REIRD_COLUMNS + ("decision_sequence",)}, "columns"),
        ({"columns": ()}, "columns"),
        # NOT VALID: pre-existing rows were never checked.
        ({"convalidated": False}, "not_validated"),
        # DEFERRABLE: the violation surfaces at COMMIT, so the repository's
        # per-statement 23505 recovery arm is never reached.
        ({"condeferrable": True}, "deferrable"),
        ({"condeferrable": True, "condeferred": True}, "deferrable"),
        ({"condeferred": True}, "initially_deferred"),
        # No backing index at all → nothing enforces uniqueness.
        ({"conindid": 0, "index_relation": None}, "index_missing"),
        ({"index_relation": None}, "index_missing"),
        # The index belongs to a DIFFERENT relation.
        ({"index_relation": 777}, "index_relation"),
        # A non-unique backing index enforces nothing.
        ({"index_unique": False}, "index_not_unique"),
        # Invalid / not-ready / not-live indexes do not enforce on write.
        ({"index_valid": False}, "index_not_valid"),
        ({"index_ready": False}, "index_not_ready"),
        ({"index_live": False}, "index_not_live"),
        # A non-immediate index defers enforcement past the statement.
        ({"index_immediate": False}, "index_not_immediate"),
        # Expression / partial indexes do not constrain the raw tuple.
        ({"index_has_expressions": True}, "index_expression"),
        ({"index_has_predicate": True}, "index_partial"),
    ],
)
def test_request_constraint_drift_is_discriminated(overrides, reason):
    assert bridge._constraint_problem(
        _canonical_constraint_state(**overrides), expected_columns=REIRD_COLUMNS
    ) == reason


def test_every_manifest_constraint_is_checked_against_its_own_columns():
    """A constraint must not be certifiable by another constraint's columns."""
    for relation, conname, columns in bridge.CATALOG_CONSTRAINTS:
        state = _canonical_constraint_state(columns=columns)
        assert bridge._constraint_problem(
            state, expected_columns=columns
        ) is None, conname
        for other_relation, other_name, other_columns in bridge.CATALOG_CONSTRAINTS:
            if other_columns == columns:
                continue
            assert bridge._constraint_problem(
                state, expected_columns=other_columns
            ) == "columns", (conname, other_name)


def test_constraint_manifest_entries_are_unique_and_bounded():
    names = [conname for _r, conname, _c in bridge.CATALOG_CONSTRAINTS]
    assert len(names) == len(set(names)) == 6
    relations = {relation for relation, _n, _c in bridge.CATALOG_CONSTRAINTS}
    # One request-id constraint per request-bearing ledger.
    assert len(relations) == 6
    assert relations <= set(bridge.CATALOG_MAIN_RELATIONS)


# ─────────── direct script execution (repo-root entrypoint, no PYTHONPATH) ───────────
#
# REVIEW FINDING 1: the CLI imports `config` at module scope, but a direct
# invocation puts `mas/tools` — not `mas` — at sys.path[0], so the tool died with
# ModuleNotFoundError before argparse ever ran. Importing
# `tools.research_evidence_bridge` in-process does NOT reproduce that: this
# module already inserted the `mas` root into sys.path above. Only spawning the
# real script entrypoint from the repository root, with PYTHONPATH removed from
# the environment, is discriminating.

REPO_ROOT = ROOT.parent
SCRIPT_RELATIVE_PATH = "mas/tools/research_evidence_bridge.py"

# An unreachable unix-socket directory, so `preflight` resolves its connection
# attempt locally and immediately without touching any real database.
_UNREACHABLE_DSN = (
    "postgresql://bridge:bridge@/bridge"
    "?host=/nonexistent-research-evidence-bridge-probe"
)


def _run_script_directly(*argv, extra_env=None):
    """Invoke the actual script from the repo root with NO PYTHONPATH help."""
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, SCRIPT_RELATIVE_PATH, *argv],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_direct_script_help_succeeds_from_repo_root_without_pythonpath():
    result = _run_script_directly("--help")
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert result.returncode == 0, (result.returncode, result.stderr)
    # argparse initialized and produced the real usage banner.
    assert "usage: research_evidence_bridge" in result.stdout
    assert "trace-inspect" in result.stdout
    assert result.stderr == ""


def test_direct_script_preflight_reaches_bridge_execution():
    """A normal `preflight` must reach the command, not die during imports.

    Runtime conditions may legitimately be unavailable/disabled — the assertion
    is that Python imports and argparse completed and the bridge's own JSON
    contract was produced.
    """
    result = _run_script_directly(
        "preflight",
        extra_env={
            "DATABASE_URL": _UNREACHABLE_DSN,
            "MAS_RESEARCH_EVIDENCE_ENABLED": "false",
            "MAS_EVIDENCE_SNAPSHOT_ENABLED": "false",
        },
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, (result.returncode, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["command"] == "preflight"
    assert payload["committed"] is False
    # Reached the command body: it evaluated the feature gates it was given.
    assert payload["research_evidence_enabled"] is False
    # Unreachable database is a normal degraded runtime condition, not a crash.
    assert payload["writes_allowed"] is False


def test_direct_script_unknown_command_is_an_argparse_usage_error():
    """Discriminates argparse initialization from an import failure."""
    result = _run_script_directly("definitely-not-a-command")
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert result.returncode == bridge.EXIT_USAGE
    assert "usage: research_evidence_bridge" in result.stderr


# ═══════════════ P2-B: socket-safe runtime cluster fingerprint ═══════════════
#
# Over a Unix-domain socket PostgreSQL returns NULL for inet_server_addr() and
# inet_server_port(), which the identity COALESCEs to empty strings. On the
# socket-backed topology this project runs on, database/user/schema alone then
# fail to distinguish two local clusters. `_runtime_identity` therefore also
# folds in a stable, non-secret cluster discriminator — the configured `port`
# GUC and `pg_control_system().system_identifier` — so the fingerprint stays
# distinct. These unit tests exercise the pure function against a fake connection
# returning the exact 7-column identity row `_runtime_identity` selects.

# The 7 columns of the identity SELECT, in order.
_IDENTITY_COLUMNS = (
    "current_database", "inet_server_addr", "inet_server_port",
    "configured_port", "system_identifier", "current_user", "current_schema",
)
_SOCKET_IDENTITY = {
    "current_database": "disposable",
    "inet_server_addr": "",          # NULL over a Unix socket
    "inet_server_port": "",          # NULL over a Unix socket
    "configured_port": "5432",
    "system_identifier": "7300000000000000001",
    "current_user": "postgres",
    "current_schema": "research_evidence_schema",
}


def _identity_conn(**overrides):
    """A FakeConn whose identity query returns a 7-column row (with overrides).

    ``system_identifier=None`` simulates a cluster/role that cannot supply the
    control-file identity (e.g. pg_control_system() is not executable).
    """
    ident = dict(_SOCKET_IDENTITY)
    ident.update(overrides)
    row = tuple(ident[col] for col in _IDENTITY_COLUMNS)

    def responder(q, p):
        if "pg_control_system" in q and "inet_server" in q:
            return [row]
        raise AssertionError(f"unexpected query in identity unit test: {q!r}")

    return FakeConn(responder)


def test_runtime_identity_exposes_socket_safe_cluster_discriminators():
    ident = bridge._runtime_identity(_identity_conn())
    # Socket addr/port are empty, yet the cluster is still identified.
    assert ident["inet_server_addr"] == ""
    assert ident["inet_server_port"] == ""
    assert ident["configured_port"] == "5432"
    assert ident["system_identifier"] == "7300000000000000001"
    assert set(ident) == set(_IDENTITY_COLUMNS)


def test_fingerprint_changes_when_only_system_identifier_changes():
    base = bridge._runtime_fingerprint(_identity_conn())
    other = bridge._runtime_fingerprint(
        _identity_conn(system_identifier="7300000000000000002")
    )
    assert base != other


def test_configured_port_participates_in_the_canonical_identity():
    base = bridge._runtime_fingerprint(_identity_conn())
    other = bridge._runtime_fingerprint(_identity_conn(configured_port="5544"))
    assert base != other


def test_socket_empty_addr_port_does_not_erase_cluster_identity():
    # Two socket clusters: identical db/user/schema and empty inet addr/port, but
    # different system_identifier. The OLD identity dimensions collide; the fix
    # keeps the fingerprints distinct.
    a = _identity_conn(system_identifier="7300000000000000001")
    b = _identity_conn(system_identifier="7300000000000000002")
    ia, ib = bridge._runtime_identity(a), bridge._runtime_identity(b)
    # Old collision surface is genuinely identical...
    for col in ("current_database", "inet_server_addr", "inet_server_port",
                "current_user", "current_schema"):
        assert ia[col] == ib[col]
    assert ia["inet_server_addr"] == "" and ia["inet_server_port"] == ""
    # ...yet the corrected fingerprints differ.
    assert bridge._runtime_fingerprint(a) != bridge._runtime_fingerprint(b)


@pytest.mark.parametrize(
    "field", ["current_database", "current_user", "current_schema"]
)
def test_existing_identity_dimensions_still_alter_the_fingerprint(field):
    base = bridge._runtime_fingerprint(_identity_conn())
    other = bridge._runtime_fingerprint(_identity_conn(**{field: "different"}))
    assert base != other


def test_runtime_identity_emits_no_dsn_or_credentials():
    # The emitted identity is bounded and non-secret: no DSN, password, host, or
    # connection option ever appears in it.
    ident = bridge._runtime_identity(_identity_conn())
    blob = json.dumps(ident)
    for secret in ("postgresql://", "password", "dbname=", "host=", "disposable@"):
        assert secret not in blob


def test_runtime_identity_fails_closed_when_system_identifier_missing():
    # If the cluster/role cannot supply system_identifier, the identity read
    # fails closed rather than silently degrading to the collision-prone
    # address/port-only fingerprint.
    with pytest.raises(bridge.BridgePreflightError):
        bridge._runtime_identity(_identity_conn(system_identifier=None))


def test_runtime_identity_fails_closed_when_control_function_raises():
    # A role that cannot execute pg_control_system() raises at the SELECT; the
    # identity read converts that into a fail-closed BridgePreflightError, never a
    # fallback fingerprint.
    def responder(q, p):
        raise RuntimeError("permission denied for function pg_control_system")

    with pytest.raises(bridge.BridgePreflightError):
        bridge._runtime_identity(FakeConn(responder))


# ═══════════ connection seam uses the checked DATABASE_URL, not import-time ═══════════
#
# `_require_configured_database_url` authorizes writes from the CURRENT
# `os.environ['DATABASE_URL']`, but `config.DATABASE_URL` is frozen at
# config-import time (localhost fallback). The connection seam must use the same
# current env value the guard checked, or a wrapper that sets DATABASE_URL after
# import could pass preflight yet connect to the stale fallback database.


def test_authoritative_connection_uses_current_env_database_url(monkeypatch):
    import psycopg

    captured = {}
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn: captured.setdefault("dsn", dsn)
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://explicit-host/live_db")
    # Even if the import-time config snapshot points elsewhere...
    monkeypatch.setattr(bridge.config, "DATABASE_URL", "postgresql://stale/fallback")
    bridge._open_authoritative_connection()
    assert captured["dsn"] == "postgresql://explicit-host/live_db"


def test_authoritative_connection_falls_back_to_config_without_env(monkeypatch):
    import psycopg

    captured = {}
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn: captured.setdefault("dsn", dsn)
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        bridge.config, "DATABASE_URL", "postgresql://config-fallback/db"
    )
    # No env configured is a read-only diagnostic case (writes require the env);
    # the config default still applies.
    bridge._open_authoritative_connection()
    assert captured["dsn"] == "postgresql://config-fallback/db"
