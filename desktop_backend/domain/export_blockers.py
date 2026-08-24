"""Shared export-empty blocker policy for desktop backend resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Dict

from peap_core.record_state_policy import ExportBlockerCategory, state_to_export_blocker_category


def summarize_export_blocker_categories(scope_state_counts: Dict[str, int] | None) -> Dict[ExportBlockerCategory, int]:
    totals: Dict[ExportBlockerCategory, int] = {
        ExportBlockerCategory.NONE: 0,
        ExportBlockerCategory.PENDING_MAPPING: 0,
        ExportBlockerCategory.MAPPING_CONFLICT: 0,
        ExportBlockerCategory.CONFLICT: 0,
        ExportBlockerCategory.SKIPPED: 0,
        ExportBlockerCategory.FIELD_MISSING: 0,
    }
    if scope_state_counts is None:
        state_counts = {}
    elif isinstance(scope_state_counts, Mapping):
        state_counts = scope_state_counts
    else:
        raise TypeError("scope_state_counts must be a mapping")

    for raw_state, raw_count in state_counts.items():
        count = int(raw_count or 0)
        if count <= 0:
            continue
        category = state_to_export_blocker_category(raw_state)
        totals[category] = totals.get(category, 0) + count
    return totals


def classify_empty_export_result(
    scope_state_counts: Dict[str, int] | None,
    *,
    field_missing_blocked_records: int = 0,
) -> tuple[str, str]:
    category_totals = summarize_export_blocker_categories(scope_state_counts)
    pending_count = category_totals[ExportBlockerCategory.PENDING_MAPPING]
    mapping_conflict_count = category_totals[ExportBlockerCategory.MAPPING_CONFLICT]
    conflict_count = category_totals[ExportBlockerCategory.CONFLICT]
    skipped_count = category_totals[ExportBlockerCategory.SKIPPED]
    field_missing_count = category_totals[ExportBlockerCategory.FIELD_MISSING]

    if pending_count > 0:
        return ("pending_mapping_blocked", f"当前条件下没有可导出的记录；待补映射 {pending_count} 条")

    if mapping_conflict_count > 0:
        message = f"当前条件下没有可导出的记录；映射冲突 {mapping_conflict_count} 条"
        if conflict_count > 0:
            message = f"{message}，记录冲突 {conflict_count} 条"
        return ("mapping_conflict_blocked", message)

    if conflict_count > 0:
        return ("conflict_blocked", f"当前条件下没有可导出的记录；记录冲突 {conflict_count} 条")

    if skipped_count > 0:
        return ("skipped_only", f"当前条件下没有可导出的记录；已跳过 {skipped_count} 条")

    resolved_field_missing_count = field_missing_count or int(field_missing_blocked_records or 0)
    if resolved_field_missing_count > 0:
        return (
            "field_missing_blocked_records",
            f"当前条件下没有可导出的记录；导出字段缺失 {resolved_field_missing_count} 条",
        )

    return ("no_matching_records", "当前条件下没有可导出的记录")
