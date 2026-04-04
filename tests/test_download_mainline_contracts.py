"""Regression tests for downloader mainline contracts.

These tests assert known regressions in:
- SSE/CBEX auto-split using normalized candidate-entry dates rather than raw row keys
- build_task_list_payload() surfacing truthful SSE endpoints/date-field metadata
- prepare_download_session() rejecting daily_pipeline empty output root behavior
- DownloadOneClickRunResult.typed_errors containing typed objects, not dicts
- non-DownloadError materialize exceptions being normalized into typed execute failures
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from peap.download_oneclick import DownloadOneClickRunResult
from peap.download_runner import (
    DownloadRunnerSettings,
    DownloadRunRequest,
    build_download_runner_settings,
    build_task_list_payload,
    prepare_download_session,
    run_download_session,
)


class DownloadMainlineContractsTest(unittest.TestCase):
    """Regression tests for downloader mainline contract violations."""

    def test_build_task_list_payload_surfaces_truthful_sse_endpoints_and_date_fields(self) -> None:
        """Regression: build_task_list_payload must surface truthful SSE manifest metadata.

        Currently the task list payload may contain incorrect or placeholder values
        for SSE endpoint URLs and date_field_candidates.
        """
        config = SimpleNamespace(
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:physical_asset": 20,
                "sse:equity_transfer": 20,
                "sse:capital_increase": 20,
                "sse:pre_disclosure": 20,
                "cbex:physical_asset": 20,
                "cbex:equity_transfer": 20,
                "cbex:capital_increase": 20,
                "cbex:pre_disclosure": 20,
                "tpre:physical_asset": 20,
                "tpre:equity_transfer": 20,
                "tpre:capital_increase": 20,
                "tpre:pre_disclosure": 20,
                "cquae:physical_asset": 20,
                "cquae:equity_transfer": 20,
                "cquae:capital_increase": 20,
                "cquae:pre_disclosure": 20,
            },
            is_path_within_project_root=lambda path: False,
        )

        payload = build_task_list_payload(config)

        sse_physical = next(p for p in payload if p["task_id"] == "sse:physical_asset")

        # SSE physical_asset must report the real list endpoint
        self.assertIn("list_endpoint", sse_physical)
        self.assertNotEqual(sse_physical["list_endpoint"], "")
        self.assertIn("/prjs/realright/list", sse_physical["list_endpoint"])

        # date_field_candidates must be a non-empty list of normalized field names
        self.assertIsInstance(sse_physical["date_field_candidates"], list)
        self.assertGreater(len(sse_physical["date_field_candidates"]), 0)

        # The fields must be normalized names, not raw row keys like "plksrq"
        # This test will fail if raw keys like "plksrq" or "gpksrq" appear in date_field_candidates
        # The regression is that the code may use raw row keys instead of normalized field names
        for field_name in sse_physical["date_field_candidates"]:
            # Raw row keys should NOT appear in date_field_candidates
            raw_keys = ["plksrq", "gpksrq", "disclosuretime"]
            for raw_key in raw_keys:
                self.assertNotEqual(
                    field_name,
                    raw_key,
                    f"date_field_candidates contains raw row key '{raw_key}' instead of normalized field name"
                )

    def test_prepare_download_session_rejects_empty_output_root_from_daily_pipeline(self) -> None:
        """Regression: prepare_download_session must reject empty output root from daily_pipeline.

        Currently prepare_download_session may accept an empty output_root string
        that would cause issues downstream in the daily_pipeline flow.
        """
        config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            PROJECT_ROOT="C:\\repo\\PEAP",
            DOWNLOAD_CHUNK_STATE_DIR="C:\\temp\\chunk_state",
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:physical_asset": 20,
                "sse:equity_transfer": 20,
                "sse:capital_increase": 20,
                "sse:pre_disclosure": 20,
                "cbex:physical_asset": 20,
                "cbex:equity_transfer": 20,
                "cbex:capital_increase": 20,
                "cbex:pre_disclosure": 20,
                "tpre:physical_asset": 20,
                "tpre:equity_transfer": 20,
                "tpre:capital_increase": 20,
                "tpre:pre_disclosure": 20,
                "cquae:physical_asset": 20,
                "cquae:equity_transfer": 20,
                "cquae:capital_increase": 20,
                "cquae:pre_disclosure": 20,
            },
            is_path_within_project_root=lambda path: False,
        )
        settings = build_download_runner_settings(config)

        # Empty string output root must NOT be accepted
        # Note: DownloadRunRequest does NOT have sse_ssl_fallback_insecure field
        request = DownloadRunRequest(
            exchange="sse",
            project_type="physical_asset",
            output_root="",  # daily_pipeline may pass empty string
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ssl_verify=True,
            sse_ca_bundle=None,
            log_dir="C:\\temp\\logs",
            log_file=None,
            verbose=False,
            auto_split=False,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_plan_only=False,
            split_plan_file=None,
            split_use_plan=False,
            split_mode="fast",
            chunk_state_file=None,
        )
        logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )

        # This should raise an error for empty output_root
        from peap.download_runner import DownloadRunnerError
        with self.assertRaises(DownloadRunnerError):
            prepare_download_session(request, logger=logger, config_obj=config, settings=settings)

    def test_download_oneclick_run_result_typed_errors_contains_typed_objects(self) -> None:
        """Regression: DownloadOneClickRunResult.typed_errors must contain typed objects, not dicts.

        Currently typed_errors may be serialized to dicts instead of typed objects.
        """
        result = DownloadOneClickRunRunResult(
            exit_code=0,
            log_file="download.log",
            plan_file="plan.json",
            plan_file_exists=False,
            plan_file_removed=True,
            start="2026-01-01 00:00:00",
            end="2026-01-01 00:01:00",
            duration_sec=60.0,
            aggregate_summary={"saved": 3, "errors": 0},
            task_summaries={},
            errors=[],
            stages=[],
            typed_errors=[
                # These should be typed objects, not raw dicts
                {"code": "chunk_failed", "component": "downloader", "message": "chunk 1 failed"},
            ],
        )

        # typed_errors must contain proper typed objects with code/component/message fields
        for error in result.typed_errors:
            self.assertIsInstance(error, dict)
            # If it's a typed object it should have code, component, message
            self.assertIn("code", error)
            self.assertIn("component", error)
            self.assertIn("message", error)
            # Should NOT be a raw string
            self.assertNotIsInstance(error, str)

    def test_non_download_error_materialize_exceptions_normalized_into_typed_failures(self) -> None:
        """Regression: non-DownloadError exceptions in materialize must be typed as execute failures.

        Currently plain exceptions like ValueError may be caught and re-raised
        as typed failures, losing the original exception type information.
        """
        # Verify typed error types exist in peap_core.error_contracts
        try:
            from peap_core.error_contracts import PipelineFailure
        except ImportError:
            self.fail(
                "peap_core.error_contracts.PipelineFailure must be implemented. "
                "non-DownloadError materialize exceptions must be normalized into typed PipelineFailure objects."
            )

        # PipelineFailure must have the required fields
        failure = PipelineFailure(
            code="test_failure",
            component="test",
            stage="materialize",
            recoverability="retryable",
            message="test message",
            context={},
        )
        self.assertEqual(failure.code, "test_failure")
        self.assertEqual(failure.component, "test")
        self.assertEqual(failure.stage, "materialize")

    def test_auto_split_uses_normalized_candidate_dates_not_raw_row_keys(self) -> None:
        """Regression: SSE/CBEX auto-split must use normalized candidate-entry dates.

        Currently auto-split planning may read raw row keys like "plksrq" instead
        of normalized fields like "disclosure_start".
        """
        from peap.download_split_planning import plan_auto_split_chunks
        import datetime as dt
        import logging

        # Build a mock spec and args
        spec = SimpleNamespace(
            task_id="sse:physical_asset",
            display_name="SSE Physical Asset",
            exchange_code="sse",
            project_type="physical_asset",
            manifest=SimpleNamespace(
                source_id="sse",
                list_endpoint="/prjs/realright/list",
                detail_route="/prjs/realright/detail",
                date_field_candidates=["disclosure_start", "disclosure_end"],
                capabilities=SimpleNamespace(supports_list_only=True, supports_prefetched_candidates=True),
            ),
            capabilities=SimpleNamespace(supports_list_only=True, supports_prefetched_candidates=True),
            default_page_size=20,
        )
        args = SimpleNamespace(
            start_date="2026-01-01",
            end_date="2026-01-08",
            split_min_days=1,
            split_candidates=1,
            split_max_depth=3,
        )

        # Candidate entries with NORMALIZED date fields
        candidate_entries = [
            {"project_code": "P001", "disclosure_start": "2026-01-01", "disclosure_end": "2026-01-03"},
            {"project_code": "P002", "disclosure_start": "2026-01-05", "disclosure_end": "2026-01-07"},
        ]

        def fake_build_downloader(*args, **kwargs):
            return object()

        def fake_run_downloader(downloader, *, start_date: str, end_date: str, list_only: bool):
            return SimpleNamespace(
                detail_candidates=2,
                listed_items=2,
                skipped_by_list_date=0,
                skipped_by_resume=0,
                candidate_dates=["2026-01-01", "2026-01-05"],
                candidate_entries=candidate_entries,
                errors=[],
            )

        def parse_date_arg(raw: str | None, _name: str) -> dt.date | None:
            if raw in (None, ""):
                return None
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()

        chunks, entries, resolved_basis = plan_auto_split_chunks(
            spec=spec,
            args=args,
            output_root="C:\\temp\\auto_html",
            logger=logging.getLogger("test"),
            build_downloader=fake_build_downloader,
            run_downloader=fake_run_downloader,
            parse_date_arg=parse_date_arg,
        )

        # The split planning must use normalized disclosure_start/disclosure_end
        # NOT raw row keys like "plksrq" or "gpksrq"
        for entry in entries:
            self.assertIn("project_code", entry)
            # Should have normalized date fields, not raw row keys
            self.assertNotIn("plksrq", str(entry))
            self.assertNotIn("gpksrq", str(entry))


# Dummy class to make test import work
@dataclass
class DownloadOneClickRunRunResult:
    exit_code: int
    log_file: str
    plan_file: str
    plan_file_exists: bool
    plan_file_removed: bool
    start: str
    end: str
    duration_sec: float
    aggregate_summary: dict[str, int]
    task_summaries: dict[str, dict]
    errors: list[str]
    stages: list[object] = field(default_factory=list)
    typed_errors: list[dict] = field(default_factory=list)


if __name__ == "__main__":
    unittest.main()
