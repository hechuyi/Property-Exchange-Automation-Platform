from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from peap_core import DecodedDocument, SourceMatch
from .base import WebPageParser


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
        binding = self._bindings.get(source_match.source_id)
        if binding is None:
            raise KeyError(source_match.source_id)
        if binding.selector is not None:
            return binding.selector(document)
        return binding


__all__ = ["ParserFamilyBinding", "ParserRegistry"]
