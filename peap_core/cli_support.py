"""Shared helpers for CLI entrypoints."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from typing import Any, Mapping, Optional

from .runtime import read_optional_json_object, write_json_file


def close_cli_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _resolve_cli_log_level(verbose: bool, base_level: int | str | None) -> int:
    if verbose:
        return logging.DEBUG
    if isinstance(base_level, int) and not isinstance(base_level, bool):
        return base_level
    if isinstance(base_level, str):
        resolved = getattr(logging, base_level.strip().upper(), None)
        if isinstance(resolved, int):
            return resolved
    return logging.INFO


def setup_cli_logger(
    *,
    name: str,
    verbose: bool,
    log_dir: str,
    log_file: Optional[str],
    default_log_dir: str,
    file_prefix: str,
    base_level: int | str | None = None,
    enable_file_logging: bool = True,
) -> tuple[logging.Logger, str]:
    level = _resolve_cli_log_level(verbose, base_level)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    close_cli_logger(logger)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(console_handler)

    resolved_log_file = str(log_file or "").strip()
    if not resolved_log_file and not enable_file_logging:
        return logger, ""
    try:
        if not resolved_log_file:
            safe_dir = str(log_dir or default_log_dir).strip() or str(default_log_dir)
            os.makedirs(safe_dir, exist_ok=True)
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            resolved_log_file = os.path.join(safe_dir, f"{file_prefix}_{timestamp}.log")
        resolved_log_file = os.path.abspath(resolved_log_file)
        parent = os.path.dirname(resolved_log_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        file_handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to enable file logging: %s (%s)", resolved_log_file or default_log_dir, exc)
        return logger, ""
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    logger.info("%s log file: %s", name, resolved_log_file)
    return logger, resolved_log_file


def read_summary_json(path_value: str) -> dict[str, Any] | None:
    payload = read_optional_json_object(path_value, encoding="utf-8")
    return payload if isinstance(payload, dict) else None


def write_summary_json(
    path_value: str | None,
    payload: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
    label: str = "Summary json",
) -> str:
    target = str(path_value or "").strip()
    if not target:
        return ""
    output_path = write_json_file(
        target,
        dict(payload),
        encoding="utf-8",
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if logger is not None:
        logger.info("%s: %s", label, output_path)
    return output_path
