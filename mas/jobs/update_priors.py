"""
v4.1 MAS — Nightly Prior-Update Job

Runs once per day (cron, Kubernetes CronJob, or `python -m jobs.update_priors`).
Reads resolved outcomes from the last 90 days, computes per-phase Brier and ECE,
and writes new `prior_snapshots` rows that the orchestrator will use as seeds
for the next project. This is what closes the Bayesian loop — without it, the
meta-learner is decorative infrastructure.

Designed to be idempotent: running it twice on the same day just overwrites the
last snapshot for each phase.

Usage:
    python -m jobs.update_priors                   # run against $DATABASE_URL
    python -m jobs.update_priors --dry-run         # compute, don't write
    python -m jobs.update_priors --window-days 30  # shorter rolling window
"""
import os
import sys
import asyncio
import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.calibration import (
    Prediction,
    brier_score,
    expected_calibration_error,
    recommend_prior,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("update_priors")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DEFAULT_WINDOW_DAYS = 90


async def fetch_resolved_by_phase(pool, window_days: int) -> dict[str, list[Prediction]]:
    """Return {phase: [Prediction, ...]} for all resolved outcomes in the window."""
    cutoff = date.today() - timedelta(days=window_days)
    rows = await pool.fetch(
        """
        SELECT phase, predicted_probability, realized
        FROM outcomes
        WHERE realized IS NOT NULL
          AND resolution_date >= $1
        """,
        cutoff,
    )
    by_phase: dict[str, list[Prediction]] = {}
    for r in rows:
        by_phase.setdefault(r["phase"], []).append(
            Prediction(predicted_probability=r["predicted_probability"], realized=r["realized"])
        )
    return by_phase


async def upsert_snapshot(pool, phase: str, snapshot: dict, dry_run: bool):
    """Write a new prior_snapshots row. Replaces today's row if one already exists."""
    if dry_run:
        logger.info(f"[DRY RUN] would write: {phase} {snapshot}")
        return

    await pool.execute(
        """
        DELETE FROM prior_snapshots
        WHERE phase = $1 AND snapshot_date = CURRENT_DATE
        """,
        phase,
    )
    await pool.execute(
        """
        INSERT INTO prior_snapshots (
            phase, snapshot_date, n_outcomes, brier_score, ece,
            recommended_alpha, recommended_beta, direction, notes
        ) VALUES (
            $1, CURRENT_DATE, $2, $3, $4, $5, $6, $7, $8
        )
        """,
        phase,
        snapshot["n_outcomes"],
        snapshot["brier"],
        snapshot["ece"],
        snapshot["recommended_alpha"],
        snapshot["recommended_beta"],
        snapshot["direction"],
        snapshot["notes"],
    )
    logger.info(f"Wrote snapshot for {phase}: Brier={snapshot['brier']:.4f} ECE={snapshot['ece']:.4f}")


async def compute_framework_performance(pool, window_days: int, dry_run: bool):
    """Aggregate framework-level usage stats over the rolling window.

    This is a deliberately minimal stub that writes one row per (framework, phase) pair
    so downstream dashboards never 404 on the framework_performance table. It populates:
      - n_uses: count of phase_outputs in the window whose output_json mentions the framework
      - n_verdict_changes: 0 (placeholder; requires before/after gauntlet diffing)
      - avg_brier_when_used / avg_brier_when_absent / value_score: NULL

    The Brier-based value scoring requires joining phase_outputs.output_json with the
    outcomes table per (framework, hypothesis) pair. That join is deployment-specific
    because the framework-tagging convention inside output_json varies. Implement it
    when (a) you have ≥10 resolved projects to calibrate against and (b) you have
    standardized how phase outputs reference framework names.

    Until then: rows exist, dashboards work, no fabricated Brier deltas.
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=window_days)

    try:
        from config import FRAMEWORKS_BY_PHASE
    except ImportError:
        logger.warning("config.FRAMEWORKS_BY_PHASE not importable — skipping framework stub")
        return

    # Build the (framework_name, phase) inventory from config
    inventory: list[tuple[str, str]] = []
    for phase, frameworks in FRAMEWORKS_BY_PHASE.items():
        for fw_label in frameworks:
            # Strip the [#NN] prefix if present: "[#1] STEELMAN" -> "STEELMAN"
            name = fw_label.split("] ", 1)[-1].strip() if "] " in fw_label else fw_label.strip()
            inventory.append((name, phase))

    written = 0
    for fw_name, phase in inventory:
        # Crude but honest: count phase_outputs rows that mention this framework in their JSON
        try:
            row = await pool.fetchrow(
                """
                SELECT COUNT(*) AS n_uses
                FROM phase_outputs
                WHERE phase = $1
                  AND created_at >= $2
                  AND output_json::text ILIKE '%' || $3 || '%'
                """,
                phase, cutoff, fw_name,
            )
        except Exception as e:
            logger.warning(f"  framework_performance query failed for ({fw_name},{phase}): {e}")
            continue

        n_uses = int(row["n_uses"]) if row else 0
        if dry_run:
            logger.info(f"  [DRY RUN] {fw_name:24s} {phase:12s} n_uses={n_uses}")
            continue

        try:
            # Idempotent: replace today's row for this (framework, phase)
            await pool.execute(
                """
                DELETE FROM framework_performance
                WHERE framework_name = $1 AND phase = $2 AND snapshot_date = CURRENT_DATE
                """,
                fw_name, phase,
            )
            await pool.execute(
                """
                INSERT INTO framework_performance (
                    framework_name, phase, snapshot_date,
                    n_uses, n_verdict_changes,
                    avg_brier_when_used, avg_brier_when_absent, value_score
                ) VALUES (
                    $1, $2, CURRENT_DATE,
                    $3, 0,
                    NULL, NULL, NULL
                )
                """,
                fw_name, phase, n_uses,
            )
            written += 1
        except Exception as e:
            logger.warning(f"  framework_performance write failed for ({fw_name},{phase}): {e}")

    logger.info(f"Framework performance: wrote {written} stub rows over {window_days}d window")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set — cannot run update_priors")
        sys.exit(2)

    try:
        import asyncpg
    except ImportError:
        logger.error("asyncpg not installed")
        sys.exit(2)

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    try:
        by_phase = await fetch_resolved_by_phase(pool, args.window_days)
        logger.info(f"Found resolved outcomes for {len(by_phase)} phases "
                    f"over {args.window_days}-day window")

        for phase, preds in by_phase.items():
            if len(preds) < 5:
                logger.info(f"  {phase}: only {len(preds)} resolved — skipping (need ≥5)")
                continue
            brier = brier_score(preds)
            ece = expected_calibration_error(preds)
            alpha, beta, direction = recommend_prior(preds)
            notes = (
                f"mean_pred={sum(p.predicted_probability for p in preds)/len(preds):.3f} "
                f"mean_real={sum(1 for p in preds if p.realized)/len(preds):.3f}"
            )
            await upsert_snapshot(
                pool,
                phase,
                {
                    "n_outcomes": len(preds),
                    "brier": brier,
                    "ece": ece,
                    "recommended_alpha": alpha,
                    "recommended_beta": beta,
                    "direction": direction,
                    "notes": notes,
                },
                args.dry_run,
            )

        await compute_framework_performance(pool, args.window_days, args.dry_run)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
