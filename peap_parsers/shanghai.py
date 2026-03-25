#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上海联合产权交易所路由解析器。"""

import re
from typing import Any, Dict

from .base import ParserOutput, WebPageParser
from .shanghai_special import ShanghaiSpecialParser
from .shanghai_standard import ShanghaiStandardParser


class ShanghaiParser(WebPageParser):
    """根据页面模板路由到标准或特殊解析器。"""

    def _is_special_template(self) -> bool:
        page_text = self.soup.get_text(" ", strip=True)
        if re.search(r"\bCP\d{4}SH\d+\b", page_text, re.IGNORECASE):
            return True
        if "综合招商" in page_text:
            return True
        return False

    def parse(self) -> Dict[str, Any] | ParserOutput:
        parser_cls = ShanghaiSpecialParser if self._is_special_template() else ShanghaiStandardParser
        parser = self.spawn_child_parser(parser_cls)
        return parser.parse()
