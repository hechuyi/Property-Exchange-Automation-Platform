"""Streaming one-click pipeline: download, ingest item-by-item, then export ready records."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict

from peap_core.cli_support import close_cli_logger, setup_cli_logger

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
    message = _stage_error_message(payload)
    if (
        "suaee.com/manageprojectweb/foreign/project/queryAllNew" in message
        and "HTTP Error 404: Not Found" in message
    ):
        return "sse_list_api_not_found"
    return ""


def _stage_display_error_message(payload: Dict[str, Any]) -> str:
    error_type = _stage_error_type(payload)
    if error_type == "sse_list_api_not_found":
        return "上交所列表接口 queryAllNew 返回 404，当前扫描已中止"
    return _stage_error_message(payload)


def _failure_summary_fields(job_events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(job_events):
        if str(event.get("status") or "").strip() != "failed":
            continue
        stage = str(event.get("stage") or "").strip()
        payload = dict(event.get("payload") or {}) if isinstance(event.get("payload"), dict) else {}
        failure_code = str(event.get("error_type") or payload.get("error_code") or "").strip()
        failure_message = str(event.get("error_message") or "").strip()
        if not failure_code and not failure_message:
            continue
        return {
            "failure_code": failure_code,
            "failure_stage": stage,
            "failure_message": failure_message,
        }
    return {}


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


def _load_rules_config(config_path: str | None) -> Dict[str, Any]:
    target = str(config_path or "").strip()
    if not target or not os.path.isfile(target):
        return {}
    try:
        from peap_postprocess.postprocess_engine.config import load_config

        config = load_config(target)
        return {
            rule_id: {
                "enabled": bool(rule.enabled),
                "priority": int(rule.priority),
                "params": dict(rule.params),
            }
            for rule_id, rule in dict(config.rules or {}).items()
        }
    except Exception:
        try:
            with open(target, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            out: Dict[str, Any] = {}
            for item in raw_rules:
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("id") or "").strip()
                if rule_id:
                    out[rule_id] = item
            return out
        if isinstance(raw_rules, dict):
            return dict(raw_rules)
        return {}


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
    if max_pages is None:
        try:
            start_date = dt.datetime.strptime(str(start_text), "%Y-%m-%d").date()
            end_date = dt.datetime.strptime(str(end_text), "%Y-%m-%d").date()
        except ValueError:
            max_pages = None
        else:
            if abs((end_date - start_date).days) <= 7:
                max_pages = 10

    return DownloadRunRequest(
        exchange=str(getattr(args, "exchange", "all")),
        project_type=str(getattr(args, "project_type", "all")),
        list_tasks=False,
        output_root=str(output_root or getattr(config_obj, "AUTO_HTML_FOLDER", "")),
        force_manual_root=False,
        start_date=start_text,
        end_date=end_text,
        page_size=getattr(args, "page_size", None),
        max_pages=max_pages,
        concurrency=int(getattr(args, "concurrency", defaults["concurrency"])),
        resume=not bool(getattr(args, "no_resume", False)),
        save_json=bool(getattr(args, "save_json", False)),
        sse_ssl_verify=bool(defaults.get("sse_ssl_verify", True)),
        sse_ssl_fallback_insecure=bool(defaults.get("sse_ssl_fallback_insecure", True)),
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
) -> StreamingDailyPipelineRunResult:
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
        db_path = os.path.abspath(
            str(
                getattr(args, "streaming_db", "")
                or getattr(config_obj, "STREAMING_DB_PATH", "")
                or os.path.join(str(config_obj.LOG_DIR), "streaming_ingest.sqlite3")
            )
        )
        resolved_archive_root = os.path.abspath(
            str(archive_root or getattr(config_obj, "ARCHIVE_ROOT", "") or os.path.join(str(config_obj.DATA_ROOT), "outputs", "submission"))
        )
        resolved_export_root = os.path.abspath(
            str(export_root or getattr(config_obj, "OUTPUT_EXCEL_DIR", ""))
        )
        rules_config = _load_rules_config(getattr(args, "postprocess_config", None))
        store = StreamingStore(db_path)
        run_streaming_store_maintenance(store)
        runner = StreamingIngestRunner(
            store=store,
            archive_root=resolved_archive_root,
            rules_config=rules_config,
            dependencies=StreamingIngestDependencies(),
        )
        service = StreamingIngestService(store=store, runner=runner)
        service.start()

        try:
            job_id = store.create_job(
                str(job_type),
                metadata={
                    "start_date": start_text,
                    "end_date": end_text,
                    "exchange": getattr(args, "exchange", "all"),
                    "project_type": getattr(args, "project_type", "all"),
                    "archive_root": resolved_archive_root,
                    "export_root": resolved_export_root,
                },
            )
            if not str(job_id or "").strip():
                raise RuntimeError(f"{job_type} job did not provide job_id")
            if job_created_callback is not None:
                try:
                    job_created_callback(job_id, db_path)
                except Exception:
                    pass
            started_at = time.monotonic()
            callback = service.build_callback(job_id=job_id)

            def _stage_callback(payload: Dict[str, Any]) -> None:
                phase_code = str(payload.get("phase_code") or "").strip()
                if not phase_code:
                    return
                store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage=phase_code,
                        status=str(payload.get("status") or "running"),
                        error_type=_stage_error_type(payload),
                        error_message=_stage_display_error_message(payload),
                        payload=dict(payload),
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
                existing_project_codes=frozenset(
                    store.list_existing_project_codes(states=["ready", "pending_mapping", "skipped", "conflict"])
                ),
                existing_candidate_tokens=frozenset(
                    store.list_existing_candidate_tokens(states=["ready", "pending_mapping", "skipped", "conflict"])
                ),
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
        artifacts: list[str] = []
        exit_code = download_result.exit_code
        should_auto_export = (
            not bool(getattr(args, "no_auto_export", False))
            if auto_export is None
            else bool(auto_export)
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
                export_result = run_ready_export(
                    store,
                    ExportRequest(
                        date_from=start_text,
                        date_to=end_text,
                        business_types=[],
                        mode="incremental",
                        output_dir=resolved_export_root,
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
                store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status="done" if artifacts else "empty",
                        payload={
                            "label": "导出完成" if artifacts else "当前没有可导出的记录",
                            "artifacts": artifacts,
                        },
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
        has_pending_mapping = any(str(event.get("status") or "") == "pending_mapping" for event in job_events)
        final_status = "failed"
        if exit_code == 0:
            final_status = "success_with_warnings" if job_info["exception_count"] > 0 or has_pending_mapping else "success"
        store.finish_job(
            job_id,
            status=final_status,
            summary={
                "download_exit_code": download_result.exit_code,
                "downloaded_count": job_info["downloaded_count"],
                "persisted_count": job_info["persisted_count"],
                "exception_count": job_info["exception_count"],
                "pending_mapping": has_pending_mapping,
                "pending_mapping_count": int(status_counts.get("pending_mapping", 0)),
                "skipped_count": int(status_counts.get("skipped", 0)),
                "export_artifacts": artifacts,
                **(_failure_summary_fields(job_events) if exit_code != 0 else {}),
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
