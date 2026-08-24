from __future__ import annotations

import unittest
from unittest.mock import patch

from peap_core import DecodedDocument, SourceMatch
from peap_parsers.base import ParserContext, WebPageParser


class ParserRegistryContractTest(unittest.TestCase):
    def test_every_runtime_binding_resolves_to_a_concrete_family_parser(self) -> None:
        from peap.business_runtime import iter_source_business_bindings
        from peap_parsers.builtin_registry import build_builtin_registry

        registry = build_builtin_registry()
        bindings = tuple(iter_source_business_bindings())

        self.assertEqual(len(bindings), 32)
        for runtime_binding in bindings:
            with self.subTest(task_id=runtime_binding.task_id):
                parser_binding = registry.resolve(
                    SourceMatch(
                        source_id=runtime_binding.source_id,
                        page_kind=runtime_binding.record_family,
                        confidence=1.0,
                        status="matched",
                        reasons=("runtime binding coverage",),
                        classifier_version="source_classifier/v1",
                    )
                )

                self.assertEqual(parser_binding.page_kind, runtime_binding.record_family)
                self.assertIsInstance(parser_binding.parser_cls, type)
                self.assertTrue(issubclass(parser_binding.parser_cls, WebPageParser))

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

    def test_registry_resolves_deal_source_ids_to_legacy_family_bindings(self) -> None:
        from peap_parsers.builtin_registry import build_builtin_registry

        registry = build_builtin_registry()
        expectations = {
            "cbex": ("beijing", "builtin/beijing/v1", "builtin/beijing/deal/v1"),
            "sse": ("shanghai", "builtin/shanghai/v1", "builtin/shanghai/deal/v1"),
            "tpre": ("tianjin", "builtin/tianjin/v1", "builtin/tianjin/deal/v1"),
            "cquae": ("chongqing", "builtin/chongqing/v1", "builtin/chongqing/deal/v1"),
        }

        for source_id, (family_id, family_version, variant_version) in expectations.items():
            with self.subTest(source_id=source_id):
                binding = registry.resolve(
                    SourceMatch(
                        source_id=source_id,
                        page_kind="deal",
                        confidence=0.95,
                        status="matched",
                        reasons=("record_family=deal",),
                        classifier_version="source_classifier/v1",
                    )
                )

                self.assertEqual(binding.family_id, family_id)
                self.assertEqual(binding.family_version, family_version)
                self.assertEqual(binding.page_kind, "deal")
                self.assertEqual(binding.variant_id, "deal")
                self.assertEqual(binding.variant_version, variant_version)

    def test_registry_uses_page_kind_when_canonical_source_has_legacy_listing_alias(self) -> None:
        from peap_parsers.builtin_registry import build_builtin_registry

        registry = build_builtin_registry()

        listing = registry.resolve(
            SourceMatch(
                source_id="cbex",
                page_kind="listing",
                confidence=0.95,
                status="matched",
                reasons=("canonical source",),
                classifier_version="source_classifier/v1",
            )
        )
        deal = registry.resolve(
            SourceMatch(
                source_id="beijing",
                page_kind="deal",
                confidence=0.95,
                status="matched",
                reasons=("legacy source alias",),
                classifier_version="source_classifier/v1",
            )
        )

        self.assertEqual(listing.page_kind, "listing")
        self.assertEqual(listing.family_id, "beijing")
        self.assertEqual(deal.page_kind, "deal")
        self.assertEqual(deal.variant_id, "deal")

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

    def test_runtime_marks_unknown_source_and_page_kind_as_partial_parse(self) -> None:
        from peap_parsers.family_runtime import parse_document_with_registry
        from peap_parsers.parser_registry import ParserFamilyBinding, ParserRegistry

        class UnknownIdentityParser(WebPageParser):
            def parse(self):
                return self.build_parser_output(
                    standard_payload={
                        "project_code": "P-UNKNOWN-001",
                        "project_name": "未知来源项目",
                    },
                )

        registry = ParserRegistry(
            {
                "mystery": ParserFamilyBinding(
                    family_id="mystery",
                    family_version="fake-family/v1",
                    parser_cls=UnknownIdentityParser,
                    variant_id="unknown",
                    variant_version="fake-variant/v1",
                    page_kind="UNKNOWN",
                )
            }
        )
        document = DecodedDocument(
            snapshot_id="snap-runtime-unknown-source",
            document_kind="html",
            primary_text="未知来源项目",
            dom="<html><body>未知来源项目</body></html>",
            metadata={"source_url": "https://example.invalid/unknown/1"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="mystery",
            page_kind="UNKNOWN",
            confidence=0.0,
            status="unknown",
            reasons=("classifier could not identify source",),
            classifier_version="source_classifier/v1",
        )

        result = parse_document_with_registry(
            document=document,
            source_match=match,
            registry=registry,
            context=ParserContext(source_file="/tmp/mystery.html"),
        )

        self.assertEqual(result.recoverability, "partial")
        self.assertEqual(result.page_identity["source_id"], "mystery")
        self.assertEqual(result.page_identity["record_family"], "UNKNOWN")
        self.assertEqual(result.page_identity["page_kind"], "UNKNOWN")
        self.assertEqual(result.diagnostics[0].type, "parse_partial")
        self.assertEqual(result.diagnostics[0].stage, "parse")
        self.assertEqual(result.diagnostics[0].recoverability, "partial")
        self.assertIn("unknown source_id", result.diagnostics[0].message)
        self.assertTrue(any("unknown page_kind" in item.message for item in result.diagnostics))

    def test_runtime_selects_special_beijing_variant_from_document_content(self) -> None:
        from peap_parsers.base import ParserOutput
        from peap_parsers.builtin_registry import build_builtin_registry
        from peap_parsers.family_runtime import parse_document_with_registry

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

    def test_deal_parser_runtime_marks_missing_business_type_unknown(self) -> None:
        from peap_parsers.builtin_registry import build_builtin_registry
        from peap_parsers.family_runtime import parse_document_with_registry

        document = DecodedDocument(
            snapshot_id="snap-sse-deal-unknown-business",
            document_kind="html",
            primary_text="成交公告",
            dom="""
            <html>
              <body>
                <script id="deal_detail" type="application/json">
                  {
                    "project_code": "CJ202604300001",
                    "project_name": "成交公告",
                    "deal_date": "2026-04-30"
                  }
                </script>
              </body>
            </html>
            """,
            metadata={"source_url": "https://example.invalid/notice/deal/unknown"},
            decoder_version="snapshot_decoder/v1",
        )
        match = SourceMatch(
            source_id="sse",
            page_kind="deal",
            confidence=0.99,
            status="matched",
            reasons=("record_family=deal",),
            classifier_version="source_classifier/v1",
        )

        result = parse_document_with_registry(
            document=document,
            source_match=match,
            registry=build_builtin_registry(),
            context=ParserContext(source_file="/tmp/sse-deal-unknown.html"),
        )

        facts = {str(item["field"]): item["value"] for item in result.facts}
        self.assertEqual(result.page_identity["record_family"], "deal")
        self.assertEqual(facts["business_type"], "未知")

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
