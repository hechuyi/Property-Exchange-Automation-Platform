#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central configuration loaded from deployment config file.

No runtime default values are hardcoded in code. Deployment values must be
provided by a JSON config file.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable

from peap_core.runtime import (
    load_json_object,
)
from peap_core.runtime import (
    load_runtime_config as _load_runtime_config,
)
from peap_core.runtime import (
    normalize_path as _normalize_path,
)
from peap_core.runtime import (
    resolve_path as _resolve_path,
)


def _paths_equal(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(os.path.abspath(left))) == os.path.normcase(
        os.path.normpath(os.path.abspath(right))
    )


def _require_object(payload: Any, name: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be an object")
    return payload


def _require_string(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _require_optional_string(payload: Dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    trimmed = value.strip()
    return trimmed or None


def _require_int(payload: Dict[str, Any], key: str, *, min_value: int | None = None) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"{key} must be >= {min_value}")
    return parsed


def _require_optional_int(payload: Dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer or null")
    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{key} must be an integer or null") from exc


def _parse_bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(int(value))
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{key} must be a boolean")


def _require_bool(payload: Dict[str, Any], key: str) -> bool:
    return _parse_bool(payload.get(key), key=key)


def _require_string_list(payload: Any, *, name: str) -> list[str]:
    if not isinstance(payload, list):
        raise ValueError(f"{name} must be an array")
    values: list[str] = []
    for idx, item in enumerate(payload):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{idx}] must be a non-empty string")
        values.append(item.strip())
    return values


def _require_string_mapping(payload: Any, *, name: str) -> Dict[str, str]:
    obj = _require_object(payload, name)
    out: Dict[str, str] = {}
    for raw_key, raw_value in obj.items():
        key = str(raw_key or "").strip()
        if not key:
            raise ValueError(f"{name} contains an empty key")
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"{name}.{key} must be a non-empty string")
        out[key] = raw_value.strip()
    return out


def _require_keys(payload: Dict[str, Any], *, name: str, required_keys: Iterable[str]) -> None:
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise ValueError(f"{name} missing keys: {missing}")


def _load_runtime_config_payload(
    *,
    project_root: str,
    runtime_config_file: str | None = None,
) -> tuple[str, Dict[str, Any]]:
    explicit_file = _normalize_path(runtime_config_file or "")
    if explicit_file:
        if not os.path.isfile(explicit_file):
            raise RuntimeError(f"Runtime config file not found: {explicit_file}")
        try:
            payload = load_json_object(
                explicit_file,
                encoding="utf-8-sig",
                label="runtime config",
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to load runtime config: {explicit_file} ({exc})") from exc
        return explicit_file, payload
    return _load_runtime_config(project_root)


class Config:
    """Project-wide runtime configuration."""

    def __init__(
        self,
        *,
        project_root: str | None = None,
        runtime_config_file: str | None = None,
    ) -> None:
        self.PROJECT_ROOT = os.path.abspath(project_root or os.path.dirname(__file__))
        self.reload(runtime_config_file=runtime_config_file)

    @classmethod
    def from_runtime(
        cls,
        *,
        project_root: str | None = None,
        runtime_config_file: str | None = None,
    ) -> "Config":
        return cls(project_root=project_root, runtime_config_file=runtime_config_file)

    def reload(self, *, runtime_config_file: str | None = None) -> "Config":
        resolved_file, payload = _load_runtime_config_payload(
            project_root=self.PROJECT_ROOT,
            runtime_config_file=runtime_config_file,
        )
        settings = self._build_settings(
            runtime_config_file=resolved_file,
            runtime_raw=payload,
        )
        self.__dict__.update(settings)
        return self

    def _build_settings(
        self,
        *,
        runtime_config_file: str,
        runtime_raw: Dict[str, Any],
    ) -> Dict[str, Any]:
        paths = _require_object(runtime_raw.get("paths"), "paths")
        data_root = _resolve_path(_require_string(paths, "data_root"), base_dir=self.PROJECT_ROOT)
        html_folder = _resolve_path(_require_string(paths, "html_folder"), base_dir=data_root)
        auto_html_folder = _resolve_path(_require_string(paths, "auto_html_folder"), base_dir=data_root)
        log_dir = _resolve_path(_require_string(paths, "log_dir"), base_dir=data_root)
        output_excel_dir = _resolve_path(_require_string(paths, "output_excel_dir"), base_dir=data_root)
        excel_schema_file = _resolve_path(
            _require_string(paths, "excel_schema_file"),
            base_dir=self.PROJECT_ROOT,
        )
        regression_root = _resolve_path(_require_string(paths, "regression_root"), base_dir=data_root)
        regression_workdir_root = _resolve_path(
            _require_string(paths, "regression_workdir_root"),
            base_dir=data_root,
        )
        compare_report_dir = _resolve_path(
            _require_string(paths, "compare_report_dir"),
            base_dir=data_root,
        )
        parser_cache_db = _resolve_path(
            _require_string(paths, "parser_cache_db"),
            base_dir=data_root,
        )
        download_chunk_state_dir = _resolve_path(
            _require_string(paths, "download_chunk_state_dir"),
            base_dir=data_root,
        )

        if _paths_equal(html_folder, auto_html_folder):
            raise ValueError(
                "paths.html_folder and paths.auto_html_folder must be different directories "
                f"(got {html_folder})"
            )

        output_file_names = _require_string_mapping(
            runtime_raw.get("output_file_names"),
            name="output_file_names",
        )
        deal_file_names = _require_string_mapping(
            runtime_raw.get("deal_file_names"),
            name="deal_file_names",
        )
        _require_keys(
            output_file_names,
            name="output_file_names",
            required_keys=("equity_transfer", "pre_disclosure", "physical_asset", "capital_increase"),
        )
        _require_keys(
            deal_file_names,
            name="deal_file_names",
            required_keys=("equity_transfer", "physical_asset", "capital_increase"),
        )

        output_files = {
            "equity_transfer": os.path.join(output_excel_dir, output_file_names["equity_transfer"]),
            "pre_disclosure": os.path.join(output_excel_dir, output_file_names["pre_disclosure"]),
            "physical_asset": os.path.join(output_excel_dir, output_file_names["physical_asset"]),
            "capital_increase": os.path.join(output_excel_dir, output_file_names["capital_increase"]),
        }
        deal_files = {
            "equity_transfer": os.path.join(output_excel_dir, deal_file_names["equity_transfer"]),
            "physical_asset": os.path.join(output_excel_dir, deal_file_names["physical_asset"]),
            "capital_increase": os.path.join(output_excel_dir, deal_file_names["capital_increase"]),
        }

        logging_payload = _require_object(runtime_raw.get("logging"), "logging")
        log_level = _require_string(logging_payload, "level").upper()
        log_to_file = _require_bool(logging_payload, "to_file")

        supported_exchanges = _require_string_list(
            runtime_raw.get("supported_exchanges"),
            name="supported_exchanges",
        )
        project_code_prefixes = _require_string_mapping(
            runtime_raw.get("project_code_prefixes"),
            name="project_code_prefixes",
        )
        exchange_names = _require_string_mapping(
            runtime_raw.get("exchange_names"),
            name="exchange_names",
        )

        parser_defaults_raw = _require_object(runtime_raw.get("parser_defaults"), "parser_defaults")
        parser_defaults = {
            "limit": _require_optional_int(parser_defaults_raw, "limit"),
            "batch_flush_interval": _require_int(
                parser_defaults_raw,
                "batch_flush_interval",
                min_value=0,
            ),
            "compat_profile": _require_string(parser_defaults_raw, "compat_profile"),
            "progress_interval": _require_int(
                parser_defaults_raw,
                "progress_interval",
                min_value=0,
            ),
            "compare_fields": _require_string_list(
                parser_defaults_raw.get("compare_fields"),
                name="parser_defaults.compare_fields",
            ),
            "parse_cache_enabled": _require_bool(parser_defaults_raw, "parse_cache_enabled"),
        }
        if parser_defaults["compat_profile"] not in {"full", "ppe_ready"}:
            raise ValueError("parser_defaults.compat_profile must be one of: full, ppe_ready")

        downloader_defaults_raw = _require_object(
            runtime_raw.get("downloader_defaults"),
            "downloader_defaults",
        )
        downloader_defaults = {
            "exchange": _require_string(downloader_defaults_raw, "exchange"),
            "project_type": _require_string(downloader_defaults_raw, "project_type"),
            "concurrency": _require_int(downloader_defaults_raw, "concurrency", min_value=1),
            "split_candidates": _require_int(downloader_defaults_raw, "split_candidates", min_value=1),
            "split_min_days": _require_int(downloader_defaults_raw, "split_min_days", min_value=1),
            "split_max_depth": _require_int(downloader_defaults_raw, "split_max_depth", min_value=1),
            "split_mode": _require_string(downloader_defaults_raw, "split_mode"),
            "resume": _require_bool(downloader_defaults_raw, "resume"),
            "save_json": _require_bool(downloader_defaults_raw, "save_json"),
            "auto_split": _require_bool(downloader_defaults_raw, "auto_split"),
            "sse_ssl_verify": _parse_bool(
                downloader_defaults_raw.get("sse_ssl_verify", True),
                key="sse_ssl_verify",
            ),
            "sse_ssl_fallback_insecure": _parse_bool(
                downloader_defaults_raw.get("sse_ssl_fallback_insecure", True),
                key="sse_ssl_fallback_insecure",
            ),
            "sse_ca_bundle": _require_optional_string(downloader_defaults_raw, "sse_ca_bundle"),
        }
        if downloader_defaults["split_mode"] not in {"fast", "steady"}:
            raise ValueError("downloader_defaults.split_mode must be one of: fast, steady")
        if downloader_defaults["exchange"] not in {"cbex", "sse", "tpre", "cquae", "all"}:
            raise ValueError(
                "downloader_defaults.exchange must be one of: cbex, sse, tpre, cquae, all"
            )
        if downloader_defaults["project_type"] not in {
            "physical_asset",
            "equity_transfer",
            "capital_increase",
            "pre_disclosure",
            "all",
        }:
            raise ValueError(
                "downloader_defaults.project_type must be one of: "
                "physical_asset, equity_transfer, capital_increase, pre_disclosure, all"
            )

        downloader_page_size_raw = _require_object(
            runtime_raw.get("downloader_task_page_size"),
            "downloader_task_page_size",
        )
        downloader_task_ids = (
            "sse:physical_asset",
            "cbex:physical_asset",
            "sse:equity_transfer",
            "sse:capital_increase",
            "sse:pre_disclosure",
            "cbex:equity_transfer",
            "cbex:capital_increase",
            "cbex:pre_disclosure",
            "tpre:physical_asset",
            "tpre:equity_transfer",
            "tpre:capital_increase",
            "tpre:pre_disclosure",
            "cquae:physical_asset",
            "cquae:equity_transfer",
            "cquae:capital_increase",
            "cquae:pre_disclosure",
        )
        _require_keys(
            downloader_page_size_raw,
            name="downloader_task_page_size",
            required_keys=downloader_task_ids,
        )
        downloader_task_page_size: Dict[str, int] = {}
        for task_id in downloader_task_ids:
            downloader_task_page_size[task_id] = _require_int(
                downloader_page_size_raw,
                task_id,
                min_value=1,
            )

        regression_defaults_raw = _require_object(
            runtime_raw.get("regression_defaults"),
            "regression_defaults",
        )
        regression_defaults = {
            "print_top": _require_int(regression_defaults_raw, "print_top", min_value=0),
            "ignored_fields": _require_string_list(
                regression_defaults_raw.get("ignored_fields"),
                name="regression_defaults.ignored_fields",
            ),
            "show_parser_tail": _require_bool(regression_defaults_raw, "show_parser_tail"),
        }

        return {
            "PROJECT_ROOT": self.PROJECT_ROOT,
            "RUNTIME_CONFIG_FILE": runtime_config_file,
            "_RUNTIME_RAW": dict(runtime_raw),
            "DATA_ROOT": data_root,
            "HTML_FOLDER": html_folder,
            "AUTO_HTML_FOLDER": auto_html_folder,
            "LOG_DIR": log_dir,
            "OUTPUT_EXCEL_DIR": output_excel_dir,
            "EXCEL_SCHEMA_FILE": excel_schema_file,
            "REGRESSION_ROOT": regression_root,
            "REGRESSION_RAW_PAGES_ROOT": os.path.join(regression_root, "RawPages"),
            "REGRESSION_WORKDIR_ROOT": regression_workdir_root,
            "COMPARE_REPORT_DIR": compare_report_dir,
            "PARSER_CACHE_DB": parser_cache_db,
            "DOWNLOAD_CHUNK_STATE_DIR": download_chunk_state_dir,
            "HTML_FOLDER_NAME": os.path.basename(html_folder),
            "AUTO_HTML_FOLDER_NAME": os.path.basename(auto_html_folder),
            "OUTPUT_FILE_NAMES": output_file_names,
            "DEAL_FILE_NAMES": deal_file_names,
            "OUTPUT_FILES": output_files,
            "DEAL_FILES": deal_files,
            "LOG_LEVEL": log_level,
            "LOG_TO_FILE": log_to_file,
            "SUPPORTED_EXCHANGES": supported_exchanges,
            "PROJECT_CODE_PREFIXES": project_code_prefixes,
            "EXCHANGE_NAMES": exchange_names,
            "PARSER_DEFAULTS": parser_defaults,
            "DOWNLOADER_DEFAULTS": downloader_defaults,
            "DOWNLOADER_TASK_PAGE_SIZE": downloader_task_page_size,
            "REGRESSION_DEFAULTS": regression_defaults,
        }

    def get_data_root(self) -> str:
        return self.DATA_ROOT

    def is_path_within_project_root(self, path_value: str) -> bool:
        target = os.path.abspath(str(path_value or ""))
        try:
            return os.path.commonpath([self.PROJECT_ROOT, target]) == self.PROJECT_ROOT
        except ValueError:
            return False

    def is_path_within_data_root(self, path_value: str) -> bool:
        target = os.path.abspath(str(path_value or ""))
        try:
            return os.path.commonpath([self.DATA_ROOT, target]) == self.DATA_ROOT
        except ValueError:
            return False

    def get_output_file(self, file_type: str, status: str = "\u6302\u724c") -> str:
        if status == "\u6210\u4ea4":
            return self.DEAL_FILES.get(file_type, f"\u6210\u4ea4_{file_type}.xlsx")
        return self.OUTPUT_FILES.get(file_type, f"\u6302\u724c_{file_type}.xlsx")

    def get_exchange_name(self, exchange_code: str) -> str:
        return self.EXCHANGE_NAMES.get(exchange_code, exchange_code)

    def get_project_type(self, project_code: str) -> str:
        for prefix, ptype in self.PROJECT_CODE_PREFIXES.items():
            if project_code.startswith(prefix):
                return ptype
        return "\u672a\u77e5\u7c7b\u578b"


def load_config(
    *,
    project_root: str | None = None,
    runtime_config_file: str | None = None,
) -> Config:
    return Config.from_runtime(
        project_root=project_root,
        runtime_config_file=runtime_config_file,
    )


config = load_config()


def reload_config(*, runtime_config_file: str | None = None) -> Config:
    return config.reload(runtime_config_file=runtime_config_file)
