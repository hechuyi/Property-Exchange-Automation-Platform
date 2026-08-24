"""CLI entrypoint for PostProcess Engine."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Iterable, Optional

from peap_core.cli_support import write_summary_json

from .runner import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_DIR,
    postprocess_result_to_summary_payload,
    run_postprocess_cli_args,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PostProcess Engine")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run postprocess pipeline")
    run_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to PPE config file (json/yaml)",
    )
    run_parser.add_argument(
        "--mode",
        choices=["plan", "apply"],
        default=None,
        help="Override mode in config",
    )
    run_parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="Directory for PPE log files")
    run_parser.add_argument("--log-file", default=None, help="Explicit PPE log file path")
    run_parser.add_argument("--summary-json", default=None, help="Optional json path for structured PPE summary")
    run_parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    run_parser.add_argument(
        "--skip-unresolved-list",
        action="store_true",
        help="Skip exporting unresolved source-type query list after run",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        # Support running without explicit sub-command.
        synthetic_argv = ["run", *(list(argv) if argv is not None else sys.argv[1:])]
        args = parser.parse_args(synthetic_argv)

    result = run_postprocess_cli_args(
        args,
        emit_console=True,
    )
    write_summary_json(
        getattr(args, "summary_json", None),
        {
            **postprocess_result_to_summary_payload(result),
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        label="PPE summary json",
    )
    return result.exit_code
