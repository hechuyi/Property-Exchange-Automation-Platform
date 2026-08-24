"""Pure record scope normalization contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from peap.streaming_models import RecordFamily
from peap_core.business_catalog import get_business_descriptor, resolve_business_descriptor
from peap_core.family_catalog import get_family_descriptor
from peap_core.source_catalog import resolve_source_descriptor


@dataclass(frozen=True)
class RecordScope:
    record_family: RecordFamily = "listing"
    state: str = "all"
    business_id: str = "all"
    business_label: str = ""
    exchange: str = "all"
    keyword: str = ""
    date_from: str = ""
    date_to: str = ""
    page: int = 1
    page_size: int = 50


class RecordScopeValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip() or "invalid_scope"


def _scope_error(reason_code: str, message: str) -> RecordScopeValidationError:
    return RecordScopeValidationError(reason_code, message)


def _coerce_text(value: Any, *, field_name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise _scope_error(f"invalid_{field_name}", f"invalid {field_name}: {value!r}")
    text = value.strip()
    return text if text else default


def _coerce_int(value: Any, *, field_name: str, default: int, minimum: int = 1) -> int:
    if value is None or value == "":
        number = default
    else:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise _scope_error(f"invalid_{field_name}", f"invalid {field_name}: {value!r}") from exc
    return max(minimum, number)


def _normalize_record_family(value: Any) -> RecordFamily:
    raw = _coerce_text(value, field_name="record_family", default="listing")
    if not raw:
        return "listing"
    try:
        descriptor = get_family_descriptor(raw)
    except KeyError as exc:
        raise _scope_error("unknown_record_family", f"unknown record_family: {value!r}") from exc
    return cast(RecordFamily, descriptor.family_id)


def _normalize_business_id(value: Any, *, family: RecordFamily) -> str:
    business_id = _coerce_text(value, field_name="business_id", default="all")
    if business_id in {"", "all"}:
        return "all"
    try:
        descriptor = get_business_descriptor(business_id, family_id=family)
    except KeyError as exc:
        raise _scope_error("unknown_business_id", f"unknown business_id: {value!r}") from exc
    return descriptor.business_id


def _normalize_exchange(value: Any, *, family: RecordFamily) -> str:
    exchange = _coerce_text(value, field_name="exchange", default="all")
    if exchange in {"", "all"}:
        return "all"
    descriptor = resolve_source_descriptor(exchange, allow_substring=True)
    if descriptor is None or family not in descriptor.supported_record_families:
        raise _scope_error("invalid_exchange", f"invalid exchange: {value!r}")
    return descriptor.source_id


def _business_identity_from_payload(
    data: Mapping[str, Any],
    *,
    family: RecordFamily,
) -> tuple[str, str]:
    if "project_type" in data:
        raise ValueError("project_type is not supported in record scope; use business_id")
    explicit_business_id = _coerce_text(data.get("business_id"), field_name="business_id")
    explicit_business_label = _coerce_text(data.get("business_label"), field_name="business_label")

    business_id = _normalize_business_id(explicit_business_id, family=family)
    business_label = explicit_business_label
    if business_id in {"", "all"}:
        business_id = "all"
        business_label = ""
    elif business_label:
        descriptor = get_business_descriptor(business_id, family_id=family)
        matched_descriptor = resolve_business_descriptor(business_label, family_id=family)
        if matched_descriptor is None:
            raise _scope_error(
                "business_label_mismatch",
                f"business_label does not match business_id: {business_label!r}",
            )
        if matched_descriptor.business_id != descriptor.business_id:
            raise _scope_error(
                "business_label_mismatch",
                f"business_label does not match business_id: {business_label!r}",
            )
    return business_id, business_label


def normalize_record_scope(payload: Mapping[str, Any] | None) -> RecordScope:
    if payload is None:
        data: dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        data = dict(payload)
    else:
        raise _scope_error("invalid_record_scope", f"invalid record scope: {payload!r}")
    record_family = _normalize_record_family(data.get("record_family"))
    business_id, business_label = _business_identity_from_payload(data, family=record_family)
    return RecordScope(
        record_family=record_family,
        state=_coerce_text(data.get("state"), field_name="state", default="all") or "all",
        business_id=business_id,
        business_label=business_label,
        exchange=_normalize_exchange(data.get("exchange"), family=record_family),
        keyword=_coerce_text(data.get("keyword"), field_name="keyword"),
        date_from=_coerce_text(data.get("date_from"), field_name="date_from"),
        date_to=_coerce_text(data.get("date_to"), field_name="date_to"),
        page=_coerce_int(data.get("page"), field_name="page", default=1),
        page_size=_coerce_int(data.get("page_size"), field_name="page_size", default=50),
    )


def record_scope_to_dict(scope: RecordScope) -> dict[str, Any]:
    normalized = normalize_record_scope(scope.__dict__)
    payload = {
        "record_family": normalized.record_family,
        "state": normalized.state,
        "business_id": normalized.business_id or "all",
        "exchange": normalized.exchange,
        "keyword": normalized.keyword,
        "date_from": normalized.date_from,
        "date_to": normalized.date_to,
        "page": normalized.page,
        "page_size": normalized.page_size,
    }
    if normalized.business_label:
        payload["business_label"] = normalized.business_label
    return payload


def resolve_scope_business_ids(scope: RecordScope) -> list[str]:
    normalized = normalize_record_scope(scope.__dict__)
    if normalized.business_id and normalized.business_id != "all":
        return [normalized.business_id]
    return list(get_family_descriptor(normalized.record_family).business_ids)


def resolve_listing_business_ids(scope: RecordScope) -> list[str]:
    """Backward-compatible alias kept for call sites/tests during Task 7 rollout."""
    return resolve_scope_business_ids(scope)
