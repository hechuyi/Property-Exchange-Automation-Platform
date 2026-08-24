from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_submission


class PrepareSubmissionConfigBoundaryTest(unittest.TestCase):
    def _runtime_payload(self, tmp_dir: str, **submission_defaults):
        data_root = os.path.join(tmp_dir, "data")
        os.makedirs(os.path.join(data_root, "raw"))
        return (
            os.path.join(tmp_dir, "runtime.json"),
            {
                "paths": {
                    "data_root": data_root,
                    "output_excel_dir": "outputs/excel",
                },
                "submission_defaults": submission_defaults,
            },
        )

    def test_unknown_mapping_source_is_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "scripts.prepare_submission.load_runtime_config",
                return_value=self._runtime_payload(tmp_dir, mapping_source="metadata_only"),
            ):
                with self.assertRaises(prepare_submission.SubmissionConfigurationError):
                    prepare_submission.load_config_from_runtime()

    def test_unparseable_filename_max_bytes_is_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "scripts.prepare_submission.load_runtime_config",
                return_value=self._runtime_payload(tmp_dir, filename_max_bytes="many"),
            ):
                with self.assertRaises(prepare_submission.SubmissionConfigurationError):
                    prepare_submission.load_config_from_runtime()

    def test_invalid_resume_bool_is_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "scripts.prepare_submission.load_runtime_config",
                return_value=self._runtime_payload(tmp_dir, resume="tru"),
            ):
                with self.assertRaises(prepare_submission.SubmissionConfigurationError):
                    prepare_submission.load_config_from_runtime()

    def test_invalid_prefer_auto_bool_is_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "scripts.prepare_submission.load_runtime_config",
                return_value=self._runtime_payload(tmp_dir, prefer_auto="manual"),
            ):
                with self.assertRaises(prepare_submission.SubmissionConfigurationError):
                    prepare_submission.load_config_from_runtime()

    def test_excel_only_missing_excel_dir_returns_configuration_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch(
                    "scripts.prepare_submission.load_runtime_config",
                    return_value=self._runtime_payload(tmp_dir, mapping_source="excel_only"),
                ),
                patch("scripts.prepare_submission.run_submission_prepare") as run_submission_prepare,
            ):
                exit_code = prepare_submission.main()

        self.assertEqual(exit_code, 2)
        run_submission_prepare.assert_not_called()

    def test_build_mapping_rejects_excel_only_missing_excel_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_base_dir = os.path.join(tmp_dir, "raw")
            os.makedirs(raw_base_dir)
            config = prepare_submission.SubmissionConfig(
                data_root=tmp_dir,
                raw_base_dir=raw_base_dir,
                output_dir=os.path.join(tmp_dir, "outputs"),
                output_excel_dir=os.path.join(tmp_dir, "missing-excel"),
                submission_dir=os.path.join(tmp_dir, "outputs", "submission"),
                log_dir=os.path.join(tmp_dir, "logs"),
                resume=True,
                prefer_auto=True,
                mapping_source="excel_only",
                filename_max_bytes=200,
            )

            with self.assertRaises(prepare_submission.SubmissionConfigurationError):
                prepare_submission.build_mapping(config)

    def test_runtime_config_uses_current_manual_archive_and_export_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = os.path.join(tmp_dir, "workspace")
            data_root = os.path.join(workspace_root, "data")
            manual_root = os.path.join(workspace_root, "manual")
            archive_root = os.path.join(workspace_root, "archive")
            os.makedirs(manual_root)
            os.makedirs(archive_root)
            with open(os.path.join(manual_root, "MANUAL-002.html"), "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            with open(os.path.join(archive_root, "AUTO-001.html"), "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            payload = {
                "paths": {
                    "data_root": data_root,
                    "html_folder": "../manual",
                    "auto_html_folder": "../archive",
                    "log_dir": "../logs",
                    "output_excel_dir": "../exports",
                    "submission_dir": "../exports/submission",
                },
                "submission_defaults": {
                    "resume": True,
                    "prefer_auto": True,
                    "mapping_source": "excel_then_metadata",
                    "filename_max_bytes": 200,
                },
            }
            with (
                patch.dict(os.environ, {"HOME": tmp_dir}, clear=True),
                patch(
                    "scripts.prepare_submission.load_runtime_config",
                    return_value=(os.path.join(tmp_dir, "runtime.json"), payload),
                ),
            ):
                config = prepare_submission.load_config_from_runtime()

            self.assertEqual(config.data_root, data_root)
            self.assertEqual(
                config.source_roots(),
                (
                    ("auto", os.path.join(workspace_root, "archive")),
                    ("manual", os.path.join(workspace_root, "manual")),
                ),
            )
            self.assertEqual(config.output_excel_dir, os.path.join(workspace_root, "exports"))
            self.assertEqual(
                config.submission_dir,
                os.path.join(workspace_root, "exports", "submission"),
            )
            pages, unresolved = prepare_submission.scan_source_pages(
                config.raw_base_dir,
                source_roots=config.source_roots(),
            )
            self.assertEqual(unresolved, [])
            self.assertEqual(
                {(page.exact_code, page.source_kind) for page in pages},
                {("AUTO-001", "auto"), ("MANUAL-002", "manual")},
            )


if __name__ == "__main__":
    unittest.main()
