"""Agent Blueprint Studio S1 repository — sticky-ephemeral, insert-only.

Two backends share the model types:

* **PostgreSQL backend** (``pg_*`` functions): operate on a caller-supplied
  synchronous ``psycopg`` connection (the evidence-snapshot/automation-roi
  convention). Insert-only; the caller owns the transaction (commit/rollback).
* **Ephemeral backend**: an in-process registry. Single-process, restart-lost,
  non-shareable. No database, no psycopg import.

The **facade** (``create_draft`` and friends) binds a draft's persistence mode at
creation time and routes every later operation by that binding:

* an ephemeral draft id lives only in the in-process registry and is **never**
  touched by a database connection again — so it can never silently persist if the
  database later becomes available (stickiness). Promotion to a persisted draft is a
  later, explicit "Save as new persisted draft" capability and is NOT implemented in
  Wave 1.

Nothing here imports or touches the Decision Engine (ProjectState / store.py).
"""
from __future__ import annotations

import contextlib
import uuid
from typing import Any, Callable, Optional

import config

from .models import (
    ArtifactInputBinding,
    BlueprintProject,
    ConfigRevision,
    CuratedExtract,
    DraftArtifact,
    PersistenceMode,
    SourceManifestItem,
    SourceRights,
)

# ─────────────────────────── feature-flag gate (facade only) ───────────────────────────


class StudioDisabled(RuntimeError):
    """Raised by the high-level Studio facade when the feature flag is off.

    One narrow surface for callers (and a future API layer) to map to a uniform
    "feature disabled" response. The low-level ``pg_*`` helpers are intentionally
    NOT gated — they operate on a caller-supplied connection and are used directly
    by the schema tests and by any future, separately-gated runner.
    """


def _require_enabled() -> None:
    """Guard every facade entry point. When disabled, raise BEFORE touching the
    connection factory or the in-process registry, so a flag-off call can neither
    open a connection nor create an ephemeral draft."""
    if not config.agent_blueprint_studio_enabled():
        raise StudioDisabled(
            "Agent Blueprint Studio is disabled "
            "(set MAS_AGENT_BLUEPRINT_STUDIO_ENABLED to enable it)"
        )


# ─────────────────────────── connection seam (DI for tests) ───────────────────────────


def open_connection():
    """Open the authoritative MAS database connection. Tests monkeypatch this."""
    import psycopg

    return psycopg.connect(config.DATABASE_URL)


def _try_open(conn_factory: Callable[[], Any]) -> Optional[Any]:
    """Return a connection or None if one cannot be opened (no exception leaks)."""
    try:
        return conn_factory()
    except Exception:
        return None


# The COMPLETE v50 Agent Blueprint Studio table set. Persisted mode requires EVERY
# one of these to be resolvable — a partial or damaged Studio schema must never
# permit persisted drafts (a new draft falls back to sticky EPHEMERAL instead).
STUDIO_V50_TABLES: tuple[str, ...] = (
    "blueprint_project",
    "blueprint_config_revision",
    "blueprint_source_item",
    "blueprint_source_extract",
    "blueprint_artifact",
    "blueprint_artifact_input_binding",
    "blueprint_lint_result",
    "blueprint_lint_finding",
    "blueprint_eval_case",
    "blueprint_eval_run",
    "blueprint_draft_export",
)


def studio_tables_present(conn) -> bool:
    """True only when the COMPLETE v50 Studio schema (all 11 tables) resolves on the
    connection's search path.

    A partial or damaged Studio schema — e.g. only ``blueprint_project`` and
    ``blueprint_artifact_input_binding`` present — returns False, so a new draft binds
    to sticky EPHEMERAL mode and never persists into an incomplete schema. The probe
    counts present tables in one round-trip, preserving ``to_regclass`` search-path
    resolution."""
    expr = " + ".join("(to_regclass(%s) IS NOT NULL)::int" for _ in STUDIO_V50_TABLES)
    try:
        row = conn.execute(f"SELECT {expr}", tuple(STUDIO_V50_TABLES)).fetchone()
    except Exception:
        return False
    return bool(row) and row[0] == len(STUDIO_V50_TABLES)


def resolve_persistence_mode(conn_factory: Callable[[], Any] = open_connection) -> PersistenceMode:
    """Decide the mode for a NEW draft: PERSISTED iff a connection opens and the
    Studio schema is present; otherwise EPHEMERAL. Disabled ⇒ StudioDisabled (no
    connection attempt)."""
    _require_enabled()
    conn = _try_open(conn_factory)
    if conn is None:
        return PersistenceMode.EPHEMERAL
    try:
        return PersistenceMode.PERSISTED if studio_tables_present(conn) else PersistenceMode.EPHEMERAL
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ─────────────────────────── in-process ephemeral registry ───────────────────────────

_EPHEMERAL: dict[str, dict] = {}


def _reset_ephemeral_for_tests() -> None:
    """Clear the in-process ephemeral registry. Tests only."""
    _EPHEMERAL.clear()


def is_ephemeral(project_id: str) -> bool:
    return project_id in _EPHEMERAL


def _eph(project_id: str) -> dict:
    draft = _EPHEMERAL.get(project_id)
    if draft is None:
        raise KeyError(f"ephemeral draft {project_id} not found")
    return draft


# ─────────────────────────── PostgreSQL backend (insert-only) ───────────────────────────


def pg_create_project(
    conn,
    *,
    name: str,
    domain_label: str = "",
    created_by: str = "",
    linked_project_id: Optional[str] = None,
) -> BlueprintProject:
    row = conn.execute(
        """
        INSERT INTO blueprint_project (name, domain_label, created_by, linked_project_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id::text, status, created_at
        """,
        (name, domain_label, created_by, linked_project_id),
    ).fetchone()
    return BlueprintProject(
        id=row[0],
        name=name,
        persistence=PersistenceMode.PERSISTED,
        domain_label=domain_label,
        status=row[1],
        linked_project_id=linked_project_id,
        created_by=created_by,
        created_at=row[2],
    )


def pg_get_project(conn, project_id: str) -> Optional[BlueprintProject]:
    row = conn.execute(
        """
        SELECT id::text, name, domain_label, status,
               linked_project_id::text, created_by, created_at
        FROM blueprint_project WHERE id = %s
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return BlueprintProject(
        id=row[0], name=row[1], persistence=PersistenceMode.PERSISTED,
        domain_label=row[2], status=row[3],
        linked_project_id=row[4], created_by=row[5], created_at=row[6],
    )


def pg_add_config_revision(
    conn,
    *,
    blueprint_project_id: str,
    revision_no: int = 1,
    config: Optional[dict] = None,
    terminology_map: Optional[dict] = None,
    terminology_map_version: str = "",
    locale: str = "",
    content_hash: str = "",
    created_by: str = "",
) -> ConfigRevision:
    import json

    row = conn.execute(
        """
        INSERT INTO blueprint_config_revision
            (blueprint_project_id, revision_no, config_json, terminology_map_json,
             terminology_map_version, locale, content_hash, created_by)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            blueprint_project_id, revision_no, json.dumps(config or {}),
            json.dumps(terminology_map or {}), terminology_map_version, locale,
            content_hash, created_by,
        ),
    ).fetchone()
    return ConfigRevision(
        id=row[0], blueprint_project_id=blueprint_project_id, revision_no=revision_no,
        config=config or {}, terminology_map=terminology_map or {},
        terminology_map_version=terminology_map_version, locale=locale,
        content_hash=content_hash, created_by=created_by,
    )


def pg_add_source_item(
    conn,
    *,
    blueprint_project_id: str,
    title: str = "",
    source_kind: str = "",
    locator: str = "",
    authority_tier: Optional[str] = None,
    rights: Optional[SourceRights] = None,
    created_by: str = "",
) -> SourceManifestItem:
    """Insert a manifest item. When ``rights``/``authority_tier`` are omitted, the
    DB DEFAULT-DENY column defaults apply and are read back via RETURNING."""
    if rights is None:
        row = conn.execute(
            """
            INSERT INTO blueprint_source_item
                (blueprint_project_id, title, source_kind, locator, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id::text, authority_tier, use_allowed, quote_allowed, export_allowed,
                      external_processing_allowed, permitted_audience, sensitivity_level,
                      retention_declaration
            """,
            (blueprint_project_id, title, source_kind, locator, created_by),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO blueprint_source_item
                (blueprint_project_id, title, source_kind, locator, authority_tier,
                 use_allowed, quote_allowed, export_allowed, external_processing_allowed,
                 permitted_audience, sensitivity_level, retention_declaration, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text, authority_tier, use_allowed, quote_allowed, export_allowed,
                      external_processing_allowed, permitted_audience, sensitivity_level,
                      retention_declaration
            """,
            (
                blueprint_project_id, title, source_kind, locator,
                authority_tier if authority_tier is not None else "unspecified",
                rights.use_allowed, rights.quote_allowed, rights.export_allowed,
                rights.external_processing_allowed, rights.permitted_audience,
                rights.sensitivity_level, rights.retention_declaration, created_by,
            ),
        ).fetchone()
    return SourceManifestItem(
        id=row[0],
        blueprint_project_id=blueprint_project_id,
        title=title,
        source_kind=source_kind,
        locator=locator,
        authority_tier=row[1],
        rights=SourceRights(
            use_allowed=row[2], quote_allowed=row[3], export_allowed=row[4],
            external_processing_allowed=row[5], permitted_audience=row[6],
            sensitivity_level=row[7], retention_declaration=row[8],
        ),
        created_by=created_by,
    )


def pg_add_extract(conn, extract: CuratedExtract) -> CuratedExtract:
    row = conn.execute(
        """
        INSERT INTO blueprint_source_extract
            (blueprint_project_id, source_item_id, extract_type, text_value, numeric_value,
             unit, as_of_date, curated_by, curation_status, extract_content_fingerprint)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            extract.blueprint_project_id, extract.source_item_id, extract.extract_type,
            extract.text_value, extract.numeric_value, extract.unit, extract.as_of_date,
            extract.curated_by, extract.curation_status, extract.extract_content_fingerprint,
        ),
    ).fetchone()
    extract.id = row[0]
    return extract


def pg_create_artifact(conn, artifact: DraftArtifact) -> DraftArtifact:
    import json

    row = conn.execute(
        """
        INSERT INTO blueprint_artifact
            (blueprint_project_id, config_revision_id, artifact_kind, baseline_artifact_id,
             content_json, content_hash, artifact_schema_version, compiler_version,
             template_set_version, terminology_map_version, locale)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            artifact.blueprint_project_id, artifact.config_revision_id, artifact.artifact_kind,
            artifact.baseline_artifact_id, json.dumps(artifact.content), artifact.content_hash,
            artifact.artifact_schema_version, artifact.compiler_version,
            artifact.template_set_version, artifact.terminology_map_version, artifact.locale,
        ),
    ).fetchone()
    artifact.id = row[0]
    return artifact


def pg_bind_input(
    conn,
    *,
    blueprint_project_id: str,
    artifact_id: str,
    extract_id: str,
    input_order: int,
    extract_content_fingerprint: str = "",
) -> ArtifactInputBinding:
    """Bind one extract into an artifact's input contract at the operator-DECLARED
    ``input_order``. The caller supplies the position; it is never derived from the
    binding/extract ids or insertion time. Distinct per (artifact_id, input_order)."""
    row = conn.execute(
        """
        INSERT INTO blueprint_artifact_input_binding
            (blueprint_project_id, artifact_id, extract_id, input_order,
             extract_content_fingerprint)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (blueprint_project_id, artifact_id, extract_id, input_order,
         extract_content_fingerprint),
    ).fetchone()
    return ArtifactInputBinding(
        id=row[0], blueprint_project_id=blueprint_project_id, artifact_id=artifact_id,
        extract_id=extract_id, input_order=input_order,
        extract_content_fingerprint=extract_content_fingerprint,
    )


def pg_list_bindings(conn, *, artifact_id: str) -> list[ArtifactInputBinding]:
    """List an artifact's bindings in deterministic operator-declared order."""
    rows = conn.execute(
        """
        SELECT id::text, blueprint_project_id::text, artifact_id::text, extract_id::text,
               input_order, extract_content_fingerprint
        FROM blueprint_artifact_input_binding
        WHERE artifact_id = %s
        ORDER BY input_order
        """,
        (artifact_id,),
    ).fetchall()
    return [
        ArtifactInputBinding(
            id=r[0], blueprint_project_id=r[1], artifact_id=r[2], extract_id=r[3],
            input_order=r[4], extract_content_fingerprint=r[5],
        )
        for r in rows
    ]


# ─────────────────────────── facade (mode binding + sticky routing) ───────────────────────────


def create_draft(
    *,
    name: str,
    domain_label: str = "",
    created_by: str = "",
    linked_project_id: Optional[str] = None,
    conn_factory: Callable[[], Any] = open_connection,
) -> BlueprintProject:
    """Create a draft, binding its persistence mode now. PERSISTED iff a connection
    opens AND the Studio schema is present; otherwise a sticky EPHEMERAL draft.
    Disabled ⇒ StudioDisabled, before any connection attempt or ephemeral draft."""
    _require_enabled()
    conn = _try_open(conn_factory)
    if conn is not None and studio_tables_present(conn):
        try:
            project = pg_create_project(
                conn, name=name, domain_label=domain_label,
                created_by=created_by, linked_project_id=linked_project_id,
            )
            conn.commit()
            return project
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    if conn is not None:
        with contextlib.suppress(Exception):
            conn.close()
    # Ephemeral: bound for life; never auto-persisted.
    project_id = str(uuid.uuid4())
    project = BlueprintProject(
        id=project_id, name=name, persistence=PersistenceMode.EPHEMERAL,
        domain_label=domain_label, status="draft", linked_project_id=linked_project_id,
        created_by=created_by,
    )
    _EPHEMERAL[project_id] = {
        "project": project,
        "config_revisions": [],
        "source_items": {},
        "extracts": {},
        "artifacts": {},
        "bindings": [],
    }
    return project


def get_draft(
    project_id: str,
    *,
    conn_factory: Callable[[], Any] = open_connection,
) -> Optional[BlueprintProject]:
    """Fetch a draft. An ephemeral draft is served from memory and NEVER opens a
    connection (so it can never silently persist). Disabled ⇒ StudioDisabled (no
    connection attempt)."""
    _require_enabled()
    if is_ephemeral(project_id):
        return _eph(project_id)["project"]
    conn = _try_open(conn_factory)
    if conn is None:
        return None
    try:
        return pg_get_project(conn, project_id)
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def add_source_item(
    project_id: str,
    *,
    title: str = "",
    source_kind: str = "",
    locator: str = "",
    authority_tier: Optional[str] = None,
    rights: Optional[SourceRights] = None,
    created_by: str = "",
    conn_factory: Callable[[], Any] = open_connection,
) -> SourceManifestItem:
    """Add a manifest item, routing by the draft's bound mode. Omitted rights ⇒
    default-deny. Disabled ⇒ StudioDisabled (no connection attempt)."""
    _require_enabled()
    if is_ephemeral(project_id):
        item = SourceManifestItem(
            id=str(uuid.uuid4()),
            blueprint_project_id=project_id,
            title=title,
            source_kind=source_kind,
            locator=locator,
            authority_tier=authority_tier if authority_tier is not None else "unspecified",
            rights=rights if rights is not None else SourceRights(),
            created_by=created_by,
        )
        _eph(project_id)["source_items"][item.id] = item
        return item
    conn = conn_factory()
    try:
        item = pg_add_source_item(
            conn, blueprint_project_id=project_id, title=title, source_kind=source_kind,
            locator=locator, authority_tier=authority_tier, rights=rights, created_by=created_by,
        )
        conn.commit()
        return item
    finally:
        with contextlib.suppress(Exception):
            conn.close()
