"""Strict posture as a property of the *worker*, not of a capture scope.

The finding: strict fail-closed behavior was enforced only where a capture scope
happened to exist. ``transport.handle_async_request`` refused to send when
``capture.posture == "strict"`` and the start could not be persisted — but a
provider call made with no capture scope at all read ``current_capture() is
None`` and took the transparent path. A strict experiment could therefore make
real, unrecorded provider calls from any code path that forgot to open a scope,
and nothing in the run would say so. "Strict" described a block of code rather
than the process, which is not what a paired experiment needs: it needs the
guarantee that *this worker made no provider request outside the experiment*.

This module makes that a process invariant.

``MAS_PROVIDER_TELEMETRY_POSTURE=strict`` marks the process **strict-required**.
In a strict-required process:

* every supported provider entry point verifies the posture before doing
  anything, and a call outside a valid strict telemetry run is refused;
* the refusal happens **before transport** — the request is not sent, not
  retried and not queued, and the transport layer refuses independently so a
  path that bypassed the entry point is still caught at the wire;
* an ``observational`` or ``off`` scope cannot be opened inside the process, and
  a nested scope cannot downgrade the posture;
* a conflicting or malformed configuration fails at preflight rather than being
  silently normalized to something weaker.

The one way out is explicit and named: ``MAS_PROVIDER_TELEMETRY_NON_EXPERIMENT``
declares the process to be a test harness or an administrative probe rather than
an experiment worker. It has to be set deliberately, it is recorded in the
preflight payload, and it is refused in combination with a strict posture unless
the caller means both — which is why the two together are a configuration error
rather than a silent precedence rule.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import config

from .models import POSTURE_OBSERVATIONAL, POSTURE_STRICT

POSTURE_OFF = "off"

# Set to a truthy value to declare this process a non-experiment worker: a test
# harness, a migration runner, an operator probe. Never set in an RB3 worker.
NON_EXPERIMENT_ENV = "MAS_PROVIDER_TELEMETRY_NON_EXPERIMENT"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class StrictPostureViolation(RuntimeError):
    """A provider call was attempted outside a valid strict telemetry scope.

    Raised *before* transport. The SDK sees it as a request that failed to
    start, its retry policy applies unchanged, and every retry hits the same
    refusal — so a strict-required worker with no scope makes no provider
    request at all, which is the guarantee the posture sells.
    """


class StrictPostureMisconfigured(RuntimeError):
    """The process's telemetry configuration is conflicting or malformed."""


# ─────────────────────────── configuration ───────────────────────────


def raw_posture() -> str:
    return str(os.getenv(config.PROVIDER_TELEMETRY_POSTURE_ENV, "") or "").strip().lower()


def non_experiment() -> bool:
    """True when this process has explicitly opted out of the worker contract."""
    return str(os.getenv(NON_EXPERIMENT_ENV, "") or "").strip().lower() in _TRUE


def strict_required() -> bool:
    """True when this process must not make an unscoped provider request.

    Derived from the posture rather than from a second flag, so there is no way
    to configure a strict experiment that forgot to arm its own guard.
    """
    return raw_posture() == POSTURE_STRICT and not non_experiment()


def configuration_problems() -> list[str]:
    """Everything wrong with this process's telemetry configuration.

    Returned rather than raised so a preflight can report all of it at once.
    Each entry is a stable token, never an environment *value*: a malformed
    posture variable is exactly the kind of place an operator pastes something
    they should not have.
    """
    problems: list[str] = []
    posture = raw_posture()
    if posture and posture not in (POSTURE_OFF, POSTURE_OBSERVATIONAL, POSTURE_STRICT):
        problems.append("unknown_posture")

    compat = str(
        os.getenv(config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV, "") or ""
    ).strip().lower()
    if compat and compat not in _TRUE | _FALSE:
        problems.append("unknown_compat_flag")
    if posture == POSTURE_STRICT and compat in _FALSE - {""}:
        # "strict" and "telemetry is off" are not two settings to reconcile by
        # precedence; they are two operators disagreeing, and a strict run is
        # the last place to guess which one meant it.
        problems.append("strict_posture_with_disabled_compat_flag")
    if posture == POSTURE_STRICT and non_experiment():
        problems.append("strict_posture_with_non_experiment_optout")
    if posture != POSTURE_STRICT and non_experiment():
        # Harmless, but it means someone believes this process is exempt from a
        # guard that was never armed. Reported so the belief is visible.
        problems.append("non_experiment_optout_without_strict_posture")

    raw_non_experiment = str(os.getenv(NON_EXPERIMENT_ENV, "") or "").strip().lower()
    if raw_non_experiment and raw_non_experiment not in _TRUE | _FALSE:
        problems.append("unknown_non_experiment_flag")
    return problems


def require_valid_configuration() -> None:
    """Fail startup on a configuration that cannot mean one thing."""
    problems = configuration_problems()
    if problems:
        raise StrictPostureMisconfigured(
            "provider telemetry configuration is not interpretable: "
            + ", ".join(sorted(problems))
        )


# ─────────────────────────── the worker invariant ───────────────────────────


def _active_strict_session() -> Optional[Any]:
    """The current session, if it is a valid strict one."""
    from . import service

    session = service.current_session()
    if session is None:
        return None
    if getattr(session, "posture", None) != POSTURE_STRICT:
        return None
    if not getattr(session, "telemetry_run_id", ""):
        return None
    return session


def scope_state() -> dict[str, Any]:
    """What this worker's posture and scope look like right now."""
    session = None
    try:
        from . import service

        session = service.current_session()
    except Exception:  # pragma: no cover - import guard
        session = None
    return {
        "posture": raw_posture() or POSTURE_OFF,
        "strict_required": strict_required(),
        "non_experiment": non_experiment(),
        "in_strict_scope": _active_strict_session() is not None,
        "telemetry_run_id": getattr(session, "telemetry_run_id", "") if session else "",
    }


def enforce_provider_call(entry_point: str) -> None:
    """Refuse a provider call this worker is not allowed to make.

    Called by every supported entry point *and* by the transport wrapper, on
    purpose: the entry point gives a caller a clear failure at the top of the
    stack, and the transport check means a path that never went through an
    entry point — a direct gateway call, a helper somebody added later, an SDK
    used straight — is still refused at the wire.
    """
    if not strict_required():
        return
    if _active_strict_session() is not None:
        return
    raise StrictPostureViolation(
        f"{entry_point}: this worker is configured strict-required, and a "
        "provider call was attempted outside a valid strict telemetry run. "
        "The request was not sent. Open a telemetry_scope, or declare the "
        f"process a non-experiment worker with {NON_EXPERIMENT_ENV}=1."
    )


def require_strict_scope_posture(resolved: str) -> None:
    """Refuse to open a weaker scope inside a strict-required worker.

    A nested ``run_session(posture="observational")`` inside a strict experiment
    would create a region where the worker's own guarantee does not hold, and
    every call made in that region would be invisible to the strict run that
    encloses it.
    """
    if not strict_required():
        return
    if resolved == POSTURE_STRICT:
        return
    raise StrictPostureViolation(
        f"a strict-required worker cannot open a {resolved!r} telemetry scope; "
        "posture is a property of the process, not of a block"
    )


__all__ = [
    "NON_EXPERIMENT_ENV",
    "StrictPostureMisconfigured",
    "StrictPostureViolation",
    "configuration_problems",
    "enforce_provider_call",
    "non_experiment",
    "raw_posture",
    "require_strict_scope_posture",
    "require_valid_configuration",
    "scope_state",
    "strict_required",
]
