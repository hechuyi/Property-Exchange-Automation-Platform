from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap import daily_pipeline
from peap.streaming_daily_pipeline import StreamingDailyPipelineRunResult


class DailyPipelineFacadeTest(unittest.TestCase):
    def test_run_daily_pipeline_always_forwards_to_streaming_pipeline(self) -> None:
        args = SimpleNamespace(streaming=False, start_date="2026-01-01")
        config = SimpleNamespace()
        expected = StreamingDailyPipelineRunResult(
            exit_code=0,
            log_file="streaming.log",
            db_path="/tmp/streaming.sqlite3",
            job_id="job-1",
            start_date="2026-01-01",
            end_date="2026-01-02",
            duration_sec=0.1,
        )

        with patch.object(
            daily_pipeline,
            "run_streaming_daily_pipeline",
            return_value=expected,
        ) as streaming_pipeline:
            result = daily_pipeline.run_daily_pipeline(
                args,
                config_obj=config,
                emit_console=False,
            )

        self.assertIs(result, expected)
        streaming_pipeline.assert_called_once_with(
            args,
            config_obj=config,
            emit_console=False,
        )

    def test_daily_pipeline_has_no_legacy_download_parser_or_postprocess_orchestration(self) -> None:
        source = inspect.getsource(daily_pipeline)
        for legacy_symbol in (
            "run_download_oneclick",
            "run_parser_request",
            "run_postprocess_request",
            "DownloadOneClickRequest",
            "ParserRunRequest",
            "PostProcessRunRequest",
        ):
            with self.subTest(legacy_symbol=legacy_symbol):
                self.assertNotIn(legacy_symbol, source)

        self.assertNotIn("getattr(args, \"streaming\"", source)
        self.assertIs(daily_pipeline.DailyPipelineRunResult, StreamingDailyPipelineRunResult)


if __name__ == "__main__":
    unittest.main()
