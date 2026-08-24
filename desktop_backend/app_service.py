"""Application-facing service layer for the desktop app."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import stat
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Callable, Dict

from peap.artifact_truth import resolve_artifact_evidence_verdict
from peap.business_runtime import iter_source_business_bindings
from peap.product_profile import get_product_profile
from peap.streaming_export import (
    count_records_in_export_scope_by_state,
    run_ready_export,
)
from peap.streaming_ingest import StreamingIngestRunner
from peap.streaming_models import ItemSavedPayload
from peap.streaming_store import REQUIRED_SCHEMA_COLUMNS, SCHEMA_VERSION, StreamingStore
from peap.submission_layout import safe_submission_name, submission_month_dir_name
from peap.surface_contract import KNOWN_SURFACES, supported_sources_for_surface
from peap_core.business_catalog import get_business_descriptor, list_business_descriptors
from peap_core.family_catalog import list_family_descriptors
from peap_core.source_business_contract import (
    get_scope_policy_descriptor,
    list_source_business_requirements,
    source_business_requirement_supported_by_catalog,
)
from peap_core.source_catalog import (
    list_source_descriptors,
    resolve_source_descriptor,
)  # noqa: F401 — re-exported for test assertions

# ── Domain layer imports ──
from .domain.normalizers import (
    coerce_int as _coerce_int,
)
from .domain.normalizers import (
    normalize_local_path as _normalize_local_path,
)
from .domain.normalizers import (
    normalize_mapping_payload as _normalize_mapping_payload,
)
from .domain.normalizers import (
    normalize_record_state_value as _normalize_record_state_value,
)
from .domain.normalizers import (
    parse_bool as _parse_bool,
)
from .domain.normalizers import (
    parse_local_path as _parse_local_path,
)
from .domain.normalizers import (
    parse_text as _parse_text,
)
from .domain.normalizers import (
    path_within_root as _path_within_root,
)
from .domain.normalizers import (
    validate_mapping_payload as _validate_mapping_payload,
)
from .error_codes import (
    ERROR_INVALID_PATH_SELECTION_KIND,
    ERROR_INVALID_REQUEST,
    ERROR_LOCAL_PATH_OPEN_FAILED,
    ERROR_LOCAL_PATH_PICKER_FAILED,
    ERROR_LOCAL_PATH_REQUIRED,
    ERROR_RECORD_ARTIFACT_NOT_FOUND,
    ERROR_RECORD_ARTIFACT_OPEN_FAILED,
    ERROR_SCHEMA_NOT_READY,
)
from .local_paths import LocalPathError, pick_local_path, reveal_in_file_manager
from .record_identity import FAILED_RECORD_STATES, pick_reprocess_evidence_path
from .repositories import PipelineRepository
from .request_contract import normalize_mapping_record_selection_request
from .runtime_dependencies import RuntimeDependencyManager
from .services.execution_service import ExecutionService
from .services.mapping_service import MappingService
from .services.records_service import (
    RecordsService,
)
from .services.records_service import (
    normalize_request_scope as _records_normalize_request_scope,
)
from .services.review_problem_service import ReviewProblemService
from .services.runtime_service import RuntimeService
from .services.settings_service import (
    SettingsService,
    _default_postprocess_config_path,
    _default_stored_preference,
    _scope_from_payload,
)


def _namespace(**kwargs):
    return argparse.Namespace(**kwargs)


def _optional_payload_object(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return dict(payload)


def _mapping_refresh_scope_metadata(records: list[Dict[str, Any]]) -> dict[str, Any]:
    """Build a truthful parent scope snapshot for mapping-refresh jobs.

    Mapping changes can affect records from more than one family/source.  A
    parent job therefore publishes one concrete scope when there is exactly
    one, and an aggregate family scope otherwise; it never silently assigns a
    default listing business to an unknown record.
    """

    snapshots: dict[tuple[str, str, str], dict[str, str]] = {}
    for record in records:
        family = str(record.get("record_family") or "").strip()
        business_id = str(record.get("business_id") or "").strip()
        exchange = str(record.get("exchange") or "").strip()
        if not family or not business_id:
            continue
        business_label = str(record.get("business_label") or "").strip()
        if not business_label:
            try:
                business_label = get_business_descriptor(business_id, family_id=family).canonical_label
            except (KeyError, ValueError):
                business_label = ""
        key = (family, business_id, exchange)
        snapshots.setdefault(
            key,
            {
                "record_family": family,
                "business_id": business_id,
                "business_label": business_label,
                "exchange": exchange,
            },
        )
    scopes = [snapshots[key] for key in sorted(snapshots)]
    if not scopes:
        return {}
    if len(scopes) == 1:
        return {"scope": scopes[0]}
    return {
        "record_families": sorted({scope["record_family"] for scope in scopes}),
        "family_scopes": scopes,
    }


def _optional_mapping_snapshot(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _required_mapping_snapshot(value: Any, *, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _optional_list_snapshot(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _required_list_snapshot(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _required_non_empty_text_list_snapshot(value: Any, *, field_name: str) -> list[str]:
    items = _required_list_snapshot(value, field_name=field_name)
    values: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        normalized = item.strip()
        if not normalized:
            raise ValueError(f"{field_name}[{index}] must be a non-empty string")
        values.append(normalized)
    if not values:
        raise ValueError(f"{field_name} must contain at least one value")
    return values


def _undo_entry_snapshot(operation: Mapping[str, Any], field_name: str) -> Dict[str, Any]:
    value = operation.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"undo operation {field_name} must be a mapping")
    return dict(value)


def _timestamp_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


_RECORD_REVEAL_EVIDENCE_STATUSES = {"verified", "present_unverified", "shared_official_page"}
_REPROCESS_EVIDENCE_STATUSES = {"verified", "shared_official_page"}
def _record_evidence_error_result(
    *,
    record_id: str,
    state: str,
    error_code: str,
    error_message: str,
    evidence_status: str,
    evidence_reason_code: str,
) -> Dict[str, Any]:
    return {
        "state": state or "parse_failed",
        "record_id": record_id,
        "error_code": error_code,
        "error_message": error_message,
        "evidence_status": evidence_status,
        "evidence_reason_code": evidence_reason_code,
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        import json as _json
        parsed = _json.loads(value)
        if isinstance(parsed, dict):
            return dict(parsed)
    raise ValueError("expected JSON object")


def _record_reprocess_runtime_supported(record: Dict[str, Any], source_identity: Dict[str, Any]) -> bool:
    canonical_record = _json_object(record.get("canonical_record"))
    canonical_business_identity = _json_object(canonical_record.get("business_identity"))
    canonical_source_identity = _json_object(canonical_record.get("source_identity"))
    family = str(
        record.get("record_family")
        or canonical_record.get("record_family")
        or canonical_business_identity.get("record_family")
        or canonical_source_identity.get("record_family")
        or source_identity.get("record_family")
        or ""
    ).strip()
    business_id = str(
        record.get("business_id")
        or canonical_business_identity.get("business_id")
        or canonical_source_identity.get("business_id")
        or source_identity.get("business_id")
        or ""
    ).strip()
    raw_source = str(
        source_identity.get("source_id")
        or canonical_source_identity.get("source_id")
        or source_identity.get("exchange")
        or canonical_source_identity.get("exchange")
        or record.get("source_id")
        or record.get("exchange")
        or ""
    ).strip()
    if not family or not business_id or not raw_source:
        return False
    descriptor = resolve_source_descriptor(raw_source, allow_substring=True)
    source_id = descriptor.source_id if descriptor is not None else raw_source
    bindings = tuple(iter_source_business_bindings(source_id=source_id, record_family=family))
    if any(
        binding.business_id == business_id
        and bool(getattr(binding, "implemented", True))
        for binding in bindings
    ):
        return True

    # A verified archive can be reclassified by the parser when the persisted
    # business id is stale or was never implemented for this source/family.
    # Keep the fallback scoped to a source/family that has at least one real
    # runtime binding; an entirely unsupported scope must still fail closed.
    if not any(bool(getattr(binding, "implemented", True)) for binding in bindings):
        return False
    evidence_record = dict(record)
    if "source_identity_json" not in evidence_record and source_identity:
        evidence_record["source_identity"] = dict(source_identity)
    return resolve_artifact_evidence_verdict(evidence_record).status == "verified"


def _iter_candidate_archive_repair_paths(
    *,
    app_home: str,
    archive_root: str,
    source_file: str,
    archive_path: str,
    listing_date: str,
    project_code: str,
    project_name: str,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(path_value: str) -> None:
        normalized = os.path.abspath(str(path_value or "").strip())
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    def _normalized_identity_token(raw_value: str) -> str:
        token = safe_submission_name(str(raw_value or "").strip())
        token = re.sub(r"__conflict\d+$", "", token, flags=re.IGNORECASE)
        return token.casefold()

    def _base_project_code_token(raw_value: str) -> str:
        token = _normalized_identity_token(raw_value)
        return re.sub(r"-\d+$", "", token)

    def _push_unique_identity_match() -> None:
        base_code = _base_project_code_token(project_code)
        normalized_title = _normalized_identity_token(project_name)
        if not base_code or not normalized_title:
            return
        matches: list[str] = []
        for root, _, files in os.walk(os.path.abspath(archive_root)):
            for file_name in files:
                if not file_name.lower().endswith(".html"):
                    continue
                stem_token = _normalized_identity_token(os.path.splitext(file_name)[0])
                if not stem_token.startswith(f"{base_code}-"):
                    continue
                if not stem_token.endswith(f"-{normalized_title}"):
                    continue
                matches.append(os.path.join(root, file_name))
                if len(matches) > 1:
                    return
        if len(matches) == 1:
            _push(matches[0])

    month_dir = submission_month_dir_name(listing_date)
    path_values = [source_file, archive_path]
    basename = ""
    for path_value in path_values:
        current = os.path.abspath(str(path_value or "").strip())
        if not current:
            continue
        basename = basename or os.path.basename(current)
        submission_prefix = os.path.join(os.path.abspath(app_home), "submission") + os.sep
        if current.startswith(submission_prefix):
            relative = current[len(submission_prefix):]
            _push(os.path.join(os.path.abspath(archive_root), relative))
        _push(current.replace(f"{os.sep}submission{os.sep}", f"{os.sep}archive{os.sep}"))
    if basename:
        _push(os.path.join(os.path.abspath(archive_root), month_dir, basename))
        _push(os.path.join(os.path.abspath(archive_root), "unknown_month", basename))
        _push(os.path.join(os.path.abspath(app_home), month_dir, basename))
        _push_unique_identity_match()
        archive_matches: list[str] = []
        for root, _, files in os.walk(os.path.abspath(archive_root)):
            if basename in files:
                archive_matches.append(os.path.join(root, basename))
                if len(archive_matches) > 1:
                    break
        if len(archive_matches) == 1:
            _push(archive_matches[0])
    return candidates


# Constants, normalizers, record projections, and settings helpers are now
# provided by the domain layer.  The underscore-prefixed aliases above
# (e.g. _coerce_int, _normalize_local_path) keep all internal call-sites
# working without changes.


class AppUserFacingError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        error_code: str,
        http_status: int,
        details: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message or "")
        self.error_code = str(error_code or "")
        self.http_status = int(http_status)
        if details is None:
            self.details = {}
        elif not isinstance(details, Mapping):
            raise TypeError("details must be a dict")
        else:
            self.details = dict(details)


def _summary_count(summary: Dict[str, Any], key: str) -> int:
    return _coerce_int(summary.get(key), default=0)


def _state_counts(rows: list[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "").strip()
        if not state:
            continue
        counts[state] = counts.get(state, 0) + 1
    return counts


def _export_artifact_availability(
    summary: Dict[str, Any],
    *,
    is_tombstone: bool,
    pruned_by_retention: bool,
    managed_export_root: str | None,
) -> Dict[str, Any]:
    artifact_values = _optional_list_snapshot(
        summary.get("artifacts"),
        field_name="export history summary.artifacts",
    )
    artifacts: list[str] = []
    for index, path in enumerate(artifact_values):
        if not isinstance(path, str):
            raise ValueError(f"export history summary.artifacts[{index}] must be a string")
        normalized_path = path.strip()
        if not normalized_path:
            raise ValueError(
                f"export history summary.artifacts[{index}] must be a non-empty string"
            )
        artifacts.append(normalized_path)
    managed_root = str(managed_export_root or "").strip()
    try:
        managed_root_stat = os.lstat(managed_root) if managed_root else None
    except OSError:
        managed_root_stat = None
    managed_root_is_directory = bool(
        managed_root_stat is not None
        and stat.S_ISDIR(managed_root_stat.st_mode)
        and not stat.S_ISLNK(managed_root_stat.st_mode)
    )

    def _is_available(path: str) -> bool:
        try:
            path_stat = os.lstat(path)
        except OSError:
            return False
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            return False
        return managed_root_is_directory and _path_within_root(path, managed_root)

    existing_artifacts = [
        path
        for path in artifacts
        if _is_available(path)
    ]
    missing_artifacts = [
        path
        for path in artifacts
        if not _is_available(path)
    ]
    artifact_incomplete = bool(existing_artifacts) and bool(missing_artifacts)
    artifact_unavailable = not artifacts or not existing_artifacts
    tombstone = bool(is_tombstone) or bool(pruned_by_retention) or artifact_unavailable or artifact_incomplete
    if pruned_by_retention:
        retention_status = "pruned_by_retention"
    elif artifact_incomplete:
        retention_status = "artifact_incomplete"
    elif tombstone:
        retention_status = "artifact_unavailable"
    else:
        retention_status = "available"
    return {
        "artifacts": artifacts,
        "existing_artifacts": existing_artifacts,
        "missing_artifacts": missing_artifacts,
        "artifact_count": len(artifacts),
        "openable": bool(artifacts) and not bool(missing_artifacts) and not tombstone,
        "rebuildable": not tombstone,
        "is_tombstone": tombstone,
        "retention_status": retention_status,
    }


def _export_artifact_sha256(path: str) -> str:
    normalized_path = str(path or "").strip()
    try:
        path_stat = os.lstat(normalized_path)
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise RuntimeError(f"export artifact is not a regular file: {normalized_path}")
        fd = os.open(normalized_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(path_stat, opened_stat):
                raise RuntimeError(f"export artifact changed before checksum: {normalized_path}")
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
            raise RuntimeError(f"export artifact changed during checksum: {normalized_path}")
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError(f"export artifact cannot be read: {normalized_path}: {exc}") from exc
    return digest.hexdigest()


def _verified_export_artifact_checksums(
    manifest: Mapping[str, Any],
    artifacts: list[str],
) -> dict[str, str]:
    raw_checksums = manifest.get("artifact_checksums")
    if not isinstance(raw_checksums, Mapping):
        raise ValueError("manifest.artifact_checksums must be an object")
    checksums_by_key: dict[str, tuple[str, str]] = {}
    for raw_path, raw_digest in raw_checksums.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("manifest.artifact_checksums contains an empty path")
        if not isinstance(raw_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", raw_digest.strip()):
            raise ValueError(f"manifest.artifact_checksums[{raw_path!r}] is not a SHA-256 digest")
        path = raw_path.strip()
        key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if key in checksums_by_key:
            raise ValueError("manifest.artifact_checksums contains duplicate paths")
        checksums_by_key[key] = (path, raw_digest.strip().lower())

    artifacts_by_key: dict[str, str] = {}
    for path in artifacts:
        key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if key in artifacts_by_key:
            raise ValueError("existing_artifacts contains duplicate paths")
        artifacts_by_key[key] = path
    if set(artifacts_by_key) != set(checksums_by_key):
        raise ValueError("manifest.artifact_checksums must exactly match export artifacts")

    verified: dict[str, str] = {}
    for key, path in artifacts_by_key.items():
        expected = checksums_by_key[key][1]
        actual = _export_artifact_sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"export artifact checksum mismatch: {path}: expected {expected}, got {actual}"
            )
        verified[path] = expected
    return verified


def _copy_export_artifact_exclusive(source_path: str, destination_path: str) -> None:
    source_fd = -1
    destination_fd = -1
    destination_created = False
    try:
        source_stat = os.lstat(source_path)
        if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
            raise RuntimeError(f"export artifact is not a regular file: {source_path}")
        source_fd = os.open(source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened_source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(opened_source_stat.st_mode) or not os.path.samestat(
            source_stat,
            opened_source_stat,
        ):
            raise RuntimeError(f"export artifact changed before copy: {source_path}")
        destination_fd = os.open(
            destination_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IMODE(opened_source_stat.st_mode) or 0o600,
        )
        destination_created = True
        with (
            os.fdopen(source_fd, "rb", closefd=False) as source_handle,
            os.fdopen(destination_fd, "wb", closefd=False) as destination_handle,
        ):
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_fd)
        os.utime(
            destination_path,
            ns=(opened_source_stat.st_atime_ns, opened_source_stat.st_mtime_ns),
            follow_symlinks=False,
        )
    except Exception:
        if destination_created:
            try:
                os.remove(destination_path)
            except OSError:
                pass
        raise
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)


def _normalize_request_scope(
    payload: Dict[str, Any] | None,
    *,
    require_explicit_scope: bool,
):
    return _records_normalize_request_scope(payload, require_explicit_scope=require_explicit_scope)


def _source_backed_family_ids(sources: list[Any]) -> set[str]:
    backed_family_ids: set[str] = set()
    for source in sources:
        if not bool(getattr(source, "enabled", True)):
            continue
        for raw_family_id in tuple(getattr(source, "supported_record_families", ()) or ()):
            family_id = str(raw_family_id or "").strip()
            if family_id:
                backed_family_ids.add(family_id)
    return backed_family_ids


def _enabled_source_ids_by_family(sources: list[Any]) -> Dict[str, set[str]]:
    source_ids_by_family: Dict[str, set[str]] = {}
    for source in sources:
        if not bool(getattr(source, "enabled", True)):
            continue
        source_id = str(getattr(source, "source_id", "") or "").strip()
        if not source_id:
            continue
        for raw_family_id in tuple(getattr(source, "supported_record_families", ()) or ()):
            family_id = str(raw_family_id or "").strip()
            if family_id:
                source_ids_by_family.setdefault(family_id, set()).add(source_id)
    return source_ids_by_family


def _source_business_requirements_payload() -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    payload: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for requirement in list_source_business_requirements():
        if not source_business_requirement_supported_by_catalog(requirement):
            continue
        scope_policy = str(getattr(requirement, "scope_policy", "") or "").strip()
        if not scope_policy:
            continue
        family_id = str(requirement.record_family or "").strip()
        business_id = str(requirement.business_id or "").strip()
        source_id = str(requirement.source_id or "").strip()
        if not (family_id and business_id and source_id):
            continue
        try:
            descriptor = get_scope_policy_descriptor(scope_policy)
        except KeyError:
            continue
        payload.setdefault(family_id, {}).setdefault(business_id, {})[source_id] = {
            "scope_policy": scope_policy,
            "scope_policy_label": descriptor.label,
            "scope_policy_summary": descriptor.summary,
        }
    return payload


def _discover_import_files(input_dir: str) -> list[str]:
    root = os.path.abspath(str(input_dir or "").strip())
    if not root or not os.path.isdir(root):
        return []
    matches: list[str] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names.sort()
        for file_name in sorted(file_names):
            lowered = file_name.lower()
            if lowered.endswith((".html", ".htm", ".mhtml")):
                matches.append(os.path.join(current_root, file_name))
    return matches


def _database_schema_ready(db_path: str) -> bool:
    normalized_path = os.path.abspath(str(db_path or "").strip())
    if not normalized_path or not os.path.exists(normalized_path):
        return False
    try:
        with sqlite3.connect(f"file:{normalized_path}?mode=ro", uri=True) as conn:
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if user_version != SCHEMA_VERSION:
                return False
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
            table_names = {str(row[0]) for row in rows}
            if not set(REQUIRED_SCHEMA_COLUMNS).issubset(table_names):
                return False
            for table_name, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
                column_rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                column_names = {str(row[1]) for row in column_rows}
                if not required_columns.issubset(column_names):
                    return False
    except sqlite3.Error:
        return False
    return True


class AppService:
    """Thin orchestration layer consumed by the local desktop API."""

    def __init__(self, *, config_obj: object, runtime_dependencies: RuntimeDependencyManager | None = None) -> None:
        self.config = config_obj
        self.app_home = str(getattr(config_obj, "APP_HOME", getattr(config_obj, "DATA_ROOT", "")))
        self.db_path = os.path.abspath(
            str(
                getattr(config_obj, "STREAMING_DB_PATH", "")
                or os.path.join(str(config_obj.LOG_DIR), "streaming_ingest.sqlite3")
            )
        )
        self.default_archive_root = os.path.abspath(
            str(getattr(config_obj, "ARCHIVE_ROOT", "") or os.path.join(str(config_obj.DATA_ROOT), "outputs", "archive"))
        )
        self.default_export_root = os.path.abspath(str(getattr(config_obj, "OUTPUT_EXCEL_DIR", "")))
        self.default_postprocess_config = _default_postprocess_config_path(config_obj)
        self._schema_ready = _database_schema_ready(self.db_path)
        self.store = StreamingStore(self.db_path, auto_migrate=False)
        self.pipeline_repository = PipelineRepository(store=self.store)
        self.mapping_service = MappingService(repository=self.pipeline_repository)
        managed_artifact_roots = self._managed_artifact_roots()
        self.review_problem_service = ReviewProblemService(
            repository=self.pipeline_repository,
            managed_artifact_roots=managed_artifact_roots,
        )
        self.records_service = RecordsService(
            repository=self.pipeline_repository,
            db_path=self.db_path,
            managed_artifact_roots=managed_artifact_roots,
        )
        self.settings_service = SettingsService(
            config_obj=self.config,
            repository=self.pipeline_repository,
            app_home=self.app_home,
            default_archive_root=self.default_archive_root,
            default_export_root=self.default_export_root,
        )
        self.runtime_dependencies = runtime_dependencies or RuntimeDependencyManager(
            browser_cache_dir=str(getattr(config_obj, "PLAYWRIGHT_BROWSERS_PATH", "")),
        )
        self.runtime_service = RuntimeService(
            config_obj=self.config,
            repository=self.pipeline_repository,
            runtime_dependencies=self.runtime_dependencies,
        )
        self._startup_session_id = secrets.token_hex(16)
        self.execution_service = ExecutionService(
            config_obj=self.config,
            repository=self.pipeline_repository,
            db_path=self.db_path,
            runtime_service=self.runtime_service,
            get_basic_settings=self.get_basic_settings,
            get_advanced_settings=self.get_advanced_settings,
            run_store_maintenance=self._run_store_maintenance,
            repair_missing_archives_once=self._repair_missing_archives_once,
            build_ingest_runner=self._build_ingest_runner,
            user_error_cls=AppUserFacingError,
            startup_session_id=self._startup_session_id,
        )
        self._active_mutating_jobs = self.execution_service._active_mutating_jobs
        self._lock = threading.Lock()
        self._undo_lock = threading.Lock()
        self._mapping_undo_stack: list[Dict[str, Any]] = []
        self._archive_repair_attempted = False
        self._archive_repair_in_progress = False
        if self._schema_ready:
            interrupted_jobs = self.pipeline_repository.interrupt_running_jobs(
                reason="desktop backend restarted before task completed"
            )
            if interrupted_jobs:
                self.pipeline_repository.add_audit_entry(
                    "running_jobs_interrupted_on_startup",
                    {"job_ids": interrupted_jobs, "count": len(interrupted_jobs)},
                )
            self._run_store_maintenance()

    def _managed_artifact_roots(self) -> tuple[str, ...]:
        roots = [
            self.default_archive_root,
            str(getattr(self.config, "AUTO_HTML_ROOT", "") or ""),
        ]
        return tuple(dict.fromkeys(os.path.abspath(root) for root in roots if str(root or "").strip()))

    def _schema_readiness_payload(self) -> Dict[str, Any]:
        return {
            "ready": bool(self._schema_ready),
            "expected_user_version": SCHEMA_VERSION,
        }

    def _require_schema_ready(self) -> None:
        if self._schema_ready:
            return
        raise AppUserFacingError(
            message="database schema is not ready; run the explicit migration before using DB-backed routes",
            error_code=ERROR_SCHEMA_NOT_READY,
            http_status=503,
            details={
                "db_path": self.db_path,
                "app_home": self.app_home,
                "workspace_root": self.app_home,
                "schema": self._schema_readiness_payload(),
                "migration_required": True,
            },
        )

    def _run_store_maintenance(self) -> None:
        self._require_schema_ready()
        rules_config = self._load_effective_rules_config()
        self.pipeline_repository.run_store_maintenance(rules_config=rules_config, mutate=True)

    def _repair_missing_archives_once(self) -> None:
        self._require_schema_ready()
        with self._lock:
            if self._archive_repair_attempted or self._archive_repair_in_progress:
                return
            self._archive_repair_in_progress = True

        try:
            archive_root = self.get_basic_settings()["archive_root"]
            inspected_records = 0
            evidence_verdict_counts: dict[str, int] = {}
            evidence_reason_counts: dict[str, int] = {}
            for record in self.pipeline_repository.iter_latest_records(sort="recent"):
                if str(record.get("state") or "").strip() in FAILED_RECORD_STATES:
                    continue
                inspected_records += 1
                evidence_verdict = resolve_artifact_evidence_verdict(record)
                if evidence_verdict.status in {"verified", "shared_official_page"}:
                    continue
                evidence_verdict_counts[evidence_verdict.status] = (
                    evidence_verdict_counts.get(evidence_verdict.status, 0) + 1
                )
                evidence_reason_counts[evidence_verdict.reason_code] = (
                    evidence_reason_counts.get(evidence_verdict.reason_code, 0) + 1
                )
            if evidence_verdict_counts:
                self.pipeline_repository.add_audit_entry(
                    "missing_archive_repair_deferred",
                    {
                        "archive_root": archive_root,
                        "inspected_records": inspected_records,
                        "missing_archive_records": evidence_verdict_counts.get("stale_reference", 0),
                        "evidence_verdict_counts": dict(sorted(evidence_verdict_counts.items())),
                        "evidence_reason_counts": dict(sorted(evidence_reason_counts.items())),
                        "report_only": True,
                        "next_action": "Review the archive issue through the controlled operations process before changing records or archives.",
                    },
                )
        except Exception:
            with self._lock:
                self._archive_repair_in_progress = False
            raise
        else:
            with self._lock:
                self._archive_repair_attempted = True
                self._archive_repair_in_progress = False

    def _effective_postprocess_config_path(self) -> str:
        return self.settings_service.effective_postprocess_config_path()

    def _load_effective_rules_config(self) -> Dict[str, Any]:
        return self.settings_service.load_effective_rules_config()

    def _build_ingest_runner(
        self,
        *,
        archive_root: str | None = None,
        archive_roots_by_family: Mapping[str, str] | None = None,
    ) -> StreamingIngestRunner:
        resolved_archive_root = archive_root or self.get_basic_settings()["archive_root"]
        return StreamingIngestRunner(
            store=self.store,
            archive_root=resolved_archive_root,
            archive_roots_by_family=archive_roots_by_family,
            rules_config=self._load_effective_rules_config(),
        )

    def _build_runtime_install_state(self, **overrides: Any) -> Dict[str, Any]:
        return self.runtime_service._build_runtime_install_state(**overrides)

    def _get_runtime_install_state(self) -> Dict[str, Any]:
        return self.runtime_service.get_runtime_install_state()

    def _build_product_readiness(self, *, browser_runtime: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.runtime_service.build_product_readiness(browser_runtime=browser_runtime)

    def _product_profile_payload(self) -> Dict[str, Any]:
        return self.runtime_service.product_profile_payload()

    def get_basic_settings(self) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.settings_service.get_basic_settings()

    def get_advanced_settings(self) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.settings_service.get_advanced_settings()

    def set_basic_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.settings_service.set_basic_settings(payload)

    def set_advanced_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.settings_service.set_advanced_settings(payload)

    def _visibility_payload(self) -> Dict[str, Any]:
        source_backed_family_ids = _source_backed_family_ids(list_source_descriptors())
        visible_families = [
            str(family.family_id or "").strip()
            for family in list_family_descriptors()
            if str(family.family_id or "").strip() in source_backed_family_ids
            and list_business_descriptors(family_id=family.family_id)
        ]
        return {
            "mode": "listing_only" if visible_families == ["listing"] else "multi_family",
            "visible_families": visible_families,
        }

    def _catalog_active_profile_payload(
        self,
        *,
        visible_family_ids: list[str],
        default_scope: Dict[str, Any],
    ) -> Dict[str, Any]:
        preferred_family_id = str(default_scope.get("record_family") or "").strip()
        if preferred_family_id not in visible_family_ids:
            preferred_family_id = "listing" if "listing" in visible_family_ids else ""
        if not preferred_family_id and visible_family_ids:
            preferred_family_id = visible_family_ids[0]
        profile = (
            get_product_profile(record_family=preferred_family_id)
            if preferred_family_id
            else get_product_profile()
        )
        return {"profile_id": profile.profile_id}

    def _default_scope_resource(self) -> Dict[str, Any]:
        basic = self._read_surface_basic_settings()
        return {
            "stored_preference": dict(basic.get("stored_preference") or {}),
            "effective_scope": dict(basic.get("effective_default_scope") or {}),
            "stale_resolution": dict(basic.get("stale_default_metadata") or {}),
        }

    def _overview_defaults_resource(self) -> Dict[str, Any]:
        basic = self._read_surface_basic_settings()
        advanced = self._read_surface_advanced_settings()
        return {
            "manual_import_input_dir": str(advanced.get("raw_manual_root") or "").strip(),
            "archive_root": os.path.abspath(str(basic.get("archive_root") or "")),
            "default_scope": self._default_scope_resource(),
        }

    def _fallback_basic_settings(self) -> Dict[str, Any]:
        defaults = {
            "default_exchange": "all",
            "default_concurrency": int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            "archive_root": self.default_archive_root,
            "deal_archive_root": os.path.join(self.default_archive_root, "deal"),
            "export_root": self.default_export_root,
            "retention_count": 20,
            "workspace_root": self.app_home,
            "stored_preference": _default_stored_preference(),
        }
        defaults.update(_scope_from_payload(defaults))
        return defaults

    def _fallback_advanced_settings(self) -> Dict[str, Any]:
        basic = self._fallback_basic_settings()
        return {
            "app_home": self.app_home,
            "streaming_db": str(getattr(self.config, "STREAMING_DB_PATH", "")),
            "save_json": False,
            "postprocess_config": self.default_postprocess_config,
            "log_dir": str(self.config.LOG_DIR),
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": basic["archive_root"],
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "archive_root": basic["archive_root"],
            "export_root": basic["export_root"],
            "retention_count": int(basic.get("retention_count") or 20),
            "stored_preference": dict(basic.get("stored_preference") or {}),
            "effective_default_scope": dict(basic.get("effective_default_scope") or {}),
            "stale_default_metadata": dict(basic.get("stale_default_metadata") or {}),
        }

    def _read_surface_basic_settings(self) -> Dict[str, Any]:
        try:
            return _required_mapping_snapshot(
                self.get_basic_settings(),
                field_name="basic settings",
            )
        except AppUserFacingError as exc:
            if getattr(exc, "error_code", "") != ERROR_SCHEMA_NOT_READY:
                raise
            return self._fallback_basic_settings()

    def _read_surface_advanced_settings(self) -> Dict[str, Any]:
        try:
            return _required_mapping_snapshot(
                self.get_advanced_settings(),
                field_name="advanced settings",
            )
        except AppUserFacingError as exc:
            if getattr(exc, "error_code", "") != ERROR_SCHEMA_NOT_READY:
                raise
            return self._fallback_advanced_settings()

    def get_catalog(self) -> Dict[str, Any]:
        visible_families: list[Dict[str, Any]] = []
        support_matrix: Dict[str, Dict[str, Dict[str, bool]]] = {}
        surface_source_matrix: Dict[str, Dict[str, Dict[str, list[str]]]] = {}
        source_descriptors = list_source_descriptors()
        source_ids_by_family = _enabled_source_ids_by_family(source_descriptors)
        source_backed_family_ids = set(source_ids_by_family)
        runtime_bindings = list(iter_source_business_bindings())
        executable_sources_by_scope: Dict[tuple[str, str], set[str]] = {}
        for binding in runtime_bindings:
            if not bool(getattr(binding, "implemented", True)):
                continue
            key = (str(binding.record_family or "").strip(), str(binding.business_id or "").strip())
            executable_sources_by_scope.setdefault(key, set()).add(str(binding.source_id or "").strip())

        for family in list_family_descriptors():
            family_id = str(getattr(family, "family_id", "") or "").strip()
            if family_id not in source_backed_family_ids:
                continue
            family_businesses = list_business_descriptors(family_id=family.family_id)
            if not family_businesses:
                continue
            business_entries: list[Dict[str, Any]] = []
            family_matrix: Dict[str, Dict[str, bool]] = {}
            family_surface_sources: Dict[str, Dict[str, list[str]]] = {}
            source_backed_ids = set(source_ids_by_family.get(family_id, set()))
            expected_source_ids = {
                str(source_id or "").strip()
                for source_id in tuple(getattr(family, "source_ids", ()) or ())
                if str(source_id or "").strip()
            }
            expected_source_ids = (
                expected_source_ids & source_backed_ids
                if expected_source_ids
                else source_backed_ids
            )
            if not expected_source_ids:
                continue
            for business in family_businesses:
                business_label = business.canonical_label
                executable_sources = executable_sources_by_scope.get((family_id, business.business_id), set())
                try:
                    supported_sources = {
                        surface: list(
                            supported_sources_for_surface(
                                record_family=family_id,
                                business_id=business.business_id,
                                surface=surface,
                            )
                        )
                        for surface in KNOWN_SURFACES
                    }
                except KeyError:
                    supported_sources = {surface: [] for surface in KNOWN_SURFACES}
                for surface in ("records", "export"):
                    supported_sources[surface] = [
                        source_id
                        for source_id in supported_sources[surface]
                        if source_id in expected_source_ids
                    ]
                supported_sources["one_click"] = [
                    source_id
                    for source_id in supported_sources["one_click"]
                    if source_id in expected_source_ids and source_id in executable_sources
                ]
                if not supported_sources["one_click"]:
                    supported_sources["one_click"] = [
                        source_id
                        for source_id in sorted(executable_sources)
                        if source_id in expected_source_ids
                    ]
                supported_surfaces = {
                    surface: bool(supported_sources[surface])
                    for surface in KNOWN_SURFACES
                }
                family_matrix[business.business_id] = dict(supported_surfaces)
                family_surface_sources[business.business_id] = {
                    surface: list(supported_sources[surface])
                    for surface in KNOWN_SURFACES
                }
                business_entries.append(
                    {
                        "business_id": business.business_id,
                        "business_label": business_label,
                        "supported_surfaces": [key for key, enabled in supported_surfaces.items() if enabled],
                    }
                )
            visible_families.append(
                {
                    "family_id": family.family_id,
                    "family_label": family.canonical_label,
                    "businesses": business_entries,
                }
            )
            support_matrix[family.family_id] = family_matrix
            surface_source_matrix[family.family_id] = family_surface_sources

        default_scope = dict(self._read_surface_basic_settings().get("effective_default_scope") or {})
        visibility = self._visibility_payload()
        visible_family_ids = [
            str(item.get("family_id") or "").strip()
            for item in visible_families
            if str(item.get("family_id") or "").strip()
        ]

        return {
            "active_profile": self._catalog_active_profile_payload(
                visible_family_ids=visible_family_ids,
                default_scope=default_scope,
            ),
            "visible_families": visible_families,
            "sources": [
                {
                    "source_id": source.source_id,
                    "source_label": source.canonical_label,
                    "record_families": list(source.supported_record_families),
                }
                for source in source_descriptors
            ],
            "support_matrix": support_matrix,
            "surface_source_matrix": surface_source_matrix,
            "source_business_requirements": _source_business_requirements_payload(),
            "default_scope": default_scope,
            "visibility": visibility,
        }

    def choose_local_path(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        request = _optional_payload_object(payload)
        selection_kind = _parse_text(request.get("selection_kind"), field_name="selection_kind", default="directory").lower() or "directory"
        prompt = _parse_text(request.get("prompt"), field_name="prompt", default="选择路径")
        current_path = _parse_local_path(request.get("current_path"), field_name="current_path")
        if selection_kind not in {"directory", "file"}:
            raise AppUserFacingError(
                message=f"不支持的选择类型：{selection_kind}",
                error_code=ERROR_INVALID_PATH_SELECTION_KIND,
                http_status=400,
                details={"selection_kind": selection_kind},
            )
        try:
            selected_path = pick_local_path(
                selection_kind=selection_kind,
                current_path=current_path,
                prompt=prompt,
            )
        except LocalPathError as exc:
            raise AppUserFacingError(
                message=str(exc),
                error_code=ERROR_LOCAL_PATH_PICKER_FAILED,
                http_status=500,
                details={"selection_kind": selection_kind},
            ) from exc
        return {
            "selected": bool(selected_path),
            "path": _normalize_local_path(selected_path),
            "selection_kind": selection_kind,
        }

    def open_local_path(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        request = _optional_payload_object(payload)
        path_value = _parse_local_path(request.get("path"), field_name="path")
        reveal = _parse_bool(request.get("reveal"), field_name="reveal")
        if not path_value:
            raise AppUserFacingError(
                message="path is required",
                error_code=ERROR_LOCAL_PATH_REQUIRED,
                http_status=400,
            )
        try:
            opened_path = reveal_in_file_manager(path_value, reveal=reveal)
        except LocalPathError as exc:
            raise AppUserFacingError(
                message=str(exc),
                error_code=ERROR_LOCAL_PATH_OPEN_FAILED,
                http_status=400,
                details={"path": path_value, "reveal": reveal},
            ) from exc
        return {
            "opened": True,
            "path": opened_path,
            "reveal": reveal,
        }

    def list_exports_history(self, *, limit: int = 100) -> Dict[str, Any]:
        self._require_schema_ready()
        rows: list[Dict[str, Any]] = []
        for item in self.pipeline_repository.list_exports(limit=limit):
            summary = _optional_mapping_snapshot(item.get("summary"), field_name="export history summary")
            manifest = _optional_mapping_snapshot(
                summary.get("manifest"),
                field_name="export history summary.manifest",
            )
            pruned_by_retention = bool(item.get("pruned_by_retention") or summary.get("pruned_by_retention"))
            artifact_state = _export_artifact_availability(
                summary,
                is_tombstone=bool(item.get("is_tombstone")),
                pruned_by_retention=pruned_by_retention,
                managed_export_root=str(item.get("output_dir") or "").strip(),
            )
            rows.append(
                {
                    "export_id": str(item.get("export_id") or ""),
                    "cursor_id": str(item.get("cursor_id") or ""),
                    "requested_export_mode": str(summary.get("requested_export_mode") or ""),
                    "revision_watermark": int(
                        summary.get("revision_watermark") or manifest.get("revision_watermark") or 0
                    ),
                    "created_at": str(item.get("created_at") or ""),
                    "artifact_count": int(artifact_state["artifact_count"]),
                    "openable": bool(artifact_state["openable"]),
                    "rebuildable": bool(artifact_state["rebuildable"]),
                    "is_tombstone": bool(artifact_state["is_tombstone"]),
                    "pruned_by_retention": pruned_by_retention,
                    "retention_status": str(artifact_state["retention_status"]),
                    "retention_count": int(item.get("retention_count") or summary.get("retention_count") or 20),
                }
            )
        return {"rows": rows}

    def get_export_history_detail(self, export_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        normalized_export_id = _parse_text(export_id, field_name="export_id")
        if not normalized_export_id:
            raise ValueError("export_id is required")
        item = self.pipeline_repository.get_export(normalized_export_id)
        summary = _optional_mapping_snapshot(item.get("summary"), field_name="export history summary")
        manifest = _optional_mapping_snapshot(
            summary.get("manifest"),
            field_name="export history summary.manifest",
        )
        cursor_value = _optional_mapping_snapshot(
            summary.get("cursor_value"),
            field_name="export history summary.cursor_value",
        )
        pruned_by_retention = bool(item.get("pruned_by_retention") or summary.get("pruned_by_retention"))
        artifact_state = _export_artifact_availability(
            summary,
            is_tombstone=bool(item.get("is_tombstone")),
            pruned_by_retention=pruned_by_retention,
            managed_export_root=str(item.get("output_dir") or "").strip(),
        )
        if not manifest:
            manifest = self.pipeline_repository.get_export_manifest(normalized_export_id)
        if not cursor_value:
            cursor_value = self.pipeline_repository.get_export_cursor_value(str(item.get("cursor_id") or ""))
        missing_snapshots: list[str] = []
        if not manifest:
            missing_snapshots.append("manifest")
        if not cursor_value:
            missing_snapshots.append("cursor_value")
        return {
            "export_id": str(item.get("export_id") or ""),
            "cursor_id": str(item.get("cursor_id") or ""),
            "requested_export_mode": str(summary.get("requested_export_mode") or ""),
            "revision_watermark": int(
                summary.get("revision_watermark") or manifest.get("revision_watermark") or 0
            ),
            "created_at": str(item.get("created_at") or ""),
            "artifacts": list(artifact_state["artifacts"]),
            "existing_artifacts": list(artifact_state["existing_artifacts"]),
            "missing_artifacts": list(artifact_state["missing_artifacts"]),
            "manifest": manifest,
            "cursor_value": cursor_value,
            "snapshot_status": "missing" if missing_snapshots else "available",
            "missing_snapshots": missing_snapshots,
            "openable": bool(artifact_state["openable"]),
            "rebuildable": bool(artifact_state["rebuildable"]),
            "is_tombstone": bool(artifact_state["is_tombstone"]),
            "pruned_by_retention": pruned_by_retention,
            "retention_status": str(artifact_state["retention_status"]),
            "retention_count": int(item.get("retention_count") or summary.get("retention_count") or 20),
        }

    def open_export_history(self, export_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        detail = self.get_export_history_detail(export_id)
        if not detail.get("openable"):
            return {
                "export_id": str(detail.get("export_id") or ""),
                "opened": False,
                "openable": False,
                "rebuildable": False,
                "is_tombstone": bool(detail.get("is_tombstone")),
                "retention_status": str(detail.get("retention_status") or ""),
            }
        existing_artifacts = _required_non_empty_text_list_snapshot(
            detail.get("existing_artifacts"),
            field_name="existing_artifacts",
        )
        first_artifact = existing_artifacts[0]
        opened_path = reveal_in_file_manager(first_artifact, reveal=True)
        return {
            "export_id": str(detail.get("export_id") or ""),
            "opened": True,
            "path": opened_path,
            "openable": True,
            "rebuildable": True,
            "is_tombstone": False,
            "retention_status": str(detail.get("retention_status") or "available"),
        }

    def download_export_history(self, export_id: str, *, output_dir: str = "") -> Dict[str, Any]:
        self._require_schema_ready()
        target_root = _parse_local_path(output_dir, field_name="output_dir")
        if not target_root:
            current_settings = self.get_basic_settings()
            target_root = _parse_local_path(
                current_settings.get("export_root"),
                field_name="export_root",
            )
        if not target_root:
            raise ValueError("output_dir is required")
        detail = self.get_export_history_detail(export_id)
        if not detail.get("openable"):
            return {
                "export_id": str(detail.get("export_id") or ""),
                "downloaded": False,
                "openable": False,
                "rebuildable": False,
                "is_tombstone": bool(detail.get("is_tombstone")),
                "retention_status": str(detail.get("retention_status") or ""),
                "artifacts": [],
            }
        existing_artifacts = _required_non_empty_text_list_snapshot(
            detail.get("existing_artifacts"),
            field_name="existing_artifacts",
        )
        manifest = _required_mapping_snapshot(detail.get("manifest"), field_name="manifest")
        _verified_export_artifact_checksums(manifest, existing_artifacts)
        os.makedirs(target_root, exist_ok=True)
        target_root_real = os.path.realpath(os.path.abspath(target_root))
        destinations: list[tuple[str, str]] = []
        destination_keys: set[str] = set()
        for src in existing_artifacts:
            dst = os.path.join(target_root, os.path.basename(src))
            key = os.path.normcase(os.path.abspath(dst))
            if key in destination_keys:
                raise ValueError("export artifacts have colliding destination names")
            destination_keys.add(key)
            destinations.append((src, dst))

        created: list[tuple[str, tuple[int, int] | None]] = []
        result_paths: list[str] = []
        try:
            for src, dst in destinations:
                source_dir_real = os.path.realpath(os.path.dirname(os.path.abspath(src)))
                if source_dir_real == target_root_real:
                    result_paths.append(src)
                    continue
                if os.path.lexists(dst):
                    raise FileExistsError(f"destination already exists: {dst}")
                _copy_export_artifact_exclusive(src, dst)
                try:
                    destination_stat = os.lstat(dst)
                    if stat.S_ISLNK(destination_stat.st_mode) or not stat.S_ISREG(destination_stat.st_mode):
                        raise RuntimeError(f"export destination is not a regular file: {dst}")
                    destination_identity: tuple[int, int] | None = (
                        int(destination_stat.st_dev),
                        int(destination_stat.st_ino),
                    )
                except OSError as exc:
                    raise RuntimeError(f"export destination cannot be inspected: {dst}: {exc}") from exc
                created.append((dst, destination_identity))
                result_paths.append(dst)
            # Verify every destination, including same-directory no-op paths.
            # A source-only recheck would miss a target that was modified or
            # replaced after the copy completed.
            expected_checksums = _verified_export_artifact_checksums(manifest, existing_artifacts)
            for src, dst in destinations:
                actual = _export_artifact_sha256(dst)
                expected = expected_checksums.get(src)
                if expected is None or actual != expected:
                    raise RuntimeError(
                        f"export destination checksum mismatch: {dst}: expected {expected}, got {actual}"
                    )
        except Exception:
            for path, identity in reversed(created):
                try:
                    current_stat = os.lstat(path)
                    if identity is None or (
                        stat.S_ISREG(current_stat.st_mode)
                        and (int(current_stat.st_dev), int(current_stat.st_ino)) == identity
                    ):
                        os.remove(path)
                except OSError:
                    continue
            raise
        return {
            "export_id": str(detail.get("export_id") or ""),
            "downloaded": bool(result_paths),
            "openable": True,
            "rebuildable": True,
            "is_tombstone": False,
            "retention_status": str(detail.get("retention_status") or "available"),
            "artifacts": result_paths,
        }

    def reveal_record_folder(self, record_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        normalized_record_id = _parse_text(record_id, field_name="record_id")
        record = self.pipeline_repository.load_record(normalized_record_id)
        verdict = resolve_artifact_evidence_verdict(record)
        artifact_path = str(verdict.inspection_openable_path or "").strip()
        if verdict.status not in _RECORD_REVEAL_EVIDENCE_STATUSES or not artifact_path:
            raise AppUserFacingError(
                message=f"记录未找到可定位的网页文件：{normalized_record_id}",
                error_code=ERROR_RECORD_ARTIFACT_NOT_FOUND,
                http_status=404,
                details={
                    "record_id": normalized_record_id,
                    "evidence_status": verdict.status,
                    "evidence_reason_code": verdict.reason_code,
                    "authoritative_path": verdict.authoritative_path,
                },
            )
        try:
            opened_path = reveal_in_file_manager(artifact_path, reveal=True)
        except LocalPathError as exc:
            raise AppUserFacingError(
                message=str(exc),
                error_code=ERROR_RECORD_ARTIFACT_OPEN_FAILED,
                http_status=400,
                details={"record_id": normalized_record_id, "path": artifact_path},
            ) from exc
        return {
            "record_id": normalized_record_id,
            "path": opened_path,
            "artifact_name": os.path.basename(artifact_path),
            "opened": True,
        }

    def health(self) -> Dict[str, Any]:
        runtime_payload = self.runtime_service.runtime_payload()
        if not self._schema_ready:
            return {
                "ok": False,
                "db_path": self.db_path,
                "workspace_root": self.app_home,
                "app_home": self.app_home,
                "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
                "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
                "log_dir": str(self.config.LOG_DIR),
                "browser_runtime": runtime_payload["browser"],
                "browser_install": runtime_payload["install"],
                "product_readiness": runtime_payload["readiness"],
                "visibility": self._visibility_payload(),
                "schema": self._schema_readiness_payload(),
            }
        return {
            "ok": bool(self._schema_ready),
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "archive_root": self.get_basic_settings()["archive_root"],
            "export_root": self.get_basic_settings()["export_root"],
            "app_home": self.app_home,
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": self.get_basic_settings()["archive_root"],
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "log_dir": str(self.config.LOG_DIR),
            "browser_runtime": runtime_payload["browser"],
            "browser_install": runtime_payload["install"],
            "product_readiness": runtime_payload["readiness"],
            "visibility": self._visibility_payload(),
            "schema": self._schema_readiness_payload(),
        }

    def readiness(self) -> Dict[str, Any]:
        return {
            "ok": bool(self._schema_ready),
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "app_home": self.app_home,
            "schema": self._schema_readiness_payload(),
        }

    def overview(self) -> Dict[str, Any]:
        basic = self._read_surface_basic_settings()
        snapshot = self.pipeline_repository.load_overview_snapshot(recent_job_limit=5) if self._schema_ready else None
        jobs = list(snapshot.recent_jobs) if snapshot is not None else []
        latest = snapshot.latest_job if snapshot is not None else None
        runtime_payload = self.runtime_service.runtime_payload()
        return {
            "archive_root": basic["archive_root"],
            "export_root": basic["export_root"],
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "app_home": self.app_home,
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": basic["archive_root"],
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "runtime": runtime_payload,
            "record_summary": {
                "state_counts": dict(snapshot.record_state_counts) if snapshot is not None else {},
                "pending_mapping_count": snapshot.pending_mapping_count if snapshot is not None else 0,
            },
            "latest_job": latest,
            "latest_progress": self._build_latest_progress(latest) if latest is not None else {},
            "recent_jobs": jobs,
            "visibility": self._visibility_payload(),
            "defaults": self._overview_defaults_resource(),
            "schema": self._schema_readiness_payload(),
        }

    def get_runtime_dependencies(self) -> Dict[str, Any]:
        return self.runtime_service.runtime_payload()

    def _build_latest_progress(self, latest_job: Dict[str, Any] | None) -> Dict[str, Any]:
        return self.execution_service.build_latest_progress(latest_job)

    def list_records(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.records_service.list_records(payload)

    def acknowledge_field_missing(self, record_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        normalized_record_id = _parse_text(record_id, field_name="record_id")
        record = self.pipeline_repository.load_record(normalized_record_id)
        if str(record.get("state") or "").strip() != "field_missing":
            raise AppUserFacingError(
                message=f"record is not in field_missing state: {normalized_record_id}",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"record_id": normalized_record_id, "state": str(record.get("state") or "")},
            )
        missing_fields: list[Dict[str, Any]] = []
        findings = record.get("findings")
        if findings is None:
            findings = []
        if not isinstance(findings, list):
            raise ValueError("findings must be a list")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("findings entries must be objects")
            if str(finding.get("type") or "").strip() not in {"export_field_missing", "canonical_field_missing"}:
                continue
            evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
            raw_missing_fields = evidence.get("missing_fields")
            if raw_missing_fields is None:
                raw_missing_fields = []
            if not isinstance(raw_missing_fields, list):
                raise ValueError("missing_fields must be a list")
            missing_fields.extend(
                {"field": str(field or "").strip(), "export_field": str(field or "").strip(), "kind": "export"}
                for field in raw_missing_fields
                if str(field or "").strip()
            )
            if not missing_fields and str(finding.get("message") or "").strip():
                missing_fields.append({"field": str(finding.get("message") or "").strip(), "kind": "export"})
        if not missing_fields:
            raise ValueError("missing_fields is empty")
        acknowledged = self.pipeline_repository.acknowledge_field_missing(
            record_id=normalized_record_id,
            missing_fields=missing_fields,
        )
        payload = self.records_service.row_from_record(acknowledged)
        return {
            "record_id": payload["record_id"],
            "state": payload["state"],
            "field_missing_acknowledgement": payload["field_missing_acknowledgement"],
            "attention": payload["attention"],
            "export_eligible": payload["export_eligible"],
            "exportable": payload["exportable"],
        }

    def launch_browser_runtime_install(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.runtime_service.launch_browser_runtime_install(payload)

    def install_browser_runtime(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.runtime_service.install_browser_runtime(payload)

    def list_jobs(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        self._require_schema_ready()
        return self.execution_service.list_jobs(limit=limit)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.get_job(job_id)

    def get_job_events(self, job_id: str, *, limit: int = 200) -> list[Dict[str, Any]]:
        self._require_schema_ready()
        return self.execution_service.get_job_events(job_id, limit=limit)

    def count_job_events(self, job_id: str) -> int:
        self._require_schema_ready()
        return self.execution_service.count_job_events(job_id)

    def build_job_progress(self, job: Dict[str, Any] | None) -> Dict[str, Any]:
        return self.execution_service.build_latest_progress(job)

    def _build_mapping_work_item(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.mapping_service.build_mapping_work_item(record)

    def list_pending_mappings(self) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.mapping_service.list_pending_mappings()

    def list_review_problems(self, query: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.review_problem_service.list_review_problems(query)

    def _enrich_mapping_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return self.mapping_service.enrich_mapping_entry(entry)

    def list_mapping_entries(self) -> list[Dict[str, Any]]:
        self._require_schema_ready()
        return self.mapping_service.list_mapping_entries()

    def _get_mapping_entry(self, *, entry_id: str) -> Dict[str, Any]:
        return self.mapping_service.get_mapping_entry(entry_id=entry_id)

    def _start_background_thread(self, *, name: str, target) -> None:
        self.execution_service.start_background_thread(name=name, target=target)

    def _reserve_mutating_job(self, job_type: str) -> None:
        self.execution_service.reserve_mutating_job(job_type)

    def _release_mutating_job(self, job_type: str, *, job_id: str | None = None) -> bool:
        return self.execution_service.release_mutating_job(job_type, job_id=job_id)

    @contextmanager
    def _mutating_job_scope(self, job_type: str):
        with self.execution_service.mutating_job_scope(job_type):
            yield

    def _thread_job_stack(self) -> list[str]:
        return self.execution_service.thread_job_stack()

    @contextmanager
    def _thread_job_scope(self, job_type: str):
        with self.execution_service.thread_job_scope(job_type):
            yield

    def _current_thread_holds_mutating_job(self, job_type: str) -> bool:
        return self.execution_service.current_thread_holds_mutating_job(job_type)

    def _fail_active_background_job(
        self,
        job_id: str,
        *,
        job_type: str,
        stage: str,
        exc: Exception,
    ) -> None:
        self.execution_service.fail_active_job(
            job_id,
            job_type=job_type,
            stage=stage,
            exc=exc,
        )

    def _find_records_for_mapping_refresh(self, *, match_field: str, source_name: str) -> list[Dict[str, Any]]:
        return self.mapping_service.find_records_for_mapping_refresh(match_field=match_field, source_name=source_name)

    def _find_records_for_mapping_refresh_specs(self, specs: list[Dict[str, str]]) -> list[Dict[str, Any]]:
        return self.mapping_service.find_records_for_mapping_refresh_specs(specs)

    def _find_pending_mapping_records(self) -> list[Dict[str, Any]]:
        return self.mapping_service.find_pending_mapping_records()

    def _select_business_re_evaluation_items(self, payload: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
        return self.mapping_service.select_business_re_evaluation_items(payload)

    def _select_mapping_refresh_items(self, payload: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
        request = normalize_mapping_record_selection_request(payload)
        requested_record_id_values = _required_list_snapshot(
            request.get("record_ids"),
            field_name="record_ids",
        )
        requested_record_ids = {
            str(value or "").strip()
            for value in requested_record_id_values
            if str(value or "").strip()
        }
        backlog = self.list_pending_mappings()
        backlog_sections = _optional_list_snapshot(
            _optional_mapping_snapshot(backlog, field_name="mapping backlog").get("sections"),
            field_name="mapping backlog sections",
        )
        sections: Dict[str, Mapping[str, Any]] = {}
        for section in backlog_sections:
            if not isinstance(section, Mapping):
                raise ValueError("mapping backlog sections[*] must be an object")
            sections[str(section.get("section_id") or "")] = section
        mapping_section = sections.get("mapping_gap_resolution")
        mapping_items = (
            _optional_list_snapshot(
                mapping_section.get("items"),
                field_name="mapping_gap_resolution.items",
            )
            if mapping_section is not None
            else []
        )
        for item in mapping_items:
            if not isinstance(item, Mapping):
                raise ValueError("mapping_gap_resolution.items[*] must be an object")
        if requested_record_ids:
            mapping_items = [item for item in mapping_items if str(item.get("record_id") or "") in requested_record_ids]
        return mapping_items

    def _find_existing_mapping_entry(self, *, source_name: str, match_field: str, target_field: str) -> Dict[str, Any] | None:
        return self.mapping_service.find_existing_mapping_entry(
            source_name=source_name,
            match_field=match_field,
            target_field=target_field,
        )

    def preview_mapping_upsert(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.mapping_service.preview_mapping_upsert(payload)

    def _run_mapping_refresh_job(self, *, job_id: str, record_ids: list[str], refresh_fn=None) -> None:
        self.pipeline_repository.ensure_mapping_refresh_job_running(job_id)
        refreshed = 0
        pending_mapping = 0
        mapping_conflict = 0
        skipped = 0
        failed = 0
        accepted_completed = 0
        refresh = refresh_fn or self.refresh_record_postprocess
        for index, record_id in enumerate(record_ids, start=1):
            if str(self.pipeline_repository.get_job(job_id).get("status") or "").strip() != "running":
                return
            self.pipeline_repository.append_mapping_refresh_progress(
                job_id=job_id,
                record_id=record_id,
                index=index,
                total=len(record_ids),
            )
            try:
                result = refresh(record_id)
                state = _normalize_record_state_value(result.get("state"))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.pipeline_repository.record_mapping_refresh_failure(
                    job_id=job_id,
                    record_id=record_id,
                    error_message=str(exc),
                )
                continue

            refreshed += 1
            if state == "pending_mapping":
                pending_mapping += 1
            elif state == "mapping_conflict":
                mapping_conflict += 1
            elif state == "skipped":
                skipped += 1
                accepted_completed += 1
            elif state in {"ready", "conflict"}:
                accepted_completed += 1
            elif state not in {"ready", "conflict"}:
                failed += 1
            self.pipeline_repository.record_mapping_refresh_result(
                job_id=job_id,
                record_id=record_id,
                result=result,
                state=state,
            )

        self.pipeline_repository.finish_mapping_refresh_job(
            job_id=job_id,
            refreshed_count=refreshed,
            pending_mapping_count=pending_mapping,
            mapping_conflict_count=mapping_conflict,
            skipped_count=skipped,
            failed_count=failed,
        )

    def _run_business_re_evaluation_job(self, *, job_id: str, record_ids: list[str]) -> None:
        self.pipeline_repository.ensure_job_running(job_id)
        refreshed = 0
        pending_review = 0
        pending_mapping = 0
        mapping_conflict = 0
        skipped = 0
        failed = 0
        accepted_completed = 0
        for index, record_id in enumerate(record_ids, start=1):
            if str(self.pipeline_repository.get_job(job_id).get("status") or "").strip() != "running":
                return
            self.pipeline_repository.append_business_re_evaluation_progress(
                job_id=job_id,
                record_id=record_id,
                index=index,
                total=len(record_ids),
            )
            try:
                result = self._refresh_record_postprocess(
                    record_id,
                    caller="business_re_evaluation",
                    job_id=job_id,
                )
                state = _normalize_record_state_value(result.get("state"))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.pipeline_repository.record_business_re_evaluation_failure(
                    job_id=job_id,
                    record_id=record_id,
                    error_message=str(exc),
                )
                continue

            refreshed += 1
            if state == "pending_review":
                pending_review += 1
            elif state == "pending_mapping":
                pending_mapping += 1
            elif state == "mapping_conflict":
                mapping_conflict += 1
            elif state == "skipped":
                skipped += 1
                accepted_completed += 1
            elif state in {"ready", "conflict"}:
                accepted_completed += 1
            elif state not in {"ready", "conflict"}:
                failed += 1
            self.pipeline_repository.record_business_re_evaluation_result(
                job_id=job_id,
                record_id=record_id,
                result=result,
                state=state,
            )

        unresolved = pending_review + pending_mapping + mapping_conflict
        self.pipeline_repository.finish_job(
            job_id,
            status="success"
            if refreshed > 0 and failed <= 0 and unresolved <= 0 and skipped <= 0
            else "success_with_warnings" if refreshed > 0 else "failed",
            summary={
                "refreshed_count": refreshed,
                "pending_review_count": pending_review,
                "pending_mapping_count": pending_mapping,
                "mapping_conflict_count": mapping_conflict,
                "skipped_count": skipped,
                "failed_count": failed,
                "accepted_completed_count": accepted_completed,
            },
        )

    def _launch_mapping_refresh_job(
        self,
        *,
        source_name: str,
        match_field: str,
        target_field: str,
        entry_id: str,
        on_job_bound: Callable[[str], None] | None = None,
    ) -> Dict[str, Any]:
        affected_records = self._find_records_for_mapping_refresh(match_field=match_field, source_name=source_name)
        return self._launch_mapping_refresh_job_for_records(
            record_ids=[str(item["record_id"]) for item in affected_records],
            metadata={
                "entry_id": entry_id,
                "source_name": source_name,
                "match_field": match_field,
                "target_field": target_field,
                "affected_count": len(affected_records),
                **_mapping_refresh_scope_metadata(affected_records),
            },
            on_job_bound=on_job_bound,
        )

    def _launch_mapping_refresh_job_for_records(
        self,
        *,
        record_ids: list[str],
        metadata: Mapping[str, Any],
        on_job_bound: Callable[[str], None] | None = None,
    ) -> Dict[str, Any]:
        if not isinstance(record_ids, (list, tuple)):
            raise ValueError("record_ids must be a list of non-empty strings")
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        job_metadata = dict(metadata)
        unique_record_ids: list[str] = []
        seen_record_ids: set[str] = set()
        for record_id in record_ids:
            if not isinstance(record_id, str):
                raise ValueError("record_ids entries must be non-empty strings")
            normalized_record_id = record_id.strip()
            if not normalized_record_id:
                raise ValueError("record_ids entries must be non-empty strings")
            if normalized_record_id in seen_record_ids:
                continue
            seen_record_ids.add(normalized_record_id)
            unique_record_ids.append(normalized_record_id)
        if not unique_record_ids:
            return {"job_id": "", "job_type": "mapping_refresh", "affected_count": 0}
        job_id = self.pipeline_repository.create_mapping_refresh_job(
            metadata=job_metadata | {"affected_count": len(unique_record_ids)},
        )
        self.execution_service.bind_mutating_job("mapping_refresh", job_id=job_id)
        if on_job_bound is not None:
            on_job_bound(str(job_id))

        def refresh_fn(record_id: str) -> Dict[str, Any]:
            return self._refresh_record_postprocess(
                record_id,
                caller="mapping_refresh",
                job_id=job_id,
            )

        def _run_mapping_refresh_wrapper() -> None:
            try:
                self.execution_service.bind_mutating_job(
                    "mapping_refresh",
                    job_id=job_id,
                    worker_thread_name=threading.current_thread().name,
                )
                with self._thread_job_scope("mapping_refresh"):
                    self._run_mapping_refresh_job(
                        job_id=job_id,
                        record_ids=unique_record_ids,
                        refresh_fn=refresh_fn,
                    )
            except Exception as exc:  # noqa: BLE001
                self._fail_active_background_job(
                    job_id,
                    job_type="mapping_refresh",
                    stage="mapping_refresh",
                    exc=exc,
                )
                raise
            finally:
                self._release_mutating_job("mapping_refresh", job_id=job_id)

        worker_name = f"peap-mapping-refresh-{int(time.time())}"
        self.execution_service.bind_mutating_job(
            "mapping_refresh",
            job_id=job_id,
            worker_thread_name=worker_name,
        )
        self._start_background_thread(
            name=worker_name,
            target=_run_mapping_refresh_wrapper,
        )
        return {"job_id": job_id, "job_type": "mapping_refresh", "affected_count": len(unique_record_ids)}

    def resolve_mapping_conflict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.mapping_service.resolve_mapping_conflict(payload, upsert_mapping=self.upsert_mapping)

    def startup_session_id(self) -> str:
        return self._startup_session_id

    def mapping_undo_state(self) -> Dict[str, Any]:
        with self._undo_lock:
            operation = dict(self._mapping_undo_stack[-1]) if self._mapping_undo_stack else {}
        return {
            "available": bool(operation),
            "startup_session_id": self._startup_session_id,
            "operation_kind": str(operation.get("kind") or "").strip(),
        }

    def undo_last_mapping_operation(self, *, startup_session_id: str) -> Dict[str, Any]:
        normalized_session_id = str(startup_session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("startup_session_id is required")
        if normalized_session_id != self._startup_session_id:
            raise ValueError("startup_session_id mismatch; undo is only available in current backend startup session")
        with self._undo_lock:
            if not self._mapping_undo_stack:
                raise ValueError("no undoable mapping operation in current startup session")
            operation = dict(self._mapping_undo_stack[-1])
            undo_kind = str(operation.get("kind") or "").strip()
            if undo_kind == "upsert":
                entry_id = str(operation.get("entry_id") or "").strip()
                if entry_id:
                    self.delete_mapping(entry_id, _record_undo=False)
                result = {"undone": True, "undo_kind": undo_kind, "entry_id": entry_id}
            elif undo_kind == "update":
                current_entry_id = str(operation.get("current_entry_id") or "").strip()
                previous_entry = _undo_entry_snapshot(operation, "previous_entry")
                restored = self.update_mapping(
                    current_entry_id,
                    {
                        "rule_kind": str(previous_entry.get("rule_kind") or ""),
                        "source_name": str(previous_entry.get("source_name") or ""),
                        "target_value": str(previous_entry.get("target_value") or ""),
                        "notes": str(previous_entry.get("notes") or ""),
                        "confirm_overwrite": True,
                    },
                    _record_undo=False,
                )
                result = {
                    "undone": True,
                    "undo_kind": undo_kind,
                    "entry_id": str(restored.get("entry_id") or current_entry_id),
                }
            elif undo_kind == "delete":
                deleted_entry = _undo_entry_snapshot(operation, "deleted_entry")
                restored = self.upsert_mapping(
                    {
                        "rule_kind": str(deleted_entry.get("rule_kind") or ""),
                        "source_name": str(deleted_entry.get("source_name") or ""),
                        "target_value": str(deleted_entry.get("target_value") or ""),
                        "notes": str(deleted_entry.get("notes") or ""),
                        "confirm_overwrite": True,
                    },
                    _record_undo=False,
                )
                result = {
                    "undone": True,
                    "undo_kind": undo_kind,
                    "entry_id": str(restored.get("entry_id") or ""),
                }
            else:
                raise ValueError("unsupported undo operation")
            self._mapping_undo_stack.pop()
            return result

    def launch_pending_mapping_refresh(self, _payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._require_schema_ready()
        request = normalize_mapping_record_selection_request(_payload)
        background_launched = False
        lease_job_id = ""
        self._reserve_mutating_job("mapping_refresh")
        try:
            affected_records = self._select_mapping_refresh_items(request)
            if not affected_records:
                if request.get("record_ids"):
                    raise ValueError("no actionable mapping refresh records selected")
                return {"job_id": "", "job_type": "mapping_refresh", "affected_count": 0}
            scope_metadata = _mapping_refresh_scope_metadata(affected_records)
            if "scope" in scope_metadata:
                scope_metadata["scope"] = {
                    **dict(scope_metadata["scope"]),
                    "state": "pending_mapping",
                    "workflow": "mapping_resolution",
                }
            job_id = self.pipeline_repository.create_job(
                "mapping_refresh",
                metadata={
                    "affected_count": len(affected_records),
                    "state": "pending_mapping",
                    "workflow": "mapping_resolution",
                    **scope_metadata,
                },
            )
            lease_job_id = str(job_id)
            self.execution_service.bind_mutating_job("mapping_refresh", job_id=job_id)
            record_ids = [str(item["record_id"]) for item in affected_records]

            def _run_pending_mapping_refresh_wrapper() -> None:
                try:
                    self.execution_service.bind_mutating_job(
                        "mapping_refresh",
                        job_id=job_id,
                        worker_thread_name=threading.current_thread().name,
                    )
                    with self._thread_job_scope("mapping_refresh"):
                        def refresh_pending_record(record_id: str) -> Dict[str, Any]:
                            return self._refresh_record_postprocess(
                                record_id,
                                caller="mapping_refresh",
                                job_id=job_id,
                            )

                        self._run_mapping_refresh_job(
                            job_id=job_id,
                            record_ids=record_ids,
                            refresh_fn=refresh_pending_record,
                        )
                except Exception as exc:  # noqa: BLE001
                    self._fail_active_background_job(
                        job_id,
                        job_type="mapping_refresh",
                        stage="mapping_refresh",
                        exc=exc,
                    )
                    raise
                finally:
                    self._release_mutating_job("mapping_refresh", job_id=job_id)

            worker_name = f"peap-pending-mapping-refresh-{int(time.time())}"
            self.execution_service.bind_mutating_job(
                "mapping_refresh",
                job_id=job_id,
                worker_thread_name=worker_name,
            )
            self._start_background_thread(
                name=worker_name,
                target=_run_pending_mapping_refresh_wrapper,
            )
            background_launched = True
            return {"job_id": job_id, "job_type": "mapping_refresh", "affected_count": len(affected_records)}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh", job_id=lease_job_id or None)

    def launch_business_re_evaluation(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._require_schema_ready()
        request = normalize_mapping_record_selection_request(payload)
        background_launched = False
        lease_job_id = ""
        self._reserve_mutating_job("business_re_evaluation")
        try:
            selected_items = self._select_business_re_evaluation_items(request)
            if not selected_items:
                if request.get("record_ids"):
                    raise ValueError("no eligible business re-evaluation records selected")
                return {"job_id": "", "job_type": "business_re_evaluation", "affected_count": 0, "db_path": "", "input_dir": "", "discovered_count": 0}
            scope_metadata = _mapping_refresh_scope_metadata(selected_items)
            if "scope" in scope_metadata:
                scope_metadata["scope"] = {
                    **dict(scope_metadata["scope"]),
                    "state": "pending_review",
                    "workflow": "business_resolution",
                }
            job_id = self.pipeline_repository.create_job(
                "business_re_evaluation",
                metadata={
                    "affected_count": len(selected_items),
                    "record_ids": [str(item.get("record_id") or "") for item in selected_items],
                    "state": "pending_review",
                    "workflow": "business_resolution",
                    **scope_metadata,
                },
            )
            lease_job_id = str(job_id)
            self.execution_service.bind_mutating_job("business_re_evaluation", job_id=job_id)
            record_ids = [str(item["record_id"]) for item in selected_items]

            def _run_business_re_evaluation_wrapper() -> None:
                try:
                    self.execution_service.bind_mutating_job(
                        "business_re_evaluation",
                        job_id=job_id,
                        worker_thread_name=threading.current_thread().name,
                    )
                    with self._thread_job_scope("business_re_evaluation"):
                        self._run_business_re_evaluation_job(job_id=job_id, record_ids=record_ids)
                except Exception as exc:  # noqa: BLE001
                    self._fail_active_background_job(
                        job_id,
                        job_type="business_re_evaluation",
                        stage="business_re_evaluation",
                        exc=exc,
                    )
                    raise
                finally:
                    self._release_mutating_job("business_re_evaluation", job_id=job_id)

            worker_name = f"peap-business-re-evaluation-{int(time.time())}"
            self.execution_service.bind_mutating_job(
                "business_re_evaluation",
                job_id=job_id,
                worker_thread_name=worker_name,
            )
            self._start_background_thread(
                name=worker_name,
                target=_run_business_re_evaluation_wrapper,
            )
            background_launched = True
            return {
                "job_id": job_id,
                "job_type": "business_re_evaluation",
                "affected_count": len(selected_items),
                "db_path": "",
                "input_dir": "",
                "discovered_count": len(selected_items),
            }
        finally:
            if not background_launched:
                self._release_mutating_job("business_re_evaluation", job_id=lease_job_id or None)

    def _ingest_manual_import_file(
        self,
        file_path: str,
        *,
        import_scope: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self.execution_service.ingest_manual_import_file(file_path, import_scope=import_scope)

    def _manual_import_smoke_delay_seconds(self, file_path: str) -> float:
        return self.execution_service.manual_import_smoke_delay_seconds(file_path)

    def _run_manual_import_job(
        self,
        *,
        job_id: str,
        files: list[str],
        ingest_file: Callable[[str], Dict[str, Any]] | None = None,
    ) -> None:
        self.execution_service.run_manual_import_job(
            job_id=job_id,
            files=files,
            ingest_file=ingest_file or self._ingest_manual_import_file,
            sleep_fn=time.sleep,
        )

    def launch_manual_import(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.launch_manual_import(
            payload,
            start_background_thread=self._start_background_thread,
        )

    def launch_archive_reprocess(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._require_schema_ready()
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return self.execution_service.launch_archive_reprocess(
            payload,
            start_background_thread=self._start_background_thread,
        )

    def retry_job(self, job_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.retry_job(job_id)

    def upsert_mapping(self, payload: Dict[str, Any], *, _record_undo: bool = True) -> Dict[str, Any]:
        self._require_schema_ready()
        background_launched = False
        lease_job_id = ""
        self._reserve_mutating_job("mapping_refresh")
        try:
            normalized = _normalize_mapping_payload(payload)
            _validate_mapping_payload(normalized)
            source_name = normalized["source_name"]
            match_field = normalized["match_field"]
            target_field = normalized["target_field"]
            rule_kind = normalized["rule_kind"]
            group_name = normalized["group_name"]
            source_type = normalized["source_type"]
            preview = self.preview_mapping_upsert(payload)
            confirm_overwrite = _parse_bool(payload.get("confirm_overwrite"), field_name="confirm_overwrite")
            if preview.get("conflict") and not confirm_overwrite:
                raise ValueError("mapping overwrite requires confirmation")
            entry_id = self.pipeline_repository.save_mapping_rule(
                source_name=source_name,
                group_name=group_name,
                source_type=source_type,
                rule_kind=rule_kind,
                match_field=match_field,
                target_field=target_field,
                metadata={
                    key: value
                    for key, value in payload.items()
                    if key not in {"company_name", "group_name", "source_type", "source_name", "target_value", "confirm_overwrite"}
                }
                | {
                    "rule_kind": rule_kind,
                    "match_field": match_field,
                    "target_field": target_field,
                },
            )

            def remember_lease_job(job_id: str) -> None:
                nonlocal lease_job_id
                lease_job_id = str(job_id or "").strip()

            refresh_payload = self._launch_mapping_refresh_job(
                source_name=source_name,
                match_field=match_field,
                target_field=target_field,
                entry_id=entry_id,
                on_job_bound=remember_lease_job,
            )
            if _record_undo:
                with self._undo_lock:
                    self._mapping_undo_stack.append({"kind": "upsert", "entry_id": entry_id})
            background_launched = bool(refresh_payload.get("job_id"))
            return {"entry_id": entry_id, **preview, **refresh_payload}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh", job_id=lease_job_id or None)

    def update_mapping(self, entry_id: str, payload: Dict[str, Any], *, _record_undo: bool = True) -> Dict[str, Any]:
        self._require_schema_ready()
        background_launched = False
        lease_job_id = ""
        normalized_entry_id = _parse_text(entry_id, field_name="entry_id")
        if not normalized_entry_id:
            raise ValueError("entry_id is required")
        request_payload = _optional_payload_object(payload)
        self._reserve_mutating_job("mapping_refresh")
        try:
            request_payload["entry_id"] = normalized_entry_id
            current_entry = self._get_mapping_entry(entry_id=normalized_entry_id)
            normalized = _normalize_mapping_payload(request_payload)
            _validate_mapping_payload(normalized)
            source_name = normalized["source_name"]
            match_field = normalized["match_field"]
            target_field = normalized["target_field"]
            rule_kind = normalized["rule_kind"]
            group_name = normalized["group_name"]
            source_type = normalized["source_type"]
            preview = self.preview_mapping_upsert(request_payload)
            confirm_overwrite = _parse_bool(request_payload.get("confirm_overwrite"), field_name="confirm_overwrite")
            if preview.get("conflict") and not confirm_overwrite:
                raise ValueError("mapping overwrite requires confirmation")
            updated_entry_id = self.pipeline_repository.save_mapping_rule(
                source_name=source_name,
                group_name=group_name,
                source_type=source_type,
                rule_kind=rule_kind,
                match_field=match_field,
                target_field=target_field,
                metadata={
                    key: value
                    for key, value in request_payload.items()
                    if key not in {"entry_id", "company_name", "group_name", "source_type", "source_name", "target_value", "confirm_overwrite"}
                }
                | {
                    "rule_kind": rule_kind,
                    "match_field": match_field,
                    "target_field": target_field,
                },
                replace_entry_id=normalized_entry_id,
            )
            affected_records = self._find_records_for_mapping_refresh_specs(
                [
                    {
                        "match_field": str(current_entry.get("match_field") or "").strip(),
                        "source_name": str(current_entry.get("source_name") or "").strip(),
                    },
                    {
                        "match_field": match_field,
                        "source_name": source_name,
                    },
                ]
            )

            def remember_lease_job(job_id: str) -> None:
                nonlocal lease_job_id
                lease_job_id = str(job_id or "").strip()

            refresh_payload = self._launch_mapping_refresh_job_for_records(
                record_ids=[str(item.get("record_id") or "") for item in affected_records],
                metadata={
                    "entry_id": updated_entry_id,
                    "previous_entry_id": normalized_entry_id,
                    "mutation": "update",
                    "source_name": source_name,
                    "match_field": match_field,
                    "target_field": target_field,
                    **_mapping_refresh_scope_metadata(affected_records),
                },
                on_job_bound=remember_lease_job,
            )
            if _record_undo:
                with self._undo_lock:
                    self._mapping_undo_stack.append(
                        {
                            "kind": "update",
                            "current_entry_id": updated_entry_id,
                            "previous_entry": current_entry,
                        }
                    )
            background_launched = bool(refresh_payload.get("job_id"))
            return {"entry_id": updated_entry_id, **preview, **refresh_payload}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh", job_id=lease_job_id or None)

    def delete_mapping(self, entry_id: str, *, _record_undo: bool = True) -> Dict[str, Any]:
        self._require_schema_ready()
        background_launched = False
        lease_job_id = ""
        normalized_entry_id = _parse_text(entry_id, field_name="entry_id")
        if not normalized_entry_id:
            raise ValueError("entry_id is required")
        self._reserve_mutating_job("mapping_refresh")
        try:
            current_entry = self._get_mapping_entry(entry_id=normalized_entry_id)
            if not self.pipeline_repository.delete_mapping_rule(entry_id=normalized_entry_id):
                raise KeyError(normalized_entry_id)
            affected_records = self._find_records_for_mapping_refresh_specs(
                [
                    {
                        "match_field": str(current_entry.get("match_field") or "").strip(),
                        "source_name": str(current_entry.get("source_name") or "").strip(),
                    }
                ]
            )

            def remember_lease_job(job_id: str) -> None:
                nonlocal lease_job_id
                lease_job_id = str(job_id or "").strip()

            refresh_payload = self._launch_mapping_refresh_job_for_records(
                record_ids=[str(item.get("record_id") or "") for item in affected_records],
                metadata={
                    "entry_id": normalized_entry_id,
                    "mutation": "delete",
                    "source_name": str(current_entry.get("source_name") or "").strip(),
                    "match_field": str(current_entry.get("match_field") or "").strip(),
                    "target_field": str(current_entry.get("target_field") or "").strip(),
                    **_mapping_refresh_scope_metadata(affected_records),
                },
                on_job_bound=remember_lease_job,
            )
            if _record_undo:
                with self._undo_lock:
                    self._mapping_undo_stack.append(
                        {
                            "kind": "delete",
                            "deleted_entry": current_entry,
                        }
                    )
            background_launched = bool(refresh_payload.get("job_id"))
            return {"entry_id": normalized_entry_id, "deleted": True, **refresh_payload}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh", job_id=lease_job_id or None)

    def _refresh_record_postprocess(
        self,
        record_id: str,
        *,
        caller: str = "refresh_postprocess",
        job_id: str = "",
    ) -> Dict[str, Any]:
        archive_root = self.get_basic_settings()["archive_root"]
        runner = self._build_ingest_runner(archive_root=archive_root)
        try:
            result = runner.refresh_postprocess(record_id)
        except Exception as exc:
            self.pipeline_repository.mark_postprocess_refresh_failed(
                record_id=record_id,
                error_message=str(exc),
                caller=caller,
                job_id=job_id,
            )
            raise
        self.pipeline_repository.record_postprocess_refreshed(
            record_id=record_id,
            result=result,
            caller=caller,
            job_id=job_id,
        )
        return result

    def refresh_record_postprocess(self, record_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        normalized_record_id = _parse_text(record_id, field_name="record_id")
        if self._current_thread_holds_mutating_job("refresh_postprocess"):
            return self._refresh_record_postprocess(normalized_record_id)
        with self._mutating_job_scope("refresh_postprocess"):
            return self._refresh_record_postprocess(normalized_record_id)

    def _reprocess_record(self, record_id: str) -> Dict[str, Any]:
        record = self.pipeline_repository.load_record(record_id)
        basic_settings = self.get_basic_settings()
        archive_root = str(basic_settings["archive_root"])
        deal_archive_root = str(
            basic_settings.get("deal_archive_root") or os.path.join(archive_root, "deal")
        )
        state = str(record.get("state") or "").strip()
        source_identity = _json_object(record.get("source_identity_json"))
        if state in FAILED_RECORD_STATES:
            preferred_source = pick_reprocess_evidence_path(
                {
                    **record,
                    "source_identity": source_identity,
                }
            )
            if not preferred_source or not os.path.isfile(preferred_source):
                error_message = f"original evidence missing for failed record: {record_id}"
                self.pipeline_repository.mark_reprocess_source_missing(
                    record_id=record_id,
                    error_message=error_message,
                )
                return {
                    "state": state or "parse_failed",
                    "record_id": record_id,
                    "error_code": "source_missing",
                    "error_message": error_message,
                }
            verdict = resolve_artifact_evidence_verdict(
                {
                    **record,
                    "source_identity": source_identity,
                },
                source_file=preferred_source,
                archive_path=preferred_source,
            )
            if verdict.status not in _REPROCESS_EVIDENCE_STATUSES:
                error_message = f"source evidence invalid for record: {record_id}"
                self.pipeline_repository.mark_reprocess_source_missing(
                    record_id=record_id,
                    error_message=error_message,
                )
                error_code = "source_missing" if verdict.status in {"stale_reference", "undeclared"} else "source_evidence_invalid"
                return _record_evidence_error_result(
                    record_id=record_id,
                    state=state,
                    error_code=error_code,
                    error_message=error_message,
                    evidence_status=verdict.status,
                    evidence_reason_code=verdict.reason_code,
                )
        else:
            verdict = resolve_artifact_evidence_verdict(record)
            preferred_source = str(verdict.authoritative_path or "").strip()
            if verdict.status not in _REPROCESS_EVIDENCE_STATUSES or not preferred_source:
                error_message = f"source file missing for record: {record_id}"
                self.pipeline_repository.mark_reprocess_source_missing(
                    record_id=record_id,
                    error_message=error_message,
                )
                error_code = "source_missing" if verdict.status in {"stale_reference", "undeclared"} else "source_evidence_invalid"
                return _record_evidence_error_result(
                    record_id=record_id,
                    state=state,
                    error_code=error_code,
                    error_message=error_message,
                    evidence_status=verdict.status,
                    evidence_reason_code=verdict.reason_code,
                )
            if not _record_reprocess_runtime_supported(record, source_identity):
                error_message = f"reprocess runtime unsupported for record: {record_id}"
                self.pipeline_repository.mark_reprocess_source_missing(
                    record_id=record_id,
                    error_message=error_message,
                )
                return _record_evidence_error_result(
                    record_id=record_id,
                    state=state,
                    error_code="reprocess_unsupported",
                    error_message=error_message,
                    evidence_status=verdict.status,
                    evidence_reason_code=verdict.reason_code,
                )
        try:
            runner = self._build_ingest_runner(
                archive_root=archive_root,
                archive_roots_by_family={
                    "listing": archive_root,
                    "deal": deal_archive_root,
                },
            )
            with self.pipeline_repository.write_transaction() as connection:
                result = runner.ingest(
                    ItemSavedPayload(
                        source_file=preferred_source,
                        page_url=str(
                            record["parser_payload"].get("page_url")
                            or source_identity.get("source_url")
                            or ""
                        ),
                        project_code=str(record["project_code"]),
                        project_name=str(record["project_name"]),
                        exchange=str(record["exchange"]),
                        listing_date=str(record["listing_date"]),
                        extra={
                            "project_type_fallback": str(record.get("project_type") or ""),
                            "snapshot_id": str(source_identity.get("snapshot_id") or ""),
                            "snapshot_digest": str(source_identity.get("snapshot_digest") or ""),
                            "preserve_source_artifact": True,
                            "reuse_current_conflict": True,
                        },
                    ),
                    _connection=connection,
                )
                # ingest() persists failed states too; supersession validation
                # must commit or roll back with that write.
                self.pipeline_repository.record_reprocessed(
                    record_id=record_id,
                    result=result,
                    _connection=connection,
                )
        except Exception as exc:
            self.pipeline_repository.mark_reprocess_failed(record_id=record_id, error_message=str(exc))
            raise
        return result

    def reprocess_record(self, record_id: str) -> Dict[str, Any]:
        self._require_schema_ready()
        normalized_record_id = _parse_text(record_id, field_name="record_id")
        if self._current_thread_holds_mutating_job("record_reprocess"):
            return self._reprocess_record(normalized_record_id)
        with self._mutating_job_scope("record_reprocess"):
            return self._reprocess_record(normalized_record_id)

    def run_export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.run_export_with_contract(
            payload,
            run_ready_export_fn=run_ready_export,
            count_scope_fn=count_records_in_export_scope_by_state,
        )

    def launch_one_click(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.launch_one_click(payload, start_background_thread=self._start_background_thread)

    def launch_download_ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_schema_ready()
        return self.execution_service.launch_download_ingest(payload, start_background_thread=self._start_background_thread)

    def _launch_streaming_job(
        self,
        payload: Dict[str, Any],
        *,
        job_type: str,
        auto_export: bool,
    ) -> Dict[str, Any]:
        return self.execution_service.launch_streaming_job(
            payload,
            job_type=job_type,
            auto_export=auto_export,
            start_background_thread=self._start_background_thread,
        )
