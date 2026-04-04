#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北京产权交易所路由解析器
"""

import json
from typing import Any, Dict

from .base import ParserOutput, ParserVariantBinding, WebPageParser
from .beijing_special import BeijingSpecialParser
from .beijing_standard import BeijingStandardParser
from .parser_registry import ParserFamilyBinding


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
        obj = json_data.get("object")
        if isinstance(obj, dict) and isinstance(obj.get("detail"), dict):
            return True

        project = obj.get("detail") if isinstance(obj, dict) and isinstance(obj.get("detail"), dict) else {}
        if isinstance(project, dict):
            project_code = str(project.get("projectcode") or project.get("XMBH") or "").upper()
            if project_code.startswith("CP"):
                return True

        return False

    def parse(self) -> Dict[str, Any] | ParserOutput:
        binding = select_beijing_variant_binding_from_parser(self)
        parser = self.spawn_child_parser(binding.parser_cls)
        return parser.parse()


def select_beijing_variant_binding(document) -> ParserFamilyBinding:
    parser = BeijingParser(str(document.dom) if document is not None else "<html></html>")
    return _binding_from_parser(parser, binding_cls=ParserFamilyBinding)


def select_beijing_variant_binding_from_parser(parser: BeijingParser) -> ParserVariantBinding:
    return _binding_from_parser(parser, binding_cls=ParserVariantBinding)


def _binding_from_parser(parser: BeijingParser, *, binding_cls):
    json_data = parser._load_json_data()
    is_special = parser._is_special_template(json_data)
    parser_cls = BeijingSpecialParser if is_special else BeijingStandardParser
    variant_id = "special" if is_special else "standard"
    variant_version = f"builtin/beijing/{variant_id}/v1"
    kwargs = {
        "variant_id": variant_id,
        "variant_version": variant_version,
        "parser_cls": parser_cls,
    }
    if binding_cls is ParserFamilyBinding:
        kwargs.update(
            family_id="beijing",
            family_version="builtin/beijing/v1",
            page_kind="listing",
        )
    return binding_cls(**kwargs)
