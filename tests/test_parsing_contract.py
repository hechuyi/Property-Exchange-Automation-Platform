from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.compat_compare import compare_data_fields
from peap.constants import (
    KEY_LISTING_TIMES,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_LISTED,
    TYPE_EQUITY_TRANSFER,
    TYPE_UNKNOWN,
)
from peap.output_mapping import map_standard_to_excel_payload
from peap.parsing import ParseError, SkipParse, build_parsed_project, parse_file
from peap.pipeline import ParserPipeline
from peap.standard_model import build_standard_project
from peap_parsers import BeijingParser, GuangzhouParser, ParserOutput, ShanghaiParser
from peap_parsers.base import ParserContext, WebPageParser
from peap_parsers.beijing_standard import BeijingStandardParser
from peap_parsers.shanghai_standard import ShanghaiStandardParser


class _DummyParser(WebPageParser):
    def parse(self) -> dict[str, object]:
        return {"source_file": self.source_file}


class ParsingContractTest(unittest.TestCase):
    def test_parse_file_recovers_cbex_otc_fixture_without_path_project_type_fallback(self) -> None:
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
        self.assertEqual(parsed.project_type, TYPE_UNKNOWN)
        self.assertEqual(parsed.standard_record.project_type, TYPE_UNKNOWN)

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

    def test_parse_file_cbex_otc_parser_output_can_recover_from_standard_payload_only(self) -> None:
        file_path = "C:\\temp\\cbex-otc-standard-only.html"

        class FakeParser(WebPageParser):
            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    compat_payload={},
                    standard_payload={
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
        self.assertEqual(parsed.project_type, TYPE_UNKNOWN)

    def test_parse_file_cbex_otc_recoverable_marker_without_identity_still_fails(self) -> None:
        file_path = "C:\\temp\\cbex-otc-missing-identity.html"

        class FakeParser(WebPageParser):
            def parse(self) -> ParserOutput:
                return self.build_parser_output(
                    compat_payload={},
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
                    compat_payload={},
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
        self.assertEqual(parsed.standard_record.project_type, TYPE_UNKNOWN)
        self.assertEqual(parsed.project_code, "P001")
        self.assertEqual(parsed.project_name, "\u793a\u4f8b\u9879\u76ee")
        self.assertEqual(parsed.status, STATUS_LISTED)
        self.assertEqual(parsed.project_type, TYPE_UNKNOWN)
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

    def test_parse_file_projects_compat_payload_from_standard_record_not_raw_parser_data(self) -> None:
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
                "project_type": TYPE_EQUITY_TRANSFER,
            },
        )

        with patch("peap.parsing.run_parser_subsystem", return_value=subsystem_result):
            parsed = parse_file(file_path)

        compat_payload = parsed.standard_record.to_legacy_payload(include_raw=True)
        self.assertEqual(parsed.data["项目名称"], "原始兼容名称")
        self.assertEqual(parsed.standard_record.project_name, "结构化名称")
        self.assertEqual(compat_payload["项目名称"], "结构化名称")
        self.assertEqual(compat_payload["转让方"], "结构化转让方")
        self.assertNotIn("神秘字段", compat_payload)

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
                "project_type": TYPE_EQUITY_TRANSFER,
            },
        )

        with patch("peap.parsing.run_parser_subsystem", return_value=subsystem_result):
            parsed = parse_file(file_path)

        compat_payload = parsed.standard_record.to_legacy_payload(include_raw=True)
        self.assertEqual(parsed.data["项目名称"], "兼容名称")
        self.assertEqual(parsed.standard_record.project_code, "P010")
        self.assertEqual(parsed.standard_record.project_name, "结构化名称")
        self.assertEqual(parsed.standard_record.seller, "结构化转让方")
        self.assertEqual(parsed.standard_record.status, STATUS_LISTED)
        self.assertEqual(parsed.standard_record.project_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(parsed.project_code, "P010")
        self.assertEqual(parsed.project_name, "结构化名称")
        self.assertEqual(parsed.status, STATUS_LISTED)
        self.assertEqual(parsed.project_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(compat_payload["项目名称"], "结构化名称")
        self.assertEqual(compat_payload["转让方"], "结构化转让方")

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

    def test_beijing_family_runtime_selector_marks_special_variant(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.beijing import BeijingSpecialParser, select_beijing_variant_binding

        document = DecodedDocument(
            snapshot_id="snap-beijing-special",
            document_kind="html",
            primary_text="北交互联",
            dom='''
            <html>
              <body>
                <textarea id="jsonobj">{"object": {"detail": {"projectcode": "CP2026BJ0001"}}}</textarea>
              </body>
            </html>
            ''',
            metadata={"source_url": "https://example.invalid/beijing/1"},
            decoder_version="snapshot_decoder/v1",
        )

        binding = select_beijing_variant_binding(document)

        self.assertEqual(binding.variant_id, "special")
        self.assertIs(binding.parser_cls, BeijingSpecialParser)

    def test_shanghai_family_runtime_selector_marks_special_variant(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.shanghai import ShanghaiSpecialParser, select_shanghai_variant_binding

        document = DecodedDocument(
            snapshot_id="snap-shanghai-special",
            document_kind="html",
            primary_text="综合招商 CP2026SH0001",
            dom='''
            <html>
              <head><title>上海联合产权交易所</title></head>
              <body>
                <div class="project_code">CP2026SH0001</div>
                <div>综合招商</div>
              </body>
            </html>
            ''',
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
        self.assertEqual(result.compat_payload["交易所"], "北交所")
        self.assertEqual(result.standard_payload["project_code"], "P020")
        self.assertEqual(result.standard_payload["project_name"], "北京项目")
        self.assertEqual(result.standard_payload["seller"], "北京转让方")
        self.assertEqual(result.standard_payload["exchange"], "北交所")

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
        self.assertEqual(result.compat_payload["交易所"], parser.EXCHANGE_NAME)
        self.assertEqual(result.standard_payload["project_code"], "G32026GD0001")
        self.assertEqual(result.standard_payload["project_name"], "广州项目")
        self.assertEqual(result.standard_payload["seller"], "广州转让方")
        self.assertEqual(result.standard_payload["exchange"], parser.EXCHANGE_NAME)

    def test_parsed_project_drives_compare_and_output_mapping(self) -> None:
        baseline = build_parsed_project(
            file_path="C:\\temp\\baseline.html",
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P001",
                "项目名称": "示例项目",
                "类型": "旧类型",
                "挂牌价格": "88.00",
                KEY_LISTING_TIMES: 1,
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
        )
        primary = build_parsed_project(
            file_path="C:\\temp\\primary.html",
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P001",
                "项目名称": "示例项目",
                "类型": "新类型",
                "挂牌价格": "108.00",
                KEY_LISTING_TIMES: 2,
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
        )

        diffs = compare_data_fields(
            file_path=primary.file_path,
            compare_fields=["类型", KEY_LISTING_TIMES],
            primary_profile="full",
            baseline_profile="ppe_ready",
            primary_data=primary,
            baseline_data=baseline,
        )
        mapped = map_standard_to_excel_payload(primary, "挂牌_股权转让.xlsx")

        self.assertEqual({diff["field"] for diff in diffs}, {"类型", KEY_LISTING_TIMES})
        self.assertTrue(all(diff["project_code"] == "P001" for diff in diffs))
        self.assertEqual(primary.standard_record.to_legacy_payload(include_raw=True)["挂牌价格"], "108.00")
        self.assertEqual(mapped["项目编号"], "P001")
        self.assertEqual(mapped["挂牌价格"], "108.00")
        self.assertEqual(mapped["挂牌次数"], 2)
        self.assertEqual(mapped["项目状态"], STATUS_LISTED)

    def test_build_standard_project_prefers_financing_amount_for_capital_projects(self) -> None:
        standard = build_standard_project(
            {
                KEY_PROJECT_TYPE: "增资扩股",
                "挂牌价格": 40000.0,
                "融资金额": "不超过40000万元",
            }
        )

        self.assertEqual(standard.price, "不超过40000万元")

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

    def test_standard_to_legacy_payload_emits_项目状态(self) -> None:
        """Standard model to_legacy_payload must emit 项目状态."""
        standard = build_standard_project({
            KEY_PROJECT_CODE: "P002",
            "项目名称": "legacy状态测试",
            KEY_STATUS: STATUS_LISTED,
        })
        legacy = standard.to_legacy_payload()

        # Must emit 项目状态, NOT 状态
        self.assertIn("项目状态", legacy, "legacy payload must emit 项目状态")
        self.assertNotIn("状态", legacy, "legacy payload must NOT emit 状态")
        self.assertEqual(legacy["项目状态"], STATUS_LISTED)

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


if __name__ == "__main__":
    unittest.main()
