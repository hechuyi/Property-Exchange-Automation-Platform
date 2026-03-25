#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
山东产权交易所解析器
"""

import re
from typing import Any, Dict, List, Optional

from .base import WebPageParser


class ShandongParser(WebPageParser):
    """山东产权交易所解析器"""

    PROJECT_CODE_PATTERN = re.compile(r'(?:YQCQ|SDCQ|ZBZR)\d+[A-Z]?(?:-\d+)?')
    DATE_RANGE_PATTERN = re.compile(r'(\d{4}[/-]\d{2}[/-]\d{2})\s*[至到]\s*(\d{4}[/-]\d{2}[/-]\d{2})')

    @staticmethod
    def _cell_text(cell) -> str:
        return cell.get_text(" ", strip=True)

    @staticmethod
    def _normalize_ratio(raw_ratio: Any) -> str:
        ratio = str(raw_ratio or "").strip().replace("％", "%")
        ratio = re.sub(r"\s+", "", ratio)
        if not ratio:
            return ""
        if re.fullmatch(r"\d+(?:\.\d+)?%?", ratio):
            return ratio if ratio.endswith("%") else f"{ratio}%"
        match = re.search(r"(\d+(?:\.\d+)?)", ratio)
        if match:
            return f"{match.group(1)}%"
        return ""

    @staticmethod
    def _is_seller_label(text: str) -> bool:
        norm = re.sub(r"\s+", "", str(text or ""))
        return bool(re.fullmatch(r"转让方(?:[一二三四五六七八九十\d]+)?名称", norm))

    @staticmethod
    def _is_ratio_label(text: str) -> bool:
        norm = re.sub(r"\s+", "", str(text or ""))
        if "拟转让" in norm and "比例" in norm:
            return True
        return norm in {"比例(%)", "比例（%）", "拟转让比例"}

    def _extract_inline_value(self, cell, label: str) -> str:
        """提取同一单元格中的“标签:值”结构，优先取 desc 值。"""
        desc = cell.find('span', class_='desc')
        if desc:
            value = self._cell_text(desc)
            if value:
                return value

        text = self._cell_text(cell)
        match = re.search(rf'{re.escape(label)}\s*[：:]\s*(.+)', text)
        if match:
            return match.group(1).strip()
        return ''

    def _upsert_seller(self, sellers_info: List[Dict[str, str]], seller_name: str) -> Optional[Dict[str, str]]:
        seller_name = seller_name.strip()
        if not seller_name:
            return None

        current_seller = next((s for s in sellers_info if s.get('name') == seller_name), None)
        if current_seller is None:
            current_seller = {'name': seller_name}
            sellers_info.append(current_seller)

        # 明确字段优先级：转让方名称 > 转让方（简写）
        self.data['转让方'] = seller_name
        return current_seller
    
    def parse(self) -> Dict[str, Any]:
        """解析山东交易所网页"""
        # 从表格中提取数据
        tables = self.soup.find_all('table')
        sellers_info: List[Dict[str, str]] = []  # 收集多个转让方及其比例
        current_seller = None  # 当前正在处理的转让方
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                for i, cell in enumerate(cells):
                    text = self._cell_text(cell)
                    
                    # 项目编号 - 山东交易所格式如 YQCQ260002
                    # 山东交易所的项目编号在同一单元格内，格式为"项目编号：YQCQ260002"
                    if '项目编号' in text:
                        # 从当前单元格文本中提取项目编号
                        code_text = self._extract_inline_value(cell, '项目编号') or text
                        match = self.PROJECT_CODE_PATTERN.search(code_text)
                        if match:
                            self.data['项目编号'] = match.group(0)
                    
                    # 转让底价
                    elif '转让底价' in text:
                        price_text = self._extract_inline_value(cell, '转让底价')
                        if not price_text and i + 1 < len(cells):
                            price_text = self._cell_text(cells[i + 1])
                        if price_text:
                            self.data['挂牌价格'] = self.clean_price(price_text)
                    
                    # 转让方名称（处理多转让方情况）
                    elif self._is_seller_label(text) and i + 1 < len(cells):
                        seller_name = self._cell_text(cells[i + 1])
                        current_seller = self._upsert_seller(sellers_info, seller_name)
                    
                    # 转让方（简写）
                    elif '转让方：' in text or '转让方:' in text:
                        seller_name = self._extract_inline_value(cell, '转让方')
                        if not seller_name and i + 1 < len(cells):
                            seller_name = self._cell_text(cells[i + 1])
                        if seller_name:
                            current_seller = self._upsert_seller(sellers_info, seller_name)
                    
                    # 拟转让比例
                    elif self._is_ratio_label(text) and current_seller:
                        ratio = self._extract_inline_value(cell, '拟转让比例')
                        if not ratio and i + 1 < len(cells):
                            ratio = self._cell_text(cells[i + 1])
                        ratio = self._normalize_ratio(ratio)
                        if ratio:
                            current_seller['ratio'] = ratio
                    
                    # 挂牌起止日期
                    # 山东交易所的日期在同一单元格内，格式为"挂牌起止日期：2026-02-11至2026-03-16"
                    elif '挂牌起止日期' in text:
                        # 从当前单元格文本中提取日期
                        # 匹配日期格式 2026-02-11至2026-03-16
                        date_text = self._extract_inline_value(cell, '挂牌起止日期') or text
                        match = self.DATE_RANGE_PATTERN.search(date_text)
                        if match:
                            self.data['挂牌开始日期'] = self.clean_date(match.group(1))
                            self.data['挂牌截止日期'] = self.clean_date(match.group(2))
                    
                    # 国家出资企业或主管部门名称（隶属集团）
                    elif '国家出资企业或主管部门名称' in text and i + 1 < len(cells):
                        hq = self._cell_text(cells[i + 1])
                        self.data['隶属集团'] = hq
                        # 同时将隶属集团信息添加到当前转让方（如果存在）
                        if current_seller:
                            current_seller['hq'] = hq
                    
                    # 国资监管机构
                    elif '国资监管机构' in text and i + 1 < len(cells):
                        pass

                    # 所在地区
                    elif text == '所在地区' and i + 1 < len(cells):
                        region = self._cell_text(cells[i + 1])
                        region = re.sub(r'\s*-\s*', '', region)
                        self.data['所在地区'] = self.clean_region(region)

                    # 所属行业
                    elif text == '所属行业' and i + 1 < len(cells):
                        self.data['所属行业'] = self._cell_text(cells[i + 1])

                    # 经办人
                    elif '交易机构联系人' in text and i + 1 < len(cells):
                        self.data['经办人'] = self._cell_text(cells[i + 1])

        # 项目编号兜底，避免编号只出现在正文时漏识别
        if '项目编号' not in self.data:
            page_text = self.soup.get_text(" ", strip=True)
            match = self.PROJECT_CODE_PATTERN.search(page_text)
            if match:
                self.data['项目编号'] = match.group(0)
        
        # 项目名称 - 从title标签提取
        title = self.soup.find('title')
        if title:
            self.data['项目名称'] = title.get_text(strip=True)
        
        # 设置交易所
        self.data['交易所'] = '山交所'
        
        # 处理多转让方情况
        if len(sellers_info) > 1:
            # 使用基类的通用多转讓方处理（包括集团差异备注）
            self.process_multi_sellers(sellers_info, self.data)
            # 山东交易所使用中文逗号分隔，将基类的空格分隔改为中文逗号
            if '转让方' in self.data:
                self.data['转让方'] = self.data['转让方'].replace(' ', '，')
        

        
        return self.data
