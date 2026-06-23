"""Slice B — Automation ROI Foundation (PR1: schema, engine, repositories).

Additive, append-only persistence that turns operator-approved Slice A evidence
into deterministic Automation ROI results with report-ready provenance. Off by
default (``config.automation_roi_enabled``); uses the authoritative MAS
PostgreSQL database. No API, projections, dashboards, scenarios, Bayesian, or BNN
work lives here.
"""
from __future__ import annotations

from . import approvals, calculator, repository, service

__all__ = ["approvals", "calculator", "repository", "service"]
