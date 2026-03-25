#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北京产权交易所特殊模板解析器（如 CP 综合招商模板）
"""

from typing import Any, Dict

from .beijing_standard import BeijingStandardParser


class BeijingSpecialParser(BeijingStandardParser):
    """特殊模板先复用标准解析逻辑，后续可按模板差异独立演进。"""

    def parse(self) -> Dict[str, Any]:
        return super().parse()
