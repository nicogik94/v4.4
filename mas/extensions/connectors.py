"""Read-side connector contracts for evidence and signal ingestion."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from state import Evidence, Signal


@dataclass
class ConnectorConfig:
    name: str
    enabled: bool = False
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class CSVColumnMapping:
    column: str
    target_type: Literal["evidence", "signal", "ignore"] = "ignore"
    target_field: str = ""
    value_type: Literal["string", "number", "boolean", "datetime", "category"] = "string"
    required: bool = False
    transform: list[str] = field(default_factory=list)
    signal_kind: str = ""
    confidence: float | None = None
    drop_if_empty: bool = False
    default_value: Any = None


@dataclass
class RowIssue:
    row_index: int
    column: str = ""
    message: str = ""
    severity: Literal["error", "warning"] = "error"


@dataclass
class ValidationResult:
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    row_issues: list[RowIssue] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    mapped_columns: list[str] = field(default_factory=list)


@dataclass
class NormalizedEvidenceRecord:
    evidence: Evidence
    row_index: int


@dataclass
class NormalizedSignalRecord:
    signal: Signal
    row_index: int


@dataclass
class ConnectorImportRequest:
    source_ref: str
    raw_text: str = ""
    initiated_by: str = "operator"
    dry_run: bool = True
    filename: str = ""
    mapping: list[CSVColumnMapping] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectorImportResult:
    connector_name: str
    source_type: str = "connector_import"
    evidence: list[Evidence] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_issues: list[RowIssue] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    mapped_columns: list[str] = field(default_factory=list)
    row_count: int = 0
    imported_rows: int = 0
    skipped_rows: int = 0
    dry_run: bool = True
    checksum: str = ""


IngestionRequest = ConnectorImportRequest
IngestionResult = ConnectorImportResult


class Connector(Protocol):
    name: str

    def validate(self, request: ConnectorImportRequest) -> ValidationResult:
        ...

    def ingest(self, request: ConnectorImportRequest) -> ConnectorImportResult:
        ...


class ConnectorRegistry:
    def __init__(self):
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._connectors)
