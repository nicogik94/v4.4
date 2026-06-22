"""Slice A — Evidence Snapshot Foundation.

Additive, append-only source-capture persistence (SourceBlob, SourceSnapshot,
CandidateFactRevision, EvidenceRetentionEvent, IngestOperation). Off by default;
when enabled it uses the authoritative MAS PostgreSQL database. No dashboards,
APIs, scenario, ROI, Bayesian, or BNN work lives here.
"""
from __future__ import annotations

from . import capture, repository, validation

__all__ = ["capture", "repository", "validation"]
