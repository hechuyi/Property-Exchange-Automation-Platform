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
    normalized_findings: List[PostProcessFinding] = []
    preserved_project_type_unknown: PostProcessFinding | None = None
    for item in findings or []:
        finding_type = str(item.type or "")
        if finding_type == "mapping_missing":
            continue
        if finding_type == "project_type_unknown":
            message = str(item.message or "").strip()
            if message.startswith("entity_type_mapping_file not found:"):
                evidence = dict(item.evidence or {})
                evidence.setdefault("reason_code", "project_type_mapping_template_missing")
                evidence.setdefault("template_path", message.split(":", 1)[1].strip())
                preserved_project_type_unknown = PostProcessFinding(
                    severity=item.severity,
                    type="project_type_unknown",
                    message="项目类型映射模板缺失，当前记录无法完成类型归属",
                    evidence=evidence,
                )
            continue
        normalized_findings.append(item)
    if not _clean_text(resolved.get("挂牌次数")):
        derived_listing_times = derive_listing_times_from_project_code(_clean_text(resolved.get("项目编号")))
        if derived_listing_times:
            resolved["挂牌次数"] = derived_listing_times
    project_type = _clean_text(resolved.get("项目类型"))
    if preserved_project_type_unknown is not None:
        normalized_findings.append(preserved_project_type_unknown)
    elif project_type not in BUSINESS_PROJECT_TYPES:
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


def _entry_is_authoritative(item: Dict[str, Any]) -> bool:
    metadata = _entry_metadata(item)
    return bool(metadata.get("authoritative"))


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


def _unique_non_empty(values: Iterable[Any]) -> List[str]:
    return sorted({str(item or "").strip() for item in values if str(item or "").strip()})


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


def _trace_group_chain(
    entries: Iterable[Dict[str, Any]],
    *,
    group_name: str,
) -> tuple[str, List[Dict[str, str]], List[PostProcessFinding]]:
    findings: List[PostProcessFinding] = []
    chain: List[Dict[str, str]] = []
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
            ambiguous_type="mapping_conflict",
            ambiguous_message="ambiguous group mapping for group={subject_name}",
        )
        findings.extend(extra_findings)
        if not next_group or next_group == current:
            break
        chain.append(
            {
                "match_field": MATCH_GROUP,
                "target_field": TARGET_GROUP,
                "source_name": current,
                "target_value": next_group,
                "label": f"集团 {current} -> 集团 {next_group}",
            }
        )
        current = next_group
    return current, chain, findings


def analyze_mapping_candidates(
    payload: Dict[str, Any],
    *,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    resolved = dict(payload or {})
    entries = [dict(item) for item in (mapping_entries or [])]
    findings: List[PostProcessFinding] = []
    company_name = _first_non_empty(resolved, COMPANY_FIELDS)
    current_group = _first_non_empty(resolved, GROUP_FIELDS)
    current_type = _first_non_empty(resolved, TYPE_FIELDS)
    analysis = {
        "company_name": company_name,
        "current_group": current_group,
        "current_type": current_type,
        "resolved_group": current_group,
        "resolved_type": current_type,
        "gap_codes": [],
        "recommended_rule": {},
        "available_rule_kinds": [],
        "candidate_resolutions": [],
        "has_conflict": False,
        "findings": findings,
    }
    if not company_name:
        return analysis

    transferor_entries = _matching_entries(entries, match_field=MATCH_TRANSFEROR, source_name=company_name)
    transferor_group_values = _collect_target_values(transferor_entries, target_field=TARGET_GROUP)
    transferor_type_values = _collect_target_values(transferor_entries, target_field=TARGET_TYPE)

    group_candidates: List[Dict[str, Any]] = []
    transferor_group_authoritative = any(
        _entry_is_authoritative(item) and TARGET_GROUP in _entry_targets(item)
        for item in transferor_entries
    )
    for value in transferor_group_values:
        group_candidates.append(
            {
                "field": TARGET_GROUP,
                "rule_kind": "transferor_group",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
                "source_name": company_name,
                "target_value": value,
                "label": f"转让方 {company_name} -> 集团 {value}",
                "evidence_chain": [
                    {
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_GROUP,
                        "source_name": company_name,
                        "target_value": value,
                        "label": f"转让方 {company_name} -> 集团 {value}",
                    }
                ],
            }
        )
    if current_group and not transferor_group_authoritative:
        group_candidates.append(
            {
                "field": TARGET_GROUP,
                "rule_kind": "transferor_group",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
                "source_name": company_name,
                "target_value": current_group,
                "label": f"保留当前集团 {current_group}",
                "evidence_chain": [
                    {
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_GROUP,
                        "source_name": company_name,
                        "target_value": current_group,
                        "label": f"保留当前集团 {current_group}",
                    }
                ],
            }
        )

    unique_group_values = _unique_non_empty(item["target_value"] for item in group_candidates)
    if len(unique_group_values) > 1:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_conflict",
                message=f"conflicting group candidates for company={company_name}",
                evidence={"company_name": company_name, "options": unique_group_values, "field": TARGET_GROUP},
            )
        )
        analysis["has_conflict"] = True
        analysis["candidate_resolutions"].extend(group_candidates)
    elif unique_group_values:
        analysis["resolved_group"] = unique_group_values[0]

    normalized_group = str(analysis["resolved_group"] or "").strip()
    group_chain: List[Dict[str, str]] = []
    if normalized_group:
        normalized_group, chain, extra_findings = _trace_group_chain(entries, group_name=normalized_group)
        group_chain = chain
        findings.extend(extra_findings)
        analysis["resolved_group"] = normalized_group or analysis["resolved_group"]
        if extra_findings:
            analysis["has_conflict"] = True
            analysis["candidate_resolutions"].extend(
                {
                    "field": TARGET_GROUP,
                    "rule_kind": "group_group",
                    "match_field": MATCH_GROUP,
                    "target_field": TARGET_GROUP,
                    "source_name": item["source_name"],
                    "target_value": item["target_value"],
                    "label": item["label"],
                    "evidence_chain": [item],
                }
                for item in chain
            )

    type_candidates: List[Dict[str, Any]] = []
    for value in transferor_type_values:
        type_candidates.append(
            {
                "field": TARGET_TYPE,
                "rule_kind": "transferor_type",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_TYPE,
                "source_name": company_name,
                "target_value": value,
                "label": f"转让方 {company_name} -> 类型 {value}",
                "evidence_chain": [
                    {
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_TYPE,
                        "source_name": company_name,
                        "target_value": value,
                        "label": f"转让方 {company_name} -> 类型 {value}",
                    }
                ],
            }
        )
    group_type_authoritative = False
    if analysis["resolved_group"]:
        group_entries = _matching_entries(entries, match_field=MATCH_GROUP, source_name=analysis["resolved_group"])
        group_type_values = _collect_target_values(group_entries, target_field=TARGET_TYPE)
        group_type_authoritative = any(
            _entry_is_authoritative(item) and TARGET_TYPE in _entry_targets(item)
            for item in group_entries
        )
        for value in group_type_values:
            type_candidates.append(
                {
                    "field": TARGET_TYPE,
                    "rule_kind": "group_type",
                    "match_field": MATCH_GROUP,
                    "target_field": TARGET_TYPE,
                    "source_name": analysis["resolved_group"],
                    "target_value": value,
                    "label": f"集团 {analysis['resolved_group']} -> 类型 {value}",
                    "evidence_chain": group_chain + [
                        {
                            "match_field": MATCH_GROUP,
                            "target_field": TARGET_TYPE,
                            "source_name": analysis["resolved_group"],
                            "target_value": value,
                            "label": f"集团 {analysis['resolved_group']} -> 类型 {value}",
                        }
                    ],
                }
            )
    transferor_type_authoritative = any(
        _entry_is_authoritative(item) and TARGET_TYPE in _entry_targets(item)
        for item in transferor_entries
    )
    authoritative_type_candidate = transferor_type_authoritative or group_type_authoritative
    if current_type and not authoritative_type_candidate:
        type_candidates.append(
            {
                "field": TARGET_TYPE,
                "rule_kind": "transferor_type",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_TYPE,
                "source_name": company_name,
                "target_value": current_type,
                "label": f"保留当前类型 {current_type}",
                "evidence_chain": [
                    {
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_TYPE,
                        "source_name": company_name,
                        "target_value": current_type,
                        "label": f"保留当前类型 {current_type}",
                    }
                ],
            }
        )

    unique_type_values = _unique_non_empty(item["target_value"] for item in type_candidates)
    if len(unique_type_values) > 1:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_conflict",
                message=f"conflicting type candidates for company={company_name}",
                evidence={"company_name": company_name, "options": unique_type_values, "field": TARGET_TYPE},
            )
        )
        analysis["has_conflict"] = True
        analysis["candidate_resolutions"].extend(type_candidates)
    elif unique_type_values:
        analysis["resolved_type"] = unique_type_values[0]

    gap_codes: List[str] = []
    available_rule_kinds: List[str] = []
    recommended_rule: Dict[str, str] = {}
    if not analysis["has_conflict"]:
        if not analysis["resolved_group"]:
            gap_codes.append("missing_group")
            available_rule_kinds.extend(["transferor_group", "transferor_type"])
            recommended_rule = {
                "rule_kind": "transferor_group",
                "source_name": company_name,
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
            }
        elif not analysis["resolved_type"]:
            gap_codes.append("missing_type")
            available_rule_kinds.extend(["group_type", "group_group", "transferor_type"])
            recommended_rule = {
                "rule_kind": "group_type",
                "source_name": str(analysis["resolved_group"] or ""),
                "match_field": MATCH_GROUP,
                "target_field": TARGET_TYPE,
            }
    else:
        gap_codes.append("has_conflict")
        available_rule_kinds.extend(["transferor_group", "transferor_type", "group_group", "group_type"])
    analysis["gap_codes"] = gap_codes
    analysis["recommended_rule"] = recommended_rule
    analysis["available_rule_kinds"] = available_rule_kinds
    return analysis


def apply_mapping_entries(
    payload: Dict[str, Any],
    *,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved = dict(payload or {})
    analysis = analyze_mapping_candidates(resolved, mapping_entries=mapping_entries)
    findings: List[PostProcessFinding] = list(analysis["findings"])
    company_name = str(analysis["company_name"] or "").strip()
    if not company_name:
        return resolved, findings

    resolved_group = str(analysis["resolved_group"] or "").strip()
    resolved_type = str(analysis["resolved_type"] or "").strip()
    current_group = _first_non_empty(resolved, GROUP_FIELDS)
    current_type = _first_non_empty(resolved, TYPE_FIELDS)
    changed = False
    if resolved_group and resolved_group != current_group:
        resolved["隶属集团"] = resolved_group
        changed = True
    if resolved_type and resolved_type != current_type:
        resolved["类型"] = resolved_type
        changed = True
    if analysis["has_conflict"]:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_conflict",
                message=f"mapping conflict requires resolution for company={company_name}",
                evidence={"company_name": company_name, "candidate_resolutions": analysis["candidate_resolutions"]},
            )
        )
        return resolved, findings
    if "missing_group" in analysis["gap_codes"] or "missing_type" in analysis["gap_codes"]:
        missing_fields = []
        if "missing_group" in analysis["gap_codes"]:
            missing_fields.append("集团")
        if "missing_type" in analysis["gap_codes"]:
            missing_fields.append("类型")
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_gap",
                message=f"缺少{'、'.join(missing_fields)}，暂不能进入导出",
                evidence={
                    "company_name": company_name,
                    "missing_fields": missing_fields,
                    "recommended_rule": analysis["recommended_rule"],
                },
            )
        )
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_missing",
                message=f"no mapping entry for company={company_name}",
                evidence={"company_name": company_name},
            )
        )
    if changed:
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_applied",
                message=f"mapping applied for company={company_name}",
                evidence={"company_name": company_name},
            )
        )
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_resolution_applied",
                message=f"mapping resolution applied for company={company_name}",
                evidence={
                    "company_name": company_name,
                    "group_name": resolved_group,
                    "source_type": resolved_type,
                },
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
