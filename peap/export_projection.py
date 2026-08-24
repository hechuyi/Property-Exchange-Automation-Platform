"""Export projection: the ONLY allowed flat output boundary for export.

This module provides the canonical export projection that transforms a
CanonicalRecord into a flat dict suitable for export/output.

Export must use canonical data only - NO raw payload merge fallback.
Missing canonical fields must fail loudly through PipelineFailure or PostProcessFinding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterable

from peap.streaming_models import PostProcessFinding
from peap.streaming_postprocess import is_summary_investor_name
from peap_core import CanonicalRecord
from peap_core.error_contracts import PipelineFailure
from peap_core.source_business_contract import list_export_readiness_requirements

from .output_contract import KIND_PHYSICAL, KIND_PRE, clone_field_candidates, clone_output_columns
from .projection_registry import resolve_projection_profile

# Required canonical fields that must be preserved through the chain for listing exports.
DEFAULT_LISTING_REQUIRED_CANONICAL_FIELDS = (
    "project_code",
    "project_name",
    "status",
    "start_date",
    "price",
    "seller",
)

DEAL_REQUIRED_EXPORT_FIELDS = (
    "project_code",
    "project_name",
    "status",
)

DEAL_READINESS_REQUIREMENTS = tuple(list_export_readiness_requirements(record_family="deal"))
DEAL_PRICE_REQUIRED_BUSINESS_IDS = frozenset(
    item.business_id for item in DEAL_READINESS_REQUIREMENTS if item.requires_deal_price
)
DEAL_CAPITAL_INCREASE_BUSINESS_IDS = frozenset(
    item.business_id
    for item in DEAL_READINESS_REQUIREMENTS
    if item.requires_non_summary_investor and item.requires_investor_amount
)
DEAL_DATE_AUDIT_MISSING_FIELD = next(
    (
        item.deal_date_policy
        for item in DEAL_READINESS_REQUIREMENTS
        if item.deal_date_policy and item.allows_collection_date_audit_fallback
    ),
    "deal_date_or_collection_date_audit",
)

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
    "deal_date": "成交日期",
    "deal_price": "交易价格",
    "valuation": "转让标的评估值",
    "reserve_price": "转让底价",
    "listing_times": "挂牌次数",
}

_OUTPUT_CONTRACT_COLUMNS = {
    column_name
    for columns in clone_output_columns().values()
    for column_name in columns
}
_OUTPUT_CONTRACT_FIELD_CANDIDATES = {
    candidate_name
    for mapping in clone_field_candidates().values()
    for candidates in mapping.values()
    for candidate_name in candidates
}
EXPORT_EXTRA_FIELDS = frozenset(
    field_name
    for field_name in (_OUTPUT_CONTRACT_COLUMNS | _OUTPUT_CONTRACT_FIELD_CANDIDATES)
    if field_name != "ID" and field_name not in set(CANONICAL_TO_COMPAT.values())
)

EXPORT_EMPTY_PLACEHOLDERS = frozenset(
    {
        "-",
        "--",
        "\u2014",
        "/",
        "\uff0f",
        "\u6682\u65e0",
        "N/A",
        "NA",
        "null",
        "None",
        "\u65e0",
    }
)


@dataclass(frozen=True)
class ExportRequirementProfile:
    required_canonical_fields: tuple[str, ...]
    required_export_fields: tuple[tuple[str, str], ...] = ()


DEFAULT_LISTING_EXPORT_REQUIREMENTS = ExportRequirementProfile(
    required_canonical_fields=DEFAULT_LISTING_REQUIRED_CANONICAL_FIELDS,
)
DEAL_EXPORT_REQUIREMENTS = ExportRequirementProfile(
    required_canonical_fields=DEAL_REQUIRED_EXPORT_FIELDS,
)
EXPORT_REQUIREMENTS_BY_OUTPUT_KIND = {
    KIND_PHYSICAL: ExportRequirementProfile(
        required_canonical_fields=(
            "project_code",
            "project_name",
            "start_date",
            "price",
        ),
        required_export_fields=(
            ("项目编号", "project_code"),
            ("项目名称", "project_name"),
            ("挂牌开始日期", "start_date"),
            ("挂牌价格（万元）", "price"),
            ("转让方", "seller"),
            ("类型", "source_type"),
        ),
    ),
    KIND_PRE: ExportRequirementProfile(
        required_canonical_fields=(
            "project_code",
            "project_name",
            "status",
            "start_date",
            "seller",
        ),
    ),
}


def _business_id_of(source: CanonicalRecord | Dict[str, Any]) -> str:
    business_identity = _business_identity(source)
    business_id = str(business_identity.get("business_id") or "").strip()
    if business_id:
        return business_id
    source_identity = _source_identity(source)
    return str(source_identity.get("business_id") or "").strip()


def _derived_project_type_label(
    *,
    canonical_fields: Dict[str, Any],
    business_identity: Dict[str, Any],
) -> str:
    project_type = str(canonical_fields.get("project_type") or "").strip()
    if project_type:
        return project_type
    raw_business_label = str(
        business_identity.get("raw_business_label")
        or business_identity.get("business_label")
        or ""
    ).strip()
    if raw_business_label:
        return raw_business_label
    return ""


def _record_family_of(source: CanonicalRecord | Dict[str, Any]) -> str:
    if isinstance(source, CanonicalRecord):
        value = source.record_family
    elif isinstance(source, dict):
        for value in (
            source.get("record_family"),
            _business_identity(source).get("record_family"),
            _source_identity(source).get("record_family"),
        ):
            family = str(value or "").strip().lower()
            if family:
                return family
        value = None
    else:
        value = None
    family = str(value or "").strip().lower()
    return family or "listing"


def _export_requirement_profile(
    *,
    record_family: str,
    business_id: str,
) -> ExportRequirementProfile:
    normalized_family = str(record_family or "").strip().lower() or "listing"
    if normalized_family == "deal":
        return DEAL_EXPORT_REQUIREMENTS
    profile = resolve_projection_profile(normalized_family, business_id)
    if profile is not None:
        requirement = EXPORT_REQUIREMENTS_BY_OUTPUT_KIND.get(profile.output_kind)
        if requirement is not None:
            return requirement
    return DEFAULT_LISTING_EXPORT_REQUIREMENTS


def _output_kind_for(
    *,
    record_family: str,
    business_id: str,
) -> str:
    profile = resolve_projection_profile(record_family, business_id)
    if profile is None:
        return ""
    return str(profile.output_kind or "").strip()


class ExportProjectionError(Exception):
    """Raised when export projection cannot produce a valid output."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "export_projection_failed",
        missing_fields: tuple[dict[str, str], ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = str(failure_code or "export_projection_failed")
        self.missing_fields = _structured_missing_fields_tuple(missing_fields)


def _structured_missing_fields_tuple(
    missing_fields: Iterable[dict[str, str]] | None,
) -> tuple[dict[str, str], ...]:
    if missing_fields is None:
        return ()
    if isinstance(missing_fields, Mapping) or isinstance(missing_fields, (str, bytes)):
        raise TypeError("missing_fields must be an iterable of mappings")
    try:
        iterator = iter(missing_fields)
    except TypeError:
        raise TypeError("missing_fields must be an iterable of mappings") from None
    resolved: list[dict[str, str]] = []
    for item in iterator:
        if not isinstance(item, Mapping):
            raise TypeError("missing_fields[*] must be a mapping")
        resolved.append({str(key): str(value) for key, value in dict(item).items()})
    return tuple(resolved)


def _missing_fields_evidence_list(evidence: Mapping[str, Any]) -> list[Any]:
    if "missing_fields" not in evidence:
        return []
    missing_fields = evidence.get("missing_fields")
    if missing_fields is None:
        return []
    if isinstance(missing_fields, Mapping) or isinstance(missing_fields, (str, bytes)):
        raise TypeError("finding.evidence.missing_fields must be a list or tuple")
    if not isinstance(missing_fields, list | tuple):
        raise TypeError("finding.evidence.missing_fields must be a list or tuple")
    return list(missing_fields)


def _identity_mapping(source: CanonicalRecord | Dict[str, Any], field_name: str) -> Dict[str, Any]:
    if isinstance(source, CanonicalRecord):
        return dict(getattr(source, field_name))
    if isinstance(source, dict):
        if field_name not in source:
            return {}
        nested = source.get(field_name)
        if isinstance(nested, dict):
            return dict(nested)
        raise ExportProjectionError(
            f"{field_name} must be an object when present, got {type(nested).__name__}",
            failure_code="invalid_identity_shape",
        )
    return {}


def _business_identity(source: CanonicalRecord | Dict[str, Any]) -> Dict[str, Any]:
    return _identity_mapping(source, "business_identity")


def _source_identity(source: CanonicalRecord | Dict[str, Any]) -> Dict[str, Any]:
    return _identity_mapping(source, "source_identity")


def _optional_object_mapping(value: Any, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise ExportProjectionError(
        f"{field_name} must be an object when present, got {type(value).__name__}",
        failure_code=f"invalid_{field_name}_shape",
    )


def is_export_empty_value(value: Any) -> bool:
    """Return whether a value is rendered as an empty export cell."""
    if value is None:
        return True
    text = str(value).strip()
    return not text or text in EXPORT_EMPTY_PLACEHOLDERS


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _has_export_ready_deal_date(canonical_fields: Dict[str, Any]) -> bool:
    basis = str(canonical_fields.get("deal_date_basis") or "").strip()
    is_collection_date_imputed = basis == "collection_date" or _coerce_bool(canonical_fields.get("deal_date_is_imputed"))
    if is_collection_date_imputed:
        return not is_export_empty_value(canonical_fields.get("collection_date"))
    return not is_export_empty_value(canonical_fields.get("deal_date"))


def _has_deal_price_unit_basis(canonical_fields: Dict[str, Any]) -> bool:
    basis = str(canonical_fields.get("deal_price_unit_basis") or "").strip()
    if basis in {
        "raw_unit",
        "converted_from_yuan",
        "converted_from_yi_yuan",
        "default_wan",
        "field_label",
        "field_unit_wan",
        "converted_from_field_yuan",
        "converted_from_field_yi_yuan",
    }:
        return True
    return False


def _first_non_empty_value(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if not is_export_empty_value(value):
            return str(value).strip()
    return ""


def _investor_missing_fields(entry: Dict[str, Any], index: int) -> tuple[str, ...]:
    missing: list[str] = []
    investor_name = _first_non_empty_value(entry, "name", "投资方名称", "投资方", "投资人", "investor_name")
    investment_amount = _first_non_empty_value(
        entry,
        "amount",
        "投资金额（万元）",
        "投资金额",
        "投资额",
        "investment_amount",
        "investmentAmount",
    )
    if not investor_name:
        missing.append(f"investors[{index}].投资方名称")
    if not investment_amount:
        missing.append(f"investors[{index}].投资金额（万元）")
    return tuple(missing)


def _is_summary_investor_entry(entry: Dict[str, Any]) -> bool:
    investor_name = _first_non_empty_value(entry, "name", "投资方名称", "投资方", "投资人", "investor_name")
    return bool(investor_name and is_summary_investor_name(investor_name))


def _compute_missing_investor_fields(export_extras: Dict[str, Any]) -> tuple[str, ...]:
    investors = export_extras.get("investors")
    if not isinstance(investors, list) or not investors:
        return ("investors",)
    missing: list[str] = []
    has_valid_investor = False
    for index, raw_entry in enumerate(investors):
        entry = dict(raw_entry) if isinstance(raw_entry, dict) else {"name": raw_entry}
        if _is_summary_investor_entry(entry):
            continue
        entry_missing = _investor_missing_fields(entry, index)
        if entry_missing:
            missing.extend(entry_missing)
        else:
            has_valid_investor = True
    if has_valid_investor:
        return ()
    return tuple(missing or ("investors",))


def _compute_missing_canonical_fields(canonical_fields: Dict[str, Any]) -> tuple[str, ...]:
    """Compute which required canonical fields are missing or empty."""
    return _compute_missing_canonical_fields_for_family(
        canonical_fields,
        record_family="listing",
    )


def _compute_missing_canonical_fields_for_family(
    canonical_fields: Dict[str, Any],
    *,
    record_family: str,
    business_id: str = "",
    export_extras: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Compute missing required canonical fields for the record family."""
    normalized_family = str(record_family or "").strip().lower()
    required_fields = _export_requirement_profile(
        record_family=normalized_family,
        business_id=business_id,
    ).required_canonical_fields
    missing = []
    for field_name in required_fields:
        value = canonical_fields.get(field_name)
        if is_export_empty_value(value):
            missing.append(field_name)
    if normalized_family != "deal":
        return tuple(missing)
    normalized_business_id = str(business_id or "").strip()
    if normalized_business_id in DEAL_PRICE_REQUIRED_BUSINESS_IDS and is_export_empty_value(
        canonical_fields.get("deal_price")
    ):
        missing.append("deal_price")
    if normalized_business_id in DEAL_PRICE_REQUIRED_BUSINESS_IDS and not is_export_empty_value(
        canonical_fields.get("deal_price")
    ):
        if not _has_deal_price_unit_basis(canonical_fields):
            missing.append("deal_price_unit_basis")
    if normalized_business_id in DEAL_CAPITAL_INCREASE_BUSINESS_IDS:
        missing.extend(_compute_missing_investor_fields(_optional_object_mapping(export_extras, "export_extras")))
    if not _has_export_ready_deal_date(canonical_fields):
        missing.append(DEAL_DATE_AUDIT_MISSING_FIELD)
    return tuple(missing)


def _build_export_payload(
    *,
    canonical_fields: Dict[str, Any],
    business_identity: Dict[str, Any],
    export_extras: Dict[str, Any],
    missing_required_canonical_fields: Iterable[str] = (),
) -> Dict[str, Any]:
    export_payload: Dict[str, Any] = {}
    blocked_fallback_fields = {str(field_name) for field_name in missing_required_canonical_fields}
    for canonical_key, compat_key in CANONICAL_TO_COMPAT.items():
        value = canonical_fields.get(canonical_key)
        if canonical_key == "project_type" and value in (None, ""):
            value = _derived_project_type_label(
                canonical_fields=canonical_fields,
                business_identity=business_identity,
            )
        if is_export_empty_value(value) and canonical_key not in blocked_fallback_fields:
            value = export_extras.get(compat_key)
        if not is_export_empty_value(value):
            export_payload[compat_key] = value
    for field_name in sorted(EXPORT_EXTRA_FIELDS):
        if field_name in export_payload:
            continue
        value = export_extras.get(field_name)
        if not is_export_empty_value(value):
            export_payload[field_name] = value
    return export_payload


def _compute_missing_export_fields(
    export_payload: Dict[str, Any],
    *,
    record_family: str,
    business_id: str,
) -> tuple[tuple[str, str], ...]:
    field_candidates = clone_field_candidates()
    output_kind = _output_kind_for(record_family=record_family, business_id=business_id)
    requirements = _export_requirement_profile(
        record_family=record_family,
        business_id=business_id,
    ).required_export_fields
    missing: list[tuple[str, str]] = []
    for export_field, canonical_field in requirements:
        if (
            output_kind == KIND_PHYSICAL
            and export_field in {"转让方", "类型"}
            and is_export_empty_value(export_payload.get("转让方"))
        ):
            continue
        candidate_keys = field_candidates.get(output_kind, {}).get(export_field, [export_field])
        candidate_values = [export_payload.get(candidate_key) for candidate_key in candidate_keys]
        if all(is_export_empty_value(value) for value in candidate_values):
            missing.append((export_field, canonical_field))
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
    business_identity: Dict[str, Any] = {}
    if isinstance(canonical, CanonicalRecord):
        canonical_fields = dict(canonical.canonical_fields)
        export_extras = _optional_object_mapping(canonical.export_extras, "export_extras")
        business_identity = _business_identity(canonical)
    elif isinstance(canonical, dict):
        nested = canonical.get("canonical_fields")
        if isinstance(nested, dict):
            canonical_fields = dict(nested)
        else:
            canonical_fields = dict(canonical)
        export_extras = _optional_object_mapping(canonical.get("export_extras"), "export_extras")
        business_identity = _business_identity(canonical)
    else:
        raise ExportProjectionError(f"Expected CanonicalRecord or dict, got {type(canonical)}")

    record_family = _record_family_of(canonical)
    business_id = _business_id_of(canonical)
    missing_fields = _compute_missing_canonical_fields_for_family(
        canonical_fields,
        record_family=record_family,
        business_id=business_id,
        export_extras=export_extras,
    )
    export_payload = _build_export_payload(
        canonical_fields=canonical_fields,
        business_identity=business_identity,
        export_extras=export_extras,
        missing_required_canonical_fields=missing_fields,
    )
    missing_export_fields = _compute_missing_export_fields(
        export_payload,
        record_family=record_family,
        business_id=business_id,
    )

    diagnostic_failures: list[tuple[str, str, Dict[str, Any]]] = []
    if missing_fields:
        diagnostic_failures.append(
            (
                "canonical_field_missing",
                f"Missing required canonical fields for export: {', '.join(missing_fields)}",
                {"missing_fields": list(missing_fields)},
            )
        )
    if missing_export_fields:
        export_labels = [f"{label} ({canonical_field})" for label, canonical_field in missing_export_fields]
        diagnostic_failures.append(
            (
                "export_field_missing",
                "Missing required export fields for workbook: " + ", ".join(export_labels),
                {
                    "missing_fields": [
                        {
                            "export_field": label,
                            "canonical_field": canonical_field,
                        }
                        for label, canonical_field in missing_export_fields
                    ]
                },
            )
        )

    if diagnostic_failures and fail_on_missing:
        code, message, context = diagnostic_failures[0]
        structured_missing_fields: list[dict[str, str]] = []
        if code == "canonical_field_missing":
            for field_name in _missing_fields_evidence_list(context):
                text = str(field_name or "").strip()
                if not text:
                    continue
                structured_missing_fields.append(
                    {
                        "kind": "canonical",
                        "field": text,
                        "canonical_field": text,
                        "export_field": "",
                        "message": f"canonical field {text} is required",
                    }
                )
        elif code == "export_field_missing":
            for item in _missing_fields_evidence_list(context):
                if not isinstance(item, Mapping):
                    raise TypeError("context.missing_fields[*] must be a mapping")
                export_field = str(item.get("export_field") or "").strip()
                canonical_field = str(item.get("canonical_field") or "").strip()
                structured_missing_fields.append(
                    {
                        "kind": "export",
                        "field": export_field or canonical_field,
                        "canonical_field": canonical_field,
                        "export_field": export_field,
                        "message": f"export field {export_field or canonical_field} is required",
                    }
                )
        failure = PipelineFailure(
            code=code,
            component="export_projection",
            stage="project",
            recoverability="permanent",
            message=message,
            context=context,
        )
        raise ExportProjectionError(
            str(failure),
            failure_code=code,
            missing_fields=tuple(structured_missing_fields),
        ) from None

    for code, message, evidence in diagnostic_failures:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type=code,
                message=message,
                evidence=evidence,
            )
        )

    return export_payload, tuple(findings)


def append_export_projection_findings(
    findings: Iterable[PostProcessFinding],
    canonical: CanonicalRecord | Dict[str, Any],
) -> tuple[PostProcessFinding, ...]:
    """Return findings plus canonical export projection diagnostics without duplicates."""
    findings_list = list(findings)
    _, projection_findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)
    seen = {_finding_identity(item) for item in findings_list}
    for item in projection_findings:
        identity = _finding_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        findings_list.append(item)
    return tuple(findings_list)


def _finding_identity(finding: PostProcessFinding) -> tuple[str, str]:
    evidence = finding.evidence
    if not isinstance(evidence, Mapping):
        raise TypeError(f"finding.evidence must be a mapping, got {type(evidence).__name__}")
    evidence = dict(evidence)
    return (
        str(finding.type),
        json.dumps(_missing_fields_evidence_list(evidence), ensure_ascii=False, sort_keys=True),
    )


__all__ = [
    "DEFAULT_LISTING_REQUIRED_CANONICAL_FIELDS",
    "CANONICAL_TO_COMPAT",
    "EXPORT_EXTRA_FIELDS",
    "EXPORT_EMPTY_PLACEHOLDERS",
    "ExportProjectionError",
    "append_export_projection_findings",
    "is_export_empty_value",
    "project_canonical_record_to_export_payload",
]
