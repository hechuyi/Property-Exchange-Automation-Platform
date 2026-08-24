#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重庆产权交易所解析器
"""

import re
from typing import Any, Dict, List

from .base import WebPageParser


class ChongqingParser(WebPageParser):
    """重庆产权交易所解析器"""
    
    # 使用中文分隔符（与深交所/山交所/广交所风格一致）
    _use_chinese_separator = True
    
    def parse(self) -> Dict[str, Any]:
        """解析重交所网页"""
        def normalize_label(value: str) -> str:
            text = str(value or "").strip()
            text = re.sub(r"\s+", "", text)
            return text.replace("：", "").replace(":", "")

        def normalize_ratio(value: str) -> str:
            ratio = str(value or "").strip().replace("％", "%")
            if not ratio:
                return ""
            if ratio.endswith("%"):
                return ratio
            if re.fullmatch(r"\d+(?:\.\d+)?", ratio):
                return f"{ratio}%"
            return ratio

        def normalize_contact(value: str) -> str:
            contact = str(value or "").strip()
            if not contact:
                return ""
            contact = re.split(r"[/／]", contact, maxsplit=1)[0].strip()
            extracted = self.extract_contact_person(contact)
            return extracted or contact

        def is_valid_date(value: str) -> bool:
            text = str(value or "").strip()
            return bool(re.fullmatch(r"20\d{2}[/-]\d{2}[/-]\d{2}", text))

        def pick_by_regex(text: str, patterns: List[str]) -> str:
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = str(match.group(1) or "").strip()
                    if value:
                        return value
            return ""

        def clean_project_name(name: str) -> str:
            project_name = str(name or "").strip()
            if not project_name:
                return ""
            project_name = re.sub(r"\s+-\s*重庆产权交易网\s*$", "", project_name)
            project_name = re.sub(r"^\s*项目名称[:：]\s*", "", project_name)
            return project_name.strip()

        empty_tokens = {'', '无', '暂无', '-', '--', '—', 'N/A', 'NA', 'null', 'None'}

        def normalize_group(value: str) -> str:
            group = str(value or "").strip()
            return "" if group in empty_tokens else group

        def is_specific_regulator(value: str) -> bool:
            regulator = normalize_group(value)
            if not regulator:
                return False
            # 泛化枚举值不写入隶属集团
            generic_regulators = {
                '市级(区县)国资委监管',
                '省级(直辖市)国资委监管',
                '省级(直辖市)其他部门监管',
                '市级(区县)其他部门监管',
            }
            if regulator in generic_regulators:
                return False
            # 仅保留有明确地区信息的监管机构描述
            return bool(re.search(r'[\u4e00-\u9fa5]+(?:市|县|区)国资委', regulator))

        invalid_group_tokens = {
            '转让方名称',
            '转让方',
            '项目编号',
            '项目名称',
            '国资监管机构',
            '所属集团或主管部门名称',
            '国家出资企业或主管部门名称',
            '批准单位名称',
            '拟转让产(股)权比例(%)',
            '持有产(股)权比例(%)',
        }

        # 从表格中提取数据
        tables = self.soup.find_all('table')
        sellers_info: List[Dict[str, str]] = []  # 收集多个转让方及其比例
        current_seller = None  # 当前正在处理的转让方
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                # 只取当前行直接单元格，避免嵌套表格导致标签和值错位
                cells = row.find_all(['td', 'th'], recursive=False)
                for i, cell in enumerate(cells):
                    text = normalize_label(cell.get_text(strip=True))
                    
                    if '项目编号' in text and i + 1 < len(cells):
                        self.data['项目编号'] = cells[i + 1].get_text(strip=True)
                    elif '项目名称' in text and i + 1 < len(cells):
                        self.data['项目名称'] = cells[i + 1].get_text(strip=True)
                    elif '转让标的名称' in text and i + 1 < len(cells):
                        self.data['项目名称'] = cells[i + 1].get_text(strip=True)
                    elif '转让底价' in text and i + 1 < len(cells):
                        self.data['挂牌价格'] = self.clean_price(cells[i + 1].get_text(strip=True))
                    elif '拟募集资金总额' in text and i + 1 < len(cells):
                        # 增资扩股项目的融资金额（兼容(万元)等后缀）
                        raw_amount = cells[i + 1].get_text(strip=True)
                        self.data['融资金额'] = raw_amount
                        self.data['挂牌价格'] = self.clean_price(raw_amount)
                    elif '拟募集资金' in text and '持股比例' in text and i + 1 < len(cells):
                        ratio = normalize_ratio(cells[i + 1].get_text(strip=True))
                        if ratio:
                            self.data['持股比例'] = ratio
                    elif ('增资企业名称' in text or text == '融资方名称' or text == '增资方名称') and i + 1 < len(cells):
                        company_name = cells[i + 1].get_text(strip=True)
                        if company_name:
                            self.data['融资方'] = company_name
                            # 预披露表结构复用“转让方”列，增资项目也同步写入
                            if not self.data.get('转让方'):
                                self.data['转让方'] = company_name
                    elif '标的所属行业' in text and i + 1 < len(cells):
                        self.data['所属行业'] = cells[i + 1].get_text(strip=True)
                    elif text == '\u8d44\u4ea7\u7c7b\u522b' and i + 1 < len(cells):
                        asset_cat = cells[i + 1].get_text(strip=True)
                        if asset_cat:
                            self.data['所属行业'] = asset_cat
                    elif '所属行业' in text and i + 1 < len(cells):
                        # 预披露项目的所属行业
                        if '所属行业' not in self.data:
                            self.data['所属行业'] = cells[i + 1].get_text(strip=True)
                    elif '标的所在地区' in text and i + 1 < len(cells):
                        self.data['所在地区'] = self.clean_region(cells[i + 1].get_text(strip=True))
                    elif text == '住所' and i + 1 < len(cells):
                        # 预披露项目的住所作为所在地区
                        if '所在地区' not in self.data:
                            self.data['所在地区'] = self.clean_region(cells[i + 1].get_text(strip=True))
                    elif '经济类型' in text and i + 1 < len(cells):
                        pass
                    elif ('信息披露起始日期' in text or '挂牌起始日期' in text or text == '挂牌开始日期') and i + 1 < len(cells):
                        self.data['挂牌开始日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    elif (
                        '信息披露结束日期' in text
                        or '信息披露期满日期' in text
                        or '挂牌期满日期' in text
                        or text == '挂牌截止日期'
                    ) and i + 1 < len(cells):
                        self.data['挂牌截止日期'] = self.clean_date(cells[i + 1].get_text(strip=True))
                    elif text == '转让方名称' and i + 1 < len(cells):
                        # 处理多转让方情况
                        seller_name = cells[i + 1].get_text(strip=True)
                        if seller_name and '转让方' not in self.data:
                            self.data['转让方'] = seller_name
                        if seller_name:
                            current_seller = {'name': seller_name}
                            sellers_info.append(current_seller)
                    elif text == '转让方' and i + 1 < len(cells):
                        # 预披露项目的转让方
                        if '转让方' not in self.data:
                            seller_name = cells[i + 1].get_text(strip=True)
                            if seller_name:
                                self.data['转让方'] = seller_name
                                current_seller = {'name': seller_name}
                                sellers_info.append(current_seller)
                    elif '拟转让' in text and '比例' in text and i + 1 < len(cells) and current_seller:
                        # 记录转让比例
                        ratio = normalize_ratio(cells[i + 1].get_text(strip=True))
                        current_seller['ratio'] = ratio
                    elif (
                        text == '所属集团或主管部门名称'
                        or text == '国家出资企业或主管部门名称'
                        or text == '主管部门名称'
                        or text == '\u884c\u4e3a\u6279\u51c6\u673a\u6784'
                    ) and i + 1 < len(cells):
                        group_name = normalize_group(cells[i + 1].get_text(strip=True))
                        # “行为批准机构”只作为隶属集团补充来源，不覆盖已识别结果。
                        if text == '\u884c\u4e3a\u6279\u51c6\u673a\u6784' and self.data.get('隶属集团'):
                            continue
                        if text == '\u884c\u4e3a\u6279\u51c6\u673a\u6784' and not any(k in group_name for k in ('集团', '公司', '有限')):
                            continue
                        if group_name and normalize_label(group_name) not in invalid_group_tokens:
                            self.data['隶属集团'] = group_name
                            if current_seller is not None:
                                current_seller['hq'] = group_name
                    elif text == '批准单位名称' and i + 1 < len(cells):
                        # 预披露项目的批准单位作为隶属集团
                        project_code = str(self.data.get('项目编号') or '')
                        if (
                            project_code.endswith('-0')
                            and ('隶属集团' not in self.data or not self.data.get('隶属集团'))
                        ):
                            approved = normalize_group(cells[i + 1].get_text(strip=True))
                            if approved and normalize_label(approved) not in invalid_group_tokens:
                                self.data['隶属集团'] = approved
                    elif '项目受理联系人' in text and i + 1 < len(cells):
                        contact_text = normalize_contact(cells[i + 1].get_text(strip=True))
                        if contact_text:
                            self.data['经办人'] = contact_text
                    elif '联系人' in text and i + 1 < len(cells):
                        # 预披露项目的普通联系人（优先级低于“项目受理联系人/交易机构联系人”）
                        if '经办人' not in self.data:
                            contact_text = normalize_contact(cells[i + 1].get_text(strip=True))
                            if contact_text:
                                self.data['经办人'] = contact_text
                    elif '国资监管机构' in text and i + 1 < len(cells):
                        regulator = normalize_group(cells[i + 1].get_text(strip=True))
                        if (
                            is_specific_regulator(regulator)
                            and ('隶属集团' not in self.data or not self.data.get('隶属集团'))
                        ):
                            self.data['隶属集团'] = regulator
                        if is_specific_regulator(regulator) and current_seller is not None and not current_seller.get('hq'):
                            current_seller['hq'] = regulator

        project_name = str(self.data.get('项目名称') or '').strip()
        if not self.data.get('融资方') and project_name.endswith('增资项目'):
            inferred_name = project_name[: -len('增资项目')].strip()
            if inferred_name:
                self.data['融资方'] = inferred_name
                if not self.data.get('转让方'):
                    self.data['转让方'] = inferred_name
        
        # 去重并处理多转让方（重庆页面常出现重复区块，需去重）
        unique_sellers: List[Dict[str, str]] = []
        seen = set()
        for seller in sellers_info:
            name = str(seller.get('name') or '').strip()
            ratio = str(seller.get('ratio') or '').strip()
            hq = str(seller.get('hq') or '').strip()
            if not name:
                continue
            key = (name, ratio, hq)
            if key in seen:
                continue
            seen.add(key)
            normalized = {'name': name}
            if ratio:
                normalized['ratio'] = ratio
            if hq:
                normalized['hq'] = hq
            unique_sellers.append(normalized)

        # 使用基类的通用多转让方处理方法（处理比例拼接和集团差异备注）
        if unique_sellers:
            self.process_multi_sellers(unique_sellers, self.data)

        # “无/暂无/-”等占位值统一清空
        if str(self.data.get('隶属集团') or '').strip() in empty_tokens:
            self.data['隶属集团'] = ''
        if str(self.data.get('持股比例') or '').strip() in empty_tokens:
            self.data['持股比例'] = ''

        # 重交所实物资产页大量字段为“标签:值”内联文本，做正则回填与纠错
        page_text = self.soup.get_text("\n", strip=True)
        title_text = self.soup.title.get_text(" ", strip=True) if self.soup.title else ""
        project_name_from_title = clean_project_name(title_text)
        project_name = str(self.data.get('项目名称') or '').strip()
        if not project_name and project_name_from_title:
            self.data['项目名称'] = project_name_from_title

        project_code = str(self.data.get('项目编号') or '').strip()
        is_physical_like = (
            project_code.startswith(('GR', 'QR', 'PR'))
            or ('资产转让公告' in page_text)
            or ('标的简况' in page_text)
            or ('标的名称' in page_text and ('挂牌价' in page_text or '转让底价' in page_text))
        )

        if not project_code:
            extracted_code = pick_by_regex(
                page_text,
                [
                    r'项目编号[:：]?\s*([A-Z]{2}\d{4}CQ\d+(?:-\d+)?)',
                    r'项目编号[:：]?\s*(20\d{10})',
                ],
            )
            if extracted_code:
                self.data['项目编号'] = extracted_code
            elif is_physical_like:
                html_text = str(self.soup)
                id_match = re.search(r'/Project/(?:Show|Object/Obj_Show\d+)\?id=(\d{5,})', html_text)
                if id_match:
                    self.data['项目编号'] = f"CQID{id_match.group(1)}"

        if (not self.data.get('转让方')) and is_physical_like:
            seller_name = pick_by_regex(
                page_text,
                [
                    r'转让方名称[:：]?\s*([^\n\r]{2,120})',
                ],
            )
            if not seller_name:
                title_name = str(self.data.get('项目名称') or '')
                if '持有的' in title_name:
                    candidate = title_name.split('持有的', 1)[0].strip()
                    if len(candidate) >= 2:
                        seller_name = candidate
            if seller_name:
                self.data['转让方'] = seller_name

        current_price = str(self.data.get('挂牌价格') or '').strip()
        if (
            (not current_price)
            or ('保证金' in current_price)
            or ('联系人' in current_price)
            or ('发布机构' in current_price)
        ):
            price_text = pick_by_regex(
                page_text,
                [
                    r'转让底价[:：]?\s*([0-9][0-9,]*(?:\.\d+)?)\s*万元?',
                    r'挂牌价[:：]?\s*([0-9][0-9,]*(?:\.\d+)?)\s*万元?',
                ],
            )
            if price_text:
                self.data['挂牌价格'] = self.clean_price(price_text)

        start_date = str(self.data.get('挂牌开始日期') or '').strip()
        if not is_valid_date(start_date):
            start_raw = pick_by_regex(
                page_text,
                [
                    r'信息披露起始日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'挂牌开始日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'挂牌起始日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                ],
            )
            if start_raw:
                self.data['挂牌开始日期'] = self.clean_date(start_raw)

        end_date = str(self.data.get('挂牌截止日期') or '').strip()
        if not is_valid_date(end_date):
            end_raw = pick_by_regex(
                page_text,
                [
                    r'信息披露结束日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'信息披露期满日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'挂牌截止日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'挂牌期满日期[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                    r'首次挂牌到期时间[:：]?\s*(20\d{2}[-/]\d{2}[-/]\d{2})',
                ],
            )
            if end_raw:
                self.data['挂牌截止日期'] = self.clean_date(end_raw)

        if not self.data.get('所在地区'):
            region = pick_by_regex(page_text, [r'标的所在地区[:：]?\s*([^\n\r]{2,120})'])
            if region:
                self.data['所在地区'] = self.clean_region(region)

        if not self.data.get('所属行业'):
            industry = pick_by_regex(page_text, [r'标的所属行业[:：]?\s*([^\n\r]{2,120})'])
            if not industry:
                industry = pick_by_regex(page_text, [r'资产类别[:：]?\s*([^\n\r]{1,30})'])
            if industry and ('标的所在地区' not in industry):
                self.data['所属行业'] = industry

        # 经办人统一优先使用“项目受理联系人/交易机构联系人”
        preferred_contact = pick_by_regex(
            page_text,
            [
                r'项目受理联系人[:：]?\s*([^\n\r]{2,120})',
                r'交易机构联系人[:：]?\s*([^\n\r]{2,120})',
            ],
        )
        if preferred_contact:
            normalized = normalize_contact(preferred_contact)
            if normalized:
                self.data['经办人'] = normalized
        elif not self.data.get('经办人'):
            contact_text = pick_by_regex(
                page_text,
                [
                    r'业务联系人[:：]?\s*([^\n\r]{2,120})',
                    r'联系人[:：]?\s*([^\n\r]{2,120})',
                ],
            )
            if contact_text:
                normalized = normalize_contact(contact_text)
                if normalized:
                    self.data['经办人'] = normalized

        self.data['交易所'] = '重交所'
        return self.data
