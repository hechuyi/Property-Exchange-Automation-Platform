"""Downloader split-plan helpers."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable

from peap_core.runtime import load_json_file, write_json_file_atomic

from .download_models import (
    SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
    DateChunk,
    SplitPlanResolvedBasis,
    TaskSplitPlan,
)
from .download_reporting import (
    summary_discovery_task_manifest,
    validate_discovery_task_manifest_reference,
)
from .download_tasks import DownloadTaskSpec
from .downloaders.discovery_evidence import verify_discovery_candidate_subset


def estimate_candidates(summary: object) -> int:
    if hasattr(summary, "detail_candidates"):
        return int(getattr(summary, "detail_candidates", 0) or 0)
    listed_items = int(getattr(summary, "listed_items", 0) or 0)
    skipped_by_list_date = int(getattr(summary, "skipped_by_list_date", 0) or 0)
    skipped_by_resume = int(getattr(summary, "skipped_by_resume", 0) or 0)
    skipped_by_duplicate = int(getattr(summary, "skipped_by_duplicate", 0) or 0)
    skipped_by_business_filter = int(getattr(summary, "skipped_by_business_filter", 0) or 0)
    return max(
        0,
        listed_items
        - skipped_by_list_date
        - skipped_by_resume
        - skipped_by_duplicate
        - skipped_by_business_filter,
    )


def _split_chunk(start: dt.date, end: dt.date) -> list[DateChunk] | None:
    days = (end - start).days + 1
    if days < 2:
        return None
    left_days = days // 2
    left_end = start + dt.timedelta(days=left_days - 1)
    right_start = left_end + dt.timedelta(days=1)
    return [
        DateChunk(start=start, end=left_end, estimated_candidates=0),
        DateChunk(start=right_start, end=end, estimated_candidates=0),
    ]


def candidate_date_fields(spec: DownloadTaskSpec) -> tuple[str, ...]:
    return tuple(
        str(value)
        for value in getattr(spec.manifest, "date_field_candidates", ())
        if str(value)
    )


def entry_date(
    entry: dict[str, object],
    *,
    date_fields: tuple[str, ...],
) -> dt.date | None:
    for key in date_fields:
        raw = entry.get(key)
        if raw in (None, ""):
            continue
        try:
            return dt.datetime.strptime(str(raw), "%Y-%m-%d").date()
        except Exception:
            continue
    return None


def _check_summary_for_typed_errors(summary: object) -> None:
    """Raise an explicit error if the list-stage summary contains typed errors.

    Split planning must not produce chunks when the list-stage scan itself
    failed with typed errors.
    """
    typed_errors = getattr(summary, "typed_errors", None)
    if isinstance(typed_errors, list) and typed_errors:
        first_error = typed_errors[0]
        error_msg = str(getattr(first_error, "error_message", first_error))
        raise ValueError(f"list-stage scan failed with typed errors: {error_msg}")


def extract_candidate_entries(
    summary: object,
    *,
    start: dt.date,
    end: dt.date,
    date_fields: tuple[str, ...],
    unresolved_candidate_policy: str,
) -> list[dict[str, object]]:
    raw_values = getattr(summary, "candidate_entries", None)
    if not isinstance(raw_values, list):
        return []

    if unresolved_candidate_policy != SPLIT_PLAN_UNRESOLVED_POLICY_SKIP:
        raise ValueError(
            f"unsupported unresolved candidate policy: {unresolved_candidate_policy}"
        )

    values: list[dict[str, object]] = []
    for candidate_index, raw in enumerate(raw_values):
        if not isinstance(raw, dict):
            continue
        normalized = dict(raw)
        for key in date_fields:
            raw_date = normalized.get(key)
            if raw_date in (None, ""):
                continue
            try:
                dt.datetime.strptime(str(raw_date), "%Y-%m-%d").date()
            except Exception as exc:
                raise ValueError(
                    f"candidate_entries[{candidate_index}].{key} must be YYYY-MM-DD"
                ) from exc
        item_date = entry_date(normalized, date_fields=date_fields)
        if item_date is None:
            continue
        if item_date < start or item_date > end:
            continue
        values.append(normalized)
    return values


def extract_candidate_dates(
    summary: object,
    *,
    start: dt.date,
    end: dt.date,
    date_fields: tuple[str, ...],
    unresolved_candidate_policy: str,
) -> list[dt.date]:
    raw_candidate_entries = getattr(summary, "candidate_entries", None)
    if isinstance(raw_candidate_entries, list):
        entry_values: list[dt.date] = []
        for entry in extract_candidate_entries(
            summary,
            start=start,
            end=end,
            date_fields=date_fields,
            unresolved_candidate_policy=unresolved_candidate_policy,
        ):
            item_date = entry_date(entry, date_fields=date_fields)
            if item_date is not None:
                entry_values.append(item_date)
        return entry_values

    raw_values = getattr(summary, "candidate_dates", None)
    if not isinstance(raw_values, list):
        return []
    values: list[dt.date] = []
    for raw in raw_values:
        try:
            item_date = dt.datetime.strptime(str(raw), "%Y-%m-%d").date()
        except Exception:
            continue
        if start <= item_date <= end:
            values.append(item_date)
    return values


def build_chunks_from_dates(
    *,
    task_id: str,
    start: dt.date,
    end: dt.date,
    dates: list[dt.date],
    split_candidates: int,
    min_days: int,
    max_depth: int,
    logger: logging.Logger,
) -> list[DateChunk]:
    def rec(
        cur_start: dt.date,
        cur_end: dt.date,
        cur_dates: list[dt.date],
        depth: int,
    ) -> list[DateChunk]:
        count = len(cur_dates)
        days = (cur_end - cur_start).days + 1
        chunk = DateChunk(start=cur_start, end=cur_end, estimated_candidates=count)

        should_split = count > split_candidates and days > min_days and depth < max_depth
        if not should_split:
            logger.info(
                "Auto-split plan: %s %s..%s estimated=%s keep",
                task_id,
                chunk.start_str,
                chunk.end_str,
                count,
            )
            return [chunk]

        children = _split_chunk(cur_start, cur_end)
        if not children:
            logger.info(
                "Auto-split plan: %s %s..%s estimated=%s keep",
                task_id,
                chunk.start_str,
                chunk.end_str,
                count,
            )
            return [chunk]

        left, right = children
        left_dates = [item_date for item_date in cur_dates if item_date <= left.end]
        right_dates = [item_date for item_date in cur_dates if item_date >= right.start]
        logger.info(
            "Auto-split plan: %s %s..%s estimated=%s -> split",
            task_id,
            chunk.start_str,
            chunk.end_str,
            count,
        )
        return rec(left.start, left.end, left_dates, depth + 1) + rec(
            right.start,
            right.end,
            right_dates,
            depth + 1,
        )

    return rec(start, end, dates, 0)


def assign_entries_to_chunks(
    *,
    chunks: list[DateChunk],
    candidate_entries: list[dict[str, object]],
    date_fields: tuple[str, ...],
    unresolved_candidate_policy: str,
) -> list[list[dict[str, object]]]:
    if not chunks:
        return []
    if unresolved_candidate_policy != SPLIT_PLAN_UNRESOLVED_POLICY_SKIP:
        raise ValueError(
            f"unsupported unresolved candidate policy: {unresolved_candidate_policy}"
        )
    assigned: list[list[dict[str, object]]] = [[] for _ in chunks]
    sorted_chunks = sorted(enumerate(chunks), key=lambda item: item[1].start)
    for entry in candidate_entries:
        item_date = entry_date(entry, date_fields=date_fields)
        if item_date is None:
            continue

        target_idx: int | None = None
        for idx, chunk in sorted_chunks:
            if chunk.start <= item_date <= chunk.end:
                target_idx = idx
                break
        if target_idx is None:
            if item_date < sorted_chunks[0][1].start:
                target_idx = sorted_chunks[0][0]
            else:
                target_idx = sorted_chunks[-1][0]
        assigned[target_idx].append(entry)
    return assigned


def load_split_plan_file(path: str, *, scope: dict[str, object] | None = None) -> dict[str, TaskSplitPlan]:
    payload = load_json_file(path, encoding="utf-8-sig")
    if not isinstance(payload, dict):
        raise ValueError(f"invalid split plan format: {path}")
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        raise ValueError(f"invalid split plan format: {path}")
    # Validate scope if provided
    if scope is not None:
        saved_scope = payload.get("scope")
        if not isinstance(saved_scope, dict):
            raise ValueError(f"split plan scope is missing from: {path}")
        saved_scope_clean = {k: v for k, v in saved_scope.items() if k in scope}
        if saved_scope_clean != scope:
            raise ValueError(f"split plan scope mismatch: saved={saved_scope_clean} != requested={scope}")
    parsed: dict[str, TaskSplitPlan] = {}
    for task_id, task_raw in tasks.items():
        if not isinstance(task_id, str):
            continue
        if not isinstance(task_raw, dict):
            raise ValueError(f"split plan task {task_id} missing resolved_basis")
        if "chunks" not in task_raw:
            raise ValueError(f"split plan task {task_id} chunks are required")
        chunks_raw = task_raw.get("chunks")
        candidate_entries_raw = task_raw.get("candidate_entries", [])
        resolved_basis_raw = task_raw.get("resolved_basis")
        discovery_task_manifest_raw = task_raw.get("discovery_task_manifest")
        if not isinstance(chunks_raw, list):
            raise ValueError(f"split plan task {task_id} chunks must be a list")
        if not chunks_raw:
            raise ValueError(f"split plan task {task_id} chunks must be a non-empty list")
        if not isinstance(candidate_entries_raw, list):
            raise ValueError(f"split plan task {task_id} candidate_entries must be a list")
        if not isinstance(resolved_basis_raw, dict):
            raise ValueError(f"split plan task {task_id} missing resolved_basis")
        chunks: list[DateChunk] = []
        for chunk_index, chunk_raw in enumerate(chunks_raw):
            if not isinstance(chunk_raw, dict):
                raise ValueError(f"split plan task {task_id} chunks[{chunk_index}] must be an object")
            chunk = DateChunk.from_dict(chunk_raw)
            if chunk.start > chunk.end:
                raise ValueError(f"split plan task {task_id} chunks[{chunk_index}] start is after end")
            chunks.append(chunk)
        candidate_entries: list[dict[str, object]] = []
        for candidate_index, item in enumerate(candidate_entries_raw):
            if not isinstance(item, dict):
                raise ValueError(
                    f"split plan task {task_id} candidate_entries[{candidate_index}] must be an object"
                )
            candidate_entries.append(dict(item))
        parsed[task_id] = TaskSplitPlan(
            chunks=chunks,
            candidate_entries=candidate_entries,
            resolved_basis=SplitPlanResolvedBasis.from_dict(resolved_basis_raw),
            discovery_task_manifest=(
                None
                if discovery_task_manifest_raw is None
                else validate_discovery_task_manifest_reference(
                    discovery_task_manifest_raw,
                    name=f"split plan task {task_id} discovery_task_manifest",
                )
            ),
        )
    return parsed


def save_split_plan_file(
    path: str,
    *,
    tasks_to_plan: dict[str, TaskSplitPlan],
    scope: dict[str, object],
) -> None:
    payload = {
        "version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "scope": dict(scope),
        "tasks": {
            task_id: {
                "chunks": [chunk.to_dict() for chunk in plan.chunks],
                "candidate_entries": plan.candidate_entries,
                "resolved_basis": plan.resolved_basis.to_dict(),
                "discovery_task_manifest": plan.discovery_task_manifest,
            }
            for task_id, plan in tasks_to_plan.items()
        },
    }
    write_json_file_atomic(
        path,
        payload,
        encoding="utf-8",
        ensure_ascii=False,
        sort_keys=False,
    )


def plan_auto_split_task(
    *,
    spec: DownloadTaskSpec,
    args: object,
    output_root: str,
    logger: logging.Logger,
    build_downloader: Callable[..., object],
    run_downloader: Callable[..., Any],
    parse_date_arg: Callable[[str | None, str], dt.date | None],
) -> TaskSplitPlan:
    start = parse_date_arg(getattr(args, "start_date", None), "start-date")
    end = parse_date_arg(getattr(args, "end_date", None), "end-date")
    if start is None or end is None:
        raise ValueError("auto-split requires both --start-date and --end-date")
    if start > end:
        raise ValueError("start-date must be before or equal to end-date")

    runtime = build_downloader(spec, args=args, output_root=output_root, logger=logger)
    summary = run_downloader(runtime, start_date=start.isoformat(), end_date=end.isoformat(), list_only=True)
    _check_summary_for_typed_errors(summary)
    discovery_task_manifest = summary_discovery_task_manifest(summary) or None
    date_fields = candidate_date_fields(spec)
    resolved_basis = SplitPlanResolvedBasis(
        date_fields=date_fields,
        unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
    )
    dates = extract_candidate_dates(
        summary,
        start=start,
        end=end,
        date_fields=resolved_basis.date_fields,
        unresolved_candidate_policy=resolved_basis.unresolved_candidate_policy,
    )
    candidate_entries = extract_candidate_entries(
        summary,
        start=start,
        end=end,
        date_fields=resolved_basis.date_fields,
        unresolved_candidate_policy=resolved_basis.unresolved_candidate_policy,
    )
    if spec.record_family == "listing":
        if discovery_task_manifest is None:
            raise ValueError("listing split plan requires a discovery task manifest")
        verify_discovery_candidate_subset(
            root=output_root,
            reference=discovery_task_manifest,
            candidate_entries=candidate_entries,
            expected_source_id=str(spec.manifest.source_id or spec.exchange_code),
            expected_task_id=spec.task_id,
            expected_run_id=str(discovery_task_manifest["run_id"]),
        )
    if candidate_entries:
        estimated_candidates = len(candidate_entries)
    else:
        estimated_candidates = estimate_candidates(summary)
    if estimated_candidates <= int(getattr(args, "split_candidates", 0) or 0):
        chunks = [DateChunk(start=start, end=end, estimated_candidates=estimated_candidates)]
    else:
        chunks = build_chunks_from_dates(
            task_id=spec.task_id,
            start=start,
            end=end,
            dates=dates,
            split_candidates=int(getattr(args, "split_candidates", 0) or 0),
            min_days=int(getattr(args, "split_min_days", 0) or 0),
            max_depth=int(getattr(args, "split_max_depth", 0) or 0),
            logger=logger,
        )
    return TaskSplitPlan(
        chunks=chunks,
        candidate_entries=candidate_entries,
        resolved_basis=resolved_basis,
        discovery_task_manifest=discovery_task_manifest,
    )


def plan_auto_split_chunks(
    *,
    spec: DownloadTaskSpec,
    args: object,
    output_root: str,
    logger: logging.Logger,
    build_downloader: Callable[..., object],
    run_downloader: Callable[..., Any],
    parse_date_arg: Callable[[str | None, str], dt.date | None],
) -> tuple[list[DateChunk], list[dict[str, object]], SplitPlanResolvedBasis]:
    plan = plan_auto_split_task(
        spec=spec,
        args=args,
        output_root=output_root,
        logger=logger,
        build_downloader=build_downloader,
        run_downloader=run_downloader,
        parse_date_arg=parse_date_arg,
    )
    return plan.chunks, plan.candidate_entries, plan.resolved_basis


__all__ = [
    "assign_entries_to_chunks",
    "build_chunks_from_dates",
    "candidate_date_fields",
    "entry_date",
    "estimate_candidates",
    "extract_candidate_dates",
    "extract_candidate_entries",
    "load_split_plan_file",
    "plan_auto_split_chunks",
    "plan_auto_split_task",
    "save_split_plan_file",
]
