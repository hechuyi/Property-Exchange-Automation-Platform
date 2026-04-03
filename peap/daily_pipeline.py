"""Structured daily pipeline orchestration."""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from dataclasses import dataclass

from peap_core.cli_support import close_cli_logger, setup_cli_logger
from peap_postprocess.postprocess_engine.runner import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_DIR,
    PostProcessRunRequest,
    PostProcessRunResult,
    run_postprocess_request,
)

from .download_oneclick import (
    DownloadOneClickRequest,
    DownloadOneClickRunResult,
    run_download_oneclick,
)
from .download_runner import DownloadRunRequest
from .parser_runner import (
    ParserRunRequest,
    ParserRunResult,
    default_parser_html_root,
    run_parser_request,
)
from .streaming_daily_pipeline import run_streaming_daily_pipeline


@dataclass
class DailyPipelineRunResult:
    exit_code: int
    log_file: str
    start_date: str
    end_date: str
    duration_sec: float
    download_result: DownloadOneClickRunResult | None = None
    parser_result: ParserRunResult | None = None
    postprocess_result: PostProcessRunResult | None = None


def today_local() -> dt.date:
    return dt.date.today()


def parse_date(raw_value: str | None, *, default: dt.date) -> dt.date:
    if raw_value is None or not str(raw_value).strip():
        return default
    return dt.datetime.strptime(str(raw_value).strip(), "%Y-%m-%d").date()


def setup_daily_logger(*, verbose: bool, config_obj: object) -> tuple[logging.Logger, str]:
    logger, log_file = setup_cli_logger(
        name="daily_oneclick",
        verbose=verbose,
        log_dir=str(config_obj.LOG_DIR),
        log_file=None,
        default_log_dir=str(config_obj.LOG_DIR),
        file_prefix="daily_oneclick",
        base_level=str(getattr(config_obj, "LOG_LEVEL", "INFO")),
        enable_file_logging=bool(getattr(config_obj, "LOG_TO_FILE", True)),
    )
    if log_file:
        logger.info("Daily one-click log file: %s", log_file)
    return logger, log_file


def _to_int(summary: dict[str, object], key: str) -> int:
    raw = str(summary.get(key, "") or "").strip()
    if not raw:
        return 0
    try:
        return int(float(raw))
    except Exception:
        return 0


def _download_counts(result: DownloadOneClickRunResult | None) -> tuple[int, int, int]:
    if result is None:
        return 0, 0, 0
    summary = result.aggregate_summary
    success = _to_int(summary, "saved")
    failed = max(_to_int(summary, "errors"), _to_int(summary, "detail_failed"))
    skipped = (
        _to_int(summary, "list_date_skipped")
        + _to_int(summary, "detail_date_skipped")
        + _to_int(summary, "resume_skipped")
        + _to_int(summary, "duplicate_skipped")
        + _to_int(summary, "missing_xmid_skipped")
    )
    return success, failed, skipped


def _parse_counts(result: ParserRunResult | None) -> tuple[int, int, int]:
    if result is None:
        return 0, 0, 0
    summary = result.summary
    return (
        _to_int(summary, "succeeded"),
        _to_int(summary, "failed"),
        _to_int(summary, "upsert_skipped"),
    )


def _postprocess_counts(result: PostProcessRunResult | None) -> tuple[int, int, int]:
    if result is None:
        return 0, 0, 0
    summary = result.summary
    discovered = _to_int(summary, "discovered_files")
    failed = _to_int(summary, "failed_files")
    success = max(discovered - failed, 0)
    skipped = max(discovered - success - failed, 0)
    return success, failed, skipped


def print_final_summary(
    *,
    start_text: str,
    end_text: str,
    total_elapsed_sec: float,
    download_result: DownloadOneClickRunResult | None,
    parser_result: ParserRunResult | None,
    postprocess_result: PostProcessRunResult | None,
) -> None:
    download_success, download_failed, download_skipped = _download_counts(download_result)
    parse_success, parse_failed, parse_skipped = _parse_counts(parser_result)
    post_success, post_failed, post_skipped = _postprocess_counts(postprocess_result)

    print("=" * 72)
    print("Daily Pipeline Final Summary")
    print("=" * 72)
    print(f"date_window: {start_text} -> {end_text}")
    print(f"total_elapsed_sec: {total_elapsed_sec:.2f}")
    print(f"download: success={download_success}, failed={download_failed}, skipped={download_skipped}")
    print(f"parse: success={parse_success}, failed={parse_failed}, skipped={parse_skipped}")
    print(f"postprocess: success={post_success}, failed={post_failed}, skipped={post_skipped}")
    print("=" * 72)


def _resolve_archive_root(config_obj: object, args: object) -> str:
    """Resolve the download output root using the same policy as streaming_daily_pipeline."""
    archive_root = getattr(args, "archive_root", None) or getattr(config_obj, "ARCHIVE_ROOT", "")
    fallback = os.path.join(str(config_obj.DATA_ROOT), "outputs", "submission")
    return os.path.abspath(str(archive_root or fallback))


def _build_download_request(
    args: object,
    *,
    start_text: str,
    end_text: str,
    config_obj: object,
    output_root: str,
) -> DownloadRunRequest:
    defaults = config_obj.DOWNLOADER_DEFAULTS
    return DownloadRunRequest(
        exchange=str(getattr(args, "exchange", "all")),
        project_type=str(getattr(args, "project_type", "all")),
        list_tasks=False,
        output_root=str(output_root),
        force_manual_root=False,
        start_date=start_text,
        end_date=end_text,
        page_size=getattr(args, "page_size", None),
        max_pages=getattr(args, "max_pages", None),
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
    )


def _build_parser_request(args: object, *, config_obj: object, html_root: str) -> ParserRunRequest:
    parser_defaults = config_obj.PARSER_DEFAULTS
    return ParserRunRequest(
        self_check=False,
        dry_run=False,
        limit=parser_defaults["limit"],
        batch_flush_interval=parser_defaults["batch_flush_interval"],
        html_root=str(html_root),
        log_dir=str(config_obj.LOG_DIR),
        log_file=None,
        compat_profile=parser_defaults["compat_profile"],
        dual_run_compare=False,
        compare_report_file=None,
        compare_fields=list(parser_defaults["compare_fields"]),
        parse_cache_enabled=bool(parser_defaults["parse_cache_enabled"]),
        parse_cache_db=str(config_obj.PARSER_CACHE_DB),
        progress_interval=parser_defaults["progress_interval"],
        verbose=bool(getattr(args, "verbose", False)),
    )


def _build_postprocess_request(args: object) -> PostProcessRunRequest:
    return PostProcessRunRequest(
        config_path=str(getattr(args, "postprocess_config", DEFAULT_CONFIG_PATH)),
        mode=str(getattr(args, "postprocess_mode", "apply")),
        log_dir=DEFAULT_LOG_DIR,
        log_file=None,
        verbose=bool(getattr(args, "verbose", False)),
        skip_unresolved_list=False,
    )


def run_daily_pipeline(
    args: object,
    *,
    config_obj: object,
    emit_console: bool = True,
) -> DailyPipelineRunResult:
    if bool(getattr(args, "streaming", False)):
        return run_streaming_daily_pipeline(
            args,
            config_obj=config_obj,
            emit_console=emit_console,
        )
    logger, log_file = setup_daily_logger(
        verbose=bool(getattr(args, "verbose", False)),
        config_obj=config_obj,
    )
    try:
        if emit_console and log_file:
            print(f"Daily one-click log file: {log_file}")

        today = today_local()
        default_start = today - dt.timedelta(days=1)
        start_date = parse_date(getattr(args, "start_date", None), default=default_start)
        end_date = parse_date(getattr(args, "end_date", None), default=today)
        if start_date > end_date:
            if emit_console:
                print("start-date cannot be later than end-date")
            return DailyPipelineRunResult(
                exit_code=2,
                log_file=log_file,
                start_date=str(start_date),
                end_date=str(end_date),
                duration_sec=0.0,
            )

        start_text = start_date.strftime("%Y-%m-%d")
        end_text = end_date.strftime("%Y-%m-%d")
        logger.info(
            "Daily pipeline window: start_date=%s end_date=%s exchange=%s project_type=%s",
            start_text,
            end_text,
            getattr(args, "exchange", "all"),
            getattr(args, "project_type", "all"),
        )

        run_started = time.monotonic()
        download_result: DownloadOneClickRunResult | None = None
        parser_result: ParserRunResult | None = None
        postprocess_result: PostProcessRunResult | None = None

        # Resolve archive root once, use for both download output and parser input
        resolved_archive_root = _resolve_archive_root(config_obj, args)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_file = os.path.join(str(config_obj.LOG_DIR), f"split_plan_onclick_{timestamp}.json")
        download_request = DownloadOneClickRequest(
            download_request=_build_download_request(
                args,
                start_text=start_text,
                end_text=end_text,
                config_obj=config_obj,
                output_root=resolved_archive_root,
            ),
            plan_file=plan_file,
            keep_plan=False,
            with_refresh=bool(getattr(args, "with_refresh", False)),
        )
        download_result = run_download_oneclick(
            download_request,
            config_obj=config_obj,
            emit_console=emit_console,
        )
        if download_result.exit_code != 0:
            duration_sec = time.monotonic() - run_started
            print_final_summary(
                start_text=start_text,
                end_text=end_text,
                total_elapsed_sec=duration_sec,
                download_result=download_result,
                parser_result=parser_result,
                postprocess_result=postprocess_result,
            )
            return DailyPipelineRunResult(
                exit_code=download_result.exit_code,
                log_file=log_file,
                start_date=start_text,
                end_date=end_text,
                duration_sec=round(duration_sec, 3),
                download_result=download_result,
            )

        parser_result = run_parser_request(
            _build_parser_request(args, config_obj=config_obj, html_root=resolved_archive_root),
            config_obj=config_obj,
            emit_console=emit_console,
        )
        if parser_result.exit_code != 0:
            duration_sec = time.monotonic() - run_started
            print_final_summary(
                start_text=start_text,
                end_text=end_text,
                total_elapsed_sec=duration_sec,
                download_result=download_result,
                parser_result=parser_result,
                postprocess_result=postprocess_result,
            )
            return DailyPipelineRunResult(
                exit_code=parser_result.exit_code,
                log_file=log_file,
                start_date=start_text,
                end_date=end_text,
                duration_sec=round(duration_sec, 3),
                download_result=download_result,
                parser_result=parser_result,
            )

        postprocess_result = run_postprocess_request(
            _build_postprocess_request(args),
            emit_console=emit_console,
        )
        duration_sec = time.monotonic() - run_started
        print_final_summary(
            start_text=start_text,
            end_text=end_text,
            total_elapsed_sec=duration_sec,
            download_result=download_result,
            parser_result=parser_result,
            postprocess_result=postprocess_result,
        )
        exit_code = postprocess_result.exit_code
        if exit_code == 0:
            logger.info("Daily pipeline DONE")
        return DailyPipelineRunResult(
            exit_code=exit_code,
            log_file=log_file,
            start_date=start_text,
            end_date=end_text,
            duration_sec=round(duration_sec, 3),
            download_result=download_result,
            parser_result=parser_result,
            postprocess_result=postprocess_result,
        )
    finally:
        close_cli_logger(logger)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DailyPipelineRunResult",
    "parse_date",
    "print_final_summary",
    "run_daily_pipeline",
    "today_local",
]
