from __future__ import annotations

from .beijing import select_beijing_variant_binding
from .chongqing import ChongqingParser
from .deal_cbex import DealCBEXParser
from .deal_cquae import DealCQUAEParser
from .deal_sse import DealSSEParser
from .deal_tpre import DealTPREParser
from .guangzhou import GuangzhouParser
from .parser_registry import ParserFamilyBinding, ParserRegistry
from .shandong import ShandongParser
from .shanghai import select_shanghai_variant_binding
from .shenzhen import ShenzhenParser
from .tianjin import TianjinParser


def build_builtin_registry() -> ParserRegistry:
    return ParserRegistry(
        {
            "beijing": ParserFamilyBinding(
                family_id="beijing",
                family_version="builtin/beijing/v1",
                parser_cls=select_beijing_variant_binding(None).parser_cls,
                variant_id="standard",
                variant_version="builtin/beijing/standard/v1",
                page_kind="listing",
                selector=select_beijing_variant_binding,
            ),
            "cbex": ParserFamilyBinding(
                family_id="beijing",
                family_version="builtin/beijing/v1",
                parser_cls=DealCBEXParser,
                variant_id="deal",
                variant_version="builtin/beijing/deal/v1",
                page_kind="deal",
            ),
            "shanghai": ParserFamilyBinding(
                family_id="shanghai",
                family_version="builtin/shanghai/v1",
                parser_cls=select_shanghai_variant_binding(None).parser_cls,
                variant_id="standard",
                variant_version="builtin/shanghai/standard/v1",
                page_kind="listing",
                selector=select_shanghai_variant_binding,
            ),
            "sse": ParserFamilyBinding(
                family_id="shanghai",
                family_version="builtin/shanghai/v1",
                parser_cls=DealSSEParser,
                variant_id="deal",
                variant_version="builtin/shanghai/deal/v1",
                page_kind="deal",
            ),
            "shenzhen": ParserFamilyBinding(
                family_id="shenzhen",
                family_version="builtin/shenzhen/v1",
                parser_cls=ShenzhenParser,
                variant_id="standard",
                variant_version="builtin/shenzhen/standard/v1",
                page_kind="listing",
            ),
            "chongqing": ParserFamilyBinding(
                family_id="chongqing",
                family_version="builtin/chongqing/v1",
                parser_cls=ChongqingParser,
                variant_id="standard",
                variant_version="builtin/chongqing/standard/v1",
                page_kind="listing",
            ),
            "cquae": ParserFamilyBinding(
                family_id="chongqing",
                family_version="builtin/chongqing/v1",
                parser_cls=DealCQUAEParser,
                variant_id="deal",
                variant_version="builtin/chongqing/deal/v1",
                page_kind="deal",
            ),
            "tianjin": ParserFamilyBinding(
                family_id="tianjin",
                family_version="builtin/tianjin/v1",
                parser_cls=TianjinParser,
                variant_id="standard",
                variant_version="builtin/tianjin/standard/v1",
                page_kind="listing",
            ),
            "tpre": ParserFamilyBinding(
                family_id="tianjin",
                family_version="builtin/tianjin/v1",
                parser_cls=DealTPREParser,
                variant_id="deal",
                variant_version="builtin/tianjin/deal/v1",
                page_kind="deal",
            ),
            "shandong": ParserFamilyBinding(
                family_id="shandong",
                family_version="builtin/shandong/v1",
                parser_cls=ShandongParser,
                variant_id="standard",
                variant_version="builtin/shandong/standard/v1",
                page_kind="listing",
            ),
            "guangdong": ParserFamilyBinding(
                family_id="guangdong",
                family_version="builtin/guangdong/v1",
                parser_cls=GuangzhouParser,
                variant_id="standard",
                variant_version="builtin/guangdong/standard/v1",
                page_kind="listing",
            ),
        }
    )


__all__ = ["build_builtin_registry"]
