"""Select output excel file from parsed project data."""

import os
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any, Dict, Optional

from .constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_LISTED,
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from .output_contract import PUBLIC_RESOURCE_OUTPUT_FILENAME
from .parsing import ParsedProject


@dataclass(frozen=True)
class OutputTargetSettings:
    output_excel_dir: str = ""
    output_files: Dict[str, str] = field(default_factory=dict)
    deal_files: Dict[str, str] = field(default_factory=dict)
    public_resource_output_filename: str = PUBLIC_RESOURCE_OUTPUT_FILENAME


def build_output_target_settings(config_obj: object) -> OutputTargetSettings:
    return OutputTargetSettings(
        output_excel_dir=str(getattr(config_obj, "OUTPUT_EXCEL_DIR", "") or ""),
        output_files=dict(getattr(config_obj, "OUTPUT_FILES", {}) or {}),
        deal_files=dict(getattr(config_obj, "DEAL_FILES", {}) or {}),
    )


PROJECT_TYPE_TO_OUTPUT = {
    TYPE_EQUITY_TRANSFER: "equity_transfer",
    TYPE_PHYSICAL_ASSET: "physical_asset",
    TYPE_CAPITAL_INCREASE: "capital_increase",
}


def _normalize_status(value: Any) -> str:
    if not value:
        return STATUS_LISTED
    return str(value)


def _infer_output_from_code(project_code: str) -> Optional[str]:
    if project_code.startswith(("G3", "Q3", "T3", "YQCQ", "SDCQ")):
        return "equity_transfer"
    if project_code.startswith(("GR", "QR", "PR", "TR", "TA")):
        return "physical_asset"
    if project_code.startswith(("G6", "Q6", "T6")):
        return "capital_increase"
    return None


def _resolve_output_file(settings: OutputTargetSettings, file_type: str, status: str = STATUS_LISTED) -> str:
    normalized_status = _normalize_status(status)
    if normalized_status == "成交":
        file_path = str(settings.deal_files.get(file_type) or "").strip()
        fallback_name = f"成交_{file_type}.xlsx"
    else:
        file_path = str(settings.output_files.get(file_type) or "").strip()
        fallback_name = f"{STATUS_LISTED}_{file_type}.xlsx"

    if file_path:
        return file_path
    if settings.output_excel_dir:
        return _join_output_path(settings.output_excel_dir, fallback_name)
    return fallback_name


def _join_output_path(base_dir: str, file_name: str) -> str:
    base = str(base_dir or "").strip()
    if not base:
        return str(file_name)
    # Preserve Windows-style joins even when tests run on POSIX hosts.
    if "\\" in base or (len(base) >= 2 and base[1] == ":"):
        return str(PureWindowsPath(base) / str(file_name))
    return os.path.join(base, str(file_name))


def _resolve_source_exchange(data: Dict[str, Any] | ParsedProject) -> str:
    if isinstance(data, ParsedProject):
        return data.exchange.strip().lower()
    return str(
        data.get("__source_exchange")
        or data.get("_source_exchange")
        or data.get("exchange_type")
        or ""
    ).strip().lower()


def _resolve_status(data: Dict[str, Any] | ParsedProject) -> str:
    if isinstance(data, ParsedProject):
        return _normalize_status(data.status)
    return _normalize_status(data.get(KEY_STATUS))


def _resolve_project_type(data: Dict[str, Any] | ParsedProject) -> str:
    if isinstance(data, ParsedProject):
        return data.project_type
    return str(data.get(KEY_PROJECT_TYPE) or "")


def _resolve_is_pre_disclosure(data: Dict[str, Any] | ParsedProject) -> bool:
    if isinstance(data, ParsedProject):
        return data.is_pre_disclosure
    return bool(data.get(KEY_IS_PRE_DISCLOSURE))


def _resolve_project_code(data: Dict[str, Any] | ParsedProject) -> str:
    if isinstance(data, ParsedProject):
        return data.project_code
    return str(data.get(KEY_PROJECT_CODE) or "")


def decide_output_file(
    data: Dict[str, Any] | ParsedProject,
    *,
    settings: Optional[OutputTargetSettings] = None,
) -> Optional[str]:
    resolved_settings = settings or OutputTargetSettings()
    source_exchange = _resolve_source_exchange(data)
    if source_exchange == "public_resource":
        if resolved_settings.output_excel_dir:
            return _join_output_path(
                resolved_settings.output_excel_dir,
                resolved_settings.public_resource_output_filename,
            )
        return resolved_settings.public_resource_output_filename

    status = _resolve_status(data)
    project_type = _resolve_project_type(data)
    is_pre = _resolve_is_pre_disclosure(data)

    if is_pre or project_type == TYPE_PRE_DISCLOSURE:
        return _resolve_output_file(resolved_settings, "pre_disclosure")

    output_key = PROJECT_TYPE_TO_OUTPUT.get(project_type)
    if output_key:
        return _resolve_output_file(resolved_settings, output_key, status)

    project_code = _resolve_project_code(data)
    inferred_key = _infer_output_from_code(project_code)
    if inferred_key:
        return _resolve_output_file(resolved_settings, inferred_key, status)

    return None
