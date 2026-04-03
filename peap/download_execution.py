"""Downloader execution helpers for auto-split chunk runs."""

from __future__ import annotations

from typing import Any, Callable

from .download_errors import DownloadError, execute_failed_error

from .download_chunk_state import get_chunk_state, save_chunk_state, update_chunk_state
from .download_models import (
    ChunkStateContext,
    DateChunk,
    DownloadMaterializeResult,
    SplitPlanResolvedBasis,
    TaskTypedErrorList,
)
from .download_reporting import accumulate, new_totals, print_summary
from .download_split_planning import assign_entries_to_chunks
from .download_tasks import DownloadTaskSpec


def execute_split_task(
    *,
    spec: DownloadTaskSpec,
    args: object,
    logger,
    output_root: str,
    chunks: list[DateChunk],
    candidate_entries: list[dict[str, object]],
    resolved_basis: SplitPlanResolvedBasis,
    task_totals: dict[str, int] | None = None,
    any_failure: bool = False,
    build_downloader: Callable[..., object],
    run_downloader_with_prefetched: Callable[..., Any],
    task_chunk_state: dict[str, object] | None,
    chunk_state_ctx: ChunkStateContext | None,
) -> DownloadMaterializeResult:
    task_totals = task_totals if task_totals is not None else new_totals()
    task_typed_errors = TaskTypedErrorList()
    task_downloaded_this_run: set[str] = set()
    chunk_prefetched = assign_entries_to_chunks(
        chunks=chunks,
        candidate_entries=candidate_entries,
        date_fields=resolved_basis.date_fields,
        unresolved_candidate_policy=resolved_basis.unresolved_candidate_policy,
    )
    use_prefetched = any(len(x) > 0 for x in chunk_prefetched)
    if use_prefetched:
        print(
            f"[{spec.task_id}] Use cached candidate list for chunk execution "
            f"(entries={len(candidate_entries)})"
        )
        logger.info(
            "[%s] Use cached candidate list for chunk execution (entries=%s)",
            spec.task_id,
            len(candidate_entries),
        )
    elif candidate_entries:
        print(
            f"[{spec.task_id}] Candidate cache exists but no chunk assignment, fallback to list scan."
        )
        logger.warning(
            "[%s] Candidate cache exists but no chunk assignment, fallback to list scan.",
            spec.task_id,
        )

    skip_zero_chunks = args.split_mode == "fast"

    for idx, chunk in enumerate(chunks, start=1):
        chunk_state = get_chunk_state(task_chunk_state, chunk) if task_chunk_state else None
        chunk_status = (
            str(chunk_state.get("status") or "pending").strip().lower()
            if chunk_state is not None
            else "pending"
        )
        if chunk_state is not None and chunk_status == "done":
            print(
                f"[{spec.task_id}] Skip chunk {idx}/{len(chunks)} "
                f"{chunk.start_str}..{chunk.end_str} status=done"
            )
            logger.info(
                "[%s] Skip chunk %s/%s %s..%s status=done",
                spec.task_id,
                idx,
                len(chunks),
                chunk.start_str,
                chunk.end_str,
            )
            continue
        if skip_zero_chunks and chunk.estimated_candidates <= 0:
            print(
                f"[{spec.task_id}] Skip chunk {idx}/{len(chunks)} "
                f"{chunk.start_str}..{chunk.end_str} estimated_candidates=0 mode=fast"
            )
            logger.info(
                "[%s] Skip chunk %s/%s %s..%s estimated_candidates=0 mode=fast",
                spec.task_id,
                idx,
                len(chunks),
                chunk.start_str,
                chunk.end_str,
            )
            if task_chunk_state is not None and chunk_state_ctx is not None:
                update_chunk_state(task_chunk_state, chunk, status="done")
                save_chunk_state(chunk_state_ctx)
            continue
        if task_chunk_state is not None and chunk_state_ctx is not None:
            update_chunk_state(
                task_chunk_state,
                chunk,
                status="running",
                increment_attempts=True,
            )
            save_chunk_state(chunk_state_ctx)
        try:
            force_chunk_rerun = task_chunk_state is not None
            if force_chunk_rerun and args.resume:
                logger.info(
                    "[%s] Chunk %s/%s runs with resume disabled due to chunk-state recovery priority",
                    spec.task_id,
                    idx,
                    len(chunks),
                )
            downloader = build_downloader(
                spec,
                args=args,
                output_root=output_root,
                logger=logger,
                resume_override=False if force_chunk_rerun else None,
            )
            prefetched_for_chunk: list[dict[str, object]] | None = None
            if use_prefetched and idx - 1 < len(chunk_prefetched):
                prefetched_for_chunk = chunk_prefetched[idx - 1]
            summary = run_downloader_with_prefetched(
                downloader,
                start_date=chunk.start_str,
                end_date=chunk.end_str,
                list_only=False,
                prefetched_candidates=prefetched_for_chunk,
            )
        except Exception as exc:  # noqa: BLE001
            any_failure = True
            typed_error = (
                exc
                if isinstance(exc, DownloadError)
                else execute_failed_error(
                    source_id=str(spec.manifest.source_id or spec.exchange_code),
                    task_id=str(spec.task_id),
                    raw_reason=f"chunk-{idx}-failed: {exc}",
                )
            )
            task_typed_errors.append(typed_error)
            print(f"[{spec.task_id}] Chunk {idx}/{len(chunks)} failed: {exc}")
            logger.exception("[%s] Chunk %s/%s failed", spec.task_id, idx, len(chunks))
            if task_chunk_state is not None and chunk_state_ctx is not None:
                update_chunk_state(
                    task_chunk_state,
                    chunk,
                    status="failed",
                    error=typed_error.error_message,
                )
                save_chunk_state(chunk_state_ctx)
            continue

        typed_summary_errors = [item for item in list(getattr(summary, "typed_errors", []) or []) if isinstance(item, DownloadError)]
        if typed_summary_errors:
            summary.typed_errors = typed_summary_errors

        print_summary(
            prefix=(
                f"[{spec.task_id}] Download summary "
                f"(chunk {idx}/{len(chunks)} {chunk.start_str}..{chunk.end_str}):"
            ),
            summary=summary,
            logger=logger,
        )
        if task_chunk_state is not None and chunk_state_ctx is not None:
            if typed_summary_errors:
                update_chunk_state(
                    task_chunk_state,
                    chunk,
                    status="failed",
                    error=typed_summary_errors[0].error_message,
                )
            else:
                update_chunk_state(task_chunk_state, chunk, status="done")
            save_chunk_state(chunk_state_ctx)
        accumulate(summary, task_totals, task_typed_errors, task_downloaded_this_run)
        if typed_summary_errors:
            any_failure = True
    return DownloadMaterializeResult(
        any_failure=any_failure,
        totals=task_totals,
        typed_errors=task_typed_errors,
        chunk_count=len(chunks),
        downloaded_this_run=task_downloaded_this_run,
    )
