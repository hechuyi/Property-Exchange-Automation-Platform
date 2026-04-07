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


class ExportBlockerCategory(str, Enum):
    """Export blocker categories — shared semantic layer."""
    NONE = "none"
    PENDING_MAPPING = "pending_mapping"
    CONFLICT = "conflict"
    SKIPPED = "skipped"


class FindingLike(Protocol):
    """Protocol for finding-like objects (PostProcessFinding, dict)."""
    type: str


def _state_value(state) -> str:
    """Normalize a RecordState to its string value.

    Uses .value to avoid inconsistencies from str() vs .name.
    """
    # Import here to avoid circular import at module level
    from peap_core.pipeline_state_contracts import RecordState
    if isinstance(state, RecordState):
        return state.value
    return str(state or "").strip()


# Import RecordState at module level for type annotations (no circular issue)
from peap_core.pipeline_state_contracts import RecordState

MAINTENANCE_NORMALIZABLE_STATES: tuple[RecordState, ...] = (
    RecordState.READY,
    RecordState.PENDING_MAPPING,
)

BACKLOG_OWNING_STATES: tuple[RecordState, ...] = (
    RecordState.PENDING_MAPPING,
)


def classify_record_state(findings: Iterable, *, had_conflict: bool = False) -> RecordState:
    """Classify record state from findings.

    Finding type priority (highest first):
    1. mapping_conflict -> MAPPING_CONFLICT
    2. {mapping_missing, mapping_gap, mapping_ambiguous, project_type_unknown} -> PENDING_MAPPING
    3. had_conflict -> CONFLICT
    4. otherwise -> READY
    """
    finding_types: set[str] = set()
    for f in findings:
        if hasattr(f, 'type'):
            finding_types.add(_state_value(f.type))
        elif isinstance(f, dict):
            finding_types.add(str(f.get('type', '')))
    if "mapping_conflict" in finding_types:
        return RecordState.MAPPING_CONFLICT
    if {"mapping_missing", "mapping_gap", "mapping_ambiguous", "project_type_unknown"} & finding_types:
        return RecordState.PENDING_MAPPING
    if had_conflict:
        return RecordState.CONFLICT
    return RecordState.READY


def state_requires_mapping_pending(state) -> bool:
    """Return True if records in this state should own a mapping_pending backlog row."""
    return _state_value(state) == RecordState.PENDING_MAPPING.value


def state_allows_maintenance_reclassification(state) -> bool:
    """Return True if records in this state are candidates for maintenance reclassification."""
    try:
        s = RecordState(_state_value(state))
    except ValueError:
        return False
    return s in MAINTENANCE_NORMALIZABLE_STATES


def state_to_export_blocker_category(state) -> ExportBlockerCategory:
    """Map record state to export blocker category.

    Note: conflict and mapping_conflict are both CONFLICT at the policy level.
    UI may surface mapping_conflict_blocked as an alias for CONFLICT.
    """
    try:
        s = RecordState(_state_value(state))
    except ValueError:
        return ExportBlockerCategory.NONE

    if s == RecordState.PENDING_MAPPING:
        return ExportBlockerCategory.PENDING_MAPPING
    if s in (RecordState.MAPPING_CONFLICT, RecordState.CONFLICT):
        return ExportBlockerCategory.CONFLICT
    if s == RecordState.SKIPPED:
        return ExportBlockerCategory.SKIPPED
    return ExportBlockerCategory.NONE
