#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Common parser utilities."""

import re
from typing import Optional

from bs4 import BeautifulSoup


def detect_exchange(html_content: str) -> Optional[str]:
    """
    Detect exchange type by mixed signals in saved html.

    Returns one of:
    shenzhen/beijing/shanghai/chongqing/tianjin/shandong/guangzhou
    """
    soup = BeautifulSoup(html_content, "html.parser")
    content_head = html_content[:10000]
    content_snippet = html_content[:50000]
    snippet_lower = content_snippet.lower()

    # National public resource platform MHTML snapshots.
    if (
        "snapshot-content-location: https://www.ggzy.gov.cn/" in snippet_lower
        or "content-location: https://www.ggzy.gov.cn/" in snippet_lower
        or "www.ggzy.gov.cn/information/deal/html/" in snippet_lower
    ):
        return "public_resource"

    # Guangzhou has highly specific code/domain patterns.
    if re.search(r"(?:G[36R]|Q[36R])\d{4}(?:GD|GZ)\d+(?:-\d+)?", content_head):
        if (
            "/portal/pro/index.jsp?proId=" in html_content
            or re.search(r'orgEname\s*=\s*"G[DZ]\d*"', html_content)
        ):
            return "guangzhou"
    if re.search(
        r"saved from url=.*https?://(?:www\.)?(?:gduaee\.com|gz\.gemas\.com\.cn)",
        html_content[:5000],
        flags=re.IGNORECASE,
    ):
        return "guangzhou"

    # Beijing must be checked before generic Shandong keywords.
    if (
        soup.find("textarea", {"id": "jsonobj"}) is not None
        or re.search(
            r"saved from url=.*https?://(?:www\.)?cbex\.com",
            html_content[:5000],
            flags=re.IGNORECASE,
        )
        or "utrgcemsproject" in snippet_lower
        or "otc.cbex.com" in snippet_lower
    ):
        return "beijing"

    # Title-based checks.
    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    if "深圳联合产权交易所" in title_text:
        return "shenzhen"
    if "北京产权交易所" in title_text:
        return "beijing"
    if "上海联合产权交易所" in title_text:
        return "shanghai"
    if "重庆产权交易" in title_text:
        return "chongqing"
    if "天津产权交易中心" in title_text:
        return "tianjin"
    if "山东产权交易" in title_text:
        return "shandong"
    if "广东联合产权交易中心" in title_text or "广州产权交易所" in title_text:
        return "guangzhou"

    # Shandong detection with guards to avoid Beijing false positives.
    if "sdcqjy.com" in snippet_lower:
        return "shandong"
    if (
        (
            "山东产权交易中心" in content_head
            or "山东产权交易集团" in content_head
            or "山东产权" in content_head
        )
        and "cbex.com" not in snippet_lower
        and soup.find("textarea", {"id": "jsonobj"}) is None
    ):
        return "shandong"

    # Other content features.
    if soup.find("div", {"id": "js_projectName"}) or "深圳联合产权交易所" in content_head:
        return "shenzhen"
    if "北京产权交易所" in content_head:
        return "beijing"
    if "重庆产权交易" in content_head:
        return "chongqing"
    if "上海联合产权交易所" in content_head:
        return "shanghai"
    if soup.find("div", {"class": "project_code"}) and "suaee.com" in snippet_lower:
        return "shanghai"
    if (
        "/portal/pro/index.jsp?proId=" in html_content
        and re.search(r'orgEname\s*=\s*"G[DZ]\d*"', html_content)
    ):
        return "guangzhou"

    # Domain fallback.
    if "sotcbb.com" in snippet_lower:
        return "shenzhen"
    if "cbex.com" in snippet_lower:
        return "beijing"
    if "suaee.com" in snippet_lower:
        return "shanghai"
    if "cquae.com" in snippet_lower:
        return "chongqing"
    if "tpre.cn" in snippet_lower:
        return "tianjin"
    if "sdcqjy.com" in snippet_lower:
        return "shandong"

    return None
INDUSTRY_CODE_MAP = {
    "A": "农、林、牧、渔业",
    "B": "采矿业",
    "C": "制造业",
    "D": "电力、热力、燃气及水生产和供应业",
    "E": "建筑业",
    "F": "批发和零售业",
    "G": "交通运输、仓储和邮政业",
    "H": "住宿和餐饮业",
    "I": "信息传输、软件和信息技术服务业",
    "J": "金融业",
    "K": "房地产业",
    "L": "租赁和商务服务业",
    "M": "科学研究和技术服务业",
    "N": "水利、环境和公共设施管理业",
    "O": "居民服务、修理和其他服务业",
    "P": "教育",
    "Q": "卫生和社会工作",
    "R": "文化、体育和娱乐业",
    "S": "公共管理、社会保障和社会组织",
    "T": "国际组织",
}


def map_industry_code(code: str) -> str:
    """Map one-letter industry code to full Chinese label."""
    if not code:
        return code

    code = code.strip().upper()
    if len(code) == 1 and code.isalpha():
        return INDUSTRY_CODE_MAP.get(code, code)
    return code

