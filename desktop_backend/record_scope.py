"""Pure record scope normalization contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from peap.streaming_models import RecordFamily

LISTING_BUSINESS_TYPES = ("股权转让", "实物资产", "增资扩股", "预披露")


@dataclass(frozen=True)
class RecordScope:
    record_family: RecordFamily = "listing"
    state: str = "all"
    project_type: str = "all"
    keyword: str = ""
    date_from: str = ""
    date_to: str = ""
    page: int = 1
    page_size: int = 50


def _coerce_text(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _coerce_int(value: Any, *, default: int, minimum: int = 1) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, number)


def _normalize_record_family(value: Any) -> RecordFamily:
    family = _coerce_text(value, default="listing").lower()
    if family == "deal":
        return "deal"
    return "listing"


def normalize_record_scope(payload: Mapping[str, Any] | None) -> RecordScope:
    data = dict(payload or {})
    return RecordScope(
        record_family=_normalize_record_family(data.get("record_family")),
        state=_coerce_text(data.get("state"), default="all") or "all",
        project_type=_coerce_text(data.get("project_type"), default="all") or "all",
        keyword=_coerce_text(data.get("keyword")),
        date_from=_coerce_text(data.get("date_from")),
        date_to=_coerce_text(data.get("date_to")),
        page=_coerce_int(data.get("page"), default=1),
        page_size=_coerce_int(data.get("page_size"), default=50),
    )


def record_scope_to_dict(scope: RecordScope) -> dict[str, Any]:
    normalized = normalize_record_scope(scope.__dict__)
    return {
        "record_family": normalized.record_family,
        "state": normalized.state,
        "project_type": normalized.project_type,
        "keyword": normalized.keyword,
        "date_from": normalized.date_from,
        "date_to": normalized.date_to,
        "page": normalized.page,
        "page_size": normalized.page_size,
    }


def resolve_listing_business_types(scope: RecordScope) -> list[str]:
    normalized = normalize_record_scope(scope.__dict__)
    if normalized.record_family != "listing":
        return []
    if normalized.project_type and normalized.project_type != "all":
        return [normalized.project_type]
    return list(LISTING_BUSINESS_TYPES)
