"""Task-level downloader orchestration helpers."""

from __future__ import annotations

from typing import Any, Callable

from .download_chunk_state import (
    load_chunk_state,
    prepare_task_chunk_state,
    resolve_chunk_state_path,
    save_chunk_state,
)
from .download_execution import execute_split_task
from .download_models import ChunkStateContext, DateChunk, DownloadTaskRunResult, TaskSplitPlan
from .download_reporting import (
    accumulate,
    build_task_result,
    new_totals,
    print_summary,
    summary_to_dict,
    totals_to_summary_dict,
)
from .download_split_planning import load_split_plan_file, plan_auto_split_chunks
from .download_tasks import DownloadTaskSpec


class DownloadTaskFlowError(RuntimeError):
    """Raised when task-flow setup fails before task execution starts."""


def load_requested_split_plans(args: object, *, logger) -> dict[str, TaskSplitPlan]:
    if not getattr(args, "split_use_plan", False):
        return {}
    try:
        return load_split_plan_file(str(args.split_plan_file))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load split plan file: {args.split_plan_file} ({exc})")
        logger.exception("Failed to load split plan file: %s", args.split_plan_file)
        raise DownloadTaskFlowError(str(exc)) from exc


def prepare_chunk_state_context(
    args: object,
    *,
    logger,
    default_dir: str,
) -> ChunkStateContext | None:
    if not getattr(args, "auto_split", False) or getattr(args, "split_plan_only", False):
        return None
    try:
        chunk_state_path = resolve_chunk_state_path(
            args,
            default_dir=default_dir,
        )
        ctx = load_chunk_state(chunk_state_path)
        save_chunk_state(ctx)
        print(f"Chunk state file: {ctx.path}")
        logger.info("Chunk state file: %s", ctx.path)
        return ctx
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load chunk state file: {exc}")
        logger.exception("Failed to load chunk state file")
        raise DownloadTaskFlowError(str(exc)) from exc


def _print_task_chunks(spec: DownloadTaskSpec, chunks: list[DateChunk], *, logger) -> None:
    print(f"[{spec.task_id}] Auto-split chunks={len(chunks)}")
    logger.info("[%s] Auto-split chunks=%s", spec.task_id, len(chunks))
    for idx, chunk in enumerate(chunks, start=1):
        print(
            f"[{spec.task_id}]   chunk {idx}/{len(chunks)} "
            f"{chunk.start_str}..{chunk.end_str} estimated_candidates={chunk.estimated_candidates}"
        )
        logger.info(
            "[%s]   chunk %s/%s %s..%s estimated_candidates=%s",
            spec.task_id,
            idx,
            len(chunks),
            chunk.start_str,
            chunk.end_str,
            chunk.estimated_candidates,
        )


def _resolve_split_task_plan(
    spec: DownloadTaskSpec,
    *,
    args: object,
    logger,
    output_root: str,
    loaded_plan_map: dict[str, TaskSplitPlan],
    build_downloader: Callable[..., object],
    run_downloader: Callable[..., Any],
    parse_date_arg: Callable[[str | None, str], Any],
) -> tuple[list[DateChunk], list[dict[str, object]], TaskSplitPlan | None, bool, list[str]]:
    if getattr(args, "split_use_plan", False):
        task_plan = loaded_plan_map.get(spec.task_id)
        chunks = task_plan.chunks if task_plan else []
        candidate_entries = task_plan.candidate_entries if task_plan else []
        if chunks:
            return chunks, candidate_entries, None, False, []

        print(
            f"[{spec.task_id}] No split chunks found in plan file, fallback to direct date range."
        )
        logger.warning(
            "[%s] No split chunks found in plan file, fallback to direct date range.",
            spec.task_id,
        )
        start = parse_date_arg(getattr(args, "start_date", None), "start-date")
        end = parse_date_arg(getattr(args, "end_date", None), "end-date")
        if start is None or end is None:
            print(f"[{spec.task_id}] Missing start/end date and no split plan available.")
            logger.error(
                "[%s] Missing start/end date and no split plan available.",
                spec.task_id,
            )
            return [], [], None, True, [f"{spec.task_id}: missing-split-plan-and-date-range"]
        return [DateChunk(start=start, end=end, estimated_candidates=1)], [], None, False, []

    try:
        chunks, candidate_entries = plan_auto_split_chunks(
            spec=spec,
            args=args,
            output_root=output_root,
            logger=logger,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            parse_date_arg=parse_date_arg,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[{spec.task_id}] Split planning failed: {exc}")
        logger.exception("[%s] Split planning failed", spec.task_id)
        return [], [], None, True, [f"{spec.task_id}: split-plan-failed: {exc}"]
    generated_plan = TaskSplitPlan(
        chunks=chunks,
        candidate_entries=candidate_entries,
    )
    return chunks, candidate_entries, generated_plan, False, []


def run_download_task(
    spec: DownloadTaskSpec,
    *,
    args: object,
    logger,
    output_root: str,
    loaded_plan_map: dict[str, TaskSplitPlan],
    chunk_state_ctx: ChunkStateContext | None,
    build_downloader: Callable[..., object],
    run_downloader: Callable[..., Any],
    run_downloader_with_prefetched: Callable[..., Any],
    parse_date_arg: Callable[[str | None, str], Any],
) -> DownloadTaskRunResult:
    task_header = f"=== Running downloader: {spec.task_id} ({spec.display_name}) ==="
    print(task_header)
    logger.info(task_header)

    task_totals = new_totals()
    task_errors: list[str] = []
    any_failure = False

    if getattr(args, "auto_split", False):
        chunks, candidate_entries, generated_plan, any_failure, setup_errors = _resolve_split_task_plan(
            spec,
            args=args,
            logger=logger,
            output_root=output_root,
            loaded_plan_map=loaded_plan_map,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            parse_date_arg=parse_date_arg,
        )
        task_errors.extend(setup_errors)
        if any_failure:
            return DownloadTaskRunResult(
                any_failure=True,
                totals=task_totals,
                errors=task_errors,
                generated_plan=generated_plan,
            )

        _print_task_chunks(spec, chunks, logger=logger)

        if getattr(args, "split_plan_only", False):
            return DownloadTaskRunResult(
                any_failure=False,
                totals=task_totals,
                errors=task_errors,
                task_result=build_task_result(
                    display_name=spec.display_name,
                    summary=totals_to_summary_dict(task_totals, task_errors),
                    errors=task_errors,
                    chunk_count=len(chunks),
                ),
                generated_plan=generated_plan,
            )

        task_chunk_state: dict[str, object] | None = None
        if chunk_state_ctx is not None:
            task_chunk_state = prepare_task_chunk_state(
                chunk_state_ctx,
                task_id=spec.task_id,
                chunks=chunks,
            )
            save_chunk_state(chunk_state_ctx)

        any_failure = execute_split_task(
            spec=spec,
            args=args,
            logger=logger,
            output_root=output_root,
            chunks=chunks,
            candidate_entries=candidate_entries,
            task_totals=task_totals,
            task_errors=task_errors,
            any_failure=any_failure,
            build_downloader=build_downloader,
            run_downloader_with_prefetched=run_downloader_with_prefetched,
            task_chunk_state=task_chunk_state,
            chunk_state_ctx=chunk_state_ctx,
        )
        return DownloadTaskRunResult(
            any_failure=any_failure,
            totals=task_totals,
            errors=task_errors,
            task_result=build_task_result(
                display_name=spec.display_name,
                summary=totals_to_summary_dict(task_totals, task_errors),
                errors=task_errors,
                chunk_count=len(chunks),
            ),
            generated_plan=generated_plan,
        )

    try:
        downloader = build_downloader(spec, args=args, output_root=output_root, logger=logger)
        summary = run_downloader(
            downloader,
            start_date=getattr(args, "start_date", None),
            end_date=getattr(args, "end_date", None),
            list_only=False,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{spec.task_id}: unexpected-error: {exc}"
        print(f"[{spec.task_id}] Unexpected failure: {exc}")
        logger.exception("[%s] Unexpected failure", spec.task_id)
        return DownloadTaskRunResult(
            any_failure=True,
            totals=task_totals,
            errors=[error],
        )

    print_summary(
        prefix=f"[{spec.task_id}] Download summary:",
        summary=summary,
        logger=logger,
    )
    accumulate(summary, task_totals, task_errors)
    any_failure = bool(summary.errors)
    return DownloadTaskRunResult(
        any_failure=any_failure,
        totals=task_totals,
        errors=task_errors,
        task_result=build_task_result(
            display_name=spec.display_name,
            summary=summary_to_dict(summary),
            errors=task_errors,
        ),
    )
