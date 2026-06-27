"""Agent Blueprint Studio S1 — ephemeral mode, stickiness, and rights defaults (no DB)."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent_blueprint_studio.repository as repo  # noqa: E402
from agent_blueprint_studio.models import (  # noqa: E402
    PersistenceMode,
    SourceRights,
    compute_extract_fingerprint,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    repo._reset_ephemeral_for_tests()
    yield
    repo._reset_ephemeral_for_tests()


@pytest.fixture
def studio_on(monkeypatch):
    """Enable the Studio feature flag for facade behavior tests (off by default)."""
    monkeypatch.setenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", "true")


def _db_down():
    raise RuntimeError("database unavailable")


# ── Rights defaults (model layer) ─────────────────────────────────────────────

def test_source_rights_default_deny():
    r = SourceRights()
    assert r.use_allowed is False
    assert r.quote_allowed is False
    assert r.export_allowed is False
    assert r.external_processing_allowed is False
    assert r.permitted_audience == "operator_only"
    assert r.sensitivity_level == "restricted"
    assert r.retention_declaration == "undeclared_restricted"


# ── Ephemeral creation + binding-at-creation ──────────────────────────────────

def test_create_draft_is_ephemeral_when_db_unavailable(studio_on):
    project = repo.create_draft(name="Internal Strategy Agent", conn_factory=_db_down)
    assert project.persistence == PersistenceMode.EPHEMERAL
    assert repo.is_ephemeral(project.id)
    fetched = repo.get_draft(project.id, conn_factory=_db_down)
    assert fetched is not None
    assert fetched.persistence == PersistenceMode.EPHEMERAL


def test_ephemeral_add_source_item_is_default_deny(studio_on):
    project = repo.create_draft(name="Example Knowledge Project", conn_factory=_db_down)
    item = repo.add_source_item(project.id, title="manual source", conn_factory=_db_down)
    r = item.rights
    assert (r.use_allowed, r.quote_allowed, r.export_allowed, r.external_processing_allowed) == (
        False, False, False, False,
    )
    assert r.permitted_audience == "operator_only"
    assert r.sensitivity_level == "restricted"
    assert item.authority_tier == "unspecified"


# ── Stickiness: an ephemeral draft never silently persists ─────────────────────

def test_ephemeral_is_sticky_and_never_opens_a_connection(studio_on):
    project = repo.create_draft(name="sticky draft", conn_factory=_db_down)

    opened = []

    def tripwire():
        opened.append(1)
        raise AssertionError("an ephemeral draft must never open a database connection")

    # "The database becomes available": operations on the ephemeral draft must still
    # route to memory and must NOT open a connection (so it cannot silently persist).
    fetched = repo.get_draft(project.id, conn_factory=tripwire)
    assert fetched.persistence == PersistenceMode.EPHEMERAL

    item = repo.add_source_item(project.id, title="t", conn_factory=tripwire)
    assert item.rights.use_allowed is False

    assert opened == []
    assert repo.is_ephemeral(project.id)  # still ephemeral; not promoted


# ── Feature-flag gate at the facade (flag OFF) ─────────────────────────────────


def _tripwire():
    raise AssertionError("the connection factory must not be called when disabled")


def test_flag_off_create_draft_raises_without_connection_or_ephemeral_draft(monkeypatch):
    monkeypatch.delenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", raising=False)
    with pytest.raises(repo.StudioDisabled):
        repo.create_draft(name="x", conn_factory=_tripwire)
    # neither a connection attempt nor an ephemeral draft was created
    assert repo._EPHEMERAL == {}


def test_flag_off_resolve_mode_raises_without_connection(monkeypatch):
    monkeypatch.delenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", raising=False)
    with pytest.raises(repo.StudioDisabled):
        repo.resolve_persistence_mode(conn_factory=_tripwire)


def test_flag_off_get_draft_raises_without_connection(monkeypatch):
    monkeypatch.delenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", raising=False)
    with pytest.raises(repo.StudioDisabled):
        repo.get_draft("00000000-0000-0000-0000-000000000000", conn_factory=_tripwire)
    assert repo._EPHEMERAL == {}


def test_flag_off_add_source_item_raises_without_connection(monkeypatch):
    monkeypatch.delenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", raising=False)
    with pytest.raises(repo.StudioDisabled):
        repo.add_source_item(
            "00000000-0000-0000-0000-000000000000", title="t", conn_factory=_tripwire
        )
    assert repo._EPHEMERAL == {}


def test_low_level_pg_helpers_are_not_flag_gated(monkeypatch):
    """pg_* helpers operate on a caller-supplied connection and stay unguarded:
    importing/calling them must not consult the flag (no StudioDisabled)."""
    monkeypatch.delenv("MAS_AGENT_BLUEPRINT_STUDIO_ENABLED", raising=False)
    # _require_enabled is the facade gate; the pg_* helpers never call it.
    assert callable(repo.pg_create_project)
    assert callable(repo.pg_bind_input)
    with pytest.raises(repo.StudioDisabled):
        repo._require_enabled()


# ── Mode resolution ────────────────────────────────────────────────────────────

def test_resolve_mode_ephemeral_when_open_fails(studio_on):
    assert repo.resolve_persistence_mode(conn_factory=_db_down) == PersistenceMode.EPHEMERAL


class _StubCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _StubConn:
    def __init__(self, row):
        self._row = row
        self.closed = False

    def execute(self, *args, **kwargs):
        return _StubCursor(self._row)

    def close(self):
        self.closed = True


# studio_tables_present probes how many of the 11 v50 tables resolve; persisted
# mode requires the COMPLETE set. These stub the probe's single count result.

def test_resolve_mode_persisted_when_tables_present(studio_on):
    conn = _StubConn((len(repo.STUDIO_V50_TABLES),))  # all 11 present
    assert repo.resolve_persistence_mode(conn_factory=lambda: conn) == PersistenceMode.PERSISTED
    assert conn.closed is True


def test_resolve_mode_ephemeral_when_schema_partial(studio_on):
    # Only blueprint_project + blueprint_artifact_input_binding (2 of 11): a partial/
    # damaged Studio schema must resolve to sticky EPHEMERAL, never PERSISTED.
    conn = _StubConn((2,))
    assert repo.resolve_persistence_mode(conn_factory=lambda: conn) == PersistenceMode.EPHEMERAL
    assert conn.closed is True


def test_resolve_mode_ephemeral_when_tables_absent(studio_on):
    conn = _StubConn((0,))
    assert repo.resolve_persistence_mode(conn_factory=lambda: conn) == PersistenceMode.EPHEMERAL


# ── Change-detection fingerprint ───────────────────────────────────────────────

def test_extract_fingerprint_is_stable_and_change_sensitive():
    args = dict(extract_type="numeric", text_value=None, unit="", as_of_date=None)
    fp1 = compute_extract_fingerprint(numeric_value=Decimal("52"), **args)
    fp2 = compute_extract_fingerprint(numeric_value=Decimal("52"), **args)
    fp3 = compute_extract_fingerprint(numeric_value=Decimal("53"), **args)
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 64
