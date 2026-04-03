"""Standalone CLI for automated downloading."""

import argparse
import datetime as dt
import logging
import os
import sys
import time
from typing import Iterable, Optional, Tuple

from peap_core.cli_support import close_cli_logger, setup_cli_logger, write_summary_json

from .download_cli_payloads import (
    download_error_to_summary_payload,
    download_result_to_summary_payload,
    download_run_finished_message,
    download_task_list_to_summary_payload,
    format_task_list_lines,
)
from .download_runner import (
    DownloadRunnerError,
)
from .download_runner import (
    build_task_list_payload as _build_task_list_payload,
)
from .download_runner import (
    run_download_cli_args as _run_download_cli_args,
)
from .download_tasks import (
    PROJECT_TYPE_CHOICES,
    exchange_choices,
)


def _load_default_download_config() -> object:
    from config import config as default_config

    return default_config


_SESSION_LOG_FILE: Optional[str] = None


def _setup_logger(
    verbose: bool,
    *,
    log_dir: str,
    log_file: Optional[str],
    default_log_dir: str,
    config_obj: object,
) -> Tuple[logging.Logger, str]:
    global _SESSION_LOG_FILE

    resolved_log_file = str(log_file or "").strip()
    if not resolved_log_file and _SESSION_LOG_FILE:
        resolved_log_file = _SESSION_LOG_FILE
    logger, resolved_log_file = setup_cli_logger(
        name="downloader_v2",
        verbose=verbose,
        log_dir=log_dir,
        log_file=resolved_log_file,
        default_log_dir=default_log_dir,
        file_prefix="download",
        base_level="INFO",
        enable_file_logging=True,
    )
    _SESSION_LOG_FILE = resolved_log_file
    if resolved_log_file:
        logger.info("Download log file: %s", resolved_log_file)
    return logger, resolved_log_file


def build_parser(config_obj: object | None = None) -> argparse.ArgumentParser:
    resolved_config = config_obj or _load_default_download_config()
    defaults = resolved_config.DOWNLOADER_DEFAULTS
    task_exchange_choices = exchange_choices(resolved_config)
    parser = argparse.ArgumentParser(description="Automated page downloader")
    parser.add_argument(
        "--exchange",
        choices=task_exchange_choices + ["all"],
        default=defaults["exchange"],
        help="Target exchange code (or all registered exchanges)",
    )
    parser.add_argument(
        "--project-type",
        choices=PROJECT_TYPE_CHOICES + ["all"],
        default=defaults["project_type"],
        help="Target project type (or all registered types)",
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="List registered downloader tasks and exit",
    )
    parser.add_argument(
        "--output-root",
        default=str(getattr(resolved_config, "AUTO_HTML_FOLDER", "") or ""),
        help="Output root folder for auto-downloaded pages",
    )
    parser.add_argument(
        "--force-manual-root",
        action="store_true",
        help=f"Allow writing into manual folder ({resolved_config.HTML_FOLDER})",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Filter disclosure start date >= this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Filter disclosure start date <= this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=None,
        help="List page size (task-specific default if omitted)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional max pages",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=defaults["concurrency"],
        help="Concurrent detail-page workers",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=defaults["resume"],
        help="Skip when target html and *_files already exist (--resume/--no-resume)",
    )
    parser.add_argument(
        "--save-json",
        action=argparse.BooleanOptionalAction,
        default=defaults["save_json"],
        help="Save download sidecar json files (--save-json/--no-save-json)",
    )
    parser.add_argument(
        "--sse-ssl-verify",
        action=argparse.BooleanOptionalAction,
        default=defaults.get("sse_ssl_verify", True),
        help="Enable SSE certificate verification (--sse-ssl-verify/--no-sse-ssl-verify)",
    )
    parser.add_argument(
        "--sse-ca-bundle",
        default=defaults.get("sse_ca_bundle"),
        help="Optional custom CA bundle path for SSE HTTPS requests",
    )
    parser.add_argument(
        "--log-dir",
        default=resolved_config.LOG_DIR,
        help="Directory for auto-generated download log files",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Explicit download log file path (overrides --log-dir)",
    )
    parser.add_argument("--summary-json", default=None, help="Optional json path for structured download summary")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logs",
    )
    parser.add_argument(
        "--auto-split",
        action=argparse.BooleanOptionalAction,
        default=defaults["auto_split"],
        help="Auto split date range by estimated candidate volume before detail download",
    )
    parser.add_argument(
        "--split-candidates",
        type=int,
        default=defaults["split_candidates"],
        help="Target max estimated candidates per chunk when --auto-split is enabled",
    )
    parser.add_argument(
        "--split-min-days",
        type=int,
        default=defaults["split_min_days"],
        help="Minimum window length (days) to allow further split in auto-split mode",
    )
    parser.add_argument(
        "--split-max-depth",
        type=int,
        default=defaults["split_max_depth"],
        help="Maximum recursive split depth in auto-split mode",
    )
    parser.add_argument(
        "--split-plan-only",
        action="store_true",
        help="Only print split plan, do not execute downloads",
    )
    parser.add_argument(
        "--split-plan-file",
        default=None,
        help="Optional json file path to save/load split plans",
    )
    parser.add_argument(
        "--split-use-plan",
        action="store_true",
        help="Load split chunks from --split-plan-file and skip split planning scan",
    )
    parser.add_argument(
        "--split-mode",
        choices=["fast", "steady"],
        default=defaults["split_mode"],
        help=(
            "Chunk execution strategy in auto-split mode: "
            "fast=skip estimated zero-candidate chunks, steady=execute every chunk"
        ),
    )
    parser.add_argument(
        "--chunk-state-file",
        default=None,
        help=(
            "Optional chunk state file used for resumable auto-split execution; "
            "default is <split-plan-file>.state.json when split plan file is provided"
        ),
    )
    return parser


_build_parser = build_parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    resolved_config = _load_default_download_config()
    parser = build_parser(resolved_config)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_tasks:
        tasks = _build_task_list_payload(resolved_config)
        for line in format_task_list_lines(tasks):
            print(line)
        write_summary_json(
            args.summary_json,
            download_task_list_to_summary_payload(
                tasks,
                generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            label="Download summary json",
        )
        return 0

    logger, log_path = _setup_logger(
        args.verbose,
        log_dir=str(args.log_dir),
        log_file=args.log_file,
        default_log_dir=str(resolved_config.LOG_DIR),
        config_obj=resolved_config,
    )
    try:
        if log_path:
            print(f"Download log file: {log_path}")
        run_started_at = dt.datetime.now()
        run_started_monotonic = time.monotonic()
        logger.info(
            "Run context: cwd=%s pid=%s python=%s executable=%s",
            os.getcwd(),
            os.getpid(),
            sys.version.split()[0],
            sys.executable,
        )
        try:
            run_result = _run_download_cli_args(
                args,
                logger=logger,
                config_obj=resolved_config,
            )
        except DownloadRunnerError as exc:
            write_summary_json(
                args.summary_json,
                download_error_to_summary_payload(
                    log_file=log_path,
                    split_plan_only=bool(args.split_plan_only),
                    error=str(exc),
                    generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
                logger=logger,
                label="Download summary json",
            )
            return 2

        run_finished_at = dt.datetime.now()
        duration_seconds = time.monotonic() - run_started_monotonic
        exit_code = run_result.exit_code
        write_summary_json(
            args.summary_json,
            download_result_to_summary_payload(
                run_result,
                log_file=log_path,
                split_plan_only=bool(args.split_plan_only),
                generated_at=run_finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                start=run_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                end=run_finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                duration_sec=duration_seconds,
            ),
            logger=logger,
            label="Download summary json",
        )
        final_message = download_run_finished_message(
            result=run_result,
            log_file=log_path,
            start=run_started_at.strftime("%Y-%m-%d %H:%M:%S"),
            end=run_finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration_sec=duration_seconds,
        )
        print(final_message)
        logger.info(final_message)
        return exit_code
    finally:
        close_cli_logger(logger)


if __name__ == "__main__":
    raise SystemExit(main())
