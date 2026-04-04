#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产权交易网页解析器包

包含各交易所的网页解析器实现
"""

from peap_core import DecodedDocument

from .base import ParserContext, ParserOutput, ParserVariantBinding, WebPageParser
from .beijing import BeijingParser, select_beijing_variant_binding
from .beijing_special import BeijingSpecialParser
from .beijing_standard import BeijingStandardParser
from .builtin_registry import build_builtin_registry
from .chongqing import ChongqingParser
from .family_runtime import parse_document_with_registry
from .guangzhou import GuangzhouParser
from .parser_registry import ParserFamilyBinding, ParserRegistry
from .public_resource import PublicResourceParser
from .shandong import ShandongParser
from .shanghai import ShanghaiParser, select_shanghai_variant_binding
from .shanghai_special import ShanghaiSpecialParser
from .shanghai_standard import ShanghaiStandardParser
from .shenzhen import ShenzhenParser
from .tianjin import TianjinParser
from .utils import detect_exchange

__all__ = [
    'DecodedDocument',
    'ParserContext',
    'ParserOutput',
    'ParserVariantBinding',
    'ParserRegistry',
    'ParserFamilyBinding',
    'build_builtin_registry',
    'parse_document_with_registry',
    'WebPageParser',
    'ShenzhenParser',
    'BeijingParser',
    'BeijingStandardParser',
    'BeijingSpecialParser',
    'ShanghaiParser',
    'ShanghaiStandardParser',
    'ShanghaiSpecialParser',
    'ChongqingParser',
    'TianjinParser',
    'ShandongParser',
    'GuangzhouParser',
    'PublicResourceParser',
    'select_beijing_variant_binding',
    'select_shanghai_variant_binding',
    'detect_exchange',
]
