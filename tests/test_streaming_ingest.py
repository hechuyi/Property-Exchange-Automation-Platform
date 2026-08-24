from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from peap.output_mapping import map_standard_to_excel_payload
from peap.standard_model import build_standard_project
from peap.streaming_ingest import (
    StreamingIngestDependencies,
    StreamingIngestRunner,
    _assemble_ingested_record,
    _build_canonical_projection_payload,
    _build_registry_parse_payload,
    _canonical_archive_target,
    _default_parse_file,
    _extract_default_parse_metadata,
    _merge_record_payloads,
    copy_snapshot_to_archive,
    materialize_snapshot_to_archive,
)
from peap.streaming_models import IngestedRecord, ItemSavedPayload, PostProcessFinding
from peap.streaming_store import StreamingStore
from peap.submission_layout import resolve_submission_snapshot_target
from peap_core.record_identity import pick_reprocess_evidence_path


class SkipParse(RuntimeError):
    pass


class StreamingIngestRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        self.archive_root = os.path.join(self.temp_dir.name, "submission")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.html_path = os.path.join(self.temp_dir.name, "raw.html")
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>ok</body></html>")
        os.makedirs(f"{os.path.splitext(self.html_path)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(self.html_path)[0]}_files/style.css", "w", encoding="utf-8") as handle:
            handle.write("body{}")

    def test_init_rejects_explicit_non_object_rules_config(self) -> None:
        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            rules_config=None,
        )
        self.assertEqual(runner.rules_config, {})

        for rules_config in (False, [], "not-an-object"):
            with self.subTest(rules_config=rules_config):
                with self.assertRaisesRegex(ValueError, "rules_config"):
                    StreamingIngestRunner(
                        store=self.store,
                        archive_root=self.archive_root,
                        rules_config=rules_config,  # type: ignore[arg-type]
                    )

    def test_ingest_ready_record_copies_into_month_archive(self) -> None:
        def fake_parser(file_path: str):
            self.assertEqual(file_path, self.html_path)
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "上海电气集团恒联企业发展有限公司35%股权",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "上海电气集团恒联企业发展有限公司",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        self.assertEqual(result["state"], "ready")
        self.assertIn("2026年3月", result["archive_path"])
        self.assertTrue(os.path.isfile(result["archive_path"]))
        self.assertTrue(os.path.isdir(f"{os.path.splitext(result['archive_path'])[0]}_files"))

        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["postprocess_payload"]["类型"], "国资")
        self.assertEqual(latest[0]["canonical_record"]["canonical_fields"]["status"], "挂牌")

    def test_ingest_listing_missing_export_field_enters_field_missing(self) -> None:
        def fake_parser(_file_path: str):
            return {
                "项目编号": "G32026GD1000998",
                "项目名称": "挂牌价格缺失项目",
                "项目类型": "股权转让",
                "项目状态": "挂牌中",
                "交易所": "guangdong",
                "挂牌开始日期": "2026-08-18",
                "转让方": "广东测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **_kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="guangdong",
                extra={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "guangdong",
                },
            )
        )

        self.assertEqual(result["state"], "field_missing")
        self.assertEqual(
            [item["type"] for item in result["findings"]],
            ["canonical_field_missing"],
        )
        self.assertEqual(result["findings"][0]["evidence"]["missing_fields"], ["price"])

    def test_refresh_listing_recomputes_export_readiness_before_ready(self) -> None:
        def fake_parser(_file_path: str):
            return {
                "项目编号": "G32026GD1000999",
                "项目名称": "回刷价格缺失项目",
                "项目类型": "股权转让",
                "项目状态": "挂牌中",
                "交易所": "guangdong",
                "挂牌开始日期": "2026-08-18",
                "转让方": "广东测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **_kwargs: (
                    {**dict(payload), "挂牌价格": "100.00"},
                    [],
                ),
            ),
        )
        created = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="guangdong",
                extra={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "guangdong",
                },
            )
        )
        self.assertEqual(created["state"], "ready")

        runner.dependencies = StreamingIngestDependencies(
            parser=fake_parser,
            postprocess=lambda payload, **_kwargs: (dict(payload), []),
        )
        refreshed = runner.refresh_postprocess(str(created["record_id"]))

        self.assertEqual(refreshed["state"], "field_missing")
        stored = self.store.get_record(str(created["record_id"]))
        self.assertEqual(stored["state"], "field_missing")
        self.assertEqual(
            [item["type"] for item in stored["findings"]],
            ["canonical_field_missing"],
        )

    def test_parse_failed_record_preserves_item_scope_identity(self) -> None:
        def fake_parser(_file_path: str):
            raise RuntimeError("exchange detect failed")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(parser=fake_parser),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="guangdong",
                extra={
                    "source_id": "guangdong",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "project_type_fallback": "股权转让",
                },
            )
        )

        self.assertEqual(result["state"], "parse_failed")
        latest = self.store.iter_latest_records(states=["parse_failed"])
        self.assertEqual(len(latest), 1)
        record = latest[0]
        self.assertEqual(record["record_family"], "listing")
        self.assertEqual(record["business_id"], "equity_transfer")
        self.assertEqual(record["source_identity_json"]["source_id"], "guangdong")
        self.assertEqual(record["source_identity_json"]["business_id_hint"], "equity_transfer")
        self.assertEqual(record["source_identity_json"]["business_label_hint"], "股权转让")
        self.assertEqual(record["source_identity_json"]["original_evidence_path"], self.html_path)

    def test_ingest_rejects_symlink_source_before_parser(self) -> None:
        symlink_path = os.path.join(self.temp_dir.name, "linked-source.html")
        os.symlink(self.html_path, symlink_path)
        parser_calls: list[str] = []

        def fake_parser(file_path: str):
            parser_calls.append(file_path)
            raise AssertionError("parser must not receive a symlink source")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(parser=fake_parser),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=symlink_path,
                exchange="shanghai",
                project_code="SYMLINK-SOURCE-001",
            )
        )

        self.assertEqual(parser_calls, [])
        self.assertEqual(result["state"], "parse_failed")
        self.assertEqual(result["error_type"], "source_snapshot_invalid")
        self.assertIn("regular non-symlink file", result["error_message"])

    def test_parse_failed_record_rejects_invalid_item_candidate_tokens_before_persisting(self) -> None:
        def fake_parser(_file_path: str):
            raise RuntimeError("exchange detect failed")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(parser=fake_parser),
        )

        for candidate_tokens in (
            False,
            "not-a-list",
            {"token": "project_id:BAD"},
            ("project_id:BAD",),
            {"project_id:BAD"},
        ):
            with self.subTest(candidate_tokens=candidate_tokens):
                with self.assertRaisesRegex((TypeError, ValueError), "candidate_tokens"):
                    runner.ingest(
                        ItemSavedPayload(
                            source_file=self.html_path,
                            exchange="guangdong",
                            extra={"candidate_tokens": candidate_tokens},
                        )
                    )

        self.assertEqual(self.store.iter_latest_records(states=["parse_failed"]), [])

    def test_default_parse_file_preserves_parser_fields_and_normalizes_standard_only_fields(self) -> None:
        parsed = SimpleNamespace(
            data={
                "资产类别": "机械设备",
                "挂牌截止日期": "2026/03/16",
                "转让方": "测试转让方",
            },
            standard_record=SimpleNamespace(
                source_type="国资",
                to_standard_dict=lambda: {
                    "project_code": "GR2026SH1000001",
                    "project_name": "测试项目",
                    "business_type": "实物资产",
                    "status": "挂牌",
                    "exchange": "shanghai",
                    "source_type": "国资",
                    "seller": "测试转让方",
                    "deal_method": "",
                    "buyer_name": "",
                    "group_name": "",
                    "industry": "机械设备",
                    "region": "",
                    "contact": "王炜",
                    "agency": "上海联交所",
                    "price": 1.23,
                    "valuation": None,
                    "start_date": "2026/03/02",
                    "end_date": "2026/03/16",
                    "profit": 12.5,
                    "asset_total": 88.0,
                    "share_ratio": "",
                    "listing_times": 3,
                    "is_pre_disclosure": False,
                    "remark": "标准字段备注",
                },
            ),
            project_code="GR2026SH1000001",
            project_name="测试项目",
            project_type="实物资产",
            status="挂牌",
            exchange="shanghai",
        )

        with patch("peap.parsing.parse_file", return_value=parsed):
            payload = _default_parse_file("/tmp/fake.html")

        self.assertEqual(payload["项目编号"], "GR2026SH1000001")
        self.assertEqual(payload["项目名称"], "测试项目")
        self.assertEqual(payload["项目类型"], "实物资产")
        self.assertEqual(payload["项目状态"], "挂牌")
        self.assertEqual(payload["交易所"], "shanghai")
        self.assertEqual(payload["资产类别"], "机械设备")
        self.assertEqual(payload["经办人"], "王炜")
        self.assertEqual(payload["受托机构"], "上海联交所")
        self.assertEqual(payload["近一年净利润（万）"], 12.5)
        self.assertEqual(payload["总资产（万）"], 88.0)
        self.assertNotIn("挂牌次数", payload)
        self.assertEqual(payload["备注"], "标准字段备注")

    def test_default_parse_file_rejects_explicit_non_object_parsed_data(self) -> None:
        parsed = SimpleNamespace(
            data=False,
            standard_record=SimpleNamespace(to_standard_dict=lambda: {}),
            project_code="",
            project_name="",
            project_type="",
            status="",
            exchange="",
        )

        with (
            patch("peap.streaming_ingest._build_registry_parse_payload", return_value={}),
            patch("peap.parsing.parse_file", return_value=parsed),
        ):
            with self.assertRaisesRegex(ValueError, "data must be an object, got bool"):
                _default_parse_file(self.html_path)

    def test_default_parse_file_rejects_symlink_before_registry_parser(self) -> None:
        symlink_path = os.path.join(self.temp_dir.name, "default-linked-source.html")
        os.symlink(self.html_path, symlink_path)
        with patch(
            "peap.streaming_ingest._build_registry_parse_payload",
            side_effect=AssertionError("registry parser must not receive a symlink source"),
        ):
            with self.assertRaisesRegex(ValueError, "regular non-symlink file"):
                _default_parse_file(symlink_path)

    def test_default_parse_file_surfaces_registry_parser_exception_without_legacy_fallback(self) -> None:
        class RegistryParserError(RuntimeError):
            pass

        with (
            patch(
                "peap.streaming_ingest._build_registry_parse_payload",
                side_effect=RegistryParserError("registry parser failed"),
            ),
            patch(
                "peap.parsing.parse_file",
                side_effect=AssertionError("legacy parser should not run after registry parser failure"),
            ),
        ):
            with self.assertRaisesRegex(RegistryParserError, "registry parser failed"):
                _default_parse_file(self.html_path)

    def test_registry_parse_payload_preserves_parse_partial_diagnostic(self) -> None:
        html = """
        <html>
          <head><title>北京产权交易所-只有名称</title></head>
          <body>
            <table><tr><td class="object">只有名称</td></tr></table>
          </body>
        </html>
        """
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        payload = _build_registry_parse_payload(file_path=self.html_path, content=html)

        diagnostics = payload.get("parse_diagnostics")
        self.assertIsInstance(diagnostics, list)
        self.assertEqual(diagnostics[0]["type"], "parse_partial")
        self.assertEqual(diagnostics[0]["recoverability"], "partial")

    def test_default_parse_file_accepts_guangdong_rendered_detail_page(self) -> None:
        html = """
        <html>
          <head>
            <title>惠州国云锦和置业有限公司100%股权及债权项目</title>
            <meta name="keywords" content="广东联合产权交易中心" />
            <meta name="description" content="广东联合产权交易中心" />
            <script>
              window.COMPANY = '广东联合产权交易中心有限责任公司';
              window.TITLE = '广东联合产权交易中心';
            </script>
          </head>
          <body>
            <a href="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=158467">项目中心</a>
            <div class="project-detail-cont-title">项目编号：T32026GD0000001-4</div>
          </body>
        </html>
        """
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        payload = _default_parse_file(self.html_path)

        self.assertEqual(payload["source_id"], "guangdong")
        self.assertEqual(payload["record_family"], "listing")
        self.assertEqual(payload["项目编号"], "T32026GD0000001-4")
        self.assertEqual(payload["项目名称"], "惠州国云锦和置业有限公司100%股权及债权项目")

    def test_ingest_does_not_use_item_project_code_when_parser_project_code_is_missing(self) -> None:
        def fake_parser(_file_path: str):
            return {
                "项目名称": "缺编号解析项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "测试转让方",
                "类型": "国资",
                "parse_diagnostics": [
                    {
                        "severity": "warn",
                        "type": "parse_partial",
                        "message": "missing project code",
                        "stage": "parse",
                        "recoverability": "partial",
                    }
                ],
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **_kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="shanghai",
                project_code="G32026PAYLOAD999",
            )
        )

        self.assertEqual(result["project_code"], "")
        self.assertEqual(result["state"], "pending_review")
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        record = latest[0]
        self.assertEqual(record["project_code"], "")
        self.assertEqual(record["source_identity_json"].get("project_code"), "")
        diagnostic_types = {str(item.get("type") or "") for item in record["canonical_record"]["diagnostics"]}
        self.assertIn("parse_partial", diagnostic_types)

    def test_default_parse_metadata_rejects_corrupt_same_stem_deal_sidecar_json(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "corrupt_deal_sidecar.html")
        html = (
            "<html><body>"
            '<script id="deal_metadata" type="application/json">'
            '{"record_family":"deal","source_id":"html-fallback","source_url":"https://example.test/fallback"}'
            "</script>"
            "</body></html>"
        )
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            handle.write("{bad json")

        with self.assertRaisesRegex(ValueError, "deal_sidecar_invalid_json"):
            _extract_default_parse_metadata(html, file_path=snapshot_path)

    def test_default_parse_metadata_rejects_non_object_same_stem_deal_sidecar_json(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "non_object_deal_sidecar.html")
        html = (
            "<html><body>"
            '<script id="deal_metadata" type="application/json">'
            '{"record_family":"deal","source_id":"html-fallback","source_url":"https://example.test/fallback"}'
            "</script>"
            "</body></html>"
        )
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump([], handle)

        with self.assertRaisesRegex(ValueError, "deal_sidecar_invalid_schema"):
            _extract_default_parse_metadata(html, file_path=snapshot_path)

    def test_default_parse_metadata_rejects_symlink_same_stem_sidecar_json(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "symlink_deal_sidecar.html")
        html = (
            "<html><body>"
            '<script id="deal_metadata" type="application/json">'
            '{"record_family":"deal","source_id":"html-fallback"}'
            "</script>"
            "</body></html>"
        )
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        external_sidecar = os.path.join(self.temp_dir.name, "external-deal-sidecar.json")
        with open(external_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"metadata": {"record_family": "deal"}}, handle)
        os.symlink(external_sidecar, os.path.splitext(snapshot_path)[0] + ".json")

        with self.assertRaisesRegex(ValueError, "deal_sidecar_symlink"):
            _extract_default_parse_metadata(html, file_path=snapshot_path)

    def test_default_parse_metadata_keeps_missing_deal_sidecar_optional(self) -> None:
        html = (
            "<html><body>"
            '<script id="deal_metadata" type="application/json">'
            '{"record_family":"deal","source_id":"html-fallback","source_url":"https://example.test/fallback"}'
            "</script>"
            "</body></html>"
        )

        metadata = _extract_default_parse_metadata(
            html,
            file_path=os.path.join(self.temp_dir.name, "missing_sidecar.html"),
        )

        self.assertEqual(metadata["record_family"], "deal")
        self.assertEqual(metadata["source_id"], "html-fallback")
        self.assertEqual(metadata["source_url"], "https://example.test/fallback")

    def test_default_parse_metadata_keeps_missing_deal_metadata_script_optional(self) -> None:
        metadata = _extract_default_parse_metadata("<html><body>ok</body></html>")

        self.assertEqual(metadata, {})

    def test_non_deal_sidecar_metadata_requires_matching_hash_or_bytes_integrity(self) -> None:
        html = "<html><body>listing snapshot</body></html>"
        html_bytes = html.encode("utf-8")
        cases = (
            ("missing", {}, False),
            ("bytes_mismatch", {"archive_content_bytes": len(html_bytes) + 1}, False),
            ("bytes_match", {"archive_content_bytes": len(html_bytes)}, True),
        )
        for label, integrity, should_trust in cases:
            with self.subTest(label=label):
                snapshot_path = os.path.join(self.temp_dir.name, f"non_deal_{label}.html")
                with open(snapshot_path, "wb") as handle:
                    handle.write(html_bytes)
                with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            **integrity,
                            "metadata": {
                                "record_family": "listing",
                                "source_id": "sse",
                                "source_url": "https://example.test/cross-exchange",
                                "project_code": "G32026SH9999999",
                            },
                        },
                        handle,
                    )

                metadata = _extract_default_parse_metadata(html, file_path=snapshot_path)

                if should_trust:
                    self.assertEqual(metadata["source_id"], "sse")
                    self.assertEqual(metadata["record_family"], "listing")
                else:
                    self.assertEqual(metadata, {})

    def test_deal_sidecar_rejects_every_explicit_non_success_save_status(self) -> None:
        project_code = "G32026CQ1000062"
        html = (
            "<html><head><title>重庆产权交易网</title></head><body>"
            "<div>交易结果公示</div>"
            f"<span>{project_code}</span><span>成交日期 2026-07-02</span>"
            "</body></html>"
        )
        html_bytes = html.encode("utf-8")
        cases = (
            ("root_pending", "pending", None),
            ("metadata_failed", None, "failed"),
            ("metadata_interrupted", "complete", "interrupted"),
        )
        for label, root_status, metadata_status in cases:
            with self.subTest(label=label):
                snapshot_path = os.path.join(self.temp_dir.name, f"deal_status_{label}.html")
                with open(snapshot_path, "wb") as handle:
                    handle.write(html_bytes)
                sidecar: dict[str, object] = {
                    "archive_content_sha256": hashlib.sha256(html_bytes).hexdigest(),
                    "metadata": {
                        "record_family": "deal",
                        "source_id": "cquae",
                        "project_code": project_code,
                    },
                }
                if root_status is not None:
                    sidecar["save_status"] = root_status
                if metadata_status is not None:
                    metadata = dict(sidecar["metadata"])
                    metadata["save_status"] = metadata_status
                    sidecar["metadata"] = metadata
                with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
                    json.dump(sidecar, handle)

                self.assertEqual(
                    _extract_default_parse_metadata(html, file_path=snapshot_path),
                    {},
                )

    def test_default_parse_metadata_rejects_corrupt_deal_metadata_script(self) -> None:
        html = (
            "<html><body>"
            '<script id="deal_metadata" type="application/json">'
            '{"record_family":"deal",'
            "</script>"
            "</body></html>"
        )

        with self.assertRaises(json.JSONDecodeError):
            _extract_default_parse_metadata(html)

    def test_registry_parse_ignores_conflicting_same_stem_deal_sidecar_for_listing_html(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "shenzhen_listing_with_old_deal_sidecar.html")
        html = """
        <html><head><title>深圳联合产权交易所</title></head><body>
          <div class="title" id="js_projectName">当前挂牌项目(国资监测编号G32026SZ1000101)</div>
          <span id="gpqsrq">2026-08-01</span>
          <span id="gpqmrq">2026-08-31</span>
        </body></html>
        """
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "metadata": {
                        "record_family": "deal",
                        "source_id": "sse",
                        "project_code": "G32026SH9999999",
                        "project_name": "陈旧成交项目",
                    },
                    "detail_payload": {"project_code": "G32026SH9999999"},
                },
                handle,
                ensure_ascii=False,
            )

        payload = _build_registry_parse_payload(file_path=snapshot_path, content=html)

        self.assertEqual(payload["record_family"], "listing")
        self.assertEqual(payload["source_id"], "shenzhen")
        self.assertEqual(payload["项目编号"], "G32026SZ1000101")
        self.assertEqual(payload["项目名称"], "当前挂牌项目")

    def test_registry_parse_uses_integrity_bound_tpre_result_sidecar_over_split_table_headers(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "tpre_result_split_table.html")
        html = """
        <html><head><title>天津交易集团</title></head><body>
          <div id="tab-result" class="el-tabs__item is-active" aria-selected="true">结果公告</div>
          <table><thead><tr>
            <th>项目编号</th><th>项目名称</th><th>标的评估值（万元）</th>
            <th>转让底价（万元）</th><th>交易价格（万元）</th><th>合同签订日期</th>
          </tr></thead></table>
          <table><tbody><tr>
            <td>G32026TJ1000016</td><td>天津新誉国际商务有限公司33%股权</td>
            <td>332.7423</td><td>344.068</td><td>344.068</td><td>2026-07-22</td>
          </tr></tbody></table>
        </body></html>
        """
        html_bytes = html.encode("utf-8")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_id": "tpre",
            "source_url": "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=G32026TJ1000016",
            "project_code": "G32026TJ1000016",
            "project_name": "天津新誉国际商务有限公司33%股权",
            "deal_date": "2026-07-22",
            "deal_date_basis": "contractSignTime",
            "deal_date_is_imputed": False,
            "collection_date": "2026-08-14",
        }
        with open(snapshot_path, "wb") as handle:
            handle.write(html_bytes)
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "archive_content_sha256": hashlib.sha256(html_bytes).hexdigest(),
                    "archive_content_bytes": len(html_bytes),
                    "metadata": metadata,
                    "detail_payload": {},
                },
                handle,
                ensure_ascii=False,
            )

        payload = _build_registry_parse_payload(file_path=snapshot_path, content=html)

        self.assertEqual(payload["project_code"], metadata["project_code"])
        self.assertEqual(payload["project_name"], metadata["project_name"])
        self.assertEqual(payload["record_family"], "deal")
        self.assertEqual(payload["source_id"], "tpre")
        self.assertEqual(payload["business_id"], "deal_equity_transfer")
        self.assertEqual(payload["deal_date"], "2026/07/22")
        self.assertEqual(payload["deal_date_basis"], "contractSignTime")
        self.assertFalse(payload["deal_date_is_imputed"])
        self.assertEqual(payload["collection_date"], "2026/08/14")

    def test_ingest_rejects_explicit_non_object_parser_payload(self) -> None:
        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: False,  # type: ignore[return-value]
            ),
        )

        with self.assertRaisesRegex(ValueError, "parser_payload must be an object, got bool"):
            runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

    def test_legacy_output_mapping_accepts_deal_target_filename(self) -> None:
        standard = build_standard_project(
            {
                "项目编号": "D32026SHMAP0001",
                "项目名称": "成交映射兼容项目",
                "项目类型": "股权转让",
                "status": "成交",
                "exchange": "sse",
                "deal_date": "2026-04-18",
                "deal_price": "1080.5",
                "valuation": "1200",
                "reserve_price": "1000",
                "交易方式": "网络竞价",
                "受让方名称": "兼容受让方",
                "转让方": "示例转让方",
                "类型": "国资",
            }
        )

        mapped = map_standard_to_excel_payload(standard, "成交_股权转让.xlsx")

        self.assertEqual(mapped["项目编号"], "D32026SHMAP0001")
        self.assertEqual(mapped["项目名称"], "成交映射兼容项目")
        self.assertEqual(mapped["项目状态"], "成交")
        self.assertEqual(mapped["成交日期"], "2026-04-18")
        self.assertEqual(mapped["交易价格"], "1080.5")
        self.assertEqual(mapped["转让标的评估值"], "1200")
        self.assertEqual(mapped["转让底价"], "1000")
        self.assertEqual(mapped["交易方式"], "网络竞价")
        self.assertEqual(mapped["受让方名称"], "兼容受让方")
        self.assertNotIn("挂牌开始日期", mapped)
        self.assertNotIn("挂牌价格", mapped)

    def test_merge_record_payloads_rejects_non_object_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "parser_payload must be an object, got null"):
            _merge_record_payloads(parser_payload=None, postprocess_payload={})  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "postprocess_payload must be an object, got list"):
            _merge_record_payloads(parser_payload={}, postprocess_payload=[])  # type: ignore[arg-type]

    def test_build_canonical_projection_payload_rejects_non_object_canonical_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical_fields"):
            _build_canonical_projection_payload({"canonical_fields": []})  # type: ignore[arg-type]

    def test_assemble_ingested_record_rejects_explicit_non_object_source_identity(self) -> None:
        for source_identity in (False, [], None):
            with self.subTest(source_identity=source_identity):
                with self.assertRaisesRegex(ValueError, "source_identity"):
                    _assemble_ingested_record(
                        record_id="rec-invalid-source-identity",
                        project_code="G32026SH1000001",
                        project_name="坏身份对象项目",
                        project_type="股权转让",
                        exchange="shanghai",
                        listing_date="2026-03-21",
                        state="ready",
                        source_file=self.html_path,
                        archive_path=self.html_path,
                        parser_payload={},
                        postprocess_payload={},
                        findings=[],
                        source_identity=source_identity,  # type: ignore[arg-type]
                    )

    def test_assemble_ingested_record_rejects_explicit_non_mapping_finding_evidence(self) -> None:
        with self.assertRaisesRegex(TypeError, "evidence"):
            _assemble_ingested_record(
                record_id="rec-invalid-finding-evidence",
                project_code="G32026SH1000002",
                project_name="坏证据对象项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=self.html_path,
                archive_path=self.html_path,
                parser_payload={},
                postprocess_payload={},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="bad evidence shape",
                        evidence=False,  # type: ignore[arg-type]
                    )
                ],
                source_identity={"record_family": "listing"},
            )

    def test_assemble_ingested_record_accepts_mapping_finding_evidence(self) -> None:
        record = _assemble_ingested_record(
            record_id="rec-mapping-finding-evidence",
            project_code="G32026SH1000003",
            project_name="映射证据对象项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="pending_mapping",
            source_file=self.html_path,
            archive_path=self.html_path,
            parser_payload={},
            postprocess_payload={},
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="mapping evidence",
                    evidence=MappingProxyType({"missing_fields": ["类型"]}),  # type: ignore[arg-type]
                )
            ],
            source_identity={"record_family": "listing"},
        )

        self.assertEqual(
            record.canonical_record["diagnostics"][0]["evidence"],
            {"missing_fields": ["类型"]},
        )

    def test_ingest_ready_record_rewrites_asset_references_after_archive_rename(self) -> None:
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write(
                '<html><head><link rel="stylesheet" href="raw_files/style.css" /></head>'
                '<body><img src="raw_files/image.png" /></body></html>'
            )
        with open(f"{os.path.splitext(self.html_path)[0]}_files/image.png", "wb") as handle:
            handle.write(b"png")

        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "上海电气集团恒联企业发展有限公司35%股权",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "上海电气集团恒联企业发展有限公司",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        archive_path = result["archive_path"]
        with open(archive_path, "r", encoding="utf-8") as handle:
            archived_html = handle.read()

        archive_assets_ref = f"{os.path.splitext(os.path.basename(archive_path))[0]}_files/style.css"
        self.assertIn(archive_assets_ref, archived_html)
        self.assertNotIn("raw_files/style.css", archived_html)

    def test_resolve_submission_target_rejects_symlink_month_directory(self) -> None:
        outside_dir = os.path.join(self.temp_dir.name, "outside-month")
        os.makedirs(outside_dir)
        os.makedirs(self.archive_root)
        os.symlink(outside_dir, os.path.join(self.archive_root, "2026年3月"))

        with self.assertRaisesRegex(ValueError, "must not use symlinks"):
            resolve_submission_snapshot_target(
                archive_root=self.archive_root,
                project_code="G32026SH1009901",
                project_name="链接月份目录",
                listing_date="2026-03-21",
            )

        self.assertEqual(os.listdir(outside_dir), [])

    def test_archive_materializers_reject_symlink_source_html(self) -> None:
        real_source = os.path.join(self.temp_dir.name, "real-source.html")
        symlink_source = os.path.join(self.temp_dir.name, "symlink-source.html")
        with open(real_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>trusted source</body></html>")
        os.symlink(real_source, symlink_source)

        for materializer in (copy_snapshot_to_archive, materialize_snapshot_to_archive):
            with self.subTest(materializer=materializer.__name__):
                with self.assertRaisesRegex(ValueError, "regular non-symlink file"):
                    materializer(
                        source_file=symlink_source,
                        archive_root=self.archive_root,
                        project_code="G32026SH1009902",
                        project_name="链接源文件",
                        listing_date="2026-03-21",
                    )

        self.assertFalse(os.path.exists(self.archive_root))
        with open(real_source, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<html><body>trusted source</body></html>")

    def test_archive_materializers_reject_symlink_source_sidecar_without_temp_residue(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "linked-sidecar-source.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source</body></html>")
        outside_sidecar = os.path.join(self.temp_dir.name, "outside-sidecar.json")
        with open(outside_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"outside": True}, handle)
        os.symlink(outside_sidecar, os.path.splitext(source_path)[0] + ".json")

        for materializer in (copy_snapshot_to_archive, materialize_snapshot_to_archive):
            with self.subTest(materializer=materializer.__name__):
                with self.assertRaisesRegex(ValueError, "companion must not be a symlink"):
                    materializer(
                        source_file=source_path,
                        archive_root=self.archive_root,
                        project_code="G32026SH1009903",
                        project_name="链接伴随文件",
                        listing_date="2026-03-21",
                    )

        self.assertFalse(os.path.exists(self.archive_root))
        with open(outside_sidecar, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"outside": True})

    def test_archive_materializers_reject_symlink_inside_source_assets_without_temp_residue(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "linked-assets-source.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source</body></html>")
        source_assets = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets)
        outside_asset = os.path.join(self.temp_dir.name, "outside.css")
        with open(outside_asset, "w", encoding="utf-8") as handle:
            handle.write("outside css")
        os.symlink(outside_asset, os.path.join(source_assets, "linked.css"))

        for materializer in (copy_snapshot_to_archive, materialize_snapshot_to_archive):
            with self.subTest(materializer=materializer.__name__):
                with self.assertRaisesRegex(ValueError, "must not contain symlinks"):
                    materializer(
                        source_file=source_path,
                        archive_root=self.archive_root,
                        project_code="G32026SH1009904",
                        project_name="链接资源文件",
                        listing_date="2026-03-21",
                    )

        self.assertFalse(os.path.exists(self.archive_root))
        with open(outside_asset, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "outside css")

    def test_materialize_rejects_symlink_target_bundle_members_without_external_mutation(self) -> None:
        scenarios = ("html", "sidecar", "assets")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                archive_root = os.path.join(self.temp_dir.name, f"target-{scenario}")
                month_dir = os.path.join(archive_root, "2026年3月")
                os.makedirs(month_dir)
                source_path = os.path.join(self.temp_dir.name, f"source-{scenario}.html")
                with open(source_path, "w", encoding="utf-8") as handle:
                    handle.write(f"<html><body>{scenario} source</body></html>")

                target_path = os.path.join(
                    month_dir,
                    f"G32026SH1009905-目标{scenario}.html",
                )
                outside_path = os.path.join(self.temp_dir.name, f"outside-target-{scenario}")
                if scenario == "html":
                    with open(outside_path, "w", encoding="utf-8") as handle:
                        handle.write("outside html")
                    os.symlink(outside_path, target_path)
                    expected_error = "must not use symlinks"
                elif scenario == "sidecar":
                    with open(outside_path, "w", encoding="utf-8") as handle:
                        handle.write("outside sidecar")
                    os.symlink(outside_path, os.path.splitext(target_path)[0] + ".json")
                    expected_error = "must not use symlinks"
                else:
                    os.makedirs(outside_path)
                    outside_asset = os.path.join(outside_path, "keep.css")
                    with open(outside_asset, "w", encoding="utf-8") as handle:
                        handle.write("outside asset")
                    os.symlink(outside_path, f"{os.path.splitext(target_path)[0]}_files")
                    expected_error = "must not use symlinks"

                with self.assertRaisesRegex(ValueError, expected_error):
                    materialize_snapshot_to_archive(
                        source_file=source_path,
                        archive_root=archive_root,
                        project_code="G32026SH1009905",
                        project_name=f"目标{scenario}",
                        listing_date="2026-03-21",
                    )

                if scenario == "assets":
                    with open(os.path.join(outside_path, "keep.css"), "r", encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), "outside asset")
                else:
                    with open(outside_path, "r", encoding="utf-8") as handle:
                        expected = "outside html" if scenario == "html" else "outside sidecar"
                        self.assertEqual(handle.read(), expected)
                self.assertTrue(os.path.isfile(source_path))
                self.assertEqual(
                    [name for name in os.listdir(month_dir) if name.startswith(".")],
                    [],
                )

    def test_copy_snapshot_to_archive_keeps_existing_snapshot_when_asset_copy_fails(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "incoming.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write('<html><head><link rel="stylesheet" href="incoming_files/new.css" /></head></html>')
        source_assets_dir = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets_dir, exist_ok=True)
        with open(os.path.join(source_assets_dir, "new.css"), "w", encoding="utf-8") as handle:
            handle.write("new css")

        target_path = os.path.join(self.archive_root, "2026年3月", "G32026SH1009001-测试项目.html")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>old snapshot</body></html>")
        target_assets_dir = f"{os.path.splitext(target_path)[0]}_files"
        os.makedirs(target_assets_dir, exist_ok=True)
        with open(os.path.join(target_assets_dir, "old.css"), "w", encoding="utf-8") as handle:
            handle.write("old css")

        with (
            patch("peap.streaming_ingest.shutil.copytree", side_effect=OSError("copytree failed")),
            self.assertRaisesRegex(OSError, "copytree failed"),
        ):
            copy_snapshot_to_archive(
                source_file=source_path,
                archive_root=self.archive_root,
                project_code="G32026SH1009001",
                project_name="测试项目",
                listing_date="2026-03-21",
            )

        with open(target_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<html><body>old snapshot</body></html>")
        with open(os.path.join(target_assets_dir, "old.css"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "old css")

    def test_materialize_keeps_source_bundle_when_staging_copy_fails(self) -> None:
        source_dir = os.path.join(self.archive_root, "staging")
        os.makedirs(source_dir)
        source_path = os.path.join(source_dir, "incoming.html")
        source_html = '<html><body><img src="incoming_files/new.css" /></body></html>'
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write(source_html)
        source_assets = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets)
        with open(os.path.join(source_assets, "new.css"), "w", encoding="utf-8") as handle:
            handle.write("new css")

        with (
            patch("peap.streaming_ingest.shutil.copytree", side_effect=OSError("copytree failed")),
            self.assertRaisesRegex(OSError, "copytree failed"),
        ):
            materialize_snapshot_to_archive(
                source_file=source_path,
                archive_root=self.archive_root,
                project_code="G32026SH1009906",
                project_name="归档复制失败项目",
                listing_date="2026-03-21",
            )

        expected_target = os.path.join(
            self.archive_root,
            "2026年3月",
            "G32026SH1009906-归档复制失败项目.html",
        )
        self.assertFalse(os.path.exists(expected_target))
        with open(source_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), source_html)
        with open(os.path.join(source_assets, "new.css"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "new css")

    def test_copy_snapshot_to_archive_keeps_existing_snapshot_when_sidecar_replace_fails(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "incoming_sidecar.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write('<html><head><link rel="stylesheet" href="incoming_sidecar_files/new.css" /></head></html>')
        with open(os.path.splitext(source_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"version": "new"}, handle)
        source_assets_dir = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets_dir, exist_ok=True)
        with open(os.path.join(source_assets_dir, "new.css"), "w", encoding="utf-8") as handle:
            handle.write("new css")

        target_path = os.path.join(self.archive_root, "2026年3月", "G32026SH1009002-测试项目.html")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>old snapshot</body></html>")
        target_sidecar = os.path.splitext(target_path)[0] + ".json"
        conflict_sidecar = os.path.splitext(target_path)[0] + "__conflict1.json"
        with open(target_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"version": "old"}, handle)
        target_assets_dir = f"{os.path.splitext(target_path)[0]}_files"
        os.makedirs(target_assets_dir, exist_ok=True)
        with open(os.path.join(target_assets_dir, "old.css"), "w", encoding="utf-8") as handle:
            handle.write("old css")

        real_replace = os.replace

        def fail_sidecar_replace(src: str, dst: str) -> None:
            if dst == conflict_sidecar and src.endswith(".json"):
                raise OSError("sidecar replace failed")
            real_replace(src, dst)

        with (
            patch("peap.streaming_ingest.os.replace", side_effect=fail_sidecar_replace),
            self.assertRaisesRegex(OSError, "sidecar replace failed"),
        ):
            copy_snapshot_to_archive(
                source_file=source_path,
                archive_root=self.archive_root,
                project_code="G32026SH1009002",
                project_name="测试项目",
                listing_date="2026-03-21",
            )

        with open(target_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<html><body>old snapshot</body></html>")
        with open(target_sidecar, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"version": "old"})
        with open(os.path.join(target_assets_dir, "old.css"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "old css")

    def test_copy_snapshot_to_archive_preserves_canonical_bundle_and_uses_conflict_stem(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "incoming_clean.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>current snapshot</body></html>")
        source_sidecar = os.path.splitext(source_path)[0] + ".json"
        with open(source_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"version": "current"}, handle)
        source_assets = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets, exist_ok=True)
        with open(os.path.join(source_assets, "current.css"), "w", encoding="utf-8") as handle:
            handle.write("current css")
        target_path = os.path.join(self.archive_root, "2026年3月", "G32026SH1009003-测试项目.html")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>old snapshot</body></html>")
        target_sidecar = os.path.splitext(target_path)[0] + ".json"
        with open(target_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"version": "old"}, handle)
        target_assets = f"{os.path.splitext(target_path)[0]}_files"
        os.makedirs(target_assets, exist_ok=True)
        with open(os.path.join(target_assets, "old.css"), "w", encoding="utf-8") as handle:
            handle.write("old css")

        archived_path, had_conflict = copy_snapshot_to_archive(
            source_file=source_path,
            archive_root=self.archive_root,
            project_code="G32026SH1009003",
            project_name="测试项目",
            listing_date="2026-03-21",
        )

        conflict_path = os.path.splitext(target_path)[0] + "__conflict1.html"
        self.assertTrue(had_conflict)
        self.assertEqual(archived_path, conflict_path)
        with open(target_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<html><body>old snapshot</body></html>")
        with open(target_sidecar, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"version": "old"})
        self.assertTrue(os.path.isfile(os.path.join(target_assets, "old.css")))
        with open(conflict_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<html><body>current snapshot</body></html>")
        with open(os.path.splitext(conflict_path)[0] + ".json", "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], "current")
        self.assertTrue(os.path.isfile(f"{os.path.splitext(conflict_path)[0]}_files/current.css"))

    def test_copy_snapshot_to_archive_same_content_is_bundle_preserving_noop(self) -> None:
        html = "<html><body>same snapshot</body></html>"
        source_path = os.path.join(self.temp_dir.name, "same_content.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        with open(os.path.splitext(source_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"version": "incoming"}, handle)
        source_assets = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets, exist_ok=True)
        with open(os.path.join(source_assets, "incoming.css"), "w", encoding="utf-8") as handle:
            handle.write("incoming css")

        target_path = os.path.join(self.archive_root, "2026年3月", "G32026SH1009006-同内容项目.html")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        target_sidecar = os.path.splitext(target_path)[0] + ".json"
        with open(target_sidecar, "w", encoding="utf-8") as handle:
            json.dump({"version": "canonical"}, handle)
        target_assets = f"{os.path.splitext(target_path)[0]}_files"
        os.makedirs(target_assets, exist_ok=True)
        with open(os.path.join(target_assets, "canonical.css"), "w", encoding="utf-8") as handle:
            handle.write("canonical css")

        archived_path, had_conflict = copy_snapshot_to_archive(
            source_file=source_path,
            archive_root=self.archive_root,
            project_code="G32026SH1009006",
            project_name="同内容项目",
            listing_date="2026-03-21",
        )

        self.assertFalse(had_conflict)
        self.assertEqual(archived_path, target_path)
        with open(target_sidecar, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"version": "canonical"})
        self.assertTrue(os.path.isfile(os.path.join(target_assets, "canonical.css")))
        self.assertFalse(os.path.exists(os.path.join(target_assets, "incoming.css")))

    def test_copy_snapshot_to_archive_refreshes_sidecar_integrity_after_reference_rewrite(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "incoming_integrity.html")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write('<html><body><img src="incoming_integrity_files/image.png"></body></html>')
        source_assets = f"{os.path.splitext(source_path)[0]}_files"
        os.makedirs(source_assets, exist_ok=True)
        with open(os.path.join(source_assets, "image.png"), "wb") as handle:
            handle.write(b"png")
        with open(os.path.splitext(source_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"save_status": "complete"}, handle)

        archived_path, _ = copy_snapshot_to_archive(
            source_file=source_path,
            archive_root=self.archive_root,
            project_code="G32026SH1009004",
            project_name="完整性项目",
            listing_date="2026-03-21",
        )

        archived_sidecar = os.path.splitext(archived_path)[0] + ".json"
        with open(archived_path, "rb") as handle:
            archived_bytes = handle.read()
        with open(archived_sidecar, "r", encoding="utf-8") as handle:
            sidecar = json.load(handle)
        self.assertEqual(sidecar["archive_content_sha256"], hashlib.sha256(archived_bytes).hexdigest())
        self.assertEqual(sidecar["archive_content_bytes"], len(archived_bytes))
        self.assertNotIn("incoming_integrity_files/", archived_bytes.decode("utf-8"))

    def test_materialize_same_target_removes_unverifiable_managed_companions(self) -> None:
        target_path = os.path.join(
            self.archive_root,
            "2026年3月",
            "G32026SH1009005-同路径项目.html",
        )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>replacement snapshot</body></html>")
        companion_paths = (
            os.path.splitext(target_path)[0] + ".json",
            f"{target_path}.peap-save-status.json",
            f"{target_path}.peap-evidence.json",
        )
        for companion_path in companion_paths:
            with open(companion_path, "w", encoding="utf-8") as handle:
                json.dump({"save_status": "complete"}, handle)

        archived_path, had_conflict = materialize_snapshot_to_archive(
            source_file=target_path,
            archive_root=self.archive_root,
            project_code="G32026SH1009005",
            project_name="同路径项目",
            listing_date="2026-03-21",
        )

        self.assertEqual(archived_path, target_path)
        self.assertFalse(had_conflict)
        for companion_path in companion_paths:
            self.assertFalse(os.path.exists(companion_path))

    def test_materialize_same_target_preserves_and_binds_trusted_legacy_deal_sidecar(self) -> None:
        project_code = "G32026CQ1000062"
        project_name = "长安福特新能源汽车科技有限公司40%股权"
        target_path, _ = _canonical_archive_target(
            archive_root=self.archive_root,
            project_code=project_code,
            project_name=project_name,
            listing_date="2026-07-02",
            source_file=os.path.join(self.temp_dir.name, "incoming.html"),
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="cquae",
        )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        html = (
            "<html><head>"
            f"<title>{project_name} - 重庆产权交易网</title>"
            "</head><body><div>交易结果公示</div><table>"
            f"<tr><th>项目编号</th><td>{project_code}</td></tr>"
            f"<tr><th>标的名称</th><td>{project_name}</td></tr>"
            "<tr><th>成交日期</th><td>2026-07-02</td></tr>"
            "</table></body></html>"
        )
        html_bytes = html.encode("utf-8")
        with open(target_path, "wb") as handle:
            handle.write(html_bytes)
        sidecar_path = os.path.splitext(target_path)[0] + ".json"
        with open(sidecar_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "metadata": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "cquae",
                        "project_code": project_code,
                        "project_name": project_name,
                    },
                },
                handle,
                ensure_ascii=False,
            )

        for _ in range(2):
            archived_path, had_conflict = materialize_snapshot_to_archive(
                source_file=target_path,
                archive_root=self.archive_root,
                project_code=project_code,
                project_name=project_name,
                listing_date="2026-07-02",
                record_family="deal",
                business_id="deal_equity_transfer",
                source_id="cquae",
                preserve_source=True,
            )
            self.assertEqual(archived_path, target_path)
            self.assertFalse(had_conflict)
            self.assertTrue(os.path.isfile(sidecar_path))

        with open(sidecar_path, "r", encoding="utf-8") as handle:
            sidecar = json.load(handle)
        self.assertEqual(sidecar["archive_content_sha256"], hashlib.sha256(html_bytes).hexdigest())
        self.assertEqual(sidecar["archive_content_bytes"], len(html_bytes))

    def test_materialize_preserve_source_copies_existing_archive_instead_of_moving_it(self) -> None:
        source_path = os.path.join(self.archive_root, "legacy", "cquae-deal.html")
        os.makedirs(os.path.dirname(source_path), exist_ok=True)
        html_bytes = b"<html><body>preserved evidence</body></html>"
        with open(source_path, "wb") as handle:
            handle.write(html_bytes)
        source_sidecar = os.path.splitext(source_path)[0] + ".json"
        with open(source_sidecar, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "archive_content_sha256": hashlib.sha256(html_bytes).hexdigest(),
                    "archive_content_bytes": len(html_bytes),
                },
                handle,
            )

        archived_path, _ = materialize_snapshot_to_archive(
            source_file=source_path,
            archive_root=self.archive_root,
            project_code="G32026CQ1000063",
            project_name="重庆津舟进出口贸易有限公司21%股权",
            listing_date="2026-07-01",
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="cquae",
            preserve_source=True,
        )

        self.assertNotEqual(archived_path, source_path)
        self.assertTrue(os.path.isfile(source_path))
        self.assertTrue(os.path.isfile(source_sidecar))
        self.assertTrue(os.path.isfile(archived_path))
        with open(source_path, "rb") as handle:
            self.assertEqual(handle.read(), html_bytes)
        with open(archived_path, "rb") as handle:
            self.assertEqual(handle.read(), html_bytes)

    def test_ingest_uses_archive_root_selected_by_final_record_family(self) -> None:
        listing_root = os.path.join(self.temp_dir.name, "listing-archive")
        deal_root = os.path.join(self.temp_dir.name, "deal-archive")

        def fake_parser(_file_path: str):
            return {
                "项目编号": "G32026CQ1000062",
                "项目名称": "长安福特新能源汽车科技有限公司40%股权",
                "项目类型": "股权转让",
                "交易所": "重交所",
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "source_id": "cquae",
                "deal_date": "2026-07-02",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=listing_root,
            archive_roots_by_family={"listing": listing_root, "deal": deal_root},
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **_kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="cquae",
                extra={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "cquae",
                    "preserve_source_artifact": True,
                },
            )
        )

        self.assertEqual(os.path.commonpath((deal_root, result["archive_path"])), deal_root)
        self.assertTrue(os.path.isfile(self.html_path))

    def test_ingest_persists_candidate_identity_tokens_into_latest_record_context(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000195",
                "项目名称": "带候选标识项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "上海电气集团恒联企业发展有限公司",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                page_url="https://example.test/detail/ingest-meta",
                exchange="shanghai",
                extra={"project_id": "INGESTMETA001"},
            )
        )

        tokens = self.store.list_existing_candidate_tokens(states=["ready"])

        self.assertIn("page_url:https://example.test/detail/ingest-meta", tokens)
        self.assertIn("project_id:INGESTMETA001", tokens)

    def test_ingest_pending_mapping_record_is_excluded_from_ready_set(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000999",
                "项目名称": "缺映射项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "转让方": "未知公司",
            }

        def fake_postprocess(payload, **kwargs):
            return dict(payload), [
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="missing mapping",
                    evidence={},
                )
            ]

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        self.assertEqual(result["state"], "pending_mapping")
        self.assertEqual(self.store.iter_latest_records(states=["ready"]), [])
        pending = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(pending), 1)

    def test_refresh_postprocess_reuses_stored_parser_payload_without_reparsing(self) -> None:
        missing_archive_path = os.path.join(self.archive_root, "2026年3月", "rec-refresh.html")
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh",
                revision_hash="hash-rec-refresh-initial",
                project_code="G32025SH1002001",
                project_name="待回刷项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=missing_archive_path,
                archive_path=missing_archive_path,
                parser_payload={
                    "项目编号": "G32025SH1002001",
                    "项目名称": "待回刷项目",
                    "项目类型": "股权转让",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1002001",
                    "项目名称": "待回刷项目",
                    "项目类型": "股权转让",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="缺少类型，暂不能进入导出",
                        evidence={"missing_fields": ["类型"]},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "original_source_file": missing_archive_path,
                    "source_url": "",
                    "project_code": "G32025SH1002001",
                    "project_name": "待回刷项目",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                    "candidate_tokens": ["project_code:G32025SH1002001"],
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1002001",
                        "project_name": "待回刷项目",
                        "project_type": "股权转让",
                        "status": "",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "",
                        "seller": "上海电气集团恒联企业发展有限公司",
                        "source_type": "",
                        "group_name": "",
                    },
                },
                canonical_projection={
                    "项目编号": "G32025SH1002001",
                    "项目名称": "待回刷项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "挂牌开始日期": "2026-03-21",
                },
            )
        )
        self.store.mark_mapping_pending(
            record_id="rec-refresh",
            revision_id=1,
            project_code="G32025SH1002001",
            payload={"项目编号": "G32025SH1002001"},
        )

        def fake_parser(_file_path: str):
            raise AssertionError("refresh_postprocess should not call parser")

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            updated["canonical_projection"] = {
                "项目编号": payload["项目编号"],
                "项目名称": payload["项目名称"],
                "项目类型": "股权转让",
                "转让方": payload["转让方"],
                "类型": "国资",
                "挂牌开始日期": payload["挂牌开始日期"],
            }
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.refresh_postprocess("rec-refresh")

        self.assertEqual(result["state"], "ready")
        latest = self.store.get_record("rec-refresh")
        self.assertEqual(latest["postprocess_payload"]["类型"], "国资")
        self.assertEqual(latest["archive_path"], missing_archive_path)
        self.assertEqual(latest["canonical_record"]["canonical_fields"]["source_type"], "国资")
        self.assertEqual(latest["canonical_projection"]["类型"], "国资")
        self.assertEqual(self.store.list_pending_mappings(), [])

    def test_refresh_postprocess_rejects_null_stored_parser_payload(self) -> None:
        record = {
            "record_id": "rec-null-parser-payload",
            "project_code": "G32025SH1002999",
            "project_name": "损坏载荷项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "listing_date": "2026-03-21",
            "state": "pending_mapping",
            "source_file": self.html_path,
            "archive_path": self.html_path,
            "parser_payload": None,
            "source_identity_json": {"record_family": "listing"},
            "record_family": "listing",
            "business_id": "",
        }
        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        with patch.object(self.store, "get_record", return_value=record):
            with self.assertRaisesRegex(ValueError, "parser_payload must be an object, got null"):
                runner.refresh_postprocess("rec-null-parser-payload")

    def test_refresh_postprocess_rejects_explicit_non_object_source_identity_json(self) -> None:
        parser_payload = {
            "项目编号": "G32025SH1003000",
            "项目名称": "坏身份载荷项目",
            "项目类型": "股权转让",
            "交易所": "shanghai",
            "挂牌开始日期": "2026-03-21",
            "转让方": "测试转让方",
        }
        base_record = {
            "record_id": "rec-invalid-source-identity-json",
            "project_code": "G32025SH1003000",
            "project_name": "坏身份载荷项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "listing_date": "2026-03-21",
            "state": "pending_mapping",
            "source_file": self.html_path,
            "archive_path": self.html_path,
            "parser_payload": parser_payload,
            "record_family": "listing",
            "business_id": "",
        }
        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        for source_identity_json in (False, []):
            with self.subTest(source_identity_json=source_identity_json):
                record = {**base_record, "source_identity_json": source_identity_json}
                with patch.object(self.store, "get_record", return_value=record):
                    with self.assertRaisesRegex(ValueError, "source_identity_json"):
                        runner.refresh_postprocess("rec-invalid-source-identity-json")

    def test_refresh_postprocess_missing_authoritative_record_family_enters_pending_review(self) -> None:
        archive_path = os.path.join(self.archive_root, "2026年3月", "rec-refresh-missing-family.html")
        record = {
            "record_id": "rec-refresh-missing-family",
            "project_code": "G32025SH1003001",
            "project_name": "缺权威family回刷项目",
            "project_type": "股权转让",
            "exchange": "",
            "listing_date": "2026-03-21",
            "state": "pending_mapping",
            "source_file": archive_path,
            "archive_path": archive_path,
            "parser_payload": {
                "项目编号": "G32025SH1003001",
                "项目名称": "缺权威family回刷项目",
                "项目类型": "股权转让",
                "挂牌开始日期": "2026-03-21",
                "转让方": "测试转让方",
                "类型": "国资",
            },
            "source_identity_json": {
                "original_source_file": archive_path,
                "source_url": "",
                "project_code": "G32025SH1003001",
                "project_name": "缺权威family回刷项目",
                "exchange": "",
                "listing_date": "2026-03-21",
                "candidate_tokens": ["project_code:G32025SH1003001"],
            },
            "record_family": "",
            "business_id": "",
        }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        with patch.object(self.store, "get_record", return_value=record):
            result = runner.refresh_postprocess("rec-refresh-missing-family")

        self.assertEqual(result["state"], "pending_review")
        latest = self.store.get_record("rec-refresh-missing-family")
        family_blocker = next(
            item for item in latest["findings"] if str(item.get("type") or "") == "record_family_authority_missing"
        )
        self.assertEqual(family_blocker["evidence"].get("stage"), "refresh_postprocess")

    def test_refresh_postprocess_preserves_existing_operational_overlay_until_service_updates_it(self) -> None:
        missing_archive_path = os.path.join(self.archive_root, "2026年3月", "rec-refresh-overlay.html")
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh-overlay",
                revision_hash="hash-rec-refresh-overlay-initial",
                project_code="G32025SH1002003",
                project_name="回刷保留诊断项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=missing_archive_path,
                archive_path=missing_archive_path,
                parser_payload={
                    "项目编号": "G32025SH1002003",
                    "项目名称": "回刷保留诊断项目",
                    "项目类型": "股权转让",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1002003",
                    "项目名称": "回刷保留诊断项目",
                    "项目类型": "股权转让",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="缺少类型，暂不能进入导出",
                        evidence={"missing_fields": ["类型"]},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "original_source_file": missing_archive_path,
                    "source_url": "",
                    "project_code": "G32025SH1002003",
                    "project_name": "回刷保留诊断项目",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                    "candidate_tokens": ["project_code:G32025SH1002003"],
                },
            )
        )
        self.store.record_operation_result(
            "rec-refresh-overlay",
            kind="reprocess",
            code="source_missing",
            message="source file missing",
            artifact_status="missing",
        )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-overlay")

        self.assertEqual(result["state"], "ready")
        latest = self.store.get_record("rec-refresh-overlay")
        self.assertEqual(latest["artifact_status"], "missing")
        self.assertEqual(latest["last_operation_kind"], "reprocess")
        self.assertEqual(latest["last_operation_code"], "source_missing")
        self.assertEqual(latest["postprocess_payload"]["类型"], "国资")

    def test_refresh_postprocess_preserves_existing_project_type_as_fallback(self) -> None:
        archive_path = os.path.join(self.archive_root, "2026年3月", "rec-refresh-type.html")
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh-type",
                revision_hash="hash-rec-refresh-type-initial",
                project_code="G32025SH1002002",
                project_name="缺类型回刷项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=archive_path,
                archive_path=archive_path,
                parser_payload={
                    "项目编号": "G32025SH1002002",
                    "项目名称": "缺类型回刷项目",
                    "项目类型": "未知",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1002002",
                    "项目名称": "缺类型回刷项目",
                    "项目类型": "未知",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型未识别，暂不能进入导出",
                        evidence={"project_type": "未知"},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "original_source_file": archive_path,
                    "source_url": "",
                    "project_code": "G32025SH1002002",
                    "project_name": "缺类型回刷项目",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                    "candidate_tokens": ["project_code:G32025SH1002002"],
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1002002",
                        "project_name": "缺类型回刷项目",
                        "project_type": "股权转让",
                        "status": "",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "",
                        "seller": "上海电气集团恒联企业发展有限公司",
                        "source_type": "",
                        "group_name": "上海电气集团",
                    },
                },
            )
        )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-type")

        self.assertEqual(result["project_type"], "股权转让")
        latest = self.store.get_record("rec-refresh-type")
        self.assertEqual(latest["project_type"], "股权转让")
        self.assertEqual(latest["postprocess_payload"]["项目类型"], "股权转让")
        self.assertEqual(latest["canonical_record"]["canonical_fields"]["project_type"], "股权转让")

    def test_refresh_postprocess_reclassifies_unknown_business_from_stored_parser_payload(self) -> None:
        archive_path = os.path.join(self.archive_root, "2026年4月", "rec-refresh-business.html")
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh-business",
                revision_hash="hash-rec-refresh-business-initial",
                project_code="G32025BJ1000444-6",
                project_name="新疆凯宏投资有限公司66%股权",
                project_type="",
                exchange="北交所",
                listing_date="2026-04-01",
                state="pending_review",
                source_file=archive_path,
                archive_path=archive_path,
                parser_payload={
                    "项目编号": "G32025BJ1000444-6",
                    "项目名称": "新疆凯宏投资有限公司66%股权",
                    "项目类型": "未知",
                    "项目状态": "挂牌",
                    "交易所": "北交所",
                    "挂牌开始日期": "2026-04-01",
                    "挂牌价格": "100.00",
                    "转让方": "首钢伊犁钢铁有限公司",
                    "隶属集团": "首钢集团有限公司",
                },
                postprocess_payload={
                    "项目编号": "G32025BJ1000444-6",
                    "项目名称": "新疆凯宏投资有限公司66%股权",
                    "项目类型": "未知",
                    "项目状态": "挂牌",
                    "交易所": "北交所",
                    "挂牌开始日期": "2026-04-01",
                    "挂牌价格": "100.00",
                    "转让方": "首钢伊犁钢铁有限公司",
                    "隶属集团": "首钢集团有限公司",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="business_resolution_required",
                        message="业务类型未识别，需完成人工归类后再继续处理",
                        evidence={"raw_business_label": "未知"},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "original_source_file": archive_path,
                    "source_url": "",
                    "project_code": "G32025BJ1000444-6",
                    "project_name": "新疆凯宏投资有限公司66%股权",
                    "exchange": "北交所",
                    "listing_date": "2026-04-01",
                    "candidate_tokens": ["project_code:G32025BJ1000444-6"],
                },
            )
        )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-business")

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["project_type"], "股权转让")
        latest = self.store.get_record("rec-refresh-business")
        self.assertEqual(latest["project_type"], "股权转让")
        self.assertEqual(latest["business_id"], "equity_transfer")
        self.assertEqual(latest["raw_business_label"], "股权转让")
        self.assertEqual(latest["postprocess_payload"]["项目类型"], "股权转让")

    def test_refresh_postprocess_canonicalizes_alias_source_id_in_source_identity(self) -> None:
        archive_path = os.path.join(self.archive_root, "2026年4月", "rec-refresh-source-alias.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-refresh-source-alias",
                revision_hash="hash-rec-refresh-source-alias-initial",
                project_code="D32026SHREFRESH001",
                project_name="刷新来源别名项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-19",
                state="ready",
                source_file=archive_path,
                archive_path=archive_path,
                parser_payload={
                    "项目编号": "D32026SHREFRESH001",
                    "项目名称": "刷新来源别名项目",
                    "项目类型": "股权转让",
                    "项目状态": "成交",
                    "交易所": "shanghai",
                    "成交日期": "2026-04-19",
                    "deal_price": "120",
                    "类型": "国资",
                },
                postprocess_payload={
                    "项目编号": "D32026SHREFRESH001",
                    "项目名称": "刷新来源别名项目",
                    "项目类型": "股权转让",
                    "项目状态": "成交",
                    "交易所": "shanghai",
                    "成交日期": "2026-04-19",
                    "deal_price": "120",
                    "类型": "国资",
                },
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id_hint": "deal_equity_transfer",
                    "business_id": "deal_equity_transfer",
                    "original_source_file": archive_path,
                    "source_url": "https://example.test/deal/refresh-source-alias",
                    "project_code": "D32026SHREFRESH001",
                    "exchange": "shanghai",
                    "source_id": "shanghai",
                    "listing_date": "2026-04-19",
                    "candidate_tokens": ["project_code:D32026SHREFRESH001"],
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "D32026SHREFRESH001",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SHREFRESH001",
                        "project_name": "刷新来源别名项目",
                        "project_type": "股权转让",
                        "exchange": "shanghai",
                        "start_date": "2026-04-19",
                    },
                },
            )
        )

        def fake_parser(file_path: str):
            raise AssertionError("refresh_postprocess should not call parser")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-source-alias")

        latest = self.store.get_record("rec-refresh-source-alias")
        self.assertEqual(result["state"], "ready")
        self.assertEqual(latest["source_identity_json"].get("source_id"), "sse")
        self.assertEqual(latest["canonical_record"]["source_identity"].get("source_id"), "sse")

    def test_ingest_canonicalizes_guangzhou_alias_source_id_to_guangdong(self) -> None:
        def fake_parser(file_path: str):
            self.assertEqual(file_path, self.html_path)
            return {
                "项目编号": "G32026GD100003",
                "项目名称": "广东别名入库项目",
                "项目类型": "股权转让",
                "交易所": "guangzhou",
                "挂牌开始日期": "2026-04-20",
                "挂牌价格": "100.00",
                "转让方": "广东测试集团",
                "source_id": "guangzhou",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="guangzhou"))

        latest = self.store.get_record(result["record_id"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(latest["exchange"], "guangdong")
        self.assertEqual(latest["source_identity_json"].get("source_id"), "guangdong")
        self.assertEqual(latest["source_identity_json"].get("exchange"), "guangdong")
        self.assertEqual(latest["canonical_record"]["source_identity"].get("source_id"), "guangdong")
        self.assertEqual(latest["canonical_record"]["source_identity"].get("exchange"), "guangdong")
        self.assertEqual(latest["canonical_record"]["canonical_fields"].get("exchange"), "guangdong")

    def test_ingest_uses_parsed_guangdong_equity_truth_over_stale_capital_task_hint(self) -> None:
        def fake_parser(file_path: str):
            self.assertEqual(file_path, self.html_path)
            return {
                "项目编号": "G32026GD0000081-4",
                "项目名称": "北京大唐永盛科技发展有限公司",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
                "交易所": "广交所",
                "挂牌开始日期": "2026-04-20",
                "挂牌价格": "100.00",
                "转让方": "电信科学技术研究院有限公司",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "央企"}, []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                page_url="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=160131",
                exchange="guangdong",
                extra={
                    "record_family": "listing",
                    "business_id": "capital_increase",
                    "business_label": "增资扩股",
                    "source_id": "guangdong",
                },
            )
        )

        latest = self.store.get_record(result["record_id"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(latest["business_id"], "equity_transfer")
        self.assertEqual(latest["project_type"], "股权转让")
        self.assertEqual(latest["source_identity_json"]["business_id"], "equity_transfer")

    def test_ingest_canonicalizes_guangdong_parser_exchange_label_without_source_id(self) -> None:
        def fake_parser(file_path: str):
            self.assertEqual(file_path, self.html_path)
            return {
                "项目编号": "G32026GD100004",
                "项目名称": "广东中文交易所标签项目",
                "项目类型": "股权转让",
                "交易所": "广交所",
                "挂牌开始日期": "2026-04-21",
                "挂牌价格": "100.00",
                "转让方": "广东测试集团",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange=""))

        latest = self.store.get_record(result["record_id"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(latest["source_identity_json"].get("source_id"), "guangdong")
        self.assertEqual(latest["canonical_record"]["source_identity"].get("source_id"), "guangdong")

    def test_ingest_rejects_legacy_public_resource_payload(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026PR000001",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "北交互联",
                "source_id": "public_resource",
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "挂牌开始日期": "2026-03-21",
                "成交日期": "2026-03-22",
                "deal_price": "120",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="北交互联",
                extra={
                    "source_id": "public_resource",
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "preserve_source_artifact": True,
                },
            )
        )

        self.assertEqual(result["state"], "parse_failed")
        self.assertEqual(result["error_type"], "unsupported_product_source")

    def test_ingest_weak_deal_payload_keeps_storage_family_aligned_with_postprocess_family(self) -> None:
        def fake_parser(file_path: str):
            return {
                "record_family": "deal",
                "项目编号": "D32026SSE000099",
                "项目名称": "弱结构增资成交项目",
                "项目类型": "增资扩股",
                "交易所": "sse",
                "挂牌开始日期": "2026-04-18",
                "investors": [{"investor_name": "总计"}],
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="sse"))

        self.assertEqual(result["state"], "pending_review")
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["postprocess_payload"].get("record_family"), "deal")
        self.assertEqual(latest[0]["record_family"], "deal")
        self.assertEqual(latest[0]["business_id"], "deal_capital_increase")

    def test_ingest_deal_capital_increase_uses_deal_family_in_postprocess_context(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026SSE000011",
                "项目名称": "成交增资项目",
                "项目类型": "增资扩股",
                "项目状态": "成交",
                "交易所": "sse",
                "成交日期": "2026-04-18",
                "类型": "国资",
                "investors": [{"investor_name": "总计"}],
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="sse",
                extra={"record_family": "deal", "business_id": "deal_capital_increase", "source_id": "sse"},
            )
        )

        self.assertEqual(result["state"], "pending_review")
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        finding_types = {str(item.get("type") or "") for item in latest[0]["findings"]}
        self.assertIn("business_resolution_required", finding_types)

    def test_refresh_postprocess_deal_capital_increase_uses_existing_family_in_context(self) -> None:
        archive_path = os.path.join(self.archive_root, "2026年4月", "rec-refresh-deal-capital-family.html")
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh-deal-capital-family",
                revision_hash="hash-rec-refresh-deal-capital-family-initial",
                project_code="D32026SSE000012",
                project_name="刷新成交增资项目",
                project_type="增资扩股",
                exchange="sse",
                listing_date="2026-04-18",
                state="ready",
                source_file=archive_path,
                archive_path=archive_path,
                parser_payload={
                    "项目编号": "D32026SSE000012",
                    "项目名称": "刷新成交增资项目",
                    "项目类型": "增资扩股",
                    "项目状态": "成交",
                    "交易所": "sse",
                    "成交日期": "2026-04-18",
                    "investors": [{"investor_name": "总计"}],
                },
                postprocess_payload={
                    "项目编号": "D32026SSE000012",
                    "项目名称": "刷新成交增资项目",
                    "项目类型": "增资扩股",
                    "项目状态": "成交",
                    "交易所": "sse",
                    "成交日期": "2026-04-18",
                    "investors": [{"investor_name": "总计"}],
                },
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id_hint": "deal_capital_increase",
                    "business_id": "deal_capital_increase",
                    "original_source_file": archive_path,
                    "project_code": "D32026SSE000012",
                    "project_name": "刷新成交增资项目",
                    "exchange": "sse",
                    "listing_date": "2026-04-18",
                    "source_id": "sse",
                    "candidate_tokens": ["project_code:D32026SSE000012"],
                },
            )
        )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-deal-capital-family")

        self.assertEqual(result["state"], "pending_review")
        latest = self.store.get_record("rec-refresh-deal-capital-family")
        finding_types = {str(item.get("type") or "") for item in latest["findings"]}
        self.assertIn("business_resolution_required", finding_types)

    def test_ingest_maps_deal_listing_date_to_effective_deal_date(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026SSE000010",
                "项目名称": "成交日期映射项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "sse",
                "挂牌开始日期": "2026-03-20",
                "成交日期": "2026-04-18",
                "deal_price": "120",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="sse",
                listing_date="2026-03-20",
                extra={"record_family": "deal", "business_id": "deal_equity_transfer", "source_id": "sse"},
            )
        )

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["record_family"], "deal")
        self.assertEqual(latest[0]["listing_date"], "2026-04-18")
        self.assertEqual(latest[0]["canonical_record"]["canonical_fields"]["start_date"], "2026-04-18")
        self.assertEqual(latest[0]["source_identity_json"].get("business_id"), "deal_equity_transfer")
        self.assertEqual(latest[0]["source_identity_json"].get("source_id"), "sse")

    def test_ingest_keeps_missing_real_deal_date_empty_while_using_collection_date_for_scope(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026SSE000013",
                "项目名称": "缺真实成交日入库项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "sse",
                "collection_date": "2026-04-20",
                "deal_date_basis": "collection_date",
                "deal_date_is_imputed": True,
                "deal_price": "320",
                "转让方": "测试公司",
                "类型": "国资",
                "备注": "原始备注；成交日期缺失，按采集日填列",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="sse",
                extra={"record_family": "deal", "business_id": "deal_equity_transfer", "source_id": "sse"},
            )
        )

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        canonical_fields = latest[0]["canonical_record"]["canonical_fields"]
        self.assertEqual(latest[0]["listing_date"], "2026-04-20")
        self.assertEqual(canonical_fields.get("deal_date"), "")
        self.assertEqual(canonical_fields.get("collection_date"), "2026-04-20")
        self.assertEqual(canonical_fields.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(canonical_fields.get("deal_date_is_imputed")))

    def test_ingest_deal_missing_required_export_fields_enters_field_missing(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "XZSYZC",
                "项目名称": "行政事业资产",
                "项目类型": "实物资产",
                "项目状态": "成交",
                "交易所": "cbex",
                "collection_date": "2026-05-08",
                "deal_date_basis": "collection_date",
                "deal_date_is_imputed": True,
                "备注": "成交日期缺失，按采集日填列",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="cbex",
                extra={"record_family": "deal", "business_id": "deal_physical_asset", "source_id": "cbex"},
            )
        )

        self.assertEqual(result["state"], "field_missing")
        latest = self.store.iter_latest_records(states=["field_missing"])
        self.assertEqual(len(latest), 1)
        findings = latest[0]["findings"]
        self.assertEqual([finding["type"] for finding in findings], ["canonical_field_missing"])
        self.assertEqual(findings[0]["evidence"]["missing_fields"], ["deal_price"])

    def test_ingest_cbex_deal_category_page_is_skipped_before_review_or_field_missing(self) -> None:
        def fake_parser(file_path: str):
            return {
                "record_family": "deal",
                "source_id": "cbex",
                "source_url": "https://www.cbex.com.cn/xm/zczr/fwtd/",
                "项目编号": "FWTD",
                "项目名称": "房屋土地",
                "项目类型": "实物资产",
                "项目状态": "成交",
                "交易所": "cbex",
                "collection_date": "2026-05-08",
                "deal_date_basis": "collection_date",
                "deal_date_is_imputed": True,
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="cbex",
                page_url="https://www.cbex.com.cn/xm/zczr/fwtd/",
                extra={"record_family": "deal", "business_id": "deal_physical_asset", "source_id": "cbex"},
            )
        )

        self.assertEqual(result["state"], "skipped")
        self.assertEqual(result["error_type"], "invalid_source_page")
        self.assertEqual(self.store.iter_latest_records(states=["pending_review"]), [])
        self.assertEqual(self.store.iter_latest_records(states=["field_missing"]), [])

    def test_refresh_postprocess_returns_projection_findings_for_deal_field_missing(self) -> None:
        self.store.upsert_record(
            record=IngestedRecord(
                record_id="rec-refresh-deal-field-missing",
                revision_hash="hash-rec-refresh-deal-field-missing-initial",
                project_code="XZSYZC",
                project_name="行政事业资产",
                project_type="实物资产",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=self.html_path,
                archive_path=self.html_path,
                parser_payload={
                    "项目编号": "XZSYZC",
                    "项目名称": "行政事业资产",
                    "项目类型": "实物资产",
                    "项目状态": "成交",
                    "交易所": "cbex",
                    "collection_date": "2026-05-08",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                },
                postprocess_payload={
                    "项目编号": "XZSYZC",
                    "项目名称": "行政事业资产",
                    "项目类型": "实物资产",
                    "项目状态": "成交",
                    "交易所": "cbex",
                    "collection_date": "2026-05-08",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                },
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "business_id_hint": "deal_physical_asset",
                    "source_id": "cbex",
                    "exchange": "cbex",
                    "original_source_file": self.html_path,
                },
            )
        )
        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=lambda _file_path: (_ for _ in ()).throw(AssertionError("parser should not run")),
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.refresh_postprocess("rec-refresh-deal-field-missing")

        self.assertEqual(result["state"], "field_missing")
        self.assertEqual([finding["type"] for finding in result["findings"]], ["canonical_field_missing"])
        stored = self.store.get_record("rec-refresh-deal-field-missing")
        self.assertEqual([finding["type"] for finding in stored["findings"]], ["canonical_field_missing"])

    def test_ingest_deal_missing_real_and_collection_date_does_not_fallback_to_listing_or_standard_dates(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026SSE000014",
                "项目名称": "缺全部成交日期入库项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "sse",
                "挂牌开始日期": "2026-03-20",
                "挂牌截止日期": "2026-04-30",
                "deal_price": "120",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="sse",
                listing_date="2026-03-20",
                extra={"record_family": "deal", "business_id": "deal_equity_transfer", "source_id": "sse"},
            )
        )

        self.assertEqual(result["state"], "field_missing")
        self.assertEqual(result["listing_date"], "")
        self.assertIn("unknown_month", result["archive_path"])
        finding_types = {str(item.get("type") or "") for item in result["findings"]}
        self.assertIn("deal_effective_date_missing", finding_types)
        self.assertIn("canonical_field_missing", finding_types)
        latest = self.store.iter_latest_records(states=["field_missing"])
        self.assertEqual(len(latest), 1)
        canonical_fields = latest[0]["canonical_record"]["canonical_fields"]
        self.assertEqual(latest[0]["listing_date"], "")
        self.assertEqual(latest[0]["source_identity_json"].get("listing_date"), "")
        self.assertEqual(canonical_fields.get("start_date"), "")
        self.assertEqual(canonical_fields.get("deal_date"), "")
        self.assertEqual(canonical_fields.get("collection_date", ""), "")
        diagnostic_types = {str(item.get("type") or "") for item in latest[0]["canonical_record"]["diagnostics"]}
        self.assertIn("deal_effective_date_missing", diagnostic_types)

    def test_ingest_canonicalizes_alias_source_id_for_deal_archive_and_identity(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "D32026SHALIAS001",
                "项目名称": "别名来源成交项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-20",
                "成交日期": "2026-04-19",
                "deal_price": "120",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="shanghai",
                listing_date="2026-03-20",
                extra={"record_family": "deal", "business_id": "deal_equity_transfer", "source_id": "shanghai"},
            )
        )

        latest = self.store.iter_latest_records(states=["ready"])
        archive_name = os.path.basename(result["archive_path"])
        self.assertEqual(result["state"], "ready")
        self.assertIn("_deal_deal_equity_transfer_sse", archive_name)
        self.assertNotIn("_deal_deal_equity_transfer_shanghai", archive_name)
        self.assertEqual(latest[0]["source_identity_json"].get("source_id"), "sse")
        self.assertEqual(latest[0]["canonical_record"]["source_identity"].get("source_id"), "sse")

    def test_canonical_archive_target_omits_catalog_default_listing_family(self) -> None:
        with patch("peap.streaming_ingest.get_family_descriptor", create=True) as get_descriptor:
            get_descriptor.return_value = SimpleNamespace(family_id="catalog_listing")

            target_path, had_conflict = _canonical_archive_target(
                archive_root=self.archive_root,
                project_code="P20260530001",
                project_name="测试项目",
                listing_date="2026-05-30",
                source_file=self.html_path,
                record_family="catalog_listing",
                business_id="equity_transfer",
                source_id="sse",
            )

        get_descriptor.assert_any_call("listing")
        self.assertFalse(had_conflict)
        self.assertEqual(os.path.basename(target_path), "P20260530001-测试项目.html")

    def test_canonical_archive_target_keeps_unknown_family_non_default(self) -> None:
        target_path, had_conflict = _canonical_archive_target(
            archive_root=self.archive_root,
            project_code="P20260530002",
            project_name="测试项目",
            listing_date="2026-05-30",
            source_file=self.html_path,
            record_family="Experimental_Family",
            business_id="bespoke_business",
            source_id="shanghai",
        )

        self.assertFalse(had_conflict)
        self.assertEqual(
            os.path.basename(target_path),
            "P20260530002_experimental_family_bespoke_business_sse-测试项目.html",
        )

    def test_default_ingest_parses_sse_deal_snapshot_and_preserves_deal_identity_fields(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "deal_snapshot_sse.html")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_capital_increase",
            "source_id": "sse",
            "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?xmid=XM123456",
            "project_code": "G62026SH000123",
            "project_name": "某增资扩股成交公告",
            "deal_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "deal_date_remark_suffix": "成交日期缺失，按采集日填列",
            "remark_suffix": "成交日期缺失，按采集日填列",
            "collection_date": "2026-04-20",
        }
        detail_payload = {
            "xmbh": "G62026SH000123",
            "xmmc": "某增资扩股成交公告",
            "xmlx": "增资扩股",
            "cjjg": "5000",
            "pgjz": "5100",
            "zrdf": "4800",
            "investors": [
                {"name": "投资方甲", "amount": "3000"},
                {"name": "投资方乙", "amount": "2000"},
            ],
            "financingPartyNames": ["融资方A"],
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'></head><body>"
                "<h1>SSE Deal Notice</h1>"
                "<script id='deal_metadata' type='application/json'>"
                + json.dumps(metadata, ensure_ascii=False)
                + "</script>"
                "<script id='deal_detail' type='application/json'>"
                + json.dumps(detail_payload, ensure_ascii=False)
                + "</script>"
                "</body></html>"
            )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="sse"))

        self.assertEqual(result["state"], "ready")
        latest = self.store.get_record(str(result["record_id"]))
        self.assertEqual(latest["record_family"], "deal")
        self.assertEqual(latest["business_id"], "deal_capital_increase")
        self.assertEqual(latest["project_code"], "G62026SH000123")
        self.assertEqual(latest["source_identity_json"].get("source_id"), "sse")
        self.assertTrue(str(latest["source_identity_json"].get("source_url") or "").startswith("https://www.suaee.com/"))

        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("deal_date"), "")
        self.assertEqual(canonical_fields.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(canonical_fields.get("deal_date_is_imputed")))
        self.assertEqual(canonical_fields.get("collection_date"), "2026/04/20")
        self.assertEqual(canonical_fields.get("deal_price"), "5000")
        self.assertEqual(canonical_fields.get("valuation"), "5100")
        self.assertEqual(canonical_fields.get("reserve_price"), "4800")

    def test_default_ingest_parses_sse_deal_original_html_with_sidecar_payload(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "sse_original_deal.html")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_physical_asset",
            "source_id": "sse",
            "source_url": "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
            "api_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=31285",
            "project_code": "GR2026SH1000563",
            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
            "deal_date": "2026-05-07",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "collection_date": "2026-05-08",
        }
        detail_payload = {
            "data": [
                {
                    "XMBH": "GR2026SH1000563",
                    "XMMC": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                    "XMLX": "实物资产",
                    "CJRQ": "2026-05-07",
                    "CJJG": "20.995922（万元）",
                    "PGZ": "21.000000（万元）",
                    "ZRDJ": "20.995922（万元）",
                }
            ]
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'></head><body>"
                "<div class='inside-title'>成交公告</div>"
                "<h1>上海江南长兴造船有限责任公司部分资产(平面流水线)</h1>"
                "<table><tr><td>项目编号</td><td>GR2026SH1000563</td><td>成交日期</td><td>2026-05-07</td></tr></table>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="sse"))

        self.assertEqual(result["state"], "ready")
        archived_sidecar = os.path.splitext(str(result["archive_path"]))[0] + ".json"
        self.assertTrue(os.path.isfile(archived_sidecar))
        latest = self.store.get_record(str(result["record_id"]))
        self.assertEqual(latest["source_identity_json"].get("source_url"), metadata["source_url"])
        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("deal_price"), "20.995922")
        self.assertEqual(canonical_fields.get("deal_price_unit"), "万元")
        self.assertEqual(canonical_fields.get("deal_price_unit_basis"), "raw_unit")
        self.assertEqual(canonical_fields.get("valuation"), "21.000000（万元）")
        self.assertEqual(canonical_fields.get("reserve_price"), "20.995922（万元）")

    def test_default_ingest_cbex_deal_sidecar_project_name_and_deal_date_override_page_title(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "cbex_deal_original.html")
        real_project_name = "东北中小企业融资再担保股份有限公司34,690,000股股份（占总股本的1.1365%）"
        metadata = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_id": "cbex",
            "source_url": "https://www.cbex.com.cn/xm/cqzr/cjjggs/",
            "project_code": "G32026BJ1000085",
            "project_name": real_project_name,
            "deal_date": "2026-04-29",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "collection_date": "2026-05-08",
        }
        detail_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000085",
                "object": real_project_name,
                "tradevalue": "4998.8605",
                "objectprice": "4998.8605万元",
                "tradedate": "2026-04-29",
            },
            "utrgcemsobject": {"objectevaluatevalue": "4998.86"},
        }
        textarea_payload = json.dumps(detail_payload, ensure_ascii=False).replace('"', "&quot;")
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'>"
                "<title>北京产权交易所_成交结果公示</title>"
                "</head><body>"
                "<div style='display:none'>"
                f"<textarea class='source' rows='3' cols='100'>{textarea_payload}</textarea>"
                "</div>"
                "<footer><a href='https://www.suaee.com/suaeeHome/#/home'>上海联合产权交易所</a></footer>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="cbex"))

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["project_code"], "G32026BJ1000085")
        self.assertEqual(result["project_name"], real_project_name)
        self.assertEqual(result["listing_date"], "2026/04/29")

        latest = self.store.get_record(str(result["record_id"]))
        self.assertEqual(latest["record_family"], "deal")
        self.assertEqual(latest["business_id"], "deal_equity_transfer")
        self.assertEqual(latest["project_name"], real_project_name)
        self.assertEqual(latest["listing_date"], "2026-04-29")
        self.assertEqual(latest["source_identity_json"].get("project_name"), real_project_name)
        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("project_name"), real_project_name)
        self.assertEqual(canonical_fields.get("deal_date"), "2026/04/29")
        self.assertFalse(bool(canonical_fields.get("deal_date_is_imputed")))

    def test_default_ingest_cbex_rendered_list_page_keeps_sidecar_target_identity_over_last_visible_row(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "cbex_rendered_list_multirow.html")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_id": "cbex",
            "source_url": "https://www.cbex.com.cn/xm/cqzr/cjjggs/",
            "project_code": "G32026BJ1000085",
            "project_name": "目标项目",
            "deal_date": "2026-04-29",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "collection_date": "2026-05-08",
        }
        detail_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000085",
                "object": "目标项目",
                "tradevalue": "4998.8605",
                "objectprice": "4998.8605万元",
                "tradedate": "2026-04-29",
            },
            "utrgcemsobject": {"objectevaluatevalue": "4998.86"},
        }
        last_row_payload = {
            "utrgcemsproject": {
                "projectcode": "G32025BJ1000729",
                "object": "最后一行项目",
                "tradevalue": "9999.99",
                "objectprice": "9999.99万元",
                "tradedate": "2025-12-31",
            },
            "utrgcemsobject": {"objectevaluatevalue": "8888.88"},
        }
        target_textarea = json.dumps(detail_payload, ensure_ascii=False).replace('"', "&quot;")
        last_row_textarea = json.dumps(last_row_payload, ensure_ascii=False).replace('"', "&quot;")
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'>"
                "<title>北京产权交易所_成交结果公示</title>"
                "</head><body>"
                "<div style='display:none'>"
                f"<textarea class='source' rows='3' cols='100'>{target_textarea}</textarea>"
                f"<textarea class='source' rows='3' cols='100'>{last_row_textarea}</textarea>"
                "</div>"
                "<table><thead><tr><th>项目编号</th><th>标的名称</th><th>交易价格（万元）</th></tr></thead>"
                "<tbody>"
                "<tr><td>G32026BJ1000085</td><td>目标项目</td><td>4998.8605</td></tr>"
                "<tr><td>G32025BJ1000729</td><td>最后一行项目</td><td>9999.99</td></tr>"
                "</tbody></table>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="cbex"))

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["project_code"], "G32026BJ1000085")
        self.assertEqual(result["project_name"], "目标项目")
        latest = self.store.get_record(str(result["record_id"]))
        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("project_code"), "G32026BJ1000085")
        self.assertEqual(canonical_fields.get("project_name"), "目标项目")
        self.assertEqual(canonical_fields.get("deal_price"), "4998.8605")
        self.assertEqual(canonical_fields.get("valuation"), "4998.86")
        self.assertEqual(canonical_fields.get("reserve_price"), "4998.8605万元")
        self.assertEqual(canonical_fields.get("deal_date"), "2026/04/29")

    def test_default_ingest_cbex_sidecar_metadata_survives_embedded_script_end_tag(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "cbex_sidecar_metadata_with_html.html")
        target_project_name = "目标项目"
        metadata = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_id": "cbex",
            "source_url": "https://www.cbex.com.cn/xm/cqzr/cjjggs/",
            "project_code": "G32026BJ1000085",
            "project_name": target_project_name,
            "collection_date": "2026-05-08",
            "list_html": "<tr><td>官方列表片段</td></tr><script>window.cbex = true;</script>",
        }
        target_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000085",
                "object": target_project_name,
                "tradevalue": "4998.8605",
                "objectprice": "4998.8605万元",
                "tradedate": "2026-04-29",
            },
            "utrgcemsobject": {"objectevaluatevalue": "4998.86"},
        }
        other_payload = {
            "utrgcemsproject": {
                "projectcode": "G32025BJ1000729",
                "object": "非目标项目",
                "tradevalue": "9999.99",
                "objectprice": "9999.99万元",
                "tradedate": "2026-04-30",
            },
            "utrgcemsobject": {"objectevaluatevalue": "9999.99"},
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'>"
                "<title>北京产权交易所_成交结果公示</title>"
                "</head><body>"
                "<table><thead><tr><th>项目编号</th><th>标的名称</th><th>交易价格（万元）</th></tr></thead>"
                "<tbody>"
                "<tr><td>G32025BJ1000729</td><td>非目标项目</td><td>9999.99</td></tr>"
                "<tr><td>G32026BJ1000085</td><td>目标项目</td><td>4998.8605</td></tr>"
                "</tbody></table>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {"metadata": metadata, "detail_payload": {"candidates": [other_payload, target_payload]}},
                handle,
                ensure_ascii=False,
            )

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="cbex"))

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["project_code"], "G32026BJ1000085")
        self.assertEqual(result["project_name"], target_project_name)
        latest = self.store.get_record(str(result["record_id"]))
        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("project_code"), "G32026BJ1000085")
        self.assertEqual(canonical_fields.get("project_name"), target_project_name)
        self.assertEqual(canonical_fields.get("deal_price"), "4998.8605")

    def test_default_ingest_keeps_sse_capital_increase_pending_review_when_sidecar_only_has_summary_amount(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "sse_capital_missing_investor_amount.html")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_capital_increase",
            "source_id": "sse",
            "source_url": "https://www.suaee.com/jyxx.html#/xxggDetail?ID=864&FCLASS=cjgg1C&skipDateCheck=1",
            "api_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=864",
            "detail_api_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=864",
            "collection_date": "2026-05-08",
            "deal_date": "2026-04-28",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "project_code": "G62024SH1000060",
            "project_name": "上海新微科技集团有限公司增资项目",
        }
        detail_payload = {
            "code": 200,
            "msg": "查询成功",
            "extra": {"total": 1},
            "data": [
                {
                    "ZJCZE": "20000.000000",
                    "ZZZHBL": "2.059949",
                    "TZFMC": "总计",
                },
            ],
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'></head><body>"
                "<div class='inside-title'>成交公告</div>"
                "<h1>上海新微科技集团有限公司增资项目</h1>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="sse"))

        self.assertEqual(result["state"], "pending_review")
        self.assertTrue(
            any(
                str(item.get("type") or "") == "business_resolution_required"
                and str((item.get("evidence") or {}).get("reason_code") or "") == "deal_capital_increase_missing_investor"
                and "缺少非汇总投资方" in str(item.get("message") or "")
                for item in result["findings"]
            )
        )
        latest = self.store.get_record(str(result["record_id"]))
        postprocess_payload = latest["postprocess_payload"]
        self.assertEqual(postprocess_payload.get("total_investment_amount"), "20000.000000")
        self.assertEqual(postprocess_payload.get("holding_ratio"), "2.059949")
        self.assertNotIn("investors", postprocess_payload)
        self.assertNotIn("investment_amount", postprocess_payload)

    def test_default_ingest_marks_sse_capital_increase_ready_when_sidecar_has_non_summary_investor_amount(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "sse_capital_investor_amount_ready.html")
        metadata = {
            "record_family": "deal",
            "business_id": "deal_capital_increase",
            "source_id": "sse",
            "source_url": "https://www.suaee.com/jyxx.html#/xxggDetail?ID=864&FCLASS=cjgg1C&skipDateCheck=1",
            "api_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=864",
            "detail_api_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=864",
            "collection_date": "2026-05-08",
            "deal_date": "2026-04-28",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "project_code": "G62024SH1000060",
            "project_name": "上海新微科技集团有限公司增资项目",
        }
        detail_payload = {
            "code": 200,
            "msg": "查询成功",
            "extra": {"total": 2},
            "data": [
                {
                    "ZZFQYMC": "上海新微科技集团有限公司",
                    "XMMC": "上海新微科技集团有限公司增资项目",
                    "ZJCZE": "20000.000000",
                    "ZZZHBL": "2.059949",
                    "XMBH": "G62024SH1000060",
                    "XMID": 864,
                    "CJRQ": "2026-04-28",
                    "TZFMC": "上海思秘科企业管理服务合伙企业（有限合伙）",
                    "T_TZF": 2881,
                },
                {
                    "ZJCZE": "20000.000000",
                    "ZZZHBL": "2.059949",
                    "TZFMC": "总计",
                },
            ],
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'></head><body>"
                "<div class='inside-title'>成交公告</div>"
                "<h1>上海新微科技集团有限公司增资项目</h1>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="sse"))

        self.assertEqual(result["state"], "ready")
        self.assertFalse(
            any(
                str(item.get("type") or "") == "business_resolution_required"
                and str((item.get("evidence") or {}).get("reason_code") or "") == "deal_capital_increase_missing_investor_amount"
                for item in result["findings"]
            )
        )
        latest = self.store.get_record(str(result["record_id"]))
        postprocess_payload = latest["postprocess_payload"]
        self.assertEqual(postprocess_payload.get("project_code"), "G62024SH1000060")
        self.assertEqual(postprocess_payload.get("investment_amount"), "20000.000000")
        self.assertEqual(
            postprocess_payload.get("investors"),
            [
                {
                    "name": "上海思秘科企业管理服务合伙企业（有限合伙）",
                    "amount": "20000.000000",
                    "ratio": "2.059949",
                }
            ],
        )

    def test_default_ingest_keeps_cquae_html_project_name_over_sidecar_candidate(self) -> None:
        snapshot_path = os.path.join(self.temp_dir.name, "cquae_original_deal.html")
        html_project_name = "湖北鹏程保险经纪有限公司5.0195%股权"
        metadata = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_id": "cquae",
            "source_url": "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53332",
            "collection_date": "2026-05-08",
            "deal_date": "2026-04-28",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "project_code": "G32025CQ1000152",
            "project_name": "candidate",
        }
        detail_payload = {
            "projectCode": "G32025CQ1000152",
            "dealAmount": "95.255052",
            "valuationValue": "95.255052",
            "reservePrice": "95.255052",
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><head><meta charset='utf-8'>"
                f"<title>{html_project_name} - 重庆产权交易网</title>"
                "</head><body>"
                "<div class='detail-title'>交易结果公示</div>"
                "<table>"
                "<tr><th>标的名称</th><td>" + html_project_name + "</td></tr>"
                "<tr><th>项目编号</th><td>G32025CQ1000152</td></tr>"
                "<tr><th>成交日期</th><td>2026/4/28</td></tr>"
                "<tr><th>成交金额</th><td>95.255052</td></tr>"
                "</table>"
                "</body></html>"
            )
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata, "detail_payload": detail_payload}, handle, ensure_ascii=False)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path, exchange="cquae"))

        self.assertEqual(result["state"], "ready")
        latest = self.store.get_record(str(result["record_id"]))
        self.assertEqual(latest["project_name"], html_project_name)
        self.assertEqual(latest["project_code"], "G32025CQ1000152")
        self.assertEqual(latest["state"], "ready")
        self.assertEqual(latest["source_identity_json"].get("source_url"), metadata["source_url"])
        canonical_fields = dict(latest["canonical_record"]["canonical_fields"])
        self.assertEqual(canonical_fields.get("project_name"), html_project_name)
        self.assertEqual(latest["canonical_projection"]["项目名称"], html_project_name)

    def test_default_ingest_uses_cquae_result_sidecar_family_without_explicit_scope(self) -> None:
        project_code = "G32026CQ1000062"
        project_name = "长安福特新能源汽车科技有限公司40%股权"
        snapshot_path = os.path.join(
            self.temp_dir.name,
            f"{project_code}_deal_deal_equity_transfer_cquae-{project_name}.html",
        )
        html = (
            "<html><head><meta charset='utf-8'>"
            f"<title>{project_name} - 重庆产权交易网</title>"
            "</head><body><table>"
            f"<tr><th>标的名称</th><td>{project_name}</td></tr>"
            f"<tr><th>项目编号</th><td>{project_code}</td></tr>"
            "<tr><th>成交日期</th><td>2026/7/2</td></tr>"
            "<tr><th>交易价格（万元）</th><td>15384.92</td></tr>"
            "</table></body></html>"
        )
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        with open(os.path.splitext(snapshot_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "metadata": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "business_label": "股权转让",
                        "source_id": "cquae",
                        "source_url": "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54750",
                        "project_code": project_code,
                        "project_name": project_name,
                        "deal_date": "2026-07-02",
                        "collection_date": "2026-07-06",
                    },
                },
                handle,
                ensure_ascii=False,
            )

        runner = StreamingIngestRunner(store=self.store, archive_root=self.archive_root)
        result = runner.ingest(ItemSavedPayload(source_file=snapshot_path))

        record = self.store.get_record(str(result["record_id"]))
        self.assertEqual(record["record_family"], "deal")
        self.assertEqual(record["business_id"], "deal_equity_transfer")
        self.assertEqual(record["source_identity_json"]["source_id"], "cquae")
        self.assertEqual(record["parser_payload"]["deal_date"], "2026/07/02")
        self.assertEqual(record["parser_payload"]["deal_price"], "15384.92")
        self.assertNotEqual(record["state"], "pending_mapping")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_ingest_record_missing_source_type_still_becomes_pending_mapping(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000194-4",
                "项目名称": "缺类型项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "转让方": "上海电气集团恒联企业发展有限公司",
                "隶属集团": "上海电气集团",
            }

        def fake_postprocess(payload, **kwargs):
            return dict(payload), []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "pending_mapping")
        pending = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(pending), 1)
        findings = pending[0]["findings"]
        self.assertTrue(any(str(item.get("type") or "") == "mapping_missing" for item in findings))
        self.assertIn("类型", str(findings[0].get("message") or ""))

    def test_ingest_skip_parse_is_recorded_as_skipped(self) -> None:
        def fake_parser(file_path: str):
            raise SkipParse(f"skip-cbex-otc-page: {file_path}")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                project_code="GR2026BJ1001615",
                exchange="beijing",
            )
        )

        self.assertEqual(result["state"], "skipped")
        self.assertEqual(result["error_type"], "skip_parse")
        self.assertIn("skip-cbex-otc-page", result["error_message"])
        skipped = self.store.iter_latest_records(states=["skipped"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["state"], "skipped")

    def test_ingest_ready_record_persists_snapshot_identity_and_canonical_projection(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000200",
                "项目名称": "带谱系项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "上海测试公司",
                "page_url": "https://example.test/detail/lineage",
                "project_id": "LINEAGE001",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            updated["canonical_projection"] = {
                "项目编号": payload["项目编号"],
                "项目名称": payload["项目名称"],
                "项目类型": payload["项目类型"],
                "转让方": payload["转让方"],
            }
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="shanghai",
                page_url="https://example.test/detail/lineage",
                extra={"project_id": "LINEAGE001"},
            )
        )

        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["parser_payload"]["page_url"], "https://example.test/detail/lineage")
        self.assertEqual(latest[0]["parser_payload"]["project_id"], "LINEAGE001")
        self.assertEqual(latest[0]["postprocess_payload"]["page_url"], "https://example.test/detail/lineage")
        self.assertEqual(latest[0]["postprocess_payload"]["project_id"], "LINEAGE001")
        self.assertEqual(latest[0]["project_code"], "G32025SH1000200")
        self.assertEqual(latest[0]["project_name"], "带谱系项目")
        self.assertEqual(latest[0]["source_identity_json"]["original_source_file"], result["archive_path"])
        self.assertEqual(latest[0]["source_identity_json"]["original_evidence_path"], self.html_path)
        self.assertEqual(latest[0]["source_identity_json"]["source_url"], "https://example.test/detail/lineage")
        self.assertEqual(
            latest[0]["source_identity_json"]["candidate_tokens"],
            [
                "project_code:G32025SH1000200",
                "project_id:LINEAGE001",
                "page_url:https://example.test/detail/lineage",
            ],
        )
        self.assertEqual(
            latest[0]["canonical_record"]["canonical_fields"]["project_code"],
            "G32025SH1000200",
        )
        self.assertEqual(
            latest[0]["canonical_record"]["canonical_fields"]["project_name"],
            "带谱系项目",
        )
        self.assertEqual(
            latest[0]["canonical_record"]["canonical_fields"]["seller"],
            "上海测试公司",
        )
        self.assertEqual(
            latest[0]["canonical_record"]["canonical_fields"]["source_type"],
            "国资",
        )
        self.assertEqual(
            latest[0]["canonical_record"]["record_id"],
            latest[0]["record_id"],
        )
        self.assertEqual(latest[0]["canonical_projection"]["项目编号"], "G32025SH1000200")
        self.assertEqual(latest[0]["canonical_projection"]["项目名称"], "带谱系项目")
        self.assertEqual(latest[0]["canonical_projection"]["项目类型"], "股权转让")
        self.assertEqual(latest[0]["canonical_projection"]["转让方"], "上海测试公司")
        self.assertEqual(latest[0]["canonical_projection"]["类型"], "国资")
        self.assertEqual(latest[0]["canonical_projection"]["挂牌开始日期"], "2026-03-21")
        self.assertEqual(latest[0]["canonical_projection"]["交易所"], "shanghai")
        self.assertNotIn("canonical_projection", latest[0]["postprocess_payload"])

    def test_ingest_canonical_projection_ignores_stale_postprocess_seed(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000201",
                "项目名称": "收敛项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "规范化卖方",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            updated["canonical_projection"] = {
                "项目编号": payload["项目编号"],
                "项目名称": "过期项目名",
                "项目类型": payload["项目类型"],
                "转让方": "过期卖方",
                "挂牌价格": "999.99",
            }
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        latest = self.store.iter_latest_records(states=["ready"])

        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["canonical_projection"]["项目名称"], "收敛项目")
        self.assertEqual(latest[0]["canonical_projection"]["转让方"], "规范化卖方")
        self.assertEqual(latest[0]["canonical_projection"]["类型"], "国资")
        self.assertEqual(latest[0]["canonical_projection"]["挂牌价格"], "100.00")

    def test_ingest_repeated_business_key_preserves_persisted_record_identity(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000202",
                "项目名称": "重复入库项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "转让方": "上海测试公司",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        first = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        second = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))
        record = self.store.get_record(first["record_id"])

        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(record["record_id"], second["record_id"])
        self.assertEqual(record["canonical_record"]["record_id"], record["record_id"])

    def test_ingest_parse_failure_preserves_typed_failure_taxonomy(self) -> None:
        def fake_parser(file_path: str):
            raise RuntimeError("decode_failed: malformed snapshot")

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="shanghai",
                project_code="FAIL-001",
            )
        )

        failed = self.store.iter_latest_records(states=["parse_failed"])
        self.assertEqual(result["state"], "parse_failed")
        self.assertEqual(result["error_type"], "decode_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["last_error_type"], "decode_failed")
        self.assertIn("decode_failed", failed[0]["last_error_message"])

    def test_ingest_ready_record_uses_existing_canonical_file_without_copy(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "G32025SH1000194-测试项目.html")
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>ok</body></html>")
        os.makedirs(f"{os.path.splitext(canonical_path)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(canonical_path)[0]}_files/style.css", "w", encoding="utf-8") as handle:
            handle.write("body{}")

        def fake_parser(file_path: str):
            self.assertEqual(file_path, canonical_path)
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "转让方": "上海电气集团恒联企业发展有限公司",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=canonical_path, exchange="shanghai"))

        self.assertEqual(result["archive_path"], canonical_path)
        self.assertTrue(os.path.isfile(canonical_path))
        self.assertFalse(os.path.exists(os.path.join(canonical_dir, "G32025SH1000194-测试项目__conflict1.html")))

    def test_ingest_reuses_existing_canonical_target_for_same_identity_without_conflict_copy(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "G32025SH1000194-测试项目.html")
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>existing</body></html>")

        staged_path = os.path.join(self.temp_dir.name, "incoming.html")
        with open(staged_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>existing</body></html>")

        def fake_parser(file_path: str):
            self.assertEqual(file_path, staged_path)
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=staged_path, exchange="shanghai"))

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["archive_path"], canonical_path)
        self.assertFalse(os.path.exists(os.path.join(canonical_dir, "G32025SH1000194-测试项目__conflict1.html")))
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["archive_path"], canonical_path)

    def test_resolve_reuses_current_conflict_variant_on_reprocess(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "G32025SH1000194-测试项目.html")
        conflict_path = os.path.splitext(canonical_path)[0] + "__conflict1.html"
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("old snapshot")
        with open(conflict_path, "w", encoding="utf-8") as handle:
            handle.write("current snapshot")

        resolved, had_conflict = resolve_submission_snapshot_target(
            archive_root=self.archive_root,
            project_code="G32025SH1000194",
            project_name="测试项目",
            listing_date="2026-03-21",
            current_path=conflict_path,
            reuse_current_conflict=True,
        )

        self.assertEqual(resolved, conflict_path)
        self.assertFalse(had_conflict)

    def test_ingest_moves_workspace_snapshot_into_canonical_archive_path(self) -> None:
        listed_dir = os.path.join(self.archive_root, "挂牌_实物资产")
        os.makedirs(listed_dir, exist_ok=True)
        staged_path = os.path.join(listed_dir, "GR2026SH1000324-4.html")
        with open(staged_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body><img src=\"GR2026SH1000324-4_files/image.png\" /></body></html>")
        os.makedirs(f"{os.path.splitext(staged_path)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(staged_path)[0]}_files/image.png", "wb") as handle:
            handle.write(b"png")

        def fake_parser(file_path: str):
            self.assertEqual(file_path, staged_path)
            return {
                "项目编号": "GR2026SH1000324-4",
                "项目名称": "淮安市淮阴医院有限公司部分资产（一台双源CT机）",
                "项目类型": "实物资产",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "淮安市淮阴医院有限公司",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            updated["类型"] = "国资"
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=staged_path, exchange="shanghai"))

        expected_prefix = os.path.join(self.archive_root, "2026年3月", "GR2026SH1000324-4-淮安市淮阴医院有限公司部分资产（一台双源CT机）")
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["archive_path"], f"{expected_prefix}.html")
        self.assertTrue(os.path.isfile(result["archive_path"]))
        self.assertTrue(os.path.isdir(f"{expected_prefix}_files"))
        self.assertFalse(os.path.exists(staged_path))
        self.assertFalse(os.path.exists(f"{os.path.splitext(staged_path)[0]}_files"))

        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(latest[0]["source_file"], result["archive_path"])
        self.assertEqual(latest[0]["archive_path"], result["archive_path"])

    def test_ingest_cbex_otc_fixture_without_business_hint_classifies_business_before_mapping_gap(self) -> None:
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
                "expiredate": "2026-03-31",
                "objectprice": "100.00"
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
        fixture_dir = os.path.join(self.temp_dir.name, "挂牌_实物资产")
        os.makedirs(fixture_dir, exist_ok=True)
        fixture_path = os.path.join(fixture_dir, "cbex-otc-recoverable.html")
        with open(fixture_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=fixture_path,
                exchange="beijing",
                project_code="GR2026BJ1999001",
            )
        )

        self.assertEqual(result["state"], "pending_mapping")
        self.assertEqual(result["project_code"], "GR2026BJ1999001")
        self.assertEqual(self.store.iter_latest_records(states=["pending_review"]), [])
        latest = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["project_code"], "GR2026BJ1999001")
        self.assertEqual(latest[0]["project_type"], "实物资产")
        self.assertEqual(latest[0]["business_id"], "physical_asset")
        self.assertEqual(latest[0]["postprocess_payload"]["项目类型"], "实物资产")
        finding_types = {str(item.get("type") or "") for item in latest[0]["findings"]}
        self.assertNotIn("business_resolution_required", finding_types)
        self.assertIn("mapping_missing", finding_types)

    def test_ingest_accepts_upstream_project_type_fallback_without_path_inference(self) -> None:
        html = """
        <html>
          <head>
            <title>北交互联-报废设备一批</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>
            <textarea id="jsonobj">{
              "object": {
                "projectcode": "GR2026BJ1999003",
                "object": "报废设备一批",
                "publishdate": "2026-03-21",
                "expiredate": "2026-03-31",
                "objectprice": "100.00"
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
        fixture_path = os.path.join(self.temp_dir.name, "cbex-otc-upstream-known.html")
        with open(fixture_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=_default_parse_file,
                postprocess=lambda payload, **kwargs: ({**dict(payload), "类型": "国资"}, []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=fixture_path,
                exchange="beijing",
                project_code="GR2026BJ1999003",
                extra={"project_type_fallback": "physical_asset"},
            )
        )

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["project_type"], "实物资产")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["project_code"], "GR2026BJ1999003")
        self.assertEqual(latest[0]["project_type"], "实物资产")
        self.assertEqual(latest[0]["postprocess_payload"]["项目类型"], "实物资产")

    def test_ingest_applies_optional_rule_immediately_for_fallback_business(self) -> None:
        html = """
        <html>
          <head>
            <title>北交互联-报废设备一批</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>
            <textarea id="jsonobj">{
              "object": {
                "projectcode": "GR2026BJ1999004",
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
        fixture_path = os.path.join(self.temp_dir.name, "cbex-otc-mapping-refresh.html")
        with open(fixture_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            rules_config={
                "R010_filter_scrap_physical_asset": {
                    "enabled": True,
                    "priority": 5,
                    "params": {"active": True, "severity": "info", "search_all_fields": True},
                }
            },
            dependencies=StreamingIngestDependencies(parser=_default_parse_file),
        )

        initial = runner.ingest(
            ItemSavedPayload(
                source_file=fixture_path,
                exchange="beijing",
                project_code="GR2026BJ1999004",
                extra={"project_type_fallback": "physical_asset"},
            )
        )

        self.assertEqual(initial["state"], "skipped")
        self.assertEqual(self.store.iter_latest_records(states=["ready"]), [])
        latest = self.store.get_record(initial["record_id"])
        finding_types = {
            str(item.get("type") or "")
            for item in list(latest.get("findings") or [])
            if isinstance(item, dict)
        }
        self.assertEqual(latest["project_type"], "实物资产")
        self.assertEqual(latest["postprocess_payload"]["项目类型"], "实物资产")
        self.assertIn("rule_filtered", finding_types)
        self.assertIn("scrap_physical_asset_filtered", finding_types)
        self.assertIn("mapping_missing", finding_types)
        self.assertIn("mapping_missing", finding_types)

    def test_ingest_does_not_let_project_type_fallback_override_parser_value(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32026BJ1000005",
                "项目名称": "回刷纠正类型项目",
                "项目类型": "实物资产",
                "交易所": "beijing",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="beijing",
                project_code="G32026BJ1000005",
                extra={"project_type_fallback": "equity_transfer"},
            )
        )

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(latest[0]["project_type"], "实物资产")
        self.assertEqual(latest[0]["postprocess_payload"]["项目类型"], "实物资产")

    def test_ingest_cbex_otc_fixture_without_registry_match_records_parse_failure(self) -> None:
        html = """
        <html>
          <head>
            <title>北交互联</title>
            <meta name="keywords" content="北交互联" />
          </head>
          <body>欢迎来到北交互联</body>
        </html>
        """
        fixture_path = os.path.join(self.temp_dir.name, "cbex-otc-empty.html")
        with open(fixture_path, "w", encoding="utf-8") as handle:
            handle.write(html)

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
        )

        result = runner.ingest(
            ItemSavedPayload(
                source_file=fixture_path,
                exchange="beijing",
                project_code="GR2026BJ1999002",
            )
        )

        self.assertEqual(result["state"], "parse_failed")
        self.assertEqual(result["error_type"], "parse_failed")
        self.assertIn("No source markers matched the document", result["error_message"])
        failed = self.store.iter_latest_records(states=["parse_failed"])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["state"], "parse_failed")

    def test_ingest_conflict_does_not_hide_pending_mapping_state(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "G32025SH1000194-测试项目.html")
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>existing</body></html>")

        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "转让方": "未知公司",
            }

        def fake_postprocess(payload, **kwargs):
            return dict(payload), [
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="missing mapping",
                    evidence={},
                )
            ]

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "pending_mapping")
        pending = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(pending), 1)

    def test_ingest_unknown_business_label_enters_pending_review(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "UNKNOWN-001",
                "项目名称": "未知类型项目",
                "项目类型": "未知",
                "交易所": "beijing",
                "挂牌开始日期": "2026-03-21",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="beijing"))

        self.assertEqual(result["state"], "pending_review")
        self.assertEqual(self.store.iter_latest_records(states=["pending_mapping"]), [])
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        business_blocker = next(
            item for item in latest[0]["findings"] if str(item.get("type") or "") == "business_resolution_required"
        )
        self.assertEqual(business_blocker["evidence"].get("raw_business_label", ""), "")

    def test_ingest_missing_business_label_enters_pending_review(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "UNKNOWN-EMPTY-001",
                "项目名称": "空业务类型项目",
                "项目类型": "",
                "交易所": "beijing",
                "挂牌开始日期": "2026-03-21",
                "转让方": "测试公司",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="beijing"))

        self.assertEqual(result["state"], "pending_review")
        self.assertEqual(self.store.iter_latest_records(states=["pending_mapping"]), [])
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        business_blocker = next(
            item for item in latest[0]["findings"] if str(item.get("type") or "") == "business_resolution_required"
        )
        self.assertEqual(business_blocker["evidence"]["raw_business_label"], "")

    def test_ingest_missing_authoritative_record_family_enters_pending_review(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1003999",
                "项目名称": "缺权威family主链项目",
                "项目类型": "股权转让",
                "挂牌开始日期": "2026-03-21",
                "转让方": "测试转让方",
                "类型": "国资",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange=""))

        self.assertEqual(result["state"], "pending_review")
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        family_blocker = next(
            item for item in latest[0]["findings"] if str(item.get("type") or "") == "record_family_authority_missing"
        )
        self.assertEqual(family_blocker["evidence"].get("stage"), "ingest")
        self.assertEqual(family_blocker["evidence"].get("classified_record_family"), "listing")

    def test_ingest_uses_atomic_record_and_mapping_pending_upsert(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "sync-policy-test.html")
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>sync policy test</body></html>")

        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000299",
                "项目名称": "同步策略测试",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "100.00",
                "转让方": "测试公司",
                "类型": "国资",
            }

        def fake_postprocess(payload, **kwargs):
            # Return ready findings -> record should be READY and backlog resolved
            return dict(payload), []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        with patch.object(
            self.store,
            "upsert_record_with_mapping_pending",
            wraps=self.store.upsert_record_with_mapping_pending,
        ) as mock_atomic_upsert:
            result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "ready")
        mock_atomic_upsert.assert_called_once()
        stored_record = mock_atomic_upsert.call_args.args[0]
        self.assertEqual(stored_record.record_id, result["record_id"])
        self.assertEqual(stored_record.state, "ready")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_ingest_mapping_conflict_record_uses_atomic_sync_policy(self) -> None:
        canonical_dir = os.path.join(self.archive_root, "2026年3月")
        os.makedirs(canonical_dir, exist_ok=True)
        canonical_path = os.path.join(canonical_dir, "mc-sync-test.html")
        with open(canonical_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>mc sync test</body></html>")

        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000300",
                "项目名称": "冲突同步测试",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
            }

        def fake_postprocess(payload, **kwargs):
            return dict(payload), [
                PostProcessFinding(
                    severity="error",
                    type="mapping_conflict",
                    message="mapping conflict detected",
                    evidence={},
                )
            ]

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        with patch.object(
            self.store,
            "upsert_record_with_mapping_pending",
            wraps=self.store.upsert_record_with_mapping_pending,
        ) as mock_atomic_upsert:
            result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "mapping_conflict")
        mock_atomic_upsert.assert_called_once()
        stored_record = mock_atomic_upsert.call_args.args[0]
        self.assertEqual(stored_record.state, "mapping_conflict")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_ingest_mapping_sync_failure_rolls_back_record_revision_backlog_and_audit(self) -> None:
        def fake_parser(_file_path: str):
            return {
                "项目编号": "G32026BJ1000999",
                "项目名称": "原子写入回滚测试",
                "项目类型": "股权转让",
                "交易所": "beijing",
                "挂牌开始日期": "2026-07-10",
                "转让方": "待映射公司",
                "record_family": "listing",
                "source_id": "cbex",
            }

        def pending_postprocess(payload, **_kwargs):
            return dict(payload), [
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="mapping missing",
                    evidence={},
                )
            ]

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=pending_postprocess,
            ),
        )
        with sqlite3.connect(self.db_path) as conn:
            audit_count_before = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

        with patch.object(
            self.store,
            "_sync_mapping_pending_for_record",
            side_effect=RuntimeError("injected mapping sync failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected mapping sync failure"):
                runner.ingest(
                    ItemSavedPayload(
                        source_file=self.html_path,
                        exchange="beijing",
                        extra={
                            "source_id": "cbex",
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                        },
                    )
                )

        with sqlite3.connect(self.db_path) as conn:
            persisted_counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("records", "record_revisions", "mapping_pending")
            }
            audit_count_after = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        self.assertEqual(persisted_counts, {"records": 0, "record_revisions": 0, "mapping_pending": 0})
        self.assertEqual(audit_count_after, audit_count_before)

    def test_refresh_mapping_sync_failure_rolls_back_revision_state_backlog_and_audit(self) -> None:
        def fake_parser(_file_path: str):
            return {
                "项目编号": "G32026BJ1001000",
                "项目名称": "原子刷新回滚测试",
                "项目类型": "股权转让",
                "交易所": "beijing",
                "挂牌开始日期": "2026-07-11",
                "挂牌价格": "100.00",
                "转让方": "待映射刷新公司",
                "record_family": "listing",
                "source_id": "cbex",
            }

        def pending_postprocess(payload, **_kwargs):
            return dict(payload), [
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="mapping missing",
                    evidence={},
                )
            ]

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=pending_postprocess,
            ),
        )
        created = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="beijing",
                extra={
                    "source_id": "cbex",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                },
            )
        )
        self.assertEqual(created["state"], "pending_mapping")
        record_before = self.store.get_record(created["record_id"])
        with sqlite3.connect(self.db_path) as conn:
            revision_count_before = int(conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0])
            pending_before = conn.execute(
                "SELECT revision_id, payload_json, resolved_at FROM mapping_pending WHERE record_id = ?",
                (created["record_id"],),
            ).fetchone()
            audit_count_before = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

        def ready_postprocess(payload, **_kwargs):
            updated = dict(payload)
            updated["隶属集团"] = "原子刷新测试集团"
            updated["类型"] = "央企"
            return updated, []

        runner.dependencies = StreamingIngestDependencies(
            parser=fake_parser,
            postprocess=ready_postprocess,
        )
        original_sync = self.store._sync_mapping_pending_for_record
        observed_state = ""

        def sync_then_fail(conn, **kwargs):
            nonlocal observed_state
            state_value = kwargs.get("state")
            observed_state = str(getattr(state_value, "value", state_value) or "")
            original_sync(conn, **kwargs)
            raise RuntimeError("injected refresh sync failure")

        with patch.object(
            self.store,
            "_sync_mapping_pending_for_record",
            side_effect=sync_then_fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected refresh sync failure"):
                runner.refresh_postprocess(created["record_id"])

        record_after = self.store.get_record(created["record_id"])
        with sqlite3.connect(self.db_path) as conn:
            revision_count_after = int(conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0])
            pending_after = conn.execute(
                "SELECT revision_id, payload_json, resolved_at FROM mapping_pending WHERE record_id = ?",
                (created["record_id"],),
            ).fetchone()
            audit_count_after = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

        self.assertEqual(observed_state, "ready")
        self.assertEqual(record_after["state"], record_before["state"])
        self.assertEqual(record_after["revision_id"], record_before["revision_id"])
        self.assertEqual(record_after["postprocess_payload"], record_before["postprocess_payload"])
        self.assertEqual(revision_count_after, revision_count_before)
        self.assertEqual(pending_after, pending_before)
        self.assertEqual(audit_count_after, audit_count_before)


class StreamingIngestCanonicalFieldRegressionTest(unittest.TestCase):
    """Regression tests for canonical field preservation in streaming ingest."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming_canonical.sqlite3")
        self.archive_root = os.path.join(self.temp_dir.name, "submission")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.html_path = os.path.join(self.temp_dir.name, "canonical_test.html")
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>canonical field test</body></html>")

    def test_ingest_preserves_canonical_fields_through_assemble_normalize(self) -> None:
        """Regression: ingest must preserve canonical fields through assemble -> normalize.

        Fields like project_type, status, start_date, price, seller must be preserved.
        """
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "规范场测试项目",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "108.00",
                "转让方": "上海测试公司",
                "项目状态": "挂牌中",
                "类型": "国资",
            }

        def fake_postprocess(payload, **kwargs):
            updated = dict(payload)
            # Simulate canonical normalization that should preserve these fields
            updated["canonical_projection"] = {
                "项目编号": payload["项目编号"],
                "项目名称": payload["项目名称"],
                "项目类型": payload["项目类型"],
                "挂牌开始日期": payload["挂牌开始日期"],
                "挂牌价格": payload["挂牌价格"],
                "转让方": payload["转让方"],
            }
            return updated, []

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=fake_postprocess,
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)

        # Canonical fields must be preserved in the stored record
        record = latest[0]

        # project_type must be preserved
        self.assertEqual(record["project_type"], "股权转让")

        # canonical_record must contain all required fields
        canonical = record.get("canonical_record", {})
        canonical_fields = canonical.get("canonical_fields", {})

        # These fields must be preserved through the canonical chain
        self.assertIn("project_type", canonical_fields, "project_type must be in canonical_fields")
        self.assertIn("status", canonical_fields, "status must be in canonical_fields")
        self.assertIn("start_date", canonical_fields, "start_date must be in canonical_fields")
        self.assertIn("price", canonical_fields, "price must be in canonical_fields")
        self.assertIn("seller", canonical_fields, "seller must be in canonical_fields")

    def test_ingest_bridges_standard_field_keys_into_canonical_record_and_listing_date(self) -> None:
        """Regression: standard English parser keys must still feed canonical/export fields."""

        def fake_parser(file_path: str):
            return {
                "project_code": "G62025SH1000038",
                "project_name": "武汉华润置地焕新置业开发有限公司增资项目",
                "project_type": "增资扩股",
                "status": "挂牌",
                "exchange": "上交所",
                "source_type": "央企",
                "seller": "武汉华润置地焕新置业开发有限公司",
                "price": "视征集情况而定",
                "start_date": "2026/02/28",
                "group_name": "中国华润有限公司",
                "项目编号": "G62025SH1000038",
                "项目名称": "武汉华润置地焕新置业开发有限公司增资项目",
                "项目类型": "增资扩股",
                "项目状态": "挂牌",
                "交易所": "上交所",
                "类型": "央企",
            }

        runner = StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=fake_parser,
                postprocess=lambda payload, **kwargs: (dict(payload), []),
            ),
        )

        result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(latest), 1)
        record = latest[0]
        canonical_fields = record["canonical_record"]["canonical_fields"]

        self.assertEqual(record["listing_date"], "2026-02-28")
        self.assertEqual(canonical_fields["start_date"], "2026/02/28")
        self.assertEqual(canonical_fields["seller"], "武汉华润置地焕新置业开发有限公司")
        self.assertEqual(canonical_fields["price"], "视征集情况而定")
        self.assertEqual(canonical_fields["group_name"], "中国华润有限公司")
        self.assertEqual(record["canonical_projection"]["挂牌开始日期"], "2026/02/28")
        self.assertEqual(record["canonical_projection"]["挂牌价格"], "视征集情况而定")
        self.assertEqual(record["canonical_projection"]["转让方"], "武汉华润置地焕新置业开发有限公司")


class StreamingIngestProvenanceTest(unittest.TestCase):
    """Canonical archive paths must not create provenance-only revisions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "provenance.sqlite3")
        self.archive_root = os.path.join(self.temp_dir.name, "archive")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.project_code = "G32026SH1099999"
        self.project_name = "冲突路径幂等测试项目"
        self.html = b"<html><body>same canonical snapshot</body></html>"

    def _runner(self) -> StreamingIngestRunner:
        def parser(_path: str):
            return {
                "项目编号": self.project_code,
                "项目名称": self.project_name,
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-08-16",
                "转让方": "测试转让方",
                "类型": "国资",
                "source_id": "shanghai",
            }

        return StreamingIngestRunner(
            store=self.store,
            archive_root=self.archive_root,
            dependencies=StreamingIngestDependencies(
                parser=parser,
                postprocess=lambda payload, **_kwargs: (dict(payload), []),
            ),
        )

    def _write(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(self.html)

    def test_conflict_suffix_to_canonical_is_idempotent_and_retains_evidence(self) -> None:
        canonical_path, _ = _canonical_archive_target(
            archive_root=self.archive_root,
            project_code=self.project_code,
            project_name=self.project_name,
            listing_date="2026-08-16",
            source_file=os.path.join(self.archive_root, "incoming.html"),
        )
        conflict_path = f"{os.path.splitext(canonical_path)[0]}__conflict3.html"
        self._write(canonical_path)
        self._write(conflict_path)

        runner = self._runner()
        first = runner.ingest(
            ItemSavedPayload(
                source_file=conflict_path,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )
        first_record = self.store.get_record(str(first["record_id"]))
        self.assertEqual(first["archive_path"], canonical_path)
        self.assertEqual(first_record["source_file"], canonical_path)
        self.assertEqual(first_record["archive_path"], canonical_path)
        self.assertEqual(first_record["source_identity_json"]["original_source_file"], canonical_path)
        self.assertEqual(first_record["source_identity_json"]["original_evidence_path"], conflict_path)
        self.assertEqual(pick_reprocess_evidence_path(first_record), conflict_path)
        self.assertTrue(os.path.isfile(conflict_path))

        second = runner.ingest(
            ItemSavedPayload(
                source_file=canonical_path,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )
        second_record = self.store.get_record(str(second["record_id"]))
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(second["revision_id"], first["revision_id"])
        self.assertFalse(second["changed"])
        self.assertEqual(
            second_record["source_identity_json"]["original_evidence_path"],
            conflict_path,
        )
        with sqlite3.connect(self.db_path) as conn:
            revision_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM record_revisions WHERE record_id = ?",
                    (first["record_id"],),
                ).fetchone()[0]
            )
        self.assertEqual(revision_count, 1)

    def test_different_source_path_still_creates_revision(self) -> None:
        source_a = os.path.join(self.temp_dir.name, "source-a.html")
        source_b = os.path.join(self.temp_dir.name, "source-b.html")
        self._write(source_a)
        self._write(source_b)
        runner = self._runner()

        first = runner.ingest(
            ItemSavedPayload(
                source_file=source_a,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )
        second = runner.ingest(
            ItemSavedPayload(
                source_file=source_b,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )

        self.assertEqual(second["record_id"], first["record_id"])
        self.assertNotEqual(second["revision_id"], first["revision_id"])
        self.assertTrue(second["changed"])
        record = self.store.get_record(str(second["record_id"]))
        self.assertEqual(record["source_identity_json"]["original_evidence_path"], source_b)

    def test_canonical_reprocess_retains_external_evidence_path(self) -> None:
        source_path = os.path.join(self.temp_dir.name, "external-source.html")
        self._write(source_path)
        runner = self._runner()

        first = runner.ingest(
            ItemSavedPayload(
                source_file=source_path,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )
        canonical_path = str(first["archive_path"])
        self.assertNotEqual(canonical_path, source_path)
        first_record = self.store.get_record(str(first["record_id"]))
        self.assertEqual(
            first_record["source_identity_json"]["original_evidence_path"],
            source_path,
        )

        second = runner.ingest(
            ItemSavedPayload(
                source_file=canonical_path,
                exchange="shanghai",
                extra={"preserve_source_artifact": True},
            )
        )
        second_record = self.store.get_record(str(second["record_id"]))
        self.assertEqual(second["revision_id"], first["revision_id"])
        self.assertFalse(second["changed"])
        self.assertEqual(
            second_record["source_identity_json"]["original_evidence_path"],
            source_path,
        )


if __name__ == "__main__":
    unittest.main()
