from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from peap_core import (
    load_runtime_config,
    normalize_path,
    read_summary_json,
    resolve_path,
    setup_cli_logger,
    write_json_file_atomic,
    write_summary_json,
)


class RuntimeHelpersTest(unittest.TestCase):
    def test_normalize_path_empty(self) -> None:
        self.assertEqual(normalize_path(""), "")

    def test_resolve_path_joins_base_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            resolved = resolve_path("data/file.json", base_dir=tmp_dir)
            self.assertEqual(resolved, os.path.join(tmp_dir, "data", "file.json"))

    def test_load_runtime_config_prefers_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = os.path.join(tmp_dir, "runtime.json")
            write_summary_json(
                config_path,
                {
                    "paths": {
                        "data_root": "../PEAP_DATA",
                    }
                },
            )
            original = os.environ.get("PEAP_RUNTIME_CONFIG_FILE")
            os.environ["PEAP_RUNTIME_CONFIG_FILE"] = config_path
            try:
                resolved_path, payload = load_runtime_config(tmp_dir)
            finally:
                if original is None:
                    os.environ.pop("PEAP_RUNTIME_CONFIG_FILE", None)
                else:
                    os.environ["PEAP_RUNTIME_CONFIG_FILE"] = original

            self.assertEqual(resolved_path, config_path)
            self.assertEqual(payload["paths"]["data_root"], "../PEAP_DATA")

    def test_load_runtime_config_missing_file_mentions_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(tmp_dir)

        self.assertIn("runtime_config.template.json", str(ctx.exception))


class CliSupportTest(unittest.TestCase):
    def test_write_json_file_atomic_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = os.path.join(tmp_dir, "atomic.json")
            payload = {"kind": "atomic-test", "count": 2}
            write_json_file_atomic(summary_path, payload)
            self.assertEqual(read_summary_json(summary_path), payload)

    def test_summary_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = os.path.join(tmp_dir, "summary.json")
            payload = {"kind": "unit-test", "count": 3}
            write_summary_json(summary_path, payload)
            self.assertEqual(read_summary_json(summary_path), payload)

    def test_setup_cli_logger_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger, log_file = setup_cli_logger(
                name="unit_test_logger",
                verbose=False,
                log_dir=tmp_dir,
                log_file=None,
                default_log_dir=tmp_dir,
                file_prefix="unit",
            )
            logger.info("hello")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            self.assertTrue(os.path.isfile(log_file))

    def test_setup_cli_logger_respects_base_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger, log_file = setup_cli_logger(
                name="unit_test_logger_level",
                verbose=False,
                log_dir=tmp_dir,
                log_file=None,
                default_log_dir=tmp_dir,
                file_prefix="unit",
                base_level="WARNING",
            )
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            self.assertEqual(logger.level, logging.WARNING)
            self.assertTrue(os.path.isfile(log_file))

    def test_setup_cli_logger_can_disable_file_logging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger, log_file = setup_cli_logger(
                name="unit_test_logger_console_only",
                verbose=False,
                log_dir=tmp_dir,
                log_file=None,
                default_log_dir=tmp_dir,
                file_prefix="unit",
                enable_file_logging=False,
            )
            logger.info("hello")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            self.assertEqual(log_file, "")
            self.assertEqual(os.listdir(tmp_dir), [])

    def test_setup_cli_logger_falls_back_to_console_when_file_handler_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("peap_core.cli_support.logging.FileHandler", side_effect=PermissionError("blocked")):
                logger, log_file = setup_cli_logger(
                    name="unit_test_logger_fallback",
                    verbose=False,
                    log_dir=tmp_dir,
                    log_file=None,
                    default_log_dir=tmp_dir,
                    file_prefix="unit",
                )
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            self.assertEqual(log_file, "")
            self.assertEqual(logger.level, logging.INFO)


if __name__ == "__main__":
    unittest.main()
