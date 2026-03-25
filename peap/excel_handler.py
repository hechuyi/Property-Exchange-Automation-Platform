#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel writer with mapping-driven output rules."""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from .output_contract import (
    BASE_FIELD_CANDIDATES,
    BASE_OUTPUT_COLUMNS,
    detect_output_kind,
)
from .output_contract import (
    DEFAULT_INTERNAL_KEYS as BASE_INTERNAL_KEYS,
)
from .output_contract import (
    clone_field_candidates as contract_clone_field_candidates,
)
from .output_contract import (
    clone_output_columns as contract_clone_output_columns,
)


def _detect_kind(target_file: str) -> str:
    return detect_output_kind(target_file)


DEFAULT_INTERNAL_KEYS = set(BASE_INTERNAL_KEYS)
DEFAULT_OUTPUT_COLUMNS = contract_clone_output_columns(BASE_OUTPUT_COLUMNS)
DEFAULT_FIELD_CANDIDATES = contract_clone_field_candidates(BASE_FIELD_CANDIDATES)
DEFAULT_EMPTY_PLACEHOLDERS = {
    "-",
    "--",
    "\u2014",
    "/",
    "\uff0f",
    "\u6682\u65e0",
    "N/A",
    "NA",
    "null",
    "None",
    "\u65e0",
}
DEFAULT_SAVE_RETRY_DELAYS = (0.2, 0.5, 1.0)


@dataclass(frozen=True)
class ExcelSchemaSettings:
    schema_path: str = ""


@dataclass(frozen=True)
class ExcelOutputRuntime:
    internal_keys: Set[str]
    output_columns: Dict[str, List[str]]
    field_candidates: Dict[str, Dict[str, List[str]]]
    empty_placeholders: Set[str]
    save_retry_delays: Tuple[float, ...]
    schema_status: Dict[str, Any]


def build_excel_schema_settings(config_obj: object) -> ExcelSchemaSettings:
    return ExcelSchemaSettings(
        schema_path=str(getattr(config_obj, "EXCEL_SCHEMA_FILE", "") or ""),
    )


def _load_default_excel_schema_settings() -> ExcelSchemaSettings:
    try:
        from config import config as default_config
    except Exception:
        return ExcelSchemaSettings()
    return build_excel_schema_settings(default_config)


_DEFAULT_EXCEL_SCHEMA_SETTINGS: ExcelSchemaSettings = _load_default_excel_schema_settings()


def get_default_excel_schema_settings() -> ExcelSchemaSettings:
    return _DEFAULT_EXCEL_SCHEMA_SETTINGS


def set_default_excel_schema_settings(settings: Optional[ExcelSchemaSettings]) -> ExcelSchemaSettings:
    global _DEFAULT_EXCEL_SCHEMA_SETTINGS
    _DEFAULT_EXCEL_SCHEMA_SETTINGS = settings or ExcelSchemaSettings()
    return _DEFAULT_EXCEL_SCHEMA_SETTINGS


def _resolve_excel_schema_path(settings: Optional[ExcelSchemaSettings] = None) -> str:
    resolved_settings = settings or get_default_excel_schema_settings()
    resolved_path = str(resolved_settings.schema_path or "").strip()
    if resolved_path:
        return os.path.abspath(resolved_path)
    return ""


def _build_schema_status(
    *,
    path: str,
    source: str = "default",
    loaded: bool = False,
    used_defaults: bool = True,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "path": path,
        "source": source,
        "loaded": loaded,
        "used_defaults": used_defaults,
        "error": error,
    }


def _resolve_logger(logger_obj: Optional[logging.Logger] = None) -> logging.Logger:
    return logger_obj if logger_obj is not None else logging.getLogger(__name__)


def _build_excel_output_runtime(
    settings: Optional[ExcelSchemaSettings] = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> ExcelOutputRuntime:
    resolved_logger = _resolve_logger(logger)
    schema_path = _resolve_excel_schema_path(settings)
    internal_keys = set(DEFAULT_INTERNAL_KEYS)
    output_columns = _clone_output_columns(DEFAULT_OUTPUT_COLUMNS)
    field_candidates = _clone_field_candidates(DEFAULT_FIELD_CANDIDATES)
    empty_placeholders = set(DEFAULT_EMPTY_PLACEHOLDERS)
    save_retry_delays = tuple(DEFAULT_SAVE_RETRY_DELAYS)
    schema_status = _build_schema_status(path=schema_path)

    if not os.path.isfile(schema_path):
        schema_status = _build_schema_status(
            path=schema_path,
            error=f"schema file not found: {schema_path}",
        )
        return ExcelOutputRuntime(
            internal_keys=internal_keys,
            output_columns=output_columns,
            field_candidates=field_candidates,
            empty_placeholders=empty_placeholders,
            save_retry_delays=save_retry_delays,
            schema_status=schema_status,
        )

    try:
        payload = _load_schema_payload(schema_path)
    except Exception as exc:
        schema_status = _build_schema_status(
            path=schema_path,
            error=f"failed to parse schema file: {exc}",
        )
        resolved_logger.warning("Failed to load excel output schema: %s (%s)", schema_path, exc)
        return ExcelOutputRuntime(
            internal_keys=internal_keys,
            output_columns=output_columns,
            field_candidates=field_candidates,
            empty_placeholders=empty_placeholders,
            save_retry_delays=save_retry_delays,
            schema_status=schema_status,
        )

    type_errors = _validate_schema_payload_types(payload, schema_path)
    if type_errors:
        schema_status = _build_schema_status(
            path=schema_path,
            error="; ".join(type_errors),
        )
        resolved_logger.warning("Skip excel output schema: %s", schema_status["error"])
        return ExcelOutputRuntime(
            internal_keys=internal_keys,
            output_columns=output_columns,
            field_candidates=field_candidates,
            empty_placeholders=empty_placeholders,
            save_retry_delays=save_retry_delays,
            schema_status=schema_status,
        )

    internal_keys_raw = _normalize_string_list(payload.get("internal_keys"))
    output_columns_raw = _normalize_output_columns(payload.get("output_columns"))
    field_candidates_raw = _normalize_field_candidates(payload.get("field_candidates"))
    empty_placeholders_raw = _normalize_string_list(payload.get("empty_placeholders"))
    retry_delays_raw = _normalize_retry_delays(payload.get("save_retry_delays"))

    internal_keys.update(internal_keys_raw)
    output_columns = _merge_output_columns(DEFAULT_OUTPUT_COLUMNS, output_columns_raw)
    field_candidates = _merge_field_candidates(DEFAULT_FIELD_CANDIDATES, field_candidates_raw)
    empty_placeholders.update(empty_placeholders_raw)
    save_retry_delays = retry_delays_raw or tuple(DEFAULT_SAVE_RETRY_DELAYS)

    contract_errors = _validate_output_contract(output_columns, field_candidates)
    if contract_errors:
        schema_status = _build_schema_status(
            path=schema_path,
            error="; ".join(contract_errors),
        )
        resolved_logger.warning("Skip excel output schema overrides: %s", schema_status["error"])
        return ExcelOutputRuntime(
            internal_keys=set(DEFAULT_INTERNAL_KEYS),
            output_columns=_clone_output_columns(DEFAULT_OUTPUT_COLUMNS),
            field_candidates=_clone_field_candidates(DEFAULT_FIELD_CANDIDATES),
            empty_placeholders=set(DEFAULT_EMPTY_PLACEHOLDERS),
            save_retry_delays=tuple(DEFAULT_SAVE_RETRY_DELAYS),
            schema_status=schema_status,
        )

    schema_status = _build_schema_status(
        path=schema_path,
        source="external",
        loaded=True,
        used_defaults=False,
    )
    return ExcelOutputRuntime(
        internal_keys=internal_keys,
        output_columns=output_columns,
        field_candidates=field_candidates,
        empty_placeholders=empty_placeholders,
        save_retry_delays=save_retry_delays,
        schema_status=schema_status,
    )


def load_excel_output_runtime(
    settings: Optional[ExcelSchemaSettings] = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> ExcelOutputRuntime:
    return _build_excel_output_runtime(settings, logger=logger)


def _resolve_runtime(runtime: Optional[ExcelOutputRuntime] = None) -> ExcelOutputRuntime:
    if runtime is not None:
        return runtime
    return load_excel_output_runtime()


def _runtime_internal_keys(runtime: Optional[ExcelOutputRuntime] = None) -> Set[str]:
    return _resolve_runtime(runtime).internal_keys


def _runtime_output_columns(runtime: Optional[ExcelOutputRuntime] = None) -> Dict[str, List[str]]:
    return _resolve_runtime(runtime).output_columns


def _runtime_field_candidates(
    runtime: Optional[ExcelOutputRuntime] = None,
) -> Dict[str, Dict[str, List[str]]]:
    return _resolve_runtime(runtime).field_candidates


def _runtime_empty_placeholders(runtime: Optional[ExcelOutputRuntime] = None) -> Set[str]:
    return _resolve_runtime(runtime).empty_placeholders


def _runtime_save_retry_delays(
    runtime: Optional[ExcelOutputRuntime] = None,
) -> Tuple[float, ...]:
    return _resolve_runtime(runtime).save_retry_delays


def _runtime_schema_status(runtime: Optional[ExcelOutputRuntime] = None) -> Dict[str, Any]:
    return dict(_resolve_runtime(runtime).schema_status)


def _normalize_string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    normalized: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_output_columns(payload: Any) -> Dict[str, List[str]]:
    if not isinstance(payload, dict):
        return {}
    normalized: Dict[str, List[str]] = {}
    for kind, columns in payload.items():
        key = str(kind or "").strip()
        if not key:
            continue
        normalized[key] = _normalize_string_list(columns)
    return normalized


def _normalize_field_candidates(payload: Any) -> Dict[str, Dict[str, List[str]]]:
    if not isinstance(payload, dict):
        return {}
    normalized: Dict[str, Dict[str, List[str]]] = {}
    for kind, mapping in payload.items():
        kind_key = str(kind or "").strip()
        if not kind_key or not isinstance(mapping, dict):
            continue
        field_map: Dict[str, List[str]] = {}
        for column_name, candidates in mapping.items():
            col_key = str(column_name or "").strip()
            if not col_key:
                continue
            candidate_list = _normalize_string_list(candidates)
            if candidate_list:
                field_map[col_key] = candidate_list
        normalized[kind_key] = field_map
    return normalized


def _clone_output_columns(payload: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {kind: list(columns) for kind, columns in payload.items()}


def _clone_field_candidates(payload: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    return {
        kind: {column_name: list(candidates) for column_name, candidates in mapping.items()}
        for kind, mapping in payload.items()
    }


def _merge_output_columns(
    base: Dict[str, List[str]],
    overrides: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    merged = _clone_output_columns(base)
    for kind, columns in overrides.items():
        merged[kind] = list(columns)
    return merged


def _merge_field_candidates(
    base: Dict[str, Dict[str, List[str]]],
    overrides: Dict[str, Dict[str, List[str]]],
) -> Dict[str, Dict[str, List[str]]]:
    merged = _clone_field_candidates(base)
    for kind, mapping in overrides.items():
        current = merged.setdefault(kind, {})
        for column_name, candidates in mapping.items():
            current[column_name] = list(candidates)
    return merged


def _normalize_retry_delays(payload: Any) -> Optional[Tuple[float, ...]]:
    if not isinstance(payload, list):
        return None
    delays: List[float] = []
    for value in payload:
        try:
            delay = float(value)
        except Exception:
            continue
        if delay >= 0:
            delays.append(delay)
    if not delays:
        return None
    return tuple(delays)


def _validate_output_contract(
    output_columns: Dict[str, List[str]],
    field_candidates: Dict[str, Dict[str, List[str]]],
) -> List[str]:
    errors: List[str] = []
    for kind, columns in output_columns.items():
        if not columns:
            errors.append(f"output columns are empty for kind={kind}")
            continue
        candidates_map = field_candidates.get(kind)
        if candidates_map is None:
            errors.append(f"missing field_candidates for kind={kind}")
            continue
        for column_name in columns:
            if column_name == "ID":
                continue
            if not candidates_map.get(column_name):
                errors.append(f"missing field candidates for kind={kind}, column={column_name}")

    for kind, mapping in field_candidates.items():
        if kind not in output_columns:
            errors.append(f"field_candidates defined for unknown kind={kind}")
            continue
        known_columns = set(output_columns[kind])
        for column_name in mapping:
            if column_name == "ID":
                continue
            if column_name not in known_columns:
                errors.append(f"field_candidates references unknown column: kind={kind}, column={column_name}")
    return errors


def _load_schema_payload(schema_path: str) -> Any:
    with open(schema_path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _build_schema_error_prefix(schema_path: str) -> str:
    return f"excel output schema ({schema_path})"


def _validate_schema_payload_types(payload: Any, schema_path: str) -> List[str]:
    prefix = _build_schema_error_prefix(schema_path)
    if not isinstance(payload, dict):
        return [f"{prefix}: root must be an object"]

    errors: List[str] = []
    if "internal_keys" in payload and not isinstance(payload.get("internal_keys"), list):
        errors.append(f"{prefix}: internal_keys must be a list")
    if "output_columns" in payload and not isinstance(payload.get("output_columns"), dict):
        errors.append(f"{prefix}: output_columns must be an object")
    if "field_candidates" in payload and not isinstance(payload.get("field_candidates"), dict):
        errors.append(f"{prefix}: field_candidates must be an object")
    if "empty_placeholders" in payload and not isinstance(payload.get("empty_placeholders"), list):
        errors.append(f"{prefix}: empty_placeholders must be a list")
    if "save_retry_delays" in payload and not isinstance(payload.get("save_retry_delays"), list):
        errors.append(f"{prefix}: save_retry_delays must be a list")
    return errors


def reload_excel_output_schema(
    settings: Optional[ExcelSchemaSettings] = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    if settings is not None:
        set_default_excel_schema_settings(settings)
    runtime = load_excel_output_runtime(logger=logger)
    return get_excel_schema_status(runtime=runtime)


def get_excel_schema_status(
    runtime: Optional[ExcelOutputRuntime] = None,
) -> Dict[str, Any]:
    resolved_runtime = _resolve_runtime(runtime)
    return _runtime_schema_status(resolved_runtime)


def get_output_schema_snapshot(
    runtime: Optional[ExcelOutputRuntime] = None,
) -> Dict[str, Any]:
    resolved_runtime = _resolve_runtime(runtime)
    return {
        "internal_keys": sorted(_runtime_internal_keys(resolved_runtime)),
        "output_columns": _clone_output_columns(_runtime_output_columns(resolved_runtime)),
        "field_candidates": _clone_field_candidates(_runtime_field_candidates(resolved_runtime)),
    }


def validate_excel_output_schema(
    runtime: Optional[ExcelOutputRuntime] = None,
) -> List[str]:
    resolved_runtime = _resolve_runtime(runtime)
    errors = _validate_output_contract(
        _runtime_output_columns(resolved_runtime),
        _runtime_field_candidates(resolved_runtime),
    )
    if not _runtime_internal_keys(resolved_runtime):
        errors.append("internal keys are empty")
    return errors


def validate_configured_excel_output_schema(schema_path: Optional[str] = None) -> List[str]:
    resolved_path = os.path.abspath(schema_path or _resolve_excel_schema_path())
    if not os.path.isfile(resolved_path):
        return [f"excel output schema file not found: {resolved_path}"]

    try:
        payload = _load_schema_payload(resolved_path)
    except Exception as exc:
        return [f"failed to parse excel output schema: {exc}"]

    errors = _validate_schema_payload_types(payload, resolved_path)
    if errors:
        return errors

    internal_keys = _normalize_string_list(payload.get("internal_keys"))
    output_columns = _normalize_output_columns(payload.get("output_columns"))
    field_candidates = _normalize_field_candidates(payload.get("field_candidates"))
    empty_placeholders = _normalize_string_list(payload.get("empty_placeholders"))
    retry_delays = _normalize_retry_delays(payload.get("save_retry_delays"))

    merged_internal_keys = set(DEFAULT_INTERNAL_KEYS)
    merged_internal_keys.update(internal_keys)
    merged_output_columns = _merge_output_columns(DEFAULT_OUTPUT_COLUMNS, output_columns)
    merged_field_candidates = _merge_field_candidates(DEFAULT_FIELD_CANDIDATES, field_candidates)
    contract_errors = _validate_output_contract(merged_output_columns, merged_field_candidates)
    if not merged_internal_keys:
        contract_errors.append("configured excel schema would leave internal keys empty")
    if empty_placeholders and not any(str(item).strip() for item in empty_placeholders):
        contract_errors.append("configured excel schema empty_placeholders contains no usable values")
    if payload.get("save_retry_delays") is not None and retry_delays is None:
        contract_errors.append("configured excel schema save_retry_delays contains no usable values")
    return contract_errors


def _is_empty_placeholder(
    value: Any,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return text in _runtime_empty_placeholders(runtime)


def _to_string(
    value: Any,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> str:
    if _is_empty_placeholder(value, runtime=runtime):
        return ""
    return str(value).strip()


def _normalize_cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def _normalize_target_file(target_file: str) -> str:
    return normalize_excel_target_path(target_file)


def normalize_excel_target_path(target_file: str) -> str:
    # Keep normalization conservative: normalize path syntax only.
    # Do not alter inner characters (e.g. '-' in project-like names).
    value = str(target_file or "").replace("\ufeff", "").strip()
    if not value:
        return ""
    return os.path.normpath(value)


def _normalize_date(
    value: Any,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> str:
    if _is_empty_placeholder(value, runtime=runtime):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    text = str(value).strip()
    if not text:
        return ""
    if " " in text:
        text = text.split(" ")[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}/{text[4:6]}/{text[6:]}"
    return text.replace("-", "/")


def _pick_first(data: Dict[str, Any], candidates: List[str]) -> Any:
    for key in candidates:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _build_row_data(
    data: Dict[str, Any],
    kind: str,
    *,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> Dict[str, str]:
    output_columns = _runtime_output_columns(runtime)
    field_candidates = _runtime_field_candidates(runtime)
    row_data: Dict[str, str] = {}
    if "ID" in output_columns.get(kind, []):
        row_data["ID"] = ""
    candidates_map = field_candidates[kind]

    for column_name in output_columns[kind]:
        if column_name == "ID":
            continue
        value = _pick_first(data, candidates_map.get(column_name, [column_name]))
        if "日期" in column_name:
            row_data[column_name] = _normalize_date(value, runtime=runtime)
        else:
            row_data[column_name] = _to_string(value, runtime=runtime)

    return row_data


def _pick_meta_value(data: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _collect_error_context(
    data: Dict[str, Any],
    target_file: str,
    source_file: str = "",
    exchange: str = "",
    runtime: Optional[ExcelOutputRuntime] = None,
) -> str:
    source_value = source_file or _pick_meta_value(
        data, ["_source_file", "__source_file", "source_file"]
    )
    exchange_value = exchange or _pick_meta_value(
        data, ["_source_exchange", "__source_exchange", "exchange_type", "交易所"]
    )

    key_snapshot = {}
    for key in ("项目编号", "项目名称", "国资监测编号", "标的名称"):
        value = data.get(key)
        if value not in (None, ""):
            key_snapshot[key] = _to_string(value, runtime=runtime)

    numbered_keys = [str(k) for k, v in data.items() if "编号" in str(k) and v not in (None, "")]

    context_parts = [f"target={target_file}"]
    if source_value:
        context_parts.append(f"source={source_value}")
    if exchange_value:
        context_parts.append(f"exchange={exchange_value}")
    if key_snapshot:
        context_parts.append(f"fields={key_snapshot}")
    if numbered_keys:
        context_parts.append(f"numbered_keys={numbered_keys}")
    return " | ".join(context_parts)


def _next_id(df: pd.DataFrame) -> str:
    if "ID" not in df.columns or len(df) == 0:
        return "1"
    id_values = pd.to_numeric(df["ID"], errors="coerce").dropna()
    max_id = int(id_values.max()) if len(id_values) > 0 else 0
    return str(max_id + 1)


def _ensure_columns(
    df: pd.DataFrame,
    kind: str,
    *,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> pd.DataFrame:
    output_columns = _runtime_output_columns(runtime)
    for column_name in output_columns[kind]:
        if column_name not in df.columns:
            df[column_name] = ""
    if "ID" not in output_columns.get(kind, []) and "ID" in df.columns:
        df = df.drop(columns=["ID"])
    return df


def _project_code_column(
    kind: str,
    *,
    runtime: Optional[ExcelOutputRuntime] = None,
) -> str:
    columns = _runtime_output_columns(runtime).get(kind, [])
    if "项目编号" in columns:
        return "项目编号"
    return columns[2] if len(columns) > 2 else ""


def _is_retryable_save_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        # Windows may throw Errno 22/13/32 for transient file lock or race-like conditions.
        return exc.errno in {13, 22, 32}
    return False


def _save_df_with_retry(
    df: pd.DataFrame,
    target_file: str,
    context: str,
    *,
    runtime: Optional[ExcelOutputRuntime] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    resolved_logger = _resolve_logger(logger)
    retry_delays = _runtime_save_retry_delays(runtime)
    attempts = len(retry_delays) + 1
    for i in range(attempts):
        try:
            df.to_excel(target_file, index=False)
            return
        except Exception as exc:
            can_retry = _is_retryable_save_error(exc) and i < attempts - 1
            if not can_retry:
                raise
            delay = retry_delays[i]
            resolved_logger.warning(
                "保存Excel重试(%s/%s): %s (repr=%r), %.1fs后重试 | %s",
                i + 1,
                attempts - 1,
                exc,
                target_file,
                delay,
                context,
            )
            time.sleep(delay)


class ExcelBatchWriter:
    """Batch writer that keeps each target workbook in memory and flushes once."""

    def __init__(
        self,
        runtime: Optional[ExcelOutputRuntime] = None,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._runtime = _resolve_runtime(runtime)
        self._logger = _resolve_logger(logger)
        self._frames: Dict[str, pd.DataFrame] = {}
        self._kinds: Dict[str, str] = {}
        self._contexts: Dict[str, str] = {}
        self._project_indexes: Dict[str, Dict[str, int]] = {}
        self._next_ids: Dict[str, int] = {}
        self._dirty_targets: Set[str] = set()

    def _load_target_df(self, target_file: str, kind: str, context: str) -> Optional[pd.DataFrame]:
        if os.path.exists(target_file):
            try:
                df = pd.read_excel(target_file, dtype=str)
            except PermissionError:
                self._logger.error("无法读取Excel文件（可能被占用）: %s | %s", target_file, context)
                return None
            except Exception as exc:
                self._logger.error("读取Excel文件失败 %s: %s | %s", target_file, exc, context)
                return None
            had_id_column = "ID" in df.columns
            normalized = _ensure_columns(df, kind, runtime=self._runtime)
            if had_id_column and "ID" not in normalized.columns:
                normalized.attrs["_structure_changed"] = True
            return normalized
        return pd.DataFrame(columns=_runtime_output_columns(self._runtime)[kind])

    def _build_project_index(self, target_file: str, project_code_col: str) -> None:
        df = self._frames[target_file]
        index_map: Dict[str, int] = {}
        if project_code_col and project_code_col in df.columns:
            for idx, value in df[project_code_col].items():
                key = str(value).strip()
                if not key or key.lower() == "nan":
                    continue
                if key not in index_map:
                    index_map[key] = int(idx)
        self._project_indexes[target_file] = index_map

    def _init_next_id(self, target_file: str) -> None:
        next_id = _next_id(self._frames[target_file])
        try:
            self._next_ids[target_file] = int(next_id)
        except Exception:
            self._next_ids[target_file] = 1

    def upsert(
        self,
        data: Dict[str, Any],
        target_file: str,
        *,
        source_file: str = "",
        exchange: str = "",
    ) -> bool:
        target_file = _normalize_target_file(target_file)
        output_columns = _runtime_output_columns(self._runtime)
        safe_data = data if isinstance(data, dict) else {}
        context = _collect_error_context(
            safe_data,
            target_file,
            source_file=source_file,
            exchange=exchange,
            runtime=self._runtime,
        )
        if not target_file or not data:
            self._logger.error("保存失败: 缺少目标文件或数据 | %s", context)
            return False

        kind = _detect_kind(target_file)
        filtered_data = {k: v for k, v in data.items() if k not in _runtime_internal_keys(self._runtime)}
        row_data = _build_row_data(filtered_data, kind, runtime=self._runtime)
        project_code_col = _project_code_column(kind, runtime=self._runtime)
        project_code = str(row_data.get(project_code_col, "")).strip()
        if not project_code:
            self._logger.error("保存失败: 缺少项目编号 | %s", context)
            return False

        target_dir = os.path.dirname(target_file)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        if target_file not in self._frames:
            df = self._load_target_df(target_file, kind, context)
            if df is None:
                return False
            self._frames[target_file] = df
            self._kinds[target_file] = kind
            self._contexts[target_file] = context
            self._build_project_index(target_file, project_code_col)
            if "ID" in output_columns.get(kind, []):
                self._init_next_id(target_file)
            if bool(df.attrs.get("_structure_changed")):
                self._dirty_targets.add(target_file)
                df.attrs["_structure_changed"] = False
        else:
            loaded_kind = self._kinds.get(target_file)
            if loaded_kind and loaded_kind != kind:
                self._logger.error(
                    "批量写入失败: 目标文件类型不一致 target=%s loaded=%s current=%s | %s",
                    target_file,
                    loaded_kind,
                    kind,
                    context,
                )
                return False

        df = self._frames[target_file]
        project_index = self._project_indexes[target_file]
        match_idx = project_index.get(project_code)
        if match_idx is not None and match_idx in df.index:
            changed = False
            for key, value in row_data.items():
                if key == "ID":
                    continue
                existing = _normalize_cell_text(df.at[match_idx, key] if key in df.columns else "")
                incoming = _normalize_cell_text(value)
                if existing != incoming:
                    changed = True
                    break
            if not changed:
                self._logger.debug("跳过未变化项目: %s", project_code)
                return True
            for key, value in row_data.items():
                if key != "ID":
                    df.at[match_idx, key] = value
            self._logger.info("更新已有项目: %s", project_code)
        else:
            has_id_column = "ID" in output_columns.get(kind, [])
            if has_id_column:
                row_data["ID"] = str(self._next_ids[target_file])
                self._next_ids[target_file] += 1
            df.loc[len(df)] = {col: row_data.get(col, "") for col in output_columns[kind]}
            project_index[project_code] = int(df.index[-1])
            if has_id_column:
                self._logger.info("新增项目: %s, ID: %s", project_code, row_data["ID"])
            else:
                self._logger.info("新增项目: %s", project_code)

        self._dirty_targets.add(target_file)
        return True

    def flush(self) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        for target_file in sorted(self._dirty_targets):
            df = self._frames[target_file]
            context = self._contexts.get(target_file, f"target={target_file}")
            try:
                _save_df_with_retry(
                    df,
                    target_file,
                    context,
                    runtime=self._runtime,
                    logger=self._logger,
                )
                self._logger.info("批量保存完成: %s", target_file)
            except PermissionError as exc:
                self._logger.error("无法保存Excel文件（可能被占用）: %s | %s", target_file, context)
                errors[target_file] = str(exc)
            except Exception as exc:
                self._logger.error("保存Excel文件失败 %s: %s | %s", target_file, exc, context)
                errors[target_file] = str(exc)

        self._dirty_targets = {target for target in self._dirty_targets if target in errors}
        return errors


def save_to_excel(
    data: Dict[str, Any],
    target_file: str,
    *,
    source_file: str = "",
    exchange: str = "",
    runtime: Optional[ExcelOutputRuntime] = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Save one project record into target Excel (update by 项目编号, else append)."""
    resolved_runtime = _resolve_runtime(runtime)
    resolved_logger = _resolve_logger(logger)
    target_file = _normalize_target_file(target_file)
    output_columns = _runtime_output_columns(resolved_runtime)
    safe_data = data if isinstance(data, dict) else {}
    context = _collect_error_context(
        safe_data,
        target_file,
        source_file=source_file,
        exchange=exchange,
        runtime=resolved_runtime,
    )

    if not target_file or not data:
        resolved_logger.error("保存失败: 缺少目标文件或数据 | %s", context)
        return False

    kind = _detect_kind(target_file)
    filtered_data = {k: v for k, v in data.items() if k not in _runtime_internal_keys(resolved_runtime)}
    row_data = _build_row_data(filtered_data, kind, runtime=resolved_runtime)
    project_code_col = _project_code_column(kind, runtime=resolved_runtime)
    project_code = row_data.get(project_code_col, "")
    if not project_code:
        resolved_logger.error("保存失败: 缺少项目编号 | %s", context)
        return False

    target_dir = os.path.dirname(target_file)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    if os.path.exists(target_file):
        try:
            df = pd.read_excel(target_file, dtype=str)
        except PermissionError:
            resolved_logger.error("无法读取Excel文件（可能被占用）: %s | %s", target_file, context)
            return False
        except Exception as exc:
            resolved_logger.error("读取Excel文件失败 %s: %s | %s", target_file, exc, context)
            return False

        df = _ensure_columns(df, kind, runtime=resolved_runtime)
        match_idx = df[df[project_code_col].astype(str) == str(project_code)].index
        if len(match_idx) > 0:
            idx = match_idx[0]
            for key, value in row_data.items():
                if key != "ID":
                    df.at[idx, key] = value
            resolved_logger.info("更新已有项目: %s", project_code)
        else:
            has_id_column = "ID" in output_columns.get(kind, [])
            if has_id_column:
                row_data["ID"] = _next_id(df)
            df = pd.concat([df, pd.DataFrame([row_data])], ignore_index=True)
            if has_id_column:
                resolved_logger.info("新增项目: %s, ID: %s", project_code, row_data["ID"])
            else:
                resolved_logger.info("新增项目: %s", project_code)
    else:
        if "ID" in output_columns.get(kind, []):
            row_data["ID"] = "1"
        df = pd.DataFrame([row_data], columns=output_columns[kind])
        resolved_logger.info("创建新文件: %s", target_file)

    try:
        _save_df_with_retry(
            df,
            target_file,
            context,
            runtime=resolved_runtime,
            logger=resolved_logger,
        )
        resolved_logger.info("数据已保存到: %s", target_file)
        return True
    except PermissionError:
        resolved_logger.error(
            "无法保存Excel文件（可能被占用）: %s (repr=%r) | %s",
            target_file,
            target_file,
            context,
        )
        return False
    except Exception as exc:
        resolved_logger.error(
            "保存Excel文件失败 %s (repr=%r): %s | %s",
            target_file,
            target_file,
            exc,
            context,
        )
        return False
