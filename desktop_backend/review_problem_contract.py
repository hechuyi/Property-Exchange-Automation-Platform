"""Read-only review-problem projection contract."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Any

from peap_core.family_catalog import list_family_descriptors

PROBLEM_KINDS = (
    "project_type_unresolved",
    "business_family_unresolved",
    "deal_data_incomplete",
    "source_artifact_unavailable",
    "export_fields_missing",
    "manual_review_unclassified",
)

PROBLEM_LABELS = {
    "project_type_unresolved": "项目类型待确认",
    "business_family_unresolved": "业务大类待确认",
    "deal_data_incomplete": "成交数据待复核",
    "source_artifact_unavailable": "原网页不可用",
    "export_fields_missing": "导出必填字段缺失",
    "manual_review_unclassified": "未归类复核事项",
}

SUMMARY_KEYS = tuple(f"{kind}_count" for kind in PROBLEM_KINDS)
EXTENDED_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _allowed_record_families() -> set[str]:
    return {"all", *(descriptor.family_id for descriptor in list_family_descriptors())}


def _first_value(query: Mapping[str, Any], name: str) -> str:
    value = query.get(name)
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError(f"invalid {name}: {value!r}")
        value = value[0]
        if not isinstance(value, str):
            raise ValueError(f"invalid {name}: {value!r}")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"invalid {name}: {value!r}")
    return value.strip()


def _parse_int(raw_value: str, *, field_name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    if raw_value in {None, ""}:
        value = default
    else:
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field_name}: {raw_value}") from exc
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def normalize_review_problem_query(query: Mapping[str, Any] | None) -> dict[str, Any]:
    if query is None:
        source: Mapping[str, Any] = {}
    elif isinstance(query, Mapping):
        source = query
    else:
        raise ValueError(f"invalid review problem query: {query!r}")
    problem_kind = _first_value(source, "problem_kind") or "all"
    if problem_kind not in {"all", *PROBLEM_KINDS}:
        raise ValueError(f"invalid problem_kind: {problem_kind}")
    record_family = _first_value(source, "record_family") or "all"
    if record_family not in _allowed_record_families():
        raise ValueError(f"invalid record_family: {record_family}")
    state = _first_value(source, "state") or "all"
    if state not in {"all", "pending_review", "field_missing"}:
        raise ValueError(f"invalid state: {state}")
    page = _parse_int(_first_value(source, "page"), field_name="page", default=1, minimum=1)
    page_size = _parse_int(_first_value(source, "page_size"), field_name="page_size", default=50, minimum=1, maximum=200)
    normalized = {
        "problem_kind": problem_kind,
        "record_family": record_family,
        "business_id": _first_value(source, "business_id") or "all",
        "exchange": _first_value(source, "exchange") or "all",
        "state": state,
        "keyword": _first_value(source, "keyword"),
        "date_from": _first_value(source, "date_from"),
        "date_to": _first_value(source, "date_to"),
        "page": page,
        "page_size": page_size,
    }
    for field in ("date_from", "date_to"):
        value = normalized[field]
        if value:
            try:
                if not EXTENDED_DATE_PATTERN.fullmatch(value):
                    raise ValueError
                dt.date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"invalid {field}: {value}") from exc
    if normalized["date_from"] and normalized["date_to"] and normalized["date_from"] > normalized["date_to"]:
        raise ValueError("date_from must be on or before date_to")
    return normalized


def empty_review_problem_summary() -> dict[str, int]:
    return {"total_count": 0, **{key: 0 for key in SUMMARY_KEYS}}


def build_review_problems_resource(
    rows: list[dict[str, Any]],
    *,
    total_count: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    summary = empty_review_problem_summary()
    summary["total_count"] = int(total_count)
    for row in rows:
        kind = str(row.get("problem_kind") or "manual_review_unclassified").strip()
        key = f"{kind}_count"
        if key in summary:
            summary[key] += 1
    returned_count = len(rows)
    return {
        "summary": summary,
        "rows": rows,
        "returned_count": returned_count,
        "total_count": int(total_count),
        "truncated": int(page) * int(page_size) < int(total_count),
    }
