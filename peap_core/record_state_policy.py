"""Shared record-state policy for the pipeline.

语义收敛点:
- record state 分类逻辑 (classify_record_state)
- backlog ownership 判定 (state_requires_mapping_pending)
- maintenance 重分类范围 (state_allows_maintenance_reclassification)
- export blocker 映射 (state_to_export_blocker_category)

硬约束:
- 不允许重新定义 RecordState
- 不允许 import peap.streaming_models
- 不允许在 policy 中出现 store/app I/O
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Protocol

from peap_core.pipeline_state_contracts import RecordState


class ExportBlockerCategory(str, Enum):
    """Export blocker categories — shared semantic layer."""
    NONE = "none"
    PENDING_MAPPING = "pending_mapping"
    MAPPING_CONFLICT = "mapping_conflict"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    FIELD_MISSING = "field_missing"


class FindingLike(Protocol):
    """Protocol for finding-like objects (PostProcessFinding, dict)."""
    type: str


def _state_value(state) -> str:
    """Normalize a RecordState to its string value.

    Uses .value to avoid inconsistencies from str() vs .name.
    """
    if isinstance(state, RecordState):
        return state.value
    return str(state or "").strip()

MAINTENANCE_NORMALIZABLE_STATES: tuple[RecordState, ...] = (
    RecordState.READY,
    RecordState.PENDING_MAPPING,
    RecordState.PENDING_REVIEW,
)

OPTIONAL_RULE_NORMALIZABLE_STATES: tuple[RecordState, ...] = (
    *MAINTENANCE_NORMALIZABLE_STATES,
    RecordState.SKIPPED,
)

BACKLOG_OWNING_STATES: tuple[RecordState, ...] = (
    RecordState.PENDING_MAPPING,
)


def classify_record_state(findings: Iterable, *, had_conflict: bool = False) -> RecordState:
    """Classify record state from findings.

    Finding type priority (highest first):
    1. rule_filtered -> SKIPPED
    2. {business_resolution_required, rule_error} -> PENDING_REVIEW
    3. mapping_conflict -> MAPPING_CONFLICT
    4. {mapping_missing, mapping_gap, mapping_ambiguous} -> PENDING_MAPPING
    5. had_conflict -> CONFLICT
    6. otherwise -> READY
    """
    finding_types: set[str] = set()
    for f in findings:
        if hasattr(f, 'type'):
            finding_types.add(_state_value(f.type))
        elif isinstance(f, dict):
            finding_types.add(str(f.get('type', '')))
    if "rule_filtered" in finding_types:
        return RecordState.SKIPPED
    if {
        "export_field_missing",
        "canonical_field_missing",
        "source_artifact_missing",
        "source_artifact_invalid",
    } & finding_types:
        return RecordState.FIELD_MISSING
    if {"business_resolution_required", "rule_error", "parse_partial", "parse_unrecoverable"} & finding_types:
        return RecordState.PENDING_REVIEW
    if "mapping_conflict" in finding_types:
        return RecordState.MAPPING_CONFLICT
    if {"mapping_missing", "mapping_gap", "mapping_ambiguous"} & finding_types:
        return RecordState.PENDING_MAPPING
    if had_conflict:
        return RecordState.CONFLICT
    return RecordState.READY


def state_requires_mapping_pending(state) -> bool:
    """Return True if records in this state should own a mapping_pending backlog row."""
    try:
        normalized = RecordState(_state_value(state))
    except ValueError:
        return False
    return normalized in BACKLOG_OWNING_STATES


def state_allows_maintenance_reclassification(state) -> bool:
    """Return True if records in this state are candidates for maintenance reclassification."""
    try:
        s = RecordState(_state_value(state))
    except ValueError:
        return False
    return s in MAINTENANCE_NORMALIZABLE_STATES


def state_to_export_blocker_category(state) -> ExportBlockerCategory:
    """Map record state to export blocker category.

    mapping_conflict and conflict are distinct blocker categories because they
    represent different remediation paths at the export boundary.
    """
    try:
        s = RecordState(_state_value(state))
    except ValueError:
        return ExportBlockerCategory.NONE

    if s == RecordState.PENDING_MAPPING:
        return ExportBlockerCategory.PENDING_MAPPING
    if s == RecordState.MAPPING_CONFLICT:
        return ExportBlockerCategory.MAPPING_CONFLICT
    if s == RecordState.CONFLICT:
        return ExportBlockerCategory.CONFLICT
    if s == RecordState.SKIPPED:
        return ExportBlockerCategory.SKIPPED
    if s == RecordState.FIELD_MISSING:
        return ExportBlockerCategory.FIELD_MISSING
    return ExportBlockerCategory.NONE
