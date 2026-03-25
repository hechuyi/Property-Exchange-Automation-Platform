"""Core data contracts for PostProcess Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal

Severity = Literal["info", "warn", "error"]


@dataclass(frozen=True)
class CanonicalRecord:
    """Normalized view for one row across all file types."""

    source_file: str
    file_name: str
    sheet_name: str
    row_index: int
    project_code: str
    company_name_primary: str
    group_name: str
    raw_fields: Dict[str, Any]


@dataclass(frozen=True)
class Patch:
    field: str
    old_value: Any
    new_value: Any
    action: str = "update"
    reason: str = ""


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    type: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleResult:
    patches: List[Patch] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    stop_processing: bool = False


@dataclass(frozen=True)
class AuditRow:
    run_id: str
    timestamp: str
    file: str
    sheet: str
    row: int
    project_code: str
    rule_id: str
    severity: str
    action: str
    field: str
    old_value: str
    new_value: str
    reason: str
    evidence: str


@dataclass
class ExecutionSummary:
    run_id: str
    mode: str
    discovered_files: int = 0
    processed_files: int = 0
    processed_rows: int = 0
    applied_patches: int = 0
    findings: int = 0
    failed_files: int = 0
    output_files: List[str] = field(default_factory=list)
    audit_report: str = ""
    errors: List[str] = field(default_factory=list)
