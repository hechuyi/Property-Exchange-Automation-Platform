"""Shared runtime helpers for PEAP entrypoints and scripts."""

from .cli_support import read_summary_json, setup_cli_logger, write_summary_json
from .runtime import (
    load_json_file,
    load_json_object,
    load_runtime_config,
    normalize_path,
    read_optional_json_object,
    resolve_path,
    resolve_runtime_config_file,
    write_json_file,
    write_json_file_atomic,
)

__all__ = [
    "load_json_file",
    "load_json_object",
    "load_runtime_config",
    "normalize_path",
    "read_optional_json_object",
    "read_summary_json",
    "resolve_path",
    "resolve_runtime_config_file",
    "setup_cli_logger",
    "write_json_file_atomic",
    "write_json_file",
    "write_summary_json",
]
