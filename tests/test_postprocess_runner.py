from __future__ import annotations

import unittest
from types import SimpleNamespace

from peap_postprocess.postprocess_engine.contracts import ExecutionSummary
from peap_postprocess.postprocess_engine.runner import (
    PostProcessRunRequest,
    PostProcessRunResult,
    postprocess_result_to_summary_payload,
    run_postprocess_request,
)


class PostProcessRunnerTest(unittest.TestCase):
    def _build_logger(self) -> SimpleNamespace:
        return SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )

    def test_run_postprocess_request_uses_injected_dependencies(self) -> None:
        request = PostProcessRunRequest(
            config_path="C:\\temp\\postprocess.json",
            mode="apply",
            log_dir="C:\\temp\\logs",
            log_file="C:\\temp\\logs\\ppe.log",
            verbose=True,
            skip_unresolved_list=False,
        )
        fake_logger = self._build_logger()
        fake_config = SimpleNamespace(name="ppe-config")
        captured: dict[str, object] = {}
        summary = ExecutionSummary(
            run_id="20260319_100000",
            mode="apply",
            discovered_files=3,
            processed_files=2,
            processed_rows=10,
            applied_patches=4,
            findings=1,
            failed_files=0,
            output_files=["C:\\temp\\out.xlsx"],
            audit_report="C:\\temp\\audit.csv",
        )

        class FakeEngine:
            def run(self, config, *, mode_override=None):
                captured["engine_config"] = config
                captured["mode_override"] = mode_override
                return summary

        def config_loader(path: str):
            captured["config_path"] = path
            return fake_config

        def engine_factory(logger):
            captured["engine_logger"] = logger
            return FakeEngine()

        def export_unresolved_list(config_path: str, logger):
            captured["export_args"] = (config_path, logger)
            return 0, "C:\\temp\\unresolved.xlsx"

        def close_logger(logger) -> None:
            captured["closed_logger"] = logger

        result = run_postprocess_request(
            request,
            emit_console=False,
            config_loader=config_loader,
            engine_factory=engine_factory,
            export_unresolved_list=export_unresolved_list,
            logger_factory=lambda verbose, log_dir, log_file: (fake_logger, "C:\\temp\\logs\\ppe.log"),
            close_logger=close_logger,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.log_file, "C:\\temp\\logs\\ppe.log")
        self.assertEqual(result.summary["processed_rows"], 10)
        self.assertEqual(result.output_files, ["C:\\temp\\out.xlsx"])
        self.assertEqual(result.audit_report, "C:\\temp\\audit.csv")
        self.assertEqual(result.unresolved_output_file, "C:\\temp\\unresolved.xlsx")
        self.assertEqual(captured["config_path"], "C:\\temp\\postprocess.json")
        self.assertIs(captured["engine_config"], fake_config)
        self.assertEqual(captured["mode_override"], "apply")
        self.assertEqual(
            captured["export_args"],
            ("C:\\temp\\postprocess.json", fake_logger),
        )
        self.assertIs(captured["closed_logger"], fake_logger)

    def test_run_postprocess_request_returns_config_error_without_engine_run(self) -> None:
        fake_logger = self._build_logger()
        captured: dict[str, object] = {}

        def config_loader(path: str):
            raise ValueError(f"bad config: {path}")

        def engine_factory(logger):
            captured["engine_called"] = True
            raise AssertionError("engine should not be created")

        def export_unresolved_list(config_path: str, logger):
            captured["export_called"] = True
            return 0, ""

        def close_logger(logger) -> None:
            captured["closed_logger"] = logger

        result = run_postprocess_request(
            PostProcessRunRequest(config_path="C:\\temp\\missing.json", skip_unresolved_list=False),
            emit_console=False,
            config_loader=config_loader,
            engine_factory=engine_factory,
            export_unresolved_list=export_unresolved_list,
            logger_factory=lambda verbose, log_dir, log_file: (fake_logger, "ppe.log"),
            close_logger=close_logger,
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("bad config", result.errors[0])
        self.assertNotIn("engine_called", captured)
        self.assertNotIn("export_called", captured)
        self.assertIs(captured["closed_logger"], fake_logger)

    def test_run_postprocess_request_propagates_export_failure_to_exit_code(self) -> None:
        fake_logger = self._build_logger()
        summary = ExecutionSummary(
            run_id="20260319_100500",
            mode="plan",
            discovered_files=1,
            processed_files=1,
            processed_rows=2,
            applied_patches=0,
            findings=0,
            failed_files=0,
        )

        class FakeEngine:
            def run(self, config, *, mode_override=None):
                return summary

        result = run_postprocess_request(
            PostProcessRunRequest(config_path="C:\\temp\\postprocess.json", skip_unresolved_list=False),
            emit_console=False,
            config_loader=lambda path: SimpleNamespace(path=path),
            engine_factory=lambda logger: FakeEngine(),
            export_unresolved_list=lambda config_path, logger: (7, "C:\\temp\\unresolved.xlsx"),
            logger_factory=lambda verbose, log_dir, log_file: (fake_logger, "ppe.log"),
            close_logger=lambda logger: None,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.export_exit_code, 7)
        self.assertEqual(result.unresolved_output_file, "C:\\temp\\unresolved.xlsx")

    def test_postprocess_result_to_summary_payload_preserves_structured_fields(self) -> None:
        result = PostProcessRunResult(
            exit_code=1,
            log_file="C:\\temp\\logs\\ppe.log",
            summary={"processed_files": 2},
            output_files=["C:\\temp\\out.xlsx"],
            audit_report="C:\\temp\\audit.csv",
            unresolved_output_file="C:\\temp\\unresolved.xlsx",
            export_exit_code=7,
            errors=["boom"],
        )

        payload = postprocess_result_to_summary_payload(result)

        self.assertEqual(payload["kind"], "postprocess")
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["log_file"], "C:\\temp\\logs\\ppe.log")
        self.assertEqual(payload["summary"], {"processed_files": 2})
        self.assertEqual(payload["output_files"], ["C:\\temp\\out.xlsx"])
        self.assertEqual(payload["audit_report"], "C:\\temp\\audit.csv")
        self.assertEqual(payload["unresolved_output_file"], "C:\\temp\\unresolved.xlsx")
        self.assertEqual(payload["export_exit_code"], 7)
        self.assertEqual(payload["errors"], ["boom"])


if __name__ == "__main__":
    unittest.main()
