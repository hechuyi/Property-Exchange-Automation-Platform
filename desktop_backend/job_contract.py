"""HTTP response contract helpers for job summary resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .job_result_contract import build_job_result_view
from .progress_contract import extract_job_identity
from .progress_resource_contract import build_progress_resource

RETRYABLE_JOB_TYPES = frozenset({"one_click", "download_ingest", "manual_import", "archive_reprocess"})
RETRYABLE_JOB_STATUSES = frozenset({"failed", "cancelled", "canceled", "aborted", "interrupted", "error"})
_STREAMING_RETRY_REQUIRED_FIELDS = frozenset({"start_date", "end_date", "exchange"})


def _has_replayable_metadata(payload: Mapping[str, Any], *, job_type: str) -> bool:
    """Return whether a failed job contains the request needed for a replay.

    A status/type match alone is not a capability: old or corrupt rows can be
    retryable in principle while lacking the input required to launch them.
    Keep this check structural and side-effect free so both the API view and
    the execution endpoint use the same fail-closed rule.
    """

    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    if job_type in {"manual_import", "archive_reprocess"}:
        value = metadata.get("input_dir")
        return isinstance(value, str) and bool(value.strip())
    if job_type not in {"one_click", "download_ingest"}:
        return False
    if not _STREAMING_RETRY_REQUIRED_FIELDS.issubset(metadata):
        return False
    scope = metadata.get("scope")
    if scope is not None and not isinstance(scope, Mapping):
        return False
    record_family = str(
        metadata.get("record_family")
        or (scope.get("record_family") if isinstance(scope, Mapping) else "")
        or ""
    ).strip()
    record_families = metadata.get("record_families")
    family_scopes = metadata.get("family_scopes")
    if record_families is not None:
        if not isinstance(record_families, list) or not record_families:
            return False
        if any(not str(value or "").strip() for value in record_families):
            return False
    if family_scopes is not None:
        if not isinstance(family_scopes, list) or not family_scopes:
            return False
        if any(not isinstance(scope, Mapping) for scope in family_scopes):
            return False
    return bool(record_family or record_families or family_scopes)


def job_actions(payload: Mapping[str, Any]) -> dict[str, bool]:
    """Expose server-owned action capability; malformed/unknown jobs fail closed."""
    if not isinstance(payload, Mapping):
        return {"retry": False}
    job_type = str(payload.get("job_type") or "").strip().lower()
    status = str(payload.get("status") or "").strip().lower()
    return {
        "retry": (
            job_type in RETRYABLE_JOB_TYPES
            and status in RETRYABLE_JOB_STATUSES
            and _has_replayable_metadata(payload, job_type=job_type)
        )
    }


def _integer(value: Any, *, field_name: str) -> int:
    if value is None or (isinstance(value, str) and value == ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def build_job_view(payload: Mapping[str, Any] | None, *, progress: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("job payload must be an object")
    source = dict(payload)
    identity = extract_job_identity(source)
    return {
        "job_id": str(source.get("job_id") or "").strip(),
        "job_type": str(source.get("job_type") or "").strip(),
        "status": str(source.get("status") or "").strip(),
        "actions": job_actions(source),
        "created_at": str(source.get("created_at") or "").strip(),
        "updated_at": str(source.get("updated_at") or "").strip(),
        "record_family": identity["record_family"],
        "business_id": identity["business_id"],
        "business_label": identity["business_label"],
        "scope": identity["scope"],
        "counts": {
            "downloaded": _integer(source.get("downloaded_count"), field_name="downloaded_count"),
            "persisted": _integer(source.get("persisted_count"), field_name="persisted_count"),
            "exceptions": _integer(source.get("exception_count"), field_name="exception_count"),
        },
        "progress": build_progress_resource(progress, job=source),
        "result": build_job_result_view(source),
    }
