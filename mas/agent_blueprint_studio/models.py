"""Agent Blueprint Studio S1 data models (draft-snapshot semantics).

Plain dataclasses + enums shared by the persisted (PostgreSQL) and sticky-ephemeral
backends. No API request/response models here (no routes in Wave 1).

Design rules encoded here:
  * Rights/sensitivity/retention are DISTINCT, explicit, DEFAULT-DENY fields; an
    unknown value is non-permissive.
  * Curation is OPERATOR-DECLARED only — ``curated_by`` / ``curation_status`` are
    operator labels, never authenticated, independent, or verified review.
  * ``content_hash`` / ``extract_content_fingerprint`` are for CHANGE DETECTION
    only — not a tamper-evident or immutable-provenance claim.
  * Numeric values are ``Decimal`` (never float).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class PersistenceMode(str, Enum):
    """Where a draft lives. Bound when the draft is created; never flips."""

    PERSISTED = "persisted"      # durable row in the authoritative MAS database
    EPHEMERAL = "ephemeral"      # single-process, restart-lost, non-shareable


# Default-deny rights/sensitivity/retention defaults (unknown ⇒ non-permissive).
DEFAULT_AUTHORITY_TIER = "unspecified"
DEFAULT_PERMITTED_AUDIENCE = "operator_only"
DEFAULT_SENSITIVITY_LEVEL = "restricted"
DEFAULT_RETENTION_DECLARATION = "undeclared_restricted"

EXTRACT_TYPES = ("claim", "quote", "numeric", "categorical")
ASSURANCE_STATUS_DRAFT = "draft_unvalidated"  # the only S1 assurance value


@dataclass(frozen=True)
class SourceRights:
    """Distinct, explicit, default-deny rights for a source manifest item."""

    use_allowed: bool = False
    quote_allowed: bool = False
    export_allowed: bool = False
    external_processing_allowed: bool = False
    permitted_audience: str = DEFAULT_PERMITTED_AUDIENCE
    sensitivity_level: str = DEFAULT_SENSITIVITY_LEVEL
    retention_declaration: str = DEFAULT_RETENTION_DECLARATION

    def as_dict(self) -> dict:
        return {
            "use_allowed": self.use_allowed,
            "quote_allowed": self.quote_allowed,
            "export_allowed": self.export_allowed,
            "external_processing_allowed": self.external_processing_allowed,
            "permitted_audience": self.permitted_audience,
            "sensitivity_level": self.sensitivity_level,
            "retention_declaration": self.retention_declaration,
        }


@dataclass
class BlueprintProject:
    id: str
    name: str
    persistence: PersistenceMode
    domain_label: str = ""
    status: str = "draft"  # draft-only in S1 (DB-enforced by ck_bp_status_draft_only)
    linked_project_id: Optional[str] = None
    created_by: str = ""
    created_at: Optional[datetime] = None
    # No current-revision pointer: the latest revision is DERIVED from
    # ConfigRevision.revision_no, never tracked by an unenforceable column.


@dataclass
class ConfigRevision:
    id: str
    blueprint_project_id: str
    revision_no: int
    config: dict = field(default_factory=dict)
    terminology_map: dict = field(default_factory=dict)
    terminology_map_version: str = ""
    locale: str = ""
    content_hash: str = ""
    created_by: str = ""


@dataclass
class SourceManifestItem:
    id: str
    blueprint_project_id: str
    title: str = ""
    source_kind: str = ""
    locator: str = ""
    authority_tier: str = DEFAULT_AUTHORITY_TIER
    rights: SourceRights = field(default_factory=SourceRights)
    created_by: str = ""


@dataclass
class CuratedExtract:
    id: str
    blueprint_project_id: str
    source_item_id: str
    extract_type: str
    text_value: Optional[str] = None
    numeric_value: Optional[Decimal] = None
    unit: str = ""
    as_of_date: Optional[date] = None
    curated_by: str = ""              # operator-declared; NOT authenticated/verified
    curation_status: str = "operator_declared"
    extract_content_fingerprint: str = ""


@dataclass
class DraftArtifact:
    id: str
    blueprint_project_id: str
    config_revision_id: str
    content_hash: str
    artifact_kind: str = "blueprint"
    baseline_artifact_id: Optional[str] = None
    content: dict = field(default_factory=dict)
    artifact_schema_version: str = "1"
    compiler_version: str = ""
    template_set_version: str = ""
    terminology_map_version: str = ""
    locale: str = ""
    assurance_status: str = ASSURANCE_STATUS_DRAFT


@dataclass
class ArtifactInputBinding:
    id: str
    blueprint_project_id: str
    artifact_id: str
    extract_id: str
    input_order: int  # operator-DECLARED position; never derived from generated ids
    extract_content_fingerprint: str = ""


def compute_extract_fingerprint(
    *,
    extract_type: str,
    text_value: Optional[str],
    numeric_value: Optional[Decimal],
    unit: str,
    as_of_date: Optional[date],
) -> str:
    """Stable sha256 over an extract's typed fields, for CHANGE DETECTION only.

    This is intentionally NOT the Wave-3 artifact canonicalization; it is a per-row
    content fingerprint used to bind/track extracts in the many-row binding contract.
    """
    body = {
        "extract_type": extract_type,
        "text_value": text_value,
        "numeric_value": format(numeric_value, "f") if isinstance(numeric_value, Decimal) else None,
        "unit": unit or "",
        "as_of_date": as_of_date.isoformat() if isinstance(as_of_date, date) else None,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
