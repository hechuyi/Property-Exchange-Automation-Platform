"""Export ready records from the streaming store."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import uuid
from collections import defaultdict
from typing import Any, Dict, Iterable, List

from .output_contract import clone_field_candidates, detect_output_kind, get_output_columns_for_kind
from .standard_model import build_standard_project
from .streaming_postprocess import derive_listing_times_from_project_code, merge_record_payloads
from .streaming_models import ExportArtifact, ExportRequest, ExportRunResult
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
    "状态",
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


def _safe_suffix(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    return cleaned or "default"


def _default_cursor_key(request: ExportRequest) -> str:
    seed = "|".join(
        [
            request.mode,
            request.date_from or "",
            request.date_to or "",
            ",".join(sorted(request.business_types)),
            request.output_dir,
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


def record_to_export_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    merged_payload = merge_record_payloads(
        record.get("parser_payload") or {},
        record.get("postprocess_payload") or {},
    )
    merged_payload.setdefault("项目编号", str(record.get("project_code") or ""))
    merged_payload.setdefault("项目名称", str(record.get("project_name") or ""))
    merged_payload.setdefault("项目类型", str(record.get("project_type") or ""))
    merged_payload.setdefault("交易所", str(record.get("exchange") or ""))
    standard = build_standard_project(merged_payload)
    compatible = standard.to_legacy_payload(include_raw=True)
    compatible.update({key: value for key, value in merged_payload.items() if value not in (None, "")})
    if compatible.get("挂牌次数") in (None, ""):
        derived_listing_times = derive_listing_times_from_project_code(str(compatible.get("项目编号") or ""))
        if derived_listing_times:
            compatible["挂牌次数"] = derived_listing_times
    return compatible


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
    if record_family != "listing":
        raise ValueError(f"unsupported record_family: {record_family}")
    output_dir = os.path.abspath(request.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    cursor_key = request.cursor_key or _default_cursor_key(request)
    export_id = f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"

    business_types = list(request.business_types or BUSINESS_TYPE_LABELS.keys())
    exported = store.get_exported_revision_map(cursor_key)
    records = store.iter_latest_records(
        states=["ready"],
        date_from=request.date_from,
        date_to=request.date_to,
    )

    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    marked_records: List[Dict[str, Any]] = []
    new_count = 0
    changed_count = 0

    for record in records:
        business_type = str(record["project_type"] or "").strip()
        if business_types and business_type not in business_types:
            continue
        payload = record_to_export_payload(record)
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
        "mode": request.mode,
    }
    store.mark_exported(
        export_id=export_id,
        cursor_key=cursor_key,
        mode=request.mode,
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
