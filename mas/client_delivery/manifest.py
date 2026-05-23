"""Manifest writer for Client Delivery Generator v0.5."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import DeliveryPackage


VALIDATION_STATUS = "awaiting_side_by_side_defense_test"
ARTIFACT_TYPE = "client_delivery_generator_v0_5"


def write_delivery_manifest(package: DeliveryPackage, output_path, files, warnings) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "project_id": str(package.project_id),
        "generated_at": _utc_now(),
        "requires_human_review": True,
        "validation_status": VALIDATION_STATUS,
        "artifact_type": ARTIFACT_TYPE,
        "files": {str(role): str(file_path) for role, file_path in files.items()},
        "quality_warnings": list(warnings or []),
        "counts": {
            "critical_assumptions": int(len(package.critical_assumptions)),
            "execution_actions": int(len(package.execution_plan)),
            "kpis": int(len(package.kpis)),
            "reentry_triggers": int(len(package.review.reentry_triggers)),
        },
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
