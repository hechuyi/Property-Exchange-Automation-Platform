#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Common parser utilities."""

from typing import Optional

from .source_classifier import detect_source_from_content


def detect_exchange(html_content: str) -> Optional[str]:
    """
    Detect exchange type by mixed signals in saved html.

    Returns one of:
    shenzhen/beijing/shanghai/chongqing/tianjin/shandong/guangzhou/public_resource
    """
    return detect_source_from_content(html_content)


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
