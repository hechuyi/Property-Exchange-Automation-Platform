"""Developer repair planning for failed or blocked records."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from peap_core.record_identity import FAILED_RECORD_STATES
from peap_core.runtime_paths import resolve_runtime_workspace_paths

from .failed_record_supersession import (
    build_superseding_record_index,
    find_superseding_record,
    reprocess_source_path,
)
from .streaming_store import StreamingStore
from .write_coordinator import WriteCoordinator

DEFAULT_REPAIR_STATES = (
    "parse_failed",
    "postprocess_failed",
    "pending_review",
    "field_missing",
    "pending_mapping",
    "mapping_conflict",
    "conflict",
)


@dataclass(frozen=True)
class FailureRepairRuntime:
    """Dependencies required to apply a reviewed failure-repair plan.

    The caller owns process-level write exclusion before constructing this
    runtime. This module deliberately does not select or import a desktop
    service implementation.
    """

    store: StreamingStore
    reprocess_record: Callable[[str], Mapping[str, Any]]


def _finding_types(record: dict[str, Any]) -> set[str]:
    findings = record.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    return {str(item.get("type") or "").strip() for item in findings if isinstance(item, dict)}


def _source_exists(record: dict[str, Any]) -> bool:
    path = reprocess_source_path(record)
    return bool(path and os.path.isfile(path))


def _project_business_id(record: dict[str, Any]) -> str:
    business_id = str(record.get("business_id") or "").strip()
    if business_id:
        return business_id
    canonical_record = record.get("canonical_record")
    if not isinstance(canonical_record, dict):
        return ""
    for identity_key in ("business_identity", "source_identity"):
        identity = canonical_record.get(identity_key)
        if not isinstance(identity, dict):
            continue
        business_id = str(identity.get("business_id") or "").strip()
        if business_id:
            return business_id
    return ""


def _classify_record(record: dict[str, Any], superseding_record: dict[str, Any] | None = None) -> dict[str, Any]:
    state = str(record.get("state") or "").strip()
    error_type = str(record.get("last_error_type") or "").strip()
    error_message = str(record.get("last_error_message") or "").strip()
    findings = _finding_types(record)
    source_path = reprocess_source_path(record)
    source_exists = _source_exists(record)
    superseded_by_record_id = ""
    superseded_by_state = ""
    if superseding_record is not None:
        if not isinstance(superseding_record, dict):
            raise ValueError("superseding_record must be a record object")
        raw_superseded_by_record_id = superseding_record.get("record_id")
        if not isinstance(raw_superseded_by_record_id, str) or not raw_superseded_by_record_id.strip():
            raise ValueError("superseding_record.record_id must be a non-empty string")
        superseded_by_record_id = raw_superseded_by_record_id.strip()
        superseded_by_state = str(superseding_record.get("state") or "")

    action = "inspect"
    reason_code = error_type or state
    apply_supported = False
    blocked_reason = ""

    if state in {"parse_failed", "postprocess_failed"}:
        if superseding_record is not None:
            action = "superseded_by_record"
            reason_code = "superseded_by_record"
            blocked_reason = "a non-failed record already exists for the same project code and source evidence"
        elif error_type == "synthetic_archive_quarantined" or "synthetic" in error_message or "list-row fallback" in error_message:
            action = "source_refetch_required"
            reason_code = "synthetic_archive_quarantined"
            blocked_reason = "stored evidence is a quarantined synthetic/list-row snapshot, not a valid source page"
        elif not source_exists:
            action = "source_missing"
            reason_code = "source_missing"
            blocked_reason = "source evidence file is missing"
        else:
            action = "record_reprocess_from_evidence"
            apply_supported = True
    elif state == "pending_review" and "business_resolution_required" in findings:
        if source_exists:
            action = "business_re_evaluation_required"
            reason_code = "business_resolution_required"
            blocked_reason = "business classification must be resolved or business rules must be updated before re-evaluation"
        else:
            action = "source_missing"
            reason_code = "source_missing"
            blocked_reason = "source evidence file is missing"
    elif state == "field_missing":
        action = "source_data_required"
        reason_code = "field_missing"
        blocked_reason = "canonical/export required fields are absent; repair requires better source evidence or parser support"
    elif state == "pending_mapping":
        action = "mapping_rule_required"
        reason_code = "mapping_missing"
        blocked_reason = "mapping gap must be resolved through mapping rules before refresh"
    elif state == "mapping_conflict":
        action = "mapping_conflict_authoritative_resolution_required"
        reason_code = "mapping_conflict"
        blocked_reason = "mapping ambiguity/conflict must be explicitly resolved"
    elif state == "conflict":
        action = "archive_conflict_resolution_required"
        reason_code = "archive_conflict"
        blocked_reason = "archive conflict needs a reviewed source choice"
    else:
        blocked_reason = f"unsupported state for failure repair: {state}"

    return {
        "record_id": str(record.get("record_id") or ""),
        "state": state,
        "project_code": str(record.get("project_code") or ""),
        "project_name": str(record.get("project_name") or ""),
        "exchange": str(record.get("exchange") or ""),
        "business_id": _project_business_id(record),
        "error_type": error_type,
        "action": action,
        "reason_code": reason_code,
        "apply_supported": apply_supported,
        "blocked_reason": blocked_reason,
        "source_path": source_path,
        "source_exists": source_exists,
        "superseded_by_record_id": superseded_by_record_id,
        "superseded_by_state": superseded_by_state,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _repair_result_failed(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return True
    if str(result.get("error_code") or "").strip():
        return True
    state = str(result.get("state") or "").strip()
    if not state:
        return True
    return state in FAILED_RECORD_STATES


def _selected_records(
    store: StreamingStore,
    *,
    states: Iterable[str] | None,
    record_ids: Iterable[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    ids: list[str] = []
    if record_ids is not None:
        if not isinstance(record_ids, (list, tuple)):
            raise ValueError("record_ids must be a list of non-empty strings")
        for item in record_ids:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("record_ids entries must be non-empty strings")
            ids.append(item.strip())
    if ids:
        return [store.get_record(record_id) for record_id in ids]
    return store.iter_latest_records(states=tuple(states or DEFAULT_REPAIR_STATES), limit=limit, sort="recent")


def build_failure_repair_plan(
    *,
    app_home: str,
    states: Iterable[str] | None = None,
    record_ids: Iterable[str] | None = None,
    limit: int | None = None,
    store: StreamingStore | None = None,
) -> dict[str, Any]:
    paths = resolve_runtime_workspace_paths(app_home=app_home)
    resolved_store = store or StreamingStore(paths.streaming_db_path, auto_migrate=False)
    selected_records = _selected_records(resolved_store, states=states, record_ids=record_ids, limit=limit)
    superseding_index = build_superseding_record_index(resolved_store.iter_latest_records(sort="recent"))
    items = [
        _classify_record(record, find_superseding_record(record, superseding_index))
        for record in selected_records
    ]
    action_counts = Counter(str(item.get("action") or "") for item in items)
    repairable_count = sum(1 for item in items if bool(item.get("apply_supported")))
    return {
        "app_home": paths.app_home,
        "db_path": paths.streaming_db_path,
        "applied": False,
        "summary": {
            "total_count": len(items),
            "repairable_count": repairable_count,
            "blocked_count": len(items) - repairable_count,
            "action_counts": dict(sorted(action_counts.items())),
        },
        "items": items,
    }


def apply_failure_repair_plan(
    *,
    app_home: str,
    states: Iterable[str] | None = None,
    record_ids: Iterable[str] | None = None,
    limit: int | None = None,
    allow_batch: bool = False,
    runtime: FailureRepairRuntime,
) -> tuple[int, dict[str, Any]]:
    plan = build_failure_repair_plan(
        app_home=app_home,
        states=states,
        record_ids=record_ids,
        limit=limit,
        store=runtime.store,
    )
    repairable_items = [item for item in plan["items"] if bool(item.get("apply_supported"))]
    if not repairable_items:
        return 2, {
            **plan,
            "applied": False,
            "error": {
                "code": "no_repairable_records",
                "message": "no records in the selected scope have supported automatic repairs",
            },
        }
    if len(repairable_items) > 1 and not allow_batch:
        return 3, {
            **plan,
            "error": {
                "code": "batch_confirmation_required",
                "message": "multiple repairable records require allow_batch=True",
            },
        }

    coordinator = WriteCoordinator(store=runtime.store)

    def _run(operation):
        results: list[dict[str, Any]] = []
        for item in repairable_items:
            record_id = str(item.get("record_id") or "")
            try:
                result = _json_safe(runtime.reprocess_record(record_id))
                if _repair_result_failed(result):
                    results.append({"record_id": record_id, "status": "failed", "result": result})
                else:
                    results.append({"record_id": record_id, "status": "succeeded", "result": result})
            except Exception as exc:  # noqa: BLE001
                results.append({"record_id": record_id, "status": "failed", "error": {"type": exc.__class__.__name__, "message": str(exc)}})
        operation.update_manifest({"plan": plan, "results": results})
        if any(item["status"] == "failed" for item in results):
            operation.fail({"code": "partial_failure", "message": "one or more record repairs failed"})
        else:
            operation.succeed()
        return results

    results = coordinator.write_operation(
        "failed_record_repair",
        {"record_ids": [item["record_id"] for item in repairable_items]},
        _run,
    )
    refreshed_plan = build_failure_repair_plan(
        app_home=app_home,
        states=states,
        record_ids=record_ids,
        limit=limit,
        store=runtime.store,
    )
    failed = any(str(item.get("status") or "") == "failed" for item in results)
    payload = {
        **refreshed_plan,
        "applied": True,
        "results": results,
    }
    if failed:
        payload["error"] = {
            "code": "partial_failure",
            "message": "one or more record repairs failed",
        }
        return 4, payload
    return 0, payload


__all__ = ["DEFAULT_REPAIR_STATES", "FailureRepairRuntime", "apply_failure_repair_plan", "build_failure_repair_plan"]
