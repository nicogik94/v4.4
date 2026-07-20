"""Disposable-PostgreSQL integration for the R2.0A-4A consumer.

Proves against a genuine database that the consumer: consumes exactly the
A-2/A-3 authorized internal_analysis membership, holds a read-only, no-commit,
no-mutation posture on its own connection, and cannot resurrect revoked or
other-scope-only evidence. Skips unless TEST_EVIDENCE_PG_DSN is set.
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import research_evidence_context as rc  # noqa: E402
from research_evidence import pack_service  # noqa: E402
from research_evidence.pack_models import (  # noqa: E402
    ResearchEvidenceUsageAuthorizationDecisionCreate,
)
from research_evidence.presentation_projection_service import (  # noqa: E402
    project_research_evidence_presentation,
)
from tests.test_research_evidence_pack_schema import (  # noqa: E402
    _annotate_claim,
    _prepare_service_authorization,
)


@pytest.fixture
def pack_schema():
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


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _authorize_scope(conn, claim, evidence, *, tag, usage_scope):
    return pack_service.record_usage_authorization_decision(
        conn,
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            project_id=claim["project"], claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"], usage_scope=usage_scope,
            decision="authorized", reason="Explicitly authorized",
            actor="operator", request_id=f"{tag}-{usage_scope}-authorization",
        ),
    )


class RecordingConn:
    """Transparent proxy that records commits/writes and read-only posture."""

    def __init__(self, real):
        self._real = real
        self._commit_calls = 0
        self._executed = []
        self._read_only_history = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def commit_calls(self):
        return self._commit_calls

    @property
    def executed(self):
        return list(self._executed)

    @property
    def read_only_history(self):
        return list(self._read_only_history)

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
        self._read_only_history.append(value)
        self._real.read_only = value

    def execute(self, query, *args, **kwargs):
        self._executed.append(str(query))
        return self._real.execute(query, *args, **kwargs)

    def commit(self):
        self._commit_calls += 1
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()


def _consumer_connect(schema, *, recorder=None):
    def connect():
        real = pg.connect(schema=schema, autocommit=True)
        conn = RecordingConn(real)
        if recorder is not None:
            recorder.append(conn)
        return conn

    return connect


def _state(project_id):
    import types

    return types.SimpleNamespace(
        project_id=project_id, project_type="strategic_audit", policy_audit_log=[],
    )


def _consume(project_id, schema, *, phase="audit", recorder=None):
    return asyncio.run(
        rc.load_research_evidence_consumption(
            _state(project_id), phase, connect=_consumer_connect(schema, recorder=recorder),
        )
    )


def _write_forbidden_under_read_only(schema):
    """Open a connection with the consumer posture and attempt a write."""
    real = pg.connect(schema=schema, autocommit=True)
    conn = RecordingConn(real)
    rc._enforce_read_only_posture(conn)
    try:
        conn.execute(
            "INSERT INTO projects (name, brief) VALUES ('blocked', '')"
        )
        return None
    except Exception as exc:  # psycopg read-only-transaction error expected
        return exc
    finally:
        rc._safe_close(conn)


# ─────────────────────────── consumption ────────────────────────────────────


def test_consumer_uses_authorized_internal_analysis_membership(pack_schema):
    conn, schema = pack_schema
    tag = "consume-used"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    record = _authorize_scope(
        conn, claim, evidence, tag=tag, usage_scope="internal_analysis",
    )
    reference = project_research_evidence_presentation(
        conn, project_id=claim["project"], usage_scope="internal_analysis",
    )
    conn.commit()  # the consumer reads committed state on its own connection

    consumption = _consume(claim["project"], schema)

    assert consumption.used
    assert consumption.projection_fingerprint == reference.projection_fingerprint
    assert consumption.claim_count == 1
    assert consumption.source_count == 1
    assert consumption.relationship_count == 1
    # membership rendered into the model-facing block
    assert reference.claims[0].claim_draft_id in consumption.block
    assert reference.claims[0].claim_text in consumption.block
    assert reference.sources[0].source_snapshot_id in consumption.block
    # internal persistence mechanics still omitted even against a real DB
    assert record.id not in consumption.block
    assert reference.sources[0].source_blob_id not in consumption.block


def test_consumer_connection_is_read_only_no_commit_no_write(pack_schema):
    conn, schema = pack_schema
    tag = "consume-readonly"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    _authorize_scope(conn, claim, evidence, tag=tag, usage_scope="internal_analysis")
    conn.commit()

    recorder = []
    consumption = _consume(claim["project"], schema, recorder=recorder)
    assert consumption.used

    spy = recorder[0]
    assert spy.commit_calls == 0
    assert spy.read_only_history and spy.read_only_history[-1] is True
    writes = [
        sql for sql in spy.executed
        if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []

    # the DB itself rejects a write under the consumer's read-only posture
    error = _write_forbidden_under_read_only(schema)
    assert error is not None
    assert "read-only" in str(error).lower()


def test_consumer_does_not_mutate_authorization_state(pack_schema):
    conn, schema = pack_schema
    tag = "consume-nomutate"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    _authorize_scope(conn, claim, evidence, tag=tag, usage_scope="internal_analysis")
    conn.commit()

    def snapshot():
        rows = {}
        for table in (
            "research_evidence_usage_authorization_decision",
            "research_evidence_claim_annotation_revision",
            "source_snapshot",
            "candidate_fact_revision",
        ):
            rows[table] = conn.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
        return rows

    before = snapshot()
    consumption = _consume(claim["project"], schema)
    assert consumption.used
    conn.rollback()  # discard read snapshot; loader committed nothing
    assert snapshot() == before


# ─────────────────────────── non-resurrection ───────────────────────────────


def test_consumer_cannot_resurrect_revoked_evidence(pack_schema):
    conn, schema = pack_schema
    tag = "consume-revoke"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    _authorize_scope(conn, claim, evidence, tag=tag, usage_scope="internal_analysis")
    conn.commit()
    assert _consume(claim["project"], schema).used

    pack_service.record_usage_authorization_decision(
        conn,
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            project_id=claim["project"], claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"],
            usage_scope="internal_analysis", decision="revoked",
            reason="No longer authorized", actor="operator",
            request_id=f"{tag}-revocation",
        ),
    )
    conn.commit()

    consumption = _consume(claim["project"], schema)
    assert consumption.empty
    assert consumption.block is None
    assert consumption.relationship_count == 0


def test_consumer_cannot_resurrect_stale_annotation_evidence(pack_schema):
    conn, schema = pack_schema
    tag = "consume-stale"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    _authorize_scope(conn, claim, evidence, tag=tag, usage_scope="internal_analysis")
    conn.commit()
    assert _consume(claim["project"], schema).used

    # superseding the annotation makes the prior authorization stale
    _annotate_claim(conn, claim["project"], claim["claim"], f"{tag}-2")
    conn.commit()

    consumption = _consume(claim["project"], schema)
    assert consumption.empty
    assert consumption.relationship_count == 0


def test_consumer_ignores_other_scope_only_authorizations(pack_schema):
    conn, schema = pack_schema
    tag = "consume-scope-isolation"
    # _prepare_service_authorization authorizes client_report only; add
    # operator_dossier. internal_analysis is deliberately NOT authorized.
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    _authorize_scope(conn, claim, evidence, tag=tag, usage_scope="operator_dossier")
    conn.commit()

    consumption = _consume(claim["project"], schema)
    assert consumption.empty
    assert consumption.relationship_count == 0
