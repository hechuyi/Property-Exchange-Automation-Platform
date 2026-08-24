"""Record-level postprocess helpers for the streaming pipeline."""

from __future__ import annotations

import copy
import logging
import numbers
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

from peap_core.business_catalog import resolve_business_descriptor
from peap_core.family_catalog import get_family_descriptor
from peap_core.source_business_contract import (
    list_export_readiness_requirements,
    list_optional_postprocess_rule_requirements,
)

from .mapping_subjects import first_match_subject, normalize_match_text
from .streaming_models import PostProcessFinding

logger = logging.getLogger(__name__)

__all__ = [
    "BUSINESS_PROJECT_TYPES",
    "RecordPostprocessContext",
    "derive_listing_times_from_project_code",
    "is_summary_investor_name",
    "finalize_streaming_payload",
    "apply_postprocess_context",
    "normalize_record_payload",
    "is_optional_rule_finding",
    "reapply_optional_rule_findings",
    "apply_mapping_entries",
    "apply_policy_engine_to_payload",
    "run_record_postprocess",
    "analyze_mapping_candidates",
    "findings_to_json",
    "resolve_project_type_label",
]

COMPANY_FIELDS = (
    "转让方",
    "融资方",
    "转让方名称",
    "融资方名称",
    "company_name_primary",
    "seller",
)
GROUP_FIELDS = ("隶属集团", "集团名称", "group_name")
TYPE_FIELDS = ("类型", "source_type")
INVESTOR_ENTRY_NAME_FIELDS = ("investor_name", "投资方名称", "投资方", "name", "投资人")
INVESTOR_ENTRY_AMOUNT_FIELDS = ("investment_amount", "amount", "investmentAmount", "投资金额（万元）", "投资金额", "投资额")
TOP_LEVEL_INVESTOR_NAME_FIELDS = ("investor_name", "投资方名称", "投资方", "投资人")
TOP_LEVEL_INVESTOR_AMOUNT_FIELDS = ("investment_amount", "amount", "investmentAmount", "投资金额（万元）", "投资金额", "投资额")
BUSINESS_ID_FAMILY_FIELDS = ("business_id", "business_id_hint", "业务ID", "业务类型ID")
NESTED_BUSINESS_ID_FAMILY_FIELDS = ("business_id", "business_id_hint", "raw_business_label", "business_label")

LISTING_RECORD_FAMILY = "listing"
DEAL_RECORD_FAMILY = "deal"
CAPITAL_INCREASE_PROJECT_TYPE = "增资扩股"
DEAL_CAPITAL_INCREASE_BUSINESS_IDS = frozenset(
    item.business_id
    for item in list_export_readiness_requirements(record_family=DEAL_RECORD_FAMILY)
    if item.requires_non_summary_investor and item.requires_investor_amount
)
CAPITAL_INCREASE_BUSINESS_IDS = DEAL_CAPITAL_INCREASE_BUSINESS_IDS | frozenset({"capital_increase"})
CONTRACT_LISTING_ONLY_OPTIONAL_RULE_IDS = frozenset(
    item.rule_id
    for item in list_optional_postprocess_rule_requirements(record_family=LISTING_RECORD_FAMILY)
    if item.listing_only
)
LISTING_ONLY_OPTIONAL_RULE_IDS = CONTRACT_LISTING_ONLY_OPTIONAL_RULE_IDS | frozenset({"R006_derive_listing_times"})
SUMMARY_INVESTOR_MARKERS_PATTERN = re.compile(
    r"^(?:总计|合计|小计|subtotal|total)",
    re.IGNORECASE,
)
SUMMARY_INVESTOR_PUNCTUATION_PATTERN = re.compile(r"^[\s:：,，;；。.!！?？、\"'`·~～\-_\\/]*$")
SUMMARY_INVESTOR_NUMERIC_SUFFIX_PATTERN = re.compile(
    r"^[￥¥$]?\s*[-+]?\d[\d,，.\s]*(?:万元|亿元|元|万|亿|%|％)?$",
    re.IGNORECASE,
)
SUMMARY_INVESTOR_SEMANTIC_SUFFIX_PATTERN = re.compile(
    r"^(?:amount|amt|金额|总额|总金额|合计金额|投资金额)(?:\s*(?:\(?(?:万元|亿元|元|万|亿)\)?))?$",
    re.IGNORECASE,
)
SUMMARY_INVESTOR_UNIT_SUFFIX_PATTERN = re.compile(r"^(?:万元|亿元|元|万|亿|rmb|cny)$", re.IGNORECASE)
SUMMARY_INVESTOR_WRAPPER_PAIRS = (
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("<", ">"),
    ("（", "）"),
    ("【", "】"),
    ("《", "》"),
)

MATCH_TRANSFEROR = "transferor"
MATCH_GROUP = "group"
TARGET_GROUP = "group_name"
TARGET_TYPE = "source_type"
BUSINESS_PROJECT_TYPES = {"股权转让", "实物资产", "增资扩股", "预披露"}
PROJECT_TYPE_FALLBACKS = {
    "equity_transfer": "股权转让",
    "physical_asset": "实物资产",
    "capital_increase": "增资扩股",
    "pre_disclosure": "预披露",
    "股权转让": "股权转让",
    "实物资产": "实物资产",
    "增资扩股": "增资扩股",
    "预披露": "预披露",
}
OPTIONAL_RULE_FINDING_TYPES = frozenset({"rule_error", "rule_filtered", "rule_plan_warning"})
LEGACY_OPTIONAL_RULE_FINDING_RULE_IDS = {
    "listing_times_conflict": "R006_derive_listing_times",
    "listing_times_project_code_inconsistent": "R009_consistency_validate",
}
BUSINESS_RESOLUTION_FINDING_TYPE = "business_resolution_required"
LEGACY_UNKNOWN_BUSINESS_FINDING_TYPES = frozenset({"project_type_unknown", BUSINESS_RESOLUTION_FINDING_TYPE})
RECORD_FAMILY_RESOLUTION_BLOCKER_KIND = "record_family_resolution"
ANONYMOUS_TRANSFEROR_NAMES = frozenset({"某企业", "某公司", "某单位", "某转让方"})
SUPERVISOR_TYPE_PATTERNS = (
    ("央企", ("国务院国资委", "国务院国有资产监督管理委员会")),
    ("部委", ("财政部监管", "中央国家机关", "中央部委")),
    ("市属", ("省级国资委", "市级", "区县", "地方国资", "地方政府")),
)


@dataclass(frozen=True)
class RecordPostprocessContext:
    page_url: str = ""
    project_id: str = ""
    project_type_hint: str = ""
    project_type_label: str = ""
    project_type_fallback: str = ""
    record_family: str = ""


def _first_non_empty(payload: Dict[str, Any], fields: Iterable[str]) -> str:
    for field_name in fields:
        value = str(payload.get(field_name) or "").strip()
        if value:
            return value
    return ""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _is_anonymous_transferor_name(value: Any) -> bool:
    return re.sub(r"\s+", "", str(value or "").strip()) in ANONYMOUS_TRANSFEROR_NAMES


def _is_placeholder_mapping_value(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return False
    if not text.replace("-", "").replace("－", "").replace("—", ""):
        return True
    return text in {"无", "不涉及", "暂无", "未知"}


def _source_type_from_supervision_text(*values: Any) -> str:
    text = " ".join(str(value or "").strip() for value in values if str(value or "").strip())
    if not text:
        return ""
    for source_type, markers in SUPERVISOR_TYPE_PATTERNS:
        if any(marker in text for marker in markers):
            return source_type
    return ""


def _apply_anonymous_transferor_type_inference(payload: Dict[str, Any]) -> None:
    company_name = _first_non_empty(payload, COMPANY_FIELDS)
    if not _is_anonymous_transferor_name(company_name):
        return
    if _first_non_empty(payload, TYPE_FIELDS):
        return
    inferred_type = _source_type_from_supervision_text(
        payload.get("state_asset_supervisor"),
        payload.get("国资监管机构"),
        payload.get("state_funded_department"),
        payload.get("国家出资企业或主管部门名称"),
    )
    if inferred_type:
        payload["类型"] = inferred_type


def _scalar_text_value(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, numbers.Number):
        return str(value).strip()
    return ""


def _normalize_record_family(value: Any) -> str:
    family = _clean_text(value)
    if not family:
        return ""
    try:
        return get_family_descriptor(family).family_id
    except KeyError:
        return ""


def _record_family_from_business_identity(payload: Dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates: list[Any] = [payload.get(field_name) for field_name in BUSINESS_ID_FAMILY_FIELDS]
    for nested_key in ("business_identity", "source_identity"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            candidates.extend(nested.get(field_name) for field_name in NESTED_BUSINESS_ID_FAMILY_FIELDS)

    for candidate in candidates:
        text = _clean_text(candidate)
        if not text:
            continue
        try:
            descriptor = resolve_business_descriptor(text)
        except KeyError:
            descriptor = None
        if descriptor is not None:
            return descriptor.family_id
        lowered = text.lower()
        if lowered.startswith("deal_") or lowered.startswith("deal "):
            return DEAL_RECORD_FAMILY
    return ""


def _resolve_record_family(
    payload: Dict[str, Any] | None,
    *,
    context: RecordPostprocessContext | None = None,
) -> str:
    resolved = _optional_payload_mapping(payload)
    payload_family_raw = _clean_text(resolved.get("record_family"))
    family = _normalize_record_family(payload_family_raw)
    context_family = ""
    if context is not None:
        context_family = _normalize_record_family(context.record_family)
        if context_family:
            if family and family != context_family:
                logger.warning(
                    "Record family conflict: payload claims %r but context says %r; "
                    "using context family %r. payload_family_raw=%r",
                    family, context_family, context_family, payload_family_raw,
                )
            return context_family
    if family:
        return family
    business_family = _record_family_from_business_identity(resolved)
    if business_family:
        return business_family
    if payload_family_raw:
        return ""
    return ""


def resolve_project_type_label(*values: Any) -> str:
    for raw_value in values:
        text = _clean_text(raw_value)
        if not text or text in {"未知", "unknown", "UNKNOWN"}:
            continue
        if text in BUSINESS_PROJECT_TYPES:
            return text
        mapped = PROJECT_TYPE_FALLBACKS.get(text.lower()) or PROJECT_TYPE_FALLBACKS.get(text)
        if mapped:
            return mapped
    return ""


def apply_postprocess_context(
    payload: Dict[str, Any] | None,
    *,
    context: RecordPostprocessContext | None = None,
) -> Dict[str, Any]:
    if payload is None:
        resolved = {}
    elif isinstance(payload, Mapping):
        resolved = dict(payload)
    else:
        raise TypeError("payload must be a dict")
    payload_family_raw = _clean_text(resolved.get("record_family"))
    context_family_raw = ""
    if context is not None:
        if context.page_url and not _clean_text(resolved.get("page_url")):
            resolved["page_url"] = context.page_url
        if context.project_id and not _clean_text(resolved.get("project_id")):
            resolved["project_id"] = context.project_id
        context_family_raw = _clean_text(context.record_family)
        project_type = resolve_project_type_label(
            resolved.get("项目类型"),
            resolved.get("project_type"),
            resolved.get("business_type"),
            resolved.get("business_id"),
            context.project_type_hint,
            context.project_type_label,
            context.project_type_fallback,
        )
        if project_type:
            resolved["项目类型"] = project_type
    resolved_family = _resolve_record_family(resolved, context=context)
    if resolved_family and (payload_family_raw or context_family_raw or resolved_family == DEAL_RECORD_FAMILY):
        resolved["record_family"] = resolved_family
    return resolved


def _payload_mapping(payload: Dict[str, Any], *, field_name: str = "payload") -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{field_name} must be a dict")
    return dict(payload)


def _optional_payload_mapping(payload: Dict[str, Any] | None, *, field_name: str = "payload") -> Dict[str, Any]:
    if payload is None:
        return {}
    return _payload_mapping(payload, field_name=field_name)


def _mapping_entries_list(mapping_entries: Iterable[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    if mapping_entries is None:
        return []
    if isinstance(mapping_entries, Mapping) or isinstance(mapping_entries, (str, bytes)):
        raise TypeError("mapping_entries must be an iterable of mappings")
    try:
        iterator = iter(mapping_entries)
    except TypeError:
        raise TypeError("mapping_entries must be an iterable of mappings") from None
    entries: List[Dict[str, Any]] = []
    for item in iterator:
        if not isinstance(item, Mapping):
            raise TypeError("mapping_entries[*] must be a dict")
        entries.append(dict(item))
    return entries


def _postprocess_findings_list(findings: Iterable[PostProcessFinding] | None) -> List[PostProcessFinding]:
    if findings is None:
        return []
    if isinstance(findings, Mapping) or isinstance(findings, (str, bytes)):
        raise TypeError("findings must be an iterable of PostProcessFinding")
    try:
        iterator = iter(findings)
    except TypeError:
        raise TypeError("findings must be an iterable of PostProcessFinding") from None
    resolved: List[PostProcessFinding] = []
    for item in iterator:
        if not isinstance(item, PostProcessFinding):
            raise TypeError("findings[*] must be a PostProcessFinding")
        resolved.append(item)
    return resolved


def _string_iterable_list(values: Iterable[str] | None, *, field_name: str) -> List[str]:
    if values is None:
        return []
    if isinstance(values, Mapping) or isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be an iterable of strings")
    try:
        iterator = iter(values)
    except TypeError:
        raise TypeError(f"{field_name} must be an iterable of strings") from None
    return [str(item or "").strip() for item in iterator if str(item or "").strip()]


def _merge_postprocess_payloads(
    *,
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if parser_payload is None:
        merged = {}
    elif isinstance(parser_payload, Mapping):
        merged = dict(parser_payload)
    else:
        raise TypeError("parser_payload must be a dict")

    if postprocess_payload is None:
        postprocess_items = {}
    elif isinstance(postprocess_payload, Mapping):
        postprocess_items = dict(postprocess_payload)
    else:
        raise TypeError("postprocess_payload must be a dict")

    for key, value in postprocess_items.items():
        if value is None or value == "":
            continue
        merged[str(key)] = value
    return merged


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


def _required_mapping_fields(
    payload: Dict[str, Any],
    *,
    record_family: str = "",
) -> List[str]:
    missing: List[str] = []
    if _normalize_record_family(record_family) != LISTING_RECORD_FAMILY:
        return missing
    project_type = resolve_project_type_label(
        payload.get("项目类型"),
        payload.get("project_type"),
        payload.get("business_type"),
        payload.get("business_id"),
    )
    if project_type == "实物资产" and not _first_non_empty(payload, COMPANY_FIELDS) and not _first_non_empty(payload, GROUP_FIELDS):
        return missing
    if not _first_non_empty(payload, TYPE_FIELDS):
        missing.append("类型")
    return missing


def _mapping_gap_codes_from_missing_fields(missing_fields: Iterable[str]) -> List[str]:
    codes: List[str] = []
    for field_name in missing_fields:
        if str(field_name or "").strip() == "类型":
            codes.append("missing_type")
    return codes


def _investor_name_from_candidate(candidate: Any) -> str:
    def _clean_investor_name(value: Any) -> str:
        return str(value).strip() if isinstance(value, str) else ""

    if isinstance(candidate, dict):
        for key in INVESTOR_ENTRY_NAME_FIELDS:
            value = _clean_investor_name(candidate.get(key))
            if value:
                return value
        return ""
    return _clean_investor_name(candidate)


def _investor_amount_from_candidate(candidate: Any) -> str:
    def _clean_investor_amount(value: Any) -> str:
        return "" if value is None else str(value).strip()

    if isinstance(candidate, dict):
        for key in INVESTOR_ENTRY_AMOUNT_FIELDS:
            value = _clean_investor_amount(candidate.get(key))
            if value:
                return value
    return ""


def _strip_wrapped_summary_suffix(value: str) -> str:
    text = str(value or "").strip()
    while len(text) >= 2:
        unwrapped = False
        for left, right in SUMMARY_INVESTOR_WRAPPER_PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left) : len(text) - len(right)].strip()
                unwrapped = True
                break
        if not unwrapped:
            break
    return text


def _strip_summary_edge_punctuation(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[\s:：,，;；。.!！?？、\"'`·~～\-_\\/]+", "", text)
    text = re.sub(r"[\s:：,，;；。.!！?？、\"'`·~～\-_\\/]+$", "", text)
    return text.strip()


def is_summary_investor_name(name: str) -> bool:
    normalized = _clean_text(name)
    if not normalized:
        return False
    marker_match = SUMMARY_INVESTOR_MARKERS_PATTERN.match(normalized)
    if marker_match is None:
        return False
    marker = marker_match.group(0)
    tail = normalized[len(marker) :].strip()
    if not tail:
        return True
    unwrapped_tail = _strip_wrapped_summary_suffix(tail)
    if not unwrapped_tail:
        return True
    if SUMMARY_INVESTOR_PUNCTUATION_PATTERN.fullmatch(unwrapped_tail):
        return True
    tail_without_edge_punctuation = _strip_summary_edge_punctuation(unwrapped_tail)
    if not tail_without_edge_punctuation:
        return True
    if SUMMARY_INVESTOR_NUMERIC_SUFFIX_PATTERN.fullmatch(tail_without_edge_punctuation):
        return True
    if SUMMARY_INVESTOR_SEMANTIC_SUFFIX_PATTERN.fullmatch(tail_without_edge_punctuation):
        return True
    return bool(SUMMARY_INVESTOR_UNIT_SUFFIX_PATTERN.fullmatch(tail_without_edge_punctuation))


# Backward-compatible private alias for existing internal imports.
_is_summary_investor_name = is_summary_investor_name


def _iter_investor_names(payload: Dict[str, Any]) -> Iterable[str]:
    investors = payload.get("investors")
    if isinstance(investors, list):
        for candidate in investors:
            name = _investor_name_from_candidate(candidate)
            if name:
                yield name
    elif investors is not None:
        name = _investor_name_from_candidate(investors)
        if name:
            yield name
    for key in TOP_LEVEL_INVESTOR_NAME_FIELDS:
        raw_value = payload.get(key)
        value = _scalar_text_value(raw_value)
        if value:
            yield value


def _has_non_summary_investor(payload: Dict[str, Any]) -> bool:
    for name in _iter_investor_names(payload):
        if not _is_summary_investor_name(name):
            return True
    return False


def _has_export_ready_non_summary_investor(payload: Dict[str, Any]) -> bool:
    investors = payload.get("investors")
    if isinstance(investors, list):
        for candidate in investors:
            name = _investor_name_from_candidate(candidate)
            if name and not _is_summary_investor_name(name) and _investor_amount_from_candidate(candidate):
                return True
    elif investors is not None:
        name = _investor_name_from_candidate(investors)
        if name and not _is_summary_investor_name(name) and _investor_amount_from_candidate(investors):
            return True

    for name_key in TOP_LEVEL_INVESTOR_NAME_FIELDS:
        raw_name = payload.get(name_key)
        name = _scalar_text_value(raw_name)
        if not name or _is_summary_investor_name(name):
            continue
        for amount_key in TOP_LEVEL_INVESTOR_AMOUNT_FIELDS:
            raw_amount = payload.get(amount_key)
            amount = _scalar_text_value(raw_amount)
            if amount:
                return True
    return False


def _is_deal_capital_increase_record(payload: Dict[str, Any], *, record_family: str) -> bool:
    if _normalize_record_family(record_family) != DEAL_RECORD_FAMILY:
        return False
    project_type = resolve_project_type_label(payload.get("项目类型"), payload.get("project_type"))
    if project_type == CAPITAL_INCREASE_PROJECT_TYPE:
        return True
    business_id = _clean_text(payload.get("business_id")).lower()
    return business_id in CAPITAL_INCREASE_BUSINESS_IDS


def _is_legacy_mapping_blocker(finding: PostProcessFinding) -> bool:
    finding_type = str(finding.type or "").strip()
    if finding_type not in {"mapping_missing", "mapping_gap"}:
        return False
    return True


def _finding_evidence_mapping(raw_evidence: Any, *, allow_none: bool = False) -> Dict[str, Any]:
    if raw_evidence is None and allow_none:
        return {}
    if isinstance(raw_evidence, Mapping):
        return dict(raw_evidence)
    raise TypeError(f"finding.evidence must be a mapping, got {type(raw_evidence).__name__}")


def _build_business_resolution_finding(
    *,
    raw_business_label: str,
    diagnostic_gap_codes: Iterable[str] | None = None,
    reason_code: str = "",
    message: str = "",
) -> PostProcessFinding:
    resolved_message = str(message or "").strip()
    if not resolved_message:
        resolved_message = "缺少业务类型，需完成人工归类后再继续处理" if not raw_business_label else "业务类型未识别，需完成人工归类后再继续处理"
    evidence = {
        "raw_business_label": str(raw_business_label or "").strip(),
        "diagnostic_gap_codes": _string_iterable_list(
            diagnostic_gap_codes,
            field_name="diagnostic_gap_codes",
        ),
        "blocker_kind": "business_resolution",
    }
    if reason_code:
        evidence["reason_code"] = str(reason_code).strip()
    return PostProcessFinding(
        severity="warn",
        type=BUSINESS_RESOLUTION_FINDING_TYPE,
        message=resolved_message,
        evidence=evidence,
    )


def _record_family_resolution_finding(
    *,
    reason_code: str,
    message: str,
    evidence: Dict[str, Any],
) -> PostProcessFinding:
    payload = {
        "blocker_kind": RECORD_FAMILY_RESOLUTION_BLOCKER_KIND,
        "reason_code": reason_code,
    }
    payload.update({key: value for key, value in evidence.items() if value})
    return PostProcessFinding(
        severity="warn",
        type=BUSINESS_RESOLUTION_FINDING_TYPE,
        message=message,
        evidence=payload,
    )


def _build_record_family_resolution_findings(
    *payloads: Dict[str, Any] | None,
    context: RecordPostprocessContext | None = None,
) -> List[PostProcessFinding]:
    context_family_raw = _clean_text(context.record_family) if context is not None else ""
    context_family = _normalize_record_family(context_family_raw)
    invalid_payload_family = ""

    for payload in payloads:
        if not isinstance(payload, dict) or "record_family" not in payload:
            continue
        payload_family_raw = _clean_text(payload.get("record_family"))
        if not payload_family_raw:
            continue
        payload_family = _normalize_record_family(payload_family_raw)
        if context_family and payload_family and payload_family != context_family:
            return [
                _record_family_resolution_finding(
                    reason_code="record_family_conflict",
                    message="record_family 与当前处理上下文不一致，需人工确认记录族后再继续处理",
                    evidence={
                        "payload_record_family": payload_family,
                        "context_record_family": context_family,
                    },
                )
            ]
        if not payload_family and not invalid_payload_family:
            invalid_payload_family = payload_family_raw

    if invalid_payload_family and not context_family:
        return [
            _record_family_resolution_finding(
                reason_code="invalid_record_family",
                message="record_family 无法识别，需人工确认记录族后再继续处理",
                evidence={"payload_record_family": invalid_payload_family},
            )
        ]
    return []


def _has_record_family_resolution_finding(
    findings: Iterable[PostProcessFinding],
    *,
    reason_code: str,
) -> bool:
    for item in findings:
        if item.type != BUSINESS_RESOLUTION_FINDING_TYPE:
            continue
        evidence = _finding_evidence_mapping(item.evidence)
        if evidence.get("blocker_kind") == RECORD_FAMILY_RESOLUTION_BLOCKER_KIND and evidence.get("reason_code") == reason_code:
            return True
    return False


def finalize_streaming_payload(
    payload: Dict[str, Any],
    *,
    findings: Iterable[PostProcessFinding] | None = None,
    context: RecordPostprocessContext | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved = _payload_mapping(payload)
    record_family = _resolve_record_family(resolved, context=context)
    payload_family_raw = _clean_text(resolved.get("record_family"))
    context_family_raw = _clean_text(context.record_family) if context is not None else ""
    if record_family and (payload_family_raw or context_family_raw or record_family == DEAL_RECORD_FAMILY):
        resolved["record_family"] = record_family
    normalized_findings: List[PostProcessFinding] = []
    unresolved_business_reason_code = ""
    unresolved_business_message = ""
    for item in _postprocess_findings_list(findings):
        finding_type = str(item.type or "")
        if _is_legacy_mapping_blocker(item):
            continue
        if finding_type in LEGACY_UNKNOWN_BUSINESS_FINDING_TYPES:
            evidence = _finding_evidence_mapping(item.evidence)
            if finding_type == BUSINESS_RESOLUTION_FINDING_TYPE and evidence.get("blocker_kind") == RECORD_FAMILY_RESOLUTION_BLOCKER_KIND:
                normalized_findings.append(item)
                continue
            message = str(item.message or "").strip()
            if message.startswith("entity_type_mapping_file not found:"):
                unresolved_business_reason_code = BUSINESS_RESOLUTION_FINDING_TYPE
                unresolved_business_message = "项目类型映射模板缺失，当前记录无法完成业务归属"
            continue
        normalized_findings.append(item)
    for item in _build_record_family_resolution_findings(resolved, context=context):
        reason_code = str(item.evidence.get("reason_code") or "")
        if not _has_record_family_resolution_finding(normalized_findings, reason_code=reason_code):
            normalized_findings.append(item)
    if record_family == LISTING_RECORD_FAMILY and not _clean_text(resolved.get("挂牌次数")):
        derived_listing_times = derive_listing_times_from_project_code(_clean_text(resolved.get("项目编号")))
        if derived_listing_times:
            resolved["挂牌次数"] = derived_listing_times
    project_type = resolve_project_type_label(
        resolved.get("项目类型"),
        resolved.get("project_type"),
        resolved.get("business_type"),
        resolved.get("business_id"),
    )
    if project_type and not _clean_text(resolved.get("项目类型")):
        resolved["项目类型"] = project_type
    _apply_anonymous_transferor_type_inference(resolved)
    missing_fields = _required_mapping_fields(resolved, record_family=record_family)
    if project_type not in BUSINESS_PROJECT_TYPES:
        diagnostic_gap_codes = _mapping_gap_codes_from_missing_fields(missing_fields)
        normalized_findings.append(
            _build_business_resolution_finding(
                raw_business_label=project_type,
                diagnostic_gap_codes=diagnostic_gap_codes,
                reason_code=unresolved_business_reason_code,
                message=unresolved_business_message,
            )
        )
        return resolved, normalized_findings
    if _is_deal_capital_increase_record(resolved, record_family=record_family):
        if not _has_non_summary_investor(resolved):
            normalized_findings.append(
                _build_business_resolution_finding(
                    raw_business_label=project_type,
                    reason_code="deal_capital_increase_missing_investor",
                    message="增资成交记录缺少非汇总投资方，需人工复核后再继续处理",
                )
            )
        elif not _has_export_ready_non_summary_investor(resolved):
            normalized_findings.append(
                _build_business_resolution_finding(
                    raw_business_label=project_type,
                    reason_code="deal_capital_increase_missing_investor_amount",
                    message="增资成交记录在官方原页/接口中未提供可自动入库的非汇总投资方金额，需人工复核后再继续处理",
                )
            )
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


def normalize_record_payload(
    *,
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
    findings: Iterable[PostProcessFinding] | None = None,
    context: RecordPostprocessContext | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    family_findings = _build_record_family_resolution_findings(
        parser_payload,
        postprocess_payload,
        context=context,
    )
    merged = apply_postprocess_context(
        _merge_postprocess_payloads(
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
        ),
        context=context,
    )
    return finalize_streaming_payload(
        merged,
        findings=[*family_findings, *_postprocess_findings_list(findings)],
        context=context,
    )


def is_optional_rule_finding(finding: PostProcessFinding | Dict[str, Any] | Any) -> bool:
    if isinstance(finding, dict):
        finding_type = str(finding.get("type") or "").strip().lower()
        evidence = _finding_evidence_mapping(finding.get("evidence"), allow_none=True)
    else:
        finding_type = str(getattr(finding, "type", "") or "").strip().lower()
        evidence = _finding_evidence_mapping(getattr(finding, "evidence", None))
    rule_id = str(evidence.get("rule_id") or "").strip() or LEGACY_OPTIONAL_RULE_FINDING_RULE_IDS.get(finding_type, "")
    return (
        finding_type in OPTIONAL_RULE_FINDING_TYPES
        or finding_type.endswith("_filtered")
        or bool(rule_id)
    )


def reapply_optional_rule_findings(
    *,
    parser_payload: Dict[str, Any] | None,
    postprocess_payload: Dict[str, Any] | None,
    findings: Iterable[PostProcessFinding] | None = None,
    source_file: str,
    rules_config: Dict[str, Any] | None = None,
    context: RecordPostprocessContext | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    source_payload = _merge_postprocess_payloads(
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
    )
    merged_payload = apply_postprocess_context(source_payload, context=context)

    preserved_findings = [
        item
        for item in _postprocess_findings_list(findings)
        if not is_optional_rule_finding(item)
    ]
    resolved_payload, rule_findings = _apply_optional_rule_registry(
        merged_payload,
        source_file=source_file,
        rules_config=rules_config,
    )
    return normalize_record_payload(
        parser_payload=source_payload,
        postprocess_payload=resolved_payload,
        findings=[*preserved_findings, *rule_findings],
        context=context,
    )


def _normalize_company(value: str) -> str:
    return normalize_match_text(value)


def _mapping_subject_name(value: Any, *, match_field: str) -> str:
    return first_match_subject(value, match_field=match_field) or str(value or "").strip()


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
    for item in _mapping_entries_list(entries):
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
            for item in _mapping_entries_list(entries)
            if target_field in _entry_targets(item) and str(item.get(value_key) or "").strip()
        }
    )
    return values


def _entries_for_target(entries: Iterable[Dict[str, Any]], *, target_field: str) -> List[Dict[str, Any]]:
    value_key = "group_name" if target_field == TARGET_GROUP else "source_type"
    return [
        dict(item)
        for item in _mapping_entries_list(entries)
        if target_field in _entry_targets(item) and str(item.get(value_key) or "").strip()
    ]


def _unique_non_empty(values: Iterable[Any]) -> List[str]:
    return sorted({str(item or "").strip() for item in values if str(item or "").strip()})


def _prefer_authoritative_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    materialized = [dict(item) for item in candidates if str(item.get("target_value") or "").strip()]
    authoritative = [item for item in materialized if bool(item.get("authoritative"))]
    return authoritative or materialized


def _resolve_single_mapping(
    *,
    entries: Iterable[Dict[str, Any]],
    target_field: str,
    subject_name: str,
    ambiguous_type: str,
    ambiguous_message: str,
) -> tuple[str, List[PostProcessFinding]]:
    findings: List[PostProcessFinding] = []
    relevant_entries = _entries_for_target(entries, target_field=target_field)
    authoritative_entries = [item for item in relevant_entries if _entry_is_authoritative(item)]
    values = _collect_target_values(
        authoritative_entries if authoritative_entries else relevant_entries,
        target_field=target_field,
    )
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
    resolved = _payload_mapping(payload)
    entries = _mapping_entries_list(mapping_entries)
    findings: List[PostProcessFinding] = []
    company_raw = _first_non_empty(resolved, COMPANY_FIELDS)
    company_name = _mapping_subject_name(company_raw, match_field=MATCH_TRANSFEROR)
    current_group = _first_non_empty(resolved, GROUP_FIELDS)
    if _is_placeholder_mapping_value(current_group):
        current_group = ""
    current_type = _first_non_empty(resolved, TYPE_FIELDS)
    if _is_placeholder_mapping_value(current_type):
        current_type = ""
    if not current_type:
        current_type = _source_type_from_supervision_text(
            resolved.get("state_asset_supervisor"),
            resolved.get("国资监管机构"),
            resolved.get("state_funded_department"),
            resolved.get("国家出资企业或主管部门名称"),
        )
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
    if not company_name and not current_group:
        return analysis
    if _is_anonymous_transferor_name(company_name) and not current_group:
        return analysis

    transferor_entries = _matching_entries(entries, match_field=MATCH_TRANSFEROR, source_name=company_name) if company_name else []
    transferor_group_entries = _entries_for_target(transferor_entries, target_field=TARGET_GROUP)
    transferor_type_entries = _entries_for_target(transferor_entries, target_field=TARGET_TYPE)

    group_candidates: List[Dict[str, Any]] = []
    transferor_group_authoritative = any(
        _entry_is_authoritative(item) and TARGET_GROUP in _entry_targets(item)
        for item in transferor_entries
    )
    for entry in transferor_group_entries:
        value = str(entry.get("group_name") or "").strip()
        group_candidates.append(
            {
                "field": TARGET_GROUP,
                "rule_kind": "transferor_group",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
                "source_name": company_name,
                "target_value": value,
                "authoritative": _entry_is_authoritative(entry),
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
    if company_name and current_group and not transferor_group_authoritative:
        current_group_value = current_group
        current_group_chain: List[Dict[str, str]] = []
        traced_current_group, traced_chain, traced_findings = _trace_group_chain(entries, group_name=current_group)
        if traced_current_group and not traced_findings:
            current_group_value = traced_current_group
            current_group_chain = traced_chain
        group_candidates.append(
            {
                "field": TARGET_GROUP,
                "rule_kind": "transferor_group",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
                "source_name": company_name,
                "target_value": current_group_value,
                "authoritative": False,
                "label": f"保留当前集团 {current_group}",
                "evidence_chain": current_group_chain + [
                    {
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_GROUP,
                        "source_name": company_name,
                        "target_value": current_group_value,
                        "label": f"保留当前集团 {current_group}",
                    }
                ],
            }
        )

    group_candidates = _prefer_authoritative_candidates(group_candidates)
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
    for entry in transferor_type_entries:
        value = str(entry.get("source_type") or "").strip()
        type_candidates.append(
            {
                "field": TARGET_TYPE,
                "rule_kind": "transferor_type",
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_TYPE,
                "source_name": company_name,
                "target_value": value,
                "authoritative": _entry_is_authoritative(entry),
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
        group_type_entries = _entries_for_target(group_entries, target_field=TARGET_TYPE)
        group_type_authoritative = any(
            _entry_is_authoritative(item) and TARGET_TYPE in _entry_targets(item)
            for item in group_entries
        )
        for entry in group_type_entries:
            value = str(entry.get("source_type") or "").strip()
            type_candidates.append(
                {
                    "field": TARGET_TYPE,
                    "rule_kind": "group_type",
                    "match_field": MATCH_GROUP,
                    "target_field": TARGET_TYPE,
                    "source_name": analysis["resolved_group"],
                    "target_value": value,
                    "authoritative": _entry_is_authoritative(entry),
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
                "authoritative": False,
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

    type_candidates = _prefer_authoritative_candidates(type_candidates)
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
        missing_group = bool(company_name and not analysis["resolved_group"])
        missing_type = not analysis["resolved_type"]
        if missing_group:
            gap_codes.append("missing_group")
            available_rule_kinds.append("transferor_group")
        if missing_type:
            gap_codes.append("missing_type")
            if analysis["resolved_group"]:
                recommended_rule = {
                    "rule_kind": "group_type",
                    "source_name": str(analysis["resolved_group"] or ""),
                    "match_field": MATCH_GROUP,
                    "target_field": TARGET_TYPE,
                }
            elif company_name:
                recommended_rule = {
                    "rule_kind": "transferor_type",
                    "source_name": company_name,
                    "match_field": MATCH_TRANSFEROR,
                    "target_field": TARGET_TYPE,
                }
            if company_name:
                available_rule_kinds.append("transferor_type")
            if analysis["resolved_group"]:
                available_rule_kinds.extend(["group_type", "group_group"])
        elif missing_group:
            recommended_rule = {
                "rule_kind": "transferor_group",
                "source_name": company_name,
                "match_field": MATCH_TRANSFEROR,
                "target_field": TARGET_GROUP,
            }
    else:
        gap_codes.append("has_conflict")
        available_rule_kinds.extend(["transferor_group", "transferor_type", "group_group", "group_type"])
    analysis["gap_codes"] = list(dict.fromkeys(gap_codes))
    analysis["recommended_rule"] = recommended_rule
    analysis["available_rule_kinds"] = list(dict.fromkeys(available_rule_kinds))
    return analysis


def apply_mapping_entries(
    payload: Dict[str, Any],
    *,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved = _payload_mapping(payload)
    record_family = _resolve_record_family(resolved)
    analysis = analyze_mapping_candidates(resolved, mapping_entries=mapping_entries)
    findings: List[PostProcessFinding] = list(analysis["findings"])
    has_conflict = bool(analysis["has_conflict"])
    company_name = str(analysis["company_name"] or "").strip()
    resolved_group = str(analysis["resolved_group"] or "").strip()
    resolved_type = str(analysis["resolved_type"] or "").strip()
    subject_name = company_name or resolved_group
    if not subject_name:
        return resolved, findings

    changed = False
    if resolved_group and any(
        _clean_text(resolved.get(field)) != resolved_group
        for field in ("隶属集团", "group_name")
    ):
        resolved["隶属集团"] = resolved_group
        resolved["group_name"] = resolved_group
        changed = True
    if resolved_type and any(
        _clean_text(resolved.get(field)) != resolved_type
        for field in ("类型", "source_type")
    ):
        resolved["类型"] = resolved_type
        resolved["source_type"] = resolved_type
        changed = True
    if has_conflict:
        if record_family not in {"", LISTING_RECORD_FAMILY}:
            findings = [f for f in findings if str(f.type or "") != "mapping_conflict"]
            findings.append(
                PostProcessFinding(
                    severity="info",
                    type="mapping_conflict_non_blocking",
                    message=f"mapping conflict detected for subject={subject_name} (non-blocking for non-listing family)",
                    evidence={
                        "company_name": company_name,
                        "group_name": resolved_group,
                        "candidate_resolutions": analysis["candidate_resolutions"],
                        "non_blocking": True,
                    },
                )
            )
            return resolved, findings
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_conflict",
                message=f"mapping conflict requires resolution for subject={subject_name}",
                evidence={"company_name": company_name, "group_name": resolved_group, "candidate_resolutions": analysis["candidate_resolutions"]},
            )
        )
        return resolved, findings
    if record_family == LISTING_RECORD_FAMILY and "missing_type" in analysis["gap_codes"]:
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_gap",
                message="缺少类型，暂不能进入导出",
                evidence={
                    "company_name": company_name,
                    "group_name": resolved_group,
                    "missing_fields": ["类型"],
                    "recommended_rule": analysis["recommended_rule"],
                },
            )
        )
        findings.append(
            PostProcessFinding(
                severity="warn",
                type="mapping_missing",
                message=f"no mapping entry for subject={subject_name}",
                evidence={"company_name": company_name, "group_name": resolved_group},
            )
        )
    if "missing_group" in analysis["gap_codes"]:
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_advisory",
                message="缺少集团，可补转让方 -> 集团以完善映射链路",
                evidence={
                    "company_name": company_name,
                    "group_name": resolved_group,
                    "recommended_rule": {
                        "rule_kind": "transferor_group",
                        "source_name": company_name,
                        "match_field": MATCH_TRANSFEROR,
                        "target_field": TARGET_GROUP,
                    } if company_name else {},
                },
            )
        )
    if changed:
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_applied",
                message=f"mapping applied for subject={subject_name}",
                evidence={"company_name": company_name, "group_name": resolved_group},
            )
        )
        findings.append(
            PostProcessFinding(
                severity="info",
                type="mapping_resolution_applied",
                message=f"mapping resolution applied for subject={subject_name}",
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
    resolved = dict(payload)
    record_family = _resolve_record_family(resolved)
    bindings, warnings = registry.build_plan(rules_config, record_family=record_family)
    findings = [
        PostProcessFinding(severity="warn", type="rule_plan_warning", message=text, evidence={})
        for text in warnings
    ]
    for binding in bindings:
        rule_id = binding.rule.rule_id()
        if record_family != LISTING_RECORD_FAMILY and rule_id in LISTING_ONLY_OPTIONAL_RULE_IDS:
            continue
        try:
            result = binding.rule.apply(record, {"mode": "streaming"})
        except Exception as exc:  # pragma: no cover - defensive path
            findings.append(
                PostProcessFinding(
                    severity="error",
                    type="rule_error",
                    message=str(exc),
                    evidence={"rule_id": rule_id},
                )
            )
            continue
        for patch in result.patches:
            if patch.action == "filter_out_row":
                findings.append(
                    PostProcessFinding(
                        severity="warn",
                        type="rule_filtered",
                        message=f"rule filtered record: {rule_id}",
                        evidence={"rule_id": rule_id},
                    )
                )
                continue
            resolved[patch.field] = patch.new_value
            if patch.field in TYPE_FIELDS:
                resolved["类型"] = patch.new_value
                resolved["source_type"] = patch.new_value
            if patch.field in GROUP_FIELDS:
                resolved["隶属集团"] = patch.new_value
                resolved["group_name"] = patch.new_value
        for finding in result.findings:
            evidence = _finding_evidence_mapping(finding.evidence)
            if str(finding.rule_id or "").strip() and not str(evidence.get("rule_id") or "").strip():
                evidence["rule_id"] = str(finding.rule_id)
            findings.append(
                PostProcessFinding(
                    severity=finding.severity,
                    type=finding.type,
                    message=finding.message,
                    evidence=evidence,
                )
            )
        record = _build_canonical_record(resolved, source_file=source_file)
    return resolved, findings




def apply_policy_engine_to_payload(
    payload: Dict[str, Any],
    *,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    resolved, findings = apply_mapping_entries(payload, mapping_entries=mapping_entries)
    return resolved, findings


def run_record_postprocess(
    payload: Dict[str, Any],
    *,
    source_file: str,
    mapping_entries: Iterable[Dict[str, Any]] | None = None,
    rules_config: Dict[str, Any] | None = None,
    context: RecordPostprocessContext | None = None,
) -> tuple[Dict[str, Any], List[PostProcessFinding]]:
    working = apply_postprocess_context(copy.deepcopy(_payload_mapping(payload)), context=context)
    mapped_payload, findings = apply_policy_engine_to_payload(
        working,
        mapping_entries=mapping_entries,
    )
    rule_payload, rule_findings = _apply_optional_rule_registry(
        apply_postprocess_context(mapped_payload, context=context),
        source_file=source_file,
        rules_config=rules_config,
    )
    findings.extend(rule_findings)
    return normalize_record_payload(
        parser_payload=payload,
        postprocess_payload=rule_payload,
        findings=findings,
        context=context,
    )


def findings_to_json(findings: Iterable[PostProcessFinding]) -> List[Dict[str, Any]]:
    return [asdict(item) for item in findings]
