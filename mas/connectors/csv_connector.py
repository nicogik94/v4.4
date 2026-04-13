"""Concrete CSV connector for normalized Evidence and Signal imports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime
from typing import Any

from decision_objects import stable_object_id
from extensions.connectors import (
    CSVColumnMapping,
    ConnectorImportRequest,
    ConnectorImportResult,
    RowIssue,
    ValidationResult,
)
from state import Evidence, Provenance, Signal


_ALLOWED_TRANSFORMS = {
    "trim",
    "lowercase",
    "uppercase",
    "parse_number",
    "parse_boolean",
    "parse_datetime",
}
_EVIDENCE_FIELDS = {"title", "summary", "category", "linked_hypothesis_ids", "linked_risk_ids"}
_SIGNAL_FIELDS = {"name", "description", "cadence", "kind", "confidence", "linked_hypothesis_ids"}
_TEXT_LIMITS = {
    "title": 240,
    "summary": 2000,
    "category": 120,
    "name": 180,
    "description": 1200,
    "cadence": 120,
    "kind": 120,
}


class CSVConnector:
    name = "csv"

    def validate(self, request: ConnectorImportRequest) -> ValidationResult:
        result = ValidationResult(ok=True)
        if not request.raw_text.strip():
            result.row_issues.append(RowIssue(row_index=0, message="CSV payload is empty"))
            result.ok = False
            return result
        if not request.mapping:
            result.row_issues.append(RowIssue(row_index=0, message="At least one mapping rule is required"))
            result.ok = False
            return result

        headers, header_error = _read_headers(request.raw_text)
        if header_error:
            result.row_issues.append(RowIssue(row_index=0, message=header_error))
            result.ok = False
            return result

        mapped_columns = [mapping.column for mapping in request.mapping if mapping.target_type != "ignore"]
        result.mapped_columns = sorted(set(mapped_columns))
        result.unknown_columns = sorted(header for header in headers if header not in result.mapped_columns)
        if result.unknown_columns:
            result.warnings.append(
                f"Ignoring unmapped columns: {', '.join(result.unknown_columns)}"
            )

        if not result.mapped_columns:
            result.row_issues.append(RowIssue(row_index=0, message="Mappings do not target evidence or signal fields"))

        for mapping in request.mapping:
            if mapping.target_type == "ignore":
                continue
            allowed_fields = _EVIDENCE_FIELDS if mapping.target_type == "evidence" else _SIGNAL_FIELDS
            if mapping.target_field not in allowed_fields:
                result.row_issues.append(
                    RowIssue(
                        row_index=0,
                        column=mapping.column,
                        message=f"Unsupported {mapping.target_type} target_field: {mapping.target_field}",
                    )
                )
            invalid_transforms = [item for item in mapping.transform if item not in _ALLOWED_TRANSFORMS]
            if invalid_transforms:
                result.row_issues.append(
                    RowIssue(
                        row_index=0,
                        column=mapping.column,
                        message=f"Unsupported transforms: {', '.join(sorted(invalid_transforms))}",
                    )
                )
            if mapping.required and mapping.column not in headers:
                result.row_issues.append(
                    RowIssue(
                        row_index=0,
                        column=mapping.column,
                        message="Required column is missing from CSV header",
                    )
                )
            if mapping.confidence is not None and not (0.0 <= float(mapping.confidence) <= 1.0):
                result.row_issues.append(
                    RowIssue(
                        row_index=0,
                        column=mapping.column,
                        message="confidence must be between 0.0 and 1.0",
                    )
                )

        result.ok = not any(issue.severity == "error" for issue in result.row_issues)
        return result

    def ingest(self, request: ConnectorImportRequest) -> ConnectorImportResult:
        validation = self.validate(request)
        file_checksum = hashlib.sha256(request.raw_text.encode("utf-8")).hexdigest()
        result = ConnectorImportResult(
            connector_name=self.name,
            source_type="connector_import",
            warnings=list(validation.warnings),
            row_issues=list(validation.row_issues),
            unknown_columns=list(validation.unknown_columns),
            mapped_columns=list(validation.mapped_columns),
            dry_run=request.dry_run,
            checksum=file_checksum,
        )
        if not validation.ok:
            return result

        reader = csv.DictReader(io.StringIO(request.raw_text.lstrip("\ufeff")))
        for row_index, row in enumerate(reader, start=2):
            result.row_count += 1
            evidence_errors: list[RowIssue] = []
            signal_errors: list[RowIssue] = []
            evidence_payload: dict[str, Any] = {
                "category": "csv_import",
                "source_phase": "connector_csv",
                "untrusted_source": True,
            }
            signal_payload: dict[str, Any] = {
                "source_phase": "connector_csv",
                "cadence": "imported",
                "untrusted_source": True,
            }
            evidence_touched = False
            signal_touched = False

            for mapping in request.mapping:
                if mapping.target_type == "ignore":
                    continue
                target_errors = evidence_errors if mapping.target_type == "evidence" else signal_errors
                has_value, value = _mapped_value(row, mapping, row_index, target_errors)
                if not has_value:
                    continue

                if mapping.target_type == "evidence":
                    evidence_touched = True
                    evidence_payload[mapping.target_field] = value
                else:
                    signal_touched = True
                    signal_payload[mapping.target_field] = value
                    if mapping.signal_kind and not signal_payload.get("kind"):
                        signal_payload["kind"] = _truncate_text(mapping.signal_kind, "kind")
                    if mapping.confidence is not None and signal_payload.get("confidence") is None:
                        signal_payload["confidence"] = float(mapping.confidence)

            canonical_row = _canonical_row(row)
            row_checksum = hashlib.sha256(canonical_row.encode("utf-8")).hexdigest()

            evidence = None
            if evidence_touched:
                if not (str(evidence_payload.get("title") or "").strip() or str(evidence_payload.get("summary") or "").strip()):
                    evidence_errors.append(
                        RowIssue(row_index=row_index, message="Evidence rows require title or summary")
                    )
                if not evidence_errors:
                    provenance = _provenance(
                        filename=request.filename or request.source_ref or "import.csv",
                        row_index=row_index,
                        row_checksum=row_checksum,
                        file_checksum=file_checksum,
                        initiated_by=request.initiated_by,
                    )
                    evidence = Evidence(
                        evidence_id=stable_object_id(
                            "evidence",
                            request.source_ref,
                            request.filename,
                            row_checksum,
                            "csv",
                        ),
                        linked_decision_ids=[],
                        provenance=provenance,
                        **evidence_payload,
                    )

            signal = None
            if signal_touched:
                if not str(signal_payload.get("name") or "").strip():
                    signal_errors.append(
                        RowIssue(row_index=row_index, message="Signal rows require name")
                    )
                if not signal_errors:
                    provenance = _provenance(
                        filename=request.filename or request.source_ref or "import.csv",
                        row_index=row_index,
                        row_checksum=row_checksum,
                        file_checksum=file_checksum,
                        initiated_by=request.initiated_by,
                    )
                    signal = Signal(
                        signal_id=stable_object_id(
                            "signal",
                            request.source_ref,
                            request.filename,
                            row_checksum,
                            "csv",
                        ),
                        linked_decision_ids=[],
                        provenance=provenance,
                        **signal_payload,
                    )

            result.row_issues.extend(evidence_errors)
            result.row_issues.extend(signal_errors)

            if evidence is not None:
                result.evidence.append(evidence)
            if signal is not None:
                result.signals.append(signal)

            if evidence is not None or signal is not None:
                result.imported_rows += 1
            else:
                result.skipped_rows += 1

        return result


def _read_headers(raw_text: str) -> tuple[list[str], str]:
    try:
        reader = csv.reader(io.StringIO(raw_text.lstrip("\ufeff")))
        headers = next(reader, None)
    except csv.Error as exc:
        return [], f"Malformed CSV header: {exc}"
    if not headers:
        return [], "CSV header row is missing"
    cleaned = [str(header).strip() for header in headers if str(header).strip()]
    if not cleaned:
        return [], "CSV header row is empty"
    return cleaned, ""


def _mapped_value(
    row: dict[str, Any],
    mapping: CSVColumnMapping,
    row_index: int,
    issues: list[RowIssue],
) -> tuple[bool, Any]:
    value = row.get(mapping.column)
    if value is None or (isinstance(value, str) and not value.strip()):
        if mapping.default_value is not None:
            value = mapping.default_value
        elif mapping.required:
            issues.append(
                RowIssue(
                    row_index=row_index,
                    column=mapping.column,
                    message="Required value is missing",
                )
            )
            return False, None
        elif mapping.drop_if_empty:
            return False, None
        else:
            return False, None

    try:
        value = _apply_transforms(value, mapping.transform)
        value = _coerce_value_type(value, mapping.value_type)
        value = _coerce_target_value(value, mapping.target_field)
    except ValueError as exc:
        severity = "error" if mapping.required else "warning"
        issues.append(
            RowIssue(
                row_index=row_index,
                column=mapping.column,
                message=str(exc),
                severity=severity,
            )
        )
        return False, None

    if value in ("", [], None):
        if mapping.required:
            issues.append(
                RowIssue(
                    row_index=row_index,
                    column=mapping.column,
                    message="Required value resolved to empty content",
                )
            )
        return False, None

    if mapping.target_field in _TEXT_LIMITS:
        value = _truncate_text(str(value), mapping.target_field)
    return True, value


def _apply_transforms(value: Any, transforms: list[str]) -> Any:
    current = value
    for transform in transforms:
        if transform == "trim":
            current = str(current).strip()
        elif transform == "lowercase":
            current = str(current).lower()
        elif transform == "uppercase":
            current = str(current).upper()
        elif transform == "parse_number":
            current = _parse_number(current)
        elif transform == "parse_boolean":
            current = _parse_boolean(current)
        elif transform == "parse_datetime":
            current = _parse_datetime(current)
    return current


def _coerce_value_type(value: Any, value_type: str) -> Any:
    if value_type == "number":
        return _parse_number(value) if not isinstance(value, (int, float)) else float(value)
    if value_type == "boolean":
        return _parse_boolean(value) if not isinstance(value, bool) else value
    if value_type == "datetime":
        return _parse_datetime(value)
    if value_type in {"string", "category"}:
        return str(value).strip()
    return value


def _coerce_target_value(value: Any, target_field: str) -> Any:
    if target_field in {"linked_hypothesis_ids", "linked_risk_ids"}:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).replace(";", ",")
        return [part.strip() for part in text.split(",") if part.strip()]
    if target_field == "confidence":
        return float(value)
    return value


def _parse_number(value: Any) -> float:
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Could not parse number from {text!r}") from exc


def _parse_boolean(value: Any) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Could not parse boolean from {text!r}")


def _parse_datetime(value: Any) -> str:
    text = str(value).strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError(f"Could not parse datetime from {text!r}") from exc


def _truncate_text(value: str, field_name: str) -> str:
    limit = _TEXT_LIMITS.get(field_name, 500)
    text = " ".join(value.split())
    return text[:limit]


def _canonical_row(row: dict[str, Any]) -> str:
    payload = {
        str(key).strip(): str(value).strip()
        for key, value in row.items()
        if str(key).strip()
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _provenance(
    *,
    filename: str,
    row_index: int,
    row_checksum: str,
    file_checksum: str,
    initiated_by: str,
) -> Provenance:
    return Provenance(
        source_type="connector_import",
        source_ref=f"{filename}#row={row_index}",
        captured_at=datetime.now().isoformat(),
        captured_by=initiated_by or "operator",
        connector="csv",
        external_uri=filename,
        checksum=row_checksum,
        notes=f"file_sha256={file_checksum}; untrusted_source=true",
    )
