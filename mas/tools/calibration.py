"""
v4.1 MAS — Calibration helpers
Brier score, Expected Calibration Error (ECE), and Beta-distribution prior updates.

All functions operate on plain Python lists/tuples for portability — no numpy dependency
in the hot path so update_priors.py can run in a minimal container.
"""
from dataclasses import dataclass
from typing import Sequence


@dataclass
class Prediction:
    """A single resolved prediction."""
    predicted_probability: float   # 0..1, the model's stated belief
    realized: bool                 # the actual outcome


def brier_score(predictions: Sequence[Prediction]) -> float:
    """Mean squared error between predicted probability and realized outcome.
    Lower is better. 0 = perfect, 0.25 = uniform random on binary outcomes.
    """
    if not predictions:
        return 0.0
    total = 0.0
    for p in predictions:
        actual = 1.0 if p.realized else 0.0
        total += (p.predicted_probability - actual) ** 2
    return total / len(predictions)


def expected_calibration_error(
    predictions: Sequence[Prediction],
    n_bins: int = 10,
) -> float:
    """ECE: weighted average of |accuracy - confidence| across probability bins.
    Measures how well the stated confidence tracks the realized frequency.
    0 = perfectly calibrated, 1 = completely miscalibrated.
    """
    if not predictions:
        return 0.0
    bins: list[list[Prediction]] = [[] for _ in range(n_bins)]
    for p in predictions:
        # Clamp to [0, n_bins-1]
        idx = min(int(p.predicted_probability * n_bins), n_bins - 1)
        bins[idx].append(p)

    total = len(predictions)
    ece = 0.0
    for bin_preds in bins:
        if not bin_preds:
            continue
        weight = len(bin_preds) / total
        avg_conf = sum(p.predicted_probability for p in bin_preds) / len(bin_preds)
        avg_acc = sum(1 for p in bin_preds if p.realized) / len(bin_preds)
        ece += weight * abs(avg_conf - avg_acc)
    return ece


def reliability_curve(
    predictions: Sequence[Prediction],
    n_bins: int = 10,
) -> list[tuple[float, float, int]]:
    """Return (mean_predicted, mean_realized, count) per bin for plotting."""
    bins: list[list[Prediction]] = [[] for _ in range(n_bins)]
    for p in predictions:
        idx = min(int(p.predicted_probability * n_bins), n_bins - 1)
        bins[idx].append(p)
    result = []
    for bin_preds in bins:
        if not bin_preds:
            result.append((0.0, 0.0, 0))
            continue
        conf = sum(p.predicted_probability for p in bin_preds) / len(bin_preds)
        acc = sum(1 for p in bin_preds if p.realized) / len(bin_preds)
        result.append((conf, acc, len(bin_preds)))
    return result


def update_beta_prior(
    prior_alpha: float,
    prior_beta: float,
    outcomes: Sequence[bool],
    prior_weight: float = 1.0,
) -> tuple[float, float]:
    """Conjugate Beta update for a Bernoulli likelihood.

    prior_weight < 1 downweights the prior (useful when the meta-learner wants
    reality to dominate historical priors after enough data).
    Returns (posterior_alpha, posterior_beta).
    """
    successes = sum(1 for o in outcomes if o)
    failures = len(outcomes) - successes
    return (
        prior_alpha * prior_weight + successes,
        prior_beta * prior_weight + failures,
    )


def recommend_prior(
    predictions: Sequence[Prediction],
    base_alpha: float = 1.0,
    base_beta: float = 1.0,
    min_n: int = 5,
) -> tuple[float, float, str]:
    """Given resolved predictions, recommend a new Beta prior for the next project.

    Returns (alpha, beta, direction) where direction is one of:
      'more_confident'  — model is under-confident, shrink variance toward reality
      'less_confident'  — model is over-confident, widen variance
      'none'            — too few samples or well-calibrated
    """
    if len(predictions) < min_n:
        return base_alpha, base_beta, "none"

    brier = brier_score(predictions)
    ece = expected_calibration_error(predictions)
    mean_pred = sum(p.predicted_probability for p in predictions) / len(predictions)
    mean_real = sum(1 for p in predictions if p.realized) / len(predictions)

    # Direction
    if ece < 0.05:
        direction = "none"
    elif mean_pred > mean_real + 0.10:
        direction = "less_confident"
    elif mean_pred < mean_real - 0.10:
        direction = "more_confident"
    else:
        direction = "none"

    # Bayesian update from the resolved outcomes
    alpha, beta = update_beta_prior(
        base_alpha, base_beta,
        [p.realized for p in predictions],
        prior_weight=0.5,  # let reality dominate the flat prior
    )
    return alpha, beta, direction
