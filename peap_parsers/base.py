#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网页解析器基类

提供通用的数据清理和提取方法
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, TypeVar, Union

from bs4 import BeautifulSoup


@dataclass
class ParserContext:
    source_file: str = ""


@dataclass(frozen=True)
class ParserOutput:
    compat_payload: Dict[str, Any]
    standard_payload: Optional[Dict[str, Any]] = None


ParserT = TypeVar("ParserT", bound="WebPageParser")


class WebPageParser:
    """网页解析器基类"""
    
    def __init__(
        self,
        html_content: str,
        field_mapping: Optional[Dict[str, Any]] = None,
        *,
        context: Optional[ParserContext] = None,
    ):
        self.soup = BeautifulSoup(html_content, 'html.parser')
        self.mapping = field_mapping or {}
        self.data: Dict[str, Any] = {}
        self.context = context or ParserContext()

    @property
    def source_file(self) -> str:
        return str(self.context.source_file or "").strip()

    @source_file.setter
    def source_file(self, value: Optional[str]) -> None:
        self.context.source_file = str(value or "").strip()

    def require_source_file(self) -> str:
        source_file = self.source_file
        if not source_file:
            raise ValueError(f"{self.__class__.__name__} requires source_file")
        return source_file

    def child_context(self) -> ParserContext:
        """Clone parser context so delegated parsers keep the explicit contract."""
        return replace(self.context)

    def spawn_child_parser(
        self,
        parser_cls: type[ParserT],
        *,
        html_content: Optional[str] = None,
        field_mapping: Optional[Dict[str, Any]] = None,
    ) -> ParserT:
        return parser_cls(
            str(html_content if html_content is not None else self.soup),
            field_mapping if field_mapping is not None else self.mapping,
            context=self.child_context(),
        )
         
    def parse(self) -> Union[Dict[str, Any], ParserOutput]:
        """解析网页，提取数据 - 子类必须实现"""
        raise NotImplementedError

    def build_standard_payload_from_data(
        self,
        field_mapping: Dict[str, Union[str, tuple[str, ...]]],
    ) -> Dict[str, Any]:
        standard_payload: Dict[str, Any] = {}
        for standard_field, source_keys in field_mapping.items():
            keys = (source_keys,) if isinstance(source_keys, str) else tuple(source_keys)
            for key in keys:
                value = self.data.get(key)
                if value not in (None, ""):
                    standard_payload[standard_field] = value
                    break
        return standard_payload

    def build_parser_output(
        self,
        *,
        compat_payload: Optional[Dict[str, Any]] = None,
        standard_payload: Optional[Dict[str, Any]] = None,
    ) -> ParserOutput:
        return ParserOutput(
            compat_payload=dict(self.data if compat_payload is None else compat_payload),
            standard_payload=None if standard_payload is None else dict(standard_payload),
        )
        
    def clean_price(self, price_str: Optional[Union[str, float]]) -> Optional[Union[str, float]]:
        """清理价格数据，转换为数值或保留特殊值"""
        if not price_str:
            return None
        price_str = str(price_str).strip()
        # 处理特殊值
        if price_str in ['择优确定', '无']:
            return price_str
        # 去掉万元单位
        price_str = price_str.replace('万元', '').replace(',', '')
        try:
            return float(price_str)
        except (ValueError, TypeError):
            return price_str
            
    def clean_date(self, date_str: Optional[str]) -> Optional[str]:
        """转换日期格式为 yyyy/mm/dd"""
        if not date_str:
            return None
        date_str = str(date_str).strip()
        # 去掉时分秒部分（如果有）
        if ' ' in date_str:
            date_str = date_str.split(' ')[0]
        if 'T' in date_str:
            date_str = date_str.split('T')[0]
        date_str = (
            date_str
            .replace('年', '/')
            .replace('月', '/')
            .replace('日', '')
            .replace('.', '/')
            .replace('-', '/')
        )
        # 处理 YYYYMMDD 格式
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}"
        # 处理 YYYY/M/D 或 YYYY/MM/DD，统一补零
        match = re.fullmatch(r'(\d{4})/(\d{1,2})/(\d{1,2})', date_str)
        if match:
            y, m, d = match.groups()
            return f"{int(y):04d}/{int(m):02d}/{int(d):02d}"
        return date_str

    def clean_region(self, region_str: Optional[str]) -> Optional[str]:
        """转换地区格式，将>替换为空格"""
        if not region_str:
            return region_str
        return str(region_str).replace('>', ' ')
        
    def extract_project_code(self) -> Optional[str]:
        """提取项目编号 - 子类可重写"""
        raise NotImplementedError
        
    def is_pre_disclosure(self, project_code: Optional[str]) -> bool:
        """判断是否为预披露项目（项目编号以-0结尾）"""
        if not project_code:
            return False
        return project_code.endswith('-0')
    

    
    def extract_contact_person(self, text: str) -> Optional[str]:
        """从文本中提取联系人/经办人姓名"""
        if not text:
            return None
        
        # 优先匹配"联系人："或"业务联系人："后的姓名
        match = re.search(r'(?:业务)?联系人[：:]\s*([\u4e00-\u9fa5]{2,4})', text)
        if match:
            return match.group(1)
        
        # 匹配"项目负责人"后的姓名
        match = re.search(r'项目负责人[：:]\s*([^电\s]{2,4})', text)
        if match:
            return match.group(1).strip()
        
        # 如果没有前缀，提取第一个2-4个连续中文字符
        match = re.search(r'[\u4e00-\u9fa5]{2,4}', text)
        if match:
            return match.group(0)
        
        return None
    
    def extract_profit_from_audit(self, text: str) -> Optional[float]:
        """从审计报告文本中提取净利润数值"""
        if not text:
            return None
        
        # 提取所有数值（包括负数）
        numbers = re.findall(r'-?\d+\.?\d*', str(text))
        if len(numbers) >= 3:
            # 格式: 营业收入XX万元营业利润XX万元净利润XX万元...
            # 第三个数值是净利润
            try:
                return float(numbers[2])
            except (ValueError, IndexError):
                pass
        
        return None
    
    def parse_table_data(self, tables, field_mapping: Dict[str, str]) -> Dict[str, Any]:
        """
        通用表格数据解析方法
        
        Args:
            tables: BeautifulSoup查找的表格列表
            field_mapping: 字段映射字典，格式为 {'标签文本': '目标字段名'}
        
        Returns:
            解析后的数据字典
        """
        result = {}
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                for i, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    
                    for label, field_name in field_mapping.items():
                        if label in text and i + 1 < len(cells):
                            result[field_name] = cells[i + 1].get_text(strip=True)
                            break
        
        return result
    
    @staticmethod
    def _append_unique_remark(data: Dict[str, Any], note: str) -> None:
        """向备注字段添加唯一的备注信息"""
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

    @staticmethod
    def _is_natural_person_seller(seller: Dict[str, Any]) -> bool:
        """Heuristic: natural person sellers do not require group-name review."""
        if bool(seller.get("is_natural_person")):
            return True

        type_text = f"{seller.get('seller_type', '')}{seller.get('seller_type_zw', '')}"
        if "自然人" in str(type_text):
            return True

        name = str(seller.get("name") or "").strip()
        if not name:
            return False

        # Organization keywords strongly indicate non-natural sellers.
        org_tokens = (
            "公司",
            "集团",
            "企业",
            "中心",
            "委员会",
            "管理局",
            "事务所",
            "合伙",
            "医院",
            "学校",
            "大学",
            "银行",
            "分行",
            "支行",
            "厂",
            "院",
            "所",
            "协会",
            "政府",
            "机关",
        )
        if any(token in name for token in org_tokens):
            return False

        # Personal names are usually short Chinese strings, optionally with "·".
        return bool(re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", name))
    
    def process_multi_sellers(self, sellers: list, data: Dict[str, Any]) -> None:
        """
        通用多转让方处理方法：处理多转让方比例拼接和集团差异备注
        
        Args:
            sellers: 转让方列表，每个元素为 {'name': 名称, 'ratio': 比例, 'hq': 集团}
            data: 目标数据字典，会被原地修改
        """
        from typing import List
        
        if not sellers:
            return
        
        # 标准化转让方数据
        normalized: List[Dict[str, Any]] = []
        seen_names = set()
        for seller in sellers:
            name = str(seller.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            normalized.append(
                {
                    "name": name,
                    "ratio": self._normalize_ratio(seller.get("ratio")) if hasattr(self, '_normalize_ratio') else str(seller.get("ratio") or "").strip(),
                    "ratio_inferred": bool(seller.get("ratio_inferred")),
                    "hq": str(seller.get("hq") or "").strip(),
                    "is_natural_person": self._is_natural_person_seller(seller),
                }
            )
        
        if not normalized:
            return
        
        # 处理多转让方：拼接名称和比例
        if len(normalized) > 1:
            seller_texts = [f"{s['name']}({s['ratio']})" if s.get("ratio") else s["name"] for s in normalized]
            # 使用"、"作为分隔符（北交所风格），兼容空格分隔（上交所风格）
            separator = "、" if hasattr(self, '_use_chinese_separator') and self._use_chinese_separator else " "
            data["转让方"] = separator.join(seller_texts)
        else:
            data["转让方"] = normalized[0]["name"]
        
        # 设置隶属集团（取第一个有集团信息的转让方）
        if not data.get("隶属集团"):
            first_hq = next((s.get("hq", "") for s in normalized if s.get("hq")), "")
            if first_hq:
                data["隶属集团"] = first_hq
        
        # 处理多转让方集团备注：多转让方时按“各转让方-隶属集团”写入备注。
        if len(normalized) > 1:
            hq_pairs = []
            for s in normalized:
                hq = str(s.get("hq") or "").strip()
                if not hq:
                    continue
                hq_pairs.append(f"{s['name']}隶属{hq}")
            if hq_pairs:
                self._append_unique_remark(data, "；".join(dict.fromkeys(hq_pairs)))

            # 多转让方信息不完整时，显式提示人工复核。
            missing_ratio = any(not str(s.get("ratio") or "").strip() for s in normalized)
            inferred_ratio = any(bool(s.get("ratio_inferred")) for s in normalized)
            missing_hq = any(
                (not str(s.get("hq") or "").strip()) and (not bool(s.get("is_natural_person")))
                for s in normalized
            )
            if missing_ratio or inferred_ratio:
                self._append_unique_remark(
                    data,
                    "多转让方未明确各转让方拟转让比例，请人工复核",
                )
            if missing_hq:
                self._append_unique_remark(
                    data,
                    "多转让方未明确各转让方隶属集团，请人工复核",
                )
