"""Disposable-PostgreSQL coverage for the bounded candidate-fact service.

Proves the wrapper against a genuine v47 schema: it binds a validated fact to an
existing same-project snapshot, rejects a foreign-project snapshot, preserves
caller transaction ownership (rollback discards it; the service never commits),
and appends rather than mutates. Skips unless TEST_EVIDENCE_PG_DSN is set.
"""
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import fact_service  # noqa: E402
from knowledge.evidence_snapshot.validation import (  # noqa: E402
    FactValidationError,
    ValidatedFact,
    validate_fact,
)


@pytest.fixture(autouse=True)
def snapshot_enabled(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")


@pytest.fixture
def schema():
    import psycopg

    conn = pg.connect()
    with pg.fresh_schema(conn) as schema_name:
        # The service now requires an EXPLICITLY pinned READ COMMITTED level;
        # pin it here (no active transaction) before invoking the service.
        conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        yield conn, schema_name
    conn.close()


def _project_with_snapshot(conn, tag):
    project_id = pg.insert_project(conn, name=f"fact-{tag}")
    blob = ev_blob(conn, project_id, tag)
    snapshot = pg_snapshot(conn, project_id, blob, tag)
    return project_id, snapshot


def _project_with_snapshot_and_blob(conn, tag):
    project_id = pg.insert_project(conn, name=f"fact-{tag}")
    blob = ev_blob(conn, project_id, tag)
    snapshot = pg_snapshot(conn, project_id, blob, tag)
    return project_id, snapshot, blob


def _append_retention(conn, *, project_id, event_type, snapshot=None, blob=None):
    from knowledge.evidence_snapshot import repository as ev_repo

    return ev_repo.insert_retention_event(
        conn, project_id=project_id, event_type=event_type,
        source_snapshot_id=snapshot, source_blob_id=blob,
        reason="", created_by="op",
    )


def ev_blob(conn, project_id, tag):
    from knowledge.evidence_snapshot import repository as ev_repo

    return ev_repo.insert_or_get_blob(
        conn, project_id=project_id,
        content_hash=f"{tag}-{uuid.uuid4().hex}", byte_size=17,
    )


def pg_snapshot(conn, project_id, blob, tag):
    from knowledge.evidence_snapshot import repository as ev_repo

    return ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id,
        storage_ref=f"/facts/{tag}/{uuid.uuid4().hex}",
    )


def _count_facts(conn, project_id):
    return conn.execute(
        "SELECT count(*) FROM candidate_fact_revision WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0]


def test_creates_fact_bound_to_same_project_snapshot(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "bind")
    fact_id = fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=validate_fact("count", value=11, counted_entity="records"),
        created_by="op",
    )
    row = conn.execute(
        """SELECT source_snapshot_id::text, fact_type, numeric_value
           FROM candidate_fact_revision WHERE id = %s""",
        (fact_id,),
    ).fetchone()
    assert row[0] == snapshot and row[1] == "count"


def test_rejects_foreign_project_snapshot(schema):
    conn, _ = schema
    project_a, snapshot_a = _project_with_snapshot(conn, "a")
    project_b = pg.insert_project(conn, name="fact-b")
    with pytest.raises(fact_service.CandidateFactSourceSnapshotNotFound):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_b, source_snapshot_id=snapshot_a,
            fact=validate_fact("count", value=1, counted_entity="x"),
        )
    conn.rollback()


def test_caller_rollback_discards_fact_service_never_commits(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "rollback")
    conn.commit()
    assert _count_facts(conn, project_id) == 0

    fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=validate_fact("count", value=5, counted_entity="rows"),
    )
    # the service did not commit; a caller rollback discards the fact entirely
    conn.rollback()
    assert _count_facts(conn, project_id) == 0


def test_appends_multiple_facts_on_commit(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "append")
    for value in (1, 2, 3):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=snapshot,
            fact=validate_fact("count", value=value, counted_entity="rows"),
        )
    conn.commit()
    assert _count_facts(conn, project_id) == 3


def test_rejects_missing_snapshot_in_project(schema):
    # Regression: an existence miss (not availability) still raises NotFound —
    # snapshot_available's NOT EXISTS would spuriously say "available" here.
    conn, _ = schema
    project_id = pg.insert_project(conn, name="fact-missing")
    conn.commit()
    with pytest.raises(fact_service.CandidateFactSourceSnapshotNotFound):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=str(uuid.uuid4()),
            fact=validate_fact("count", value=1, counted_entity="x"),
        )
    conn.rollback()
    assert _count_facts(conn, project_id) == 0


# ─── REVIEW FINDING 4: reject facts from canonically UNAVAILABLE snapshots ───
#
# Canonical v47 (repository.snapshot_available) treats tombstone/redact on the
# snapshot OR its blob as blocking; legal_hold does NOT. The wrapper must not
# create a NEW candidate_fact_revision from a retained source. Existence is
# checked FIRST (a missing/foreign snapshot stays NotFound), then availability.


@pytest.mark.parametrize("event_type", ["tombstone", "redact"])
def test_rejects_snapshot_target_retention(schema, event_type):
    # A (tombstone) + B (redact): retention event targets the SNAPSHOT.
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, f"snap-{event_type}")
    _append_retention(
        conn, project_id=project_id, event_type=event_type, snapshot=snapshot
    )
    conn.commit()
    before = _count_facts(conn, project_id)
    with pytest.raises(fact_service.CandidateFactSourceSnapshotUnavailable):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=snapshot,
            fact=validate_fact("count", value=7, counted_entity="rows"),
        )
    conn.commit()  # nothing to persist; prove no fact leaked through
    assert _count_facts(conn, project_id) == before == 0


@pytest.mark.parametrize("event_type", ["tombstone", "redact"])
def test_rejects_blob_target_retention(schema, event_type):
    # C (tombstone) + D (redact): retention event targets the underlying BLOB.
    conn, _ = schema
    project_id, snapshot, blob = _project_with_snapshot_and_blob(
        conn, f"blob-{event_type}"
    )
    _append_retention(
        conn, project_id=project_id, event_type=event_type, blob=blob
    )
    conn.commit()
    before = _count_facts(conn, project_id)
    with pytest.raises(fact_service.CandidateFactSourceSnapshotUnavailable):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=snapshot,
            fact=validate_fact("count", value=9, counted_entity="rows"),
        )
    conn.commit()
    assert _count_facts(conn, project_id) == before == 0


@pytest.mark.parametrize("target", ["snapshot", "blob"])
def test_legal_hold_does_not_block_fact_creation(schema, target):
    # E (mandatory control): legal_hold is NOT a blocking event — the snapshot
    # stays canonically available and a valid fact still succeeds. Guards against
    # treating every retention event as blocking.
    conn, _ = schema
    project_id, snapshot, blob = _project_with_snapshot_and_blob(
        conn, f"hold-{target}"
    )
    _append_retention(
        conn, project_id=project_id, event_type="legal_hold",
        snapshot=snapshot if target == "snapshot" else None,
        blob=blob if target == "blob" else None,
    )
    conn.commit()

    from knowledge.evidence_snapshot import repository as ev_repo
    assert ev_repo.snapshot_available(conn, snapshot) is True

    fact_id = fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=validate_fact("count", value=13, counted_entity="rows"),
        created_by="op",
    )
    conn.commit()
    assert _count_facts(conn, project_id) == 1
    assert fact_id


# ─────────────── P2-A: non-finite numeric facts rejected (real PG) ───────────────
#
# PostgreSQL NUMERIC stores NaN/±Infinity, so the database cannot be relied on to
# reject them. Prove the service rejects a directly-constructed non-finite fact
# for every numeric profile, with FactValidationError and NO row inserted, even
# on a real v47 schema and even when the caller then commits.


def _non_finite_profile(fact_type, value):
    if fact_type == "money":
        return ValidatedFact(
            fact_type="money", numeric_value=value,
            currency_code="USD", as_of_date=date(2026, 1, 1),
        )
    if fact_type == "rate":
        return ValidatedFact(
            fact_type="rate", numeric_value=value,
            numerator_context="defects", denominator_context="units",
        )
    if fact_type == "percentage":
        return ValidatedFact(
            fact_type="percentage", numeric_value=value,
            percentage_basis="qoq", percentage_subtype="change",
        )
    if fact_type == "duration":
        return ValidatedFact(
            fact_type="duration", numeric_value=value, time_unit="days",
        )
    return ValidatedFact(
        fact_type="count", numeric_value=value, counted_entity="records",
    )


@pytest.mark.parametrize("fact_type", ["money", "rate", "percentage", "duration", "count"])
@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_rejects_non_finite_numeric_fact_no_row_inserted(schema, fact_type, value):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "nonfinite")
    conn.commit()
    assert _count_facts(conn, project_id) == 0
    with pytest.raises(FactValidationError) as exc:
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=snapshot,
            fact=_non_finite_profile(fact_type, value), created_by="op",
        )
    assert str(exc.value) == "numeric candidate facts must be finite"
    # Even if the caller commits, no non-finite fact ever reached the table.
    conn.commit()
    assert _count_facts(conn, project_id) == 0

    # A finite control on the same profile still persists.
    good = validate_fact(
        fact_type,
        value=Decimal("1"),
        currency_code="USD" if fact_type == "money" else None,
        as_of_date=date(2026, 1, 1) if fact_type == "money" else None,
        numerator_context="defects" if fact_type == "rate" else None,
        denominator_context="units" if fact_type == "rate" else None,
        percentage_basis="qoq" if fact_type == "percentage" else None,
        percentage_subtype="change" if fact_type == "percentage" else None,
        time_unit="days" if fact_type == "duration" else None,
        counted_entity="records" if fact_type == "count" else None,
    )
    fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=good, created_by="op",
    )
    conn.commit()
    assert _count_facts(conn, project_id) == 1
