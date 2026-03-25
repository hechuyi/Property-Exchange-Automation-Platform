from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig


class AppConfigTest(unittest.TestCase):
    def test_from_env_builds_single_workspace_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            with patch.dict(
                os.environ,
                {
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
                },
                clear=False,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            self.assertEqual(config.APP_HOME, os.path.abspath(app_home))
            self.assertTrue(config.DATA_ROOT.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.CACHE_DIR.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.LOG_DIR.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.ARCHIVE_ROOT.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.OUTPUT_EXCEL_DIR.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.STREAMING_DB_PATH.startswith(os.path.abspath(app_home)))
            self.assertTrue(config.PLAYWRIGHT_BROWSERS_PATH.startswith(os.path.abspath(app_home)))
            self.assertEqual(config.HTML_FOLDER, os.path.join(os.path.abspath(app_home), "manual"))
            self.assertEqual(config.AUTO_HTML_FOLDER, config.ARCHIVE_ROOT)
            self.assertTrue(os.path.isdir(config.AUTO_HTML_FOLDER))
            self.assertTrue(os.path.isdir(config.HTML_FOLDER))
            self.assertTrue(os.path.isdir(config.LOG_DIR))
            self.assertTrue(os.path.isdir(config.DOWNLOAD_CHUNK_STATE_DIR))
            self.assertFalse(os.path.exists(os.path.join(app_home, "data", "raw")))

    def test_env_overrides_can_customize_subpaths_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            archive_root = os.path.join(temp_dir, "archive_root")
            export_root = os.path.join(temp_dir, "export_root")
            auto_html_root = os.path.join(temp_dir, "raw_auto")
            browser_cache_root = os.path.join(temp_dir, "browser_cache")
            with patch.dict(
                os.environ,
                {
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
                    "PEAP_ARCHIVE_ROOT": archive_root,
                    "PEAP_EXPORT_ROOT": export_root,
                    "PEAP_AUTO_HTML_ROOT": auto_html_root,
                    "PEAP_PLAYWRIGHT_BROWSERS_PATH": browser_cache_root,
                },
                clear=False,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            self.assertEqual(config.ARCHIVE_ROOT, os.path.abspath(archive_root))
            self.assertEqual(config.OUTPUT_EXCEL_DIR, os.path.abspath(export_root))
            self.assertEqual(config.AUTO_HTML_FOLDER, os.path.abspath(auto_html_root))
            self.assertEqual(config.PLAYWRIGHT_BROWSERS_PATH, os.path.abspath(browser_cache_root))

    def test_from_env_migrates_workspace_raw_layout_into_manual_and_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            legacy_manual_root = os.path.join(app_home, "data", "raw", "manual")
            legacy_auto_root = os.path.join(app_home, "data", "raw", "auto")
            os.makedirs(legacy_manual_root, exist_ok=True)
            os.makedirs(legacy_auto_root, exist_ok=True)
            legacy_manual_file = os.path.join(legacy_manual_root, "manual.html")
            legacy_auto_file = os.path.join(legacy_auto_root, "auto.html")
            with open(legacy_manual_file, "w", encoding="utf-8") as handle:
                handle.write("manual")
            with open(legacy_auto_file, "w", encoding="utf-8") as handle:
                handle.write("auto")

            with patch.dict(
                os.environ,
                {
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
                },
                clear=False,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            self.assertTrue(os.path.isfile(os.path.join(config.HTML_FOLDER, "manual.html")))
            self.assertTrue(os.path.isfile(os.path.join(config.ARCHIVE_ROOT, "auto.html")))
            self.assertFalse(os.path.exists(os.path.join(app_home, "data", "raw", "manual", "manual.html")))
            self.assertFalse(os.path.exists(os.path.join(app_home, "data", "raw", "auto", "auto.html")))


if __name__ == "__main__":
    unittest.main()
