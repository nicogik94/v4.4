"""Lightweight online Bayesian updates for scenario-policy shadow mode."""
from __future__ import annotations

import math
import random

from scenarios.policy import ScenarioPosteriorSnapshot


def beta_mean(alpha: float, beta: float) -> float:
    total = alpha + beta
    return 0.5 if total <= 0 else alpha / total


def beta_variance(alpha: float, beta: float) -> float:
    total = alpha + beta
    if total <= 1:
        return 0.0833333333
    return (alpha * beta) / ((total ** 2) * (total + 1))


def beta_std(alpha: float, beta: float) -> float:
    return math.sqrt(max(beta_variance(alpha, beta), 0.0))


def update_beta(alpha: float, beta: float, *, success: bool) -> tuple[float, float]:
    return (alpha + 1.0, beta) if success else (alpha, beta + 1.0)


def update_normal_mean(
    mean: float,
    precision: float,
    observation: float,
    *,
    observation_precision: float,
) -> tuple[float, float]:
    posterior_precision = max(precision, 1e-9) + max(observation_precision, 1e-9)
    posterior_mean = (
        (max(precision, 1e-9) * mean) + (max(observation_precision, 1e-9) * observation)
    ) / posterior_precision
    return posterior_mean, posterior_precision


def sample_beta(alpha: float, beta: float, rng: random.Random) -> float:
    return rng.betavariate(max(alpha, 1e-6), max(beta, 1e-6))


def sample_positive_normal(mean: float, precision: float, rng: random.Random) -> float:
    std = math.sqrt(1.0 / max(precision, 1e-9))
    return max(0.0, rng.gauss(mean, std))


def reliability_snapshot(snapshot: ScenarioPosteriorSnapshot) -> tuple[float, float]:
    return beta_mean(snapshot.success_alpha, snapshot.success_beta), beta_std(snapshot.success_alpha, snapshot.success_beta)


def feedback_mean(snapshot: ScenarioPosteriorSnapshot) -> float:
    return beta_mean(snapshot.feedback_alpha, snapshot.feedback_beta)
