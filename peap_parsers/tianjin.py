#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天津产权交易所解析器
"""

import re
from typing import Any, Dict, List

from .base import WebPageParser
from .utils import map_industry_code


class TianjinParser(WebPageParser):
    """天津产权交易所解析器"""

    def _infer_project_type(self) -> None:
        if self.data.get("项目类型"):
            return
        project_code = str(self.data.get("项目编号") or "").strip().upper()
        project_name = str(self.data.get("项目名称") or "").strip()
        if project_code.endswith("-0") or "预披露" in project_name:
            self.data["项目类型"] = "预披露"
        elif project_code.startswith("G6") or project_name.endswith("增资项目") or "增资扩股" in project_name:
            self.data["项目类型"] = "增资扩股"
        elif project_code.startswith(("GR", "TR")) or "土地使用权" in project_name or "在建工程" in project_name or "房产" in project_name:
            self.data["项目类型"] = "实物资产"
        elif project_code.startswith(("G3", "T3", "Q3")) or "股权" in project_name or "股份" in project_name:
            self.data["项目类型"] = "股权转让"

    def _build_standard_payload(self) -> Dict[str, Any]:
        return self.build_standard_payload_from_data(
            {
                "project_code": "项目编号",
                "project_name": "项目名称",
                "business_type": "项目类型",
                "project_type": "项目类型",
                "exchange": "交易所",
                "seller": ("融资方", "转让方"),
                "group_name": "隶属集团",
                "industry": "所属行业",
                "region": "所在地区",
                "contact": "经办人",
                "price": ("融资金额", "挂牌价格"),
                "start_date": ("披露开始日期", "预披露开始日期", "挂牌开始日期"),
                "end_date": ("披露截止日期", "预披露截止日期", "挂牌截止日期"),
                "profit": ("近一年净利润", "近一年净利润（万）"),
                "asset_total": ("总资产", "总资产（万）"),
                "share_ratio": "持股比例",
                "remark": "备注",
            }
        )
    
    def parse(self):
        """解析天津交易所网页"""
        # 提取标题
        title = self.soup.find('title')
        if title:
            self.data['项目名称'] = title.get_text(strip=True)
        
        # 从Vue渲染的HTML中提取数据
        # 项目名称
        title_elem = self.soup.find('div', class_='title')
        if title_elem:
            self.data['项目名称'] = title_elem.get_text(strip=True)
        
        # 尝试从project-view类中提取项目编号（Vue渲染的新格式）
        project_view = self.soup.find('div', class_='project-view')
        if project_view:
            # 查找所有span标签，第一个通常是项目编号
            spans = project_view.find_all('span')
            for span in spans:
                text = span.get_text(strip=True)
                # 匹配天津交易所项目编号（如 GR2025TJ1000468-4、TR2024TJ1000004-3）
                match = re.search(r'([A-Z]{1,3}\d{4}TJ\d+(?:-\d+)?)', text)
                if match:
                    self.data['项目编号'] = match.group(0)
                    break
        
        # 尝试从listing-price-text类中提取挂牌价格（实物资产页面）
        price_div = self.soup.find('div', class_='listing-price-text')
        if price_div:
            price_text = price_div.get_text(strip=True)
            # 提取价格数字（支持小数），如 "挂牌价: 11,142.66万元"
            match = re.search(r'([\d,]+(?:\.\d+)?)\s*万元', price_text)
            if match:
                self.data['挂牌价格'] = self.clean_price(match.group(1))
        
        # 尝试从project-price类中提取价格（另一种格式）
        if '挂牌价格' not in self.data:
            project_price = self.soup.find('div', class_='project-price')
            if project_price:
                price_text = project_price.get_text(strip=True)
                match = re.search(r'([\d,]+(?:\.\d+)?)\s*万元', price_text)
                if match:
                    self.data['挂牌价格'] = self.clean_price(match.group(1))
        
        # 查找所有表格数据
        tables = self.soup.find_all('table')
        sellers_info: List[Dict[str, str]] = []  # 收集多个转让方及其比例
        current_seller = None  # 当前正在处理的转让方
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['th', 'td'])
                for i, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    
                    # 项目编号 - 天津交易所格式如 G32025TJ1000067-2
                    # 只有当还没有找到项目编号时才从表格中提取
                    if text == '项目编号' and i + 1 < len(cells) and '项目编号' not in self.data:
                        self.data['项目编号'] = cells[i + 1].get_text(strip=True)

                    elif text in {'项目名称', '标的名称'} and i + 1 < len(cells):
                        self.data['项目名称'] = cells[i + 1].get_text(strip=True)

                    elif text in {'项目类型', '业务类型'} and i + 1 < len(cells):
                        self.data['项目类型'] = cells[i + 1].get_text(strip=True)
                    
                    # 挂牌价格
                    elif '转让底价' in text and i + 1 < len(cells):
                        price_text = cells[i + 1].get_text(strip=True)
                        self.data['挂牌价格'] = self.clean_price(price_text)

                    # 企业增资页面使用“拟融资金额”作为挂牌金额。
                    elif '拟融资金额' in text and i + 1 < len(cells):
                        price_text = cells[i + 1].get_text(strip=True)
                        self.data['融资金额'] = self.clean_price(price_text)
                    
                    # 转让方（处理多转让方情况）
                    elif '转让方名称' in text and i + 1 < len(cells):
                        seller_name = cells[i + 1].get_text(strip=True)
                        if '转让方' not in self.data:
                            self.data['转让方'] = seller_name
                        current_seller = {'name': seller_name}
                        sellers_info.append(current_seller)

                    # 企业增资页面以“增资企业名称”标识融资方。
                    elif text in {'增资企业名称', '融资方名称', '融资方'} and i + 1 < len(cells):
                        financing_name = cells[i + 1].get_text(strip=True)
                        if financing_name and '融资方' not in self.data:
                            self.data['融资方'] = financing_name
                    
                    # 拟转让比例
                    elif '拟转让比例' in text and i + 1 < len(cells) and current_seller:
                        ratio = cells[i + 1].get_text(strip=True)
                        current_seller['ratio'] = ratio
                    
                    # 所属行业
                    elif '所属行业' in text and i + 1 < len(cells):
                        self.data['所属行业'] = cells[i + 1].get_text(strip=True)
                    
                    # 所在地区 - 优先使用"所在地区"标签
                    elif '所在地区' in text and i + 1 < len(cells):
                        self.data['所在地区'] = self.clean_region(cells[i + 1].get_text(strip=True))
                    # 如果没有所在地区，使用注册地的省份城市信息
                    elif '注册地' in text or '住所' in text:
                        if i + 1 < len(cells) and '所在地区' not in self.data:
                            region_cell = cells[i + 1]
                            # 尝试从属性中提取省份和城市
                            province = region_cell.get('zoneprovincezw', '')
                            city = region_cell.get('zonecityzw', '')
                            if province and city and city != '市辖区':
                                self.data['所在地区'] = f"{province}{city}"
                            elif province:
                                self.data['所在地区'] = province
                            else:
                                # 从文本中提取前几个字符作为地区
                                region_text = region_cell.get_text(strip=True)
                                self.data['所在地区'] = self.clean_region(region_text)
                    
                    # 信息披露起始日期
                    elif '信息披露起始日期' in text and i + 1 < len(cells):
                        self.data['挂牌开始日期'] = self.clean_date(cells[i + 1].get_text(strip=True))

                    # 预披露页面使用独立的日期标签。
                    elif '预披露起始日期' in text and i + 1 < len(cells):
                        self.data['预披露开始日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    
                    # 信息披露截止日期
                    elif '信息披露截止日期' in text and i + 1 < len(cells):
                        self.data['挂牌截止日期'] = self.clean_date(cells[i + 1].get_text(strip=True))

                    elif '预披露截止日期' in text and i + 1 < len(cells):
                        self.data['预披露截止日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    
                    # 隶属集团
                    elif '国家出资企业或主管部门名称' in text and i + 1 < len(cells):
                        hq = cells[i + 1].get_text(strip=True)
                        self.data['隶属集团'] = hq
                        # 同时将隶属集团信息添加到当前转让方（如果存在）
                        if current_seller:
                            current_seller['hq'] = hq
                    
                    # 国资监管机构
                    elif '国资监管机构' in text and i + 1 < len(cells):
                        pass
                    
                    # 净利润 - 从2024年度审计报告表格中提取
                    elif '2024年度审计报告' in text and '净利润' in text:
                        profit = self.extract_profit_from_audit(text)
                        if profit is not None:
                            self.data['近一年净利润'] = profit
                    
                    # 交易机构信息（包含项目负责人/经办人）
                    elif '交易机构' in text and i + 1 < len(cells):
                        agency_text = cells[i + 1].get_text(strip=True)
                        # 提取项目负责人
                        if '项目负责人' in agency_text:
                            match = re.search(r'项目负责人[:：]\s*([^电\s]+)', agency_text)
                            if match:
                                self.data['经办人'] = match.group(1).strip()

        # 兜底提取项目编号（避免部分页面编号样式变体导致缺失）
        if not self.data.get('项目编号'):
            page_text = self.soup.get_text(" ", strip=True)
            match = re.search(r'([A-Z]{1,3}\d{4}TJ\d+(?:-\d+)?)', page_text)
            if match:
                self.data['项目编号'] = match.group(1)

        # 兜底提取经办人（天津新版页面常在 contact-info-container 中）
        if not self.data.get('经办人'):
            contact_container = self.soup.find('div', class_='contact-info-container')
            search_text = (
                contact_container.get_text(" ", strip=True)
                if contact_container
                else self.soup.get_text(" ", strip=True)
            )
            match = re.search(r'项目负责人[:：]\s*([\u4e00-\u9fa5A-Za-z·]{2,20})', search_text)
            if not match:
                match = re.search(r'(?:业务)?联系人[:：]\s*([\u4e00-\u9fa5A-Za-z·]{2,20})', search_text)
            if match:
                self.data['经办人'] = match.group(1).strip()
        
        # 设置交易所
        self.data['交易所'] = '天交所'
        self._infer_project_type()
        
        # 处理多转让方情况
        if len(sellers_info) > 1:
            # 使用基类的通用多转让方处理（包括集团差异备注）
            self.process_multi_sellers(sellers_info, self.data)
        
        # 处理所属行业代码（如"L"）- 如果只有一位字母代码，尝试查找完整名称
        if '所属行业' in self.data:
            self.data['所属行业'] = map_industry_code(self.data['所属行业'])
        
        return self.build_parser_output(standard_payload=self._build_standard_payload())
