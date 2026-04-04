#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上海联合产权交易所路由解析器。"""

import re
from typing import Any, Dict

from .base import ParserOutput, ParserVariantBinding, WebPageParser
from .parser_registry import ParserFamilyBinding
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
        binding = select_shanghai_variant_binding_from_parser(self)
        parser = self.spawn_child_parser(binding.parser_cls)
        return parser.parse()


def select_shanghai_variant_binding(document) -> ParserFamilyBinding:
    parser = ShanghaiParser(str(document.dom) if document is not None else "<html></html>")
    return _binding_from_parser(parser, binding_cls=ParserFamilyBinding)


def select_shanghai_variant_binding_from_parser(parser: ShanghaiParser) -> ParserVariantBinding:
    return _binding_from_parser(parser, binding_cls=ParserVariantBinding)


def _binding_from_parser(parser: ShanghaiParser, *, binding_cls):
    is_special = parser._is_special_template()
    parser_cls = ShanghaiSpecialParser if is_special else ShanghaiStandardParser
    variant_id = "special" if is_special else "standard"
    variant_version = f"builtin/shanghai/{variant_id}/v1"
    kwargs = {
        "variant_id": variant_id,
        "variant_version": variant_version,
        "parser_cls": parser_cls,
    }
    if binding_cls is ParserFamilyBinding:
        kwargs.update(
            family_id="shanghai",
            family_version="builtin/shanghai/v1",
            page_kind="listing",
        )
    return binding_cls(**kwargs)
