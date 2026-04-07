from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from peap.streaming_ingest import (
    StreamingIngestDependencies,
    StreamingIngestRunner,
    _default_parse_file,
)
from peap.streaming_models import IngestedRecord, ItemSavedPayload, PostProcessFinding
from peap.streaming_store import StreamingStore


class SkipParse(RuntimeError):
    pass


class StreamingIngestRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        self.archive_root = os.path.join(self.temp_dir.name, "submission")
        self.store = StreamingStore(self.db_path)
        self.html_path = os.path.join(self.temp_dir.name, "raw.html")
        with open(self.html_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>ok</body></html>")
        os.makedirs(f"{os.path.splitext(self.html_path)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(self.html_path)[0]}_files/style.css", "w", encoding="utf-8") as handle:
            handle.write("body{}")

    def test_ingest_ready_record_copies_into_month_archive(self) -> None:
        def fake_parser(file_path: str):
            self.assertEqual(file_path, self.html_path)
            return {
                "项目编号": "G32025SH1000194",
                "项目名称": "上海电气集团恒联企业发展有限公司35%股权",
                "项目类型": "股权转让",
                "交易所": "shanghai",
                "挂牌开始日期": "2026-03-21",
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

    def test_ingest_persists_candidate_identity_tokens_into_latest_record_context(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32025SH1000195",
                "项目名称": "带候选标识项目",
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
                    "转让方": "上海电气集团恒联企业发展有限公司",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1002001",
                    "项目名称": "待回刷项目",
                    "项目类型": "股权转让",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
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
        self.assertEqual(latest[0]["source_identity_json"]["original_source_file"], self.html_path)
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
        self.assertNotIn("挂牌价格", latest[0]["canonical_projection"])

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

    def test_ingest_cbex_otc_fixture_persists_unknown_project_type_without_saved_payload_fallback(self) -> None:
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
        latest = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["project_code"], "GR2026BJ1999001")
        self.assertEqual(latest[0]["project_type"], "")
        self.assertEqual(latest[0]["postprocess_payload"]["项目类型"], "未知")
        self.assertTrue(any(str(item.get("type") or "") == "project_type_unknown" for item in latest[0]["findings"]))

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

    def test_ingest_does_not_let_project_type_fallback_override_parser_value(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32026BJ1000005",
                "项目名称": "回刷纠正类型项目",
                "项目类型": "实物资产",
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

    def test_ingest_cbex_otc_fixture_without_recoverable_payload_records_parse_failure(self) -> None:
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
        self.assertEqual(result["error_type"], "cbex-otc-page-unrecoverable")
        self.assertIn("cbex-otc-page-unrecoverable", result["error_message"])
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

    def test_ingest_unknown_project_type_requires_manual_review(self) -> None:
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

        self.assertEqual(result["state"], "pending_mapping")
        latest = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(latest), 1)
        self.assertTrue(any(str(item.get("type") or "") == "project_type_unknown" for item in latest[0]["findings"]))

    def test_ingest_missing_project_type_requires_manual_review(self) -> None:
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

        self.assertEqual(result["state"], "pending_mapping")
        latest = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(latest), 1)
        self.assertTrue(any(str(item.get("type") or "") == "project_type_unknown" for item in latest[0]["findings"]))

    def test_ingest_calls_sync_mapping_pending_for_record_not_direct_backlog_methods(self) -> None:
        """Ingest must call sync_mapping_pending_for_record, not mark/resolve directly."""
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
                "类型": "股权转让",
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

        with patch.object(self.store, "sync_mapping_pending_for_record", wraps=self.store.sync_mapping_pending_for_record) as mock_sync:
            result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "ready")
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        self.assertEqual(call_kwargs["record_id"], result["record_id"])
        self.assertEqual(call_kwargs["state"], "ready")

    def test_ingest_mapping_conflict_record_syncs_conflict_state_to_backlog(self) -> None:
        """A record with mapping_conflict findings must sync conflict state to backlog."""
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

        with patch.object(self.store, "sync_mapping_pending_for_record", wraps=self.store.sync_mapping_pending_for_record) as mock_sync:
            result = runner.ingest(ItemSavedPayload(source_file=self.html_path, exchange="shanghai"))

        self.assertEqual(result["state"], "mapping_conflict")
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        self.assertEqual(call_kwargs["state"], "mapping_conflict")


class StreamingIngestCanonicalFieldRegressionTest(unittest.TestCase):
    """Regression tests for canonical field preservation in streaming ingest."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming_canonical.sqlite3")
        self.archive_root = os.path.join(self.temp_dir.name, "submission")
        self.store = StreamingStore(self.db_path)
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


if __name__ == "__main__":
    unittest.main()
