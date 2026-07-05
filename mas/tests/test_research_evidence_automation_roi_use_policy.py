"""Pure policy tests for the R1.6A Automation ROI snapshot foundation."""
from datetime import datetime, timezone

from research_evidence.automation_roi_use_policy import (
    CONSUMER_CONTRACT,
    CONSUMER_CONTRACT_VERSION,
    EVALUATOR_VERSION,
    POLICY_FINGERPRINT,
    POLICY_IDENTIFIER,
    POLICY_PARAMETERS,
    POLICY_VERSION,
    REQUIRED_ROLES,
    canonical_policy_json,
    evaluate_binding_set,
)
from research_evidence.binding_models import (
    ResearchEvidenceConsumerInputBindingRecord,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROJECT = "00000000-0000-0000-0000-000000000001"


def _binding(role: str, index: int, **changes):
    identifier = f"00000000-0000-0000-0000-{index:012d}"
    values = {
        "id": identifier,
        "project_id": PROJECT,
        "consumer_contract": CONSUMER_CONTRACT,
        "consumer_contract_version": CONSUMER_CONTRACT_VERSION,
        "binding_set_id": "set-1",
        "input_key": role,
        "request_id": f"binding-{index}",
        "evidence_intake_item_id": identifier,
        "approved_calculation_input_id": identifier,
        "policy_identifier": POLICY_IDENTIFIER,
        "policy_version": POLICY_VERSION,
        "policy_parameters_json": POLICY_PARAMETERS,
        "policy_fingerprint": POLICY_FINGERPRINT,
        "evaluator_version": EVALUATOR_VERSION,
        "freshness_as_of": NOW,
        "consumer_disposition": "meets_contract",
        "disposition_reasons": ("inputs_observed",),
        "evaluated_by": "operator",
        "calculation_kind": "automation_roi",
        "source_snapshot_id": identifier,
        "source_blob_id": identifier,
        "source_metadata_revision_id": identifier,
        "candidate_fact_revision_id": identifier,
        "fact_metadata_revision_id": identifier,
        "availability_status": True,
        "retention_basis": (),
        "lineage_is_current": True,
        "lineage_basis": (),
        "review_decision_id": identifier,
        "review_decision_sequence": 1,
        "review_status": "approved",
        "freshness_assessment_id": identifier,
        "freshness_assessment_sequence": 1,
        "fresh_through": NOW,
        "freshness_status": "fresh",
        "drift_status": "no_material_drift",
        "binding_sequence": 1,
        "evaluated_at": NOW,
    }
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingRecord(**values)


def _bindings(**role_changes):
    return tuple(
        _binding(role, index + 10, **role_changes.get(role, {}))
        for index, role in enumerate(REQUIRED_ROLES)
    )


def _evaluate(bindings, successors=()):
    return evaluate_binding_set(
        bindings,
        project_id=PROJECT,
        binding_set_id="set-1",
        freshness_as_of=NOW,
        successor_binding_ids=successors,
    )


def test_policy_fingerprint_is_golden_and_canonical():
    assert POLICY_FINGERPRINT == (
        "ca7aadce968c35f9839d79b61a4cbb62"
        "fe9bc05fcc692e6c773ee36ec4a13c9d"
    )
    assert " " not in canonical_policy_json()
    assert canonical_policy_json().startswith('{"binding_record_must_be_current"')


def test_all_positive_conditions_satisfy_and_meets_is_not_sufficient():
    assert _evaluate(_bindings()).status == "satisfies"
    changed = {
        REQUIRED_ROLES[0]: {
            "availability_status": False,
            "consumer_disposition": "meets_contract",
        }
    }
    result = _evaluate(_bindings(**changed))
    assert result.status == "does_not_satisfy"
    assert result.reasons == (
        f"role:{REQUIRED_ROLES[0]}:evidence_unavailable",
    )


def test_precedence_is_hard_then_indeterminate_then_qualified():
    changes = {
        REQUIRED_ROLES[0]: {"freshness_status": "stale"},
        REQUIRED_ROLES[1]: {"drift_status": "indeterminate"},
        REQUIRED_ROLES[2]: {"review_status": "withdrawn"},
    }
    assert _evaluate(_bindings(**changes)).status == "does_not_satisfy"
    changes.pop(REQUIRED_ROLES[2])
    assert _evaluate(_bindings(**changes)).status == "indeterminate"
    changes.pop(REQUIRED_ROLES[1])
    assert _evaluate(_bindings(**changes)).status == "qualified"


def test_successor_contradiction_and_policy_mismatch_are_hard_failures():
    first = _bindings()[0]
    result = _evaluate(_bindings(), successors=(first.id,))
    assert result.status == "does_not_satisfy"
    assert result.reasons[0].endswith("binding_record_superseded")

    changes = {
        REQUIRED_ROLES[0]: {
            "disposition_reasons": ("contradiction_declared",),
            "policy_version": "other",
        }
    }
    result = _evaluate(_bindings(**changes))
    assert result.status == "does_not_satisfy"
    assert any(reason.endswith("policy_version_mismatch") for reason in result.reasons)
    assert any(reason.endswith("contradiction_declared") for reason in result.reasons)


def test_deterministic_bindings_never_consume_claim_semantics():
    binding = _bindings()[0].model_copy(
        update={"semantic_relationship": "contradiction"}
    )
    result = _evaluate((binding, *_bindings()[1:]))
    assert result.status == "does_not_satisfy"
    assert result.reasons[0].endswith("claim_semantics_present")
