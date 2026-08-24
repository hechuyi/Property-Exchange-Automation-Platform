"""Streaming one-click pipeline: download, ingest item-by-item, then export ready records."""

from __future__ import annotations

import datetime as dt
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict

from peap_core.cli_support import close_cli_logger, setup_cli_logger
from peap_core.source_catalog import canonical_source_code

from .job_event_summary import failure_summary_fields as _failure_summary_fields
from .job_event_summary import has_failed_job_event as _has_failed_job_event
from .rules_config import load_effective_rules_config
from .streaming_export import run_ready_export
from .streaming_ingest import StreamingIngestDependencies, StreamingIngestRunner
from .streaming_models import ExportRequest, ItemProgressEvent
from .streaming_queue import StreamingIngestService
from .streaming_store import StreamingStore
from .streaming_store_maintenance import run_streaming_store_maintenance


@dataclass
class StreamingDailyPipelineRunResult:
    exit_code: int
    log_file: str
    db_path: str
    job_id: str
    start_date: str
    end_date: str
    duration_sec: float
    download_result: Any | None = None
    export_artifacts: list[str] | None = None
    downloaded_count: int = 0
    persisted_count: int = 0
    exception_count: int = 0


def today_local() -> dt.date:
    return dt.date.today()


def parse_date(raw_value: str | None, *, default: dt.date) -> dt.date:
    if raw_value is None or not str(raw_value).strip():
        return default
    return dt.datetime.strptime(str(raw_value).strip(), "%Y-%m-%d").date()


def _first_error_message(raw_errors: object) -> str:
    if isinstance(raw_errors, (list, tuple)):
        for item in raw_errors:
            message = str(item or "").strip()
            if message:
                return message
    elif isinstance(raw_errors, str):
        return raw_errors.strip()
    return ""


def _required_mapping_value(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _optional_mapping_field(payload: Mapping[str, Any], key: str, *, field_name: str) -> dict[str, Any]:
    if key not in payload:
        return {}
    value = payload.get(key)
    if value is None:
        return {}
    return _required_mapping_value(value, field_name=field_name)


def _progress_event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ValueError("progress_event must be a mapping")
    return _optional_mapping_field(event, "payload", field_name="progress_event.payload")


def _download_typed_errors(download_result: Any) -> list[Any]:
    if not hasattr(download_result, "typed_errors"):
        return []
    typed_errors = download_result.typed_errors
    if typed_errors is None:
        return []
    if not isinstance(typed_errors, list):
        raise TypeError("download_result.typed_errors must be a list")
    return list(typed_errors)


def _download_archive_audit_summary(download_result: Any) -> dict[str, Any]:
    if not hasattr(download_result, "archive_audit"):
        return {}
    archive_audit = download_result.archive_audit
    if archive_audit is None:
        return {}
    if not isinstance(archive_audit, Mapping):
        raise TypeError("download_result.archive_audit must be a mapping")
    if not archive_audit:
        return {}
    return {"download_archive_audit": dict(archive_audit)}


def _stage_error_message(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("error_message") or "").strip()
    if explicit:
        return explicit
    message = _first_error_message(payload.get("errors"))
    if message:
        return message
    summary_payload = payload.get("summary_payload")
    if isinstance(summary_payload, dict):
        message = _first_error_message(summary_payload.get("errors"))
        if message:
            return message
    for task_summaries in (payload.get("task_summaries"), summary_payload.get("task_summaries") if isinstance(summary_payload, dict) else None):
        if not isinstance(task_summaries, dict):
            continue
        for item in task_summaries.values():
            if not isinstance(item, dict):
                continue
            message = _first_error_message(item.get("errors"))
            if message:
                return message
    return ""


def _stage_error_type(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("error_code") or payload.get("error_type") or "").strip()
    if explicit:
        return explicit
    summary_payload = payload.get("summary_payload")
    if isinstance(summary_payload, dict):
        explicit = str(summary_payload.get("error_code") or summary_payload.get("error_type") or "").strip()
        if explicit:
            return explicit
    return ""


def _stage_display_error_message(payload: Dict[str, Any]) -> str:
    return _stage_error_message(payload)


def _warning_summary_fields(job_events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(job_events):
        payload = _progress_event_payload(event)
        summary = _optional_mapping_field(
            payload,
            "summary",
            field_name="progress_event.payload.summary",
        )
        summary_payload = _optional_mapping_field(
            payload,
            "summary_payload",
            field_name="progress_event.payload.summary_payload",
        )
        summary_payload_summary = _optional_mapping_field(
            summary_payload,
            "summary",
            field_name="progress_event.payload.summary_payload.summary",
        )
        warning_code = str(
            payload.get("warning_code")
            or summary_payload.get("warning_code")
            or summary.get("warning_code")
            or summary_payload_summary.get("warning_code")
            or ""
        ).strip()
        warning_message = str(
            payload.get("warning_message")
            or summary_payload.get("warning_message")
            or summary.get("warning_message")
            or summary_payload_summary.get("warning_message")
            or ""
        ).strip()
        if warning_code or warning_message:
            return {
                "warning_code": warning_code,
                "warning_message": warning_message,
            }
    return {}


def _download_failure_summary_fields(download_result: Any) -> dict[str, Any]:
    for item in _download_typed_errors(download_result):
        error_code = str(getattr(item, "error_code", "") or "").strip()
        error_message = str(getattr(item, "error_message", "") or str(item or "")).strip()
        failure_stage = str(getattr(item, "stage", "") or "").strip()
        if error_code or error_message:
            return {
                "failure_code": error_code,
                "failure_stage": failure_stage,
                "failure_message": error_message,
            }
    return {}


def _resolve_failure_summary(
    download_result: Any,
    job_events: list[dict[str, Any]],
) -> dict[str, Any]:
    download_failure = _download_failure_summary_fields(download_result)
    if download_failure:
        return download_failure
    return _failure_summary_fields(job_events)


def _setup_logger(*, verbose: bool, config_obj: object) -> tuple[object, str]:
    return setup_cli_logger(
        name="streaming_daily_pipeline",
        verbose=verbose,
        log_dir=str(config_obj.LOG_DIR),
        log_file=None,
        default_log_dir=str(config_obj.LOG_DIR),
        file_prefix="streaming_daily",
        base_level=str(getattr(config_obj, "LOG_LEVEL", "INFO")),
        enable_file_logging=bool(getattr(config_obj, "LOG_TO_FILE", True)),
    )


_load_rules_config = load_effective_rules_config

def _coerce_path_value(raw_value: object, *, field_name: str) -> str:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, os.PathLike)):
        raise TypeError(f"{field_name} must be str or os.PathLike")
    return os.fspath(raw_value)


def _coerce_optional_path_value(raw_value: object, *, field_name: str) -> str | None:
    if raw_value is None:
        return None
    value = _coerce_path_value(raw_value, field_name=field_name)
    if not str(value).strip():
        return None
    return value


def _resolve_streaming_db_path(args: object, *, config_obj: object) -> str:
    raw_arg = getattr(args, "streaming_db", None)
    arg_value = _coerce_optional_path_value(raw_arg, field_name="streaming_db")
    if arg_value is not None:
        return os.path.abspath(arg_value)

    raw_config = getattr(config_obj, "STREAMING_DB_PATH", None)
    config_value = _coerce_optional_path_value(raw_config, field_name="STREAMING_DB_PATH")
    if config_value is not None:
        return os.path.abspath(config_value)

    return os.path.abspath(os.path.join(str(config_obj.LOG_DIR), "streaming_ingest.sqlite3"))


def _resolve_archive_root(archive_root: object, *, config_obj: object) -> str:
    explicit_value = _coerce_optional_path_value(archive_root, field_name="archive_root")
    if explicit_value is not None:
        return os.path.abspath(explicit_value)

    raw_config = getattr(config_obj, "ARCHIVE_ROOT", None)
    config_value = _coerce_optional_path_value(raw_config, field_name="ARCHIVE_ROOT")
    if config_value is not None:
        return os.path.abspath(config_value)

    return os.path.abspath(os.path.join(str(config_obj.DATA_ROOT), "outputs", "submission"))


def _resolve_export_root(export_root: object, *, config_obj: object, auto_export_enabled: bool) -> str:
    explicit_value = _coerce_optional_path_value(export_root, field_name="export_root")
    if explicit_value is not None:
        return os.path.abspath(explicit_value)

    raw_config = getattr(config_obj, "OUTPUT_EXCEL_DIR", None)
    config_value = _coerce_optional_path_value(raw_config, field_name="OUTPUT_EXCEL_DIR")
    if config_value is not None:
        return os.path.abspath(config_value)

    if auto_export_enabled:
        raise ValueError("export_root is required when auto export is enabled")
    return ""


def _build_download_request(
    args: object,
    *,
    start_text: str,
    end_text: str,
    config_obj: object,
    output_root: str,
    item_saved_callback=None,
):
    from .download_runner import DownloadRunRequest

    defaults = config_obj.DOWNLOADER_DEFAULTS
    max_pages = getattr(args, "max_pages", None)

    return DownloadRunRequest(
        exchange=str(getattr(args, "exchange", "all")),
        record_family=str(getattr(args, "record_family", "listing")),
        business_id=str(getattr(args, "business_id", "all")),
        list_tasks=False,
        output_root=str(output_root or ""),
        force_manual_root=False,
        start_date=start_text,
        end_date=end_text,
        page_size=getattr(args, "page_size", None),
        max_pages=max_pages,
        concurrency=int(getattr(args, "concurrency", defaults["concurrency"])),
        resume=not bool(getattr(args, "no_resume", False)),
        save_json=bool(getattr(args, "save_json", False)),
        sse_ssl_verify=bool(defaults.get("sse_ssl_verify", True)),
        sse_ca_bundle=defaults.get("sse_ca_bundle"),
        log_dir=str(config_obj.LOG_DIR),
        log_file=None,
        verbose=bool(getattr(args, "verbose", False)),
        auto_split=False,
        split_candidates=int(defaults["split_candidates"]),
        split_min_days=int(defaults["split_min_days"]),
        split_max_depth=int(defaults["split_max_depth"]),
        split_plan_only=False,
        split_plan_file=None,
        split_use_plan=False,
        split_mode=str(defaults["split_mode"]),
        chunk_state_file=None,
        item_saved_callback=item_saved_callback,
    )


def run_streaming_daily_pipeline(
    args: object,
    *,
    config_obj: object,
    emit_console: bool = True,
    job_created_callback: Callable[[str, str], None] | None = None,
    job_type: str = "one_click",
    archive_root: str | None = None,
    export_root: str | None = None,
    auto_export: bool | None = None,
    job_id: str | None = None,
    manage_job_lifecycle: bool = True,
) -> StreamingDailyPipelineRunResult:
    if job_id is not None and not isinstance(job_id, str):
        raise TypeError("job_id must be str")

    logger, log_file = _setup_logger(
        verbose=bool(getattr(args, "verbose", False)),
        config_obj=config_obj,
    )
    try:
        from .download_oneclick import DownloadOneClickRequest, run_download_oneclick

        today = today_local()
        default_start = today
        start_date = parse_date(getattr(args, "start_date", None), default=default_start)
        end_date = parse_date(getattr(args, "end_date", None), default=today)
        if start_date > end_date:
            return StreamingDailyPipelineRunResult(
                exit_code=2,
                log_file=log_file,
                db_path="",
                job_id="",
                start_date=str(start_date),
                end_date=str(end_date),
                duration_sec=0.0,
            )

        start_text = start_date.isoformat()
        end_text = end_date.isoformat()
        should_auto_export = (
            not bool(getattr(args, "no_auto_export", False))
            if auto_export is None
            else bool(auto_export)
        )
        db_path = _resolve_streaming_db_path(args, config_obj=config_obj)
        resolved_archive_root = _resolve_archive_root(archive_root, config_obj=config_obj)
        resolved_export_root = _resolve_export_root(
            export_root,
            config_obj=config_obj,
            auto_export_enabled=should_auto_export,
        )
        rules_config = _load_rules_config(getattr(args, "postprocess_config", None))
        store = StreamingStore(db_path, auto_migrate=True)
        run_streaming_store_maintenance(store, rules_config=rules_config, mutate=True)
        runner = StreamingIngestRunner(
            store=store,
            archive_root=resolved_archive_root,
            rules_config=rules_config,
            dependencies=StreamingIngestDependencies(),
        )
        service = StreamingIngestService(store=store, runner=runner)
        service.start()

        try:
            job_metadata = {
                "start_date": start_text,
                "end_date": end_text,
                "exchange": getattr(args, "exchange", "all"),
                "record_family": getattr(args, "record_family", "listing"),
                "business_id": getattr(args, "business_id", "all"),
                "archive_root": resolved_archive_root,
                "export_root": resolved_export_root,
            }
            if manage_job_lifecycle:
                if not str(job_id or "").strip():
                    job_id = store.create_job(
                        str(job_type),
                        metadata=job_metadata,
                    )
                else:
                    try:
                        store.get_job(str(job_id))
                    except KeyError:
                        job_id = store.create_job(
                            str(job_type),
                            metadata=job_metadata,
                            job_id=str(job_id),
                        )
                if not str(job_id or "").strip():
                    raise RuntimeError(f"{job_type} job did not provide job_id")
                # Transition job from STARTING to RUNNING before acknowledging
                # startup to the caller.
                store.start_job(job_id)
                if job_created_callback is not None:
                    job_created_callback(job_id, db_path)
            else:
                if not str(job_id or "").strip():
                    raise RuntimeError(f"{job_type} job requires job_id when lifecycle is external")
                store.get_job(str(job_id))
                if job_created_callback is not None:
                    job_created_callback(str(job_id), db_path)
            if not str(job_id or "").strip():
                raise RuntimeError(f"{job_type} job did not provide job_id")
            started_at = time.monotonic()
            callback = service.build_callback(job_id=job_id)
            requested_family = str(getattr(args, "record_family", "listing") or "").strip()
            requested_business_id = str(getattr(args, "business_id", "all") or "").strip()
            raw_requested_source_id = str(getattr(args, "exchange", "all") or "").strip()
            if raw_requested_source_id.lower() == "all":
                requested_source_id = raw_requested_source_id
            else:
                requested_source_id = str(canonical_source_code(raw_requested_source_id) or raw_requested_source_id).strip()
            existing_project_codes = frozenset(
                store.list_existing_project_codes(
                    states=["ready", "pending_review", "pending_mapping", "mapping_conflict", "skipped", "conflict"],
                    record_family=requested_family,
                    business_id=requested_business_id,
                    source_id=requested_source_id,
                    include_scoped_keys=True,
                    require_existing_artifact=True,
                )
            )
            existing_candidate_tokens = frozenset(
                store.list_existing_candidate_tokens(
                    states=["ready", "pending_review", "pending_mapping", "mapping_conflict", "skipped", "conflict"],
                    record_family=requested_family,
                    business_id=requested_business_id,
                    source_id=requested_source_id,
                    include_scoped_tokens=True,
                    require_existing_artifact=True,
                )
            )

            def _stage_callback(payload: Dict[str, Any]) -> None:
                phase_code = str(payload.get("phase_code") or "").strip()
                if not phase_code:
                    scoped_payload = {
                        **dict(payload),
                        "contract_violation": "missing_phase_code",
                        "scope": {
                            "record_family": requested_family,
                            "business_id": requested_business_id,
                            "exchange": requested_source_id,
                        },
                    }
                    store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="contract_violation",
                            status="failed",
                            error_type="missing_phase_code",
                            error_message="stage callback payload missing phase_code",
                            payload=scoped_payload,
                        )
                    )
                    return
                scoped_payload = {
                    **dict(payload),
                    "scope": {
                        "record_family": requested_family,
                        "business_id": requested_business_id,
                        "exchange": requested_source_id,
                    },
                }
                store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage=phase_code,
                        status=str(payload.get("status") or "running"),
                        error_type=_stage_error_type(payload),
                        error_message=_stage_display_error_message(payload),
                        payload=scoped_payload,
                    )
                )

            request = DownloadOneClickRequest(
                download_request=_build_download_request(
                    args,
                    start_text=start_text,
                    end_text=end_text,
                    config_obj=config_obj,
                    output_root=resolved_archive_root,
                    item_saved_callback=callback,
                ),
                plan_file="",
                keep_plan=False,
                with_refresh=False,
                stage_callback=_stage_callback,
                existing_project_codes=existing_project_codes,
                existing_candidate_tokens=existing_candidate_tokens,
            )
            download_result = run_download_oneclick(
                request,
                config_obj=config_obj,
                emit_console=emit_console,
            )
            service.wait_for_idle()
        finally:
            try:
                service.wait_for_idle()
            finally:
                service.stop()

        job_info = store.get_job(job_id)
        job_events = store.list_job_events(job_id, limit=100000)
        artifacts: list[str] = []
        export_warning_summary: dict[str, Any] = {}
        exit_code = download_result.exit_code
        if exit_code == 0 and (job_info["exception_count"] > 0 or _has_failed_job_event(job_events)):
            exit_code = 1
            store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="ingest_guard",
                    status="failed",
                    error_type="ingest_failed",
                    error_message="streaming ingest recorded failures; auto export blocked",
                    payload={
                        "label": "入库失败，已阻止自动导出",
                        "exception_count": job_info["exception_count"],
                    },
                )
            )
        if exit_code == 0 and should_auto_export:
            store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="exporting",
                    status="running",
                    payload={"label": "正在导出 Excel"},
                )
            )
            try:
                requested_family = str(getattr(args, "record_family", "listing") or "listing").strip() or "listing"
                requested_business_id = str(getattr(args, "business_id", "all") or "all").strip()
                business_scope = [] if requested_business_id.lower() == "all" else [requested_business_id]
                export_result = run_ready_export(
                    store,
                    ExportRequest(
                        date_from=start_text,
                        date_to=end_text,
                        business_types=business_scope,
                        exchange=str(getattr(args, "exchange", "all") or "all"),
                        requested_export_mode=str(getattr(args, "requested_export_mode", "") or "full"),
                        output_dir=resolved_export_root,
                        record_family=requested_family,
                    ),
                )
            except Exception as exc:
                exit_code = 1
                artifacts = []
                store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status="failed",
                        error_type="export_failed",
                        error_message=str(exc),
                        payload={"label": "导出失败"},
                    )
                )
                store.add_audit_entry(
                    "streaming_export_failed",
                    {"job_id": job_id, "error": str(exc)},
                )
            else:
                artifacts = [item.file_path for item in export_result.artifacts]
                field_missing_blocked_records = int(getattr(export_result, "field_missing_blocked_records", 0) or 0)
                field_missing_diagnostics = list(getattr(export_result, "field_missing_diagnostics", []) or [])
                export_payload: dict[str, Any] = {
                    "label": "导出完成" if artifacts else "当前没有可导出的记录",
                    "artifacts": artifacts,
                }
                export_status = "done" if artifacts else "empty"
                if not artifacts and (field_missing_blocked_records > 0 or field_missing_diagnostics):
                    export_status = "warning"
                    export_payload.update(
                        {
                            "label": "存在字段缺失阻断，未生成导出文件",
                            "warning_code": "field_missing_blocked_records",
                            "warning_message": "存在字段缺失阻断，未生成导出文件",
                            "field_missing_blocked_records": field_missing_blocked_records,
                            "field_missing_diagnostics": field_missing_diagnostics,
                        }
                    )
                    export_warning_summary = {
                        "field_missing_blocked_records": field_missing_blocked_records,
                        "field_missing_diagnostics": field_missing_diagnostics,
                    }
                store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status=export_status,
                        payload=export_payload,
                    )
                )
                store.add_audit_entry(
                    "streaming_export",
                    {
                        "job_id": job_id,
                        "export_id": export_result.export_id,
                        "artifacts": artifacts,
                    },
                )

        duration_sec = time.monotonic() - started_at
        job_events = store.list_job_events(job_id, limit=100000)
        status_counts = store.get_job_event_counts(job_id)
        review_statuses = {"pending_review", "pending_mapping", "mapping_conflict"}
        has_review_backlog = any(str(event.get("status") or "") in review_statuses for event in job_events)
        warning_summary = _warning_summary_fields(job_events)
        final_status = "failed"
        if exit_code == 0:
            final_status = (
                "success_with_warnings"
                if job_info["exception_count"] > 0 or has_review_backlog or warning_summary
                else "success"
            )
        if manage_job_lifecycle:
            failure_summary = _resolve_failure_summary(download_result, job_events)
            archive_audit_summary = _download_archive_audit_summary(download_result)
            store.finish_job(
                job_id,
                status=final_status,
                summary={
                    "download_exit_code": download_result.exit_code,
                    "downloaded_count": job_info["downloaded_count"],
                    "persisted_count": job_info["persisted_count"],
                    "exception_count": job_info["exception_count"],
                    "pending_review": any(str(event.get("status") or "") == "pending_review" for event in job_events),
                    "pending_mapping": any(str(event.get("status") or "") == "pending_mapping" for event in job_events),
                    "pending_mapping_count": int(status_counts.get("pending_mapping", 0)),
                    "pending_review_count": int(status_counts.get("pending_review", 0)),
                    "mapping_conflict_count": int(status_counts.get("mapping_conflict", 0)),
                    "skipped_count": int(status_counts.get("skipped", 0)),
                    "export_artifacts": artifacts,
                    **archive_audit_summary,
                    **failure_summary,
                    **export_warning_summary,
                    **warning_summary,
                },
            )
        return StreamingDailyPipelineRunResult(
            exit_code=exit_code,
            log_file=log_file,
            db_path=db_path,
            job_id=job_id,
            start_date=start_text,
            end_date=end_text,
            duration_sec=round(duration_sec, 3),
            download_result=download_result,
            export_artifacts=artifacts,
            downloaded_count=job_info["downloaded_count"],
            persisted_count=job_info["persisted_count"],
            exception_count=job_info["exception_count"],
        )
    finally:
        close_cli_logger(logger)
