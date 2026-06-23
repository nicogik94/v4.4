"""Shared seeding helpers for Slice B (Automation ROI) PostgreSQL tests.

Not a test module. Builds ROI-eligible candidate facts (Slice A fact + Slice B
extraction context), approvals, and frozen inputs so the schema/integrity and
lifecycle tests can compose realistic lifecycles.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from knowledge.evidence_snapshot import repository as ev_repo
from knowledge.evidence_snapshot.validation import validate_fact
from knowledge.automation_roi import approvals, repository as repo
from knowledge.automation_roi.calculator import HOURS_ROLES, MONEY_ROLES, ROLES

AS_OF = date(2026, 1, 1)

_DEFAULT_VALUES = {
    "baseline_hours_per_period": "10",
    "post_automation_hours_per_period": "2",
    "fully_loaded_rate_per_hour": "50",
    "periods_per_year": "52",
    "annual_recurring_cost": "1000",
    "one_time_implementation_cost": "5000",
}


def make_validated_fact(role: str, *, value: str | None = None, currency: str = "USD"):
    v = Decimal(value if value is not None else _DEFAULT_VALUES[role])
    if role in HOURS_ROLES:
        return validate_fact("duration", value=v, time_unit="hours")
    if role == "fully_loaded_rate_per_hour":
        return validate_fact("money", value=v, currency_code=currency, as_of_date=AS_OF, unit="per_hour")
    if role == "periods_per_year":
        return validate_fact("count", value=v, counted_entity="weeks per year")
    if role in ("annual_recurring_cost", "one_time_implementation_cost"):
        return validate_fact("money", value=v, currency_code=currency, as_of_date=AS_OF)
    raise ValueError(role)


def seed_eligible_fact(conn, project_id, role, *, value=None, currency="USD", tag=""):
    """Create a Slice A fact + 1:1 extraction context; return (cfr_id, snapshot_id)."""
    fact = make_validated_fact(role, value=value, currency=currency)
    blob = ev_repo.insert_or_get_blob(
        conn, project_id=project_id, content_hash=f"h-{role}-{tag}", byte_size=8,
    )
    snap = ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id, storage_ref=f"/store/{role}/{tag}",
    )
    period = "week" if role in HOURS_ROLES else None
    cfr, _ctx = repo.create_eligible_fact(
        conn, project_id=project_id, source_snapshot_id=snap, fact=fact,
        subject_label="process X", metric_label=role, period_basis=period,
        source_locator=f"doc#{role}", extraction_rationale="operator extracted", actor="op",
    )
    return cfr, snap


def seed_and_freeze(conn, project_id, role, *, value=None, currency="USD", tag=""):
    """Create an eligible fact, approve it, and freeze it for ``role``."""
    cfr, snap = seed_eligible_fact(conn, project_id, role, value=value, currency=currency, tag=tag or role)
    approvals.append_decision(
        conn, project_id=project_id, candidate_fact_revision_id=cfr,
        decision_type="approve", actor="op",
    )
    iid = repo.freeze_input(
        conn, project_id=project_id, candidate_fact_revision_id=cfr, input_role=role, frozen_by="op",
    )
    return cfr, snap, iid


def seed_six(conn, project_id, *, overrides: dict | None = None, currency="USD"):
    """Seed+approve+freeze all six roles. Returns {role: {cfr, snap, input}}."""
    overrides = overrides or {}
    out: dict[str, dict] = {}
    for role in ROLES:
        ccy = currency if role in MONEY_ROLES else "USD"
        cfr, snap, iid = seed_and_freeze(
            conn, project_id, role, value=overrides.get(role), currency=ccy,
        )
        out[role] = {"cfr": cfr, "snap": snap, "input": iid}
    return out


def input_map(seeded: dict[str, dict]) -> dict[str, str]:
    return {role: seeded[role]["input"] for role in ROLES}
