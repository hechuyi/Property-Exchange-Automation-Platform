#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deal parser for Chongqing exchange (CQUAE) with canonical payload keys."""

from __future__ import annotations

from .base import ParserOutput
from .deal_sse import CanonicalDealParserBase


class DealCQUAEParser(CanonicalDealParserBase):
    EXCHANGE = "重交所"
    FIELD_ALIASES = {
        "project_code": ("project_code", "projectCode", "项目编号"),
        "project_name": ("project_name", "projectName", "项目名称", "标的名称"),
        "business_type": ("business_id", "businessId", "business_type", "businessType", "项目类型", "业务类型"),
        "deal_date": ("deal_date", "dealDate", "成交日期"),
        "collection_date": ("collection_date", "collectionDate", "publish_date", "采集日期"),
        "deal_price": ("deal_price", "dealAmount", "成交金额", "成交价"),
        "valuation": ("valuation", "valuationValue", "评估值"),
        "reserve_price": ("reserve_price", "reservePrice", "转让底价", "挂牌底价"),
        "project_parties": ("project_parties", "projectParties", "partyList"),
        "transferors": ("transferors", "transferorNames"),
        "financing_party_names": ("financing_party_names", "financingPartyNames"),
        "capital_company_name": ("capital_company_name", "capital_increase_company_name", "capitalIncreaseCompanyName"),
        "investors": ("investors", "investorList"),
        "remark": ("remark", "备注"),
        "deal_method": ("deal_method", "dealMethod", "transactionMethod", "交易方式"),
        "buyer_name": ("buyer_name", "buyerName", "transfereeName", "transferee", "受让方名称", "受让方"),
        "auction_flag": ("auction_flag", "isAuction", "auctionFlag", "是否竞价"),
        "deal_status": ("deal_status", "isDeal", "dealStatus", "是否成交"),
    }

    def _load_snapshot_metadata(self) -> dict[str, object]:
        metadata = dict(super()._load_snapshot_metadata())
        metadata.pop("project_name", None)
        metadata.pop("projectName", None)
        return metadata

    def _title_project_name(self) -> str:
        title_node = self.soup.find("title")
        if title_node is None:
            return ""
        title = " ".join(title_node.get_text(" ", strip=True).split())
        for suffix in (" - 重庆产权交易网", "- 重庆产权交易网", "_重庆产权交易网"):
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()
        return title.strip()

    def parse(self) -> ParserOutput:
        output = super().parse()
        payload = dict(output.standard_payload)
        current_project_name = str(payload.get("project_name") or "").strip()
        row_project_name, _ = self._pick_text((), self._table_rows(), self._aliases("project_name"))
        title_project_name = self._title_project_name()
        preferred_project_name = row_project_name or (
            title_project_name if not current_project_name or current_project_name.lower() == "candidate" else ""
        )
        if preferred_project_name:
            payload["project_name"] = preferred_project_name
        return self.build_parser_output(standard_payload=payload)


__all__ = ["DealCQUAEParser"]
