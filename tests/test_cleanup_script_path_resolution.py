"""Verify cleanup scripts pick up AppConfig defaults when CLI omits paths."""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from path_isolation import assert_paths_under_temp, isolated_peap_env

from desktop_backend.app_config import AppConfig
from scripts._paths import resolve_cleanup_paths
from scripts.cleanup_archive_conflicts import plan_archive_conflicts


def _fake_config(base: str) -> AppConfig:
    return AppConfig(
        APP_HOME=os.path.join(base, "PEAP"),
        PROJECT_ROOT=os.path.join(base, "repo"),
        DATA_ROOT=os.path.join(base, "PEAP", "data"),
        CACHE_DIR=os.path.join(base, "PEAP", "cache"),
        HTML_FOLDER=os.path.join(base, "PEAP", "manual"),
        AUTO_HTML_FOLDER=os.path.join(base, "PEAP", "archive"),
        LOG_DIR=os.path.join(base, "PEAP", "logs"),
        OUTPUT_EXCEL_DIR=os.path.join(base, "PEAP", "exports"),
        ARCHIVE_ROOT=os.path.join(base, "PEAP", "archive"),
        DOWNLOAD_CHUNK_STATE_DIR=os.path.join(base, "PEAP", "cache", "dl"),
        PLAYWRIGHT_BROWSERS_PATH=os.path.join(base, "PEAP", "cache", "pw"),
        STREAMING_DB_PATH=os.path.join(base, "PEAP", "data", "streaming_ingest.sqlite3"),
    )


def _assert_paths_under(testcase: unittest.TestCase, root: str, *paths: str) -> None:
    temp_root = os.path.abspath(root)
    for path_value in paths:
        testcase.assertEqual(os.path.commonpath([temp_root, os.path.abspath(path_value)]), temp_root)


class CleanupScriptPathResolutionTest(unittest.TestCase):
    REAL_PEAP_HOME = "/Users/rtoc/Documents/PEAP"

    def test_rejects_real_peap_workspace_default_paths(self) -> None:
        args = argparse.Namespace(archive_root=None, db=None, app_home=None)
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.REAL_PEAP_HOME,
                "PEAP_DATA_ROOT": os.path.join(self.REAL_PEAP_HOME, "data"),
                "PEAP_ARCHIVE_ROOT": os.path.join(self.REAL_PEAP_HOME, "archive"),
                "PEAP_EXPORT_ROOT": os.path.join(self.REAL_PEAP_HOME, "exports"),
                "PEAP_CACHE_DIR": os.path.join(self.REAL_PEAP_HOME, "cache"),
                "PEAP_STREAMING_DB_PATH": os.path.join(
                    self.REAL_PEAP_HOME,
                    "data",
                    "streaming_ingest.sqlite3",
                ),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "real PEAP workspace"):
                resolve_cleanup_paths(args, report_only=False)

    def test_rejects_real_peap_workspace_explicit_cli_paths(self) -> None:
        forbidden_paths = (
            ("archive_root", os.path.join(self.REAL_PEAP_HOME, "archive")),
            ("db", os.path.join(self.REAL_PEAP_HOME, "data", "streaming_ingest.sqlite3")),
            ("app_home", self.REAL_PEAP_HOME),
        )
        for attr, path_value in forbidden_paths:
            with self.subTest(attr=attr), tempfile.TemporaryDirectory() as tmp:
                args = argparse.Namespace(
                    archive_root=os.path.join(tmp, "archive"),
                    db=os.path.join(tmp, "db.sqlite3"),
                    app_home=os.path.join(tmp, "app"),
                )
                setattr(args, attr, path_value)
                with self.assertRaisesRegex(ValueError, "real PEAP workspace"):
                    resolve_cleanup_paths(args, report_only=False)

    def test_report_only_resolution_allows_real_workspace_defaults(self) -> None:
        args = argparse.Namespace(archive_root=None, db=None, app_home=None)
        with patch.dict(
            os.environ,
            {"PEAP_APP_HOME": self.REAL_PEAP_HOME},
            clear=True,
        ):
            resolve_cleanup_paths(args)

        self.assertEqual(args.app_home, self.REAL_PEAP_HOME)
        self.assertEqual(args.archive_root, os.path.join(self.REAL_PEAP_HOME, "archive"))

    def test_resolves_defaults_when_cli_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                archive_root=None,
                db=None,
                app_home=None,
            )
            resolve_cleanup_paths(args, config=_fake_config(tmp))
            self.assertEqual(args.archive_root, os.path.join(tmp, "PEAP", "archive"))
            self.assertEqual(args.db, os.path.join(tmp, "PEAP", "data", "streaming_ingest.sqlite3"))
            self.assertEqual(args.app_home, os.path.join(tmp, "PEAP"))
            _assert_paths_under(self, tmp, args.archive_root, args.db, args.app_home)

    def test_cli_value_wins_over_config_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit_archive = os.path.join(tmp, "explicit", "archive")
            args = argparse.Namespace(
                archive_root=explicit_archive,
                db=None,
                app_home=None,
            )
            resolve_cleanup_paths(args, config=_fake_config(tmp))
            self.assertEqual(args.archive_root, explicit_archive)
            self.assertEqual(args.db, os.path.join(tmp, "PEAP", "data", "streaming_ingest.sqlite3"))
            _assert_paths_under(self, tmp, args.archive_root, args.db)

    def test_skips_config_when_all_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                archive_root=os.path.join(tmp, "archive"),
                db=os.path.join(tmp, "streaming.sqlite3"),
                app_home=os.path.join(tmp, "app"),
            )
            with patch("scripts._paths.AppConfig.from_env") as mocked:
                resolve_cleanup_paths(args)
                mocked.assert_not_called()
            _assert_paths_under(self, tmp, args.archive_root, args.db, args.app_home)

    def test_default_resolution_uses_readonly_app_config_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(archive_root=None, db=None, app_home=None)
            with patch("scripts._paths.AppConfig.from_env", return_value=_fake_config(tmp)) as mocked:
                resolve_cleanup_paths(args)
            mocked.assert_called_once_with(ensure_dirs=False, migrate_legacy=False)
            _assert_paths_under(self, tmp, args.archive_root, args.db, args.app_home)

    def test_only_fills_attributes_present_on_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(db=None)
            resolve_cleanup_paths(args, config=_fake_config(tmp))
            self.assertEqual(args.db, os.path.join(tmp, "PEAP", "data", "streaming_ingest.sqlite3"))
            _assert_paths_under(self, tmp, args.db)
            self.assertFalse(hasattr(args, "archive_root"))
            self.assertFalse(hasattr(args, "app_home"))

    def test_treats_empty_string_as_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(archive_root="", db="", app_home="")
            resolve_cleanup_paths(args, config=_fake_config(tmp))
            self.assertEqual(args.archive_root, os.path.join(tmp, "PEAP", "archive"))
            self.assertEqual(args.db, os.path.join(tmp, "PEAP", "data", "streaming_ingest.sqlite3"))
            _assert_paths_under(self, tmp, args.archive_root, args.db, args.app_home)

    def test_path_resolution_has_no_filesystem_side_effects(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "FRESH_PEAP_WORKSPACE")
            self.assertFalse(os.path.exists(workspace))
            with patch.dict(
                os.environ,
                {
                    **isolated_peap_env(tmp, app_home=workspace),
                    "PEAP_WORKSPACE_ROOT": workspace,
                },
                clear=True,
            ):
                assert_paths_under_temp(
                    self,
                    tmp,
                    (
                        os.environ["PEAP_APP_HOME"],
                        os.environ["PEAP_DATA_ROOT"],
                        os.environ["PEAP_ARCHIVE_ROOT"],
                        os.environ["PEAP_EXPORT_ROOT"],
                        os.environ["PEAP_CACHE_DIR"],
                        os.environ["PEAP_STREAMING_DB_PATH"],
                    ),
                )
                args = argparse.Namespace(archive_root=None, db=None)
                resolve_cleanup_paths(args)
            self.assertEqual(args.archive_root, os.path.join(workspace, "archive"))
            self.assertEqual(args.db, os.path.join(workspace, "data", "streaming_ingest.sqlite3"))
            _assert_paths_under(self, tmp, args.archive_root, args.db)
            self.assertFalse(
                os.path.exists(workspace),
                "resolve_cleanup_paths must not create the workspace tree",
            )

    def test_archive_conflict_script_rejects_apply_and_legacy_copy_flags_without_deleting_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_root = os.path.join(tmp, "archive")
            os.makedirs(archive_root, exist_ok=True)
            canonical = os.path.join(archive_root, "project.html")
            conflict = os.path.join(archive_root, "project__conflict1.html")
            with open(canonical, "w", encoding="utf-8") as handle:
                handle.write("canonical")
            with open(conflict, "w", encoding="utf-8") as handle:
                handle.write("conflict")
            db_path = os.path.join(tmp, "streaming.sqlite3")
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE records (source_file TEXT, archive_path TEXT)")

            manifest = plan_archive_conflicts(archive_root)
            self.assertEqual(manifest["mode"], "report_only")
            self.assertEqual(manifest["conflict_group_count"], 1)
            self.assertIn("content_sha256", manifest["actions"][0]["group_paths"][0])

            legacy_external_copy_flag = "--" + "back" + "up-root"
            for flag in ("--apply", legacy_external_copy_flag):
                result = subprocess.run(
                    [
                        sys.executable,
                        os.path.join(os.getcwd(), "scripts", "cleanup_archive_conflicts.py"),
                        flag,
                        os.path.join(tmp, "legacy-copy") if flag == legacy_external_copy_flag else "",
                        "--archive-root",
                        archive_root,
                        "--db",
                        db_path,
                    ],
                    cwd=os.getcwd(),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(result.returncode, 0)

            self.assertTrue(os.path.exists(canonical))
            self.assertTrue(os.path.exists(conflict))

    def test_archive_conflict_plan_rejects_missing_archive_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_archive_root = os.path.join(tmp, "missing-archive")
            self.assertFalse(os.path.exists(missing_archive_root))

            with self.assertRaisesRegex(FileNotFoundError, "archive root not found"):
                plan_archive_conflicts(missing_archive_root)

    def test_archive_conflict_plan_rejects_archive_root_that_is_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_root_file = os.path.join(tmp, "archive-root-file")
            with open(archive_root_file, "w", encoding="utf-8") as handle:
                handle.write("not a directory")

            with self.assertRaisesRegex(NotADirectoryError, "archive root is not a directory"):
                plan_archive_conflicts(archive_root_file)

    def test_cleanup_scripts_do_not_expose_user_copy_contracts(self) -> None:
        repo_root = os.getcwd()
        script_paths = [
            os.path.join(repo_root, "scripts", "cleanup_duplicate_source_records.py"),
            os.path.join(repo_root, "scripts", "cleanup_missing_source_records.py"),
            os.path.join(repo_root, "scripts", "cleanup_sse_bad_snapshots.py"),
            os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"),
            os.path.join(repo_root, "scripts", "refetch_sse_from_cleanup_manifest.py"),
        ]
        for script_path in script_paths:
            result = subprocess.run(
                [sys.executable, script_path, "--help"],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            help_text = f"{result.stdout}\n{result.stderr}"
            self.assertNotIn("--" + "back" + "up-root", help_text)
            self.assertNotIn("<APP_HOME>/" + "back" + "ups", help_text)

    def test_cleanup_scripts_are_report_only_and_do_not_expose_apply(self) -> None:
        repo_root = os.getcwd()
        script_paths = [
            os.path.join(repo_root, "scripts", "cleanup_duplicate_source_records.py"),
            os.path.join(repo_root, "scripts", "cleanup_missing_source_records.py"),
            os.path.join(repo_root, "scripts", "cleanup_sse_bad_snapshots.py"),
            os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"),
            os.path.join(repo_root, "scripts", "refetch_sse_from_cleanup_manifest.py"),
        ]
        for script_path in script_paths:
            with self.subTest(script=script_path):
                help_result = subprocess.run(
                    [sys.executable, script_path, "--help"],
                    cwd=repo_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(help_result.returncode, 0, msg=help_result.stderr)
                self.assertNotIn("--apply", f"{help_result.stdout}\n{help_result.stderr}")

                apply_result = subprocess.run(
                    [sys.executable, script_path, "--apply", "--db", os.path.join(repo_root, "missing.sqlite3")],
                    cwd=repo_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(apply_result.returncode, 0)

    def test_report_only_recovery_planners_do_not_expose_ignored_runtime_flags(self) -> None:
        repo_root = os.getcwd()
        script_paths = (
            os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"),
            os.path.join(repo_root, "scripts", "refetch_sse_from_cleanup_manifest.py"),
        )
        for script_path in script_paths:
            with self.subTest(script=script_path):
                result = subprocess.run(
                    [sys.executable, script_path, "--help"],
                    cwd=repo_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                help_text = f"{result.stdout}\n{result.stderr}"
                self.assertNotIn("--concurrency", help_text)
                self.assertNotIn("--timeout", help_text)

    def test_report_only_cleanup_scripts_do_not_create_missing_db_files(self) -> None:
        repo_root = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            archive_root = os.path.join(tmp, "archive")
            app_home = os.path.join(tmp, "app-home")
            os.makedirs(archive_root, exist_ok=True)
            os.makedirs(app_home, exist_ok=True)
            script_args = {
                os.path.join(repo_root, "scripts", "cleanup_duplicate_source_records.py"): [],
                os.path.join(repo_root, "scripts", "cleanup_missing_source_records.py"): ["--app-home", app_home],
                os.path.join(repo_root, "scripts", "cleanup_sse_bad_snapshots.py"): [
                    "--archive-root",
                    archive_root,
                    "--app-home",
                    app_home,
                ],
                os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"): ["--archive-root", archive_root],
            }
            for script_path, extra_args in script_args.items():
                with self.subTest(script=script_path):
                    missing_db = os.path.join(
                        tmp,
                        f"{os.path.basename(script_path)}.missing.sqlite3",
                    )
                    self.assertFalse(os.path.exists(missing_db))
                    result = subprocess.run(
                        [
                            sys.executable,
                            script_path,
                            "--db",
                            missing_db,
                            *extra_args,
                        ],
                        cwd=repo_root,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(
                        os.path.exists(missing_db),
                        msg=f"{script_path} created {missing_db} on a report-only read path",
                    )
                    output = f"{result.stdout}\n{result.stderr}"
                    self.assertIn("database not found", output)


if __name__ == "__main__":
    unittest.main()
