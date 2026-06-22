"""Upload-backed knowledge helpers.

These helpers store raw files in a sidecar directory while keeping only
manifests and parsed artifacts in ProjectState.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import uuid

from connectors import CONNECTOR_REGISTRY
from config import UPLOAD_LAYER
from decision_objects import ensure_decision_objects, stable_object_id
from extensions.connectors import CSVColumnMapping, ConnectorImportRequest
from ingestion import merge_imported_records
from state import (
    FileParseStatus,
    FileParseSummary,
    FileRole,
    KnowledgeItem,
    KnowledgeItemStatus,
    ProjectState,
    Provenance,
    SourceRegistryEntry,
    UploadedFileManifest,
)

from .file_parsers import ParsedChunk, ParsedTable, UploadParseError, parse_upload_bytes
from .freshness import build_knowledge_health
from .registry import ensure_knowledge_layer, upsert_source_entry

logger = logging.getLogger(__name__)


@dataclass
class FileUploadResult:
    manifest: UploadedFileManifest
    source: SourceRegistryEntry
    knowledge_items: list[KnowledgeItem]
    import_summary: dict | None
    knowledge_summary: dict


@dataclass(frozen=True)
class UploadStoreHealth:
    status: str
    path: str
    writable: bool
    message: str


class UploadStorageError(RuntimeError):
    """Controlled error for local upload storage failures."""

    public_message = "Upload storage is unavailable. Check server-side upload storage configuration."

    def __init__(self, *, path: str = "", operation: str = "", cause: Exception | None = None):
        super().__init__(self.public_message)
        self.path = path
        self.operation = operation
        self.cause = cause


def list_uploaded_files(state: ProjectState) -> list[UploadedFileManifest]:
    layer = state.knowledge_layer
    if layer is None:
        return []
    return list(layer.uploaded_files or [])


def get_uploaded_file_manifest(state: ProjectState, file_id: str) -> UploadedFileManifest | None:
    layer = state.knowledge_layer
    if layer is None:
        return None
    for manifest in layer.uploaded_files or []:
        if manifest.file_id == file_id:
            return manifest
    return None


def ingest_uploaded_file(
    state: ProjectState,
    *,
    filename: str,
    media_type: str,
    content: bytes,
    actor: str,
    role: str = FileRole.CONTEXT.value,
    import_mode: str = "knowledge",
    sheet_name: str = "",
    mapping: list[CSVColumnMapping] | None = None,
) -> FileUploadResult:
    layer = ensure_knowledge_layer(state)
    original_sources = [item.model_copy(deep=True) for item in layer.sources]
    original_items = [item.model_copy(deep=True) for item in layer.items]
    original_files = [item.model_copy(deep=True) for item in layer.uploaded_files]
    original_sync_state = layer.sync_state.model_copy(deep=True)
    original_imported_evidence = [item.model_copy(deep=True) for item in (state.imported_evidence or [])]
    original_imported_signals = [item.model_copy(deep=True) for item in (state.imported_signals or [])]
    now = datetime.now().isoformat()
    file_id = stable_object_id("file", state.project_id, filename, now, uuid.uuid4().hex)
    source_id = stable_object_id("knowledge_source", state.project_id, file_id)
    checksum = hashlib.sha256(content).hexdigest()
    storage_ref = ""

    try:
        storage_ref = _store_upload_bytes(state.project_id, file_id, filename, content)
        parsed = parse_upload_bytes(filename, media_type, content, sheet_name=sheet_name)
        role_value = _normalize_role(role)
        layer = ensure_knowledge_layer(state)
        source = upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id=source_id,
                name=filename,
                source_kind="uploaded_file",
                connector_type=parsed.parser_kind,
                owner=actor,
                domain_tags=["uploads"],
                sensitivity="internal",
                trust_tier="operator_curated",
                enabled=True,
                access_mode="manual_upload",
                freshness_policy_id="default_offline",
                notes=f"uploaded_file;role={role_value.value if hasattr(role_value, 'value') else role_value}",
                last_sync_at=now,
                last_success_at=now,
                last_checksum_sha256=checksum,
            ),
        )
        knowledge_items = _upsert_parsed_knowledge_items(
            state,
            source=source,
            file_id=file_id,
            filename=filename,
            parsed=parsed,
            actor=actor,
            captured_at=now,
            checksum=checksum,
        )
        import_summary = None
        evidence_count = 0
        signal_count = 0
        if import_mode == "structured_import":
            if parsed.file_kind != "table" or parsed.table is None:
                raise UploadParseError("Structured import mode is only supported for CSV and XLSX files.")
            if not mapping:
                raise UploadParseError("Structured import mode requires a mapping.")
            import_summary = _run_structured_import(
                state,
                parsed_table=parsed.table,
                file_id=file_id,
                filename=filename,
                actor=actor,
                mapping=mapping,
            )
            evidence_count = int(import_summary.get("evidence_count", 0) or 0)
            signal_count = int(import_summary.get("signal_count", 0) or 0)

        layer.sync_state.status = "current"
        layer.sync_state.last_sync_at = now
        layer.sync_state.last_success_at = now
        layer.sync_state.last_error = ""
        layer.sync_state.last_job_id = f"upload:{file_id}"

        parse_summary = FileParseSummary(
            parser_kind=parsed.parser_kind,
            status=FileParseStatus.COMPLETED,
            page_count=parsed.page_count,
            row_count=parsed.row_count,
            sheet_count=parsed.sheet_count,
            sheet_name=parsed.sheet_name,
            chunk_count=len(parsed.chunks),
            knowledge_item_count=len(knowledge_items),
            evidence_count=evidence_count,
            signal_count=signal_count,
        )
        manifest = UploadedFileManifest(
            file_id=file_id,
            source_id=source.source_id,
            filename=filename,
            media_type=parsed.media_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            uploaded_at=now,
            uploaded_by=actor,
            parser_kind=parsed.parser_kind,
            storage_ref=storage_ref,
            role=role_value,
            import_mode=import_mode,
            parse_summary=parse_summary,
        )
        _upsert_file_manifest(state, manifest)
        ensure_decision_objects(state, trigger="knowledge.file_upload")
        # Slice A (additive, off by default): capture immutable source evidence
        # from the genuine raw bytes and stable storage_ref. Capture failure must
        # never break the upload and never claims durable evidence.
        try:
            from knowledge.evidence_snapshot import capture as _evidence_capture

            _evidence_capture.capture_upload(
                project_id=state.project_id,
                content=content,
                storage_ref=storage_ref,
                source_kind="uploaded_file",
                source_locator=f"upload:{file_id}",
                actor=actor,
            )
        except Exception:
            logger.warning(
                "evidence-snapshot capture failed for upload %s; upload preserved, "
                "no durable Slice A evidence recorded",
                file_id,
                exc_info=True,
            )
        return FileUploadResult(
            manifest=manifest,
            source=source,
            knowledge_items=knowledge_items,
            import_summary=import_summary,
            knowledge_summary=build_knowledge_health(state),
        )
    except Exception:
        layer.sources = original_sources
        layer.items = original_items
        layer.uploaded_files = original_files
        layer.sync_state = original_sync_state
        state.imported_evidence = original_imported_evidence
        state.imported_signals = original_imported_signals
        _delete_storage_ref(storage_ref)
        raise


def delete_uploaded_file(state: ProjectState, file_id: str) -> dict:
    layer = ensure_knowledge_layer(state)
    manifest = get_uploaded_file_manifest(state, file_id)
    if manifest is None:
        raise KeyError(file_id)

    # Slice A (additive, off by default): guard deletion through the snapshot
    # storage_ref. When enabled, refuse hard deletion of snapshot-linked storage
    # and fail closed if linkage cannot be verified. No-op when disabled.
    from knowledge.evidence_snapshot import capture as _evidence_capture

    _evidence_capture.assert_safe_to_delete_storage_ref(manifest.storage_ref)

    _delete_storage_ref(manifest.storage_ref)
    layer.uploaded_files = [item for item in layer.uploaded_files if item.file_id != file_id]
    layer.sources = [source for source in layer.sources if source.source_id != manifest.source_id]
    layer.items = [item for item in layer.items if item.source_id != manifest.source_id]
    state.imported_evidence = [
        item for item in (state.imported_evidence or [])
        if (item.provenance.external_uri or "") != f"upload:{file_id}"
    ]
    state.imported_signals = [
        item for item in (state.imported_signals or [])
        if (item.provenance.external_uri or "") != f"upload:{file_id}"
    ]
    ensure_decision_objects(state, trigger="knowledge.file_delete")
    return {
        "deleted": True,
        "file_id": file_id,
        "source_id": manifest.source_id,
        "knowledge_summary": build_knowledge_health(state),
    }


def delete_project_uploads(project_id: str) -> None:
    base = Path(UPLOAD_LAYER.storage_dir) / project_id
    if not base.exists():
        return
    for child in base.iterdir():
        if child.is_file():
            child.unlink()
    try:
        base.rmdir()
    except OSError:
        pass


def describe_uploaded_file(state: ProjectState, file_id: str) -> dict:
    manifest = get_uploaded_file_manifest(state, file_id)
    if manifest is None:
        raise KeyError(file_id)
    source = next((item for item in (state.knowledge_layer.sources if state.knowledge_layer else []) if item.source_id == manifest.source_id), None)
    knowledge_items = [item for item in (state.knowledge_layer.items if state.knowledge_layer else []) if item.source_id == manifest.source_id]
    imported_evidence = [item for item in (state.imported_evidence or []) if (item.provenance.external_uri or "") == f"upload:{file_id}"]
    imported_signals = [item for item in (state.imported_signals or []) if (item.provenance.external_uri or "") == f"upload:{file_id}"]
    return {
        "manifest": manifest.model_dump(mode="json"),
        "source": source.model_dump(mode="json") if source else None,
        "knowledge_items": [
            {
                "item_id": item.item_id,
                "title": item.title,
                "summary": item.summary,
                "freshness_status": item.freshness_status.value if hasattr(item.freshness_status, "value") else str(item.freshness_status),
                "source_ref": item.source_ref,
            }
            for item in knowledge_items
        ],
        "structured_import_summary": {
            "evidence_count": len(imported_evidence),
            "signal_count": len(imported_signals),
        },
    }


def _store_upload_bytes(project_id: str, file_id: str, filename: str, content: bytes) -> str:
    extension = Path(filename or "").suffix.lower()
    base = Path(UPLOAD_LAYER.storage_dir)
    target_dir = base / project_id
    target = target_dir / f"{file_id}{extension}"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except OSError as exc:
        operation = "write" if target_dir.exists() else "mkdir"
        raise UploadStorageError(path=str(target), operation=operation, cause=exc) from exc
    return str(target)


def check_upload_store_writable(storage_dir: str | None = None) -> UploadStoreHealth:
    base = Path(storage_dir or UPLOAD_LAYER.storage_dir)
    probe = base / f".upload_store_probe_{uuid.uuid4().hex}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        if not base.is_dir():
            return UploadStoreHealth(
                status="fail",
                path=str(base),
                writable=False,
                message="Upload storage path exists but is not a directory.",
            )
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return UploadStoreHealth(
            status="fail",
            path=str(base),
            writable=False,
            message="Upload storage is not writable; see server logs for path details.",
        )
    return UploadStoreHealth(
        status="ok",
        path=str(base),
        writable=True,
        message="Upload storage root is writable.",
    )


def _delete_storage_ref(storage_ref: str) -> None:
    if not storage_ref:
        return
    path = Path(storage_ref)
    if path.exists():
        path.unlink()


def _normalize_role(role: str) -> FileRole:
    normalized = (role or "").strip().lower()
    return FileRole.DATA if normalized == FileRole.DATA.value else FileRole.CONTEXT


def _upsert_file_manifest(state: ProjectState, manifest: UploadedFileManifest) -> None:
    layer = ensure_knowledge_layer(state)
    layer.uploaded_files = [item for item in layer.uploaded_files if item.file_id != manifest.file_id]
    layer.uploaded_files.append(manifest)
    layer.uploaded_files.sort(key=lambda item: (item.uploaded_at, item.filename, item.file_id), reverse=True)


def _upsert_parsed_knowledge_items(
    state: ProjectState,
    *,
    source: SourceRegistryEntry,
    file_id: str,
    filename: str,
    parsed,
    actor: str,
    captured_at: str,
    checksum: str,
) -> list[KnowledgeItem]:
    layer = ensure_knowledge_layer(state)
    layer.items = [item for item in layer.items if item.source_id != source.source_id]
    knowledge_items = (
        _document_knowledge_items(source.source_id, file_id, filename, parsed.chunks, actor, captured_at, checksum)
        if parsed.file_kind == "document"
        else _table_knowledge_items(source.source_id, file_id, filename, parsed.table, actor, captured_at, checksum)
    )
    layer.items.extend(knowledge_items)
    layer.items.sort(key=lambda item: (item.captured_at, item.item_id), reverse=True)
    return knowledge_items


def _document_knowledge_items(
    source_id: str,
    file_id: str,
    filename: str,
    chunks: list[ParsedChunk],
    actor: str,
    captured_at: str,
    checksum: str,
) -> list[KnowledgeItem]:
    items: list[KnowledgeItem] = []
    for index, chunk in enumerate(chunks, start=1):
        items.append(
            KnowledgeItem(
                item_id=stable_object_id("knowledge", source_id, file_id, "chunk", index, chunk.source_ref),
                source_id=source_id,
                source_ref=f"upload:{file_id}:{chunk.source_ref}",
                title=chunk.title[:240],
                summary=chunk.text[:2000],
                structured_payload={
                    "category": "uploaded_document",
                    "chunk_index": index,
                    "filename": Path(filename).name,
                },
                observed_at=captured_at,
                captured_at=captured_at,
                checksum_sha256=hashlib.sha256(f"{checksum}:{index}:{chunk.text}".encode("utf-8")).hexdigest(),
                provenance=Provenance(
                    source_type="connector_import",
                    source_ref=f"upload:{file_id}:{chunk.source_ref}",
                    captured_at=captured_at,
                    captured_by=actor,
                    connector="uploaded_file",
                    external_uri=f"upload:{file_id}",
                    checksum=checksum,
                    notes="uploaded_file;document_chunk;untrusted_source=true",
                ),
                freshness_status=KnowledgeItemStatus.FRESH,
                trust_tier="operator_curated",
                sensitivity="internal",
                untrusted_source=True,
                eligible_for_retrieval=False,
            )
        )
    return items


def _table_knowledge_items(
    source_id: str,
    file_id: str,
    filename: str,
    table: ParsedTable | None,
    actor: str,
    captured_at: str,
    checksum: str,
) -> list[KnowledgeItem]:
    if table is None:
        return []
    items: list[KnowledgeItem] = []
    header_summary = ", ".join(table.headers[:10]) or "No headers"
    for start in range(0, min(len(table.rows), UPLOAD_LAYER.max_table_rows), UPLOAD_LAYER.max_table_chunk_rows):
        chunk_rows = table.rows[start : start + UPLOAD_LAYER.max_table_chunk_rows]
        if not chunk_rows:
            continue
        row_summaries = []
        for row in chunk_rows[: min(5, len(chunk_rows))]:
            facts = [f"{key}={value}" for key, value in row.items() if value][:4]
            if facts:
                row_summaries.append("; ".join(facts))
        summary = (
            f"Headers: {header_summary}. "
            f"Rows {start + 1}-{start + len(chunk_rows)}. "
            f"{' '.join(row_summaries)}"
        )[:2000]
        items.append(
            KnowledgeItem(
                item_id=stable_object_id("knowledge", source_id, file_id, "rows", start, len(chunk_rows)),
                source_id=source_id,
                source_ref=f"upload:{file_id}:{Path(filename).name}#rows={start + 1}-{start + len(chunk_rows)}",
                title=f"{Path(filename).name} — rows {start + 1}-{start + len(chunk_rows)}",
                summary=summary,
                structured_payload={
                    "category": "uploaded_table",
                    "sheet_name": table.sheet_name,
                    "row_start": start + 1,
                    "row_end": start + len(chunk_rows),
                    "metric": table.headers[0] if table.headers else "",
                },
                observed_at=captured_at,
                captured_at=captured_at,
                checksum_sha256=hashlib.sha256(
                    f"{checksum}:{start}:{json.dumps(chunk_rows, sort_keys=True)}".encode("utf-8")
                ).hexdigest(),
                provenance=Provenance(
                    source_type="connector_import",
                    source_ref=f"upload:{file_id}:{Path(filename).name}#rows={start + 1}-{start + len(chunk_rows)}",
                    captured_at=captured_at,
                    captured_by=actor,
                    connector="uploaded_file",
                    external_uri=f"upload:{file_id}",
                    checksum=checksum,
                    notes="uploaded_file;table_chunk;untrusted_source=true",
                ),
                freshness_status=KnowledgeItemStatus.FRESH,
                trust_tier="operator_curated",
                sensitivity="internal",
                untrusted_source=True,
                eligible_for_retrieval=False,
            )
        )
        if len(items) >= UPLOAD_LAYER.max_table_chunks:
            break
    return items


def _run_structured_import(
    state: ProjectState,
    *,
    parsed_table: ParsedTable,
    file_id: str,
    filename: str,
    actor: str,
    mapping: list[CSVColumnMapping],
) -> dict:
    connector = CONNECTOR_REGISTRY.get("csv")
    if connector is None:
        raise UploadParseError("CSV connector is not available for structured imports.")
    request = ConnectorImportRequest(
        source_ref=f"upload:{file_id}:{filename}",
        raw_text=parsed_table.csv_text,
        initiated_by=actor,
        dry_run=False,
        filename=filename,
        mapping=mapping,
    )
    result = connector.ingest(request)
    if (
        result.row_count == 0
        and not result.evidence
        and not result.signals
        and any(issue.severity == "error" for issue in result.row_issues)
    ):
        messages = "; ".join(issue.message for issue in result.row_issues[:5])
        raise UploadParseError(f"Structured import failed validation: {messages}")
    for evidence in result.evidence:
        evidence.provenance.external_uri = f"upload:{file_id}"
        evidence.provenance.connector = "uploaded_file"
        evidence.provenance.notes = _append_note(evidence.provenance.notes, "uploaded_file;untrusted_source=true")
    for signal in result.signals:
        signal.provenance.external_uri = f"upload:{file_id}"
        signal.provenance.connector = "uploaded_file"
        signal.provenance.notes = _append_note(signal.provenance.notes, "uploaded_file;untrusted_source=true")
    totals = merge_imported_records(state, evidence=result.evidence, signals=result.signals)
    return {
        "connector": "csv",
        "checksum": result.checksum,
        "row_count": result.row_count,
        "imported_rows": result.imported_rows,
        "skipped_rows": result.skipped_rows,
        "evidence_count": len(result.evidence),
        "signal_count": len(result.signals),
        "warnings": list(result.warnings),
        "unknown_columns": list(result.unknown_columns),
        "mapped_columns": list(result.mapped_columns),
        "row_issues": [vars(issue) for issue in result.row_issues],
        "totals": totals,
    }


def _append_note(existing: str, extra: str) -> str:
    existing_clean = (existing or "").strip("; ")
    if not existing_clean:
        return extra
    if extra in existing_clean:
        return existing_clean
    return f"{existing_clean};{extra}"
