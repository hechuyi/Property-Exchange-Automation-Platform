from __future__ import annotations

import os
import tempfile
import unittest

from peap.streaming_ingest import StreamingIngestDependencies, StreamingIngestRunner
from peap.streaming_models import ItemSavedPayload, PostProcessFinding
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

    def test_ingest_uses_saved_payload_project_type_when_parser_path_fallback_is_unknown(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32026BJ1000003",
                "项目名称": "测试股权项目",
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

        result = runner.ingest(
            ItemSavedPayload(
                source_file=self.html_path,
                exchange="beijing",
                project_code="G32026BJ1000003",
                extra={"project_type_fallback": "equity_transfer"},
            )
        )

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(latest[0]["project_type"], "股权转让")

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

    def test_ingest_prefers_downloader_project_type_over_parser_fallback(self) -> None:
        def fake_parser(file_path: str):
            return {
                "项目编号": "G32026BJ1000004",
                "项目名称": "下载阶段已知类型项目",
                "项目类型": "股权转让",
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
                project_code="G32026BJ1000004",
                extra={"project_type": "physical_asset"},
            )
        )

        self.assertEqual(result["state"], "ready")
        latest = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(latest[0]["project_type"], "实物资产")
        self.assertEqual(latest[0]["postprocess_payload"]["项目类型"], "实物资产")

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


if __name__ == "__main__":
    unittest.main()
