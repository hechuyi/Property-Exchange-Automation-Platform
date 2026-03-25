#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北京产权交易所路由解析器
"""

import json
from typing import Any, Dict

from .base import ParserOutput, WebPageParser
from .beijing_special import BeijingSpecialParser
from .beijing_standard import BeijingStandardParser


class BeijingParser(WebPageParser):
    """根据页面模板自动路由到标准或特殊解析器。"""

    def _load_json_data(self) -> Dict[str, Any]:
        textarea = self.soup.find("textarea", id="jsonobj")
        if not textarea:
            return {}
        try:
            data = json.loads(textarea.text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {}
        return {}

    def _is_special_template(self, json_data: Dict[str, Any]) -> bool:
        # 1) object.detail usually indicates CP comprehensive招商 template.
        obj = json_data.get("object")
        if isinstance(obj, dict) and isinstance(obj.get("detail"), dict):
            return True

        # 2) CP project codes are non-standard compared with G*/Q*/GR*.
        project = obj.get("detail") if isinstance(obj, dict) and isinstance(obj.get("detail"), dict) else {}
        if isinstance(project, dict):
            project_code = str(project.get("projectcode") or project.get("XMBH") or "").upper()
            if project_code.startswith("CP"):
                return True

        return False

    def parse(self) -> Dict[str, Any] | ParserOutput:
        json_data = self._load_json_data()
        parser_cls = BeijingSpecialParser if self._is_special_template(json_data) else BeijingStandardParser
        parser = self.spawn_child_parser(parser_cls)
        return parser.parse()
