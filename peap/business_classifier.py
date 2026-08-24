"""Family-aware business classification for parser payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from peap.constants import STATUS_DEAL
from peap_core.business_catalog import (
    BusinessDescriptor,
    get_business_descriptor,
    resolve_business_descriptor_by_project_type,
)
from peap_core.family_catalog import get_family_descriptor


@dataclass(frozen=True)
class BusinessClassification:
    record_family: str
    business_id: str
    project_type_label: str
    raw_business_label: str


_MISSING_PARSER_PAYLOAD = object()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _project_type_from_payload(payload: Mapping[str, Any]) -> str:
    return _text(
        payload.get("项目类型") or payload.get("business_type") or payload.get("project_type")
    )


def _status_from_payload(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("项目状态") or payload.get("status"))


def _project_name(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("项目名称") or payload.get("project_name"))


def _project_code(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("项目编号") or payload.get("project_code")).upper()


def _has_value(payload: Mapping[str, Any], *field_names: str) -> bool:
    return any(_text(payload.get(field_name)) for field_name in field_names)


_STRONG_DEAL_STATUSES = frozenset((STATUS_DEAL, "deal"))
_DEAL_PATH_SEGMENTS = frozenset(("deal", "chengjiao", "成交"))
_PRE_DISCLOSURE_TRUTHY_VALUES = frozenset(("1", "true", "yes", "y", "是", "预披露"))


def _route_business_signal(*values: Any) -> str:
    """Return a business signal only from an explicit detail route."""
    for value in values:
        text = _text(value).lower()
        if not text:
            continue
        if any(
            marker in text
            for marker in ("capital_increasedetail", "capitalincrease/detail", "capital-increase")
        ):
            return "capital_increase"
        if any(marker in text for marker in ("equitydetail", "equity/detail", "equity-detail")):
            return "equity_transfer"
    return ""


def _status_has_deal_signal(value: Any) -> bool:
    status = _text(value)
    if not status:
        return False
    return status == STATUS_DEAL or status.lower() in _STRONG_DEAL_STATUSES


def _url_has_deal_signal(*values: Any) -> bool:
    for value in values:
        url_text = _text(value)
        if not url_text:
            continue
        parsed = urlparse(url_text)
        path = parsed.path or url_text.split("?", 1)[0].split("#", 1)[0]
        path_segments = [
            unquote(segment).strip().lower() for segment in path.split("/") if segment.strip()
        ]
        if any(segment in _DEAL_PATH_SEGMENTS for segment in path_segments):
            return True
    return False


def _payload_pre_disclosure_flag(payload: Mapping[str, Any]) -> bool:
    for field_name in ("是否预披露", "is_pre_disclosure", "pre_disclosure"):
        raw_value = payload.get(field_name)
        if isinstance(raw_value, bool):
            if raw_value:
                return True
            continue
        text = _text(raw_value).lower()
        if text in _PRE_DISCLOSURE_TRUTHY_VALUES:
            return True
    return False


def _resolve_family(
    *,
    payload: Mapping[str, Any],
    record_family_hint: Any = "",
    page_url: Any = "",
    source_url: Any = "",
) -> str:
    hint = _text(record_family_hint)
    if hint:
        return get_family_descriptor(hint).family_id
    status = _status_from_payload(payload)
    status_is_deal = _status_has_deal_signal(status)
    if status_is_deal:
        return "deal"
    payload_family = _text(payload.get("record_family"))
    if payload_family:
        return get_family_descriptor(payload_family).family_id
    if _url_has_deal_signal(page_url, source_url):
        return "deal"
    return "listing"


def _descriptor_for_explicit_business_id(
    *,
    family_id: str,
    business_id_hint: Any,
) -> BusinessDescriptor | None:
    business_id = _text(business_id_hint)
    if not business_id:
        return None
    return get_business_descriptor(business_id, family_id=family_id)


def _descriptor_for_project_type(
    *,
    family_id: str,
    values: tuple[Any, ...],
) -> BusinessDescriptor | None:
    for raw_value in values:
        descriptor = resolve_business_descriptor_by_project_type(raw_value, family_id=family_id)
        if descriptor is not None:
            return descriptor
    return None


def _has_pre_disclosure_markers(
    *,
    payload: Mapping[str, Any],
    project_name: str,
    project_code: str,
    page_url: str,
    source_url: str,
) -> bool:
    urls = f"{page_url} {source_url}".lower()
    return any(
        (
            "预披露" in project_name,
            project_code.endswith("-0"),
            _payload_pre_disclosure_flag(payload),
            _has_value(payload, "预披露开始日期", "预披露截止日期"),
            any(marker in urls for marker in ("jymhchanquanyu", "jymhzichanyu", "jymhzengziyu")),
        )
    )


def _has_capital_markers(
    *, payload: Mapping[str, Any], project_name: str, project_code: str
) -> bool:
    return any(
        (
            # 持股比例 is also a normal equity-transfer field and is not a
            # capital signal by itself.  Financing-labelled fields remain
            # valid signals for pages without an explicit route.
            _has_value(
                payload,
                "融资金额",
                "融资金额（万元）",
                "融资方",
                "融资方名称",
                "拟募集资金",
                "增资企业名称",
            ),
            any(token in project_name for token in ("增资项目", "增资扩股", "增资")),
            bool(re.match(r"^G62\d+", project_code)),
        )
    )


def _has_physical_markers(
    *, payload: Mapping[str, Any], project_name: str, project_code: str
) -> bool:
    tokens = (
        "报废",
        "设备",
        "资产",
        "房产",
        "房屋",
        "车辆",
        "机器",
        "在建工程",
        "物资",
        "土地",
    )
    return any(
        (
            _has_value(payload, "资产类别"),
            bool(re.match(r"^G[AR]\d+", project_code)),
            any(token in project_name for token in tokens),
        )
    )


def _has_equity_markers(*, project_name: str, project_code: str) -> bool:
    return any(
        (
            bool(re.match(r"^(G320|CP)", project_code)),
            any(token in project_name for token in ("股权", "股份", "财产份额", "合伙企业份额")),
        )
    )


def _infer_descriptor_from_payload(
    *,
    family_id: str,
    payload: Mapping[str, Any],
    page_url: str,
    source_url: str,
) -> BusinessDescriptor | None:
    project_name = _project_name(payload)
    project_code = _project_code(payload)
    if family_id == "deal":
        if _has_capital_markers(
            payload=payload, project_name=project_name, project_code=project_code
        ):
            return get_business_descriptor("deal_capital_increase", family_id=family_id)
        if _has_equity_markers(project_name=project_name, project_code=project_code):
            return get_business_descriptor("deal_equity_transfer", family_id=family_id)
        if _has_physical_markers(
            payload=payload, project_name=project_name, project_code=project_code
        ):
            return get_business_descriptor("deal_physical_asset", family_id=family_id)
        return None

    if _has_pre_disclosure_markers(
        payload=payload,
        project_name=project_name,
        project_code=project_code,
        page_url=page_url,
        source_url=source_url,
    ):
        return get_business_descriptor("pre_disclosure", family_id=family_id)
    if _has_capital_markers(payload=payload, project_name=project_name, project_code=project_code):
        return get_business_descriptor("capital_increase", family_id=family_id)
    if _has_physical_markers(payload=payload, project_name=project_name, project_code=project_code):
        return get_business_descriptor("physical_asset", family_id=family_id)
    if _has_equity_markers(project_name=project_name, project_code=project_code):
        return get_business_descriptor("equity_transfer", family_id=family_id)
    return None


def classify_record_business(
    *,
    parser_payload: Mapping[str, Any] | object = _MISSING_PARSER_PAYLOAD,
    record_family_hint: Any = "",
    business_id_hint: Any = "",
    business_label_hint: Any = "",
    project_type_hint: Any = "",
    project_type_label: Any = "",
    project_type_fallback: Any = "",
    page_url: Any = "",
    source_url: Any = "",
) -> BusinessClassification:
    if parser_payload is _MISSING_PARSER_PAYLOAD:
        payload: dict[str, Any] = {}
    elif not isinstance(parser_payload, Mapping):
        raise TypeError("parser_payload must be a mapping")
    else:
        payload = dict(parser_payload)
    family_id = _resolve_family(
        payload=payload,
        record_family_hint=record_family_hint,
        page_url=page_url,
        source_url=source_url,
    )
    page_url_text = _text(page_url)
    source_url_text = _text(source_url)
    route_signal = _route_business_signal(page_url_text, source_url_text)
    # Validate explicit metadata even when stronger page evidence is available.
    # A malformed task binding is a contract error; a valid task binding is only
    # a fallback because archived files can survive an earlier misrouted task.
    hint_descriptor = _descriptor_for_explicit_business_id(
        family_id=family_id,
        business_id_hint=business_id_hint,
    )
    descriptor: BusinessDescriptor | None = None
    if family_id == "listing" and _has_pre_disclosure_markers(
        payload=payload,
        project_name=_project_name(payload),
        project_code=_project_code(payload),
        page_url=page_url_text,
        source_url=source_url_text,
    ):
        descriptor = get_business_descriptor("pre_disclosure", family_id=family_id)
    if descriptor is None and family_id == "listing" and route_signal:
        descriptor = get_business_descriptor(route_signal, family_id=family_id)
    if descriptor is None:
        descriptor = _descriptor_for_project_type(
            family_id=family_id,
            values=(_project_type_from_payload(payload),),
        )
    if descriptor is None:
        descriptor = hint_descriptor
    if descriptor is None:
        descriptor = _descriptor_for_project_type(
            family_id=family_id,
            values=(
                project_type_hint,
                project_type_label,
                project_type_fallback,
                business_label_hint,
            ),
        )
    if descriptor is None:
        descriptor = _infer_descriptor_from_payload(
            family_id=family_id,
            payload=payload,
            page_url=page_url_text,
            source_url=source_url_text,
        )
    if descriptor is None:
        raw_business_label = _project_type_from_payload(payload) or _text(project_type_fallback)
        return BusinessClassification(
            record_family=family_id,
            business_id="",
            project_type_label="",
            raw_business_label=raw_business_label,
        )
    project_type_value = _text(descriptor.project_type_label)
    return BusinessClassification(
        record_family=family_id,
        business_id=descriptor.business_id,
        project_type_label=project_type_value,
        raw_business_label=project_type_value,
    )


__all__ = ["BusinessClassification", "classify_record_business"]
