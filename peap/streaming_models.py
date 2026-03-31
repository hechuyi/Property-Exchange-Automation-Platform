"""Core contracts for the streaming ingest pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal

RecordFamily = Literal["listing", "deal"]

JobType = Literal["one_click", "download_ingest", "export_excel", "manual_import", "mapping_refresh"]
ItemStage = Literal[
    "downloaded",
    "queued_for_parse",
    "prepare_tasks",
    "save_pages",
    "manual_import_scan",
    "reprocessing",
    "refresh_history",
    "exporting",
    "parsed",
    "postprocessed",
    "persisted",
    "skipped",
    "failed",
]
RecordState = Literal[
    "ready",
    "pending_mapping",
    "mapping_conflict",
    "skipped",
    "parse_failed",
    "postprocess_failed",
    "conflict",
]
Severity = Literal["info", "warn", "error"]


@dataclass(frozen=True)
class ItemIngestRequest:
    exchange: str
    start_date: str
    end_date: str
    concurrency: int = 1
    rulepack_version: str = ""


@dataclass(frozen=True)
class ItemSavedPayload:
    source_file: str
    page_url: str = ""
    project_code: str = ""
    project_name: str = ""
    exchange: str = ""
    listing_date: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemProgressEvent:
    job_id: str
    stage: ItemStage
    status: str
    project_code: str = ""
    archive_path: str = ""
    error_type: str = ""
    error_message: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    record_family: RecordFamily = "listing"


@dataclass(frozen=True)
class PostProcessFinding:
    severity: Severity
    type: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestedRecord:
    record_id: str
    revision_hash: str
    project_code: str
    project_name: str
    project_type: str
    exchange: str
    listing_date: str
    state: RecordState
    source_file: str
    archive_path: str
    parser_payload: Dict[str, Any]
    postprocess_payload: Dict[str, Any]
    findings: List[PostProcessFinding] = field(default_factory=list)
    record_family: RecordFamily = "listing"
    source_identity: Dict[str, Any] = field(default_factory=dict)
    canonical_record: Dict[str, Any] = field(default_factory=dict)
    canonical_projection: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportRequest:
    date_from: str | None = None
    date_to: str | None = None
    business_types: List[str] = field(default_factory=list)
    mode: str = "incremental"
    cursor_key: str = ""
    output_dir: str = ""
    record_family: RecordFamily = "listing"


@dataclass(frozen=True)
class ExportArtifact:
    business_type: str
    change_bucket: str
    file_path: str
    record_count: int


@dataclass(frozen=True)
class ExportRunResult:
    export_id: str
    cursor_key: str
    artifacts: List[ExportArtifact]
    new_records: int
    changed_records: int
