"""Task-level downloader orchestration helpers."""

from __future__ import annotations

from typing import Any, Callable

from .download_chunk_state import (
    load_chunk_state,
    prepare_task_chunk_state,
    resolve_chunk_state_path,
    save_chunk_state,
)
from .download_errors import DownloadError, collect_failed_error, execute_failed_error
from .download_execution import execute_split_task
from .download_models import (
    SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
    ChunkStateContext,
    DateChunk,
    DownloadCollectResult,
    DownloadTaskRunResult,
    SplitPlanResolvedBasis,
    TaskSplitPlan,
    TaskTypedErrorList,
)
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
        scope = {
            "source_id": getattr(args, "exchange", None),
            "project_type": getattr(args, "project_type", None),
            "start_date": getattr(args, "start_date", None),
            "end_date": getattr(args, "end_date", None),
            "split_candidates": int(getattr(args, "split_candidates", 0)),
            "split_min_days": int(getattr(args, "split_min_days", 0)),
            "split_max_depth": int(getattr(args, "split_max_depth", 0)),
            "split_mode": str(getattr(args, "split_mode", "")),
        }
        return load_split_plan_file(str(args.split_plan_file), scope=scope)
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
) -> DownloadCollectResult:
    if getattr(args, "split_use_plan", False):
        task_plan = loaded_plan_map.get(spec.task_id)
        chunks = task_plan.chunks if task_plan else []
        candidate_entries = task_plan.candidate_entries if task_plan else []
        if chunks:
            return DownloadCollectResult(
                chunks=chunks,
                candidate_entries=candidate_entries,
                resolved_basis=task_plan.resolved_basis,
                generated_plan=None,
                any_failure=False,
            )

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
            return DownloadCollectResult(
                chunks=[],
                candidate_entries=[],
                generated_plan=None,
                any_failure=True,
                typed_errors=TaskTypedErrorList(
                    [
                        collect_failed_error(
                            source_id=str(spec.manifest.source_id or spec.exchange_code),
                            task_id=str(spec.task_id),
                            raw_reason="missing-split-plan-and-date-range",
                        )
                    ]
                ),
            )
        return DownloadCollectResult(
            chunks=[DateChunk(start=start, end=end, estimated_candidates=1)],
            candidate_entries=[],
            resolved_basis=SplitPlanResolvedBasis(
                date_fields=(),
                unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
            ),
            generated_plan=None,
            any_failure=False,
        )

    try:
        chunks, candidate_entries, resolved_basis = plan_auto_split_chunks(
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
        return DownloadCollectResult(
            chunks=[],
            candidate_entries=[],
            resolved_basis=None,
            generated_plan=None,
            any_failure=True,
            typed_errors=TaskTypedErrorList(
                [
                    collect_failed_error(
                        source_id=str(spec.manifest.source_id or spec.exchange_code),
                        task_id=str(spec.task_id),
                        raw_reason=f"split-plan-failed: {exc}",
                    )
                ]
            ),
        )
    generated_plan = TaskSplitPlan(
        chunks=chunks,
        candidate_entries=candidate_entries,
        resolved_basis=resolved_basis,
    )
    return DownloadCollectResult(
        chunks=chunks,
        candidate_entries=candidate_entries,
        resolved_basis=resolved_basis,
        generated_plan=generated_plan,
        any_failure=False,
    )


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
    task_typed_errors = TaskTypedErrorList()
    any_failure = False

    if getattr(args, "auto_split", False):
        collect_result = _resolve_split_task_plan(
            spec,
            args=args,
            logger=logger,
            output_root=output_root,
            loaded_plan_map=loaded_plan_map,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            parse_date_arg=parse_date_arg,
        )
        task_typed_errors.extend(collect_result.typed_errors)
        if collect_result.any_failure:
            return DownloadTaskRunResult(
                any_failure=True,
                totals=task_totals,
                typed_errors=task_typed_errors,
                generated_plan=collect_result.generated_plan,
            )

        _print_task_chunks(spec, collect_result.chunks, logger=logger)

        if getattr(args, "split_plan_only", False):
            return DownloadTaskRunResult(
                any_failure=False,
                totals=task_totals,
                typed_errors=task_typed_errors,
                task_result=build_task_result(
                    display_name=spec.display_name,
                    summary=totals_to_summary_dict(task_totals),
                    typed_errors=task_typed_errors,
                    chunk_count=len(collect_result.chunks),
                ),
                generated_plan=collect_result.generated_plan,
            )

        task_chunk_state: dict[str, object] | None = None
        if chunk_state_ctx is not None:
            task_chunk_state = prepare_task_chunk_state(
                chunk_state_ctx,
                task_id=spec.task_id,
                chunks=collect_result.chunks,
            )
            save_chunk_state(chunk_state_ctx)

        materialize_result = execute_split_task(
            spec=spec,
            args=args,
            logger=logger,
            output_root=output_root,
            chunks=collect_result.chunks,
            candidate_entries=collect_result.candidate_entries,
            resolved_basis=collect_result.resolved_basis
            or SplitPlanResolvedBasis(
                date_fields=(),
                unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
            ),
            task_totals=task_totals,
            any_failure=False,
            build_downloader=build_downloader,
            run_downloader_with_prefetched=run_downloader_with_prefetched,
            task_chunk_state=task_chunk_state,
            chunk_state_ctx=chunk_state_ctx,
        )
        return DownloadTaskRunResult(
            any_failure=materialize_result.any_failure,
            totals=materialize_result.totals,
            typed_errors=materialize_result.typed_errors,
            task_result=build_task_result(
                display_name=spec.display_name,
                summary=totals_to_summary_dict(materialize_result.totals),
                typed_errors=materialize_result.typed_errors,
                chunk_count=materialize_result.chunk_count,
                new_downloads=sorted(materialize_result.downloaded_this_run),
            ),
            generated_plan=collect_result.generated_plan,
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
        print(f"[{spec.task_id}] Unexpected failure: {exc}")
        logger.exception("[%s] Unexpected failure", spec.task_id)
        typed_errors = TaskTypedErrorList(
            [
                exc
                if isinstance(exc, DownloadError)
                else execute_failed_error(
                    source_id=str(spec.manifest.source_id or spec.exchange_code),
                    task_id=str(spec.task_id),
                    raw_reason=str(exc),
                )
            ]
        )
        return DownloadTaskRunResult(
            any_failure=True,
            totals=task_totals,
            typed_errors=typed_errors,
        )

    print_summary(
        prefix=f"[{spec.task_id}] Download summary:",
        summary=summary,
        logger=logger,
    )
    accumulate(summary, task_totals, task_typed_errors)
    any_failure = bool(task_typed_errors)
    return DownloadTaskRunResult(
        any_failure=any_failure,
        totals=task_totals,
        typed_errors=task_typed_errors,
        task_result=build_task_result(
            display_name=spec.display_name,
            summary=summary_to_dict(summary),
            typed_errors=task_typed_errors,
            new_downloads=sorted(getattr(summary, "downloaded_this_run", None) or []),
        ),
    )
