"""Structured one-click downloader orchestration."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from peap_core.cli_support import close_cli_logger, setup_cli_logger

from .download_reporting import accumulate, new_totals, summary_to_dict, totals_to_summary_dict
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
    errors: list[str]
    stages: list[DownloadOneClickStageResult] = field(default_factory=list)


@dataclass(frozen=True)
class _CollectedTask:
    task_id: str
    display_name: str
    task_label: str
    candidate_entries: list[dict[str, object]]
    existing_skipped: int
    summary: dict[str, int]
    errors: list[str]
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
        base_level=str(getattr(config_obj, "LOG_LEVEL", "INFO")),
        enable_file_logging=bool(getattr(config_obj, "LOG_TO_FILE", True)),
    )
    if resolved_log_file:
        logger.info("Download log file: %s", resolved_log_file)
    return logger, resolved_log_file


def _coerce_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _first_error_message(raw_errors: object) -> str:
    if isinstance(raw_errors, (list, tuple)):
        for item in raw_errors:
            message = str(item or "").strip()
            if message:
                return message
    elif isinstance(raw_errors, str):
        return raw_errors.strip()
    return ""


_LIST_FAILURE_RE = re.compile(
    r"^(?P<exchange>[a-z0-9_]+)-list-failed:\s*(?P<reason>.+)$",
    re.IGNORECASE,
)
_COLLECT_FAILURE_RE = re.compile(
    r"^(?P<prefix>[a-z0-9_:-]+):\s*collect-failed:\s*(?P<reason>.+)$",
    re.IGNORECASE,
)


def _structured_collect_error(
    *,
    exchange: str,
    failure_kind: str,
    raw_message: str,
    raw_reason: str,
    task_id: str = "",
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "exchange": exchange,
        "stage": "prepare_tasks",
        "failure_kind": failure_kind,
        "raw_reason": raw_reason,
    }
    normalized_task_id = str(task_id or "").strip()
    if normalized_task_id:
        details["task_id"] = normalized_task_id
    return {
        "error_code": f"{exchange}_{failure_kind}_failed",
        "error_message": raw_message,
        "error_details": details,
    }


def _classify_collect_error(raw_errors: object) -> dict[str, Any]:
    message = _first_error_message(raw_errors)
    if not message:
        return {}
    if (
        "www.suaee.com" in message
        and "queryAllNew" in message
        and "HTTP Error 404" in message
    ):
        return {
            "error_code": "sse_list_api_not_found",
            "error_message": "上交所列表接口 queryAllNew 返回 404，当前扫描已中止",
            "error_details": {
                "exchange": "sse",
                "stage": "prepare_tasks",
                "upstream_url": "https://www.suaee.com/manageprojectweb/foreign/project/queryAllNew",
            },
        }
    list_failed_match = _LIST_FAILURE_RE.match(message)
    if list_failed_match:
        exchange = str(list_failed_match.group("exchange") or "").strip().lower()
        reason = str(list_failed_match.group("reason") or "").strip()
        if exchange and reason:
            return _structured_collect_error(
                exchange=exchange,
                failure_kind="list",
                raw_message=message,
                raw_reason=reason,
            )
    collect_failed_match = _COLLECT_FAILURE_RE.match(message)
    if collect_failed_match:
        task_prefix = str(collect_failed_match.group("prefix") or "").strip().lower()
        exchange = task_prefix.split(":", 1)[0].strip()
        reason = str(collect_failed_match.group("reason") or "").strip()
        if exchange and reason:
            return _structured_collect_error(
                exchange=exchange,
                failure_kind="collect",
                raw_message=message,
                raw_reason=reason,
                task_id=task_prefix if ":" in task_prefix else "",
            )
    return {"error_message": message}


def _stage_error_message(summary_payload: dict[str, Any] | None) -> str:
    payload = dict(summary_payload or {})
    explicit = str(payload.get("error_message") or "").strip()
    if explicit:
        return explicit
    message = _first_error_message(payload.get("errors"))
    if message:
        return message
    task_summaries = payload.get("task_summaries")
    if isinstance(task_summaries, dict):
        for item in task_summaries.values():
            if not isinstance(item, dict):
                continue
            message = _first_error_message(item.get("errors"))
            if message:
                return message
    return ""


def _emit_stage(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    phase_code: str,
    status: str,
    label: str,
    summary_payload: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    error_message = _stage_error_message(summary_payload)
    payload = {
        "phase_code": str(phase_code),
        "status": str(status),
        "label": str(label),
        "summary_payload": dict(summary_payload or {}),
    }
    if summary_payload:
        payload.update(dict(summary_payload))
    if error_message:
        payload["error_message"] = error_message
    callback(payload)


def _build_phase_summary(
    *,
    totals: dict[str, int],
    task_index: int,
    task_total: int,
    task_label: str,
    phase_percent: int,
    collected_candidates: int = 0,
) -> dict[str, Any]:
    return {
        "task_label": task_label,
        "task_index": int(task_index),
        "task_total": int(task_total),
        "phase_percent": int(phase_percent),
        "summary": {
            **totals_to_summary_dict(totals, []),
            "collected_candidates": int(collected_candidates),
        },
    }


def _filter_existing_candidates(
    candidate_entries: list[dict[str, object]],
    *,
    existing_project_codes: frozenset[str],
    existing_candidate_tokens: frozenset[str],
) -> tuple[list[dict[str, object]], int]:
    if not existing_project_codes and not existing_candidate_tokens:
        return list(candidate_entries), 0
    filtered: list[dict[str, object]] = []
    skipped = 0
    for entry in candidate_entries:
        project_code = str(dict(entry).get("project_code") or "").strip().upper()
        candidate_tokens = set()
        if project_code:
            candidate_tokens.add(f"project_code:{project_code}")
        project_id = str(dict(entry).get("project_id") or "").strip().upper()
        if project_id:
            candidate_tokens.add(f"project_id:{project_id}")
        page_url = str(dict(entry).get("page_url") or "").strip()
        if page_url:
            candidate_tokens.add(f"page_url:{page_url}")
        if (project_code and project_code in existing_project_codes) or (
            existing_candidate_tokens and candidate_tokens & existing_candidate_tokens
        ):
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
    errors: list[str] = []
    discovered_total = 0

    try:
        prepared = prepare_download_session(
            request.download_request,
            logger=logger,
            config_obj=config_obj,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - stage_start
        summary_payload = {
            "kind": "collect",
            "errors": [str(exc)],
            "aggregate_summary": totals_to_summary_dict(totals, [str(exc)]),
        }
        _emit_stage(
            request.stage_callback,
            phase_code="prepare_tasks",
            status="failed",
            label="正在扫描网页",
            summary_payload=summary_payload,
        )
        return [], DownloadOneClickStageResult(
            label=label,
            exit_code=2,
            elapsed_sec=elapsed,
            summary_payload=summary_payload,
        )

    task_total = len(prepared.tasks)
    if task_total <= 0:
        elapsed = time.monotonic() - stage_start
        summary_payload = {
            "kind": "collect",
            "errors": ["no downloader task matched current filters"],
            "aggregate_summary": totals_to_summary_dict(totals, ["no downloader task matched current filters"]),
        }
        return [], DownloadOneClickStageResult(
            label=label,
            exit_code=2,
            elapsed_sec=elapsed,
            summary_payload=summary_payload,
        )

    for index, spec in enumerate(prepared.tasks, start=1):
        task_label = task_progress_label(spec)
        phase_percent = min(49, 5 + int(index * 44 / max(task_total, 1)))
        existing_skipped = 0
        try:
            downloader = build_downloader(
                spec,
                args=request.download_request,
                output_root=prepared.output_root,
                logger=logger,
            )
            summary = run_downloader(
                downloader,
                start_date=getattr(request.download_request, "start_date", None),
                end_date=getattr(request.download_request, "end_date", None),
                list_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            task_errors = [f"{spec.task_id}: collect-failed: {exc}"]
            errors.extend(task_errors)
            summary_dict = totals_to_summary_dict(new_totals(), task_errors)
            candidate_entries: list[dict[str, object]] = []
        else:
            task_errors = [str(item) for item in getattr(summary, "errors", []) or []]
            errors.extend(task_errors)
            accumulate(summary, totals, [])
            summary_dict = summary_to_dict(summary)
            raw_entries = getattr(summary, "candidate_entries", None)
            candidate_entries = [dict(item) for item in raw_entries] if isinstance(raw_entries, list) else []
            filtered_entries, existing_skipped = _filter_existing_candidates(
                candidate_entries,
                existing_project_codes=frozenset(request.existing_project_codes or ()),
                existing_candidate_tokens=frozenset(request.existing_candidate_tokens or ()),
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
                errors=task_errors,
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
    aggregate_summary = totals_to_summary_dict(totals, errors)
    summary_payload = {
        "kind": "collect",
        "aggregate_summary": aggregate_summary,
        "discovered_candidates": int(discovered_total),
        "errors": list(errors),
        "task_summaries": {
            item.task_id: {
                "display_name": item.display_name,
                "summary": dict(item.summary),
                "errors": list(item.errors),
                "candidate_count": len(item.candidate_entries),
                "existing_skipped": int(item.existing_skipped),
            }
            for item in collected
        },
    }
    _emit_stage(
        request.stage_callback,
        phase_code="prepare_tasks",
        status="failed" if errors else "done",
        label="扫描失败" if errors else "扫描完成",
        summary_payload={
            **_build_phase_summary(
                totals=totals,
                task_index=task_total,
                task_total=task_total,
                task_label=collected[-1].task_label if collected else "",
                phase_percent=49,
                collected_candidates=discovered_total,
            ),
            **(_classify_collect_error(errors) if errors else {}),
            **summary_payload,
        },
    )
    return collected, DownloadOneClickStageResult(
        label=label,
        exit_code=1 if errors else 0,
        elapsed_sec=elapsed,
        summary_payload=summary_payload,
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
    errors: list[str] = []
    task_summaries: dict[str, dict[str, Any]] = {}
    executable_tasks = [task for task in collected if task.candidate_entries]
    total_collected_candidates = sum(len(item.candidate_entries) for item in collected)
    if not executable_tasks:
        summary_payload = {
            "kind": "download",
            "aggregate_summary": totals_to_summary_dict(totals, []),
            "task_summaries": {},
            "errors": [],
        }
        _emit_stage(
            request.stage_callback,
            phase_code="save_pages",
            status="done",
            label="当前没有需要下载的网页，无需下载",
            summary_payload={
                **_build_phase_summary(
                    totals=totals,
                    task_index=0,
                    task_total=0,
                    task_label="",
                    phase_percent=98,
                    collected_candidates=0,
                ),
                **summary_payload,
            },
        )
        return DownloadOneClickStageResult(
            label=label,
            exit_code=0,
            elapsed_sec=time.monotonic() - stage_start,
            summary_payload=summary_payload,
        )

    prepared = prepare_download_session(
        request.download_request,
        logger=logger,
        config_obj=config_obj,
    )
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
                args=request.download_request,
                output_root=prepared.output_root,
                logger=logger,
            )
            summary = run_downloader_with_prefetched(
                downloader,
                start_date=getattr(request.download_request, "start_date", None),
                end_date=getattr(request.download_request, "end_date", None),
                list_only=False,
                prefetched_candidates=list(task.candidate_entries),
            )
        except Exception as exc:  # noqa: BLE001
            task_errors = [f"{task.task_id}: execute-failed: {exc}"]
            errors.extend(task_errors)
            task_summaries[task.task_id] = {
                "display_name": task.display_name,
                "summary": totals_to_summary_dict(new_totals(), task_errors),
                "errors": list(task_errors),
                "candidate_count": len(task.candidate_entries),
            }
            continue

        task_errors = [str(item) for item in getattr(summary, "errors", []) or []]
        errors.extend(task_errors)
        accumulate(summary, totals, [])
        task_summaries[task.task_id] = {
            "display_name": task.display_name,
            "summary": summary_to_dict(summary),
            "errors": list(task_errors),
            "candidate_count": len(task.candidate_entries),
        }

    elapsed = time.monotonic() - stage_start
    summary_payload = {
        "kind": "download",
        "aggregate_summary": totals_to_summary_dict(totals, errors),
        "task_summaries": task_summaries,
        "errors": list(errors),
    }
    _emit_stage(
        request.stage_callback,
        phase_code="save_pages",
        status="failed" if errors else "done",
        label="正在保存网页",
        summary_payload={
            **_build_phase_summary(
                totals=totals,
                task_index=task_total,
                task_total=task_total,
                task_label=executable_tasks[-1].task_label if executable_tasks else "",
                phase_percent=98 if task_total else 50,
                collected_candidates=total_collected_candidates,
            ),
            **summary_payload,
        },
    )
    return DownloadOneClickStageResult(
        label=label,
        exit_code=1 if errors else 0,
        elapsed_sec=elapsed,
        summary_payload=summary_payload,
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
        if collect_stage.exit_code != 0:
            exit_code = collect_stage.exit_code
            aggregate_summary = dict(collect_stage.summary_payload.get("aggregate_summary") or {})
            task_summaries = dict(collect_stage.summary_payload.get("task_summaries") or {})
            errors = list(collect_stage.summary_payload.get("errors") or [])
        else:
            if sum(len(item.candidate_entries) for item in collected) <= 0:
                aggregate_summary = dict(collect_stage.summary_payload.get("aggregate_summary") or {})
                execute_stage = DownloadOneClickStageResult(
                    label="Stage 2/2: No Download Needed",
                    exit_code=0,
                    elapsed_sec=0.0,
                    summary_payload={
                        "kind": "download",
                        "aggregate_summary": aggregate_summary,
                        "task_summaries": dict(collect_stage.summary_payload.get("task_summaries") or {}),
                        "errors": [],
                    },
                )
                _emit_stage(
                    request.stage_callback,
                    phase_code="save_pages",
                    status="done",
                    label="当前没有需要下载的网页，无需下载",
                    summary_payload={
                        "task_label": "",
                        "task_index": 0,
                        "task_total": 0,
                        "phase_percent": 98,
                        "summary": aggregate_summary,
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
            exit_code = execute_stage.exit_code
            aggregate_summary = dict(execute_stage.summary_payload.get("aggregate_summary") or {})
            task_summaries = dict(execute_stage.summary_payload.get("task_summaries") or {})
            errors = list(execute_stage.summary_payload.get("errors") or [])

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
            errors=errors,
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
