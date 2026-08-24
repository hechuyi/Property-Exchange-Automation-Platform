from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig


def _isolated_peap_env(temp_dir: str, **overrides: str) -> dict[str, str]:
    app_home = os.path.join(temp_dir, "workspace_root")
    env = {
        "HOME": os.path.join(temp_dir, "home"),
        "PEAP_APP_HOME": app_home,
        "PEAP_DATA_ROOT": os.path.join(app_home, "data"),
        "PEAP_ARCHIVE_ROOT": os.path.join(app_home, "archive"),
        "PEAP_EXPORT_ROOT": os.path.join(app_home, "exports"),
        "PEAP_CACHE_DIR": os.path.join(app_home, "cache"),
        "PEAP_STREAMING_DB_PATH": os.path.join(app_home, "data", "streaming_ingest.sqlite3"),
        "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
    }
    env.update(overrides)
    return env


def _assert_peap_paths_under(testcase: unittest.TestCase, config: AppConfig, temp_dir: str) -> None:
    temp_root = os.path.abspath(temp_dir)
    for path_value in (
        config.APP_HOME,
        config.DATA_ROOT,
        config.CACHE_DIR,
        config.ARCHIVE_ROOT,
        config.OUTPUT_EXCEL_DIR,
        config.STREAMING_DB_PATH,
    ):
        testcase.assertEqual(os.path.commonpath([temp_root, os.path.abspath(path_value)]), temp_root)


class AppConfigTest(unittest.TestCase):
    def test_from_env_builds_single_workspace_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            with patch.dict(
                os.environ,
                {
                    "HOME": os.path.join(temp_dir, "home"),
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
                },
                clear=True,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            self.assertEqual(config.APP_HOME, os.path.abspath(app_home))
            _assert_peap_paths_under(self, config, temp_dir)
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
                _isolated_peap_env(
                    temp_dir,
                    PEAP_APP_HOME=app_home,
                    PEAP_ARCHIVE_ROOT=archive_root,
                    PEAP_EXPORT_ROOT=export_root,
                    PEAP_AUTO_HTML_ROOT=auto_html_root,
                    PEAP_PLAYWRIGHT_BROWSERS_PATH=browser_cache_root,
                ),
                clear=True,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            _assert_peap_paths_under(self, config, temp_dir)
            self.assertEqual(config.ARCHIVE_ROOT, os.path.abspath(archive_root))
            self.assertEqual(config.OUTPUT_EXCEL_DIR, os.path.abspath(export_root))
            self.assertEqual(config.AUTO_HTML_FOLDER, os.path.abspath(auto_html_root))
            self.assertEqual(config.PLAYWRIGHT_BROWSERS_PATH, os.path.abspath(browser_cache_root))

    def test_path_overrides_expand_home_and_environment_variables_consistently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_home = os.path.join(temp_dir, "home")
            with patch.dict(
                os.environ,
                {
                    "HOME": fake_home,
                    "PEAP_APP_HOME": "$HOME/PEAP-workspace",
                    "PEAP_DATA_ROOT": "$HOME/PEAP-workspace/data",
                    "PEAP_ARCHIVE_ROOT": "$HOME/PEAP-workspace/archive",
                    "PEAP_EXPORT_ROOT": "$HOME/PEAP-workspace/exports",
                    "PEAP_STREAMING_DB_PATH": "$HOME/PEAP-workspace/data/streaming.sqlite3",
                },
                clear=True,
            ):
                config = AppConfig.from_env(
                    app_home="~/PEAP-workspace",
                    project_root="$HOME/project",
                    ensure_dirs=False,
                    migrate_legacy=False,
                )

            expected_home = os.path.join(fake_home, "PEAP-workspace")
            self.assertEqual(config.APP_HOME, expected_home)
            self.assertEqual(config.PROJECT_ROOT, os.path.join(fake_home, "project"))
            self.assertEqual(config.DATA_ROOT, os.path.join(expected_home, "data"))
            self.assertEqual(config.ARCHIVE_ROOT, os.path.join(expected_home, "archive"))
            self.assertEqual(config.STREAMING_DB_PATH, os.path.join(expected_home, "data", "streaming.sqlite3"))

    def test_from_env_migrates_workspace_raw_layout_into_manual_and_archive(self) -> None:
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
                    "HOME": os.path.join(temp_dir, "home"),
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": os.path.join(temp_dir, "legacy_documents"),
                },
                clear=True,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            _assert_peap_paths_under(self, config, temp_dir)
            self.assertTrue(os.path.isfile(os.path.join(config.HTML_FOLDER, "manual.html")))
            self.assertTrue(os.path.isfile(os.path.join(config.ARCHIVE_ROOT, "auto.html")))
            self.assertFalse(os.path.exists(os.path.join(app_home, "data", "raw", "manual", "manual.html")))
            self.assertFalse(os.path.exists(os.path.join(app_home, "data", "raw", "auto", "auto.html")))

    def test_from_env_rejects_symlinked_legacy_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            legacy_manual_root = os.path.join(app_home, "data", "raw", "manual")
            outside_root = os.path.join(temp_dir, "outside")
            os.makedirs(os.path.dirname(legacy_manual_root), exist_ok=True)
            os.makedirs(outside_root, exist_ok=True)
            os.symlink(outside_root, legacy_manual_root)

            with patch.dict(
                os.environ,
                {"HOME": os.path.join(temp_dir, "home"), "PEAP_APP_HOME": app_home},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "legacy migration refuses symlink"):
                    AppConfig.from_env(project_root=temp_dir)

    def test_from_env_rejects_dangling_symlinked_legacy_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            legacy_manual_root = os.path.join(app_home, "data", "raw", "manual")
            os.makedirs(os.path.dirname(legacy_manual_root), exist_ok=True)
            os.symlink(os.path.join(temp_dir, "missing-target"), legacy_manual_root)

            with patch.dict(
                os.environ,
                {"HOME": os.path.join(temp_dir, "home"), "PEAP_APP_HOME": app_home},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "legacy migration refuses symlink"):
                    AppConfig.from_env(project_root=temp_dir)

    def test_from_env_prefers_archive_root_and_merges_legacy_submission_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "workspace_root")
            legacy_documents_root = os.path.join(temp_dir, "legacy_documents")
            archive_root = os.path.join(legacy_documents_root, "archive")
            submission_root = os.path.join(legacy_documents_root, "submission")
            os.makedirs(archive_root, exist_ok=True)
            os.makedirs(submission_root, exist_ok=True)
            with open(os.path.join(archive_root, "archive.html"), "w", encoding="utf-8") as handle:
                handle.write("archive")
            with open(os.path.join(submission_root, "submission.html"), "w", encoding="utf-8") as handle:
                handle.write("submission")

            with patch.dict(
                os.environ,
                {
                    "HOME": os.path.join(temp_dir, "home"),
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": legacy_documents_root,
                },
                clear=True,
            ):
                config = AppConfig.from_env(project_root=temp_dir)

            _assert_peap_paths_under(self, config, temp_dir)
            self.assertEqual(config.ARCHIVE_ROOT, os.path.join(os.path.abspath(app_home), "archive"))
            self.assertTrue(os.path.isfile(os.path.join(config.ARCHIVE_ROOT, "archive.html")))
            self.assertTrue(os.path.isfile(os.path.join(config.ARCHIVE_ROOT, "submission.html")))
            self.assertFalse(os.path.exists(os.path.join(submission_root, "submission.html")))

    def test_temp_workspace_does_not_move_default_documents_archive(self) -> None:
        cases = (
            ("workspace_env", "PEAP_WORKSPACE_ROOT", False),
            ("app_home_env", "PEAP_APP_HOME", False),
            ("app_home_argument", "", True),
        )
        for case_name, env_name, pass_app_home in cases:
            with self.subTest(case_name), tempfile.TemporaryDirectory() as temp_dir:
                fake_home = os.path.join(temp_dir, "home")
                app_home = os.path.join(temp_dir, "isolated_workspace")
                documents_archive = os.path.join(fake_home, "Documents", "PEAP", "archive")
                os.makedirs(documents_archive, exist_ok=True)
                archived_file = os.path.join(documents_archive, "evidence.html")
                with open(archived_file, "w", encoding="utf-8") as handle:
                    handle.write("evidence")

                env = {"HOME": fake_home}
                if env_name:
                    env[env_name] = app_home
                with patch.dict(os.environ, env, clear=True):
                    kwargs = {"app_home": app_home} if pass_app_home else {}
                    config = AppConfig.from_env(project_root=temp_dir, **kwargs)

                self.assertEqual(config.ARCHIVE_ROOT, os.path.join(os.path.abspath(app_home), "archive"))
                self.assertTrue(os.path.isfile(archived_file))
                self.assertFalse(os.path.exists(os.path.join(config.ARCHIVE_ROOT, "evidence.html")))


if __name__ == "__main__":
    unittest.main()
