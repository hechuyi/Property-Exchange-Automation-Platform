from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from peap_core import DecodedDocument, SourceMatch

from .base import WebPageParser

# The built-in registry has seven canonical listing sources.  Four of those
# sources also have historical dual keys (legacy listing name vs canonical
# deal code); keep only those cross-page bridges explicit.  Source-catalog
# display aliases such as ``guangzhou`` are normalized by classifier/ingest
# and must not become implicit parser-registry entry points.
_PAGE_KIND_SOURCE_ALIASES: Mapping[tuple[str, str], str] = {
    ("cbex", "listing"): "beijing",
    ("sse", "listing"): "shanghai",
    ("tpre", "listing"): "tianjin",
    ("cquae", "listing"): "chongqing",
    ("beijing", "deal"): "cbex",
    ("shanghai", "deal"): "sse",
    ("tianjin", "deal"): "tpre",
    ("chongqing", "deal"): "cquae",
}


@dataclass(frozen=True)
class ParserFamilyBinding:
    family_id: str
    family_version: str
    parser_cls: type[WebPageParser]
    variant_id: str
    variant_version: str
    page_kind: str
    selector: Callable[[DecodedDocument | None], "ParserFamilyBinding"] | None = None


class ParserRegistry:
    def __init__(self, bindings: Mapping[str, ParserFamilyBinding]):
        self._bindings = dict(bindings)

    def resolve(self, source_match: SourceMatch, document: DecodedDocument | None = None) -> ParserFamilyBinding:
        source_id = str(source_match.source_id or "").strip()
        page_kind = str(source_match.page_kind or "").strip()
        binding = self._bindings.get(source_id)

        # Resolve the explicit page kind before accepting a direct source-id
        # hit so a canonical listing cannot silently select the canonical
        # deal parser, or vice versa.  Only the four known dual-key pairs are
        # bridged here; the other three listing sources use their canonical
        # keys directly, and arbitrary source aliases remain rejected.
        if binding is None or (page_kind and binding.page_kind != page_kind):
            alias_id = _PAGE_KIND_SOURCE_ALIASES.get((source_id, page_kind))
            binding = self._bindings.get(alias_id) if alias_id else None
        if binding is None:
            raise KeyError((source_id, page_kind))
        if binding.selector is not None:
            return binding.selector(document)
        return binding


__all__ = ["ParserFamilyBinding", "ParserRegistry"]
