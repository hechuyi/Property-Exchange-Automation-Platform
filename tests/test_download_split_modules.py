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


def _build_scope(exchange="sse", project_type="physical_asset", start="2026-01-01", end="2026-01-08"):
    return {
        "source_id": exchange,
        "project_type": project_type,
        "start_date": start,
        "end_date": end,
        "split_candidates": 1,
        "split_min_days": 1,
        "split_max_depth": 3,
        "split_mode": "fast",
    }


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

    def test_load_split_plan_file_validates_scope_mismatch(self) -> None:
        """A saved plan whose scope does not match current args is rejected."""
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
                    candidate_entries=[],
                    resolved_basis=SplitPlanResolvedBasis(
                        date_fields=("disclosure_start",),
                        unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                    ),
                )
            }
            # Save plan with one scope
            saved_scope = _build_scope(exchange="sse", project_type="physical_asset", start="2026-01-01", end="2026-01-08")
            save_split_plan_file(plan_path, tasks_to_plan=plan_map, scope=saved_scope)

            # Load with mismatched scope - should raise
            mismatched_scope = _build_scope(exchange="cbex", project_type="physical_asset", start="2026-01-01", end="2026-01-08")
            with self.assertRaises(ValueError) as ctx:
                load_split_plan_file(plan_path, scope=mismatched_scope)
            self.assertIn("scope", str(ctx.exception).lower())

    def test_load_split_plan_file_reuses_matching_scope(self) -> None:
        """A saved plan with matching scope is loaded without error."""
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
            matching_scope = _build_scope(exchange="sse", project_type="physical_asset", start="2026-01-01", end="2026-01-08")
            save_split_plan_file(plan_path, tasks_to_plan=plan_map, scope=matching_scope)

            # Load with matching scope - should succeed
            loaded = load_split_plan_file(plan_path, scope=matching_scope)
            self.assertEqual(list(loaded), ["sse:physical_asset"])
            self.assertEqual(loaded["sse:physical_asset"].chunks[0].start_str, "2026-01-01")

    def test_prepare_task_chunk_state_preserves_done_chunks(self) -> None:
        """Persisted done chunks stay done after chunk state reload."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = os.path.join(tmp_dir, "chunk_state.json")
            chunk = DateChunk(
                start=dt.date(2026, 1, 1),
                end=dt.date(2026, 1, 3),
                estimated_candidates=4,
            )
            # Pre-populate state with a done chunk
            write_json_file(
                state_path,
                {
                    "version": 1,
                    "tasks": {
                        "sse:physical_asset": {
                            "plan_signature": _chunks_signature([chunk]),
                            "chunks": {
                                "2026-01-01..2026-01-03": {
                                    "status": "done",
                                    "attempts": 2,
                                    "updated_at": "2026-01-01T12:00:00",
                                    "last_error": None,
                                }
                            }
                        }
                    },
                },
                ensure_ascii=False,
            )

            ctx = load_chunk_state(state_path)
            task_state = prepare_task_chunk_state(
                ctx,
                task_id="sse:physical_asset",
                chunks=[chunk],
            )
            # The done chunk must remain done, not reset to pending
            chunk_state = get_chunk_state(task_state, chunk)
            self.assertEqual(chunk_state["status"], "done")
            self.assertEqual(chunk_state["attempts"], 2)

    def test_prepare_task_chunk_state_preserves_failed_chunks(self) -> None:
        """Persisted failed chunks retain their attempts after reload."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = os.path.join(tmp_dir, "chunk_state.json")
            chunk = DateChunk(
                start=dt.date(2026, 1, 1),
                end=dt.date(2026, 1, 3),
                estimated_candidates=4,
            )
            write_json_file(
                state_path,
                {
                    "version": 1,
                    "tasks": {
                        "sse:physical_asset": {
                            "plan_signature": _chunks_signature([chunk]),
                            "chunks": {
                                "2026-01-01..2026-01-03": {
                                    "status": "failed",
                                    "attempts": 3,
                                    "updated_at": "2026-01-01T12:00:00",
                                    "last_error": "boom",
                                }
                            }
                        }
                    },
                },
                ensure_ascii=False,
            )

            ctx = load_chunk_state(state_path)
            task_state = prepare_task_chunk_state(
                ctx,
                task_id="sse:physical_asset",
                chunks=[chunk],
            )
            chunk_state = get_chunk_state(task_state, chunk)
            self.assertEqual(chunk_state["status"], "failed")
            self.assertEqual(chunk_state["attempts"], 3)
            self.assertEqual(chunk_state["last_error"], "boom")


def _chunks_signature(chunks: list[DateChunk]) -> str:
    parts = [f"{chunk.start_str}..{chunk.end_str}:{int(chunk.estimated_candidates)}" for chunk in chunks]
    return "|".join(parts)
