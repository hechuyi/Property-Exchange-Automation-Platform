from __future__ import annotations

import os
import tempfile
import types
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch


@dataclass(frozen=True)
class _FakeDownloadRunRequest:
    exchange: str = "all"
    project_type: str = "all"
    list_tasks: bool = False
    output_root: str = ""
    force_manual_root: bool = False
    start_date: str | None = None
    end_date: str | None = None
    page_size: int | None = None
    max_pages: int | None = None
    concurrency: int = 1
    resume: bool = True
    save_json: bool = False
    sse_ssl_verify: bool = True
    sse_ssl_fallback_insecure: bool = True
    sse_ca_bundle: str | None = None
    log_dir: str = ""
    log_file: str | None = None
    verbose: bool = False
    auto_split: bool = False
    split_candidates: int = 0
    split_min_days: int = 0
    split_max_depth: int = 0
    split_plan_only: bool = False
    split_plan_file: str | None = None
    split_use_plan: bool = False
    split_mode: str = "fast"
    chunk_state_file: str | None = None
    item_saved_callback: object = None
    task_progress_callback: object = None


@dataclass(frozen=True)
class _FakePreparedSession:
    settings: object
    output_root: str
    tasks: list[object]


class _FakeDownloadRunnerError(RuntimeError):
    pass


fake_download_runner = types.ModuleType("peap.download_runner")
fake_download_runner.DownloadRunnerError = _FakeDownloadRunnerError
fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest
fake_download_runner.prepare_download_session = lambda *args, **kwargs: None
fake_download_runner.build_downloader = lambda *args, **kwargs: None
fake_download_runner.run_downloader = lambda *args, **kwargs: None
fake_download_runner.run_downloader_with_prefetched = lambda *args, **kwargs: None
fake_download_runner.task_progress_label = lambda spec: getattr(spec, "display_name", "")

with patch.dict("sys.modules", {"peap.download_runner": fake_download_runner}):
    import peap.download_oneclick as download_oneclick_module
    from peap.download_oneclick import DownloadOneClickRequest, run_download_oneclick

DownloadRunnerError = _FakeDownloadRunnerError
DownloadRunRequest = _FakeDownloadRunRequest


@dataclass
class _FakeSummary:
    listed_items: int = 0
    detail_fetched: int = 0
    saved: int = 0
    skipped_by_list_date: int = 0
    skipped_by_detail_date: int = 0
    skipped_by_resume: int = 0
    skipped_by_duplicate: int = 0
    skipped_by_missing_xmid: int = 0
    detail_candidates: int = 0
    detail_failed: int = 0
    list_unaccounted: int = 0
    detail_unaccounted: int = 0
    pages_requested: int = 0
    candidate_entries: list[dict[str, object]] | None = None
    errors: list[str] | None = None


class DownloadOneClickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(LOG_DIR=self.temp_dir.name, LOG_LEVEL="INFO", LOG_TO_FILE=False)

    def _build_download_request(self) -> DownloadRunRequest:
        return DownloadRunRequest(
            exchange="sse",
            project_type="physical_asset",
            output_root=self.temp_dir.name,
            start_date="2026-01-01",
            end_date="2026-01-02",
            concurrency=2,
            log_dir=self.temp_dir.name,
            log_file=os.path.join(self.temp_dir.name, "download.log"),
        )

    def test_run_download_oneclick_collects_before_execute(self) -> None:
        task_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        call_order: list[tuple[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            call_order.append(("prepare", request))
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec, "output_root": output_root}

        def fake_run_downloader(downloader, *, start_date, end_date, list_only):
            call_order.append(("collect", list_only))
            return _FakeSummary(
                listed_items=5,
                detail_candidates=3,
                candidate_entries=[{"project_code": "A"}, {"project_code": "B"}, {"project_code": "C"}],
                errors=[],
            )

        def fake_run_downloader_with_prefetched(downloader, *, start_date, end_date, list_only, prefetched_candidates):
            call_order.append(("execute", list(prefetched_candidates)))
            return _FakeSummary(
                detail_fetched=3,
                saved=3,
                detail_candidates=3,
                candidate_entries=[],
                errors=[],
            )

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(download_oneclick_module, "run_downloader", side_effect=fake_run_downloader),
            patch.object(download_oneclick_module, "run_downloader_with_prefetched", side_effect=fake_run_downloader_with_prefetched),
            patch.object(download_oneclick_module, "task_progress_label", return_value="上交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    plan_file=os.path.join(self.temp_dir.name, "plan.json"),
                    keep_plan=False,
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.aggregate_summary["saved"], 3)
        self.assertEqual(len(result.stages), 2)
        self.assertEqual(call_order[0][0], "prepare")
        self.assertEqual(call_order[1], ("collect", True))
        self.assertEqual(call_order[2][0], "prepare")
        self.assertEqual(call_order[3][0], "execute")
        self.assertEqual(len(call_order[3][1]), 3)

    def test_run_download_oneclick_aborts_when_collect_stage_fails(self) -> None:
        def fake_prepare_download_session(request, *, logger, config_obj):
            raise DownloadRunnerError("collect boom")

        with patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    plan_file=os.path.join(self.temp_dir.name, "plan.json"),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(len(result.stages), 1)
        self.assertIn("collect boom", result.errors)

    def test_run_download_oneclick_emits_task_context_for_stage_callback(self) -> None:
        task_spec = SimpleNamespace(task_id="cbex:physical_asset", display_name="北交所 - 挂牌实物资产")
        stage_events: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=8,
                    detail_candidates=4,
                    candidate_entries=[{"project_code": "X"}],
                    errors=[],
                ),
            ),
            patch.object(
                download_oneclick_module,
                "run_downloader_with_prefetched",
                return_value=_FakeSummary(detail_fetched=1, saved=1, detail_candidates=1, candidate_entries=[], errors=[]),
            ),
            patch.object(download_oneclick_module, "task_progress_label", return_value="北交所 - 挂牌实物资产"),
        ):
            run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        prepare_events = [event for event in stage_events if event.get("phase_code") == "prepare_tasks"]
        save_events = [event for event in stage_events if event.get("phase_code") == "save_pages"]
        self.assertTrue(prepare_events)
        self.assertTrue(save_events)
        self.assertEqual(prepare_events[0]["task_label"], "北交所 - 挂牌实物资产")
        self.assertGreater(int(prepare_events[0]["phase_percent"]), 0)
        self.assertEqual(save_events[0]["task_label"], "北交所 - 挂牌实物资产")

    def test_run_download_oneclick_skips_empty_execute_tasks(self) -> None:
        empty_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        candidate_spec = SimpleNamespace(task_id="cquae:physical_asset", display_name="重交所 - 挂牌实物资产")
        execute_calls: list[list[dict[str, object]]] = []
        stage_events: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(
                settings=object(),
                output_root=self.temp_dir.name,
                tasks=[empty_spec, candidate_spec],
            )

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        def fake_run_downloader(downloader, *, start_date, end_date, list_only):
            task_id = downloader["spec"].task_id
            if task_id == "sse:physical_asset":
                return _FakeSummary(listed_items=20, detail_candidates=0, candidate_entries=[], errors=[])
            return _FakeSummary(
                listed_items=5,
                detail_candidates=1,
                candidate_entries=[{"project_code": "CQ-1", "project_id": "CQ-1", "page_url": "https://example.test/cq/1"}],
                errors=[],
            )

        def fake_run_downloader_with_prefetched(downloader, *, start_date, end_date, list_only, prefetched_candidates):
            execute_calls.append(list(prefetched_candidates))
            return _FakeSummary(
                detail_fetched=1,
                saved=1,
                detail_candidates=1,
                candidate_entries=[],
                errors=[],
            )

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(download_oneclick_module, "run_downloader", side_effect=fake_run_downloader),
            patch.object(download_oneclick_module, "run_downloader_with_prefetched", side_effect=fake_run_downloader_with_prefetched),
            patch.object(
                download_oneclick_module,
                "task_progress_label",
                side_effect=lambda spec: "上交所 - 挂牌实物资产" if spec.task_id == "sse:physical_asset" else "重交所 - 挂牌实物资产",
            ),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(execute_calls), 1)
        self.assertEqual(execute_calls[0][0]["project_code"], "CQ-1")
        save_events = [event for event in stage_events if event.get("phase_code") == "save_pages" and event.get("status") == "running"]
        self.assertEqual(len(save_events), 1)
        self.assertEqual(save_events[0]["task_total"], 1)
        self.assertEqual(save_events[0]["task_label"], "重交所 - 挂牌实物资产")

    def test_run_download_oneclick_short_circuits_when_all_candidates_are_filtered_out(self) -> None:
        task_spec = SimpleNamespace(task_id="cquae:physical_asset", display_name="重交所 - 挂牌实物资产")
        stage_events: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=3,
                    detail_candidates=1,
                    candidate_entries=[{"project_code": "CQ-EXISTING", "project_id": "CQ-EXISTING", "page_url": "https://example.test/cq/existing"}],
                    errors=[],
                ),
            ),
            patch.object(download_oneclick_module, "run_downloader_with_prefetched") as execute_mock,
            patch.object(download_oneclick_module, "task_progress_label", return_value="重交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    existing_project_codes=frozenset({"CQ-EXISTING"}),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        execute_mock.assert_not_called()
        self.assertEqual(len(result.stages), 2)
        self.assertEqual(result.stages[1].summary_payload["aggregate_summary"]["duplicate_skipped"], 1)
        final_save_event = [event for event in stage_events if event.get("phase_code") == "save_pages"][-1]
        self.assertEqual(final_save_event["status"], "done")
        self.assertEqual(final_save_event["phase_percent"], 98)
        self.assertIn("无需下载", str(final_save_event.get("label") or ""))

    def test_run_download_oneclick_emits_final_collect_failure_reason(self) -> None:
        task_spec = SimpleNamespace(task_id="tpre:pre_disclosure", display_name="某交易所 - 预披露")
        stage_events: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=2,
                    detail_candidates=1,
                    candidate_entries=[{"project_code": "PRE-1"}],
                    errors=["tpre: collect-failed: upstream 500"],
                ),
            ),
            patch.object(download_oneclick_module, "task_progress_label", return_value="某交易所 - 预披露"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
        )

        self.assertEqual(result.exit_code, 1)
        prepare_events = [event for event in stage_events if event.get("phase_code") == "prepare_tasks"]
        self.assertEqual(len(prepare_events), 1)
        final_event = prepare_events[-1]
        self.assertEqual(final_event["status"], "failed")
        self.assertEqual(final_event["error_message"], "tpre: collect-failed: upstream 500")
        self.assertEqual(final_event["errors"], ["tpre: collect-failed: upstream 500"])

    def test_run_download_oneclick_classifies_collect_failure_generically(self) -> None:
        task_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        stage_events: list[dict[str, object]] = []
        raw_error = (
            "list-realright-page-1-request-failed: "
            "POST https://www.suaee.com/si/prjs/realright/list "
            "failed: HTTP Error 502: Bad Gateway"
        )

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=0,
                    detail_candidates=0,
                    candidate_entries=[],
                    errors=[raw_error],
                ),
            ),
            patch.object(download_oneclick_module, "task_progress_label", return_value="上交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 1)
        final_event = [event for event in stage_events if event.get("phase_code") == "prepare_tasks"][-1]
        self.assertEqual(final_event["status"], "failed")
        self.assertEqual(final_event["error_code"], "collect_failed")
        self.assertEqual(final_event["error_message"], raw_error)
        self.assertEqual(final_event["errors"], [raw_error])

    def test_run_download_oneclick_emits_single_terminal_prepare_failure_event(self) -> None:
        task_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        stage_events: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=0,
                    detail_candidates=0,
                    candidate_entries=[],
                    errors=["upstream broken"],
                ),
            ),
            patch.object(download_oneclick_module, "task_progress_label", return_value="上交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 1)
        failed_prepare_events = [
            event for event in stage_events if event.get("phase_code") == "prepare_tasks" and event.get("status") == "failed"
        ]
        self.assertEqual(len(failed_prepare_events), 1)

    def test_run_download_oneclick_emits_single_failed_prepare_tasks_terminal_event(self) -> None:
        task_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        stage_events: list[dict[str, object]] = []
        raw_error = (
            "list-realright-page-1-request-failed: "
            "POST https://www.suaee.com/si/prjs/realright/list "
            "failed: HTTP Error 502: Bad Gateway"
        )

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec}

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(
                download_oneclick_module,
                "run_downloader",
                return_value=_FakeSummary(
                    listed_items=0,
                    detail_candidates=0,
                    candidate_entries=[],
                    errors=[raw_error],
                ),
            ),
            patch.object(download_oneclick_module, "task_progress_label", return_value="上交所 - 挂牌实物资产"),
        ):
            run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    stage_callback=lambda payload: stage_events.append(dict(payload)),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        prepare_failed = [
            event
            for event in stage_events
            if event.get("phase_code") == "prepare_tasks" and event.get("status") == "failed"
        ]
        self.assertEqual(len(prepare_failed), 1)

    def test_run_download_oneclick_skips_already_ingested_project_codes_before_execute(self) -> None:
        task_spec = SimpleNamespace(task_id="sse:physical_asset", display_name="上交所 - 挂牌实物资产")
        executed_candidates: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec, "output_root": output_root}

        def fake_run_downloader(downloader, *, start_date, end_date, list_only):
            return _FakeSummary(
                listed_items=3,
                detail_candidates=3,
                candidate_entries=[
                    {"project_code": "A"},
                    {"project_code": "B"},
                    {"project_code": "C"},
                ],
                errors=[],
            )

        def fake_run_downloader_with_prefetched(downloader, *, start_date, end_date, list_only, prefetched_candidates):
            executed_candidates.extend(list(prefetched_candidates))
            return _FakeSummary(
                detail_fetched=len(prefetched_candidates),
                saved=len(prefetched_candidates),
                detail_candidates=len(prefetched_candidates),
                candidate_entries=[],
                errors=[],
            )

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(download_oneclick_module, "run_downloader", side_effect=fake_run_downloader),
            patch.object(download_oneclick_module, "run_downloader_with_prefetched", side_effect=fake_run_downloader_with_prefetched),
            patch.object(download_oneclick_module, "task_progress_label", return_value="上交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    existing_project_codes={"A", "C"},
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(executed_candidates, [{"project_code": "B"}])
        self.assertEqual(result.aggregate_summary["saved"], 1)

    def test_run_download_oneclick_skips_already_seen_candidate_tokens_before_execute(self) -> None:
        task_spec = SimpleNamespace(task_id="cquae:physical_asset", display_name="重交所 - 挂牌实物资产")
        executed_candidates: list[dict[str, object]] = []

        def fake_prepare_download_session(request, *, logger, config_obj):
            return _FakePreparedSession(settings=object(), output_root=self.temp_dir.name, tasks=[task_spec])

        def fake_build_downloader(spec, *, args, output_root, logger):
            return {"spec": spec, "output_root": output_root}

        def fake_run_downloader(downloader, *, start_date, end_date, list_only):
            return _FakeSummary(
                listed_items=2,
                detail_candidates=2,
                candidate_entries=[
                    {"project_id": "CQ001", "project_code": None, "page_url": "https://example.test/detail/1"},
                    {"project_id": "CQ002", "project_code": None, "page_url": "https://example.test/detail/2"},
                ],
                errors=[],
            )

        def fake_run_downloader_with_prefetched(downloader, *, start_date, end_date, list_only, prefetched_candidates):
            executed_candidates.extend(list(prefetched_candidates))
            return _FakeSummary(
                detail_fetched=len(prefetched_candidates),
                saved=len(prefetched_candidates),
                detail_candidates=len(prefetched_candidates),
                candidate_entries=[],
                errors=[],
            )

        with (
            patch.object(download_oneclick_module, "prepare_download_session", side_effect=fake_prepare_download_session),
            patch.object(download_oneclick_module, "build_downloader", side_effect=fake_build_downloader),
            patch.object(download_oneclick_module, "run_downloader", side_effect=fake_run_downloader),
            patch.object(download_oneclick_module, "run_downloader_with_prefetched", side_effect=fake_run_downloader_with_prefetched),
            patch.object(download_oneclick_module, "task_progress_label", return_value="重交所 - 挂牌实物资产"),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=self._build_download_request(),
                    existing_candidate_tokens=frozenset({"project_id:CQ001"}),
                ),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            executed_candidates,
            [{"project_id": "CQ002", "project_code": None, "page_url": "https://example.test/detail/2"}],
        )
        self.assertEqual(result.aggregate_summary["saved"], 1)


if __name__ == "__main__":
    unittest.main()
