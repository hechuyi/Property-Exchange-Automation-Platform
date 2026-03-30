"""Explicit downstream compatibility payload projection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from .standard_model import StandardProject

COMPAT_FIELD_TO_STANDARD_FIELD = {
    "项目编号": "project_code",
    "项目名称": "project_name",
    "项目类型": "project_type",
    "状态": "status",
    "交易所": "exchange",
    "类型": "source_type",
    "转让方": "seller",
    "交易方式": "deal_method",
    "受让方名称": "buyer_name",
    "隶属集团": "group_name",
    "所属行业": "industry",
    "所在地区": "region",
    "经办人": "contact",
    "受托机构": "agency",
    "挂牌价格": "price",
    "融资方": "seller",
    "融资金额": "price",
    "融资金额（万）": "price",
    "转让标的评估值": "valuation",
    "成交金额": "price",
    "挂牌开始日期": "start_date",
    "挂牌截止日期": "end_date",
    "预披露开始日期": "start_date",
    "预披露截止日期": "end_date",
    "披露开始日期": "start_date",
    "披露截止日期": "end_date",
    "成交日期": "end_date",
    "近一年净利润": "profit",
    "近一年净利润（万）": "profit",
    "总资产": "asset_total",
    "总资产（万）": "asset_total",
    "持股比例": "share_ratio",
    "挂牌次数": "listing_times",
    "是否预披露": "is_pre_disclosure",
    "备注": "remark",
}

# Keep reviewed passthrough extras explicit here; empty by default.
ALLOWED_RAW_COMPAT_FIELDS = frozenset()
COMPAT_PAYLOAD_KEYS = frozenset(COMPAT_FIELD_TO_STANDARD_FIELD) | ALLOWED_RAW_COMPAT_FIELDS


def build_compat_payload(
    standard_project: "StandardProject",
    *,
    raw_payload: Mapping[str, Any] | None = None,
    allowed_extra_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    payload = {
        compat_field: getattr(standard_project, standard_field)
        for compat_field, standard_field in COMPAT_FIELD_TO_STANDARD_FIELD.items()
    }
    extras = frozenset(
        ALLOWED_RAW_COMPAT_FIELDS if allowed_extra_fields is None else allowed_extra_fields
    )
    if raw_payload and extras:
        for field_name in extras:
            value = raw_payload.get(field_name)
            if value not in (None, ""):
                payload[field_name] = value
    return payload


__all__ = [
    "ALLOWED_RAW_COMPAT_FIELDS",
    "COMPAT_FIELD_TO_STANDARD_FIELD",
    "COMPAT_PAYLOAD_KEYS",
    "build_compat_payload",
]
