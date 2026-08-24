"""Read-only projection for records that require manual review."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Dict

from peap.failed_record_supersession import (
    build_superseding_record_index,
    is_superseded_failed_record,
)
from peap_core.family_catalog import get_family_descriptor

from ..domain.normalizers import normalize_exchange_code, normalize_exchange_label, status_label
from ..domain.record_projection import (
    build_record_evidence_verdict,
    record_artifact_legacy_fields_from_verdict,
    resolve_record_artifact_path,
)
from ..repositories import PipelineRepository
from ..review_problem_contract import PROBLEM_KINDS, PROBLEM_LABELS, build_review_problems_resource

UNKNOWN_RAW_BUSINESS_LABELS = {"", "未知", "unknown", "unrecognized", "未识别", "无法识别"}
SOURCE_ARTIFACT_FINDING_TYPES = {"source_artifact_invalid", "source_artifact_missing"}
UNRESOLVED_PROJECT_TYPE_LABEL = "项目类型未识别"
PROJECT_TYPE_TEMPLATE_MISSING_LABEL = "项目类型映射模板缺失"


def _optional_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return dict(value)


def _optional_nested_mapping(payload: Mapping[str, Any], key: str, *, field_name: str) -> dict[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return dict(value)


def _findings(record: Dict[str, Any]) -> list[dict[str, Any]]:
    raw_findings = record.get("findings")
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        raise TypeError("record.findings must be a list")
    findings: list[dict[str, Any]] = []
    for item in raw_findings:
        if not isinstance(item, Mapping):
            raise TypeError("record.findings items must be objects")
        findings.append(dict(item))
    return findings


def _finding_type(finding: dict[str, Any]) -> str:
    return str(finding.get("type") or "").strip()


def _evidence(finding: dict[str, Any]) -> dict[str, Any]:
    return _optional_mapping(finding.get("evidence"), field_name="finding.evidence")


def _reason_code(finding: dict[str, Any]) -> str:
    evidence = _evidence(finding)
    return str(evidence.get("reason_code") or finding.get("reason_code") or "").strip()


def _severity(finding: dict[str, Any]) -> str:
    severity = str(finding.get("severity") or "warning").strip()
    return "warning" if severity == "warn" else severity or "warning"


def _record_family_projection(record: Dict[str, Any]) -> tuple[str, str]:
    canonical_record = _optional_mapping(record.get("canonical_record"), field_name="canonical_record")
    family_candidates = [record.get("record_family"), canonical_record.get("record_family")]
    business_identity = _optional_nested_mapping(
        canonical_record,
        "business_identity",
        field_name="canonical_record.business_identity",
    )
    family_candidates.append(business_identity.get("record_family"))
    source_identity = _optional_nested_mapping(
        canonical_record,
        "source_identity",
        field_name="canonical_record.source_identity",
    )
    family_candidates.append(source_identity.get("record_family"))

    raw_family = ""
    for candidate in family_candidates:
        raw_family = str(candidate or "").strip()
        if raw_family:
            break
    if not raw_family:
        return "", ""
    try:
        descriptor = get_family_descriptor(raw_family)
    except KeyError:
        return raw_family, raw_family
    family_id = str(descriptor.family_id or "").strip() or raw_family
    family_label = str(descriptor.canonical_label or "").strip() or family_id
    return family_id, family_label


def _payload(record: Dict[str, Any]) -> dict[str, Any]:
    for key in ("postprocess_payload", "canonical_record", "parser_payload"):
        value = record.get(key)
        if value is None:
            continue
        payload = _optional_mapping(value, field_name=key)
        if payload:
            return payload
    return {}


def _raw_business_label(record: Dict[str, Any], findings: list[dict[str, Any]]) -> str:
    for finding in findings:
        evidence = _evidence(finding)
        value = str(evidence.get("raw_business_label") or evidence.get("business_label") or "").strip()
        if value:
            return value
    payload = _payload(record)
    return str(
        record.get("raw_business_label")
        or record.get("business_label")
        or payload.get("项目类型")
        or payload.get("业务类型")
        or record.get("project_type")
        or ""
    ).strip()


def _business_id(record: Dict[str, Any]) -> str:
    direct = str(record.get("business_id") or "").strip()
    if direct:
        return direct
    canonical = _optional_mapping(record.get("canonical_record"), field_name="canonical_record")
    business_identity = _optional_nested_mapping(
        canonical,
        "business_identity",
        field_name="canonical_record.business_identity",
    )
    identity_business_id = str(business_identity.get("business_id") or "").strip()
    if identity_business_id:
        return identity_business_id
    source_identity = _optional_nested_mapping(
        canonical,
        "source_identity",
        field_name="canonical_record.source_identity",
    )
    return str(source_identity.get("business_id") or "").strip()


def _safe_field_ref(item: Any) -> dict[str, str]:
    if isinstance(item, dict):
        field = str(item.get("field") or item.get("canonical_field") or item.get("export_field") or item.get("name") or "").strip()
        label = str(item.get("label") or item.get("name") or field or "").strip()
    else:
        field = ""
        label = str(item or "").strip()
    result = {"field": field, "label": label}
    return result if field or label else {}


def _safe_missing_fields(record: Dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        if _finding_type(finding) not in {"export_field_missing", "canonical_field_missing"}:
            continue
        evidence = _evidence(finding)
        missing_fields = evidence.get("missing_fields")
        if missing_fields is None:
            continue
        if not isinstance(missing_fields, list):
            raise TypeError("finding.evidence.missing_fields must be a list")
        for item in missing_fields:
            value = _safe_field_ref(item)
            key = (value.get("field", ""), value.get("label", ""))
            if value and key not in seen:
                fields.append(value)
                seen.add(key)
    return fields


def _missing_field_labels(record: Dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    return [item["label"] or item["field"] for item in _safe_missing_fields(record, findings) if item.get("label") or item.get("field")]


def _safe_related_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for finding in findings:
        evidence = _evidence(finding)
        missing_fields = evidence.get("missing_fields")
        if missing_fields is None:
            missing_fields = []
        elif not isinstance(missing_fields, list):
            raise TypeError("finding.evidence.missing_fields must be a list")
        fields = [_safe_field_ref(item) for item in missing_fields]
        fields = [item for item in fields if item]
        summary: dict[str, Any] = {
            "finding_type": _finding_type(finding),
            "severity": _severity(finding),
            "reason_code": _reason_code(finding),
        }
        if fields:
            summary["fields"] = fields
        summaries.append(summary)
    return summaries


def _artifact_evidence_verdict_summary(verdict: dict[str, Any]) -> dict[str, str]:
    return {
        "status": str(verdict.get("status") or "").strip(),
        "reason_code": str(verdict.get("reason_code") or "").strip(),
    }


def _first_evidence_value(findings: list[dict[str, Any]], *keys: str) -> str:
    for finding in findings:
        evidence = _evidence(finding)
        for key in keys:
            value = str(evidence.get(key) or "").strip()
            if value:
                return value
    return ""


def _classify(record: Dict[str, Any], findings: list[dict[str, Any]]) -> tuple[str, str]:
    state = str(record.get("state") or "").strip()
    finding_types = {_finding_type(item) for item in findings}
    reason_codes = {_reason_code(item) for item in findings if _reason_code(item)}
    last_error_type = str(record.get("last_error_type") or "").strip()
    artifact_reasons = (finding_types | reason_codes | {last_error_type}) & SOURCE_ARTIFACT_FINDING_TYPES
    if artifact_reasons:
        return "source_artifact_unavailable", sorted(artifact_reasons)[0]
    if state == "field_missing" or finding_types.intersection({"export_field_missing", "canonical_field_missing"}):
        return "export_fields_missing", "field_missing"
    if reason_codes.intersection({"deal_capital_increase_missing_investor", "deal_capital_increase_missing_investor_amount"}):
        return "deal_data_incomplete", sorted(reason_codes.intersection({"deal_capital_increase_missing_investor", "deal_capital_increase_missing_investor_amount"}))[0]
    family_reason_codes = {"record_family_conflict", "business_family_conflict", "invalid_record_family"}
    if any(str(_evidence(item).get("blocker_kind") or "").strip() == "record_family_resolution" for item in findings) or reason_codes.intersection(family_reason_codes):
        return "business_family_unresolved", sorted(reason_codes.intersection(family_reason_codes) or {"record_family_resolution"})[0]
    raw_business_label = _raw_business_label(record, findings).casefold()
    has_raw_business_label = bool(raw_business_label)
    has_business_resolution_blocker = any(
        _finding_type(item) == "business_resolution_required"
        and not _reason_code(item)
        and str(_evidence(item).get("blocker_kind") or "").strip() == "business_resolution"
        for item in findings
    )
    if (
        reason_codes.intersection({"unrecognized_business", "project_type_mapping_template_missing", "business_resolution_required"})
        or has_business_resolution_blocker
        or (has_raw_business_label and raw_business_label in UNKNOWN_RAW_BUSINESS_LABELS)
    ):
        return "project_type_unresolved", sorted(reason_codes.intersection({"unrecognized_business", "project_type_mapping_template_missing", "business_resolution_required"}) or {"business_resolution_required"})[0]
    for finding in findings:
        if str(finding.get("message") or "").startswith("entity_type_mapping_file not found:"):
            return "project_type_unresolved", "project_type_mapping_template_missing"
    return "manual_review_unclassified", "manual_review_unclassified"


def _business_explanation(kind: str, reason_code: str, record: Dict[str, Any], findings: list[dict[str, Any]]) -> tuple[str, str]:
    missing = "、".join(_missing_field_labels(record, findings)) or "必填字段"
    if kind == "project_type_unresolved" and reason_code == "project_type_mapping_template_missing":
        return (
            "项目类型映射模板缺失。这是配置问题，不是单条记录内容错误。",
            "请查看业务目录配置或项目类型映射模板。",
        )
    if kind == "project_type_unresolved":
        return (
            f"{UNRESOLVED_PROJECT_TYPE_LABEL}。系统目录中没有可用匹配，当前不能判断应使用哪套业务规则和导出表头。",
            "请查看来源页面项目类型、业务目录配置或项目类型映射模板。",
        )
    if kind == "business_family_unresolved":
        payload_record_family = ""
        context_record_family = ""
        for finding in findings:
            evidence = _evidence(finding)
            payload_record_family = payload_record_family or str(evidence.get("payload_record_family") or evidence.get("record_family") or "").strip()
            context_record_family = context_record_family or str(evidence.get("context_record_family") or "").strip()
        return (
            f"记录标记为“{payload_record_family or '未明确'}”，但当前处理上下文是“{context_record_family or '未明确'}”。系统不能自动决定按挂牌还是成交处理。",
            "请查看记录标记业务大类与当前处理业务大类。",
        )
    if kind == "deal_data_incomplete":
        return (
            "这是一条成交增资记录，但未识别到可自动入库的非汇总投资方和金额。请查看官方原页是否包含投资方明细。",
            "请查看官方原页是否包含投资方明细。",
        )
    if kind == "source_artifact_unavailable":
        if reason_code == "source_artifact_invalid":
            return (
                "本地文件不是完整原网页，可能只是交易所接口壳或未渲染页面。系统不能用它支撑浏览、复核或导出。",
                "请重新下载真实渲染页；如果交易所已不可访问，请从备份恢复完整原网页。",
            )
        return (
            "数据库记录指向的原网页文件不存在或不可读取。系统不能用旧下载记录证明当前文件可用。",
            "请重新下载该记录，或从备份恢复对应原网页。",
        )
    if kind == "export_fields_missing":
        return (
            f"缺少“{missing}”。确认提示只降低噪音，不会补字段，也不会允许导出。",
            "请查看 canonical/export 必填字段来源。",
        )
    return (
        "系统记录了复核阻断，但未能归入已知类型。请联系维护人员查看原始处理日志和来源文件。",
        "请联系维护人员查看原始处理日志和来源文件。",
    )


def _updated_at_sort_value(row: Dict[str, Any]) -> str:
    return str(row.get("updated_at") or "")


def _record_updated_date(record: Dict[str, Any]) -> str:
    return str(record.get("updated_at") or "")[:10]


def _review_business_label(kind: str, reason_code: str, record: Dict[str, Any]) -> str:
    business_label = str(record.get("business_label") or "").strip()
    if business_label:
        return business_label
    if kind == "project_type_unresolved" and reason_code == "project_type_mapping_template_missing":
        return PROJECT_TYPE_TEMPLATE_MISSING_LABEL
    if kind == "project_type_unresolved":
        return UNRESOLVED_PROJECT_TYPE_LABEL
    return ""


def _matches_updated_at_date(record: Dict[str, Any], *, date_from: str, date_to: str) -> bool:
    updated_date = _record_updated_date(record)
    if date_from and (not updated_date or updated_date < date_from):
        return False
    if date_to and (not updated_date or updated_date > date_to):
        return False
    return True


class ReviewProblemService:
    def __init__(
        self,
        *,
        repository: PipelineRepository | None = None,
        store=None,
        managed_artifact_roots: tuple[str, ...] = (),
    ) -> None:
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository
        self._managed_artifact_roots = tuple(
            os.path.abspath(str(item or "").strip())
            for item in managed_artifact_roots
            if str(item or "").strip()
        )

    def row_from_record(self, record: Dict[str, Any]) -> dict[str, Any]:
        findings = _findings(record)
        kind, reason_code = _classify(record, findings)
        explanation, suggested_review = _business_explanation(kind, reason_code, record, findings)
        payload = _payload(record)
        family, family_label = _record_family_projection(record)
        exchange_code = normalize_exchange_code(record.get("exchange") or payload.get("交易所") or "")
        review_business_label = _review_business_label(kind, reason_code, record)
        artifact_path = resolve_record_artifact_path(record, managed_roots=self._managed_artifact_roots)
        evidence_verdict = build_record_evidence_verdict(
            record,
            managed_roots=self._managed_artifact_roots,
            managed_provenance_path=artifact_path,
        )
        legacy_artifact_fields = record_artifact_legacy_fields_from_verdict(evidence_verdict)
        related_findings = _safe_related_findings(findings)
        evidence = {
            "finding_type": _finding_type(findings[0]) if findings else "",
            "reason_code": reason_code,
            "missing_fields": _safe_missing_fields(record, findings),
            "payload_record_family": _first_evidence_value(findings, "payload_record_family", "record_family"),
            "context_record_family": _first_evidence_value(findings, "context_record_family"),
            "investor_detail_result": _first_evidence_value(findings, "investor_detail_result", "investor_detail_status"),
            "related_finding_count": len(findings),
            "related_findings": related_findings,
            "artifact_evidence_verdict": _artifact_evidence_verdict_summary(evidence_verdict),
        }
        return {
            "problem_id": f"{record.get('record_id') or ''}:{record.get('revision_id') or record.get('latest_revision_id') or 0}:{kind}:{reason_code}",
            "record_id": str(record.get("record_id") or ""),
            "revision_id": int(record.get("revision_id") or record.get("latest_revision_id") or 0),
            "state": str(record.get("state") or ""),
            "status_label": status_label(str(record.get("state") or "")),
            "problem_kind": kind,
            "problem_label": PROBLEM_LABELS[kind],
            "reason_code": reason_code,
            "severity": _severity(findings[0]) if findings else "warning",
            "business_explanation": explanation,
            "business_impact": "该记录暂不能进入导出。",
            "suggested_review": suggested_review,
            "record_family": family,
            "record_family_label": family_label,
            "business_id": _business_id(record),
            "business_label": str(record.get("business_label") or "").strip(),
            "raw_business_label": review_business_label,
            "project_code": str(record.get("project_code") or payload.get("项目编号") or ""),
            "project_name": str(record.get("project_name") or payload.get("项目名称") or ""),
            "exchange_code": exchange_code,
            "exchange_label": normalize_exchange_label(record.get("exchange") or payload.get("交易所") or ""),
            "source_file": str(record.get("source_file") or ""),
            "archive_path": str(record.get("archive_path") or ""),
            "artifact_status": legacy_artifact_fields["artifact_status"],
            "artifact_missing_reason": legacy_artifact_fields["artifact_missing_reason"],
            "evidence_verdict": evidence_verdict,
            "has_local_artifact": legacy_artifact_fields["has_local_artifact"],
            "local_artifact_name": legacy_artifact_fields["local_artifact_name"],
            "updated_at": str(record.get("updated_at") or ""),
            "evidence": evidence,
            "actions": {
                "primary_action_kind": "none",
                "primary_action_enabled": False,
                "available_actions": [],
            },
        }

    def list_review_problems(self, query: dict[str, Any]) -> dict[str, Any]:
        states = ["pending_review", "field_missing"] if query["state"] == "all" else [query["state"]]
        records = self.repository.iter_latest_records(
            states=states,
            record_family=None if query["record_family"] == "all" else query["record_family"],
            sort="recent",
        )
        superseding_index = build_superseding_record_index(
            self.repository.iter_latest_records(
                record_family=None if query["record_family"] == "all" else query["record_family"],
                sort="recent",
            )
        )
        records = [
            record for record in records
            if _matches_updated_at_date(record, date_from=query["date_from"], date_to=query["date_to"])
            and not is_superseded_failed_record(record, superseding_index)
        ]
        rows = [self.row_from_record(record) for record in records]
        if query["problem_kind"] != "all":
            rows = [row for row in rows if row["problem_kind"] == query["problem_kind"]]
        if query["business_id"] not in {"", "all"}:
            rows = [row for row in rows if row["business_id"] == query["business_id"]]
        if query["exchange"] not in {"", "all"}:
            exchange = normalize_exchange_code(query["exchange"])
            rows = [row for row in rows if row["exchange_code"] == exchange]
        keyword = str(query["keyword"] or "").strip().casefold()
        if keyword:
            rows = [
                row for row in rows
                if keyword in " ".join(str(row.get(key) or "") for key in ("project_code", "project_name", "source_file", "archive_path", "raw_business_label")).casefold()
            ]
        rows.sort(key=lambda row: (_updated_at_sort_value(row), str(row.get("record_id") or "")), reverse=False)
        rows = sorted(rows, key=lambda row: str(row.get("record_id") or ""))
        rows = sorted(rows, key=lambda row: _updated_at_sort_value(row), reverse=True)
        total_count = len(rows)
        start = (int(query["page"]) - 1) * int(query["page_size"])
        page_rows = rows[start:start + int(query["page_size"])]
        resource = build_review_problems_resource(page_rows, total_count=total_count, page=query["page"], page_size=query["page_size"])
        summary = resource["summary"]
        for kind in PROBLEM_KINDS:
            summary[f"{kind}_count"] = sum(1 for row in rows if row["problem_kind"] == kind)
        return resource
