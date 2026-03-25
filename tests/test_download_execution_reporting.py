from __future__ import annotations

import datetime as dt
import unittest
from types import SimpleNamespace

from peap.download_execution import execute_split_task
from peap.download_models import ChunkStateContext, DateChunk, TaskSplitPlan
from peap.download_reporting import (
    accumulate,
    new_totals,
    summary_to_dict,
    totals_to_summary_dict,
)
from peap.download_task_flow import run_download_task
from peap.download_tasks import build_task_registry


class DownloadExecutionReportingTest(unittest.TestCase):
    def test_reporting_helpers_accumulate_expected_fields(self) -> None:
        summary = SimpleNamespace(
            pages_requested=2,
            listed_items=5,
            detail_fetched=4,
            saved=3,
            skipped_by_list_date=1,
            skipped_by_detail_date=0,
            skipped_by_resume=2,
            skipped_by_duplicate=1,
            skipped_by_missing_xmid=0,
            detail_candidates=6,
            detail_failed=1,
            list_unaccounted=0,
            detail_unaccounted=2,
            errors=["boom"],
        )

        totals = new_totals()
        errors: list[str] = []
        accumulate(summary, totals, errors)

        self.assertEqual(summary_to_dict(summary)["detail_candidates"], 6)
        self.assertEqual(totals["saved"], 3)
        self.assertEqual(totals_to_summary_dict(totals, errors)["errors"], 1)

    def test_execute_split_task_skips_done_and_zero_chunks_and_updates_state(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:physical_asset"]
        args = SimpleNamespace(split_mode="fast", resume=True)
        logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        chunks = [
            DateChunk(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 1), estimated_candidates=3),
            DateChunk(start=dt.date(2026, 1, 2), end=dt.date(2026, 1, 2), estimated_candidates=0),
            DateChunk(start=dt.date(2026, 1, 3), end=dt.date(2026, 1, 3), estimated_candidates=2),
        ]
        task_chunk_state = {
            "chunks": {
                "2026-01-01..2026-01-01": {"status": "done", "attempts": 1},
                "2026-01-02..2026-01-02": {"status": "pending", "attempts": 0},
                "2026-01-03..2026-01-03": {"status": "pending", "attempts": 0},
            }
        }
        ctx = ChunkStateContext(path="noop.json", payload={"tasks": {}})
        save_calls: list[str] = []
        build_calls: list[object] = []
        run_calls: list[dict[str, object]] = []

        def build_downloader(*args, **kwargs):
            build_calls.append(kwargs.get("resume_override"))
            return object()

        def run_downloader_with_prefetched(
            downloader,
            *,
            start_date: str,
            end_date: str,
            list_only: bool,
            prefetched_candidates,
        ):
            run_calls.append(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "list_only": list_only,
                    "prefetched_candidates": prefetched_candidates,
                }
            )
            return SimpleNamespace(
                pages_requested=1,
                listed_items=2,
                detail_fetched=2,
                saved=2,
                skipped_by_list_date=0,
                skipped_by_detail_date=0,
                skipped_by_resume=0,
                skipped_by_duplicate=0,
                skipped_by_missing_xmid=0,
                detail_candidates=2,
                detail_failed=0,
                list_unaccounted=0,
                detail_unaccounted=0,
                errors=[],
            )

        from peap import download_execution as execution_module

        original_save = execution_module.save_chunk_state
        execution_module.save_chunk_state = lambda ctx: save_calls.append(ctx.path)
        try:
            any_failure = execute_split_task(
                spec=spec,
                args=args,
                logger=logger,
                output_root="C:\\temp\\auto_html",
                chunks=chunks,
                candidate_entries=[
                    {"project_code": "XM001", "list_disclosure_start": "2026-01-03"},
                ],
                task_totals=new_totals(),
                task_errors=[],
                any_failure=False,
                build_downloader=build_downloader,
                run_downloader_with_prefetched=run_downloader_with_prefetched,
                task_chunk_state=task_chunk_state,
                chunk_state_ctx=ctx,
            )
        finally:
            execution_module.save_chunk_state = original_save

        self.assertFalse(any_failure)
        self.assertEqual(len(run_calls), 1)
        self.assertEqual(run_calls[0]["start_date"], "2026-01-03")
        self.assertEqual(len(run_calls[0]["prefetched_candidates"]), 1)
        self.assertEqual(build_calls, [False])
        self.assertEqual(task_chunk_state["chunks"]["2026-01-02..2026-01-02"]["status"], "done")
        self.assertEqual(task_chunk_state["chunks"]["2026-01-03..2026-01-03"]["status"], "done")
        self.assertGreaterEqual(len(save_calls), 2)

    def test_run_download_task_split_plan_only_returns_task_result_without_execution(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:physical_asset"]
        args = SimpleNamespace(
            auto_split=True,
            split_use_plan=True,
            split_plan_only=True,
            start_date="2026-01-01",
            end_date="2026-01-10",
        )
        logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        loaded_plan_map = {
            spec.task_id: TaskSplitPlan(
                chunks=[
                    DateChunk(
                        start=dt.date(2026, 1, 1),
                        end=dt.date(2026, 1, 3),
                        estimated_candidates=2,
                    ),
                    DateChunk(
                        start=dt.date(2026, 1, 4),
                        end=dt.date(2026, 1, 6),
                        estimated_candidates=1,
                    ),
                ],
                candidate_entries=[],
            )
        }
        build_calls: list[object] = []
        run_calls: list[object] = []

        def build_downloader(*args, **kwargs):
            build_calls.append((args, kwargs))
            return object()

        def run_downloader(*args, **kwargs):
            run_calls.append((args, kwargs))
            return None

        result = run_download_task(
            spec,
            args=args,
            logger=logger,
            output_root="C:\\temp\\auto_html",
            loaded_plan_map=loaded_plan_map,
            chunk_state_ctx=None,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            run_downloader_with_prefetched=run_downloader,
            parse_date_arg=lambda raw, _name: dt.datetime.strptime(raw, "%Y-%m-%d").date() if raw else None,
        )

        self.assertFalse(result.any_failure)
        self.assertEqual(result.totals["saved"], 0)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.task_result["chunk_count"], 2)
        self.assertEqual(result.task_result["summary"]["errors"], 0)
        self.assertEqual(build_calls, [])
        self.assertEqual(run_calls, [])


if __name__ == "__main__":
    unittest.main()
