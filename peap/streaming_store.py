"""SQLite persistence for the streaming ingest pipeline."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from typing import Any, Dict, Iterable, Iterator, List, Mapping
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname

from peap_core.business_catalog import (
    resolve_business_descriptor,
    resolve_business_descriptor_by_project_type,
)
from peap_core.family_catalog import get_family_descriptor
from peap_core.field_missing_contract import (
    build_field_missing_ack_payload,
    normalize_missing_fields,
)
from peap_core.pipeline_state_contracts import CANONICAL_RECORD_STATES, RecordState
from peap_core.record_identity import (
    FAILED_RECORD_STATES,
    build_identity_anchor,
    build_source_identity_payload,
)
from peap_core.source_catalog import resolve_source_descriptor

from .artifact_truth import (
    ARTIFACT_EVIDENCE_REPORT_CLASSIFICATIONS,
    classify_artifact_evidence_verdict,
    declared_artifact_is_available,
    resolve_artifact_evidence_verdict,
)
from .business_classifier import classify_record_business
from .cbex_deal_source_policy import is_cbex_deal_non_detail_page
from .export_projection import (
    append_export_projection_findings,
    project_canonical_record_to_export_payload,
)
from .failed_record_supersession import (
    build_superseding_record_index,
    find_superseding_record,
    is_superseded_failed_record,
)
from .pipeline_payload_projection import build_export_extras_from_payload
from .source_artifact_integrity import (
    SourceArtifactIssue,
    inspect_deal_source_artifact,
    read_deal_source_artifact_text,
    source_artifact_issue_finding,
)
from .standard_model import build_standard_project
from .streaming_models import IngestedRecord, ItemProgressEvent, PostProcessFinding

SCHEMA_VERSION = 4

REQUIRED_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "jobs": {
        "job_id", "job_type", "status", "downloaded_count", "persisted_count",
        "exception_count", "metadata_json", "summary_json", "created_at", "updated_at",
    },
    "job_events": {
        "event_id", "job_id", "event_ts", "stage", "status", "project_code",
        "archive_path", "error_type", "error_message", "payload_json",
    },
    "records": {
        "record_id", "business_key", "record_family", "business_id", "raw_business_label",
        "identity_anchor", "source_identity_json", "project_code", "project_name",
        "project_type", "exchange", "listing_date", "state", "source_file", "archive_path",
        "latest_revision_id", "last_error_type", "last_error_message", "artifact_status",
        "last_operation_kind", "last_operation_code", "last_operation_message",
        "last_operation_at", "acknowledged_payload_json", "created_at", "updated_at",
    },
    "record_revisions": {
        "revision_id", "record_id", "revision_hash", "parser_payload_json",
        "postprocess_payload_json", "canonical_record_json", "canonical_projection_json",
        "findings_json", "state", "source_file", "created_at",
    },
    "exports": {
        "export_id", "cursor_key", "cursor_id", "mode", "date_from", "date_to",
        "project_type", "output_dir", "summary_json", "created_at", "is_tombstone",
        "pruned_by_retention", "retention_count",
    },
    "export_manifests": {"export_id", "manifest_json", "created_at"},
    "mapping_pending": {
        "pending_id", "record_id", "revision_id", "project_code", "payload_json",
        "created_at", "resolved_at",
    },
    "operation_journal": {
        "operation_id", "operation_type", "status", "recovery_state", "started_at",
        "finished_at", "metadata_json", "manifest_json", "error_json",
    },
    "settings": {"key", "value_json", "updated_at"},
}

_ACTIVE_JOB_STATUSES = frozenset({"starting", "running"})
_TERMINAL_JOB_STATUSES = frozenset({"success", "success_with_warnings", "failed", "interrupted"})
_REPROCESS_SUPERSEDING_RECORD_STATES = frozenset(CANONICAL_RECORD_STATES) - {
    RecordState.SKIPPED.value,
}
_SOURCE_PROJECT_CODE_ALIAS_ORIGINAL_STATES = frozenset(
    {
        RecordState.PENDING_MAPPING.value,
        RecordState.MAPPING_CONFLICT.value,
    }
)
_BUSINESS_RECLASSIFICATION_ORIGINAL_STATES = frozenset(
    {RecordState.READY.value, RecordState.FIELD_MISSING.value}
)
_BUSINESS_RECLASSIFICATION_TARGET_STATES = frozenset(
    {RecordState.READY.value, RecordState.FIELD_MISSING.value}
)
_BUSINESS_RECLASSIFICATION_RECORD_FAMILY = "listing"
_BUSINESS_RECLASSIFICATION_SOURCE_BUSINESS_ID = "capital_increase"
_BUSINESS_RECLASSIFICATION_TARGET_BUSINESS_ID = "equity_transfer"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    downloaded_count INTEGER NOT NULL DEFAULT 0,
    persisted_count INTEGER NOT NULL DEFAULT 0,
    exception_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event_ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    project_code TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    business_key TEXT NOT NULL UNIQUE,
    record_family TEXT NOT NULL DEFAULT 'listing',
    business_id TEXT NOT NULL DEFAULT '',
    raw_business_label TEXT NOT NULL DEFAULT '',
    identity_anchor TEXT NOT NULL DEFAULT '',
    source_identity_json TEXT NOT NULL DEFAULT '{}',
    project_code TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    project_type TEXT NOT NULL DEFAULT '',
    exchange TEXT NOT NULL DEFAULT '',
    listing_date TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    latest_revision_id INTEGER,
    last_error_type TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    artifact_status TEXT NOT NULL DEFAULT 'unknown',
    last_operation_kind TEXT NOT NULL DEFAULT '',
    last_operation_code TEXT NOT NULL DEFAULT '',
    last_operation_message TEXT NOT NULL DEFAULT '',
    last_operation_at TEXT NOT NULL DEFAULT '',
    acknowledged_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    revision_hash TEXT NOT NULL,
    parser_payload_json TEXT NOT NULL DEFAULT '{}',
    postprocess_payload_json TEXT NOT NULL DEFAULT '{}',
    canonical_record_json TEXT NOT NULL DEFAULT '{}',
    canonical_projection_json TEXT NOT NULL DEFAULT '{}',
    findings_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES records(record_id)
);

CREATE TABLE IF NOT EXISTS exports (
    export_id TEXT PRIMARY KEY,
    cursor_key TEXT NOT NULL,
    cursor_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    date_from TEXT NOT NULL DEFAULT '',
    date_to TEXT NOT NULL DEFAULT '',
    project_type TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    is_tombstone INTEGER NOT NULL DEFAULT 0,
    pruned_by_retention INTEGER NOT NULL DEFAULT 0,
    retention_count INTEGER NOT NULL DEFAULT 20
);

CREATE TABLE IF NOT EXISTS export_cursor_records (
    cursor_key TEXT NOT NULL,
    record_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    revision_hash TEXT NOT NULL,
    export_id TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    PRIMARY KEY (cursor_key, record_id)
);

CREATE TABLE IF NOT EXISTS export_manifests (
    export_id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (export_id) REFERENCES exports(export_id)
);

CREATE TABLE IF NOT EXISTS export_cursor_values (
    cursor_id TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mapping_entries (
    entry_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mapping_pending (
    pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    project_code TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rulepacks (
    rulepack_id TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inactive',
    scope_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS operation_journal (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    recovery_state TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_state_listing_date
    ON records(state, listing_date);
CREATE INDEX IF NOT EXISTS idx_revisions_record_created
    ON record_revisions(record_id, created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job_ts
    ON job_events(job_id, event_ts);
CREATE INDEX IF NOT EXISTS idx_mapping_pending_open
    ON mapping_pending(record_id, resolved_at);
CREATE INDEX IF NOT EXISTS idx_operation_journal_status_started
    ON operation_journal(status, started_at);
"""


def _utcnow() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _required_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is empty")
    return text


def _object_payload(value: Dict[str, Any], *, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _mapping_payload(
    value: Mapping[str, Any] | sqlite3.Row | None,
    *,
    field: str,
    allow_none: bool = False,
) -> Dict[str, Any]:
    if value is None:
        if allow_none:
            return {}
        raise ValueError(f"{field} must be an object")
    if isinstance(value, sqlite3.Row):
        return {key: value[key] for key in value.keys()}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _sequence_payload(value: Any, *, field: str, allow_none: bool = True) -> list[Any]:
    if value is None and allow_none:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise TypeError(f"{field} must be a list")
    return list(value)


def _text_sequence_payload(value: Any, *, field: str, allow_none: bool = True) -> list[str]:
    items = _sequence_payload(value, field=field, allow_none=allow_none)
    out: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise TypeError(f"{field}[{index}] must be text")
        out.append(item.strip())
    return out


def _unique_text_sequence_payloads(*sources: tuple[Any, str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value, field in sources:
        for text in _text_sequence_payload(value, field=field):
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _artifact_paths_payload(value: Any, *, field: str) -> list[str]:
    paths: list[str] = []
    for index, item in enumerate(_text_sequence_payload(value, field=field)):
        path = str(item or "").strip()
        if not path:
            raise ValueError(f"{field}[{index}] is empty")
        paths.append(path)
    return paths


def _artifact_checksum_payload(value: Any, *, field: str) -> dict[str, str]:
    payload = _mapping_payload(value, field=field)
    checksums: dict[str, str] = {}
    normalized_paths: set[str] = set()
    for raw_path, raw_digest in payload.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"{field} contains an empty artifact path")
        if not isinstance(raw_digest, str):
            raise ValueError(f"{field}[{raw_path!r}] must be a SHA-256 string")
        path = raw_path.strip()
        digest = raw_digest.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{field}[{path!r}] must be a SHA-256 string")
        normalized_path = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if normalized_path in normalized_paths:
            raise ValueError(f"{field} contains duplicate artifact paths")
        normalized_paths.add(normalized_path)
        checksums[path] = digest
    return checksums


def _regular_file_sha256(path: str, *, field: str) -> str:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError(f"{field} is empty")
    try:
        path_stat = os.lstat(normalized_path)
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise RuntimeError(f"{field} is not a regular file: {normalized_path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(normalized_path, flags)
        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(path_stat, opened_stat):
                raise RuntimeError(f"{field} changed before checksum: {normalized_path}")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            final_stat = os.fstat(fd)
        finally:
            os.close(fd)
        current_stat = os.lstat(normalized_path)
        if (
            not os.path.samestat(opened_stat, final_stat)
            or not os.path.samestat(final_stat, current_stat)
            or final_stat.st_size != opened_stat.st_size
            or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            or final_stat.st_nlink < 1
        ):
            raise RuntimeError(f"{field} changed during checksum: {normalized_path}")
    except (ValueError, RuntimeError):
        raise
    except OSError as exc:
        raise RuntimeError(f"{field} cannot be read: {normalized_path}: {exc}") from exc
    return digest.hexdigest()


def _validate_export_artifact_checksums(
    artifact_paths: list[str],
    manifest: Mapping[str, Any],
) -> dict[str, str]:
    raw_checksums = manifest.get("artifact_checksums")
    if raw_checksums is None:
        if artifact_paths:
            raise ValueError("manifest.artifact_checksums is required when artifacts are present")
        return {}
    checksums = _artifact_checksum_payload(raw_checksums, field="manifest.artifact_checksums")
    artifacts_by_key: dict[str, str] = {}
    for path in artifact_paths:
        key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if key in artifacts_by_key:
            raise ValueError("summary.artifacts contains duplicate paths")
        artifacts_by_key[key] = path
    checksums_by_key = {
        os.path.normcase(os.path.realpath(os.path.abspath(path))): (path, digest)
        for path, digest in checksums.items()
    }
    if set(artifacts_by_key) != set(checksums_by_key):
        raise ValueError("manifest.artifact_checksums must exactly match summary.artifacts")
    for key, path in artifacts_by_key.items():
        expected = checksums_by_key[key][1]
        actual = _regular_file_sha256(path, field="export artifact")
        if actual != expected:
            raise RuntimeError(
                f"export artifact checksum mismatch: {path}: expected {expected}, got {actual}"
            )
    return checksums


def _artifact_path_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _validate_artifacts_within_output_dir(
    artifact_paths: list[str],
    output_dir: Any,
    *,
    field: str,
) -> set[str]:
    if not artifact_paths:
        return set()
    root = _required_text(output_dir, field=f"{field}.output_dir")
    root_path = os.path.abspath(root)
    try:
        root_stat = os.lstat(root_path)
    except OSError as exc:
        raise RuntimeError(f"{field}.output_dir cannot be inspected: {root_path}: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeError(f"{field}.output_dir is not a regular directory: {root_path}")
    root_real = os.path.normcase(os.path.realpath(root_path))

    keys: set[str] = set()
    for index, path in enumerate(artifact_paths):
        try:
            path_stat = os.lstat(path)
        except OSError as exc:
            raise RuntimeError(f"{field}.artifacts[{index}] cannot be inspected: {path}: {exc}") from exc
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise RuntimeError(f"{field}.artifacts[{index}] is not a regular file: {path}")
        key = _artifact_path_key(path)
        try:
            within_root = os.path.commonpath((root_real, key)) == root_real
        except ValueError:
            within_root = False
        if not within_root:
            raise RuntimeError(f"{field}.artifacts[{index}] escapes output_dir: {path}")
        if key in keys:
            raise ValueError(f"{field}.artifacts contains duplicate paths")
        keys.add(key)
    return keys


def _retention_artifact_fingerprint(
    path: str,
    *,
    field: str,
) -> tuple[int, int, int, int, str]:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"{field} cannot be inspected: {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"{field} is not a regular file: {path}")
    digest = _regular_file_sha256(path, field=field)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"{field} disappeared after checksum: {path}: {exc}") from exc
    if (
        not stat.S_ISREG(after.st_mode)
        or not os.path.samestat(before, after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeError(f"{field} changed while being fingerprinted: {path}")
    return (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
        digest,
    )


def _retention_manifest_payload(
    summary_payload: Mapping[str, Any],
    stored_manifest_json: str | None,
    *,
    field: str,
) -> dict[str, Any]:
    summary_manifest: dict[str, Any] = {}
    if "manifest" in summary_payload:
        summary_manifest = _mapping_payload(
            summary_payload.get("manifest"),
            field=f"{field}.summary.manifest",
        )
    stored_manifest = (
        _json_object_loads(stored_manifest_json, field=f"{field}.manifest_json")
        if stored_manifest_json is not None
        else {}
    )
    if summary_manifest and stored_manifest:
        summary_checksums = _artifact_checksum_payload(
            summary_manifest.get("artifact_checksums"),
            field=f"{field}.summary.manifest.artifact_checksums",
        )
        stored_checksums = _artifact_checksum_payload(
            stored_manifest.get("artifact_checksums"),
            field=f"{field}.manifest.artifact_checksums",
        )
        summary_by_key = {_artifact_path_key(path): digest for path, digest in summary_checksums.items()}
        stored_by_key = {_artifact_path_key(path): digest for path, digest in stored_checksums.items()}
        if summary_by_key != stored_by_key:
            raise ValueError(f"{field} summary and stored manifest artifact checksums differ")
    manifest = stored_manifest or summary_manifest
    if not manifest:
        raise ValueError(f"{field} manifest is missing")
    return manifest


def _validate_retention_row_artifacts(
    *,
    export_id: str,
    output_dir: Any,
    summary_payload: Mapping[str, Any],
    stored_manifest_json: str | None,
    artifact_paths: list[str],
) -> dict[str, tuple[int, int, int, int, str]]:
    field = f"export[{export_id}]"
    keys = _validate_artifacts_within_output_dir(
        artifact_paths,
        output_dir,
        field=field,
    )
    if not artifact_paths:
        return {}
    manifest = _retention_manifest_payload(
        summary_payload,
        stored_manifest_json,
        field=field,
    )
    checksums = _artifact_checksum_payload(
        manifest.get("artifact_checksums"),
        field=f"{field}.manifest.artifact_checksums",
    )
    checksums_by_key = {_artifact_path_key(path): digest for path, digest in checksums.items()}
    if keys != set(checksums_by_key):
        raise ValueError(f"{field} manifest artifact checksums must exactly match summary.artifacts")
    fingerprints: dict[str, tuple[int, int, int, int, str]] = {}
    for path in artifact_paths:
        key = _artifact_path_key(path)
        expected = checksums_by_key[key]
        fingerprint = _retention_artifact_fingerprint(path, field=f"{field}.artifact")
        if fingerprint[-1] != expected:
            raise RuntimeError(
                f"{field} artifact checksum mismatch: {path}: expected {expected}, got {fingerprint[-1]}"
            )
        fingerprints[key] = fingerprint
    return fingerprints


def _restore_retention_staged_files(staged_files: list[tuple[str, str]]) -> None:
    restore_errors: list[str] = []
    for original_path, staged_path in reversed(staged_files):
        if not os.path.lexists(staged_path):
            continue
        if os.path.lexists(original_path):
            restore_errors.append(f"destination already exists: {original_path}")
            continue
        try:
            os.replace(staged_path, original_path)
        except OSError as exc:
            restore_errors.append(f"{staged_path} -> {original_path}: {exc}")
    if restore_errors:
        raise RuntimeError("failed to restore retention artifacts: " + "; ".join(restore_errors))


def _cleanup_retention_staged_files(staged_files: list[tuple[str, str]]) -> None:
    for _, staged_path in staged_files:
        try:
            os.remove(staged_path)
        except OSError:
            # The database is already committed. Keeping a hidden staged file is
            # safer than surfacing an error that could make callers delete the
            # newly committed export artifacts.
            continue


def _findings_mapping_payloads(value: Any, *, field: str = "findings_json") -> list[Dict[str, Any]]:
    items = _sequence_payload(value, field=field)
    out: list[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TypeError(f"{field}[{index}] must be an object")
        out.append(dict(item))
    return out


def _legacy_missing_field_values(value: Any, *, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    return _sequence_payload(value, field=field, allow_none=False)


def _finding_evidence_payload(value: Any, *, field: str = "finding.evidence") -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping, got {type(value).__name__}")
    return dict(value)


def _finding_dict_evidence_payload(finding: Mapping[str, Any], *, field: str = "evidence") -> Dict[str, Any]:
    if "evidence" not in finding:
        return {}
    return _finding_evidence_payload(finding.get("evidence"), field=field)


def _postprocess_finding_payload(finding: PostProcessFinding) -> Dict[str, Any]:
    return {
        "severity": str(finding.severity),
        "type": str(finding.type),
        "message": str(finding.message),
        "evidence": _finding_evidence_payload(finding.evidence),
    }


_EXPORT_PROJECTION_FINDING_TYPES = frozenset(
    {"canonical_field_missing", "export_field_missing"}
)


def _refresh_export_projection_findings(
    findings: Iterable[PostProcessFinding],
    canonical_record: Dict[str, Any],
) -> list[PostProcessFinding]:
    """Recompute projection findings whenever a maintenance path can reach READY."""
    from peap_core.record_state_policy import classify_record_state

    base_findings = [
        item
        for item in findings
        if str(item.type or "").strip() not in _EXPORT_PROJECTION_FINDING_TYPES
    ]
    if classify_record_state(base_findings) != RecordState.READY:
        return base_findings
    return list(append_export_projection_findings(base_findings, canonical_record))


def _json_loads(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _source_identity_json_loads(raw: str | None) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str) and not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("source_identity_json must be an object")
    return dict(payload)


def _json_object_loads(raw: str | None, *, field: str = "json") -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str) and not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be an object")
    return dict(payload)


def _acknowledged_payload_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="acknowledged_payload_json")


def _payload_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="payload_json")


def _stored_object_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="value_json")


def _job_summary_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="summary_json")


def _job_metadata_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="metadata_json")


def _job_event_payload_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="payload_json")


def _operation_journal_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="metadata_json")


def _mapping_metadata_json_loads(raw: str | None) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str) and not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("metadata_json must be an object")
    return dict(payload)


def _mapping_pending_payload_json_loads(raw: str | None) -> Dict[str, Any]:
    return _json_object_loads(raw, field="payload_json")


def _json_list_loads(raw: str | None) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, str) and not raw.strip():
        return []
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("json must be a list")
    return list(payload)


def _findings_json_loads(raw: str | None) -> list[Any]:
    return _json_list_loads(raw)


def _is_sqlite_uri(db_path: str) -> bool:
    return str(db_path or "").startswith("file:")


def _sqlite_uri_parts(db_path: str):
    return urlsplit(str(db_path or ""))


def _sqlite_uri_mode(db_path: str) -> str:
    if not _is_sqlite_uri(db_path):
        return ""
    query = parse_qs(_sqlite_uri_parts(db_path).query)
    return str(query.get("mode", [""])[0] or "").strip().lower()


def _sqlite_uri_local_path(db_path: str) -> str:
    if not _is_sqlite_uri(db_path):
        return ""
    parts = _sqlite_uri_parts(db_path)
    if parts.netloc and parts.netloc not in {"", "localhost"}:
        return ""
    raw_path = parts.path or ""
    if not raw_path or raw_path == ":memory:":
        return ""
    return url2pathname(raw_path)


def _normalize_db_path(db_path: str) -> str:
    raw_path = str(db_path or "").strip()
    if not raw_path:
        raise ValueError("db_path is empty")
    if _is_sqlite_uri(raw_path):
        return raw_path
    return os.path.abspath(raw_path)


def _ensure_db_parent_dir(db_path: str) -> None:
    if _is_sqlite_uri(db_path):
        if _sqlite_uri_mode(db_path) == "ro":
            return
        resolved_path = _sqlite_uri_local_path(db_path)
        if not resolved_path:
            return
        parent_dir = os.path.dirname(resolved_path)
    else:
        parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)


def _connection_kwargs(db_path: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"database": db_path}
    if _is_sqlite_uri(db_path):
        kwargs["uri"] = True
    return kwargs

_SETTING_DECODE_ERROR_KEY = "__peap_settings_decode_error__"


def _sync_canonical_record_diagnostics(
    canonical_record: Dict[str, Any],
    findings: list,
) -> str:
    """Update canonical_record_json diagnostics and policy_state to match findings."""
    canonical_record["diagnostics"] = [
        _postprocess_finding_payload(item)
        for item in findings
    ]
    canonical_record.setdefault("policy_state", {})["findings"] = [
        str(item.type) for item in findings
    ]
    return _json_dumps(canonical_record)


def _invalid_source_page_finding(*, source_url: str) -> Dict[str, Any]:
    return {
        "severity": "info",
        "type": "rule_filtered",
        "message": "CBEX deal source URL is not a detail page",
        "evidence": {
            "reason_code": "invalid_source_page",
            "source_url": str(source_url or "").strip(),
        },
    }


TERMINAL_MAINTENANCE_ERROR_TYPES = {"invalid_source_page", "superseded_by_record"}


def _maintenance_terminal_error_filter_sql() -> str:
    quoted = ", ".join(f"'{item}'" for item in sorted(TERMINAL_MAINTENANCE_ERROR_TYPES))
    return f"records.last_error_type NOT IN ({quoted})"


def _superseded_record_finding(*, superseded_by_record: dict[str, Any]) -> Dict[str, Any]:
    return {
        "severity": "info",
        "type": "rule_filtered",
        "message": "Record shell superseded by a canonical record",
        "evidence": {
            "reason_code": "superseded_by_record",
            "superseded_by_record_id": str(superseded_by_record.get("record_id") or ""),
            "superseded_by_state": str(superseded_by_record.get("state") or ""),
            "superseded_by_project_code": str(superseded_by_record.get("project_code") or ""),
        },
    }


def _first_text_from_mapping(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _sync_canonical_record_dict_diagnostics(
    canonical_record: Dict[str, Any],
    findings: list[dict[str, Any]],
) -> str:
    canonical_record["diagnostics"] = [dict(item) for item in findings]
    canonical_record.setdefault("policy_state", {})["findings"] = [
        str(item.get("type") or "").strip() for item in findings if str(item.get("type") or "").strip()
    ]
    return _json_dumps(canonical_record)


ARTIFACT_UNAVAILABLE_ERROR_TYPES = frozenset({
    "source_artifact_invalid",
    "source_artifact_missing",
    "invalid_source_page",
})


def _record_has_usable_artifact(row: sqlite3.Row) -> bool:
    if str(row["last_error_type"] or "").strip() in ARTIFACT_UNAVAILABLE_ERROR_TYPES:
        return False
    return declared_artifact_is_available(
        source_file=row["source_file"],
        archive_path=row["archive_path"],
    )


def _backfill_failed_record_contracts(conn: sqlite3.Connection) -> None:
    failed_state_placeholders = ",".join("?" for _ in FAILED_RECORD_STATES)
    rows = conn.execute(
        f"""
        SELECT
            records.record_id,
            records.project_code,
            records.record_family,
            records.business_key,
            records.identity_anchor,
            records.source_identity_json,
            records.source_file,
            records.state,
            revisions.parser_payload_json
        FROM records
        LEFT JOIN record_revisions AS revisions
          ON revisions.revision_id = records.latest_revision_id
        WHERE records.state IN ({failed_state_placeholders})
          AND (
            records.identity_anchor = ''
            OR records.source_identity_json = '{{}}'
            OR records.business_id = ''
            OR records.business_key NOT LIKE 'failed:%'
          )
        """,
        list(FAILED_RECORD_STATES),
    ).fetchall()
    for row in rows:
        payload = _payload_json_loads(row["parser_payload_json"])
        existing_source_identity = _source_identity_json_loads(row["source_identity_json"])
        source_identity = _merge_source_identity(
            existing_source_identity,
            _build_failed_source_identity(
                project_code=str(row["project_code"] or ""),
                source_file=str(row["source_file"] or ""),
                state=str(row["state"] or ""),
                payload=payload if isinstance(payload, dict) else {},
            ),
        )
        identity_anchor = _first_non_empty(
            str(row["identity_anchor"] or "").strip(),
            build_identity_anchor(record_state=str(row["state"] or ""), source_identity=source_identity),
        )
        stored_business_id = _first_non_empty(
            source_identity.get("business_id"),
            source_identity.get("business_id_hint"),
        )
        stored_raw_business_label = _first_non_empty(
            source_identity.get("business_label_hint"),
            source_identity.get("project_type_fallback"),
        )
        conn.execute(
            """
            UPDATE records
            SET record_family = ?,
                business_id = ?,
                raw_business_label = ?,
                identity_anchor = ?,
                source_identity_json = ?,
                business_key = ?
            WHERE record_id = ?
            """,
            (
                _first_non_empty(
                    str(row["record_family"] or "").strip(),
                    str(source_identity.get("record_family") or "").strip(),
                    "listing",
                ),
                stored_business_id,
                stored_raw_business_label,
                identity_anchor,
                _json_dumps(source_identity),
                f"failed:{identity_anchor}",
                str(row["record_id"]),
            ),
        )


def _legacy_missing_fields_from_findings(raw_findings: Any) -> list[dict[str, str]]:
    missing_fields: list[Any] = []
    for finding in _findings_mapping_payloads(raw_findings, field="findings"):
        finding_type = str(finding.get("type") or "").strip()
        if finding_type not in {"export_field_missing", "canonical_field_missing"}:
            continue
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        if "missing_fields" in evidence:
            missing_fields.extend(
                _legacy_missing_field_values(
                    evidence.get("missing_fields"),
                    field="finding.evidence.missing_fields",
                )
            )
        if not missing_fields and str(finding.get("message") or "").strip():
            missing_fields.append(str(finding.get("message") or "").strip())
    return normalize_missing_fields(missing_fields)


def _backfill_field_missing_acknowledgements(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            records.record_id,
            records.state,
            records.latest_revision_id,
            records.acknowledged_payload_json,
            revisions.findings_json
        FROM records
        JOIN record_revisions AS revisions
          ON revisions.revision_id = records.latest_revision_id
        """
    ).fetchall()
    for row in rows:
        previous_state = str(row["state"] or "")
        findings = _findings_json_loads(row["findings_json"])
        missing_fields = _legacy_missing_fields_from_findings(findings)
        if not missing_fields:
            continue
        existing_ack = _acknowledged_payload_json_loads(row["acknowledged_payload_json"])
        _mapping_payload(existing_ack.get("field_missing"), field="field_missing", allow_none=True)
        ack_payload = build_field_missing_ack_payload(
            previous_state=previous_state,
            evidence_source="legacy_findings",
            missing_fields=missing_fields,
            revision_id=int(row["latest_revision_id"]) if row["latest_revision_id"] is not None else None,
        )
        existing_ack.update(ack_payload)
        conn.execute(
            """
            UPDATE records
            SET state = 'field_missing',
                acknowledged_payload_json = ?
            WHERE record_id = ?
            """,
            (_json_dumps(existing_ack), str(row["record_id"])),
        )
        conn.execute(
            """
            UPDATE record_revisions
            SET state = 'field_missing'
            WHERE revision_id = ?
            """,
            (int(row["latest_revision_id"]),),
        )
        field_missing_payload = _mapping_payload(
            existing_ack.get("field_missing"),
            field="field_missing",
            allow_none=True,
        )
        conn.execute(
            """
            INSERT INTO audit_log (event_ts, action, payload_json)
            VALUES (?, ?, ?)
            """,
            (
                _utcnow(),
                "field_missing_backfill",
                _json_dumps(
                    {
                        "record_id": str(row["record_id"]),
                        "previous_state": previous_state,
                        "evidence_source": field_missing_payload.get("evidence_source", "legacy_findings"),
                        "missing_fields_hash": field_missing_payload.get("missing_fields_hash", ""),
                    }
                ),
            ),
        )


def _apply_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(records)").fetchall()
    }
    migration_columns = [
        ("record_family", "TEXT NOT NULL DEFAULT 'listing'"),
        ("business_id", "TEXT NOT NULL DEFAULT ''"),
        ("raw_business_label", "TEXT NOT NULL DEFAULT ''"),
        ("identity_anchor", "TEXT NOT NULL DEFAULT ''"),
        ("source_identity_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("artifact_status", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("last_operation_kind", "TEXT NOT NULL DEFAULT ''"),
        ("last_operation_code", "TEXT NOT NULL DEFAULT ''"),
        ("last_operation_message", "TEXT NOT NULL DEFAULT ''"),
        ("last_operation_at", "TEXT NOT NULL DEFAULT ''"),
        ("acknowledged_payload_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for column_name, column_spec in migration_columns:
        if column_name in existing_columns:
            continue
        conn.execute(f"ALTER TABLE records ADD COLUMN {column_name} {column_spec}")
    revision_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(record_revisions)").fetchall()
    }
    revision_migration_columns = [
        ("canonical_record_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("canonical_projection_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for column_name, column_spec in revision_migration_columns:
        if column_name in revision_columns:
            continue
        conn.execute(f"ALTER TABLE record_revisions ADD COLUMN {column_name} {column_spec}")
    export_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(exports)").fetchall()
    }
    export_migration_columns = [
        ("cursor_id", "TEXT NOT NULL DEFAULT ''"),
        ("is_tombstone", "INTEGER NOT NULL DEFAULT 0"),
        ("pruned_by_retention", "INTEGER NOT NULL DEFAULT 0"),
        ("retention_count", "INTEGER NOT NULL DEFAULT 20"),
    ]
    for column_name, column_spec in export_migration_columns:
        if column_name in export_columns:
            continue
        conn.execute(f"ALTER TABLE exports ADD COLUMN {column_name} {column_spec}")
    conn.execute("UPDATE exports SET cursor_id = cursor_key WHERE cursor_id = ''")
    _backfill_failed_record_contracts(conn)
    _backfill_field_missing_acknowledgements(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _canonical_record_family(value: Any) -> str:
    family = _clean_text(value)
    if not family:
        return "listing"
    return get_family_descriptor(family).family_id


def _resolve_business_kernel_fields(
    *,
    record_family: Any,
    project_type: Any,
    canonical_record: Dict[str, Any] | None = None,
) -> tuple[str, str]:
    family = _canonical_record_family(record_family)
    canonical = _maintenance_mapping_payload(canonical_record, field="canonical_record")
    business_identity = _maintenance_mapping_payload(
        canonical.get("business_identity"),
        field="canonical_record.business_identity",
    )
    canonical_fields = _maintenance_mapping_payload(
        canonical.get("canonical_fields"),
        field="canonical_record.canonical_fields",
    )
    descriptor = resolve_business_descriptor_by_project_type(
        _clean_text(project_type) or _clean_text(canonical_fields.get("project_type")),
        family_id=family,
    )
    raw_business_label = (
        _clean_text(business_identity.get("raw_business_label"))
        or _clean_text(business_identity.get("business_label"))
        or _clean_text(getattr(descriptor, "project_type_label", ""))
        or _clean_text(project_type)
        or _clean_text(canonical_fields.get("project_type"))
    )
    business_id = (
        _clean_text(business_identity.get("business_id"))
        or _clean_text(getattr(descriptor, "business_id", ""))
        or ""
    )
    raw_business_label = raw_business_label or ""
    if family != "listing":
        return business_id, raw_business_label
    return business_id, raw_business_label


def _record_business_key(project_code: str, source_file: str) -> str:
    return _scoped_record_business_key(
        project_code=project_code,
        source_file=source_file,
        record_family="listing",
        business_id="",
        source_id="",
    )


def _legacy_record_business_keys(project_code: str, source_file: str) -> tuple[str, ...]:
    keys: list[str] = []
    code = str(project_code or "").strip().upper()
    if code:
        keys.append(code)
    return tuple(keys)


def _normalize_scope_component(value: Any, *, uppercase: bool = False) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text.lower() == "all":
        return ""
    return text.upper() if uppercase else text.lower()


def _normalize_scope_family(value: Any) -> str:
    text = _clean_text(value)
    if not text or text.lower() == "all":
        return ""
    try:
        return _canonical_record_family(text)
    except Exception:
        return text.lower()


def _resolve_scope_source_id(*, source_identity: Dict[str, Any] | None, exchange: Any = "") -> str:
    if source_identity is None:
        payload: dict[str, Any] = {}
    elif isinstance(source_identity, Mapping):
        payload = dict(source_identity)
    else:
        raise ValueError("source_identity must be an object")
    raw_source = _first_non_empty(payload.get("source_id"), payload.get("exchange"), exchange)
    if not raw_source:
        return ""
    descriptor = resolve_source_descriptor(raw_source, allow_substring=True)
    source_id = descriptor.source_id if descriptor is not None else str(raw_source)
    return _normalize_scope_component(source_id)


def _scope_signature(*, record_family: Any, business_id: Any, source_id: Any) -> tuple[str, str, str]:
    return (
        _normalize_scope_family(record_family),
        _normalize_scope_component(business_id),
        _normalize_scope_component(source_id),
    )


def _scope_matches(
    *,
    record_family: Any,
    business_id: Any,
    source_id: Any,
    expected_family: Any = "",
    expected_business_id: Any = "",
    expected_source_id: Any = "",
) -> bool:
    family, business, source = _scope_signature(
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    expected_family_norm, expected_business_norm, expected_source_norm = _scope_signature(
        record_family=expected_family,
        business_id=expected_business_id,
        source_id=expected_source_id,
    )
    if expected_family_norm and family != expected_family_norm:
        return False
    if expected_business_norm and business != expected_business_norm:
        return False
    if expected_source_norm and source != expected_source_norm:
        return False
    return True


def _scope_is_compatible_for_upgrade(
    *,
    record_family: Any,
    business_id: Any,
    source_id: Any,
    expected_family: Any,
    expected_business_id: Any,
    expected_source_id: Any,
) -> bool:
    family, business, source = _scope_signature(
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    expected_family_norm, expected_business_norm, expected_source_norm = _scope_signature(
        record_family=expected_family,
        business_id=expected_business_id,
        source_id=expected_source_id,
    )
    if expected_family_norm and family and family != expected_family_norm:
        return False
    if not expected_business_norm and business:
        return False
    if expected_business_norm and business and business != expected_business_norm:
        return False
    if expected_source_norm and source and source != expected_source_norm:
        return False
    return True


def _scoped_identity_token(
    kind: str,
    value: Any,
    *,
    record_family: Any,
    business_id: Any,
    source_id: Any,
) -> str:
    normalized_kind = _clean_text(kind).lower()
    if normalized_kind not in {"project_code", "project_id", "page_url"}:
        return ""
    raw_value = _clean_text(value)
    if not raw_value:
        return ""
    normalized_value = raw_value.upper() if normalized_kind in {"project_code", "project_id"} else raw_value
    family, business, source = _scope_signature(
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    return f"scope:{family}|{business}|{source}|{normalized_kind}:{normalized_value}"


def _split_identity_token(token: Any) -> tuple[str, str]:
    text = _clean_text(token)
    kind, sep, value = text.partition(":")
    if not sep:
        return "", ""
    normalized_kind = _clean_text(kind).lower()
    normalized_value = _clean_text(value)
    return normalized_kind, normalized_value


def _scoped_record_business_key(
    *,
    project_code: str,
    source_file: str,
    record_family: Any,
    business_id: Any,
    source_id: Any,
) -> str:
    code = str(project_code or "").strip().upper()
    family, business, source = _scope_signature(
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    scope_prefix = f"{family}|{business}|{source}"
    if code:
        return f"{scope_prefix}|{code}"
    digest = hashlib.sha1(f"{scope_prefix}|{str(source_file or '')}".encode("utf-8")).hexdigest()
    return f"{scope_prefix}|source:{digest}"


def _derive_canonical_projection(canonical_record: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(canonical_record, dict) or not canonical_record:
        return {}
    payload, _ = project_canonical_record_to_export_payload(canonical_record, fail_on_missing=False)
    return dict(payload)


def _canonical_export_projection_payload(canonical_record: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(canonical_record, dict) or not canonical_record:
        return {}
    payload, _ = project_canonical_record_to_export_payload(canonical_record, fail_on_missing=False)
    return dict(payload)


def _maintenance_mapping_payload(value: Any, *, field: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _evidence_mapping_payload(value: Any, *, field: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return dict(value)


def _merge_maintenance_payload_with_canonical_projection(
    payload: Dict[str, Any],
    canonical_record: Dict[str, Any],
) -> Dict[str, Any]:
    merged = _maintenance_mapping_payload(payload, field="payload")
    canonical = _maintenance_mapping_payload(canonical_record, field="canonical_record")
    export_extras = _maintenance_mapping_payload(
        canonical.get("export_extras"),
        field="canonical_record.export_extras",
    )
    canonical_fields = _maintenance_mapping_payload(
        canonical.get("canonical_fields"),
        field="canonical_record.canonical_fields",
    )
    business_identity = _maintenance_mapping_payload(
        canonical.get("business_identity"),
        field="canonical_record.business_identity",
    )
    for key, value in export_extras.items():
        if _has_value(value):
            merged.setdefault(str(key), value)
    for key, value in _canonical_export_projection_payload(canonical).items():
        if not _has_value(value):
            continue
        merged.setdefault(str(key), value)
    for key, value in (
        ("project_code", canonical_fields.get("project_code")),
        ("project_name", canonical_fields.get("project_name")),
        ("project_type", canonical_fields.get("project_type")),
        ("business_id", business_identity.get("business_id")),
    ):
        if _has_value(value):
            merged.setdefault(key, value)
    return merged


def _drop_maintenance_canonical_supplements(
    payload: Dict[str, Any],
    *,
    original_postprocess_payload: Dict[str, Any],
    canonical_record: Dict[str, Any],
) -> Dict[str, Any]:
    cleaned = _maintenance_mapping_payload(payload, field="payload")
    original = _maintenance_mapping_payload(original_postprocess_payload, field="original_postprocess_payload")
    canonical = _maintenance_mapping_payload(canonical_record, field="canonical_record")
    export_extras = _maintenance_mapping_payload(
        canonical.get("export_extras"),
        field="canonical_record.export_extras",
    )
    supplemental_keys = set(_canonical_export_projection_payload(canonical))
    supplemental_keys.update(export_extras)
    supplemental_keys.update({"project_code", "project_name", "project_type", "business_id"})
    for key in supplemental_keys:
        if key in original:
            continue
        cleaned.pop(key, None)
    return cleaned


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _values_equal(existing: Any, candidate: Any) -> bool:
    if isinstance(candidate, str):
        return str(existing or "").strip() == candidate
    if isinstance(existing, str):
        return existing.strip() == str(candidate)
    return existing == candidate


def _merge_record_payloads(
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if parser_payload is None:
        merged: Dict[str, Any] = {}
    elif isinstance(parser_payload, Mapping):
        merged = dict(parser_payload)
    else:
        raise ValueError("parser_payload must be an object")

    if postprocess_payload is None:
        postprocess_items: Iterable[tuple[Any, Any]] = ()
    elif isinstance(postprocess_payload, Mapping):
        postprocess_items = postprocess_payload.items()
    else:
        raise ValueError("postprocess_payload must be an object")

    for key, value in postprocess_items:
        if not _has_value(value):
            continue
        merged[str(key)] = value
    return merged


def _repair_listing_record_contract(
    *,
    record_id: str,
    record_family: str,
    project_code: str,
    project_name: str,
    project_type: str,
    exchange: str,
    listing_date: str,
    source_identity: Dict[str, Any] | None,
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
    canonical_record: Dict[str, Any] | None,
) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    merged_payload = _merge_record_payloads(parser_payload, postprocess_payload)
    standard = build_standard_project(merged_payload)
    repaired = _maintenance_mapping_payload(canonical_record, field="canonical_record")
    canonical_fields = _maintenance_mapping_payload(
        repaired.get("canonical_fields"),
        field="canonical_record.canonical_fields",
    )
    raw_start_date = (
        _clean_text(standard.start_date)
        or _clean_text(canonical_fields.get("start_date"))
        or _clean_text(listing_date)
    )
    repaired_listing_date = _normalize_date_text(
        _clean_text(standard.start_date)
        or _clean_text(listing_date)
        or raw_start_date
    )

    candidate_fields = {
        "project_code": project_code or _clean_text(standard.project_code) or canonical_fields.get("project_code"),
        "project_name": project_name or _clean_text(standard.project_name) or canonical_fields.get("project_name"),
        "project_type": project_type or _clean_text(standard.business_type) or canonical_fields.get("project_type"),
        "status": _clean_text(standard.status) or canonical_fields.get("status"),
        "exchange": exchange or _clean_text(standard.exchange) or canonical_fields.get("exchange"),
        "start_date": raw_start_date,
        "price": standard.price if _has_value(standard.price) else canonical_fields.get("price"),
        "seller": _clean_text(standard.seller) or canonical_fields.get("seller"),
        "source_type": _clean_text(standard.source_type) or canonical_fields.get("source_type"),
        "group_name": _clean_text(standard.group_name) or canonical_fields.get("group_name"),
        "listing_times": merged_payload.get("挂牌次数")
        if _has_value(merged_payload.get("挂牌次数"))
        else standard.listing_times if _has_value(standard.listing_times) else canonical_fields.get("listing_times"),
    }
    for field_name, candidate in candidate_fields.items():
        if not _has_value(candidate):
            continue
        normalized_candidate = candidate.strip() if isinstance(candidate, str) else candidate
        if _values_equal(canonical_fields.get(field_name), normalized_candidate):
            continue
        canonical_fields[field_name] = normalized_candidate

    repaired["record_id"] = _clean_text(repaired.get("record_id")) or record_id
    repaired["record_family"] = _clean_text(repaired.get("record_family")) or record_family or "listing"
    if "source_identity" not in repaired and isinstance(source_identity, dict):
        repaired["source_identity"] = dict(source_identity)
    business_identity = _maintenance_mapping_payload(
        repaired.get("business_identity"),
        field="canonical_record.business_identity",
    )
    if not _has_value(business_identity.get("project_code")) and _has_value(canonical_fields.get("project_code")):
        business_identity["project_code"] = canonical_fields["project_code"]
    if business_identity:
        repaired["business_identity"] = business_identity
    repaired["canonical_fields"] = canonical_fields
    if "field_provenance" not in repaired:
        repaired["field_provenance"] = {}
    if "diagnostics" not in repaired:
        repaired["diagnostics"] = []
    if "policy_state" not in repaired:
        repaired["policy_state"] = {}
    if not _has_value(repaired.get("normalizer_version")):
        repaired["normalizer_version"] = "streaming_ingest/v1"

    export_extras = _maintenance_mapping_payload(
        repaired.get("export_extras"),
        field="canonical_record.export_extras",
    )
    projected_export_extras = build_export_extras_from_payload(
        merged_payload,
        record_family=record_family or str(repaired.get("record_family") or "listing"),
        project_type=project_type or _clean_text(canonical_fields.get("project_type")),
        business_id=_clean_text(business_identity.get("business_id")),
    )
    for field_name, candidate in projected_export_extras.items():
        normalized_candidate = candidate.strip() if isinstance(candidate, str) else candidate
        if _values_equal(export_extras.get(field_name), normalized_candidate):
            continue
        export_extras[field_name] = normalized_candidate
    if export_extras:
        repaired["export_extras"] = export_extras

    return repaired_listing_date, repaired, _derive_canonical_projection(repaired)


def _bind_ingested_record_identity(record: IngestedRecord, record_id: str) -> IngestedRecord:
    canonical_record = _maintenance_mapping_payload(record.canonical_record, field="canonical_record")
    if canonical_record:
        canonical_record["record_id"] = record_id
    business_id, raw_business_label = _resolve_business_kernel_fields(
        record_family=record.record_family,
        project_type=record.project_type,
        canonical_record=canonical_record,
    )
    business_identity = _maintenance_mapping_payload(
        canonical_record.get("business_identity"),
        field="canonical_record.business_identity",
    )
    if business_id:
        business_identity["business_id"] = business_id
    if raw_business_label:
        business_identity["raw_business_label"] = raw_business_label
    if business_identity:
        canonical_record["business_identity"] = business_identity
    canonical_projection = _derive_canonical_projection(canonical_record)
    return replace(
        record,
        record_id=record_id,
        canonical_record=canonical_record,
        canonical_projection=canonical_projection,
    )


def _strict_conflict_path_alias(*, evidence_path: Any, canonical_path: Any) -> bool:
    """Recognize only an identical snapshot under the managed conflict suffix."""
    evidence = os.path.abspath(str(evidence_path or "").strip())
    canonical = os.path.abspath(str(canonical_path or "").strip())
    if not evidence or not canonical or os.path.normcase(evidence) == os.path.normcase(canonical):
        return False
    if os.path.normcase(os.path.dirname(evidence)) != os.path.normcase(os.path.dirname(canonical)):
        return False
    evidence_stem, evidence_ext = os.path.splitext(os.path.basename(evidence))
    canonical_stem, canonical_ext = os.path.splitext(os.path.basename(canonical))
    if os.path.normcase(evidence_ext) != os.path.normcase(canonical_ext):
        return False
    suffix = evidence_stem[len(canonical_stem) :]
    if not evidence_stem.startswith(canonical_stem) or not re.fullmatch(r"__conflict[1-9][0-9]*", suffix):
        return False
    try:
        return _regular_file_sha256(evidence, field="conflict evidence") == _regular_file_sha256(
            canonical,
            field="canonical evidence",
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _same_snapshot_content(*, evidence_path: Any, canonical_path: Any) -> bool:
    """Compare two regular snapshot files without treating different paths as aliases."""
    evidence = os.path.abspath(str(evidence_path or "").strip())
    canonical = os.path.abspath(str(canonical_path or "").strip())
    if not evidence or not canonical:
        return False
    if os.path.normcase(evidence) == os.path.normcase(canonical):
        return True
    try:
        return _regular_file_sha256(evidence, field="evidence snapshot") == _regular_file_sha256(
            canonical,
            field="canonical snapshot",
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _reconcile_conflict_provenance(
    *,
    record: IngestedRecord,
    existing_source_identity: Mapping[str, Any] | None,
) -> IngestedRecord:
    """Keep the first conflict evidence path when the next input is canonical."""
    existing_identity = _mapping_payload(existing_source_identity, field="existing_source_identity", allow_none=True)
    incoming_identity = _mapping_payload(record.source_identity, field="source_identity", allow_none=True)
    existing_evidence = str(existing_identity.get("original_evidence_path") or "").strip()
    incoming_evidence = str(incoming_identity.get("original_evidence_path") or "").strip()
    canonical_path = str(record.archive_path or record.source_file or "").strip()
    if not existing_evidence or not canonical_path:
        return record
    if incoming_evidence and os.path.normcase(os.path.abspath(incoming_evidence)) != os.path.normcase(
        os.path.abspath(canonical_path)
    ):
        return record
    if not (
        _strict_conflict_path_alias(evidence_path=existing_evidence, canonical_path=canonical_path)
        or _same_snapshot_content(evidence_path=existing_evidence, canonical_path=canonical_path)
    ):
        return record
    incoming_identity["original_evidence_path"] = existing_evidence
    canonical_record = _maintenance_mapping_payload(record.canonical_record, field="canonical_record")
    canonical_record["source_identity"] = dict(incoming_identity)
    return _bind_ingested_record_identity(
        replace(record, source_identity=incoming_identity, canonical_record=canonical_record),
        record.record_id,
    )


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _unique_text_values(*values: Any) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [value]
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _candidate_identity_token(kind: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized_kind = str(kind or "").strip()
    if normalized_kind in {"project_code", "project_id"}:
        text = text.upper()
    return f"{normalized_kind}:{text}" if normalized_kind else text


def _record_reprocess_identity_tokens(record: Mapping[str, Any]) -> set[str]:
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    parser_payload = _payload_json_loads(record.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(record.get("postprocess_payload_json"))
    tokens: set[str] = set()
    for raw_token in _unique_text_sequence_payloads(
        (source_identity.get("candidate_tokens"), "source_identity.candidate_tokens"),
    ):
        kind, value = _split_identity_token(raw_token)
        if kind not in {"project_code", "project_id", "page_url"}:
            continue
        token = _candidate_identity_token(kind, value)
        if token:
            tokens.add(token)
    payloads = (record, source_identity, parser_payload, postprocess_payload)
    for payload in payloads:
        for field in ("project_code", "项目编号"):
            token = _candidate_identity_token("project_code", payload.get(field))
            if token:
                tokens.add(token)
        for field in ("project_id", "content_id", "contentId"):
            token = _candidate_identity_token("project_id", payload.get(field))
            if token:
                tokens.add(token)
        for field in ("source_url", "page_url", "detail_url"):
            token = _candidate_identity_token("page_url", payload.get(field))
            if token:
                tokens.add(token)
    return tokens


def _record_reprocess_project_code(record: Mapping[str, Any]) -> str:
    """Return the strongest normalized project code available for a record."""
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    parser_payload = _payload_json_loads(record.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(record.get("postprocess_payload_json"))
    for payload in (record, source_identity, parser_payload, postprocess_payload):
        for field in ("project_code", "项目编号"):
            normalized = _normalize_scope_component(payload.get(field), uppercase=True)
            if normalized:
                return normalized
    return ""


def _record_reprocess_project_codes_conflict(
    original: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> bool:
    original_code = _record_reprocess_project_code(original)
    replacement_code = _record_reprocess_project_code(replacement)
    if not original_code or not replacement_code or original_code == replacement_code:
        return False
    return not bool(
        _record_reprocess_source_project_code_alias_relationship(
            original,
            replacement,
        )
    )


def _canonical_reprocess_source(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    descriptor = resolve_source_descriptor(text, allow_substring=True)
    if descriptor is not None:
        return descriptor.source_id
    return _normalize_scope_component(text)


def _record_reprocess_source_scope(record: Mapping[str, Any]) -> tuple[str, str, str]:
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    parser_payload = _payload_json_loads(record.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(record.get("postprocess_payload_json"))
    raw_exchange = _first_non_empty(
        record.get("exchange"),
        source_identity.get("exchange"),
        parser_payload.get("exchange"),
        parser_payload.get("交易所"),
        postprocess_payload.get("exchange"),
        postprocess_payload.get("交易所"),
    )
    raw_source_id = _first_non_empty(
        source_identity.get("source_id"),
        parser_payload.get("source_id"),
        postprocess_payload.get("source_id"),
        raw_exchange,
    )
    return (
        _normalize_scope_component(raw_source_id),
        _canonical_reprocess_source(raw_source_id),
        _canonical_reprocess_source(raw_exchange),
    )


def _record_reprocess_sidecar_status_is_trusted(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata")
    containers = (payload, metadata) if isinstance(metadata, Mapping) else (payload,)
    for container in containers:
        if "save_status" not in container:
            continue
        if str(container.get("save_status") or "").strip().lower() not in {
            "complete",
            "completed",
            "success",
            "succeeded",
        }:
            return False
    return True


def _record_reprocess_sidecar_status_is_explicitly_complete(
    payload: Mapping[str, Any],
) -> bool:
    metadata = payload.get("metadata")
    containers = (payload, metadata) if isinstance(metadata, Mapping) else (payload,)
    statuses = [
        str(container.get("save_status") or "").strip().lower()
        for container in containers
        if "save_status" in container
    ]
    return bool(statuses) and all(
        status in {"complete", "completed", "success", "succeeded"}
        for status in statuses
    )


def _record_reprocess_project_name(record: Mapping[str, Any]) -> str:
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    parser_payload = _payload_json_loads(record.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(record.get("postprocess_payload_json"))
    for payload in (record, source_identity, parser_payload, postprocess_payload):
        for field in ("project_name", "项目名称"):
            value = str(payload.get(field) or "").strip()
            if value:
                return value
    return ""


def _normalized_reprocess_project_name(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(normalized.casefold().split())


def _shenzhen_content_id_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    hostname = str(parsed.hostname or "").strip().lower()
    if hostname != "sotcbb.com" and not hostname.endswith(".sotcbb.com"):
        return ""
    if str(parsed.path or "").rstrip("/").lower() != "/bddetail.htm":
        return ""
    values = [
        str(item or "").strip()
        for item in parse_qs(parsed.query).get("contentId", ())
        if str(item or "").strip()
    ]
    if len(set(values)) != 1:
        return ""
    return values[0]


def _record_reprocess_project_ids(record: Mapping[str, Any]) -> set[str]:
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    parser_payload = _payload_json_loads(record.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(record.get("postprocess_payload_json"))
    project_ids: set[str] = set()
    for raw_token in _unique_text_sequence_payloads(
        (source_identity.get("candidate_tokens"), "source_identity.candidate_tokens"),
    ):
        kind, value = _split_identity_token(raw_token)
        if kind == "project_id" and value:
            project_ids.add(value.upper())
    for payload in (record, source_identity, parser_payload, postprocess_payload):
        for field in ("project_id", "content_id", "contentId"):
            value = str(payload.get(field) or "").strip()
            if value:
                project_ids.add(value.upper())
        for field in ("source_url", "page_url", "detail_url"):
            value = _shenzhen_content_id_from_url(payload.get(field))
            if value:
                project_ids.add(value.upper())
    return project_ids


def _iter_reprocess_sidecar_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_reprocess_sidecar_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_reprocess_sidecar_mappings(child)


def _verified_shenzhen_source_project_code_alias_sidecar(
    replacement: Mapping[str, Any],
    *,
    canonical_project_code: str,
    source_project_code: str,
    project_id: str,
    project_name: str,
) -> bool:
    source_identity = _source_identity_json_loads(replacement.get("source_identity_json"))
    candidate_paths = _unique_text_values(
        replacement.get("archive_path"),
        replacement.get("source_file"),
        source_identity.get("original_evidence_path"),
        source_identity.get("original_source_file"),
    )
    expected_name = _normalized_reprocess_project_name(project_name)
    for artifact_path in candidate_paths:
        sidecar_path = os.path.splitext(artifact_path)[0] + ".json"
        try:
            artifact_stat = os.lstat(artifact_path)
            sidecar_stat = os.lstat(sidecar_path)
            if (
                stat.S_ISLNK(artifact_stat.st_mode)
                or not stat.S_ISREG(artifact_stat.st_mode)
                or stat.S_ISLNK(sidecar_stat.st_mode)
                or not stat.S_ISREG(sidecar_stat.st_mode)
            ):
                continue
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        if not _record_reprocess_sidecar_status_is_explicitly_complete(payload):
            continue
        if _canonical_reprocess_source(payload.get("source_id")) != "shenzhen":
            continue
        expected_hash = str(payload.get("archive_content_sha256") or "").strip().lower()
        expected_hash = expected_hash.removeprefix("sha256:")
        expected_bytes = payload.get("archive_content_bytes")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            continue
        try:
            if expected_bytes in (None, ""):
                continue
            if _regular_file_sha256(
                artifact_path,
                field="reprocess Shenzhen alias source artifact",
            ) != expected_hash:
                continue
            if os.path.getsize(artifact_path) != int(expected_bytes):
                continue
        except (OSError, TypeError, ValueError, RuntimeError):
            continue

        relations: set[tuple[str, str, str, str]] = set()
        for node in _iter_reprocess_sidecar_mappings(payload):
            package = node.get("portalTPackage")
            if not isinstance(package, Mapping):
                continue
            relation = (
                str(package.get("gzwCode") or package.get("国资监测编号") or "")
                .strip()
                .upper(),
                str(package.get("projectCode") or package.get("项目编号") or "")
                .strip()
                .upper(),
                str(
                    package.get("packageId")
                    or package.get("package_id")
                    or package.get("contentId")
                    or ""
                )
                .strip()
                .upper(),
                _normalized_reprocess_project_name(
                    package.get("projectName") or package.get("项目名称")
                ),
            )
            if all(relation):
                relations.add(relation)
        expected_relation = (
            canonical_project_code,
            source_project_code,
            project_id,
            expected_name,
        )
        source_relations = {
            relation
            for relation in relations
            if relation[1] == source_project_code
        }
        canonical_relations = {
            relation
            for relation in relations
            if relation[0] == canonical_project_code
        }
        if (
            expected_relation in relations
            and source_relations == {expected_relation}
            and canonical_relations == {expected_relation}
        ):
            return True
    return False


def _record_reprocess_source_project_code_alias_relationship(
    original: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> str:
    original_code = _record_reprocess_project_code(original)
    replacement_code = _record_reprocess_project_code(replacement)
    if (
        not re.fullmatch(r"CQ\d{8,}(?:-\d+)?", original_code)
        or not re.fullmatch(r"G(?:3|6|R)\d{4}SZ\d+(?:-\d+)?", replacement_code)
    ):
        return ""

    parser_payload = _payload_json_loads(replacement.get("parser_payload_json"))
    parser_project_code = _normalize_scope_component(
        parser_payload.get("project_code") or parser_payload.get("项目编号"),
        uppercase=True,
    )
    parser_source_project_code = _normalize_scope_component(
        parser_payload.get("source_project_code"),
        uppercase=True,
    )
    parser_project_id = _normalize_scope_component(
        parser_payload.get("project_id"),
        uppercase=True,
    )
    if (
        parser_project_code != replacement_code
        or parser_source_project_code != original_code
        or not parser_project_id
    ):
        return ""

    original_ids = _record_reprocess_project_ids(original)
    replacement_ids = _record_reprocess_project_ids(replacement)
    if original_ids != {parser_project_id} or replacement_ids != {parser_project_id}:
        return ""

    original_name = _record_reprocess_project_name(original)
    replacement_name = _record_reprocess_project_name(replacement)
    normalized_original_name = _normalized_reprocess_project_name(original_name)
    normalized_replacement_name = _normalized_reprocess_project_name(replacement_name)
    normalized_parser_name = _normalized_reprocess_project_name(
        parser_payload.get("project_name") or parser_payload.get("项目名称")
    )
    if (
        not normalized_original_name
        or normalized_original_name != normalized_replacement_name
        or normalized_original_name != normalized_parser_name
    ):
        return ""

    original_raw_source, original_source, original_exchange = _record_reprocess_source_scope(
        original
    )
    replacement_raw_source, replacement_source, replacement_exchange = (
        _record_reprocess_source_scope(replacement)
    )
    if (
        original_source != "shenzhen"
        or replacement_source != "shenzhen"
        or not original_raw_source
        or original_raw_source != replacement_raw_source
        or not original_exchange
        or original_exchange != replacement_exchange
    ):
        return ""
    try:
        original_family = get_family_descriptor(
            str(original.get("record_family") or "")
        ).family_id
        replacement_family = get_family_descriptor(
            str(replacement.get("record_family") or "")
        ).family_id
        original_business = resolve_business_descriptor(
            original.get("business_id"),
            family_id=original_family,
        )
        replacement_business = resolve_business_descriptor(
            replacement.get("business_id"),
            family_id=replacement_family,
        )
    except KeyError:
        return ""
    if (
        original_family != "listing"
        or replacement_family != "listing"
        or original_business is None
        or replacement_business is None
        or original_business.business_id != replacement_business.business_id
    ):
        return ""
    if not _verified_shenzhen_source_project_code_alias_sidecar(
        replacement,
        canonical_project_code=replacement_code,
        source_project_code=original_code,
        project_id=parser_project_id,
        project_name=replacement_name,
    ):
        return ""
    return f"{original_code}->{replacement_code}"


def _record_reprocess_verified_sidecar_evidence(
    record: Mapping[str, Any],
) -> tuple[bool, set[str]]:
    source_identity = _source_identity_json_loads(record.get("source_identity_json"))
    _raw_source_id, canonical_source_id, _canonical_exchange = _record_reprocess_source_scope(
        record
    )
    project_code = str(record.get("project_code") or "").strip().upper()
    candidate_paths = _unique_text_values(
        record.get("archive_path"),
        record.get("source_file"),
        source_identity.get("original_evidence_path"),
        source_identity.get("original_source_file"),
    )
    verified_hashes: set[str] = set()
    has_verified_integrity = False

    def _identity_values(payload: object, keys: set[str]) -> set[str]:
        values: set[str] = set()

        def _walk(node: object) -> None:
            if isinstance(node, Mapping):
                for raw_key, value in node.items():
                    normalized_key = unicodedata.normalize("NFKC", str(raw_key or "")).casefold()
                    if normalized_key in keys and isinstance(value, (str, int, float)):
                        text = str(value).strip()
                        if text:
                            values.add(text)
                    _walk(value)
            elif isinstance(node, list):
                for child in node:
                    _walk(child)

        _walk(payload)
        return values

    project_code_keys = {
        "project_code",
        "projectcode",
        "project_no",
        "projectno",
        "source_project_code",
        "sourceprojectcode",
        "gzw_code",
        "gzwcode",
        "项目编号",
        "国资监测编号",
    }
    project_name_keys = {
        "project_name",
        "projectname",
        "项目名称",
        "package_name",
        "packagename",
    }

    def _project_name_matches(expected: str, candidates: set[str]) -> bool:
        expected_normalized = _normalized_reprocess_project_name(expected)
        if not expected_normalized:
            return True
        for candidate in candidates:
            normalized = _normalized_reprocess_project_name(candidate)
            if normalized == expected_normalized:
                return True
            # Shenzhen list rows append the monitoring code to the otherwise
            # identical project name. Accept only that explicit suffix.
            suffix = normalized.removeprefix(expected_normalized)
            if suffix and re.fullmatch(r"\(国资监测编号[a-z0-9-]+\)", suffix):
                return True
        return False

    for artifact_path in candidate_paths:
        if not os.path.isfile(artifact_path):
            continue
        sidecar_path = os.path.splitext(artifact_path)[0] + ".json"
        try:
            sidecar_stat = os.lstat(sidecar_path)
            if stat.S_ISLNK(sidecar_stat.st_mode) or not stat.S_ISREG(sidecar_stat.st_mode):
                continue
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping) or not _record_reprocess_sidecar_status_is_trusted(
            payload
        ):
            continue
        metadata = payload.get("metadata")
        identity_payload = metadata if isinstance(metadata, Mapping) else payload
        sidecar_source_id = _canonical_reprocess_source(
            identity_payload.get("source_id") or payload.get("source_id")
        )
        sidecar_project_codes = {
            str(value or "").strip().upper()
            for value in _identity_values(payload, project_code_keys)
            if str(value or "").strip()
        }
        sidecar_project_names = _identity_values(payload, project_name_keys)
        if (
            not canonical_source_id
            or not sidecar_source_id
            or sidecar_source_id != canonical_source_id
            or not project_code
            or project_code not in sidecar_project_codes
            or (sidecar_project_names and not _project_name_matches(
                _record_reprocess_project_name(record),
                sidecar_project_names,
            ))
        ):
            continue
        expected_hash = str(payload.get("archive_content_sha256") or "").strip().lower()
        expected_hash = expected_hash.removeprefix("sha256:")
        expected_bytes = payload.get("archive_content_bytes")
        if not expected_hash and expected_bytes in (None, ""):
            continue
        try:
            if expected_hash:
                if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    continue
                if _regular_file_sha256(artifact_path, field="reprocess source artifact") != expected_hash:
                    continue
            if expected_bytes not in (None, "") and os.path.getsize(artifact_path) != int(
                expected_bytes
            ):
                continue
        except (OSError, TypeError, ValueError, RuntimeError):
            continue
        has_verified_integrity = True
        if expected_hash:
            verified_hashes.add(expected_hash)
    return has_verified_integrity, verified_hashes


def _record_reprocess_identity_relationship(
    original: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> tuple[str, str]:
    alias_relationship = _record_reprocess_source_project_code_alias_relationship(
        original,
        replacement,
    )
    if alias_relationship:
        return "source_project_code_alias", alias_relationship
    if _record_reprocess_project_codes_conflict(original, replacement):
        return "", ""
    shared_tokens = _record_reprocess_identity_tokens(original) & _record_reprocess_identity_tokens(
        replacement
    )
    for strong_kind in ("page_url", "project_id"):
        prefix = f"{strong_kind}:"
        matching_tokens = sorted(token for token in shared_tokens if token.startswith(prefix))
        if matching_tokens:
            return strong_kind, matching_tokens[0][len(prefix) :]
    original_code = str(original.get("project_code") or "").strip().upper()
    replacement_code = str(replacement.get("project_code") or "").strip().upper()
    if original_code and replacement_code and original_code == replacement_code:
        return "project_code", original_code
    project_code_tokens = sorted(
        token for token in shared_tokens if token.startswith("project_code:")
    )
    if project_code_tokens:
        return "project_code_token", project_code_tokens[0].split(":", 1)[1]
    return "", ""


def _record_reprocess_source_evidence_is_valid(
    original: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    relationship_basis: str,
) -> bool:
    if relationship_basis == "source_project_code_alias":
        return bool(
            _record_reprocess_source_project_code_alias_relationship(
                original,
                replacement,
            )
        )
    if _record_reprocess_project_codes_conflict(original, replacement):
        return False
    original_raw_source, original_source, original_exchange = _record_reprocess_source_scope(
        original
    )
    replacement_raw_source, replacement_source, replacement_exchange = (
        _record_reprocess_source_scope(replacement)
    )
    if (
        not original_exchange
        or original_exchange != replacement_exchange
        or not original_source
        or original_source != replacement_source
    ):
        return False
    try:
        original_family = get_family_descriptor(str(original.get("record_family") or ""))
        replacement_family = get_family_descriptor(str(replacement.get("record_family") or ""))
    except KeyError:
        return False
    if (
        original_source not in original_family.source_ids
        or replacement_source not in replacement_family.source_ids
    ):
        return False

    original_integrity, original_hashes = _record_reprocess_verified_sidecar_evidence(original)
    replacement_integrity, replacement_hashes = _record_reprocess_verified_sidecar_evidence(
        replacement
    )
    if not original_integrity or not replacement_integrity:
        return False
    if original_raw_source == replacement_raw_source:
        return True
    return relationship_basis in {"page_url", "project_id"} or bool(
        original_hashes & replacement_hashes
    )


def _record_reprocess_scope_transition_is_valid(
    original: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    relationship_basis: str = "",
) -> bool:
    try:
        original_family = get_family_descriptor(str(original.get("record_family") or "")).family_id
        replacement_family = get_family_descriptor(str(replacement.get("record_family") or "")).family_id
        original_business = resolve_business_descriptor(
            original.get("business_id"),
            family_id=original_family,
        )
        replacement_business = resolve_business_descriptor(
            replacement.get("business_id"),
            family_id=replacement_family,
        )
    except KeyError:
        return False
    if original_business is None or replacement_business is None:
        return False
    if original_family == replacement_family:
        if original_business.business_id == replacement_business.business_id:
            return True
        if original_family != "listing":
            return False
        # A listing may be corrected from one business classifier to another,
        # but only when the identity resolver found a strong relationship.
        # Project-code equality is checked independently by the caller before
        # this transition gate.
        return relationship_basis in {
            "project_code",
            "project_code_token",
            "page_url",
            "project_id",
        }
    if {original_family, replacement_family} != {"listing", "deal"}:
        return False
    return bool(
        original_business.project_type_label
        and original_business.project_type_label == replacement_business.project_type_label
    )


def _has_verified_artifact_evidence(record: Mapping[str, Any] | None = None, **overrides: Any) -> bool:
    return resolve_artifact_evidence_verdict(record, **overrides).status == "verified"


_REVIEW_DEDUP_STATES = frozenset(
    {
        RecordState.PENDING_REVIEW.value,
        RecordState.PENDING_MAPPING.value,
        RecordState.MAPPING_CONFLICT.value,
        RecordState.CONFLICT.value,
        RecordState.SKIPPED.value,
    }
)


def _can_use_record_for_existing_download_dedup(record: Mapping[str, Any]) -> bool:
    payload = _mapping_payload(record, field="record")
    if _has_verified_artifact_evidence(payload):
        return True
    state = str(payload.get("state") or "").strip()
    if state not in _REVIEW_DEDUP_STATES:
        return False
    return resolve_artifact_evidence_verdict(payload).status == "present_unverified"


def _record_identity_value(*payloads: Any, key: str) -> Any:
    for payload in payloads:
        if isinstance(payload, dict) and key in payload and str(payload.get(key) or "").strip():
            return payload.get(key)
    return ""


def _normalize_date_text(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    match = re.match(r"^(?P<year>\d{4})[-/.\u5e74](?P<month>\d{1,2})[-/.\u6708](?P<day>\d{1,2})", text)
    if match:
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    return (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )


def _build_failed_source_identity(
    *,
    project_code: str,
    source_file: str,
    state: str,
    payload: Dict[str, Any] | None,
) -> Dict[str, Any]:
    data = _mapping_payload(payload, field="payload", allow_none=True)
    nested_source_identity = data.get("source_identity")
    if not isinstance(nested_source_identity, dict):
        nested_source_identity = {}
    parser_payload = data.get("parser_payload")
    if not isinstance(parser_payload, dict):
        parser_payload = {}
    postprocess_payload = data.get("postprocess_payload")
    if not isinstance(postprocess_payload, dict):
        postprocess_payload = {}

    original_evidence_path = _first_non_empty(
        data.get("original_evidence_path"),
        nested_source_identity.get("original_evidence_path"),
        nested_source_identity.get("original_source_file"),
        data.get("source_file"),
        parser_payload.get("source_file"),
        source_file,
    )
    source_url = _first_non_empty(
        data.get("source_url"),
        data.get("page_url"),
        nested_source_identity.get("source_url"),
        parser_payload.get("page_url"),
        postprocess_payload.get("page_url"),
    )
    resolved_project_code = _first_non_empty(
        data.get("project_code"),
        nested_source_identity.get("project_code"),
        parser_payload.get("project_code"),
        parser_payload.get("项目编号"),
        postprocess_payload.get("project_code"),
        postprocess_payload.get("项目编号"),
        project_code,
    )
    resolved_project_name = _first_non_empty(
        data.get("project_name"),
        nested_source_identity.get("project_name"),
        parser_payload.get("project_name"),
        parser_payload.get("项目名称"),
        postprocess_payload.get("project_name"),
        postprocess_payload.get("项目名称"),
    )
    resolved_exchange = _first_non_empty(
        data.get("exchange"),
        nested_source_identity.get("exchange"),
        parser_payload.get("exchange"),
        parser_payload.get("交易所"),
        postprocess_payload.get("exchange"),
        postprocess_payload.get("交易所"),
    )
    listing_date = _first_non_empty(
        data.get("listing_date"),
        nested_source_identity.get("listing_date"),
        parser_payload.get("listing_date"),
        parser_payload.get("挂牌开始日期"),
        parser_payload.get("预披露开始日期"),
        postprocess_payload.get("listing_date"),
        postprocess_payload.get("挂牌开始日期"),
        postprocess_payload.get("预披露开始日期"),
    )
    candidate_tokens = _unique_text_sequence_payloads(
        (nested_source_identity.get("candidate_tokens"), "payload.source_identity.candidate_tokens"),
        (data.get("candidate_tokens"), "payload.candidate_tokens"),
    )
    for kind, value in (
        ("project_code", resolved_project_code),
        (
            "project_id",
            _first_non_empty(
                data.get("project_id"),
                nested_source_identity.get("project_id"),
                parser_payload.get("project_id"),
                postprocess_payload.get("project_id"),
            ),
        ),
        ("page_url", source_url),
    ):
        token = _candidate_identity_token(kind, value)
        if token and token not in candidate_tokens:
            candidate_tokens.append(token)

    record_family = _first_non_empty(data.get("record_family"), nested_source_identity.get("record_family"), "listing")
    business_id_hint = _first_non_empty(
        data.get("business_id_hint"),
        nested_source_identity.get("business_id_hint"),
    )
    source_identity = build_source_identity_payload(
        record_family=record_family,
        source_file=original_evidence_path,
        source_url=source_url,
        project_code=resolved_project_code,
        project_name=resolved_project_name,
        exchange=resolved_exchange,
        listing_date=listing_date,
        candidate_tokens=candidate_tokens,
        business_id_hint=business_id_hint,
        business_label_hint=_first_non_empty(
            data.get("business_label_hint"),
            nested_source_identity.get("business_label_hint"),
        ),
        project_type_fallback=_first_non_empty(
            data.get("project_type_fallback"),
            nested_source_identity.get("project_type_fallback"),
        ),
    )
    try:
        descriptor = resolve_business_descriptor(business_id_hint, family_id=record_family)
    except KeyError:
        descriptor = None
    if descriptor is not None:
        source_identity["business_id"] = descriptor.business_id
    resolved_source_id = _first_non_empty(data.get("source_id"), nested_source_identity.get("source_id"))
    if resolved_source_id:
        source_identity["source_id"] = resolved_source_id
    source_identity["original_evidence_path"] = original_evidence_path
    source_identity["original_source_file"] = _first_non_empty(
        nested_source_identity.get("original_source_file"),
        original_evidence_path,
    )
    source_identity["record_state"] = str(state or "").strip()
    return source_identity


def _merge_source_identity(existing: Dict[str, Any] | None, incoming: Dict[str, Any] | None) -> Dict[str, Any]:
    existing_data = _mapping_payload(existing, field="existing", allow_none=True)
    incoming_data = _mapping_payload(incoming, field="incoming", allow_none=True)
    merged = dict(existing_data)
    for key in (
        "record_family",
        "original_evidence_path",
        "original_source_file",
        "source_url",
        "project_code",
        "project_name",
        "exchange",
        "listing_date",
        "business_id",
        "business_id_hint",
        "business_label_hint",
        "project_type_fallback",
        "record_state",
    ):
        merged[key] = _first_non_empty(merged.get(key), incoming_data.get(key))
    merged["candidate_tokens"] = _unique_text_sequence_payloads(
        (existing_data.get("candidate_tokens"), "existing.candidate_tokens"),
        (incoming_data.get("candidate_tokens"), "incoming.candidate_tokens"),
    )
    return merged


_BUSINESS_RECLASSIFICATION_SNAPSHOT_FIELDS = (
    "record_id",
    "revision_id",
    "revision_hash",
    "state",
    "record_family",
    "business_id",
    "business_key",
    "project_code",
    "exchange",
    "source_id",
    "source_file",
    "archive_path",
    "identity_anchor",
    "source_identity_sha256",
    "parser_payload_sha256",
    "postprocess_payload_sha256",
    "canonical_record_sha256",
)


def _json_payload_sha256(payload: Any) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _business_reclassification_row(
    conn: sqlite3.Connection,
    record_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            records.record_id,
            records.business_key,
            records.record_family,
            records.business_id,
            records.identity_anchor,
            records.source_identity_json,
            records.project_code,
            records.project_name,
            records.project_type,
            records.exchange,
            records.state,
            records.source_file,
            records.archive_path,
            records.latest_revision_id,
            records.last_error_type,
            records.last_error_message,
            revisions.revision_id,
            revisions.revision_hash,
            revisions.parser_payload_json,
            revisions.postprocess_payload_json,
            revisions.canonical_record_json
        FROM records
        JOIN record_revisions AS revisions
          ON revisions.revision_id = records.latest_revision_id
        WHERE records.record_id = ?
        """,
        (record_id,),
    ).fetchone()


def _business_reclassification_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    source_identity = _source_identity_json_loads(payload.get("source_identity_json"))
    parser_payload = _payload_json_loads(payload.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(payload.get("postprocess_payload_json"))
    canonical_record = _json_object_loads(payload.get("canonical_record_json"))
    _raw_source, canonical_source, canonical_exchange = _record_reprocess_source_scope(payload)
    return {
        "record_id": str(payload.get("record_id") or ""),
        "revision_id": int(payload.get("revision_id") or 0),
        "revision_hash": str(payload.get("revision_hash") or ""),
        "state": str(payload.get("state") or ""),
        "record_family": str(payload.get("record_family") or ""),
        "business_id": str(payload.get("business_id") or ""),
        "business_key": str(payload.get("business_key") or ""),
        "project_code": _record_reprocess_project_code(payload),
        "exchange": str(payload.get("exchange") or "").strip(),
        "source_id": canonical_source or canonical_exchange,
        "source_file": str(payload.get("source_file") or "").strip(),
        "archive_path": str(payload.get("archive_path") or "").strip(),
        "identity_anchor": str(payload.get("identity_anchor") or ""),
        "source_identity_sha256": _json_payload_sha256(source_identity),
        "parser_payload_sha256": _json_payload_sha256(parser_payload),
        "postprocess_payload_sha256": _json_payload_sha256(postprocess_payload),
        "canonical_record_sha256": _json_payload_sha256(canonical_record),
    }


def _validate_business_reclassification_snapshot(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    expected_payload = _mapping_payload(expected, field=f"{role}_snapshot")
    missing = [
        field
        for field in _BUSINESS_RECLASSIFICATION_SNAPSHOT_FIELDS
        if field not in expected_payload
    ]
    if missing:
        raise ValueError(f"{role}_snapshot is missing fields: {', '.join(missing)}")
    actual = _business_reclassification_snapshot(row)
    mismatched = [
        field
        for field in _BUSINESS_RECLASSIFICATION_SNAPSHOT_FIELDS
        if actual[field] != expected_payload[field]
    ]
    if mismatched:
        raise RuntimeError(
            f"business reclassification {role} drift: {', '.join(mismatched)}"
        )
    return actual


def _business_reclassification_record_urls(row: Mapping[str, Any]) -> set[str]:
    payload = dict(row)
    source_identity = _source_identity_json_loads(payload.get("source_identity_json"))
    parser_payload = _payload_json_loads(payload.get("parser_payload_json"))
    postprocess_payload = _payload_json_loads(payload.get("postprocess_payload_json"))
    urls: set[str] = set()
    for container in (source_identity, parser_payload, postprocess_payload):
        for field in ("source_url", "page_url", "detail_url"):
            value = str(container.get(field) or "").strip()
            if value:
                urls.add(value)
    return urls


def _business_reclassification_record_paths(row: Mapping[str, Any]) -> set[str]:
    payload = dict(row)
    source_identity = _source_identity_json_loads(payload.get("source_identity_json"))
    paths = _unique_text_values(
        payload.get("archive_path"),
        payload.get("source_file"),
        source_identity.get("original_evidence_path"),
        source_identity.get("original_source_file"),
    )
    return {os.path.normcase(os.path.abspath(path)) for path in paths}


def _business_reclassification_proof_fingerprint(proof: Mapping[str, Any]) -> str:
    public_proof = {
        str(key): value
        for key, value in proof.items()
        if str(key) not in {"parser_payload", "evidence_fingerprint"}
    }
    return _json_payload_sha256(public_proof)


def _validate_business_reclassification_proof(
    original: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    proof_payload = _mapping_payload(proof, field="proof")
    required_text_fields = (
        "evidence_kind",
        "parser_payload_sha256",
        "record_family",
        "original_business_id",
        "proposed_business_id",
        "project_code",
        "source_id",
        "exchange",
        "evidence_fingerprint",
    )
    resolved = {
        field: _required_text(proof_payload.get(field), field=f"proof.{field}")
        for field in required_text_fields
    }
    if resolved["evidence_kind"] not in {"fresh_source_parse", "locked_target_revision"}:
        raise ValueError("proof.evidence_kind is unsupported")
    if not re.fullmatch(r"[0-9a-f]{64}", resolved["parser_payload_sha256"].lower()):
        raise ValueError("proof.parser_payload_sha256 must be a SHA-256 string")
    if not re.fullmatch(r"[0-9a-f]{64}", resolved["evidence_fingerprint"].lower()):
        raise ValueError("proof.evidence_fingerprint must be a SHA-256 string")
    parser_payload = _mapping_payload(
        proof_payload.get("parser_payload"),
        field="proof.parser_payload",
    )
    if _json_payload_sha256(parser_payload) != resolved["parser_payload_sha256"]:
        raise ValueError("proof parser payload fingerprint mismatch")
    if _business_reclassification_proof_fingerprint(proof_payload) != resolved["evidence_fingerprint"]:
        raise ValueError("proof evidence fingerprint mismatch")

    if resolved["record_family"] != _BUSINESS_RECLASSIFICATION_RECORD_FAMILY:
        raise ValueError(
            "business reclassification proof is limited to listing records"
        )
    if resolved["original_business_id"] != _BUSINESS_RECLASSIFICATION_SOURCE_BUSINESS_ID:
        raise ValueError(
            "business reclassification proof source must be capital_increase"
        )
    if resolved["proposed_business_id"] != _BUSINESS_RECLASSIFICATION_TARGET_BUSINESS_ID:
        raise ValueError(
            "business reclassification proof target must be equity_transfer"
        )

    original_snapshot = _business_reclassification_snapshot(original)
    if original_snapshot["record_family"] != resolved["record_family"]:
        raise ValueError("proof record_family does not match original")
    if original_snapshot["business_id"] != resolved["original_business_id"]:
        raise ValueError("proof original_business_id does not match original")
    if original_snapshot["project_code"] != resolved["project_code"].upper():
        raise ValueError("proof project_code does not match original")
    if original_snapshot["source_id"] != _canonical_reprocess_source(resolved["source_id"]):
        raise ValueError("proof source_id does not match original")
    if original_snapshot["exchange"] != resolved["exchange"]:
        raise ValueError("proof exchange does not match original")
    source_url = str(proof_payload.get("source_url") or "").strip()
    if source_url and source_url not in _business_reclassification_record_urls(original):
        raise ValueError("proof source_url is not declared by original")

    if resolved["evidence_kind"] == "fresh_source_parse":
        source_path = _required_text(
            proof_payload.get("source_path"),
            field="proof.source_path",
        )
        source_sha256 = _required_text(
            proof_payload.get("source_sha256"),
            field="proof.source_sha256",
        ).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ValueError("proof.source_sha256 must be a SHA-256 string")
        normalized_source_path = os.path.normcase(os.path.abspath(source_path))
        if normalized_source_path not in _business_reclassification_record_paths(original):
            raise ValueError("proof source_path is not declared by original")
        actual_source_sha256 = _regular_file_sha256(
            source_path,
            field="business reclassification source",
        )
        if actual_source_sha256 != source_sha256:
            raise RuntimeError("business reclassification source SHA-256 drift")
    else:
        _required_text(
            proof_payload.get("target_record_id"),
            field="proof.target_record_id",
        )
        target_revision_id = proof_payload.get("target_revision_id")
        if isinstance(target_revision_id, bool) or not isinstance(target_revision_id, int):
            raise ValueError("proof.target_revision_id must be an integer")
        _required_text(
            proof_payload.get("target_revision_hash"),
            field="proof.target_revision_hash",
        )

    fresh_project_code = str(
        parser_payload.get("项目编号")
        or parser_payload.get("project_code")
        or ""
    ).strip().upper()
    if fresh_project_code != resolved["project_code"].upper():
        raise ValueError("fresh parser project_code does not match proof")
    fresh_source = _canonical_reprocess_source(
        parser_payload.get("source_id")
        or parser_payload.get("交易所")
        or parser_payload.get("exchange")
    )
    if not fresh_source or fresh_source != _canonical_reprocess_source(resolved["source_id"]):
        raise ValueError("fresh parser source_id does not match proof")
    classification = classify_record_business(
        parser_payload=parser_payload,
        record_family_hint=resolved["record_family"],
        page_url=source_url,
        source_url=source_url,
    )
    if classification.record_family != resolved["record_family"]:
        raise ValueError("fresh parser record_family does not match proof")
    if classification.business_id != resolved["proposed_business_id"]:
        raise ValueError("fresh parser business classification does not match proof")
    if resolved["original_business_id"] == resolved["proposed_business_id"]:
        raise ValueError("business reclassification does not change business_id")
    return proof_payload, parser_payload


class StreamingStore:
    """Thin sqlite-backed store with JSON payload columns."""

    def __init__(self, db_path: str, *, auto_migrate: bool = False) -> None:
        self.db_path = _normalize_db_path(db_path)
        _ensure_db_parent_dir(self.db_path)
        if auto_migrate:
            self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(**_connection_kwargs(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one write connection and commit or roll back the whole unit of work."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def migrate(self) -> int:
        with self._connect() as conn:
            self._ensure_schema(conn)
            version_row = conn.execute("PRAGMA user_version").fetchone()
        return int(version_row[0] if version_row else 0)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        _apply_schema_migrations(conn)

    def create_job(
        self,
        job_type: str,
        *,
        metadata: Dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> str:
        resolved_job_id = uuid.uuid4().hex if job_id is None else _required_text(job_id, field="job_id")
        resolved_job_type = _required_text(job_type, field="job_type")
        if metadata is None:
            metadata_payload: Dict[str, Any] = {}
        elif isinstance(metadata, dict):
            metadata_payload = dict(metadata)
        else:
            raise ValueError("metadata must be an object")
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, job_type, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (resolved_job_id, resolved_job_type, "starting", _json_dumps(metadata_payload), now, now),
            )
        return resolved_job_id

    def start_job(self, job_id: str) -> None:
        """Transition a job from STARTING to RUNNING and emit a startup stage event.

        Called by the worker thread when it actually begins pipeline execution.
        """
        now = _utcnow()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ? AND status = ?",
                ("running", now, str(job_id), "starting"),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (str(job_id),),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"start_job: no job found with job_id={job_id!r}")
                raise RuntimeError(
                    f"start_job: job {job_id!r} cannot transition from status={row['status']!r}"
                )

            # Emit a startup stage event to record that pipeline execution has begun.
            # This marks the bootstrap/thread-init phase as complete.
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, project_code, archive_path,
                    error_type, error_message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job_id),
                    now,
                    "startup",
                    "running",
                    "",
                    "",
                    "",
                    "",
                    "{}",
                ),
            )

    def fail_job(
        self,
        job_id: str,
        *,
        failure,  # PipelineFailure
        event_payload: Dict[str, Any] | None = None,
        exception_inc: int = 1,
    ) -> None:
        """Atomically fail a job: updates status + appends failure event.

        This is the single authoritative method for recording startup failures.
        It replaces any ad-hoc finish_job(failed) patching in service code.
        """
        now = _utcnow()
        if hasattr(failure, "to_dict"):
            failure_dict = _mapping_payload(failure.to_dict(), field="failure")
            failure_dict["context"] = _mapping_payload(
                failure_dict.get("context"),
                field="failure.context",
                allow_none=True,
            )
        else:
            failure_context = _mapping_payload(
                getattr(failure, "context", None),
                field="failure.context",
                allow_none=True,
            )
            failure_dict = {
                "code": getattr(failure, "code", "unknown"),
                "component": getattr(failure, "component", "unknown"),
                "stage": getattr(failure, "stage", "unknown"),
                "recoverability": getattr(failure, "recoverability", "permanent"),
                "message": str(failure),
                "context": failure_context,
            }
        event_payload_object = _mapping_payload(event_payload, field="event_payload", allow_none=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, summary_json FROM jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"fail_job: no job found with job_id={job_id!r}")
            current_status = str(row["status"] or "").strip()
            if current_status in _TERMINAL_JOB_STATUSES:
                return
            if current_status not in _ACTIVE_JOB_STATUSES:
                raise RuntimeError(
                    f"fail_job: job {job_id!r} cannot transition from status={current_status!r}"
                )

            summary = _job_summary_json_loads(row["summary_json"])
            summary.update(
                {
                    "failure_code": str(failure_dict.get("code") or "unknown"),
                    "failure_stage": str(failure_dict.get("stage") or "unknown"),
                    "failure_message": str(failure_dict.get("message") or ""),
                    "message": str(failure_dict.get("message") or ""),
                    "failure_context": dict(failure_dict.get("context") or {}),
                }
            )

            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, summary_json = ?,
                    exception_count = exception_count + ?, updated_at = ?
                WHERE job_id = ? AND status IN ('starting', 'running')
                """,
                ("failed", _json_dumps(summary), int(exception_inc), now, str(job_id)),
            )
            if cursor.rowcount == 0:
                return

            # Append failure event with stage=startup
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, project_code, archive_path,
                    error_type, error_message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job_id),
                    now,
                    "startup",
                    "failed",
                    "",
                    "",
                    failure_dict.get("code", "unknown"),
                    failure_dict.get("message", ""),
                    _json_dumps(event_payload_object),
                ),
            )

    def update_job_counts(
        self,
        job_id: str,
        *,
        downloaded_inc: int = 0,
        persisted_inc: int = 0,
        exception_inc: int = 0,
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET downloaded_count = downloaded_count + ?,
                    persisted_count = persisted_count + ?,
                    exception_count = exception_count + ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    int(downloaded_inc),
                    int(persisted_inc),
                    int(exception_inc),
                    now,
                    job_id,
                ),
            )
            if cursor.rowcount == 0:
                raise RuntimeError(f"update_job_counts: no job found with job_id={job_id!r}")

    def finish_job(self, job_id: str, *, status: str, summary: Dict[str, Any] | None = None) -> None:
        now = _utcnow()
        resolved_status = _required_text(status, field="status")
        if resolved_status not in _TERMINAL_JOB_STATUSES:
            raise ValueError(f"status is not terminal: {resolved_status}")
        summary_payload = {} if summary is None else _object_payload(summary, field="summary")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, summary_json = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('starting', 'running')
                """,
                (resolved_status, _json_dumps(summary_payload), now, job_id),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (str(job_id),),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"finish_job: no job found with job_id={job_id!r}")
                current_status = str(row["status"] or "").strip()
                if current_status in _TERMINAL_JOB_STATUSES:
                    return
                raise RuntimeError(
                    f"finish_job: job {job_id!r} cannot transition from status={current_status!r}"
                )

    def record_operation_result(
        self,
        record_id: str,
        *,
        kind: str,
        code: str,
        message: str = "",
        artifact_status: str | None = None,
    ) -> None:
        now = _utcnow()
        resolved_kind = _required_text(kind, field="kind")
        resolved_code = _required_text(code, field="code")
        if not isinstance(message, str):
            raise ValueError("message must be text")
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT artifact_status
                FROM records
                WHERE record_id = ?
                """,
                (str(record_id),),
            ).fetchone()
            if current is None:
                raise RuntimeError(f"record_operation_result: no record found with record_id={record_id!r}")
            resolved_artifact_status = (
                _required_text(artifact_status, field="artifact_status")
                if artifact_status is not None
                else str(current["artifact_status"] or "unknown")
            )
            conn.execute(
                """
                UPDATE records
                SET artifact_status = ?,
                    last_operation_kind = ?,
                    last_operation_code = ?,
                    last_operation_message = ?,
                    last_operation_at = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (
                    resolved_artifact_status,
                    resolved_kind,
                    resolved_code,
                    message,
                    now,
                    now,
                    str(record_id),
                ),
            )

    def record_reprocess_result(
        self,
        record_id: str,
        *,
        result: Dict[str, Any],
        _connection: sqlite3.Connection | None = None,
    ) -> Dict[str, Any]:
        """Persist a reprocess result and retire an explicitly replaced identity atomically."""
        resolved_record_id = _required_text(record_id, field="record_id")
        result_payload = _object_payload(result, field="result")
        result_record_id = str(result_payload.get("record_id") or "").strip()
        now = _utcnow()
        connection_context = self._connect() if _connection is None else nullcontext(_connection)
        with connection_context as conn:
            original = conn.execute(
                """
                SELECT records.record_id, records.record_family, records.business_id,
                       records.project_code, records.project_name, records.exchange, records.state,
                       records.source_file, records.archive_path, records.latest_revision_id,
                       records.source_identity_json, revisions.parser_payload_json,
                       revisions.postprocess_payload_json
                FROM records
                LEFT JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_id = ?
                """,
                (resolved_record_id,),
            ).fetchone()
            if original is None:
                raise RuntimeError(
                    "record_reprocess_result: no record found with "
                    f"record_id={resolved_record_id!r}"
                )

            original_payload = dict(original)
            superseding = None
            relationship_basis = ""
            relationship_value = ""
            if result_record_id and result_record_id != resolved_record_id:
                replacement = conn.execute(
                    """
                    SELECT records.record_id, records.record_family, records.business_id,
                           records.project_code, records.project_name, records.exchange, records.state,
                           records.source_file, records.archive_path, records.latest_revision_id,
                           records.source_identity_json, revisions.parser_payload_json,
                           revisions.postprocess_payload_json
                    FROM records
                    LEFT JOIN record_revisions AS revisions
                      ON revisions.revision_id = records.latest_revision_id
                    WHERE records.record_id = ?
                    """,
                    (result_record_id,),
                ).fetchone()
                if replacement is None:
                    raise RuntimeError(
                        "record_reprocess_result: result record is not persisted: "
                        f"{result_record_id!r}"
                    )
                replacement_state = str(replacement["state"] or "").strip()
                replacement_payload = dict(replacement)
                alias_relationship = _record_reprocess_source_project_code_alias_relationship(
                    original_payload,
                    replacement_payload,
                )
                if alias_relationship and (
                    str(original["state"] or "").strip()
                    not in _SOURCE_PROJECT_CODE_ALIAS_ORIGINAL_STATES
                    or replacement_state != RecordState.READY.value
                ):
                    raise ValueError(
                        "record_reprocess_result: invalid source_project_code_alias state transition: "
                        f"{original['state']} -> {replacement_state}"
                    )
                if replacement_state in _REPROCESS_SUPERSEDING_RECORD_STATES:
                    if _record_reprocess_project_codes_conflict(original_payload, replacement_payload):
                        raise ValueError(
                            "record_reprocess_result: project_code mismatch between original and replacement"
                        )
                    relationship_basis, relationship_value = _record_reprocess_identity_relationship(
                        original_payload,
                        replacement_payload,
                    )
                    if not relationship_basis:
                        raise ValueError(
                            "record_reprocess_result: result record has no identity relationship to "
                            f"original record: original={resolved_record_id!r}, result={result_record_id!r}"
                        )
                    if not _record_reprocess_scope_transition_is_valid(
                        original_payload,
                        replacement_payload,
                        relationship_basis=relationship_basis,
                    ):
                        raise ValueError(
                            "record_reprocess_result: invalid family/business transition: "
                            f"{original['record_family']}:{original['business_id']} -> "
                            f"{replacement['record_family']}:{replacement['business_id']}"
                        )
                    if not _record_reprocess_source_evidence_is_valid(
                        original_payload,
                        replacement_payload,
                        relationship_basis=relationship_basis,
                    ):
                        raise ValueError(
                            "record_reprocess_result: result record source scope or sidecar evidence "
                            "does not match original record"
                        )
                    superseding = replacement

            superseded = superseding is not None
            resolved_pending = 0
            if superseded:
                latest_revision_id = original["latest_revision_id"]
                if latest_revision_id is None:
                    raise RuntimeError(
                        "record_reprocess_result: original record has no latest revision: "
                        f"{resolved_record_id!r}"
                    )
                canonical_row = conn.execute(
                    """
                    SELECT canonical_record_json
                    FROM record_revisions
                    WHERE revision_id = ? AND record_id = ?
                    """,
                    (int(latest_revision_id), resolved_record_id),
                ).fetchone()
                if canonical_row is None:
                    raise RuntimeError(
                        "record_reprocess_result: original latest revision is missing: "
                        f"record_id={resolved_record_id!r}, revision_id={int(latest_revision_id)!r}"
                    )

                superseding_payload = dict(superseding)
                finding = _superseded_record_finding(
                    superseded_by_record=superseding_payload,
                )
                findings_json = _json_dumps([finding])
                canonical_record = _json_object_loads(canonical_row["canonical_record_json"])
                canonical_record_json = _sync_canonical_record_dict_diagnostics(
                    canonical_record,
                    [finding],
                )
                message = f"Record superseded by reprocess result: {result_record_id}"
                cursor = conn.execute(
                    """
                    UPDATE records
                    SET state = 'skipped',
                        last_error_type = 'superseded_by_record',
                        last_error_message = ?,
                        artifact_status = 'ok',
                        last_operation_kind = 'reprocess',
                        last_operation_code = 'ok',
                        last_operation_message = '',
                        last_operation_at = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (message, now, now, resolved_record_id),
                )
                if cursor.rowcount == 0:
                    raise RuntimeError(
                        "record_reprocess_result: original record disappeared: "
                        f"{resolved_record_id!r}"
                    )
                revision_cursor = conn.execute(
                    """
                    UPDATE record_revisions
                    SET state = 'skipped',
                        findings_json = ?,
                        canonical_record_json = ?
                    WHERE revision_id = ? AND record_id = ?
                    """,
                    (
                        findings_json,
                        canonical_record_json,
                        int(latest_revision_id),
                        resolved_record_id,
                    ),
                )
                if revision_cursor.rowcount == 0:
                    raise RuntimeError(
                        "record_reprocess_result: original latest revision disappeared: "
                        f"record_id={resolved_record_id!r}, revision_id={int(latest_revision_id)!r}"
                    )
                pending_cursor = conn.execute(
                    """
                    UPDATE mapping_pending
                    SET resolved_at = ?
                    WHERE record_id = ? AND resolved_at = ''
                    """,
                    (now, resolved_record_id),
                )
                resolved_pending = int(pending_cursor.rowcount or 0)
                self._add_audit_entry_conn(
                    conn,
                    "record_superseded_by_reprocess",
                    {
                        "record_id": resolved_record_id,
                        "previous_state": str(original["state"] or ""),
                        "previous_record_family": str(original["record_family"] or ""),
                        "previous_business_id": str(original["business_id"] or ""),
                        "project_code": str(original["project_code"] or ""),
                        "superseded_by_record_id": result_record_id,
                        "superseded_by_state": str(superseding["state"] or ""),
                        "superseded_by_record_family": str(superseding["record_family"] or ""),
                        "superseded_by_business_id": str(superseding["business_id"] or ""),
                        "superseded_by_project_code": str(superseding["project_code"] or ""),
                        "identity_relationship_basis": relationship_basis,
                        "identity_relationship_value": relationship_value,
                        "resolved_mapping_pending": resolved_pending,
                    },
                    event_ts=now,
                )
            else:
                conn.execute(
                    """
                    UPDATE records
                    SET artifact_status = 'ok',
                        last_operation_kind = 'reprocess',
                        last_operation_code = 'ok',
                        last_operation_message = '',
                        last_operation_at = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (now, now, resolved_record_id),
                )

            self._add_audit_entry_conn(
                conn,
                "record_reprocessed",
                {
                    "record_id": resolved_record_id,
                    "result": result_payload,
                    "superseded": superseded,
                    "superseded_by_record_id": result_record_id if superseded else "",
                },
                event_ts=now,
            )
        return {
            "record_id": resolved_record_id,
            "superseded": superseded,
            "superseded_by_record_id": result_record_id if superseded else "",
            "resolved_mapping_pending": resolved_pending,
        }

    def consolidate_business_reclassification(
        self,
        *,
        original_snapshot: Mapping[str, Any],
        proof: Mapping[str, Any],
        target_snapshot: Mapping[str, Any] | None = None,
        target_record: IngestedRecord | None = None,
    ) -> Dict[str, Any]:
        """Atomically retire a misclassified record in favor of a classified target.

        ``target_record`` is an in-memory result. When supplied, its insert or
        update shares the same SQLite transaction as retirement of the
        original. ``target_snapshot`` may additionally lock an existing
        non-ready shell that the upsert is expected to replace.
        """

        expected_original = _mapping_payload(
            original_snapshot,
            field="original_snapshot",
        )
        expected_target = (
            None
            if target_snapshot is None
            else _mapping_payload(target_snapshot, field="target_snapshot")
        )
        if target_record is None and expected_target is None:
            raise ValueError("target_snapshot is required when target_record is absent")

        original_record_id = _required_text(
            expected_original.get("record_id"),
            field="original_snapshot.record_id",
        )
        intended_target_record_id = (
            _required_text(target_record.record_id, field="target_record.record_id")
            if target_record is not None
            else _required_text(
                expected_target.get("record_id") if expected_target is not None else "",
                field="target_snapshot.record_id",
            )
        )
        if original_record_id == intended_target_record_id:
            raise ValueError("original and target record_id must differ")

        now = _utcnow()
        with self._connect() as conn:
            original = _business_reclassification_row(conn, original_record_id)
            if original is None:
                raise RuntimeError(
                    "business reclassification original record is missing: "
                    f"{original_record_id!r}"
                )
            actual_original = _validate_business_reclassification_snapshot(
                original,
                expected_original,
                role="original",
            )
            if actual_original["state"] not in _BUSINESS_RECLASSIFICATION_ORIGINAL_STATES:
                raise ValueError(
                    "business reclassification original state is unsupported: "
                    f"{actual_original['state']}"
                )
            proof_payload, _fresh_parser_payload = _validate_business_reclassification_proof(
                original,
                proof,
            )

            proposed_business_id = str(proof_payload["proposed_business_id"])
            proof_record_family = str(proof_payload["record_family"])
            proof_project_code = str(proof_payload["project_code"]).upper()
            proof_source_id = _canonical_reprocess_source(proof_payload["source_id"])
            proof_exchange = str(proof_payload["exchange"])
            evidence_kind = str(proof_payload["evidence_kind"])
            source_path = str(proof_payload.get("source_path") or "")
            source_sha256 = str(proof_payload.get("source_sha256") or "").lower()

            target_before: dict[str, Any] | None = None
            target_changed = False
            target_revision_id = 0
            if target_record is not None:
                if evidence_kind != "fresh_source_parse":
                    raise ValueError(
                        "target creation requires fresh_source_parse evidence"
                    )
                target_state = str(getattr(target_record.state, "value", target_record.state) or "")
                if target_state not in _BUSINESS_RECLASSIFICATION_TARGET_STATES:
                    raise ValueError(
                        "business reclassification target record state is unsupported: "
                        f"{target_state}"
                    )
                target_family = _canonical_record_family(target_record.record_family)
                target_business_id, _target_label = _resolve_business_kernel_fields(
                    record_family=target_record.record_family,
                    project_type=target_record.project_type,
                    canonical_record=target_record.canonical_record,
                )
                target_source_id = _canonical_reprocess_source(
                    _resolve_scope_source_id(
                        source_identity=target_record.source_identity,
                        exchange=target_record.exchange,
                    )
                )
                if target_family != proof_record_family:
                    raise ValueError("target record_family does not match proof")
                if target_business_id != proposed_business_id:
                    raise ValueError("target business_id does not match proof")
                if str(target_record.project_code or "").strip().upper() != proof_project_code:
                    raise ValueError("target project_code does not match proof")
                if str(target_record.exchange or "").strip() != proof_exchange:
                    raise ValueError("target exchange does not match proof")
                if target_source_id != proof_source_id:
                    raise ValueError("target source_id does not match proof")
                if {
                    os.path.normcase(os.path.abspath(str(target_record.source_file or ""))),
                    os.path.normcase(os.path.abspath(str(target_record.archive_path or ""))),
                } != {os.path.normcase(os.path.abspath(source_path))}:
                    raise ValueError("target source paths do not match proof")

                if expected_target is not None:
                    target_shell = _business_reclassification_row(
                        conn,
                        intended_target_record_id,
                    )
                    if target_shell is None:
                        raise RuntimeError(
                            "business reclassification expected target shell is missing: "
                            f"{intended_target_record_id!r}"
                        )
                    target_before = _validate_business_reclassification_snapshot(
                        target_shell,
                        expected_target,
                        role="target",
                    )
                    if target_before["state"] in _BUSINESS_RECLASSIFICATION_TARGET_STATES:
                        raise ValueError(
                            "classified target must be consolidated without target_record upsert"
                        )
                elif _business_reclassification_row(conn, intended_target_record_id) is not None:
                    raise RuntimeError(
                        "business reclassification unexpected target record already exists: "
                        f"{intended_target_record_id!r}"
                    )

                stored = self.upsert_record(
                    target_record,
                    _connection=conn,
                )
                stored_target_record_id = str(stored["record_id"])
                if stored_target_record_id != intended_target_record_id:
                    raise RuntimeError(
                        "business reclassification target identity collision: "
                        f"expected={intended_target_record_id!r}, "
                        f"stored={stored_target_record_id!r}"
                    )
                target_revision_id = int(stored["revision_id"])
                target_changed = bool(stored["changed"])
                self._sync_mapping_pending_for_record(
                    conn,
                    record_id=stored_target_record_id,
                    revision_id=target_revision_id,
                    project_code=target_record.project_code,
                    payload=_mapping_payload(
                        target_record.postprocess_payload,
                        field="target_record.postprocess_payload",
                    ),
                    state=target_state,
                )
                target = _business_reclassification_row(conn, stored_target_record_id)
                if target is None:
                    raise RuntimeError("business reclassification target upsert disappeared")
            else:
                target = _business_reclassification_row(conn, intended_target_record_id)
                if target is None:
                    raise RuntimeError(
                        "business reclassification target record is missing: "
                        f"{intended_target_record_id!r}"
                    )
                target_before = _validate_business_reclassification_snapshot(
                    target,
                    expected_target or {},
                    role="target",
                )
                target_revision_id = int(target_before["revision_id"])

            target_after = _business_reclassification_snapshot(target)
            if target_after["state"] not in _BUSINESS_RECLASSIFICATION_TARGET_STATES:
                raise ValueError(
                    "business reclassification target state is unsupported: "
                    f"{target_after['state']}"
                )
            if target_after["record_family"] != proof_record_family:
                raise ValueError("target record_family does not match proof")
            if target_after["business_id"] != proposed_business_id:
                raise ValueError("target business_id does not match proof")
            if target_after["project_code"] != proof_project_code:
                raise ValueError("target project_code does not match proof")
            if target_after["source_id"] != proof_source_id:
                raise ValueError("target source_id does not match proof")
            if target_after["exchange"] != proof_exchange:
                raise ValueError("target exchange does not match proof")
            if evidence_kind == "locked_target_revision":
                if target_record is not None:
                    raise ValueError("locked_target_revision evidence cannot upsert a target")
                if str(proof_payload["target_record_id"]) != intended_target_record_id:
                    raise ValueError("proof target_record_id does not match target")
                if int(proof_payload["target_revision_id"]) != target_after["revision_id"]:
                    raise RuntimeError("proof target revision_id drift")
                if str(proof_payload["target_revision_hash"]) != target_after["revision_hash"]:
                    raise RuntimeError("proof target revision_hash drift")
                if str(proof_payload["parser_payload_sha256"]) != target_after[
                    "parser_payload_sha256"
                ]:
                    raise RuntimeError("proof target parser payload drift")

            finding = _superseded_record_finding(
                superseded_by_record={
                    "record_id": intended_target_record_id,
                    "state": target_after["state"],
                    "project_code": target_after["project_code"],
                }
            )
            canonical_record = _json_object_loads(original["canonical_record_json"])
            canonical_record_json = _sync_canonical_record_dict_diagnostics(
                canonical_record,
                [finding],
            )
            message = (
                "Record superseded by business reclassification: "
                f"{intended_target_record_id}"
            )
            original_cursor = conn.execute(
                """
                UPDATE records
                SET state = 'skipped',
                    last_error_type = 'superseded_by_record',
                    last_error_message = ?,
                    artifact_status = 'ok',
                    last_operation_kind = 'business_reclassification',
                    last_operation_code = 'ok',
                    last_operation_message = '',
                    last_operation_at = ?,
                    updated_at = ?
                WHERE record_id = ? AND latest_revision_id = ?
                """,
                (
                    message,
                    now,
                    now,
                    original_record_id,
                    int(actual_original["revision_id"]),
                ),
            )
            if original_cursor.rowcount != 1:
                raise RuntimeError("business reclassification original changed during apply")
            revision_cursor = conn.execute(
                """
                UPDATE record_revisions
                SET state = 'skipped',
                    findings_json = ?,
                    canonical_record_json = ?
                WHERE revision_id = ?
                  AND record_id = ?
                  AND revision_hash = ?
                """,
                (
                    _json_dumps([finding]),
                    canonical_record_json,
                    int(actual_original["revision_id"]),
                    original_record_id,
                    actual_original["revision_hash"],
                ),
            )
            if revision_cursor.rowcount != 1:
                raise RuntimeError("business reclassification original revision changed during apply")
            pending_cursor = conn.execute(
                """
                UPDATE mapping_pending
                SET resolved_at = ?
                WHERE record_id = ? AND resolved_at = ''
                """,
                (now, original_record_id),
            )
            resolved_mapping_pending = int(pending_cursor.rowcount or 0)

            cursor_rows = conn.execute(
                """
                SELECT DISTINCT cursor_key
                FROM export_cursor_records
                WHERE record_id IN (?, ?)
                ORDER BY cursor_key
                """,
                (original_record_id, intended_target_record_id),
            ).fetchall()
            affected_cursor_ids = [str(row["cursor_key"]) for row in cursor_rows]

            public_proof = {
                str(key): value
                for key, value in proof_payload.items()
                if str(key) != "parser_payload"
            }
            audit_payload = {
                "record_id": original_record_id,
                "previous_state": actual_original["state"],
                "previous_record_family": actual_original["record_family"],
                "previous_business_id": actual_original["business_id"],
                "project_code": actual_original["project_code"],
                "superseded_by_record_id": intended_target_record_id,
                "superseded_by_revision_id": target_revision_id,
                "superseded_by_state": target_after["state"],
                "superseded_by_record_family": target_after["record_family"],
                "superseded_by_business_id": target_after["business_id"],
                "target_created_or_updated": target_record is not None,
                "target_revision_changed": target_changed,
                "resolved_mapping_pending": resolved_mapping_pending,
                "affected_cursor_ids": affected_cursor_ids,
                "cursor_rows_preserved_for_removal": True,
                "proof": public_proof,
            }
            self._add_audit_entry_conn(
                conn,
                "record_business_reclassification_consolidated",
                audit_payload,
                event_ts=now,
            )

            if evidence_kind == "fresh_source_parse" and _regular_file_sha256(
                source_path,
                field="business reclassification source recheck",
            ) != source_sha256:
                raise RuntimeError("business reclassification source changed during apply")

        return {
            "record_id": original_record_id,
            "state": RecordState.SKIPPED.value,
            "superseded_by_record_id": intended_target_record_id,
            "superseded_by_revision_id": target_revision_id,
            "target_created_or_updated": target_record is not None,
            "target_revision_changed": target_changed,
            "resolved_mapping_pending": resolved_mapping_pending,
            "affected_cursor_ids": affected_cursor_ids,
            "cursor_rows_preserved_for_removal": True,
        }

    def update_record_state(
        self,
        record_id: str,
        *,
        state: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update just the state of an existing record.

        This is used when reprocess fails to transition the original record
        to a failed state instead of creating a new sibling failed record.
        """
        now = _utcnow()
        if error_type is not None:
            last_error_type = str(error_type)
        else:
            last_error_type = ""
        if error_message is not None:
            last_error_message = str(error_message)
        else:
            last_error_message = ""
        with self._connect() as conn:
            record_row = conn.execute(
                "SELECT latest_revision_id FROM records WHERE record_id = ?",
                (str(record_id),),
            ).fetchone()
            if record_row is None:
                raise RuntimeError(f"update_record_state: no record found with record_id={record_id!r}")
            # First update the record
            cursor = conn.execute(
                """
                UPDATE records
                SET state = ?,
                    last_error_type = ?,
                    last_error_message = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (str(state), last_error_type, last_error_message, now, str(record_id)),
            )
            if cursor.rowcount == 0:
                raise RuntimeError(f"update_record_state: no record found with record_id={record_id!r}")
            # Also update the latest revision
            latest_revision_id = record_row["latest_revision_id"]
            if latest_revision_id is not None:
                revision_cursor = conn.execute(
                    """
                    UPDATE record_revisions
                    SET state = ?
                    WHERE revision_id = ?
                    """,
                    (str(state), int(latest_revision_id)),
                )
                if revision_cursor.rowcount == 0:
                    raise RuntimeError(
                        "update_record_state: missing latest revision "
                        f"revision_id={int(latest_revision_id)!r} for record_id={record_id!r}"
                    )

    def mark_ready_record_field_missing(
        self,
        record_id: str,
        *,
        revision_id: int,
        finding: PostProcessFinding,
    ) -> bool:
        """Atomically demote a stale READY revision rejected by export projection."""
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_revision_id = int(revision_id)
        if resolved_revision_id <= 0:
            raise ValueError("revision_id must be positive")
        finding_payload = _postprocess_finding_payload(finding)
        finding_type = str(finding_payload.get("type") or "").strip()
        if finding_type not in {"canonical_field_missing", "export_field_missing"}:
            raise ValueError(
                "finding.type must be canonical_field_missing or export_field_missing"
            )

        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    records.state,
                    records.latest_revision_id,
                    records.project_code,
                    revisions.findings_json,
                    revisions.canonical_record_json,
                    revisions.postprocess_payload_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_id = ?
                """,
                (resolved_record_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "mark_ready_record_field_missing: no record found with "
                    f"record_id={resolved_record_id!r}"
                )
            if (
                str(row["state"] or "").strip() != RecordState.READY.value
                or int(row["latest_revision_id"] or 0) != resolved_revision_id
            ):
                return False

            existing_findings = _findings_mapping_payloads(
                _findings_json_loads(row["findings_json"]),
                field="findings_json",
            )
            finding_identity = (
                finding_type,
                _json_dumps(finding_payload.get("evidence") or {}),
            )
            if all(
                (
                    str(item.get("type") or "").strip(),
                    _json_dumps(_finding_dict_evidence_payload(item)),
                )
                != finding_identity
                for item in existing_findings
            ):
                existing_findings.append(finding_payload)

            canonical_record = _json_object_loads(row["canonical_record_json"])
            canonical_record_json = _sync_canonical_record_dict_diagnostics(
                canonical_record,
                existing_findings,
            )
            record_cursor = conn.execute(
                """
                UPDATE records
                SET state = ?,
                    updated_at = ?
                WHERE record_id = ?
                  AND state = ?
                  AND latest_revision_id = ?
                """,
                (
                    RecordState.FIELD_MISSING.value,
                    now,
                    resolved_record_id,
                    RecordState.READY.value,
                    resolved_revision_id,
                ),
            )
            if record_cursor.rowcount == 0:
                return False
            revision_cursor = conn.execute(
                """
                UPDATE record_revisions
                SET state = ?,
                    findings_json = ?,
                    canonical_record_json = ?
                WHERE revision_id = ?
                  AND record_id = ?
                """,
                (
                    RecordState.FIELD_MISSING.value,
                    _json_dumps(existing_findings),
                    canonical_record_json,
                    resolved_revision_id,
                    resolved_record_id,
                ),
            )
            if revision_cursor.rowcount == 0:
                raise RuntimeError(
                    "mark_ready_record_field_missing: latest revision disappeared for "
                    f"record_id={resolved_record_id!r}, revision_id={resolved_revision_id!r}"
                )
            self._sync_mapping_pending_for_record(
                conn,
                record_id=resolved_record_id,
                revision_id=resolved_revision_id,
                project_code=str(row["project_code"] or ""),
                payload=_payload_json_loads(row["postprocess_payload_json"]),
                state=RecordState.FIELD_MISSING.value,
            )
            self._add_audit_entry_conn(
                conn,
                "ready_record_field_missing_detected_by_export",
                {
                    "record_id": resolved_record_id,
                    "revision_id": resolved_revision_id,
                    "previous_state": RecordState.READY.value,
                    "new_state": RecordState.FIELD_MISSING.value,
                    "finding_type": finding_type,
                    "missing_fields": finding_payload.get("evidence", {}).get(
                        "missing_fields",
                        [],
                    ),
                },
                event_ts=now,
            )
        return True

    def interrupt_running_jobs(self, *, reason: str) -> list[str]:
        now = _utcnow()
        resolved_reason = _required_text(reason, field="reason")
        interrupted: list[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, summary_json
                FROM jobs
                WHERE status IN ('starting', 'running')
                """
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"] or "").strip()
                if not job_id:
                    continue
                summary = _job_summary_json_loads(row["summary_json"])
                summary.update(
                    {
                        "status": "interrupted",
                        "message": resolved_reason,
                        "interrupted_at": now,
                    }
                )
                cursor = conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, summary_json = ?, updated_at = ?
                    WHERE job_id = ? AND status IN ('starting', 'running')
                    """,
                    ("interrupted", _json_dumps(summary), now, job_id),
                )
                if cursor.rowcount == 0:
                    continue
                conn.execute(
                    """
                    INSERT INTO job_events (
                        job_id, event_ts, stage, status, project_code, archive_path,
                        error_type, error_message, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        now,
                        "failed",
                        "interrupted",
                        "",
                        "",
                        "job_interrupted",
                        resolved_reason,
                        _json_dumps({"label": "任务已中断", "reason": resolved_reason}),
                    ),
                )
                interrupted.append(job_id)
        return interrupted

    def interrupt_job(self, job_id: str, *, reason: str) -> bool:
        now = _utcnow()
        normalized_job_id = _required_text(job_id, field="job_id")
        resolved_reason = _required_text(reason, field="reason")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status, summary_json
                FROM jobs
                WHERE job_id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"interrupt_job: no job found with job_id={normalized_job_id!r}")
            current_status = str(row["status"] or "").strip()
            if current_status not in {"starting", "running"}:
                return False
            summary = _job_summary_json_loads(row["summary_json"])
            summary.update(
                {
                    "status": "interrupted",
                    "message": resolved_reason,
                    "interrupted_at": now,
                }
            )
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, summary_json = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('starting', 'running')
                """,
                ("interrupted", _json_dumps(summary), now, normalized_job_id),
            )
            if cursor.rowcount == 0:
                return False
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, project_code, archive_path,
                    error_type, error_message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_job_id,
                    now,
                    "failed",
                    "interrupted",
                    "",
                    "",
                    "job_interrupted",
                    resolved_reason,
                    _json_dumps({"label": "任务已中断", "reason": resolved_reason}),
                ),
            )
        return True

    def append_event(self, event: ItemProgressEvent) -> None:
        now = _utcnow()
        payload = _mapping_payload(event.payload, field="event.payload")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, project_code, archive_path,
                    error_type, error_message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.job_id,
                    now,
                    event.stage,
                    event.status,
                    event.project_code,
                    event.archive_path,
                    event.error_type,
                    event.error_message,
                    _json_dumps(payload),
                ),
            )
            conn.execute(
                """
                UPDATE jobs
                SET updated_at = ?
                WHERE job_id = ?
                """,
                (now, event.job_id),
            )

    def _add_audit_entry_conn(
        self,
        conn: sqlite3.Connection,
        action: str,
        payload: Dict[str, Any],
        *,
        event_ts: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log (event_ts, action, payload_json) VALUES (?, ?, ?)",
            (event_ts or _utcnow(), str(action), _json_dumps(payload)),
        )

    def add_audit_entry(self, action: str, payload: Dict[str, Any]) -> None:
        resolved_action = _required_text(action, field="action")
        payload_object = _object_payload(payload, field="payload")
        with self._connect() as conn:
            self._add_audit_entry_conn(conn, resolved_action, payload_object)

    def acknowledge_field_missing(
        self,
        record_id: str,
        *,
        missing_fields: Any,
        evidence_source: str = "operator_acknowledge",
    ) -> Dict[str, Any]:
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_evidence_source = _required_text(evidence_source, field="evidence_source")
        normalized_missing_fields = normalize_missing_fields(missing_fields)
        if not normalized_missing_fields:
            raise ValueError("missing_fields is empty")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.state,
                    records.latest_revision_id,
                    records.acknowledged_payload_json
                FROM records
                WHERE records.record_id = ?
                """,
                (resolved_record_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resolved_record_id)
            existing_ack = _acknowledged_payload_json_loads(row["acknowledged_payload_json"])
            _mapping_payload(existing_ack.get("field_missing"), field="field_missing", allow_none=True)
            ack_payload = build_field_missing_ack_payload(
                previous_state=str(row["state"] or ""),
                evidence_source=resolved_evidence_source,
                missing_fields=normalized_missing_fields,
                revision_id=int(row["latest_revision_id"]) if row["latest_revision_id"] is not None else None,
            )
            ack_payload["field_missing"]["acknowledged"] = True
            existing_ack.update(ack_payload)
            conn.execute(
                """
                UPDATE records
                SET acknowledged_payload_json = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (_json_dumps(existing_ack), _utcnow(), resolved_record_id),
            )
            self._add_audit_entry_conn(
                conn,
                "field_missing_acknowledged",
                {
                    "record_id": resolved_record_id,
                    "state": str(row["state"] or ""),
                    "revision_id": int(row["latest_revision_id"] or 0),
                    "missing_fields_hash": ack_payload["field_missing"].get("missing_fields_hash", ""),
                },
            )
        return self.get_record(resolved_record_id)

    def create_operation_journal(
        self,
        operation_type: str,
        *,
        metadata: Dict[str, Any] | None = None,
        operation_id: str | None = None,
        manifest: Dict[str, Any] | None = None,
    ) -> str:
        resolved_operation_id = (
            uuid.uuid4().hex if operation_id is None else _required_text(operation_id, field="operation_id")
        )
        resolved_operation_type = _required_text(operation_type, field="operation_type")
        metadata_payload = {} if metadata is None else _object_payload(metadata, field="metadata")
        manifest_payload = {} if manifest is None else _object_payload(manifest, field="manifest")
        started_at = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operation_journal (
                    operation_id,
                    operation_type,
                    status,
                    recovery_state,
                    started_at,
                    finished_at,
                    metadata_json,
                    manifest_json,
                    error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_operation_id,
                    resolved_operation_type,
                    "pending",
                    "",
                    started_at,
                    "",
                    _json_dumps(metadata_payload),
                    _json_dumps(manifest_payload),
                    _json_dumps({}),
                ),
            )
        return resolved_operation_id

    def update_operation_journal(
        self,
        operation_id: str,
        *,
        status: str | None = None,
        recovery_state: str | None = None,
        finished_at: str | None = None,
        manifest: Dict[str, Any] | None = None,
        error: Dict[str, Any] | None = None,
    ) -> None:
        resolved_operation_id = _required_text(operation_id, field="operation_id")
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(_required_text(status, field="status"))
        if recovery_state is not None:
            updates.append("recovery_state = ?")
            params.append(_required_text(recovery_state, field="recovery_state"))
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(_required_text(finished_at, field="finished_at"))
        if manifest is not None:
            updates.append("manifest_json = ?")
            params.append(_json_dumps(_object_payload(manifest, field="manifest")))
        if error is not None:
            updates.append("error_json = ?")
            params.append(_json_dumps(_object_payload(error, field="error")))
        if not updates:
            return
        params.append(resolved_operation_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE operation_journal SET {', '.join(updates)} WHERE operation_id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise KeyError(resolved_operation_id)

    def get_operation_journal(self, operation_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT operation_id, operation_type, status, recovery_state, started_at, finished_at,
                       metadata_json, manifest_json, error_json
                FROM operation_journal
                WHERE operation_id = ?
                """,
                (str(operation_id or ""),),
            ).fetchone()
        if row is None:
            raise KeyError(str(operation_id or ""))
        return {
            "operation_id": str(row["operation_id"]),
            "operation_type": str(row["operation_type"]),
            "status": str(row["status"]),
            "recovery_state": str(row["recovery_state"] or ""),
            "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"] or ""),
            "metadata": _operation_journal_json_loads(row["metadata_json"]),
            "manifest": _operation_journal_json_loads(row["manifest_json"]),
            "error": _operation_journal_json_loads(row["error_json"]),
        }

    def list_operation_journals(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT operation_id, operation_type, status, recovery_state, started_at, finished_at,
                       metadata_json, manifest_json, error_json
                FROM operation_journal
                ORDER BY started_at DESC, operation_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "operation_id": str(row["operation_id"]),
                "operation_type": str(row["operation_type"]),
                "status": str(row["status"]),
                "recovery_state": str(row["recovery_state"] or ""),
                "started_at": str(row["started_at"]),
                "finished_at": str(row["finished_at"] or ""),
                "metadata": _operation_journal_json_loads(row["metadata_json"]),
                "manifest": _operation_journal_json_loads(row["manifest_json"]),
                "error": _operation_journal_json_loads(row["error_json"]),
            }
            for row in rows
        ]

    def get_operation_snapshot(self) -> Dict[str, Any]:
        with self._connect() as conn:
            user_version_row = conn.execute("PRAGMA user_version").fetchone()
            records_count = int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])
            jobs_count = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            exports_count = int(conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0])
            pending_mappings_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM mapping_pending
                    WHERE resolved_at = ''
                    """
                ).fetchone()[0]
            )
        return {
            "db_path": self.db_path,
            "schema_version": SCHEMA_VERSION,
            "user_version": int(user_version_row[0] if user_version_row else 0),
            "counts": {
                "records": records_count,
                "jobs": jobs_count,
                "exports": exports_count,
                "pending_mappings": pending_mappings_count,
            },
        }

    def normalize_legacy_skip_parse_entries(self) -> Dict[str, int]:
        with self._connect() as conn:
            records_count = conn.execute(
                """
                UPDATE records
                SET state = 'skipped',
                    last_error_type = 'skip_parse',
                    updated_at = updated_at
                WHERE state <> 'skipped'
                  AND (
                    last_error_type = 'skip_parse'
                    OR last_error_message LIKE 'skip-cbex-otc-page:%'
                  )
                """
            ).rowcount
            revisions_count = conn.execute(
                """
                UPDATE record_revisions
                SET state = 'skipped'
                WHERE state <> 'skipped'
                  AND record_id IN (
                    SELECT record_id
                    FROM records
                    WHERE state = 'skipped' AND last_error_type = 'skip_parse'
                  )
                """
            ).rowcount
            events_count = conn.execute(
                """
                UPDATE job_events
                SET stage = 'skipped',
                    status = 'skipped',
                    error_type = 'skip_parse'
                WHERE status <> 'skipped'
                  AND (
                    error_type = 'skip_parse'
                    OR error_message LIKE 'skip-cbex-otc-page:%'
                  )
                """
            ).rowcount

            affected_job_rows = conn.execute(
                """
                SELECT DISTINCT job_id
                FROM job_events
                WHERE status = 'skipped' AND error_type = 'skip_parse'
                """
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in affected_job_rows]
            for job_id in job_ids:
                stats_row = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN stage = 'failed' THEN 1 ELSE 0 END) AS exception_count
                    FROM job_events
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE jobs
                    SET exception_count = ?
                    WHERE job_id = ?
                    """,
                    (
                        int(stats_row["exception_count"] or 0),
                        job_id,
                    ),
                )

        return {
            "records": int(records_count or 0),
            "revisions": int(revisions_count or 0),
            "events": int(events_count or 0),
            "jobs": len(job_ids),
        }

    def normalize_listing_dates(self) -> int:
        updated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, listing_date
                FROM records
                WHERE listing_date <> ''
                """
            ).fetchall()
            for row in rows:
                normalized = _normalize_date_text(str(row["listing_date"] or ""))
                if not normalized or normalized == str(row["listing_date"] or ""):
                    continue
                conn.execute(
                    """
                    UPDATE records
                    SET listing_date = ?
                    WHERE record_id = ?
                    """,
                    (normalized, row["record_id"]),
                )
                updated += 1
        return updated

    def normalize_invalid_source_pages(self) -> Dict[str, int]:
        """Move legacy CBEX deal records built from non-detail pages out of review queues."""
        now = _utcnow()
        updates: list[tuple[str, int, str, str]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.state,
                    records.business_id,
                    records.source_identity_json,
                    records.exchange,
                    records.latest_revision_id,
                    revisions.state AS revision_state,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json,
                    revisions.canonical_record_json,
                    revisions.findings_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_family = 'deal'
                  AND records.state IN ('ready', 'pending_review', 'field_missing', 'skipped')
                """
            ).fetchall()
            for row in rows:
                source_identity = _source_identity_json_loads(row["source_identity_json"])
                parser_payload = _payload_json_loads(row["parser_payload_json"])
                postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
                canonical_record = _json_object_loads(row["canonical_record_json"])
                raw_source = canonical_record.get("source_identity")
                canonical_source = dict(raw_source) if isinstance(raw_source, dict) else {}
                source_id = str(
                    (source_identity if isinstance(source_identity, dict) else {}).get("source_id")
                    or (parser_payload if isinstance(parser_payload, dict) else {}).get("source_id")
                    or (postprocess_payload if isinstance(postprocess_payload, dict) else {}).get("source_id")
                    or canonical_source.get("source_id")
                    or row["exchange"]
                    or ""
                ).strip()
                if source_id not in {"cbex", "北交所"}:
                    continue
                business_id = str(row["business_id"] or "").strip()
                if not business_id and isinstance(parser_payload, dict):
                    business_id = str(parser_payload.get("business_id") or "").strip()
                if not business_id and isinstance(postprocess_payload, dict):
                    business_id = str(postprocess_payload.get("business_id") or "").strip()
                source_url = str(
                    (source_identity if isinstance(source_identity, dict) else {}).get("source_url")
                    or (parser_payload if isinstance(parser_payload, dict) else {}).get("source_url")
                    or (parser_payload if isinstance(parser_payload, dict) else {}).get("page_url")
                    or (postprocess_payload if isinstance(postprocess_payload, dict) else {}).get("source_url")
                    or (postprocess_payload if isinstance(postprocess_payload, dict) else {}).get("page_url")
                    or canonical_source.get("source_url")
                    or ""
                ).strip()
                if not is_cbex_deal_non_detail_page(source_url, business_id=business_id):
                    continue
                if (
                    str(row["state"] or "") == RecordState.SKIPPED.value
                    and str(row["revision_state"] or "") == RecordState.SKIPPED.value
                ):
                    raw_findings = _findings_json_loads(row["findings_json"])
                    finding_types = {
                        str(item.get("type") or "").strip()
                        for item in _findings_mapping_payloads(raw_findings, field="findings_json")
                    }
                    if "rule_filtered" in finding_types:
                        continue
                updates.append((str(row["record_id"]), int(row["latest_revision_id"]), source_url, str(row["state"] or "")))

            for record_id, revision_id, source_url, previous_state in updates:
                finding = _invalid_source_page_finding(source_url=source_url)
                findings_json = _json_dumps([finding])
                canonical_row = conn.execute(
                    "SELECT canonical_record_json FROM record_revisions WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                canonical_record = _json_object_loads(
                    canonical_row["canonical_record_json"] if canonical_row is not None else "{}"
                )
                canonical_record_json = _sync_canonical_record_dict_diagnostics(canonical_record, [finding])
                conn.execute(
                    """
                    UPDATE records
                    SET state = 'skipped',
                        last_error_type = 'invalid_source_page',
                        last_error_message = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (
                        f"CBEX deal source URL is not a detail page: {source_url}",
                        now,
                        record_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET state = 'skipped',
                        findings_json = ?,
                        canonical_record_json = ?
                    WHERE revision_id = ?
                    """,
                    (findings_json, canonical_record_json, revision_id),
                )
                conn.execute(
                    """
                    INSERT INTO audit_log (event_ts, action, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        now,
                        "invalid_source_page_backfill",
                        _json_dumps(
                            {
                                "record_id": record_id,
                                "previous_state": previous_state,
                                "source_url": source_url,
                            }
                        ),
                    ),
                )
        return {"records": len(updates), "revisions": len(updates)}

    def purge_invalid_source_page_records(self) -> Dict[str, int]:
        """Remove terminal invalid source-page records from the active store.

        Eligibility is intentionally narrow: the record must already be terminal
        ``skipped/invalid_source_page`` and either be a CBEX deal non-detail URL,
        or be a missing-artifact deal duplicate with a ready same-project deal
        record. The deletion key is always the record_id selected by that rule;
        project_code is only evidence, never the deletion target.
        """
        now = _utcnow()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.record_family,
                    records.business_id,
                    records.project_code,
                    records.exchange,
                    records.source_file,
                    records.source_identity_json,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json,
                    revisions.canonical_record_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_family = 'deal'
                  AND records.state = 'skipped'
                  AND records.last_error_type = 'invalid_source_page'
                """
            ).fetchall()

            purge_candidates: list[tuple[str, str, str, str, str]] = []
            for row in rows:
                source_identity = _source_identity_json_loads(row["source_identity_json"])
                parser_payload = _payload_json_loads(row["parser_payload_json"])
                postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
                canonical_record = _json_object_loads(row["canonical_record_json"])
                if not isinstance(source_identity, dict):
                    source_identity = {}
                if not isinstance(parser_payload, dict):
                    parser_payload = {}
                if not isinstance(postprocess_payload, dict):
                    postprocess_payload = {}
                canonical_source = canonical_record.get("source_identity")
                if not isinstance(canonical_source, dict):
                    canonical_source = {}

                source_id = (
                    _first_text_from_mapping(source_identity, "source_id")
                    or _first_text_from_mapping(parser_payload, "source_id")
                    or _first_text_from_mapping(postprocess_payload, "source_id")
                    or _first_text_from_mapping(canonical_source, "source_id")
                    or str(row["exchange"] or "").strip()
                )
                business_id = (
                    str(row["business_id"] or "").strip()
                    or _first_text_from_mapping(parser_payload, "business_id")
                    or _first_text_from_mapping(postprocess_payload, "business_id")
                )
                source_url = (
                    _first_text_from_mapping(source_identity, "source_url")
                    or _first_text_from_mapping(parser_payload, "source_url", "page_url")
                    or _first_text_from_mapping(postprocess_payload, "source_url", "page_url")
                    or _first_text_from_mapping(canonical_source, "source_url")
                )
                record_id = str(row["record_id"] or "")
                project_code = str(row["project_code"] or "").strip()
                reason = ""
                if source_id.strip().lower() in {"cbex", "北交所"} and is_cbex_deal_non_detail_page(
                    source_url,
                    business_id=business_id,
                ):
                    reason = "cbex_deal_non_detail_page"
                elif project_code and not os.path.isfile(str(row["source_file"] or "")):
                    sibling = conn.execute(
                        """
                        SELECT record_id
                        FROM records
                        WHERE record_id <> ?
                          AND record_family = ?
                          AND project_code = ?
                          AND state = 'ready'
                          AND last_error_type = ''
                        LIMIT 1
                        """,
                        (record_id, str(row["record_family"] or ""), project_code),
                    ).fetchone()
                    if sibling is not None:
                        reason = "missing_invalid_source_duplicate_with_ready_record"

                if reason:
                    purge_candidates.append(
                        (
                            record_id,
                            project_code,
                            str(row["source_file"] or ""),
                            source_url,
                            reason,
                        )
                    )

            deleted_revisions = 0
            deleted_mapping_pending = 0
            deleted_export_cursor_records = 0
            for record_id, project_code, source_file, source_url, reason in purge_candidates:
                revision_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM record_revisions WHERE record_id = ?",
                    (record_id,),
                ).fetchone()
                cursor = conn.execute(
                    "DELETE FROM mapping_pending WHERE record_id = ?",
                    (record_id,),
                )
                deleted_mapping_pending += int(cursor.rowcount if cursor.rowcount is not None else 0)
                cursor = conn.execute(
                    "DELETE FROM export_cursor_records WHERE record_id = ?",
                    (record_id,),
                )
                deleted_export_cursor_records += int(cursor.rowcount if cursor.rowcount is not None else 0)
                conn.execute("DELETE FROM record_revisions WHERE record_id = ?", (record_id,))
                conn.execute("DELETE FROM records WHERE record_id = ?", (record_id,))
                deleted_revisions += int(revision_count["count"] if revision_count is not None else 0)
                conn.execute(
                    """
                    INSERT INTO audit_log (event_ts, action, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        now,
                        "invalid_source_page_record_purged",
                        _json_dumps(
                            {
                                "record_id": record_id,
                                "project_code": project_code,
                                "source_file": source_file,
                                "source_url": source_url,
                                "reason": reason,
                            }
                        ),
                    ),
                )

        return {
            "records": len(purge_candidates),
            "revisions": deleted_revisions,
            "mapping_pending": deleted_mapping_pending,
            "export_cursor_records": deleted_export_cursor_records,
        }

    def purge_quarantined_synthetic_failed_records(self) -> Dict[str, int]:
        """Remove terminal failed records created from legacy synthetic deal shells."""
        now = _utcnow()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, project_code, source_file, latest_revision_id
                FROM records
                WHERE state IN ('parse_failed', 'postprocess_failed')
                  AND last_error_type = 'synthetic_archive_quarantined'
                """
            ).fetchall()
            rows = [
                row
                for row in rows
                if not os.path.isfile(str(row["source_file"] or ""))
            ]
            deleted_revisions = 0
            deleted_mapping_pending = 0
            deleted_export_cursor_records = 0
            for row in rows:
                record_id = str(row["record_id"] or "")
                revision_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM record_revisions WHERE record_id = ?",
                    (record_id,),
                ).fetchone()
                cursor = conn.execute("DELETE FROM mapping_pending WHERE record_id = ?", (record_id,))
                deleted_mapping_pending += int(cursor.rowcount if cursor.rowcount is not None else 0)
                cursor = conn.execute("DELETE FROM export_cursor_records WHERE record_id = ?", (record_id,))
                deleted_export_cursor_records += int(cursor.rowcount if cursor.rowcount is not None else 0)
                conn.execute("DELETE FROM record_revisions WHERE record_id = ?", (record_id,))
                conn.execute("DELETE FROM records WHERE record_id = ?", (record_id,))
                deleted_revisions += int(revision_count["count"] if revision_count is not None else 0)
                conn.execute(
                    """
                    INSERT INTO audit_log (event_ts, action, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        now,
                        "quarantined_synthetic_failed_record_purged",
                        _json_dumps(
                            {
                                "record_id": record_id,
                                "project_code": str(row["project_code"] or ""),
                                "source_file": str(row["source_file"] or ""),
                                "reason": "synthetic_archive_quarantined_missing_artifact",
                            }
                        ),
                    ),
                )
        return {
            "records": len(rows),
            "revisions": deleted_revisions,
            "mapping_pending": deleted_mapping_pending,
            "export_cursor_records": deleted_export_cursor_records,
        }

    def normalize_superseded_record_shells(self) -> Dict[str, int]:
        """Move legacy review/field-missing shells superseded by canonical records to a terminal state."""
        now = _utcnow()
        records = self.iter_latest_records(sort="recent")
        superseding_index = build_superseding_record_index(records)
        updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for record in records:
            state = str(record.get("state") or "").strip()
            if state not in {
                RecordState.PENDING_REVIEW.value,
                RecordState.FIELD_MISSING.value,
                "parse_failed",
                "postprocess_failed",
            }:
                continue
            superseding_record = find_superseding_record(record, superseding_index)
            if superseding_record is not None:
                updates.append((record, superseding_record))

        with self._connect() as conn:
            for record, superseding_record in updates:
                record_id = str(record.get("record_id") or "")
                revision_id = int(record.get("revision_id") or record.get("latest_revision_id") or 0)
                if not record_id or revision_id <= 0:
                    continue
                finding = _superseded_record_finding(superseded_by_record=superseding_record)
                findings_json = _json_dumps([finding])
                canonical_row = conn.execute(
                    "SELECT canonical_record_json FROM record_revisions WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                canonical_record = _json_object_loads(
                    canonical_row["canonical_record_json"] if canonical_row is not None else "{}"
                )
                canonical_record_json = _sync_canonical_record_dict_diagnostics(canonical_record, [finding])
                conn.execute(
                    """
                    UPDATE records
                    SET state = 'skipped',
                        last_error_type = 'superseded_by_record',
                        last_error_message = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (
                        "Record shell superseded by canonical record: "
                        f"{superseding_record.get('record_id') or ''}",
                        now,
                        record_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET state = 'skipped',
                        findings_json = ?,
                        canonical_record_json = ?
                    WHERE revision_id = ?
                    """,
                    (findings_json, canonical_record_json, revision_id),
                )
                conn.execute(
                    """
                    INSERT INTO audit_log (event_ts, action, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        now,
                        "superseded_record_shell_backfill",
                        _json_dumps(
                            {
                                "record_id": record_id,
                                "previous_state": str(record.get("state") or ""),
                                "superseded_by_record_id": str(superseding_record.get("record_id") or ""),
                                "superseded_by_state": str(superseding_record.get("state") or ""),
                            }
                        ),
                    ),
                )
        return {"records": len(updates), "revisions": len(updates)}

    def normalize_deal_source_artifacts(self) -> Dict[str, int]:
        """Reclassify deal records whose source artifact cannot support audit/export."""
        from peap_core.record_state_policy import classify_record_state

        now = _utcnow()
        updates: list[tuple[str, int, str, str, dict[str, Any], str]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.state,
                    records.project_code,
                    records.exchange,
                    records.source_file,
                    records.artifact_status,
                    records.latest_revision_id,
                    records.source_identity_json,
                    revisions.state AS revision_state,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json,
                    revisions.canonical_record_json,
                    revisions.findings_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_family = 'deal'
                  AND records.state IN ('ready', 'field_missing')
                  AND records.last_error_type NOT IN ('invalid_source_page', 'superseded_by_record')
                """
            ).fetchall()
            for row in rows:
                source_identity = _source_identity_json_loads(row["source_identity_json"])
                parser_payload = _payload_json_loads(row["parser_payload_json"])
                postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
                canonical_record = _json_object_loads(row["canonical_record_json"])
                if not isinstance(source_identity, dict):
                    source_identity = {}
                if not isinstance(parser_payload, dict):
                    parser_payload = {}
                if not isinstance(postprocess_payload, dict):
                    postprocess_payload = {}
                canonical_source = canonical_record.get("source_identity")
                if not isinstance(canonical_source, dict):
                    canonical_source = {}
                source_id = _first_text_from_mapping(source_identity, "source_id")
                source_id = source_id or _first_text_from_mapping(parser_payload, "source_id")
                source_id = source_id or _first_text_from_mapping(postprocess_payload, "source_id")
                source_id = source_id or _first_text_from_mapping(canonical_source, "source_id")
                source_id = source_id or str(row["exchange"] or "").strip()
                issue = inspect_deal_source_artifact(
                    source_file=str(row["source_file"] or "").strip(),
                    source_id=source_id,
                    project_code=str(row["project_code"] or "").strip(),
                )
                if issue is None:
                    continue
                artifact_status = "missing" if issue.error_type == "source_artifact_missing" else "invalid"
                raw_findings = _findings_json_loads(row["findings_json"])
                existing_findings = _findings_mapping_payloads(raw_findings, field="findings_json")
                if any(
                    str(item.get("type") or "").strip() == issue.error_type
                    and _finding_dict_evidence_payload(item).get("source_file") == issue.evidence.get("source_file")
                    for item in existing_findings
                ) and str(row["artifact_status"] or "") == artifact_status:
                    continue
                finding = source_artifact_issue_finding(issue)
                findings = [finding]
                new_state = classify_record_state(findings).value
                updates.append(
                    (
                        str(row["record_id"]),
                        int(row["latest_revision_id"]),
                        str(row["state"] or ""),
                        new_state,
                        finding,
                        issue.error_type,
                        artifact_status,
                    )
                )

            for record_id, revision_id, previous_state, new_state, finding, error_type, artifact_status in updates:
                findings_json = _json_dumps([finding])
                canonical_row = conn.execute(
                    "SELECT canonical_record_json FROM record_revisions WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                canonical_record = _json_object_loads(
                    canonical_row["canonical_record_json"] if canonical_row is not None else "{}"
                )
                canonical_record_json = _sync_canonical_record_dict_diagnostics(canonical_record, [finding])
                conn.execute(
                    """
                    UPDATE records
                    SET state = ?,
                        last_error_type = ?,
                        last_error_message = ?,
                        artifact_status = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (new_state, error_type, str(finding.get("message") or ""), artifact_status, now, record_id),
                )
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET state = ?,
                        findings_json = ?,
                        canonical_record_json = ?
                    WHERE revision_id = ?
                    """,
                    (new_state, findings_json, canonical_record_json, revision_id),
                )
                conn.execute(
                    """
                    INSERT INTO audit_log (event_ts, action, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        now,
                        "deal_source_artifact_integrity_backfill",
                        _json_dumps(
                            {
                                "record_id": record_id,
                                "previous_state": previous_state,
                                "new_state": new_state,
                                "error_type": error_type,
                                "evidence": _finding_dict_evidence_payload(finding),
                            }
                        ),
                    ),
                )

        return {"records": len(updates), "revisions": len(updates)}

    def normalize_business_kernel_fields(self) -> Dict[str, int]:
        updated_records = 0
        updated_revisions = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.record_family,
                    records.project_type,
                    records.business_id,
                    records.raw_business_label,
                    records.latest_revision_id,
                    revisions.revision_id,
                    revisions.canonical_record_json
                FROM records
                LEFT JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                """
            ).fetchall()
            for row in rows:
                if row["latest_revision_id"] is not None and row["revision_id"] is None:
                    raise RuntimeError(
                        "normalize_business_kernel_fields: missing latest revision "
                        f"revision_id={int(row['latest_revision_id'])!r} for record_id={str(row['record_id'])!r}"
                    )
                canonical_record = _json_object_loads(row["canonical_record_json"])
                business_id, raw_business_label = _resolve_business_kernel_fields(
                    record_family=row["record_family"],
                    project_type=row["project_type"],
                    canonical_record=canonical_record,
                )
                record_changed = (
                    business_id != str(row["business_id"] or "")
                    or raw_business_label != str(row["raw_business_label"] or "")
                )
                if record_changed:
                    conn.execute(
                        """
                        UPDATE records
                        SET business_id = ?,
                            raw_business_label = ?
                        WHERE record_id = ?
                        """,
                        (business_id, raw_business_label, str(row["record_id"])),
                    )
                    updated_records += 1

                business_identity = _maintenance_mapping_payload(
                    canonical_record.get("business_identity"),
                    field="canonical_record.business_identity",
                )
                canonical_changed = False
                if business_id != str(business_identity.get("business_id") or ""):
                    canonical_changed = True
                    if business_id:
                        business_identity["business_id"] = business_id
                    else:
                        business_identity.pop("business_id", None)
                if raw_business_label != str(business_identity.get("raw_business_label") or ""):
                    canonical_changed = True
                    if raw_business_label:
                        business_identity["raw_business_label"] = raw_business_label
                    else:
                        business_identity.pop("raw_business_label", None)
                if canonical_changed:
                    if business_identity:
                        canonical_record["business_identity"] = business_identity
                    else:
                        canonical_record.pop("business_identity", None)
                    if row["revision_id"] is not None:
                        conn.execute(
                            """
                            UPDATE record_revisions
                            SET canonical_record_json = ?
                            WHERE revision_id = ?
                            """,
                            (_json_dumps(canonical_record), int(row["revision_id"])),
                        )
                        updated_revisions += 1
        return {
            "records": updated_records,
            "revisions": updated_revisions,
        }

    def normalize_canonical_contracts(self) -> Dict[str, int]:
        updated_records = 0
        updated_revisions = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.record_family,
                    records.project_code,
                    records.project_name,
                    records.project_type,
                    records.exchange,
                    records.listing_date,
                    records.source_identity_json,
                    revisions.revision_id,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json,
                    revisions.canonical_record_json,
                    revisions.canonical_projection_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_family = 'listing'
                """
            ).fetchall()
            for row in rows:
                parser_payload = _payload_json_loads(row["parser_payload_json"])
                postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
                canonical_record = _json_object_loads(row["canonical_record_json"])
                canonical_projection = _json_object_loads(row["canonical_projection_json"])
                repaired_listing_date, repaired_canonical, repaired_projection = _repair_listing_record_contract(
                    record_id=str(row["record_id"] or ""),
                    record_family=str(row["record_family"] or "listing"),
                    project_code=str(row["project_code"] or ""),
                    project_name=str(row["project_name"] or ""),
                    project_type=str(row["project_type"] or ""),
                    exchange=str(row["exchange"] or ""),
                    listing_date=str(row["listing_date"] or ""),
                    source_identity=_source_identity_json_loads(row["source_identity_json"]),
                    parser_payload=parser_payload if isinstance(parser_payload, dict) else {},
                    postprocess_payload=postprocess_payload if isinstance(postprocess_payload, dict) else {},
                    canonical_record=canonical_record if isinstance(canonical_record, dict) else {},
                )
                listing_date_changed = (
                    bool(repaired_listing_date)
                    and repaired_listing_date != str(row["listing_date"] or "")
                )
                canonical_changed = _json_dumps(repaired_canonical) != _json_dumps(canonical_record)
                projection_changed = _json_dumps(repaired_projection) != _json_dumps(canonical_projection)
                if not (listing_date_changed or canonical_changed or projection_changed):
                    continue
                if listing_date_changed:
                    conn.execute(
                        """
                        UPDATE records
                        SET listing_date = ?
                        WHERE record_id = ?
                        """,
                        (repaired_listing_date, str(row["record_id"])),
                    )
                if canonical_changed or projection_changed:
                    conn.execute(
                        """
                        UPDATE record_revisions
                        SET canonical_record_json = ?,
                            canonical_projection_json = ?
                        WHERE revision_id = ?
                        """,
                        (
                            _json_dumps(repaired_canonical),
                            _json_dumps(repaired_projection),
                            int(row["revision_id"]),
                        ),
                    )
                    updated_revisions += 1
                updated_records += 1
        return {
            "records": updated_records,
            "revisions": updated_revisions,
        }

    def _normalize_record_payload_and_state(self, conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str]]]:
        """Phase 1: re-run normalize on normalizable records, update state.

        Scans records whose state is in MAINTENANCE_NORMALIZABLE_STATES.
        Re-runs normalize_record_payload + classify_record_state.
        Updates records.state, record_revisions.state/postprocess_payload_json/findings_json.

        Returns (updated_count, state_transitions) where state_transitions is a list of
        (record_id, old_state) for records whose state actually changed.
        This is needed by _reconcile_mapping_pending_backlog to correctly resolve
        entries for records that left BACKLOG_OWNING_STATES during normalization.

        禁止: 插入或解决 mapping_pending
        """
        from peap_core.record_state_policy import (
            BACKLOG_OWNING_STATES,
            MAINTENANCE_NORMALIZABLE_STATES,
            classify_record_state,
        )

        from .streaming_postprocess import RecordPostprocessContext, normalize_record_payload

        state_values = {_s.value for _s in MAINTENANCE_NORMALIZABLE_STATES}
        placeholders = ",".join("?" for _ in state_values)
        rows = conn.execute(
            f"""
            SELECT
                records.record_id,
                records.record_family,
                records.project_code,
                records.state,
                records.last_error_type,
                revisions.revision_id,
                revisions.parser_payload_json,
                revisions.postprocess_payload_json,
                revisions.findings_json,
                revisions.canonical_record_json
            FROM records
            JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE records.state IN ({placeholders})
              AND {_maintenance_terminal_error_filter_sql()}
            """,
            list(state_values),
        ).fetchall()

        records_changed = 0
        findings_changed = 0
        state_transitions: list[tuple[str, str]] = []
        backlog_values = {_s.value for _s in BACKLOG_OWNING_STATES}
        for row in rows:
            record_id = str(row["record_id"])
            record_family = str(row["record_family"] or "")
            old_state = str(row["state"])
            parser_payload = _payload_json_loads(row["parser_payload_json"])
            postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
            canonical_record = _json_object_loads(row["canonical_record_json"])
            merged_payload = _merge_maintenance_payload_with_canonical_projection(
                _merge_record_payloads(parser_payload, postprocess_payload),
                canonical_record,
            )
            raw_findings = _findings_json_loads(row["findings_json"])
            findings = [
                PostProcessFinding(
                    severity=str(item.get("severity") or "warn"),
                    type=str(item.get("type") or ""),
                    message=str(item.get("message") or ""),
                    evidence=_finding_dict_evidence_payload(item),
                )
                for item in _findings_mapping_payloads(raw_findings, field="findings_json")
            ]
            context = RecordPostprocessContext(record_family=record_family)
            normalized_payload, normalized_findings = normalize_record_payload(
                parser_payload={},
                postprocess_payload=merged_payload,
                findings=findings,
                context=context,
            )
            normalized_findings = _refresh_export_projection_findings(
                normalized_findings,
                canonical_record,
            )
            new_state = classify_record_state(normalized_findings)
            new_findings_json = _json_dumps([_postprocess_finding_payload(item) for item in normalized_findings])
            postprocess_payload_for_write = _drop_maintenance_canonical_supplements(
                normalized_payload,
                original_postprocess_payload=postprocess_payload,
                canonical_record=canonical_record,
            )
            new_payload_json = _json_dumps(postprocess_payload_for_write)

            if old_state != new_state.value:
                conn.execute(
                    "UPDATE records SET state = ? WHERE record_id = ?",
                    (new_state.value, record_id),
                )
                conn.execute(
                    "UPDATE record_revisions SET state = ? WHERE revision_id = ?",
                    (new_state.value, int(row["revision_id"])),
                )
                records_changed += 1
                # Track transitions that left BACKLOG_OWNING_STATES
                if old_state in backlog_values and new_state.value not in backlog_values:
                    state_transitions.append((record_id, old_state))

            canonical_findings = set(canonical_record.get("policy_state", {}).get("findings", []))
            expected_findings = {str(item.type) for item in normalized_findings}
            canonical_stale = canonical_findings != expected_findings

            if (
                str(row["postprocess_payload_json"] or "") != new_payload_json
                or str(row["findings_json"] or "") != new_findings_json
                or canonical_stale
            ):
                new_canonical_json = _sync_canonical_record_diagnostics(canonical_record, normalized_findings)
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET postprocess_payload_json = ?,
                        findings_json = ?,
                        canonical_record_json = ?,
                        state = ?
                    WHERE revision_id = ?
                    """,
                    (new_payload_json, new_findings_json, new_canonical_json, new_state.value, int(row["revision_id"])),
                )
                findings_changed += 1

        return records_changed, findings_changed, state_transitions

    def _normalize_optional_rule_findings(
        self,
        conn: sqlite3.Connection,
        *,
        rules_config: Dict[str, Any],
    ) -> tuple[int, int, list[tuple[str, str]]]:
        """Phase 1b: reapply optional-rule findings on normalizable records."""
        from peap_core.record_state_policy import (
            BACKLOG_OWNING_STATES,
            OPTIONAL_RULE_NORMALIZABLE_STATES,
            classify_record_state,
        )

        from .streaming_postprocess import RecordPostprocessContext, reapply_optional_rule_findings

        if not rules_config:
            return 0, 0, []

        state_values = {_s.value for _s in OPTIONAL_RULE_NORMALIZABLE_STATES}
        placeholders = ",".join("?" for _ in state_values)
        rows = conn.execute(
            f"""
            SELECT
                records.record_id,
                records.record_family,
                records.state,
                records.last_error_type,
                records.source_file,
                revisions.revision_id,
                revisions.parser_payload_json,
                revisions.postprocess_payload_json,
                revisions.findings_json,
                revisions.canonical_record_json
            FROM records
            JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE records.state IN ({placeholders})
              AND {_maintenance_terminal_error_filter_sql()}
            """,
            list(state_values),
        ).fetchall()

        records_changed = 0
        revisions_changed = 0
        state_transitions: list[tuple[str, str]] = []
        backlog_values = {_s.value for _s in BACKLOG_OWNING_STATES}
        for row in rows:
            record_id = str(row["record_id"])
            record_family = str(row["record_family"] or "")
            old_state = str(row["state"])
            parser_payload = _payload_json_loads(row["parser_payload_json"])
            postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
            canonical_record = _json_object_loads(row["canonical_record_json"])
            merged_payload = _merge_maintenance_payload_with_canonical_projection(
                _merge_record_payloads(parser_payload, postprocess_payload),
                canonical_record,
            )
            raw_findings = _findings_json_loads(row["findings_json"])
            findings = [
                PostProcessFinding(
                    severity=str(item.get("severity") or "warn"),
                    type=str(item.get("type") or ""),
                    message=str(item.get("message") or ""),
                    evidence=_finding_dict_evidence_payload(item),
                )
                for item in _findings_mapping_payloads(raw_findings, field="findings_json")
            ]
            context = RecordPostprocessContext(record_family=record_family)
            normalized_payload, normalized_findings = reapply_optional_rule_findings(
                parser_payload={},
                postprocess_payload=merged_payload,
                findings=findings,
                source_file=str(row["source_file"] or ""),
                rules_config=rules_config,
                context=context,
            )
            normalized_findings = _refresh_export_projection_findings(
                normalized_findings,
                canonical_record,
            )
            new_state = classify_record_state(normalized_findings)
            new_findings_json = _json_dumps([_postprocess_finding_payload(item) for item in normalized_findings])
            postprocess_payload_for_write = _drop_maintenance_canonical_supplements(
                normalized_payload,
                original_postprocess_payload=postprocess_payload,
                canonical_record=canonical_record,
            )
            new_payload_json = _json_dumps(postprocess_payload_for_write)

            if old_state != new_state.value:
                conn.execute(
                    "UPDATE records SET state = ? WHERE record_id = ?",
                    (new_state.value, record_id),
                )
                conn.execute(
                    "UPDATE record_revisions SET state = ? WHERE revision_id = ?",
                    (new_state.value, int(row["revision_id"])),
                )
                records_changed += 1
                if old_state in backlog_values and new_state.value not in backlog_values:
                    state_transitions.append((record_id, old_state))

            canonical_findings = set(canonical_record.get("policy_state", {}).get("findings", []))
            expected_findings = {str(item.type) for item in normalized_findings}
            canonical_stale = canonical_findings != expected_findings

            if (
                str(row["postprocess_payload_json"] or "") != new_payload_json
                or str(row["findings_json"] or "") != new_findings_json
                or canonical_stale
            ):
                new_canonical_json = _sync_canonical_record_diagnostics(canonical_record, normalized_findings)
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET postprocess_payload_json = ?,
                        findings_json = ?,
                        canonical_record_json = ?,
                        state = ?
                    WHERE revision_id = ?
                    """,
                    (new_payload_json, new_findings_json, new_canonical_json, new_state.value, int(row["revision_id"])),
                )
                revisions_changed += 1

        return records_changed, revisions_changed, state_transitions

    def _sync_mapping_pending_for_record(
        self,
        conn: sqlite3.Connection,
        *,
        record_id: str,
        revision_id: int,
        project_code: str,
        payload: Dict[str, Any],
        state: str,
    ) -> None:
        """Phase 2a: maintain single-record mapping_pending backlog.

        Called by ingest after successful upsert, and by backlog reconciler.
        根据 state_requires_mapping_pending(state) 决定插入或解决。
        """
        from peap_core.record_state_policy import state_requires_mapping_pending

        if state_requires_mapping_pending(state):
            existing = conn.execute(
                "SELECT pending_id FROM mapping_pending WHERE record_id = ? AND resolved_at = ''",
                (str(record_id),),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE mapping_pending
                    SET revision_id = ?, project_code = ?, payload_json = ?, created_at = ?
                    WHERE pending_id = ?
                    """,
                    (int(revision_id), str(project_code or ""), _json_dumps(payload), _utcnow(), int(existing["pending_id"])),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO mapping_pending (record_id, revision_id, project_code, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(record_id), int(revision_id), str(project_code or ""), _json_dumps(payload), _utcnow()),
                )
        else:
            conn.execute(
                "UPDATE mapping_pending SET resolved_at = ? WHERE record_id = ? AND resolved_at = ''",
                (_utcnow(), str(record_id)),
            )

    def _reconcile_mapping_pending_backlog(
        self, conn: sqlite3.Connection, *, state_transitions: list[tuple[str, str]] | None = None
    ) -> tuple[int, int]:
        """Phase 2b: bidirectional reconciliation of mapping_pending backlog.

        双向对账:
        - resolve stale: open row exists but record is no longer in BACKLOG_OWNING_STATES
          (handles both DB-state-based resolution AND transitions from phase 1)
        - insert missing: record in BACKLOG_OWNING_STATES without open row

        state_transitions: list of (record_id, old_state) from phase 1 normalization.
        Records that left BACKLOG_OWNING_STATES during normalization get their
        pending entries resolved here even if the DB state was already updated.

        Returns (resolved_count, inserted_count).
        """
        from peap_core.record_state_policy import (
            BACKLOG_OWNING_STATES,
            state_requires_mapping_pending,
        )

        backlog_states = {_s.value for _s in BACKLOG_OWNING_STATES}
        backlog_placeholders = ",".join("?" for _ in backlog_states)

        resolved_count = 0

        # Resolve based on phase-1 state transitions (records that left backlog states)
        if state_transitions:
            for record_id, _ in state_transitions:
                rows = conn.execute(
                    "SELECT pending_id FROM mapping_pending WHERE record_id = ? AND resolved_at = ''",
                    (record_id,),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "UPDATE mapping_pending SET resolved_at = ? WHERE pending_id = ?",
                        (_utcnow(), int(row["pending_id"])),
                    )
                    resolved_count += 1

        # Resolve stale: open row but record's current state is not backlog-owning
        stale_rows = conn.execute(
            f"""
            SELECT mp.pending_id, mp.record_id, r.state
            FROM mapping_pending mp
            JOIN records r ON r.record_id = mp.record_id
            WHERE mp.resolved_at = '' AND r.state NOT IN ({backlog_placeholders})
            """,
            list(backlog_states),
        ).fetchall()
        for row in stale_rows:
            conn.execute(
                "UPDATE mapping_pending SET resolved_at = ? WHERE pending_id = ?",
                (_utcnow(), int(row["pending_id"])),
            )
            resolved_count += 1

        # insert missing
        missing_rows = conn.execute(
            f"""
            SELECT r.record_id, r.latest_revision_id, r.project_code,
                   revisions.postprocess_payload_json, r.state
            FROM records r
            JOIN record_revisions revisions ON revisions.revision_id = r.latest_revision_id
            WHERE r.state IN ({backlog_placeholders})
              AND r.record_id NOT IN (
                  SELECT record_id FROM mapping_pending WHERE resolved_at = ''
              )
            """,
            list(backlog_states),
        ).fetchall()
        inserted_count = 0
        for row in missing_rows:
            if state_requires_mapping_pending(row["state"]):
                conn.execute(
                    """
                    INSERT INTO mapping_pending (record_id, revision_id, project_code, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["record_id"],
                        row["latest_revision_id"],
                        str(row["project_code"] or ""),
                        row["postprocess_payload_json"],
                        _utcnow(),
                    ),
                )
                inserted_count += 1

        return resolved_count, inserted_count

    def normalize_required_mapping_states(self) -> Dict[str, int]:
        """Single-transaction orchestration: phase1 (normalize) + phase2 (reconcile).

        Returns aggregated stats from both phases.
        """
        with self._connect() as conn:
            records_changed, findings_changed, state_transitions = self._normalize_record_payload_and_state(conn)
            resolved_pending, inserted_pending = self._reconcile_mapping_pending_backlog(
                conn, state_transitions=state_transitions
            )

        return {
            "records": records_changed,
            "revisions": findings_changed,
            "pending_inserted": inserted_pending,
            "pending_resolved": resolved_pending,
        }

    def normalize_optional_rule_findings(self, *, rules_config: Dict[str, Any] | None = None) -> Dict[str, int]:
        """Single-transaction orchestration: reapply optional-rule findings + reconcile."""
        if not rules_config:
            return {
                "records": 0,
                "revisions": 0,
                "pending_inserted": 0,
                "pending_resolved": 0,
            }

        with self._connect() as conn:
            records_changed, findings_changed, state_transitions = self._normalize_optional_rule_findings(
                conn,
                rules_config=dict(rules_config),
            )
            resolved_pending, inserted_pending = self._reconcile_mapping_pending_backlog(
                conn,
                state_transitions=state_transitions,
            )

        return {
            "records": records_changed,
            "revisions": findings_changed,
            "pending_inserted": inserted_pending,
            "pending_resolved": resolved_pending,
        }

    def normalize_deal_export_readiness(self) -> Dict[str, int]:
        """Reclassify legacy ready deal records using the canonical export boundary."""
        from peap_core.record_state_policy import classify_record_state

        from .deal_amounts import apply_deal_price_amount_fields
        from .export_projection import append_export_projection_findings
        from .streaming_models import PostProcessFinding

        updated_records = 0
        updated_revisions = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.state,
                    records.project_code,
                    records.source_file,
                    revisions.revision_id,
                    revisions.findings_json,
                    revisions.canonical_record_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.record_family = 'deal'
                  AND records.state IN ('ready', 'field_missing')
                  AND records.last_error_type NOT IN ('source_artifact_invalid', 'source_artifact_missing', 'invalid_source_page', 'superseded_by_record')
                """
            ).fetchall()
            for row in rows:
                raw_findings = _findings_json_loads(row["findings_json"])
                findings = [
                    PostProcessFinding(
                        severity=str(item.get("severity") or "warn"),
                        type=str(item.get("type") or ""),
                        message=str(item.get("message") or ""),
                        evidence=_finding_dict_evidence_payload(item),
                    )
                    for item in _findings_mapping_payloads(raw_findings, field="findings_json")
                    if str(item.get("type") or "").strip() not in {"export_field_missing", "canonical_field_missing"}
                ]
                canonical_record = _json_object_loads(row["canonical_record_json"])
                canonical_fields = canonical_record.get("canonical_fields")
                source_artifact_issue: SourceArtifactIssue | None = None
                if isinstance(canonical_fields, dict):
                    source_text = read_deal_source_artifact_text(
                        source_file=str(row["source_file"] or "").strip(),
                        project_code=str(row["project_code"] or "").strip(),
                        max_chars=512_000,
                    )
                    if isinstance(source_text, SourceArtifactIssue):
                        source_artifact_issue = source_text
                    else:
                        canonical_record["canonical_fields"] = apply_deal_price_amount_fields(
                            canonical_fields,
                            source_html=source_text,
                        )
                if source_artifact_issue is not None:
                    source_artifact_finding = source_artifact_issue_finding(source_artifact_issue)
                    normalized_findings = [
                        PostProcessFinding(
                            severity="error",
                            type=str(source_artifact_finding.get("type") or ""),
                            message=str(source_artifact_finding.get("message") or ""),
                            evidence=_finding_dict_evidence_payload(source_artifact_finding),
                        )
                    ]
                else:
                    normalized_findings = list(append_export_projection_findings(findings, canonical_record))
                new_state = classify_record_state(normalized_findings)
                new_findings_json = _json_dumps([_postprocess_finding_payload(item) for item in normalized_findings])
                new_canonical_json = _sync_canonical_record_diagnostics(canonical_record, normalized_findings)

                if (
                    str(row["state"] or "") != new_state.value
                    or str(row["findings_json"] or "") != new_findings_json
                    or str(row["canonical_record_json"] or "") != new_canonical_json
                ):
                    if source_artifact_issue is None:
                        conn.execute(
                            "UPDATE records SET state = ?, updated_at = ? WHERE record_id = ?",
                            (new_state.value, _utcnow(), str(row["record_id"])),
                        )
                    else:
                        artifact_status = (
                            "missing" if source_artifact_issue.error_type == "source_artifact_missing" else "invalid"
                        )
                        conn.execute(
                            """
                            UPDATE records
                            SET state = ?,
                                last_error_type = ?,
                                last_error_message = ?,
                                artifact_status = ?,
                                updated_at = ?
                            WHERE record_id = ?
                            """,
                            (
                                new_state.value,
                                source_artifact_issue.error_type,
                                source_artifact_issue.message,
                                artifact_status,
                                _utcnow(),
                                str(row["record_id"]),
                            ),
                        )
                    conn.execute(
                        """
                        UPDATE record_revisions
                        SET state = ?,
                            findings_json = ?,
                            canonical_record_json = ?
                        WHERE revision_id = ?
                        """,
                        (new_state.value, new_findings_json, new_canonical_json, int(row["revision_id"])),
                    )
                    updated_revisions += 1
                    if source_artifact_issue is not None or str(row["state"] or "") != new_state.value:
                        updated_records += 1

        return {
            "records": updated_records,
            "revisions": updated_revisions,
        }

    def normalize_export_projection_readiness(self) -> Dict[str, int]:
        """Recompute export-only blockers for ready and field-missing records.

        This sweep is deliberately separate from mapping normalization.  A
        field-missing record may become ready after canonical contract repair,
        while generic postprocess normalization can legitimately classify
        unrelated business or mapping blockers.  Keeping projection recovery
        here prevents those concerns from overwriting one another.
        """
        from peap_core.record_state_policy import classify_record_state

        updated_records = 0
        updated_revisions = 0
        state_transitions: list[tuple[str, str]] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    records.record_id,
                    records.state,
                    revisions.revision_id,
                    revisions.findings_json,
                    revisions.canonical_record_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.state IN ('ready', 'field_missing')
                  AND {_maintenance_terminal_error_filter_sql()}
                """
            ).fetchall()
            for row in rows:
                old_state = str(row["state"] or "")
                raw_findings = _findings_json_loads(row["findings_json"])
                findings = [
                    PostProcessFinding(
                        severity=str(item.get("severity") or "warn"),
                        type=str(item.get("type") or ""),
                        message=str(item.get("message") or ""),
                        evidence=_finding_dict_evidence_payload(item),
                    )
                    for item in _findings_mapping_payloads(
                        raw_findings,
                        field="findings_json",
                    )
                ]
                canonical_record = _json_object_loads(row["canonical_record_json"])
                normalized_findings = _refresh_export_projection_findings(
                    findings,
                    canonical_record,
                )
                new_state = classify_record_state(normalized_findings)
                new_findings_json = _json_dumps(
                    [_postprocess_finding_payload(item) for item in normalized_findings]
                )
                new_canonical_json = _sync_canonical_record_diagnostics(
                    canonical_record,
                    normalized_findings,
                )
                state_changed = old_state != new_state.value
                revision_changed = (
                    str(row["findings_json"] or "") != new_findings_json
                    or str(row["canonical_record_json"] or "") != new_canonical_json
                    or state_changed
                )
                if state_changed:
                    conn.execute(
                        "UPDATE records SET state = ?, updated_at = ? WHERE record_id = ?",
                        (new_state.value, _utcnow(), str(row["record_id"])),
                    )
                    updated_records += 1
                    state_transitions.append((str(row["record_id"]), old_state))
                if revision_changed:
                    conn.execute(
                        """
                        UPDATE record_revisions
                        SET state = ?,
                            findings_json = ?,
                            canonical_record_json = ?
                        WHERE revision_id = ?
                        """,
                        (
                            new_state.value,
                            new_findings_json,
                            new_canonical_json,
                            int(row["revision_id"]),
                        ),
                    )
                    updated_revisions += 1

            resolved_pending, inserted_pending = self._reconcile_mapping_pending_backlog(
                conn,
                state_transitions=state_transitions,
            )

        return {
            "records": updated_records,
            "revisions": updated_revisions,
            "pending_inserted": inserted_pending,
            "pending_resolved": resolved_pending,
        }

    def sync_mapping_pending_for_record(
        self,
        *,
        record_id: str,
        revision_id: int,
        project_code: str,
        payload: Dict[str, Any],
        state: str,
    ) -> None:
        """Single synchronous entry point for mapping_pending sync.

        Called by ingest after successful upsert.
        """
        with self._connect() as conn:
            self._sync_mapping_pending_for_record(
                conn,
                record_id=record_id,
                revision_id=revision_id,
                project_code=project_code,
                payload=payload,
                state=state,
            )

    def set_setting(self, key: str, value: Dict[str, Any]) -> None:
        now = _utcnow()
        resolved_key = _required_text(key, field="key")
        payload = _object_payload(value, field="value")
        encoded = _json_dumps(payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (resolved_key, encoded, now),
            )
            conn.execute(
                "INSERT INTO settings_revisions (key, value_json, updated_at) VALUES (?, ?, ?)",
                (resolved_key, encoded, now),
            )

    def get_setting(self, key: str, *, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        fallback = {} if default is None else _object_payload(default, field="default")
        with self._connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return fallback
        payload = _json_loads(row["value_json"], default=None)
        if isinstance(payload, dict):
            return payload
        fallback[_SETTING_DECODE_ERROR_KEY] = "invalid_json"
        return fallback

    def upsert_mapping_entry(
        self,
        *,
        company_name: str,
        group_name: str = "",
        source_type: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        normalized = str(company_name or "").strip()
        if not normalized:
            raise ValueError("company_name is empty")
        metadata_payload = {} if metadata is None else _object_payload(metadata, field="metadata")
        match_field = str(metadata_payload.get("match_field") or "transferor").strip() or "transferor"
        target_field = str(
            metadata_payload.get("target_field") or ("group_name" if str(group_name or "").strip() else "source_type")
        ).strip() or "group_name"
        metadata_payload["match_field"] = match_field
        metadata_payload["target_field"] = target_field
        entry_key = "|".join([match_field, target_field, normalized.lower()])
        entry_id = hashlib.sha1(entry_key.encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mapping_entries (
                    entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    group_name = excluded.group_name,
                    source_type = excluded.source_type,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    entry_id,
                    normalized,
                    str(group_name or "").strip(),
                    str(source_type or "").strip(),
                    _json_dumps(metadata_payload),
                    now,
                    now,
                ),
            )
        return entry_id

    def replace_mapping_entry(
        self,
        *,
        entry_id: str,
        company_name: str,
        group_name: str = "",
        source_type: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        normalized_entry_id = str(entry_id or "").strip()
        if not normalized_entry_id:
            raise ValueError("entry_id is empty")
        normalized = str(company_name or "").strip()
        if not normalized:
            raise ValueError("company_name is empty")
        metadata_payload = {} if metadata is None else _object_payload(metadata, field="metadata")
        match_field = str(metadata_payload.get("match_field") or "transferor").strip() or "transferor"
        target_field = str(
            metadata_payload.get("target_field") or ("group_name" if str(group_name or "").strip() else "source_type")
        ).strip() or "group_name"
        metadata_payload["match_field"] = match_field
        metadata_payload["target_field"] = target_field
        entry_key = "|".join([match_field, target_field, normalized.lower()])
        replacement_entry_id = hashlib.sha1(entry_key.encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mapping_entries (
                    entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    group_name = excluded.group_name,
                    source_type = excluded.source_type,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    replacement_entry_id,
                    normalized,
                    str(group_name or "").strip(),
                    str(source_type or "").strip(),
                    _json_dumps(metadata_payload),
                    now,
                    now,
                ),
            )
            if replacement_entry_id != normalized_entry_id:
                cursor = conn.execute(
                    "DELETE FROM mapping_entries WHERE entry_id = ?",
                    (normalized_entry_id,),
                )
                if int(cursor.rowcount or 0) <= 0:
                    raise KeyError(normalized_entry_id)
        return replacement_entry_id

    def list_mapping_entries(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                FROM mapping_entries
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "entry_id": row["entry_id"],
                    "company_name": row["company_name"],
                    "group_name": row["group_name"],
                    "source_type": row["source_type"],
                    "metadata": _mapping_metadata_json_loads(row["metadata_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def get_mapping_entry(self, *, entry_id: str) -> Dict[str, Any]:
        normalized_entry_id = _required_text(entry_id, field="entry_id")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                FROM mapping_entries
                WHERE entry_id = ?
                LIMIT 1
                """,
                (normalized_entry_id,),
            ).fetchone()
        if row is None:
            raise KeyError(normalized_entry_id)
        return {
            "entry_id": row["entry_id"],
            "company_name": row["company_name"],
            "group_name": row["group_name"],
            "source_type": row["source_type"],
            "metadata": _mapping_metadata_json_loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_mapping_entry(self, *, entry_id: str) -> bool:
        normalized_entry_id = _required_text(entry_id, field="entry_id")
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM mapping_entries WHERE entry_id = ?",
                (normalized_entry_id,),
            )
        return int(cursor.rowcount or 0) > 0

    def mark_mapping_pending(
        self,
        *,
        record_id: str,
        revision_id: int,
        project_code: str,
        payload: Dict[str, Any],
    ) -> None:
        payload_object = _object_payload(payload, field="payload")
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT pending_id
                FROM mapping_pending
                WHERE record_id = ? AND resolved_at = ''
                ORDER BY pending_id DESC
                LIMIT 1
                """,
                (str(record_id),),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE mapping_pending
                    SET revision_id = ?,
                        project_code = ?,
                        payload_json = ?,
                        created_at = ?
                    WHERE pending_id = ?
                    """,
                    (
                        int(revision_id),
                        str(project_code or ""),
                        _json_dumps(payload_object),
                        _utcnow(),
                        int(existing["pending_id"]),
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO mapping_pending (
                    record_id, revision_id, project_code, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, int(revision_id), str(project_code or ""), _json_dumps(payload_object), _utcnow()),
            )

    def resolve_mapping_pending(self, record_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mapping_pending
                SET resolved_at = ?
                WHERE record_id = ? AND resolved_at = ''
                """,
                (_utcnow(), record_id),
            )

    def upsert_record(
        self,
        record: IngestedRecord,
        *,
        preserve_operational_overlay: bool = False,
        _connection: sqlite3.Connection | None = None,
    ) -> Dict[str, Any]:
        record_family = _canonical_record_family(record.record_family)
        now = _utcnow()
        listing_date = _normalize_date_text(record.listing_date)
        findings_json = [_postprocess_finding_payload(item) for item in record.findings]
        provisional_record_id = record.record_id or uuid.uuid4().hex
        record = _bind_ingested_record_identity(record, provisional_record_id)
        source_identity_payload = _mapping_payload(record.source_identity, field="source_identity")
        record = replace(record, source_identity=source_identity_payload)
        source_scope_id = _resolve_scope_source_id(
            source_identity=source_identity_payload,
            exchange=record.exchange,
        )
        business_id, raw_business_label = _resolve_business_kernel_fields(
            record_family=record.record_family,
            project_type=record.project_type,
            canonical_record=record.canonical_record,
        )
        business_key = _scoped_record_business_key(
            project_code=record.project_code,
            source_file=record.source_file,
            record_family=record_family,
            business_id=business_id,
            source_id=source_scope_id,
        )
        connection_context = self._connect() if _connection is None else nullcontext(_connection)
        with connection_context as conn:
            existing = conn.execute(
                """
                SELECT record_id, latest_revision_id,
                       artifact_status, last_operation_kind, last_operation_code,
                       last_operation_message, last_operation_at,
                       business_key, record_family, business_id, exchange,
                       source_identity_json, acknowledged_payload_json
                FROM records
                WHERE business_key = ?
                """,
                (business_key,),
            ).fetchone()
            if existing is None:
                legacy_keys = _legacy_record_business_keys(record.project_code, record.source_file)
                if legacy_keys:
                    failed_states = tuple(sorted(FAILED_RECORD_STATES))
                    failed_clause = ""
                    params: list[Any] = list(legacy_keys)
                    if failed_states:
                        failed_clause = f"AND state NOT IN ({','.join('?' for _ in failed_states)})"
                        params.extend(failed_states)
                    legacy_rows = conn.execute(
                        f"""
                        SELECT record_id, latest_revision_id,
                               artifact_status, last_operation_kind, last_operation_code,
                               last_operation_message, last_operation_at,
                               business_key, record_family, business_id, exchange,
                               source_identity_json, acknowledged_payload_json
                        FROM records
                        WHERE business_key IN ({','.join('?' for _ in legacy_keys)})
                          {failed_clause}
                        ORDER BY updated_at DESC, created_at DESC
                        """,
                        params,
                    ).fetchall()
                    for row in legacy_rows:
                        row_source_identity = _source_identity_json_loads(row["source_identity_json"])
                        if not isinstance(row_source_identity, dict):
                            row_source_identity = {}
                        row_source_id = _resolve_scope_source_id(
                            source_identity=row_source_identity,
                            exchange=row["exchange"],
                        )
                        if _scope_is_compatible_for_upgrade(
                            record_family=row["record_family"],
                            business_id=row["business_id"],
                            source_id=row_source_id,
                            expected_family=record_family,
                            expected_business_id=business_id,
                            expected_source_id=source_scope_id,
                        ):
                            existing = row
                            break
            if existing is None:
                record_id_row = conn.execute(
                    """
                    SELECT record_id, latest_revision_id,
                           artifact_status, last_operation_kind, last_operation_code,
                           last_operation_message, last_operation_at,
                           business_key, record_family, business_id, exchange,
                           source_identity_json, acknowledged_payload_json
                    FROM records
                    WHERE record_id = ?
                    """,
                    (str(provisional_record_id),),
                ).fetchone()
                if record_id_row is not None:
                    row_source_identity = _source_identity_json_loads(record_id_row["source_identity_json"])
                    if not isinstance(row_source_identity, dict):
                        row_source_identity = {}
                    row_source_id = _resolve_scope_source_id(
                        source_identity=row_source_identity,
                        exchange=record_id_row["exchange"],
                    )
                    if _scope_is_compatible_for_upgrade(
                        record_family=record_id_row["record_family"],
                        business_id=record_id_row["business_id"],
                        source_id=row_source_id,
                        expected_family=record_family,
                        expected_business_id=business_id,
                        expected_source_id=source_scope_id,
                    ):
                        existing = record_id_row
                    else:
                        provisional_record_id = uuid.uuid4().hex
            record_id = existing["record_id"] if existing is not None else provisional_record_id
            if record.record_id != record_id:
                record = _bind_ingested_record_identity(record, record_id)
                source_identity_payload = _mapping_payload(record.source_identity, field="source_identity")
                record = replace(record, source_identity=source_identity_payload)
                source_scope_id = _resolve_scope_source_id(
                    source_identity=source_identity_payload,
                    exchange=record.exchange,
                )
                business_id, raw_business_label = _resolve_business_kernel_fields(
                    record_family=record.record_family,
                    project_type=record.project_type,
                    canonical_record=record.canonical_record,
                )
                business_key = _scoped_record_business_key(
                    project_code=record.project_code,
                    source_file=record.source_file,
                    record_family=record_family,
                    business_id=business_id,
                    source_id=source_scope_id,
                )
            if existing is not None:
                record = _reconcile_conflict_provenance(
                    record=record,
                    existing_source_identity=_source_identity_json_loads(existing["source_identity_json"]),
                )
                source_identity_payload = _mapping_payload(record.source_identity, field="source_identity")
            business_id, raw_business_label = _resolve_business_kernel_fields(
                record_family=record.record_family,
                project_type=record.project_type,
                canonical_record=record.canonical_record,
            )
            canonical_projection = record.canonical_projection
            parser_payload_json = _json_dumps(record.parser_payload)
            postprocess_payload_json = _json_dumps(record.postprocess_payload)
            canonical_record_json = _json_dumps(record.canonical_record)
            canonical_projection_json = _json_dumps(canonical_projection)
            findings_payload_json = _json_dumps(findings_json)
            latest_revision_id = existing["latest_revision_id"] if existing is not None else None
            if preserve_operational_overlay and existing is not None:
                artifact_status = str(existing["artifact_status"] or "unknown")
                last_operation_kind = str(existing["last_operation_kind"] or "")
                last_operation_code = str(existing["last_operation_code"] or "")
                last_operation_message = str(existing["last_operation_message"] or "")
                last_operation_at = str(existing["last_operation_at"] or "")
                acknowledged_payload_json = str(existing["acknowledged_payload_json"] or "{}")
            else:
                artifact_status = "ok"
                last_operation_kind = ""
                last_operation_code = ""
                last_operation_message = ""
                last_operation_at = ""
                acknowledged_payload_json = "{}"
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id, business_key, record_family, business_id, raw_business_label,
                        identity_anchor, source_identity_json,
                    project_code, project_name, project_type,
                    exchange, listing_date, state, source_file, archive_path,
                    latest_revision_id, last_error_type, last_error_message,
                    artifact_status, last_operation_kind, last_operation_code,
                    last_operation_message, last_operation_at, acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                        business_key,
                        record_family,
                        business_id,
                        raw_business_label,
                        build_identity_anchor(record_state=record.state, source_identity=record.source_identity)
                        if record.source_identity
                        else "",
                        _json_dumps(record.source_identity),
                        record.project_code,
                        record.project_name,
                        record.project_type,
                        record.exchange,
                        listing_date,
                        record.state,
                        record.source_file,
                        record.archive_path,
                        artifact_status,
                        last_operation_kind,
                        last_operation_code,
                        last_operation_message,
                        last_operation_at,
                        acknowledged_payload_json,
                        now,
                        now,
                    ),
                )

            revision_row = None
            if latest_revision_id is not None:
                revision_row = conn.execute(
                    """
                    SELECT revision_id, revision_hash, parser_payload_json,
                           postprocess_payload_json, canonical_record_json,
                           canonical_projection_json, source_file
                    FROM record_revisions
                    WHERE revision_id = ?
                    """,
                    (latest_revision_id,),
                ).fetchone()
            changed = revision_row is None or revision_row["revision_hash"] != record.revision_hash
            if not changed and revision_row is not None:
                changed = any(
                    [
                        revision_row["parser_payload_json"] != parser_payload_json,
                        revision_row["postprocess_payload_json"] != postprocess_payload_json,
                        revision_row["canonical_record_json"] != canonical_record_json,
                        revision_row["canonical_projection_json"] != canonical_projection_json,
                        revision_row["source_file"] != record.source_file,
                    ]
                )
            revision_id = latest_revision_id
            if changed:
                cur = conn.execute(
                    """
                    INSERT INTO record_revisions (
                        record_id, revision_hash, parser_payload_json,
                        postprocess_payload_json, canonical_record_json, canonical_projection_json,
                        findings_json, state, source_file, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        record.revision_hash,
                        parser_payload_json,
                        postprocess_payload_json,
                        canonical_record_json,
                        canonical_projection_json,
                        findings_payload_json,
                        record.state,
                        record.source_file,
                        now,
                    ),
                )
                revision_id = int(cur.lastrowid)
            elif revision_id is not None:
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET findings_json = ?,
                        state = ?,
                        source_file = ?
                    WHERE revision_id = ?
                    """,
                    (
                        findings_payload_json,
                        record.state,
                        record.source_file,
                        int(revision_id),
                    ),
                )

            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, business_id, raw_business_label,
                    identity_anchor, source_identity_json,
                    project_code, project_name, project_type,
                    exchange, listing_date, state, source_file, archive_path,
                    latest_revision_id, last_error_type, last_error_message,
                    artifact_status, last_operation_kind, last_operation_code,
                    last_operation_message, last_operation_at,
                    acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    business_key = excluded.business_key,
                    record_family = excluded.record_family,
                    business_id = excluded.business_id,
                    raw_business_label = excluded.raw_business_label,
                    identity_anchor = excluded.identity_anchor,
                    source_identity_json = excluded.source_identity_json,
                    project_code = excluded.project_code,
                    project_name = excluded.project_name,
                    project_type = excluded.project_type,
                    exchange = excluded.exchange,
                    listing_date = excluded.listing_date,
                    state = excluded.state,
                    source_file = excluded.source_file,
                    archive_path = excluded.archive_path,
                    latest_revision_id = excluded.latest_revision_id,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    artifact_status = excluded.artifact_status,
                    last_operation_kind = excluded.last_operation_kind,
                    last_operation_code = excluded.last_operation_code,
                    last_operation_message = excluded.last_operation_message,
                    last_operation_at = excluded.last_operation_at,
                    acknowledged_payload_json = excluded.acknowledged_payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    business_key,
                    record_family,
                    business_id,
                    raw_business_label,
                    build_identity_anchor(record_state=record.state, source_identity=record.source_identity)
                    if record.source_identity
                    else "",
                    _json_dumps(record.source_identity),
                    record.project_code,
                    record.project_name,
                    record.project_type,
                    record.exchange,
                    listing_date,
                    record.state,
                    record.source_file,
                    record.archive_path,
                    revision_id,
                    "",
                    "",
                    artifact_status,
                    last_operation_kind,
                    last_operation_code,
                    last_operation_message,
                    last_operation_at,
                    acknowledged_payload_json,
                    now,
                    now,
                ),
            )
        return {
            "record_id": record_id,
            "revision_id": revision_id,
            "changed": changed,
            "business_key": business_key,
        }

    def upsert_record_with_mapping_pending(
        self,
        record: IngestedRecord,
        *,
        preserve_operational_overlay: bool = False,
        _connection: sqlite3.Connection | None = None,
    ) -> Dict[str, Any]:
        """Persist a record revision and its mapping backlog in one transaction."""
        mapping_payload = _mapping_payload(
            record.postprocess_payload,
            field="postprocess_payload",
        )
        connection_context = self._connect() if _connection is None else nullcontext(_connection)
        with connection_context as conn:
            stored = self.upsert_record(
                record,
                preserve_operational_overlay=preserve_operational_overlay,
                _connection=conn,
            )
            self._sync_mapping_pending_for_record(
                conn,
                record_id=str(stored["record_id"]),
                revision_id=int(stored["revision_id"]),
                project_code=record.project_code,
                payload=mapping_payload,
                state=record.state,
            )
        return stored

    def upsert_failed_record(
        self,
        *,
        project_code: str,
        source_file: str,
        state: str,
        error_type: str,
        error_message: str,
        payload: Dict[str, Any] | None = None,
        severity: str = "error",
        _connection: sqlite3.Connection | None = None,
    ) -> Dict[str, Any]:
        payload_object = _mapping_payload(payload, field="payload", allow_none=True)
        source_identity = _build_failed_source_identity(
            project_code=project_code,
            source_file=source_file,
            state=state,
            payload=payload_object,
        )
        identity_anchor = build_identity_anchor(record_state=state, source_identity=source_identity)
        business_key = f"failed:{identity_anchor}"
        now = _utcnow()
        connection_context = self._connect() if _connection is None else nullcontext(_connection)
        with connection_context as conn:
            existing = conn.execute(
                """
                SELECT record_id, latest_revision_id, identity_anchor, source_identity_json, record_family
                FROM records
                WHERE business_key = ?
                """,
                (business_key,),
            ).fetchone()
            record_id = existing["record_id"] if existing is not None else uuid.uuid4().hex
            stored_identity_anchor = _first_non_empty(existing["identity_anchor"] if existing is not None else "", identity_anchor)
            stored_source_identity = source_identity
            stored_record_family = _first_non_empty(
                existing["record_family"] if existing is not None else "",
                source_identity.get("record_family"),
                "listing",
            )
            stored_business_id = _first_non_empty(
                source_identity.get("business_id"),
                source_identity.get("business_id_hint"),
            )
            stored_raw_business_label = _first_non_empty(
                source_identity.get("business_label_hint"),
                source_identity.get("project_type_fallback"),
            )
            stored_exchange = _first_non_empty(source_identity.get("exchange"))
            stored_listing_date = _first_non_empty(source_identity.get("listing_date"))
            if existing is not None:
                existing_source_identity = _source_identity_json_loads(existing["source_identity_json"])
                if isinstance(existing_source_identity, dict) and existing_source_identity:
                    stored_source_identity = _merge_source_identity(existing_source_identity, source_identity)
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id, business_key, record_family, business_id, raw_business_label,
                        identity_anchor, source_identity_json,
                        project_code, project_name, project_type, exchange, listing_date,
                        state, source_file, archive_path, latest_revision_id,
                        last_error_type, last_error_message,
                        artifact_status, last_operation_kind, last_operation_code,
                        last_operation_message, last_operation_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'unknown', '', '', '', '', ?, ?)
                    """,
                    (
                        record_id,
                        business_key,
                        stored_record_family,
                        stored_business_id,
                        stored_raw_business_label,
                        stored_identity_anchor,
                        _json_dumps(stored_source_identity),
                        str(project_code or ""),
                        "",
                        "",
                        stored_exchange,
                        stored_listing_date,
                        state,
                        source_file,
                        "",
                        error_type,
                        error_message,
                        now,
                        now,
                    ),
                )
            cur = conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    hashlib.sha1(f"{state}|{source_file}|{now}".encode("utf-8")).hexdigest(),
                    _json_dumps(payload_object),
                    _json_dumps({}),
                    _json_dumps([{"severity": str(severity or "error"), "type": error_type, "message": error_message}]),
                    state,
                    source_file,
                    now,
                ),
            )
            revision_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, business_id, raw_business_label,
                    identity_anchor, source_identity_json,
                        project_code, project_name, project_type, exchange, listing_date,
                        state, source_file, archive_path, latest_revision_id,
                        last_error_type, last_error_message,
                        artifact_status, last_operation_kind, last_operation_code,
                        last_operation_message, last_operation_at,
                        created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    project_code = excluded.project_code,
                    state = excluded.state,
                    source_file = excluded.source_file,
                    latest_revision_id = excluded.latest_revision_id,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    artifact_status = excluded.artifact_status,
                    last_operation_kind = excluded.last_operation_kind,
                    last_operation_code = excluded.last_operation_code,
                    last_operation_message = excluded.last_operation_message,
                    last_operation_at = excluded.last_operation_at,
                    record_family = CASE
                        WHEN record_family = '' THEN excluded.record_family
                        ELSE record_family
                    END,
                    business_id = CASE
                        WHEN business_id = '' THEN excluded.business_id
                        ELSE business_id
                    END,
                    raw_business_label = CASE
                        WHEN raw_business_label = '' THEN excluded.raw_business_label
                        ELSE raw_business_label
                    END,
                    identity_anchor = CASE
                        WHEN identity_anchor = '' THEN excluded.identity_anchor
                        ELSE identity_anchor
                    END,
                    source_identity_json = excluded.source_identity_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    business_key,
                    stored_record_family,
                    stored_business_id,
                    stored_raw_business_label,
                    stored_identity_anchor,
                    _json_dumps(stored_source_identity),
                    str(project_code or ""),
                    "",
                    "",
                    stored_exchange,
                    stored_listing_date,
                    state,
                    source_file,
                    "",
                    revision_id,
                    error_type,
                    error_message,
                    "unknown",
                    "",
                    "",
                    "",
                    "",
                    now,
                    now,
                ),
            )
        return {
            "record_id": record_id,
            "revision_id": revision_id,
            "business_key": business_key,
            "identity_anchor": stored_identity_anchor,
        }

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return {
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "downloaded_count": int(row["downloaded_count"]),
            "persisted_count": int(row["persisted_count"]),
            "exception_count": int(row["exception_count"]),
            "metadata": _job_metadata_json_loads(row["metadata_json"]),
            "summary": _job_summary_json_loads(row["summary_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_jobs(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "job_type": row["job_type"],
                "status": row["status"],
                "downloaded_count": int(row["downloaded_count"]),
                "persisted_count": int(row["persisted_count"]),
                "exception_count": int(row["exception_count"]),
                "metadata": _job_metadata_json_loads(row["metadata_json"]),
                "summary": _job_summary_json_loads(row["summary_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_job_events(self, job_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        self.get_job(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_ts, stage, status, project_code, archive_path,
                       error_type, error_message, payload_json
                FROM job_events
                WHERE job_id = ?
                ORDER BY event_id DESC
                LIMIT ?
                """,
                (job_id, int(limit)),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_ts": row["event_ts"],
                "stage": row["stage"],
                "status": row["status"],
                "project_code": row["project_code"],
                "archive_path": row["archive_path"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
                "payload": _job_event_payload_json_loads(row["payload_json"]),
            }
            for row in rows
        ]

    def get_job_event_counts(self, job_id: str) -> Dict[str, int]:
        self.get_job(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c
                FROM job_events
                WHERE job_id = ?
                GROUP BY status
                """,
                (job_id,),
            ).fetchall()
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM job_events
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        counts = {str(row["status"]): int(row["c"]) for row in rows}
        counts["total_count"] = int(total_row["c"]) if total_row is not None else 0
        return counts

    def count_pending_mappings(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT record_id) AS c FROM mapping_pending WHERE resolved_at = ''"
            ).fetchone()
        return int(row["c"]) if row is not None else 0

    def build_maintenance_artifact_evidence_manifest(self) -> Dict[str, Any]:
        records = self.iter_latest_records(sort="recent")
        evidence_counts: Dict[str, int] = {}
        manifest_records: list[Dict[str, Any]] = []
        source_evidence_missing = 0
        required_field_missing = 0
        for record in records:
            verdict = resolve_artifact_evidence_verdict(record)
            evidence_counts[verdict.status] = evidence_counts.get(verdict.status, 0) + 1
            state = str(record.get("state") or "").strip()
            is_terminal_failed = state in FAILED_RECORD_STATES
            classification, _reason_code = classify_artifact_evidence_verdict(record, verdict)
            if state == "field_missing":
                required_field_missing += 1

            if is_terminal_failed:
                maintenance_status = state
            elif classification in ARTIFACT_EVIDENCE_REPORT_CLASSIFICATIONS:
                maintenance_status = f"source_evidence_{classification}"
                source_evidence_missing += 1
            elif state == "field_missing":
                maintenance_status = "required_field_missing"
            elif state == "skipped":
                maintenance_status = "skipped"
            else:
                maintenance_status = "ready"
            manifest_records.append(
                {
                    "record_id": str(record.get("record_id") or ""),
                    "state": state,
                    "maintenance_status": maintenance_status,
                    "evidence_verdict": {
                        "status": verdict.status,
                        "reason_code": verdict.reason_code,
                        "logical_record_identity": verdict.logical_record_identity,
                        "identity_confidence": verdict.identity_confidence,
                        "authoritative_path": verdict.authoritative_path,
                        "inspection_openable_path": verdict.inspection_openable_path,
                        "safe_evidence": _evidence_mapping_payload(
                            verdict.safe_evidence,
                            field="safe_evidence",
                        ),
                    },
                }
            )
        return {
            "artifact_evidence": evidence_counts,
            "source_evidence_missing": {"records": source_evidence_missing},
            "required_field_missing": {"records": required_field_missing},
            "records": manifest_records,
        }

    def list_existing_project_codes(
        self,
        *,
        states: Iterable[str] | None = None,
        record_family: str | None = None,
        business_id: str | None = None,
        source_id: str | None = None,
        include_scoped_keys: bool = False,
        require_existing_artifact: bool = False,
    ) -> set[str]:
        clauses = ["project_code <> ''"]
        params: list[Any] = []
        if states:
            items = [str(item) for item in states if str(item or "").strip()]
            if items:
                clauses.append(f"state IN ({','.join('?' for _ in items)})")
                params.extend(items)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT project_code, state, record_family, business_id, exchange,
                       source_file, archive_path, source_identity_json, last_error_type
                FROM records
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        result: set[str] = set()
        for row in rows:
            project_code = str(row["project_code"] or "").strip().upper()
            if not project_code:
                continue
            source_identity = _source_identity_json_loads(row["source_identity_json"])
            if not isinstance(source_identity, dict):
                source_identity = {}
            row_source_id = _resolve_scope_source_id(
                source_identity=source_identity,
                exchange=row["exchange"],
            )
            if not _scope_matches(
                record_family=row["record_family"],
                business_id=row["business_id"],
                source_id=row_source_id,
                expected_family=record_family,
                expected_business_id=business_id,
                expected_source_id=source_id,
            ):
                continue
            if require_existing_artifact and not _can_use_record_for_existing_download_dedup(row):
                continue
            result.add(project_code)
            if include_scoped_keys:
                scoped_key = _scoped_identity_token(
                    "project_code",
                    project_code,
                    record_family=row["record_family"],
                    business_id=row["business_id"],
                    source_id=row_source_id,
                )
                if scoped_key:
                    result.add(scoped_key)
        return result

    def list_existing_candidate_tokens(
        self,
        *,
        states: Iterable[str] | None = None,
        record_family: str | None = None,
        business_id: str | None = None,
        source_id: str | None = None,
        include_scoped_tokens: bool = False,
        require_existing_artifact: bool = False,
    ) -> set[str]:
        tokens: set[str] = set()
        allowed_project_codes: set[str] | None = None
        allowed_source_files: set[str] | None = None
        state_items = [str(item) for item in states if str(item or "").strip()] if states else []
        record_clauses = ["1=1"]
        record_params: list[Any] = []
        if state_items:
            record_clauses.append(f"records.state IN ({','.join('?' for _ in state_items)})")
            record_params.extend(state_items)
        with self._connect() as conn:
            record_rows = conn.execute(
                f"""
                SELECT
                    records.project_code,
                    records.state,
                    records.record_family,
                    records.business_id,
                    records.exchange,
                    records.source_file,
                    records.archive_path,
                    records.last_error_type,
                    records.source_identity_json,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json
                FROM records
                LEFT JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE {' AND '.join(record_clauses)}
                """,
                record_params,
            ).fetchall()
        filtered_record_rows: list[sqlite3.Row] = []
        for row in record_rows:
            source_identity = _source_identity_json_loads(row["source_identity_json"])
            if not isinstance(source_identity, dict):
                source_identity = {}
            row_source_id = _resolve_scope_source_id(
                source_identity=source_identity,
                exchange=row["exchange"],
            )
            if not _scope_matches(
                record_family=row["record_family"],
                business_id=row["business_id"],
                source_id=row_source_id,
                expected_family=record_family,
                expected_business_id=business_id,
                expected_source_id=source_id,
            ):
                continue
            if require_existing_artifact and not _can_use_record_for_existing_download_dedup(row):
                continue
            filtered_record_rows.append(row)

        if state_items:
            allowed_project_codes = {
                str(row["project_code"] or "").strip().upper()
                for row in filtered_record_rows
                if str(row["project_code"] or "").strip()
            }
            allowed_source_files = {
                str(row["source_file"] or "").strip()
                for row in filtered_record_rows
                if str(row["source_file"] or "").strip()
            }
            for row in filtered_record_rows:
                source_identity = _source_identity_json_loads(row["source_identity_json"])
                if not isinstance(source_identity, dict):
                    continue
                original_evidence_path = str(source_identity.get("original_evidence_path") or "").strip()
                original_source_file = str(source_identity.get("original_source_file") or "").strip()
                if original_evidence_path:
                    allowed_source_files.add(original_evidence_path)
                if original_source_file:
                    allowed_source_files.add(original_source_file)
        for row in filtered_record_rows:
            project_code = str(row["project_code"] or "").strip().upper()
            if project_code:
                token = _candidate_identity_token("project_code", project_code)
                if token:
                    tokens.add(token)
            parser_payload = _payload_json_loads(row["parser_payload_json"])
            postprocess_payload = _payload_json_loads(row["postprocess_payload_json"])
            source_identity = _source_identity_json_loads(row["source_identity_json"])
            if not isinstance(source_identity, dict):
                source_identity = {}
            row_source_id = _resolve_scope_source_id(
                source_identity=source_identity,
                exchange=row["exchange"],
            )
            if include_scoped_tokens and project_code:
                scoped_project_token = _scoped_identity_token(
                    "project_code",
                    project_code,
                    record_family=row["record_family"],
                    business_id=row["business_id"],
                    source_id=row_source_id,
                )
                if scoped_project_token:
                    tokens.add(scoped_project_token)
            for kind in ("project_id", "page_url"):
                value = _record_identity_value(postprocess_payload, parser_payload, key=kind)
                token = _candidate_identity_token(
                    kind,
                    value,
                )
                if token:
                    tokens.add(token)
                if include_scoped_tokens:
                    scoped_token = _scoped_identity_token(
                        kind,
                        value,
                        record_family=row["record_family"],
                        business_id=row["business_id"],
                        source_id=row_source_id,
                    )
                    if scoped_token:
                        tokens.add(scoped_token)
            for normalized_token in _unique_text_sequence_payloads(
                (source_identity.get("candidate_tokens"), "source_identity.candidate_tokens")
            ):
                if not normalized_token:
                    continue
                tokens.add(normalized_token)
                if not include_scoped_tokens:
                    continue
                token_kind, token_value = _split_identity_token(normalized_token)
                scoped_token = _scoped_identity_token(
                    token_kind,
                    token_value,
                    record_family=row["record_family"],
                    business_id=row["business_id"],
                    source_id=row_source_id,
                )
                if scoped_token:
                    tokens.add(scoped_token)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM job_events
                WHERE stage = 'downloaded'
                """
            ).fetchall()
        for row in rows:
            payload = _job_event_payload_json_loads(row["payload_json"])
            if not isinstance(payload, dict):
                continue
            if require_existing_artifact and not _has_verified_artifact_evidence(payload):
                continue
            payload_source_id = _resolve_scope_source_id(
                source_identity={
                    "source_id": payload.get("source_id"),
                    "exchange": payload.get("exchange"),
                },
                exchange="",
            )
            if not _scope_matches(
                record_family=payload.get("record_family"),
                business_id=payload.get("business_id"),
                source_id=payload_source_id,
                expected_family=record_family,
                expected_business_id=business_id,
                expected_source_id=source_id,
            ):
                continue
            if allowed_project_codes is not None and allowed_source_files is not None:
                project_code = str(payload.get("project_code") or "").strip().upper()
                source_file = str(payload.get("source_file") or "").strip()
                if project_code:
                    if project_code not in allowed_project_codes and (
                        not source_file or source_file not in allowed_source_files
                    ):
                        continue
                elif not source_file or source_file not in allowed_source_files:
                    continue
            for kind in ("project_code", "project_id", "page_url"):
                token = _candidate_identity_token(kind, payload.get(kind))
                if token:
                    tokens.add(token)
                if include_scoped_tokens:
                    scoped_token = _scoped_identity_token(
                        kind,
                        payload.get(kind),
                        record_family=payload.get("record_family"),
                        business_id=payload.get("business_id"),
                        source_id=payload_source_id,
                    )
                    if scoped_token:
                        tokens.add(scoped_token)
        return tokens

    def count_records_by_state(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        business_types: Iterable[str] | None = None,
        record_family: str | None = None,
        include_failed: bool = False,
    ) -> Dict[str, int]:
        clauses = ["1=1"]
        params: List[Any] = []
        normalized_column = (
            "replace(replace(replace(replace(replace(records.listing_date, '年', '-'), '月', '-'), '日', ''), '/', '-'), '.', '-')"
        )
        normalized_from = _normalize_date_text(date_from or "")
        normalized_to = _normalize_date_text(date_to or "")
        if normalized_from:
            clauses.append(f"{normalized_column} >= ?")
            params.append(normalized_from)
        if normalized_to:
            clauses.append(f"{normalized_column} <= ?")
            params.append(normalized_to)
        if business_types:
            items = [str(item) for item in business_types if str(item or "").strip()]
            if items:
                clauses.append(f"records.project_type IN ({','.join('?' for _ in items)})")
                params.extend(items)
        normalized_record_family = str(record_family or "").strip()
        if normalized_record_family:
            clauses.append("records.record_family = ?")
            params.append(normalized_record_family)
        if not include_failed:
            clauses.append(f"records.state IN ({','.join('?' for _ in CANONICAL_RECORD_STATES)})")
            params.extend(CANONICAL_RECORD_STATES)

        with self._connect() as conn:
            if include_failed:
                rows = conn.execute(
                    f"""
                    SELECT records.state, COUNT(*) AS c
                    FROM records
                    WHERE {' AND '.join(clauses)}
                    GROUP BY records.state
                    """,
                    params,
                ).fetchall()
                return {str(row["state"]): int(row["c"]) for row in rows}

            rows = conn.execute(
                f"""
                SELECT
                    records.record_id,
                    records.state,
                    records.project_code,
                    records.source_file,
                    records.archive_path,
                    records.source_identity_json
                FROM records
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        records = [
            {
                "record_id": row["record_id"],
                "state": row["state"],
                "project_code": row["project_code"],
                "source_file": row["source_file"],
                "archive_path": row["archive_path"],
                "source_identity_json": _source_identity_json_loads(row["source_identity_json"]),
            }
            for row in rows
        ]
        superseding_index = build_superseding_record_index(records)
        counts: Dict[str, int] = {}
        for record in records:
            if is_superseded_failed_record(record, superseding_index):
                continue
            state = str(record.get("state") or "")
            counts[state] = counts.get(state, 0) + 1
        return counts

    def list_pending_mappings(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pending_id, record_id, revision_id, project_code, payload_json, created_at
                FROM mapping_pending
                WHERE resolved_at = ''
                  AND pending_id IN (
                    SELECT MAX(pending_id)
                    FROM mapping_pending
                    WHERE resolved_at = ''
                    GROUP BY record_id
                  )
                ORDER BY pending_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "pending_id": int(row["pending_id"]),
                "record_id": row["record_id"],
                "revision_id": int(row["revision_id"]),
                "project_code": row["project_code"],
                "payload": _mapping_pending_payload_json_loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_record(self, record_id: str) -> Dict[str, Any]:
        rows = self.iter_latest_records()
        for row in rows:
            if row["record_id"] == record_id:
                return row
        raise KeyError(record_id)

    def update_record_archive_path(self, record_id: str, archive_path: str) -> None:
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_archive_path = _required_text(archive_path, field="archive_path")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE records
                SET archive_path = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (resolved_archive_path, _utcnow(), resolved_record_id),
            )
            if cursor.rowcount == 0:
                raise RuntimeError(
                    f"update_record_archive_path: no record found with record_id={resolved_record_id!r}"
                )

    def update_record_source_file(self, record_id: str, source_file: str) -> None:
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_source_file = _required_text(source_file, field="source_file")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    project_code,
                    business_key,
                    latest_revision_id,
                    identity_anchor,
                    state,
                    record_family,
                    business_id,
                    exchange,
                    source_identity_json
                FROM records
                WHERE record_id = ?
                """,
                (resolved_record_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resolved_record_id)
            failed_identity = _first_non_empty(
                str(row["identity_anchor"] or "").strip(),
                "legacy-failed" if str(row["state"] or "").strip() in FAILED_RECORD_STATES else "",
            )
            business_key = (
                str(row["business_key"] or "").strip()
                if failed_identity
                else _scoped_record_business_key(
                    project_code=str(row["project_code"] or ""),
                    source_file=resolved_source_file,
                    record_family=str(row["record_family"] or "listing"),
                    business_id=str(row["business_id"] or ""),
                    source_id=_resolve_scope_source_id(
                        source_identity=_source_identity_json_loads(row["source_identity_json"]),
                        exchange=row["exchange"],
                    ),
                )
            )
            now = _utcnow()
            if failed_identity:
                conn.execute(
                    """
                    UPDATE records
                    SET source_file = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (resolved_source_file, now, resolved_record_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE records
                    SET source_file = ?,
                        business_key = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (resolved_source_file, business_key, now, resolved_record_id),
                )
            latest_revision_id = row["latest_revision_id"]
            if latest_revision_id is not None:
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET source_file = ?
                    WHERE revision_id = ?
                    """,
                    (resolved_source_file, int(latest_revision_id)),
                )

    def update_downloaded_event_source_file(self, old_source_file: str, new_source_file: str) -> int:
        old_path = _required_text(old_source_file, field="old_source_file")
        new_path = _required_text(new_source_file, field="new_source_file")
        if old_path == new_path:
            return 0
        updated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, payload_json
                FROM job_events
                WHERE stage = 'downloaded'
                """
            ).fetchall()
            for row in rows:
                payload = _job_event_payload_json_loads(row["payload_json"])
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("source_file") or "").strip() != old_path:
                    continue
                payload["source_file"] = new_path
                conn.execute(
                    """
                    UPDATE job_events
                    SET payload_json = ?
                    WHERE event_id = ?
                    """,
                    (_json_dumps(payload), int(row["event_id"])),
                )
                updated += 1
        return updated

    def iter_latest_records(
        self,
        *,
        states: Iterable[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        business_type: str | None = None,
        record_family: str | None = None,
        limit: int | None = None,
        sort: str = "business",
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        normalized_column = (
            "replace(replace(replace(replace(replace(records.listing_date, '年', '-'), '月', '-'), '日', ''), '/', '-'), '.', '-')"
        )
        if states:
            items = [str(item) for item in states]
            clauses.append(f"records.state IN ({','.join('?' for _ in items)})")
            params.extend(items)
        if date_from:
            clauses.append(f"{normalized_column} >= ?")
            params.append(_normalize_date_text(date_from))
        if date_to:
            clauses.append(f"{normalized_column} <= ?")
            params.append(_normalize_date_text(date_to))
        if business_type:
            clauses.append("records.project_type = ?")
            params.append(str(business_type))
        if record_family:
            clauses.append("records.record_family = ?")
            params.append(str(record_family))

        order_clause = "records.project_type, records.project_code, records.updated_at"
        if str(sort or "").strip().lower() == "recent":
            order_clause = "records.updated_at DESC, records.record_id DESC"

        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(int(limit))

        query = f"""
            SELECT
                records.record_id,
                records.business_key,
                records.record_family,
                records.business_id,
                records.raw_business_label,
                records.identity_anchor,
                records.source_identity_json,
                records.project_code,
                records.project_name,
                records.project_type,
                records.exchange,
                records.listing_date,
                records.state,
                records.source_file,
                records.archive_path,
                records.last_error_type,
                records.last_error_message,
                records.artifact_status,
                records.last_operation_kind,
                records.last_operation_code,
                records.last_operation_message,
                records.last_operation_at,
                records.acknowledged_payload_json,
                records.created_at,
                records.updated_at,
                revisions.revision_id,
                revisions.revision_hash,
                revisions.parser_payload_json,
                revisions.postprocess_payload_json,
                revisions.canonical_record_json,
                revisions.canonical_projection_json,
                revisions.findings_json
            FROM records
            JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_clause}{limit_clause}
        """
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "record_id": row["record_id"],
                    "business_key": row["business_key"],
                    "record_family": row["record_family"],
                    "business_id": row["business_id"],
                    "raw_business_label": row["raw_business_label"],
                    "identity_anchor": row["identity_anchor"],
                    "source_identity_json": _source_identity_json_loads(row["source_identity_json"]),
                    "project_code": row["project_code"],
                    "project_name": row["project_name"],
                    "project_type": row["project_type"],
                    "exchange": row["exchange"],
                    "listing_date": row["listing_date"],
                    "state": row["state"],
                    "source_file": row["source_file"],
                    "archive_path": row["archive_path"],
                    "last_error_type": row["last_error_type"],
                    "last_error_message": row["last_error_message"],
                    "artifact_status": row["artifact_status"],
                    "last_operation_kind": row["last_operation_kind"],
                    "last_operation_code": row["last_operation_code"],
                    "last_operation_message": row["last_operation_message"],
                    "last_operation_at": row["last_operation_at"],
                    "acknowledged_payload_json": _acknowledged_payload_json_loads(row["acknowledged_payload_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "revision_id": int(row["revision_id"]),
                    "revision_hash": row["revision_hash"],
                    "parser_payload": _payload_json_loads(row["parser_payload_json"]),
                    "postprocess_payload": _payload_json_loads(row["postprocess_payload_json"]),
                    "canonical_record": _json_object_loads(row["canonical_record_json"]),
                    "canonical_projection": _json_object_loads(row["canonical_projection_json"]),
                    "findings": _findings_json_loads(row["findings_json"]),
                }
            )
        return out

    def get_exported_revision_map(self, cursor_key: str) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, revision_id, revision_hash, export_id, exported_at
                FROM export_cursor_records
                WHERE cursor_key = ?
                """,
                (cursor_key,),
            ).fetchall()
        return {
            row["record_id"]: {
                "revision_id": int(row["revision_id"]),
                "revision_hash": row["revision_hash"],
                "export_id": row["export_id"],
                "exported_at": row["exported_at"],
            }
            for row in rows
        }

    def has_export_history(self, cursor_id: str) -> bool:
        normalized_cursor_id = str(cursor_id or "").strip()
        if not normalized_cursor_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM exports
                WHERE cursor_id = ?
                   OR cursor_key = ?
                LIMIT 1
                """,
                (normalized_cursor_id, normalized_cursor_id),
            ).fetchone()
        return row is not None

    def mark_exported(
        self,
        *,
        export_id: str,
        cursor_id: str | None = None,
        cursor_key: str | None = None,
        requested_export_mode: str,
        date_from: str | None,
        date_to: str | None,
        project_type: str,
        output_dir: str,
        summary: Mapping[str, Any],
        records: Iterable[Dict[str, Any]],
        manifest: Mapping[str, Any] | None = None,
        cursor_value: Mapping[str, Any] | None = None,
        audit_action: str | None = None,
        audit_payload: Dict[str, Any] | None = None,
        retention_count: int | None = None,
    ) -> None:
        now = _utcnow()
        record_items = list(records)
        if cursor_id is None:
            resolved_cursor_id = _required_text(str(cursor_key or ""), field="cursor_id")
        else:
            resolved_cursor_id = _required_text(cursor_id, field="cursor_id")
        resolved_export_mode = str(requested_export_mode or "full").strip().lower() or "full"
        resolved_output_dir = _required_text(output_dir, field="output_dir")
        summary_payload = _mapping_payload(summary, field="summary")
        if "artifacts" in summary_payload:
            _artifact_paths_payload(summary_payload.get("artifacts"), field="summary.artifacts")
        resolved_retention_count = int(retention_count or summary_payload.get("retention_count") or 20)
        if resolved_retention_count < 1:
            resolved_retention_count = 20
        summary_payload["cursor_id"] = resolved_cursor_id
        summary_payload["requested_export_mode"] = resolved_export_mode
        summary_payload["retention_count"] = resolved_retention_count
        revision_watermark = max((int(item.get("revision_id") or 0) for item in record_items), default=0)
        summary_payload["revision_watermark"] = int(summary_payload.get("revision_watermark") or revision_watermark)
        manifest_was_provided = manifest is not None or "manifest" in summary_payload
        if manifest is None:
            manifest_payload = (
                _mapping_payload(summary_payload["manifest"], field="manifest")
                if "manifest" in summary_payload
                else {}
            )
        else:
            manifest_payload = _mapping_payload(manifest, field="manifest")
        if manifest_was_provided and not manifest_payload:
            raise ValueError("manifest must not be empty")
        if not manifest_payload:
            manifest_payload = {
                "export_id": export_id,
                "cursor_id": resolved_cursor_id,
                "requested_export_mode": resolved_export_mode,
                "effective_export_mode": str(summary_payload.get("effective_export_mode") or resolved_export_mode),
                "revision_watermark": int(summary_payload.get("revision_watermark") or 0),
                "included_count": int(summary_payload.get("new_records") or 0) + int(summary_payload.get("changed_records") or 0),
                "excluded_count": int(summary_payload.get("field_missing_blocked_records") or 0),
                "artifact_checksums": {},
                "cursor_basis": {"export_id": export_id, "eligible_set_hash": ""},
            }
        artifact_paths = _artifact_paths_payload(
            summary_payload.get("artifacts"),
            field="summary.artifacts",
        )
        _validate_artifacts_within_output_dir(
            artifact_paths,
            resolved_output_dir,
            field="export",
        )
        if not manifest_was_provided and artifact_paths:
            manifest_payload["artifact_checksums"] = {
                path: _regular_file_sha256(path, field="export artifact")
                for path in artifact_paths
            }
        manifest_payload.setdefault("export_id", export_id)
        manifest_payload.setdefault("cursor_id", resolved_cursor_id)
        manifest_payload.setdefault("revision_watermark", int(summary_payload.get("revision_watermark") or 0))
        _validate_export_artifact_checksums(artifact_paths, manifest_payload)
        summary_payload["manifest"] = manifest_payload
        cursor_value_was_provided = cursor_value is not None or "cursor_value" in summary_payload
        if cursor_value is None:
            cursor_value_payload = (
                _mapping_payload(summary_payload["cursor_value"], field="cursor_value")
                if "cursor_value" in summary_payload
                else {}
            )
        else:
            cursor_value_payload = _mapping_payload(cursor_value, field="cursor_value")
        if cursor_value_was_provided and not cursor_value_payload:
            raise ValueError("cursor_value must not be empty")
        if not cursor_value_payload:
            cursor_value_payload = {
                "last_successful_revision_watermark": int(summary_payload.get("revision_watermark") or 0),
                "last_successful_export_id": export_id,
                "cursor_basis_export_id": export_id,
                "eligible_set_hash": str(manifest_payload.get("cursor_basis", {}).get("eligible_set_hash") or ""),
            }
        summary_payload["cursor_value"] = cursor_value_payload
        audit_payload_object = None if audit_payload is None else _object_payload(audit_payload, field="audit_payload")
        staged_retention_files: list[tuple[str, str]] = []
        try:
            with self._connect() as conn:
                conn.execute(
                """
                INSERT INTO exports (
                    export_id, cursor_key, cursor_id, mode, date_from, date_to,
                    project_type, output_dir, summary_json, created_at,
                    is_tombstone, pruned_by_retention, retention_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    resolved_cursor_id,
                    resolved_cursor_id,
                    resolved_export_mode,
                    str(date_from or ""),
                    str(date_to or ""),
                    project_type,
                    resolved_output_dir,
                    _json_dumps(summary_payload),
                    now,
                    0,
                    0,
                    resolved_retention_count,
                ),
                )
                conn.execute(
                """
                INSERT INTO export_manifests (export_id, manifest_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(export_id) DO UPDATE SET
                    manifest_json = excluded.manifest_json
                """,
                (export_id, _json_dumps(manifest_payload), now),
                )
                conn.execute(
                """
                INSERT INTO export_cursor_values (cursor_id, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cursor_id) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (resolved_cursor_id, _json_dumps(cursor_value_payload), now),
                )
                if audit_action:
                    self._add_audit_entry_conn(
                        conn,
                        str(audit_action),
                        {} if audit_payload_object is None else audit_payload_object,
                        event_ts=now,
                    )
                for item in record_items:
                    conn.execute(
                    """
                    INSERT INTO export_cursor_records (
                        cursor_key, record_id, revision_id, revision_hash, export_id, exported_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cursor_key, record_id) DO UPDATE SET
                        revision_id = excluded.revision_id,
                        revision_hash = excluded.revision_hash,
                        export_id = excluded.export_id,
                        exported_at = excluded.exported_at
                    """,
                    (
                        resolved_cursor_id,
                        item["record_id"],
                        int(item["revision_id"]),
                        item["revision_hash"],
                        export_id,
                        now,
                    ),
                    )
                rows = conn.execute(
                """
                SELECT e.export_id, e.output_dir, e.summary_json, m.manifest_json
                FROM exports AS e
                LEFT JOIN export_manifests AS m ON m.export_id = e.export_id
                WHERE e.cursor_id = ?
                  AND e.is_tombstone = 0
                ORDER BY e.created_at DESC, e.rowid DESC
                """,
                (resolved_cursor_id,),
                ).fetchall()
                prune_rows = rows[resolved_retention_count:]
                prune_ids = {str(row["export_id"]) for row in prune_rows}
                active_reference_keys: set[str] = set()
                active_rows = conn.execute(
                    """
                    SELECT e.export_id, e.output_dir, e.summary_json, m.manifest_json
                    FROM exports AS e
                    LEFT JOIN export_manifests AS m ON m.export_id = e.export_id
                    WHERE e.is_tombstone = 0
                    """
                ).fetchall()
                for active_row in active_rows:
                    active_export_id = str(active_row["export_id"])
                    if active_export_id in prune_ids:
                        continue
                    try:
                        active_payload = _stored_object_json_loads(active_row["summary_json"])
                        active_paths = _artifact_paths_payload(
                            active_payload.get("artifacts"),
                            field="summary.artifacts",
                        )
                        active_reference_keys.update(
                            _validate_retention_row_artifacts(
                                export_id=active_export_id,
                                output_dir=active_row["output_dir"],
                                summary_payload=active_payload,
                                stored_manifest_json=active_row["manifest_json"],
                                artifact_paths=active_paths,
                            )
                        )
                    except (TypeError, ValueError, RuntimeError):
                        # A polluted active row must not be able to shield an
                        # unrelated path from retention as a shared artifact.
                        continue

                prune_plans: list[
                    tuple[
                        sqlite3.Row,
                        dict[str, Any],
                        list[str],
                        dict[str, tuple[int, int, int, int, str]],
                    ]
                ] = []
                for row in prune_rows:
                    payload = _stored_object_json_loads(row["summary_json"])
                    paths = _artifact_paths_payload(payload.get("artifacts"), field="summary.artifacts")
                    validated_artifacts: dict[str, tuple[int, int, int, int, str]] = {}
                    if paths:
                        try:
                            validated_artifacts = _validate_retention_row_artifacts(
                                export_id=str(row["export_id"]),
                                output_dir=row["output_dir"],
                                summary_payload=payload,
                                stored_manifest_json=row["manifest_json"],
                                artifact_paths=paths,
                            )
                        except (TypeError, ValueError, RuntimeError):
                            # Corrupt historical ownership or integrity data is
                            # tombstoned without touching any referenced path.
                            validated_artifacts = {}
                    prune_plans.append((row, payload, paths, validated_artifacts))

                staged_keys: set[str] = set()
                for row, payload, paths, validated_artifacts in prune_plans:
                    paths_to_stage = paths if validated_artifacts else []
                    for path in paths_to_stage:
                        key = _artifact_path_key(path)
                        expected_fingerprint = validated_artifacts.get(key)
                        if expected_fingerprint is None:
                            raise RuntimeError(f"retention artifact ownership changed before staging: {path}")
                        if key in active_reference_keys or key in staged_keys:
                            continue
                        current_fingerprint = _retention_artifact_fingerprint(
                            path,
                            field="retention artifact",
                        )
                        if current_fingerprint != expected_fingerprint:
                            raise RuntimeError(f"retention artifact changed before staging: {path}")
                        staged_path = f"{path}.peap-retention-{uuid.uuid4().hex}.pending"
                        os.replace(path, staged_path)
                        staged_retention_files.append((path, staged_path))
                        staged_fingerprint = _retention_artifact_fingerprint(
                            staged_path,
                            field="staged retention artifact",
                        )
                        if staged_fingerprint != expected_fingerprint:
                            raise RuntimeError(f"retention artifact changed during staging: {path}")
                        staged_keys.add(key)
                    payload["pruned_by_retention"] = True
                    payload["retention_count"] = resolved_retention_count
                    conn.execute(
                    """
                    UPDATE exports
                    SET is_tombstone = 1,
                        pruned_by_retention = 1,
                        retention_count = ?,
                        summary_json = ?
                    WHERE export_id = ?
                    """,
                    (resolved_retention_count, _json_dumps(payload), row["export_id"]),
                    )
                # Recheck the artifacts immediately before the transaction
                # commit after all SQL and
                # retention staging. Any race causes the whole transaction to
                # roll back and staged files are restored by the outer handler.
                _validate_artifacts_within_output_dir(
                    artifact_paths,
                    resolved_output_dir,
                    field="export",
                )
                _validate_export_artifact_checksums(artifact_paths, manifest_payload)
        except Exception as transaction_exc:
            try:
                _restore_retention_staged_files(staged_retention_files)
            except Exception as restore_exc:
                raise ExceptionGroup(
                    "export transaction failed and retention restoration failed",
                    [transaction_exc, restore_exc],
                ) from None
            raise
        _cleanup_retention_staged_files(staged_retention_files)

    def list_exports(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT export_id, cursor_key, cursor_id, mode, date_from, date_to,
                       project_type, output_dir, summary_json, created_at,
                       is_tombstone, pruned_by_retention, retention_count
                FROM exports
                ORDER BY created_at DESC, export_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "export_id": row["export_id"],
                "cursor_id": _required_text(row["cursor_id"], field="cursor_id"),
                "date_from": row["date_from"],
                "date_to": row["date_to"],
                "project_type": row["project_type"],
                "output_dir": row["output_dir"],
                "summary": _stored_object_json_loads(row["summary_json"]),
                "created_at": row["created_at"],
                "is_tombstone": bool(row["is_tombstone"]),
                "pruned_by_retention": bool(row["pruned_by_retention"]),
                "retention_count": int(row["retention_count"]),
            }
            for row in rows
        ]

    def get_export_manifest(self, export_id: str) -> Dict[str, Any]:
        normalized_export_id = str(export_id or "").strip()
        if not normalized_export_id:
            raise KeyError(export_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT manifest_json
                FROM export_manifests
                WHERE export_id = ?
                LIMIT 1
                """,
                (normalized_export_id,),
            ).fetchone()
            if row is None:
                export_row = conn.execute(
                    """
                    SELECT export_id
                    FROM exports
                    WHERE export_id = ?
                    LIMIT 1
                    """,
                    (normalized_export_id,),
                ).fetchone()
        if row is None:
            if export_row is None:
                raise KeyError(normalized_export_id)
            return {}
        return _stored_object_json_loads(row["manifest_json"])

    def get_export_cursor_value(self, cursor_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value_json
                FROM export_cursor_values
                WHERE cursor_id = ?
                LIMIT 1
                """,
                (str(cursor_id or ""),),
            ).fetchone()
        if row is None:
            return {}
        return _stored_object_json_loads(row["value_json"])

    def get_export(self, export_id: str) -> Dict[str, Any]:
        normalized_export_id = str(export_id or "").strip()
        if not normalized_export_id:
            raise KeyError(export_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT export_id, cursor_key, cursor_id, mode, date_from, date_to,
                       project_type, output_dir, summary_json, created_at,
                       is_tombstone, pruned_by_retention, retention_count
                FROM exports
                WHERE export_id = ?
                LIMIT 1
                """,
                (normalized_export_id,),
            ).fetchone()
        if row is None:
            raise KeyError(normalized_export_id)
        return {
            "export_id": row["export_id"],
            "cursor_id": _required_text(row["cursor_id"], field="cursor_id"),
            "date_from": row["date_from"],
            "date_to": row["date_to"],
            "project_type": row["project_type"],
            "output_dir": row["output_dir"],
            "summary": _stored_object_json_loads(row["summary_json"]),
            "created_at": row["created_at"],
            "is_tombstone": bool(row["is_tombstone"]),
            "pruned_by_retention": bool(row["pruned_by_retention"]),
            "retention_count": int(row["retention_count"]),
        }
