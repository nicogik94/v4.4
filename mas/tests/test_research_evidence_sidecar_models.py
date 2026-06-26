"""Model-level tests for R1.1 research-evidence sidecar payloads."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.models import (  # noqa: E402
    ClaimDraftCreate,
    FactMetadataRevisionCreate,
    SourceMetadataRevisionCreate,
)


def test_source_metadata_timestamps_must_be_timezone_aware():
    with pytest.raises(ValidationError):
        SourceMetadataRevisionCreate(
            project_id="00000000-0000-0000-0000-000000000001",
            source_snapshot_id="00000000-0000-0000-0000-000000000002",
            published_at=datetime(2026, 1, 1, 12, 0, 0),
        )

    model = SourceMetadataRevisionCreate(
        project_id="00000000-0000-0000-0000-000000000001",
        source_snapshot_id="00000000-0000-0000-0000-000000000002",
        published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert model.published_at is not None


def test_models_reject_extra_fields_that_belong_to_later_waves():
    with pytest.raises(ValidationError):
        FactMetadataRevisionCreate(
            project_id="00000000-0000-0000-0000-000000000001",
            candidate_fact_revision_id="00000000-0000-0000-0000-000000000002",
            freshness_status="fresh",
        )
    with pytest.raises(ValidationError):
        ClaimDraftCreate(
            project_id="00000000-0000-0000-0000-000000000001",
            claim_text="Draft claim",
            source_snapshot_id="00000000-0000-0000-0000-000000000002",
        )


def test_fact_metadata_does_not_copy_typed_fact_values():
    fields = set(FactMetadataRevisionCreate.model_fields)
    for forbidden in (
        "fact_type",
        "numeric_value",
        "text_value",
        "unit",
        "currency_code",
        "as_of_date",
        "period_basis",
    ):
        assert forbidden not in fields


def test_claim_draft_is_isolated_from_sources_and_facts():
    fields = set(ClaimDraftCreate.model_fields)
    assert "source_snapshot_id" not in fields
    assert "candidate_fact_revision_id" not in fields
    assert "citation_locator" not in fields
