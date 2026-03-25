"""Record-level postprocess helpers for the streaming pipeline."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from .streaming_models import PostProcessFinding

COMPANY_FIELDS = (
    "转让方",
    "融资方",
    "转让方名称",
    "融资方名称",
    "company_name_primary",
)
GROUP_FIELDS = ("隶属集团", "集团名称", "group_name")
TYPE_FIELDS = ("类型", "source_type")

MATCH_TRANSFEROR = "transferor"
MATCH_GROUP = "group"
TARGET_GROUP = "group_name"
TARGET_TYPE = "source_type"
BUSINESS_PROJECT_TYPES = {"股权转让", "实物资产", "增资扩股", "预披露"}


def _first_non_empty(payload: Dict[str, Any], fields: Iterable[str]) -> str:
    for field_name in fields:
        value = str(payload.get(field_name) or "").strip()
        if value:
            return value
    return ""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def derive_listing_times_from_project_code(project_code: str) -> str:
    code = _clean_text(project_code)
    if not code:
        return ""
    match = re.search(r"-(\d+)$", code)
    if not match:
        return "首次挂牌"
    times = int(match.group(1))
    if times <= 0:
        return ""
    if times == 1:
        return "首次挂牌"
    digits = {
        1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
        6: "六", 7: "七", 8: "八", 9: "九", 0: "零",
    }
    if times < 10:
        return f"{digits[times]}次挂牌"
    if times < 100:
        tens = times // 10
        ones = times % 10
        if tens == 1:
            return "十次挂牌" if ones == 0 else f"十{digits[ones]}次挂牌"
        return f"{digits[tens]}十次挂牌" if ones == 0 else f"{digits[tens]}十{digits[ones]}次挂牌"
    return f"{times}次挂牌"


def _required_mapping_fields(payload: Dict[str, Any]) -> List[str]:
    project_type = _clean_text(payload.get("项目类型"))
    missing: List[str] = []
    if not _first_non_empty(payload, TYPE_FIELDS):
        missing.append("类型")
    return missing


def finalize_streaming_payload(
    payload: Dict[str, Any],
    *,
    findings: Iterable[PostProcessFinding] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved = dict(payload or {})
    normalized_findings = [
        item
        for item in (findings or [])
        if str(item.type or "") not in {"mapping_missing", "project_type_unknown"}
    ]
    if not _clean_text(resolved.get("挂牌次数")):
        derived_listing_times = derive_listing_times_from_project_code(_clean_text(resolved.get("项目编号")))
        if derived_listing_times:
            resolved["挂牌次数"] = derived_listing_times
    project_type = _clean_text(resolved.get("项目类型"))
    if project_type not in BUSINESS_PROJECT_TYPES:
        message = "缺少项目类型，暂不能进入导出" if not project_type else "项目类型未识别，暂不能进入导出"
        normalized_findings.append(
            PostProcessFinding(
                severity="warn",
                type="project_type_unknown",
                message=message,
                evidence={"project_type": project_type},
            )
        )
    missing_fields = _required_mapping_fields(resolved)
    if missing_fields:
        normalized_findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_missing",
                message=f"缺少{'、'.join(missing_fields)}，暂不能进入导出",
                evidence={"missing_fields": missing_fields},
            )
        )
    return resolved, normalized_findings


def merge_record_payloads(
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
) -> Dict[str, Any]:
    merged = dict(parser_payload or {})
    for key, value in dict(postprocess_payload or {}).items():
        if value is None or value == "":
            continue
        merged[str(key)] = value
    return merged


def normalize_record_payload(
    *,
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
    findings: Iterable[PostProcessFinding] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    merged = merge_record_payloads(parser_payload, postprocess_payload)
    return finalize_streaming_payload(merged, findings=findings)


def _normalize_company(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _entry_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _entry_match_field(item: Dict[str, Any]) -> str:
    value = str(_entry_metadata(item).get("match_field") or MATCH_TRANSFEROR).strip().lower()
    return value if value in {MATCH_TRANSFEROR, MATCH_GROUP} else MATCH_TRANSFEROR


def _entry_targets(item: Dict[str, Any]) -> set[str]:
    metadata = _entry_metadata(item)
    target_field = str(metadata.get("target_field") or "").strip().lower()
    if target_field in {TARGET_GROUP, TARGET_TYPE}:
        return {target_field}
    targets: set[str] = set()
    if str(item.get("group_name") or "").strip():
        targets.add(TARGET_GROUP)
    if str(item.get("source_type") or "").strip():
        targets.add(TARGET_TYPE)
    return targets


def _matching_entries(
    entries: Iterable[Dict[str, Any]],
    *,
    match_field: str,
    source_name: str,
) -> List[Dict[str, Any]]:
    target_key = _normalize_company(source_name)
    if not target_key:
        return []
    bucket: List[Dict[str, Any]] = []
    for item in entries or []:
        if _entry_match_field(item) != match_field:
            continue
        if _normalize_company(str(item.get("company_name") or "")) == target_key:
            bucket.append(dict(item))
    return bucket


def _collect_target_values(entries: Iterable[Dict[str, Any]], *, target_field: str) -> List[str]:
    value_key = "group_name" if target_field == TARGET_GROUP else "source_type"
    values = sorted(
        {
            str(item.get(value_key) or "").strip()
            for item in entries or []
            if target_field in _entry_targets(item) and str(item.get(value_key) or "").strip()
        }
    )
    return values


def _resolve_single_mapping(
    *,
    entries: Iterable[Dict[str, Any]],
    target_field: str,
    subject_name: str,
    ambiguous_type: str,
    ambiguous_message: str,
) -> tuple[str, List[PostProcessFinding]]:
    findings: List[PostProcessFinding] = []
    values = _collect_target_values(entries, target_field=target_field)
    if len(values) > 1:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type=ambiguous_type,
                message=ambiguous_message.format(subject_name=subject_name),
                evidence={"subject_name": subject_name, "options": values},
            )
        )
        return "", findings
    return (values[0] if values else ""), findings


def _resolve_group_chain(
    entries: Iterable[Dict[str, Any]],
    *,
    group_name: str,
) -> tuple[str, List[PostProcessFinding]]:
    findings: List[PostProcessFinding] = []
    current = str(group_name or "").strip()
    visited: set[str] = set()
    while current:
        normalized = _normalize_company(current)
        if normalized in visited:
            findings.append(
                PostProcessFinding(
                    severity="warn",
                    type="mapping_conflict",
                    message=f"group mapping cycle group={group_name}",
                    evidence={"group_name": group_name},
                )
            )
            break
        visited.add(normalized)
        matched = _matching_entries(entries, match_field=MATCH_GROUP, source_name=current)
        next_group, extra_findings = _resolve_single_mapping(
            entries=matched,
            target_field=TARGET_GROUP,
            subject_name=current,
            ambiguous_type="mapping_ambiguous",
            ambiguous_message="ambiguous group mapping for group={subject_name}",
        )
        findings.extend(extra_findings)
        if not next_group or next_group == current:
            break
        current = next_group
    return current, findings


def apply_mapping_entries(
    payload: Dict[str, Any],
    *,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved = dict(payload or {})
    findings: List[PostProcessFinding] = []
    company_name = _first_non_empty(resolved, COMPANY_FIELDS)
    if not company_name:
        return resolved, findings

    current_group = _first_non_empty(resolved, GROUP_FIELDS)
    current_type = _first_non_empty(resolved, TYPE_FIELDS)
    entries = [dict(item) for item in (mapping_entries or [])]
    transferor_entries = _matching_entries(entries, match_field=MATCH_TRANSFEROR, source_name=company_name)

    mapped_group, extra_findings = _resolve_single_mapping(
        entries=transferor_entries,
        target_field=TARGET_GROUP,
        subject_name=company_name,
        ambiguous_type="mapping_ambiguous",
        ambiguous_message="ambiguous transferor-group mapping for company={subject_name}",
    )
    findings.extend(extra_findings)
    resolved_group = mapped_group or current_group

    if resolved_group:
        normalized_group, extra_findings = _resolve_group_chain(entries, group_name=resolved_group)
        findings.extend(extra_findings)
        resolved_group = normalized_group or resolved_group

    mapped_type, extra_findings = _resolve_single_mapping(
        entries=transferor_entries,
        target_field=TARGET_TYPE,
        subject_name=company_name,
        ambiguous_type="mapping_ambiguous",
        ambiguous_message="ambiguous transferor-type mapping for company={subject_name}",
    )
    findings.extend(extra_findings)
    resolved_type = mapped_type or current_type
    if not mapped_type and resolved_group:
        group_entries = _matching_entries(entries, match_field=MATCH_GROUP, source_name=resolved_group)
        mapped_type, extra_findings = _resolve_single_mapping(
            entries=group_entries,
            target_field=TARGET_TYPE,
            subject_name=resolved_group,
            ambiguous_type="mapping_ambiguous",
            ambiguous_message="ambiguous group-type mapping for group={subject_name}",
        )
        findings.extend(extra_findings)
        resolved_type = mapped_type or resolved_type

    if not resolved_group and not resolved_type and not current_group and not current_type:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_missing",
                message=f"no mapping entry for company={company_name}",
                evidence={"company_name": company_name},
            )
        )
        return resolved, findings

    changed = False
    if resolved_group and resolved_group != current_group:
        resolved["隶属集团"] = resolved_group
        changed = True
    if resolved_type and resolved_type != current_type:
        resolved["类型"] = resolved_type
        changed = True
    if changed:
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_applied",
                message=f"mapping applied for company={company_name}",
                evidence={"company_name": company_name},
            )
        )
    return resolved, findings


def _build_canonical_record(payload: Dict[str, Any], *, source_file: str):
    from peap_postprocess.postprocess_engine.contracts import CanonicalRecord

    company_name = _first_non_empty(payload, COMPANY_FIELDS)
    group_name = _first_non_empty(payload, GROUP_FIELDS)
    return CanonicalRecord(
        source_file=source_file,
        file_name=source_file.split("/")[-1].split("\\")[-1],
        sheet_name="streaming",
        row_index=2,
        project_code=str(payload.get("项目编号") or "").strip(),
        company_name_primary=company_name,
        group_name=group_name,
        raw_fields={str(key): value for key, value in payload.items()},
    )


def _apply_optional_rule_registry(
    payload: Dict[str, Any],
    *,
    source_file: str,
    rules_config: Dict[str, Any] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    if not rules_config:
        return dict(payload), []
    try:
        from peap_postprocess.postprocess_engine.rules import RuleRegistry
    except Exception as exc:  # pragma: no cover - depends on optional runtime deps
        return dict(payload), [
            PostProcessFinding(
                severity="warn",
                type="rule_registry_unavailable",
                message=str(exc),
                evidence={},
            )
        ]

    record = _build_canonical_record(payload, source_file=source_file)
    registry = RuleRegistry()
    bindings, warnings = registry.build_plan(rules_config)
    findings = [
        PostProcessFinding(severity="warn", type="rule_plan_warning", message=text, evidence={})
        for text in warnings
    ]
    resolved = dict(payload)
    for binding in bindings:
        try:
            result = binding.rule.apply(record, {"mode": "streaming"})
        except Exception as exc:  # pragma: no cover - defensive path
            findings.append(
                PostProcessFinding(
                    severity="error",
                    type="rule_error",
                    message=str(exc),
                    evidence={"rule_id": binding.rule.rule_id()},
                )
            )
            continue
        for patch in result.patches:
            if patch.action == "filter_out_row":
                findings.append(
                    PostProcessFinding(
                        severity="warn",
                        type="rule_filtered",
                        message=f"rule filtered record: {binding.rule.rule_id()}",
                        evidence={"rule_id": binding.rule.rule_id()},
                    )
                )
                continue
            resolved[patch.field] = patch.new_value
        for finding in result.findings:
            findings.append(
                PostProcessFinding(
                    severity=finding.severity,
                    type=finding.type,
                    message=finding.message,
                    evidence=dict(finding.evidence),
                )
            )
        record = _build_canonical_record(resolved, source_file=source_file)
    return resolved, findings


def run_record_postprocess(
    payload: Dict[str, Any],
    *,
    source_file: str,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
    rules_config: Dict[str, Any] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    working = copy.deepcopy(dict(payload or {}))
    mapped_payload, findings = apply_mapping_entries(
        working,
        mapping_entries=mapping_entries,
    )
    rule_payload, rule_findings = _apply_optional_rule_registry(
        mapped_payload,
        source_file=source_file,
        rules_config=rules_config,
    )
    findings.extend(rule_findings)
    return finalize_streaming_payload(rule_payload, findings=findings)


def findings_to_json(findings: Iterable[PostProcessFinding]) -> List[Dict[str, Any]]:
    return [asdict(item) for item in findings]
