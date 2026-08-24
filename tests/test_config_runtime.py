# ruff: noqa: E402
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config as config_module
from config import Config
from desktop_backend.app_config import (
    DEFAULT_DOWNLOADER_DEFAULTS,
    DEFAULT_DOWNLOADER_TASK_PAGE_SIZE,
)
from peap.download_task_defaults import DEFAULT_DOWNLOAD_TASK_PAGE_SIZE
from peap.download_tasks import (
    build_download_task_registry_settings,
    build_task_registry,
)
from peap_core.runtime import load_json_object, write_json_file


class ConfigRuntimeTest(unittest.TestCase):
    def _repo_root(self) -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _runtime_template_path(self) -> str:
        return os.path.join(self._repo_root(), "assets", "runtime_config.template.json")

    def _write_runtime_config(self, tmp_dir: str, *, data_root_name: str) -> tuple[str, str]:
        payload = load_json_object(
            self._runtime_template_path(),
            encoding="utf-8-sig",
            label="runtime config",
        )
        payload["paths"] = dict(payload["paths"])
        data_root = os.path.join(tmp_dir, data_root_name)
        payload["paths"]["data_root"] = data_root
        config_path = os.path.join(tmp_dir, f"runtime_{data_root_name}.json")
        write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)
        return config_path, data_root

    def _write_runtime_config_payload(
        self,
        tmp_dir: str,
        *,
        data_root_name: str,
        payload: dict,
        filename: str,
    ) -> tuple[str, str]:
        payload["paths"] = dict(payload["paths"])
        data_root = os.path.join(tmp_dir, data_root_name)
        payload["paths"]["data_root"] = data_root
        config_path = os.path.join(tmp_dir, filename)
        write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)
        return config_path, data_root

    def _write_runtime_config_with_page_size(
        self,
        tmp_dir: str,
        *,
        data_root_name: str,
        task_id: str,
        page_size: int,
    ) -> str:
        payload = load_json_object(
            self._runtime_template_path(),
            encoding="utf-8-sig",
            label="runtime config",
        )
        payload["paths"] = dict(payload["paths"])
        payload["paths"]["data_root"] = os.path.join(tmp_dir, data_root_name)
        payload["downloader_task_page_size"] = dict(payload["downloader_task_page_size"])
        payload["downloader_task_page_size"][task_id] = page_size
        config_path = os.path.join(tmp_dir, f"runtime_{data_root_name}_{task_id}.json")
        write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)
        return config_path

    def test_explicit_runtime_file_builds_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, data_root = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

            self.assertEqual(cfg.RUNTIME_CONFIG_FILE, config_path)
            self.assertEqual(cfg.DATA_ROOT, data_root)
            workspace_root = os.path.dirname(data_root)
            self.assertEqual(cfg.LOG_DIR, os.path.join(workspace_root, "logs"))
            self.assertEqual(cfg.HTML_FOLDER, os.path.join(workspace_root, "manual"))

    def test_default_runtime_paths_follow_desktop_workspace_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = os.path.join(tmp_dir, "workspace")
            with patch.dict(
                os.environ,
                {"HOME": tmp_dir, "PEAP_WORKSPACE_ROOT": workspace_root},
                clear=True,
            ):
                cfg = Config(project_root=self._repo_root())

            self.assertEqual(cfg.DATA_ROOT, os.path.join(workspace_root, "data"))
            self.assertEqual(cfg.HTML_FOLDER, os.path.join(workspace_root, "manual"))
            self.assertEqual(cfg.AUTO_HTML_FOLDER, os.path.join(workspace_root, "archive"))
            self.assertEqual(cfg.LOG_DIR, os.path.join(workspace_root, "logs"))
            self.assertEqual(cfg.OUTPUT_EXCEL_DIR, os.path.join(workspace_root, "exports"))

    def test_runtime_config_publishes_streaming_db_path_for_download_artifact_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, data_root = self._write_runtime_config(tmp_dir, data_root_name="data_audit")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertEqual(
            cfg.STREAMING_DB_PATH,
            os.path.join(data_root, "streaming_ingest.sqlite3"),
        )

    def test_legacy_runtime_config_inherits_streaming_db_path_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["paths"] = dict(payload["paths"])
            payload["paths"].pop("streaming_db_path", None)
            config_path, data_root = self._write_runtime_config_payload(
                tmp_dir,
                data_root_name="legacy_audit",
                payload=payload,
                filename="legacy_audit_runtime.json",
            )

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertEqual(
            cfg.STREAMING_DB_PATH,
            os.path.join(data_root, "streaming_ingest.sqlite3"),
        )

    def test_output_files_follow_runtime_file_name_contract_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["output_file_names"] = dict(payload["output_file_names"])
            payload["output_file_names"]["new_listing_business"] = "listing_new_business.xlsx"
            config_path, data_root = self._write_runtime_config_payload(
                tmp_dir,
                data_root_name="output_contract_data",
                payload=payload,
                filename="output_contract_runtime.json",
            )

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertEqual(
            cfg.OUTPUT_FILES["new_listing_business"],
            os.path.join(os.path.dirname(data_root), "exports", "listing_new_business.xlsx"),
        )

    def test_deal_files_follow_runtime_file_name_contract_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["deal_file_names"] = dict(payload["deal_file_names"])
            payload["deal_file_names"]["new_deal_business"] = "deal_new_business.xlsx"
            config_path, data_root = self._write_runtime_config_payload(
                tmp_dir,
                data_root_name="deal_contract_data",
                payload=payload,
                filename="deal_contract_runtime.json",
            )

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertEqual(
            cfg.DEAL_FILES["new_deal_business"],
            os.path.join(os.path.dirname(data_root), "exports", "deal_new_business.xlsx"),
        )

    def test_config_module_does_not_keep_independent_output_file_path_truth(self) -> None:
        build_settings_source = textwrap.dedent(inspect.getsource(config_module.Config._build_settings))
        tree = ast.parse(build_settings_source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            assigned_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if not assigned_names.intersection({"output_files", "deal_files"}):
                continue

            with self.subTest(assigned_names=assigned_names):
                self.assertNotIsInstance(node.value, ast.Dict)

    def test_reload_updates_paths_without_replacing_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path_a, data_root_a = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            config_path_b, data_root_b = self._write_runtime_config(tmp_dir, data_root_name="data_b")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path_a)

            same_cfg = cfg.reload(runtime_config_file=config_path_b)

            self.assertIs(cfg, same_cfg)
            self.assertEqual(cfg.DATA_ROOT, data_root_b)
            self.assertNotEqual(cfg.DATA_ROOT, data_root_a)
            self.assertEqual(cfg.LOG_DIR, os.path.join(os.path.dirname(data_root_b), "logs"))

    def test_failed_reload_keeps_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, data_root = self._write_runtime_config(tmp_dir, data_root_name="stable_data")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)
            invalid_config_path = os.path.join(tmp_dir, "invalid_runtime.json")
            write_json_file(invalid_config_path, {"paths": {}}, encoding="utf-8", ensure_ascii=False)

            with self.assertRaises(ValueError):
                cfg.reload(runtime_config_file=invalid_config_path)

            self.assertEqual(cfg.DATA_ROOT, data_root)
            self.assertEqual(cfg.RUNTIME_CONFIG_FILE, config_path)

    def test_download_task_registry_uses_runtime_config_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = self._write_runtime_config_with_page_size(
                tmp_dir,
                data_root_name="registry_data",
                task_id="sse:listing:physical_asset",
                page_size=77,
            )
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

            settings = build_download_task_registry_settings(cfg)
            registry = build_task_registry(settings=settings)

            self.assertEqual(registry["sse:listing:physical_asset"].default_page_size, 77)

    def test_downloader_defaults_publish_business_id_instead_of_project_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, _ = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

            self.assertIn("business_id", cfg.DOWNLOADER_DEFAULTS)
            self.assertEqual(cfg.DOWNLOADER_DEFAULTS["business_id"], "physical_asset")
            self.assertNotIn("project_type", cfg.DOWNLOADER_DEFAULTS)
            self.assertEqual(DEFAULT_DOWNLOADER_DEFAULTS["business_id"], "all")
            self.assertNotIn("project_type", DEFAULT_DOWNLOADER_DEFAULTS)

    def test_default_page_size_catalog_does_not_publish_unsupported_deal_asset_tasks(self) -> None:
        unsupported_task_ids = {
            "tpre:deal:deal_physical_asset",
            "cquae:deal:deal_physical_asset",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, _ = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertTrue(unsupported_task_ids.isdisjoint(DEFAULT_DOWNLOADER_TASK_PAGE_SIZE))
        self.assertTrue(unsupported_task_ids.isdisjoint(cfg.DOWNLOADER_TASK_PAGE_SIZE))

    def test_legacy_runtime_config_inherits_new_listing_page_size_defaults(self) -> None:
        new_listing_task_ids = {
            "shandong:listing:equity_transfer",
            "shandong:listing:capital_increase",
            "guangdong:listing:equity_transfer",
            "guangdong:listing:capital_increase",
            "shenzhen:listing:equity_transfer",
            "shenzhen:listing:capital_increase",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["paths"] = dict(payload["paths"])
            payload["paths"]["data_root"] = os.path.join(tmp_dir, "legacy_data")
            payload["downloader_task_page_size"] = {
                task_id: page_size
                for task_id, page_size in payload["downloader_task_page_size"].items()
                if task_id not in new_listing_task_ids
            }
            config_path = os.path.join(tmp_dir, "legacy_runtime.json")
            write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        for task_id in new_listing_task_ids:
            with self.subTest(task_id=task_id):
                self.assertEqual(
                    cfg.DOWNLOADER_TASK_PAGE_SIZE[task_id],
                    DEFAULT_DOWNLOADER_TASK_PAGE_SIZE[task_id],
                )

    def test_config_module_does_not_keep_independent_download_task_truth(self) -> None:
        config_source = inspect.getsource(config_module)

        self.assertNotIn('"sse:deal:deal_physical_asset"', config_source)
        self.assertNotIn('"shandong:listing:equity_transfer"', config_source)
        self.assertNotIn("deal_task_fallbacks = {", config_source)

    def test_config_module_does_not_keep_independent_downloader_default_scope_truth(self) -> None:
        config_source = inspect.getsource(config_module)

        self.assertNotIn('"cbex",\n            "sse",\n            "tpre"', config_source)
        self.assertNotIn('"deal_equity_transfer",\n            "deal_capital_increase"', config_source)
        self.assertNotIn(
            '"cbex, sse, tpre, cquae, shandong, guangdong, shenzhen, all"',
            config_source,
        )
        self.assertNotIn(
            '"deal_physical_asset, deal_equity_transfer, deal_capital_increase, all"',
            config_source,
        )

    def test_downloader_defaults_accept_catalog_aliases_and_publish_canonical_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["paths"] = dict(payload["paths"])
            payload["paths"]["data_root"] = os.path.join(tmp_dir, "catalog_alias_data")
            payload["downloader_defaults"] = dict(payload["downloader_defaults"])
            payload["downloader_defaults"]["exchange"] = "guangzhou"
            payload["downloader_defaults"]["business_id"] = "成交股权转让"
            config_path = os.path.join(tmp_dir, "catalog_alias_runtime.json")
            write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertEqual(cfg.DOWNLOADER_DEFAULTS["exchange"], "guangdong")
        self.assertEqual(cfg.DOWNLOADER_DEFAULTS["business_id"], "deal_equity_transfer")

    def test_legacy_listing_only_runtime_config_inherits_all_download_task_defaults(self) -> None:
        listing_only_task_ids = {
            "sse:listing:physical_asset",
            "cbex:listing:physical_asset",
            "sse:listing:equity_transfer",
            "sse:listing:capital_increase",
            "sse:listing:pre_disclosure",
            "cbex:listing:equity_transfer",
            "cbex:listing:capital_increase",
            "cbex:listing:pre_disclosure",
            "tpre:listing:physical_asset",
            "tpre:listing:equity_transfer",
            "tpre:listing:capital_increase",
            "tpre:listing:pre_disclosure",
            "cquae:listing:physical_asset",
            "cquae:listing:equity_transfer",
            "cquae:listing:capital_increase",
            "cquae:listing:pre_disclosure",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["paths"] = dict(payload["paths"])
            payload["paths"]["data_root"] = os.path.join(tmp_dir, "legacy_listing_only_data")
            payload["downloader_task_page_size"] = {
                task_id: page_size
                for task_id, page_size in payload["downloader_task_page_size"].items()
                if task_id in listing_only_task_ids
            }
            config_path = os.path.join(tmp_dir, "legacy_listing_only_runtime.json")
            write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)
            settings = build_download_task_registry_settings(cfg)
            registry = build_task_registry(settings=settings)

        self.assertEqual(cfg.DOWNLOADER_TASK_PAGE_SIZE, DEFAULT_DOWNLOAD_TASK_PAGE_SIZE)
        self.assertEqual(
            registry["sse:deal:deal_physical_asset"].default_page_size,
            DEFAULT_DOWNLOAD_TASK_PAGE_SIZE["sse:deal:deal_physical_asset"],
        )
        self.assertEqual(
            registry["shandong:listing:equity_transfer"].default_page_size,
            DEFAULT_DOWNLOAD_TASK_PAGE_SIZE["shandong:listing:equity_transfer"],
        )

    def test_runtime_config_uses_shared_download_task_defaults_not_desktop_backend(self) -> None:
        config_source = inspect.getsource(config_module)
        self.assertNotIn("desktop_backend.app_config", config_source)
        self.assertEqual(DEFAULT_DOWNLOADER_TASK_PAGE_SIZE, DEFAULT_DOWNLOAD_TASK_PAGE_SIZE)

    def test_runtime_config_publishes_guangdong_as_canonical_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path, _ = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertIn("guangdong", cfg.SUPPORTED_EXCHANGES)
        self.assertNotIn("guangzhou", cfg.SUPPORTED_EXCHANGES)
        self.assertIn("guangdong", cfg.EXCHANGE_NAMES)
        self.assertNotIn("guangzhou", cfg.EXCHANGE_NAMES)

    def test_legacy_runtime_config_guangzhou_exchange_alias_loads_as_guangdong(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = load_json_object(
                self._runtime_template_path(),
                encoding="utf-8-sig",
                label="runtime config",
            )
            payload["paths"] = dict(payload["paths"])
            payload["paths"]["data_root"] = os.path.join(tmp_dir, "legacy_guangzhou_data")
            payload["supported_exchanges"] = [
                "shenzhen",
                "beijing",
                "shanghai",
                "chongqing",
                "tianjin",
                "shandong",
                "guangzhou",
            ]
            payload["exchange_names"] = dict(payload["exchange_names"])
            payload["exchange_names"].pop("guangdong", None)
            payload["exchange_names"]["guangzhou"] = "广交所"
            payload["downloader_defaults"] = dict(payload["downloader_defaults"])
            payload["downloader_defaults"]["exchange"] = "guangzhou"
            config_path = os.path.join(tmp_dir, "legacy_guangzhou_runtime.json")
            write_json_file(config_path, payload, encoding="utf-8", ensure_ascii=False)

            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

        self.assertIn("guangdong", cfg.SUPPORTED_EXCHANGES)
        self.assertNotIn("guangzhou", cfg.SUPPORTED_EXCHANGES)
        self.assertEqual(cfg.EXCHANGE_NAMES.get("guangdong"), "广交所")
        self.assertNotIn("guangzhou", cfg.EXCHANGE_NAMES)
        self.assertEqual(cfg.DOWNLOADER_DEFAULTS["exchange"], "guangdong")


if __name__ == "__main__":
    unittest.main()
