"""Repository/service tests for the R1.1 research-evidence sidecar."""
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402
from research_evidence import repository as repo, service  # noqa: E402
from research_evidence.models import (  # noqa: E402
    ClaimDraftCreate,
    FactMetadataRevisionCreate,
    SourceMetadataRevisionCreate,
)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_r51(conn):
    with pg.fresh_schema(conn) as s:
        pg.apply_v51_research(conn)
        yield s


@pytest.fixture(autouse=True)
def sidecar_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_snapshot_fact(conn, project_id: str):
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=f"hash-{uuid.uuid4().hex}",
        byte_size=12,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/sidecar/{uuid.uuid4().hex}",
    )
    fact_id = ev_repo.insert_fact(
        conn,
        project_id=project_id,
        source_snapshot_id=snapshot_id,
        fact=validate_fact("count", value=7, counted_entity="records"),
    )
    conn.commit()
    return snapshot_id, fact_id


def test_source_metadata_requires_existing_same_project_snapshot(conn, schema_r51):
    project_a = pg.insert_project(conn, name="project-a")
    project_b = pg.insert_project(conn, name="project-b")
    conn.commit()
    snapshot_a, _fact_a = _seed_snapshot_fact(conn, project_a)

    with pytest.raises(repo.SidecarParentNotFound):
        repo.insert_source_metadata_revision(
            conn,
            SourceMetadataRevisionCreate(
                project_id=project_b,
                source_snapshot_id=snapshot_a,
            ),
        )


def test_fact_metadata_requires_existing_same_project_fact(conn, schema_r51):
    project_a = pg.insert_project(conn, name="project-a")
    project_b = pg.insert_project(conn, name="project-b")
    conn.commit()
    _snapshot_a, fact_a = _seed_snapshot_fact(conn, project_a)

    with pytest.raises(repo.SidecarParentNotFound):
        repo.insert_fact_metadata_revision(
            conn,
            FactMetadataRevisionCreate(
                project_id=project_b,
                candidate_fact_revision_id=fact_a,
            ),
        )


def test_service_source_metadata_creates_event_in_same_transaction(conn, schema_r51):
    project_id = pg.insert_project(conn, name="source-service")
    conn.commit()
    snapshot_id, _fact_id = _seed_snapshot_fact(conn, project_id)

    record = service.create_source_metadata_revision(
        conn,
        SourceMetadataRevisionCreate(
            project_id=project_id,
            source_snapshot_id=snapshot_id,
            canonical_source_locator="doc#source",
            publisher="Operator-declared publisher",
            created_by="operator",
        ),
    )
    events = repo.list_events(
        conn,
        project_id=project_id,
        entity_type="source_metadata_revision",
        entity_id=record.id,
    )
    assert len(events) == 1
    assert events[0].event_type == "created"
    assert events[0].event_sequence == 1
    conn.rollback()

    assert repo.list_source_metadata_revisions(conn, project_id=project_id) == []
    assert repo.list_events(conn, project_id=project_id) == []


def test_fact_metadata_corrections_create_superseding_rows(conn, schema_r51):
    project_id = pg.insert_project(conn, name="fact-correction")
    conn.commit()
    _snapshot_id, fact_id = _seed_snapshot_fact(conn, project_id)

    first = service.create_fact_metadata_revision(
        conn,
        FactMetadataRevisionCreate(
            project_id=project_id,
            candidate_fact_revision_id=fact_id,
            stable_fact_key="metric-a",
            citation_locator="doc#row=1",
            created_by="operator",
        ),
    )
    second = service.create_fact_metadata_revision(
        conn,
        FactMetadataRevisionCreate(
            project_id=project_id,
            candidate_fact_revision_id=fact_id,
            stable_fact_key="metric-a-corrected",
            supersedes_metadata_revision_id=first.id,
            created_by="operator",
        ),
    )
    conn.commit()

    revisions = repo.list_fact_metadata_revisions(
        conn,
        project_id=project_id,
        candidate_fact_revision_id=fact_id,
    )
    assert [r.id for r in revisions] == [first.id, second.id]
    assert revisions[0].stable_fact_key == "metric-a"
    assert revisions[1].supersedes_metadata_revision_id == first.id
    events = repo.list_events(conn, project_id=project_id)
    assert [e.event_type for e in events] == ["created", "correction_recorded"]


def test_claims_remain_isolated_and_events_are_sequenced(conn, schema_r51):
    project_id = pg.insert_project(conn, name="claim-service")
    conn.commit()

    first = service.create_claim_draft(
        conn,
        ClaimDraftCreate(
            project_id=project_id,
            claim_text="Draft claim",
            claim_category="research",
            created_by="operator",
        ),
    )
    repo.insert_event(
        conn,
        project_id=project_id,
        entity_type="claim_draft",
        entity_id=first.id,
        event_type="withdrawn",
        actor="operator",
    )
    second = service.create_claim_draft(
        conn,
        ClaimDraftCreate(
            project_id=project_id,
            claim_text="Replacement draft claim",
            claim_category="research",
            supersedes_claim_id=first.id,
            created_by="operator",
        ),
    )
    conn.commit()

    claims = repo.list_claim_drafts(conn, project_id=project_id)
    assert [c.id for c in claims] == [first.id, second.id]
    assert claims[1].supersedes_claim_id == first.id
    first_events = repo.list_events(
        conn,
        project_id=project_id,
        entity_type="claim_draft",
        entity_id=first.id,
    )
    assert [e.event_sequence for e in first_events] == [1, 2]
    assert [e.event_type for e in first_events] == ["created", "withdrawn"]
