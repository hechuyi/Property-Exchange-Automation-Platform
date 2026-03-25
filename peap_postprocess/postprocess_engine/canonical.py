"""Canonical layer: convert sheet rows to normalized records."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .adapters import TabularSheet
from .contracts import CanonicalRecord

PROJECT_CODE_FIELDS = [
    "project_code",
    "project id",
    "project_id",
    "project_no",
    "项目编号",
    "项目编码",
    "产权项目编号",
]

COMPANY_PRIMARY_FIELDS = [
    "company_name_primary",
    "company_name",
    "seller_name",
    "融资方",
    "融资方名称",
    "转让方",
    "转让方名称",
    "企业名称",
    "标的企业",
]

GROUP_NAME_FIELDS = [
    "group_name",
    "group",
    "所属集团",
    "隶属集团",
    "集团名称",
]


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _first_non_empty(raw_fields: Dict[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        if key not in raw_fields:
            continue
        value = _normalize_value(raw_fields.get(key, ""))
        if value:
            return value
    return ""


def canonicalize_sheet(sheet: TabularSheet) -> List[CanonicalRecord]:
    records: List[CanonicalRecord] = []
    for index, row in sheet.dataframe.iterrows():
        raw_fields: Dict[str, str] = {
            str(column).strip(): _normalize_value(value) for column, value in row.to_dict().items()
        }
        if not any(raw_fields.values()):
            continue

        records.append(
            CanonicalRecord(
                source_file=sheet.file_path,
                file_name=sheet.file_name,
                sheet_name=sheet.sheet_name,
                row_index=int(index) + 2,  # +1 header row and +1 to make row index 1-based.
                project_code=_first_non_empty(raw_fields, PROJECT_CODE_FIELDS),
                company_name_primary=_first_non_empty(raw_fields, COMPANY_PRIMARY_FIELDS),
                group_name=_first_non_empty(raw_fields, GROUP_NAME_FIELDS),
                raw_fields=raw_fields,
            )
        )
    return records
