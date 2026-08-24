"""Record display projection — transforms raw DB records into
presentation-ready payloads with column ordering and display aliases.

This module is consumed by both the record listing and export endpoints.
It has no side-effects and does not touch the database.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Dict

from peap.artifact_truth import (
    resolve_artifact_evidence_verdict,
    resolve_declared_artifact_presence,
)
from peap.export_projection import CANONICAL_TO_COMPAT
from peap.mapping_subjects import subject_matches_source
from peap.output_contract import (
    clone_field_candidates,
    get_output_columns_for_kind,
)
from peap.projection_registry import resolve_projection_profile
from peap_core.business_catalog import get_business_descriptor

from .constants import (
    DISPLAY_ALIAS_FIELDS,
    DISPLAY_COMPATIBLE_KEYS,
)
from .normalizers import (
    normalize_exchange_label,
    normalize_local_path,
    normalize_match_text,
    status_label,
)

# ── Internal helpers ──


def _first_value(payload: Dict[str, Any], fields: list[str]) -> str:
    for field in fields:
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    return ""


def _merge_display_payload(target: Dict[str, Any], source: Dict[str, Any] | None) -> None:
    if not isinstance(source, dict):
        return
    for raw_key, raw_value in source.items():
        key = str(raw_key or "").strip()
        if not key or raw_value in (None, ""):
            continue
        target[key] = raw_value


def _optional_display_mapping(record: Dict[str, Any], field_name: str) -> Dict[str, Any]:
    value = record.get(field_name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return dict(value)


def _optional_record_mapping(record: Dict[str, Any], field_name: str) -> Dict[str, Any]:
    if field_name not in record or record.get(field_name) is None:
        return {}
    value = record.get(field_name)
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return dict(value)


def _optional_nested_mapping(payload: Mapping[str, Any], field_name: str) -> Dict[str, Any]:
    if field_name not in payload or payload.get(field_name) is None:
        return {}
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise TypeError(f"canonical_record.{field_name} must be a mapping")
    return dict(value)


def _record_findings(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_findings = record.get("findings")
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        raise TypeError("findings must be a list")
    findings: list[Dict[str, Any]] = []
    for item in raw_findings:
        if not isinstance(item, Mapping):
            raise TypeError("findings[*] must be a mapping")
        findings.append(dict(item))
    return findings


def _finding_evidence(item: Mapping[str, Any]) -> Dict[str, Any]:
    if "evidence" not in item or item.get("evidence") is None:
        return {}
    evidence = item.get("evidence")
    if not isinstance(evidence, Mapping):
        raise TypeError("findings[*].evidence must be a mapping")
    return dict(evidence)


def _apply_display_aliases(payload: Dict[str, Any]) -> None:
    for source_key, target_key in {**CANONICAL_TO_COMPAT, **DISPLAY_ALIAS_FIELDS}.items():
        if payload.get(target_key) in (None, "") and payload.get(source_key) not in (None, ""):
            payload[target_key] = payload[source_key]


def _source_identity_dict(record: Dict[str, Any]) -> Dict[str, Any]:
    if "source_identity_json" in record and record.get("source_identity_json") is not None:
        source_identity = record.get("source_identity_json")
        if not isinstance(source_identity, Mapping):
            raise TypeError("source_identity_json must be a mapping")
        return dict(source_identity)
    if "source_identity" in record and record.get("source_identity") is not None:
        source_identity = record.get("source_identity")
        if not isinstance(source_identity, Mapping):
            raise TypeError("source_identity must be a mapping")
        return dict(source_identity)
    return {}


def _record_projection_kind(record: Dict[str, Any]) -> str:
    record_family = str(record.get("record_family") or "").strip()
    business_id = str(record.get("business_id") or "").strip()
    canonical_record = record.get("canonical_record")
    if isinstance(canonical_record, dict):
        business_identity = canonical_record.get("business_identity")
        if not isinstance(business_identity, dict):
            business_identity = {}
        source_identity = canonical_record.get("source_identity")
        if not isinstance(source_identity, dict):
            source_identity = {}
        if not record_family:
            record_family = str(
                canonical_record.get("record_family")
                or business_identity.get("record_family")
                or source_identity.get("record_family")
                or ""
            ).strip()
        if not business_id:
            business_id = str(
                business_identity.get("business_id")
                or source_identity.get("business_id")
                or ""
            ).strip()
    if record_family and business_id:
        profile = resolve_projection_profile(record_family, business_id)
        if profile is not None:
            return profile.output_kind
    return ""


def _record_family(record: Dict[str, Any]) -> str:
    direct = str(record.get("record_family") or "").strip().lower()
    if direct:
        return direct
    canonical_record = record.get("canonical_record")
    if isinstance(canonical_record, dict):
        nested = str(canonical_record.get("record_family") or "").strip().lower()
        if nested:
            return nested
        business_identity = canonical_record.get("business_identity")
        if isinstance(business_identity, dict):
            nested = str(business_identity.get("record_family") or "").strip().lower()
            if nested:
                return nested
        source_identity = canonical_record.get("source_identity")
        if isinstance(source_identity, dict):
            nested = str(source_identity.get("record_family") or "").strip().lower()
            if nested:
                return nested
    return "listing"


def _record_business_id(record: Dict[str, Any]) -> str:
    direct = str(record.get("business_id") or "").strip()
    if direct:
        return direct
    canonical_record = record.get("canonical_record")
    if isinstance(canonical_record, dict):
        business_identity = canonical_record.get("business_identity")
        if isinstance(business_identity, dict):
            nested = str(business_identity.get("business_id") or "").strip()
            if nested:
                return nested
        source_identity = canonical_record.get("source_identity")
        if isinstance(source_identity, dict):
            nested = str(source_identity.get("business_id") or "").strip()
            if nested:
                return nested
    return ""


LISTING_MIXED_RECORD_DISPLAY_COLUMNS = [
    "项目编号",
    "项目名称",
    "项目类型",
    "交易所",
    "主体",
    "隶属集团",
    "开始日期",
    "截止日期",
    "金额",
    "类型",
]

LISTING_MIXED_RECORD_DISPLAY_FIELD_CANDIDATES = {
    "项目编号": ["项目编号"],
    "项目名称": ["项目名称"],
    "项目类型": ["项目类型"],
    "交易所": ["交易所"],
    "主体": ["融资方", "转让方"],
    "隶属集团": ["隶属集团"],
    "开始日期": ["披露开始日期", "预披露开始日期", "挂牌开始日期", "信息披露起始日期"],
    "截止日期": ["披露截止日期", "预披露截止日期", "挂牌截止日期", "信息披露截止日期"],
    "金额": ["融资金额", "融资金额（万）", "挂牌价格", "挂牌价格（万）", "挂牌价格（万元）"],
    "类型": ["类型"],
}

DEAL_MIXED_RECORD_DISPLAY_COLUMNS = [
    "项目编号",
    "项目名称",
    "业务",
    "交易所",
    "成交日期",
    "金额",
    "状态",
]

DEAL_MIXED_RECORD_DISPLAY_FIELD_CANDIDATES = {
    "项目编号": ["项目编号"],
    "项目名称": ["项目名称", "标的名称"],
    "业务": ["业务", "项目类型", "类型"],
    "交易所": ["交易所"],
    "成交日期": ["成交日期", "deal_date"],
    "金额": ["交易价格（万元）", "交易价格", "成交金额", "投资总金额（万元）", "投资金额（万元）", "融资金额"],
    "状态": ["项目状态", "状态"],
}

# Backward-compatibility for legacy imports/tests that still reference the old name.
MIXED_RECORD_DISPLAY_COLUMNS = LISTING_MIXED_RECORD_DISPLAY_COLUMNS


def mixed_record_display_columns(record_family: str) -> list[str]:
    return (
        list(DEAL_MIXED_RECORD_DISPLAY_COLUMNS)
        if str(record_family or "").strip().lower() == "deal"
        else list(LISTING_MIXED_RECORD_DISPLAY_COLUMNS)
    )


def _business_catalog_label(business_id: str, *, family_id: str) -> str:
    try:
        descriptor = get_business_descriptor(business_id, family_id=family_id)
    except KeyError:
        return ""
    return str(getattr(descriptor, "canonical_label", "") or "").strip()


# ── Public projections ──


def build_record_display_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a canonical display-ready payload for public record resources."""
    from peap.streaming_export import record_to_export_payload

    payload: Dict[str, Any] = {}
    _merge_display_payload(payload, record_to_export_payload(record))
    _apply_display_aliases(payload)

    if not payload.get("ID"):
        revision_id = record.get("revision_id") or record.get("latest_revision_id")
        if revision_id not in (None, ""):
            payload["ID"] = str(revision_id)

    if not payload.get("项目编号"):
        payload["项目编号"] = str(record.get("project_code") or "").strip()
    if not payload.get("项目名称"):
        payload["项目名称"] = str(record.get("project_name") or "").strip()
    if not payload.get("项目类型"):
        payload["项目类型"] = str(record.get("project_type") or "").strip()

    return payload


def build_record_mapping_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build the mapping-analysis payload from raw and canonical record layers."""
    from peap.streaming_export import record_to_export_payload

    payload: Dict[str, Any] = {}
    _merge_display_payload(payload, _optional_display_mapping(record, "parser_payload"))
    _merge_display_payload(payload, _optional_display_mapping(record, "postprocess_payload"))
    _merge_display_payload(payload, record_to_export_payload(record))
    _apply_display_aliases(payload)

    if not payload.get("项目编号"):
        payload["项目编号"] = str(record.get("project_code") or "").strip()
    if not payload.get("项目名称"):
        payload["项目名称"] = str(record.get("project_name") or "").strip()
    if not payload.get("项目类型"):
        payload["项目类型"] = str(record.get("project_type") or "").strip()

    return payload


def build_record_display_values(record: Dict[str, Any], *, project_kind: str | None) -> Dict[str, Any]:
    """Build column-aligned display values for a record row."""
    payload = build_record_display_payload(record)
    payload["交易所"] = normalize_exchange_label(str(payload.get("交易所") or record.get("exchange") or ""))

    resolved_kind = project_kind
    if not resolved_kind:
        resolved_kind = _record_projection_kind(record)
    if not resolved_kind:
        return {
            key: value
            for key, value in payload.items()
            if str(key or "").strip() in DISPLAY_COMPATIBLE_KEYS and value not in (None, "")
        }

    field_candidates = clone_field_candidates().get(resolved_kind, {})
    columns = list(get_output_columns_for_kind(resolved_kind))
    values: Dict[str, Any] = {}
    consumed_keys: set[str] = set()
    for column in columns:
        if column == "ID":
            resolved_id = _first_value(payload, ["ID"]) or str(record.get("revision_id") or record.get("latest_revision_id") or "")
            if resolved_id:
                values[column] = resolved_id
            consumed_keys.add("ID")
            continue
        candidates = list(field_candidates.get(column) or [column])
        consumed_keys.add(column)
        consumed_keys.update(str(candidate or "").strip() for candidate in candidates if str(candidate or "").strip())
        resolved_value = _first_value(payload, candidates)
        if resolved_value not in (None, ""):
            values[column] = resolved_value
    return values


def build_mixed_record_display_values(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact generic projection for mixed-business record browsing."""
    payload = build_record_display_payload(record)
    payload["交易所"] = normalize_exchange_label(str(payload.get("交易所") or record.get("exchange") or ""))
    record_family = _record_family(record)
    business_id = _record_business_id(record)
    if record_family == "deal":
        columns = DEAL_MIXED_RECORD_DISPLAY_COLUMNS
        field_candidates = DEAL_MIXED_RECORD_DISPLAY_FIELD_CANDIDATES
    else:
        columns = LISTING_MIXED_RECORD_DISPLAY_COLUMNS
        field_candidates = LISTING_MIXED_RECORD_DISPLAY_FIELD_CANDIDATES
    values: Dict[str, Any] = {}
    for column in columns:
        if column == "业务" and record_family == "deal":
            resolved_value = _business_catalog_label(business_id, family_id=record_family)
            if not resolved_value:
                resolved_value = _first_value(payload, field_candidates.get(column, [column]))
        elif column == "成交日期" and record_family == "deal":
            resolved_value = _first_value(payload, field_candidates.get(column, [column]))
            if not resolved_value:
                resolved_value = str(record.get("listing_date") or "").strip()
        else:
            resolved_value = _first_value(payload, field_candidates.get(column, [column]))
        if resolved_value not in (None, ""):
            values[column] = resolved_value
    return values


def build_record_top_level_fields(record: Dict[str, Any]) -> Dict[str, str]:
    canonical_record = _optional_record_mapping(record, "canonical_record")
    canonical_fields = _optional_nested_mapping(canonical_record, "canonical_fields")

    def _canonical_text(field_name: str) -> str:
        value = canonical_fields.get(field_name)
        if value in (None, ""):
            return ""
        return str(value).strip()

    return {
        "seller": _canonical_text("seller"),
        "price": _canonical_text("price"),
    }


def record_canonical_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    canonical_record = _optional_record_mapping(record, "canonical_record")
    return _optional_nested_mapping(canonical_record, "canonical_fields")


def record_has_invalid_source_artifact(record: Dict[str, Any]) -> bool:
    if str(record.get("last_error_type") or "").strip() == "source_artifact_invalid":
        return True
    for item in _record_findings(record):
        if str(item.get("type") or "").strip() == "source_artifact_invalid":
            return True
    return False


def _is_under_managed_root(path: str, managed_roots: tuple[str, ...]) -> bool:
    if not path or not managed_roots:
        return False
    real_path = os.path.realpath(path)
    for root in managed_roots:
        normalized_root = normalize_local_path(root)
        if not normalized_root:
            continue
        real_root = os.path.realpath(normalized_root)
        try:
            if os.path.commonpath([real_path, real_root]) == real_root:
                return True
        except ValueError:
            continue
    return False


def _managed_provenance_path(record: Dict[str, Any], *, managed_roots: tuple[str, ...]) -> str:
    if not managed_roots:
        return ""
    source_identity = _source_identity_dict(record)
    declared_paths = {
        normalize_local_path(record.get("source_file")),
        normalize_local_path(record.get("archive_path")),
    }
    for candidate in (
        source_identity.get("original_source_file"),
        source_identity.get("original_evidence_path"),
    ):
        path = normalize_local_path(candidate)
        if path in declared_paths:
            continue
        if path and os.path.isfile(path) and _is_under_managed_root(path, managed_roots):
            return path
    return ""


def _json_safe_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_mapping(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe_mapping(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_mapping(item) for item in value]
    return value


def build_record_evidence_verdict(
    record: Dict[str, Any],
    *,
    managed_roots: tuple[str, ...] = (),
    managed_provenance_path: str = "",
) -> Dict[str, Any]:
    """Return the structured artifact evidence verdict for records/review rows."""
    provenance_path = normalize_local_path(managed_provenance_path) or _managed_provenance_path(
        record,
        managed_roots=managed_roots,
    )
    verdict = resolve_artifact_evidence_verdict(
        record,
        managed_provenance_path=provenance_path,
    )
    return {
        "status": verdict.status,
        "logical_record_identity": verdict.logical_record_identity,
        "identity_confidence": verdict.identity_confidence,
        "authoritative_path": verdict.authoritative_path,
        "inspection_openable_path": verdict.inspection_openable_path,
        "reason_code": verdict.reason_code,
        "safe_evidence": _json_safe_mapping(dict(verdict.safe_evidence)),
    }


def record_artifact_legacy_fields_from_verdict(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """Map structured evidence truth into legacy records/review artifact fields."""
    status = str(verdict.get("status") or "").strip()
    reason_code = str(verdict.get("reason_code") or "").strip()
    openable_path = normalize_local_path(verdict.get("inspection_openable_path"))
    return {
        "artifact_status": "available" if openable_path else "unresolved",
        "artifact_missing_reason": "" if status in {"verified", "present_unverified"} else reason_code,
        "has_local_artifact": bool(openable_path),
        "local_artifact_name": os.path.basename(openable_path) if openable_path else "",
    }


def resolve_record_artifact_path(record: Dict[str, Any], *, managed_roots: tuple[str, ...] = ()) -> str:
    """Return a browsable managed artifact for records UI / reveal flows.

    Only app-managed `source_file` / `archive_path` locations qualify here.
    Provenance-only paths such as `original_source_file` may point to ad-hoc
    parser fixtures outside the managed workspace and must not make a record
    appear browsable in the operator UI.
    """
    if record_has_invalid_source_artifact(record):
        return ""
    declared_presence = resolve_declared_artifact_presence(
        source_file=normalize_local_path(record.get("source_file")),
        archive_path=normalize_local_path(record.get("archive_path")),
    )
    if declared_presence.available:
        return declared_presence.authoritative_path
    if not managed_roots:
        return ""

    source_identity = _source_identity_dict(record)
    candidates = [
        source_identity.get("original_source_file"),
        source_identity.get("original_evidence_path"),
    ]
    normalized_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = normalize_local_path(candidate)
        if not path or path in seen:
            continue
        seen.add(path)
        normalized_candidates.append(path)
    for path in normalized_candidates:
        if not os.path.isfile(path):
            continue
        if path in {normalize_local_path(record.get("source_file")), normalize_local_path(record.get("archive_path"))}:
            continue
        if _is_under_managed_root(path, managed_roots):
            return path
    return ""


def record_artifact_missing_reason(record: Dict[str, Any], artifact_path: str) -> str:
    if artifact_path:
        return ""
    if record_has_invalid_source_artifact(record):
        return "source_artifact_invalid"
    if str(record.get("source_file") or "").strip() or str(record.get("archive_path") or "").strip():
        return "artifact_path_unresolved"
    source_identity = _source_identity_dict(record)
    if (
        str(source_identity.get("original_source_file") or "").strip()
        or str(source_identity.get("original_evidence_path") or "").strip()
    ):
        return "artifact_provenance_unresolved"
    return "artifact_path_missing"


# ── Record-state presentation ──


def is_internal_skip_message(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("skip-") or normalized.startswith("skip_")


def is_rule_skipped_record(record: Dict[str, Any]) -> bool:
    if str(record.get("state") or "").strip() != "skipped":
        return False
    if str(record.get("last_error_type") or "").strip() == "skip_parse":
        return True
    raw_message = str(record.get("last_error_message") or "").strip()
    if is_internal_skip_message(raw_message) or "按规则跳过" in raw_message:
        return True
    for item in _record_findings(record):
        finding_type = str(item.get("type") or "").strip().lower()
        message = str(item.get("message") or "").strip()
        if finding_type == "rule_filtered" or finding_type.endswith("_filtered") or "skip" in finding_type or "跳过" in message:
            return True
    return False


def record_status_label(record: Dict[str, Any]) -> str:
    if is_rule_skipped_record(record):
        return "已按后处理规则跳过"
    return status_label(str(record.get("state") or ""))


def mapping_template_issue(message: str, evidence: Dict[str, Any] | None = None) -> tuple[str, str] | None:
    if evidence is None:
        evidence_mapping: Mapping[str, Any] = {}
    elif isinstance(evidence, Mapping):
        evidence_mapping = evidence
    else:
        raise TypeError("evidence must be a mapping")
    reason_code = str(evidence_mapping.get("reason_code") or "").strip()
    normalized_message = str(message or "").strip()
    if (
        reason_code == "project_type_mapping_template_missing"
        or normalized_message.startswith("entity_type_mapping_file not found:")
    ):
        return ("business_resolution_required", "项目类型映射模板缺失，当前记录无法完成类型归属")
    if normalized_message.startswith("transferor_group_mapping_file not found:"):
        return ("transferor_group_mapping_template_missing", "转让方集团映射模板缺失，当前记录无法完成集团归属")
    if normalized_message.startswith("group_group_mapping_file not found:"):
        return ("group_group_mapping_template_missing", "集团层级映射模板缺失，当前记录无法完成集团归属")
    if normalized_message.startswith("transferor_type_mapping_file not found:"):
        return ("transferor_type_mapping_template_missing", "转让方类型映射模板缺失，当前记录无法完成类型归属")
    return None


SAFE_STATUS_DETAIL_BY_FINDING_TYPE = {
    "business_resolution_required": "业务归属待补全，暂不能进入导出",
    "canonical_field_missing": "导出必填字段缺失，暂不能进入导出",
    "export_field_missing": "导出必填字段缺失，暂不能进入导出",
    "group_group_mapping_template_missing": "集团层级映射模板缺失，当前记录无法完成集团归属",
    "mapping_conflict": "存在多个映射候选结果，需要人工裁决",
    "mapping_gap": "缺少映射规则，暂不能进入导出",
    "mapping_missing": "缺少映射规则，暂不能进入导出",
    "project_type_mapping_template_missing": "项目类型映射模板缺失，当前记录无法完成类型归属",
    "transferor_group_mapping_template_missing": "转让方集团映射模板缺失，当前记录无法完成集团归属",
    "transferor_type_mapping_template_missing": "转让方类型映射模板缺失，当前记录无法完成类型归属",
}


def _safe_finding_status_detail(item: Dict[str, Any], *, state: str) -> str:
    finding_type = str(item.get("type") or "").strip()
    evidence = _finding_evidence(item)
    raw_message = str(item.get("message") or "").strip()
    template_issue = mapping_template_issue(raw_message, evidence)
    if template_issue is not None:
        finding_type, safe_message = template_issue
        return safe_message
    safe_message = SAFE_STATUS_DETAIL_BY_FINDING_TYPE.get(finding_type)
    if safe_message:
        return safe_message
    severity = str(item.get("severity") or "").strip().lower()
    if severity in {"error", "warn", "warning"}:
        if state == "pending_mapping":
            return "缺少映射规则，暂不能进入导出"
        if state == "field_missing":
            return "导出必填字段缺失，暂不能进入导出"
        if state == "pending_review":
            return "业务归属或导出必填字段待补全，暂不能进入导出"
    return ""


def record_status_detail(record: Dict[str, Any]) -> str:
    state = str(record.get("state") or "").strip()
    findings = _record_findings(record)
    archive_path = str(record.get("archive_path") or "").strip()
    archive_conflict_path = ""
    for finding in findings:
        if str(finding.get("type") or "").strip() == "archive_conflict":
            evidence = _finding_evidence(finding)
            archive_conflict_path = str(evidence.get("archive_path") or archive_path).strip()
            break
    if state == "conflict":
        if archive_conflict_path:
            return f"归档文件同名，已另存为 {os.path.basename(archive_conflict_path)}"
        if archive_path:
            return f"归档文件同名，当前文件为 {os.path.basename(archive_path)}"
        return "归档文件同名"
    if state == "pending_mapping":
        prioritized = []
        for item in findings:
            message = _safe_finding_status_detail(item, state=state)
            if not message:
                continue
            severity = str(item.get("severity") or "").strip().lower()
            finding_type = str(item.get("type") or "").strip()
            rank = 2
            if severity in {"error", "warn", "warning"}:
                rank = 0
            elif finding_type in {"mapping_gap", "mapping_missing", "mapping_conflict"}:
                rank = 1
            prioritized.append((rank, message))
        messages = [message for _, message in sorted(prioritized, key=lambda item: item[0])]
        if messages:
            return messages[0]
        return "缺少映射规则，暂不能进入导出"
    if state == "field_missing":
        prioritized = []
        for item in findings:
            message = _safe_finding_status_detail(item, state=state)
            if not message:
                continue
            severity = str(item.get("severity") or "").strip().lower()
            finding_type = str(item.get("type") or "").strip()
            rank = 3
            if finding_type in {"export_field_missing", "canonical_field_missing"}:
                rank = 0
            elif finding_type == "business_resolution_required":
                rank = 1
            elif severity in {"error", "warn", "warning"}:
                rank = 2
            prioritized.append((rank, message))
        messages = [message for _, message in sorted(prioritized, key=lambda item: item[0])]
        if messages:
            return messages[0]
        return "导出必填字段缺失，暂不能进入导出"
    if state == "pending_review":
        prioritized = []
        for item in findings:
            message = _safe_finding_status_detail(item, state=state)
            if not message:
                continue
            severity = str(item.get("severity") or "").strip().lower()
            finding_type = str(item.get("type") or "").strip()
            rank = 3
            if finding_type == "business_resolution_required":
                rank = 1
            elif severity in {"error", "warn", "warning"}:
                rank = 2
            prioritized.append((rank, message))
        messages = [message for _, message in sorted(prioritized, key=lambda item: item[0])]
        if messages:
            return messages[0]
        return "业务归属或导出必填字段待补全，暂不能进入导出"
    if state == "parse_failed":
        return "解析失败，暂不能进入录入"
    if state == "postprocess_failed":
        return "后处理失败，暂不能进入录入"
    if state == "skipped":
        if is_rule_skipped_record(record):
            return "当前网页已按后处理规则跳过，不进入录入"
        return "当前网页按规则跳过，不进入录入"
    if archive_conflict_path:
        return f"归档文件曾同名，当前文件为 {os.path.basename(archive_conflict_path)}"
    return ""


def record_matches_mapping_source(record: Dict[str, Any], *, match_field: str, source_name: str) -> bool:
    from .constants import MAPPING_MATCH_FIELDS as _MATCH_FIELDS

    normalized_source = normalize_match_text(source_name)
    if not normalized_source:
        return False
    fields = _MATCH_FIELDS.get(str(match_field or "").strip().lower(), _MATCH_FIELDS["transferor"])
    canonical_fields_dict = record_canonical_fields(record)
    payloads = [
        _optional_display_mapping(record, "postprocess_payload"),
        _optional_display_mapping(record, "parser_payload"),
        _optional_display_mapping(record, "canonical_projection"),
        canonical_fields_dict,
        {
            "转让方": canonical_fields_dict.get("seller"),
            "隶属集团": canonical_fields_dict.get("group_name"),
            "类型": canonical_fields_dict.get("source_type"),
        },
    ]
    normalized_match_field = str(match_field or "").strip().lower()
    for payload in payloads:
        for field_name in fields:
            value = payload.get(field_name)
            if normalized_match_field == "transferor":
                if subject_matches_source(value, match_field="transferor", source_name=source_name):
                    return True
                continue
            if normalize_match_text(value) == normalized_source:
                return True
    return False
