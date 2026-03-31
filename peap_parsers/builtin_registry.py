from __future__ import annotations

from .beijing import select_beijing_variant_binding
from .guangzhou import GuangzhouParser
from .parser_registry import ParserFamilyBinding, ParserRegistry
from .public_resource import PublicResourceParser
from .shanghai import select_shanghai_variant_binding
from .shandong import ShandongParser
from .shenzhen import ShenzhenParser
from .tianjin import TianjinParser
from .chongqing import ChongqingParser


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
            "shanghai": ParserFamilyBinding(
                family_id="shanghai",
                family_version="builtin/shanghai/v1",
                parser_cls=select_shanghai_variant_binding(None).parser_cls,
                variant_id="standard",
                variant_version="builtin/shanghai/standard/v1",
                page_kind="listing",
                selector=select_shanghai_variant_binding,
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
            "tianjin": ParserFamilyBinding(
                family_id="tianjin",
                family_version="builtin/tianjin/v1",
                parser_cls=TianjinParser,
                variant_id="standard",
                variant_version="builtin/tianjin/standard/v1",
                page_kind="listing",
            ),
            "shandong": ParserFamilyBinding(
                family_id="shandong",
                family_version="builtin/shandong/v1",
                parser_cls=ShandongParser,
                variant_id="standard",
                variant_version="builtin/shandong/standard/v1",
                page_kind="listing",
            ),
            "guangzhou": ParserFamilyBinding(
                family_id="guangzhou",
                family_version="builtin/guangzhou/v1",
                parser_cls=GuangzhouParser,
                variant_id="standard",
                variant_version="builtin/guangzhou/standard/v1",
                page_kind="listing",
            ),
            "public_resource": ParserFamilyBinding(
                family_id="public_resource",
                family_version="builtin/public_resource/v1",
                parser_cls=PublicResourceParser,
                variant_id="deal",
                variant_version="builtin/public_resource/deal/v1",
                page_kind="deal",
            ),
        }
    )


__all__ = ["build_builtin_registry"]
