"""Disposable-PostgreSQL integration for the R2.0A-4B operator bridge.

Proves against a genuine database that the bridge:

* builds the full source/fact/claim/intake/review/support/annotation/
  authorization chain through the canonical services (dogfooded end to end);
* produces an A-2 pack and A-3 projection/fingerprint equal to a direct service
  call, with the frozen byte-budget classification;
* fixes ``internal_analysis`` and never widens scope;
* enforces dry-run-by-default, typed authorization/revocation confirmation,
  revocation + reauthorization, request-key idempotency and mismatch rejection,
  empty packs, and the ``report`` phase never consuming;
* holds a read-only preview posture and redacts secrets.

Skips unless TEST_EVIDENCE_PG_DSN is set.
"""
import io
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_evidence_context as rc  # noqa: E402
import tests.evidence_snapshot_pg as pg  # noqa: E402
import tools.research_evidence_bridge as bridge  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from research_evidence import UsageScope  # noqa: E402
from research_evidence.pack_service import (  # noqa: E402
    assemble_research_evidence_pack,
)
from research_evidence.presentation_projection_service import (  # noqa: E402
    project_research_evidence_presentation,
)
from state import ProjectState  # noqa: E402
from tests.test_research_evidence_pack_schema import (  # noqa: E402
    _prepare_service_authorization,
)

# The write commands that require the fail-closed preflight + a pinned runtime
# fingerprint. The bridge harness injects the fingerprint for these.
WRITE_COMMANDS = frozenset({
    "source-metadata-create", "fact-create", "claim-create", "intake-create",
    "intake-item-create", "review-record", "freshness-record",
    "claim-support-record", "annotation-record", "context-record",
    "authorize-internal-analysis", "revoke-internal-analysis",
})


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    # Writes require an explicitly configured DATABASE_URL env var. The bridge
    # connection itself is injected (open_bridge_connection), so this only needs
    # to be a non-empty explicit value.
    monkeypatch.setenv("DATABASE_URL", os.environ.get("TEST_EVIDENCE_PG_DSN", ""))


@pytest.fixture
def pack_schema():
    """The COMPLETE approved runtime topology: v51→v60 + v61.

    The bridge's write preflight now requires the full v51→v61 topology
    (including v57–v60), so the disposable fixture applies it in full. v59/v60
    live in the dedicated ``research_evidence_automation_roi`` schema, dropped on
    teardown.
    """
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_through_v60_research_topology(conn, schema)
        pg.apply_v61_research_evidence_pack(conn)
        # The migration scripts pin their own search_path; restore ours so seeds
        # and assertions on this connection hit the isolated test schema.
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        try:
            yield conn, schema
        finally:
            pg.drop_schema(conn, "research_evidence_automation_roi")
    conn.close()


@pytest.fixture
def partial_schema():
    """A partial topology (v51–v56 + v61, missing v57–v60) for negative tests."""
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        pg.apply_v61_research_evidence_pack(conn)
        yield conn, schema
    conn.close()


def _install_state_snapshots(conn):
    """Create the state_snapshots table the way store.py would, for the fixture.

    trace-inspect never creates this table (it must not); tests seed it directly
    so the bridge can read a persisted ProjectState read-only.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state_snapshots (
            project_id UUID PRIMARY KEY,
            state_json JSONB NOT NULL,
            version INT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def _persist_state(conn, state: ProjectState):
    conn.execute(
        """
        INSERT INTO state_snapshots (project_id, state_json)
        VALUES (%s::uuid, %s::jsonb)
        ON CONFLICT (project_id) DO UPDATE SET state_json = EXCLUDED.state_json
        """,
        (state.project_id, json.dumps(state.model_dump(mode="json"))),
    )


def _consumption_event(phase, *, status, fingerprint, sources, counts):
    return {
        "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
        "phase": phase,
        "details": {
            "phase": phase,
            "usage_scope": "internal_analysis",
            "status": status,
            "projection_fingerprint": fingerprint,
            "policy_identifier": "presentation-policy",
            "policy_version": "1",
            "counts": counts,
            "sources": sources,
            "blocked_reason": "",
        },
    }


# ─────────────────────────── bridge invocation harness ──────────────────────


class _RecordingConn:
    """Proxy recording commits/writes so read-only posture can be asserted."""

    def __init__(self, real, log):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_log", log)
        log["commits"] = 0
        log["writes"] = []
        log["read_only"] = None

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def autocommit(self):
        return self._real.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._real.autocommit = value

    @property
    def read_only(self):
        return self._real.read_only

    @read_only.setter
    def read_only(self, value):
        self._log["read_only"] = value
        self._real.read_only = value

    @property
    def isolation_level(self):
        return self._real.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self._real.isolation_level = value

    def execute(self, query, params=None):
        text = str(query).strip().upper()
        # DDL is recorded alongside DML: a read-only command must not create the
        # storage it reads (e.g. trace-inspect must never create state_snapshots).
        if text.startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE")
        ):
            self._log["writes"].append(str(query))
        return self._real.execute(query, params)

    def commit(self):
        self._log["commits"] += 1
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()


def _runtime_fingerprint_for(schema):
    conn = pg.connect(schema=schema, autocommit=True)
    try:
        return bridge._runtime_fingerprint(conn)
    finally:
        conn.close()


def _bridge(monkeypatch, schema, argv, *, recorder=None):
    def factory():
        real = pg.connect(schema=schema, autocommit=True)
        if recorder is not None:
            return _RecordingConn(real, recorder)
        return real

    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    argv = list(argv)
    # Write commands require the operator-supplied runtime fingerprint. Inject
    # the correct one unless a test pins its own (to prove a mismatch block).
    if argv and argv[0] in WRITE_COMMANDS and "--expect-runtime-fingerprint" not in argv:
        argv += ["--expect-runtime-fingerprint", _runtime_fingerprint_for(schema)]
    out = io.StringIO()
    code = bridge.main(argv, stream=out)
    return code, json.loads(out.getvalue())


# ─────────────────────────── base seed ──────────────────────────


def _seed_authorized_internal(conn, tag):
    claim, evidence, _, record = _prepare_service_authorization(
        conn, tag, usage_scope="internal_analysis"
    )
    conn.commit()
    return claim, evidence, record


# ═══════════════════════════ pack + projection equality ═══════════════════════════


def test_pack_preview_equals_direct_service(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "equal-pack")

    code, payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert code == 0
    direct = assemble_research_evidence_pack(
        conn, project_id=claim["project"], usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert payload["pack_status"] == "POPULATED"
    assert payload["counts"]["claim_count"] == direct.counts.claim_count == 1
    assert payload["counts"]["source_count"] == direct.counts.source_count == 1
    assert payload["counts"]["relationship_count"] == direct.counts.relationship_count == 1
    assert payload["source_identities"][0]["source_snapshot_id"] == (
        direct.sources[0].source_snapshot_id
    )


def test_projection_preview_fingerprint_equals_direct_and_within_limit(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "equal-proj")

    code, payload = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", claim["project"]]
    )
    assert code == 0
    direct = project_research_evidence_presentation(
        conn, project_id=claim["project"], usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert payload["projection_fingerprint"] == direct.projection_fingerprint
    assert payload["presentation_policy_identifier"] == direct.policy_identifier
    assert payload["presentation_policy_fingerprint"] == direct.policy_fingerprint
    assert payload["block_status"] == "WITHIN_LIMIT"
    assert payload["rendered_utf8_bytes"] > 0
    assert payload["prompt_budget_bytes"] == 65536


def test_authorization_list_counts_internal_effective(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "auth-list")
    code, payload = _bridge(
        monkeypatch, schema, ["authorization-list", "--project-id", claim["project"]]
    )
    assert code == 0
    assert payload["counts"]["internal_analysis_effective_count"] == 1


# ═══════════════════════════ read-only posture ═══════════════════════════


def test_preview_connection_is_read_only_no_commit_no_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "readonly")
    recorder = {}
    code, payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]],
        recorder=recorder,
    )
    assert code == 0
    assert recorder["commits"] == 0
    assert recorder["writes"] == []
    assert recorder["read_only"] is True


# ═══════════════════════════ dry-run vs commit ═══════════════════════════


def test_claim_create_dry_run_does_not_persist_but_commit_does(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="dry-run")
    conn.commit()

    def claim_count():
        return conn.execute(
            "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
            (project_id,),
        ).fetchone()[0]

    conn.rollback()
    assert claim_count() == 0

    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "Draft only"],
    )
    assert code == 0 and payload["dry_run"] is True and payload["committed"] is False
    conn.rollback()
    assert claim_count() == 0  # dry-run persisted nothing

    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "Committed", "--commit"],
    )
    assert code == 0 and payload["committed"] is True
    conn.rollback()
    assert claim_count() == 1


# ═══════════════════════════ full chain via bridge ═══════════════════════════


def test_full_chain_via_bridge_builds_equal_pack(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="full-chain")
    blob = ev_repo.insert_or_get_blob(
        conn, project_id=project_id,
        content_hash=f"full-chain-{uuid.uuid4().hex}", byte_size=42,
    )
    snapshot = ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id,
        storage_ref=f"/r2a4b/{uuid.uuid4().hex}",
    )
    conn.commit()

    def run(argv):
        code, payload = _bridge(monkeypatch, schema, argv + ["--commit"])
        assert code == 0, payload
        assert payload["committed"] is True
        return payload

    meta = run([
        "source-metadata-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot, "--actor", "op",
        "--citation-label", "Bridge Source 2026",
        "--canonical-source-locator", "https://example.org/doc",
    ])
    source_metadata_id = meta["source_metadata_revision_id"]

    fact = run([
        "fact-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot, "--actor", "op",
        "--fact-type", "count", "--value", "11", "--counted-entity", "records",
        "--citation-locator", "section 2",
    ])
    fact_id = fact["candidate_fact_revision_id"]
    fact_metadata_id = fact["fact_metadata_revision_id"]

    claim = run([
        "claim-create", "--project-id", project_id, "--actor", "op",
        "--claim-text", "Bridge authorized claim",
    ])
    claim_draft_id = claim["claim_draft_id"]

    ev_intake = run([
        "intake-create", "--project-id", project_id, "--actor", "op",
        "--source-snapshot-id", snapshot,
        "--source-metadata-revision-id", source_metadata_id,
        "--selection-reason", "evidence intake",
    ])
    ev_item = run([
        "intake-item-create", "--project-id", project_id, "--actor", "op",
        "--research-evidence-intake-id", ev_intake["research_evidence_intake_id"],
        "--item-kind", "candidate_fact",
        "--candidate-fact-revision-id", fact_id,
        "--fact-metadata-revision-id", fact_metadata_id,
    ])
    evidence_item_id = ev_item["research_evidence_intake_item_id"]

    cl_intake = run([
        "intake-create", "--project-id", project_id, "--actor", "op",
        "--source-snapshot-id", snapshot,
        "--source-metadata-revision-id", source_metadata_id,
        "--selection-reason", "claim intake",
    ])
    cl_item = run([
        "intake-item-create", "--project-id", project_id, "--actor", "op",
        "--research-evidence-intake-id", cl_intake["research_evidence_intake_id"],
        "--item-kind", "claim_draft", "--claim-draft-id", claim_draft_id,
    ])
    claim_item_id = cl_item["research_evidence_intake_item_id"]

    for item_id, suffix in ((claim_item_id, "claim"), (evidence_item_id, "evidence")):
        run([
            "review-record", "--project-id", project_id,
            "--research-evidence-intake-item-id", item_id,
            "--decision-type", "approved", "--decision-reason", "reviewed",
            "--actor", "op", "--request-id", f"full-review-{suffix}",
        ])

    run([
        "claim-support-record", "--project-id", project_id,
        "--claim-intake-item-id", claim_item_id,
        "--evidence-intake-item-id", evidence_item_id,
        "--request-id", "full-support",
        "--locator-resolution", "resolvable", "--locator-rationale", "ok",
        "--evidence-linkage", "linked", "--evidence-linkage-rationale", "ok",
        "--semantic-relationship", "support",
        "--semantic-relationship-rationale", "ok", "--actor", "op",
    ])

    run([
        "annotation-record", "--project-id", project_id,
        "--claim-draft-id", claim_draft_id, "--request-id", "full-annotation",
        "--epistemic-status", "reported_fact", "--confidence-label", "high",
        "--decision-relevance", "relevant", "--supports-statement", "supports",
        "--does-not-prove", "does not prove causality", "--actor", "op",
    ])

    confirm = f"{project_id} {claim_item_id} {evidence_item_id}"
    auth = run([
        "authorize-internal-analysis", "--project-id", project_id,
        "--claim-intake-item-id", claim_item_id,
        "--evidence-intake-item-id", evidence_item_id,
        "--request-id", "full-authorization", "--reason", "authorized",
        "--actor", "op", "--confirm", confirm,
    ])
    assert auth["usage_scope"] == "internal_analysis"
    assert auth["authorization_preview"]["decision_scope"] == "internal_analysis"
    assert "client_report" in auth["authorization_preview"]["authorization_does_not_extend_to"]

    # The bridge-built pack equals the direct A-2/A-3 contract.
    conn.rollback()
    direct_pack = assemble_research_evidence_pack(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert direct_pack.counts.relationship_count == 1
    _, proj_payload = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", project_id]
    )
    direct_proj = project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert proj_payload["projection_fingerprint"] == direct_proj.projection_fingerprint


# ─── REVIEW FINDING 4: fact-create rejects a canonically UNAVAILABLE snapshot ───
#
# Through the REAL operator path (not repo.snapshot_available directly): even
# when topology preflight passes, fact-create must fail closed before creating a
# fact when the source snapshot is tombstoned/redacted (on itself or its blob).
# legal_hold must NOT be treated as blocking.


def _count_where(conn, table, project_id):
    return conn.execute(
        f"SELECT count(*) FROM {table} WHERE project_id = %s", (project_id,)
    ).fetchone()[0]


def _fact_create_target(conn, tag):
    """A committed project + blob + snapshot ready for a fact-create."""
    project_id = pg.insert_project(conn, name=f"retain-{tag}")
    blob = ev_repo.insert_or_get_blob(
        conn, project_id=project_id,
        content_hash=f"retain-{tag}-{uuid.uuid4().hex}", byte_size=21,
    )
    snapshot = ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id,
        storage_ref=f"/r2a4b-retain/{uuid.uuid4().hex}",
    )
    conn.commit()
    return project_id, snapshot, blob


@pytest.mark.parametrize(
    "event_type,target",
    [("tombstone", "snapshot"), ("redact", "snapshot"),
     ("tombstone", "blob"), ("redact", "blob")],
)
def test_bridge_fact_create_rejects_retained_snapshot(
    pack_schema, monkeypatch, event_type, target
):
    conn, schema = pack_schema
    project_id, snapshot, blob = _fact_create_target(conn, f"{event_type}-{target}")
    retention_id = ev_repo.insert_retention_event(
        conn, project_id=project_id, event_type=event_type,
        source_snapshot_id=snapshot if target == "snapshot" else None,
        source_blob_id=blob if target == "blob" else None,
        reason="", created_by="op",
    )
    conn.commit()
    facts_before = _count_where(conn, "candidate_fact_revision", project_id)
    meta_before = _count_where(conn, "research_fact_metadata_revision", project_id)
    retention_before = _count_where(conn, "evidence_retention_event", project_id)

    recorder = {}
    code, payload = _bridge(
        monkeypatch, schema,
        ["fact-create", "--project-id", project_id,
         "--source-snapshot-id", snapshot, "--actor", "op",
         "--fact-type", "count", "--value", "11", "--counted-entity", "records",
         "--citation-locator", "section 2", "--commit"],
        recorder=recorder,
    )

    # Fails closed: non-zero exit, the exact service error, nothing committed.
    assert code == bridge.EXIT_FAILURE
    assert payload["status"] == "error"
    assert payload["committed"] is False
    assert payload["error_type"] == "CandidateFactSourceSnapshotUnavailable"
    assert recorder["commits"] == 0

    # No partial fact / metadata survives; retention state is untouched.
    conn.rollback()
    assert _count_where(conn, "candidate_fact_revision", project_id) == facts_before == 0
    assert _count_where(conn, "research_fact_metadata_revision", project_id) == meta_before == 0
    assert _count_where(conn, "evidence_retention_event", project_id) == retention_before == 1
    # The exact retention row is intact (unchanged event_type; not repaired).
    row = conn.execute(
        "SELECT event_type FROM evidence_retention_event WHERE id = %s::uuid",
        (retention_id,),
    ).fetchone()
    assert row is not None and row[0] == event_type


@pytest.mark.parametrize("target", ["snapshot", "blob"])
def test_bridge_fact_create_legal_hold_control_still_succeeds(
    pack_schema, monkeypatch, target
):
    # Control: legal_hold does not change availability, so the REAL fact-create
    # command still creates the fact. Guards against blocking on any retention.
    conn, schema = pack_schema
    project_id, snapshot, blob = _fact_create_target(conn, f"hold-{target}")
    ev_repo.insert_retention_event(
        conn, project_id=project_id, event_type="legal_hold",
        source_snapshot_id=snapshot if target == "snapshot" else None,
        source_blob_id=blob if target == "blob" else None,
        reason="", created_by="op",
    )
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema,
        ["fact-create", "--project-id", project_id,
         "--source-snapshot-id", snapshot, "--actor", "op",
         "--fact-type", "count", "--value", "11", "--counted-entity", "records",
         "--citation-locator", "section 2", "--commit"],
    )
    assert code == 0, payload
    assert payload["committed"] is True
    assert payload["candidate_fact_revision_id"]
    conn.rollback()
    assert _count_where(conn, "candidate_fact_revision", project_id) == 1


# ═══════════════════════════ authorization confirmation ═══════════════════════════


def test_authorize_wrong_confirmation_does_not_commit(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # A fresh eligible pair authorized for client_report only (internal not yet).
    from tests.test_research_evidence_pack_schema import _prepare_service_authorization
    claim, evidence, _, _ = _prepare_service_authorization(
        conn, "confirm-bad", usage_scope="client_report"
    )
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema,
        ["authorize-internal-analysis", "--project-id", claim["project"],
         "--claim-intake-item-id", claim["item"],
         "--evidence-intake-item-id", evidence["item"],
         "--request-id", "confirm-bad-auth", "--reason", "x", "--actor", "op",
         "--confirm", "totally wrong", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["status"] == "error"
    # nothing authorized for internal_analysis
    conn.rollback()
    got = conn.execute(
        """SELECT count(*) FROM research_evidence_usage_authorization_decision
           WHERE project_id = %s AND usage_scope = 'internal_analysis'""",
        (claim["project"],),
    ).fetchone()[0]
    assert got == 0


def test_authorize_correct_confirmation_commits(pack_schema, monkeypatch):
    conn, schema = pack_schema
    from tests.test_research_evidence_pack_schema import _prepare_service_authorization
    claim, evidence, _, _ = _prepare_service_authorization(
        conn, "confirm-ok", usage_scope="client_report"
    )
    conn.commit()

    confirm = f"{claim['project']} {claim['item']} {evidence['item']}"
    code, payload = _bridge(
        monkeypatch, schema,
        ["authorize-internal-analysis", "--project-id", claim["project"],
         "--claim-intake-item-id", claim["item"],
         "--evidence-intake-item-id", evidence["item"],
         "--request-id", "confirm-ok-auth", "--reason", "ok", "--actor", "op",
         "--confirm", confirm, "--commit"],
    )
    assert code == 0 and payload["committed"] is True
    assert payload["confirmation_ok"] is True
    conn.rollback()
    code2, pack_payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert pack_payload["pack_status"] == "POPULATED"


# ═══════════════════════════ revocation + reauthorization ═══════════════════════════


def test_revocation_empties_pack_then_reauthorization_restores(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "revoke-cycle")
    confirm = f"{claim['project']} {claim['item']} {evidence['item']}"

    code, payload = _bridge(
        monkeypatch, schema,
        ["revoke-internal-analysis", "--project-id", claim["project"],
         "--claim-intake-item-id", claim["item"],
         "--evidence-intake-item-id", evidence["item"],
         "--request-id", "revoke-1", "--reason", "no longer", "--actor", "op",
         "--confirm", confirm, "--commit"],
    )
    assert code == 0 and payload["committed"] is True

    _, pack_payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert pack_payload["pack_status"] == "EMPTY"

    code, payload = _bridge(
        monkeypatch, schema,
        ["authorize-internal-analysis", "--project-id", claim["project"],
         "--claim-intake-item-id", claim["item"],
         "--evidence-intake-item-id", evidence["item"],
         "--request-id", "reauth-1", "--reason", "again", "--actor", "op",
         "--confirm", confirm, "--commit"],
    )
    assert code == 0 and payload["committed"] is True
    _, pack_payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert pack_payload["pack_status"] == "POPULATED"


# ═══════════════════════════ idempotency + mismatch ═══════════════════════════


def test_request_key_idempotent_retry_and_mismatch_rejection(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "idem")
    item = claim["item"]

    code, first = _bridge(
        monkeypatch, schema,
        ["review-record", "--project-id", claim["project"],
         "--research-evidence-intake-item-id", item,
         "--decision-type", "needs_revision", "--decision-reason", "first",
         "--actor", "op", "--request-id", "idem-req", "--commit"],
    )
    assert code == 0 and first["committed"] is True

    # identical retry returns the same record id
    code, retry = _bridge(
        monkeypatch, schema,
        ["review-record", "--project-id", claim["project"],
         "--research-evidence-intake-item-id", item,
         "--decision-type", "needs_revision", "--decision-reason", "first",
         "--actor", "op", "--request-id", "idem-req", "--commit"],
    )
    assert code == 0
    assert retry["review_decision_id"] == first["review_decision_id"]

    # same request id, different content is rejected (no COMMIT)
    code, mismatch = _bridge(
        monkeypatch, schema,
        ["review-record", "--project-id", claim["project"],
         "--research-evidence-intake-item-id", item,
         "--decision-type", "approved", "--decision-reason", "different",
         "--actor", "op", "--request-id", "idem-req", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert mismatch["status"] == "error"


# ═══════════════════════════ empty pack ═══════════════════════════


def test_empty_project_previews_empty(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="empty")
    conn.commit()

    _, pack_payload = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", project_id]
    )
    assert pack_payload["pack_status"] == "EMPTY"
    assert pack_payload["counts"]["relationship_count"] == 0

    _, proj_payload = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", project_id]
    )
    assert proj_payload["block_status"] == "EMPTY"
    assert proj_payload["rendered_utf8_bytes"] == 0


# ═══════════════════════════ trace-inspect reads persisted state ═══════════════════════════
#
# trace-inspect reports the consumption A-4A ACTUALLY persisted in the
# ProjectState — never a fresh simulation, and never inferred from the current
# A-3 projection. report never consumes.


def test_trace_inspect_authorized_evidence_no_event_is_not_recorded(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-none")
    project_id = claim["project"]
    # A populated current pack exists…
    current = project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert current.relationships
    # …but no phase ever ran: persist a ProjectState with an empty audit log.
    _install_state_snapshots(conn)
    _persist_state(
        conn, ProjectState(project_id=project_id, project_type="strategic_audit")
    )
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id]
    )
    assert code == 0
    assert payload["state_present"] is True and payload["state_valid"] is True
    for phase in ("audit", "strategy"):
        assert payload["phases"][phase]["status"] == "not_recorded"
        assert payload["phases"][phase]["consumed"] is False
        assert payload["phases"][phase]["projection_fingerprint"] == ""
    assert payload["phases"]["report"]["status"] == "not_applicable"
    assert payload["report_phase_consumes"] is False


def test_trace_inspect_no_persisted_state_is_not_recorded(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-nostate")
    _install_state_snapshots(conn)  # table exists but holds no row for this project
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", claim["project"]]
    )
    assert code == 0
    assert payload["state_present"] is False
    assert payload["phases"]["audit"]["status"] == "not_recorded"
    assert payload["phases"]["strategy"]["status"] == "not_recorded"


def test_trace_inspect_audit_only_event(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-audit")
    project_id = claim["project"]
    counts = {"source_count": 2, "claim_count": 1, "evidence_count": 3,
              "relationship_count": 1}
    sources = [{"source_snapshot_id": "snap-a", "citation_label": "Src A"}]
    state = ProjectState(
        project_id=project_id, project_type="strategic_audit",
        policy_audit_log=[
            _consumption_event(
                "audit", status="used", fingerprint="fp-audit-123",
                sources=sources, counts=counts,
            )
        ],
    )
    _install_state_snapshots(conn)
    _persist_state(conn, state)
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id]
    )
    assert code == 0
    audit = payload["phases"]["audit"]
    assert audit["status"] == "used" and audit["consumed"] is True
    assert audit["projection_fingerprint"] == "fp-audit-123"
    assert audit["counts"] == counts
    assert audit["sources"] == sources
    # MAJOR 3: the block byte size is not available from the impact summary, so
    # it is omitted entirely (never fabricated as 0) — even for a USED event.
    assert "rendered_utf8_bytes" not in audit
    for phase in ("audit", "strategy", "report"):
        assert "rendered_utf8_bytes" not in payload["phases"][phase]
    # strategy never ran → not_recorded (not inferred from audit or the pack)
    assert payload["phases"]["strategy"]["status"] == "not_recorded"
    assert payload["phases"]["report"]["status"] == "not_applicable"


def test_trace_inspect_audit_and_strategy_events_exact(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-both")
    project_id = claim["project"]
    audit_counts = {"source_count": 1, "claim_count": 1, "evidence_count": 1,
                    "relationship_count": 1}
    strat_counts = {"source_count": 2, "claim_count": 2, "evidence_count": 2,
                    "relationship_count": 2}
    state = ProjectState(
        project_id=project_id, project_type="strategic_audit",
        policy_audit_log=[
            _consumption_event(
                "audit", status="used", fingerprint="fp-A",
                sources=[{"source_snapshot_id": "s1", "citation_label": "L1"}],
                counts=audit_counts,
            ),
            _consumption_event(
                "strategy", status="empty", fingerprint="fp-S",
                sources=[], counts=strat_counts,
            ),
        ],
    )
    _install_state_snapshots(conn)
    _persist_state(conn, state)
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id]
    )
    assert code == 0
    assert payload["phases"]["audit"]["projection_fingerprint"] == "fp-A"
    assert payload["phases"]["audit"]["counts"] == audit_counts
    assert payload["phases"]["strategy"]["status"] == "empty"
    assert payload["phases"]["strategy"]["projection_fingerprint"] == "fp-S"
    assert payload["phases"]["strategy"]["consumed"] is False


def test_trace_inspect_returns_historical_not_current_projection(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-hist")
    project_id = claim["project"]
    # The CURRENT projection has some fingerprint…
    current = project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert current.projection_fingerprint
    # …but the persisted historical event pinned a DIFFERENT one.
    historical_fp = "historical-fingerprint-that-differs"
    assert historical_fp != current.projection_fingerprint
    state = ProjectState(
        project_id=project_id, project_type="strategic_audit",
        policy_audit_log=[
            _consumption_event(
                "audit", status="used", fingerprint=historical_fp,
                sources=[], counts={"source_count": 0, "claim_count": 0,
                                    "evidence_count": 0, "relationship_count": 0},
            )
        ],
    )
    _install_state_snapshots(conn)
    _persist_state(conn, state)
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id]
    )
    assert code == 0
    # trace returns the HISTORICAL event, not the current projection fingerprint
    assert payload["phases"]["audit"]["projection_fingerprint"] == historical_fp
    assert (
        payload["phases"]["audit"]["projection_fingerprint"]
        != current.projection_fingerprint
    )


def test_trace_inspect_never_writes(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-ro")
    _install_state_snapshots(conn)
    _persist_state(
        conn, ProjectState(project_id=claim["project"], project_type="strategic_audit")
    )
    conn.commit()
    recorder = {}
    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", claim["project"]],
        recorder=recorder,
    )
    assert code == 0
    assert recorder["commits"] == 0
    assert recorder["writes"] == []
    assert recorder["read_only"] is True


def test_trace_inspect_invalid_persisted_state(pack_schema, monkeypatch):
    # MINOR 1: a malformed persisted ProjectState is reported as invalid_state,
    # not not_recorded, and never falls back to the current projection.
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-invalid")
    project_id = claim["project"]
    _install_state_snapshots(conn)
    # A JSON object that ProjectState cannot validate (project_type is a bad type
    # and phase_status is malformed).
    conn.execute(
        """
        INSERT INTO state_snapshots (project_id, state_json)
        VALUES (%s::uuid, %s::jsonb)
        """,
        (project_id, '{"project_id": "' + project_id + '", "phase_status": 42}'),
    )
    conn.commit()

    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id]
    )
    assert code == 0
    assert payload["state_present"] is True
    assert payload["state_valid"] is False
    for phase in ("audit", "strategy"):
        assert payload["phases"][phase]["status"] == "invalid_state"
        assert payload["phases"][phase]["consumed"] is False
    assert payload["phases"]["report"]["status"] == "not_applicable"
    assert payload.get("warnings")  # bounded, non-secret warning present


# ─────────── REVIEW FINDING 2: state_snapshots may not exist at all ───────────
#
# trace-inspect deliberately avoids store.load (which would create storage / use
# a write-capable pool), so the persistence relation may never have been
# initialized. Absence of the table is absence of a persisted attestation — a
# normal, safe report, not an UndefinedTable command error. It is detected by a
# read-only existence probe, so the read-only transaction is never aborted and
# no database error is reinterpreted as absence.


def _relation_exists(conn, name):
    return conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (name,)
    ).fetchone()[0]


def test_trace_inspect_absent_state_snapshots_table_is_not_recorded(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-notable")
    project_id = claim["project"]
    # State persistence has never initialized: the relation genuinely does not
    # exist in this schema (and a populated current pack exists, so an unsafe
    # implementation could be tempted to infer consumption from it).
    conn.commit()
    assert _relation_exists(conn, "state_snapshots") is False

    recorder = {}
    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id],
        recorder=recorder,
    )

    # Exits successfully with the safe absence report.
    assert code == 0
    assert payload["state_present"] is False
    assert payload["state_valid"] is None
    for phase in ("audit", "strategy"):
        assert payload["phases"][phase]["status"] == "not_recorded"
        assert payload["phases"][phase]["consumed"] is False
        assert payload["phases"][phase]["projection_fingerprint"] == ""
    assert payload["phases"]["report"]["status"] == "not_applicable"
    assert payload["phases"]["report"]["consumed"] is False
    assert payload["report_phase_consumes"] is False
    # A bounded, non-secret warning names the uninitialized persistence.
    assert any("state_snapshots" in w for w in payload.get("warnings", []))

    # No table was created, and the connection stayed read-only with no
    # writes/DDL and no commits.
    assert _relation_exists(conn, "state_snapshots") is False
    assert recorder["read_only"] is True
    assert recorder["commits"] == 0
    assert recorder["writes"] == []


def test_trace_inspect_absent_table_keeps_readonly_connection_usable(
    pack_schema, monkeypatch
):
    """The probe must not abort the transaction the way UndefinedTable would.

    A caught UndefinedTable would leave the read-only transaction in a failed
    state, so any subsequent statement on that connection raises. Proving the
    command still runs to completion twice on the same schema — and that the
    bridge connection is closed cleanly rather than aborted — discriminates the
    existence probe from exception recovery.
    """
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-notable-valid")
    conn.commit()
    assert _relation_exists(conn, "state_snapshots") is False

    argv = ["trace-inspect", "--project-id", claim["project"]]
    first_code, first_payload = _bridge(monkeypatch, schema, argv)
    second_code, second_payload = _bridge(monkeypatch, schema, argv)
    assert first_code == second_code == 0
    assert first_payload == second_payload

    # Now initialize persistence: the SAME command switches to the table-present
    # behavior with no code path change, confirming the probe reads live state.
    _install_state_snapshots(conn)
    conn.commit()
    third_code, third_payload = _bridge(monkeypatch, schema, argv)
    assert third_code == 0
    assert third_payload["state_present"] is False
    assert third_payload["state_valid"] is None
    assert third_payload["phases"]["audit"]["status"] == "not_recorded"
    # …and the uninitialized-persistence warning is gone.
    assert not any(
        "state_snapshots" in w for w in third_payload.get("warnings", [])
    )


# ─── REVIEW FINDING 3: model-valid ProjectState, malformed RE attestation ───
#
# ProjectState does NOT type its nested policy events (policy_audit_log is a bare
# list[dict]), so a model-valid state can still carry a
# research_evidence_consumption attestation the canonical impact builder cannot
# reconstruct (e.g. a non-numeric count → ValueError, or a malformed counts
# shape → AttributeError). trace-inspect must treat that as corrupt persisted
# history — state_valid=false / invalid_state — not let the exception escape as a
# generic command error, and never fall back to the current projection.


def _malformed_attestation_state_json(project_id, counts):
    """A ProjectState-compatible state_json whose nested RE attestation is bad."""
    return {
        "project_id": project_id,
        "project_type": "strategic_audit",
        "policy_audit_log": [
            {
                "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
                "phase": "audit",
                "details": {
                    "phase": "audit",
                    "status": "used",
                    "usage_scope": "internal_analysis",
                    "projection_fingerprint": "fp-should-not-be-trusted",
                    "counts": counts,
                    "sources": [],
                    "blocked_reason": "",
                },
            }
        ],
    }


def _assert_malformed_attestation_invalid_state(
    conn, schema, monkeypatch, project_id, counts, malformed_marker
):
    # (1) The state itself is model-valid — the whole point of the finding.
    raw = _malformed_attestation_state_json(project_id, counts)
    assert ProjectState.model_validate(raw) is not None

    # A populated CURRENT pack exists, so a bad implementation could be tempted
    # to fall back to it; the historical fingerprint above must never surface.
    current = project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=UsageScope.INTERNAL_ANALYSIS
    )
    assert current.projection_fingerprint

    _install_state_snapshots(conn)
    conn.execute(
        """
        INSERT INTO state_snapshots (project_id, state_json)
        VALUES (%s::uuid, %s::jsonb)
        """,
        (project_id, json.dumps(raw)),
    )
    conn.commit()

    recorder = {}
    code, payload = _bridge(
        monkeypatch, schema, ["trace-inspect", "--project-id", project_id],
        recorder=recorder,
    )

    # (2) The command SUCCEEDED at inspecting and reporting corrupt history.
    assert code == 0
    assert payload["state_present"] is True
    assert payload["state_valid"] is False
    for phase in ("audit", "strategy"):
        assert payload["phases"][phase]["status"] == "invalid_state"
        assert payload["phases"][phase]["consumed"] is False
        # No trusted attestation content leaks from the corrupt event.
        assert payload["phases"][phase]["projection_fingerprint"] == ""
    assert payload["phases"]["report"]["status"] == "not_applicable"
    assert payload["phases"]["report"]["consumed"] is False
    assert payload["report_phase_consumes"] is False

    # (3) No fall-back to the current projection.
    serialized = json.dumps(payload)
    assert current.projection_fingerprint not in serialized
    assert "fp-should-not-be-trusted" not in serialized

    # (4) A bounded, non-secret warning naming only the failure condition — never
    #     the malformed value itself.
    warnings = payload.get("warnings", [])
    assert warnings
    assert any("reconstructed" in w for w in warnings)
    assert not any(malformed_marker in w for w in warnings)

    # (5) Strictly read-only: no writes/DDL, no commits, connection read-only.
    assert recorder["read_only"] is True
    assert recorder["commits"] == 0
    assert recorder["writes"] == []


def test_trace_inspect_model_valid_state_non_numeric_count_is_invalid_state(
    pack_schema, monkeypatch
):
    # A — non-numeric count: build raises ValueError inside the impact builder.
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-badcount")
    _assert_malformed_attestation_invalid_state(
        conn, schema, monkeypatch, claim["project"],
        counts={
            "source_count": "not-a-number",
            "claim_count": 1,
            "evidence_count": 1,
            "relationship_count": 1,
        },
        malformed_marker="not-a-number",
    )


def test_trace_inspect_model_valid_state_malformed_counts_shape_is_invalid_state(
    pack_schema, monkeypatch
):
    # B — malformed details shape: counts is a list, not a mapping, so the
    # canonical reconstruction raises AttributeError. A DIFFERENT model-valid
    # corruption than A, proving the boundary is not keyed to one exception.
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "trace-badshape")
    _assert_malformed_attestation_invalid_state(
        conn, schema, monkeypatch, claim["project"],
        counts=["source_count", "claim_count"],
        malformed_marker="source_count",
    )


# ═══════════════════════════ drift invalidation via the bridge ═══════════════════════════


def test_annotation_drift_via_bridge_empties_pack(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "drift")
    _, before = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert before["pack_status"] == "POPULATED"

    # A new claim annotation supersedes the basis the authorization pinned.
    code, payload = _bridge(
        monkeypatch, schema,
        ["annotation-record", "--project-id", claim["project"],
         "--claim-draft-id", claim["claim"], "--request-id", "drift-supersede",
         "--epistemic-status", "estimate", "--confidence-label", "low",
         "--decision-relevance", "still relevant", "--supports-statement", "supports",
         "--does-not-prove", "no causality", "--actor", "op", "--commit"],
    )
    assert code == 0 and payload["committed"] is True

    _, after = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert after["pack_status"] == "EMPTY"


# ═══════════════════════════ project context present + absent ═══════════════════════════


def test_project_context_present_and_absent_both_assemble(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "context")
    # absent context: the pack still assembles (context optional).
    _, absent = _bridge(
        monkeypatch, schema, ["pack-preview", "--project-id", claim["project"]]
    )
    assert absent["pack_status"] == "POPULATED"

    code, payload = _bridge(
        monkeypatch, schema,
        ["context-record", "--project-id", claim["project"],
         "--request-id", "ctx-1", "--research-question", "What to decide?",
         "--project-limitations", "limited data", "--actor", "op", "--commit"],
    )
    assert code == 0 and payload["committed"] is True

    _, present = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", claim["project"]]
    )
    assert present["block_status"] == "WITHIN_LIMIT"


# ═══════════════════════════ prompt overflow against a real projection ═══════════════════════════


def test_projection_preview_reports_prompt_overflow_on_shrunk_budget(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "overflow")
    import research_evidence_context as rc

    monkeypatch.setattr(rc, "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES", 100)
    _, payload = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", claim["project"]]
    )
    assert payload["block_status"] == "WOULD_BLOCK_PROMPT_OVERFLOW"
    assert payload["rendered_utf8_bytes"] is None


# ═══════════════════════════ preflight against real DB ═══════════════════════════


def test_preflight_reports_ready_topology(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="preflight")
    conn.commit()

    def factory():
        return pg.connect(schema=schema, autocommit=True)

    monkeypatch.setattr(bridge, "open_bridge_connection", factory)
    out = io.StringIO()
    code = bridge.main(["preflight", "--project-id", project_id], stream=out)
    text = out.getvalue()
    payload = json.loads(text)
    assert code == 0
    assert payload["connection_available"] is True
    # every catalog category is satisfied by the real full topology
    for flag in ("relations_ready", "functions_ready", "triggers_ready",
                 "constraints_ready", "roles_ready", "topology_security_ready",
                 "namespace_ready"):
        assert payload[flag] is True, flag
    assert payload["database_url_configured"] is True
    assert payload["research_writes_allowed"] is True
    assert payload["fact_writes_allowed"] is True
    assert payload["requested_target_ready"] is True
    assert payload["writes_allowed"] is True
    assert payload["project_present"] is True
    # the certified write schema is reported and matches the test schema
    assert payload["current_schema"] == schema
    # bounded, non-secret runtime fingerprint reported; identity is present
    assert len(payload["runtime_fingerprint"]) == 64
    assert payload["runtime_identity"]["current_database"]
    assert payload["runtime_identity"]["current_user"]
    assert payload["runtime_identity"]["current_schema"] == schema
    # never leaks a DSN
    assert "postgresql://" not in text
    assert "@" not in text.split('"runtime_identity"')[0]  # no DSN userinfo


# ═══════════════════════════ MAJOR 4: evidence source identity ═══════════════════════════


def test_authorization_preview_uses_evidence_source_label(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # seed_pair gives the claim and evidence DISTINCT snapshots + distinct source
    # metadata revisions with distinct citation labels. Authorize client_report
    # only so the bridge's internal_analysis authorization is a fresh decision.
    claim, evidence, _, _ = _prepare_service_authorization(
        conn, "srcid", usage_scope="client_report"
    )
    conn.commit()
    confirm = f"{claim['project']} {claim['item']} {evidence['item']}"

    code, payload = _bridge(
        monkeypatch, schema,
        ["authorize-internal-analysis", "--project-id", claim["project"],
         "--claim-intake-item-id", claim["item"],
         "--evidence-intake-item-id", evidence["item"],
         "--request-id", "srcid-auth", "--reason", "ok", "--actor", "op",
         "--confirm", confirm],
    )
    assert code == 0
    preview = payload["authorization_preview"]
    # The evidence field carries the EVIDENCE snapshot's label…
    assert preview["evidence_source_citation_label"] == "source-srcid-evidence"
    # …the claim label is exposed separately and is genuinely different.
    assert preview["claim_source_citation_label"] == "source-srcid-claim"
    assert (
        preview["evidence_source_citation_label"]
        != preview["claim_source_citation_label"]
    )
    # No ambiguous collapsed label field.
    assert "source_citation_label" not in preview
    # Authorization stays bound to the correct candidate fact.
    assert preview["candidate_fact_revision_id"] == evidence["fact"]


# ═══════════════════════════ MAJOR 2: write preflight enforcement ═══════════════════════════


def test_missing_migration_blocks_actual_write_command(partial_schema, monkeypatch):
    conn, schema = partial_schema
    # A partial topology can still assemble an authorized pair (v51–v56 + v61)…
    claim, evidence, _ = _seed_authorized_internal(conn, "partial")

    def count_annotations():
        return conn.execute(
            "SELECT count(*) FROM research_evidence_claim_annotation_revision "
            "WHERE project_id = %s AND request_id = %s",
            (claim["project"], "blocked-annotation"),
        ).fetchone()[0]

    conn.rollback()
    before = count_annotations()

    # …but the actual write command is blocked by the missing v57–v60 topology.
    code, payload = _bridge(
        monkeypatch, schema,
        ["annotation-record", "--project-id", claim["project"],
         "--claim-draft-id", claim["claim"], "--request-id", "blocked-annotation",
         "--epistemic-status", "reported_fact", "--confidence-label", "high",
         "--decision-relevance", "x", "--supports-statement", "x",
         "--does-not-prove", "x", "--actor", "op", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["error_type"] == "BridgePreflightError"
    conn.rollback()
    assert count_annotations() == before  # nothing written


def test_missing_role_attributes_block_actual_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "role-block")
    # Demand a role attribute the real migration owner does not have.
    monkeypatch.setitem(
        bridge.REQUIRED_ROLE_ATTRIBUTES, "workflow_migration_owner",
        {**bridge.REQUIRED_ROLE_ATTRIBUTES["workflow_migration_owner"],
         "login": False},
    )
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", claim["project"], "--actor", "op",
         "--claim-text", "blocked by role", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["error_type"] == "BridgePreflightError"


def test_wrong_runtime_fingerprint_blocks_before_service(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="fp-block")
    conn.commit()

    def claim_count():
        return conn.execute(
            "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
            (project_id,),
        ).fetchone()[0]

    conn.rollback()
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "x", "--commit",
         "--expect-runtime-fingerprint", "0" * 64],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["error_type"] == "BridgePreflightError"
    conn.rollback()
    assert claim_count() == 0


@pytest.mark.parametrize(
    "isolation",
    ["REPEATABLE_READ", "SERIALIZABLE"],
)
def test_non_read_committed_isolation_blocks_write(pack_schema, monkeypatch, isolation):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name=f"iso-{isolation}")
    conn.commit()
    import psycopg

    real_configure = bridge._configure_write_connection

    def configure_wrong_isolation(c):
        real_configure(c)
        c.isolation_level = getattr(psycopg.IsolationLevel, isolation)
        return c

    monkeypatch.setattr(bridge, "_configure_write_connection", configure_wrong_isolation)
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "x", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["error_type"] == "BridgePreflightError"
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0] == 0


def test_failed_isolation_configuration_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="iso-fail")
    conn.commit()

    def configure_raises(c):
        raise RuntimeError("cannot pin isolation")

    monkeypatch.setattr(bridge, "_configure_write_connection", configure_raises)
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "x", "--commit"],
    )
    assert code == bridge.EXIT_FAILURE
    assert payload["status"] == "error"
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0] == 0


def test_correct_fingerprint_and_full_topology_permit_dry_run_and_commit(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    project_id = pg.insert_project(conn, name="permit")
    conn.commit()

    def claim_count():
        return conn.execute(
            "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
            (project_id,),
        ).fetchone()[0]

    conn.rollback()
    # dry-run permitted, persists nothing
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "dry"],
    )
    assert code == 0 and payload["dry_run"] is True
    conn.rollback()
    assert claim_count() == 0
    # commit permitted, persists
    code, payload = _bridge(
        monkeypatch, schema,
        ["claim-create", "--project-id", project_id, "--actor", "op",
         "--claim-text", "committed", "--commit"],
    )
    assert code == 0 and payload["committed"] is True
    conn.rollback()
    assert claim_count() == 1


# ═══════════════════════════ MAJOR 2: catalog-drift negative probes ═══════════════════════════
#
# Every probe below preserves all table NAMES but drifts a catalog fact the
# schema mega-suites would catch, and proves the ACTUAL write command fails
# before its service is called. Each uses a fresh full-topology fixture so
# object-level drift is torn down with the schema; role-membership drift is
# reverted explicitly.


def _assert_claim_write_blocked(monkeypatch, schema, conn, *, factory=None):
    project_id = pg.insert_project(conn, name="drift-target")
    conn.commit()

    def run():
        if factory is not None:
            monkeypatch.setattr(bridge, "open_bridge_connection", factory)
            out = io.StringIO()
            code = bridge.main(
                ["claim-create", "--project-id", project_id, "--actor", "op",
                 "--claim-text", "blocked", "--commit",
                 "--expect-runtime-fingerprint", "unused-namespace-blocks-first"],
                stream=out,
            )
            return code, json.loads(out.getvalue())
        return _bridge(
            monkeypatch, schema,
            ["claim-create", "--project-id", project_id, "--actor", "op",
             "--claim-text", "blocked", "--commit"],
        )

    code, payload = run()
    assert code == bridge.EXIT_FAILURE, payload
    assert payload["error_type"] == "BridgePreflightError"
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0] == 0


def test_drop_append_only_trigger_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_disable_append_only_trigger_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute(
        "ALTER TABLE candidate_fact_revision DISABLE TRIGGER trg_cfr_no_mutation"
    )
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_trigger_attached_to_wrong_function_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute(
        "CREATE FUNCTION decoy_guard() RETURNS trigger AS "
        "$$ BEGIN RETURN NULL; END $$ LANGUAGE plpgsql"
    )
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.execute(
        "CREATE TRIGGER trg_cfr_no_mutation BEFORE UPDATE OR DELETE "
        "ON candidate_fact_revision FOR EACH ROW EXECUTE FUNCTION decoy_guard()"
    )
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_wrong_signature_function_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # Same name, wrong schema-local signature: drop the trigger + canonical
    # no-arg function, recreate the name with a different signature.
    conn.execute(
        "DROP TRIGGER trg_ree_prepare_insert ON research_evidence_event"
    )
    conn.execute("DROP FUNCTION research_evidence_prepare_event_insert()")
    conn.execute(
        "CREATE FUNCTION research_evidence_prepare_event_insert(x integer) "
        "RETURNS integer AS $$ BEGIN RETURN x; END $$ LANGUAGE plpgsql"
    )
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def _raw_factory(schema):
    # A raw psycopg connection that bypasses the harness's role-provisioning
    # guard (needed when the test itself has deliberately drifted a membership,
    # which that guard would otherwise reject before the bridge even runs).
    import psycopg

    def factory():
        c = psycopg.connect(os.environ["TEST_EVIDENCE_PG_DSN"], autocommit=True)
        c.execute(f'SET search_path TO "{schema}"')
        return c

    return factory


def test_drift_canonical_role_membership_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # Grant the owner role to the runtime role — a runtime escalation path.
    conn.execute(
        "GRANT workflow_research_evidence_owner "
        "TO workflow_automation_roi_runtime"
    )
    conn.commit()
    try:
        _assert_claim_write_blocked(
            monkeypatch, schema, conn, factory=_raw_factory(schema)
        )
    finally:
        conn.rollback()
        conn.execute(
            "REVOKE workflow_research_evidence_owner "
            "FROM workflow_automation_roi_runtime"
        )
        conn.commit()


def test_drift_dedicated_schema_owner_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute(
        "ALTER SCHEMA research_evidence_automation_roi "
        "OWNER TO workflow_migration_owner"
    )
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_drift_dedicated_schema_acl_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # Runtime must not hold CREATE on the dedicated schema.
    conn.execute(
        "GRANT CREATE ON SCHEMA research_evidence_automation_roi "
        "TO workflow_automation_roi_runtime"
    )
    conn.commit()
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_decoy_search_path_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute('CREATE SCHEMA decoy_evidence')
    conn.commit()

    def decoy_factory():
        c = pg.connect(autocommit=True)
        c.execute('SET search_path TO decoy_evidence')
        return c

    _assert_claim_write_blocked(monkeypatch, schema, conn, factory=decoy_factory)


# ═══════════════ ITERATION 3: catalog-exact trigger + role hardening ═══════════════
#
# Each probe both (a) proves the ACTUAL bridge write is blocked with
# BridgePreflightError before its service and (b) discriminates the exact catalog
# category/reason via a read-only preflight, so a green suite cannot pass on an
# unrelated failure.


def _preflight_payload(monkeypatch, schema, *, factory=None):
    def default_factory():
        return pg.connect(schema=schema, autocommit=True)

    monkeypatch.setattr(bridge, "open_bridge_connection", factory or default_factory)
    out = io.StringIO()
    code = bridge.main(["preflight"], stream=out)
    assert code == 0
    return json.loads(out.getvalue())


# ── MAJOR 1: same-name trigger function in a different schema ────────────────


def test_same_name_cross_schema_trigger_function_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # A decoy schema with a function of the SAME canonical name; the trigger is
    # rebound to it while the canonical current-schema function stays present.
    conn.execute("CREATE SCHEMA decoy_fn")
    conn.execute(
        "CREATE FUNCTION decoy_fn.slicea_reject_mutation() RETURNS trigger AS "
        "$$ BEGIN RETURN NULL; END $$ LANGUAGE plpgsql"
    )
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.execute(
        "CREATE TRIGGER trg_cfr_no_mutation BEFORE UPDATE OR DELETE "
        "ON candidate_fact_revision FOR EACH ROW "
        "EXECUTE FUNCTION decoy_fn.slicea_reject_mutation()"
    )
    conn.commit()

    payload = _preflight_payload(monkeypatch, schema)
    # the canonical function is still present (functions category stays ready)…
    assert payload["functions_ready"] is True
    # …but the trigger binds cross-schema, so proname-equality is NOT enough.
    assert payload["triggers_ready"] is False
    assert any(
        "trg_cfr_no_mutation:func_schema" in b for b in payload["bad_triggers"]
    ), payload["bad_triggers"]
    _assert_claim_write_blocked(monkeypatch, schema, conn)


# ── MAJOR 2: trigger semantic drift + inventory ─────────────────────────────


def test_mutation_guard_when_false_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.execute(
        "CREATE TRIGGER trg_cfr_no_mutation BEFORE UPDATE OR DELETE "
        "ON candidate_fact_revision FOR EACH ROW WHEN (false) "
        "EXECUTE FUNCTION slicea_reject_mutation()"
    )
    conn.commit()
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["triggers_ready"] is False
    assert any("trg_cfr_no_mutation:tgqual" in b for b in payload["bad_triggers"])
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_mutation_guard_update_of_column_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.execute(
        "CREATE TRIGGER trg_cfr_no_mutation BEFORE UPDATE OF fact_type OR DELETE "
        "ON candidate_fact_revision FOR EACH ROW "
        "EXECUTE FUNCTION slicea_reject_mutation()"
    )
    conn.commit()
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["triggers_ready"] is False
    assert any("trg_cfr_no_mutation:tgattr" in b for b in payload["bad_triggers"])
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_trigger_with_arguments_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # Same canonical function, but bound with a trigger argument.
    conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
    conn.execute(
        "CREATE TRIGGER trg_cfr_no_mutation BEFORE UPDATE OR DELETE "
        "ON candidate_fact_revision FOR EACH ROW "
        "EXECUTE FUNCTION slicea_reject_mutation('drift_arg')"
    )
    conn.commit()
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["triggers_ready"] is False
    assert any("trg_cfr_no_mutation:tgnargs" in b for b in payload["bad_triggers"])
    _assert_claim_write_blocked(monkeypatch, schema, conn)


def test_extra_non_internal_trigger_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute(
        "CREATE FUNCTION extra_guard() RETURNS trigger AS "
        "$$ BEGIN RETURN NULL; END $$ LANGUAGE plpgsql"
    )
    conn.execute(
        "CREATE TRIGGER trg_cfr_extra BEFORE INSERT ON candidate_fact_revision "
        "FOR EACH ROW EXECUTE FUNCTION extra_guard()"
    )
    conn.commit()
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["triggers_ready"] is False
    assert any("trg_cfr_extra:extra" in b for b in payload["bad_triggers"])
    _assert_claim_write_blocked(monkeypatch, schema, conn)


# ── MAJOR 3: transitive runtime-role escalation ─────────────────────────────


def test_runtime_indirect_owner_reachability_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("CREATE ROLE decoy_intermediate_owner NOLOGIN")
    conn.execute(
        "GRANT decoy_intermediate_owner TO workflow_automation_roi_runtime"
    )
    conn.execute(
        "GRANT workflow_research_evidence_owner TO decoy_intermediate_owner"
    )
    conn.commit()
    try:
        payload = _preflight_payload(
            monkeypatch, schema, factory=_raw_factory(schema)
        )
        assert payload["topology_security_ready"] is False
        assert any(
            "runtime_role_escalation:workflow_research_evidence_owner" in f
            for f in payload["security_findings"]
        ), payload["security_findings"]
        _assert_claim_write_blocked(
            monkeypatch, schema, conn, factory=_raw_factory(schema)
        )
    finally:
        conn.rollback()
        conn.execute(
            "REVOKE workflow_research_evidence_owner FROM decoy_intermediate_owner"
        )
        conn.execute(
            "REVOKE decoy_intermediate_owner FROM workflow_automation_roi_runtime"
        )
        conn.execute("DROP ROLE decoy_intermediate_owner")
        conn.commit()


def test_runtime_indirect_migration_owner_reachability_blocks_write(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    conn.execute("CREATE ROLE decoy_intermediate_mig NOLOGIN")
    conn.execute("GRANT decoy_intermediate_mig TO workflow_automation_roi_runtime")
    conn.execute("GRANT workflow_migration_owner TO decoy_intermediate_mig")
    conn.commit()
    try:
        payload = _preflight_payload(
            monkeypatch, schema, factory=_raw_factory(schema)
        )
        assert payload["topology_security_ready"] is False
        assert any(
            "runtime_role_escalation:workflow_migration_owner" in f
            for f in payload["security_findings"]
        ), payload["security_findings"]
        _assert_claim_write_blocked(
            monkeypatch, schema, conn, factory=_raw_factory(schema)
        )
    finally:
        conn.rollback()
        conn.execute(
            "REVOKE workflow_migration_owner FROM decoy_intermediate_mig"
        )
        conn.execute(
            "REVOKE decoy_intermediate_mig FROM workflow_automation_roi_runtime"
        )
        conn.execute("DROP ROLE decoy_intermediate_mig")
        conn.commit()


def test_canonical_membership_option_drift_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    # Drift one material option of the canonical owner→migration membership.
    conn.execute(
        "GRANT workflow_research_evidence_owner TO workflow_migration_owner "
        "WITH ADMIN FALSE, INHERIT TRUE, SET TRUE"
    )
    conn.commit()
    try:
        payload = _preflight_payload(
            monkeypatch, schema, factory=_raw_factory(schema)
        )
        assert payload["topology_security_ready"] is False
        assert "owner_membership_options" in payload["security_findings"]
        _assert_claim_write_blocked(
            monkeypatch, schema, conn, factory=_raw_factory(schema)
        )
    finally:
        conn.rollback()
        # Restore the exact ratified options (admin=F, inherit=F, set=T).
        conn.execute(
            "GRANT workflow_research_evidence_owner TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )
        conn.commit()


# ── MINOR 1: capacity-overflow byte omission (PG path) ──────────────────────


def test_projection_preview_capacity_overflow_omits_bytes_pg(pack_schema, monkeypatch):
    conn, schema = pack_schema
    claim, evidence, _ = _seed_authorized_internal(conn, "cap-pg")
    import research_evidence.presentation_projection_service as pps
    from research_evidence.pack_service import ResearchEvidencePackLimitError

    def boom(c, *, project_id, usage_scope):
        raise ResearchEvidencePackLimitError("capacity exceeded")

    monkeypatch.setattr(pps, "project_research_evidence_presentation", boom)
    _, payload = _bridge(
        monkeypatch, schema, ["projection-preview", "--project-id", claim["project"]]
    )
    assert payload["block_status"] == "WOULD_BLOCK_CAPACITY_OVERFLOW"
    assert "rendered_utf8_bytes" not in payload


# ═══════════════ ITERATION 4: function-semantics closure ═══════════════
#
# Identity exactness (schema + name + identity args + result) is NOT sufficient:
# CREATE OR REPLACE can preserve every identity fact while replacing the BODY or
# a material execution property. Each probe below drifts exactly one such fact,
# keeping schema/name/signature canonical, and proves the ACTUAL write command
# blocks with BridgePreflightError before its service runs.


def _canonical_function_state(conn, schema, name):
    """The live semantic state of a protected function (for repair assertions)."""
    return bridge._function_semantics(conn, schema, name)


def _assert_function_drift_blocks(
    monkeypatch, schema, conn, *, function_name, reason
):
    """Preflight must fault the FUNCTIONS category with the exact reason, the
    real write must be refused, and the drift must not be repaired."""
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["functions_ready"] is False, payload
    assert any(
        b.startswith(f"{function_name}:{reason}") for b in payload["bad_functions"]
    ), payload["bad_functions"]
    # Triggers bound to an uncertified function fail closed as well.
    assert payload["triggers_ready"] is False, payload
    assert any(
        ":func_not_certified" in b for b in payload["bad_triggers"]
    ), payload["bad_triggers"]
    _assert_claim_write_blocked(monkeypatch, schema, conn)


# ── A (mandatory): same schema/name/signature, neutralised BODY ──────────────


def test_body_drifted_mutation_guard_blocks_write(pack_schema, monkeypatch):
    """The exact counterexample: a no-op append-only guard that keeps every
    catalog identity fact the identity-only check verified."""
    conn, schema = pack_schema
    before = _canonical_function_state(conn, schema, "slicea_reject_mutation")[0]
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION slicea_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    conn.commit()

    after = _canonical_function_state(conn, schema, "slicea_reject_mutation")[0]
    # Every identity fact the Iteration-3 check verified is UNCHANGED...
    for identity_key in ("args", "result", "rettype", "pronargs", "language"):
        assert after[identity_key] == before[identity_key], identity_key
    # ...only the body fingerprint moved, and that is what must block.
    assert after["prosrc_md5"] != before["prosrc_md5"]

    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="prosrc_md5",
    )
    # The bridge never repairs drift: the neutralised body is still installed.
    assert _canonical_function_state(
        conn, schema, "slicea_reject_mutation"
    )[0]["prosrc_md5"] == after["prosrc_md5"]


def test_body_drifted_guard_is_genuinely_neutralised(pack_schema):
    """Proves the counterexample is a REAL loss of the append-only guarantee,
    not merely a fingerprint change — so blocking it is load-bearing."""
    conn, _schema = pack_schema
    project_id = pg.insert_project(conn, name="neutralised-guard")
    conn.commit()
    claim = conn.execute(
        """
        INSERT INTO research_claim_draft (project_id, claim_text, created_by)
        VALUES (%s, 'original', 'op') RETURNING id
        """,
        (project_id,),
    ).fetchone()[0]
    conn.commit()
    # Canonical guard: the UPDATE is rejected.
    with pytest.raises(Exception):
        conn.execute(
            "UPDATE research_claim_draft SET claim_text = 'tampered' WHERE id = %s",
            (claim,),
        )
    conn.rollback()
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION slicea_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    conn.commit()
    # Same identity, same triggers — but the append-only ledger is now mutable.
    conn.execute(
        "UPDATE research_claim_draft SET claim_text = 'tampered' WHERE id = %s",
        (claim,),
    )
    conn.commit()
    assert conn.execute(
        "SELECT claim_text FROM research_claim_draft WHERE id = %s", (claim,)
    ).fetchone()[0] == "tampered"


# ── B: prepare trigger function, changed body, unchanged signature/result ────


def test_body_drifted_prepare_function_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    before = _canonical_function_state(
        conn, schema, "research_evidence_prepare_event_insert"
    )[0]
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION research_evidence_prepare_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            RETURN NEW;
        END;
        $$
        """
    )
    conn.commit()

    after = _canonical_function_state(
        conn, schema, "research_evidence_prepare_event_insert"
    )[0]
    # Signature, result, language, security posture and search_path all intact.
    for key in ("args", "result", "rettype", "language", "prosecdef", "proconfig"):
        assert after[key] == before[key], key
    assert after["prosrc_md5"] != before["prosrc_md5"]

    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="research_evidence_prepare_event_insert",
        reason="prosrc_md5",
    )


# ── C: material function-property drift, body otherwise canonical ────────────


def test_security_definer_drift_blocks_write(pack_schema, monkeypatch):
    """SECURITY DEFINER -> SECURITY INVOKER on a canonical prepare function."""
    conn, schema = pack_schema
    conn.execute(
        "ALTER FUNCTION research_evidence_prepare_event_insert() SECURITY INVOKER"
    )
    conn.commit()
    state = _canonical_function_state(
        conn, schema, "research_evidence_prepare_event_insert"
    )[0]
    # Body is untouched — only the security posture drifted.
    assert state["prosrc_md5"] == bridge.CATALOG_FUNCTIONS[
        "research_evidence_prepare_event_insert"
    ]["prosrc_md5"]
    assert state["prosecdef"] is False
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="research_evidence_prepare_event_insert",
        reason="prosecdef",
    )


def test_fixed_search_path_drift_blocks_write(pack_schema, monkeypatch):
    """The canonical fixed `search_path=pg_catalog` must not be repointed."""
    conn, schema = pack_schema
    conn.execute(
        "ALTER FUNCTION research_evidence_prepare_claim_annotation_insert() "
        "SET search_path = public"
    )
    conn.commit()
    state = _canonical_function_state(
        conn, schema, "research_evidence_prepare_claim_annotation_insert"
    )[0]
    assert state["proconfig"] == ("search_path=public",)
    assert state["prosecdef"] is True
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="research_evidence_prepare_claim_annotation_insert",
        reason="proconfig",
    )


def test_volatility_drift_blocks_write(pack_schema, monkeypatch):
    """VOLATILE -> IMMUTABLE on the append-only guard."""
    conn, schema = pack_schema
    conn.execute("ALTER FUNCTION slicea_reject_mutation() IMMUTABLE")
    conn.commit()
    state = _canonical_function_state(conn, schema, "slicea_reject_mutation")[0]
    assert state["provolatile"] == "i"
    assert state["prosrc_md5"] == bridge.CATALOG_FUNCTIONS[
        "slicea_reject_mutation"
    ]["prosrc_md5"]
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="provolatile",
    )


def test_strictness_drift_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("ALTER FUNCTION slicea_reject_mutation() STRICT")
    conn.commit()
    assert _canonical_function_state(
        conn, schema, "slicea_reject_mutation"
    )[0]["proisstrict"] is True
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="proisstrict",
    )


def test_parallel_mode_drift_blocks_write(pack_schema, monkeypatch):
    conn, schema = pack_schema
    conn.execute("ALTER FUNCTION slicea_reject_mutation() PARALLEL SAFE")
    conn.commit()
    assert _canonical_function_state(
        conn, schema, "slicea_reject_mutation"
    )[0]["proparallel"] == "s"
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="proparallel",
    )


def test_security_definer_added_to_invoker_guard_blocks_write(
    pack_schema, monkeypatch
):
    """The v47 guard is canonically SECURITY INVOKER: escalating it must block.

    This is the mirror of the prepare-function probe and proves the manifest is
    not uniform — the two security postures are frozen independently.
    """
    conn, schema = pack_schema
    conn.execute("ALTER FUNCTION slicea_reject_mutation() SECURITY DEFINER")
    conn.commit()
    assert _canonical_function_state(
        conn, schema, "slicea_reject_mutation"
    )[0]["prosecdef"] is True
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="prosecdef",
    )


def test_proconfig_added_to_unconfigured_guard_blocks_write(
    pack_schema, monkeypatch
):
    """The v47 guard canonically has NO proconfig; adding one must block."""
    conn, schema = pack_schema
    conn.execute("ALTER FUNCTION slicea_reject_mutation() SET search_path = pg_catalog")
    conn.commit()
    assert _canonical_function_state(
        conn, schema, "slicea_reject_mutation"
    )[0]["proconfig"] == ("search_path=pg_catalog",)
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="proconfig",
    )


# ── D: overload drift on a protected name ───────────────────────────────────


def test_extra_overload_of_protected_function_blocks_write(pack_schema, monkeypatch):
    """An extra overload of a protected name is rejected by the exact count.

    The canonical no-arg function remains present and byte-identical; only an
    additional overload appears.
    """
    conn, schema = pack_schema
    conn.execute(
        "CREATE FUNCTION slicea_reject_mutation(x integer) RETURNS integer "
        "AS $$ BEGIN RETURN x; END $$ LANGUAGE plpgsql"
    )
    conn.commit()
    states = _canonical_function_state(conn, schema, "slicea_reject_mutation")
    assert len(states) == 2
    assert any(
        s["prosrc_md5"] == bridge.CATALOG_FUNCTIONS[
            "slicea_reject_mutation"
        ]["prosrc_md5"] for s in states
    )
    _assert_function_drift_blocks(
        monkeypatch, schema, conn,
        function_name="slicea_reject_mutation", reason="overloads=2",
    )


# ── the trigger must bind to the exact CERTIFIED function OID ────────────────


def test_trigger_binding_is_compared_against_the_certified_oid(pack_schema):
    """`tgfoid` is compared to the certified OID, not merely resolved by name.

    On a live clean topology the manifest binding is proven two ways: the real
    trigger state satisfies `_trigger_problem` with the certified OID, and the
    SAME state is rejected with `func_identity` when the certified OID differs —
    even though schema, name, identity args and result type are all canonical.
    """
    conn, schema = pack_schema
    problem, certified = bridge._certify_function(
        conn, schema, "slicea_reject_mutation"
    )
    assert problem is None
    state = bridge._trigger_state(
        conn, schema, "research_claim_draft", "trg_rcd_no_mutation"
    )
    assert state["tgfoid"] == certified
    common = dict(
        expected_func="slicea_reject_mutation", expected_tgtype=27,
        expected_tgenabled="O", cur_schema=schema,
    )
    assert bridge._trigger_problem(state, certified_oid=certified, **common) is None
    # Same canonical identity facts, different certified OID → rejected.
    assert state["func_name"] == "slicea_reject_mutation"
    assert state["func_schema"] == schema
    assert state["func_args"] == "" and state["func_result"] == "trigger"
    assert bridge._trigger_problem(
        state, certified_oid=certified + 1, **common
    ) == "func_identity"
    # A function that failed certification fails its triggers closed.
    assert bridge._trigger_problem(
        state, certified_oid=None, **common
    ) == "func_not_certified"


def test_body_drift_makes_dependent_triggers_fail_closed(pack_schema, monkeypatch):
    """A drifted function decertifies EVERY trigger that binds to it.

    The triggers themselves are untouched and still resolve to the canonical
    schema+name+signature, so only the certification linkage can reject them.
    """
    conn, schema = pack_schema
    conn.execute(
        "CREATE OR REPLACE FUNCTION slicea_reject_mutation() RETURNS trigger AS "
        "$$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql"
    )
    conn.commit()
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["functions_ready"] is False
    guarded = [
        f"{relation}.{tgname}"
        for relation, tgname, func, *_ in bridge.CATALOG_TRIGGERS
        if func == "slicea_reject_mutation"
    ]
    for entry in guarded:
        assert f"{entry}:func_not_certified" in payload["bad_triggers"], entry
    _assert_claim_write_blocked(monkeypatch, schema, conn)


# ── the frozen manifest must match a clean canonical topology ────────────────


def test_function_manifest_matches_clean_canonical_topology(pack_schema):
    """Re-derive every frozen semantic value from a clean full topology.

    This is the cross-check that keeps the bounded manifest honest: if a ratified
    migration ever changes one of these functions, this test fails loudly instead
    of the bridge silently blocking every write in production.
    """
    conn, schema = pack_schema
    for name, expected in bridge.CATALOG_FUNCTIONS.items():
        states = bridge._function_semantics(conn, schema, name)
        assert len(states) == expected["overloads"], name
        state = states[0]
        for key in bridge._FUNCTION_SEMANTIC_KEYS:
            assert state[key] == expected[key], f"{name}.{key}"
        problem, oid = bridge._certify_function(conn, schema, name)
        assert problem is None, f"{name}: {problem}"
        assert isinstance(oid, int)


def test_clean_topology_certifies_every_trigger_to_its_function_oid(pack_schema):
    """On a clean topology every manifest trigger binds to the certified OID."""
    conn, schema = pack_schema
    certified = {}
    for name in bridge.CATALOG_FUNCTIONS:
        problem, oid = bridge._certify_function(conn, schema, name)
        assert problem is None, f"{name}: {problem}"
        certified[name] = oid
    for relation, tgname, func, tgtype, tgenabled in bridge.CATALOG_TRIGGERS:
        state = bridge._trigger_state(conn, schema, relation, tgname)
        assert state is not None, f"{relation}.{tgname}"
        assert state["tgfoid"] == certified[func], f"{relation}.{tgname}"
        assert bridge._trigger_problem(
            state, expected_func=func, expected_tgtype=tgtype,
            expected_tgenabled=tgenabled, cur_schema=schema,
            certified_oid=certified[func],
        ) is None, f"{relation}.{tgname}"


# ═══════ ITERATION 5: load-bearing request-id uniqueness (constraint drift) ═══════
#
# Every request-id-bearing bridge write documents an idempotent retry. That
# promise is enforced by a UNIQUE constraint, not by the repositories' pre-read:
# at READ COMMITTED two callers can both observe "no such request", and the
# prepare triggers serialise only the *sequence allocator*. The probes below
# drift each load-bearing constraint and prove (a) preflight names the exact
# constraint and reason, (b) the corresponding REAL bridge write command is
# blocked with BridgePreflightError before its service runs, (c) the ledger is
# unchanged, and (d) the bridge never repairs what it found drifted.


def _seed_request_chain(monkeypatch, schema, conn, tag):
    """Commit a clean-topology chain so every request-id write path is reachable."""
    project_id = pg.insert_project(conn, name=f"constraint-{tag}")
    blob = ev_repo.insert_or_get_blob(
        conn, project_id=project_id,
        content_hash=f"constraint-{tag}-{uuid.uuid4().hex}", byte_size=42,
    )
    snapshot = ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id,
        storage_ref=f"/r2a4b-c/{uuid.uuid4().hex}",
    )
    conn.commit()

    def run(argv):
        code, payload = _bridge(monkeypatch, schema, argv + ["--commit"])
        assert code == 0, payload
        assert payload["committed"] is True
        return payload

    meta = run([
        "source-metadata-create", "--project-id", project_id,
        "--source-snapshot-id", snapshot, "--actor", "op",
        "--citation-label", "Constraint Source", "--canonical-source-locator",
        "https://example.org/constraint",
    ])["source_metadata_revision_id"]
    fact = run([
        "fact-create", "--project-id", project_id, "--source-snapshot-id", snapshot,
        "--actor", "op", "--fact-type", "count", "--value", "7",
        "--counted-entity", "records", "--citation-locator", "section 1",
    ])
    claim_draft_id = run([
        "claim-create", "--project-id", project_id, "--actor", "op",
        "--claim-text", "Constraint-probe claim",
    ])["claim_draft_id"]

    def intake_item(reason, extra):
        intake = run([
            "intake-create", "--project-id", project_id, "--actor", "op",
            "--source-snapshot-id", snapshot,
            "--source-metadata-revision-id", meta, "--selection-reason", reason,
        ])["research_evidence_intake_id"]
        return run([
            "intake-item-create", "--project-id", project_id, "--actor", "op",
            "--research-evidence-intake-id", intake, *extra,
        ])["research_evidence_intake_item_id"]

    evidence_item_id = intake_item("evidence intake", [
        "--item-kind", "candidate_fact",
        "--candidate-fact-revision-id", fact["candidate_fact_revision_id"],
        "--fact-metadata-revision-id", fact["fact_metadata_revision_id"],
    ])
    claim_item_id = intake_item("claim intake", [
        "--item-kind", "claim_draft", "--claim-draft-id", claim_draft_id,
    ])

    for item_id, suffix in ((claim_item_id, "claim"), (evidence_item_id, "evidence")):
        run([
            "review-record", "--project-id", project_id,
            "--research-evidence-intake-item-id", item_id,
            "--decision-type", "approved", "--decision-reason", "reviewed",
            "--actor", "op", "--request-id", f"seed-review-{suffix}",
        ])
    run([
        "claim-support-record", "--project-id", project_id,
        "--claim-intake-item-id", claim_item_id,
        "--evidence-intake-item-id", evidence_item_id,
        "--request-id", "seed-support",
        "--locator-resolution", "resolvable", "--locator-rationale", "ok",
        "--evidence-linkage", "linked", "--evidence-linkage-rationale", "ok",
        "--semantic-relationship", "support",
        "--semantic-relationship-rationale", "ok", "--actor", "op",
    ])
    run([
        "annotation-record", "--project-id", project_id,
        "--claim-draft-id", claim_draft_id, "--request-id", "seed-annotation",
        "--epistemic-status", "reported_fact", "--confidence-label", "high",
        "--decision-relevance", "relevant", "--supports-statement", "supports",
        "--does-not-prove", "does not prove causality", "--actor", "op",
    ])
    conn.rollback()
    return {
        "project_id": project_id,
        "claim_draft_id": claim_draft_id,
        "claim_item_id": claim_item_id,
        "evidence_item_id": evidence_item_id,
    }


# Each load-bearing constraint → (ledger relation, argv for the REAL bridge write
# command that depends on it). The argv builder takes the seeded chain + a fresh
# request id, so on a CLEAN topology the same command would commit.
def _review_argv(chain, request_id):
    return ["review-record", "--project-id", chain["project_id"],
            "--research-evidence-intake-item-id", chain["evidence_item_id"],
            "--decision-type", "approved", "--decision-reason", "re-reviewed",
            "--actor", "op", "--request-id", request_id]


def _freshness_argv(chain, request_id):
    return ["freshness-record", "--project-id", chain["project_id"],
            "--research-evidence-intake-item-id", chain["evidence_item_id"],
            "--request-id", request_id,
            "--policy-identifier", "freshness-policy", "--policy-version", "1",
            "--evaluator-version", "1",
            "--basis-timestamp", "2026-01-01T00:00:00+00:00",
            "--fresh-through", "2026-12-31T00:00:00+00:00",
            "--drift-status", "no_material_drift", "--drift-reason", "stable",
            "--actor", "op"]


def _claim_support_argv(chain, request_id):
    return ["claim-support-record", "--project-id", chain["project_id"],
            "--claim-intake-item-id", chain["claim_item_id"],
            "--evidence-intake-item-id", chain["evidence_item_id"],
            "--request-id", request_id,
            "--locator-resolution", "resolvable", "--locator-rationale", "ok",
            "--evidence-linkage", "linked", "--evidence-linkage-rationale", "ok",
            "--semantic-relationship", "support",
            "--semantic-relationship-rationale", "ok", "--actor", "op"]


def _context_argv(chain, request_id):
    return ["context-record", "--project-id", chain["project_id"],
            "--request-id", request_id,
            "--research-question", "does the bridge fail closed?",
            "--actor", "op"]


def _annotation_argv(chain, request_id):
    return ["annotation-record", "--project-id", chain["project_id"],
            "--claim-draft-id", chain["claim_draft_id"],
            "--request-id", request_id,
            "--epistemic-status", "reported_fact", "--confidence-label", "high",
            "--decision-relevance", "relevant", "--supports-statement", "supports",
            "--does-not-prove", "does not prove causality", "--actor", "op"]


def _authorize_argv(chain, request_id):
    confirm = (f"{chain['project_id']} {chain['claim_item_id']} "
               f"{chain['evidence_item_id']}")
    return ["authorize-internal-analysis", "--project-id", chain["project_id"],
            "--claim-intake-item-id", chain["claim_item_id"],
            "--evidence-intake-item-id", chain["evidence_item_id"],
            "--request-id", request_id, "--reason", "authorized",
            "--actor", "op", "--confirm", confirm]


CONSTRAINT_WRITE_PROBES = {
    "uq_reird_item_request": (
        "research_evidence_intake_item_review_decision", _review_argv),
    "uq_reifa_item_request": (
        "research_evidence_intake_item_freshness_assessment", _freshness_argv),
    "uq_recsa_pair_request": (
        "research_evidence_claim_support_assessment", _claim_support_argv),
    "uq_repcr_project_request": (
        "research_evidence_project_context_revision", _context_argv),
    "uq_recar_claim_request": (
        "research_evidence_claim_annotation_revision", _annotation_argv),
    "uq_reuad_scope_request": (
        "research_evidence_usage_authorization_decision", _authorize_argv),
}


def _ledger_count(conn, relation, project_id):
    conn.rollback()
    return conn.execute(
        f"SELECT count(*) FROM {relation} WHERE project_id = %s", (project_id,)
    ).fetchone()[0]


def _constraint_present(conn, conname):
    conn.rollback()
    return conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname = %s AND contype = 'u'",
        (conname,),
    ).fetchone()[0]


def _assert_constraint_drift_blocks(
    monkeypatch, schema, conn, chain, conname, *, reason
):
    """Preflight names the exact drift AND the real write command is blocked."""
    relation, argv_for = CONSTRAINT_WRITE_PROBES[conname]

    payload = _preflight_payload(monkeypatch, schema)
    assert payload["constraints_ready"] is False, payload
    assert f"{relation}.{conname}:{reason}" in payload["bad_constraints"], (
        payload["bad_constraints"]
    )
    # Only the constraint category degraded — the probe is discriminating.
    for other in ("relations_ready", "functions_ready", "triggers_ready",
                  "roles_ready", "topology_security_ready", "namespace_ready"):
        assert payload[other] is True, (other, payload)
    assert payload["research_writes_allowed"] is False
    assert payload["fact_writes_allowed"] is False
    assert payload["writes_allowed"] is False

    before = _ledger_count(conn, relation, chain["project_id"])
    constraint_before = _constraint_present(conn, conname)

    code, out = _bridge(
        monkeypatch, schema,
        argv_for(chain, f"drift-{uuid.uuid4().hex[:8]}") + ["--commit"],
    )
    assert code == bridge.EXIT_FAILURE, out
    assert out["error_type"] == "BridgePreflightError", out
    # …blocked BEFORE the service ran: the ledger is untouched…
    assert _ledger_count(conn, relation, chain["project_id"]) == before
    # …and the bridge never repaired the drift it refused to write through.
    assert _constraint_present(conn, conname) == constraint_before


# ── A: the constraint is simply dropped ─────────────────────────────────────


def test_dropped_review_request_constraint_blocks_review_record(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "drop-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.commit()
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="missing",
    )


# ── B: replaced by a NON-UNIQUE index over the same columns ─────────────────


def test_non_unique_index_replacement_blocks_review_record(pack_schema, monkeypatch):
    """A same-named, same-columns plain index enforces nothing."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "index-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.execute(
        "CREATE INDEX uq_reird_item_request "
        "ON research_evidence_intake_item_review_decision "
        "(project_id, research_evidence_intake_item_id, request_id)"
    )
    conn.commit()
    # The NAME resolves — as an index, not as a constraint.
    assert conn.execute(
        "SELECT indisunique FROM pg_index "
        "WHERE indexrelid = 'uq_reird_item_request'::regclass"
    ).fetchone()[0] is False
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="missing",
    )


# ── C: recreated UNIQUE with the WRONG ordered columns ──────────────────────


def test_wrong_ordered_columns_constraint_blocks_review_record(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "order-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ADD CONSTRAINT uq_reird_item_request UNIQUE "
        "(research_evidence_intake_item_id, project_id, request_id)"
    )
    conn.commit()
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="columns",
    )


def test_widened_key_constraint_blocks_review_record(pack_schema, monkeypatch):
    """A widened key admits two rows per request_id — the exact defect."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "widen-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ADD CONSTRAINT uq_reird_item_request UNIQUE "
        "(project_id, research_evidence_intake_item_id, request_id, "
        "decision_sequence)"
    )
    conn.commit()
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="columns",
    )


# ── D: deferrability drift ──────────────────────────────────────────────────


def test_deferrable_request_constraint_blocks_review_record(pack_schema, monkeypatch):
    """DEFERRABLE moves the violation to COMMIT, past the repository's recovery."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "defer-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ADD CONSTRAINT uq_reird_item_request UNIQUE "
        "(project_id, research_evidence_intake_item_id, request_id) DEFERRABLE"
    )
    conn.commit()
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="deferrable",
    )


def test_initially_deferred_request_constraint_blocks_review_record(
    pack_schema, monkeypatch
):
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "deferred-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ADD CONSTRAINT uq_reird_item_request UNIQUE "
        "(project_id, research_evidence_intake_item_id, request_id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    conn.commit()
    # DEFERRABLE is reported first; both facts are catalog-visible.
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="deferrable",
    )
    state = bridge._constraint_state(
        conn, schema, "research_evidence_intake_item_review_decision",
        "uq_reird_item_request",
    )
    assert state["condeferrable"] is True and state["condeferred"] is True
    assert state["index_immediate"] is False


# ── E: every remaining load-bearing constraint blocks ITS OWN write command ──


@pytest.mark.parametrize(
    "conname",
    ["uq_reifa_item_request", "uq_recsa_pair_request", "uq_repcr_project_request",
     "uq_recar_claim_request", "uq_reuad_scope_request"],
)
def test_dropping_each_request_constraint_blocks_its_write_command(
    pack_schema, monkeypatch, conname
):
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, conname[3:11])
    relation, _argv = CONSTRAINT_WRITE_PROBES[conname]
    conn.execute(f"ALTER TABLE {relation} DROP CONSTRAINT {conname}")
    conn.commit()
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, conname, reason="missing",
    )


# ── the manifest must match a clean canonical topology ──────────────────────


def test_constraint_manifest_matches_clean_canonical_topology(pack_schema):
    """Every frozen constraint fact is re-derived from a clean full topology."""
    conn, schema = pack_schema
    for relation, conname, columns in bridge.CATALOG_CONSTRAINTS:
        state = bridge._constraint_state(conn, schema, relation, conname)
        assert state is not None, conname
        assert state["contype"] == "u", conname
        assert state["columns"] == columns, conname
        assert state["convalidated"] is True, conname
        assert state["condeferrable"] is False, conname
        assert state["condeferred"] is False, conname
        assert state["index_relation"] == state["conrelid"], conname
        assert state["index_unique"] is True, conname
        assert state["index_valid"] is True, conname
        assert state["index_ready"] is True, conname
        assert state["index_live"] is True, conname
        assert state["index_immediate"] is True, conname
        assert state["index_has_expressions"] is False, conname
        assert state["index_has_predicate"] is False, conname
        assert bridge._constraint_problem(
            state, expected_columns=columns
        ) is None, conname


def test_same_name_constraint_on_another_relation_does_not_satisfy(
    pack_schema, monkeypatch
):
    """A decoy constraint of the same name on a different table is not accepted."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "decoy-reird")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    # The protected NAME now exists in the schema — on the wrong relation.
    conn.execute(
        "CREATE TABLE decoy_reird (project_id UUID NOT NULL, "
        "research_evidence_intake_item_id UUID NOT NULL, request_id TEXT NOT NULL, "
        "CONSTRAINT uq_reird_item_request UNIQUE "
        "(project_id, research_evidence_intake_item_id, request_id))"
    )
    conn.commit()
    assert _constraint_present(conn, "uq_reird_item_request") == 1
    _assert_constraint_drift_blocks(
        monkeypatch, schema, conn, chain, "uq_reird_item_request",
        reason="missing",
    )


# ═══════ ITERATION 5: concurrency proof — why the UNIQUE is load-bearing ═══════
#
# The repositories pre-read the request before inserting. At READ COMMITTED that
# pre-read is NOT a guard: two callers can both observe "no such request". The
# prepare trigger then serialises them on the *sequence allocator* row
# (SELECT ... FOR UPDATE), which hands out two DISTINCT sequences rather than
# rejecting the second caller. The UNIQUE constraint is the only thing that turns
# the loser's INSERT into the 23505 that `insert_decision` recovers the winner
# from. The two tests below run that exact interleaving with genuine callers
# against genuine PostgreSQL: once with the constraint absent (duplicate commits)
# and once on clean topology (idempotent recovery). No production code is
# bypassed — the drift is made in the test's own disposable schema, and the
# second half of the drifted test proves the production bridge refuses to write
# at all in that state.


def _read_committed_caller(schema):
    """A genuine caller connection: non-autocommit, explicitly READ COMMITTED."""
    import psycopg

    conn = pg.connect(schema=schema)
    conn.autocommit = False
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    return conn


def _wait_for_lock_wait(timeout=30.0):
    """Block until some backend is waiting on a lock (caller B on the allocator)."""
    watcher = pg.connect(autocommit=True)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()[0]
            if waiting:
                return True
            time.sleep(0.05)
        return False
    finally:
        watcher.close()


def _race_two_review_callers(schema, chain, request_id):
    """Interleave two canonical review callers on one request_id.

    Returns ``(record_a, record_b, error_b)``. Caller B is started while A holds
    the allocator lock uncommitted, so B's pre-read observes no request and B's
    INSERT blocks inside the prepare trigger until A commits — the precise race
    the UNIQUE constraint exists to resolve.
    """
    from research_evidence.review_models import (
        ResearchEvidenceIntakeItemReviewDecisionCreate,
    )
    from research_evidence.review_service import record_item_review_decision

    def payload():
        return ResearchEvidenceIntakeItemReviewDecisionCreate(
            project_id=chain["project_id"],
            research_evidence_intake_item_id=chain["evidence_item_id"],
            decision_type="approved",
            decision_reason="raced retry",
            decided_by="op",
            request_id=request_id,
        )

    conn_a = _read_committed_caller(schema)
    conn_b = _read_committed_caller(schema)
    outcome = {}

    def caller_b():
        try:
            outcome["record"] = record_item_review_decision(conn_b, payload())
        except Exception as exc:  # captured, re-raised in the main thread
            outcome["error"] = exc

    try:
        # Both callers are genuinely at READ COMMITTED.
        for conn in (conn_a, conn_b):
            bridge._verify_read_committed(conn)
        # A inserts and holds the allocator lock, uncommitted.
        record_a = record_item_review_decision(conn_a, payload())
        thread = threading.Thread(target=caller_b, daemon=True)
        thread.start()
        assert _wait_for_lock_wait(), "caller B never blocked on the allocator"
        # …now A commits, releasing B into the same request_id.
        conn_a.commit()
        thread.join(timeout=60)
        assert not thread.is_alive(), "caller B did not finish"
        if "error" not in outcome:
            conn_b.commit()
        else:
            conn_b.rollback()
        return record_a, outcome.get("record"), outcome.get("error")
    finally:
        for conn in (conn_a, conn_b):
            try:
                conn.rollback()
            finally:
                conn.close()


def _request_id_rows(conn, chain, request_id):
    conn.rollback()
    return conn.execute(
        """
        SELECT id::text, decision_sequence
        FROM research_evidence_intake_item_review_decision
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
          AND request_id = %s
        ORDER BY decision_sequence
        """,
        (chain["project_id"], chain["evidence_item_id"], request_id),
    ).fetchall()


def test_absent_request_unique_lets_two_callers_commit_one_request_id(
    pack_schema, monkeypatch
):
    """With the UNIQUE gone, the documented idempotent retry silently breaks."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "race-drift")
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT uq_reird_item_request"
    )
    conn.commit()

    request_id = f"raced-{uuid.uuid4().hex[:8]}"
    record_a, record_b, error_b = _race_two_review_callers(schema, chain, request_id)

    assert error_b is None, error_b
    # Two DISTINCT decisions committed under ONE request_id — the defect.
    assert record_b.id != record_a.id
    rows = _request_id_rows(conn, chain, request_id)
    assert len(rows) == 2, rows
    # The prepare trigger did its job (distinct, contiguous sequences); it is the
    # sequence allocator, not a request guard.
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)
    assert len({r[1] for r in rows}) == 2

    # …and in exactly this state the production bridge refuses to write at all,
    # before any service runs. No production bypass exists: the drift was made
    # by the test, and the bridge still fails closed.
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["constraints_ready"] is False
    assert (
        "research_evidence_intake_item_review_decision.uq_reird_item_request:missing"
        in payload["bad_constraints"]
    )
    before = _ledger_count(
        conn, "research_evidence_intake_item_review_decision", chain["project_id"]
    )
    code, out = _bridge(
        monkeypatch, schema,
        _review_argv(chain, f"post-race-{uuid.uuid4().hex[:8]}") + ["--commit"],
    )
    assert code == bridge.EXIT_FAILURE, out
    assert out["error_type"] == "BridgePreflightError", out
    assert _ledger_count(
        conn, "research_evidence_intake_item_review_decision", chain["project_id"]
    ) == before


def test_clean_topology_makes_the_same_race_idempotent(pack_schema, monkeypatch):
    """The same interleaving on CLEAN topology yields exactly one decision."""
    conn, schema = pack_schema
    chain = _seed_request_chain(monkeypatch, schema, conn, "race-clean")

    request_id = f"raced-{uuid.uuid4().hex[:8]}"
    record_a, record_b, error_b = _race_two_review_callers(schema, chain, request_id)

    assert error_b is None, error_b
    # The loser recovered the winner's row from the named 23505 violation.
    assert record_b.id == record_a.id
    assert record_b.decision_sequence == record_a.decision_sequence
    rows = _request_id_rows(conn, chain, request_id)
    assert len(rows) == 1, rows

    # …and the bridge writes normally on this topology, including an idempotent
    # retry of the very same request_id through the real command.
    payload = _preflight_payload(monkeypatch, schema)
    assert payload["constraints_ready"] is True
    assert payload["writes_allowed"] is True

    retry_request = f"bridge-retry-{uuid.uuid4().hex[:8]}"
    first = _bridge(
        monkeypatch, schema, _review_argv(chain, retry_request) + ["--commit"]
    )
    assert first[0] == 0, first[1]
    second = _bridge(
        monkeypatch, schema, _review_argv(chain, retry_request) + ["--commit"]
    )
    assert second[0] == 0, second[1]
    assert second[1]["review_decision_id"] == first[1]["review_decision_id"]
    assert second[1]["decision_sequence"] == first[1]["decision_sequence"]
    assert _ledger_count(
        conn, "research_evidence_intake_item_review_decision", chain["project_id"]
    ) == 4  # 2 seeded reviews + 1 raced + 1 bridge retry (recorded once)
