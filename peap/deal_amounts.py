"""Deal amount normalization and unit evidence helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class NormalizedDealAmount:
    raw_text: str
    normalized_text: str
    unit: str
    source_unit: str
    unit_basis: str
    unit_verified: bool

    def as_canonical_fields(self, *, prefix: str = "deal_price") -> dict[str, object]:
        return {
            prefix: self.normalized_text,
            f"{prefix}_raw": self.raw_text,
            f"{prefix}_unit": self.unit,
            f"{prefix}_source_unit": self.source_unit,
            f"{prefix}_unit_basis": self.unit_basis,
            f"{prefix}_unit_verified": self.unit_verified,
        }


_NUMBER_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
_DEAL_PRICE_UNIT_LABEL_RE = re.compile(r"(交易价格|成交金额|成交价格|成交价)\s*[（(]\s*(亿元|万元|元)\s*[）)]")
_DEAL_PRICE_LABEL_RE = re.compile(r"(交易价格|成交金额|成交价格|成交价)")
_PAGE_UNIT_RE = re.compile(r"单位\s*[:：]?\s*(亿元|万元|元)")
_JSON_PRICE_UNIT_RE = re.compile(r'"(?:priceunitcj|priceunit|dealPriceUnit|deal_price_unit)"\s*:\s*"(亿元|万元|元)"', re.IGNORECASE)


def _strip_number_grouping(value: str) -> str:
    return value.replace(",", "").replace("，", "").strip()


def _html_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("&nbsp;", " ")
    return " ".join(text.split())


def infer_deal_price_unit_hint_from_html(html: Any) -> str:
    """Infer deal-price unit evidence from official page text or embedded JSON.

    This only returns units attached to deal amount labels, page-level amount unit
    declarations, or source JSON unit fields. It deliberately does not infer from
    unrelated tokens such as share counts or service fee prose.
    """
    raw_text = str(html or "")
    json_match = _JSON_PRICE_UNIT_RE.search(raw_text)
    if json_match:
        return f"交易价格单位:{json_match.group(1)}"

    text = _html_text(raw_text)
    label_match = _DEAL_PRICE_UNIT_LABEL_RE.search(text)
    if label_match:
        return label_match.group(0).strip()

    unit_match = _PAGE_UNIT_RE.search(text)
    if unit_match and _DEAL_PRICE_LABEL_RE.search(text):
        return f"交易价格 单位:{unit_match.group(1)}"
    return ""


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _detect_source_unit(text: str) -> str:
    normalized = str(text or "").replace("（", "(").replace("）", ")")
    if "亿元" in normalized or re.search(r"(?<!万)亿(?!元)", normalized):
        return "亿元"
    if "万元" in normalized or re.search(r"(?<!亿)万(?!元)", normalized):
        return "万元"
    if "元" in normalized:
        return "元"
    return ""


def _amount_from_source_unit(
    *,
    raw_text: str,
    amount: Decimal,
    source_unit: str,
    unit_basis: str,
) -> NormalizedDealAmount:
    if source_unit == "亿元":
        return NormalizedDealAmount(
            raw_text=raw_text,
            normalized_text=_format_decimal(amount * Decimal("10000")),
            unit="万元",
            source_unit=source_unit,
            unit_basis=unit_basis,
            unit_verified=True,
        )
    if source_unit == "元":
        return NormalizedDealAmount(
            raw_text=raw_text,
            normalized_text=_format_decimal(amount / Decimal("10000")),
            unit="万元",
            source_unit=source_unit,
            unit_basis=unit_basis,
            unit_verified=True,
        )
    if source_unit == "万元":
        return NormalizedDealAmount(
            raw_text=raw_text,
            normalized_text=_format_decimal(amount),
            unit="万元",
            source_unit=source_unit,
            unit_basis=unit_basis,
            unit_verified=True,
        )
    raise ValueError(f"unsupported source unit: {source_unit}")


def normalize_deal_amount_to_wan(value: Any, *, unit_hint: Any = "") -> NormalizedDealAmount:
    raw_text = str(value or "").strip()
    match = _NUMBER_RE.search(raw_text)
    if match is None:
        return NormalizedDealAmount(
            raw_text=raw_text,
            normalized_text="",
            unit="",
            source_unit="",
            unit_basis="missing",
            unit_verified=False,
        )
    number_text = _strip_number_grouping(match.group(0))
    try:
        amount = Decimal(number_text)
    except InvalidOperation:
        return NormalizedDealAmount(
            raw_text=raw_text,
            normalized_text="",
            unit="",
            source_unit="",
            unit_basis="missing",
            unit_verified=False,
        )

    source_unit = _detect_source_unit(raw_text)
    if source_unit:
        raw_basis = {
            "亿元": "converted_from_yi_yuan",
            "元": "converted_from_yuan",
            "万元": "raw_unit",
        }[source_unit]
        return _amount_from_source_unit(
            raw_text=raw_text,
            amount=amount,
            source_unit=source_unit,
            unit_basis=raw_basis,
        )
    hint_unit = _detect_source_unit(str(unit_hint or ""))
    if hint_unit:
        hint_basis = {
            "亿元": "converted_from_field_yi_yuan",
            "元": "converted_from_field_yuan",
            "万元": "field_unit_wan",
        }[hint_unit]
        return _amount_from_source_unit(
            raw_text=raw_text,
            amount=amount,
            source_unit=hint_unit,
            unit_basis=hint_basis,
        )
    return NormalizedDealAmount(
        raw_text=raw_text,
        normalized_text=_format_decimal(amount),
        unit="万元",
        source_unit="",
        unit_basis="default_wan",
        unit_verified=True,
    )


def apply_deal_price_amount_fields(canonical_fields: dict[str, Any], *, source_html: Any = "") -> dict[str, Any]:
    if not isinstance(canonical_fields, dict):
        raise TypeError("canonical_fields must be a dict")
    result = dict(canonical_fields)
    raw_value = result.get("deal_price_raw")
    if raw_value in (None, ""):
        raw_value = result.get("deal_price")
    unit_hint = result.get("deal_price_unit_hint") or infer_deal_price_unit_hint_from_html(source_html)
    if unit_hint and not result.get("deal_price_unit_hint"):
        result["deal_price_unit_hint"] = unit_hint
    normalized = normalize_deal_amount_to_wan(raw_value, unit_hint=unit_hint)
    if not normalized.raw_text and not normalized.normalized_text:
        return result
    result.update(normalized.as_canonical_fields(prefix="deal_price"))
    return result


__all__ = [
    "NormalizedDealAmount",
    "apply_deal_price_amount_fields",
    "infer_deal_price_unit_hint_from_html",
    "normalize_deal_amount_to_wan",
]
