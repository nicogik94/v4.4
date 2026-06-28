"""PostgreSQL contract tests for R1.5 pair-scoped claim support."""
import concurrent.futures
import sys
import threading
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402
from research_evidence import claim_support_service as service  # noqa: E402
from research_evidence.claim_support_models import (  # noqa: E402
    ResearchEvidenceClaimSupportAssessmentCreate,
)


TABLES = (
    "research_evidence_claim_support_assessment",
    "research_evidence_claim_support_sequence_allocator",
)
CONSTRAINTS = (
    "research_evidence_claim_support_assessment_pkey",
    "uq_recsa_id_project_pair",
    "uq_recsa_pair_sequence",
    "uq_recsa_pair_request",
    "uq_recsa_supersedes_once",
    "fk_recsa_claim_item_project",
    "fk_recsa_evidence_item_project",
    "fk_recsa_supersedes_same_pair",
    "fk_recsa_claim_draft_project",
    "fk_recsa_claim_source_metadata_snapshot",
    "fk_recsa_evidence_source_metadata_snapshot",
    "fk_recsa_fact_metadata_fact",
    "ck_recsa_locator_resolution",
    "ck_recsa_evidence_linkage",
    "ck_recsa_semantic_relationship",
    "research_evidence_claim_support_sequence_allocator_pkey",
)


class BarrierBeforeClaimSupportInsert:
    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier
        self._waiting = True

    def execute(self, sql, params=None):
        if (
            self._waiting
            and "INSERT INTO research_evidence_claim_support_assessment" in sql
        ):
            self._waiting = False
            self._barrier.wait()
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_v56(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_endpoint(
    conn,
    *,
    project_id=None,
    kind,
    tag,
):
    project_id = project_id or pg.insert_project(
        conn, name=f"claim-support-{tag}"
    )
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=f"claim-support-{tag}-{uuid.uuid4().hex}",
        byte_size=23,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/r1.5/{tag}/{uuid.uuid4().hex}",
    )
    source_metadata_id = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, canonical_source_locator,
             citation_label, created_by)
        VALUES (%s, %s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (
            project_id,
            snapshot_id,
            f"stored://{tag}",
            f"source-{tag}",
        ),
    ).fetchone()[0]
    intake_id = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Operator selected existing evidence', 'operator')
        RETURNING id::text
        """,
        (project_id, snapshot_id, source_metadata_id),
    ).fetchone()[0]
    result = {
        "project": project_id,
        "blob": blob_id,
        "snapshot": snapshot_id,
        "source_metadata": source_metadata_id,
        "intake": intake_id,
        "kind": kind,
    }
    if kind == "claim_draft":
        claim_id = conn.execute(
            """
            INSERT INTO research_claim_draft
                (project_id, claim_text, created_by)
            VALUES (%s, %s, 'operator')
            RETURNING id::text
            """,
            (project_id, f"Draft claim {tag}"),
        ).fetchone()[0]
        item_id = conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, claim_draft_id, created_by)
            VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
            RETURNING id::text
            """,
            (project_id, intake_id, snapshot_id, claim_id),
        ).fetchone()[0]
        result.update({"claim": claim_id, "item": item_id})
    else:
        fact_id = ev_repo.insert_fact(
            conn,
            project_id=project_id,
            source_snapshot_id=snapshot_id,
            fact=validate_fact("count", value=11, counted_entity="records"),
            created_by="operator",
        )
        fact_metadata_id = conn.execute(
            """
            INSERT INTO research_fact_metadata_revision
                (project_id, candidate_fact_revision_id, citation_locator,
                 source_char_range, excerpt_hash, created_by)
            VALUES (%s, %s, %s, '10-20', %s, 'operator')
            RETURNING id::text
            """,
            (project_id, fact_id, f"section-{tag}", f"excerpt-{tag}"),
        ).fetchone()[0]
        item_id = conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id,
                 fact_metadata_revision_id, created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
            RETURNING id::text
            """,
            (
                project_id,
                intake_id,
                snapshot_id,
                fact_id,
                fact_metadata_id,
            ),
        ).fetchone()[0]
        result.update(
            {
                "fact": fact_id,
                "fact_metadata": fact_metadata_id,
                "item": item_id,
            }
        )
    return result


def _seed_pair(conn, *, tag="pair"):
    claim = _seed_endpoint(conn, kind="claim_draft", tag=f"{tag}-claim")
    evidence = _seed_endpoint(
        conn,
        project_id=claim["project"],
        kind="candidate_fact",
        tag=f"{tag}-evidence",
    )
    assert claim["intake"] != evidence["intake"]
    return claim, evidence


def _command(claim, evidence, *, request_id="request-1", **changes):
    values = {
        "project_id": claim["project"],
        "claim_intake_item_id": claim["item"],
        "evidence_intake_item_id": evidence["item"],
        "request_id": request_id,
        "locator_resolution": "resolvable",
        "locator_rationale": "Stored locator was reviewed.",
        "evidence_linkage": "linked",
        "evidence_linkage_rationale": "The evidence item is the intended link.",
        "semantic_relationship": "support",
        "semantic_relationship_rationale": "Operator assessed supporting context.",
        "assessed_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentCreate(**values)


def _direct_insert(conn, claim, evidence, *, request_id="request-1", **changes):
    values = {
        "locator_resolution": "resolvable",
        "locator_rationale": "Locator reviewed",
        "evidence_linkage": "linked",
        "evidence_linkage_rationale": "Link reviewed",
        "semantic_relationship": "support",
        "semantic_relationship_rationale": "Relationship reviewed",
        "assessed_by": "operator",
    }
    values.update(changes)
    return conn.execute(
        """
        INSERT INTO research_evidence_claim_support_assessment
            (project_id, claim_intake_item_id, evidence_intake_item_id,
             request_id, locator_resolution, locator_rationale,
             evidence_linkage, evidence_linkage_rationale,
             semantic_relationship, semantic_relationship_rationale,
             assessed_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text, assessment_sequence,
                  supersedes_assessment_id::text, claim_draft_id::text,
                  claim_source_snapshot_id::text, claim_source_blob_id::text,
                  claim_source_metadata_revision_id::text,
                  evidence_source_snapshot_id::text,
                  evidence_source_blob_id::text,
                  evidence_source_metadata_revision_id::text,
                  candidate_fact_revision_id::text,
                  fact_metadata_revision_id::text, assessed_at
        """,
        (
            claim["project"],
            claim["item"],
            evidence["item"],
            request_id,
            values["locator_resolution"],
            values["locator_rationale"],
            values["evidence_linkage"],
            values["evidence_linkage_rationale"],
            values["semantic_relationship"],
            values["semantic_relationship_rationale"],
            values["assessed_by"],
        ),
    ).fetchone()


def test_v56_objects_constraints_triggers_and_reapply(conn, schema_v56):
    for table in TABLES:
        assert pg.table_exists(conn, schema_v56, table)
    present = conn.execute(
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND conname = ANY(%s)
        """,
        (list(CONSTRAINTS),),
    ).fetchall()
    assert {row[0] for row in present} == set(CONSTRAINTS)
    triggers = conn.execute(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND NOT tgisinternal
        """
    ).fetchall()
    assert set(triggers) == {
        ("trg_recsa_prepare_insert", "A"),
        ("trg_recsa_no_mutation", "O"),
    }
    pg.apply_v56_research_claim_support(conn)


def test_cross_intake_same_project_pair_is_accepted_and_server_derived(
    conn, schema_v56
):
    claim, evidence = _seed_pair(conn, tag="valid")
    first = _direct_insert(conn, claim, evidence, request_id="one")
    second = _direct_insert(
        conn,
        claim,
        evidence,
        request_id="two",
        evidence_linkage="not_linked",
        evidence_linkage_rationale="The later assessment retires the link.",
        semantic_relationship="not_assessed",
        semantic_relationship_rationale="No semantic assessment after unlinking.",
    )
    assert first[1:3] == (1, None)
    assert first[3:7] == (
        claim["claim"],
        claim["snapshot"],
        claim["blob"],
        claim["source_metadata"],
    )
    assert first[7:12] == (
        evidence["snapshot"],
        evidence["blob"],
        evidence["source_metadata"],
        evidence["fact"],
        evidence["fact_metadata"],
    )
    assert second[1] == 2
    assert second[2] == first[0]
    assert second[12] >= first[12]
    assert conn.execute(
        """
        SELECT evidence_linkage
        FROM research_evidence_claim_support_assessment
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        ORDER BY assessment_sequence
        """,
        (claim["project"], claim["item"], evidence["item"]),
    ).fetchall() == [("linked",), ("not_linked",)]


def test_cross_project_and_reversed_endpoint_kinds_are_rejected(conn, schema_v56):
    claim, evidence = _seed_pair(conn, tag="scope")
    foreign = _seed_endpoint(
        conn, kind="candidate_fact", tag="scope-foreign"
    )
    conn.commit()
    with pytest.raises(Exception, match="candidate-fact intake item not found"):
        _direct_insert(conn, claim, foreign)
    conn.rollback()
    with pytest.raises(Exception, match="claim-draft intake item not found"):
        _direct_insert(
            conn,
            evidence,
            claim,
        )
    conn.rollback()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("assessment_sequence", 4),
        ("supersedes_assessment_id", "00000000-0000-0000-0000-000000000001"),
        ("claim_draft_id", "00000000-0000-0000-0000-000000000001"),
        (
            "candidate_fact_revision_id",
            "00000000-0000-0000-0000-000000000001",
        ),
        ("assessed_at", "2026-01-01T00:00:00Z"),
    ],
)
def test_server_owned_fields_are_rejected(conn, schema_v56, column, value):
    claim, evidence = _seed_pair(conn, tag=f"owned-{column}")
    with pytest.raises(Exception, match="server-assigned"):
        conn.execute(
            f"""
            INSERT INTO research_evidence_claim_support_assessment
                (project_id, claim_intake_item_id, evidence_intake_item_id,
                 request_id, locator_resolution, locator_rationale,
                 evidence_linkage, evidence_linkage_rationale,
                 semantic_relationship, semantic_relationship_rationale,
                 assessed_by, {column})
            VALUES (%s, %s, %s, 'owned', 'resolvable', 'reviewed',
                    'linked', 'reviewed', 'support', 'reviewed',
                    'operator', %s)
            """,
            (claim["project"], claim["item"], evidence["item"], value),
        )
    conn.rollback()


def test_dimensions_are_independent_and_not_semantic_proof(conn, schema_v56):
    claim, evidence = _seed_pair(conn, tag="dimensions")
    record = service.record_claim_support_assessment(
        conn,
        _command(
            claim,
            evidence,
            locator_resolution="unresolvable",
            evidence_linkage="linked",
            semantic_relationship="contradiction",
        ),
    )
    assert record.locator_resolution == "unresolvable"
    assert record.evidence_linkage == "linked"
    assert record.semantic_relationship == "contradiction"
    kwargs = {
        "project_id": claim["project"],
        "claim_intake_item_id": claim["item"],
        "evidence_intake_item_id": evidence["item"],
    }
    assert service.claim_support_locator_resolution(conn, **kwargs) == "unresolvable"
    assert service.claim_support_evidence_linkage(conn, **kwargs) == "linked"
    assert (
        service.claim_support_semantic_relationship(conn, **kwargs)
        == "contradiction"
    )


def test_history_is_append_only(conn, schema_v56):
    claim, evidence = _seed_pair(conn, tag="immutable")
    row = _direct_insert(conn, claim, evidence)
    conn.commit()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            """
            UPDATE research_evidence_claim_support_assessment
            SET semantic_relationship = 'qualification'
            WHERE id = %s
            """,
            (row[0],),
        )
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "DELETE FROM research_evidence_claim_support_assessment WHERE id = %s",
            (row[0],),
        )
    conn.rollback()


def test_request_retry_is_idempotent_and_payload_mismatch_conflicts(
    conn, schema_v56
):
    claim, evidence = _seed_pair(conn, tag="retry")
    command = _command(claim, evidence)
    first = service.record_claim_support_assessment(conn, command)
    second = service.record_claim_support_assessment(conn, command)
    assert second == first
    with pytest.raises(Exception, match="different immutable"):
        service.record_claim_support_assessment(
            conn,
            _command(
                claim,
                evidence,
                semantic_relationship="qualification",
            ),
        )
    assert conn.execute(
        """
        SELECT count(*)
        FROM research_evidence_claim_support_assessment
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        """,
        (claim["project"], claim["item"], evidence["item"]),
    ).fetchone()[0] == 1


def test_v56_reapply_detects_allocator_drift(conn, schema_v56):
    claim, evidence = _seed_pair(conn, tag="allocator-drift")
    _direct_insert(conn, claim, evidence)
    conn.commit()
    conn.execute(
        """
        UPDATE research_evidence_claim_support_sequence_allocator
        SET last_sequence = 2
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        """,
        (claim["project"], claim["item"], evidence["item"]),
    )
    conn.commit()
    with pytest.raises(Exception, match="allocator diverges from pair history"):
        pg.apply_v56_research_claim_support(conn)


def test_v56_reapply_detects_altered_prepare_function(conn, schema_v56):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION research_evidence_prepare_claim_support_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ BEGIN RETURN NEW; END $$
        """
    )
    conn.execute(
        "REVOKE ALL ON FUNCTION "
        "research_evidence_prepare_claim_support_insert() FROM PUBLIC"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent claim-support prepare function"):
        pg.apply_v56_research_claim_support(conn)


def test_v56_reapply_detects_altered_check(conn, schema_v56):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_claim_support_assessment "
        "DROP CONSTRAINT ck_recsa_locator_resolution"
    )
    conn.execute(
        "ALTER TABLE research_evidence_claim_support_assessment "
        "ADD CONSTRAINT ck_recsa_locator_resolution "
        "CHECK (locator_resolution <> '')"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent claim-support check"):
        pg.apply_v56_research_claim_support(conn)


def test_v56_reapply_detects_altered_index(conn, schema_v56):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP INDEX idx_recsa_pair_sequence")
    conn.execute(
        "CREATE INDEX idx_recsa_pair_sequence "
        "ON research_evidence_claim_support_assessment(project_id)"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent claim-support index"):
        pg.apply_v56_research_claim_support(conn)


@pytest.mark.parametrize(
    ("grant_sql", "error"),
    [
        (
            "GRANT SELECT ON "
            "research_evidence_claim_support_sequence_allocator TO PUBLIC",
            "allocator has PUBLIC privileges",
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "research_evidence_prepare_claim_support_insert() TO PUBLIC",
            "divergent claim-support prepare function",
        ),
    ],
)
def test_v56_reapply_detects_privilege_drift(
    conn, schema_v56, grant_sql, error
):
    prior = pg._begin_autocommit(conn)
    conn.execute(grant_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match=error):
        pg.apply_v56_research_claim_support(conn)


def test_two_connections_produce_contiguous_pair_chain(conn, schema_v56):
    claim, evidence = _seed_pair(conn, tag="concurrent-distinct")
    conn.commit()
    barrier = threading.Barrier(2)

    def append(request_id):
        worker = pg.connect(schema=schema_v56)
        try:
            wrapped = BarrierBeforeClaimSupportInsert(worker, barrier)
            record = service.record_claim_support_assessment(
                wrapped,
                _command(claim, evidence, request_id=request_id),
            )
            worker.commit()
            return record
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(append, ("concurrent-1", "concurrent-2")))
    assert sorted(record.assessment_sequence for record in records) == [1, 2]
    rows = conn.execute(
        """
        SELECT id::text, assessment_sequence, supersedes_assessment_id::text
        FROM research_evidence_claim_support_assessment
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        ORDER BY assessment_sequence
        """,
        (claim["project"], claim["item"], evidence["item"]),
    ).fetchall()
    assert rows[0][1:] == (1, None)
    assert rows[1][1:] == (2, rows[0][0])


def test_two_connections_same_request_return_one_immutable_row(
    conn, schema_v56
):
    claim, evidence = _seed_pair(conn, tag="concurrent-retry")
    conn.commit()
    barrier = threading.Barrier(2)

    def append():
        worker = pg.connect(schema=schema_v56)
        try:
            wrapped = BarrierBeforeClaimSupportInsert(worker, barrier)
            record = service.record_claim_support_assessment(
                wrapped,
                _command(claim, evidence, request_id="same-request"),
            )
            worker.commit()
            return record
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda _: append(), range(2)))
    assert records[0] == records[1]
    assert records[0].assessment_sequence == 1
    assert conn.execute(
        """
        SELECT count(*), min(assessment_sequence), max(assessment_sequence)
        FROM research_evidence_claim_support_assessment
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        """,
        (claim["project"], claim["item"], evidence["item"]),
    ).fetchone() == (1, 1, 1)
