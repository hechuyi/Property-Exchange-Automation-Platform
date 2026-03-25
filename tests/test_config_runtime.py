from __future__ import annotations

import os
import tempfile
import unittest

from config import Config
from peap.download_tasks import build_download_task_registry_settings, build_task_registry
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
            self.assertEqual(cfg.LOG_DIR, os.path.join(data_root, "logs"))
            self.assertEqual(cfg.HTML_FOLDER, os.path.join(data_root, "raw", "manual"))

    def test_reload_updates_paths_without_replacing_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path_a, data_root_a = self._write_runtime_config(tmp_dir, data_root_name="data_a")
            config_path_b, data_root_b = self._write_runtime_config(tmp_dir, data_root_name="data_b")
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path_a)

            same_cfg = cfg.reload(runtime_config_file=config_path_b)

            self.assertIs(cfg, same_cfg)
            self.assertEqual(cfg.DATA_ROOT, data_root_b)
            self.assertNotEqual(cfg.DATA_ROOT, data_root_a)
            self.assertEqual(cfg.LOG_DIR, os.path.join(data_root_b, "logs"))

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
                task_id="sse:physical_asset",
                page_size=77,
            )
            cfg = Config(project_root=self._repo_root(), runtime_config_file=config_path)

            settings = build_download_task_registry_settings(cfg)
            registry = build_task_registry(settings=settings)

            self.assertEqual(registry["sse:physical_asset"].default_page_size, 77)


if __name__ == "__main__":
    unittest.main()
