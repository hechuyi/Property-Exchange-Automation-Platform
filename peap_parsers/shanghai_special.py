#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上海联合产权交易所特殊模板解析器
"""

from typing import Any, Dict

from .shanghai_standard import ShanghaiStandardParser


class ShanghaiSpecialParser(ShanghaiStandardParser):
    """特殊模板先复用标准逻辑，后续按模板差异单独演进。"""

    def parse(self) -> Dict[str, Any]:
        return super().parse()
