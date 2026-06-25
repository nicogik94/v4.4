"""Agent Blueprint Studio S1 — PostgreSQL schema, migration, and persisted-mode tests.

Run against a disposable PostgreSQL database (TEST_STUDIO_PG_DSN or
TEST_EVIDENCE_PG_DSN), using ephemeral schemas dropped on exit. Skipped when no DSN
is provided. The authoritative MAS database is never touched.
"""
import contextlib
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.agent_blueprint_pg as pg  # noqa: E402
import agent_blueprint_studio.repository as repo  # noqa: E402
from agent_blueprint_studio.models import (  # noqa: E402
    CuratedExtract,
    DraftArtifact,
    PersistenceMode,
    compute_extract_fingerprint,
)

REQUIRED_CONSTRAINTS = (
    "uq_baib_artifact_extract",
    "fk_baib_artifact_project",
    "fk_baib_extract_project",
    "uq_bse_id_project",
    "uq_ba_id_project",
    "ck_ba_assurance_draft_only",
)


@pytest.fixture
def conn():
    pg.require_dsn()
    c = pg.connect()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def schema_s(conn):
    with pg.fresh_schema(conn) as s:
        yield s


# ── Migration ─────────────────────────────────────────────────────────────────

def test_fresh_apply_creates_complete_studio(conn, schema_s):
    assert pg.studio_tables_present(conn, schema_s) == 11
    for name in REQUIRED_CONSTRAINTS:
        assert pg.constraint_exists(conn, schema_s, name), name
    # Wave 1 adds NO append-only triggers.
    assert pg.trigger_count(conn, schema_s) == 0


def test_complete_reapply_is_noop(conn, schema_s):
    pg.apply_v50(conn)  # must not raise
    assert pg.studio_tables_present(conn, schema_s) == 11


def test_partial_schema_is_rejected(conn, schema_s):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE blueprint_artifact_input_binding")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v50(conn)
    assert "partial/divergent" in str(ei.value) or "contract violation" in str(ei.value)


def test_requires_base_schema(conn):
    schema = f"studio_nobase_{uuid.uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        with pytest.raises(Exception) as ei:
            pg._run_script(conn, pg.V50_SQL)  # no init.sql → no projects table
        assert "requires the base schema" in str(ei.value)
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg._restore_autocommit(conn, prior)


# ── Persisted mode + rights defaults (DB layer) ────────────────────────────────

def test_persisted_round_trip_and_rights_defaults(conn, schema_s):
    project = repo.pg_create_project(conn, name="Internal Strategy Agent", domain_label="neutral")
    conn.commit()
    assert project.persistence == PersistenceMode.PERSISTED

    fetched = repo.pg_get_project(conn, project.id)
    assert fetched is not None and fetched.name == "Internal Strategy Agent"

    item = repo.pg_add_source_item(conn, blueprint_project_id=project.id, title="manual source")
    conn.commit()
    r = item.rights
    assert (r.use_allowed, r.quote_allowed, r.export_allowed, r.external_processing_allowed) == (
        False, False, False, False,
    )
    assert r.permitted_audience == "operator_only"
    assert r.sensitivity_level == "restricted"
    assert r.retention_declaration == "undeclared_restricted"
    assert item.authority_tier == "unspecified"


def test_studio_tables_present_requires_complete_schema(conn, schema_s):
    # A complete v50 schema is ready for persisted drafts.
    assert repo.studio_tables_present(conn) is True
    # Dropping any single Studio table makes the schema partial/damaged → NOT ready,
    # so a new draft would fall back to sticky EPHEMERAL instead of persisting.
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE blueprint_draft_export")
    pg._restore_autocommit(conn, prior)
    assert repo.studio_tables_present(conn) is False
    conn.rollback()


# ── Many-row artifact-input binding ────────────────────────────────────────────

def test_many_row_artifact_input_binding(conn, schema_s):
    project = repo.pg_create_project(conn, name="Example Knowledge Project")
    rev = repo.pg_add_config_revision(conn, blueprint_project_id=project.id, revision_no=1)
    item = repo.pg_add_source_item(conn, blueprint_project_id=project.id, title="src")
    conn.commit()

    extracts = []
    for value in ("alpha", "beta", "gamma"):
        fp = compute_extract_fingerprint(
            extract_type="claim", text_value=value, numeric_value=None, unit="", as_of_date=None,
        )
        extracts.append(
            repo.pg_add_extract(
                conn,
                CuratedExtract(
                    id="", blueprint_project_id=project.id, source_item_id=item.id,
                    extract_type="claim", text_value=value, extract_content_fingerprint=fp,
                ),
            )
        )
    artifact = repo.pg_create_artifact(
        conn,
        DraftArtifact(
            id="", blueprint_project_id=project.id, config_revision_id=rev.id,
            content={"sections": []}, content_hash="deadbeef",
        ),
    )
    # input_order is operator-DECLARED and independent of creation/id order: bind in
    # REVERSE creation order so position 0 maps to the last-created extract.
    declared = list(reversed(extracts))
    for input_order, extract in enumerate(declared):
        repo.pg_bind_input(
            conn, blueprint_project_id=project.id, artifact_id=artifact.id,
            extract_id=extract.id, input_order=input_order,
            extract_content_fingerprint=extract.extract_content_fingerprint,
        )
    conn.commit()

    bindings = repo.pg_list_bindings(conn, artifact_id=artifact.id)
    assert len(bindings) == 3  # one row per included extract
    assert {b.extract_id for b in bindings} == {e.id for e in extracts}
    # Deterministic order: returned by declared input_order, not by id/insertion.
    assert [b.input_order for b in bindings] == [0, 1, 2]
    assert [b.extract_id for b in bindings] == [e.id for e in declared]


def test_cross_blueprint_binding_is_rejected(conn, schema_s):
    p1 = repo.pg_create_project(conn, name="bp1")
    p2 = repo.pg_create_project(conn, name="bp2")
    rev1 = repo.pg_add_config_revision(conn, blueprint_project_id=p1.id)
    item2 = repo.pg_add_source_item(conn, blueprint_project_id=p2.id, title="other")
    conn.commit()
    extract2 = repo.pg_add_extract(
        conn,
        CuratedExtract(
            id="", blueprint_project_id=p2.id, source_item_id=item2.id,
            extract_type="claim", text_value="x",
        ),
    )
    artifact1 = repo.pg_create_artifact(
        conn,
        DraftArtifact(id="", blueprint_project_id=p1.id, config_revision_id=rev1.id,
                      content={}, content_hash="h"),
    )
    conn.commit()
    # Binding p2's extract under p1 violates the project-consistent composite FK.
    with pytest.raises(Exception):
        repo.pg_bind_input(
            conn, blueprint_project_id=p1.id, artifact_id=artifact1.id,
            extract_id=extract2.id, input_order=0,
        )
        conn.commit()
    conn.rollback()


def _binding_fixture(conn):
    """A committed project + artifact + two claim extracts, for binding tests."""
    project = repo.pg_create_project(conn, name="binding project")
    rev = repo.pg_add_config_revision(conn, blueprint_project_id=project.id, revision_no=1)
    item = repo.pg_add_source_item(conn, blueprint_project_id=project.id, title="src")
    conn.commit()
    e1 = repo.pg_add_extract(
        conn,
        CuratedExtract(
            id="", blueprint_project_id=project.id, source_item_id=item.id,
            extract_type="claim", text_value="a",
        ),
    )
    e2 = repo.pg_add_extract(
        conn,
        CuratedExtract(
            id="", blueprint_project_id=project.id, source_item_id=item.id,
            extract_type="claim", text_value="b",
        ),
    )
    artifact = repo.pg_create_artifact(
        conn,
        DraftArtifact(
            id="", blueprint_project_id=project.id, config_revision_id=rev.id,
            content={}, content_hash="h",
        ),
    )
    conn.commit()
    return project, artifact, e1, e2


def test_binding_order_is_unique_per_artifact(conn, schema_s):
    project, artifact, e1, e2 = _binding_fixture(conn)
    repo.pg_bind_input(
        conn, blueprint_project_id=project.id, artifact_id=artifact.id,
        extract_id=e1.id, input_order=0,
    )
    conn.commit()
    # A different extract at the SAME declared position violates uq_baib_artifact_order.
    with pytest.raises(Exception):
        repo.pg_bind_input(
            conn, blueprint_project_id=project.id, artifact_id=artifact.id,
            extract_id=e2.id, input_order=0,
        )
        conn.commit()
    conn.rollback()


def test_binding_extract_is_unique_per_artifact(conn, schema_s):
    project, artifact, e1, _e2 = _binding_fixture(conn)
    repo.pg_bind_input(
        conn, blueprint_project_id=project.id, artifact_id=artifact.id,
        extract_id=e1.id, input_order=0,
    )
    conn.commit()
    # Re-binding the SAME extract (even at a new position) violates uq_baib_artifact_extract.
    with pytest.raises(Exception):
        repo.pg_bind_input(
            conn, blueprint_project_id=project.id, artifact_id=artifact.id,
            extract_id=e1.id, input_order=1,
        )
        conn.commit()
    conn.rollback()


# ── Draft-only project status (C3) ─────────────────────────────────────────────

def test_project_status_must_be_draft(conn, schema_s):
    # ck_bp_status_draft_only forbids any direct non-draft status.
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO blueprint_project (name, status) VALUES (%s, %s)",
            ("non-draft project", "released"),
        )
    conn.rollback()
    # Control: an explicit 'draft' status is accepted.
    conn.execute(
        "INSERT INTO blueprint_project (name, status) VALUES (%s, %s)",
        ("draft project", "draft"),
    )
    conn.commit()


# ── Extract shape contract (C4) ────────────────────────────────────────────────

def test_extract_shape_rejects_invalid_mixed_shapes(conn, schema_s):
    project = repo.pg_create_project(conn, name="shape project")
    item = repo.pg_add_source_item(conn, blueprint_project_id=project.id, title="src")
    conn.commit()

    def _insert(extract_type, text_value, numeric_value):
        conn.execute(
            """
            INSERT INTO blueprint_source_extract
                (blueprint_project_id, source_item_id, extract_type, text_value, numeric_value)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (project.id, item.id, extract_type, text_value, numeric_value),
        )

    invalid_shapes = [
        ("numeric", "fifty-two", Decimal("52")),  # numeric forbids text_value
        ("numeric", None, None),                  # numeric requires numeric_value
        ("claim", "ok", Decimal("1")),            # claim/quote/categorical forbid numeric_value
        ("claim", "", None),                      # ... and require a NON-EMPTY text_value
        ("claim", None, None),                    # ... and require text_value at all
        ("quote", None, Decimal("3")),            # quote: numeric forbidden, text required
        ("categorical", "", None),                # categorical: non-empty text required
    ]
    for extract_type, text_value, numeric_value in invalid_shapes:
        with pytest.raises(Exception):
            _insert(extract_type, text_value, numeric_value)
        conn.rollback()

    # Control: the two valid shapes are accepted.
    _insert("numeric", None, Decimal("52"))
    _insert("claim", "a non-empty claim", None)
    conn.commit()


# ── Complete-contract preflight (C1) ───────────────────────────────────────────
# A schema that keeps every expected table and constraint NAME but diverges in a
# column, nullability, default, CHECK predicate, FK shape, or unique columns must be
# refused by a re-apply (a complete re-apply remains a no-op — see test above).

def _mutate_then_expect_rejection(conn, mutate_sql):
    prior = pg._begin_autocommit(conn)
    conn.execute(mutate_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v50(conn)
    msg = str(ei.value)
    assert "partial/divergent" in msg or "contract violation" in msg
    conn.rollback()


def test_preflight_rejects_missing_column(conn, schema_s):
    _mutate_then_expect_rejection(conn, "ALTER TABLE blueprint_artifact DROP COLUMN locale")


def test_preflight_rejects_relaxed_nullability(conn, schema_s):
    _mutate_then_expect_rejection(
        conn, "ALTER TABLE blueprint_source_item ALTER COLUMN use_allowed DROP NOT NULL"
    )


def test_preflight_rejects_weakened_default_deny_default(conn, schema_s):
    _mutate_then_expect_rejection(
        conn, "ALTER TABLE blueprint_source_item ALTER COLUMN use_allowed SET DEFAULT true"
    )


def test_preflight_rejects_redefined_named_check(conn, schema_s):
    # Same constraint NAME, weaker predicate — must still be rejected.
    _mutate_then_expect_rejection(
        conn,
        "ALTER TABLE blueprint_artifact DROP CONSTRAINT ck_ba_assurance_draft_only; "
        "ALTER TABLE blueprint_artifact "
        "ADD CONSTRAINT ck_ba_assurance_draft_only CHECK (true)",
    )


def test_preflight_rejects_degraded_project_consistent_fk(conn, schema_s):
    # Same FK NAME, but degraded from project-consistent composite to single column.
    _mutate_then_expect_rejection(
        conn,
        "ALTER TABLE blueprint_artifact_input_binding DROP CONSTRAINT fk_baib_extract_project; "
        "ALTER TABLE blueprint_artifact_input_binding ADD CONSTRAINT fk_baib_extract_project "
        "FOREIGN KEY (extract_id) REFERENCES blueprint_source_extract(id) ON DELETE CASCADE",
    )


def test_preflight_rejects_missing_required_unique(conn, schema_s):
    _mutate_then_expect_rejection(
        conn,
        "ALTER TABLE blueprint_artifact_input_binding DROP CONSTRAINT uq_baib_artifact_extract",
    )
