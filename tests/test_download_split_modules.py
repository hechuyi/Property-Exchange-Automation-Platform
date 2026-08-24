from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace

from peap.download_artifact_audit import (
    DownloadArtifactAudit,
    StaleDownloadArtifact,
    TaskArtifactAudit,
)
from peap.download_chunk_state import (
    get_chunk_state,
    load_chunk_state,
    prepare_task_chunk_state,
    save_chunk_state,
    update_chunk_state,
)
from peap.download_errors import DownloadError
from peap.download_execution import execute_split_task
from peap.download_models import DateChunk, TaskSplitPlan
from peap.download_split_planning import (
    load_split_plan_file,
    plan_auto_split_chunks,
    plan_auto_split_task,
    save_split_plan_file,
)
from peap.download_task_flow import run_download_task
from peap.download_tasks import build_task_registry
from peap.downloaders.common import DownloadSummary, HttpFetchedText
from peap.downloaders.discovery_contract import expected_discovery_query_ids
from peap.downloaders.discovery_evidence import DiscoveryTaskEvidence
from peap_core.runtime import write_json_file


def _build_scope(exchange="sse", business_id="physical_asset", start="2026-01-01", end="2026-01-08"):
    return {
        "source_id": exchange,
        "record_family": "listing",
        "business_id": business_id,
        "start_date": start,
        "end_date": end,
        "split_candidates": 1,
        "split_min_days": 1,
        "split_max_depth": 3,
        "split_mode": "fast",
    }


def _write_discovery_manifest(
    root: str,
    *,
    task_id: str,
    run_id: str,
    candidate_entries: list[dict[str, object]],
) -> dict[str, object]:
    source_id, record_family, business_id = task_id.split(":")
    query_ids = expected_discovery_query_ids(
        source_id=source_id,
        record_family=record_family,
        business_id=business_id,
    )
    task = DiscoveryTaskEvidence(
        root=root,
        source_id=source_id,
        task_id=task_id,
        run_id=run_id,
        expected_query_ids=query_ids,
    )
    for index, query_id in enumerate(query_ids, start=1):
        query = task.query(query_id, authoritative_total=True, page_size=20)
        response = HttpFetchedText(
            '{"rows":[]}',
            source_url=f"https://example.test/list?query={index}",
            final_url=f"https://example.test/list?query={index}",
            http_status=200,
            raw_bytes=b'{"rows":[]}',
        )
        query.record_page(
            page_index=1,
            response=response,
            body_format="json",
            extracted_row_count=0,
            declared_total_items=0,
            declared_total_pages=1,
        )
        query.complete(termination_reason="declared_pages_exhausted")
    task.complete(candidate_entries=candidate_entries)
    return task.manifest_reference()


class DownloadSplitModulesTest(unittest.TestCase):
    def test_load_chunk_state_marks_running_chunks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = os.path.join(tmp_dir, "chunk_state.json")
            write_json_file(
                state_path,
                {
                    "version": 1,
                    "tasks": {
                        "sse:listing:physical_asset": {
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

            chunk_payload = ctx.payload["tasks"]["sse:listing:physical_asset"]["chunks"]["2026-01-01..2026-01-02"]
            self.assertEqual(chunk_payload["status"], "failed")
            self.assertEqual(chunk_payload["attempts"], 2)

    def test_load_chunk_state_rejects_corrupt_existing_state_shape(self) -> None:
        for case_name, payload in (
            ("top-level-list", []),
            ("tasks-list", {"version": 1, "tasks": []}),
        ):
            with self.subTest(case_name=case_name), tempfile.TemporaryDirectory() as tmp_dir:
                state_path = os.path.join(tmp_dir, "chunk_state.json")
                write_json_file(state_path, payload, ensure_ascii=False)

                with self.assertRaises(ValueError) as ctx:
                    load_chunk_state(state_path)

                self.assertIn("invalid chunk state format", str(ctx.exception))

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
                task_id="sse:listing:physical_asset",
                chunks=[chunk],
            )
            self.assertEqual(get_chunk_state(task_state, chunk)["status"], "pending")

            update_chunk_state(task_state, chunk, status="running", increment_attempts=True)
            update_chunk_state(task_state, chunk, status="failed", error="boom")
            save_chunk_state(ctx)

            reloaded = load_chunk_state(state_path)
            reloaded_chunk = reloaded.payload["tasks"]["sse:listing:physical_asset"]["chunks"]["2026-01-01..2026-01-03"]
            self.assertEqual(reloaded_chunk["status"], "failed")
            self.assertEqual(reloaded_chunk["attempts"], 1)
            self.assertEqual(reloaded_chunk["last_error"], "boom")

    def test_save_and_load_split_plan_round_trip(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = os.path.join(tmp_dir, "split_plan.json")
            plan_map = {
                "sse:listing:physical_asset": TaskSplitPlan(
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
                    discovery_task_manifest={
                        "source_id": "sse",
                        "task_id": "sse:listing:physical_asset",
                        "run_id": "run-1",
                        "path": "_evidence/run-1/sse__listing__physical_asset/discovery/task_manifest.json",
                        "sha256": "sha256:" + "c" * 64,
                        "bytes": 640,
                    },
                )
            }

            save_split_plan_file(
                plan_path,
                tasks_to_plan=plan_map,
                scope={"exchange": "sse", "record_family": "listing", "business_id": "physical_asset"},
            )
            loaded = load_split_plan_file(plan_path)

            self.assertEqual(list(loaded), ["sse:listing:physical_asset"])
            self.assertEqual(loaded["sse:listing:physical_asset"].chunks[0].start_str, "2026-01-01")
            self.assertEqual(
                loaded["sse:listing:physical_asset"].candidate_entries[0]["project_code"],
                "XM001",
            )
            self.assertEqual(
                loaded["sse:listing:physical_asset"].discovery_task_manifest,
                plan_map["sse:listing:physical_asset"].discovery_task_manifest,
            )

    def test_load_split_plan_file_rejects_bad_task_collection_fields(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP

        for field_name, bad_value in (
            ("chunks", None),
            ("candidate_entries", {"project_code": "XM001"}),
        ):
            with self.subTest(field_name=field_name), tempfile.TemporaryDirectory() as tmp_dir:
                plan_path = os.path.join(tmp_dir, "split_plan.json")
                task_payload = {
                    "chunks": [
                        {
                            "start": "2026-01-01",
                            "end": "2026-01-03",
                            "estimated_candidates": 1,
                        }
                    ],
                    "candidate_entries": [],
                    "resolved_basis": {
                        "date_fields": ["disclosure_start"],
                        "unresolved_candidate_policy": SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                    },
                }
                task_payload[field_name] = bad_value
                write_json_file(
                    plan_path,
                    {
                        "version": 1,
                        "tasks": {
                            "sse:listing:physical_asset": task_payload,
                        },
                    },
                    ensure_ascii=False,
                )

                with self.assertRaises(ValueError) as ctx:
                    load_split_plan_file(plan_path)

                message = str(ctx.exception)
                self.assertIn("sse:listing:physical_asset", message)
                self.assertIn(field_name, message)

    def test_load_split_plan_file_rejects_non_object_candidate_entries(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP

        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = os.path.join(tmp_dir, "split_plan.json")
            write_json_file(
                plan_path,
                {
                    "version": 1,
                    "tasks": {
                        "sse:listing:physical_asset": {
                            "chunks": [
                                {
                                    "start": "2026-01-01",
                                    "end": "2026-01-03",
                                    "estimated_candidates": 1,
                                }
                            ],
                            "candidate_entries": [
                                {"project_code": "XM001", "disclosure_start": "2026-01-02"},
                                "not-an-object",
                            ],
                            "resolved_basis": {
                                "date_fields": ["disclosure_start"],
                                "unresolved_candidate_policy": SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                            },
                        },
                    },
                },
                ensure_ascii=False,
            )

            with self.assertRaises(ValueError) as ctx:
                load_split_plan_file(plan_path)

            message = str(ctx.exception)
            self.assertIn("sse:listing:physical_asset", message)
            self.assertIn("candidate_entries[1]", message)

    def test_load_split_plan_file_rejects_invalid_chunk_schema(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP

        base_task_payload = {
            "chunks": [
                {
                    "start": "2026-01-01",
                    "end": "2026-01-03",
                    "estimated_candidates": 1,
                }
            ],
            "candidate_entries": [],
            "resolved_basis": {
                "date_fields": ["disclosure_start"],
                "unresolved_candidate_policy": SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
            },
        }
        cases = (
            (
                "missing-chunks",
                {
                    "candidate_entries": [],
                    "resolved_basis": base_task_payload["resolved_basis"],
                },
            ),
            ("empty-chunks", {**base_task_payload, "chunks": []}),
            ("non-object-chunk", {**base_task_payload, "chunks": [None]}),
            (
                "reverse-date-chunk",
                {
                    **base_task_payload,
                    "chunks": [
                        {
                            "start": "2026-01-03",
                            "end": "2026-01-01",
                            "estimated_candidates": 1,
                        }
                    ],
                },
            ),
        )

        for case_name, task_payload in cases:
            with self.subTest(case_name=case_name), tempfile.TemporaryDirectory() as tmp_dir:
                plan_path = os.path.join(tmp_dir, "split_plan.json")
                write_json_file(
                    plan_path,
                    {
                        "version": 1,
                        "tasks": {
                            "sse:listing:physical_asset": task_payload,
                        },
                    },
                    ensure_ascii=False,
                )

                with self.assertRaises(ValueError) as ctx:
                    load_split_plan_file(plan_path)

                message = str(ctx.exception)
                self.assertIn("sse:listing:physical_asset", message)
                self.assertIn("chunks", message)

    def test_plan_auto_split_chunks_uses_callbacks(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:listing:physical_asset"]
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

        with tempfile.TemporaryDirectory() as tmp_dir:
            summary.discovery_task_manifest = _write_discovery_manifest(
                tmp_dir,
                task_id=spec.task_id,
                run_id="run-callbacks",
                candidate_entries=summary.candidate_entries,
            )
            chunks, candidate_entries, resolved_basis = plan_auto_split_chunks(
                spec=spec,
                args=args,
                output_root=tmp_dir,
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

    def test_plan_auto_split_task_retains_discovery_manifest_reference(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_min_days=1,
            split_candidates=10,
            split_max_depth=3,
        )
        candidate_entries = [
            {"project_code": "XM001", "disclosure_start": "2026-01-01"}
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            reference = _write_discovery_manifest(
                tmp_dir,
                task_id=spec.task_id,
                run_id="run-1",
                candidate_entries=candidate_entries,
            )
            summary = DownloadSummary(
                detail_candidates=1,
                listed_items=1,
                candidate_dates=["2026-01-01"],
                candidate_entries=candidate_entries,
                discovery_task_manifest=reference,
            )

            plan = plan_auto_split_task(
                spec=spec,
                args=args,
                output_root=tmp_dir,
                logger=logging.getLogger("download_split_modules_test"),
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: summary,
                parse_date_arg=lambda raw, _name: dt.datetime.strptime(
                    str(raw), "%Y-%m-%d"
                ).date(),
            )

            self.assertEqual(plan.discovery_task_manifest, reference)

    def test_plan_auto_split_chunks_rejects_malformed_candidate_entry_date(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:listing:physical_asset"]
        args = SimpleNamespace(
            start_date="2026-01-01",
            end_date="2026-01-08",
            split_min_days=1,
            split_candidates=1,
            split_max_depth=3,
        )
        summary = SimpleNamespace(
            detail_candidates=1,
            listed_items=1,
            skipped_by_list_date=0,
            skipped_by_resume=0,
            candidate_entries=[
                {"project_code": "XM001", "disclosure_start": "not-a-date"},
            ],
            errors=[],
        )

        def parse_date_arg(raw: str | None, _name: str) -> dt.date | None:
            if raw in (None, ""):
                return None
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()

        with self.assertRaisesRegex(ValueError, "candidate_entries\\[0\\].*disclosure_start"):
            plan_auto_split_chunks(
                spec=spec,
                args=args,
                output_root="C:\\temp\\auto_html",
                logger=logging.getLogger("download_split_modules_test"),
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: summary,
                parse_date_arg=parse_date_arg,
            )

    def test_load_split_plan_file_validates_scope_mismatch(self) -> None:
        """A saved plan whose scope does not match current args is rejected."""
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = os.path.join(tmp_dir, "split_plan.json")
            plan_map = {
                "sse:listing:physical_asset": TaskSplitPlan(
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
            saved_scope = _build_scope(exchange="sse", business_id="physical_asset", start="2026-01-01", end="2026-01-08")
            save_split_plan_file(plan_path, tasks_to_plan=plan_map, scope=saved_scope)

            # Load with mismatched scope - should raise
            mismatched_scope = _build_scope(exchange="cbex", business_id="physical_asset", start="2026-01-01", end="2026-01-08")
            with self.assertRaises(ValueError) as ctx:
                load_split_plan_file(plan_path, scope=mismatched_scope)
            self.assertIn("scope", str(ctx.exception).lower())

    def test_load_split_plan_file_reuses_matching_scope(self) -> None:
        """A saved plan with matching scope is loaded without error."""
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = os.path.join(tmp_dir, "split_plan.json")
            plan_map = {
                "sse:listing:physical_asset": TaskSplitPlan(
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
            matching_scope = _build_scope(exchange="sse", business_id="physical_asset", start="2026-01-01", end="2026-01-08")
            save_split_plan_file(plan_path, tasks_to_plan=plan_map, scope=matching_scope)

            # Load with matching scope - should succeed
            loaded = load_split_plan_file(plan_path, scope=matching_scope)
            self.assertEqual(list(loaded), ["sse:listing:physical_asset"])
            self.assertEqual(loaded["sse:listing:physical_asset"].chunks[0].start_str, "2026-01-01")

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
                        "sse:listing:physical_asset": {
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
                task_id="sse:listing:physical_asset",
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
                        "sse:listing:physical_asset": {
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
                task_id="sse:listing:physical_asset",
                chunks=[chunk],
            )
            chunk_state = get_chunk_state(task_state, chunk)
            self.assertEqual(chunk_state["status"], "failed")
            self.assertEqual(chunk_state["attempts"], 3)
            self.assertEqual(chunk_state["last_error"], "boom")

    def test_done_chunk_with_stale_artifact_is_rechecked(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=1,
            )
            ctx = load_chunk_state(os.path.join(tmp_dir, "chunk_state.json"))
            task_state = prepare_task_chunk_state(ctx, task_id=spec.task_id, chunks=[chunk])
            update_chunk_state(task_state, chunk, status="done")
            save_chunk_state(ctx)
            calls: list[dict[str, object]] = []

            def build_downloader(*args, **kwargs):
                calls.append({"kind": "build", "kwargs": kwargs})
                return object()

            def run_with_prefetched(*args, **kwargs):
                calls.append({"kind": "run", "kwargs": kwargs})
                return DownloadSummary(saved=1)

            artifact = StaleDownloadArtifact(
                record_id="rec-missing",
                task_id=spec.task_id,
                project_code="XM-MISSING",
                project_name="missing artifact",
                listing_date=dt.date(2026, 5, 8),
                source_file=os.path.join(tmp_dir, "missing.html"),
                archive_path=os.path.join(tmp_dir, "missing.html"),
                evidence_verdict={
                    "status": "stale_reference",
                    "reason_code": "authoritative_artifact_missing",
                },
            )
            audit = TaskArtifactAudit(
                task_id=spec.task_id,
                stale_records=(artifact,),
                dated_stale_records={dt.date(2026, 5, 8): (artifact,)},
            )

            result = execute_split_task(
                spec=spec,
                args=SimpleNamespace(split_mode="fast", resume=True),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                chunks=[chunk],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                build_downloader=build_downloader,
                run_downloader_with_prefetched=run_with_prefetched,
                task_chunk_state=task_state,
                chunk_state_ctx=ctx,
                artifact_audit=audit,
            )

            self.assertEqual(result.totals["saved"], 1)
            self.assertEqual([call["kind"] for call in calls], ["build", "run"])
            self.assertEqual(calls[0]["kwargs"]["resume_override"], False)

    def test_done_chunk_with_last_error_is_rechecked(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=1,
            )
            ctx = load_chunk_state(os.path.join(tmp_dir, "chunk_state.json"))
            task_state = prepare_task_chunk_state(ctx, task_id=spec.task_id, chunks=[chunk])
            chunk_state = get_chunk_state(task_state, chunk)
            chunk_state["status"] = "done"
            chunk_state["last_error"] = "previous failure was not cleared"
            save_chunk_state(ctx)
            calls: list[str] = []

            def build_downloader(*args, **kwargs):
                calls.append("build")
                return object()

            def run_with_prefetched(*args, **kwargs):
                calls.append("run")
                return DownloadSummary(saved=1)

            result = execute_split_task(
                spec=spec,
                args=SimpleNamespace(split_mode="fast", resume=True),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                chunks=[chunk],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                build_downloader=build_downloader,
                run_downloader_with_prefetched=run_with_prefetched,
                task_chunk_state=task_state,
                chunk_state_ctx=ctx,
            )

            self.assertFalse(result.any_failure)
            self.assertEqual(result.totals["saved"], 1)
            self.assertEqual(calls, ["build", "run"])
            self.assertEqual(get_chunk_state(task_state, chunk)["status"], "done")
            self.assertNotIn("last_error", get_chunk_state(task_state, chunk))

    def test_fast_zero_candidate_chunk_with_stale_artifact_runs_with_resume_disabled(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=0,
            )
            calls: list[dict[str, object]] = []

            def build_downloader(*args, **kwargs):
                calls.append({"kind": "build", "kwargs": kwargs})
                return object()

            def run_with_prefetched(*args, **kwargs):
                calls.append({"kind": "run", "kwargs": kwargs})
                return DownloadSummary(saved=1)

            artifact = StaleDownloadArtifact(
                record_id="rec-zero-stale",
                task_id=spec.task_id,
                project_code="XM-ZERO-STALE",
                project_name="zero candidate stale artifact",
                listing_date=dt.date(2026, 5, 8),
                source_file=os.path.join(tmp_dir, "stale-zero.html"),
                archive_path=os.path.join(tmp_dir, "stale-zero.html"),
                evidence_verdict={
                    "status": "stale_reference",
                    "reason_code": "authoritative_artifact_missing",
                },
            )
            audit = TaskArtifactAudit(
                task_id=spec.task_id,
                stale_records=(artifact,),
                dated_stale_records={dt.date(2026, 5, 8): (artifact,)},
            )

            result = execute_split_task(
                spec=spec,
                args=SimpleNamespace(split_mode="fast", resume=True),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                chunks=[chunk],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                build_downloader=build_downloader,
                run_downloader_with_prefetched=run_with_prefetched,
                task_chunk_state=None,
                chunk_state_ctx=None,
                artifact_audit=audit,
            )

            self.assertEqual(result.totals["saved"], 1)
            self.assertEqual([call["kind"] for call in calls], ["build", "run"])
            self.assertEqual(calls[0]["kwargs"]["resume_override"], False)

    def test_fast_zero_candidate_skip_does_not_persist_done_chunk_state(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=0,
            )
            ctx = load_chunk_state(os.path.join(tmp_dir, "chunk_state.json"))
            task_state = prepare_task_chunk_state(ctx, task_id=spec.task_id, chunks=[chunk])

            result = execute_split_task(
                spec=spec,
                args=SimpleNamespace(split_mode="fast", resume=True),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                chunks=[chunk],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader_with_prefetched=lambda *args, **kwargs: self.fail(
                    "zero-estimate fast skip must not execute downloader"
                ),
                task_chunk_state=task_state,
                chunk_state_ctx=ctx,
            )

            self.assertFalse(result.any_failure)
            self.assertEqual(result.chunk_count, 1)
            self.assertNotEqual(get_chunk_state(task_state, chunk)["status"], "done")

    def test_split_use_plan_rejects_missing_task_instead_of_date_range_fallback(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]

            cases = (
                ("missing-task", {}, "split-plan-task-missing"),
                (
                    "empty-chunks",
                    {
                        spec.task_id: TaskSplitPlan(
                            chunks=[],
                            candidate_entries=[],
                            resolved_basis=SplitPlanResolvedBasis(
                                date_fields=("listing_date",),
                                unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                            ),
                        )
                    },
                    "split-plan-chunks-missing-or-empty",
                ),
            )

            for _name, loaded_plan_map, expected_reason in cases:
                with self.subTest(_name):
                    result = run_download_task(
                        spec,
                        args=SimpleNamespace(
                            auto_split=True,
                            split_use_plan=True,
                            split_plan_only=False,
                            split_mode="fast",
                            resume=True,
                            start_date="2026-05-01",
                            end_date="2026-05-31",
                        ),
                        logger=logging.getLogger("download_split_modules_test"),
                        output_root=tmp_dir,
                        loaded_plan_map=loaded_plan_map,
                        chunk_state_ctx=None,
                        build_downloader=lambda *args, **kwargs: self.fail(
                            "missing split-use-plan chunks must not build downloader"
                        ),
                        run_downloader=lambda *args, **kwargs: self.fail(
                            "missing split-use-plan chunks must not collect fallback chunks"
                        ),
                        run_downloader_with_prefetched=lambda *args, **kwargs: self.fail(
                            "missing split-use-plan chunks must not execute date range fallback"
                        ),
                        parse_date_arg=lambda raw, _name: dt.datetime.strptime(str(raw), "%Y-%m-%d").date(),
                    )

                    self.assertTrue(result.any_failure)
                    self.assertEqual(result.totals["saved"], 0)
                    self.assertEqual(len(result.typed_errors), 1)
                    self.assertIn(expected_reason, result.typed_errors[0].raw_reason)

    def test_split_use_plan_propagates_parent_discovery_manifest(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            reference = _write_discovery_manifest(
                tmp_dir,
                task_id=spec.task_id,
                run_id="run-plan",
                candidate_entries=[],
            )
            plan = TaskSplitPlan(
                chunks=[
                    DateChunk(
                        start=dt.date(2026, 5, 1),
                        end=dt.date(2026, 5, 31),
                        estimated_candidates=0,
                    )
                ],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                discovery_task_manifest=reference,
            )

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=True,
                    split_use_plan=True,
                    split_plan_only=False,
                    split_mode="fast",
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={spec.task_id: plan},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: self.fail(
                    "loaded plan must not recollect discovery"
                ),
                run_downloader_with_prefetched=lambda *args, **kwargs: self.fail(
                    "zero-candidate plan must not execute downloader"
                ),
                parse_date_arg=lambda raw, _name: dt.datetime.strptime(
                    str(raw), "%Y-%m-%d"
                ).date(),
            )

            self.assertFalse(result.any_failure)
            self.assertEqual(result.task_result["discovery_task_manifest"], reference)

    def test_split_use_plan_rejects_candidate_not_found_in_parent_discovery(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            discovered = {
                "xmid": "A",
                "project_code": "A",
                "page_url": "https://example.test/A",
                "disclosure_start": "2026-05-10",
                "row": {},
            }
            injected = {
                "xmid": "B",
                "project_code": "B",
                "page_url": "https://example.test/B",
                "disclosure_start": "2026-05-10",
                "row": {},
            }
            reference = _write_discovery_manifest(
                tmp_dir,
                task_id=spec.task_id,
                run_id="run-parent",
                candidate_entries=[discovered],
            )
            plan = TaskSplitPlan(
                chunks=[
                    DateChunk(
                        start=dt.date(2026, 5, 1),
                        end=dt.date(2026, 5, 31),
                        estimated_candidates=1,
                    )
                ],
                candidate_entries=[injected],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("disclosure_start",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                discovery_task_manifest=reference,
            )
            executed = False

            def run_with_prefetched(*args, **kwargs):
                nonlocal executed
                executed = True
                return DownloadSummary()

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=True,
                    split_use_plan=True,
                    split_plan_only=False,
                    split_mode="fast",
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={spec.task_id: plan},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: self.fail(
                    "loaded plan must not recollect discovery"
                ),
                run_downloader_with_prefetched=run_with_prefetched,
                parse_date_arg=lambda raw, _name: dt.datetime.strptime(
                    str(raw), "%Y-%m-%d"
                ).date(),
            )

            self.assertTrue(result.any_failure)
            self.assertFalse(executed)
            self.assertIn("not present", result.typed_errors[0].raw_reason)

    def test_split_task_rejects_non_download_error_summary_items(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=1,
            )
            bad_summary = DownloadSummary(saved=0)
            bad_summary.typed_errors = [
                DownloadError(
                    error_code="sse_execute_failed",
                    error_message="sse: execute-failed: partial",
                    stage="save_pages",
                    failure_kind="execute",
                    source_id="sse",
                    task_id="sse:listing:physical_asset",
                    raw_reason="partial",
                ),
                "not-typed",  # type: ignore[list-item]
            ]

            with self.assertRaisesRegex(TypeError, "summary.typed_errors must contain DownloadError items"):
                execute_split_task(
                    spec=spec,
                    args=SimpleNamespace(split_mode="fast", resume=True),
                    logger=logging.getLogger("download_split_modules_test"),
                    output_root=tmp_dir,
                    chunks=[chunk],
                    candidate_entries=[],
                    resolved_basis=SplitPlanResolvedBasis(
                        date_fields=("listing_date",),
                        unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                    ),
                    build_downloader=lambda *args, **kwargs: object(),
                    run_downloader_with_prefetched=lambda *args, **kwargs: bad_summary,
                    task_chunk_state=None,
                    chunk_state_ctx=None,
                )

    def test_non_split_task_with_stale_artifact_disables_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            calls: list[dict[str, object]] = []

            def build_downloader(*args, **kwargs):
                calls.append({"kind": "build", "kwargs": kwargs})
                return object()

            def run_downloader(*args, **kwargs):
                calls.append({"kind": "run", "kwargs": kwargs})
                return DownloadSummary(saved=0)

            artifact = StaleDownloadArtifact(
                record_id="rec-invalid-shell",
                task_id=spec.task_id,
                project_code="XM-INVALID",
                project_name="invalid shell artifact",
                listing_date=dt.date(2026, 5, 8),
                source_file=os.path.join(tmp_dir, "invalid-shell.html"),
                archive_path=os.path.join(tmp_dir, "invalid-shell.html"),
                evidence_verdict={
                    "status": "invalid_shell",
                    "reason_code": "known_invalid_shell_marker",
                },
            )
            audit = DownloadArtifactAudit(
                by_task_id={
                    spec.task_id: TaskArtifactAudit(
                        task_id=spec.task_id,
                        stale_records=(artifact,),
                        dated_stale_records={dt.date(2026, 5, 8): (artifact,)},
                    )
                }
            )

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=build_downloader,
                run_downloader=run_downloader,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
                artifact_audit=audit,
            )

            self.assertFalse(result.any_failure)
            self.assertEqual([call["kind"] for call in calls], ["build", "run"])
            self.assertEqual(calls[0]["kwargs"].get("resume_override"), False)

    def test_non_split_task_rejects_bad_downloaded_this_run_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            bad_summary = DownloadSummary(saved=1)
            bad_summary.downloaded_this_run = []  # type: ignore[assignment]

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: bad_summary,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
            )

            self.assertTrue(result.any_failure)
            self.assertEqual(len(result.typed_errors), 1)
            self.assertIn("summary.downloaded_this_run must be a set", result.typed_errors[0].raw_reason)

    def test_non_split_task_propagates_discovery_task_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            summary = DownloadSummary(saved=0)
            summary.discovery_task_manifest = {
                "source_id": "sse",
                "task_id": "sse:listing:physical_asset",
                "run_id": "run-1",
                "path": "_evidence/run-1/sse__listing__physical_asset/discovery/task_manifest.json",
                "sha256": "sha256:" + "b" * 64,
                "bytes": 512,
            }

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: summary,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
            )

            self.assertFalse(result.any_failure)
            self.assertEqual(
                result.task_result["discovery_task_manifest"],
                summary.discovery_task_manifest,
            )

    def test_non_split_task_rejects_bad_typed_errors_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            bad_summary = DownloadSummary(saved=0)
            bad_summary.typed_errors = {}  # type: ignore[assignment]

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: bad_summary,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
            )

            self.assertTrue(result.any_failure)
            self.assertEqual(len(result.typed_errors), 1)
            self.assertIn("summary.typed_errors must be a list", result.typed_errors[0].raw_reason)

    def test_non_split_task_converts_summary_failure_counts_to_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            summary = DownloadSummary(
                listed_items=1,
                detail_candidates=1,
                detail_failed=1,
                typed_errors=[],
            )

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: summary,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
            )

            self.assertTrue(result.any_failure)
            self.assertEqual(len(result.typed_errors), 1)
            self.assertEqual(result.typed_errors[0].error_code, "sse_summary_failure_count")
            self.assertIn("detail_failed=1", result.typed_errors[0].raw_reason)
            self.assertEqual(result.totals["detail_failed"], 1)
            self.assertIsNotNone(result.task_result)
            self.assertEqual(result.task_result["summary"]["detail_failed"], 1)
            self.assertEqual(result.task_result["summary"]["errors"], 1)

    def test_non_split_task_rejects_duplicate_download_target_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            summary = DownloadSummary(saved=2, typed_errors=[])
            summary.downloaded_this_run = {"sse__listing__physical_asset/2026年7月/P001.html"}

            result = run_download_task(
                spec,
                args=SimpleNamespace(
                    auto_split=False,
                    resume=True,
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                ),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader=lambda *args, **kwargs: summary,
                run_downloader_with_prefetched=lambda *args, **kwargs: None,
                parse_date_arg=lambda raw, _name: raw,
            )

            self.assertTrue(result.any_failure)
            self.assertEqual(len(result.typed_errors), 1)
            self.assertEqual(result.typed_errors[0].error_code, "sse_duplicate_download_target")
            self.assertIn("saved=2 unique_download_targets=1", result.typed_errors[0].raw_reason)

    def test_split_task_converts_summary_failure_counts_to_typed_error(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        for field_name, totals_key in (
            ("detail_failed", "detail_failed"),
            ("list_unaccounted", "list_unaccounted"),
            ("detail_unaccounted", "detail_unaccounted"),
        ):
            with self.subTest(field_name=field_name), tempfile.TemporaryDirectory() as tmp_dir:
                spec = build_task_registry()["sse:listing:physical_asset"]
                chunk = DateChunk(
                    start=dt.date(2026, 5, 1),
                    end=dt.date(2026, 5, 31),
                    estimated_candidates=1,
                )
                ctx = load_chunk_state(os.path.join(tmp_dir, "chunk_state.json"))
                task_state = prepare_task_chunk_state(ctx, task_id=spec.task_id, chunks=[chunk])
                summary = DownloadSummary(
                    listed_items=1,
                    detail_candidates=1,
                    typed_errors=[],
                )
                setattr(summary, field_name, 1)

                result = execute_split_task(
                    spec=spec,
                    args=SimpleNamespace(split_mode="fast", resume=True),
                    logger=logging.getLogger("download_split_modules_test"),
                    output_root=tmp_dir,
                    chunks=[chunk],
                    candidate_entries=[],
                    resolved_basis=SplitPlanResolvedBasis(
                        date_fields=("listing_date",),
                        unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                    ),
                    build_downloader=lambda *args, **kwargs: object(),
                    run_downloader_with_prefetched=lambda *args, _summary=summary, **kwargs: _summary,
                    task_chunk_state=task_state,
                    chunk_state_ctx=ctx,
                )

                self.assertTrue(result.any_failure)
                self.assertEqual(len(result.typed_errors), 1)
                expected_code = (
                    "sse_unaccounted_download_candidates"
                    if field_name in {"list_unaccounted", "detail_unaccounted"}
                    else "sse_summary_failure_count"
                )
                self.assertEqual(result.typed_errors[0].error_code, expected_code)
                self.assertIn(f"{field_name}=1", result.typed_errors[0].raw_reason)
                self.assertEqual(result.totals[totals_key], 1)
                self.assertEqual(get_chunk_state(task_state, chunk)["status"], "failed")

    def test_split_task_rejects_duplicate_download_target_counts(self) -> None:
        from peap.download_models import SPLIT_PLAN_UNRESOLVED_POLICY_SKIP, SplitPlanResolvedBasis

        with tempfile.TemporaryDirectory() as tmp_dir:
            spec = build_task_registry()["sse:listing:physical_asset"]
            chunk = DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=1,
            )
            ctx = load_chunk_state(os.path.join(tmp_dir, "chunk_state.json"))
            task_state = prepare_task_chunk_state(ctx, task_id=spec.task_id, chunks=[chunk])
            summary = DownloadSummary(saved=2, typed_errors=[])
            summary.downloaded_this_run = {"sse__listing__physical_asset/2026年7月/P001.html"}

            result = execute_split_task(
                spec=spec,
                args=SimpleNamespace(split_mode="fast", resume=True),
                logger=logging.getLogger("download_split_modules_test"),
                output_root=tmp_dir,
                chunks=[chunk],
                candidate_entries=[],
                resolved_basis=SplitPlanResolvedBasis(
                    date_fields=("listing_date",),
                    unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
                ),
                build_downloader=lambda *args, **kwargs: object(),
                run_downloader_with_prefetched=lambda *args, **kwargs: summary,
                task_chunk_state=task_state,
                chunk_state_ctx=ctx,
            )

            self.assertTrue(result.any_failure)
            self.assertEqual(len(result.typed_errors), 1)
            self.assertEqual(result.typed_errors[0].error_code, "sse_duplicate_download_target")
            self.assertIn("saved=2 unique_download_targets=1", result.typed_errors[0].raw_reason)
            self.assertEqual(get_chunk_state(task_state, chunk)["status"], "failed")


def _chunks_signature(chunks: list[DateChunk]) -> str:
    parts = [f"{chunk.start_str}..{chunk.end_str}:{int(chunk.estimated_candidates)}" for chunk in chunks]
    return "|".join(parts)
