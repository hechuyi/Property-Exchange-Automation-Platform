"""Helpers for importing legacy CSV-based mapping tables into canonical mapping entries."""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .normalizers import normalize_match_text

_FILENAME_PATTERNS = {
    "transferor_group": ("transferor_group_mapping", "transferor_group"),
    "transferor_type": ("transferor_type_mapping", "transferor_type"),
    "group_group": ("group_group_mapping", "group_group"),
    "group_type": ("group_type_mapping", "entity_type_mapping", "group_type", "entity_type"),
}

_TYPE_ALIASES = ("source_type", "type", "category", "类别", "类型")
_TRANSFEROR_NAME_ALIASES = (
    "transferor_name",
    "transferor",
    "seller_name",
    "seller",
    "company_name",
    "name",
    "转让方",
    "转让方名称",
    "融资方",
    "融资方名称",
    "名称",
)
_GROUP_NAME_ALIASES = (
    "group_name",
    "group",
    "集团名称",
    "隶属集团",
    "名称",
)
_PARENT_GROUP_ALIASES = (
    "parent_group_name",
    "parent_group",
    "parent_name",
    "group_parent",
    "上级集团",
    "上级集团名称",
)
_ENTITY_NAME_ALIASES = (
    "entity_name",
    "name",
    "group_name",
    "group",
    "company_name",
    "ministry_name",
    "ministry",
    "集团名称",
    "主管部委",
    "部委",
    "部委名称",
    "名称",
)
_ENTITY_KIND_ALIASES = ("entity_kind", "kind", "entity_type", "主体类型", "对象类型")


@dataclass(frozen=True)
class LegacyMappingEntry:
    source_name: str
    target_value: str
    rule_kind: str
    match_field: str
    target_field: str


@dataclass(frozen=True)
class LegacyMappingImportPlan:
    input_dir: str
    loaded_files: dict[str, str]
    entries: list[LegacyMappingEntry]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized_file_token(file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(str(file_name or "")))[0]
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _infer_table_key(file_name: str) -> str:
    normalized = _normalized_file_token(file_name)
    for table_key, patterns in _FILENAME_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            return table_key
    return ""


def _normalize_column_key(value: str) -> str:
    normalized = _clean(value).lower()
    normalized = re.sub(r"[\s\-./]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def _normalize_row(row: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(row, Mapping):
        raise TypeError("legacy mapping row must be an object")

    normalized: dict[str, str] = {}
    for key, value in row.items():
        normalized_key = _normalize_column_key(str(key or ""))
        if not normalized_key or normalized_key in normalized:
            continue
        normalized[normalized_key] = _clean(value)
    return normalized


def _pick_row_value(row: dict[str, str], aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        value = row.get(_normalize_column_key(alias))
        if value:
            return _clean(value)
    return ""


def _normalize_entity_kind(value: str) -> str:
    key = _normalize_column_key(value)
    return {
        "group": "group",
        "集团": "group",
        "company": "company",
        "公司": "company",
        "企业": "company",
        "transferor": "company",
        "seller": "company",
        "转让方": "company",
        "融资方": "company",
        "ministry": "group",
        "department": "group",
        "部委": "group",
        "主管部门": "group",
        "any": "group",
        "all": "group",
        "both": "group",
        "全部": "group",
    }.get(key, "group")


def _read_csv_rows(path: str) -> list[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise ValueError(f"legacy mapping csv has no header: {path}")
                rows: list[dict[str, str]] = []
                for raw_row in reader:
                    if not isinstance(raw_row, dict):
                        continue
                    normalized = _normalize_row(raw_row)
                    if normalized:
                        rows.append(normalized)
                return rows
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise ValueError(f"legacy mapping csv encoding not supported: {path}") from last_error
    raise ValueError(f"failed to open legacy mapping csv: {path}")


def _parse_transferor_group(rows: list[dict[str, str]]) -> list[LegacyMappingEntry]:
    entries: list[LegacyMappingEntry] = []
    for row in rows:
        source_name = _pick_row_value(row, _TRANSFEROR_NAME_ALIASES)
        target_value = _pick_row_value(row, _GROUP_NAME_ALIASES)
        if not source_name or not target_value:
            continue
        entries.append(
            LegacyMappingEntry(
                source_name=source_name,
                target_value=target_value,
                rule_kind="transferor_group",
                match_field="transferor",
                target_field="group_name",
            )
        )
    return entries


def _parse_transferor_type(rows: list[dict[str, str]]) -> list[LegacyMappingEntry]:
    entries: list[LegacyMappingEntry] = []
    for row in rows:
        source_name = _pick_row_value(row, _TRANSFEROR_NAME_ALIASES)
        target_value = _pick_row_value(row, _TYPE_ALIASES)
        if not source_name or not target_value:
            continue
        entries.append(
            LegacyMappingEntry(
                source_name=source_name,
                target_value=target_value,
                rule_kind="transferor_type",
                match_field="transferor",
                target_field="source_type",
            )
        )
    return entries


def _parse_group_group(rows: list[dict[str, str]]) -> list[LegacyMappingEntry]:
    entries: list[LegacyMappingEntry] = []
    for row in rows:
        source_name = _pick_row_value(row, _GROUP_NAME_ALIASES)
        target_value = _pick_row_value(row, _PARENT_GROUP_ALIASES)
        if not source_name or not target_value:
            continue
        entries.append(
            LegacyMappingEntry(
                source_name=source_name,
                target_value=target_value,
                rule_kind="group_group",
                match_field="group",
                target_field="group_name",
            )
        )
    return entries


def _parse_group_type(rows: list[dict[str, str]]) -> list[LegacyMappingEntry]:
    entries: list[LegacyMappingEntry] = []
    for row in rows:
        source_name = _pick_row_value(row, _ENTITY_NAME_ALIASES)
        target_value = _pick_row_value(row, _TYPE_ALIASES)
        if not source_name or not target_value:
            continue
        entity_kind = _normalize_entity_kind(_pick_row_value(row, _ENTITY_KIND_ALIASES))
        rule_kind = "transferor_type" if entity_kind == "company" else "group_type"
        match_field = "transferor" if entity_kind == "company" else "group"
        entries.append(
            LegacyMappingEntry(
                source_name=source_name,
                target_value=target_value,
                rule_kind=rule_kind,
                match_field=match_field,
                target_field="source_type",
            )
        )
    return entries


def _parse_table_rows(table_key: str, rows: list[dict[str, str]]) -> list[LegacyMappingEntry]:
    if table_key == "transferor_group":
        return _parse_transferor_group(rows)
    if table_key == "transferor_type":
        return _parse_transferor_type(rows)
    if table_key == "group_group":
        return _parse_group_group(rows)
    if table_key == "group_type":
        return _parse_group_type(rows)
    return []


def _dedupe_entries(entries: list[LegacyMappingEntry]) -> list[LegacyMappingEntry]:
    deduped: dict[tuple[str, str, str], LegacyMappingEntry] = {}
    for entry in entries:
        key = (
            entry.match_field,
            entry.target_field,
            normalize_match_text(entry.source_name),
        )
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = entry
            continue
        if existing.target_value == entry.target_value:
            continue
        raise ValueError(
            "conflicting legacy mapping rows for "
            f"{entry.rule_kind}: {entry.source_name} -> {existing.target_value} / {entry.target_value}"
        )
    return list(deduped.values())


def load_legacy_mapping_import_plan(input_dir: str) -> LegacyMappingImportPlan:
    root = os.path.abspath(_clean(input_dir))
    if not root:
        raise ValueError("input_dir is required")
    if not os.path.isdir(root):
        raise ValueError(f"legacy mapping directory not found: {root}")

    loaded_files: dict[str, str] = {}
    for current_root, dir_names, file_names in os.walk(root):
        dir_names.sort()
        for file_name in sorted(file_names):
            if not file_name.lower().endswith(".csv"):
                continue
            table_key = _infer_table_key(file_name)
            if not table_key:
                continue
            file_path = os.path.join(current_root, file_name)
            if table_key in loaded_files:
                raise ValueError(f"multiple legacy mapping csv files matched {table_key}: {loaded_files[table_key]} / {file_path}")
            loaded_files[table_key] = file_path

    if not loaded_files:
        raise ValueError(f"no legacy mapping csv files found under: {root}")

    entries: list[LegacyMappingEntry] = []
    for table_key in ("transferor_group", "transferor_type", "group_group", "group_type"):
        file_path = loaded_files.get(table_key)
        if not file_path:
            continue
        rows = _read_csv_rows(file_path)
        entries.extend(_parse_table_rows(table_key, rows))

    deduped_entries = _dedupe_entries(entries)
    if not deduped_entries:
        raise ValueError(f"legacy mapping csv files did not contain any importable rows: {root}")

    return LegacyMappingImportPlan(
        input_dir=root,
        loaded_files=loaded_files,
        entries=deduped_entries,
    )
