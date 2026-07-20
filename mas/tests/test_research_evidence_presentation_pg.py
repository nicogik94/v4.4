import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from research_evidence import pack_service  # noqa: E402
from research_evidence import presentation_projection_policy as policy  # noqa: E402
from research_evidence import presentation_projection_service as presentation  # noqa: E402
from research_evidence.pack_models import (  # noqa: E402
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    UsageScope,
)
from tests.test_research_evidence_pack_schema import (  # noqa: E402
    _annotate_claim,
    _prepare_service_authorization,
)


SCOPES = ("internal_analysis", "operator_dossier", "client_report")
OBSERVED_TABLES = (
    "research_evidence_project_context_revision",
    "research_evidence_claim_annotation_revision",
    "research_evidence_usage_authorization_decision",
    "research_evidence_usage_authorization_sequence_allocator",
    "research_evidence_intake_item_review_decision",
    "research_evidence_claim_support_assessment",
    "research_evidence_intake_item",
    "source_snapshot",
    "candidate_fact_revision",
    "evidence_retention_event",
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


def _project(conn, project_id, usage_scope):
    return presentation.project_research_evidence_presentation(
        conn, project_id=project_id, usage_scope=usage_scope,
    )


def test_full_authorized_pipeline_projects_each_scope(pack_schema):
    conn, _ = pack_schema
    tag = "present-full"
    claim, evidence, _, client_record = _prepare_service_authorization(conn, tag)
    records = {"client_report": client_record}
    for scope in ("internal_analysis", "operator_dossier"):
        records[scope] = _authorize_scope(
            conn, claim, evidence, tag=tag, usage_scope=scope,
        )

    projections = {}
    for scope in SCOPES:
        first = _project(conn, claim["project"], scope)
        second = _project(conn, claim["project"], scope)
        assert first == second
        assert first.projection_fingerprint == second.projection_fingerprint
        projections[scope] = first

        pack = pack_service.assemble_research_evidence_pack(
            conn, project_id=claim["project"], usage_scope=scope,
        )
        assert tuple(item.claim_draft_id for item in first.claims) == tuple(
            item.claim_draft_id for item in pack.claims
        ) == (claim["claim"],)
        assert tuple(item.source_snapshot_id for item in first.sources) == tuple(
            item.source_snapshot_id for item in pack.sources
        ) == (evidence["snapshot"],)
        assert tuple(
            item.candidate_fact_revision_id for item in first.evidence
        ) == tuple(
            item.candidate_fact_revision_id for item in pack.evidence
        ) == (evidence["fact"],)
        assert first.counts == pack.counts
        assert first.claims[0].claim_text == pack.claims[0].claim_text == (
            f"Draft claim {tag}-claim"
        )
        assert first.claims[0].does_not_prove == "limited"
        assert first.policy_fingerprint == policy.PRESENTATION_POLICY_FINGERPRINT

    internal = projections["internal_analysis"]
    operator = projections["operator_dossier"]
    client = projections["client_report"]

    assert internal.relationships[0].authorization_decision_id == (
        records["internal_analysis"].id
    )
    assert internal.relationships[0].claim_intake_item_id == claim["item"]
    assert internal.sources[0].source_metadata_revision_id == (
        evidence["source_metadata"]
    )
    assert internal.sources[0].source_blob_id == evidence["blob"]
    assert internal.evidence[0].stable_fact_key is not None
    assert internal.evidence[0].source_char_range == "10-20"
    assert internal.relationships[0].usage_scope is UsageScope.INTERNAL_ANALYSIS

    assert operator.relationships[0].authorization_decision_id is None
    assert operator.relationships[0].claim_intake_item_id is None
    assert operator.relationships[0].authorized_at is not None
    assert operator.sources[0].captured_at is not None
    assert operator.sources[0].source_locator is None
    assert operator.claims[0].decision_relevance == "relevant"
    assert operator.evidence[0].stable_fact_key is None
    assert operator.evidence[0].source_char_range is None

    assert client.claims[0].decision_relevance is None
    assert client.claims[0].annotation_recorded_at is None
    assert client.sources[0].captured_at is None
    assert client.sources[0].source_kind is None
    assert client.sources[0].citation_label == f"source-{tag}-evidence"
    assert client.sources[0].canonical_source_locator == f"stored://{tag}-evidence"
    assert client.evidence[0].citation_locator == f"section-{tag}-evidence"
    assert client.relationships[0].semantic_relationship == "support"
    assert client.relationships[0].authorized_at is None

    assert len({
        item.projection_fingerprint for item in projections.values()
    }) == len(SCOPES)


def test_usage_scope_isolation_yields_typed_empty_projections(pack_schema):
    conn, _ = pack_schema
    claim, _, _, _ = _prepare_service_authorization(conn, "present-isolate")
    client = _project(conn, claim["project"], "client_report")
    assert client.counts.relationship_count == 1
    for scope in ("internal_analysis", "operator_dossier"):
        empty = _project(conn, claim["project"], scope)
        assert empty.project_id == claim["project"]
        assert empty.usage_scope is UsageScope(scope)
        assert empty.context is None
        assert empty.claims == empty.sources == empty.evidence == ()
        assert empty.relationships == ()
        assert empty.counts.relationship_count == 0
        assert empty.policy_fingerprint == policy.PRESENTATION_POLICY_FINGERPRINT
        assert empty.projection_fingerprint != client.projection_fingerprint


def test_cross_project_isolation(pack_schema):
    conn, _ = pack_schema
    claim_a, _, _, _ = _prepare_service_authorization(conn, "present-project-a")
    claim_b, _, _, _ = _prepare_service_authorization(conn, "present-project-b")
    assert claim_a["project"] != claim_b["project"]
    projection_a = _project(conn, claim_a["project"], "client_report")
    projection_b = _project(conn, claim_b["project"], "client_report")
    assert [item.claim_draft_id for item in projection_a.claims] == [claim_a["claim"]]
    assert [item.claim_draft_id for item in projection_b.claims] == [claim_b["claim"]]
    assert projection_a.project_id == claim_a["project"]
    assert projection_b.project_id == claim_b["project"]
    assert projection_a.projection_fingerprint != projection_b.projection_fingerprint


def test_revoked_authorization_projects_empty_without_fallback(pack_schema):
    conn, _ = pack_schema
    tag = "present-revoke"
    claim, evidence, _, _ = _prepare_service_authorization(conn, tag)
    populated = _project(conn, claim["project"], "client_report")
    assert populated.counts.relationship_count == 1
    pack_service.record_usage_authorization_decision(
        conn,
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            project_id=claim["project"], claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"], usage_scope="client_report",
            decision="revoked", reason="No longer authorized", actor="operator",
            request_id=f"{tag}-revocation",
        ),
    )
    revoked = _project(conn, claim["project"], "client_report")
    assert revoked.relationships == ()
    assert revoked.claims == revoked.sources == revoked.evidence == ()
    assert revoked.counts.relationship_count == 0
    assert revoked.projection_fingerprint != populated.projection_fingerprint
    assert revoked == _project(conn, claim["project"], "client_report")


def test_stale_annotation_and_stale_review_do_not_resurrect(pack_schema):
    conn, _ = pack_schema
    claim, _, _, _ = _prepare_service_authorization(conn, "present-stale-annotation")
    assert _project(
        conn, claim["project"], "client_report",
    ).counts.relationship_count == 1
    _annotate_claim(
        conn, claim["project"], claim["claim"], "present-stale-annotation-2",
    )
    stale = _project(conn, claim["project"], "client_report")
    assert stale.relationships == ()
    assert stale.counts.relationship_count == 0

    other_claim, other_evidence, _, _ = _prepare_service_authorization(
        conn, "present-stale-review",
    )
    assert _project(
        conn, other_claim["project"], "client_report",
    ).counts.relationship_count == 1
    conn.execute(
        """INSERT INTO research_evidence_intake_item_review_decision
           (project_id,research_evidence_intake_item_id,decision_type,
            decision_reason,decided_by,request_id)
           VALUES(%s,%s,'rejected','No longer approved','operator',%s)""",
        (
            other_claim["project"], other_evidence["item"],
            "present-stale-review-reject",
        ),
    )
    rejected = _project(conn, other_claim["project"], "client_report")
    assert rejected.relationships == ()
    assert rejected.counts.relationship_count == 0


def test_retention_redaction_exclusion_is_inherited(pack_schema):
    conn, _ = pack_schema
    claim, evidence, _, _ = _prepare_service_authorization(conn, "present-redact")
    assert _project(
        conn, claim["project"], "client_report",
    ).counts.relationship_count == 1
    conn.execute(
        """INSERT INTO evidence_retention_event
           (project_id, event_type, source_snapshot_id, reason, created_by)
           VALUES (%s, 'redact', %s, 'Retention redaction', 'operator')""",
        (claim["project"], evidence["snapshot"]),
    )
    redacted = _project(conn, claim["project"], "client_report")
    assert redacted.relationships == ()
    assert redacted.counts.relationship_count == 0


def test_projection_is_read_only_and_preserves_caller_transaction(pack_schema):
    conn, _ = pack_schema
    claim, _, _, _ = _prepare_service_authorization(conn, "present-readonly")

    def observed_row_counts():
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in OBSERVED_TABLES
        }

    before = observed_row_counts()
    assert conn.autocommit is False
    for scope in SCOPES:
        _project(conn, claim["project"], scope)
    assert observed_row_counts() == before
    assert conn.info.transaction_status.name == "INTRANS"
    assert conn.execute("SELECT 1").fetchone() == (1,)
    _annotate_claim(
        conn, claim["project"], claim["claim"], "present-readonly-postwrite",
    )
    assert observed_row_counts() != before


def test_disabled_gate_fails_closed_before_reading(pack_schema, monkeypatch):
    conn, _ = pack_schema
    claim, _, _, _ = _prepare_service_authorization(conn, "present-disabled")
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "false")
    with pytest.raises(
        presentation.ResearchEvidencePresentationProjectionDisabled
    ):
        _project(conn, claim["project"], "client_report")
