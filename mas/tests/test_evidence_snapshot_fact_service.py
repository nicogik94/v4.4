"""Unit gating for the bounded candidate-fact production service (R2.0A-4B).

No database: these prove the feature gate, the caller-transaction-ownership
guard (autocommit + READ COMMITTED isolation), and canonical revalidation of a
directly-constructed ``ValidatedFact`` — all before any SQL or SAVEPOINT runs.
Repository binding, rollback, and append behavior live in the _pg suite.
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.evidence_snapshot import fact_service  # noqa: E402
from knowledge.evidence_snapshot.validation import (  # noqa: E402
    NUMERIC_FACT_TYPES,
    FactValidationError,
    ValidatedFact,
    validate_fact,
)


class FakeConn:
    """A connection whose every ``execute`` fails — proves no SQL runs.

    The gating tests rely on this: any rejection that reaches ``execute`` (i.e.
    a SAVEPOINT or SELECT) would raise AssertionError, so a passing test proves
    the rejection happened before any SQL.
    """

    def __init__(self, *, autocommit=False, isolation_level=None):
        self.autocommit = autocommit
        self.isolation_level = isolation_level
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append(str(query))
        raise AssertionError("no SQL should run in these gating tests")


def _fact():
    return validate_fact("count", value=3, counted_entity="records")


# The service now requires an EXPLICITLY pinned READ COMMITTED level, so the
# gating tests that must get past the isolation guard pin it here.
RC = psycopg.IsolationLevel.READ_COMMITTED


def _rc(**kwargs):
    kwargs.setdefault("isolation_level", RC)
    return FakeConn(**kwargs)


def test_disabled_without_snapshot_flag(monkeypatch):
    monkeypatch.delenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", raising=False)
    with pytest.raises(fact_service.CandidateFactServiceDisabled):
        fact_service.create_candidate_fact_revision(
            FakeConn(), project_id="p", source_snapshot_id="s", fact=_fact()
        )


def test_autocommit_connection_rejected(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    with pytest.raises(fact_service.CandidateFactServiceTransactionError):
        fact_service.create_candidate_fact_revision(
            FakeConn(autocommit=True),
            project_id="p",
            source_snapshot_id="s",
            fact=_fact(),
        )


def test_non_validated_fact_rejected(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    with pytest.raises(TypeError):
        fact_service.create_candidate_fact_revision(
            FakeConn(), project_id="p", source_snapshot_id="s",
            fact={"fact_type": "count"},
        )


def test_missing_snapshot_id_rejected(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    with pytest.raises(fact_service.CandidateFactSourceSnapshotNotFound):
        fact_service.create_candidate_fact_revision(
            _rc(), project_id="p", source_snapshot_id="", fact=_fact()
        )


def test_none_isolation_rejected_before_any_sql(monkeypatch):
    # isolation_level=None delegates to the server default and does NOT prove
    # READ COMMITTED; it must be rejected before any SQL runs.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = FakeConn(isolation_level=None)
    with pytest.raises(fact_service.CandidateFactServiceTransactionError):
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=_fact()
        )
    assert conn.executed == []


@pytest.mark.parametrize(
    "isolation",
    [psycopg.IsolationLevel.REPEATABLE_READ, psycopg.IsolationLevel.SERIALIZABLE],
)
def test_non_read_committed_isolation_rejected(monkeypatch, isolation):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    with pytest.raises(fact_service.CandidateFactServiceTransactionError):
        fact_service.create_candidate_fact_revision(
            FakeConn(isolation_level=isolation),
            project_id="p", source_snapshot_id="s", fact=_fact(),
        )


def test_read_committed_isolation_accepted_reaches_sql(monkeypatch):
    # An explicitly pinned READ COMMITTED is accepted, so the guard passes and
    # execution reaches SQL (proven by the FakeConn AssertionError).
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    with pytest.raises(AssertionError):
        fact_service.create_candidate_fact_revision(
            _rc(), project_id="p", source_snapshot_id="s", fact=_fact(),
        )


class _LiveIsolationConn:
    """A conn whose driver attribute says READ COMMITTED but whose LIVE
    ``SHOW transaction_isolation`` reports ``live_level`` — proving the service
    verifies the live isolation, not only the attribute."""

    def __init__(self, live_level):
        self.autocommit = False
        self.isolation_level = RC
        self._live = live_level
        self.executed = []

    def execute(self, query, params=None):
        q = str(query)
        self.executed.append(q)
        if "transaction_isolation" in q:
            return _Row((self._live,))
        raise AssertionError("only SHOW transaction_isolation expected pre-write")


class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value


def test_live_isolation_verified_rejects_non_read_committed(monkeypatch):
    # Attribute says READ COMMITTED, but the live transaction was pinned to
    # SERIALIZABLE (e.g. a raw SET after opening) — the service must reject it via
    # SHOW transaction_isolation, before opening the savepoint / inserting.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _LiveIsolationConn("serializable")
    with pytest.raises(fact_service.CandidateFactServiceTransactionError):
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=_fact()
        )
    assert any("transaction_isolation" in q for q in conn.executed)
    assert not any("SAVEPOINT" in q for q in conn.executed)


def test_live_read_committed_passes_isolation_and_reaches_write(monkeypatch):
    # Live isolation is READ COMMITTED, so the check passes and execution reaches
    # the savepoint (the next statement, which this conn rejects with an
    # AssertionError), proving the verification does not block a valid caller.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _LiveIsolationConn("read committed")
    with pytest.raises(AssertionError):
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=_fact()
        )
    assert any("transaction_isolation" in q for q in conn.executed)


# ─────────────────────────── canonical revalidation (no SQL) ───────────────────────────
#
# A ``ValidatedFact`` is a plain dataclass and can be constructed directly,
# bypassing ``validate_fact``. The service must reconstruct + revalidate every
# field and reject a forged/inconsistent value BEFORE any SQL runs — proven by
# the FakeConn, whose ``execute`` raises AssertionError (so a FactValidationError
# means validation happened first).


def _forged(**kwargs) -> ValidatedFact:
    return ValidatedFact(**kwargs)


FORGED_INVALID = [
    # rate missing numerator and denominator contexts
    _forged(fact_type="rate", numeric_value=Decimal("0.5")),
    # rate missing only the denominator context
    _forged(fact_type="rate", numeric_value=Decimal("0.5"), numerator_context="x"),
    # percentage missing subtype
    _forged(fact_type="percentage", numeric_value=Decimal("10"), percentage_basis="b"),
    # percentage invalid subtype
    _forged(
        fact_type="percentage", numeric_value=Decimal("10"),
        percentage_basis="b", percentage_subtype="not_a_subtype",
    ),
    # percentage out of bounds for its subtype (share_0_1 upper bound is 1)
    _forged(
        fact_type="percentage", numeric_value=Decimal("2"),
        percentage_basis="b", percentage_subtype="share_0_1",
    ),
    # duration negative
    _forged(fact_type="duration", numeric_value=Decimal("-1"), time_unit="days"),
    # duration invalid unit
    _forged(fact_type="duration", numeric_value=Decimal("1"), time_unit="fortnights"),
    # count non-integral
    _forged(fact_type="count", numeric_value=Decimal("3.5"), counted_entity="rows"),
    # count missing counted_entity
    _forged(fact_type="count", numeric_value=Decimal("3")),
    # money unknown currency
    _forged(
        fact_type="money", numeric_value=Decimal("5"),
        currency_code="XYZ", as_of_date=date(2026, 1, 1),
    ),
    # money missing as_of_date
    _forged(fact_type="money", numeric_value=Decimal("5"), currency_code="USD"),
    # blank textual fact
    _forged(fact_type="text", text_value="   "),
    # unknown fact_type entirely
    _forged(fact_type="totally_bogus", text_value="x"),
]


@pytest.mark.parametrize("forged", FORGED_INVALID)
def test_forged_validatedfact_rejected_before_any_sql(monkeypatch, forged):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _rc()
    with pytest.raises(FactValidationError):
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=forged
        )
    assert conn.executed == []  # no SAVEPOINT / SELECT ran before rejection


def test_valid_directly_constructed_fact_reaches_sql(monkeypatch):
    # A well-formed ValidatedFact revalidates cleanly, so execution reaches the
    # SAVEPOINT — proving canonicalization does not reject valid values.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    good = ValidatedFact(
        fact_type="rate", numeric_value=Decimal("0.5"),
        numerator_context="defects", denominator_context="units",
    )
    with pytest.raises(AssertionError):
        fact_service.create_candidate_fact_revision(
            _rc(), project_id="p", source_snapshot_id="s", fact=good
        )


# ─────────────────── P2-A: non-finite numeric facts rejected ───────────────────
#
# PostgreSQL NUMERIC itself stores NaN/±Infinity, and the canonical v47
# `validate_fact` does NOT universally reject them (money/rate admit any
# non-finite; the unbounded percentage `change` subtype admits them; count/
# duration admit infinities). So the bounded A-4B service must reject every
# non-finite numeric candidate — for EVERY numeric fact type — BEFORE any SQL or
# SAVEPOINT runs. The FakeConn's `execute` raises AssertionError, so a passing
# test also proves no SQL/SAVEPOINT/insert happened.

NON_FINITE = [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]


def _numeric_profile(fact_type, value) -> ValidatedFact:
    """An otherwise-valid, directly-constructed ValidatedFact of ``fact_type``.

    Every non-numeric field required by that type is supplied, so the ONLY
    remaining defect is the non-finite ``value``.
    """
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
        # The unbounded `change` subtype is the profile that currently admits a
        # non-finite value through validate_fact (bounded subtypes reject some
        # via their comparisons); it is the load-bearing case for this guard.
        return ValidatedFact(
            fact_type="percentage", numeric_value=value,
            percentage_basis="quarter-over-quarter", percentage_subtype="change",
        )
    if fact_type == "duration":
        return ValidatedFact(
            fact_type="duration", numeric_value=value, time_unit="days",
        )
    if fact_type == "count":
        return ValidatedFact(
            fact_type="count", numeric_value=value, counted_entity="records",
        )
    raise AssertionError(f"unhandled numeric fact_type {fact_type!r}")


@pytest.mark.parametrize("fact_type", sorted(NUMERIC_FACT_TYPES))
@pytest.mark.parametrize("value", NON_FINITE)
def test_non_finite_numeric_fact_rejected_before_any_sql(monkeypatch, fact_type, value):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _rc()
    with pytest.raises(FactValidationError) as exc:
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s",
            fact=_numeric_profile(fact_type, value),
        )
    # Uniform, bounded message that never echoes the operator's value.
    assert str(exc.value) == "numeric candidate facts must be finite"
    assert str(value) not in str(exc.value)
    # No SAVEPOINT / SELECT / insert ran before the semantic rejection.
    assert conn.executed == []


def test_non_finite_from_string_input_rejected_before_any_sql(monkeypatch):
    # A forged ValidatedFact can carry a non-Decimal numeric_value (a string).
    # validate_fact normalizes "Infinity" to Decimal("Infinity"); the finite
    # re-check still rejects it before any SQL runs.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _rc()
    forged = ValidatedFact(
        fact_type="money", numeric_value="Infinity",
        currency_code="USD", as_of_date=date(2026, 1, 1),
    )
    with pytest.raises(FactValidationError) as exc:
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=forged
        )
    assert str(exc.value) == "numeric candidate facts must be finite"
    assert conn.executed == []


# A string spelling of a non-finite value for a bound-comparing profile
# (duration / bounded percentage) would otherwise make validate_fact raise a
# non-canonical decimal.InvalidOperation; the string is normalized and rejected
# with the canonical FactValidationError instead, before any SQL runs.
_STRING_NON_FINITE = [
    ("duration", "NaN", {"time_unit": "days"}),
    ("duration", "Infinity", {"time_unit": "days"}),
    ("duration", "-inf", {"time_unit": "days"}),
    ("percentage", "NaN",
     {"percentage_basis": "b", "percentage_subtype": "share_0_100"}),
    ("percentage", "Infinity",
     {"percentage_basis": "b", "percentage_subtype": "share_0_1"}),
]


@pytest.mark.parametrize("fact_type,text_value,extra", _STRING_NON_FINITE)
def test_string_non_finite_rejected_uniformly_before_any_sql(
    monkeypatch, fact_type, text_value, extra
):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _rc()
    forged = ValidatedFact(fact_type=fact_type, numeric_value=text_value, **extra)
    with pytest.raises(FactValidationError) as exc:
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=forged
        )
    assert str(exc.value) == "numeric candidate facts must be finite"
    assert conn.executed == []


def test_non_numeric_string_still_gets_canonical_validation_error(monkeypatch):
    # A genuinely non-numeric string is NOT the finite error's concern: it is
    # left for validate_fact to reject with its own canonical message.
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    conn = _rc()
    forged = ValidatedFact(
        fact_type="count", numeric_value="not-a-number", counted_entity="rows"
    )
    with pytest.raises(FactValidationError) as exc:
        fact_service.create_candidate_fact_revision(
            conn, project_id="p", source_snapshot_id="s", fact=forged
        )
    assert str(exc.value) != "numeric candidate facts must be finite"
    assert conn.executed == []
