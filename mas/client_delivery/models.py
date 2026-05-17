"""Models for the local Client Delivery Generator v0.5."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PhaseTag = Literal["30d", "60d", "90d"]
KPIIndicatorType = Literal["leading", "lagging", "unknown"]


class Recommendation(BaseModel):
    selected_option: str = ""
    rationale: str = ""
    confidence: str = "unknown"
    evidence_strength: str = "unknown"
    evidence: list[Any] = Field(default_factory=list)


class ExecutionAction(BaseModel):
    phase: PhaseTag = "90d"
    action: str = ""
    owner: str = ""
    dependencies: list[Any] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    notes: str = ""
    success_criteria: str = ""
    status: str = "proposed"


class CriticalAssumption(BaseModel):
    assumption: str = ""
    falsification_trigger: str = ""
    owner: str = ""
    confidence: str = "unknown"
    evidence: list[Any] = Field(default_factory=list)
    notes: str = ""


class KPI(BaseModel):
    name: str = ""
    indicator_type: KPIIndicatorType = "unknown"
    threshold_red: Any = ""
    threshold_amber: Any = ""
    actual_value: Any = ""
    status: str = ""
    owner: str = ""
    cadence: str = ""
    notes: str = ""


class ReviewBlock(BaseModel):
    cadence: str = ""
    owner: str = ""
    reentry_triggers: list[Any] = Field(default_factory=list)
    notes: str = ""


class DeliveryPackage(BaseModel):
    project_id: str
    decision_statement: str = ""
    recommendation: Recommendation = Field(default_factory=Recommendation)
    execution_plan: list[ExecutionAction] = Field(default_factory=list)
    critical_assumptions: list[CriticalAssumption] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    review: ReviewBlock = Field(default_factory=ReviewBlock)
    source_report_excerpt: str = ""
    extraction_warnings: list[str] = Field(default_factory=list)
