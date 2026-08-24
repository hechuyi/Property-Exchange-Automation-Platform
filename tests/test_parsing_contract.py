from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.constants import (
    KEY_LISTING_TIMES,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_LISTED,
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
    TYPE_UNKNOWN,
)
from peap.output_contract import (
    KIND_CAPITAL,
    KIND_PHYSICAL,
    KIND_PRE,
    get_output_columns_for_kind,
)
from peap.output_mapping import map_standard_to_excel_payload
from peap.parsing import ParseError, SkipParse, build_parsed_project, parse_file
from peap.pipeline import ParserPipeline
from peap.standard_model import build_standard_project, hydrate_standard_project
from peap_parsers import BeijingParser, GuangzhouParser, ParserOutput, ShanghaiParser
from peap_parsers.base import ParserContext, WebPageParser
from peap_parsers.beijing_standard import BeijingStandardParser
from peap_parsers.shandong import ShandongParser
from peap_parsers.shanghai_standard import ShanghaiStandardParser


class _DummyParser(WebPageParser):
    def parse(self) -> dict[str, object]:
        return {"source_file": self.source_file}


class ParsingContractTest(unittest.TestCase):
    def test_build_parsed_project_rejects_non_mapping_data(self) -> None:
        for data in (None, [], [("项目名称", "pair sequence")]):
            with self.subTest(data=data):
                with self.assertRaises(TypeError):
                    build_parsed_project(
                        file_path="fixture.html",
                        exchange="test",
                        encoding="utf-8",
                        data=data,
                    )

    def test_parse_file_does_not_infer_business_type_from_cbex_otc_fixture_without_explicit_binding(
        self,
    ) -> None:
        html = """
        <html>
          <head>
            <title>北交互联-报废设备一批</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>
            <textarea id="jsonobj">{
              "object": {
                "projectcode": "GR2026BJ1999001",
                "object": "报废设备一批",
                "publishdate": "2026-03-21",
                "expiredate": "2026-03-31"
              },
              "sellerlist": {
                "utrmcemsseller": [
                  {"sellername": "测试转让方"}
                ]
              }
            }</textarea>
          </body>
        </html>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_dir = os.path.join(temp_dir, "挂牌_实物资产")
            os.makedirs(fixture_dir, exist_ok=True)
            fixture_path = os.path.join(fixture_dir, "fixture.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write(html)

            parsed = parse_file(fixture_path)

        self.assertEqual(parsed.exchange, "beijing")
        self.assertEqual(parsed.project_code, "GR2026BJ1999001")
        self.assertEqual(parsed.project_name, "报废设备一批")
        self.assertEqual(parsed.business_type, TYPE_UNKNOWN)
        self.assertEqual(parsed.standard_record.business_type, TYPE_UNKNOWN)

    def test_parse_file_cbex_otc_fixture_without_recoverable_payload_fails_explicitly(self) -> None:
        html = """
        <html>
          <head>
            <title>北交互联</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>欢迎来到北交互联</body>
        </html>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = os.path.join(temp_dir, "cbex-otc-empty.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write(html)

            with self.assertRaises(ParseError) as context:
                parse_file(fixture_path)

        self.assertIn("cbex-otc-page-unrecoverable", str(context.exception))
        self.assertNotIsInstance(context.exception, SkipParse)

    def test_parse_file_rejects_symlink_source_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            real_path = os.path.join(temp_dir, "real.html")
            linked_path = os.path.join(temp_dir, "linked.html")
            with open(real_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>linked source</body></html>")
            os.symlink(real_path, linked_path)

            with self.assertRaisesRegex(ParseError, "source_snapshot_invalid"):
                parse_file(linked_path)

    def test_parse_file_cbex_otc_parser_output_can_recover_from_standard_payload_only(self) -> None:
        file_path = "C:\\temp\\cbex-otc-standard-only.html"

        class FakeParser(WebPageParser):
            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    standard_payload={
                        "business_type": TYPE_EQUITY_TRANSFER,
                        "project_code": "GR2026BJ2999001",
                        "project_name": "仅结构化字段项目",
                    },
                )

        html = """
        <html>
          <head>
            <title>北交互联-仅结构化字段项目</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body><textarea id="jsonobj">{}</textarea></body>
        </html>
        """

        with (
            patch(
                "peap.parser_subsystem.read_text_with_fallback",
                return_value=SimpleNamespace(content=html, encoding="utf-8"),
            ),
            patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
            patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
            patch(
                "peap.parser_subsystem.detect_category_from_path",
                return_value=(STATUS_LISTED, TYPE_UNKNOWN),
            ),
            patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
            patch("peap.parser_subsystem.apply_finance_fallback"),
            patch("peap.parser_subsystem.apply_group_fallback"),
        ):
            parsed = parse_file(file_path)

        self.assertEqual(parsed.project_code, "GR2026BJ2999001")
        self.assertEqual(parsed.project_name, "仅结构化字段项目")
        self.assertEqual(parsed.business_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.standard_record.business_type, TYPE_EQUITY_TRANSFER)

    def test_parse_file_preserves_explicit_business_type_without_path_or_code_override(
        self,
    ) -> None:
        file_path = "C:\\temp\\plain\\detail.html"

        class FakeParser(WebPageParser):
            project_code = ""

            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    standard_payload={
                        "project_code": self.project_code,
                        "project_name": "编码推断项目",
                        "business_type": TYPE_EQUITY_TRANSFER,
                    },
                )

        FakeParser.project_code = "GR2026SH1000428-2"
        with (
            patch(
                "peap.parser_subsystem.read_text_with_fallback",
                return_value=SimpleNamespace(content="<html></html>", encoding="utf-8"),
            ),
            patch("peap.parser_subsystem.detect_exchange", return_value="shanghai"),
            patch("peap.parser_subsystem.PARSER_MAP", {"shanghai": FakeParser}),
            patch(
                "peap.parser_subsystem.detect_category_from_path",
                return_value=(STATUS_LISTED, TYPE_UNKNOWN),
            ),
            patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
            patch("peap.parser_subsystem.apply_finance_fallback"),
            patch("peap.parser_subsystem.apply_group_fallback"),
        ):
            parsed = parse_file(file_path)

        self.assertEqual(parsed.project_code, "GR2026SH1000428-2")
        self.assertEqual(parsed.business_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.standard_record.business_type, TYPE_EQUITY_TRANSFER)

    def test_parse_file_cbex_otc_recoverable_marker_without_identity_still_fails(self) -> None:
        file_path = "C:\\temp\\cbex-otc-missing-identity.html"

        class FakeParser(WebPageParser):
            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    standard_payload={},
                )

        html = """
        <html>
          <head>
            <title>北交互联-存在可恢复标记但无身份字段</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>
            <textarea id="jsonobj">{
              "object": {
                "projectcode": "GR2026BJ3999001"
              }
            }</textarea>
          </body>
        </html>
        """

        with (
            patch(
                "peap.parser_subsystem.read_text_with_fallback",
                return_value=SimpleNamespace(content=html, encoding="utf-8"),
            ),
            patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
            patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
            patch(
                "peap.parser_subsystem.detect_category_from_path",
                return_value=(STATUS_LISTED, TYPE_UNKNOWN),
            ),
            patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
            patch("peap.parser_subsystem.apply_finance_fallback"),
            patch("peap.parser_subsystem.apply_group_fallback"),
        ):
            with self.assertRaises(ParseError) as context:
                parse_file(file_path)

        self.assertIn("cbex-otc-page-unrecoverable", str(context.exception))

    def test_batch_pipeline_marks_cbex_otc_unrecoverable_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = os.path.join(temp_dir, "cbex-otc-empty.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
                    <html>
                      <head>
                        <title>北交互联</title>
                        <meta name="keywords" content="北交互联" />
                      </head>
                      <body>欢迎来到北交互联</body>
                    </html>
                    """
                )

            pipeline = ParserPipeline(
                html_root=temp_dir,
                dry_run=True,
                parse_cache_enabled=False,
            )

            summary = pipeline.run()

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.succeeded, 0)
        self.assertEqual(summary.failed, 1)
        self.assertTrue(any("cbex-otc-page-unrecoverable" in message for message in summary.errors))

    def test_batch_pipeline_marks_pre_disclosure_archive_failure_as_failure(self) -> None:
        class FakeBatchWriter:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def upsert(self, *args, **kwargs) -> bool:
                return True

            def flush(self) -> dict[str, str]:
                return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = os.path.join(temp_dir, "pre-disclosure.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>pre disclosure</body></html>")
            parsed = build_parsed_project(
                file_path=fixture_path,
                exchange="beijing",
                encoding="utf-8",
                data={
                    KEY_PROJECT_CODE: "G32026BJ1000001-0",
                    "项目名称": "预披露归档失败测试",
                    KEY_PROJECT_TYPE: TYPE_PRE_DISCLOSURE,
                    KEY_STATUS: STATUS_LISTED,
                    "是否预披露": True,
                },
            )
            pipeline = ParserPipeline(
                html_root=temp_dir,
                dry_run=False,
                parse_cache_enabled=False,
            )

            with (
                patch("peap.pipeline.ExcelBatchWriter", FakeBatchWriter),
                patch("peap.pipeline.parse_file", return_value=parsed),
                patch("peap.pipeline._safe_move", side_effect=PermissionError("locked")),
            ):
                summary = pipeline.run()

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.succeeded, 0)
        self.assertEqual(summary.failed, 1)
        self.assertTrue(
            any("pre-disclosure-archive-failed" in message for message in summary.errors)
        )
        self.assertTrue(any("locked" in message for message in summary.errors))

    def test_batch_pipeline_surfaces_cbex_otc_identity_gate_failure_from_parse_layer(self) -> None:
        html = """
        <html>
          <head>
            <title>北交互联-存在可恢复标记但无身份字段</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>
            <textarea id="jsonobj">{
              "object": {
                "projectcode": "GR2026BJ3999001"
              }
            }</textarea>
          </body>
        </html>
        """

        class FakeParser(WebPageParser):
            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    standard_payload={},
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = os.path.join(temp_dir, "cbex-otc-missing-identity.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write(html)

            pipeline = ParserPipeline(
                html_root=temp_dir,
                dry_run=True,
                parse_cache_enabled=False,
            )

            with (
                patch(
                    "peap.parser_subsystem.read_text_with_fallback",
                    return_value=SimpleNamespace(content=html, encoding="utf-8"),
                ),
                patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
                patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
                patch(
                    "peap.parser_subsystem.detect_category_from_path",
                    return_value=(STATUS_LISTED, TYPE_UNKNOWN),
                ),
                patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
                patch("peap.parser_subsystem.apply_finance_fallback"),
                patch("peap.parser_subsystem.apply_group_fallback"),
            ):
                summary = pipeline.run()

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.succeeded, 0)
        self.assertEqual(summary.failed, 1)
        self.assertTrue(any("cbex-otc-page-unrecoverable" in message for message in summary.errors))

    def test_web_parser_exposes_context_backed_source_file(self) -> None:
        parser = _DummyParser(
            "<html></html>",
            context=ParserContext(source_file="  C:\\temp\\sample.html  "),
        )

        self.assertEqual(parser.source_file, "C:\\temp\\sample.html")

        parser.source_file = "C:\\temp\\updated.html"

        self.assertEqual(parser.context.source_file, "C:\\temp\\updated.html")
        self.assertEqual(parser.require_source_file(), "C:\\temp\\updated.html")

    def test_parse_file_passes_explicit_parser_context(self) -> None:
        file_path = "C:\\temp\\detail.html"

        class FakeParser(WebPageParser):
            captured_context: ParserContext | None = None

            def __init__(self, html_content: str, field_mapping=None, *, context=None):
                super().__init__(html_content, field_mapping, context=context)
                FakeParser.captured_context = context

            def parse(self) -> dict[str, object]:
                return {
                    KEY_PROJECT_CODE: "P001",
                    "\u9879\u76ee\u540d\u79f0": "\u793a\u4f8b\u9879\u76ee",
                    "source_file": self.source_file,
                }

        with (
            patch(
                "peap.parser_subsystem.read_text_with_fallback",
                return_value=SimpleNamespace(content="<html></html>", encoding="utf-8"),
            ),
            patch("peap.parser_subsystem.detect_exchange", return_value="shenzhen"),
            patch("peap.parser_subsystem.PARSER_MAP", {"shenzhen": FakeParser}),
            patch(
                "peap.parser_subsystem.detect_category_from_path",
                return_value=(STATUS_LISTED, TYPE_UNKNOWN),
            ),
            patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
            patch("peap.parser_subsystem.apply_finance_fallback"),
            patch("peap.parser_subsystem.apply_group_fallback"),
        ):
            parsed = parse_file(file_path)

        self.assertIsNotNone(FakeParser.captured_context)
        self.assertEqual(FakeParser.captured_context.source_file, file_path)
        self.assertEqual(parsed.file_path, file_path)
        self.assertEqual(parsed.exchange, "shenzhen")
        self.assertEqual(parsed.encoding, "utf-8")
        self.assertEqual(parsed.data["source_file"], file_path)
        self.assertEqual(parsed.standard_record.project_code, "P001")
        self.assertEqual(parsed.standard_record.project_name, "\u793a\u4f8b\u9879\u76ee")
        self.assertEqual(parsed.standard_record.status, STATUS_LISTED)
        self.assertEqual(parsed.standard_record.business_type, TYPE_UNKNOWN)
        self.assertEqual(parsed.project_code, "P001")
        self.assertEqual(parsed.project_name, "\u793a\u4f8b\u9879\u76ee")
        self.assertEqual(parsed.status, STATUS_LISTED)
        self.assertEqual(parsed.business_type, TYPE_UNKNOWN)
        self.assertFalse(parsed.is_pre_disclosure)

    def test_parse_file_delegates_to_parser_subsystem_facade(self) -> None:
        file_path = "C:\\temp\\detail.html"
        subsystem_result = SimpleNamespace(
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P200",
                "项目名称": "子系统项目",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_UNKNOWN,
            },
            standard_payload={
                "project_code": "P200",
                "project_name": "子系统项目",
            },
        )

        with patch(
            "peap.parsing.run_parser_subsystem",
            return_value=subsystem_result,
        ) as run_subsystem:
            parsed = parse_file(file_path)

        run_subsystem.assert_called_once_with(file_path)
        self.assertEqual(parsed.file_path, file_path)
        self.assertEqual(parsed.exchange, "shenzhen")
        self.assertEqual(parsed.encoding, "utf-8")
        self.assertEqual(parsed.project_code, "P200")
        self.assertEqual(parsed.project_name, "子系统项目")

    def test_parse_file_does_not_accept_compat_profile(self) -> None:
        """parse_file no longer accepts compat_profile argument."""
        file_path = r"C:\temp\detail.html"

        # parse_file should not accept compat_profile - it's been removed
        with self.assertRaises(TypeError):
            parse_file(file_path, compat_profile="ppe_ready")

    def test_parse_file_projects_compat_payload_from_standard_record_not_raw_parser_data(
        self,
    ) -> None:
        file_path = r"C:\temp\detail.html"
        subsystem_result = SimpleNamespace(
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P201",
                "项目名称": "原始兼容名称",
                "神秘字段": "should-not-leak",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
            standard_payload={
                "project_code": "P201",
                "project_name": "结构化名称",
                "seller": "结构化转让方",
                "status": STATUS_LISTED,
                "business_type": TYPE_EQUITY_TRANSFER,
            },
        )

        with patch("peap.parsing.run_parser_subsystem", return_value=subsystem_result):
            parsed = parse_file(file_path)

        standard_dict = parsed.standard_record.to_standard_dict()
        self.assertEqual(parsed.data["项目名称"], "原始兼容名称")
        self.assertEqual(parsed.standard_record.project_name, "结构化名称")
        self.assertEqual(standard_dict["project_name"], "结构化名称")
        self.assertEqual(standard_dict["seller"], "结构化转让方")
        self.assertEqual(parsed.standard_record.business_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.project_type, TYPE_EQUITY_TRANSFER)
        self.assertNotIn("project_type", standard_dict)
        with self.assertRaises(AttributeError):
            _ = parsed.standard_record.project_type
        self.assertNotIn("神秘字段", standard_dict)

    def test_parse_file_accepts_explicit_parser_output_contract(self) -> None:
        file_path = r"C:\temp\detail.html"
        subsystem_result = SimpleNamespace(
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P010",
                "项目名称": "兼容名称",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
            standard_payload={
                "project_code": "P010",
                "project_name": "结构化名称",
                "seller": "结构化转让方",
                "status": STATUS_LISTED,
                "business_type": TYPE_EQUITY_TRANSFER,
            },
        )

        with patch("peap.parsing.run_parser_subsystem", return_value=subsystem_result):
            parsed = parse_file(file_path)

        standard_dict = parsed.standard_record.to_standard_dict()
        self.assertEqual(parsed.data["项目名称"], "兼容名称")
        self.assertEqual(parsed.standard_record.project_code, "P010")
        self.assertEqual(parsed.standard_record.project_name, "结构化名称")
        self.assertEqual(parsed.standard_record.seller, "结构化转让方")
        self.assertEqual(parsed.standard_record.status, STATUS_LISTED)
        self.assertEqual(parsed.standard_record.business_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.project_code, "P010")
        self.assertEqual(parsed.project_name, "结构化名称")
        self.assertEqual(parsed.status, STATUS_LISTED)
        self.assertEqual(parsed.business_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.project_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(standard_dict["project_name"], "结构化名称")
        self.assertEqual(standard_dict["seller"], "结构化转让方")
        self.assertNotIn("project_type", standard_dict)
        with self.assertRaises(AttributeError):
            _ = parsed.standard_record.project_type

    def test_beijing_router_preserves_context_for_delegated_parser(self) -> None:
        file_path = "C:\\temp\\beijing_detail.html"

        class FakeDelegatedParser(WebPageParser):
            captured_context: ParserContext | None = None

            def __init__(self, html_content: str, field_mapping=None, *, context=None):
                super().__init__(html_content, field_mapping, context=context)
                FakeDelegatedParser.captured_context = context

            def parse(self) -> dict[str, object]:
                return {"source_file": self.source_file}

        parser = BeijingParser("<html></html>", context=ParserContext(source_file=file_path))

        with (
            patch.object(BeijingParser, "_load_json_data", return_value={}),
            patch.object(BeijingParser, "_is_special_template", return_value=False),
            patch("peap_parsers.beijing.BeijingStandardParser", FakeDelegatedParser),
        ):
            parsed = parser.parse()

        self.assertIsNotNone(FakeDelegatedParser.captured_context)
        self.assertEqual(FakeDelegatedParser.captured_context.source_file, file_path)
        self.assertEqual(parsed["source_file"], file_path)

    def test_shanghai_router_preserves_context_for_delegated_parser(self) -> None:
        file_path = "C:\\temp\\shanghai_detail.html"

        class FakeDelegatedParser(WebPageParser):
            captured_context: ParserContext | None = None

            def __init__(self, html_content: str, field_mapping=None, *, context=None):
                super().__init__(html_content, field_mapping, context=context)
                FakeDelegatedParser.captured_context = context

            def parse(self) -> dict[str, object]:
                return {"source_file": self.source_file}

        parser = ShanghaiParser("<html></html>", context=ParserContext(source_file=file_path))

        with (
            patch.object(ShanghaiParser, "_is_special_template", return_value=False),
            patch("peap_parsers.shanghai.ShanghaiStandardParser", FakeDelegatedParser),
        ):
            parsed = parser.parse()

        self.assertIsNotNone(FakeDelegatedParser.captured_context)
        self.assertEqual(FakeDelegatedParser.captured_context.source_file, file_path)
        self.assertEqual(parsed["source_file"], file_path)

    def test_shandong_parser_rejects_error_page_with_recommended_project_code_token(self) -> None:
        html = """
        <html>
          <head><title>访问出错</title></head>
          <body>推荐项目 YQCQ260002</body>
        </html>
        """

        parser = ShandongParser(html, context=ParserContext(source_file="unit.html"))

        self.assertEqual(parser.parse(), {})

    def test_beijing_family_runtime_selector_marks_special_variant(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.beijing import BeijingSpecialParser, select_beijing_variant_binding

        document = DecodedDocument(
            snapshot_id="snap-beijing-special",
            document_kind="html",
            primary_text="北交互联",
            dom="""
            <html>
              <body>
                <textarea id="jsonobj">{"object": {"detail": {"projectcode": "CP2026BJ0001"}}}</textarea>
              </body>
            </html>
            """,
            metadata={"source_url": "https://example.invalid/beijing/1"},
            decoder_version="snapshot_decoder/v1",
        )

        binding = select_beijing_variant_binding(document)

        self.assertEqual(binding.variant_id, "special")
        self.assertIs(binding.parser_cls, BeijingSpecialParser)

    def test_beijing_family_runtime_selector_raises_for_corrupt_jsonobj(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.beijing import select_beijing_variant_binding

        document = DecodedDocument(
            snapshot_id="snap-beijing-corrupt-jsonobj",
            document_kind="html",
            primary_text="北交互联",
            dom="""
            <html>
              <body>
                <textarea id="jsonobj">{"object":</textarea>
              </body>
            </html>
            """,
            metadata={"source_url": "https://example.invalid/beijing/corrupt"},
            decoder_version="snapshot_decoder/v1",
        )

        with self.assertRaises(json.JSONDecodeError):
            select_beijing_variant_binding(document)

    def test_shanghai_family_runtime_selector_marks_special_variant(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.shanghai import ShanghaiSpecialParser, select_shanghai_variant_binding

        document = DecodedDocument(
            snapshot_id="snap-shanghai-special",
            document_kind="html",
            primary_text="综合招商 CP2026SH0001",
            dom="""
            <html>
              <head><title>上海联合产权交易所</title></head>
              <body>
                <div class="project_code">CP2026SH0001</div>
                <div>综合招商</div>
              </body>
            </html>
            """,
            metadata={"source_url": "https://example.invalid/shanghai/1"},
            decoder_version="snapshot_decoder/v1",
        )

        binding = select_shanghai_variant_binding(document)

        self.assertEqual(binding.variant_id, "special")
        self.assertIs(binding.parser_cls, ShanghaiSpecialParser)

    def test_beijing_standard_parser_returns_parser_output_contract(self) -> None:
        parser = BeijingStandardParser("<html></html>")
        parser.data.update(
            {
                KEY_PROJECT_CODE: "P020",
                "项目名称": "北京项目",
                "转让方": "北京转让方",
                "挂牌价格": 88.0,
                "挂牌开始日期": "2026/03/01",
                "挂牌截止日期": "2026/03/31",
                "经办人": "张三",
                "受托机构": "北京机构",
            }
        )

        with (
            patch.object(parser, "extract_json_data", return_value={}),
            patch.object(parser, "_parse_from_html"),
        ):
            result = parser.parse()

        self.assertIsInstance(result, ParserOutput)
        self.assertEqual(result.standard_payload.get("exchange"), "北交所")
        self.assertEqual(result.standard_payload["project_code"], "P020")
        self.assertEqual(result.standard_payload["project_name"], "北京项目")
        self.assertEqual(result.standard_payload["seller"], "北京转让方")
        self.assertEqual(result.standard_payload["exchange"], "北交所")

    def test_beijing_standard_parser_infers_physical_asset_project_type_from_ta_code(self) -> None:
        html = """
        <textarea id="jsonobj">
        {
          "utrmcemsproject": {
            "projectcode": "TA2026BJ1000944-2",
            "object": "无锡市锡山区瑚畔名筑21号楼98单元801室",
            "objectprice": "168.708965",
            "publishdate": "2026-04-24",
            "expiredate": "2026-05-11",
            "type": "A18001"
          },
          "houselist": {
            "utrmcemshouse": [{"houseaddress": "无锡市锡山区瑚畔名筑"}]
          }
        }
        </textarea>
        """

        result = BeijingStandardParser(html).parse()

        self.assertEqual(result.standard_payload["project_type"], "实物资产")
        self.assertEqual(result.standard_payload["industry"], "房屋土地")

    def test_beijing_standard_parser_does_not_treat_inspection_contact_as_seller(self) -> None:
        html = """
        <html>
          <head><title>北交互联-北京鑫海韵通商业大楼有限公司固定资产一批</title></head>
          <body>
            <p class="bd_detail_num">项目编号：GR2026BJ1005301</p>
            <table>
              <tr>
                <td class="xmtd1">资产描述</td>
                <td class="xmtd2">
                  本项目为强制踏勘项目，由转让方统一组织现场踏勘。
                  踏勘联系人：李禹羲。联系电话：13521080811。
                </td>
              </tr>
            </table>
          </body>
        </html>
        """

        result = BeijingStandardParser(html).parse()

        self.assertEqual(result.standard_payload["project_code"], "GR2026BJ1005301")
        self.assertEqual(result.standard_payload["seller"], "北京鑫海韵通商业大楼有限公司")
        self.assertEqual(result.standard_payload["contact"], "李禹羲。联系电话：13521080811。")

    def test_beijing_standard_parser_infers_physical_seller_from_strict_title_prefix(self) -> None:
        html = """
        <html>
          <head><title>北交互联-国家能源集团宁夏煤业有限责任公司拟处置废电子设备</title></head>
          <body><p class="bd_detail_num">项目编号：GR2026BJ1000977-2</p></body>
        </html>
        """

        result = BeijingStandardParser(html).parse()

        self.assertEqual(result.standard_payload["seller"], "国家能源集团宁夏煤业有限责任公司")

    def test_beijing_standard_parser_does_not_infer_equity_seller_from_title(self) -> None:
        html = """
        <html>
          <head><title>北交互联-北京鑫海韵通商业大楼有限公司100%股权</title></head>
          <body>
            <p class="bd_detail_num">项目编号：G32026BJ1005301</p>
          </body>
        </html>
        """

        result = BeijingStandardParser(html).parse()

        self.assertNotIn("seller", result.standard_payload)

    def test_beijing_standard_parser_prefers_physical_asset_hqname_over_placeholder_or_compound_approval_unit(
        self,
    ) -> None:
        cases = [
            ("中材科技股份有限公司", "中国建材集团有限公司", "-", "中国建材集团有限公司"),
            (
                "北京经纬建元建筑工程检测有限公司",
                "北京城建集团有限责任公司",
                "北京城建集团有限责任公司、北京城建二建设工程有限公司",
                "北京城建集团有限责任公司",
            ),
        ]
        for seller_name, hq_name, authorize_unit, expected_group in cases:
            with self.subTest(seller_name=seller_name):
                html = f"""
                <textarea id="jsonobj">
                {{
                  "utrmcemsproject": {{
                    "projectcode": "GR2026BJ1002397",
                    "object": "{seller_name}设备144台（套）",
                    "objectprice": "15.1777",
                    "publishdate": "2026-04-24",
                    "expiredate": "2026-05-06",
                    "type": "A18004"
                  }},
                  "sellerlist": {{
                    "utrmcemsseller": [
                      {{
                        "sellername": "{seller_name}",
                        "hqname": "{hq_name}",
                        "authorizeunit": "{authorize_unit}",
                        "sellercode": "91110108786166378R",
                        "sellereconomytypezw": "国有独资公司（企业）/国有全资企业"
                      }}
                    ]
                  }}
                }}
                </textarea>
                """

                result = BeijingStandardParser(html).parse()

                self.assertEqual(result.standard_payload["seller"], seller_name)
                self.assertEqual(result.standard_payload["group_name"], expected_group)

    def test_beijing_standard_parser_preserves_anonymous_pre_disclosure_supervision_fields(
        self,
    ) -> None:
        html = """
        <textarea id="jsonobj">
        {
          "sellerlist": {
            "utrgcemsseller": [
              {
                "sellername": "某企业",
                "sellercode": "91110106MA7H5M9J58"
              }
            ]
          },
          "utrgcemspreproject": {
            "projectcode": "G32026BJ1000182-0",
            "object": "人形机器人（上海）有限公司25%股权",
            "publishdate": "2026-04-30",
            "expiredate": "2026-06-01"
          },
          "utrgcemspreobject": {
            "industrycodezw": "软件和信息技术服务业"
          }
        }
        </textarea>
        <table>
          <tr><th>统一社会信用代码或组织机构代码</th><td>91310000MAD8RLKP46</td></tr>
        </table>
        <table id="sellercondition">
          <tr><th rowspan="3">基本情况</th><th>转让方名称</th><td colspan="3">某企业</td></tr>
          <tr><th>经济类型</th><td colspan="3">国有控股企业</td></tr>
          <tr><th>持有产（股）权比例</th><td>51%</td><th>拟转让产（股）权比例</th><td>25%</td></tr>
          <tr><th>国资监管机构</th><td colspan="3">国务院国资委监管</td></tr>
          <tr><th>国家出资企业或主管部门名称</th><td colspan="3"></td></tr>
          <tr><th>统一社会信用代码或组织机构代码</th><td colspan="3"></td></tr>
        </table>
        """

        result = BeijingStandardParser(html).parse()

        self.assertEqual(result.standard_payload["seller"], "某企业")
        self.assertEqual(result.standard_payload["project_type"], "预披露")
        self.assertEqual(result.standard_payload["state_asset_supervisor"], "国务院国资委监管")
        self.assertEqual(result.standard_payload["economic_type"], "国有控股企业")
        self.assertEqual(result.standard_payload["seller_credit_code"], "91110106MA7H5M9J58")

    def test_beijing_standard_parser_raises_for_present_but_corrupt_jsonobj(self) -> None:
        html = """
        <html>
          <body>
            <textarea id="jsonobj">{"utrmcemsproject":</textarea>
            <table>
              <tr><td class="projectcode">GR2026BJ1000001</td></tr>
              <tr><td class="object">DOM fallback project</td></tr>
            </table>
          </body>
        </html>
        """

        with self.assertRaises(json.JSONDecodeError):
            BeijingStandardParser(html).parse()

    def test_beijing_standard_parser_raises_for_present_but_corrupt_sidecar(self) -> None:
        html = """
        <textarea id="jsonobj">
        {
          "utrmcemsproject": {
            "projectcode": "TA2026BJ1000944-2",
            "object": "北京实物资产项目",
            "objectprice": "168.708965",
            "publishdate": "2026-04-24",
            "expiredate": "2026-05-11",
            "type": "A18001"
          }
        }
        </textarea>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "case.html")
            sidecar_path = os.path.join(temp_dir, "case.json")
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                handle.write('{"row":')

            parser = BeijingStandardParser(
                html,
                context=ParserContext(source_file=html_path),
            )

            with self.assertRaises(json.JSONDecodeError):
                parser.parse()

    def test_guangzhou_parser_returns_parser_output_contract(self) -> None:
        parser = GuangzhouParser("<html></html>")
        parser.data.update(
            {
                parser.KEY_PROJECT_CODE: "G32026GD0001",
                parser.KEY_PROJECT_NAME: "广州项目",
                parser.KEY_SELLER: "广州转让方",
                parser.KEY_LISTING_PRICE: 108.0,
                parser.KEY_LISTING_START: "2026/03/01",
                parser.KEY_LISTING_END: "2026/03/31",
                parser.KEY_CONTACT: "李四",
                parser.KEY_AGENCY: "广州机构",
            }
        )

        with (
            patch.object(parser, "_extract_script_vars", return_value={}),
            patch.object(parser, "_extract_from_top_summary"),
            patch.object(parser, "_extract_from_tables"),
            patch.object(parser, "_extract_seller_ratio"),
            patch.object(parser, "_extract_multi_seller_text", return_value=""),
            patch.object(parser, "_supplement_from_remote_tabs"),
            patch.object(parser, "_normalize_group_industry"),
            patch.object(parser, "_extract_profit_prefer_annual_from_tables", return_value=None),
        ):
            result = parser.parse()

        self.assertIsInstance(result, ParserOutput)
        self.assertEqual(result.standard_payload.get("exchange"), parser.EXCHANGE_NAME)
        self.assertEqual(result.standard_payload["project_code"], "G32026GD0001")
        self.assertEqual(result.standard_payload["project_name"], "广州项目")
        self.assertEqual(result.standard_payload["seller"], "广州转让方")
        self.assertEqual(result.standard_payload["exchange"], parser.EXCHANGE_NAME)

    def test_guangzhou_parser_extracts_t_prefixed_guangdong_project_code(self) -> None:
        parser = GuangzhouParser(
            """
            <html>
              <head><title>惠州国云锦和置业有限公司100%股权及债权项目</title></head>
              <body>
                <div class="project-detail-cont-title">项目编号：T32026GD0000001-4</div>
                <table><tr><td>项目名称</td><td>惠州国云锦和置业有限公司100%股权及债权项目</td></tr></table>
              </body>
            </html>
            """
        )

        result = parser.parse()

        self.assertEqual(result.standard_payload["project_code"], "T32026GD0000001-4")

    def test_guangzhou_parser_uses_project_title_on_rendered_detail_page(self) -> None:
        parser = GuangzhouParser(
            """
            <html>
              <head><title>北京大唐永盛科技发展有限公司25.56%股权</title></head>
              <body><div class="project-detail-cont-title">项目编号：G32026GD0000081</div></body>
            </html>
            """
        )

        result = parser.parse()

        self.assertEqual(
            result.standard_payload["project_name"], "北京大唐永盛科技发展有限公司25.56%股权"
        )

    def test_guangzhou_parser_extracts_current_rendered_price_and_disclosure_dates(self) -> None:
        parser = GuangzhouParser(
            """
            <html><body>
              <a href="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=160131">项目中心</a>
              <div class="project-price-box">
                <span class="project-price-name">转让底价：</span>
                <span class="project-price-num"><span class="fs30">756</span> 万元</span>
                <span class="project-price-name">保证金：</span>
                <span class="project-price-num"><span class="fs30">54</span> 万元</span>
              </div>
              <table>
                <tr><td>披露起始日期</td><td>2026-06-25</td></tr>
                <tr><td>披露期满日期</td><td>2026-07-01</td></tr>
              </table>
            </body></html>
            """
        )

        result = parser.parse()

        self.assertEqual(result.standard_payload["business_type"], "股权转让")
        self.assertEqual(result.standard_payload["price"], 756.0)
        self.assertEqual(result.standard_payload["start_date"], "2026/06/25")
        self.assertEqual(result.standard_payload["end_date"], "2026/07/01")

    def test_guangzhou_parser_rejects_lossy_local_tab_cache_before_supplementing_fields(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = os.path.join(temp_dir, "snapshot.html")
            cache_path = os.path.join(temp_dir, "snapshot_contracts.html")
            with open(cache_path, "wb") as handle:
                handle.write(
                    b"<table><tr><td>&#32463;&#21150;&#20154;</td><td>Li\xffSi</td></tr></table>"
                )

            parser = GuangzhouParser(
                "<html><body></body></html>",
                context=ParserContext(source_file=source_file),
            )

            with self.assertRaises(UnicodeDecodeError):
                parser._supplement_from_remote_tabs({})

            self.assertNotIn(parser.KEY_CONTACT, parser.data)

    def test_build_standard_project_prefers_financing_amount_for_capital_projects(self) -> None:
        standard = build_standard_project(
            {
                KEY_PROJECT_TYPE: "增资扩股",
                "挂牌价格": 40000.0,
                "融资金额": "不超过40000万元",
            }
        )

        self.assertEqual(standard.price, "不超过40000万元")

    def test_build_standard_project_maps_real_deal_source_aliases(self) -> None:
        standard = build_standard_project(
            {
                "transactionPrice": "990",
                "assessmentValue": "1100",
                "transferBasePrice": "950",
                "TZFMC": "投资方甲",
                "ZJCZE": "5000",
                "ZZFQYMC": "增资企业甲",
                "ZZZHBL": "35%",
                "stockPercent": "20%",
            }
        )

        self.assertEqual(standard.deal_price, "990")
        self.assertEqual(standard.valuation, "1100")
        self.assertEqual(standard.reserve_price, "950")
        self.assertEqual(standard.investor_name, "投资方甲")
        self.assertEqual(standard.total_investment_amount, "5000")
        self.assertEqual(standard.capital_company_name, "增资企业甲")
        self.assertEqual(standard.holding_ratio, "35%")
        self.assertEqual(standard.share_ratio, "20%")

    def test_standard_project_builders_reject_explicit_none_input(self) -> None:
        with self.assertRaises(TypeError):
            build_standard_project(None)

        with self.assertRaises(TypeError):
            hydrate_standard_project(None)

    def test_shanghai_standard_parser_uses_shareholder_structure_as_inferred_ratio(self) -> None:
        html = """
        <div class="project_code">项目编号：G32026SH1000043-0</div>
        <div class="project_xmmc">示例项目</div>
        <table>
          <tr><td class="table_label">转让方名称</td><td>上海松江交通投资运营集团有限公司</td></tr>
          <tr><td class="table_label">转让方名称</td><td>上海锦江汽车服务有限公司</td></tr>
        </table>
        <table>
          <tr>
            <td class="table_label">序号</td>
            <td class="table_label">股东名称（按持股比例多少排序）</td>
            <td class="table_label">持股比例（%）</td>
          </tr>
          <tr><td>1</td><td>上海松江交通投资运营集团有限公司</td><td>50</td></tr>
          <tr><td>2</td><td>上海锦江汽车服务有限公司</td><td>50</td></tr>
        </table>
        """
        parser = ShanghaiStandardParser(html)

        parsed = parser.parse()

        self.assertEqual(
            parsed["转让方"],
            "上海松江交通投资运营集团有限公司(50%) 上海锦江汽车服务有限公司(50%)",
        )
        self.assertIn("多转让方未明确各转让方拟转让比例，请人工复核", parsed["备注"])

    def test_shanghai_standard_parser_infers_equity_transfer_from_project_code(self) -> None:
        html = """
        <div class="project_code">项目编号：Q32026SH1000003</div>
        <div class="project_xmmc">上海旭升保险经纪有限公司100%股权</div>
        <table>
          <tr><td>转让方名称</td><td>联仁健康医疗大数据科技股份有限公司</td></tr>
        </table>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "股权转让")

    def test_shanghai_standard_parser_infers_physical_asset_from_gr_code(self) -> None:
        html = """
        <div class="project_code">项目编号：GR2026SH1000324-4</div>
        <div class="project_xmmc">淮安市淮阴医院有限公司部分资产（一台双源CT机）</div>
        <table>
          <tr><td>转让方名称</td><td>淮安市淮阴医院有限公司</td></tr>
        </table>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "实物资产")

    def test_shanghai_standard_parser_treats_dash_zero_code_as_pre_disclosure(self) -> None:
        html = """
        <div class="project_code">项目编号：Q32026SH1000010-0</div>
        <div class="project_xmmc">逸成景轩（天津）企业管理有限公司100%股权</div>
        <div class="mfooter">产权转让 企业增资 资产转让</div>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "预披露")

    def test_shanghai_standard_parser_ignores_footer_capital_keywords_for_equity_pages(
        self,
    ) -> None:
        html = """
        <a href="https://www.suaee.com/suaeeHome/#/projectdetail/jymhchanquan?xmid=113380">详情</a>
        <div class="project_code">项目编号：Q32026SH1000003</div>
        <div class="project_xmmc">上海旭升保险经纪有限公司100%股权</div>
        <div class="mfooter">产权转让 企业增资 资产转让</div>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "股权转让")

    def test_shanghai_standard_parser_ignores_footer_capital_keywords_for_physical_pages(
        self,
    ) -> None:
        html = """
        <a href="https://www.suaee.com/suaeeHome/#/projectdetail/jymhzichan?xmid=113477">详情</a>
        <div class="project_code">项目编号：GR2026SH1000264-5</div>
        <div class="project_xmmc">淮安市淮阴医院有限公司部分资产（骨密度仪）</div>
        <div class="mfooter">产权转让 企业增资 资产转让</div>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "实物资产")

    def test_shanghai_standard_parser_reads_new_layout_physical_header(self) -> None:
        html = """
        <div class="project-detail-top">
            <div class="title">星科金朋半导体(江阴)有限公司部分资产（报废设备资产包1）</div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：GR2026SH1000524</span></div>
        </div>
        <div class="project-price-box">
            <span class="project-price-name">转让底价：</span>
            <span class="project-price-num"><span class="fs30">1.469</span><span>万元</span></span>
        </div>
        <div class="xmjs-infor-box">
            <div class="infor-date">
                <ul>
                    <li><div class="text">公告开始</div><div class="numb">2026-04-09</div></li>
                    <li><div class="text">公告截止</div><div class="numb">2026-04-16</div></li>
                </ul>
            </div>
            <div class="label-infor"><span class="name">标的所在地区</span><span class="cont">江苏省 无锡市</span></div>
        </div>
        <div class="detail-info">
            <table class="table-info">
                <tr>
                    <th>交易机构</th>
                    <td>
                        <span class="text">项目负责人</span>
                        <span class="text">汤佳锋</span>
                        <span class="text">021-62657272-385、16602122306</span>
                    </td>
                </tr>
            </table>
        </div>
        <table class="xm-tab">
            <tr><td>转让方名称</td><td>星科金朋半导体(江阴)有限公司</td></tr>
            <tr><td>所属集团或主管部门名称</td><td>中国华润有限公司</td></tr>
        </table>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目编号"], "GR2026SH1000524")
        self.assertEqual(
            parsed["项目名称"], "星科金朋半导体(江阴)有限公司部分资产（报废设备资产包1）"
        )
        self.assertEqual(parsed["挂牌价格"], 1.469)
        self.assertEqual(parsed["挂牌开始日期"], "2026/04/09")
        self.assertEqual(parsed["挂牌截止日期"], "2026/04/16")
        self.assertEqual(parsed["所在地区"], "江苏省 无锡市")
        self.assertEqual(parsed["经办人"], "汤佳锋")
        self.assertEqual(parsed["转让方"], "星科金朋半导体(江阴)有限公司")
        self.assertEqual(parsed["隶属集团"], "中国华润有限公司")
        self.assertEqual(parsed["交易所"], "上交所")

    def test_shanghai_standard_parser_reads_physical_asset_category_from_new_layout_section_header(
        self,
    ) -> None:
        html = """
        <a href="https://www.suaee.com/suaeeHome/#/projectdetail/jymhzichan?xmid=200001">详情</a>
        <div class="project-detail-top">
            <div class="title">深圳航空有限责任公司江苏分公司部分资产（一辆丰田牌兰德酷路泽越野车）</div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：GR2026SH1000572</span></div>
        </div>
        <li>
            <div class="tab-title"><div class="fl">资产公告信息</div><div class="clear"></div></div>
            <table class="xm-tab">
                <tr><td class="xmtd1" colspan="4">机动车</td></tr>
                <tr><td class="xmtd1">名称</td><td class="xmtd2">丰田牌兰德酷路泽越野车</td></tr>
            </table>
        </li>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目类型"], "实物资产")
        self.assertEqual(parsed["所属行业"], "机动车")

    def test_shanghai_standard_parser_merges_new_layout_physical_asset_section_headers(
        self,
    ) -> None:
        html = """
        <a href="https://www.suaee.com/suaeeHome/#/projectdetail/jymhzichan?xmid=200002">详情</a>
        <div class="project-detail-top">
            <div class="title">昆明配售电有限公司部分资产（房屋建筑物、机器设备及土地使用权）</div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：GR2026SH1000578</span></div>
        </div>
        <li>
            <div class="tab-title"><div class="fl">资产公告信息</div><div class="clear"></div></div>
            <table class="xm-tab">
                <tr><td class="xmtd1">资产描述</td><td class="xmtd2">房屋建筑物、机器设备及土地使用权</td></tr>
                <tr><td class="xmtd1" colspan="4">不动产</td></tr>
                <tr><td class="xmtd1">不动产登记号</td><td class="xmtd2">云(2026）呈贡区不动产权第0082381号</td></tr>
                <tr><td class="xmtd1" colspan="4">土地</td></tr>
                <tr><td class="xmtd1">土地面积(平方米)</td><td class="xmtd2">8185</td></tr>
                <tr><td class="xmtd1" colspan="4">机械设备</td></tr>
                <tr><td class="xmtd1">名称</td><td class="xmtd2">详见资产清单</td></tr>
            </table>
        </li>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["所属行业"], "不动产、土地、机械设备")

    def test_shanghai_standard_parser_reads_new_layout_capital_header(self) -> None:
        html = """
        <div class="project-detail-top">
            <div class="title">上海临港司南生命科技有限公司增资项目</div>
            <div class="detail-top-label"><i>企业增资</i><i>正式披露</i><span>项目编号：G62025SH1000031</span></div>
        </div>
        <div class="project-price-box">
            <span class="project-price-name">拟募集资金总额：</span>
            <span class="project-price-num">800万元</span>
        </div>
        <div class="xmjs-infor-box">
            <div class="infor-date">
                <ul>
                    <li><div class="text">披露开始</div><div class="numb">2026-02-10</div></li>
                    <li><div class="text">披露结束</div><div class="numb">2026-03-10</div></li>
                </ul>
            </div>
            <div class="label-infor"><span class="name">所在地区</span><span class="cont">上海 闵行区</span></div>
        </div>
        <div class="detail-info">
            <table class="table-info">
                <tr>
                    <th>受托机构</th>
                    <td>
                        <span class="text">上海继祥企业管理咨询有限公司</span>
                        <span class="text">徐经理</span>
                        <span class="text">13381751186</span>
                    </td>
                </tr>
                <tr>
                    <th>交易机构</th>
                    <td>
                        <span class="text">项目负责人</span>
                        <span class="text">陆文奕</span>
                        <span class="text">62657272-381</span>
                    </td>
                </tr>
            </table>
        </div>
        <table class="xm-tab">
            <tr><td>拟募集资金总额（万元）</td><td>800万元</td></tr>
            <tr><td>基本情况</td></tr>
            <tr><td>名称</td><td>上海临港司南生命科技有限公司</td><td>住所</td><td>上海市闵行区</td></tr>
        </table>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(parsed["项目编号"], "G62025SH1000031")
        self.assertEqual(parsed["项目名称"], "上海临港司南生命科技有限公司增资项目")
        self.assertEqual(parsed["融资金额"], "800万元")
        self.assertEqual(parsed["挂牌价格"], 800.0)
        self.assertEqual(parsed["融资方"], "上海临港司南生命科技有限公司")
        self.assertEqual(parsed["挂牌开始日期"], "2026/02/10")
        self.assertEqual(parsed["挂牌截止日期"], "2026/03/10")
        self.assertEqual(parsed["所在地区"], "上海 闵行区")
        self.assertEqual(parsed["受托机构"], "上海继祥企业管理咨询有限公司")
        self.assertEqual(parsed["经办人"], "陆文奕")
        self.assertNotIn("转让方", parsed)

    def test_shanghai_standard_parser_handles_two_column_shareholder_headers(self) -> None:
        html = """
        <div class="project_code">项目编号：G32026SH1000043-0</div>
        <div class="project_xmmc">示例项目</div>
        <table>
            <tr><td class="table_label">转让方名称</td><td>上海松江交通投资运营集团有限公司</td></tr>
            <tr><td class="table_label">转让方名称</td><td>上海锦江汽车服务有限公司</td></tr>
        </table>
        <table>
            <tr>
                <td class="table_label">股东名称</td>
                <td class="table_label">持股比例(%)</td>
            </tr>
            <tr><td>上海松江交通投资运营集团有限公司</td><td>50</td></tr>
            <tr><td>上海锦江汽车服务有限公司</td><td>50</td></tr>
        </table>
        """

        parsed = ShanghaiStandardParser(html).parse()

        self.assertEqual(
            parsed["转让方"],
            "上海松江交通投资运营集团有限公司(50%) 上海锦江汽车服务有限公司(50%)",
        )


class StatusKeyContractTest(unittest.TestCase):
    """Tests to verify 项目状态 is the single flat status key."""

    def test_map_standard_to_excel_payload_emits_项目状态_not_状态(self) -> None:
        """Output mapping must emit 项目状态, not 状态."""
        parsed = build_parsed_project(
            file_path="C:\\temp\\status_key_test.html",
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P001",
                "项目名称": "状态键测试项目",
                "类型": "国资",
                "挂牌价格": "88.00",
                KEY_LISTING_TIMES: 1,
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
        )
        mapped = map_standard_to_excel_payload(parsed, "挂牌_股权转让.xlsx")

        # Must emit 项目状态, NOT 状态
        self.assertIn("项目状态", mapped, "output mapping must emit 项目状态")
        self.assertNotIn("状态", mapped, "output mapping must NOT emit 状态")
        self.assertEqual(mapped["项目状态"], STATUS_LISTED)

    def test_export_projection_emits_项目状态(self) -> None:
        """Export projection must emit 项目状态 for status field."""
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = {
            "canonical_fields": {
                "project_code": "P003",
                "project_name": "导出状态测试",
                "project_type": "股权转让",
                "status": "挂牌中",
                "start_date": "2026-03-21",
                "price": "108.00",
                "seller": "测试卖方",
            }
        }
        payload, _ = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        # Must emit 项目状态, NOT 状态
        self.assertIn("项目状态", payload, "export projection must emit 项目状态")
        self.assertNotIn("状态", payload, "export projection must NOT emit 状态")
        self.assertEqual(payload["项目状态"], "挂牌中")


class ListingReferenceSpreadsheetContractTest(unittest.TestCase):
    def test_output_contract_matches_reference_listing_spreadsheets(self) -> None:
        self.assertEqual(
            get_output_columns_for_kind(KIND_PHYSICAL),
            [
                "ID",
                "类型",
                "项目编号",
                "隶属集团",
                "转让方",
                "项目名称",
                "挂牌价格（万元）",
                "资产类别",
                "挂牌开始日期",
                "挂牌截止日期",
                "受托机构",
                "交易所",
                "经办人",
                "挂牌次数",
                "备注",
            ],
        )
        self.assertEqual(
            get_output_columns_for_kind(KIND_CAPITAL),
            [
                "ID",
                "项目编号",
                "隶属集团",
                "融资方",
                "项目名称",
                "融资金额",
                "持股比例",
                "所属行业",
                "披露开始日期",
                "披露截止日期",
                "受托机构",
                "交易所",
                "经办人",
                "近一年净利润（万）",
                "所在地区",
                "备注",
            ],
        )
        self.assertEqual(
            get_output_columns_for_kind(KIND_PRE),
            [
                "ID",
                "类型",
                "项目编号",
                "隶属集团",
                "转让方",
                "项目名称",
                "所属行业",
                "披露开始日期",
                "披露截止日期",
                "受托机构",
                "交易所",
                "经办人",
                "近一年净利润（万）",
                "总资产（万）",
                "挂牌次数",
                "备注",
            ],
        )

    def test_map_standard_to_excel_payload_uses_reference_listing_headers(self) -> None:
        physical = build_standard_project(
            {
                KEY_PROJECT_TYPE: TYPE_PHYSICAL_ASSET,
                KEY_PROJECT_CODE: "GF2025SH1000254-3",
                "项目名称": "实物资产项目",
                "类型": "国资",
                "转让方": "甘肃八冶房地产开发集团有限公司",
                "挂牌价格（万元）": "570.394",
                "资产类别": "不动产",
                "挂牌开始日期": "2025/12/18",
                "挂牌截止日期": "2026/02/27",
                KEY_STATUS: STATUS_LISTED,
            }
        )
        physical_payload = map_standard_to_excel_payload(physical, "挂牌_实物资产.xlsx")
        self.assertEqual(physical_payload["挂牌价格（万元）"], "570.394")
        self.assertEqual(physical_payload["资产类别"], "不动产")
        self.assertNotIn("挂牌价格", physical_payload)
        self.assertNotIn("所属行业", physical_payload)

        capital = build_standard_project(
            {
                KEY_PROJECT_TYPE: TYPE_CAPITAL_INCREASE,
                KEY_PROJECT_CODE: "G62025BJ1000073",
                "项目名称": "增资扩股项目",
                "类型": "国资",
                "融资方": "京东方科技集团股份有限公司",
                "融资金额": "不超过30000万元",
                "持股比例": "不超过16.7%",
                "披露开始日期": "2025/11/18",
                "披露截止日期": "2026/03/03",
                KEY_LISTING_TIMES: 3,
                KEY_STATUS: STATUS_LISTED,
            }
        )
        capital_payload = map_standard_to_excel_payload(capital, "挂牌_增资扩股.xlsx")
        self.assertEqual(capital_payload["融资金额"], "不超过30000万元")
        self.assertNotIn("融资金额（万）", capital_payload)
        self.assertNotIn("类型", capital_payload)
        self.assertNotIn("挂牌次数", capital_payload)

        pre_disclosure = build_standard_project(
            {
                KEY_PROJECT_TYPE: TYPE_PRE_DISCLOSURE,
                KEY_PROJECT_CODE: "G32026CQ1000019-0",
                "项目名称": "预披露项目",
                "挂牌价格": "108.00",
                "预披露开始日期": "2026/02/28",
                "预披露截止日期": "2026/03/26",
                KEY_STATUS: STATUS_LISTED,
            }
        )
        pre_payload = map_standard_to_excel_payload(pre_disclosure, "挂牌_预披露.xlsx")
        self.assertNotIn("挂牌价格", pre_payload)
        self.assertEqual(pre_payload["披露开始日期"], "2026/02/28")
        self.assertEqual(pre_payload["披露截止日期"], "2026/03/26")


if __name__ == "__main__":
    unittest.main()
