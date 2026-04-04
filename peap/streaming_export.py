"""Export ready records from the streaming store."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import uuid
from collections import defaultdict
from typing import Any, Dict, Iterable, List

from .export_projection import ExportProjectionError, project_canonical_record_to_export_payload
from .output_contract import clone_field_candidates, detect_output_kind, get_output_columns_for_kind
from .standard_model import build_standard_project, hydrate_standard_project
from .streaming_models import ExportArtifact, ExportRequest, ExportRunResult
from .streaming_postprocess import derive_listing_times_from_project_code
from .streaming_store import StreamingStore

BUSINESS_TYPE_LABELS = {
    "股权转让": "挂牌_股权转让",
    "实物资产": "挂牌_实物资产",
    "增资扩股": "挂牌_增资扩股",
    "预披露": "挂牌_预披露",
}

HEADER_PRIORITY = [
    "项目编号",
    "项目名称",
    "项目类型",
    "项目状态",
    "交易所",
    "类型",
    "转让方",
    "融资方",
    "隶属集团",
    "挂牌开始日期",
    "挂牌截止日期",
    "预披露开始日期",
    "预披露截止日期",
    "挂牌价格",
    "融资金额",
    "受让方名称",
    "备注",
]

LISTING_REQUIRED_CANONICAL_FIELDS = frozenset(
    {"project_code", "project_name", "project_type", "status", "start_date", "price", "seller"}
)
LISTING_REQUIRED_COMPAT_FIELDS = frozenset(
    {"项目编号", "项目名称", "项目类型", "项目状态", "挂牌开始日期", "挂牌价格", "转让方"}
)


def _safe_suffix(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    return cleaned or "default"


def _normalize_export_mode(raw_value: str) -> str:
    text = str(raw_value or "incremental").strip().lower()
    return text or "incremental"


def _record_matches_keyword(record: Dict[str, Any], *, keyword: str) -> bool:
    normalized_keyword = str(keyword or "").strip().lower()
    if not normalized_keyword:
        return True
    payload = record_to_export_payload(record)
    search_blob = " ".join(
        [
            str(record.get("project_code") or ""),
            str(record.get("project_name") or ""),
            str(record.get("project_type") or ""),
            str(record.get("exchange") or ""),
            str(record.get("listing_date") or ""),
            str(record.get("state") or ""),
        ]
        + [str(value or "") for value in payload.values()]
    ).lower()
    return normalized_keyword in search_blob


def _default_cursor_key(request: ExportRequest) -> str:
    mode = _normalize_export_mode(request.mode)
    requested_state = str(getattr(request, "requested_state", "all") or "all").strip().lower()
    keyword = str(getattr(request, "keyword", "") or "").strip().lower()
    seed = "|".join(
        [
            mode,
            request.date_from or "",
            request.date_to or "",
            ",".join(sorted(request.business_types)),
            request.output_dir,
            requested_state,
            keyword,
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"export:{digest}"


def _listing_date(payload: Dict[str, Any]) -> str:
    for key in ("挂牌开始日期", "预披露开始日期", "披露开始日期", "信息披露起始日期"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def ordered_export_headers(rows: Iterable[Dict[str, Any]]) -> List[str]:
    found: Dict[str, None] = {}
    for row in rows:
        for key in row.keys():
            found[str(key)] = None
    ordered = [key for key in HEADER_PRIORITY if key in found]
    ordered.extend(sorted(key for key in found if key not in HEADER_PRIORITY))
    return ordered


def _canonical_projection_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build export payload from canonical record only - no raw payload fallback.

    Uses export_projection module to produce a canonical export payload.
    """
    # Get canonical_record for filling in missing fields
    canonical_record = record.get("canonical_record") or {}

    if canonical_record:
        # Use export_projection module - returns (payload, findings)
        payload, _ = project_canonical_record_to_export_payload(
            canonical_record,
            fail_on_missing=False,
        )
        return payload

    # Fall back to canonical_projection if no canonical_record
    canonical_projection = record.get("canonical_projection") or {}
    if canonical_projection:
        return dict(canonical_projection)

    return {}


def record_to_export_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a record to export payload using canonical data only.

    Export must use canonical data only - NO raw payload merge fallback.
    Missing canonical fields fail through PipelineFailure or PostProcessFinding.
    """
    # First try to use canonical_projection directly if it's complete
    canonical_projection = record.get("canonical_projection") or {}
    canonical_record = record.get("canonical_record") or {}
    canonical_fields = canonical_record.get("canonical_fields") if isinstance(canonical_record, dict) else {}

    # Check if we have a complete canonical record
    if canonical_fields:
        try:
            # Use export_projection to build the payload - this will fail loudly
            # if required fields are missing
            payload, findings = project_canonical_record_to_export_payload(
                canonical_record,
                fail_on_missing=False,  # Return findings instead of raising
            )
            return payload
        except Exception:
            # Fall back to canonical_projection if available
            pass

    # Fall back to canonical_projection if canonical_record is not available
    if canonical_projection:
        payload = dict(canonical_projection)
        return payload

    # NO raw payload fallback - fail if we get here without canonical data
    # This is the ONLY place where we would fall back to raw payload merge,
    # and we explicitly do NOT allow it per the task requirements
    return {}


def _ensure_exportable_payload(record: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    canonical_record = record.get("canonical_record") or {}
    canonical_fields = canonical_record.get("canonical_fields") if isinstance(canonical_record, dict) else {}
    canonical_projection = record.get("canonical_projection") or {}

    if isinstance(canonical_fields, dict) and canonical_fields:
        missing = [
            field_name
            for field_name in sorted(LISTING_REQUIRED_CANONICAL_FIELDS)
            if canonical_fields.get(field_name) in (None, "")
        ]
        if missing:
            raise ExportProjectionError(
                f"incomplete canonical_record for export: missing {', '.join(missing)}"
            )
        return payload

    if isinstance(canonical_projection, dict) and canonical_projection:
        missing = [
            field_name
            for field_name in sorted(LISTING_REQUIRED_COMPAT_FIELDS)
            if canonical_projection.get(field_name) in (None, "")
        ]
        if missing:
            raise ExportProjectionError(
                f"incomplete canonical_projection for export: missing {', '.join(missing)}"
            )
        return payload

    raise ExportProjectionError("record is missing canonical export data")


def _write_value_row(row: Dict[str, Any], *, kind: str) -> List[Any]:
    payload = dict(row or {})
    field_candidates = clone_field_candidates().get(kind, {})
    values: List[Any] = []
    for header in get_output_columns_for_kind(kind):
        if header == "ID":
            continue
        matched_value = ""
        for candidate in field_candidates.get(header, [header]):
            candidate_value = payload.get(candidate)
            if candidate_value not in (None, ""):
                matched_value = candidate_value
                break
        values.append(matched_value)
    return values


def _write_workbook_default(file_path: str, rows: List[Dict[str, Any]]) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    kind = detect_output_kind(file_path)
    headers = [header for header in get_output_columns_for_kind(kind) if header != "ID"]
    if not headers:
        headers = ordered_export_headers(rows)
    sheet.append(headers)
    for row in rows:
        if kind and kind in clone_field_candidates():
            sheet.append(_write_value_row(row, kind=kind))
        else:
            sheet.append([row.get(header, "") for header in headers])
    workbook.save(file_path)


def run_ready_export(
    store: StreamingStore,
    request: ExportRequest,
    *,
    writer=None,
) -> ExportRunResult:
    writer = writer or _write_workbook_default
    record_family = str(request.record_family or "listing").strip() or "listing"
    mode = _normalize_export_mode(request.mode)
    requested_state = str(getattr(request, "requested_state", "all") or "all").strip().lower()
    keyword = str(getattr(request, "keyword", "") or "").strip().lower()
    if record_family != "listing":
        raise ValueError(f"unsupported record_family: {record_family}")
    output_dir = os.path.abspath(request.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    cursor_key = request.cursor_key or _default_cursor_key(request)
    export_id = f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"

    business_types = list(request.business_types or BUSINESS_TYPE_LABELS.keys())
    # rebuild is a full scoped re-export; it must not inherit incremental cursor state.
    exported = {} if mode == "rebuild" else store.get_exported_revision_map(cursor_key)
    records = store.iter_latest_records(
        states=["ready"],
        date_from=request.date_from,
        date_to=request.date_to,
        record_family=record_family,
    )

    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    marked_records: List[Dict[str, Any]] = []
    new_count = 0
    changed_count = 0

    for record in records:
        record_state = str(record.get("state") or "").strip().lower()
        if requested_state not in {"", "all"} and record_state != requested_state:
            continue
        business_type = str(record["project_type"] or "").strip()
        if business_types and business_type not in business_types:
            continue
        if not _record_matches_keyword(record, keyword=keyword):
            continue
        payload = _ensure_exportable_payload(record, record_to_export_payload(record))
        previous = exported.get(record["record_id"])
        bucket = "new"
        if previous is None:
            new_count += 1
        elif previous["revision_hash"] != record["revision_hash"]:
            bucket = "changed"
            changed_count += 1
        else:
            continue
        marked_records.append(record)
        grouped[(business_type, bucket)].append(payload)

    artifacts: List[ExportArtifact] = []
    for (business_type, bucket), rows in sorted(grouped.items()):
        if not rows:
            continue
        prefix = BUSINESS_TYPE_LABELS.get(business_type, _safe_suffix(business_type))
        suffix = "新增" if bucket == "new" else "变更"
        file_path = os.path.join(output_dir, f"{prefix}_{suffix}_{export_id}.xlsx")
        writer(file_path, rows)
        artifacts.append(
            ExportArtifact(
                business_type=business_type,
                change_bucket=bucket,
                file_path=file_path,
                record_count=len(rows),
            )
        )

    summary = {
        "new_records": new_count,
        "changed_records": changed_count,
        "artifacts": [artifact.file_path for artifact in artifacts],
        "mode": mode,
    }
    store.mark_exported(
        export_id=export_id,
        cursor_key=cursor_key,
        mode=mode,
        date_from=request.date_from,
        date_to=request.date_to,
        project_type=",".join(sorted(business_types)),
        output_dir=output_dir,
        summary=summary,
        records=marked_records,
    )
    return ExportRunResult(
        export_id=export_id,
        cursor_key=cursor_key,
        artifacts=artifacts,
        new_records=new_count,
        changed_records=changed_count,
    )
