"""
v4 Multi-Agent System — Configuration
Model routing, convergence thresholds, re-entry triggers
"""
import os
import json
from dataclasses import dataclass, field
from enum import Enum

from workflow_templates import TECHNOLOGY_READINESS_PHASE_SEQUENCE
from version import APP_VERSION

# ═══ LLM Provider Configuration ═══

class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

@dataclass
class ModelConfig:
    provider: Provider
    model: str
    max_tokens: int = 4000
    temperature: float = 0.3
    thinking_budget: int = 0  # Claude extended thinking (0 = off)


@dataclass
class RuntimeLayerConfig:
    default_provider: Provider = Provider.ANTHROPIC
    routing_strategy: str = "phase"
    cache_enabled: bool = False
    cache_ttl_seconds: int = 300
    phase_model_overrides: dict[str, str] = field(default_factory=dict)
    complexity_model_overrides: dict[str, str] = field(default_factory=dict)
    task_profile_model_candidates: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ScenarioShadowConfig:
    enabled: bool = True
    sqlite_path: str = ""
    max_scenarios: int = 4
    monte_carlo_samples: int = 48
    hard_sample_cap: int = 64
    ensemble_min_probability: float = 0.2
    hitl_min_top_probability: float = 0.55
    hitl_min_margin: float = 0.05
    hitl_min_reliability: float = 0.6


@dataclass
class UploadLayerConfig:
    storage_dir: str = ""
    max_file_bytes: int = 5_000_000
    max_document_chars: int = 12_000
    max_document_chunks: int = 10
    document_chunk_chars: int = 1_400
    max_table_rows: int = 200
    max_table_chunk_rows: int = 25
    max_table_chunks: int = 8
    max_cell_chars: int = 180
    max_row_summary_chars: int = 360


@dataclass(frozen=True)
class OperatorAuthConfig:
    api_key: str = field(default="", repr=False)
    require_operator_auth: bool = False
    implemented: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

# Cost-optimized model routing per phase
# Classify = cheap/fast, Strategy = expensive/deep, Report = mid-tier
MODEL_ROUTING: dict[str, ModelConfig] = {
    "classify": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-haiku-4-5-20251001",
        max_tokens=4000, temperature=0.2
    ),
    "hypotheses": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-opus-4-6",
        max_tokens=6000, temperature=0.4, thinking_budget=15000
    ),
    "gauntlet": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-sonnet-4-6",
        max_tokens=6000, temperature=0.3, thinking_budget=2000
    ),
    "audit": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-sonnet-4-6",
        max_tokens=6000, temperature=0.3, thinking_budget=5000
    ),
    "strategy": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-opus-4-6",
        max_tokens=8000, temperature=0.4, thinking_budget=20000
    ),
    "sqi": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-sonnet-4-6",
        max_tokens=6000, temperature=0.2, thinking_budget=5000
    ),
    "monitor": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-sonnet-4-6",
        max_tokens=4000, temperature=0.3
    ),
    "report": ModelConfig(
        provider=Provider.ANTHROPIC, model="claude-sonnet-4-6",
        max_tokens=8000, temperature=0.3, thinking_budget=10000
    ),
}
for _technology_readiness_phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
    MODEL_ROUTING.setdefault(
        _technology_readiness_phase,
        ModelConfig(
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-6",
            max_tokens=6000,
            temperature=0.2,
            thinking_budget=5000,
        ),
    )

# Fallback chain: primary → retry 3x → fallback → retry 2x → degraded
FALLBACK_CHAIN = {
    Provider.ANTHROPIC: [
        "claude-sonnet-4-6",      # primary fallback
        "claude-haiku-4-5-20251001",  # degraded
    ],
    Provider.OPENAI: [
        "gpt-5",                 # primary fallback (family alias)
        "gpt-5-mini",            # degraded
    ],
}

TASK_PROFILE_BY_PHASE: dict[str, str] = {
    "classify": "fast_classification",
    "hypotheses": "deep_reasoning",
    "gauntlet": "deep_reasoning",
    "audit": "deep_reasoning",
    "strategy": "deep_reasoning",
    "sqi": "strict_structured_output",
    "monitor": "monitoring_ops",
    "report": "report_synthesis",
}
TASK_PROFILE_BY_PHASE.update({
    "scope": "strict_structured_output",
    "scientific_inventory": "deep_reasoning",
    "trl_diagnosis": "deep_reasoning",
    "research_industry_alignment": "deep_reasoning",
    "ip_protection_axis": "deep_reasoning",
    "next_level_recommendations": "deep_reasoning",
    "technical_validation_plan": "deep_reasoning",
    "industrial_transfer_plan": "deep_reasoning",
    "readiness_roadmap": "deep_reasoning",
    "executive_summary": "report_synthesis",
})

# Candidate aliases are resolved by runtime/provider_gateway.py.  "phase_default"
# always means the MODEL_ROUTING entry for the active phase, preserving the
# current default path while making task-profile fallbacks explicit and testable.
TASK_PROFILE_MODEL_CANDIDATES: dict[str, list[str]] = {
    "fast_classification": [
        "phase_default",
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-5-mini",
    ],
    "deep_reasoning": [
        "phase_default",
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-5",
    ],
    "strict_structured_output": [
        "phase_default",
        "anthropic:claude-haiku-4-5-20251001",
        "openai:gpt-5-mini",
    ],
    "report_synthesis": [
        "phase_default",
        "anthropic:claude-opus-4-6",
        "openai:gpt-5",
    ],
    "monitoring_ops": [
        "phase_default",
        "anthropic:claude-haiku-4-5-20251001",
        "openai:gpt-5-mini",
    ],
}

# ═══ Phase Configuration ═══

PHASES = ["classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"]
PHASE_ORDER = ["classify", "hypotheses", "audit", "strategy", "monitor", "report"]  # main 6
SUPPORT_PHASES = ["gauntlet", "sqi"]  # run within parent phases

# ═══ Convergence Gates ═══

@dataclass
class GateConfig:
    """Convergence criteria for each phase exit gate"""
    min_confidence: float = 0.75
    required_fields: list[str] = field(default_factory=list)
    bayesian_threshold: float | None = None  # BF threshold
    dq_minimum: float | None = None          # DQ % threshold

GATE_CONFIGS: dict[str, GateConfig] = {
    "classify": GateConfig(
        min_confidence=0.7,
        required_fields=["domain", "bf", "variety_gaps", "ooda", "dq"],
        bayesian_threshold=10.0,
        dq_minimum=60.0
    ),
    "hypotheses": GateConfig(
        min_confidence=0.7,
        required_fields=["hypotheses"],
        bayesian_threshold=None,
        dq_minimum=60.0
    ),
    "audit": GateConfig(
        min_confidence=0.65,
        required_fields=["fmea", "top_findings"],
    ),
    "strategy": GateConfig(
        min_confidence=0.6,
        required_fields=["strategies", "preliminary_verdicts"],
    ),
    "monitor": GateConfig(
        min_confidence=0.5,
        required_fields=[],  # human-driven
    ),
    "report": GateConfig(
        min_confidence=0.7,
        required_fields=[],
    ),
    "scope": GateConfig(
        min_confidence=0.6,
        required_fields=["technology_name", "assessment_boundary", "target_environment"],
    ),
    "scientific_inventory": GateConfig(
        min_confidence=0.6,
        required_fields=["scientific_basis", "evidence_items"],
    ),
    "trl_diagnosis": GateConfig(
        min_confidence=0.6,
        required_fields=["current_trl", "target_trl", "why_not_higher", "legal_or_certification_disclaimer"],
    ),
    "research_industry_alignment": GateConfig(
        min_confidence=0.6,
        required_fields=["criteria_scores", "overall_alignment_score"],
    ),
    "ip_protection_axis": GateConfig(
        min_confidence=0.6,
        required_fields=["ip_risk_notes", "specialist_review_required"],
    ),
    "next_level_recommendations": GateConfig(
        min_confidence=0.6,
        required_fields=["current_trl", "next_target_trl", "required_evidence", "advancement_criteria"],
    ),
    "technical_validation_plan": GateConfig(
        min_confidence=0.6,
        required_fields=["validation_tests", "acceptance_criteria", "evidence_to_collect"],
    ),
    "industrial_transfer_plan": GateConfig(
        min_confidence=0.6,
        required_fields=["ideal_industrial_partner", "minimum_transfer_package", "evidence_required_before_transfer"],
    ),
    "readiness_roadmap": GateConfig(
        min_confidence=0.6,
        required_fields=["roadmap_phases", "decision_gates", "go_no_go_criteria"],
    ),
    "executive_summary": GateConfig(
        min_confidence=0.6,
        required_fields=["current_trl", "readiness_verdict_code", "readiness_verdict", "operator_summary"],
    ),
}

# ═══ Re-entry Triggers ═══

REENTRY_TRIGGERS = {
    "R1": {"condition": "assumption_shift_gt_2sigma", "target": "hypotheses",
            "description": "Assumption shifted >2σ from prior"},
    "R2": {"condition": "domain_reclassified", "target": "classify",
            "description": "Domain reclassified (e.g., Complicated → Complex)"},
    "R3": {"condition": "scope_change", "target": "classify",
            "description": "Significant scope change detected"},
    "R4": {"condition": "portfolio_rho_gt_05", "target": "hypotheses",
            "description": "Portfolio correlation ρ > 0.5"},
    "R5": {"condition": "all_hypotheses_futile", "target": "hypotheses",
            "description": "All hypotheses reached futility threshold"},
    "R6": {"condition": "majority_futile", "target": "audit",
            "description": ">50% of hypotheses futile"},
    "R7": {"condition": "slo_3_cycles", "target": "strategy",
            "description": "Strategy SLO breached 3+ cycles"},
    "R8": {"condition": "commitment_below_50", "target": "monitor",
            "description": "Commitment score < 50%"},
}

# ═══ Downstream Invalidation Map ═══

INVALIDATION_MAP: dict[str, list[str]] = {
    "classify": ["hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"],
    "hypotheses": ["gauntlet", "audit", "strategy", "sqi", "monitor", "report"],
    "gauntlet": ["audit", "strategy", "sqi", "monitor", "report"],
    "audit": ["strategy", "sqi", "monitor", "report"],
    "strategy": ["sqi", "monitor", "report"],
    "sqi": [],
    "monitor": ["report"],
    "report": [],
}

# ═══ Framework Distribution per Phase ═══

FRAMEWORKS_BY_PHASE: dict[str, list[str]] = {
    "classify": [
        "[#16] Cynefin", "[#30] Requisite Variety", "[#17] OODA",
        "[#12] RPD", "[#13] Sensemaking", "[#4] BAYES_LITE"
    ],
    "hypotheses": [
        "[#21] HDD", "[#4] BAYES_LITE", "[#25] EVOI",
        "[#26] Thompson Sampling", "[#27] Information Gain", "[#3] DOUBLE_CRUX"
    ],
    "gauntlet": [
        "[#1] STEELMAN", "[#2] PREMORTEM", "[#3] DOUBLE_CRUX",
        "[#4] BAYES_LITE", "[#5] SISTÉMICO", "[#6] LADDER",
        "[#7] FMEA", "[#8] HAZOP", "[#9] FTA", "[#28] Red Teaming"
    ],
    "audit": [
        "[#7] FMEA", "[#8] HAZOP", "[#9] FTA", "[#10] Swiss Cheese",
        "[#11] STPA", "[#14] Mental Models", "[#22] ODD",
        "[#18] Chaos Engineering", "[#19] Circuit Breaker", "[#20] Canary"
    ],
    "strategy": [
        "[#15] Prospect Theory", "[#2] PREMORTEM", "[#5] SISTÉMICO",
        "[#6] LADDER", "[#25] EVOI", "[#1] STEELMAN"
    ],
    "sqi": [
        "[#1] STEELMAN", "[#2] PREMORTEM", "[#28] Red Teaming",
        "[#5] SISTÉMICO", "[#6] LADDER", "[#15] Prospect Theory"
    ],
    "monitor": [
        "[#17] OODA", "[#18] Chaos Engineering", "[#19] Circuit Breaker",
        "[#20] Canary", "[#29] HRO"
    ],
    "report": [
        "[#24] Causal Inference", "[#10] Swiss Cheese", "[#29] HRO",
        "[#28] Red Teaming", "[#23] Ablation"
    ],
}

# ═══ Infrastructure ═══

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/workflow_v4")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPERATOR_API_KEY_ENV = "MAS_OPERATOR_API_KEY"
REQUIRE_OPERATOR_AUTH_ENV = "MAS_REQUIRE_OPERATOR_AUTH"
OPERATOR_AUTH_HEADER = "X-MAS-Operator-Key"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_operator_auth_config() -> OperatorAuthConfig:
    return OperatorAuthConfig(
        api_key=os.getenv(OPERATOR_API_KEY_ENV, "").strip(),
        require_operator_auth=_env_flag(REQUIRE_OPERATOR_AUTH_ENV, default=False),
    )


def _env_json_map(name: str) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _env_json_candidate_map(name: str) -> dict[str, list[str]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    candidates: dict[str, list[str]] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            candidates[str(key)] = [str(item) for item in value]
        elif isinstance(value, str):
            candidates[str(key)] = [value]
    return candidates


RUNTIME_LAYER = RuntimeLayerConfig(
    default_provider=Provider(os.getenv("DEFAULT_PROVIDER", Provider.ANTHROPIC.value)),
    routing_strategy=os.getenv("ROUTING_STRATEGY", "phase"),
    cache_enabled=_env_flag("SEMANTIC_CACHE_ENABLED", default=False),
    cache_ttl_seconds=int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "300")),
    phase_model_overrides=_env_json_map("PHASE_MODEL_OVERRIDES"),
    complexity_model_overrides=_env_json_map("COMPLEXITY_MODEL_OVERRIDES"),
    task_profile_model_candidates=_env_json_candidate_map("TASK_PROFILE_MODEL_CANDIDATES"),
)

SCENARIO_SHADOW = ScenarioShadowConfig(
    enabled=_env_flag("SCENARIO_SHADOW_ENABLED", default=True),
    sqlite_path=os.getenv(
        "SCENARIO_SHADOW_SQLITE_PATH",
        os.path.join(os.path.dirname(__file__), "scenario_shadow.sqlite3"),
    ),
    max_scenarios=max(1, int(os.getenv("SCENARIO_SHADOW_MAX_SCENARIOS", "4"))),
    monte_carlo_samples=max(1, int(os.getenv("SCENARIO_SHADOW_MONTE_CARLO_SAMPLES", "48"))),
    hard_sample_cap=max(1, int(os.getenv("SCENARIO_SHADOW_HARD_SAMPLE_CAP", "64"))),
    ensemble_min_probability=float(os.getenv("SCENARIO_SHADOW_ENSEMBLE_MIN_PROBABILITY", "0.2")),
    hitl_min_top_probability=float(os.getenv("SCENARIO_SHADOW_HITL_MIN_TOP_PROBABILITY", "0.55")),
    hitl_min_margin=float(os.getenv("SCENARIO_SHADOW_HITL_MIN_MARGIN", "0.05")),
    hitl_min_reliability=float(os.getenv("SCENARIO_SHADOW_HITL_MIN_RELIABILITY", "0.6")),
)

UPLOAD_LAYER = UploadLayerConfig(
    storage_dir=os.getenv(
        "UPLOAD_STORAGE_DIR",
        os.path.join(os.path.dirname(__file__), "upload_store"),
    ),
    max_file_bytes=max(1, int(os.getenv("UPLOAD_MAX_FILE_BYTES", "5000000"))),
    max_document_chars=max(500, int(os.getenv("UPLOAD_MAX_DOCUMENT_CHARS", "12000"))),
    max_document_chunks=max(1, int(os.getenv("UPLOAD_MAX_DOCUMENT_CHUNKS", "10"))),
    document_chunk_chars=max(250, int(os.getenv("UPLOAD_DOCUMENT_CHUNK_CHARS", "1400"))),
    max_table_rows=max(1, int(os.getenv("UPLOAD_MAX_TABLE_ROWS", "200"))),
    max_table_chunk_rows=max(1, int(os.getenv("UPLOAD_MAX_TABLE_CHUNK_ROWS", "25"))),
    max_table_chunks=max(1, int(os.getenv("UPLOAD_MAX_TABLE_CHUNKS", "8"))),
    max_cell_chars=max(20, int(os.getenv("UPLOAD_MAX_CELL_CHARS", "180"))),
    max_row_summary_chars=max(60, int(os.getenv("UPLOAD_MAX_ROW_SUMMARY_CHARS", "360"))),
)

MAX_RETRIES = 3
RETRY_DELAYS = [1.0, 3.0, 10.0]
REQUEST_TIMEOUT = 300  # seconds
CIRCUIT_BREAKER_THRESHOLD = 5  # failures before opening
CIRCUIT_BREAKER_COOLDOWN = 60  # seconds

# ═══ Meta-Learning ═══

BRIER_TARGET = 0.15  # target Brier score for maturity level 5
ECE_TARGET = 0.05    # target Expected Calibration Error
CALIBRATION_BINS = 10  # number of bins for calibration curves
