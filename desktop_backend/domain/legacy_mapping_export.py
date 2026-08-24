"""Helpers for exporting canonical mapping entries into legacy CSV tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegacyMappingExportFile:
    table_key: str
    file_name: str
    header: tuple[str, ...]
    rows: list[dict[str, str]]


_EXPORT_SPECS = {
    "transferor_group": {
        "file_name": "transferor_group_mapping_template.csv",
        "header": ("transferor_name", "group_name", "notes"),
    },
    "transferor_type": {
        "file_name": "transferor_type_mapping_template.csv",
        "header": ("transferor_name", "source_type", "notes"),
    },
    "group_group": {
        "file_name": "group_group_mapping_template.csv",
        "header": ("group_name", "parent_group_name", "notes"),
    },
    "group_type": {
        "file_name": "group_type_mapping_template.csv",
        "header": ("group_name", "source_type", "notes"),
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _resolved_rule_kind(entry: dict[str, Any]) -> str:
    return _text(entry.get("rule_kind"))


def _validate_export_entry(
    index: int, *, source_name: str, rule_kind: str, target_value: str
) -> None:
    if not source_name:
        raise ValueError(f"malformed legacy mapping export entry {index}: source_name is required")
    if not rule_kind:
        raise ValueError(f"malformed legacy mapping export entry {index}: rule_kind is required")
    if rule_kind not in _EXPORT_SPECS:
        raise ValueError(
            f"malformed legacy mapping export entry {index}: unknown rule_kind: {rule_kind}"
        )
    if not target_value:
        raise ValueError(f"malformed legacy mapping export entry {index}: target_value is required")


def build_legacy_mapping_export_files(
    entries: list[dict[str, Any]],
) -> list[LegacyMappingExportFile]:
    if not isinstance(entries, list):
        raise TypeError("legacy mapping export entries must be a list")
    rows_by_table: dict[str, list[dict[str, str]]] = {
        "transferor_group": [],
        "transferor_type": [],
        "group_group": [],
        "group_type": [],
    }

    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            raise TypeError("legacy mapping export entries must be objects")
        entry = dict(raw_entry)
        rule_kind = _resolved_rule_kind(entry)
        source_name = _text(entry.get("source_name"))
        target_value = _text(entry.get("target_value"))
        notes = _text(entry.get("notes"))
        _validate_export_entry(
            index,
            source_name=source_name,
            rule_kind=rule_kind,
            target_value=target_value,
        )
        if rule_kind == "transferor_group":
            rows_by_table["transferor_group"].append(
                {
                    "transferor_name": source_name,
                    "group_name": target_value,
                    "notes": notes,
                }
            )
        elif rule_kind == "transferor_type":
            rows_by_table["transferor_type"].append(
                {
                    "transferor_name": source_name,
                    "source_type": target_value,
                    "notes": notes,
                }
            )
        elif rule_kind == "group_group":
            rows_by_table["group_group"].append(
                {
                    "group_name": source_name,
                    "parent_group_name": target_value,
                    "notes": notes,
                }
            )
        elif rule_kind == "group_type":
            rows_by_table["group_type"].append(
                {
                    "group_name": source_name,
                    "source_type": target_value,
                    "notes": notes,
                }
            )

    export_files: list[LegacyMappingExportFile] = []
    for table_key in ("transferor_group", "transferor_type", "group_group", "group_type"):
        spec = _EXPORT_SPECS[table_key]
        header = tuple(spec["header"])
        rows = sorted(
            rows_by_table[table_key],
            key=lambda row: tuple(_text(row.get(column)).casefold() for column in header),
        )
        export_files.append(
            LegacyMappingExportFile(
                table_key=table_key,
                file_name=str(spec["file_name"]),
                header=header,
                rows=rows,
            )
        )
    return export_files
