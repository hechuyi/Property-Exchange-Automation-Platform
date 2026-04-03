"""Dual-run compatibility comparison helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from .constants import KEY_LISTING_TIMES, KEY_PROJECT_CODE
from .parsing import ParsedProject

KEY_SOURCE_TYPE = "\u7c7b\u578b"
DEFAULT_COMPARE_FIELDS = [KEY_SOURCE_TYPE, KEY_LISTING_TIMES]


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _as_compare_mapping(data: Mapping[str, Any] | ParsedProject) -> Mapping[str, Any]:
    if isinstance(data, ParsedProject):
        return data.standard_record.to_legacy_payload(include_raw=True)
    return data


def compare_data_fields(
    *,
    file_path: str,
    compare_fields: Iterable[str],
    primary_profile: str,
    baseline_profile: str,
    primary_data: Mapping[str, Any] | ParsedProject,
    baseline_data: Mapping[str, Any] | ParsedProject,
) -> List[Dict[str, Any]]:
    primary_mapping = _as_compare_mapping(primary_data)
    baseline_mapping = _as_compare_mapping(baseline_data)
    project_code = _normalize(primary_mapping.get(KEY_PROJECT_CODE)) or _normalize(
        baseline_mapping.get(KEY_PROJECT_CODE)
    )
    diffs: List[Dict[str, Any]] = []
    for field in compare_fields:
        old_value = _normalize(baseline_mapping.get(field))
        new_value = _normalize(primary_mapping.get(field))
        if old_value == new_value:
            continue
        diffs.append(
            {
                "file": file_path,
                "project_code": project_code,
                "field": field,
                "baseline_profile": baseline_profile,
                "baseline_value": old_value,
                "primary_profile": primary_profile,
                "primary_value": new_value,
            }
        )
    return diffs
