"""Feature-gated services for the provenance-only R1.7 foundation."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import scenario_input_evaluation_repository as repo
from .scenario_input_evaluation_models import (
    ScenarioInputEvaluationRecord,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestRecord,
    ScenarioInputManifestRegistration,
)


class ScenarioInputEvaluationDisabled(RuntimeError):
    """The existing research-evidence feature gate is disabled."""


class ScenarioInputEvaluationTransactionError(RuntimeError):
    """A write cannot preserve caller-owned transaction atomicity."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ScenarioInputEvaluationDisabled(
            "Scenario-input evidence evaluation is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _write(conn, savepoint: str):
    if conn.autocommit:
        raise ScenarioInputEvaluationTransactionError(
            "scenario-input evidence writes require a non-autocommit connection"
        )
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def register_scenario_input_manifest(
    conn, registration: ScenarioInputManifestRegistration
) -> ScenarioInputManifestRecord:
    registration = ScenarioInputManifestRegistration.model_validate(
        registration.model_dump()
        if isinstance(registration, ScenarioInputManifestRegistration)
        else registration
    )
    _require_enabled()
    with _write(conn, "research_evidence_scenario_manifest_write"):
        return repo.register_manifest(conn, registration)


def create_scenario_input_evaluation(
    conn, request: ScenarioInputEvaluationRequest
) -> ScenarioInputEvaluationRecord:
    request = ScenarioInputEvaluationRequest.model_validate(
        request.model_dump()
        if isinstance(request, ScenarioInputEvaluationRequest)
        else request
    )
    _require_enabled()
    with _write(conn, "research_evidence_scenario_evaluation_write"):
        return repo.create_evaluation(conn, request)
