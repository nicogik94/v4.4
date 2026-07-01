"""Model and canonical-policy tests for R1.7."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.scenario_input_evaluation_models import (  # noqa: E402
    EVALUATION_POLICY_CANONICAL_JSON,
    EVALUATION_POLICY_FINGERPRINT,
    EVALUATION_POLICY_IDENTIFIER,
    EVALUATION_POLICY_PARAMETERS,
    EVALUATION_POLICY_VERSION,
    EVALUATOR_VERSION,
    REASON_ORDER,
    STATUS_PRECEDENCE,
    OpaqueHypothesisDescriptor,
    ScenarioInputBindingSelection,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestRegistration,
    canonical_manifest_descriptor,
    manifest_fingerprint,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
MANIFEST_VECTOR_DESCRIPTOR = (
    "scenario-input-manifest-v1\n"
    "namespace=11:scenario.ns\n"
    "version=2:v1\n"
    "cardinality=3\n"
    "key=5:alpha\n"
    "key=4:zeta\n"
    "key=7:áccent\n"
)
MANIFEST_VECTOR_SHA256 = (
    "0aaf8b278d98a8821913af720e59b90659e116c9eff5b9d867c314142e5468e6"
)


def _request(**changes):
    values = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "request_id": "evaluation-1",
        "manifest_id": "00000000-0000-0000-0000-000000000002",
        "descriptor": OpaqueHypothesisDescriptor(
            namespace="caller.scenario",
            descriptor_version="draft-v1",
            descriptor={"opaque": [1, "two"]},
            declared_by="operator",
        ),
        "selected_bindings": (
            ScenarioInputBindingSelection(
                binding_id="00000000-0000-0000-0000-000000000003",
                dependence_declaration="not_assessed",
                rationale="No dependence assessment was performed.",
            ),
        ),
        "freshness_as_of": NOW,
    }
    values.update(changes)
    return ScenarioInputEvaluationRequest(**values)


def test_policy_constants_are_exact_and_self_fingerprinting():
    assert EVALUATION_POLICY_IDENTIFIER == "scenario_input.evidence_evaluation"
    assert EVALUATION_POLICY_VERSION == "1"
    assert EVALUATOR_VERSION == "scenario_input.evidence_evaluation.evaluator.v1"
    assert STATUS_PRECEDENCE == (
        "does_not_satisfy",
        "indeterminate",
        "qualified",
        "satisfies",
    )
    assert tuple(EVALUATION_POLICY_PARAMETERS["reason_order"]) == REASON_ORDER
    assert json.loads(EVALUATION_POLICY_CANONICAL_JSON) == (
        EVALUATION_POLICY_PARAMETERS
    )
    assert hashlib.sha256(
        EVALUATION_POLICY_CANONICAL_JSON.encode("utf-8")
    ).hexdigest() == EVALUATION_POLICY_FINGERPRINT
    assert EVALUATION_POLICY_FINGERPRINT == (
        "70d65b9b32fcf55dfef889a5dbde6d9679bf76e7ae57389d559a9416a6c2a699"
    )
    assert (
        EVALUATION_POLICY_PARAMETERS[
            "satisfies_nonempty_manifest_reachable"
        ]
        is False
    )


def test_manifest_descriptor_is_sorted_length_prefixed_utf8():
    descriptor = canonical_manifest_descriptor(
        " scenario.ns ", " v1 ", ("zeta", "áccent", "alpha")
    )
    assert descriptor == MANIFEST_VECTOR_DESCRIPTOR
    assert manifest_fingerprint(descriptor) == MANIFEST_VECTOR_SHA256
    assert hashlib.sha256(descriptor.encode("utf-8")).hexdigest() == (
        MANIFEST_VECTOR_SHA256
    )


def test_manifest_rejects_blank_and_duplicate_normalized_keys():
    with pytest.raises(ValidationError, match="unique"):
        ScenarioInputManifestRegistration(
            project_id="project",
            request_id="request",
            namespace="namespace",
            version="1",
            input_keys=("same", " same "),
            registered_by="actor",
        )
    with pytest.raises(ValidationError, match="blank"):
        ScenarioInputManifestRegistration(
            project_id="project",
            request_id="request",
            namespace="namespace",
            version="1",
            input_keys=(" ",),
            registered_by="actor",
        )


@pytest.mark.parametrize(
    "dependence",
    (
        "not_assessed",
        "declared_dependent",
        "declared_independent_not_verified",
    ),
)
def test_every_allowed_dependence_requires_nonblank_rationale(dependence):
    selection = ScenarioInputBindingSelection(
        binding_id="binding",
        dependence_declaration=dependence,
        rationale="Operator declaration only.",
    )
    assert selection.dependence_declaration == dependence
    with pytest.raises(ValidationError, match="blank"):
        ScenarioInputBindingSelection(
            binding_id="binding",
            dependence_declaration=dependence,
            rationale=" ",
        )


def test_request_rejects_duplicate_uuids_naive_time_and_nonfinite_descriptor():
    selection = _request().selected_bindings[0]
    with pytest.raises(ValidationError, match="unique"):
        _request(selected_bindings=(selection, selection))
    with pytest.raises(ValidationError, match="timezone"):
        _request(freshness_as_of=datetime(2026, 1, 1))
    with pytest.raises(ValidationError, match="non-finite"):
        OpaqueHypothesisDescriptor(
            namespace="opaque",
            descriptor_version="1",
            descriptor={"value": float("inf")},
            declared_by="actor",
        )


def test_database_payload_contains_only_the_canonical_request_contract():
    payload = _request().canonical_database_payload()
    assert set(payload) == {
        "project_id",
        "request_id",
        "manifest_id",
        "descriptor",
        "selected_bindings",
        "freshness_as_of",
    }
    prohibited = {
        "status",
        "reason_codes",
        "fingerprint",
        "sequence",
        "predecessor",
        "truth",
        "independence",
        "prior",
        "likelihood",
        "posterior",
        "run_authorized",
    }
    assert prohibited.isdisjoint(payload)
    assert set(payload["descriptor"]) == {
        "namespace",
        "descriptor_version",
        "descriptor",
        "declared_by",
    }
