#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deal parser for Shanghai exchange (SSE) with canonical payload keys."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from peap_core.business_catalog import resolve_business_descriptor

from .base import ParserOutput, WebPageParser
from .deal_aliases import deal_field_aliases, lookup_by_alias

_SUMMARY_TOKENS = ("总计", "合计", "小计", "总额")
_DATE_IMPUTE_REMARK = "成交日期缺失，按采集日填列"
_EMPTY_DASH_TOKENS = frozenset(("-", "–", "—", "－", "―", "万元", "元", "（万元）", "(万元)", "（元）", "(元)"))
_MONEY_UNIT_RE = re.compile(r"(亿元|万元|元)")
_PAGE_UNIT_RE = re.compile(r"单位\s*[:：]?\s*(亿元|万元|元)")


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _is_missing_value(value: Any) -> bool:
    if value in (None, "", [], (), {}):
        return True
    if isinstance(value, str) and value.strip() in _EMPTY_DASH_TOKENS:
        return True
    return False


def _split_text_list(value: str) -> list[str]:
    text = _normalize_text(value)
    if not text:
        return []
    return [item for item in re.split(r"[;,；、|\n]+", text) if item.strip()]


def _append_remark(existing: str, suffix: str) -> str:
    current = _normalize_text(existing)
    extra = _normalize_text(suffix)
    if not extra:
        return current
    if not current:
        return extra
    if extra in current:
        return current
    return f"{current}；{extra}"


def _summary_key(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    return text.strip(":：;；,，.。")


def _is_summary_row_label(value: Any) -> bool:
    key = _summary_key(value)
    return bool(key) and key in _SUMMARY_TOKENS


def _canonical_date_basis(alias: str) -> str:
    text = str(alias or "").strip()
    mapping = {
        "contractSignTime": "contract_sign_time",
        "contract_sign_time": "contract_sign_time",
        "dealDate": "deal_date",
        "deal_date": "deal_date",
        "cjrq": "deal_date",
        "CJRQ": "deal_date",
        "成交日期": "deal_date",
    }
    if text in mapping:
        return mapping[text]
    snake = re.sub(r"(?<!^)([A-Z])", r"_\1", text).lower()
    return snake or "deal_date"


def _identity_compare_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value)).casefold()


class CanonicalDealParserBase(WebPageParser):
    EXCHANGE = ""
    JSON_SCRIPT_IDS: tuple[str, ...] = ("deal_detail", "deal-json", "deal-data")
    JSON_TEXTAREA_ID: str | None = None
    FIELD_ALIASES: dict[str, tuple[str, ...]] = {}

    def _aliases(self, field_name: str) -> tuple[str, ...]:
        return deal_field_aliases(field_name, self.FIELD_ALIASES.get(field_name, ()))

    @staticmethod
    def _parse_json_node(node) -> dict[str, Any]:
        if node is None:
            return {}
        text = node.string if node.string is not None else node.get_text(" ", strip=False)
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        node_id = _normalize_text(node.get("id"))
        label = node_id or _normalize_text(getattr(node, "name", "")) or "JSON payload"
        raise ValueError(f"{label} root must be an object")

    def _load_snapshot_metadata(self) -> dict[str, Any]:
        for script_id in ("deal_metadata", "deal-metadata"):
            node = self.soup.find("script", id=script_id)
            parsed = self._parse_json_node(node)
            if parsed:
                return parsed
        return {}

    def _load_json_payload(self) -> dict[str, Any]:
        if self.JSON_TEXTAREA_ID:
            textarea = self.soup.find("textarea", id=self.JSON_TEXTAREA_ID)
            parsed = self._parse_json_node(textarea)
            if parsed:
                return parsed

        for script_id in self.JSON_SCRIPT_IDS:
            script = self.soup.find("script", id=script_id)
            parsed = self._parse_json_node(script)
            if parsed:
                return parsed

        for script in self.soup.find_all("script"):
            script_type = _normalize_text(script.get("type"))
            if "json" not in script_type.lower():
                continue
            parsed = self._parse_json_node(script)
            if parsed:
                return parsed
        return {}

    def _iter_payload_maps(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        if not isinstance(payload, dict):
            return ()

        queue = [payload]
        seen: set[int] = set()
        candidates = []
        while queue:
            current = queue.pop(0)
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
            candidates.append(current)

            for key in ("object", "detail", "data", "result", "project"):
                child = current.get(key)
                if isinstance(child, dict):
                    queue.append(child)
                elif isinstance(child, list):
                    queue.extend(item for item in child if isinstance(item, dict))
            for value in current.values():
                if isinstance(value, dict):
                    queue.append(value)
                elif isinstance(value, list):
                    queue.extend(item for item in value if isinstance(item, dict))
        return tuple(candidates)

    @staticmethod
    def _table_cell_texts(cells: Iterable[Any]) -> list[str]:
        return [_normalize_text(cell.get_text(" ", strip=True)) for cell in cells]

    @staticmethod
    def _is_structured_header_row(cells: list[Any], texts: list[str]) -> bool:
        if len(cells) < 2 or not any(texts):
            return False
        if all(getattr(cell, "name", "").lower() == "th" for cell in cells):
            return True
        header_fields = (
            "project_code",
            "project_name",
            "business_type",
            "deal_price",
            "valuation",
            "reserve_price",
            "deal_date",
            "party_label",
            "party_name",
            "investor_name",
            "investment_amount",
            "share_ratio",
            "actual_contribution",
        )
        header_score = 0
        for text in texts:
            aliases = (
                alias
                for field_name in header_fields
                for alias in deal_field_aliases(field_name)
            )
            if lookup_by_alias({text: text}, aliases)[1]:
                header_score += 1
        return header_score >= 2

    def _structured_table_rows(self) -> tuple[dict[str, str], ...]:
        cached = getattr(self, "_structured_table_rows_cache", None)
        if cached is not None:
            return cached

        rows: list[dict[str, str]] = []
        for table in self.soup.find_all("table"):
            headers: list[str] = []
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                texts = self._table_cell_texts(cells)
                if self._is_structured_header_row(cells, texts):
                    headers = texts
                    continue
                if not headers:
                    continue
                row = {
                    header: value
                    for header, value in zip(headers, texts, strict=False)
                    if header and value
                }
                if row:
                    rows.append(row)

        result = tuple(rows)
        self._structured_table_rows_cache = result
        return result

    def _table_rows(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        for tr in self.soup.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            texts = self._table_cell_texts(cells)
            if self._is_structured_header_row(cells, texts):
                continue
            for index in range(0, len(cells) - 1, 2):
                label_cell = cells[index]
                value_cell = cells[index + 1]
                if getattr(label_cell, "name", "").lower() != "th":
                    continue
                if getattr(value_cell, "name", "").lower() == "th":
                    continue
                label = texts[index]
                value = texts[index + 1]
                if label and value and label not in rows:
                    rows[label] = value

        for row in self._structured_table_rows():
            for label, value in row.items():
                if label and value:
                    rows[label] = value
        return rows

    @staticmethod
    def _payload_lookup(payload: dict[str, Any], key: str) -> Any:
        value, _ = lookup_by_alias(payload, (key,))
        return value

    def _project_code_from_payload(self, payload: dict[str, Any]) -> str:
        for node in self._iter_payload_maps(payload):
            value, _ = self._pick_text((node,), {}, self._aliases("project_code"))
            if value:
                return value
        return ""

    def _select_detail_candidate_for_metadata(
        self,
        detail_payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(detail_payload, dict) or not isinstance(metadata, dict):
            return detail_payload

        candidates = detail_payload.get("candidates")
        if not isinstance(candidates, list):
            return detail_payload

        metadata_code = _normalize_text(metadata.get("project_code") or metadata.get("projectCode"))
        if not metadata_code:
            return detail_payload

        matches = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and _identity_compare_text(self._project_code_from_payload(candidate)) == _identity_compare_text(metadata_code)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(
                "identity mismatch between deal_metadata and deal_detail candidates: "
                f"project_code={metadata_code!r} matched no candidate"
            )
        raise ValueError(
            "ambiguous deal_detail candidates: metadata project_code matched multiple candidates"
        )

    def _prepare_selected_detail_payload(self, detail_payload: dict[str, Any]) -> dict[str, Any]:
        return detail_payload

    def _pick_raw(
        self,
        payload_maps: Iterable[dict[str, Any]],
        row_map: dict[str, str],
        aliases: tuple[str, ...],
    ) -> tuple[Any, str]:
        for alias in aliases:
            for payload in payload_maps:
                value = self._payload_lookup(payload, alias)
                if _is_missing_value(value):
                    continue
                return value, alias
            value, matched_alias = lookup_by_alias(row_map, (alias,))
            if not _is_missing_value(value):
                return value, matched_alias or alias
        return None, ""

    def _pick_text(
        self,
        payload_maps: Iterable[dict[str, Any]],
        row_map: dict[str, str],
        aliases: tuple[str, ...],
    ) -> tuple[str, str]:
        raw, alias = self._pick_raw(payload_maps, row_map, aliases)
        return _normalize_text(raw), alias

    @staticmethod
    def _money_unit_from_text(value: Any) -> str:
        match = _MONEY_UNIT_RE.search(_normalize_text(value))
        return match.group(1) if match else ""

    def _page_level_money_unit(self) -> str:
        body_text = _normalize_text(self.soup.get_text(" ", strip=True))
        match = _PAGE_UNIT_RE.search(body_text)
        return match.group(1) if match else ""

    def _deal_price_unit_hint(
        self,
        *,
        deal_price_alias: str,
        payload_maps: Iterable[dict[str, Any]],
        row_map: dict[str, str],
    ) -> str:
        if self._money_unit_from_text(deal_price_alias):
            return deal_price_alias
        unit_aliases = (
            "deal_price_unit",
            "dealPriceUnit",
            "deal_price_unit_hint",
            "price_unit_hint",
            "priceunitcj",
            "priceunit",
            "amountUnit",
            "金额单位",
            "交易价格单位",
            "成交金额单位",
        )
        unit = ""
        for alias in unit_aliases:
            for payload in payload_maps:
                value = self._payload_lookup(payload, alias)
                unit = self._money_unit_from_text(value)
                if unit:
                    break
            if unit:
                break
            value, _ = lookup_by_alias(row_map, (alias,))
            unit = self._money_unit_from_text(value)
            if unit:
                break
        if unit:
            return f"交易价格单位:{unit}"
        page_unit = self._page_level_money_unit()
        if page_unit and deal_price_alias:
            return f"{deal_price_alias} 单位:{page_unit}"
        return deal_price_alias

    def _assert_metadata_detail_identity_matches(
        self,
        metadata: dict[str, Any],
        detail_payload: dict[str, Any],
    ) -> None:
        if not metadata or not detail_payload:
            return

        metadata_maps = self._iter_payload_maps(metadata)
        detail_maps = self._iter_payload_maps(detail_payload)
        fields = (
            ("project_code", self._aliases("project_code")),
            ("project_name", self._aliases("project_name")),
            ("business_id", self._aliases("business_type")),
        )
        for metadata_field, detail_aliases in fields:
            metadata_value, _ = self._pick_text(metadata_maps, {}, (metadata_field,))
            detail_value, _ = self._pick_text(detail_maps, {}, detail_aliases)
            if not metadata_value or not detail_value:
                continue
            if metadata_field == "business_id":
                metadata_value = self._normalize_business_type(metadata_value)
                detail_value = self._normalize_business_type(detail_value)
            if _identity_compare_text(metadata_value) == _identity_compare_text(detail_value):
                continue
            raise ValueError(
                "identity mismatch between deal_metadata and deal_detail: "
                f"{metadata_field}={metadata_value!r} detail={detail_value!r}"
            )

    def _normalize_business_type(self, value: Any) -> str:
        normalized = self._try_normalize_business_type(value)
        if normalized:
            return normalized
        raw = _normalize_text(value) or "<missing>"
        raise ValueError(f"unsupported deal business type: {raw}")

    def _try_normalize_business_type(self, value: Any) -> str:
        text = _normalize_text(value)
        token = text.upper()
        if any(marker in token for marker in ("1C", "CAPITAL", "INCREASE", "DEAL_CAPITAL", "增资")):
            return "增资扩股"
        if any(marker in token for marker in ("SW", "ASSET", "PHYSICAL", "DEAL_PHYSICAL", "实物", "资产")):
            return "实物资产"
        if any(marker in token for marker in ("GQ", "EQUITY", "PROPERTY", "TRANSFER", "DEAL_EQUITY", "股权", "产权")):
            return "股权转让"
        if not text and self.context.allow_unknown_deal_business_type:
            return "未知"
        return ""

    def _business_type_from_hint(self, payload_maps: Iterable[dict[str, Any]]) -> str:
        for alias in (
            "business_id_hint",
            "businessIdHint",
            "business_id",
            "businessId",
            "business_label_hint",
            "businessLabelHint",
            "business_label",
            "businessLabel",
        ):
            for payload in payload_maps:
                value = self._payload_lookup(payload, alias)
                if _is_missing_value(value):
                    continue
                descriptor = resolve_business_descriptor(value, family_id="deal")
                if descriptor is not None and descriptor.project_type_label:
                    return descriptor.project_type_label
        return ""

    def _normalize_business_type_with_hint(
        self,
        value: Any,
        *,
        payload_maps: Iterable[dict[str, Any]],
    ) -> str:
        normalized = self._try_normalize_business_type(value)
        if normalized:
            return normalized
        hinted = self._business_type_from_hint(payload_maps)
        if hinted:
            return hinted
        return self._normalize_business_type(value)

    @staticmethod
    def _normalize_name_list(value: Any) -> list[str]:
        if isinstance(value, list):
            items: list[str] = []
            for entry in value:
                if isinstance(entry, dict):
                    name = _normalize_text(
                        entry.get("name")
                        or entry.get("partyName")
                        or entry.get("projectPartyName")
                        or entry.get("investorName")
                        or entry.get("value")
                    )
                else:
                    name = _normalize_text(entry)
                if name:
                    items.append(name)
            return items
        if isinstance(value, tuple):
            return [name for name in (_normalize_text(item) for item in value) if name]
        return _split_text_list(_normalize_text(value))

    @classmethod
    def _normalize_project_parties(cls, value: Any) -> list[dict[str, str]]:
        if not value:
            return []

        parties: list[dict[str, str]] = []
        if isinstance(value, dict):
            for label, name in value.items():
                label_text = _normalize_text(label)
                names = cls._normalize_name_list(name)
                for name_text in names:
                    parties.append({"label": label_text, "name": name_text})
            return parties

        if isinstance(value, (list, tuple)):
            for entry in value:
                if isinstance(entry, dict):
                    label = _normalize_text(
                        entry.get("label")
                        or entry.get("role")
                        or entry.get("type")
                        or entry.get("partyType")
                    )
                    name = _normalize_text(
                        entry.get("name")
                        or entry.get("partyName")
                        or entry.get("projectPartyName")
                        or entry.get("value")
                    )
                else:
                    label = ""
                    name = _normalize_text(entry)
                if name:
                    parties.append({"label": label, "name": name})
        return parties

    @classmethod
    def _normalize_investors(cls, value: Any) -> list[dict[str, Any]]:
        if not value:
            return []

        raw_items: list[Any]
        if isinstance(value, (list, tuple)):
            raw_items = list(value)
        elif isinstance(value, dict):
            raw_items = [value]
        else:
            raw_items = _split_text_list(_normalize_text(value))

        investors: list[dict[str, Any]] = []
        for entry in raw_items:
            if isinstance(entry, dict):
                name = _normalize_text(
                    lookup_by_alias(
                        entry,
                        deal_field_aliases(
                            "investor_name",
                            (
                                "name",
                                "investorName",
                                "investor",
                                "transfereeName",
                                "transferee",
                                "受让方名称",
                                "value",
                            ),
                        ),
                    )[0]
                )
                if not name or _is_summary_row_label(name):
                    continue
                normalized: dict[str, Any] = {"name": name}
                label = _normalize_text(entry.get("label") or entry.get("role"))
                amount = _normalize_text(
                    lookup_by_alias(
                        entry,
                        deal_field_aliases(
                            "investment_amount",
                            (
                                "amount",
                                "investmentAmount",
                                "investment_amount",
                                "dealAmount",
                                "transfereeAmount",
                                "subscriptionAmount",
                                "认购金额",
                            ),
                        ),
                    )[0]
                )
                ratio = _normalize_text(
                    lookup_by_alias(
                        entry,
                        deal_field_aliases(
                            "share_ratio",
                            (
                                "ratio",
                                "shareRatio",
                                "share_ratio",
                                "stockPercent",
                                "holdingRatio",
                                "持股占比",
                                "持股占比（%）",
                            ),
                        ),
                    )[0]
                )
                actual_contribution = _normalize_text(
                    lookup_by_alias(
                        entry,
                        deal_field_aliases("actual_contribution"),
                    )[0]
                )
                if label:
                    normalized["label"] = label
                if amount:
                    normalized["amount"] = amount
                if ratio:
                    normalized["ratio"] = ratio
                if actual_contribution:
                    normalized["actual_contribution"] = actual_contribution
                investors.append(normalized)
                continue

            name = _normalize_text(entry)
            if not name or _is_summary_row_label(name):
                continue
            investors.append({"name": name})
        return investors

    @classmethod
    def _project_parties_from_structured_rows(cls, rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
        parties: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def append_party(label: str, value: Any) -> None:
            label_text = _normalize_text(label)
            for name_text in cls._normalize_name_list(value):
                key = (label_text, name_text)
                if not name_text or key in seen:
                    continue
                seen.add(key)
                parties.append({"label": label_text, "name": name_text})

        for row in rows:
            label = _normalize_text(lookup_by_alias(row, deal_field_aliases("party_label"))[0])
            name = _normalize_text(lookup_by_alias(row, deal_field_aliases("party_name"))[0])
            if label and name:
                append_party(label, name)

            for role in ("转让方", "出让方", "受让方", "融资方", "增资方", "增资企业"):
                role_value, _ = lookup_by_alias(row, (role, f"{role}名称"))
                if role_value:
                    append_party(role, role_value)
        return parties

    def _parties_from_structured_tables(self) -> list[dict[str, str]]:
        return self._project_parties_from_structured_rows(self._structured_table_rows())

    def _parties_from_dom(self) -> list[dict[str, str]]:
        container = self.soup.select_one("[data-project-parties]")
        if container is None:
            return []
        parties: list[dict[str, str]] = []
        for node in container.find_all(["li", "div"], recursive=False):
            name = _normalize_text(node.get_text(" ", strip=True))
            if not name:
                continue
            label = _normalize_text(node.get("data-label") or node.get("data-role"))
            parties.append({"label": label, "name": name})
        return parties

    def _list_from_dom(self, selector: str) -> list[str]:
        container = self.soup.select_one(selector)
        if container is None:
            return []
        names: list[str] = []
        for node in container.find_all(["li", "div"], recursive=False):
            name = _normalize_text(node.get_text(" ", strip=True))
            if name:
                names.append(name)
        return names

    def _investors_from_dom(self) -> list[dict[str, Any]]:
        table = self.soup.select_one("table[data-investors]")
        if table is None:
            return []
        investors: list[dict[str, Any]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            name = _normalize_text(cells[0].get_text(" ", strip=True))
            if not name or _is_summary_row_label(name):
                continue
            row: dict[str, Any] = {"name": name}
            if len(cells) >= 2:
                amount = _normalize_text(cells[1].get_text(" ", strip=True))
                if amount:
                    row["amount"] = amount
            investors.append(row)
        return investors

    @staticmethod
    def _structured_row_looks_like_investor(row: dict[str, str]) -> bool:
        _, name_alias = lookup_by_alias(row, deal_field_aliases("investor_name"))
        if not name_alias:
            return False
        normalized_alias = name_alias.lower()
        if any(token in name_alias for token in ("投资", "投资方", "投资人", "TZFMC")) or "investor" in normalized_alias:
            return True
        for field_name in ("investment_amount", "share_ratio", "actual_contribution"):
            value, _ = lookup_by_alias(row, deal_field_aliases(field_name))
            if value not in (None, "", [], (), {}):
                return True
        return False

    def _investors_from_structured_tables(self) -> list[dict[str, Any]]:
        investors: list[dict[str, Any]] = []
        for row in self._structured_table_rows():
            if not self._structured_row_looks_like_investor(row):
                continue
            investors.extend(self._normalize_investors(row))
        return investors

    def parse(self) -> ParserOutput:
        snapshot_metadata = self._load_snapshot_metadata()
        payload = self._load_json_payload()
        payload = self._select_detail_candidate_for_metadata(payload, snapshot_metadata)
        payload = self._prepare_selected_detail_payload(payload)
        self._assert_metadata_detail_identity_matches(snapshot_metadata, payload)
        if snapshot_metadata:
            payload = {**snapshot_metadata, **payload}
        payload_maps = self._iter_payload_maps(payload)
        row_map = self._table_rows()
        metadata_maps = self._iter_payload_maps(snapshot_metadata)

        project_code, _ = self._pick_text(payload_maps, row_map, self._aliases("project_code"))
        project_name, _ = self._pick_text(payload_maps, row_map, self._aliases("project_name"))
        business_type_raw, _ = self._pick_text(payload_maps, row_map, self._aliases("business_type"))
        deal_price, deal_price_alias = self._pick_text(payload_maps, row_map, self._aliases("deal_price"))
        deal_price_unit_hint = self._deal_price_unit_hint(
            deal_price_alias=deal_price_alias,
            payload_maps=payload_maps,
            row_map=row_map,
        )
        valuation, _ = self._pick_text(payload_maps, row_map, self._aliases("valuation"))
        reserve_price, _ = self._pick_text(payload_maps, row_map, self._aliases("reserve_price"))
        deal_date_raw, deal_date_alias = self._pick_text(payload_maps, row_map, self._aliases("deal_date"))
        collection_date_raw, _ = self._pick_text(payload_maps, row_map, self._aliases("collection_date"))
        remark, _ = self._pick_text(payload_maps, row_map, self._aliases("remark"))
        deal_method, _ = self._pick_text(payload_maps, row_map, self._aliases("deal_method"))
        buyer_name, _ = self._pick_text(payload_maps, row_map, self._aliases("buyer_name"))
        auction_flag, _ = self._pick_text(payload_maps, row_map, self._aliases("auction_flag"))
        deal_status, _ = self._pick_text(payload_maps, row_map, self._aliases("deal_status"))

        collection_date = self.clean_date(collection_date_raw or "") or ""
        deal_date = self.clean_date(deal_date_raw or "") or ""
        if not collection_date and deal_date:
            collection_date = deal_date

        if deal_date:
            deal_date_basis = _canonical_date_basis(deal_date_alias or "deal_date")
            deal_date_is_imputed = False
        elif collection_date:
            deal_date = collection_date
            deal_date_basis = "collection_date"
            deal_date_is_imputed = True
            remark = _append_remark(remark, _DATE_IMPUTE_REMARK)
        else:
            deal_date_basis = "missing"
            deal_date_is_imputed = False

        metadata_deal_date, _ = self._pick_text(metadata_maps, {}, self._aliases("deal_date"))
        metadata_collection_date, _ = self._pick_text(metadata_maps, {}, self._aliases("collection_date"))
        metadata_basis = _normalize_text(snapshot_metadata.get("deal_date_basis") or snapshot_metadata.get("dealDateBasis"))
        metadata_imputed_raw = snapshot_metadata.get("deal_date_is_imputed")
        if metadata_imputed_raw is None:
            metadata_imputed_raw = snapshot_metadata.get("dealDateIsImputed")
        metadata_remark_suffix = _normalize_text(
            snapshot_metadata.get("deal_date_remark_suffix")
            or snapshot_metadata.get("remark_suffix")
            or snapshot_metadata.get("dealDateRemarkSuffix")
        )
        metadata_imputed = None
        if isinstance(metadata_imputed_raw, bool):
            metadata_imputed = metadata_imputed_raw
        else:
            bool_text = _normalize_text(metadata_imputed_raw).lower()
            if bool_text in {"true", "1", "yes", "y"}:
                metadata_imputed = True
            elif bool_text in {"false", "0", "no", "n"}:
                metadata_imputed = False

        if metadata_deal_date:
            deal_date = self.clean_date(metadata_deal_date) or deal_date
        if metadata_collection_date:
            collection_date = self.clean_date(metadata_collection_date) or collection_date
        if metadata_basis:
            deal_date_basis = _canonical_date_basis(metadata_basis)
        if metadata_imputed is not None:
            deal_date_is_imputed = metadata_imputed
        if metadata_remark_suffix and deal_date_is_imputed:
            remark = _append_remark(remark, metadata_remark_suffix)

        project_parties_raw, _ = self._pick_raw(payload_maps, row_map, self._aliases("project_parties"))
        transferors_raw, _ = self._pick_raw(payload_maps, row_map, self._aliases("transferors"))
        financing_raw, _ = self._pick_raw(payload_maps, row_map, self._aliases("financing_party_names"))
        capital_company, _ = self._pick_text(payload_maps, row_map, self._aliases("capital_company_name"))
        investors_raw, _ = self._pick_raw(payload_maps, row_map, self._aliases("investors"))
        investor_name, _ = self._pick_text(payload_maps, row_map, self._aliases("investor_name"))
        investment_amount, _ = self._pick_text(payload_maps, row_map, self._aliases("investment_amount"))
        share_ratio, _ = self._pick_text(payload_maps, row_map, self._aliases("share_ratio"))
        total_investment_amount, _ = self._pick_text(payload_maps, row_map, self._aliases("total_investment_amount"))
        holding_ratio, _ = self._pick_text(payload_maps, row_map, self._aliases("holding_ratio"))

        project_parties = (
            self._normalize_project_parties(project_parties_raw)
            or self._parties_from_structured_tables()
            or self._parties_from_dom()
        )
        transferors = self._normalize_name_list(transferors_raw) or self._list_from_dom("[data-transferors]")
        if not transferors:
            transferors = [
                party["name"]
                for party in project_parties
                if any(token in _normalize_text(party.get("label")) for token in ("转让方", "出让方"))
            ]

        financing_party_names = self._normalize_name_list(financing_raw) or self._list_from_dom("[data-financing-party-names]")
        if not financing_party_names:
            financing_party_names = [
                party["name"]
                for party in project_parties
                if any(token in _normalize_text(party.get("label")) for token in ("融资方", "增资方", "增资企业"))
            ]
        if not capital_company and financing_party_names:
            capital_company = financing_party_names[0]

        if not investment_amount and investor_name and not _is_summary_row_label(investor_name):
            investment_amount = total_investment_amount
        if not share_ratio and investor_name and not _is_summary_row_label(investor_name):
            share_ratio = holding_ratio

        investors = (
            self._normalize_investors(investors_raw)
            or self._investors_from_structured_tables()
            or self._investors_from_dom()
        )
        if not investors and investor_name and not _is_summary_row_label(investor_name):
            investor_entry: dict[str, Any] = {"name": investor_name}
            if investment_amount:
                investor_entry["amount"] = investment_amount
            if share_ratio:
                investor_entry["ratio"] = share_ratio
            investors = [investor_entry]

        standard_payload: dict[str, Any] = {
            "record_family": "deal",
            "page_kind": "deal",
            "project_code": project_code,
            "project_name": project_name,
            "business_type": self._normalize_business_type_with_hint(
                business_type_raw,
                payload_maps=payload_maps,
            ),
            "status": "成交",
            "exchange": self.EXCHANGE,
            "deal_date": deal_date,
            "deal_date_basis": deal_date_basis,
            "deal_date_is_imputed": deal_date_is_imputed,
            "collection_date": collection_date,
            "deal_price": deal_price,
            "deal_price_unit_hint": deal_price_unit_hint,
            "valuation": valuation,
            "reserve_price": reserve_price,
            "deal_date_audit": {
                "basis": deal_date_basis,
                "is_imputed": deal_date_is_imputed,
            },
        }
        if remark:
            standard_payload["remark"] = remark
        if deal_method:
            standard_payload["deal_method"] = deal_method
        if buyer_name:
            standard_payload["buyer_name"] = buyer_name
        if auction_flag:
            standard_payload["是否竞价"] = auction_flag
        if deal_status:
            standard_payload["是否成交"] = deal_status
        if project_parties:
            standard_payload["project_parties"] = project_parties
        if transferors:
            standard_payload["transferors"] = transferors
        if financing_party_names:
            standard_payload["financing_party_names"] = financing_party_names
        if capital_company:
            standard_payload["capital_company_name"] = capital_company
            standard_payload["capital_increase_company_name"] = capital_company
        if investor_name:
            standard_payload["investor_name"] = investor_name
        if investment_amount:
            standard_payload["investment_amount"] = investment_amount
        if share_ratio:
            standard_payload["share_ratio"] = share_ratio
        if total_investment_amount:
            standard_payload["total_investment_amount"] = total_investment_amount
        if holding_ratio:
            standard_payload["holding_ratio"] = holding_ratio
        if investors:
            standard_payload["investors"] = investors
        return self.build_parser_output(standard_payload=standard_payload)


class DealSSEParser(CanonicalDealParserBase):
    EXCHANGE = "上交所"
    FIELD_ALIASES = {
        "project_code": ("project_code", "xmbh", "XMBH", "项目编号"),
        "project_name": ("project_name", "xmmc", "XMMC", "项目名称"),
        "business_type": ("business_type", "business_id", "FCLASS", "fclass", "FCLASSMC", "xmlx", "XMLX", "业务类型", "项目类型"),
        "deal_date": ("deal_date", "cjrq", "CJRQ", "成交日期"),
        "collection_date": ("collection_date", "fbsj", "publishDate", "采集日期"),
        "deal_price": ("deal_price", "cjjg", "CJJG", "成交金额", "成交价格"),
        "valuation": ("valuation", "pgjz", "valuationValue", "DWPGZ", "PGZ", "DJPGZ", "评估值"),
        "reserve_price": ("reserve_price", "zrdf", "reservePrice", "ZRDJ", "ZRDANJ", "转让底价", "挂牌底价"),
        "project_parties": ("project_parties", "projectParties", "partyList"),
        "transferors": ("transferors", "transferorNames"),
        "financing_party_names": ("financing_party_names", "financingPartyNames"),
        "capital_company_name": ("capital_company_name", "capital_increase_company_name", "capitalIncreaseCompanyName", "ZZFQYMC"),
        "investors": ("investors", "investorList", "transferee_details", "transfereeDetails"),
        "investor_name": ("investor_name", "TZFMC"),
        "investment_amount": ("investment_amount", "investmentAmount", "dealAmount"),
        "share_ratio": ("share_ratio", "shareRatio", "holdingRatio", "持股占比", "持股占比（%）"),
        "total_investment_amount": ("total_investment_amount", "ZJCZE"),
        "holding_ratio": ("holding_ratio", "ZZZHBL"),
        "remark": ("remark", "备注"),
        "deal_method": ("deal_method", "dealMethod", "transactionMethod", "交易方式"),
        "buyer_name": ("buyer_name", "buyerName", "transfereeName", "transferee", "受让方名称", "受让方"),
        "auction_flag": ("auction_flag", "isAuction", "is_auction", "sfjj", "SFJJ", "是否竞价"),
        "deal_status": ("deal_status", "isDeal", "dealStatus", "sf_cj", "SFCJ", "是否成交"),
    }


__all__ = ["CanonicalDealParserBase", "DealSSEParser"]
