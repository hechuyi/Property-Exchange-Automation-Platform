from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.streaming_models import IngestedRecord, ItemProgressEvent, PostProcessFinding


class FakeRuntimeDependencies:
    def __init__(self) -> None:
        self.install_calls: list[str] = []

    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        self.install_calls.append(browser_name)
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
            "returncode": 0,
        }


class FakeMissingRuntimeDependencies(FakeRuntimeDependencies):
    def __init__(self) -> None:
        super().__init__()
        self.installed = False

    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome" if self.installed else "",
            "installed": self.installed,
            "error": "",
        }


class FakeAsyncRuntimeDependencies(FakeMissingRuntimeDependencies):
    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        self.install_calls.append(browser_name)
        time.sleep(0.03)
        self.installed = True
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
            "returncode": 0,
        }


class CountingRuntimeDependencies(FakeRuntimeDependencies):
    def __init__(self) -> None:
        super().__init__()
        self.status_calls = 0

    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        self.status_calls += 1
        return super().get_browser_runtime_status(browser_name=browser_name)


class AppServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        self.runtime_dependencies = FakeRuntimeDependencies()
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.service = AppService(
            config_obj=self.config,
            runtime_dependencies=self.runtime_dependencies,
        )

    def _wait_for_job_status(self, job_id: str, *, timeout: float = 1.0) -> dict[str, object]:
        deadline = time.time() + timeout
        terminal_statuses = {"success", "success_with_warnings", "failed", "interrupted"}
        latest: dict[str, object] | None = None
        while time.time() < deadline:
            latest = self.service.get_job(job_id)
            if str(latest.get("status") or "") in terminal_statuses:
                return latest
            time.sleep(0.02)
        if latest is None:
            latest = self.service.get_job(job_id)
        self.fail(f"job {job_id} did not reach terminal status within {timeout} seconds: {latest}")

    def _insert_ready_record(self, *, record_id: str = "rec-1", project_code: str = "G32025SH1000194") -> None:
        source_file = os.path.join(self.temp_dir.name, f"{record_id}.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>ok</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash=f"hash-{record_id}",
                project_code=project_code,
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=os.path.join(self.temp_dir.name, "archive", f"{record_id}.html"),
                parser_payload={"项目编号": project_code, "项目名称": "测试项目"},
                postprocess_payload={
                    "项目编号": project_code,
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "状态": "挂牌中",
                    "交易所": "上海联合产权交易所",
                    "类型": "国资",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                    "挂牌开始日期": "2026-03-21",
                    "近一年净利润": "1000",
                    "近一年净利润（万）": "1000",
                },
                findings=[],
            )
        )

    def _insert_record_with_mapping_source(
        self,
        *,
        record_id: str,
        state: str,
        transferor: str,
        group_name: str = "",
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, f"{record_id}.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>mapping</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash=f"hash-{record_id}",
                project_code=f"CODE-{record_id}",
                project_name=f"项目-{record_id}",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state=state,
                source_file=source_file,
                archive_path=os.path.join(self.temp_dir.name, "archive", f"{record_id}.html"),
                parser_payload={
                    "项目编号": f"CODE-{record_id}",
                    "项目名称": f"项目-{record_id}",
                    "转让方": transferor,
                    "隶属集团": group_name,
                },
                postprocess_payload={
                    "项目编号": f"CODE-{record_id}",
                    "项目名称": f"项目-{record_id}",
                    "项目类型": "股权转让",
                    "转让方": transferor,
                    "隶属集团": group_name,
                },
                findings=[],
            )
        )

    def test_launch_one_click_does_not_enable_refresh_or_auto_export(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_streaming_daily_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
        ):
            captured["job_type"] = job_type
            captured["auto_export"] = auto_export
            captured["start_date"] = args.start_date
            captured["end_date"] = args.end_date
            captured["with_refresh"] = getattr(args, "with_refresh", None)
            captured["postprocess_config"] = getattr(args, "postprocess_config", "")
            job_created_callback("job-456", self.service.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_run_streaming_daily_pipeline):
            payload = self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

        self.assertEqual(payload["job_id"], "job-456")
        self.assertEqual(payload["job_type"], "one_click")
        self.assertEqual(captured["job_type"], "one_click")
        self.assertFalse(bool(captured["auto_export"]))
        self.assertEqual(captured["start_date"], "2026-03-22")
        self.assertEqual(captured["end_date"], "2026-03-22")
        self.assertFalse(bool(captured["with_refresh"]))
        self.assertTrue(str(captured["postprocess_config"]).endswith("postprocess_external_template.json"))

    def test_launch_one_click_rejects_when_mutating_job_running(self) -> None:
        self.service._reserve_mutating_job("manual_import")
        self.addCleanup(self.service._release_mutating_job, "manual_import")

        with self.assertRaisesRegex(RuntimeError, "已有执行中的任务：手动导入解析"):
            self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

    def test_launch_one_click_requires_real_job_id_before_success_return(self) -> None:
        def fake_run_streaming_daily_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
        ):
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_run_streaming_daily_pipeline):
            with self.assertRaisesRegex(RuntimeError, "job_id"):
                self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

    def test_default_advanced_settings_use_bundled_postprocess_config(self) -> None:
        advanced = self.service.get_advanced_settings()

        self.assertTrue(advanced["postprocess_config"].endswith("postprocess_external_template.json"))
        self.assertTrue(os.path.isfile(advanced["postprocess_config"]))

    def test_readiness_does_not_query_runtime_dependencies(self) -> None:
        runtime_dependencies = CountingRuntimeDependencies()
        service = AppService(
            config_obj=self.config,
            runtime_dependencies=runtime_dependencies,
        )

        payload = service.readiness()

        self.assertTrue(payload["ok"])
        self.assertEqual(runtime_dependencies.status_calls, 0)

    def test_overview_normalizes_ready_records_missing_type_to_pending_mapping(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-type.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>missing type</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-missing-type",
                revision_hash="hash-missing-type",
                project_code="G32025SH1000194-4",
                project_name="缺类型项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=os.path.join(self.temp_dir.name, "archive", "missing-type.html"),
                parser_payload={
                    "项目编号": "G32025SH1000194-4",
                    "项目名称": "缺类型项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000194-4",
                    "项目名称": "缺类型项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                findings=[],
            )
        )

        overview = self.service.overview()
        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "equity_transfer"})

        self.assertEqual(overview["pending_mapping_count"], 1)
        self.assertEqual(payload["rows"][0]["state"], "pending_mapping")
        self.assertEqual(payload["rows"][0]["status_label"], "待补映射")
        self.assertEqual(payload["rows"][0]["values"]["挂牌次数"], "四次挂牌")

    def test_overview_reclassifies_legacy_conflict_record_back_to_ready(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-conflict.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy conflict</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-legacy-conflict",
                revision_hash="hash-legacy-conflict",
                project_code="G32025SH1000194",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="conflict",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "类型": "国资",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "类型": "国资",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="archive_conflict",
                        message="archive naming conflict",
                        evidence={},
                    )
                ],
            )
        )

        overview = self.service.overview()
        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "equity_transfer"})

        self.assertEqual(overview["record_state_counts"].get("conflict", 0), 0)
        self.assertEqual(payload["rows"][0]["state"], "ready")

    def test_list_pending_mappings_normalizes_missing_type_ready_records(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-type-pending.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>missing type pending</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-pending-normalize",
                revision_hash="hash-pending-normalize",
                project_code="G32025SH1000194-4",
                project_name="缺类型项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=os.path.join(self.temp_dir.name, "archive", "missing-type-pending.html"),
                parser_payload={
                    "项目编号": "G32025SH1000194-4",
                    "项目名称": "缺类型项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000194-4",
                    "项目名称": "缺类型项目",
                    "项目类型": "股权转让",
                    "转让方": "上海电气集团恒联企业发展有限公司",
                    "隶属集团": "上海电气集团",
                },
                findings=[],
            )
        )

        pending = self.service.list_pending_mappings()

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["project_code"], "G32025SH1000194-4")

    def test_list_records_supports_keyword_and_date_filters_with_summary(self) -> None:
        self._insert_ready_record(record_id="rec-filter-a", project_code="G32025SH1000194")
        source_file = os.path.join(self.temp_dir.name, "rec-filter-b.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>pending</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-filter-b",
                revision_hash="hash-rec-filter-b",
                project_code="GR2026BJ1001611",
                project_name="北交所待处理项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-22",
                state="pending_mapping",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BJ1001611", "项目名称": "北交所待处理项目", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026BJ1001611", "项目名称": "北交所待处理项目", "项目类型": "实物资产"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="缺少类型，暂不能进入导出",
                        evidence={},
                    )
                ],
            )
        )

        payload = self.service.list_records(
            {
                "state": "all",
                "project_type": "all",
                "date_from": "2026-03-22",
                "date_to": "2026-03-22",
                "keyword": "北交所",
                "limit": 50,
            }
        )

        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["project_code"], "GR2026BJ1001611")
        self.assertEqual(payload["summary"]["visible_count"], 1)
        self.assertEqual(payload["summary"]["filtered_state_counts"]["pending_mapping"], 1)
        self.assertEqual(payload["summary"]["page_state_counts"]["pending_mapping"], 1)

    def test_list_records_returns_pagination_metadata(self) -> None:
        self._insert_ready_record(record_id="rec-page-1", project_code="G32025SH1000101")
        self._insert_ready_record(record_id="rec-page-2", project_code="G32025SH1000102")
        self._insert_ready_record(record_id="rec-page-3", project_code="G32025SH1000103")

        payload = self.service.list_records(
            {
                "state": "all",
                "project_type": "equity_transfer",
                "page": 2,
                "page_size": 1,
            }
        )

        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["page_size"], 1)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual(payload["page_count"], 3)
        self.assertTrue(payload["has_more"])
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["record_id"], "rec-page-2")

    def test_list_records_summary_distinguishes_total_count_from_current_page(self) -> None:
        self._insert_ready_record(record_id="rec-summary-1", project_code="G32025SH1000201")
        self._insert_ready_record(record_id="rec-summary-2", project_code="G32025SH1000202")
        self._insert_ready_record(record_id="rec-summary-3", project_code="G32025SH1000203")

        payload = self.service.list_records(
            {
                "state": "all",
                "project_type": "equity_transfer",
                "page": 1,
                "page_size": 2,
            }
        )

        self.assertEqual(payload["summary"]["visible_count"], 2)
        self.assertEqual(payload["summary"]["total_count"], 3)
        self.assertEqual(payload["summary"]["page_count"], 2)
        self.assertEqual(payload["summary"]["filtered_state_counts"]["ready"], 3)
        self.assertEqual(payload["summary"]["page_state_counts"]["ready"], 2)
        self.assertTrue(payload["has_more"])

    def test_upsert_mapping_starts_background_refresh_for_all_affected_latest_records(self) -> None:
        self._insert_record_with_mapping_source(
            record_id="rec-map-1",
            state="pending_mapping",
            transferor="上海电气集团恒联企业发展有限公司",
        )
        self._insert_record_with_mapping_source(
            record_id="rec-map-2",
            state="ready",
            transferor="上海电气集团恒联企业发展有限公司",
        )
        refreshed: list[str] = []

        def fake_reprocess(record_id: str) -> dict[str, object]:
            refreshed.append(record_id)
            return {"record_id": record_id, "state": "ready"}

        with patch.object(self.service, "reprocess_record", side_effect=fake_reprocess):
            payload = self.service.upsert_mapping(
                {
                    "source_name": "上海电气集团恒联企业发展有限公司",
                    "match_field": "transferor",
                    "target_field": "group_name",
                    "target_value": "上海电气集团",
                }
            )

            deadline = time.time() + 1.0
            while time.time() < deadline and len(refreshed) < 2:
                time.sleep(0.02)

        self.assertEqual(payload["job_type"], "mapping_refresh")
        self.assertEqual(payload["affected_count"], 2)
        self.assertTrue(payload["job_id"])
        self.assertCountEqual(refreshed, ["rec-map-1", "rec-map-2"])

    def test_preview_mapping_upsert_reports_create_update_and_overwrite_modes(self) -> None:
        create_preview = self.service.preview_mapping_upsert(
            {
                "source_name": "华润",
                "match_field": "group",
                "target_field": "source_type",
                "target_value": "央企",
            }
        )
        self.assertEqual(create_preview["mode"], "create")
        self.assertFalse(bool(create_preview["conflict"]))

        self.service.store.upsert_mapping_entry(
            company_name="华润",
            group_name="",
            source_type="央企",
            metadata={"match_field": "group", "target_field": "source_type", "notes": "old"},
        )
        update_preview = self.service.preview_mapping_upsert(
            {
                "source_name": "华润",
                "match_field": "group",
                "target_field": "source_type",
                "target_value": "央企",
                "notes": "new",
            }
        )
        self.assertEqual(update_preview["mode"], "update")
        self.assertFalse(bool(update_preview["conflict"]))

        overwrite_preview = self.service.preview_mapping_upsert(
            {
                "source_name": "华润",
                "match_field": "group",
                "target_field": "source_type",
                "target_value": "地方国企",
            }
        )
        self.assertEqual(overwrite_preview["mode"], "overwrite")
        self.assertTrue(bool(overwrite_preview["conflict"]))
        self.assertEqual(overwrite_preview["existing_entry"]["source_type"], "央企")

    def test_preview_mapping_upsert_rejects_blank_or_invalid_rule_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_name is required"):
            self.service.preview_mapping_upsert(
                {
                    "source_name": "",
                    "match_field": "group",
                    "target_field": "source_type",
                    "target_value": "央企",
                }
            )
        with self.assertRaisesRegex(ValueError, "target_value is required"):
            self.service.preview_mapping_upsert(
                {
                    "source_name": "华润",
                    "match_field": "group",
                    "target_field": "source_type",
                    "target_value": "",
                }
            )
        with self.assertRaisesRegex(ValueError, "invalid match_field"):
            self.service.preview_mapping_upsert(
                {
                    "source_name": "华润",
                    "match_field": "unknown",
                    "target_field": "source_type",
                    "target_value": "央企",
                }
            )
        with self.assertRaisesRegex(ValueError, "invalid target_field"):
            self.service.preview_mapping_upsert(
                {
                    "source_name": "华润",
                    "match_field": "group",
                    "target_field": "unknown",
                    "target_value": "央企",
                }
            )

    def test_launch_pending_mapping_refresh_reprocesses_all_current_pending_records(self) -> None:
        self._insert_record_with_mapping_source(
            record_id="rec-pending-refresh-1",
            state="pending_mapping",
            transferor="华润甲公司",
            group_name="华润",
        )
        self._insert_record_with_mapping_source(
            record_id="rec-pending-refresh-2",
            state="pending_mapping",
            transferor="华润乙公司",
            group_name="华润",
        )
        self._insert_record_with_mapping_source(
            record_id="rec-pending-refresh-3",
            state="ready",
            transferor="华润丙公司",
            group_name="华润",
        )
        refreshed: list[str] = []

        def fake_reprocess(record_id: str) -> dict[str, object]:
            refreshed.append(record_id)
            return {"record_id": record_id, "state": "ready", "project_code": record_id}

        with patch.object(self.service, "reprocess_record", side_effect=fake_reprocess):
            payload = self.service.launch_pending_mapping_refresh({})

            deadline = time.time() + 1.0
            while time.time() < deadline and len(refreshed) < 2:
                time.sleep(0.02)

        self.assertEqual(payload["job_type"], "mapping_refresh")
        self.assertEqual(payload["affected_count"], 2)
        self.assertTrue(payload["job_id"])
        self.assertCountEqual(refreshed, ["rec-pending-refresh-1", "rec-pending-refresh-2"])

    def test_launch_pending_mapping_refresh_rejects_when_mutating_job_running(self) -> None:
        self.service._reserve_mutating_job("manual_import")
        self.addCleanup(self.service._release_mutating_job, "manual_import")

        with self.assertRaisesRegex(RuntimeError, "已有执行中的任务：手动导入解析"):
            self.service.launch_pending_mapping_refresh({})

    def test_upsert_mapping_rejects_when_mutating_job_running(self) -> None:
        self.service._reserve_mutating_job("manual_import")
        self.addCleanup(self.service._release_mutating_job, "manual_import")

        with self.assertRaisesRegex(RuntimeError, "已有执行中的任务：手动导入解析"):
            self.service.upsert_mapping(
                {
                    "source_name": "华润",
                    "match_field": "group",
                    "target_field": "source_type",
                    "target_value": "央企",
                }
            )

    def test_reprocess_record_rejects_when_mutating_job_running(self) -> None:
        self._insert_record_with_mapping_source(
            record_id="rec-reprocess-lock",
            state="pending_mapping",
            transferor="华润甲公司",
            group_name="华润",
        )
        self.service._reserve_mutating_job("manual_import")
        self.addCleanup(self.service._release_mutating_job, "manual_import")

        with self.assertRaisesRegex(RuntimeError, "已有执行中的任务：手动导入解析"):
            self.service.reprocess_record("rec-reprocess-lock")

    def test_launch_manual_import_starts_job_and_discovers_html_variants(self) -> None:
        import_root = os.path.join(self.temp_dir.name, "manual_import")
        nested = os.path.join(import_root, "nested")
        os.makedirs(nested, exist_ok=True)
        for target in (
            os.path.join(import_root, "a.html"),
            os.path.join(import_root, "b.htm"),
            os.path.join(import_root, "c.mhtml"),
            os.path.join(nested, "d.html"),
        ):
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
        with open(os.path.join(import_root, "ignore.txt"), "w", encoding="utf-8") as handle:
            handle.write("skip")

        discovered: list[str] = []

        def fake_ingest_manual_file(file_path: str) -> dict[str, object]:
            discovered.append(os.path.relpath(file_path, import_root))
            return {"state": "ready", "record_id": os.path.basename(file_path), "project_code": os.path.basename(file_path)}

        with patch.object(self.service, "_ingest_manual_import_file", side_effect=fake_ingest_manual_file, create=True):
            payload = self.service.launch_manual_import({"input_dir": import_root})
            deadline = time.time() + 1.0
            while time.time() < deadline and len(discovered) < 4:
                time.sleep(0.02)

        self.assertEqual(payload["job_type"], "manual_import")
        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["discovered_count"], 4)
        self.assertCountEqual(discovered, ["a.html", "b.htm", "c.mhtml", os.path.join("nested", "d.html")])

    def test_manual_import_all_failed_resolves_to_failed_not_success_with_warnings(self) -> None:
        import_root = os.path.join(self.temp_dir.name, "manual_import_failed")
        os.makedirs(import_root, exist_ok=True)
        source_file = os.path.join(import_root, "broken.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        with patch.object(
            self.service,
            "_ingest_manual_import_file",
            return_value={"state": "parse_failed", "record_id": "rec-failed", "project_code": "CODE-FAILED"},
            create=True,
        ):
            payload = self.service.launch_manual_import({"input_dir": import_root})

        job = self._wait_for_job_status(str(payload["job_id"]))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["summary"]["failed_count"], 1)

    def test_manual_import_uses_effective_postprocess_rules_config(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-import.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>manual</body></html>")

        captured: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, *, store, archive_root, rules_config=None, dependencies=None) -> None:
                captured["archive_root"] = archive_root
                captured["rules_config"] = rules_config

            def ingest(self, item):
                captured["source_file"] = item.source_file
                return {"state": "ready", "record_id": "rec-manual", "project_code": "CODE-MANUAL", "archive_path": source_file}

        with patch("desktop_backend.app_service.StreamingIngestRunner", FakeRunner):
            result = self.service._ingest_manual_import_file(source_file)

        self.assertEqual(result["state"], "ready")
        self.assertEqual(captured["source_file"], source_file)
        self.assertIn("R005_normalize_source_type", dict(captured["rules_config"] or {}))

    def test_reprocess_uses_effective_postprocess_rules_config(self) -> None:
        self._insert_ready_record(record_id="rec-reprocess", project_code="G32025SH1000777")
        captured: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, *, store, archive_root, rules_config=None, dependencies=None) -> None:
                captured["archive_root"] = archive_root
                captured["rules_config"] = rules_config

            def ingest(self, item):
                captured["source_file"] = item.source_file
                return {"state": "ready", "record_id": "rec-reprocess", "project_code": "G32025SH1000777", "archive_path": item.source_file}

        with patch("desktop_backend.app_service.StreamingIngestRunner", FakeRunner):
            result = self.service.reprocess_record("rec-reprocess")

        self.assertEqual(result["state"], "ready")
        self.assertIn("R005_normalize_source_type", dict(captured["rules_config"] or {}))
        self.assertTrue(str(captured["source_file"]).endswith("rec-reprocess.html"))

    def test_reprocess_passes_existing_project_type_as_fallback_context(self) -> None:
        self._insert_ready_record(record_id="rec-reprocess-type", project_code="G32026BJ1000003")
        captured: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, *, store, archive_root, rules_config=None, dependencies=None) -> None:
                pass

            def ingest(self, item):
                captured["extra"] = dict(item.extra)
                return {
                    "state": "ready",
                    "record_id": "rec-reprocess-type",
                    "project_code": "G32026BJ1000003",
                    "archive_path": item.source_file,
                }

        with patch("desktop_backend.app_service.StreamingIngestRunner", FakeRunner):
            self.service.reprocess_record("rec-reprocess-type")

        self.assertNotIn("project_type", captured["extra"])
        self.assertEqual(captured["extra"]["project_type_fallback"], "股权转让")

    def test_reprocess_failed_record_uses_original_evidence_path(self) -> None:
        original_file = os.path.join(self.temp_dir.name, "original-failed-evidence.html")
        with open(original_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>original evidence</body></html>")
        current_file = os.path.join(self.temp_dir.name, "current-failed-record.html")
        with open(current_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>current file</body></html>")

        failed = self.service.store.upsert_failed_record(
            project_code="FAILED-REPROCESS-001",
            source_file=current_file,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={
                "source_identity": {
                    "original_evidence_path": original_file,
                    "original_source_file": original_file,
                },
                "source_file": current_file,
            },
        )
        captured: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, *, store, archive_root, rules_config=None, dependencies=None) -> None:
                pass

            def ingest(self, item):
                captured["source_file"] = item.source_file
                return {
                    "state": "ready",
                    "record_id": failed["record_id"],
                    "project_code": "FAILED-REPROCESS-001",
                    "archive_path": item.source_file,
                }

        with patch("desktop_backend.app_service.StreamingIngestRunner", FakeRunner):
            result = self.service.reprocess_record(str(failed["record_id"]))

        self.assertEqual(result["state"], "ready")
        self.assertEqual(captured["source_file"], original_file)

        os.remove(original_file)
        with patch("desktop_backend.app_service.StreamingIngestRunner", FakeRunner):
            with self.assertRaisesRegex(FileNotFoundError, "original evidence"):
                self.service.reprocess_record(str(failed["record_id"]))

    def test_set_advanced_settings_keeps_fixed_app_runtime_paths(self) -> None:
        updated = self.service.set_advanced_settings(
            {
                "app_home": "/tmp/ignored",
                "streaming_db": "/tmp/ignored.sqlite3",
                "log_dir": "/tmp/ignored_logs",
                "postprocess_config": "rules.json",
                "save_json": True,
            }
        )

        self.assertEqual(updated["app_home"], self.service.app_home)
        self.assertEqual(updated["streaming_db"], self.service.db_path)
        self.assertEqual(updated["log_dir"], self.config.LOG_DIR)
        self.assertEqual(updated["postprocess_config"], "rules.json")
        self.assertTrue(updated["save_json"])

    def test_set_basic_settings_keeps_workspace_derived_archive_and_export_paths(self) -> None:
        updated = self.service.set_basic_settings(
            {
                "archive_root": "/tmp/ignored_archive",
                "export_root": "/tmp/ignored_export",
                "default_exchange": "cbex",
            }
        )

        self.assertEqual(updated["workspace_root"], self.service.app_home)
        self.assertEqual(updated["archive_root"], self.config.ARCHIVE_ROOT)
        self.assertEqual(updated["export_root"], self.config.OUTPUT_EXCEL_DIR)
        self.assertEqual(updated["default_exchange"], "cbex")

    def test_advanced_settings_do_not_expose_refresh_toggle(self) -> None:
        defaults = self.service.get_advanced_settings()
        self.assertNotIn("with_refresh", defaults)

    def test_runtime_dependency_status_and_install_are_exposed(self) -> None:
        payload = self.service.get_runtime_dependencies()
        self.assertTrue(payload["browser"]["installed"])
        self.assertEqual(payload["browser"]["browser_name"], "chromium")
        self.assertTrue(payload["product_readiness"]["download_ready"])

        installed = self.service.install_browser_runtime({"browser_name": "chromium"})
        self.assertEqual(installed["returncode"], 0)
        self.assertTrue(installed["product_readiness"]["ready"])
        self.assertEqual(self.runtime_dependencies.install_calls, ["chromium"])

    def test_overview_includes_product_readiness(self) -> None:
        overview = self.service.overview()
        self.assertIn("product_readiness", overview)
        self.assertTrue(overview["product_readiness"]["download_ready"])

    def test_runtime_readiness_blocks_download_when_browser_missing(self) -> None:
        service = AppService(
            config_obj=self.config,
            runtime_dependencies=FakeMissingRuntimeDependencies(),
        )

        overview = service.overview()
        self.assertFalse(overview["product_readiness"]["download_ready"])
        self.assertEqual(overview["product_readiness"]["issues"][0]["code"], "browser_runtime_missing")

    def test_overview_and_settings_recover_after_database_file_deleted(self) -> None:
        self._insert_ready_record(record_id="rec-before-delete", project_code="G32025SH1000888")
        initial = self.service.overview()
        self.assertEqual(initial["record_state_counts"].get("ready", 0), 1)

        os.remove(self.service.db_path)

        overview = self.service.overview()
        basic = self.service.get_basic_settings()
        advanced = self.service.get_advanced_settings()

        self.assertEqual(overview["record_state_counts"], {})
        self.assertEqual(overview["pending_mapping_count"], 0)
        self.assertEqual(overview["recent_jobs"], [])
        self.assertEqual(basic["workspace_root"], self.service.app_home)
        self.assertEqual(advanced["streaming_db"], self.service.db_path)

    def test_launch_one_click_recovers_after_database_file_deleted(self) -> None:
        os.remove(self.service.db_path)
        captured: dict[str, object] = {}

        def fake_run_streaming_daily_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
        ):
            captured["archive_root"] = archive_root
            captured["export_root"] = export_root
            job_created_callback("job-recovered-db", self.service.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_run_streaming_daily_pipeline):
            payload = self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

        self.assertEqual(payload["job_id"], "job-recovered-db")
        self.assertEqual(captured["archive_root"], self.config.ARCHIVE_ROOT)
        self.assertEqual(captured["export_root"], self.config.OUTPUT_EXCEL_DIR)

    def test_service_startup_interrupts_stale_running_jobs(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={"start_date": "2026-03-22"})
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="running",
                payload={"label": "正在保存网页", "phase_percent": 71},
            )
        )

        restarted_service = AppService(
            config_obj=self.config,
            runtime_dependencies=self.runtime_dependencies,
        )

        job = restarted_service.store.get_job(job_id)
        self.assertEqual(job["status"], "interrupted")
        events = restarted_service.store.list_job_events(job_id, limit=5)
        self.assertEqual(events[0]["status"], "interrupted")
        self.assertEqual(events[0]["error_type"], "job_interrupted")
        overview = restarted_service.overview()
        self.assertEqual(overview["latest_progress"]["phase_code"], "interrupted")
        self.assertEqual(overview["latest_progress"]["phase_label"], "已中断")

    def test_run_export_recovers_after_database_file_deleted(self) -> None:
        os.remove(self.service.db_path)

        payload = self.service.run_export({"scope": {"date_from": "2026-03-22", "date_to": "2026-03-22"}})

        self.assertEqual(payload["status"], "empty")
        self.assertIn("没有可导出的记录", payload["message"])

    def test_launch_browser_runtime_install_tracks_async_state(self) -> None:
        runtime_dependencies = FakeAsyncRuntimeDependencies()
        service = AppService(
            config_obj=self.config,
            runtime_dependencies=runtime_dependencies,
        )

        started = service.launch_browser_runtime_install({"browser_name": "chromium", "trigger": "auto"})
        self.assertEqual(started["status"], "running")

        deadline = time.time() + 1.0
        latest = None
        while time.time() < deadline:
            latest = service.get_runtime_dependencies()
            if latest["browser_install"]["status"] != "running":
                break
            time.sleep(0.02)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["browser_install"]["status"], "succeeded")
        self.assertTrue(latest["browser"]["installed"])
        self.assertTrue(latest["product_readiness"]["download_ready"])
        self.assertEqual(runtime_dependencies.install_calls, ["chromium"])

    def test_overview_exposes_latest_progress_summary(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={"start_date": "2026-03-21"})
        self.service.store.update_job_counts(job_id, downloaded_inc=3, persisted_inc=1)
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="prepare_tasks",
                status="done",
                payload={"label": "正在扫描网页"},
            )
        )
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="running",
                payload={"label": "正在扫描网页"},
            )
        )
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="skipped",
                status="skipped",
                project_code="GR2026BJ1001615",
                error_type="skip_parse",
                error_message="skip-cbex-otc-page",
            )
        )

        overview = self.service.overview()
        self.assertEqual(overview["latest_progress"]["phase_code"], "archive_pending")
        self.assertEqual(overview["latest_progress"]["phase_label"], "正在存档")
        self.assertEqual(overview["latest_progress"]["downloaded_count"], 3)
        self.assertEqual(overview["latest_progress"]["persisted_count"], 1)
        self.assertEqual(overview["latest_progress"]["skipped_count"], 1)
        self.assertEqual(overview["latest_progress"]["archive_pending_count"], 1)

    def test_overview_exposes_task_context_from_latest_phase_payload(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={"start_date": "2026-03-21"})
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="prepare_tasks",
                status="running",
                payload={
                    "label": "正在扫描网页",
                    "task_label": "北交所 - 实物资产",
                    "task_index": 1,
                    "task_total": 4,
                    "phase_percent": 25,
                },
            )
        )

        overview = self.service.overview()
        self.assertEqual(overview["latest_progress"]["phase_code"], "prepare_tasks")
        self.assertEqual(overview["latest_progress"]["current_task_label"], "北交所 - 实物资产")
        self.assertEqual(overview["latest_progress"]["task_index"], 1)
        self.assertEqual(overview["latest_progress"]["task_total"], 4)
        self.assertEqual(overview["latest_progress"]["phase_percent"], 25)

    def test_overview_terminal_state_does_not_keep_old_running_phase(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={})
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="prepare_tasks",
                status="running",
                payload={"label": "正在扫描网页"},
            )
        )
        self.service.store.finish_job(job_id, status="success", summary={})

        overview = self.service.overview()
        self.assertEqual(overview["latest_progress"]["phase_code"], "completed")
        self.assertEqual(overview["latest_progress"]["phase_label"], "已完成")

    def test_terminal_progress_clears_current_item_context(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={})
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="prepare_tasks",
                status="running",
                payload={
                    "label": "正在扫描网页",
                    "task_label": "北交所 - 股权转让",
                    "task_index": 2,
                    "task_total": 4,
                },
            )
        )
        self.service.store.finish_job(job_id, status="success", summary={})

        overview = self.service.overview()

        self.assertEqual(overview["latest_progress"]["phase_code"], "completed")
        self.assertEqual(overview["latest_progress"]["current_task_label"], "")
        self.assertEqual(overview["latest_progress"]["task_index"], 0)
        self.assertEqual(overview["latest_progress"]["task_total"], 0)

    def test_list_records_returns_export_aligned_rows(self) -> None:
        self._insert_ready_record()
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-2",
                revision_hash="hash-rec-2",
                project_code="GR2026BJ1001615",
                project_name="测试实物资产项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=os.path.join(self.temp_dir.name, "rec-2.html"),
                archive_path=os.path.join(self.temp_dir.name, "archive", "rec-2.html"),
                parser_payload={"项目编号": "GR2026BJ1001615"},
                postprocess_payload={
                    "项目编号": "GR2026BJ1001615",
                    "项目名称": "测试实物资产项目",
                    "项目类型": "实物资产",
                    "交易所": "北京产权交易所",
                    "转让方": "北京测试公司",
                    "挂牌价格": "100",
                    "挂牌开始日期": "2026-03-21",
                },
                findings=[],
            )
        )
        self.service.store.upsert_failed_record(
            project_code="GR2026BJ1009999",
            source_file=os.path.join(self.temp_dir.name, "skip.html"),
            state="skipped",
            error_type="skip_parse",
            error_message="skip-cbex-otc-page",
            payload={"source_file": "skip.html", "项目编号": "GR2026BJ1009999"},
        )

        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "equity_transfer"})
        self.assertIn("项目编号", payload["columns"])
        self.assertEqual(len(payload["rows"]), 2)
        ready_row = next(row for row in payload["rows"] if row["state"] == "ready")
        skipped_row = next(row for row in payload["rows"] if row["state"] == "skipped")
        self.assertEqual(ready_row["values"]["项目名称"], "测试项目")
        self.assertEqual(ready_row["values"]["隶属集团"], "上海电气集团")
        self.assertEqual(ready_row["values"]["交易所"], "上交所")
        self.assertIn("近一年净利润（万）", payload["columns"])
        self.assertNotIn("近一年净利润", payload["columns"])
        self.assertEqual(skipped_row["status_label"], "已跳过")
        self.assertEqual(skipped_row["values"]["项目编号"], "GR2026BJ1009999")

    def test_list_records_prefers_cli_contract_fields_from_parser_payload(self) -> None:
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-cli-contract",
                revision_hash="hash-cli-contract",
                project_code="G32025SH1000666",
                project_name="CLI契约项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=os.path.join(self.temp_dir.name, "rec-cli-contract.html"),
                archive_path=os.path.join(self.temp_dir.name, "archive", "rec-cli-contract.html"),
                parser_payload={
                    "项目编号": "G32025SH1000666",
                    "项目名称": "CLI契约项目",
                    "项目类型": "股权转让",
                    "类型": "国资",
                    "转让方": "上海CLI测试公司",
                    "挂牌次数": 2,
                    "挂牌开始日期": "2026-03-21",
                },
                postprocess_payload={"项目编号": "G32025SH1000666", "项目名称": "CLI契约项目", "项目类型": "股权转让"},
                findings=[],
            )
        )

        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "equity_transfer"})
        row = next(item for item in payload["rows"] if item["record_id"] == "rec-cli-contract")

        self.assertEqual(row["values"]["类型"], "国资")
        self.assertEqual(row["values"]["转让方"], "上海CLI测试公司")
        self.assertEqual(row["values"]["挂牌次数"], "2")

    def test_list_records_normalizes_legacy_skip_parse_failures(self) -> None:
        self.service.store.upsert_failed_record(
            project_code="GR2026BJ1001611",
            source_file=os.path.join(self.temp_dir.name, "legacy_skip.html"),
            state="parse_failed",
            error_type="parse_failed",
            error_message="skip-cbex-otc-page: legacy_skip.html",
            payload={"source_file": "legacy_skip.html", "项目编号": "GR2026BJ1001611"},
        )

        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "all"})
        row = next(item for item in payload["rows"] if item["project_code"] == "GR2026BJ1001611")

        self.assertEqual(row["state"], "skipped")
        self.assertEqual(row["status_label"], "已跳过")

    def test_list_records_exposes_archive_conflict_detail(self) -> None:
        archive_path = os.path.join(self.temp_dir.name, "archive", "G32025SH1000194__conflict1.html")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-conflict",
                revision_hash="hash-conflict",
                project_code="G32025SH1000194",
                project_name="测试冲突项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="conflict",
                source_file=os.path.join(self.temp_dir.name, "raw-conflict.html"),
                archive_path=archive_path,
                parser_payload={"项目编号": "G32025SH1000194", "项目名称": "测试冲突项目", "类型": "国资"},
                postprocess_payload={"项目编号": "G32025SH1000194", "项目名称": "测试冲突项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="archive_conflict",
                        message="archive naming conflict for project_code=G32025SH1000194",
                        evidence={"archive_path": archive_path},
                    )
                ],
            )
        )

        payload = self.service.list_records({"state": "all", "limit": 20, "project_type": "all"})
        row = next(item for item in payload["rows"] if item["record_id"] == "rec-conflict")

        self.assertEqual(row["status_label"], "已录入")
        self.assertIn("__conflict1.html", row["status_detail"])

    def test_overview_exposes_latest_stage_summary_for_zero_result_job(self) -> None:
        job_id = self.service.store.create_job("one_click", metadata={"start_date": "2026-03-21"})
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="done",
                payload={
                    "label": "正在保存网页",
                    "task_label": "北交所 - 挂牌股权转让",
                    "summary": {
                        "detail_candidates": 10,
                        "detail_date_skipped": 10,
                        "detail_fetched": 0,
                        "saved": 0,
                    },
                },
            )
        )
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="exporting",
                status="empty",
                payload={"label": "当前没有可导出的记录"},
            )
        )
        self.service.store.finish_job(job_id, status="success", summary={})

        overview = self.service.overview()
        self.assertEqual(overview["latest_progress"]["phase_code"], "completed")
        self.assertEqual(overview["latest_progress"]["latest_stage_code"], "save_pages")
        self.assertEqual(overview["latest_progress"]["latest_stage_summary"]["detail_candidates"], 10)
        self.assertEqual(overview["latest_progress"]["latest_stage_summary"]["detail_date_skipped"], 10)

    def test_export_progress_uses_export_semantics_not_archive_semantics(self) -> None:
        job_id = self.service.store.create_job("export_excel", metadata={})
        self.service.store.update_job_counts(job_id, downloaded_inc=3, persisted_inc=1)
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="exporting",
                status="running",
                payload={"label": "正在导出 Excel"},
            )
        )

        overview = self.service.overview()

        self.assertEqual(overview["latest_progress"]["phase_code"], "exporting")
        self.assertEqual(overview["latest_progress"]["phase_label"], "正在导出 Excel")
        self.assertEqual(overview["latest_progress"]["archive_pending_count"], 0)

    def test_overview_repairs_missing_archive_files_from_raw_source(self) -> None:
        raw_dir = os.path.join(self.temp_dir.name, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        source_file = os.path.join(raw_dir, "repair-me.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>repair</body></html>")
        expected_archive_path = os.path.join(
            self.service.get_basic_settings()["archive_root"],
            "2026年3月",
            "G32025SH1000194-测试项目.html",
        )
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-repair",
                revision_hash="hash-repair",
                project_code="G32025SH1000194",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026/03/20",
                state="pending_mapping",
                source_file=source_file,
                archive_path=expected_archive_path,
                parser_payload={"项目编号": "G32025SH1000194", "项目名称": "测试项目"},
                postprocess_payload={"项目编号": "G32025SH1000194", "项目名称": "测试项目", "项目类型": "股权转让"},
                findings=[],
            )
        )

        self.service.overview()

        self.assertTrue(os.path.isfile(expected_archive_path))

    def test_overview_collapses_managed_raw_source_to_archive_path(self) -> None:
        raw_dir = os.path.join(self.config.DATA_ROOT, "raw", "auto", "挂牌_股权转让")
        os.makedirs(raw_dir, exist_ok=True)
        source_file = os.path.join(raw_dir, "collapse-me.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>collapse</body></html>")
        os.makedirs(f"{os.path.splitext(source_file)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(source_file)[0]}_files/test.css", "w", encoding="utf-8") as handle:
            handle.write("body{}")

        archive_path = os.path.join(
            self.service.get_basic_settings()["archive_root"],
            "2026年3月",
            "G32025SH1000333-测试项目.html",
        )
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-collapse",
                revision_hash="hash-collapse",
                project_code="G32025SH1000333",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=archive_path,
                parser_payload={"项目编号": "G32025SH1000333", "项目名称": "测试项目", "项目类型": "股权转让", "类型": "国资"},
                postprocess_payload={"项目编号": "G32025SH1000333", "项目名称": "测试项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[],
            )
        )

        self.service.overview()
        record = self.service.store.get_record("rec-collapse")

        self.assertEqual(record["source_file"], archive_path)
        self.assertFalse(os.path.exists(source_file))
        self.assertFalse(os.path.exists(f"{os.path.splitext(source_file)[0]}_files"))

    def test_overview_collapse_updates_downloaded_event_source_for_blank_code_tokens(self) -> None:
        raw_dir = os.path.join(self.config.DATA_ROOT, "raw", "auto", "挂牌_股权转让")
        os.makedirs(raw_dir, exist_ok=True)
        source_file = os.path.join(raw_dir, "collapse-no-code.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>collapse no code</body></html>")
        os.makedirs(f"{os.path.splitext(source_file)[0]}_files", exist_ok=True)
        with open(f"{os.path.splitext(source_file)[0]}_files/test.css", "w", encoding="utf-8") as handle:
            handle.write("body{}")

        archive_path = os.path.join(
            self.service.get_basic_settings()["archive_root"],
            "2026年3月",
            "legacy-no-code.html",
        )
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-collapse-no-code",
                revision_hash="hash-collapse-no-code",
                project_code="",
                project_name="无编号项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=archive_path,
                parser_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                postprocess_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[],
            )
        )
        job_id = self.service.store.create_job("one_click")
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": source_file,
                    "project_code": "",
                    "page_url": "https://example.test/legacy/no-code",
                    "project_id": "LEGACYNOCODE001",
                },
            )
        )

        self.service.overview()
        tokens = self.service.store.list_existing_candidate_tokens(states=["ready"])

        self.assertIn("page_url:https://example.test/legacy/no-code", tokens)
        self.assertIn("project_id:LEGACYNOCODE001", tokens)

    def test_overview_copy_repair_updates_downloaded_event_source_for_blank_code_tokens(self) -> None:
        raw_dir = os.path.join(self.temp_dir.name, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        source_file = os.path.join(raw_dir, "repair-no-code.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>repair no code</body></html>")

        expected_archive_path = os.path.join(
            self.service.get_basic_settings()["archive_root"],
            "2026年3月",
            "unknown-无编号项目.html",
        )
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-repair-no-code",
                revision_hash="hash-repair-no-code",
                project_code="",
                project_name="无编号项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026/03/20",
                state="ready",
                source_file=source_file,
                archive_path=expected_archive_path,
                parser_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                postprocess_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[],
            )
        )
        job_id = self.service.store.create_job("one_click")
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": source_file,
                    "project_code": "",
                    "page_url": "https://example.test/repair/no-code",
                    "project_id": "REPAIRNOCODE001",
                },
            )
        )

        self.service.overview()
        tokens = self.service.store.list_existing_candidate_tokens(states=["ready"])

        self.assertIn("page_url:https://example.test/repair/no-code", tokens)
        self.assertIn("project_id:REPAIRNOCODE001", tokens)

    def test_overview_repairs_archive_links_beyond_recent_500_records_for_blank_code_tokens(self) -> None:
        raw_dir = os.path.join(self.config.DATA_ROOT, "raw", "auto", "挂牌_股权转让")
        os.makedirs(raw_dir, exist_ok=True)
        archive_root = self.service.get_basic_settings()["archive_root"]

        oldest_page_url = "https://example.test/oldest/no-code"
        oldest_project_id = "OLDESTNOCODE001"
        oldest_source_file = ""
        oldest_archive_path = ""
        for index in range(501):
            source_file = os.path.join(raw_dir, f"repair-window-{index}.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write(f"<html><body>{index}</body></html>")
            archive_path = os.path.join(archive_root, "2026年3月", f"repair-window-{index}.html")
            os.makedirs(os.path.dirname(archive_path), exist_ok=True)
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write(f"<html><body>archive-{index}</body></html>")
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id=f"rec-repair-window-{index}",
                    revision_hash=f"hash-repair-window-{index}",
                    project_code="",
                    project_name=f"无编号项目{index}",
                    project_type="股权转让",
                    exchange="shanghai",
                    listing_date="2026-03-21",
                    state="ready",
                    source_file=source_file,
                    archive_path=archive_path,
                    parser_payload={"项目名称": f"无编号项目{index}", "项目类型": "股权转让", "类型": "国资"},
                    postprocess_payload={"项目名称": f"无编号项目{index}", "项目类型": "股权转让", "类型": "国资"},
                    findings=[],
                )
            )
            job_id = self.service.store.create_job("one_click")
            page_url = oldest_page_url if index == 0 else f"https://example.test/repair-window/{index}"
            project_id = oldest_project_id if index == 0 else f"WINDOWNOCODE{index:03d}"
            self.service.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="downloaded",
                    status="ok",
                    payload={
                        "source_file": source_file,
                        "project_code": "",
                        "page_url": page_url,
                        "project_id": project_id,
                    },
                )
            )
            if index == 0:
                oldest_source_file = source_file
                oldest_archive_path = archive_path

        self.service.overview()
        oldest_record = self.service.store.get_record("rec-repair-window-0")
        tokens = self.service.store.list_existing_candidate_tokens(states=["ready"])

        self.assertEqual(oldest_record["source_file"], oldest_archive_path)
        self.assertFalse(os.path.exists(oldest_source_file))
        self.assertIn(f"page_url:{oldest_page_url}", tokens)
        self.assertIn(f"project_id:{oldest_project_id}", tokens)

    def test_launch_one_click_repairs_archive_links_before_pipeline_starts(self) -> None:
        raw_dir = os.path.join(self.config.DATA_ROOT, "raw", "auto", "挂牌_股权转让")
        os.makedirs(raw_dir, exist_ok=True)
        source_file = os.path.join(raw_dir, "launch-collapse-no-code.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>launch collapse no code</body></html>")

        archive_path = os.path.join(
            self.service.get_basic_settings()["archive_root"],
            "2026年3月",
            "launch-legacy-no-code.html",
        )
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-launch-collapse-no-code",
                revision_hash="hash-launch-collapse-no-code",
                project_code="",
                project_name="无编号项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=archive_path,
                parser_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                postprocess_payload={"项目名称": "无编号项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[],
            )
        )
        job_id = self.service.store.create_job("one_click")
        self.service.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": source_file,
                    "project_code": "",
                    "page_url": "https://example.test/launch/no-code",
                    "project_id": "LAUNCHNOCODE001",
                },
            )
        )
        captured: dict[str, object] = {}

        def fake_run_streaming_daily_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
        ):
            captured["tokens"] = self.service.store.list_existing_candidate_tokens(states=["ready"])
            job_created_callback("job-launch-fix", self.service.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_run_streaming_daily_pipeline):
            payload = self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

        self.assertEqual(payload["job_id"], "job-launch-fix")
        self.assertIn("page_url:https://example.test/launch/no-code", captured["tokens"])
        self.assertIn("project_id:LAUNCHNOCODE001", captured["tokens"])

    def test_launch_one_click_normalizes_legacy_pending_mapping_before_pipeline_starts(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-launch-normalize.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy normalize</body></html>")

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-launch-legacy-normalize",
                revision_hash="hash-launch-legacy-normalize",
                project_code="G32026SH1999001",
                project_name="历史未知类型项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1999001", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026SH1999001", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )
        captured: dict[str, object] = {}

        def fake_run_streaming_daily_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
        ):
            captured["pending_mapping_count"] = self.service.store.count_pending_mappings()
            captured["pending_records"] = self.service.store.iter_latest_records(states=["pending_mapping"])
            captured["ready_records"] = self.service.store.iter_latest_records(states=["ready"])
            job_created_callback("job-launch-normalize", self.service.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_run_streaming_daily_pipeline):
            payload = self.service.launch_one_click({"start_date": "2026-03-22", "end_date": "2026-03-22"})

        self.assertEqual(payload["job_id"], "job-launch-normalize")
        self.assertEqual(captured["pending_mapping_count"], 1)
        self.assertEqual(len(captured["pending_records"]), 1)
        self.assertEqual(captured["pending_records"][0]["record_id"], "rec-launch-legacy-normalize")
        self.assertEqual(captured["ready_records"], [])

    def test_mapping_refresh_zero_actual_repairs_resolves_to_failed(self) -> None:
        self._insert_record_with_mapping_source(
            record_id="rec-zero-repair",
            state="pending_mapping",
            transferor="待回刷企业",
            group_name="待回刷集团",
        )

        with patch.object(
            self.service,
            "reprocess_record",
            return_value={"record_id": "rec-zero-repair", "project_code": "CODE-rec-zero-repair", "state": "pending_mapping"},
        ):
            payload = self.service.launch_pending_mapping_refresh({})

        job = self._wait_for_job_status(str(payload["job_id"]))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["summary"]["pending_mapping_count"], 1)

    def test_run_export_reports_pending_mapping_blockers_when_no_ready_rows(self) -> None:
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-pending-export",
                revision_hash="hash-pending-export",
                project_code="G32025SH1000200",
                project_name="待补映射项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026/03/20",
                state="pending_mapping",
                source_file=os.path.join(self.temp_dir.name, "pending.html"),
                archive_path=os.path.join(self.temp_dir.name, "archive", "pending.html"),
                parser_payload={"项目编号": "G32025SH1000200", "项目名称": "待补映射项目"},
                postprocess_payload={"项目编号": "G32025SH1000200", "项目名称": "待补映射项目", "项目类型": "股权转让"},
                findings=[],
            )
        )

        payload = self.service.run_export({"scope": {"date_from": "2026-03-20", "date_to": "2026-03-20"}})

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["empty_reason_code"], "pending_mapping_blocked")
        self.assertEqual(payload["scope_state_counts"]["pending_mapping"], 1)
        self.assertIn("待补映射 1 条", payload["message"])

    def test_run_export_creates_export_job_and_events(self) -> None:
        class _FakeExportResult:
            export_id = "exp-job"
            cursor_key = "cursor-job"
            new_records = 2
            changed_records = 1
            artifacts = [
                type("Artifact", (), {"file_path": os.path.join(self.temp_dir.name, "exports", "test.xlsx")})(),
            ]

        with patch("desktop_backend.app_service.run_ready_export", return_value=_FakeExportResult()):
            payload = self.service.run_export({"scope": {"date_from": "2026-03-21", "date_to": "2026-03-21"}})

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_type"], "export_excel")
        job = self.service.get_job(payload["job_id"])
        self.assertEqual(job["job_type"], "export_excel")
        self.assertEqual(job["status"], "success")
        self.assertTrue(any(str(event["stage"]) == "exporting" for event in job["events"]))

    def test_run_export_defaults_to_rebuild_mode(self) -> None:
        captured: dict[str, object] = {}

        class _FakeExportResult:
            export_id = "exp-test"
            cursor_key = "default"
            artifacts = []
            new_records = 0
            changed_records = 0

        def fake_run_ready_export(store, request):
            captured["mode"] = request.mode
            return _FakeExportResult()

        with patch("desktop_backend.app_service.run_ready_export", side_effect=fake_run_ready_export):
            self.service.run_export({"scope": {"date_from": "2026-03-20", "date_to": "2026-03-20"}})

        self.assertEqual(captured["mode"], "rebuild")

    def test_list_records_and_run_export_share_same_scope_contract(self) -> None:
        self._insert_ready_record(record_id="rec-scope-contract", project_code="G32025SH1001999")
        scope = {
            "record_family": "listing",
            "state": "all",
            "project_type": "equity_transfer",
            "keyword": "",
            "date_from": "2026-03-21",
            "date_to": "2026-03-21",
            "page": 2,
            "page_size": 5,
        }
        list_payload = self.service.list_records({"scope": scope})
        captured: dict[str, object] = {}

        class _FakeExportResult:
            export_id = "exp-scope"
            cursor_key = "cursor-scope"
            artifacts = []
            new_records = 0
            changed_records = 0

        def fake_run_ready_export(store, request):
            captured["record_family"] = request.record_family
            captured["date_from"] = request.date_from
            captured["date_to"] = request.date_to
            captured["business_types"] = list(request.business_types)
            return _FakeExportResult()

        with patch("desktop_backend.app_service.run_ready_export", side_effect=fake_run_ready_export):
            export_payload = self.service.run_export({"scope": scope})

        self.assertEqual(
            list_payload["scope"],
            {
                "record_family": "listing",
                "state": "all",
                "project_type": "equity_transfer",
                "keyword": "",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "page": 2,
                "page_size": 5,
            },
        )
        self.assertEqual(export_payload["scope"], list_payload["scope"])
        self.assertEqual(captured["record_family"], "listing")
        self.assertEqual(captured["date_from"], "2026-03-21")
        self.assertEqual(captured["date_to"], "2026-03-21")
        self.assertEqual(captured["business_types"], ["股权转让"])

    def test_list_records_summary_splits_filtered_counts_and_page_counts(self) -> None:
        self._insert_ready_record(record_id="rec-summary-split-1", project_code="G32025SH1003001")
        self._insert_ready_record(record_id="rec-summary-split-2", project_code="G32025SH1003002")
        pending_source_file = os.path.join(self.temp_dir.name, "rec-summary-split-pending.html")
        with open(pending_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>pending summary split</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-summary-split-pending",
                revision_hash="hash-rec-summary-split-pending",
                project_code="G32025SH1003003",
                project_name="待补映射项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=pending_source_file,
                archive_path=pending_source_file,
                parser_payload={"项目编号": "G32025SH1003003", "项目名称": "待补映射项目"},
                postprocess_payload={"项目编号": "G32025SH1003003", "项目名称": "待补映射项目", "项目类型": "股权转让"},
                findings=[],
            )
        )

        payload = self.service.list_records({"scope": {"state": "all", "project_type": "equity_transfer", "page": 1, "page_size": 1}})

        self.assertEqual(payload["summary"]["filtered_state_counts"], {"pending_mapping": 1, "ready": 2})
        self.assertEqual(payload["summary"]["page_state_counts"], {payload["rows"][0]["state"]: 1})
        self.assertEqual(payload["summary"]["total_count"], 3)
        self.assertEqual(payload["summary"]["visible_count"], 1)
        self.assertEqual(payload["summary"]["page"], 1)
        self.assertEqual(payload["summary"]["page_size"], 1)
        self.assertEqual(payload["summary"]["page_count"], 3)
        self.assertNotIn("state_counts", payload["summary"])

    def test_overview_and_list_records_do_not_rewrite_failed_record_identity(self) -> None:
        failed_source_file = os.path.join(self.temp_dir.name, "failed-original-source.html")
        with open(failed_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed original source</body></html>")

        failed = self.service.store.upsert_failed_record(
            project_code="FAILED-IDENTITY-001",
            source_file=failed_source_file,
            state="parse_failed",
            error_type="parse_failed",
            error_message="parse boom",
            payload={
                "source_identity": {
                    "original_evidence_path": failed_source_file,
                    "original_source_file": failed_source_file,
                },
                "source_file": failed_source_file,
            },
        )

        self.service.overview()
        payload = self.service.list_records({"scope": {"state": "all", "project_type": "all"}})
        record = self.service.store.get_record(str(failed["record_id"]))
        row = next(item for item in payload["rows"] if item["record_id"] == str(failed["record_id"]))

        self.assertEqual(record["source_file"], failed_source_file)
        self.assertEqual(record["source_identity_json"]["original_evidence_path"], failed_source_file)
        self.assertEqual(record["source_identity_json"]["original_source_file"], failed_source_file)
        self.assertEqual(row["source_file"], failed_source_file)


if __name__ == "__main__":
    unittest.main()
