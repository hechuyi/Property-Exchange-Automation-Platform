#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上海联合产权交易所标准页面解析器。"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .base import WebPageParser


class ShanghaiStandardParser(WebPageParser):
    """解析上海联交所标准 HTML 页面。"""

    _PROJECT_CODE_PATTERN = re.compile(r"\b([A-Z0-9]{2}\d{4}SH\d+(?:-\d+)?)\b", re.IGNORECASE)
    _DETAIL_ROUTE_PATTERN = re.compile(r"projectdetail/([a-z]+)", re.IGNORECASE)
    _METRIC_PLACEHOLDERS = {"", "-", "--", "—", "暂无"}
    _ROUTE_PROJECT_TYPES = {
        "jymhchanquan": "股权转让",
        "jymhzichan": "实物资产",
        "jymhzengzi": "增资扩股",
        "jymhchanquanyu": "预披露",
        "jymhzichanyu": "预披露",
        "jymhzengziyu": "预披露",
    }

    def parse(self) -> Dict[str, Any]:
        self._is_capital_project = False
        self._detail_route = self._extract_detail_route()
        self._parse_project_header()
        self._parse_new_layout_overview()
        self._parse_overview_blocks()
        self._parse_detail_rows()
        self._parse_tables()
        self._finalize_capital_fields()
        self._post_process_group_field()
        self._infer_project_type()
        self.data["交易所"] = "上交所"
        return self.data

    def _parse_project_header(self) -> None:
        if self._detail_route in {"jymhzengzi", "jymhzengziyu"}:
            self._is_capital_project = True

        detail_top = self.soup.select_one(".project-detail-top")
        if detail_top:
            self._capture_project_code(detail_top.get_text(" ", strip=True))

            detail_label = detail_top.select_one(".detail-top-label")
            if detail_label and "企业增资" in detail_label.get_text(" ", strip=True):
                self._is_capital_project = True

            title_block = detail_top.select_one(".title")
            if title_block:
                name = title_block.get_text(" ", strip=True)
                if name:
                    self.data["项目名称"] = name
                    if "增资项目" in name:
                        self._is_capital_project = True

        code_block = self.soup.find("div", class_="project_code")
        if code_block:
            self._capture_project_code(code_block.get_text(" ", strip=True))
        if not self.data.get("项目编号"):
            page_text = self.soup.get_text(" ", strip=True)
            match = re.search(r"项目编号[:：]\s*([A-Z0-9]{2}\d{4}SH\d+(?:-\d+)?)", page_text, re.IGNORECASE)
            if match:
                self._set_project_code(match.group(1))

        name_block = self.soup.find("div", class_="project_xmmc")
        if name_block and not self.data.get("项目名称"):
            name = name_block.get_text(strip=True)
            if name:
                self.data["项目名称"] = name
                if "增资项目" in name:
                    self._is_capital_project = True

    def _parse_new_layout_overview(self) -> None:
        price_box = self.soup.select_one(".project-price-box")
        if price_box:
            label = price_box.select_one(".project-price-name")
            value = price_box.select_one(".project-price-num")
            self._assign_by_label(
                self._node_text(label),
                self._node_text(value),
            )

        for date_item in self.soup.select(".xmjs-infor-box .infor-date li"):
            label = self._node_text(date_item.select_one(".text"))
            value_nodes = date_item.select(".numb")
            value = self._node_text(value_nodes[-1]) if value_nodes else ""
            self._assign_by_label(label, value)

        for info_block in self.soup.select(".xmjs-infor-box .label-infor"):
            label = self._node_text(info_block.select_one(".name"))
            value = self._node_text(info_block.select_one(".cont"))
            self._assign_by_label(label, value)

    def _parse_detail_rows(self) -> None:
        preferred_contact = ""
        fallback_contact = ""

        for row in self.soup.select(".detail-info tr"):
            header = self._node_text(row.find("th"))
            span_texts = [
                span.get_text(" ", strip=True)
                for span in row.select("td .text")
                if span.get_text(" ", strip=True)
            ]
            row_text = row.get_text(" ", strip=True)

            if "受托机构" in header:
                agency_name = self._extract_agency_name(span_texts)
                if agency_name and not self.data.get("受托机构"):
                    self.data["受托机构"] = agency_name
                if not fallback_contact:
                    fallback_contact = (
                        self._extract_labeled_contact(row_text, "联系人", "业务负责人", "业务联系人")
                        or self.extract_contact_person(" ".join(span_texts[1:]) or row_text)
                        or ""
                    )

            project_manager = self._extract_labeled_contact(row_text, "项目负责人")
            if project_manager:
                preferred_contact = project_manager
                continue

            project_contact = self._extract_labeled_contact(row_text, "项目联系人")
            if project_contact and not fallback_contact:
                fallback_contact = project_contact
                continue

            if header and not fallback_contact:
                generic_contact = self.extract_contact_person(" ".join(span_texts[1:]) or row_text)
                if generic_contact:
                    fallback_contact = generic_contact

        if preferred_contact and not self.data.get("经办人"):
            self.data["经办人"] = preferred_contact
        if fallback_contact and not self.data.get("经办人"):
            self.data["经办人"] = fallback_contact

    def _parse_overview_blocks(self) -> None:
        for block in self.soup.find_all("div", class_="project_content"):
            spans = [s.get_text(" ", strip=True) for s in block.find_all("span")]
            pairs = self._build_label_value_pairs(spans)
            for label, value in pairs:
                self._assign_by_label(label, value)

        contacts = self.soup.find_all("div", class_="project_contact")
        for contact in contacts:
            text = contact.get_text(" ", strip=True)
            if not text:
                continue

            if "受托机构" in text and not self.data.get("受托机构"):
                m = re.search(
                    r"(?:受托机构名称|受托机构)[:：]\s*(.+?)(?=\s*(?:受托机构联系人|联系人|联系电话|电话|$))",
                    text,
                )
                if m:
                    org_name = self._clean_org_name(m.group(1))
                    if org_name:
                        self.data["受托机构"] = org_name

            business_match = re.search(
                r"(?:业务负责人|业务联系人)[:：]\s*([\u4e00-\u9fa5A-Za-z·]{2,20})",
                text,
            )
            if business_match:
                self.data["经办人"] = business_match.group(1).strip()
                continue

            if not self.data.get("经办人"):
                contact_match = re.search(
                    r"(?:受托机构联系人|联系人)[:：]\s*([\u4e00-\u9fa5A-Za-z·]{2,20})",
                    text,
                )
                if contact_match:
                    self.data["经办人"] = contact_match.group(1).strip()

    @staticmethod
    def _build_label_value_pairs(spans: List[str]) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        idx = 0
        while idx < len(spans):
            label = (spans[idx] or "").strip()
            if not label:
                idx += 1
                continue
            if label.endswith("：") or label.endswith(":"):
                if idx + 1 < len(spans):
                    pairs.append((label.rstrip("：:").strip(), spans[idx + 1].strip()))
                    idx += 2
                    continue
            idx += 1
        return pairs

    def _assign_by_label(self, label: str, value: str) -> None:
        if not value:
            return

        if ("项目名称" in label or "标的企业名称" in label) and not self.data.get("项目名称"):
            self.data["项目名称"] = value
            return

        if "拟募集资金总额" in label and not self.data.get("融资金额"):
            self.data["融资金额"] = value

        if ("拟募集资金总额" in label or "转让底价" in label or "挂牌价格" in label) and not self.data.get("挂牌价格"):
            self.data["挂牌价格"] = self.clean_price(value)
            return

        if (
            "所属行业" in label
            or "标的所属行业" in label
            or "\u8d44\u4ea7\u7c7b\u522b" in label
        ) and not self.data.get("所属行业"):
            self.data["所属行业"] = value
            return

        if (
            "所在地区" in label
            or "标的所在地" in label
            or "标的所在地区" in label
        ) and not self.data.get("所在地区"):
            self.data["所在地区"] = re.sub(r"\s+", " ", self.clean_region(value) or "").strip()
            return

        if (
            "信息披露起始日期" in label
            or "挂牌开始日期" in label
            or "公告开始" in label
            or "披露开始" in label
        ) and not self.data.get("挂牌开始日期"):
            self.data["挂牌开始日期"] = self.clean_date(value)
            return

        if (
            "信息披露期满日期" in label
            or "挂牌截止日期" in label
            or "公告截止" in label
            or "披露结束" in label
        ) and not self.data.get("挂牌截止日期"):
            self.data["挂牌截止日期"] = self.clean_date(value)
            return

    def _parse_tables(self) -> None:
        sellers: List[Dict[str, str]] = []
        current_seller_idx: Optional[int] = None
        finance_years: Dict[int, int] = {}
        pending_year: Optional[int] = None
        asset_categories: List[str] = []

        for table in self.soup.find_all("table"):
            rows = table.find_all("tr")
            is_asset_notice_table = self._is_asset_notice_table(table)
            self._extract_shareholder_structure_ratios(rows, sellers)

            for row in rows:
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(" ", strip=True).replace("\xa0", " ") for c in cells]
                normalized = [self._normalize_label(t) for t in texts]
                if not any(normalized):
                    continue
                if is_asset_notice_table and self._collect_asset_notice_category(cells, texts, asset_categories):
                    continue

                for idx, label in enumerate(normalized):
                    if not label:
                        continue

                    value = texts[idx + 1].strip() if idx + 1 < len(texts) else ""
                    self._assign_by_label(label, value)

                    if "拟募集资金对应持股比例" in label and value and not self.data.get("持股比例"):
                        compact = re.sub(r"\s+", "", str(value).replace("％", "%"))
                        if "%" in compact and any(token in compact for token in ("外部投资人", "投资人", "原股东")):
                            self.data["持股比例"] = compact
                        else:
                            self.data["持股比例"] = self._normalize_ratio(value) or value
                        continue

                    if self._is_capital_project and self._is_company_name_label(normalized, idx):
                        company_name = self._extract_company_name_from_row(texts, idx)
                        if company_name and not self.data.get("融资方"):
                            self.data["融资方"] = company_name
                        continue

                    if "转让方名称" in label and value:
                        if not self._is_capital_project:
                            seller_idx = self._upsert_seller(sellers, value)
                            current_seller_idx = seller_idx if seller_idx >= 0 else None
                            if "转让方" not in self.data:
                                self.data["转让方"] = value
                        continue

                    if not self._is_capital_project and self._is_transfer_ratio_label(label):
                        if current_seller_idx is None:
                            continue
                        ratio = self._normalize_ratio(value)
                        if ratio:
                            sellers[current_seller_idx]["ratio"] = ratio
                        continue

                    if "经济类型" in label and value and not self.data.get("类型"):
                        # 类型标准化由后处理统一完成。
                        continue

                    if (
                        "所属集团或主管部门名称" in label
                        or "国家出资企业或主管部门名称" in label
                        or "主管部门名称" in label
                    ) and value:
                        if not self.data.get("隶属集团"):
                            self.data["隶属集团"] = value
                        if current_seller_idx is not None and not sellers[current_seller_idx].get("hq"):
                            sellers[current_seller_idx]["hq"] = value
                        continue

                    if "国资监管机构" in label and value and not self.data.get("类型"):
                        # 类型标准化放到后处理阶段，不在解析器内推断。
                        continue

                    if label == "年度" and value:
                        year = self._extract_year(value)
                        if year is not None:
                            pending_year = year
                        continue

                    if "净利润" == label:
                        self._set_metric_field("近一年净利润", value)
                        continue

                    if "总资产" in label or "资产总额" == label or "资产总计" == label:
                        self._set_metric_field("总资产", value)
                        continue

                # Year columns in financial matrix tables.
                if any("项目/年度" in t for t in normalized):
                    finance_years = {}
                    for col in range(1, len(texts)):
                        year = self._extract_year(texts[col])
                        if year is not None:
                            finance_years[col] = year
                    continue

                if finance_years:
                    row_label = normalized[0]
                    if row_label in ("净利润", "资产总额", "资产总计"):
                        best_col = self._pick_latest_year_col(finance_years, len(texts))
                        if best_col is not None:
                            value = texts[best_col].strip()
                            if row_label == "净利润":
                                self._set_metric_field("近一年净利润", value)
                            if row_label in ("资产总额", "资产总计"):
                                self._set_metric_field("总资产", value)

                # Fallback for older layout where 年度 row is followed by metrics.
                if pending_year is not None:
                    if normalized and normalized[0] in ("净利润", "资产总额", "资产总计") and len(texts) > 1:
                        if normalized[0] == "净利润":
                            self._set_metric_field("近一年净利润", texts[1].strip())
                        if normalized[0] in ("资产总额", "资产总计"):
                            self._set_metric_field("总资产", texts[1].strip())

        if sellers and not self._is_capital_project:
            self.process_multi_sellers(sellers, self.data)
        if asset_categories and not self.data.get("所属行业"):
            self.data["所属行业"] = "、".join(asset_categories)

    def _finalize_capital_fields(self) -> None:
        if not self._is_capital_project:
            return
        if not self.data.get("融资方"):
            project_name = (self.data.get("项目名称") or "").strip()
            if project_name.endswith("增资项目"):
                self.data["融资方"] = project_name[: -len("增资项目")].strip()
        # 增资扩股不应把股东结构写入转让方。
        self.data.pop("转让方", None)

    def _infer_project_type(self) -> None:
        if self.data.get("项目类型"):
            return

        project_code = str(self.data.get("项目编号") or "").strip().upper()
        project_name = str(self.data.get("项目名称") or "").strip()
        detail_route = str(getattr(self, "_detail_route", "") or "").strip().lower()
        detail_labels = self._extract_detail_top_labels()
        breadcrumb_labels = self._extract_breadcrumb_labels()

        if project_code.endswith("-0") or self._contains_marker(detail_labels, "预披露") or self._contains_marker(breadcrumb_labels, "预披露"):
            self.data["项目类型"] = "预披露"
            return
        route_project_type = self._ROUTE_PROJECT_TYPES.get(detail_route)
        if route_project_type:
            self.data["项目类型"] = route_project_type
            return
        if self._contains_marker(detail_labels, "企业增资") or self._contains_marker(detail_labels, "增资扩股"):
            self.data["项目类型"] = "增资扩股"
            return
        if self._contains_marker(detail_labels, "实物资产") or self._contains_marker(breadcrumb_labels, "资产正式披露"):
            self.data["项目类型"] = "实物资产"
            return
        if self._contains_marker(detail_labels, "股权转让") or self._contains_marker(breadcrumb_labels, "项目详情-产权"):
            self.data["项目类型"] = "股权转让"
            return
        if self._is_capital_project or project_name.endswith("增资项目"):
            self.data["项目类型"] = "增资扩股"
            return
        if project_code.startswith("GR") or "部分资产" in project_name or "报废设备" in project_name:
            self.data["项目类型"] = "实物资产"
            return
        if (
            re.match(r"^[GQP]320", project_code)
            or "股权" in project_name
            or "股份" in project_name
            or "财产份额" in project_name
        ):
            self.data["项目类型"] = "股权转让"

    def _extract_detail_route(self) -> str:
        html_text = str(self.soup)
        match = self._DETAIL_ROUTE_PATTERN.search(html_text)
        if not match:
            return ""
        return str(match.group(1) or "").strip().lower()

    def _extract_detail_top_labels(self) -> List[str]:
        labels: List[str] = []
        for node in self.soup.select(".project-detail-top .detail-top-label i, .project-detail-top .detail-top-label span"):
            text = self._node_text(node)
            if text:
                labels.append(text)
        return labels

    def _extract_breadcrumb_labels(self) -> List[str]:
        labels: List[str] = []
        for node in self.soup.select(".el-breadcrumb__inner"):
            text = self._node_text(node)
            if text:
                labels.append(text)
        return labels

    @staticmethod
    def _contains_marker(values: List[str], marker: str) -> bool:
        normalized_marker = str(marker or "").strip()
        if not normalized_marker:
            return False
        return any(normalized_marker in str(value or "").strip() for value in values)

    def _post_process_group_field(self) -> None:
        """隶属集团与转让方同体（含“清算组”尾缀）时留空。"""
        seller_name = self.data.get("转让方", "")
        group_name = self.data.get("隶属集团", "")
        if self._is_same_org_identity(group_name, seller_name):
            self.data["隶属集团"] = ""

    def _is_asset_notice_table(self, table: Any) -> bool:
        if self._is_capital_project:
            return False
        section = table.find_parent("li")
        if section is None:
            return False
        title_node = section.find(class_="tab-title")
        title = self._node_text(title_node)
        return "资产公告信息" in title

    def _collect_asset_notice_category(
        self,
        cells: List[Any],
        texts: List[str],
        asset_categories: List[str],
    ) -> bool:
        if len(cells) != 1 or len(texts) != 1:
            return False
        cell = cells[0]
        raw_text = str(texts[0] or "").strip()
        if not raw_text:
            return False
        try:
            colspan = int(str(cell.get("colspan") or "1").strip())
        except ValueError:
            colspan = 1
        if colspan < 2:
            return False
        label = self._normalize_label(raw_text)
        if not label or label in {"资产公告信息", "资产描述", "标的基本信息"}:
            return False
        if raw_text not in asset_categories:
            asset_categories.append(raw_text)
        return True

    @staticmethod
    def _is_company_name_label(normalized: List[str], idx: int) -> bool:
        label = normalized[idx]
        if label != "名称":
            return False
        first = normalized[0] if normalized else ""
        return first in ("基本情况", "基情况", "企业基本情况") or idx == 0

    @staticmethod
    def _extract_company_name_from_row(texts: List[str], idx: int) -> str:
        if idx + 1 < len(texts):
            value = (texts[idx + 1] or "").strip()
            if value:
                return value
        if idx + 2 < len(texts):
            value = (texts[idx + 2] or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _upsert_seller(sellers: List[Dict[str, str]], seller_name: str) -> int:
        name = str(seller_name or "").strip()
        if not name:
            return -1
        for i, seller in enumerate(sellers):
            if ShanghaiStandardParser._is_same_org_identity(seller.get("name"), name):
                return i
        sellers.append({"name": name})
        return len(sellers) - 1

    def _extract_shareholder_structure_ratios(
        self,
        rows: List[Any],
        sellers: List[Dict[str, str]],
    ) -> None:
        if self._is_capital_project or not rows:
            return

        header_cells = rows[0].find_all(["td", "th"])
        header_labels = [self._normalize_label(cell.get_text(" ", strip=True)) for cell in header_cells]
        name_col = next((idx for idx, label in enumerate(header_labels) if "股东名称" in label), None)
        ratio_col = next(
            (
                idx
                for idx, label in enumerate(header_labels)
                if "持股比例" in label and "股东名称" not in label
            ),
            None,
        )
        has_shareholder_name = name_col is not None
        has_share_ratio = ratio_col is not None
        if not (has_shareholder_name and has_share_ratio):
            return

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            texts = [cell.get_text(" ", strip=True).replace("\xa0", " ") for cell in cells]
            if name_col is None or ratio_col is None or max(name_col, ratio_col) >= len(texts):
                continue
            seller_name = str(texts[name_col] or "").strip()
            ratio = self._normalize_ratio(texts[ratio_col])
            if not seller_name or not ratio:
                continue
            seller_idx = next(
                (
                    idx
                    for idx, seller in enumerate(sellers)
                    if self._is_same_org_identity(seller.get("name"), seller_name)
                ),
                -1,
            )
            if seller_idx < 0:
                continue
            seller = sellers[seller_idx]
            if not seller.get("ratio"):
                seller["ratio"] = ratio
                seller["ratio_inferred"] = True

    @staticmethod
    def _is_transfer_ratio_label(label: str) -> bool:
        text = str(label or "")
        if "拟转让" in text and "比例" in text:
            return True
        return text in {"拟转让比例", "持有产股权比例", "比例%", "比例％"}

    @staticmethod
    def _normalize_label(text: str) -> str:
        s = (text or "").strip()
        s = re.sub(r"\s+", "", s)
        s = s.replace("︵", "").replace("︶", "").replace("（", "").replace("）", "")
        s = s.replace(":", "").replace("：", "")
        return s

    @staticmethod
    def _normalize_ratio(raw_ratio: Any) -> str:
        ratio = str(raw_ratio or "").strip().replace("％", "%")
        ratio = re.sub(r"\s+", "", ratio)
        if not ratio:
            return ""
        if re.fullmatch(r"\d+(?:\.\d+)?%?", ratio):
            return ratio if ratio.endswith("%") else f"{ratio}%"
        match = re.search(r"(?:不超过|不低于|小于等于|大于等于|小于|大于)?\d+(?:\.\d+)?%", ratio)
        if match:
            return match.group(0)
        match = re.search(r"(\d+(?:\.\d+)?)", ratio)
        if match:
            return f"{match.group(1)}%"
        return ""

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        match = re.search(r"(20\d{2})", text or "")
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _pick_latest_year_col(year_map: Dict[int, int], row_len: int) -> Optional[int]:
        valid = {col: year for col, year in year_map.items() if 0 <= col < row_len}
        if not valid:
            return None
        return max(valid, key=valid.get)

    @staticmethod
    def _to_float_or_text(raw: str) -> Any:
        if raw is None:
            return None
        text = str(raw).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return raw

    @classmethod
    def _is_metric_placeholder(cls, raw: Any) -> bool:
        text = str(raw or "").strip()
        return text in cls._METRIC_PLACEHOLDERS

    def _set_metric_field(self, field_name: str, raw_value: str) -> None:
        text = str(raw_value or "").strip()
        if not text or not self._is_metric_value(text):
            return
        if self._is_metric_placeholder(text):
            return
        number = self._to_float_or_text(text)
        if number in (None, ""):
            return

        current = self.data.get(field_name)
        if current in (None, "") or self._is_metric_placeholder(current):
            self.data[field_name] = number

    @staticmethod
    def _is_metric_value(raw: str) -> bool:
        text = str(raw or "").strip()
        if not text:
            return False
        if any(token in text for token in ("资产总额", "负债总额", "所有者权益", "营业收入", "利润总额", "净利润")):
            return False
        if text in ("-", "--", "—", "暂无"):
            return True
        return bool(re.search(r"^-?[\d,]+(?:\.\d+)?$", text))

    @staticmethod
    def _clean_org_name(raw_name: str) -> str:
        name = str(raw_name or "").replace("\u3000", " ")
        name = re.split(r"(?:受托机构联系人|联系人|联系电话|电话)", name, maxsplit=1)[0]
        name = re.sub(r"\s+", " ", name).strip()
        name = re.sub(r"[|,，;；、。]+$", "", name)
        return name

    @staticmethod
    def _normalize_org_identity(name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", "", text)
        text = text.replace("（", "(").replace("）", ")")
        text = re.sub(r"清算组$", "", text)
        return text

    @classmethod
    def _is_same_org_identity(cls, left: Any, right: Any) -> bool:
        left_identity = cls._normalize_org_identity(left)
        right_identity = cls._normalize_org_identity(right)
        if not left_identity or not right_identity:
            return False
        return left_identity == right_identity

    def _capture_project_code(self, text: str) -> None:
        match = self._PROJECT_CODE_PATTERN.search(text or "")
        if match:
            self._set_project_code(match.group(0))

    def _set_project_code(self, project_code: str) -> None:
        normalized = str(project_code or "").strip().upper()
        if not normalized:
            return
        self.data["项目编号"] = normalized
        if normalized.startswith(("G6", "Q6", "P6")):
            self._is_capital_project = True

    @staticmethod
    def _node_text(node: Any) -> str:
        if node is None:
            return ""
        return node.get_text(" ", strip=True)

    @staticmethod
    def _extract_labeled_contact(text: str, *labels: str) -> str:
        if not text or not labels:
            return ""
        pattern = rf"(?:{'|'.join(re.escape(label) for label in labels)})[：:\s]*([^\d\s]{{2,20}})"
        match = re.search(pattern, text)
        if not match:
            return ""
        return match.group(1).strip()

    def _extract_agency_name(self, span_texts: List[str]) -> str:
        if not span_texts:
            return ""
        candidate = str(span_texts[0] or "").strip()
        if not candidate:
            return ""
        if candidate in {"项目负责人", "项目联系人", "业务负责人", "业务联系人", "联系人"}:
            return ""
        if re.search(r"\d{3,}", candidate):
            return ""
        return self._clean_org_name(candidate)
