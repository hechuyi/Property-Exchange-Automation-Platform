from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace

from peap.download_chunk_state import (
    get_chunk_state,
    load_chunk_state,
    prepare_task_chunk_state,
    save_chunk_state,
    update_chunk_state,
)
from peap.download_models import DateChunk, TaskSplitPlan
from peap.download_split_planning import (
    load_split_plan_file,
    plan_auto_split_chunks,
    save_split_plan_file,
)
from peap.download_tasks import build_task_registry
from peap_core.runtime import write_json_file


class DownloadSplitModulesTest(unittest.TestCase):
    def test_load_chunk_state_marks_running_chunks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = os.path.join(tmp_dir, "chunk_state.json")
            write_json_file(
                state_path,
                {
                    "version": 1,
                    "tasks": {
                        "sse:physical_asset": {
                            "chunks": {
                                "2026-01-01..2026-01-02": {
                                    "status": "running",
                                    "attempts": 2,
                                }
                            }
                        }
                    },
                },
                ensure_ascii=False,
            )

            ctx = load_chunk_state(state_path)

            chunk_payload = ctx.payload["tasks"]["sse:physical_asset"]["chunks"]["2026-01-01..2026-01-02"]
            self.assertEqual(chunk_payload["status"], "failed")
            self.assertEqual(chunk_payload["attempts"], 2)

    def test_prepare_update_and_save_chunk_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = os.path.join(tmp_dir, "chunk_state.json")
            chunk = DateChunk(
                start=dt.date(2026, 1, 1),
                end=dt.date(2026, 1, 3),
                estimated_candidates=4,
            )
            ctx = load_chunk_state(state_path)

            task_state = prepare_task_chunk_state(
                ctx,
                task_id="sse:physical_asset",
                chunks=[chunk],
            )
            self.assertEqual(get_chunk_state(task_state, chunk)["status"], "pending")

            update_chunk_state(task_state, chunk, status="running", increment_attempts=True)
            update_chunk_state(task_state, chunk, status="failed", error="boom")
            save_chunk_state(ctx)

            reloaded = load_chunk_state(state_path)
            reloaded_chunk = reloaded.payload["tasks"]["sse:physical_asset"]["chunks"]["2026-01-01..2026-01-03"]
            self.assertEqual(reloaded_chunk["status"], "failed")
            self.assertEqual(reloaded_chunk["attempts"], 1)
            self.assertEqual(reloaded_chunk["last_error"], "boom")

    def test_save_and_load_split_plan_round_trip(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = os.path.join(tmp_dir, "split_plan.json")
            plan_map = {
                "sse:physical_asset": TaskSplitPlan(
                    chunks=[
                        DateChunk(
                            start=dt.date(2026, 1, 1),
                            end=dt.date(2026, 1, 4),
                            estimated_candidates=3,
                        )
                    ],
                    candidate_entries=[{"project_code": "XM001", "list_disclosure_start": "2026-01-02"}],
                    resolved_basis=SplitPlanResolvedBasis(
                        date_fields=("disclosure_start",),
                        unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                    ),
                )
            }

            save_split_plan_file(
                plan_path,
                tasks_to_plan=plan_map,
                scope={"exchange": "sse", "project_type": "physical_asset"},
            )
            loaded = load_split_plan_file(plan_path)

            self.assertEqual(list(loaded), ["sse:physical_asset"])
            self.assertEqual(loaded["sse:physical_asset"].chunks[0].start_str, "2026-01-01")
            self.assertEqual(
                loaded["sse:physical_asset"].candidate_entries[0]["project_code"],
                "XM001",
            )

    def test_plan_auto_split_chunks_uses_callbacks(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:physical_asset"]
        args = SimpleNamespace(
            start_date="2026-01-01",
            end_date="2026-01-08",
            split_min_days=1,
            split_candidates=1,
            split_max_depth=3,
        )
        calls: dict[str, object] = {}
        summary = SimpleNamespace(
            detail_candidates=4,
            listed_items=4,
            skipped_by_list_date=0,
            skipped_by_resume=0,
            candidate_dates=[
                "2026-01-01",
                "2026-01-02",
                "2026-01-05",
                "2026-01-07",
            ],
            candidate_entries=[
                {"project_code": "XM001", "disclosure_start": "2026-01-01"},
                {"project_code": "XM002", "disclosure_start": "2026-01-05"},
            ],
            errors=[],
        )

        def build_downloader(*args, **kwargs):
            calls["build"] = {"args": args, "kwargs": kwargs}
            return object()

        def run_downloader(downloader, *, start_date: str, end_date: str, list_only: bool):
            calls["run"] = {
                "start_date": start_date,
                "end_date": end_date,
                "list_only": list_only,
            }
            return summary

        def parse_date_arg(raw: str | None, _name: str) -> dt.date | None:
            if raw in (None, ""):
                return None
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()

        chunks, candidate_entries, resolved_basis = plan_auto_split_chunks(
            spec=spec,
            args=args,
            output_root="C:\\temp\\auto_html",
            logger=logging.getLogger("download_split_modules_test"),
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            parse_date_arg=parse_date_arg,
        )

        self.assertIn("build", calls)
        self.assertEqual(calls["run"]["list_only"], True)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len(candidate_entries), 2)
        self.assertIsNotNone(resolved_basis)


if __name__ == "__main__":
    unittest.main()
