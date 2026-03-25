from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from config import config as runtime_config
from peap.excel_handler import (
    ExcelSchemaSettings,
    build_excel_schema_settings,
    get_default_excel_schema_settings,
    get_excel_schema_status,
    get_output_schema_snapshot,
    load_excel_output_runtime,
    reload_excel_output_schema,
    save_to_excel,
    set_default_excel_schema_settings,
    validate_excel_output_schema,
)


class ExcelSchemaSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_default_settings = get_default_excel_schema_settings()
        self.addCleanup(
            lambda: set_default_excel_schema_settings(self.original_default_settings)
        )
        self.addCleanup(
            lambda: reload_excel_output_schema(build_excel_schema_settings(runtime_config))
        )

    def test_build_excel_schema_settings_from_config(self) -> None:
        settings = build_excel_schema_settings(runtime_config)

        self.assertIsInstance(settings, ExcelSchemaSettings)
        self.assertEqual(settings.schema_path, runtime_config.EXCEL_SCHEMA_FILE)

    def test_reload_excel_output_schema_uses_explicit_settings(self) -> None:
        schema_path = Path(self.temp_dir.name) / "schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "internal_keys": ["custom_internal_key"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        status = reload_excel_output_schema(
            ExcelSchemaSettings(schema_path=str(schema_path)),
        )
        snapshot = get_output_schema_snapshot()

        self.assertTrue(os.path.samefile(status["path"], schema_path))
        self.assertEqual(status["source"], "external")
        self.assertTrue(status["loaded"])
        self.assertIn("custom_internal_key", snapshot["internal_keys"])
        self.assertTrue(os.path.samefile(get_excel_schema_status()["path"], schema_path))

    def test_load_excel_output_runtime_keeps_runtime_isolated(self) -> None:
        schema_path = Path(self.temp_dir.name) / "runtime_schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "internal_keys": ["custom_internal_key"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        active_snapshot = get_output_schema_snapshot()
        runtime = load_excel_output_runtime(
            ExcelSchemaSettings(schema_path=str(schema_path)),
        )
        runtime_snapshot = get_output_schema_snapshot(runtime=runtime)
        runtime_status = get_excel_schema_status(runtime=runtime)

        self.assertEqual(validate_excel_output_schema(runtime=runtime), [])
        self.assertIn("custom_internal_key", runtime_snapshot["internal_keys"])
        self.assertNotIn("custom_internal_key", active_snapshot["internal_keys"])
        self.assertNotIn("custom_internal_key", get_output_schema_snapshot()["internal_keys"])
        self.assertTrue(os.path.samefile(runtime_status["path"], schema_path))

    def test_load_excel_output_runtime_uses_overridden_default_settings(self) -> None:
        schema_path = Path(self.temp_dir.name) / "default_schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "internal_keys": ["default_custom_key"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        set_default_excel_schema_settings(
            ExcelSchemaSettings(schema_path=str(schema_path)),
        )

        runtime = load_excel_output_runtime()
        runtime_snapshot = get_output_schema_snapshot(runtime=runtime)

        self.assertIn("default_custom_key", runtime_snapshot["internal_keys"])
        self.assertTrue(os.path.samefile(runtime.schema_status["path"], schema_path))

    def test_save_to_excel_uses_explicit_runtime(self) -> None:
        base_snapshot = get_output_schema_snapshot()
        base_columns = list(base_snapshot["output_columns"]["equity_transfer"])
        project_code_column = base_columns[2]
        project_name_column = base_columns[5]
        custom_column = "自定义列"
        schema_path = Path(self.temp_dir.name) / "writer_schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "output_columns": {
                        "equity_transfer": base_columns + [custom_column],
                    },
                    "field_candidates": {
                        "equity_transfer": {
                            custom_column: ["custom_field"],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        runtime = load_excel_output_runtime(
            ExcelSchemaSettings(schema_path=str(schema_path)),
        )
        target_file = Path(self.temp_dir.name) / "equity_transfer.xlsx"
        ok = save_to_excel(
            {
                project_code_column: "P001",
                project_name_column: "示例项目",
                "custom_field": "自定义值",
            },
            str(target_file),
            runtime=runtime,
        )

        self.assertTrue(ok)
        frame = pd.read_excel(target_file, dtype=str).fillna("")
        self.assertEqual(validate_excel_output_schema(runtime=runtime), [])
        self.assertEqual(frame.at[0, project_code_column], "P001")
        self.assertEqual(frame.at[0, custom_column], "自定义值")
        self.assertNotIn(custom_column, get_output_schema_snapshot()["output_columns"]["equity_transfer"])

    def test_reload_excel_output_schema_updates_default_writer_runtime(self) -> None:
        base_snapshot = get_output_schema_snapshot()
        base_columns = list(base_snapshot["output_columns"]["equity_transfer"])
        project_code_column = base_columns[2]
        project_name_column = base_columns[5]
        custom_column = "CUSTOM_COL"
        schema_path = Path(self.temp_dir.name) / "active_writer_schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "output_columns": {
                        "equity_transfer": base_columns + [custom_column],
                    },
                    "field_candidates": {
                        "equity_transfer": {
                            custom_column: ["custom_field"],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        reload_excel_output_schema(
            ExcelSchemaSettings(schema_path=str(schema_path)),
        )
        target_file = Path(self.temp_dir.name) / "active_equity_transfer.xlsx"
        ok = save_to_excel(
            {
                project_code_column: "P002",
                project_name_column: "sample-project",
                "custom_field": "custom-value",
            },
            str(target_file),
        )

        self.assertTrue(ok)
        frame = pd.read_excel(target_file, dtype=str).fillna("")
        self.assertEqual(frame.at[0, project_code_column], "P002")
        self.assertEqual(frame.at[0, custom_column], "custom-value")
        self.assertIn(custom_column, get_output_schema_snapshot()["output_columns"]["equity_transfer"])

    def test_get_output_schema_snapshot_returns_copies(self) -> None:
        snapshot = get_output_schema_snapshot()
        snapshot["internal_keys"].append("mutated_key")
        snapshot["output_columns"]["equity_transfer"].append("MUTATED_COL")
        snapshot["field_candidates"]["equity_transfer"]["MUTATED_COL"] = ["mutated_field"]

        fresh_snapshot = get_output_schema_snapshot()

        self.assertNotIn("mutated_key", fresh_snapshot["internal_keys"])
        self.assertNotIn("MUTATED_COL", fresh_snapshot["output_columns"]["equity_transfer"])
        self.assertNotIn(
            "MUTATED_COL",
            fresh_snapshot["field_candidates"]["equity_transfer"],
        )


if __name__ == "__main__":
    unittest.main()
