#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deal parser for Beijing exchange (CBEX) with canonical payload keys."""

from __future__ import annotations

import json
from html import unescape
from typing import Any

from .deal_aliases import lookup_by_alias
from .deal_sse import CanonicalDealParserBase


class DealCBEXParser(CanonicalDealParserBase):
    EXCHANGE = "北交所"
    JSON_TEXTAREA_ID = "jsonobj"
    FIELD_ALIASES = {
        "project_code": ("projectCode", "project_code", "projectcode", "ProjectNo", "XMBH", "项目编号"),
        "project_name": ("projectName", "project_name", "projectname", "ProjectName", "XMMC", "object", "项目名称"),
        "business_type": ("businessType", "business_type", "business", "bizType", "业务类型", "项目类型"),
        "deal_date": ("dealDate", "deal_date", "cjrq", "tradedate", "tradeDate", "成交日期"),
        "collection_date": ("collectionDate", "collection_date", "publishDate", "fbsj", "采集日期", "发布日期"),
        "deal_price": ("dealPrice", "deal_price", "cjjg", "tradevalue", "bidprice", "成交金额", "成交价格"),
        "valuation": (
            "valuation",
            "pgjz",
            "valuationValue",
            "evaluatevalue",
            "objectevaluatevalue",
            "评估值",
            "转让标的评估值",
        ),
        "reserve_price": ("reservePrice", "reserve_price", "zrdf", "objectprice", "转让底价", "挂牌底价"),
        "project_parties": ("projectParties", "project_parties", "partyList"),
        "transferors": ("transferors", "transferorNames"),
        "financing_party_names": ("financingPartyNames", "financing_party_names"),
        "capital_company_name": ("capitalIncreaseCompanyName", "capital_company_name", "capital_increase_company_name"),
        "investors": ("investors", "investorList"),
        "remark": ("remark", "备注"),
        "deal_method": ("dealMethod", "deal_method", "transactionMethod", "transaction_method", "交易方式"),
        "buyer_name": (
            "buyerName",
            "buyer_name",
            "buyername",
            "transfereeName",
            "transferee",
            "bidder",
            "受让方名称",
            "受让方",
        ),
        "auction_flag": ("isAuction", "is_auction", "auctionFlag", "是否竞价"),
        "deal_status": ("isDeal", "dealStatus", "deal_status", "是否成交"),
        "investor_name": ("investor_name", "investorName", "investorname", "bidder"),
        "investment_amount": ("investment_amount", "investmentAmount", "tradevalue", "bidprice", "pertradevalue"),
        "share_ratio": ("share_ratio", "shareRatio", "stockpercent", "holdingratio", "pertradepercent"),
    }

    def _parse_json_text(self, text: str) -> dict[str, Any]:
        parsed = json.loads(unescape(text))
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("textarea.source root must be an object")

    def _infer_business_type(self, payload: dict[str, Any]) -> str:
        if "utrzcemsproject" in payload:
            return "增资扩股"
        if "utrmcemsproject" in payload:
            return "实物资产"
        if "utrgcemsproject" in payload:
            return "股权转让"
        return ""

    def _extract_project_code(self, payload: dict[str, Any]) -> str:
        for node in self._iter_payload_maps(payload):
            value, _ = self._pick_text((node,), {}, self._aliases("project_code"))
            if value:
                return value
        return ""

    @staticmethod
    def _first_text(entry: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = entry.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _normalize_cbex_investors(self, entries: Any) -> list[dict[str, str]]:
        if not isinstance(entries, list):
            return []
        investors: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = self._first_text(entry, "investorname", "investorName", "bidder", "buyername")
            if not name:
                continue
            investor: dict[str, str] = {"name": name}
            amount = self._first_text(entry, "tradevalue", "bidprice", "pertradevalue")
            ratio = self._first_text(entry, "stockpercent", "holdingratio", "pertradepercent")
            if amount:
                investor["amount"] = amount
            if ratio:
                investor["ratio"] = ratio
            investors.append(investor)
        return investors

    def _annotate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        annotated = dict(payload)
        business_type = self._infer_business_type(annotated)
        if business_type and not annotated.get("businessType"):
            annotated["businessType"] = business_type

        holderlist = annotated.get("holderlist")
        if isinstance(holderlist, dict):
            holders = holderlist.get("utrzcemsshareholder")
            if isinstance(holders, list):
                holder_names = [
                    str(holder.get("holdername") or "").strip()
                    for holder in holders
                    if isinstance(holder, dict) and str(holder.get("holdername") or "").strip()
                ]
                if holder_names and not annotated.get("financingPartyNames"):
                    annotated["financingPartyNames"] = holder_names
                if holder_names and not annotated.get("capitalIncreaseCompanyName"):
                    annotated["capitalIncreaseCompanyName"] = holder_names[0]

        tradelist = annotated.get("tradelist")
        if isinstance(tradelist, dict):
            trades = tradelist.get("utrzcemstrade")
            normalized = self._normalize_cbex_investors(trades)
            if normalized and not annotated.get("investors"):
                annotated["investors"] = normalized

        bidinfolist = annotated.get("bidinfolist")
        if isinstance(bidinfolist, dict) and not annotated.get("investors"):
            bids = bidinfolist.get("utrmcemsbidinfo") or bidinfolist.get("utrgcemsbidinfo")
            normalized = self._normalize_cbex_investors(bids)
            if normalized:
                annotated["investors"] = normalized
                first_name = normalized[0].get("name")
                if first_name and not annotated.get("buyerName"):
                    annotated["buyerName"] = first_name

        sellerlist = annotated.get("sellerlist")
        if isinstance(sellerlist, dict):
            sellers = sellerlist.get("utrgcemsseller") or sellerlist.get("utrmcemsseller")
            if isinstance(sellers, list):
                names = [
                    str(
                        seller.get("sellername")
                        or seller.get("holdername")
                        or seller.get("name")
                        or ""
                    ).strip()
                    for seller in sellers
                    if isinstance(seller, dict)
                ]
                names = [name for name in names if name]
                if names and not annotated.get("transferorNames"):
                    annotated["transferorNames"] = names
        return annotated

    def _prepare_selected_detail_payload(self, detail_payload: dict[str, Any]) -> dict[str, Any]:
        return self._annotate_payload(detail_payload)

    def _load_textarea_source_payload(self) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for node in self.soup.select("textarea.source"):
            text = node.string if node.string is not None else node.get_text(" ", strip=False)
            parsed = self._parse_json_text(text)
            if parsed:
                candidates.append(self._annotate_payload(parsed))
        if not candidates:
            return {}
        if len(candidates) == 1:
            return candidates[0]

        project_code = str(self._load_snapshot_metadata().get("project_code") or "").strip().upper()
        if project_code:
            matches = [
                payload
                for payload in candidates
                if self._extract_project_code(payload).strip().upper() == project_code
            ]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise ValueError(
                    "ambiguous textarea.source payloads: metadata project_code did not match any candidate"
                )
            raise ValueError(
                "ambiguous textarea.source payloads: metadata project_code matched multiple candidates"
            )

        raise ValueError("ambiguous textarea.source payloads: missing metadata project_code")

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

        structured_rows = self._structured_table_rows()
        if not structured_rows:
            return rows

        project_code = str(self._load_snapshot_metadata().get("project_code") or "").strip().upper()
        if not project_code:
            project_code = self._extract_project_code(self._load_json_payload()).strip().upper()
        if project_code:
            for row in structured_rows:
                value, _ = lookup_by_alias(row, self._aliases("project_code"))
                if str(value or "").strip().upper() == project_code:
                    rows.update({label: value for label, value in row.items() if label and value})
                    return rows

        for row in structured_rows:
            for label, value in row.items():
                if label and value:
                    rows[label] = value
        return rows

    def _load_json_payload(self) -> dict[str, Any]:
        for script_id in ("deal_detail",):
            script = self.soup.find("script", id=script_id)
            parsed = self._parse_json_node(script)
            if parsed:
                return self._annotate_payload(parsed)

        if self.JSON_TEXTAREA_ID:
            textarea = self.soup.find("textarea", id=self.JSON_TEXTAREA_ID)
            parsed = self._parse_json_node(textarea)
            if parsed:
                return self._annotate_payload(parsed)

        for script_id in ("deal-json", "deal-data"):
            script = self.soup.find("script", id=script_id)
            parsed = self._parse_json_node(script)
            if parsed:
                return self._annotate_payload(parsed)

        parsed = self._load_textarea_source_payload()
        if parsed:
            return parsed

        for script in self.soup.find_all("script"):
            script_type = str(script.get("type") or "").strip().lower()
            if "json" not in script_type:
                continue
            parsed = self._parse_json_node(script)
            if parsed:
                return self._annotate_payload(parsed)
        return {}


__all__ = ["DealCBEXParser"]
