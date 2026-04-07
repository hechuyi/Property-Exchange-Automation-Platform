"""Export projection: the ONLY allowed flat output boundary for export.

This module provides the canonical export projection that transforms a
CanonicalRecord into a flat dict suitable for export/output.

Export must use canonical data only - NO raw payload merge fallback.
Missing canonical fields must fail loudly through PipelineFailure or PostProcessFinding.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from peap_core import CanonicalRecord
from peap_core.error_contracts import PipelineFailure
from peap.streaming_models import PostProcessFinding

from .output_contract import clone_output_columns


# Required canonical fields that must be preserved through the chain
REQUIRED_EXPORT_FIELDS = frozenset({
    "project_code",
    "project_name",
    "project_type",
    "status",
    "start_date",
    "price",
    "seller",
    "source_type",
    "group_name",
})

# Mapping from canonical field names to compat (Chinese) export field names
CANONICAL_TO_COMPAT = {
    "project_code": "项目编号",
    "project_name": "项目名称",
    "project_type": "项目类型",
    "status": "项目状态",
    "exchange": "交易所",
    "seller": "转让方",
    "source_type": "类型",
    "group_name": "隶属集团",
    "start_date": "挂牌开始日期",
    "price": "挂牌价格",
}

EXPORT_EXTRA_FIELDS = frozenset(
    column_name
    for columns in clone_output_columns().values()
    for column_name in columns
    if column_name != "ID" and column_name not in set(CANONICAL_TO_COMPAT.values())
)


class ExportProjectionError(Exception):
    """Raised when export projection cannot produce a valid output."""
    pass


def _compute_missing_canonical_fields(canonical_fields: Dict[str, Any]) -> tuple[str, ...]:
    """Compute which required canonical fields are missing or empty."""
    missing = []
    for field_name in REQUIRED_EXPORT_FIELDS:
        value = canonical_fields.get(field_name)
        if value is None or str(value).strip() == "":
            missing.append(field_name)
    return tuple(missing)


def project_canonical_record_to_export_payload(
    canonical: CanonicalRecord | Dict[str, Any],
    *,
    fail_on_missing: bool = True,
) -> tuple[Dict[str, Any], tuple[PostProcessFinding, ...]]:
    """Project a canonical record to an export-ready flat payload.

    This is the ONLY allowed flat output boundary for export.

    Args:
        canonical: A CanonicalRecord or a dict with 'canonical_fields' key
        fail_on_missing: If True, raise PipelineFailure for missing required fields

    Returns:
        tuple of (export_payload, findings)

    Raises:
        ExportProjectionError: If fail_on_missing=True and required fields are absent
        PipelineFailure: If a required canonical field is completely missing
    """
    findings: list[PostProcessFinding] = []

    # Extract canonical fields
    export_extras: Dict[str, Any] = {}
    if isinstance(canonical, CanonicalRecord):
        canonical_fields = dict(canonical.canonical_fields)
        export_extras = dict(canonical.export_extras)
    elif isinstance(canonical, dict):
        nested = canonical.get("canonical_fields")
        if isinstance(nested, dict):
            canonical_fields = dict(nested)
        else:
            canonical_fields = dict(canonical)
        nested_extras = canonical.get("export_extras")
        if isinstance(nested_extras, dict):
            export_extras = dict(nested_extras)
    else:
        raise ExportProjectionError(f"Expected CanonicalRecord or dict, got {type(canonical)}")

    # Check for missing required fields
    missing_fields = _compute_missing_canonical_fields(canonical_fields)

    if missing_fields:
        message = f"Missing required canonical fields for export: {', '.join(missing_fields)}"
        failure = PipelineFailure(
            code="canonical_field_missing",
            component="export_projection",
            stage="project",
            recoverability="permanent",
            message=message,
            context={"missing_fields": list(missing_fields)},
        )
        if fail_on_missing:
            # Fail through PipelineFailure - store in context and raise error
            raise ExportProjectionError(str(failure)) from None
        else:
            # Warn through PostProcessFinding
            findings.append(
                PostProcessFinding(
                    severity="warn",
                    type="canonical_field_missing",
                    message=message,
                    evidence={"missing_fields": list(missing_fields)},
                )
            )

    # Build export payload from canonical fields only
    export_payload: Dict[str, Any] = {}
    for canonical_key, compat_key in CANONICAL_TO_COMPAT.items():
        value = canonical_fields.get(canonical_key)
        if value is not None and str(value).strip() != "":
            export_payload[compat_key] = value
    for field_name in sorted(EXPORT_EXTRA_FIELDS):
        if field_name in export_payload:
            continue
        value = export_extras.get(field_name)
        if value is not None and str(value).strip() != "":
            export_payload[field_name] = value

    return export_payload, tuple(findings)


__all__ = [
    "REQUIRED_EXPORT_FIELDS",
    "CANONICAL_TO_COMPAT",
    "EXPORT_EXTRA_FIELDS",
    "ExportProjectionError",
    "project_canonical_record_to_export_payload",
]
