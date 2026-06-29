"""Model tests for R1.6 consumer-input binding evaluations."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.binding_models import (  # noqa: E402
    ResearchEvidenceConsumerInputBindingCreate,
)


AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64


def _base(**changes):
    values = {
        "project_id": "project",
        "consumer_contract": "report_evidence_register",
        "consumer_contract_version": "report-register.v1",
        "binding_set_id": "register-1",
        "input_key": "entry-1",
        "request_id": "request-1",
        "evidence_intake_item_id": "evidence-item",
        "policy_identifier": "report-evidence-policy",
        "policy_version": "1",
        "policy_parameters_json": {"stale": "qualified"},
        "evaluator_version": "binding-evaluator.v1",
        "freshness_as_of": AS_OF,
        "consumer_disposition": "qualified",
        "disposition_reasons": ("freshness_stale",),
        "evaluated_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingCreate(**values)


def test_item_only_report_shape_is_minimal():
    binding = _base()
    assert binding.claim_intake_item_id is None
    assert binding.claim_support_assessment_id is None
    assert binding.approved_calculation_input_id is None


def test_claim_pair_report_references_are_all_or_none():
    binding = _base(
        claim_intake_item_id="claim-item",
        claim_support_assessment_id="assessment",
    )
    assert binding.claim_support_assessment_id == "assessment"
    with pytest.raises(ValidationError, match="all-or-none"):
        _base(claim_intake_item_id="claim-item")


def test_calculation_requires_only_frozen_input_shape():
    binding = _base(
        consumer_contract="deterministic_calculation",
        consumer_contract_version="automation-roi.v1",
        binding_set_id="calculation-1",
        input_key="periods_per_year",
        approved_calculation_input_id="frozen-input",
    )
    assert binding.approved_calculation_input_id == "frozen-input"
    with pytest.raises(ValidationError, match="requires approved"):
        _base(consumer_contract="deterministic_calculation")
    with pytest.raises(ValidationError, match="scenario or claim-pair"):
        _base(
            consumer_contract="deterministic_calculation",
            approved_calculation_input_id="frozen-input",
            claim_intake_item_id="claim",
            claim_support_assessment_id="pair",
        )


def test_scenario_requires_versioned_fingerprint_but_no_stance():
    binding = _base(
        consumer_contract="scenario_input",
        consumer_contract_version="scenario-observation.v1",
        binding_set_id="scenario-1",
        input_key="observation-1",
        observation_identity_version="scenario-observation.v1",
        observation_identity_fingerprint=FINGERPRINT,
    )
    assert binding.observation_identity_fingerprint == FINGERPRINT
    assert "stance" not in binding.model_dump()
    with pytest.raises(ValidationError, match="versioned observation"):
        _base(consumer_contract="scenario_input")
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        _base(
            consumer_contract="scenario_input",
            observation_identity_version="v1",
            observation_identity_fingerprint="not-a-digest",
        )


@pytest.mark.parametrize(
    "field",
    (
        "consumer_contract_version",
        "binding_set_id",
        "input_key",
        "request_id",
        "policy_identifier",
        "policy_version",
        "evaluator_version",
        "evaluated_by",
    ),
)
def test_identity_and_policy_text_is_nonblank(field):
    with pytest.raises(ValidationError, match="blank"):
        _base(**{field: " \t "})


def test_policy_provenance_and_reasons_are_required():
    with pytest.raises(ValidationError, match="policy parameters"):
        _base(policy_parameters_json={}, policy_fingerprint="")
    with pytest.raises(ValidationError, match="at least one"):
        _base(disposition_reasons=())


def test_as_of_requires_timezone_and_extra_readiness_fields_are_forbidden():
    with pytest.raises(ValidationError, match="timezone"):
        _base(freshness_as_of=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        ResearchEvidenceConsumerInputBindingCreate(
            **_base().model_dump(),
            citation_ready=True,
        )
