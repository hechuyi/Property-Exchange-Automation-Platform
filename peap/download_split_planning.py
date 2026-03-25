"""Downloader split-plan helpers."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable

from peap_core.runtime import load_json_file, write_json_file_atomic

from .download_models import DateChunk, TaskSplitPlan
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


def entry_date(entry: dict[str, object]) -> dt.date | None:
    for key in ("list_disclosure_start", "disclosure_date", "date"):
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
) -> list[dict[str, object]]:
    raw_values = getattr(summary, "candidate_entries", None)
    if not isinstance(raw_values, list):
        return []

    values: list[dict[str, object]] = []
    for raw in raw_values:
        if not isinstance(raw, dict):
            continue
        normalized = dict(raw)
        item_date = entry_date(normalized)
        if item_date is not None:
            if item_date < start or item_date > end:
                continue
            normalized["list_disclosure_start"] = item_date.isoformat()
        values.append(normalized)
    return values


def extract_candidate_dates(summary: object, *, start: dt.date, end: dt.date) -> list[dt.date]:
    raw_values = getattr(summary, "candidate_dates", None)
    if not isinstance(raw_values, list):
        raw_values = []
    values: list[dt.date] = []
    for raw in raw_values:
        try:
            item_date = dt.datetime.strptime(str(raw), "%Y-%m-%d").date()
        except Exception:
            continue
        if start <= item_date <= end:
            values.append(item_date)
    if values:
        return values

    for entry in extract_candidate_entries(summary, start=start, end=end):
        item_date = entry_date(entry)
        if item_date is not None:
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
) -> list[list[dict[str, object]]]:
    if not chunks:
        return []
    assigned: list[list[dict[str, object]]] = [[] for _ in chunks]
    sorted_chunks = sorted(enumerate(chunks), key=lambda item: item[1].start)
    for entry in candidate_entries:
        item_date = entry_date(entry)
        if item_date is None:
            assigned[sorted_chunks[0][0]].append(entry)
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
        chunks_raw: object
        candidate_entries_raw: object
        if isinstance(task_raw, list):
            chunks_raw = task_raw
            candidate_entries_raw = []
        elif isinstance(task_raw, dict):
            chunks_raw = task_raw.get("chunks") or []
            candidate_entries_raw = task_raw.get("candidate_entries") or []
        else:
            continue
        if not isinstance(chunks_raw, list):
            continue
        chunks: list[DateChunk] = []
        for chunk_raw in chunks_raw:
            if not isinstance(chunk_raw, dict):
                continue
            chunks.append(DateChunk.from_dict(chunk_raw))
        candidate_entries: list[dict[str, object]] = []
        if isinstance(candidate_entries_raw, list):
            for raw in candidate_entries_raw:
                if isinstance(raw, dict):
                    candidate_entries.append(dict(raw))
        if chunks:
            parsed[task_id] = TaskSplitPlan(chunks=chunks, candidate_entries=candidate_entries)
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
) -> tuple[list[DateChunk], list[dict[str, object]]]:
    start = parse_date_arg(getattr(args, "start_date", None), "start-date")
    end = parse_date_arg(getattr(args, "end_date", None), "end-date")
    if start is None or end is None:
        raise ValueError("--auto-split requires both --start-date and --end-date")
    if start > end:
        raise ValueError(
            f"start-date {getattr(args, 'start_date', None)!r} "
            f"is after end-date {getattr(args, 'end_date', None)!r}"
        )

    min_days = max(1, int(args.split_min_days))
    split_candidates = max(1, int(args.split_candidates))
    max_depth = max(0, int(args.split_max_depth))

    downloader = build_downloader(spec, args=args, output_root=output_root, logger=logger)
    summary = run_downloader(
        downloader,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        list_only=True,
    )
    estimated = estimate_candidates(summary)
    candidate_entries = extract_candidate_entries(summary, start=start, end=end)
    candidate_dates = extract_candidate_dates(summary, start=start, end=end)

    summary_errors = list(getattr(summary, "errors", []) or [])
    if summary_errors:
        logger.warning(
            "Auto-split planning has list errors for %s (count=%s). Keep single chunk for safety.",
            spec.task_id,
            len(summary_errors),
        )
        return [DateChunk(start=start, end=end, estimated_candidates=estimated)], []

    if not candidate_dates:
        logger.info(
            "Auto-split plan: %s %s..%s estimated=%s keep",
            spec.task_id,
            start.isoformat(),
            end.isoformat(),
            estimated,
        )
        return [DateChunk(start=start, end=end, estimated_candidates=estimated)], candidate_entries

    chunks = build_chunks_from_dates(
        task_id=spec.task_id,
        start=start,
        end=end,
        dates=sorted(candidate_dates),
        split_candidates=split_candidates,
        min_days=min_days,
        max_depth=max_depth,
        logger=logger,
    )
    return chunks, candidate_entries
