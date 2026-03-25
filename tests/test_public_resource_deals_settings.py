from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from peap.public_resource_deals import (
    DEFAULT_INPUT_SUBDIR,
    DEFAULT_OUTPUT_FILENAME,
    OUTPUT_COLUMNS,
    PublicResourceDealSettings,
    build_public_resource_deal_settings,
    build_workbook,
    default_input_dir,
    default_output_file,
)


class PublicResourceDealSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(
            DATA_ROOT=os.path.join(self.temp_dir.name, "data_root"),
            OUTPUT_EXCEL_DIR=os.path.join(self.temp_dir.name, "excel"),
        )

    def test_build_public_resource_deal_settings_from_config(self) -> None:
        settings = build_public_resource_deal_settings(self.config)

        self.assertIsInstance(settings, PublicResourceDealSettings)
        self.assertEqual(
            settings.input_dir,
            os.path.join(self.config.DATA_ROOT, "raw", "manual", DEFAULT_INPUT_SUBDIR),
        )
        self.assertEqual(
            settings.output_file,
            os.path.join(self.config.OUTPUT_EXCEL_DIR, DEFAULT_OUTPUT_FILENAME),
        )

    def test_default_paths_use_explicit_settings(self) -> None:
        settings = PublicResourceDealSettings(
            input_dir=os.path.join(self.temp_dir.name, "input"),
            output_file=os.path.join(self.temp_dir.name, "output.xlsx"),
        )

        self.assertEqual(default_input_dir(settings), settings.input_dir)
        self.assertEqual(default_output_file(settings), settings.output_file)

    def test_default_paths_use_explicit_config(self) -> None:
        self.assertEqual(
            default_input_dir(config_obj=self.config),
            os.path.join(self.config.DATA_ROOT, "raw", "manual", DEFAULT_INPUT_SUBDIR),
        )
        self.assertEqual(
            default_output_file(config_obj=self.config),
            os.path.join(self.config.OUTPUT_EXCEL_DIR, DEFAULT_OUTPUT_FILENAME),
        )

    def test_build_workbook_uses_explicit_settings_defaults(self) -> None:
        input_dir = Path(self.temp_dir.name) / "raw" / "manual" / "public_resource"
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "sample.mhtml").write_text("placeholder", encoding="utf-8")
        output_file = Path(self.temp_dir.name) / "excel" / DEFAULT_OUTPUT_FILENAME
        settings = PublicResourceDealSettings(
            input_dir=str(input_dir),
            output_file=str(output_file),
        )
        normalized_row = {column_name: "" for column_name in OUTPUT_COLUMNS}
        normalized_row[OUTPUT_COLUMNS[0]] = "test-exchange"
        normalized_row[OUTPUT_COLUMNS[1]] = "PR001"
        normalized_row[OUTPUT_COLUMNS[2]] = "sample-project"
        normalized_row[OUTPUT_COLUMNS[-1]] = "2026/03/01"

        with (
            patch("peap.public_resource_deals.parse_mhtml_file", return_value={"raw": "value"}),
            patch("peap.public_resource_deals._normalize_output_row", return_value=normalized_row),
        ):
            summary = build_workbook(settings=settings)

        self.assertTrue(os.path.samefile(summary.input_dir, input_dir))
        self.assertTrue(os.path.samefile(summary.output_file, output_file))
        self.assertEqual(summary.total_files, 1)
        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.exchange_counts, {"test-exchange": 1})
        frame = pd.read_excel(output_file, dtype=str).fillna("")
        self.assertEqual(list(frame.columns), OUTPUT_COLUMNS)
        self.assertEqual(frame.at[0, OUTPUT_COLUMNS[1]], "PR001")


if __name__ == "__main__":
    unittest.main()
