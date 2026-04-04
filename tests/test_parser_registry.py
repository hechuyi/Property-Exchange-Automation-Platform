from __future__ import annotations

import unittest
from unittest.mock import patch

from peap_core import DecodedDocument, SourceMatch
from peap_parsers.base import ParserContext, WebPageParser


class ParserRegistryContractTest(unittest.TestCase):
    def test_registry_resolves_family_from_source_match_without_parser_map(self) -> None:
        from peap_parsers.builtin_registry import build_builtin_registry

        registry = build_builtin_registry()

        binding = registry.resolve(
            SourceMatch(
                source_id="beijing",
                page_kind="listing",
                confidence=0.95,
                status="matched",
                reasons=("matched beijing title",),
                classifier_version="source_classifier/v1",
            )
        )

        self.assertEqual(binding.family_id, "beijing")
        self.assertEqual(binding.family_version, "builtin/beijing/v1")
        self.assertEqual(binding.variant_id, "standard")

    def test_runtime_executes_registered_parser_and_returns_page_parse_result(self) -> None:
        from peap_parsers.family_runtime import parse_document_with_registry
        from peap_parsers.parser_registry import ParserFamilyBinding, ParserRegistry

        class FakeParser(WebPageParser):
            def parse(self):
                return self.build_parser_output(
                    standard_payload={
                        "project_code": "P001",
                        "project_name": "示例项目",
                    },
                )

        registry = ParserRegistry(
            {
                "fake-source": ParserFamilyBinding(
                    family_id="fake-source",
                    family_version="fake-family/v1",
                    parser_cls=FakeParser,
                    variant_id="detail",
                    variant_version="fake-variant/v1",
                    page_kind="detail",
                )
            }
        )
        document = DecodedDocument(
            snapshot_id="snap-runtime-1",
            document_kind="html",
            primary_text="示例项目",
            dom="<html><body>示例项目</body></html>",
            metadata={"source_url": "https://example.invalid/detail/1"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="fake-source",
            page_kind="detail",
            confidence=0.9,
            status="matched",
            reasons=("test fixture",),
            classifier_version="source_classifier/v1",
        )

        result = parse_document_with_registry(
            document=document,
            source_match=match,
            registry=registry,
            context=ParserContext(source_file="/tmp/fake.html"),
        )

        self.assertEqual(result.snapshot_id, "snap-runtime-1")
        self.assertEqual(result.source_match.source_id, "fake-source")
        self.assertEqual(result.parser_family_id, "fake-source")
        self.assertEqual(result.variant_id, "detail")
        self.assertEqual(result.page_identity["project_code"], "P001")
        self.assertEqual(result.page_identity["page_url"], "https://example.invalid/detail/1")
        self.assertEqual(result.facts[0]["field"], "project_code")
        self.assertEqual(result.facts[0]["value"], "P001")
        self.assertEqual(result.facts[1]["field"], "project_name")
        self.assertEqual(result.facts[1]["value"], "示例项目")
        self.assertEqual(result.recoverability, "none")
        self.assertEqual(result.diagnostics, ())

    def test_runtime_emits_typed_partial_diagnostic_for_missing_project_code(self) -> None:
        from peap_parsers.family_runtime import parse_document_with_registry
        from peap_parsers.parser_registry import ParserFamilyBinding, ParserRegistry

        class MissingIdentityParser(WebPageParser):
            def parse(self):
                return self.build_parser_output(
                    standard_payload={
                        "project_name": "只有名称",
                    },
                )

        registry = ParserRegistry(
            {
                "fake-source": ParserFamilyBinding(
                    family_id="fake-source",
                    family_version="fake-family/v1",
                    parser_cls=MissingIdentityParser,
                    variant_id="detail",
                    variant_version="fake-variant/v1",
                    page_kind="detail",
                )
            }
        )
        document = DecodedDocument(
            snapshot_id="snap-runtime-2",
            document_kind="html",
            primary_text="只有名称",
            dom="<html><body>只有名称</body></html>",
            metadata={"source_url": "https://example.invalid/detail/2"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="fake-source",
            page_kind="detail",
            confidence=0.9,
            status="matched",
            reasons=("test fixture",),
            classifier_version="source_classifier/v1",
        )

        result = parse_document_with_registry(
            document=document,
            source_match=match,
            registry=registry,
            context=ParserContext(source_file="/tmp/fake.html"),
        )

        self.assertEqual(result.recoverability, "partial")
        self.assertEqual(result.diagnostics[0].type, "parse_partial")
        self.assertEqual(result.diagnostics[0].stage, "parse")
        self.assertEqual(result.diagnostics[0].recoverability, "partial")

    def test_runtime_selects_special_beijing_variant_from_document_content(self) -> None:
        from peap_parsers.builtin_registry import build_builtin_registry
        from peap_parsers.family_runtime import parse_document_with_registry
        from peap_parsers.base import ParserOutput

        document = DecodedDocument(
            snapshot_id="snap-beijing-runtime-special",
            document_kind="html",
            primary_text="北交互联",
            dom='''
            <html>
              <body>
                <textarea id="jsonobj">{"object": {"detail": {"projectcode": "CP2026BJ0001"}}}</textarea>
              </body>
            </html>
            ''',
            metadata={"source_url": "https://example.invalid/beijing/special"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="beijing",
            page_kind="listing",
            confidence=0.95,
            status="matched",
            reasons=("matched beijing title",),
            classifier_version="source_classifier/v1",
        )

        with patch("peap_parsers.beijing_special.BeijingSpecialParser.parse", return_value=ParserOutput(standard_payload={"project_code": "CP2026BJ0001"})):
            result = parse_document_with_registry(
                document=document,
                source_match=match,
                registry=build_builtin_registry(),
                context=ParserContext(source_file="/tmp/beijing-special.html"),
            )

        self.assertEqual(result.variant_id, "special")
        self.assertEqual(result.variant_version, "builtin/beijing/special/v1")
        self.assertEqual(result.page_identity["project_code"], "CP2026BJ0001")

    def test_runtime_emits_typed_unrecoverable_diagnostic_for_missing_identity_and_name(self) -> None:
        from peap_parsers.family_runtime import parse_document_with_registry
        from peap_parsers.parser_registry import ParserFamilyBinding, ParserRegistry

        class EmptyParser(WebPageParser):
            def parse(self):
                return self.build_parser_output(standard_payload={})

        registry = ParserRegistry(
            {
                "fake-source": ParserFamilyBinding(
                    family_id="fake-source",
                    family_version="fake-family/v1",
                    parser_cls=EmptyParser,
                    variant_id="detail",
                    variant_version="fake-variant/v1",
                    page_kind="detail",
                )
            }
        )
        document = DecodedDocument(
            snapshot_id="snap-runtime-3",
            document_kind="html",
            primary_text="",
            dom="<html><body></body></html>",
            metadata={"source_url": "https://example.invalid/detail/3"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="fake-source",
            page_kind="detail",
            confidence=0.9,
            status="matched",
            reasons=("test fixture",),
            classifier_version="source_classifier/v1",
        )

        result = parse_document_with_registry(
            document=document,
            source_match=match,
            registry=registry,
            context=ParserContext(source_file="/tmp/fake-empty.html"),
        )
        self.assertEqual(result.recoverability, "unrecoverable")
        self.assertEqual(result.diagnostics[0].type, "parse_unrecoverable")
        self.assertEqual(result.diagnostics[0].stage, "parse")
        self.assertEqual(result.diagnostics[0].recoverability, "unrecoverable")


if __name__ == "__main__":
    unittest.main()
