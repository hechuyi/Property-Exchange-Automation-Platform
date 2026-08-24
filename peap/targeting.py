"""Select output excel file from parsed project data."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any, Dict, Optional

from .constants import (
    KEY_STATUS,
    STATUS_LISTED,
)
from .parsing import ParsedProject
from .projection_registry import resolve_projection_profile

_DEAL_OUTPUT_FILE_FALLBACK_KEYS = {
    "deal_physical_asset": "physical_asset",
    "deal_equity_transfer": "equity_transfer",
    "deal_capital_increase": "capital_increase",
}


@dataclass(frozen=True)
class OutputTargetSettings:
    output_excel_dir: str = ""
    output_files: Dict[str, str] = field(default_factory=dict)
    deal_files: Dict[str, str] = field(default_factory=dict)


def build_output_target_settings(config_obj: object) -> OutputTargetSettings:
    return OutputTargetSettings(
        output_excel_dir=str(getattr(config_obj, "OUTPUT_EXCEL_DIR", "") or ""),
        output_files=_copy_config_mapping(config_obj, "OUTPUT_FILES"),
        deal_files=_copy_config_mapping(config_obj, "DEAL_FILES"),
    )


def _copy_config_mapping(config_obj: object, attr_name: str) -> Dict[str, str]:
    if not hasattr(config_obj, attr_name):
        return {}
    value = getattr(config_obj, attr_name)
    if not isinstance(value, Mapping):
        raise TypeError(f"{attr_name} must be a mapping")
    return dict(value)


def _normalize_status(value: Any) -> str:
    if not value:
        return STATUS_LISTED
    return str(value)


def _resolve_output_file(settings: OutputTargetSettings, file_type: str, status: str = STATUS_LISTED) -> str:
    normalized_status = _normalize_status(status)
    if normalized_status == "成交":
        file_path = str(
            settings.deal_files.get(file_type)
            or settings.deal_files.get(_DEAL_OUTPUT_FILE_FALLBACK_KEYS.get(file_type, ""))
            or ""
        ).strip()
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


def _identity_block(data: Dict[str, Any]) -> Dict[str, Any] | None:
    business_identity = data.get("business_identity")
    if isinstance(business_identity, dict) and business_identity.get("business_id"):
        return business_identity
    canonical_record = data.get("canonical_record")
    if isinstance(canonical_record, dict):
        nested_identity = canonical_record.get("business_identity")
        if isinstance(nested_identity, dict) and nested_identity.get("business_id"):
            return nested_identity
    return None


def _explicit_output_kind(data: Dict[str, Any] | ParsedProject) -> str:
    if isinstance(data, ParsedProject):
        record_family = str(getattr(data, "record_family", "") or "listing")
        raw_business = getattr(data, "business_id", None) or data.project_type
    else:
        identity = _identity_block(data)
        if identity is not None:
            record_family = str(data.get("record_family") or identity.get("record_family") or "")
            raw_business = identity.get("business_id")
        else:
            record_family = str(data.get("record_family") or "")
            raw_business = None
        if not raw_business:
            raw_business = data.get("business_id")
    try:
        profile = resolve_projection_profile(record_family, str(raw_business or ""))
    except KeyError:
        return ""
    return profile.output_kind if profile is not None else ""


def decide_output_file(
    data: Dict[str, Any] | ParsedProject,
    *,
    settings: Optional[OutputTargetSettings] = None,
) -> Optional[str]:
    resolved_settings = settings or OutputTargetSettings()
    if _resolve_source_exchange(data) == "public_resource":
        return None

    status = _resolve_status(data)
    output_key = _explicit_output_kind(data)
    if output_key:
        if output_key == "pre_disclosure":
            return _resolve_output_file(resolved_settings, output_key, STATUS_LISTED)
        return _resolve_output_file(resolved_settings, output_key, status)

    return None
