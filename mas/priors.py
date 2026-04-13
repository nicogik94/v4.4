"""
v4.2 MAS — Prior Consumption

Reads the latest prior_snapshots row for a phase and returns a calibration hint
to be injected into the system prompt. This is the piece that closes the Bayesian
loop: update_priors.py writes snapshots nightly, and priors.py reads them at phase
start, so the orchestrator's behavior actually shifts based on realized outcomes.

Fail-soft contract: if there is no snapshot, no data, or no database, returns the
empty string — which means the prompt is identical to v4.1 behavior. No hallucinated
priors, no fabricated Brier scores.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory cache keyed by phase. Invalidated by cache_ttl.
_cache: dict[str, tuple[float, str]] = {}
_cache_ttl_seconds = 300  # 5 minutes


async def get_prior_hint(phase: str) -> str:
    """Return a short calibration hint for the given phase, or "" if no data.

    The hint is designed to be appended to the system prompt with no structural
    impact on the output JSON. The LLM sees it as context, not instruction.
    """
    import time
    import store

    now = time.monotonic()
    cached = _cache.get(phase)
    if cached and now - cached[0] < _cache_ttl_seconds:
        return cached[1]

    pool = await store._get_pool()
    if pool is None:
        return ""

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT n_outcomes, brier_score, ece,
                       recommended_alpha, recommended_beta, direction, snapshot_date
                FROM prior_snapshots
                WHERE phase = $1
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                phase,
            )
    except Exception as e:
        logger.warning(f"priors.get_prior_hint({phase}) failed: {e}")
        return ""

    if not row or row["n_outcomes"] < 5:
        _cache[phase] = (now, "")
        return ""

    direction_phrase = {
        "more_confident": "Historical predictions for this phase have been slightly under-confident on positive outcomes.",
        "less_confident": "Historical predictions for this phase have been slightly over-confident on positive outcomes.",
        "none": "Historical predictions for this phase are well-calibrated.",
    }.get(row["direction"], "")

    hint = (
        f"\n\nCALIBRATION NOTE (rolling {row['n_outcomes']} resolved outcomes, "
        f"last updated {row['snapshot_date']}): "
        f"Brier={row['brier_score']:.3f}, ECE={row['ece']:.3f}. "
        f"{direction_phrase} "
        f"Recommended seed prior: α={row['recommended_alpha']:.2f}, β={row['recommended_beta']:.2f}. "
        f"Use this to inform (not override) your phase-specific judgment."
    )
    _cache[phase] = (now, hint)
    return hint


def invalidate_cache(phase: Optional[str] = None):
    """Clear the cache. Called by jobs/update_priors.py after a snapshot write."""
    if phase is None:
        _cache.clear()
    else:
        _cache.pop(phase, None)
