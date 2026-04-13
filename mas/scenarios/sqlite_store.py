"""SQLite sidecar persistence for scenario shadow stats and observations."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from scenarios.policy import ScenarioObservation, ScenarioPosteriorSnapshot


class ScenarioSQLiteStore:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_posteriors (
                    phase TEXT NOT NULL,
                    scenario_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    success_alpha REAL NOT NULL,
                    success_beta REAL NOT NULL,
                    feedback_alpha REAL NOT NULL,
                    feedback_beta REAL NOT NULL,
                    latency_mean_ms REAL NOT NULL,
                    latency_precision REAL NOT NULL,
                    cost_mean_usd REAL NOT NULL,
                    cost_precision REAL NOT NULL,
                    observation_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (phase, scenario_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    scenario_key TEXT NOT NULL,
                    scenario_label TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    baseline_selected_provider TEXT NOT NULL,
                    baseline_selected_model TEXT NOT NULL,
                    actual_provider_used TEXT NOT NULL,
                    actual_model_used TEXT NOT NULL,
                    predicted_success_probability REAL NOT NULL,
                    actual_success INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    cost_usd REAL NOT NULL,
                    feedback_value REAL,
                    expected_utility REAL NOT NULL,
                    win_probability REAL NOT NULL,
                    is_baseline INTEGER NOT NULL,
                    is_best_expected INTEGER NOT NULL,
                    is_recommended INTEGER NOT NULL,
                    fallback_to_baseline INTEGER NOT NULL,
                    hitl_recommended INTEGER NOT NULL,
                    hitl_reason_summary TEXT NOT NULL,
                    sample_count INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scenario_obs_project_phase ON scenario_observations(project_id, phase, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scenario_obs_request ON scenario_observations(request_id, phase)"
            )

    def load_posterior(self, phase: str, scenario_key: str) -> Optional[ScenarioPosteriorSnapshot]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT phase, scenario_key, label, success_alpha, success_beta,
                       feedback_alpha, feedback_beta, latency_mean_ms, latency_precision,
                       cost_mean_usd, cost_precision, observation_count, updated_at
                FROM scenario_posteriors
                WHERE phase = ? AND scenario_key = ?
                """,
                (phase, scenario_key),
            ).fetchone()
        if row is None:
            return None
        return ScenarioPosteriorSnapshot(
            phase=row["phase"],
            scenario_key=row["scenario_key"],
            label=row["label"],
            success_alpha=float(row["success_alpha"]),
            success_beta=float(row["success_beta"]),
            feedback_alpha=float(row["feedback_alpha"]),
            feedback_beta=float(row["feedback_beta"]),
            latency_mean_ms=float(row["latency_mean_ms"]),
            latency_precision=float(row["latency_precision"]),
            cost_mean_usd=float(row["cost_mean_usd"]),
            cost_precision=float(row["cost_precision"]),
            observation_count=int(row["observation_count"]),
            updated_at=str(row["updated_at"]),
        )

    def save_posterior(self, snapshot: ScenarioPosteriorSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scenario_posteriors (
                    phase, scenario_key, label, success_alpha, success_beta,
                    feedback_alpha, feedback_beta, latency_mean_ms, latency_precision,
                    cost_mean_usd, cost_precision, observation_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phase, scenario_key) DO UPDATE SET
                    label = excluded.label,
                    success_alpha = excluded.success_alpha,
                    success_beta = excluded.success_beta,
                    feedback_alpha = excluded.feedback_alpha,
                    feedback_beta = excluded.feedback_beta,
                    latency_mean_ms = excluded.latency_mean_ms,
                    latency_precision = excluded.latency_precision,
                    cost_mean_usd = excluded.cost_mean_usd,
                    cost_precision = excluded.cost_precision,
                    observation_count = excluded.observation_count,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.phase,
                    snapshot.scenario_key,
                    snapshot.label,
                    snapshot.success_alpha,
                    snapshot.success_beta,
                    snapshot.feedback_alpha,
                    snapshot.feedback_beta,
                    snapshot.latency_mean_ms,
                    snapshot.latency_precision,
                    snapshot.cost_mean_usd,
                    snapshot.cost_precision,
                    snapshot.observation_count,
                    snapshot.updated_at,
                ),
            )

    def record_observation(self, observation: ScenarioObservation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scenario_observations (
                    request_id, project_id, phase, scenario_key, scenario_label, observed_at,
                    baseline_selected_provider, baseline_selected_model, actual_provider_used,
                    actual_model_used, predicted_success_probability, actual_success, latency_ms,
                    cost_usd, feedback_value, expected_utility, win_probability, is_baseline,
                    is_best_expected, is_recommended, fallback_to_baseline, hitl_recommended,
                    hitl_reason_summary, sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.request_id,
                    observation.project_id,
                    observation.phase,
                    observation.scenario_key,
                    observation.scenario_label,
                    observation.observed_at,
                    observation.baseline_selected_provider,
                    observation.baseline_selected_model,
                    observation.actual_provider_used,
                    observation.actual_model_used,
                    observation.predicted_success_probability,
                    1 if observation.actual_success else 0,
                    observation.latency_ms,
                    observation.cost_usd,
                    observation.feedback_value,
                    observation.expected_utility,
                    observation.win_probability,
                    1 if observation.is_baseline else 0,
                    1 if observation.is_best_expected else 0,
                    1 if observation.is_recommended else 0,
                    1 if observation.fallback_to_baseline else 0,
                    1 if observation.hitl_recommended else 0,
                    observation.hitl_reason_summary,
                    observation.sample_count,
                ),
            )

    def latest_request_id(self, project_id: str, phase: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT request_id
                FROM scenario_observations
                WHERE project_id = ? AND phase = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_id, phase),
            ).fetchone()
        return "" if row is None else str(row["request_id"])

    def list_request_observations(self, request_id: str, phase: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM scenario_observations
                WHERE request_id = ? AND phase = ?
                ORDER BY id ASC
                """,
                (request_id, phase),
            ).fetchall()
        return list(rows)

    def list_project_phases(self, project_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT phase
                FROM scenario_observations
                WHERE project_id = ?
                ORDER BY phase
                """,
                (project_id,),
            ).fetchall()
        return [str(row["phase"]) for row in rows]

    def list_observations_for_scenario(self, phase: str, scenario_key: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM scenario_observations
                WHERE phase = ? AND scenario_key = ?
                ORDER BY id ASC
                """,
                (phase, scenario_key),
            ).fetchall()
        return list(rows)
