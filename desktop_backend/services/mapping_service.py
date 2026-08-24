"""Mapping service — backlog analysis, rule preview, and conflict resolution helpers.

This module extracts mapping-domain responsibilities out of AppService so the
desktop service layer can move toward clearer records / mappings / jobs
boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Dict

from peap.streaming_postprocess import analyze_mapping_candidates
from peap_core.business_catalog import get_business_descriptor
from peap_core.family_catalog import get_family_descriptor

from ..domain.normalizers import (
    mapping_rule_metadata as _mapping_rule_metadata,
)
from ..domain.normalizers import (
    mapping_rule_title as _mapping_rule_title,
)
from ..domain.normalizers import (
    mapping_scope_miss_payload as _mapping_scope_miss_payload,
)
from ..domain.normalizers import (
    normalize_exchange_code as _normalize_exchange_code,
)
from ..domain.normalizers import (
    normalize_exchange_label as _normalize_exchange_label,
)
from ..domain.normalizers import (
    normalize_mapping_payload as _normalize_mapping_payload,
)
from ..domain.normalizers import (
    normalize_match_text as _normalize_match_text,
)
from ..domain.normalizers import (
    parse_text as _parse_text,
)
from ..domain.normalizers import (
    status_label as _status_label,
)
from ..domain.normalizers import (
    validate_mapping_payload as _validate_mapping_payload,
)
from ..domain.record_projection import (
    build_record_mapping_payload as _build_record_mapping_payload,
)
from ..domain.record_projection import (
    mapping_template_issue as _mapping_template_issue,
)
from ..domain.record_projection import (
    record_matches_mapping_source as _record_matches_mapping_source,
)
from ..domain.record_projection import (
    record_status_detail as _record_status_detail,
)
from ..repositories import PipelineRepository
from ..request_contract import normalize_mapping_record_selection_request

UNKNOWN_BUSINESS_LABEL = "未识别项目类型"


def _owns_mapping_backlog(item: Dict[str, Any]) -> bool:
    blocker_kind = str(item.get("blocker_kind") or "").strip()
    return bool(item.get("audit_only")) or blocker_kind in {"mapping_gap", "mapping_conflict"}


def _optional_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _required_list(source: Mapping[str, Any], field_name: str) -> list[Any]:
    if field_name not in source:
        raise ValueError(f"analysis.{field_name} is required")
    value = source.get(field_name)
    if not isinstance(value, list):
        raise ValueError(f"analysis.{field_name} must be a list")
    return list(value)


def _required_text_list(source: Mapping[str, Any], field_name: str) -> list[str]:
    items = _required_list(source, field_name)
    texts: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"analysis.{field_name}[*] must be strings")
        texts.append(item.strip())
    return texts


def _entry_metadata(entry: Mapping[str, Any]) -> Dict[str, Any]:
    raw_metadata = entry.get("metadata")
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("entry.metadata must be a mapping")
    return dict(raw_metadata)


def _findings(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_findings = record.get("findings")
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        raise ValueError("record.findings must be a list")
    findings: list[Dict[str, Any]] = []
    for item in raw_findings:
        if not isinstance(item, Mapping):
            raise ValueError("record.findings[*] must be an object")
        findings.append(dict(item))
    return findings


def _finding_evidence(item: Mapping[str, Any]) -> Dict[str, Any]:
    if "evidence" not in item:
        return {}
    raw_evidence = item.get("evidence")
    if not isinstance(raw_evidence, Mapping):
        raise ValueError("findings[*].evidence must be a mapping")
    return dict(raw_evidence)


def _finding_types(record: Dict[str, Any]) -> set[str]:
    return {
        str(item.get("type") or "").strip()
        for item in _findings(record)
        if str(item.get("type") or "").strip()
    }


def _is_business_resolution_finding_type(finding_type: str) -> bool:
    return str(finding_type or "").strip() == "business_resolution_required"


def _needs_business_re_evaluation(record: Dict[str, Any]) -> bool:
    if str(record.get("state") or "").strip() == "pending_review":
        return True
    return any(_is_business_resolution_finding_type(finding_type) for finding_type in _finding_types(record))


def _canonical_business_resolution_reason_code(reason_code: str) -> str:
    normalized = str(reason_code or "").strip()
    if normalized == "project_type_mapping_template_missing":
        return "business_resolution_required"
    return normalized


def _canonical_evidence_code(code: str) -> str:
    return str(code or "").strip()


def _finding_reason_codes(record: Dict[str, Any]) -> set[str]:
    reason_codes: set[str] = set()
    for item in _findings(record):
        evidence = _finding_evidence(item)
        finding_type = str(item.get("type") or "").strip()
        reason_code = str(evidence.get("reason_code") or "").strip()
        if _is_business_resolution_finding_type(finding_type):
            reason_code = _canonical_business_resolution_reason_code(reason_code)
        reason_code = _canonical_evidence_code(reason_code)
        if reason_code:
            reason_codes.add(reason_code)
        elif _is_business_resolution_finding_type(finding_type):
            reason_codes.add("unrecognized_business")
        if finding_type == "hidden_family":
            reason_codes.add("hidden_family")
    return reason_codes


def _raw_business_label(record: Dict[str, Any], payload: Dict[str, Any]) -> str:
    for item in _findings(record):
        evidence = _finding_evidence(item)
        raw_label = str(evidence.get("raw_business_label") or "").strip()
        if raw_label:
            return raw_label
    explicit = str(record.get("raw_business_label") or record.get("business_label") or "").strip()
    if explicit:
        return explicit
    return str(payload.get("项目类型") or record.get("project_type") or "").strip()


def _raw_business_label_candidates(record: Dict[str, Any], payload: Dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for item in _findings(record):
        evidence = _finding_evidence(item)
        for key in ("raw_business_label", "business_label"):
            text = str(evidence.get(key) or "").strip()
            if text:
                candidates.add(text)
    for value in (
        record.get("raw_business_label"),
        record.get("business_label"),
        payload.get("项目类型"),
        record.get("project_type"),
    ):
        text = str(value or "").strip()
        if text:
            candidates.add(text)
    return candidates


def _business_id(record: Dict[str, Any]) -> str:
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


def _safe_business_label(record: Dict[str, Any]) -> str:
    business_id = _business_id(record)
    if business_id:
        try:
            return get_business_descriptor(business_id, family_id=_record_family(record)).canonical_label
        except (KeyError, ValueError):
            return UNKNOWN_BUSINESS_LABEL
    return UNKNOWN_BUSINESS_LABEL


def _sanitize_display_text(value: Any, unsafe_values: set[str], *, fallback: str = "") -> str:
    text = str(value or "").strip()
    if text and text in unsafe_values:
        return fallback
    return text


def _sanitize_evidence_chain(source: Mapping[str, Any], unsafe_values: set[str]) -> list[Any]:
    sanitized: list[Any] = []
    for entry in _required_list(source, "evidence_chain"):
        if isinstance(entry, dict):
            safe_entry = {
                str(key): _sanitize_display_text(value, unsafe_values)
                for key, value in entry.items()
                if str(key) != "raw_business_label"
            }
            safe_entry = {key: value for key, value in safe_entry.items() if value}
            if safe_entry:
                sanitized.append(safe_entry)
            continue
        text = _sanitize_display_text(entry, unsafe_values)
        if text:
            sanitized.append(text)
    return sanitized


def _record_family(record: Dict[str, Any]) -> str:
    direct = str(record.get("record_family") or "").strip()
    if direct:
        return direct
    canonical_record = record.get("canonical_record")
    if isinstance(canonical_record, dict):
        nested = str(canonical_record.get("record_family") or "").strip()
        if nested:
            return nested
        business_identity = canonical_record.get("business_identity")
        if isinstance(business_identity, dict):
            nested = str(business_identity.get("record_family") or "").strip()
            if nested:
                return nested
        source_identity = canonical_record.get("source_identity")
        if isinstance(source_identity, dict):
            nested = str(source_identity.get("record_family") or "").strip()
            if nested:
                return nested
    return ""


def _is_default_mapping_family(record: Dict[str, Any]) -> bool:
    default_family = get_family_descriptor("listing").family_id
    try:
        return get_family_descriptor(_record_family(record)).family_id == default_family
    except KeyError:
        return False


def _has_invalid_record_family(record: Dict[str, Any]) -> bool:
    family = _record_family(record)
    if not family:
        return True
    try:
        get_family_descriptor(family)
    except KeyError:
        return True
    return False


def _audit_only(record: Dict[str, Any]) -> bool:
    if not _is_default_mapping_family(record):
        return True
    finding_types = _finding_types(record)
    if "hidden_family" in finding_types:
        return True
    return any(bool(_finding_evidence(item).get("hidden_family")) for item in _findings(record))


def _blocker_kind(record: Dict[str, Any], item: Dict[str, Any]) -> str:
    if bool(item.get("audit_only")):
        return "audit"
    state = str(item.get("state") or str(record.get("state") or "")).strip()
    blocking_reason_code = _canonical_business_resolution_reason_code(str(item.get("blocking_reason_code") or "").strip())
    if (
        state == "mapping_conflict"
        or bool(item.get("has_conflict"))
        or str(item.get("blocking_reason_code") or "").strip() == "mapping_conflict"
        or any(str(finding_type or "").strip() == "mapping_conflict" for finding_type in _finding_types(record))
    ):
        return "mapping_conflict"
    if blocking_reason_code == "business_resolution_required":
        return "audit"
    if any(_is_business_resolution_finding_type(finding_type) for finding_type in _finding_types(record)):
        return "audit"
    return "mapping_gap"


def _blocker_subtype(record: Dict[str, Any], item: Dict[str, Any]) -> str:
    if bool(item.get("audit_only")):
        if _has_invalid_record_family(record):
            return "invalid_family_blocker"
        return "hidden_family_blocker"
    blocker_kind = _blocker_kind(record, item)
    if blocker_kind == "mapping_conflict":
        return "mapping_conflict"
    return "rule_gap"


def _section_id_for_item(item: Dict[str, Any]) -> str:
    if bool(item.get("audit_only")):
        return "audit"
    if str(item.get("blocker_kind") or "").strip() == "mapping_conflict":
        return "mapping_conflict_resolution"
    return "mapping_gap_resolution"


def _section_cta_kind(section_id: str) -> str:
    return {
        "mapping_gap_resolution": "reprocess_pending",
        "mapping_conflict_resolution": "read_only",
        "audit": "read_only",
    }.get(str(section_id or "").strip(), "read_only")


def _build_section_payload(section_id: str, items: list[Dict[str, Any]]) -> Dict[str, Any]:
    title = {
        "mapping_gap_resolution": "待映射补全",
        "mapping_conflict_resolution": "待映射冲突",
        "audit": "审计只读",
    }.get(section_id, "待处理")
    return {
        "section_id": section_id,
        "title": title,
        "count": len(items),
        "cta_kind": _section_cta_kind(section_id),
        "items": items,
    }


def _build_backlog_payload(items: list[Dict[str, Any]]) -> Dict[str, Any]:
    sections: dict[str, list[Dict[str, Any]]] = {
        "mapping_gap_resolution": [],
        "mapping_conflict_resolution": [],
        "audit": [],
    }
    for item in items:
        section_id = _section_id_for_item(item)
        sections.setdefault(section_id, []).append(item)
    section_views = [
        _build_section_payload("mapping_gap_resolution", sections["mapping_gap_resolution"]),
        _build_section_payload("mapping_conflict_resolution", sections["mapping_conflict_resolution"]),
        _build_section_payload("audit", sections["audit"]),
    ]
    summary = {
        "actionable_count": len(sections["mapping_gap_resolution"]) + len(sections["mapping_conflict_resolution"]),
        "mapping_gap_count": len(sections["mapping_gap_resolution"]),
        "mapping_conflict_count": len(sections["mapping_conflict_resolution"]),
        "audit_count": len(sections["audit"]),
    }
    total_count = len(items)
    return {
        "sections": section_views,
        "summary": summary,
        "returned_count": total_count,
        "total_count": total_count,
        "truncated": False,
    }


class MappingService:
    """Encapsulates mapping backlog analysis and rule management helpers."""

    def __init__(self, *, repository: PipelineRepository | None = None, store=None) -> None:
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository

    def build_mapping_work_item(self, record: Dict[str, Any]) -> Dict[str, Any]:
        payload = _build_record_mapping_payload(record)
        unsafe_business_labels = _raw_business_label_candidates(record, payload)
        analysis = analyze_mapping_candidates(payload, mapping_entries=self.repository.list_mapping_entries())
        raw_recommended_rule = analysis.get("recommended_rule")
        if raw_recommended_rule is None:
            recommended_rule = {}
        elif not isinstance(raw_recommended_rule, Mapping):
            raise ValueError("recommended_rule must be a mapping")
        else:
            recommended_rule = dict(raw_recommended_rule)
        if recommended_rule:
            recommended_rule["title"] = _mapping_rule_title(str(recommended_rule.get("rule_kind") or ""))
            recommended_rule = {
                key: _sanitize_display_text(value, unsafe_business_labels)
                for key, value in recommended_rule.items()
            }
        candidate_resolutions = []
        for item in _required_list(analysis, "candidate_resolutions"):
            if not isinstance(item, Mapping):
                raise ValueError("analysis.candidate_resolutions[*] must be objects")
            resolution = dict(item)
            candidate_resolutions.append(
                {
                    "field": str(resolution.get("field") or ""),
                    "rule_kind": str(resolution.get("rule_kind") or ""),
                    "match_field": str(resolution.get("match_field") or ""),
                    "target_field": str(resolution.get("target_field") or ""),
                    "source_name": _sanitize_display_text(resolution.get("source_name"), unsafe_business_labels),
                    "target_value": _sanitize_display_text(resolution.get("target_value"), unsafe_business_labels),
                    "label": _sanitize_display_text(resolution.get("label"), unsafe_business_labels),
                    "title": _mapping_rule_title(str(resolution.get("rule_kind") or "")),
                    "evidence_chain": _sanitize_evidence_chain(resolution, unsafe_business_labels),
                }
            )
        gap_codes = _required_text_list(analysis, "gap_codes")
        available_rule_kinds = _required_text_list(analysis, "available_rule_kinds")
        finding_types = _finding_types(record)
        finding_reason_codes = _finding_reason_codes(record)
        audit_only = _audit_only(record)
        status_detail = ""
        blocking_reason_code = ""
        if "has_conflict" in gap_codes or analysis.get("has_conflict"):
            status_detail = "存在多个映射候选结果，需要人工裁决"
            blocking_reason_code = "mapping_conflict"
        elif str(record.get("state") or "") == "mapping_conflict":
            status_detail = (
                "该记录曾是映射冲突；当前候选已变化，请先回刷确认最新状态"
                if not candidate_resolutions
                else "存在多个映射候选结果，需要人工裁决"
            )
            blocking_reason_code = "stale_mapping_conflict" if not candidate_resolutions else "mapping_conflict"
        elif "missing_type" in gap_codes:
            status_detail = "缺少类型，建议优先补转让方 -> 类型"
            blocking_reason_code = "missing_type"
        elif "missing_group" in gap_codes:
            status_detail = "缺少集团，可补转让方 -> 集团以完善映射链路"
            blocking_reason_code = "missing_group"
        elif str(record.get("state") or "") in {"pending_mapping", "mapping_conflict"}:
            status_detail = _record_status_detail(record) or "当前记录仍处于待处理状态，但并非映射规则缺口"
            if any(_is_business_resolution_finding_type(finding_type) for finding_type in finding_types) or str(record.get("state") or "") == "pending_review":
                blocking_reason_code = "business_resolution_required"
                for item in _findings(record):
                    if not isinstance(item, dict):
                        continue
                    template_issue = _mapping_template_issue(
                        str(item.get("message") or "").strip(),
                        _finding_evidence(item),
                    )
                    if template_issue is None:
                        continue
                    blocking_reason_code, template_message = template_issue
                    if status_detail.startswith("entity_type_mapping_file not found:") or status_detail == str(item.get("message") or "").strip():
                        status_detail = template_message
                    break
            else:
                if not gap_codes:
                    gap_codes = ["non_mapping_blocker"]
                blocking_reason_code = "non_mapping_blocker"
        item_state = "mapping_conflict" if analysis.get("has_conflict") else str(record.get("state") or "")
        blocker_kind = _blocker_kind(record, {"state": item_state, "blocking_reason_code": blocking_reason_code, "audit_only": audit_only, "has_conflict": analysis.get("has_conflict")})
        blocker_subtype = _blocker_subtype(record, {"blocking_reason_code": blocking_reason_code, "audit_only": audit_only, "has_conflict": analysis.get("has_conflict")})
        resolved_business_id = _business_id(record)
        evidence_codes = list(dict.fromkeys([
            *list(gap_codes),
            *sorted(finding_reason_codes),
            *([_canonical_evidence_code(blocking_reason_code)] if blocking_reason_code else []),
        ]))
        return {
            "record_id": str(record.get("record_id") or ""),
            "revision_id": int(record.get("revision_id") or record.get("latest_revision_id") or 0),
            "project_code": str(record.get("project_code") or payload.get("项目编号") or ""),
            "project_name": str(record.get("project_name") or payload.get("项目名称") or ""),
            "exchange_code": _normalize_exchange_code(record.get("exchange") or payload.get("交易所") or ""),
            "exchange_label": _normalize_exchange_label(record.get("exchange") or payload.get("交易所") or ""),
            "created_at": str(record.get("updated_at") or ""),
            "state": item_state,
            "status_label": _status_label(item_state),
            "status_detail": status_detail,
            "record_family": _record_family(record),
            "business_id": resolved_business_id,
            "business_label": _safe_business_label(record),
            "project_type_label": _safe_business_label(record),
            "source_name": _sanitize_display_text(analysis.get("company_name"), unsafe_business_labels),
            "current_group": _sanitize_display_text(analysis.get("current_group"), unsafe_business_labels),
            "current_type": _sanitize_display_text(analysis.get("current_type"), unsafe_business_labels),
            "resolved_group": _sanitize_display_text(analysis.get("resolved_group"), unsafe_business_labels),
            "resolved_type": _sanitize_display_text(analysis.get("resolved_type"), unsafe_business_labels),
            "gap_codes": gap_codes,
            "evidence_codes": evidence_codes,
            "blocking_reason_code": blocking_reason_code,
            "blocker_kind": blocker_kind,
            "blocker_subtype": blocker_subtype,
            "actionable": not audit_only and blocker_kind in {"mapping_gap", "mapping_conflict"},
            "audit_only": audit_only,
            "recommended_rule": recommended_rule,
            "available_rule_kinds": available_rule_kinds,
            "candidate_resolutions": candidate_resolutions,
            "has_conflict": bool(analysis.get("has_conflict")),
        }

    def list_pending_mappings(self) -> Dict[str, Any]:
        items = [self.build_mapping_work_item(record) for record in self.find_pending_mapping_records()]
        items = [item for item in items if _owns_mapping_backlog(item)]
        return _build_backlog_payload(items)

    def enrich_mapping_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(entry)
        source_name = str(enriched.get("source_name") or enriched.get("company_name") or "").strip()
        enriched.pop("company_name", None)
        metadata = _entry_metadata(enriched)
        match_field = str(metadata.get("match_field") or "transferor").strip()
        target_field = str(metadata.get("target_field") or ("group_name" if str(enriched.get("group_name") or "").strip() else "source_type")).strip()
        target_value = (
            str(enriched.get("group_name") or "").strip()
            if target_field == "group_name"
            else str(enriched.get("source_type") or "").strip()
        )
        rule_meta = _mapping_rule_metadata(match_field=match_field, target_field=target_field)
        enriched.update(
            {
                "source_name": source_name,
                "match_field": match_field,
                "target_field": target_field,
                "target_value": target_value,
                "notes": str(metadata.get("notes") or "").strip(),
                **rule_meta,
            }
        )
        return enriched

    def list_mapping_entries(self) -> list[Dict[str, Any]]:
        return [self.enrich_mapping_entry(item) for item in self.repository.list_mapping_entries()]

    def get_mapping_entry(self, *, entry_id: str) -> Dict[str, Any]:
        return self.enrich_mapping_entry(self.repository.get_mapping_entry(entry_id=entry_id))

    def find_records_for_mapping_refresh(self, *, match_field: str, source_name: str) -> list[Dict[str, Any]]:
        records = self.repository.iter_latest_records(
            states=["ready", "pending_mapping", "mapping_conflict"],
            limit=5000,
            sort="recent",
        )
        return [
            record
            for record in records
            if _record_matches_mapping_source(record, match_field=match_field, source_name=source_name)
        ]

    def find_records_for_mapping_refresh_specs(self, specs: list[Dict[str, str]]) -> list[Dict[str, Any]]:
        if not isinstance(specs, list) or not specs:
            raise ValueError("specs must be a non-empty list of mapping refresh specs")
        affected_records: list[Dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValueError("specs entries must be objects")
            match_field = spec.get("match_field")
            source_name = spec.get("source_name")
            if not isinstance(match_field, str) or not match_field.strip():
                raise ValueError("match_field must be a non-empty string")
            if not isinstance(source_name, str) or not source_name.strip():
                raise ValueError("source_name must be a non-empty string")
            match_field = match_field.strip()
            source_name = source_name.strip()
            for record in self.find_records_for_mapping_refresh(match_field=match_field, source_name=source_name):
                record_id = str(record.get("record_id") or "").strip()
                if not record_id:
                    raise ValueError("record_id is required for mapping refresh records")
                if record_id in seen_record_ids:
                    continue
                seen_record_ids.add(record_id)
                affected_records.append(record)
        return affected_records

    def find_pending_mapping_records(self) -> list[Dict[str, Any]]:
        return self.repository.iter_latest_records(
            states=["pending_mapping", "mapping_conflict"],
            limit=5000,
            sort="recent",
        )

    def find_existing_mapping_entry(
        self,
        *,
        source_name: str,
        match_field: str,
        target_field: str,
        exclude_entry_id: str = "",
    ) -> Dict[str, Any] | None:
        normalized_source = _normalize_match_text(source_name)
        if not normalized_source:
            return None
        normalized_exclude_id = str(exclude_entry_id or "").strip()
        for entry in self.repository.list_mapping_entries():
            if normalized_exclude_id and str(entry.get("entry_id") or "").strip() == normalized_exclude_id:
                continue
            metadata = _entry_metadata(entry)
            if _normalize_match_text(entry.get("company_name")) != normalized_source:
                continue
            if str(metadata.get("match_field") or "transferor").strip() != str(match_field or "").strip():
                continue
            if str(metadata.get("target_field") or "").strip() != str(target_field or "").strip():
                continue
            return dict(entry)
        return None

    def preview_mapping_upsert(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_mapping_payload(payload)
        _validate_mapping_payload(normalized)
        current_entry_id = str(payload.get("entry_id") or "").strip()
        current_entry = self.get_mapping_entry(entry_id=current_entry_id) if current_entry_id else None
        source_name = normalized["source_name"]
        match_field = normalized["match_field"]
        target_field = normalized["target_field"]
        rule_kind = normalized["rule_kind"]
        group_name = normalized["group_name"]
        source_type = normalized["source_type"]
        target_value = group_name if target_field == "group_name" else source_type
        existing_entry = self.find_existing_mapping_entry(
            source_name=source_name,
            match_field=match_field,
            target_field=target_field,
            exclude_entry_id=current_entry_id,
        )
        affected_specs: list[Dict[str, str]] = []
        if current_entry:
            affected_specs.append(
                {
                    "match_field": str(current_entry.get("match_field") or "").strip(),
                    "source_name": str(current_entry.get("source_name") or "").strip(),
                }
            )
        affected_specs.append(
            {
                "match_field": match_field,
                "source_name": source_name,
            }
        )
        affected_records = self.find_records_for_mapping_refresh_specs(affected_specs)
        affected_pending_count = sum(
            1
            for item in affected_records
            if str(item.get("state") or "") in {"pending_mapping", "mapping_conflict"}
        )

        mode = "create"
        conflict = False
        existing_entry_view: Dict[str, Any] = self.enrich_mapping_entry(existing_entry) if existing_entry else {}
        if current_entry is not None:
            current_key = (
                str(current_entry.get("match_field") or "").strip(),
                str(current_entry.get("target_field") or "").strip(),
                _normalize_match_text(current_entry.get("source_name")),
            )
            next_key = (match_field, target_field, _normalize_match_text(source_name))
            mode = "update"
            existing_entry_view = dict(current_entry)
            if existing_entry is not None:
                mode = "overwrite"
                conflict = True
                existing_entry_view = self.enrich_mapping_entry(existing_entry)
            elif current_key == next_key:
                mode = "update"
        elif existing_entry is not None:
            existing_target = (
                str(existing_entry.get("group_name") or "").strip()
                if target_field == "group_name"
                else str(existing_entry.get("source_type") or "").strip()
            )
            mode = "update" if existing_target == target_value else "overwrite"
            conflict = mode == "overwrite"

        return {
            "conflict": conflict,
            "mode": mode,
            "existing_entry": existing_entry_view,
            "affected_count": len(affected_records),
            "affected_pending_count": affected_pending_count,
            "match_field": match_field,
            "target_field": target_field,
            "target_value": target_value,
            "source_name": source_name,
            "rule_kind": rule_kind,
            **_mapping_rule_metadata(match_field=match_field, target_field=target_field),
            **(_mapping_scope_miss_payload(source_name=source_name, match_field=match_field) if not affected_records else {"scope_miss": False}),
        }

    def resolve_mapping_conflict(
        self,
        payload: Dict[str, Any],
        *,
        upsert_mapping: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        record_id = _parse_text(payload.get("record_id"), field_name="record_id")
        raw_resolution = payload.get("selected_resolution")
        if raw_resolution is not None and not isinstance(raw_resolution, Mapping):
            raise ValueError("selected_resolution must be an object")
        resolution = {} if raw_resolution is None else dict(raw_resolution)
        if not record_id:
            raise ValueError("record_id is required")
        if not resolution:
            raise ValueError("selected_resolution is required")
        notes = _parse_text(payload.get("notes"), field_name="notes") or _parse_text(
            resolution.get("notes"),
            field_name="notes",
        )
        save_payload = {
            "source_name": _parse_text(resolution.get("source_name"), field_name="source_name"),
            "match_field": _parse_text(resolution.get("match_field"), field_name="match_field"),
            "target_field": _parse_text(resolution.get("target_field"), field_name="target_field"),
            "rule_kind": _parse_text(resolution.get("rule_kind"), field_name="rule_kind"),
            "target_value": _parse_text(resolution.get("target_value"), field_name="target_value"),
            "notes": notes,
            "authoritative": True,
            "confirm_overwrite": True,
            "resolution_record_id": record_id,
            "resolution_source": "mapping_conflict",
        }
        response = dict(upsert_mapping(save_payload))
        response["record_id"] = record_id
        response["resolution_mode"] = "rule_saved_and_refresh_started" if response.get("job_id") else "rule_saved_without_refresh"
        response["resolution"] = {
            "field": _parse_text(resolution.get("field"), field_name="field"),
            "rule_kind": save_payload["rule_kind"],
            "source_name": save_payload["source_name"],
            "target_value": save_payload["target_value"],
        }
        return response

    def select_business_re_evaluation_items(self, payload: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
        request = normalize_mapping_record_selection_request(payload)
        requested_record_ids = {
            value
            for value in request["record_ids"]
        }
        business_items: list[Dict[str, Any]] = []
        for record in self.repository.iter_latest_records(
            states=["pending_review", "pending_mapping", "mapping_conflict"],
            limit=5000,
            sort="recent",
        ):
            if not _needs_business_re_evaluation(record):
                continue
            business_items.append(self.build_mapping_work_item(record))
        if requested_record_ids:
            business_items = [item for item in business_items if str(item.get("record_id") or "") in requested_record_ids]
        return business_items
