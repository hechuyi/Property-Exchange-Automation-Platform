#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deal parser for Tianjin exchange (TPRE) with canonical payload keys."""

from __future__ import annotations

import re

from .deal_aliases import lookup_by_alias
from .deal_sse import CanonicalDealParserBase


class DealTPREParser(CanonicalDealParserBase):
    EXCHANGE = "天交所"
    RENDERED_LABEL_VALUE_SELECTORS = (
        ".field-row",
        ".detail-field",
        ".detail-item",
        ".info-row",
        ".info-item",
        ".ant-descriptions-item",
        ".el-descriptions__item",
        "p",
        "li",
    )
    RENDERED_LABEL_FIELDS = (
        "project_code",
        "project_name",
        "business_type",
        "deal_date",
        "collection_date",
        "deal_price",
        "valuation",
        "reserve_price",
    )
    _LABEL_VALUE_TEXT_RE = re.compile(r"^\s*(?P<label>[^:：\n]{2,40})\s*[:：]\s*(?P<value>.+?)\s*$")
    _LABEL_CLASS_TOKENS = ("label",)
    _VALUE_CLASS_TOKENS = ("value", "content", "text")
    FIELD_ALIASES = {
        "project_code": ("projectCode", "project_code", "项目编号"),
        "project_name": ("projectName", "project_name", "title", "项目名称"),
        "business_type": ("bizType", "businessType", "business_type", "业务类型", "项目类型"),
        "deal_date": ("contractSignTime", "dealDate", "deal_date", "成交日期", "成交时间", "合同签订时间", "合同签订日期"),
        "collection_date": ("collectionDate", "collection_date", "publishDate", "采集日期"),
        "deal_price": ("transactionPrice", "dealAmount", "deal_price", "成交金额", "成交金额（万元）", "成交价", "交易价格（万元）"),
        "valuation": ("assessmentValue", "valuationValue", "valuation", "评估值"),
        "reserve_price": ("transferBasePrice", "reservePrice", "reserve_price", "转让底价", "转让底价（万元）", "挂牌底价"),
        "project_parties": ("partyList", "project_parties", "projectParties"),
        "transferors": ("transferorNames", "transferors"),
        "financing_party_names": ("financingPartyNames", "financing_party_names"),
        "capital_company_name": ("capitalIncreaseCompanyName", "capital_company_name", "capital_increase_company_name"),
        "investors": ("investorList", "investors", "transferee_details", "transfereeDetails"),
        "remark": ("remark", "备注"),
        "deal_method": ("dealMethod", "deal_method", "transactionMethod", "交易方式"),
        "buyer_name": ("buyerName", "buyer_name", "transfereeName", "transferee", "受让方名称", "受让方"),
        "auction_flag": ("isAuction", "is_auction", "auctionFlag", "是否竞价"),
        "deal_status": ("isDeal", "dealStatus", "deal_status", "是否成交"),
    }

    @staticmethod
    def _node_text(node) -> str:
        return " ".join(str(node.get_text(" ", strip=True) or "").split())

    @staticmethod
    def _class_text(node) -> str:
        return " ".join(str(item or "").lower() for item in (node.get("class") or ()))

    @classmethod
    def _has_class_token(cls, node, tokens: tuple[str, ...]) -> bool:
        class_text = cls._class_text(node)
        return any(token in class_text for token in tokens)

    @staticmethod
    def _clean_rendered_label(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).strip(":：;；,，")

    def _supported_rendered_label(self, label: str) -> bool:
        if not label:
            return False
        marker = {label: True}
        for field_name in self.RENDERED_LABEL_FIELDS:
            if lookup_by_alias(marker, self._aliases(field_name))[1]:
                return True
        return False

    def _first_descendant_by_class_tokens(self, node, tokens: tuple[str, ...]):
        if self._has_class_token(node, tokens):
            return node
        for child in node.find_all(True):
            if self._has_class_token(child, tokens):
                return child
        return None

    def _rendered_pair_from_classed_children(self, node) -> tuple[str, str]:
        label_node = self._first_descendant_by_class_tokens(node, self._LABEL_CLASS_TOKENS)
        value_node = self._first_descendant_by_class_tokens(node, self._VALUE_CLASS_TOKENS)
        if label_node is None or value_node is None or label_node is value_node:
            return "", ""
        label = self._clean_rendered_label(self._node_text(label_node))
        value = self._node_text(value_node)
        if label and value and self._supported_rendered_label(label):
            return label, value
        return "", ""

    def _rendered_pair_from_text(self, node) -> tuple[str, str]:
        text = self._node_text(node)
        match = self._LABEL_VALUE_TEXT_RE.match(text)
        if not match:
            return "", ""
        label = self._clean_rendered_label(match.group("label"))
        value = " ".join(str(match.group("value") or "").split())
        if label and value and self._supported_rendered_label(label):
            return label, value
        return "", ""

    def _rendered_label_value_rows(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        selector = ", ".join(self.RENDERED_LABEL_VALUE_SELECTORS)
        for node in self.soup.select(selector):
            label, value = self._rendered_pair_from_classed_children(node)
            if not label:
                label, value = self._rendered_pair_from_text(node)
            if label and value and label not in rows:
                rows[label] = value
        return rows

    @staticmethod
    def _first_table_section_row(table, section_name: str):
        section = table.find(section_name)
        if section is None:
            return None
        return section.find("tr")

    def _capture_header_value_row(self, rows: dict[str, str], header_row, value_row) -> None:
        labels = [
            self._clean_rendered_label(self._node_text(cell))
            for cell in header_row.find_all(["th", "td"], recursive=False)
        ]
        values = [
            self._node_text(cell)
            for cell in value_row.find_all(["td", "th"], recursive=False)
        ]
        for label, value in zip(labels, values, strict=False):
            if label and value and label not in rows and self._supported_rendered_label(label):
                rows[label] = value

    def _thead_tbody_table_rows(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        for table in self.soup.find_all("table"):
            header_row = self._first_table_section_row(table, "thead")
            value_row = self._first_table_section_row(table, "tbody")
            if header_row is None or value_row is None:
                continue

            self._capture_header_value_row(rows, header_row, value_row)
        return rows

    def _split_thead_tbody_table_rows(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        tables = self.soup.find_all("table")
        for header_table, value_table in zip(tables, tables[1:], strict=False):
            header_row = self._first_table_section_row(header_table, "thead")
            if header_row is None or self._first_table_section_row(header_table, "tbody") is not None:
                continue

            value_row = self._first_table_section_row(value_table, "tbody")
            if value_row is None or self._first_table_section_row(value_table, "thead") is not None:
                continue

            self._capture_header_value_row(rows, header_row, value_row)
        return rows

    def _table_rows(self) -> dict[str, str]:
        rows = dict(super()._table_rows())
        for label, value in self._thead_tbody_table_rows().items():
            rows.setdefault(label, value)
        for label, value in self._split_thead_tbody_table_rows().items():
            rows.setdefault(label, value)
        for label, value in self._rendered_label_value_rows().items():
            rows.setdefault(label, value)
        return rows


__all__ = ["DealTPREParser"]
