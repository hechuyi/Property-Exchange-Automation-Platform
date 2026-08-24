"""Structured one-click downloader orchestration."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from peap_core.cli_support import close_cli_logger, setup_cli_logger

from .download_errors import DownloadError, collect_failed_error
from .download_oneclick_presenters import (
    build_phase_summary,
    emit_stage,
    format_download_error,
    present_collect_error,
    stage_error_message,
)
from .download_reporting import (
    accumulate,
    classify_terminal_download_summary,
    new_totals,
    summary_downloaded_this_run,
    summary_to_dict,
    summary_typed_errors,
    totals_to_summary_dict,
)
from .download_runner import (
    DownloadRunRequest,
    build_downloader,
    prepare_download_session,
    run_downloader,
    run_downloader_with_prefetched,
    task_progress_label,
)


@dataclass(frozen=True)
class DownloadOneClickRequest:
    download_request: DownloadRunRequest
    plan_file: str = ""
    keep_plan: bool = False
    with_refresh: bool = False
    stage_callback: Callable[[dict[str, Any]], None] | None = None
    existing_project_codes: frozenset[str] = frozenset()
    existing_candidate_tokens: frozenset[str] = frozenset()


@dataclass
class DownloadOneClickStageResult:
    label: str
    exit_code: int
    elapsed_sec: float
    typed_errors: list[DownloadError] = field(default_factory=list)
    summary_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DownloadOneClickRunResult:
    exit_code: int
    log_file: str
    plan_file: str
    plan_file_exists: bool
    plan_file_removed: bool
    start: str
    end: str
    duration_sec: float
    aggregate_summary: dict[str, int]
    task_summaries: dict[str, dict[str, Any]]
    typed_errors: list[DownloadError]
    stages: list[DownloadOneClickStageResult] = field(default_factory=list)


@dataclass(frozen=True)
class _CollectedTask:
    task_id: str
    display_name: str
    task_label: str
    candidate_entries: list[dict[str, object]]
    existing_skipped: int
    summary: dict[str, int]
    typed_errors: list[DownloadError]
    error_items: list[object]
    spec: object


def setup_download_oneclick_logger(
    *,
    verbose: bool,
    log_dir: str,
    log_file: str | None,
    config_obj: object,
) -> tuple[logging.Logger, str]:
    logger, resolved_log_file = setup_cli_logger(
        name="download_oneclick",
        verbose=verbose,
        log_dir=log_dir,
        log_file=log_file,
        default_log_dir=str(config_obj.LOG_DIR),
        file_prefix="download",
        base_level="INFO",
        enable_file_logging=True,
    )
    if resolved_log_file:
        logger.info("Download log file: %s", resolved_log_file)
    return logger, resolved_log_file


def _coerce_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _stage_error_message(summary_payload: dict[str, Any] | None) -> str:
    return stage_error_message(summary_payload)


def _emit_stage(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    phase_code: str,
    status: str,
    label: str,
    summary_payload: dict[str, Any] | None = None,
) -> None:
    emit_stage(
        callback,
        phase_code=phase_code,
        status=status,
        label=label,
        summary_payload=summary_payload,
    )


def _build_phase_summary(
    *,
    totals: dict[str, int],
    task_index: int,
    task_total: int,
    task_label: str,
    phase_percent: int,
    collected_candidates: int = 0,
) -> dict[str, Any]:
    return build_phase_summary(
        totals=totals,
        task_index=task_index,
        task_total=task_total,
        task_label=task_label,
        phase_percent=phase_percent,
        collected_candidates=collected_candidates,
    )


def _date_range_from_request(request: DownloadOneClickRequest) -> tuple[str, str]:
    download_request = request.download_request
    return (
        str(getattr(download_request, "start_date", "") or ""),
        str(getattr(download_request, "end_date", "") or ""),
    )


def _aggregate_collected_task_summaries(collected: list[_CollectedTask]) -> dict[str, Any]:
    totals = new_totals()
    for task in collected:
        summary = _task_summary_dict(task)
        for total_key in totals:
            totals[total_key] += _coerce_int(summary.get(total_key))
    return totals_to_summary_dict(totals)


def _with_terminal_download_classification(
    summary_payload: dict[str, Any],
    *,
    request: DownloadOneClickRequest,
) -> dict[str, Any]:
    if not isinstance(summary_payload, dict):
        raise TypeError("summary_payload must be a dict")
    payload = dict(summary_payload)
    aggregate_summary = _summary_payload_dict_field(payload, "aggregate_summary")
    start_date, end_date = _date_range_from_request(request)
    classification = classify_terminal_download_summary(
        aggregate_summary,
        start_date=start_date,
        end_date=end_date,
    )
    if not classification:
        return payload
    aggregate_summary.update(
        {
            "warning_code": classification["warning_code"],
            "warning_message": classification["warning_message"],
        }
    )
    payload["aggregate_summary"] = aggregate_summary
    payload.setdefault("warning_code", classification["warning_code"])
    payload.setdefault("warning_message", classification["warning_message"])
    payload.setdefault("terminal_label", classification["terminal_label"])
    return payload


def _serialize_typed_errors(errors: list[DownloadError]) -> list[dict[str, object]]:
    return [format_download_error(item) for item in errors]


def _extract_typed_errors(items: list[object]) -> list[DownloadError]:
    return [item for item in items if isinstance(item, DownloadError)]


def _summary_payload_dict_field(summary_payload: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in summary_payload:
        return {}
    value = summary_payload[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"summary_payload.{key} must be a dict")
    return dict(value)


def _task_summary_dict(task: _CollectedTask) -> dict[str, Any]:
    if not isinstance(task.summary, Mapping):
        raise TypeError("task.summary must be a mapping")
    return dict(task.summary)


def _base_error_payload(
    *,
    error_items: list[object],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    typed_errors = _extract_typed_errors(error_items)
    if fallback_payload is None:
        payload: dict[str, Any] = {}
    elif not isinstance(fallback_payload, Mapping):
        raise TypeError("fallback_payload must be a dict")
    else:
        payload = dict(fallback_payload)
    serialized_typed_errors = _serialize_typed_errors(typed_errors)
    payload["errors"] = [item["error_message"] for item in serialized_typed_errors]
    if serialized_typed_errors:
        payload["typed_errors"] = list(serialized_typed_errors)
        if "typed_error" not in payload:
            payload["typed_error"] = serialized_typed_errors[0]
    return payload



def _merge_collect_error_payload(
    *,
    error_items: list[object],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    typed_errors = _extract_typed_errors(error_items)
    if error_items and not typed_errors:
        raise TypeError("prepare_tasks requires typed DownloadError inputs")
    payload = _base_error_payload(error_items=error_items, fallback_payload=fallback_payload)
    classified_payload = present_collect_error(list(typed_errors)) if typed_errors else {}
    for key in ("error_code", "error_message", "error_details", "typed_error"):
        if key in classified_payload and key not in payload:
            payload[key] = classified_payload[key]
    return payload



def _merge_save_pages_error_payload(
    *,
    error_items: list[object],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    typed_errors = _extract_typed_errors(error_items)
    if error_items and not typed_errors:
        raise TypeError("save_pages requires typed DownloadError inputs")
    payload = _base_error_payload(error_items=error_items, fallback_payload=fallback_payload)
    if typed_errors:
        serialized = _serialize_typed_errors(typed_errors)
        first_error = serialized[0]
        payload.setdefault("error_code", first_error.get("error_code", ""))
        payload.setdefault("error_message", first_error.get("error_message", ""))
        error_details = first_error.get("error_details")
        if error_details is None:
            error_details_payload: dict[str, Any] = {}
        elif not isinstance(error_details, dict):
            raise TypeError("typed_error.error_details must be a dict")
        else:
            error_details_payload = dict(error_details)
        payload.setdefault("error_details", error_details_payload)
        payload.setdefault("typed_error", first_error)
    return payload



def _merge_error_payload(
    *,
    phase_code: str,
    error_items: list[object],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if phase_code == "prepare_tasks":
        return _merge_collect_error_payload(error_items=error_items, fallback_payload=fallback_payload)
    if phase_code == "save_pages":
        return _merge_save_pages_error_payload(error_items=error_items, fallback_payload=fallback_payload)
    return _base_error_payload(error_items=error_items, fallback_payload=fallback_payload)


def _upstream_failure_stage_label(typed_errors: list[DownloadError]) -> str:
    if not typed_errors:
        return "当前没有需要下载的网页，无需下载"
    if any(str(item.stage or "") == "prepare_tasks" for item in typed_errors):
        return "部分外站扫描失败，无法确认是否有可下载网页"
    return "部分网页下载失败，请检查失败详情"





def _normalize_scope_component(value: object, *, uppercase: bool = False) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "all":
        return ""
    return text.upper() if uppercase else text.lower()


def _scoped_identity_token(
    kind: str,
    value: object,
    *,
    record_family: str,
    business_id: str,
    source_id: str,
) -> str:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"project_code", "project_id", "page_url"}:
        return ""
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    normalized_value = raw_value.upper() if normalized_kind in {"project_code", "project_id"} else raw_value
    family = _normalize_scope_component(record_family)
    business = _normalize_scope_component(business_id)
    source = _normalize_scope_component(source_id)
    return f"scope:{family}|{business}|{source}|{normalized_kind}:{normalized_value}"


def _filter_existing_candidates(
    candidate_entries: list[dict[str, object]],
    *,
    existing_project_codes: frozenset[str],
    existing_candidate_tokens: frozenset[str],
    record_family: str = "",
    business_id: str = "",
    source_id: str = "",
) -> tuple[list[dict[str, object]], int]:
    if not existing_project_codes and not existing_candidate_tokens:
        return list(candidate_entries), 0
    scoped_mode = bool(
        _normalize_scope_component(record_family)
        or _normalize_scope_component(business_id)
        or _normalize_scope_component(source_id)
    )
    filtered: list[dict[str, object]] = []
    skipped = 0
    for entry in candidate_entries:
        project_code = str(dict(entry).get("project_code") or "").strip().upper()
        project_id = str(dict(entry).get("project_id") or "").strip().upper()
        page_url = str(dict(entry).get("page_url") or "").strip()
        candidate_tokens = set()
        scoped_candidate_tokens = set()
        if project_code:
            candidate_tokens.add(f"project_code:{project_code}")
            scoped_project_code = _scoped_identity_token(
                "project_code",
                project_code,
                record_family=record_family,
                business_id=business_id,
                source_id=source_id,
            )
            if scoped_project_code:
                scoped_candidate_tokens.add(scoped_project_code)
        if project_id:
            candidate_tokens.add(f"project_id:{project_id}")
            scoped_project_id = _scoped_identity_token(
                "project_id",
                project_id,
                record_family=record_family,
                business_id=business_id,
                source_id=source_id,
            )
            if scoped_project_id:
                scoped_candidate_tokens.add(scoped_project_id)
        if page_url:
            candidate_tokens.add(f"page_url:{page_url}")
            scoped_page_url = _scoped_identity_token(
                "page_url",
                page_url,
                record_family=record_family,
                business_id=business_id,
                source_id=source_id,
            )
            if scoped_page_url:
                scoped_candidate_tokens.add(scoped_page_url)
        if scoped_mode:
            scoped_project_key = _scoped_identity_token(
                "project_code",
                project_code,
                record_family=record_family,
                business_id=business_id,
                source_id=source_id,
            )
            scoped_project_code_exists = bool(scoped_project_key and scoped_project_key in existing_project_codes)
            scoped_token_overlap = bool(existing_candidate_tokens and scoped_candidate_tokens & existing_candidate_tokens)
            if scoped_project_code_exists or scoped_token_overlap:
                skipped += 1
                continue

            scoped_candidate_suffixes = {
                token.split("|", 3)[-1] for token in scoped_candidate_tokens if token.startswith("scope:")
            }
            scoped_existing_suffixes = {
                token.split("|", 3)[-1]
                for token in set(existing_project_codes) | set(existing_candidate_tokens)
                if str(token).startswith("scope:")
            }
            has_other_scoped_evidence = bool(scoped_candidate_suffixes & scoped_existing_suffixes)
            legacy_tokens = {token for token in existing_candidate_tokens if not str(token).startswith("scope:")}
            legacy_project_codes = {code for code in existing_project_codes if not str(code).startswith("scope:")}
            project_code_exists = (
                not has_other_scoped_evidence
                and bool(project_code and project_code in legacy_project_codes)
            )
            token_overlap = (
                not has_other_scoped_evidence
                and bool(legacy_tokens and candidate_tokens & legacy_tokens)
            )
        else:
            project_code_exists = bool(project_code and project_code in existing_project_codes)
            token_overlap = bool(existing_candidate_tokens and candidate_tokens & existing_candidate_tokens)
        if project_code_exists or token_overlap:
            skipped += 1
            continue
        filtered.append(dict(entry))
    return filtered, skipped


def _collect_tasks(
    request: DownloadOneClickRequest,
    *,
    logger: logging.Logger,
    config_obj: object,
) -> tuple[list[_CollectedTask], DownloadOneClickStageResult]:
    label = "Stage 1/2: Collect Tasks"
    stage_start = time.monotonic()
    collected: list[_CollectedTask] = []
    totals = new_totals()
    typed_errors: list[DownloadError] = []
    error_items: list[object] = []
    discovered_total = 0

    try:
        prepared = prepare_download_session(
            request.download_request,
            logger=logger,
            config_obj=config_obj,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - stage_start
        typed_errors = [
            exc
            if isinstance(exc, DownloadError)
            else collect_failed_error(
                source_id=str(getattr(request.download_request, "exchange", "") or "all"),
                task_id="",
                raw_reason=str(exc),
            )
        ]
        final_summary_payload = _merge_error_payload(
            phase_code="prepare_tasks",
            error_items=list(typed_errors),
            fallback_payload={
                "kind": "collect",
                "aggregate_summary": totals_to_summary_dict(totals),
            },
        )
        _emit_stage(
            request.stage_callback,
            phase_code="prepare_tasks",
            status="failed",
            label="正在扫描网页",
            summary_payload=final_summary_payload,
        )
        return [], DownloadOneClickStageResult(
            label=label,
            exit_code=2,
            elapsed_sec=elapsed,
            typed_errors=list(typed_errors),
            summary_payload=final_summary_payload,
        )

    prepared_request = getattr(prepared, "request", request.download_request)
    task_total = len(prepared.tasks)
    if task_total <= 0:
        elapsed = time.monotonic() - stage_start
        no_task_error = collect_failed_error(
            source_id=str(getattr(request.download_request, "exchange", "") or "all"),
            task_id="",
            raw_reason="no downloader task matched current filters",
        )
        summary_payload = _merge_error_payload(
            phase_code="prepare_tasks",
            error_items=[no_task_error],
            fallback_payload={
                "kind": "collect",
                "aggregate_summary": totals_to_summary_dict(totals),
            },
        )
        typed_errors = [no_task_error]
        return [], DownloadOneClickStageResult(
            label=label,
            exit_code=2,
            elapsed_sec=elapsed,
            typed_errors=typed_errors,
            summary_payload=summary_payload,
        )

    for index, spec in enumerate(prepared.tasks, start=1):
        task_label = task_progress_label(spec)
        phase_percent = min(49, 5 + int(index * 44 / max(task_total, 1)))
        existing_skipped = 0
        task_typed_errors: list[DownloadError] = []
        typed_task_errors: list[DownloadError] = []
        task_error_items: list[object] = []
        try:
            downloader = build_downloader(
                spec,
                args=prepared_request,
                output_root=prepared.output_root,
                logger=logger,
            )
            summary = run_downloader(
                downloader,
                start_date=getattr(prepared_request, "start_date", None),
                end_date=getattr(prepared_request, "end_date", None),
                list_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            typed_task_errors = [
                exc
                if isinstance(exc, DownloadError)
                else collect_failed_error(
                    source_id=str(spec.manifest.source_id or str(spec.task_id).split(":", 1)[0]),
                    task_id=str(spec.task_id),
                    raw_reason=str(exc),
                )
            ]
            task_error_items = list(typed_task_errors)

            typed_errors.extend(typed_task_errors)
            error_items.extend(task_error_items)
            summary_dict = totals_to_summary_dict(new_totals())
            candidate_entries: list[dict[str, object]] = []
        else:
            task_typed_errors = summary_typed_errors(summary)
            task_error_items = list(task_typed_errors)
            typed_errors.extend(task_typed_errors)
            error_items.extend(task_error_items)
            accumulate(summary, totals, [])
            summary_dict = summary_to_dict(summary)
            raw_entries = getattr(summary, "candidate_entries", None)
            candidate_entries = [dict(item) for item in raw_entries] if isinstance(raw_entries, list) else []
            task_record_family = str(
                getattr(spec.manifest, "record_family", "")
                or getattr(request.download_request, "record_family", "")
                or ""
            )
            task_business_id = str(
                getattr(spec.manifest, "business_id", "")
                or getattr(request.download_request, "business_id", "")
                or ""
            )
            task_source_id = str(
                getattr(spec.manifest, "source_id", "")
                or getattr(request.download_request, "exchange", "")
                or ""
            )
            if not task_source_id:
                task_source_id = str(spec.task_id).split(":", 1)[0]
            filtered_entries, existing_skipped = _filter_existing_candidates(
                candidate_entries,
                existing_project_codes=frozenset(request.existing_project_codes or ()),
                existing_candidate_tokens=frozenset(request.existing_candidate_tokens or ()),
                record_family=task_record_family,
                business_id=task_business_id,
                source_id=task_source_id,
            )
            raw_candidate_count = _coerce_int(summary_dict.get("detail_candidates"))
            filtered_candidate_count = len(filtered_entries) or max(raw_candidate_count - existing_skipped, 0)
            candidate_entries = filtered_entries
            summary_dict["detail_candidates"] = filtered_candidate_count
            if existing_skipped:
                summary_dict["duplicate_skipped"] = int(summary_dict.get("duplicate_skipped", 0)) + existing_skipped
                summary_dict["existing_skipped"] = existing_skipped
                totals["duplicate_skipped"] += existing_skipped
                totals["detail_candidates"] += filtered_candidate_count - raw_candidate_count
            discovered_total += filtered_candidate_count

        collected.append(
            _CollectedTask(
                task_id=str(spec.task_id),
                display_name=str(spec.display_name),
                task_label=task_label,
                candidate_entries=candidate_entries,
                existing_skipped=existing_skipped,
                summary=summary_dict,
                typed_errors=typed_task_errors,
                error_items=list(task_error_items),
                spec=spec,
            )
        )
        if index < task_total:
            _emit_stage(
                request.stage_callback,
                phase_code="prepare_tasks",
                status="running",
                label="正在扫描网页",
                summary_payload=_build_phase_summary(
                    totals=totals,
                    task_index=index,
                    task_total=task_total,
                    task_label=task_label,
                    phase_percent=phase_percent,
                    collected_candidates=discovered_total,
                ),
            )

    elapsed = time.monotonic() - stage_start
    aggregate_summary = totals_to_summary_dict(totals)
    task_summaries_payload = {
        item.task_id: {
            "display_name": item.display_name,
            "summary": _task_summary_dict(item),
            **(
                _merge_error_payload(
                    phase_code="prepare_tasks",
                    error_items=list(item.error_items),
                )
                if item.error_items
                else {"errors": []}
            ),
            "candidate_count": len(item.candidate_entries),
            "existing_skipped": int(item.existing_skipped),
        }
        for item in collected
    }
    summary_payload = (
        _merge_error_payload(
            phase_code="prepare_tasks",
            error_items=list(error_items),
            fallback_payload={
                "kind": "collect",
                "aggregate_summary": aggregate_summary,
                "discovered_candidates": int(discovered_total),
                "task_summaries": task_summaries_payload,
            },
        )
        if error_items
        else {
            "kind": "collect",
            "aggregate_summary": aggregate_summary,
            "discovered_candidates": int(discovered_total),
            "task_summaries": task_summaries_payload,
            "errors": [],
        }
    )
    final_summary_payload = {
        **_build_phase_summary(
            totals=totals,
            task_index=task_total,
            task_total=task_total,
            task_label=collected[-1].task_label if collected else "",
            phase_percent=49,
            collected_candidates=discovered_total,
        ),
        **summary_payload,
    }
    if typed_errors and "typed_error" not in final_summary_payload:
        final_summary_payload["typed_error"] = _serialize_typed_errors(typed_errors)[0]
    _emit_stage(
        request.stage_callback,
        phase_code="prepare_tasks",
        status="failed" if typed_errors else "done",
        label="扫描失败" if typed_errors else "扫描完成",
        summary_payload=final_summary_payload,
    )
    return collected, DownloadOneClickStageResult(
        label=label,
        exit_code=1 if typed_errors else 0,
        elapsed_sec=elapsed,
        typed_errors=list(typed_errors),
        summary_payload=final_summary_payload,
    )


def _execute_tasks(
    request: DownloadOneClickRequest,
    collected: list[_CollectedTask],
    *,
    logger: logging.Logger,
    config_obj: object,
) -> DownloadOneClickStageResult:
    label = "Stage 2/2: Download By Collected Tasks"
    stage_start = time.monotonic()
    totals = new_totals()
    typed_errors: list[DownloadError] = []
    error_items: list[object] = []
    task_summaries: dict[str, dict[str, Any]] = {}
    executable_tasks = [task for task in collected if task.candidate_entries]
    total_collected_candidates = sum(len(item.candidate_entries) for item in collected)
    if not executable_tasks:
        aggregate_summary = _aggregate_collected_task_summaries(collected)
        summary_payload = _with_terminal_download_classification(
            {
                "kind": "download",
                "aggregate_summary": aggregate_summary,
                "task_summaries": {
                    task.task_id: {
                        "display_name": task.display_name,
                        "summary": _task_summary_dict(task),
                        "candidate_count": len(task.candidate_entries),
                    }
                    for task in collected
                },
            },
            request=request,
        )
        label_text = str(summary_payload.get("terminal_label") or "当前没有需要下载的网页，无需下载")
        event_status = "warning" if str(summary_payload.get("warning_code") or "").strip() else "done"
        phase_summary = _build_phase_summary(
            totals=totals,
            task_index=0,
            task_total=0,
            task_label="",
            phase_percent=98,
            collected_candidates=0,
        )
        phase_summary["summary"].update(_summary_payload_dict_field(summary_payload, "aggregate_summary"))
        final_summary_payload = {
            **phase_summary,
            **summary_payload,
        }
        _emit_stage(
            request.stage_callback,
            phase_code="save_pages",
            status=event_status,
            label=label_text,
            summary_payload=final_summary_payload,
        )
        return DownloadOneClickStageResult(
            label=label,
            exit_code=0,
            elapsed_sec=time.monotonic() - stage_start,
            typed_errors=list(typed_errors),
            summary_payload=final_summary_payload,
        )

    prepared = prepare_download_session(
        request.download_request,
        logger=logger,
        config_obj=config_obj,
    )
    prepared_request = getattr(prepared, "request", request.download_request)
    task_total = len(executable_tasks)

    for index, task in enumerate(executable_tasks, start=1):
        phase_percent = min(98, 50 + int(index * 48 / max(task_total, 1)))
        _emit_stage(
            request.stage_callback,
            phase_code="save_pages",
            status="running",
            label="正在保存网页",
            summary_payload=_build_phase_summary(
                totals=totals,
                task_index=index,
                task_total=task_total,
                task_label=task.task_label,
                phase_percent=phase_percent,
                collected_candidates=total_collected_candidates,
            ),
        )
        try:
            downloader = build_downloader(
                task.spec,
                args=prepared_request,
                output_root=prepared.output_root,
                logger=logger,
            )
            summary = run_downloader_with_prefetched(
                downloader,
                start_date=getattr(prepared_request, "start_date", None),
                end_date=getattr(prepared_request, "end_date", None),
                list_only=False,
                prefetched_candidates=list(task.candidate_entries),
            )
        except Exception as exc:  # noqa: BLE001
            task_error_items: list[object]
            if not isinstance(exc, DownloadError):
                raise TypeError("save_pages requires typed DownloadError failures") from exc
            task_typed_errors = [exc]
            task_error_items = [exc]
            typed_errors.extend(task_typed_errors)
            error_items.extend(task_error_items)
            task_summaries[task.task_id] = {
                "display_name": task.display_name,
                "summary": totals_to_summary_dict(new_totals()),
                **_merge_error_payload(phase_code="save_pages", error_items=task_error_items),
                "candidate_count": len(task.candidate_entries),
            }
            continue

        task_typed_errors = summary_typed_errors(summary)
        task_error_items = list(task_typed_errors)
        typed_errors.extend(task_typed_errors)
        error_items.extend(task_error_items)
        task_downloads = summary_downloaded_this_run(summary)
        accumulate(summary, totals, [])
        task_summaries[task.task_id] = {
            "display_name": task.display_name,
            "summary": summary_to_dict(summary),
            **_merge_error_payload(phase_code="save_pages", error_items=task_error_items),
            "candidate_count": len(task.candidate_entries),
            "new_downloads": sorted(task_downloads),
        }

    elapsed = time.monotonic() - stage_start
    summary_payload = (
        _merge_error_payload(
            phase_code="save_pages",
            error_items=list(error_items),
            fallback_payload={
                "kind": "download",
                "aggregate_summary": totals_to_summary_dict(totals),
                "task_summaries": task_summaries,
            },
        )
        if error_items
        else {
            "kind": "download",
            "aggregate_summary": totals_to_summary_dict(totals),
            "task_summaries": task_summaries,
        }
    )
    final_summary_payload = {
        **_build_phase_summary(
            totals=totals,
            task_index=task_total,
            task_total=task_total,
            task_label=executable_tasks[-1].task_label if executable_tasks else "",
            phase_percent=98 if task_total else 50,
            collected_candidates=total_collected_candidates,
        ),
        **summary_payload,
    }
    _emit_stage(
        request.stage_callback,
        phase_code="save_pages",
        status="failed" if typed_errors else "done",
        label="正在保存网页",
        summary_payload=final_summary_payload,
    )
    return DownloadOneClickStageResult(
        label=label,
        exit_code=1 if typed_errors else 0,
        elapsed_sec=elapsed,
        typed_errors=list(typed_errors),
        summary_payload=final_summary_payload,
    )


def run_download_oneclick(
    request: DownloadOneClickRequest,
    *,
    config_obj: object,
    emit_console: bool = True,
) -> DownloadOneClickRunResult:
    run_start = time.monotonic()
    download_request = request.download_request
    start = str(getattr(download_request, "start_date", "") or "")
    end = str(getattr(download_request, "end_date", "") or "")
    logger, log_file = setup_download_oneclick_logger(
        verbose=bool(getattr(download_request, "verbose", False)),
        log_dir=str(download_request.log_dir or config_obj.LOG_DIR),
        log_file=getattr(download_request, "log_file", None),
        config_obj=config_obj,
    )

    try:
        stages: list[DownloadOneClickStageResult] = []
        collected, collect_stage = _collect_tasks(
            request,
            logger=logger,
            config_obj=config_obj,
        )
        stages.append(collect_stage)
        collect_errors = list(collect_stage.typed_errors) if collect_stage.typed_errors else []
        if collect_stage.exit_code != 0 and not collected:
            exit_code = collect_stage.exit_code
            aggregate_summary = _summary_payload_dict_field(collect_stage.summary_payload, "aggregate_summary")
            task_summaries = _summary_payload_dict_field(collect_stage.summary_payload, "task_summaries")
            typed_errors = collect_errors
        else:
            if sum(len(item.candidate_entries) for item in collected) <= 0:
                aggregate_summary = _summary_payload_dict_field(collect_stage.summary_payload, "aggregate_summary")
                typed_errors: list[DownloadError] = []
                has_collect_errors = bool(collect_errors)
                execute_summary_payload: dict[str, Any] = {
                    "kind": "download",
                    "aggregate_summary": aggregate_summary,
                    "task_summaries": _summary_payload_dict_field(collect_stage.summary_payload, "task_summaries"),
                }
                if has_collect_errors:
                    execute_summary_payload = _merge_error_payload(
                        phase_code="save_pages",
                        error_items=collect_errors,
                        fallback_payload=execute_summary_payload,
                    )
                else:
                    execute_summary_payload = _with_terminal_download_classification(
                        execute_summary_payload,
                        request=request,
                    )
                execute_stage = DownloadOneClickStageResult(
                    label="Stage 2/2: No Download Needed",
                    exit_code=collect_stage.exit_code if has_collect_errors else 0,
                    elapsed_sec=0.0,
                    typed_errors=[],
                    summary_payload=execute_summary_payload,
                )
                label_text = str(
                    execute_summary_payload.get("terminal_label")
                    or _upstream_failure_stage_label(collect_errors)
                )
                event_status = "warning" if has_collect_errors or str(execute_summary_payload.get("warning_code") or "").strip() else "done"
                phase_summary = {
                    "task_label": "",
                    "task_index": 0,
                    "task_total": 0,
                    "phase_percent": 98,
                    "summary": dict(aggregate_summary),
                }
                phase_summary["summary"].update(
                    _summary_payload_dict_field(execute_summary_payload, "aggregate_summary")
                )
                _emit_stage(
                    request.stage_callback,
                    phase_code="save_pages",
                    status=event_status,
                    label=label_text,
                    summary_payload={
                        **phase_summary,
                        **execute_stage.summary_payload,
                    },
                )
            else:
                execute_stage = _execute_tasks(
                    request,
                    collected,
                    logger=logger,
                    config_obj=config_obj,
                )
            stages.append(execute_stage)
            exit_code = max(collect_stage.exit_code, execute_stage.exit_code)
            aggregate_summary = _summary_payload_dict_field(execute_stage.summary_payload, "aggregate_summary")
            task_summaries = _summary_payload_dict_field(execute_stage.summary_payload, "task_summaries")
            typed_errors = collect_errors + list(execute_stage.typed_errors)

        duration_sec = round(time.monotonic() - run_start, 3)
        plan_file = str(request.plan_file or "")
        return DownloadOneClickRunResult(
            exit_code=exit_code,
            log_file=log_file,
            plan_file=plan_file,
            plan_file_exists=bool(plan_file and os.path.exists(plan_file)),
            plan_file_removed=False,
            start=start,
            end=end,
            duration_sec=duration_sec,
            aggregate_summary=aggregate_summary,
            task_summaries=task_summaries,
            typed_errors=typed_errors,
            stages=stages,
        )
    finally:
        close_cli_logger(logger)


__all__ = [
    "DownloadOneClickRequest",
    "DownloadOneClickRunResult",
    "DownloadOneClickStageResult",
    "run_download_oneclick",
    "setup_download_oneclick_logger",
]
