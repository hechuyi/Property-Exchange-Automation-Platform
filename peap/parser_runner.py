"""Structured parser runner reused by CLI and orchestration layers."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from peap_core.cli_support import close_cli_logger, setup_cli_logger

from .checks import run_self_check
from .parsing import COMPAT_PROFILE_FULL, COMPAT_PROFILE_PPE_READY
from .pipeline import ParserPipeline, ParserPipelineSettings, build_parser_pipeline_settings


@dataclass
class ParserRunRequest:
    self_check: bool = False
    dry_run: bool = False
    limit: int | None = None
    batch_flush_interval: int = 0
    html_root: str = ""
    log_dir: str = ""
    log_file: str | None = None
    compare_report_file: str | None = None
    compare_fields: list[str] = field(default_factory=list)
    parse_cache_enabled: bool = True
    parse_cache_db: str = ""
    progress_interval: int = 0
    verbose: bool = False


@dataclass
class ParserRunResult:
    kind: str
    exit_code: int
    log_file: str
    summary: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    compare_report_file: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)


def default_parser_html_root(config_obj: object) -> str:
    """Return the default HTML root for the parser.

    This function must remain consistent with _resolve_archive_root in daily_pipeline.py.
    Both must derive the same path so the parser scans the same directory where
    files were downloaded.

    Resolution order:
    1. ARCHIVE_ROOT if explicitly set
    2. DATA_ROOT/raw if that directory exists (legacy parser behavior)
    3. DATA_ROOT/outputs/submission (same fallback as _resolve_archive_root)
    """
    archive_root = str(getattr(config_obj, "ARCHIVE_ROOT", "") or "").strip()
    if archive_root:
        return os.path.abspath(archive_root)
    raw_root = os.path.abspath(os.path.join(str(config_obj.DATA_ROOT), "raw"))
    if os.path.isdir(raw_root):
        return raw_root
    return os.path.abspath(os.path.join(str(config_obj.DATA_ROOT), "outputs", "submission"))


def setup_parser_logger(
    *,
    verbose: bool,
    log_dir: str,
    log_file: str | None,
    config_obj: object,
) -> tuple[logging.Logger, str]:
    logger, resolved_log_file = setup_cli_logger(
        name="parser_v2",
        verbose=verbose,
        log_dir=log_dir,
        log_file=log_file,
        default_log_dir=str(config_obj.LOG_DIR),
        file_prefix="parser_v2",
        base_level=str(getattr(config_obj, "LOG_LEVEL", "INFO")),
        enable_file_logging=bool(getattr(config_obj, "LOG_TO_FILE", True)),
    )
    if resolved_log_file:
        logger.info("Parser log file: %s", resolved_log_file)
    return logger, resolved_log_file


def _parse_compare_fields(raw_value: str | None, config_obj: object) -> list[str]:
    compare_fields_raw = str(raw_value or "").strip()
    if compare_fields_raw:
        return [item.strip() for item in compare_fields_raw.split(",") if item.strip()]
    return list(config_obj.PARSER_DEFAULTS["compare_fields"])


def _build_request_pipeline_settings(
    *,
    pipeline_defaults: ParserPipelineSettings,
    parse_cache_db: str,
) -> ParserPipelineSettings:
    return ParserPipelineSettings(
        parse_cache_db=parse_cache_db,
        compare_report_dir=pipeline_defaults.compare_report_dir,
        output_target_settings=pipeline_defaults.output_target_settings,
        excel_schema_settings=pipeline_defaults.excel_schema_settings,
        excel_output_runtime=pipeline_defaults.excel_output_runtime,
    )


def _print_self_check_results(results: list[object], *, emit_console: bool) -> int:
    error_count = 0
    warning_count = 0
    for item in results:
        if item.passed and item.level != "warning":
            if emit_console:
                print(f"[OK] {item.name}: {item.message}")
            continue
        if item.level == "warning":
            warning_count += 1
            if emit_console:
                print(f"[WARN] {item.name}: {item.message}")
            continue
        error_count += 1
        if emit_console:
            print(f"[FAIL] {item.name}: {item.message}")

    if emit_console:
        print(f"Self-check summary: {len(results)} checks, {error_count} failed, {warning_count} warnings")
    return 0 if error_count == 0 else 1


def build_parser_run_request(
    args: object,
    *,
    config_obj: object,
) -> ParserRunRequest:
    parser_defaults = config_obj.PARSER_DEFAULTS
    parse_cache_enabled = bool(parser_defaults["parse_cache_enabled"])
    if bool(getattr(args, "no_parse_cache", False)):
        parse_cache_enabled = False

    return ParserRunRequest(
        self_check=bool(getattr(args, "self_check", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        limit=getattr(args, "limit", parser_defaults.get("limit")),
        batch_flush_interval=int(getattr(args, "batch_flush_interval", parser_defaults.get("batch_flush_interval", 0))),
        html_root=str(getattr(args, "html_root", None) or default_parser_html_root(config_obj)),
        log_dir=str(getattr(args, "log_dir", config_obj.LOG_DIR)),
        log_file=getattr(args, "log_file", None),
        compare_report_file=getattr(args, "compare_report_file", None),
        compare_fields=_parse_compare_fields(getattr(args, "compare_fields", None), config_obj),
        parse_cache_enabled=parse_cache_enabled,
        parse_cache_db=str(getattr(args, "parse_cache_db", config_obj.PARSER_CACHE_DB)),
        progress_interval=int(getattr(args, "progress_interval", parser_defaults.get("progress_interval", 0))),
        verbose=bool(getattr(args, "verbose", False)),
    )


def run_parser_request(
    request: ParserRunRequest,
    *,
    config_obj: object,
    emit_console: bool = True,
) -> ParserRunResult:
    pipeline_defaults = build_parser_pipeline_settings(config_obj)
    log_dir = str(request.log_dir or config_obj.LOG_DIR)
    html_root = str(request.html_root or default_parser_html_root(config_obj))
    parse_cache_db = str(request.parse_cache_db or pipeline_defaults.parse_cache_db)
    compare_fields = list(request.compare_fields or config_obj.PARSER_DEFAULTS["compare_fields"])
    request_pipeline_settings = _build_request_pipeline_settings(
        pipeline_defaults=pipeline_defaults,
        parse_cache_db=parse_cache_db,
    )

    logger, log_path = setup_parser_logger(
        verbose=bool(request.verbose),
        log_dir=log_dir,
        log_file=request.log_file,
        config_obj=config_obj,
    )
    try:
        if emit_console and log_path:
            print(f"Parser log file: {log_path}")

        logger.info(
            "Run context: cwd=%s pid=%s python=%s",
            os.getcwd(),
            os.getpid(),
            sys.version.split()[0],
        )
        logger.info(
            "Run request: %s",
            json.dumps(asdict(request), ensure_ascii=False, sort_keys=True),
        )

        if bool(request.self_check):
            results = run_self_check(
                html_root,
                logger=logger,
                config_obj=config_obj,
                pipeline_settings=request_pipeline_settings,
                parse_cache_enabled=bool(request.parse_cache_enabled),
            )
            exit_code = _print_self_check_results(results, emit_console=emit_console)
            logger.info("Self-check finished: exit_code=%s checks=%s", exit_code, len(results))
            return ParserRunResult(
                kind="parser_self_check",
                exit_code=exit_code,
                log_file=log_path,
                checks=[
                    {
                        "name": item.name,
                        "passed": item.passed,
                        "message": item.message,
                        "level": item.level,
                    }
                    for item in results
                ],
            )

        pipeline = ParserPipeline(
            html_root=html_root,
            dry_run=bool(request.dry_run),
            limit=request.limit,
            batch_flush_interval=int(request.batch_flush_interval),
            compare_report_file=request.compare_report_file,
            compare_fields=compare_fields,
            parse_cache_enabled=bool(request.parse_cache_enabled),
            settings=request_pipeline_settings,
            progress_interval=int(request.progress_interval),
            logger=logger,
        )
        summary = pipeline.run()

        summary_dict = {
            "processed": summary.processed,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
            "compare_diffs": summary.compare_diffs,
            "upsert_skipped": summary.excel_upsert_skipped,
            "cache_hits": summary.parse_cache_hits,
            "cache_misses": summary.parse_cache_misses,
            "cache_writes": summary.parse_cache_writes,
        }
        if emit_console:
            print(
                "Run summary: "
                f"processed={summary.processed}, "
                f"succeeded={summary.succeeded}, "
                f"failed={summary.failed}, "
                f"compare_diffs={summary.compare_diffs}, "
                f"upsert_skipped={summary.excel_upsert_skipped}, "
                f"cache_hits={summary.parse_cache_hits}, "
                f"cache_misses={summary.parse_cache_misses}, "
                f"cache_writes={summary.parse_cache_writes}"
            )
            if summary.compare_report_file:
                print(f"Compare report: {summary.compare_report_file}")
            if summary.errors:
                print("Top errors:")
                for error in summary.errors[:10]:
                    print(f"- {error}")

        logger.info(
            (
                "Run summary: processed=%s succeeded=%s failed=%s compare_diffs=%s "
                "upsert_skipped=%s cache_hits=%s cache_misses=%s cache_writes=%s errors=%s"
            ),
            summary.processed,
            summary.succeeded,
            summary.failed,
            summary.compare_diffs,
            summary.excel_upsert_skipped,
            summary.parse_cache_hits,
            summary.parse_cache_misses,
            summary.parse_cache_writes,
            len(summary.errors),
        )
        if summary.compare_report_file:
            logger.info("Compare report: %s", summary.compare_report_file)
        if summary.errors:
            logger.warning("Top errors (first 10):")
            for error in summary.errors[:10]:
                logger.warning("- %s", error)

        exit_code = 0 if summary.failed == 0 else 1
        logger.info("Run finished: exit_code=%s log_file=%s", exit_code, log_path)
        return ParserRunResult(
            kind="parser",
            exit_code=exit_code,
            log_file=log_path,
            compare_report_file=summary.compare_report_file,
            summary=summary_dict,
            errors=list(summary.errors),
        )
    finally:
        close_cli_logger(logger)


def run_parser_cli_args(
    args: object,
    *,
    config_obj: object,
    emit_console: bool = True,
) -> ParserRunResult:
    return run_parser_request(
        build_parser_run_request(args, config_obj=config_obj),
        config_obj=config_obj,
        emit_console=emit_console,
    )


def parser_result_to_summary_payload(result: ParserRunResult) -> dict[str, object]:
    if result.kind == "parser_self_check":
        return {
            "kind": result.kind,
            "exit_code": result.exit_code,
            "log_file": result.log_file,
            "checks": list(result.checks),
        }
    return {
        "kind": result.kind,
        "exit_code": result.exit_code,
        "log_file": result.log_file,
        "compare_report_file": result.compare_report_file,
        "summary": dict(result.summary),
        "errors": list(result.errors),
    }


__all__ = [
    "COMPAT_PROFILE_FULL",
    "COMPAT_PROFILE_PPE_READY",
    "ParserRunRequest",
    "ParserRunResult",
    "build_parser_run_request",
    "default_parser_html_root",
    "parser_result_to_summary_payload",
    "run_parser_request",
    "run_parser_cli_args",
    "setup_parser_logger",
]
