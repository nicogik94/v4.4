"""
v4.1 MAS — Prompt Loader
Lazy-loads phase-specific prompt modules and stitches them with the shared router prompt.

Usage:
    from prompts.loader import build_prompt
    system = build_prompt("hypotheses")   # returns router.md + phases/01-hypotheses.md
    short = build_prompt("router_only")   # returns just router.md (for gate decisions)

Design: router.md is ~200 lines (loaded always). Each phase module is 40–80 lines
(loaded only when that phase is active). Prompt-cache-friendly: the router chunk
is identical across all calls and gets cached; only the phase chunk differs.

This replaces the 930-line monolith in archive/legacy-docs/v4-Multi-Agent-System-Prompt.md, cutting
input tokens per call by ~60% on short phases (classify, monitor) and ~40% on
long phases (hypotheses, strategy).
"""
from pathlib import Path
from functools import lru_cache

_PROMPTS_DIR = Path(__file__).parent

PHASE_MODULE_MAP = {
    "classify":   "phases/00-classify.md",
    "hypotheses": "phases/01-hypotheses.md",
    "gauntlet":   "phases/01-hypotheses.md",  # gauntlet lives inside hypotheses module
    "audit":      "phases/02-audit.md",
    "strategy":   "phases/03-strategy.md",
    "sqi":        "phases/03-strategy.md",    # SQI lives inside strategy module
    "monitor":    "phases/04-monitor.md",
    "report":     "phases/05-report.md",
}


@lru_cache(maxsize=16)
def _read(rel: str) -> str:
    return (_PROMPTS_DIR / rel).read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def _router() -> str:
    return _read("router.md")


def build_prompt(phase: str, include_router: bool = True) -> str:
    """Return the system prompt for the given phase.

    phase='router_only' returns just the router (for gate decisions).
    include_router=False returns only the phase module (rarely useful).
    """
    if phase == "router_only":
        return _router()

    module = PHASE_MODULE_MAP.get(phase)
    if not module:
        raise KeyError(f"Unknown phase: {phase}. Known: {list(PHASE_MODULE_MAP.keys())}")

    phase_text = _read(module)
    if not include_router:
        return phase_text

    return f"{_router()}\n\n---\n\n# ACTIVE PHASE MODULE\n\n{phase_text}"


def list_phases() -> list[str]:
    return list(PHASE_MODULE_MAP.keys())


def token_estimate(phase: str) -> int:
    """Very rough: ~4 chars per token."""
    return len(build_prompt(phase)) // 4
