#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北京产权交易所解析器
"""

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .base import ParserOutput, WebPageParser


class BeijingStandardParser(WebPageParser):
    """北京产权交易所解析器"""
    
    # 使用中文分隔符（北交所风格）
    _use_chinese_separator = True
    
    def _build_standard_payload(self) -> Dict[str, Any]:
        return self.build_standard_payload_from_data(
            {
                "project_code": "项目编号",
                "project_name": "项目名称",
                "exchange": "交易所",
                "source_type": "类型",
                "seller": ("融资方", "转让方"),
                "group_name": "隶属集团",
                "industry": "所属行业",
                "region": "所在地区",
                "contact": "经办人",
                "agency": "受托机构",
                "price": ("融资金额", "挂牌价格"),
                "start_date": ("披露开始日期", "预披露开始日期", "挂牌开始日期"),
                "end_date": ("披露截止日期", "预披露截止日期", "挂牌截止日期"),
                "profit": ("近一年净利润", "近一年净利润（万）"),
                "asset_total": ("总资产", "总资产（万）"),
                "share_ratio": "持股比例",
                "listing_times": "挂牌次数",
                "remark": "备注",
            }
        )

    def parse(self) -> ParserOutput:
        """解析北交所网页"""
        # 尝试从JSON数据中提取
        json_data = self.extract_json_data()
        
        if json_data:
            self._parse_from_json(json_data)
        else:
            # 从HTML表格提取
            self._parse_from_html()
        
        self.data['交易所'] = '北交所'
        return self.build_parser_output(standard_payload=self._build_standard_payload())
    
    def extract_json_data(self) -> Optional[Dict]:
        """从textarea中提取JSON数据"""
        textarea = self.soup.find('textarea', id='jsonobj')
        if textarea:
            try:
                return json.loads(textarea.text)
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() not in {"none", "null"}:
                return text
        return ""

    @staticmethod
    def _to_float_or_text(value: str) -> Any:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    @staticmethod
    def _is_zero_like(value: Any) -> bool:
        text = str(value or "").strip().replace(",", "")
        if not text:
            return False
        try:
            return abs(float(text)) < 1e-12
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _is_physical_code(project_code: Any) -> bool:
        code = str(project_code or "").strip().upper()
        return code.startswith(("GR", "GF", "TA", "TR"))

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

    def _pick_physical_group_name(self, seller: Dict[str, Any], seller_name: str) -> str:
        """
        北交所实物资产“隶属集团”取值：
        - 审批单位含“清算组”时，去后缀后保留；
        - 审批单位与转让方同体时留空；
        - 审批单位属于平台公司（如“国际控股/资产经营管理”）时优先取 hqname；
        - 其他场景优先审批单位。
        """
        authorize = self._first_non_empty(
            seller.get("authorizeunit", ""),
            seller.get("authorizeunitzw", ""),
        )
        hq_name = str(seller.get("hqname", "") or "").strip()
        seller_text = str(seller_name or "").strip()

        if authorize:
            auth = str(authorize).strip()
            if auth.endswith("清算组"):
                stripped = re.sub(r"清算组$", "", auth).strip()
                if stripped:
                    return stripped

            if self._is_same_org_identity(auth, seller_text):
                return ""

            if hq_name and ("国际控股" in auth or "资产经营管理" in auth):
                return hq_name
            return auth

        return hq_name

    def _extract_bj_physical_asset_category(
        self,
        json_data: Optional[Dict[str, Any]] = None,
        project: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Strict category detection for CBEX physical assets.
        Priority:
        1) downloader sidecar list_source,
        2) json lists (houselist/trafficlist/mecequlist),
        3) controlled type/typewz mapping,
        4) scoped detail-area text fallback.
        Any multi-category conflict returns empty.
        """
        source_mapped = self._extract_bj_physical_category_from_sidecar()
        if source_mapped:
            return source_mapped

        categories = self._extract_bj_physical_categories_from_json_lists(json_data)
        if len(categories) == 1:
            return next(iter(categories))
        if len(categories) > 1:
            return ""

        categories = self._extract_bj_physical_categories_from_type(project)
        if len(categories) == 1:
            return next(iter(categories))
        if len(categories) > 1:
            return ""

        detail_area = self.soup.select_one("#project-table-box") or self.soup
        page_text = detail_area.get_text(" ", strip=True)
        if not page_text:
            return ""

        house = "房屋土地"
        transport = "交通运输工具"
        equip = "设备"

        guess = set()
        if (
            house in page_text
            or "房屋建筑物" in page_text
            or "不动产" in page_text
        ):
            guess.add(house)
        if transport in page_text or "交通运输设备" in page_text:
            guess.add(transport)
        if "机器设备" in page_text or "设备资产" in page_text:
            guess.add(equip)
        return next(iter(guess)) if len(guess) == 1 else ""

    def _extract_bj_physical_category_from_sidecar(self) -> str:
        source_file = self.source_file
        if not source_file:
            return ""
        path = Path(source_file)
        sidecar = path.with_suffix(".json")
        if not sidecar.exists():
            return ""
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return ""

        row = payload.get("row") if isinstance(payload, dict) else {}
        list_source = str((row or {}).get("list_source") or "").strip()

        house = "房屋土地"
        transport = "交通运输工具"
        equip = "设备"

        mapping = {
            "house": house,
            "transport": transport,
            "equipment": equip,
            house: house,
            transport: transport,
            equip: equip,
        }
        return mapping.get(list_source, "")

    @staticmethod
    def _extract_bj_physical_categories_from_json_lists(
        json_data: Optional[Dict[str, Any]]
    ) -> set[str]:
        if not isinstance(json_data, dict):
            return set()

        house = "房屋土地"
        transport = "交通运输工具"
        equip = "设备"

        categories: set[str] = set()
        list_map = (
            ("houselist", "utrmcemshouse", house),
            ("trafficlist", "utrmcemstraffic", transport),
            ("mecequlist", "utrmcemsmecequ", equip),
        )
        for parent_key, child_key, label in list_map:
            parent = json_data.get(parent_key)
            if not isinstance(parent, dict):
                continue
            items = parent.get(child_key)
            if isinstance(items, list) and any(isinstance(it, dict) for it in items):
                categories.add(label)
        return categories

    @staticmethod
    def _extract_bj_physical_categories_from_type(
        project: Optional[Dict[str, Any]]
    ) -> set[str]:
        if not isinstance(project, dict):
            return set()

        house = "房屋土地"
        transport = "交通运输工具"
        equip = "设备"

        code_map = {
            "A18001": house,
            "A18003": transport,
            "A18004": equip,
        }
        raw_codes = set()
        for key in ("type", "typewz"):
            raw = str(project.get(key) or "")
            if not raw:
                continue
            raw_codes.update(re.findall(r"A\d{5}", raw))
        return {code_map[c] for c in raw_codes if c in code_map}

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
    def _normalize_capital_ratio_for_output(raw_ratio: Any) -> str:
        """增资扩股持股比例标准化：保留百分比格式，其余文案保留。"""
        text = str(raw_ratio or "").strip().replace("％", "%")
        text = re.sub(r"\s+", "", text)
        if not text:
            return ""
        # 如果是纯数字，添加百分号
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return f"{text}%"
        # 如果已经有百分号，直接返回
        if re.fullmatch(r"\d+(?:\.\d+)?%", text):
            return text
        # 其他格式（如"不超过35%"）保持原样
        return text

    @staticmethod
    def _pick_best_region_value(*values: Any) -> str:
        """从多个候选地区值中选择信息量更高的那个。"""
        candidates: List[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            text = re.sub(r"\s+", "", text)
            if text:
                candidates.append(text)
        if not candidates:
            return ""

        def _score(region: str) -> tuple[int, int]:
            # 优先避免“市辖区”这类泛化值，再按长度选择更具体地区。
            penalty = 1 if "市辖区" in region else 0
            return (-penalty, len(region))

        return sorted(candidates, key=_score, reverse=True)[0]

    def _extract_region_from_html(self) -> str:
        """Extract region text from visible HTML blocks."""
        node = self.soup.select_one(".zone.deal")
        if node:
            text = re.sub(r"\s+", "", node.get_text(" ", strip=True))
            if text:
                return self.clean_region(text)

        szsf = self.soup.find("td", class_="SZSFMC")
        if szsf:
            text = re.sub(r"\s+", "", szsf.get_text(" ", strip=True))
            if text:
                return self.clean_region(text)

        text = self.soup.get_text(" ", strip=True)
        match = re.search(r"所在地区\s*[:：]?\s*([^\s，,;；|]{2,30})", text)
        if match:
            return self.clean_region(match.group(1).strip())
        return ""

    def _extract_ratio_from_detail_html(self, detail_html: str) -> str:
        """Extract ratio text from CP detail.BDJBXX HTML block."""
        raw = html.unescape(str(detail_html or ""))
        if not raw:
            return ""

        soup = BeautifulSoup(raw, "html.parser")
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            for idx, label in enumerate(texts):
                normalized = re.sub(r"\s+", "", str(label or ""))
                if "拟募集资金金额对应持股比例" in normalized and idx + 1 < len(texts):
                    value = str(texts[idx + 1] or "").strip()
                    if value:
                        return value

        page_text = soup.get_text(" ", strip=True)
        match = re.search(
            r"(?:对应)?持股比例[^0-9]{0,20}((?:不超过|不低于|不高于|不小于|高于|低于)?\d+(?:\.\d+)?%)",
            page_text,
        )
        if match:
            return match.group(1)
        return ""

    def _extract_finance_from_detail_html(self, detail_html: str) -> Dict[str, str]:
        """从综合招商 detail.BDJBXX 中提取年度审计财务指标。"""
        result: Dict[str, str] = {"profit": "", "asset": ""}
        raw = html.unescape(str(detail_html or ""))
        if not raw:
            return result

        soup = BeautifulSoup(raw, "html.parser")

        def _compact(value: str) -> str:
            return re.sub(r"\s+", "", str(value or ""))

        def _unit_from_text(text: str, default_unit: str) -> str:
            compact = _compact(text)
            if "万元" in compact:
                return "wan"
            if "元" in compact and "万元" not in compact:
                return "yuan"
            return default_unit

        def _numbers(text: str) -> List[str]:
            return re.findall(r"-?\d[\d,]*(?:\.\d+)?", _compact(text))

        def _to_wan(num_text: str, unit: str) -> str:
            try:
                value = float(str(num_text).replace(",", ""))
            except (TypeError, ValueError):
                return ""
            if unit == "yuan":
                value = value / 10000.0
            return f"{value:.10f}".rstrip("0").rstrip(".")

        default_unit = _unit_from_text(soup.get_text(" ", strip=True), "wan")
        annual_mode = False
        expect_profit_values = False

        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if not cells:
                    continue

                row_text = " ".join(cells)
                row_compact = _compact(row_text)
                row_unit = _unit_from_text(row_text, default_unit)

                if "货币单位" in row_compact:
                    default_unit = _unit_from_text(row_text, default_unit)
                    continue

                if "最近一期财务报表数据" in row_compact or "最近一期财务数据" in row_compact:
                    annual_mode = False
                    expect_profit_values = False
                    continue

                if "以下数据出自" in row_compact and ("年度审计报告" in row_compact or "年度财务报表" in row_compact):
                    annual_mode = True
                    expect_profit_values = False
                    default_unit = _unit_from_text(row_text, default_unit)
                    continue

                if (
                    "主要财务指标" in row_compact
                    and ("年度财务报告数据" in row_compact or "年度审计报告" in row_compact or "年度财务报表" in row_compact)
                ):
                    annual_mode = True
                    expect_profit_values = False
                    default_unit = _unit_from_text(row_text, default_unit)
                    continue

                if row_compact.startswith("年度项目"):
                    annual_mode = True

                if not annual_mode:
                    continue

                label = _compact(cells[0]) if cells else ""

                if "营业收入" in row_compact and "净利润" in row_compact:
                    expect_profit_values = True
                    continue

                row_numbers: List[str] = []
                value_cells = cells[1:] if len(cells) > 1 else cells
                for cell_text in value_cells:
                    row_numbers.extend(_numbers(cell_text))

                if expect_profit_values and row_numbers and not result["profit"]:
                    result["profit"] = _to_wan(row_numbers[-1], row_unit)
                    expect_profit_values = False

                if not result["profit"] and "净利润" in label and row_numbers:
                    result["profit"] = _to_wan(row_numbers[-1], row_unit)

                if not result["asset"] and ("资产总额" in label or label == "总资产" or label.startswith("总资产")) and row_numbers:
                    result["asset"] = _to_wan(row_numbers[0], row_unit)

                if result["profit"] and result["asset"]:
                    return result

        return result

    @staticmethod
    def _split_seller_names(raw: str) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        parts = re.split(r"[、，,；;]|(?:\s+及\s+)|(?:\s+和\s+)|(?:\s+与\s+)|(?:\s*及\s*)|(?:\s*和\s*)", text)
        return [p.strip() for p in parts if p and p.strip()]

    @staticmethod
    def _append_unique_remark(data: Dict[str, Any], note: str) -> None:
        text = str(note or "").strip()
        if not text:
            return
        existing = str(data.get("备注") or "").strip()
        if not existing or existing in {"nan", "None"}:
            data["备注"] = text
            return
        if text in existing:
            return
        data["备注"] = f"{existing}；{text}"

    def _apply_sellers_to_fields(self, sellers: List[Dict[str, str]]) -> None:
        """应用转让方信息到数据字段（使用基类的通用多转让方处理）"""
        self.process_multi_sellers(sellers, self.data)

    def _extract_finance_from_list(self, finance_list: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {"profit": "", "asset": "", "_target_locked": False}
        if not isinstance(finance_list, dict):
            return result

        rows = finance_list.get("utrzcemsfinance", [])
        if not isinstance(rows, list):
            return result

        def _parse_period_and_scope(row: Dict[str, Any]) -> tuple[int, int]:
            year_text = str(row.get("audityear") or "").strip()
            m_year = re.search(r"(20\d{2})", year_text)
            if m_year:
                return (2, int(m_year.group(1)) * 10000 + 1231)

            date_text = self._first_non_empty(
                row.get("reportdate"),
                row.get("stmtdate"),
                row.get("auditdate"),
            )
            m_date = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", str(date_text or ""))
            if m_date:
                y = int(m_date.group(1))
                m = int(m_date.group(2))
                d = int(m_date.group(3))
                return (1, y * 10000 + m * 100 + d)

            return (0, 0)

        candidates: List[tuple[int, int, Dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            scope, period = _parse_period_and_scope(row)
            candidates.append((scope, period, row))

        if not candidates:
            return result

        # 口径：有年度取最新年度；无年度再取最近一期。
        scopes = [scope for scope, _, _ in candidates]
        if 2 in scopes:
            target_scope = 2
        elif 1 in scopes:
            target_scope = 1
        else:
            target_scope = max(scopes)
        target_period = max(period for scope, period, _ in candidates if scope == target_scope)

        target_rows = [row for scope, period, row in candidates if scope == target_scope and period == target_period]
        if not target_rows:
            return result

        target_row = target_rows[0]
        result["_target_locked"] = True
        result["profit"] = self._first_non_empty(
            target_row.get("auditnetprofit"),
            target_row.get("stmtnetprofit"),
            target_row.get("auditgrossprofit"),
            target_row.get("stmtgrossprofit"),
            target_row.get("auditprofit"),
            target_row.get("stmtprofit"),
        )
        result["asset"] = self._first_non_empty(
            target_row.get("auditasset"),
            target_row.get("stmtasset"),
        )
        return result
    
    def _parse_from_json(self, json_data: Dict) -> None:
        """从JSON数据解析"""
        # 支持六种格式：
        # 1. 股权转让: utrgcemsproject, utrgcemsobject, utrgcemsseller
        # 2. 实物资产: utrmcemsproject, utrmcemshouse, utrmcemsseller
        # 3. 增资扩股: utrzcemsproject, utrzcemsobject, utrzcemsshareholder
        # 4. 预披露: utrgcemspreproject(股权转让)/utrzcemspreproject(增资扩股)
        # 5. 天传检测格式: object (北交互联格式)
        # 6. 综合招商格式: object.detail (CP开头的项目编号)
        project = (json_data.get('utrgcemsproject', {}) or 
                   json_data.get('utrmcemsproject', {}) or 
                   json_data.get('utrzcemsproject', {}) or
                   json_data.get('utrgcemspreproject', {}) or
                   json_data.get('utrzcemspreproject', {}))
        obj = (json_data.get('utrgcemsobject', {}) or
               json_data.get('utrmcemshouse', {}) or
               json_data.get('utrzcemsobject', {}) or
               json_data.get('utrgcemspreobject', {}) or
               json_data.get('utrgcemspreproject', {}) or
               json_data.get('utrzcemspreproject', {}))
        
        # 天传检测格式或综合招商格式：使用"object"字段
        if not project and 'object' in json_data:
            obj_data = json_data.get('object', {})
            if isinstance(obj_data, dict):
                # 检查是否是综合招商格式（有detail字段）
                if 'detail' in obj_data:
                    project = obj_data.get('detail', {})
                    obj = obj_data.get('detail', {})
                else:
                    project = obj_data
                    obj = obj_data
        if isinstance(obj, list) and len(obj) > 0:
            obj = obj[0]  # 实物资产的house是列表
        detail_data = {}
        if isinstance(obj, dict):
            detail_data = obj.get('detail', {}) if isinstance(obj.get('detail', {}), dict) else {}
            # CP 综合招商里 obj 已经被提升为 detail 本体，字段直接在当前层。
            if not detail_data and ('ZRF' in obj or 'LXR' in obj):
                detail_data = obj
        
        # 处理sellerlist（股权转让/实物资产）或holderlist（增资扩股）
        seller = {}
        sellers_for_output: List[Dict[str, str]] = []
        
        # 1. 尝试从sellerlist获取（股权转让/实物资产）
        sellerlist = json_data.get('sellerlist', {})
        if isinstance(sellerlist, dict):
            sellers = sellerlist.get('utrgcemsseller', []) or sellerlist.get('utrmcemsseller', [])
            if isinstance(sellers, list) and len(sellers) > 0:
                seller = sellers[0]
                for s in sellers:
                    if not isinstance(s, dict):
                        continue
                    seller_name = self._first_non_empty(
                        s.get('sellername', ''),
                        s.get('holdername', ''),
                        s.get('name', ''),
                    )
                    if not seller_name:
                        continue
                    seller_ratio = self._first_non_empty(
                        s.get('transferratio', ''),
                        s.get('transferpercent', ''),
                        s.get('sellpercent', ''),
                        s.get('holdingratio', ''),
                        s.get('shareholdingratio', ''),
                        s.get('ratio', ''),
                    )
                    sellers_for_output.append(
                        {
                            "name": seller_name,
                            "ratio": seller_ratio,
                            "hq": self._first_non_empty(s.get('hqname', ''), s.get('groupname', '')),
                        }
                    )
        
        # 2. 尝试从holderlist获取（增资扩股）
        if not seller:
            holderlist = json_data.get('holderlist', {})
            if isinstance(holderlist, dict):
                holders = holderlist.get('utrzcemsshareholder', [])
                if isinstance(holders, list) and len(holders) > 0:
                    holder = holders[0]
                    # 增资扩股的"转让方"实际上是原股东
                    seller = {
                        'sellername': holder.get('holdername', ''),
                        'hqname': obj.get('hqname', '')  # 隶属集团在obj中
                    }
        
        # 项目编号：标准格式用projectcode，天传检测格式用XMBH
        project_code = project.get('projectcode', '')
        if not project_code:
            project_code = project.get('XMBH', '')
        is_pre_project = str(project_code or "").strip().endswith("-0")
        self.data['项目编号'] = project_code
        
        # 项目名称：标准格式用object，天传检测格式用XMMC
        project_name = project.get('object', '')
        if not project_name:
            project_name = project.get('XMMC', '')
        self.data['项目名称'] = project_name
        
        # 如果JSON中没有项目编号和名称，从HTML中提取（天传检测格式）
        if not self.data.get('项目编号'):
            xmbh = self.soup.find('td', class_='XMBH')
            if xmbh:
                self.data['项目编号'] = xmbh.get_text(strip=True)
        
        if not self.data.get('项目名称'):
            xmmc = self.soup.find('td', class_='XMMC')
            if xmmc:
                self.data['项目名称'] = xmmc.get_text(strip=True)
        
        # 挂牌价格：股权转让/实物资产用objectprice，增资扩股用objectpricestart/objectpriceend
        price = project.get('objectprice', '')
        if not price:
            # 增资扩股项目使用价格区间 - 尝试从HTML中提取原始显示文本
            price = self._extract_price_from_html()
            if not price:
                # 如果HTML中没有，fallback到JSON数据
                price_start = project.get('objectpricestart', '')
                price_end = project.get('objectpriceend', '')
                if price_start and price_end and price_end != '999999999':
                    price = f"{price_start}-{price_end}"
                elif price_start and price_start != '0':
                    price = f"不低于{price_start}"
                elif price_end and price_end != '999999999':
                    price = f"不超过{price_end}"
        
        # 如果还没有价格，尝试从HTML表格中提取（综合招商格式）
        if not price:
            money_td = self.soup.find('td', id='money')
            if money_td:
                price = money_td.get_text(strip=True)
        
        self.data['挂牌价格'] = self.clean_price(price)

        is_capital_project = (
            'utrzcemsproject' in json_data
            or 'utrzcemspreproject' in json_data
            or bool(project.get('financepercentstart') or project.get('financepercentend'))
            or '增资' in str(self.data.get('项目名称') or '')
        )
        if is_capital_project and str(price or "").strip():
            self.data['融资金额'] = str(price).strip()
        
        # 增资扩股：提取持股比例
        if is_capital_project:
            # 尝试从HTML中提取原始显示文本
            ratio = self._extract_ratio_from_html()
            if ratio and "%" not in ratio and not any(token in ratio for token in ("不超过", "不低于", "不高于", "不小于")):
                ratio = ""
            if not ratio and isinstance(detail_data, dict) and detail_data.get('BDJBXX'):
                ratio = self._extract_ratio_from_detail_html(detail_data.get('BDJBXX', ''))
            if not ratio:
                # 如果HTML中没有，fallback到JSON数据
                finance_start = project.get('financepercentstart', '')
                finance_end = project.get('financepercentend', '')
                if finance_start and finance_end and finance_end != '999999999':
                    ratio = f"{finance_start}%-{finance_end}%"
                elif finance_end and finance_end != '999999999':
                    ratio = f"不超过{finance_end}%"
                elif finance_start and finance_start != '0':
                    ratio = f"不低于{finance_start}%"
            ratio = self._normalize_capital_ratio_for_output(ratio)
            if ratio:
                self.data['持股比例'] = ratio
            
            # 从holderlist中提取原股东持股比例（第一个股东）
            holderlist = json_data.get('holderlist', {})
            if isinstance(holderlist, dict):
                holders = holderlist.get('utrzcemsshareholder', [])
                if isinstance(holders, list) and len(holders) > 0:
                    # 找到持股比例最高的股东
                    max_ratio = 0
                    main_holder = None
                    for holder in holders:
                        ratio_str = holder.get('holdingratio', '0')
                        try:
                            ratio_num = float(ratio_str)
                            if ratio_num > max_ratio:
                                max_ratio = ratio_num
                                main_holder = holder
                        except (ValueError, TypeError):
                            continue
                    if main_holder:
                        # 如果有多个股东，记录主要股东及其持股比例
                        if len(holders) > 1:
                            holder_infos = []
                            for h in holders:
                                name = h.get('holdername', '')
                                ratio = h.get('holdingratio', '')
                                if name and ratio:
                                    holder_infos.append(f"{name}({ratio}%)")
                            if holder_infos:
                                self.data['转让方'] = ' '.join(holder_infos)
                        else:
                            self.data['转让方'] = main_holder.get('holdername', '')
        
        # 所属行业：股权转让用 industrycodezw；
        # 北交所实物资产改为“资产类别”口径，仅在可识别时赋值，否则留空。
        industry = obj.get('industrycodezw', '')
        if not industry:
            if self._is_physical_code(project_code):
                industry = self._extract_bj_physical_asset_category(json_data, project)
            else:
                industry = project.get('propertyzw', '')
        self.data['所属行业'] = industry
        
        # 挂牌日期：标准格式用publishdate/expiredate，综合招商格式用PLKSRQ/PLJSRQ
        start_date = self._first_non_empty(
            project.get('publishdate', ''),
            project.get('pubstartdate', ''),
            project.get('pubdate', ''),
            project.get('PLKSRQ', ''),
        )
        if len(start_date) < 8:
            start_date = ''
        self.data['挂牌开始日期'] = self.clean_date(start_date)
        
        end_date = self._first_non_empty(
            project.get('expiredate', ''),
            project.get('pubenddate', ''),
            project.get('PLJSRQ', ''),
        )
        self.data['挂牌截止日期'] = self.clean_date(end_date)
        
        # 如果还没有日期，尝试从HTML表格中提取（综合招商格式）
        if not self.data['挂牌开始日期'] or not self.data['挂牌截止日期']:
            pldate_td = self.soup.find('td', id='pldate')
            if pldate_td:
                date_text = pldate_td.get_text(strip=True)
                # 格式: "2025年12月31日 至 2026年02月25日"
                match = re.search(r'(\d{4})年(\d{2})月(\d{2})日\s*至\s*(\d{4})年(\d{2})月(\d{2})日', date_text)
                if match:
                    self.data['挂牌开始日期'] = self.clean_date(f"{match.group(1)}/{match.group(2)}/{match.group(3)}")
                    self.data['挂牌截止日期'] = self.clean_date(f"{match.group(4)}/{match.group(5)}/{match.group(6)}")
        
        # 所在地区
        province = str(obj.get('zoneprovincezw', '') or '').strip()
        city = str(obj.get('zonecityzw', '') or '').strip()
        county = str(obj.get('zonecountyzw', '') or '').strip()
        if city in {'市辖区', '县'}:
            city_part = '' if province == '北京市' else city
        else:
            city_part = city
        region_city = self.clean_region(f"{province}{city_part}")
        include_county = bool(county) and (city not in {'市辖区', '县'} or province == '北京市')
        region_county = self.clean_region(f"{province}{city_part}{county}") if include_county else ''

        if is_capital_project:
            zone = self._first_non_empty(
                region_county,
                region_city,
                project.get('zonezw', ''),
                self._extract_region_from_html(),
                obj.get('zoneother', ''),
            )
        else:
            zone = self._first_non_empty(
                region_city,
                project.get('zonezw', ''),
                self._extract_region_from_html(),
                region_county,
            )
        self.data['所在地区'] = zone
        
        # 转让方/融资方：优先使用sellerlist，如果没有则尝试detail.ZRF（综合招商格式）
        seller_name = seller.get('sellername', '')
        if not seller_name:
            # 尝试从detail中获取转让方/融资方（ZRF字段）
            seller_name = detail_data.get('ZRF', '')
        
        # 如果还没有，尝试从HTML表格中提取（综合招商格式）
        if not seller_name:
            zrf_td = self.soup.find('td', class_='ZRF_NMZRF')
            if zrf_td:
                seller_name = zrf_td.get_text(strip=True)
        
        if sellers_for_output:
            # 优先使用结构化 sellerlist；如缺比例，再参考 HTML 明细补齐。
            detailed_sellers = self._extract_seller_ratios_from_html()
            ratio_map = {
                str(item.get("name") or "").strip(): self._normalize_ratio(item.get("ratio"))
                for item in detailed_sellers
                if str(item.get("name") or "").strip()
            }
            hq_map = {
                str(item.get("name") or "").strip(): str(item.get("hq") or "").strip()
                for item in detailed_sellers
                if str(item.get("name") or "").strip() and str(item.get("hq") or "").strip()
            }
            for item in sellers_for_output:
                name = str(item.get("name") or "").strip()
                if name and not self._normalize_ratio(item.get("ratio")) and ratio_map.get(name):
                    item["ratio"] = ratio_map[name]
                if name and not str(item.get("hq") or "").strip() and hq_map.get(name):
                    item["hq"] = hq_map[name]
            self._apply_sellers_to_fields(sellers_for_output)
        elif seller_name and len(self._split_seller_names(seller_name)) > 1:
            # 多转让方但 JSON 无结构化列表，尝试从 HTML 提取各方比例与集团。
            detailed_sellers = self._extract_seller_ratios_from_html()
            if detailed_sellers:
                self._apply_sellers_to_fields(detailed_sellers)
            else:
                self.data['转让方'] = seller_name
        else:
            self.data['转让方'] = seller_name
        
        # 隶属集团：实物资产按审批单位/hqname综合规则处理；其他场景优先 hqname。
        if self._is_physical_code(project_code):
            hq_name = self._pick_physical_group_name(seller, seller_name)
        else:
            hq_name = seller.get('hqname', '')
        if not hq_name:
            # 尝试从HTML中提取隶属集团
            hq_td = self.soup.find('td', class_='HQNAME')
            if hq_td:
                hq_name = hq_td.get_text(strip=True)
        # 非实物资产场景：若“隶属集团”与转让方同体，按口径留空。
        if (not self._is_physical_code(project_code)) and self._is_same_org_identity(hq_name, seller_name):
            hq_name = ""
        self.data['隶属集团'] = hq_name
        
        # 经办人：优先使用procontact，如果没有则使用memberbroker，最后尝试detail.LXR或HTML
        contact = project.get('procontact', '')
        if not contact:
            contact = project.get('memberbroker', '')
        if not contact:
            # 尝试从detail中获取联系人（LXR字段）
            contact = detail_data.get('LXR', '')
        
        # 如果还没有经办人，尝试从HTML表格中提取（综合招商格式）
        if not contact:
            jyjg_td = self.soup.find('td', id='jyjg')
            if jyjg_td:
                jyjg_text = jyjg_td.get_text(strip=True)
                # 格式: "项目负责人：蒋经理 / 联系电话：010-66295650 | 部门负责人：马经理 / 联系电话：010-66295546"
                match = re.search(r'项目负责人：([^/]+)', jyjg_text)
                if match:
                    contact = match.group(1).strip()
        
        self.data['经办人'] = contact
        
        self.data['受托机构'] = project.get('memberorg', '')
        
        finance_metrics = self._extract_finance_from_list(json_data.get('financelist', {}))
        finance_target_locked = bool(finance_metrics.get("_target_locked"))
        detail_finance = {"profit": "", "asset": ""}
        if isinstance(detail_data, dict) and detail_data.get('BDJBXX'):
            detail_finance = self._extract_finance_from_detail_html(detail_data.get('BDJBXX', ''))

        obj_profit = self._first_non_empty(
            obj.get('auditnetprofit'),
            obj.get('stmtnetprofit'),
            obj.get('auditgrossprofit'),
            obj.get('stmtgrossprofit'),
            obj.get('auditprofit'),
            obj.get('stmtprofit'),
        )
        obj_asset = self._first_non_empty(
            obj.get('auditasset'),
            obj.get('stmtasset'),
            obj.get('assettotal'),
            obj.get('totalasset'),
            obj.get('totasset'),
            obj.get('totassets'),
        )

        finance_profit = finance_metrics.get('profit', '')
        finance_asset = finance_metrics.get('asset', '')
        if self._is_zero_like(finance_profit) and obj_profit and not self._is_zero_like(obj_profit):
            finance_profit = ''
        if self._is_zero_like(finance_asset) and obj_asset and not self._is_zero_like(obj_asset):
            finance_asset = ''

        if is_capital_project:
            profit_candidates = [
                finance_profit,
                detail_finance.get('profit', ''),
            ]
            asset_candidates = [
                finance_asset,
                detail_finance.get('asset', ''),
            ]
            # 已锁定目标年度/期间时，不跨数据源回填旧年值。
            if not (finance_target_locked and is_pre_project):
                profit_candidates.append(obj_profit)
                asset_candidates.append(obj_asset)
            profit_value = self._first_non_empty(*profit_candidates)
            asset_value = self._first_non_empty(*asset_candidates)
        else:
            profit_value = self._first_non_empty(
                obj_profit,
                finance_profit,
                detail_finance.get('profit', ''),
            )
            asset_value = self._first_non_empty(
                obj_asset,
                finance_asset,
                detail_finance.get('asset', ''),
            )

        if profit_value:
            self.data['近一年净利润'] = self._to_float_or_text(profit_value)
        if asset_value:
            self.data['总资产'] = self._to_float_or_text(asset_value)

        # 如果JSON中没有净利润数据，尝试从HTML中提取（天传检测格式）
        if '近一年净利润' not in self.data and not (finance_target_locked and is_pre_project):
            self._extract_profit_from_tianchuan()
        
        # 如果JSON中没有其他关键字段，从HTML中提取（天传检测格式）
        self._extract_other_fields_from_tianchuan()

        # 北交所实物资产“资产类别”仅按可识别三类输出，避免被其它 fallback 覆盖。
        if self._is_physical_code(project_code):
            self.data['所属行业'] = self._extract_bj_physical_asset_category(json_data, project)
    
    def _extract_other_fields_from_tianchuan(self) -> None:
        """从天传检测格式或综合招商格式提取其他字段"""
        text = self.soup.get_text()
        
        # 所属行业 - 优先从HTML表格中提取
        if not self.data.get('所属行业'):
            sshy = self.soup.find('td', class_='SSHYMC')
            if sshy:
                self.data['所属行业'] = sshy.get_text(strip=True)
            else:
                pattern = r'所属行业\s*([\u4e00-\u9fa5]+)'
                match = re.search(pattern, text)
                if match:
                    self.data['所属行业'] = match.group(1)
        
        # 所在地区 - 优先从HTML表格中提取
        if not self.data.get('所在地区'):
            szsf = self.soup.find('td', class_='SZSFMC')
            if szsf:
                self.data['所在地区'] = szsf.get_text(strip=True)
            else:
                pattern = r'所在地区\s*([\u4e00-\u9fa5]+)'
                match = re.search(pattern, text)
                if match:
                    self.data['所在地区'] = match.group(1)
        
        # 挂牌日期
        if not self.data.get('挂牌开始日期') or not self.data.get('挂牌截止日期'):
            pattern = r'(\d{4}年\d{2}月\d{2}日)\s*至\s*(\d{4}年\d{2}月\d{2}日)'
            match = re.search(pattern, text)
            if match:
                self.data['挂牌开始日期'] = self.clean_date(match.group(1))
                self.data['挂牌截止日期'] = self.clean_date(match.group(2))
        
        # 挂牌价格
        if not self.data.get('挂牌价格'):
            pattern = r'转让底价\s*(\d+\.?\d*)\s*万元'
            match = re.search(pattern, text)
            if match:
                self.data['挂牌价格'] = match.group(1)
        
        # 转让方
        if not self.data.get('转让方'):
            pattern = r'转让方名称\s*([\u4e00-\u9fa5（）]+)'
            match = re.search(pattern, text)
            if match:
                transferor = match.group(1)
                # 清理后缀
                transferor = transferor.replace('基本情况', '').strip()
                self.data['转让方'] = transferor
        
        # 受托机构
        if not self.data.get('受托机构'):
            pattern = r'委托会员\s*机构名称：\s*([\u4e00-\u9fa5]+)'
            match = re.search(pattern, text)
            if match:
                self.data['受托机构'] = match.group(1)
    
    def _parse_from_html(self) -> None:
        """从HTML表格提取数据"""
        # 项目编号 - 尝试多种方式提取
        # 1. 从class为projectcode的td中提取（标准格式）
        project_code = self.soup.find('td', class_='projectcode')
        if project_code:
            self.data['项目编号'] = project_code.get_text(strip=True)
        
        # 2. 从class为XMBH的td中提取（天传检测格式）
        if '项目编号' not in self.data:
            xmbh = self.soup.find('td', class_='XMBH')
            if xmbh:
                self.data['项目编号'] = xmbh.get_text(strip=True)
        
        # 3. 从class为bd_detail_num的p标签中提取（北交互联格式）
        if '项目编号' not in self.data:
            detail_num = self.soup.find('p', class_='bd_detail_num')
            if detail_num:
                text = detail_num.get_text(strip=True)
                match = re.search(r'(?:G3|G6|GR|GA|Q3|Q6|QR)\d{4}BJ\d+(?:-\d+)?', text)
                if match:
                    self.data['项目编号'] = match.group(0)
        
        # 4. 从JavaScript变量中提取（北交互联实物资产格式）
        if '项目编号' not in self.data:
            html_content = self.soup.get_text()
            match = re.search(r"xmbh:\s*['\"]([^'\"]+)['\"]", html_content)
            if match:
                self.data['项目编号'] = match.group(1)
        
        # 项目名称 - 尝试多种方式提取
        # 1. 从class为object的td中提取（标准格式）
        project_name = self.soup.find('td', class_='object')
        if project_name:
            self.data['项目名称'] = project_name.get_text(strip=True)
        
        # 2. 从class为XMMC的td中提取（天传检测格式）
        if '项目名称' not in self.data:
            xmmc = self.soup.find('td', class_='XMMC')
            if xmmc:
                self.data['项目名称'] = xmmc.get_text(strip=True)
        
        # 3. 从title标签提取（北交互联格式）
        if '项目名称' not in self.data:
            title = self.soup.find('title')
            if title:
                title_text = title.get_text(strip=True)
                # 去掉"北交互联-"前缀
                if title_text.startswith('北交互联-'):
                    self.data['项目名称'] = title_text[5:]
                elif title_text.startswith('北京产权交易所-'):
                    self.data['项目名称'] = title_text[7:]
                else:
                    self.data['项目名称'] = title_text
        
        # 转让底价
        price = self.soup.find('td', class_='objectprice')
        if price:
            self.data['挂牌价格'] = self.clean_price(price.get_text(strip=True))
        
        # 从天传检测格式提取净利润
        self._extract_profit_from_tianchuan()
        
        # 从北交互联实物资产格式提取数据
        self._extract_from_otc_format()
        
        # 从表格中提取其他字段
        tables = self.soup.find_all('table')
        sellers_info: List[Dict[str, str]] = []  # 收集多个转让方及其比例/集团
        current_seller = None  # 当前正在处理的转让方
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                for i, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    
                    # 挂牌日期
                    if '挂牌起始日期' in text and i + 1 < len(cells):
                        self.data['挂牌开始日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    elif '挂牌截止日期' in text and i + 1 < len(cells):
                        self.data['挂牌截止日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    # 所属行业
                    elif '所属行业' in text and i + 1 < len(cells):
                        self.data['所属行业'] = cells[i + 1].get_text(strip=True)
                    # 所在地区
                    elif '所在地区' in text and i + 1 < len(cells):
                        self.data['所在地区'] = cells[i + 1].get_text(strip=True)
                    # 转让方（处理多转让方情况）
                    elif '转让方名称' in text and i + 1 < len(cells):
                        seller_name = cells[i + 1].get_text(strip=True)
                        if '转让方' not in self.data:
                            self.data['转让方'] = seller_name
                        current_seller = {'name': seller_name}
                        sellers_info.append(current_seller)
                    # 拟转让比例
                    elif '拟转让产(股)权比例' in text and i + 1 < len(cells) and current_seller:
                        ratio = self._normalize_ratio(cells[i + 1].get_text(strip=True))
                        current_seller['ratio'] = ratio
                    # 隶属集团
                    elif '国家出资企业' in text or '所属集团或主管部门名称' in text:
                        if i + 1 < len(cells):
                            hq_name = cells[i + 1].get_text(strip=True)
                            self.data['隶属集团'] = hq_name
                            if current_seller:
                                current_seller['hq'] = hq_name
                    # 经办人
                    elif '项目负责人' in text and i + 1 < len(cells):
                        self.data['经办人'] = cells[i + 1].get_text(strip=True)
        
        # 处理多转让方情况（统一规则：多转让方拼接比例，并按需写入集团备注）
        if len(sellers_info) > 1:
            self._apply_sellers_to_fields(sellers_info)
    
    def _extract_from_otc_format(self) -> None:
        """从北交互联实物资产格式提取数据"""
        html_content = str(self.soup)
        text = self.soup.get_text()
        
        # 1. 提取起始价（挂牌价格）
        if '挂牌价格' not in self.data or not self.data['挂牌价格']:
            # 从zxjg_id元素中提取
            match = re.search(r'<span[^>]*id=["\']zxjg_id["\'][^>]*>([\d,\.]+)</span>', html_content)
            if match:
                self.data['挂牌价格'] = match.group(1).replace(',', '')
            else:
                # 尝试从文本中提取
                match = re.search(r'起始价[^\d]*(\d[\d,]+\.?\d*)\s*元', text)
                if match:
                    self.data['挂牌价格'] = match.group(1).replace(',', '')
        
        # 2. 提取挂牌日期
        if '挂牌开始日期' not in self.data or not self.data['挂牌开始日期']:
            match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}:\d{2}:\d{2})', text)
            if match:
                year, month, day, time = match.groups()
                self.data['挂牌开始日期'] = self.clean_date(f"{year}/{month}/{day}")
        
        # 3. 提取挂牌截止日期（限时报价开始时间）
        if '挂牌截止日期' not in self.data or not self.data['挂牌截止日期']:
            # 查找第二个日期（限时报价开始时间）
            matches = re.findall(r'(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}:\d{2}:\d{2})', text)
            if len(matches) >= 2:
                year, month, day, time = matches[1]
                self.data['挂牌截止日期'] = self.clean_date(f"{year}/{month}/{day}")
        
        # 4. 提取所在地区（从资产描述中）
        if '所在地区' not in self.data or not self.data['所在地区']:
            match = re.search(r'(?:存放于|位于|目前存放于)\s*([^<\n]{2,20}?)(?:市|区|县)', text)
            if match:
                self.data['所在地区'] = match.group(1).strip()
        
        # 5. 提取转让方（多种方式）
        if '转让方' not in self.data or not self.data['转让方']:
            # 方式1：从标准表格中提取（完整版格式）
            seller_cell = self.soup.find('td', class_='sellername')
            if seller_cell:
                self.data['转让方'] = seller_cell.get_text(strip=True)
            else:
                # 方式2：从表格标签中提取
                match = re.search(r'转让方名称[\s\S]*?<td[^>]*>([^<]+)</td>', html_content)
                if match:
                    self.data['转让方'] = match.group(1).strip()
                else:
                    # 方式3：从与转让相关的其他条件中
                    match = re.search(r'与转让方联系|转让方统一组织', text)
                    if match:
                        # 转让方信息通常在项目描述或联系人中
                        match = re.search(r'联系人[：:]\s*([^，,\s]+)', text)
                        if match:
                            self.data['转让方'] = match.group(1).strip()
        
        # 6. 提取隶属集团（从表格中提取）
        if '隶属集团' not in self.data or not self.data['隶属集团']:
            match = re.search(r'所属集团或主管部门名称[\s\S]*?<td[^>]*>([^<]+)</td>', html_content)
            if match:
                self.data['隶属集团'] = match.group(1).strip()
        
        # 8. 提取经办人（从联系人信息）
        if '经办人' not in self.data or not self.data['经办人']:
            match = re.search(r'联系人[：:]\s*([^，,\s]+)', text)
            if match:
                self.data['经办人'] = match.group(1).strip()
        
        # 7. 提取受托机构（北交所）
        # 注意：不再自动假设北交互联项目的受托机构就是北京产权交易所
        # 受托机构应该只在JSON数据中明确指定时才填充(memberorg字段)
        # 或在页面中包含"委托会员 机构名称"等明确信息时提取
    
    def _extract_profit_from_tianchuan(self) -> None:
        """从天传检测格式提取净利润"""
        # 方法1：从天传检测的特殊格式提取（所有数字连在一起）
        # 格式：营业收入营业利润净利润560.6259.4160.46
        self._extract_profit_from_text()
        
        # 方法2：如果文本提取失败，尝试从宽表中直接提取
        if '近一年净利润' not in self.data:
            self._extract_profit_from_wide_table()
    
    def _extract_profit_from_text(self) -> None:
        """从文本中提取净利润（处理天传检测格式）"""
        # 获取所有文本
        text = self.soup.get_text()
        
        # 查找"主要财务指标"和"审计报告"之间的内容
        # 格式：营业收入营业利润净利润560.6259.4160.46
        # 使用非贪婪匹配，并且要求数字格式为xxx.xx
        pattern = r'主要财务指标.*?以下数据出自(\d{4})年度审计报告.*?营业收入营业利润净利润(\d+\.\d{2})(\d+\.\d{2})(\d+\.\d{2})'
        match = re.search(pattern, text, re.DOTALL)
        
        if match:
            year = match.group(1)
            net_profit = match.group(4)  # 净利润
            
            try:
                self.data['近一年净利润'] = float(net_profit)
                self.data['净利润年度'] = year
            except (ValueError, TypeError):
                pass

    def _extract_profit_from_wide_table(self) -> None:
        """从宽表中提取净利润（处理北交所93列并行布局）"""
        try:
            tables = self.soup.find_all('table')
            for table in tables:
                rows = []
                for tr in table.find_all('tr'):
                    cells = tr.find_all(['th', 'td'])
                    row = [self._clean_cell_text(c.get_text(' ', strip=True)) for c in cells]
                    if any(row):
                        rows.append(row)
                
                if not rows:
                    continue
                
                # 检查表格宽度
                max_cols = max((len(row) for row in rows), default=0)
                if max_cols < 20:
                    # 窄表：使用三行模式提取
                    self._extract_from_narrow_table(rows)
                else:
                    # 宽表：使用列索引方法避免邻近表干扰
                    self._extract_from_wide_table_structure(rows)
        except Exception:
            pass

    @staticmethod
    def _clean_cell_text(text: str) -> str:
        """清理单元格文本"""
        return re.sub(r'\s+', ' ', str(text).strip())

    def _extract_from_narrow_table(self, rows: List[List[str]]) -> None:
        """从窄表提取财务数据（三行模式）"""
        for ridx in range(len(rows) - 2):
            header_row = rows[ridx + 1]
            value_row = rows[ridx + 2]
            
            # 查找期间信息
            period = 0
            for search_idx in range(max(0, ridx - 2), ridx + 1):
                for cell in rows[search_idx]:
                    if '近一年' in cell or '年度' in cell:
                        match = re.search(r'(\d{4})', cell)
                        if match:
                            period = int(match.group(1))
            
            # 在header行中查找指标标签
            for cidx, label in enumerate(header_row):
                if '净利润' in label and cidx < len(value_row):
                    val_text = value_row[cidx].strip()
                    if val_text and val_text not in {'-', '--'}:
                        try:
                            num = float(val_text.replace(',', ''))
                            self.data['近一年净利润'] = num
                            if period > 0:
                                self.data['净利润年度'] = str(period)
                            return
                        except (ValueError, TypeError):
                            pass

    def _extract_from_wide_table_structure(self, rows: List[List[str]]) -> None:
        """从宽表提取财务数据（处理并行布局）"""
        # 在宽表中，多个财务报表可能并行排列
        # 策略：通过列号追踪法避免误提邻近表
        
        # 第一步：找期间标记列
        period_cols = {}
        for row in rows[:3]:  # 只看前3行
            for cidx, cell in enumerate(row):
                if '年度' in cell or '日期' in cell or re.search(r'\d{4}', cell):
                    match = re.search(r'(\d{4})', cell)
                    if match:
                        period = int(match.group(1))
                        period_cols[cidx] = period
        
        # 第二步：为每个期间列找对应的财务指标列
        for metric_col_idx in range(len(rows[0]) if rows else 0):
            if metric_col_idx not in period_cols:
                continue
            
            period = period_cols[metric_col_idx]
            
            # 在同一"财务表块"中查找净利润列
            # 向右扫描最多5列找净利润标签
            for offset in range(min(5, len(rows[0]) - metric_col_idx)):
                search_col = metric_col_idx + offset
                
                # 在该列的标签行（第2-4行）查找"净利润"
                for label_row_idx in range(1, min(4, len(rows))):
                    if search_col < len(rows[label_row_idx]):
                        label = rows[label_row_idx][search_col].lower()
                        if '净利润' in label or 'net' in label:
                            # 在该列的数据行查找数值
                            for data_row_idx in range(label_row_idx + 1, len(rows)):
                                if search_col < len(rows[data_row_idx]):
                                    val_text = rows[data_row_idx][search_col].strip()
                                    if val_text and val_text not in {'-', '--'}:
                                        try:
                                            num = float(val_text.replace(',', '').replace(' ', ''))
                                            if '近一年净利润' not in self.data:
                                                self.data['近一年净利润'] = num
                                                self.data['净利润年度'] = str(period)
                                            return
                                        except (ValueError, TypeError):
                                            pass
    
    def _extract_seller_ratios_from_html(self) -> list:
        """从HTML表格中提取转让方名称和持股比例"""
        seller_map: Dict[str, Dict[str, str]] = {}
        try:
            # 查找所有表格
            tables = self.soup.find_all('table')
            for table in tables:
                text = table.get_text()
                # 检查是否包含转让方信息（必须有“转让方+拟转让比例”）
                if '转让方' in text and '拟转让比例' in text:
                    # 提取转让方信息
                    rows = table.find_all('tr')
                    current_seller = None
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            cell_texts = [cell.get_text(strip=True) for cell in cells]
                            row_text = ' '.join(cell_texts)
                            label = re.sub(r"\s+", "", cell_texts[0]) if cell_texts else ""

                            # 查找转让方名称（支持“转让方名称”“转让方一名称”“转让方1名称”等）
                            if re.fullmatch(r'转让方(?:[一二三四五六七八九十\d]+)?名称', label):
                                value = cell_texts[1].strip() if len(cell_texts) > 1 else ""
                                if value:
                                    name = value.split('基本情况')[0].strip()
                                    if name:
                                        current_seller = seller_map.get(name) or {'name': name, 'ratio': '', 'hq': ''}
                                        seller_map[name] = current_seller

                            # 拟转让比例（允许整数/小数百分比）
                            if current_seller and '拟转让比例' in row_text:
                                current_seller['ratio'] = self._normalize_ratio(row_text)

                            # 绑定隶属集团
                            if current_seller and ('国家出资企业' in row_text or '所属集团或主管部门名称' in row_text):
                                current_seller['hq'] = cell_texts[1].strip() if len(cell_texts) > 1 else ''
            
            # 去重并返回
            seen = set()
            unique_sellers = []
            for s in seller_map.values():
                seller_name = str(s.get('name') or '').strip()
                if seller_name and seller_name not in seen:
                    seen.add(seller_name)
                    unique_sellers.append(s)
            return unique_sellers
        except Exception:
            return []
    
    def _extract_price_from_html(self) -> str:
        """从HTML中提取拟募集资金金额的原始显示文本"""
        try:
            # 查找拟募集资金金额的td元素
            td = self.soup.find('td', id='objectpricebottom')
            if td:
                return td.get_text(strip=True)
            # 备选：查找带有objectpriceend属性的td
            td = self.soup.find('td', attrs={'objectpriceend': True})
            if td:
                return td.get_text(strip=True)
        except Exception:
            pass
        return ''
    
    def _extract_ratio_from_html(self) -> str:
        """从HTML中提取持股比例的原始显示文本"""
        try:
            # 查找持股比例的td元素（id为financebottom）
            td = self.soup.find('td', id='financebottom')
            if td:
                return td.get_text(strip=True)
            # 备选：查找带有financepercentend属性的td
            td = self.soup.find('td', attrs={'financepercentend': True})
            if td:
                return td.get_text(strip=True)

            # CP/综合招商页面：从“拟募集资金金额对应持股比例”标签所在行提取
            for tr in self.soup.find_all('tr'):
                cells = tr.find_all(['td', 'th'])
                if len(cells) < 2:
                    continue
                texts = [c.get_text(" ", strip=True) for c in cells]
                for idx, label in enumerate(texts):
                    normalized = re.sub(r"\s+", "", str(label or ""))
                    if "拟募集资金金额对应持股比例" in normalized and idx + 1 < len(texts):
                        value = str(texts[idx + 1] or "").strip()
                        if value:
                            return value

            page_text = self.soup.get_text(" ", strip=True)
            match = re.search(
                r"(?:对应)?持股比例[^0-9]{0,20}((?:不超过|不低于|不高于|不小于|高于|低于)?\d+(?:\.\d+)?%)",
                page_text,
            )
            if match:
                return match.group(1)
        except Exception:
            pass
        return ''

