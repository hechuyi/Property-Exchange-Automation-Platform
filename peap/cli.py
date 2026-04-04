"""CLI entrypoint for v2."""

import argparse
import datetime as dt
from typing import Iterable, Optional

from peap_core.cli_support import write_summary_json

from .parser_runner import (
    default_parser_html_root,
    parser_result_to_summary_payload,
    run_parser_cli_args,
)


def _load_default_cli_config() -> object:
    from config import config as default_config

    return default_config


def _build_parser(config_obj: object | None = None) -> argparse.ArgumentParser:
    resolved_config = config_obj or _load_default_cli_config()
    parser_defaults = resolved_config.PARSER_DEFAULTS
    compare_fields_default = ",".join(parser_defaults["compare_fields"])
    parser = argparse.ArgumentParser(description="Property parser v2")
    parser.add_argument("--self-check", action="store_true", help="Run project checks and exit")
    parser.add_argument("--dry-run", action="store_true", help="Parse files but do not write Excel output")
    parser.add_argument(
        "--limit",
        type=int,
        default=parser_defaults["limit"],
        help="Only process first N html files",
    )
    parser.add_argument(
        "--batch-flush-interval",
        type=int,
        default=parser_defaults["batch_flush_interval"],
        help="Flush Excel batch every N files (0 means only flush at end)",
    )
    parser.add_argument(
        "--html-root",
        default=default_parser_html_root(resolved_config),
        help="HTML root folder (default: <data_root>/raw when available)",
    )
    parser.add_argument("--log-dir", default=resolved_config.LOG_DIR, help="Directory for parser log files")
    parser.add_argument("--log-file", default=None, help="Explicit parser log file path")
    parser.add_argument(
        "--no-parse-cache",
        action="store_true",
        help="Disable incremental parse cache and force full parse",
    )
    parser.add_argument(
        "--parse-cache-db",
        default=resolved_config.PARSER_CACHE_DB,
        help="Path to parse cache sqlite db file",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=parser_defaults["progress_interval"],
        help="Log progress every N processed files (0 to disable periodic progress logs)",
    )
    parser.add_argument("--summary-json", default=None, help="Optional json path for structured parser summary")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    resolved_config = _load_default_cli_config()
    parser = _build_parser(resolved_config)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run_parser_cli_args(
        args,
        config_obj=resolved_config,
        emit_console=True,
    )
    write_summary_json(
        args.summary_json,
        {
            **parser_result_to_summary_payload(result),
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        logger=None,
        label="Parser summary json",
    )
    return result.exit_code


def build_parser(config_obj: object | None = None) -> argparse.ArgumentParser:
    return _build_parser(config_obj)

