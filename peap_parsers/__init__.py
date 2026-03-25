#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产权交易网页解析器包

包含各交易所的网页解析器实现
"""

from .base import ParserContext, ParserOutput, WebPageParser
from .beijing import BeijingParser
from .beijing_special import BeijingSpecialParser
from .beijing_standard import BeijingStandardParser
from .chongqing import ChongqingParser
from .guangzhou import GuangzhouParser
from .public_resource import PublicResourceParser
from .shandong import ShandongParser
from .shanghai import ShanghaiParser
from .shanghai_special import ShanghaiSpecialParser
from .shanghai_standard import ShanghaiStandardParser
from .shenzhen import ShenzhenParser
from .tianjin import TianjinParser
from .utils import detect_exchange

__all__ = [
    'ParserContext',
    'ParserOutput',
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
    'detect_exchange',
]
