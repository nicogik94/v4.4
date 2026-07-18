"""Research Evidence metadata, assessments, and consumer-input bindings.

This package attaches operator-declared metadata and draft-only intake groupings
to existing Slice A provenance and records item-, pair-, and consumer-input
scoped assessments.
It creates no parallel blob, snapshot, fact, retention, calculation, report,
scenario, prompt, retrieval, or downstream-use system.
"""
from __future__ import annotations

import config

from .automation_roi_execution_models import (
    AutomationRoiCalculationResult,
    AutomationRoiExecutionRequest,
    CalculationStatus as AutomationRoiCalculationStatus,
    RoiPercentStatus as AutomationRoiPercentStatus,
)
from .automation_roi_execution_policy import (
    ASSUMPTION_FINGERPRINT as AUTOMATION_ROI_ASSUMPTION_FINGERPRINT,
    ASSUMPTION_SET_VERSION as AUTOMATION_ROI_ASSUMPTION_SET_VERSION,
    FORMULA_FINGERPRINT as AUTOMATION_ROI_FORMULA_FINGERPRINT,
    FORMULA_IDENTIFIER as AUTOMATION_ROI_FORMULA_IDENTIFIER,
    FORMULA_VERSION as AUTOMATION_ROI_FORMULA_VERSION,
)
from .automation_roi_execution_repository import (
    AutomationRoiExecutionConflict,
    AutomationRoiExecutionIntegrityError,
    AutomationRoiExecutionRejected,
    AutomationRoiExecutionRepositoryError,
)
from .automation_roi_execution_service import (
    AutomationRoiExecutionDisabled,
    AutomationRoiExecutionTransactionError,
    execute_automation_roi,
)
from .automation_roi_use_models import (
    AutomationRoiInputSnapshotBindingRecord,
    AutomationRoiInputSnapshotCreate,
    AutomationRoiInputSnapshotRecord,
    CompletenessStatus,
    PolicyEvaluationStatus,
)
from .automation_roi_use_policy import (
    CONSUMER_CONTRACT as AUTOMATION_ROI_CONSUMER_CONTRACT,
    CONSUMER_CONTRACT_VERSION as AUTOMATION_ROI_CONSUMER_CONTRACT_VERSION,
    EVALUATOR_VERSION as AUTOMATION_ROI_EVALUATOR_VERSION,
    POLICY_FINGERPRINT as AUTOMATION_ROI_POLICY_FINGERPRINT,
    POLICY_IDENTIFIER as AUTOMATION_ROI_POLICY_IDENTIFIER,
    POLICY_PARAMETERS as AUTOMATION_ROI_POLICY_PARAMETERS,
    POLICY_VERSION as AUTOMATION_ROI_POLICY_VERSION,
    REQUIRED_ROLES as AUTOMATION_ROI_REQUIRED_ROLES,
)
from .automation_roi_use_repository import (
    AutomationRoiBindingSetIncomplete,
    AutomationRoiSnapshotIntegrityError,
    AutomationRoiSnapshotRequestConflict,
    AutomationRoiUseRepositoryError,
)
from .automation_roi_use_service import (
    AutomationRoiUseDisabled,
    AutomationRoiUseTransactionError,
    record_automation_roi_input_snapshot,
)
from .binding_models import (
    BindingReviewStatus,
    ConsumerContract,
    ConsumerDisposition,
    ResearchEvidenceConsumerInputBindingCreate,
    ResearchEvidenceConsumerInputBindingRecord,
)
from .binding_service import (
    ResearchEvidenceBindingDisabled,
    ResearchEvidenceBindingTransactionError,
    binding_availability_status,
    binding_consumer_disposition,
    binding_drift_status,
    binding_evidence_linkage,
    binding_freshness_status,
    binding_lineage_is_current,
    binding_locator_resolution,
    binding_retention_basis,
    binding_review_status,
    binding_semantic_relationship,
    get_effective_consumer_input_binding,
    record_consumer_input_binding,
)
from .claim_support_models import (
    EvidenceLinkage,
    LocatorResolution,
    ResearchEvidenceClaimSupportAssessmentCreate,
    ResearchEvidenceClaimSupportAssessmentRecord,
    SemanticRelationship,
)
from .claim_support_service import (
    ResearchEvidenceClaimSupportDisabled,
    ResearchEvidenceClaimSupportTransactionError,
    claim_support_claim_is_available,
    claim_support_claim_lineage_is_current,
    claim_support_claim_review_decision,
    claim_support_evidence_freshness_status_as_of,
    claim_support_evidence_is_available,
    claim_support_evidence_lineage_is_current,
    claim_support_evidence_linkage,
    claim_support_evidence_review_decision,
    claim_support_locator_resolution,
    claim_support_semantic_relationship,
    get_effective_claim_support_assessment,
    list_effective_claim_support_assessments,
    record_claim_support_assessment,
)
from .freshness_models import (
    DriftStatus,
    FreshnessStatus,
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
)
from .freshness_service import (
    ResearchEvidenceFreshnessDisabled,
    ResearchEvidenceFreshnessNotApplicable,
    ResearchEvidenceFreshnessTransactionError,
    item_freshness_status_as_of,
    record_item_freshness_assessment,
)
from .intake_models import (
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeItemRecord,
    ResearchEvidenceIntakeRecord,
)
from .intake_service import (
    ResearchEvidenceIntakeDisabled,
    ResearchEvidenceIntakeTransactionError,
    ResearchEvidenceSnapshotUnavailable,
    create_intake,
    create_intake_item,
)
from .pack_models import (
    ConfidenceLabel,
    EpistemicStatus,
    ProbabilityProvidedBy,
    ResearchEvidencePackAggregate,
    ResearchEvidencePackAuthorizedClaim,
    ResearchEvidencePackAuthorizedEvidence,
    ResearchEvidencePackAuthorizedRelationship,
    ResearchEvidencePackAuthorizedSource,
    ResearchEvidencePackClaimAnnotation,
    ResearchEvidencePackContext,
    ResearchEvidencePackCounts,
    ResearchEvidencePackExplicitProbability,
    ResearchEvidencePackQuery,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceClaimAnnotationRevisionRecord,
    ResearchEvidenceExplicitProbability,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceProjectContextRevisionRecord,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionRecord,
    UsageAuthorizationDecisionType,
    UsageScope,
)
from .pack_service import (
    ResearchEvidencePackDisabled,
    ResearchEvidencePackEligibilityError,
    ResearchEvidencePackLimitError,
    ResearchEvidencePackServiceError,
    ResearchEvidencePackTransactionError,
    assemble_research_evidence_pack,
    claim_evidence_usage_is_authorized,
    get_effective_claim_annotation_revision,
    get_effective_project_context_revision,
    get_effective_usage_authorization_decision,
    list_effective_project_annotations,
    list_effective_project_authorizations,
    record_claim_annotation_revision,
    record_project_context_revision,
    record_usage_authorization_decision,
)
from .review_models import (
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)
from .review_service import (
    ResearchEvidenceReviewDisabled,
    ResearchEvidenceReviewTransactionError,
    ResearchEvidenceReviewUnavailable,
    item_is_eligible_for_future_use,
    record_item_review_decision,
)
from .scenario_input_evaluation_models import (
    DependenceDeclaration,
    EVALUATION_POLICY_CANONICAL_JSON,
    EVALUATION_POLICY_FINGERPRINT,
    EVALUATION_POLICY_IDENTIFIER,
    EVALUATION_POLICY_PARAMETERS,
    EVALUATION_POLICY_VERSION,
    EVALUATOR_VERSION,
    OpaqueHypothesisDescriptor,
    REASON_ORDER,
    STATUS_PRECEDENCE,
    ScenarioInputBindingSelection,
    ScenarioInputEvaluationInputRecord,
    ScenarioInputEvaluationRecord,
    ScenarioInputEvaluationRequest,
    ScenarioInputEvaluationStatus,
    ScenarioInputManifestItemRecord,
    ScenarioInputManifestRecord,
    ScenarioInputManifestRegistration,
    canonical_manifest_descriptor,
    manifest_fingerprint,
)
from .scenario_input_evaluation_service import (
    ScenarioInputEvaluationDisabled,
    ScenarioInputEvaluationTransactionError,
    create_scenario_input_evaluation,
    register_scenario_input_manifest,
)

SCHEMA_MIGRATION = "v51_research_evidence_sidecar_foundation.sql"
INTAKE_SCHEMA_MIGRATION = "v53_research_evidence_intake_foundation.sql"
REVIEW_SCHEMA_MIGRATION = "v54_research_evidence_review_foundation.sql"
FRESHNESS_SCHEMA_MIGRATION = "v55_research_evidence_freshness_foundation.sql"
CLAIM_SUPPORT_SCHEMA_MIGRATION = (
    "v56_research_evidence_claim_support_foundation.sql"
)
BINDING_SCHEMA_MIGRATION = "v57_research_evidence_binding_foundation.sql"
SCENARIO_INPUT_EVALUATION_SCHEMA_MIGRATION = (
    "v58_research_evidence_scenario_input_evaluation_foundation.sql"
)
AUTOMATION_ROI_USE_SCHEMA_MIGRATION = (
    "v59_research_evidence_automation_roi_input_snapshot.sql"
)
PACK_SCHEMA_MIGRATION = "v61_research_evidence_pack_foundation.sql"


def is_enabled() -> bool:
    return config.research_evidence_enabled()


__all__ = [
    "AUTOMATION_ROI_CONSUMER_CONTRACT",
    "AUTOMATION_ROI_CONSUMER_CONTRACT_VERSION",
    "AUTOMATION_ROI_EVALUATOR_VERSION",
    "AUTOMATION_ROI_POLICY_FINGERPRINT",
    "AUTOMATION_ROI_POLICY_IDENTIFIER",
    "AUTOMATION_ROI_POLICY_PARAMETERS",
    "AUTOMATION_ROI_POLICY_VERSION",
    "AUTOMATION_ROI_REQUIRED_ROLES",
    "AUTOMATION_ROI_USE_SCHEMA_MIGRATION",
    "AutomationRoiBindingSetIncomplete",
    "AutomationRoiInputSnapshotBindingRecord",
    "AutomationRoiInputSnapshotCreate",
    "AutomationRoiInputSnapshotRecord",
    "AutomationRoiSnapshotIntegrityError",
    "AutomationRoiSnapshotRequestConflict",
    "AutomationRoiUseDisabled",
    "AutomationRoiUseRepositoryError",
    "AutomationRoiUseTransactionError",
    "BINDING_SCHEMA_MIGRATION",
    "BindingReviewStatus",
    "CLAIM_SUPPORT_SCHEMA_MIGRATION",
    "ConsumerContract",
    "ConsumerDisposition",
    "CompletenessStatus",
    "DependenceDeclaration",
    "DriftStatus",
    "EvidenceLinkage",
    "EVALUATION_POLICY_CANONICAL_JSON",
    "EVALUATION_POLICY_FINGERPRINT",
    "EVALUATION_POLICY_IDENTIFIER",
    "EVALUATION_POLICY_PARAMETERS",
    "EVALUATION_POLICY_VERSION",
    "EVALUATOR_VERSION",
    "FRESHNESS_SCHEMA_MIGRATION",
    "FreshnessStatus",
    "INTAKE_SCHEMA_MIGRATION",
    "LocatorResolution",
    "OpaqueHypothesisDescriptor",
    "PolicyEvaluationStatus",
    "REASON_ORDER",
    "REVIEW_SCHEMA_MIGRATION",
    "SCHEMA_MIGRATION",
    "SCENARIO_INPUT_EVALUATION_SCHEMA_MIGRATION",
    "STATUS_PRECEDENCE",
    "SemanticRelationship",
    "ResearchEvidenceClaimSupportAssessmentCreate",
    "ResearchEvidenceClaimSupportAssessmentRecord",
    "ResearchEvidenceClaimSupportDisabled",
    "ResearchEvidenceClaimSupportTransactionError",
    "ResearchEvidenceConsumerInputBindingCreate",
    "ResearchEvidenceConsumerInputBindingRecord",
    "ResearchEvidenceBindingDisabled",
    "ResearchEvidenceBindingTransactionError",
    "ResearchEvidenceIntakeCreate",
    "ResearchEvidenceFreshnessDisabled",
    "ResearchEvidenceFreshnessNotApplicable",
    "ResearchEvidenceFreshnessTransactionError",
    "ResearchEvidenceIntakeItemFreshnessAssessmentCreate",
    "ResearchEvidenceIntakeItemFreshnessAssessmentRecord",
    "ResearchEvidenceIntakeDisabled",
    "ResearchEvidenceIntakeItemCreate",
    "ResearchEvidenceIntakeItemRecord",
    "ResearchEvidenceIntakeRecord",
    "ResearchEvidenceIntakeItemReviewDecisionCreate",
    "ResearchEvidenceIntakeItemReviewDecisionRecord",
    "ResearchEvidenceIntakeTransactionError",
    "ResearchEvidenceSnapshotUnavailable",
    "ResearchEvidenceReviewDisabled",
    "ResearchEvidenceReviewTransactionError",
    "ResearchEvidenceReviewUnavailable",
    "ScenarioInputBindingSelection",
    "ScenarioInputEvaluationDisabled",
    "ScenarioInputEvaluationInputRecord",
    "ScenarioInputEvaluationRecord",
    "ScenarioInputEvaluationRequest",
    "ScenarioInputEvaluationStatus",
    "ScenarioInputEvaluationTransactionError",
    "ScenarioInputManifestItemRecord",
    "ScenarioInputManifestRecord",
    "ScenarioInputManifestRegistration",
    "claim_support_claim_is_available",
    "claim_support_claim_lineage_is_current",
    "claim_support_claim_review_decision",
    "claim_support_evidence_freshness_status_as_of",
    "claim_support_evidence_is_available",
    "claim_support_evidence_lineage_is_current",
    "claim_support_evidence_linkage",
    "claim_support_evidence_review_decision",
    "claim_support_locator_resolution",
    "claim_support_semantic_relationship",
    "binding_availability_status",
    "binding_consumer_disposition",
    "binding_drift_status",
    "binding_evidence_linkage",
    "binding_freshness_status",
    "binding_lineage_is_current",
    "binding_locator_resolution",
    "binding_retention_basis",
    "binding_review_status",
    "binding_semantic_relationship",
    "create_intake",
    "create_intake_item",
    "create_scenario_input_evaluation",
    "get_effective_claim_support_assessment",
    "get_effective_consumer_input_binding",
    "item_freshness_status_as_of",
    "item_is_eligible_for_future_use",
    "is_enabled",
    "list_effective_claim_support_assessments",
    "record_claim_support_assessment",
    "record_automation_roi_input_snapshot",
    "record_consumer_input_binding",
    "record_item_review_decision",
    "record_item_freshness_assessment",
    "register_scenario_input_manifest",
    "canonical_manifest_descriptor",
    "manifest_fingerprint",
    "PACK_SCHEMA_MIGRATION",
    "ConfidenceLabel",
    "EpistemicStatus",
    "ProbabilityProvidedBy",
    "ResearchEvidenceClaimAnnotationRevisionCreate",
    "ResearchEvidenceClaimAnnotationRevisionRecord",
    "ResearchEvidenceExplicitProbability",
    "ResearchEvidencePackDisabled",
    "ResearchEvidencePackAggregate",
    "ResearchEvidencePackAuthorizedClaim",
    "ResearchEvidencePackAuthorizedEvidence",
    "ResearchEvidencePackAuthorizedRelationship",
    "ResearchEvidencePackAuthorizedSource",
    "ResearchEvidencePackClaimAnnotation",
    "ResearchEvidencePackContext",
    "ResearchEvidencePackCounts",
    "ResearchEvidencePackExplicitProbability",
    "ResearchEvidencePackQuery",
    "ResearchEvidencePackEligibilityError",
    "ResearchEvidencePackLimitError",
    "ResearchEvidencePackServiceError",
    "ResearchEvidencePackTransactionError",
    "ResearchEvidenceProjectContextRevisionCreate",
    "ResearchEvidenceProjectContextRevisionRecord",
    "ResearchEvidenceUsageAuthorizationDecisionCreate",
    "ResearchEvidenceUsageAuthorizationDecisionRecord",
    "UsageAuthorizationDecisionType",
    "UsageScope",
    "assemble_research_evidence_pack",
    "claim_evidence_usage_is_authorized",
    "get_effective_claim_annotation_revision",
    "get_effective_project_context_revision",
    "get_effective_usage_authorization_decision",
    "list_effective_project_annotations",
    "list_effective_project_authorizations",
    "record_claim_annotation_revision",
    "record_project_context_revision",
    "record_usage_authorization_decision",
]
