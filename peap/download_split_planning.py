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
from .download_tasks import DownloadTaskSpec


def estimate_candidates(summary: object) -> int:
    if hasattr(summary, "detail_candidates"):
        return int(getattr(summary, "detail_candidates", 0) or 0)
    listed_items = int(getattr(summary, "listed_items", 0) or 0)
    skipped_by_list_date = int(getattr(summary, "skipped_by_list_date", 0) or 0)
    skipped_by_resume = int(getattr(summary, "skipped_by_resume", 0) or 0)
    return max(0, listed_items - skipped_by_list_date - skipped_by_resume)


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
    for raw in raw_values:
        if not isinstance(raw, dict):
            continue
        normalized = dict(raw)
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


def load_split_plan_file(path: str) -> dict[str, TaskSplitPlan]:
    payload = load_json_file(path, encoding="utf-8-sig")
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, dict):
        raise ValueError(f"invalid split plan format: {path}")
    parsed: dict[str, TaskSplitPlan] = {}
    for task_id, task_raw in tasks.items():
        if not isinstance(task_id, str):
            continue
        if not isinstance(task_raw, dict):
            raise ValueError(f"split plan task {task_id} missing resolved_basis")
        chunks_raw = task_raw.get("chunks") or []
        candidate_entries_raw = task_raw.get("candidate_entries") or []
        resolved_basis_raw = task_raw.get("resolved_basis")
        if not isinstance(chunks_raw, list):
            continue
        if not isinstance(resolved_basis_raw, dict):
            raise ValueError(f"split plan task {task_id} missing resolved_basis")
        chunks: list[DateChunk] = []
        for chunk_raw in chunks_raw:
            if not isinstance(chunk_raw, dict):
                continue
            chunks.append(DateChunk.from_dict(chunk_raw))
        candidate_entries = [dict(item) for item in candidate_entries_raw if isinstance(item, dict)]
        parsed[task_id] = TaskSplitPlan(
            chunks=chunks,
            candidate_entries=candidate_entries,
            resolved_basis=SplitPlanResolvedBasis.from_dict(resolved_basis_raw),
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
    start = parse_date_arg(getattr(args, "start_date", None), "start-date")
    end = parse_date_arg(getattr(args, "end_date", None), "end-date")
    if start is None or end is None:
        raise ValueError("auto-split requires both --start-date and --end-date")
    if start > end:
        raise ValueError("start-date must be before or equal to end-date")

    runtime = build_downloader(spec, args=args, output_root=output_root, logger=logger)
    summary = run_downloader(runtime, start_date=start.isoformat(), end_date=end.isoformat(), list_only=True)
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
    if candidate_entries:
        estimated_candidates = len(candidate_entries)
    else:
        estimated_candidates = estimate_candidates(summary)
    if estimated_candidates <= int(getattr(args, "split_candidates", 0) or 0):
        return [DateChunk(start=start, end=end, estimated_candidates=estimated_candidates)], candidate_entries, resolved_basis
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
    return chunks, candidate_entries, resolved_basis


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
    "save_split_plan_file",
]
