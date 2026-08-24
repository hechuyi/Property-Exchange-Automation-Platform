from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peap.download_runner import (
    DownloadRunRequest,
    build_download_runner_settings,
    build_task_list_payload,
    prepare_download_session,
    resolve_tasks,
)
from peap.download_tasks import exchange_choices


class DownloadMainlineContractsTest(unittest.TestCase):
    def _build_config(self, *, output_root: str) -> object:
        return SimpleNamespace(
            AUTO_HTML_FOLDER=output_root,
            HTML_FOLDER=f"{output_root}_manual",
            PROJECT_ROOT=f"{output_root}_project",
            DOWNLOAD_CHUNK_STATE_DIR=f"{output_root}_chunk_state",
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:listing:physical_asset": 20,
                "sse:listing:equity_transfer": 20,
                "sse:listing:capital_increase": 20,
                "sse:listing:pre_disclosure": 20,
                "cbex:listing:physical_asset": 20,
                "cbex:listing:equity_transfer": 20,
                "cbex:listing:capital_increase": 20,
                "cbex:listing:pre_disclosure": 20,
                "tpre:listing:physical_asset": 20,
                "tpre:listing:equity_transfer": 20,
                "tpre:listing:capital_increase": 20,
                "tpre:listing:pre_disclosure": 20,
                "cquae:listing:physical_asset": 20,
                "cquae:listing:equity_transfer": 20,
                "cquae:listing:capital_increase": 20,
                "cquae:listing:pre_disclosure": 20,
                "shandong:listing:equity_transfer": 20,
                "shandong:listing:capital_increase": 20,
                "guangdong:listing:equity_transfer": 20,
                "guangdong:listing:capital_increase": 20,
                "shenzhen:listing:equity_transfer": 20,
                "shenzhen:listing:capital_increase": 20,
            },
            is_path_within_project_root=lambda _path: False,
        )

    def test_build_task_list_payload_keeps_public_resource_out_of_exchange_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            payload = build_task_list_payload(config)

        deal_tasks = [row for row in payload if row["record_family"] == "deal"]
        self.assertEqual(len(deal_tasks), 10)
        self.assertEqual(
            {row["source_id"] for row in deal_tasks},
            {"sse", "cbex", "tpre", "cquae"},
        )
        self.assertEqual(
            {row["business_id"] for row in deal_tasks},
            {"deal_equity_transfer", "deal_physical_asset", "deal_capital_increase"},
        )
        self.assertNotIn("tpre:deal:deal_physical_asset", {row["task_id"] for row in deal_tasks})
        self.assertNotIn("cquae:deal:deal_physical_asset", {row["task_id"] for row in deal_tasks})

    def test_public_resource_is_not_an_exchange_task_or_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            all_equity = resolve_tasks(config, "all", "deal", "deal_equity_transfer")
            explicit_exchange = resolve_tasks(config, "sse", "deal", "deal_equity_transfer")

        self.assertNotIn(
            "public_resource:deal:deal_equity_transfer",
            {task.task_id for task in all_equity},
        )
        self.assertNotIn(
            "public_resource:deal:deal_equity_transfer",
            {task.task_id for task in explicit_exchange},
        )
        self.assertNotIn("public_resource", exchange_choices(record_family="deal"))

    def test_build_task_list_payload_surfaces_exchange_specific_deal_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            payload = {row["task_id"]: row for row in build_task_list_payload(config)}

        self.assertEqual(
            payload["sse:deal:deal_equity_transfer"]["list_endpoint"],
            "/si/notice/getDealNoticeList",
        )
        self.assertEqual(
            payload["sse:deal:deal_equity_transfer"]["detail_route"],
            "/si/notice/getNoticeDetail",
        )
        self.assertEqual(payload["sse:deal:deal_equity_transfer"]["detail_api_endpoint"], "")
        self.assertEqual(
            payload["tpre:deal:deal_equity_transfer"]["list_endpoint"],
            "/transaction/biz/transaction-management/anmuas/result-notice/page?bizType=PROPERTY_RIGHT_TRANSFER",
        )
        self.assertEqual(
            payload["tpre:deal:deal_capital_increase"]["detail_route"],
            "/transaction-view/data/common/transaction-announcement",
        )
        self.assertEqual(
            payload["tpre:deal:deal_equity_transfer"]["render_page_route"],
            "/transaction-view/data/common/transaction-announcement",
        )
        self.assertEqual(payload["tpre:deal:deal_equity_transfer"]["detail_api_endpoint"], "")
        self.assertEqual(
            payload["tpre:deal:deal_capital_increase"]["render_page_route"],
            "/transaction-view/data/common/transaction-announcement",
        )
        self.assertEqual(payload["tpre:deal:deal_capital_increase"]["detail_api_endpoint"], "")
        self.assertEqual(
            payload["tpre:deal:deal_capital_increase"]["transferee_details_endpoint"],
            "/transaction/biz/increase/transaction/transferee/anmuas/result-notice/details",
        )
        self.assertNotIn("cquae:deal:deal_physical_asset", payload)
        self.assertNotIn("tpre:deal:deal_physical_asset", payload)
        self.assertEqual(
            payload["cbex:deal:deal_capital_increase"]["list_endpoint"],
            "/xm/qyzz/cjjggs/",
        )
        self.assertEqual(payload["cbex:deal:deal_capital_increase"]["detail_api_endpoint"], "")
        self.assertEqual(payload["cquae:deal:deal_equity_transfer"]["detail_api_endpoint"], "")
        self.assertNotIn("collection_date", payload["sse:deal:deal_capital_increase"]["date_field_candidates"])
        self.assertNotIn("collection_date", payload["cbex:deal:deal_capital_increase"]["date_field_candidates"])
        self.assertGreater(len(payload["tpre:deal:deal_equity_transfer"]["date_field_candidates"]), 0)

    def test_prepare_download_session_accepts_deal_scope_with_executable_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            settings = build_download_runner_settings(config)
            logger = SimpleNamespace(
                info=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            )
            request = DownloadRunRequest(
                exchange="sse",
                record_family="deal",
                business_id="deal_equity_transfer",
                output_root=tmp_dir,
                start_date="2026-04-01",
                end_date="2026-04-02",
            )
            with patch("peap.download_runner.ensure_runtime_dependencies", return_value=True):
                prepared = prepare_download_session(
                    request,
                    logger=logger,
                    config_obj=config,
                    settings=settings,
                )

        self.assertEqual(len(prepared.tasks), 1)
        self.assertEqual(prepared.tasks[0].task_id, "sse:deal:deal_equity_transfer")
        self.assertTrue(prepared.tasks[0].implemented)

    def test_prepare_download_session_all_deal_physical_scope_uses_only_supported_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            settings = build_download_runner_settings(config)
            logger = SimpleNamespace(
                info=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            )
            request = DownloadRunRequest(
                exchange="all",
                record_family="deal",
                business_id="deal_physical_asset",
                output_root=tmp_dir,
                start_date="2026-04-01",
                end_date="2026-04-02",
            )
            with patch("peap.download_runner.ensure_runtime_dependencies", return_value=True):
                prepared = prepare_download_session(
                    request,
                    logger=logger,
                    config_obj=config,
                    settings=settings,
                )

        self.assertEqual(
            [task.task_id for task in prepared.tasks],
            ["sse:deal:deal_physical_asset", "cbex:deal:deal_physical_asset"],
        )
        self.assertTrue(all(task.implemented for task in prepared.tasks))

    def test_prepare_download_session_all_deal_scope_omits_unsupported_asset_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._build_config(output_root=tmp_dir)
            settings = build_download_runner_settings(config)
            logger = SimpleNamespace(
                info=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            )
            request = DownloadRunRequest(
                exchange="all",
                record_family="deal",
                business_id="all",
                output_root=tmp_dir,
                start_date="2026-04-01",
                end_date="2026-04-02",
            )
            with patch("peap.download_runner.ensure_runtime_dependencies", return_value=True):
                prepared = prepare_download_session(
                    request,
                    logger=logger,
                    config_obj=config,
                    settings=settings,
                )

        task_ids = {task.task_id for task in prepared.tasks}
        self.assertEqual(len(task_ids), 10)
        self.assertNotIn("public_resource:deal:deal_equity_transfer", task_ids)
        self.assertNotIn("tpre:deal:deal_physical_asset", task_ids)
        self.assertNotIn("cquae:deal:deal_physical_asset", task_ids)
        self.assertTrue(all(task.implemented for task in prepared.tasks))

    def test_downloaders_record_download_targets_only_through_common_reservation_contract(self) -> None:
        downloader_dir = Path(__file__).resolve().parents[1] / "peap" / "downloaders"
        offenders: list[str] = []
        missing_reservation: list[str] = []
        for path in sorted(downloader_dir.glob("*.py")):
            if path.name == "common.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "downloaded_this_run.add" in source:
                offenders.append(path.name)
            if "summary.saved += 1" in source and (
                "reserve_download_target(" not in source or "record_downloaded_target(" not in source
            ):
                missing_reservation.append(path.name)

        self.assertEqual(offenders, [])
        self.assertEqual(missing_reservation, [])

    def test_low_level_download_driver_is_only_used_behind_audited_entrypoints(self) -> None:
        root = Path(__file__).resolve().parents[1]
        allowed = {
            "peap/download_runtime.py",
            "peap/download_runner.py",
            "scripts/live_truth_audit.py",
        }
        offenders: list[str] = []
        for base in (root / "peap", root / "scripts", root / "desktop_backend"):
            for path in sorted(base.rglob("*.py")):
                relpath = path.relative_to(root).as_posix()
                if relpath in allowed:
                    continue
                source = path.read_text(encoding="utf-8")
                if "run_download_driver" in source:
                    offenders.append(relpath)

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
