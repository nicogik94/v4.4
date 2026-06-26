"""Research Evidence Metadata Sidecar (R1.1).

This package attaches operator-declared metadata to existing Slice A
``source_snapshot`` and ``candidate_fact_revision`` rows. It creates no parallel
blob, snapshot, fact, retention, calculation, report, scenario, prompt, or
retrieval system.
"""
from __future__ import annotations

import config

SCHEMA_MIGRATION = "v51_research_evidence_sidecar_foundation.sql"


def is_enabled() -> bool:
    return config.research_evidence_enabled()


__all__ = ["SCHEMA_MIGRATION", "is_enabled"]
