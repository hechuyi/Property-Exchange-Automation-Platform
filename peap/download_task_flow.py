"""Task-level downloader orchestration helpers."""

from __future__ import annotations

from typing import Any, Callable

from .download_artifact_audit import DownloadArtifactAudit
from .download_chunk_state import (
    load_chunk_state,
    prepare_task_chunk_state,
    resolve_chunk_state_path,
    save_chunk_state,
)
from .download_errors import (
    DownloadError,
    collect_failed_error,
    execute_failed_error,
)
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
    append_synthetic_summary_failure_error,
    build_task_result,
    new_totals,
    print_summary,
    summary_discovery_task_manifest,
    summary_downloaded_this_run,
    summary_duplicate_samples,
    summary_list_page_observations,
    summary_to_dict,
    summary_typed_errors,
    totals_to_summary_dict,
)
from .download_split_planning import load_split_plan_file, plan_auto_split_task
from .download_tasks import DownloadTaskSpec
from .downloaders.discovery_evidence import (
    DiscoveryEvidenceError,
    verify_discovery_candidate_subset,
)


class DownloadTaskFlowError(RuntimeError):
    """Raised when task-flow setup fails before task execution starts."""


def load_requested_split_plans(args: object, *, logger) -> dict[str, TaskSplitPlan]:
    if not getattr(args, "split_use_plan", False):
        return {}
    try:
        scope = {
            "source_id": getattr(args, "exchange", None),
            "record_family": getattr(args, "record_family", None),
            "business_id": getattr(args, "business_id", None),
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
        if task_plan is None:
            raw_reason = f"split-plan-task-missing: {spec.task_id}"
            print(f"[{spec.task_id}] Split plan does not contain current task.")
            logger.error("[%s] Split plan does not contain current task.", spec.task_id)
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
                            raw_reason=raw_reason,
                        )
                    ]
                ),
            )
        if not task_plan.chunks:
            raw_reason = f"split-plan-chunks-missing-or-empty: {spec.task_id}"
            print(f"[{spec.task_id}] Split plan has no chunks for current task.")
            logger.error("[%s] Split plan has no chunks for current task.", spec.task_id)
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
                            raw_reason=raw_reason,
                        )
                    ]
                ),
            )
        try:
            reference = task_plan.discovery_task_manifest
            if spec.record_family == "listing":
                if reference is None:
                    raise DiscoveryEvidenceError(
                        "loaded listing split plan has no discovery task manifest"
                    )
                verify_discovery_candidate_subset(
                    root=output_root,
                    reference=reference,
                    candidate_entries=task_plan.candidate_entries,
                    expected_source_id=str(spec.manifest.source_id or spec.exchange_code),
                    expected_task_id=spec.task_id,
                    expected_run_id=str(reference["run_id"]),
                )
        except (DiscoveryEvidenceError, KeyError, TypeError, ValueError) as exc:
            raw_reason = f"split-plan-discovery-provenance-invalid: {exc}"
            logger.error("[%s] %s", spec.task_id, raw_reason)
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
                            raw_reason=raw_reason,
                        )
                    ]
                ),
            )
        return DownloadCollectResult(
            chunks=task_plan.chunks,
            candidate_entries=task_plan.candidate_entries,
            resolved_basis=task_plan.resolved_basis,
            generated_plan=None,
            discovery_task_manifest=task_plan.discovery_task_manifest,
            any_failure=False,
        )

    try:
        generated_plan = plan_auto_split_task(
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
    return DownloadCollectResult(
        chunks=generated_plan.chunks,
        candidate_entries=generated_plan.candidate_entries,
        resolved_basis=generated_plan.resolved_basis,
        generated_plan=generated_plan,
        discovery_task_manifest=generated_plan.discovery_task_manifest,
        any_failure=False,
    )


def _attach_artifact_audit(
    task_result: dict[str, Any],
    *,
    spec: DownloadTaskSpec,
    artifact_audit: DownloadArtifactAudit | None,
) -> dict[str, Any]:
    task_artifact_audit = artifact_audit.for_task(spec.task_id) if artifact_audit else None
    if task_artifact_audit is not None and task_artifact_audit.stale_count:
        task_result["artifact_audit"] = task_artifact_audit.to_dict()
    return task_result


def _summary_has_explicit_failure_counts(summary: object) -> bool:
    for attr in ("detail_failed", "list_failed", "save_failed", "errors"):
        try:
            if int(getattr(summary, attr, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


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
    artifact_audit: DownloadArtifactAudit | None = None,
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
                    discovery_task_manifest=collect_result.discovery_task_manifest,
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
            artifact_audit=artifact_audit.for_task(spec.task_id) if artifact_audit else None,
        )
        task_result = _attach_artifact_audit(
            build_task_result(
                display_name=spec.display_name,
                summary=totals_to_summary_dict(materialize_result.totals),
                typed_errors=materialize_result.typed_errors,
                chunk_count=materialize_result.chunk_count,
                new_downloads=sorted(materialize_result.downloaded_this_run),
                discovery_task_manifest=collect_result.discovery_task_manifest,
            ),
            spec=spec,
            artifact_audit=artifact_audit,
        )
        return DownloadTaskRunResult(
            any_failure=materialize_result.any_failure,
            totals=materialize_result.totals,
            typed_errors=materialize_result.typed_errors,
            task_result=task_result,
            generated_plan=collect_result.generated_plan,
        )

    try:
        task_artifact_audit = artifact_audit.for_task(spec.task_id) if artifact_audit else None
        downloader_kwargs: dict[str, object] = {}
        if task_artifact_audit is not None and task_artifact_audit.stale_count:
            downloader_kwargs["resume_override"] = False
        downloader = build_downloader(
            spec,
            args=args,
            output_root=output_root,
            logger=logger,
            **downloader_kwargs,
        )
        summary = run_downloader(
            downloader,
            start_date=getattr(args, "start_date", None),
            end_date=getattr(args, "end_date", None),
            list_only=False,
        )
        summary_typed_errors(summary)
        summary_downloaded_this_run(summary)
        discovery_task_manifest = summary_discovery_task_manifest(summary)
        append_synthetic_summary_failure_error(
            summary,
            source_id=str(spec.manifest.source_id or spec.exchange_code),
            task_id=str(spec.task_id),
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
    any_failure = bool(task_typed_errors) or _summary_has_explicit_failure_counts(summary)
    task_result = _attach_artifact_audit(
        build_task_result(
            display_name=spec.display_name,
            summary=summary_to_dict(summary, errors=len(task_typed_errors)),
            typed_errors=task_typed_errors,
            new_downloads=sorted(summary_downloaded_this_run(summary)),
            discovery_task_manifest=discovery_task_manifest or None,
            list_page_observations=summary_list_page_observations(summary),
            duplicate_samples=summary_duplicate_samples(summary),
        ),
        spec=spec,
        artifact_audit=artifact_audit,
    )
    return DownloadTaskRunResult(
        any_failure=any_failure,
        totals=task_totals,
        typed_errors=task_typed_errors,
        task_result=task_result,
    )
