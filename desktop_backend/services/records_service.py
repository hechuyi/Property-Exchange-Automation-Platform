"""Record query service for list/scope operations."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Dict

from peap.export_evidence_policy import export_evidence_verdict_accepted
from peap.export_projection import ExportProjectionError, project_canonical_record_to_export_payload
from peap.failed_record_supersession import (
    SUPERSEDABLE_RECORD_STATES,
    build_superseding_record_index,
    is_superseded_failed_record,
)
from peap.output_contract import get_output_columns_for_kind
from peap.projection_registry import resolve_projection_profile
from peap.streaming_export import ordered_export_headers
from peap_core.pipeline_state_contracts import BROWSABLE_RECORD_STATES

from ..domain.field_missing_ack import missing_fields_hash, normalize_missing_fields
from ..domain.normalizers import (
    coerce_limit as _coerce_limit,
)
from ..domain.normalizers import (
    normalize_exchange_code as _normalize_exchange_code,
)
from ..domain.normalizers import (
    normalize_exchange_label as _normalize_exchange_label,
)
from ..domain.normalizers import (
    normalize_project_type_code as _normalize_project_type_code,
)
from ..domain.normalizers import (
    normalize_project_type_label as _normalize_project_type_label,
)
from ..domain.normalizers import (
    normalize_record_states as _normalize_record_states,
)
from ..domain.record_projection import (
    build_mixed_record_display_values as _build_mixed_record_display_values,
)
from ..domain.record_projection import (
    build_record_display_values as _build_record_display_values,
)
from ..domain.record_projection import (
    build_record_evidence_verdict as _build_record_evidence_verdict,
)
from ..domain.record_projection import (
    build_record_top_level_fields as _build_record_top_level_fields,
)
from ..domain.record_projection import (
    mixed_record_display_columns as _mixed_record_display_columns,
)
from ..domain.record_projection import (
    record_artifact_legacy_fields_from_verdict as _record_artifact_legacy_fields_from_verdict,
)
from ..domain.record_projection import (
    record_status_detail as _record_status_detail,
)
from ..domain.record_projection import (
    record_status_label as _record_status_label,
)
from ..domain.record_projection import (
    resolve_record_artifact_path as _resolve_record_artifact_path,
)
from ..record_scope import normalize_record_scope, record_scope_to_dict, resolve_scope_business_ids
from ..repositories import PipelineRepository

UNKNOWN_BUSINESS_LABEL = "未识别项目类型"
CANONICAL_EXPORT_BLOCKED_STATUS_DETAIL = "导出必填字段缺失，暂不能进入导出"


def _record_findings(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_findings = record.get("findings")
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        raise ValueError("findings must be a list")
    findings: list[Dict[str, Any]] = []
    for item in raw_findings:
        if not isinstance(item, Mapping):
            raise ValueError("findings items must be objects")
        findings.append(dict(item))
    return findings


def _finding_evidence(item: Mapping[str, Any]) -> Dict[str, Any]:
    if "evidence" not in item or item.get("evidence") is None:
        return {}
    evidence = item.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("findings[*].evidence must be an object")
    return dict(evidence)


def _unsafe_business_label_candidates(record: Dict[str, Any], values: Dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for item in _record_findings(record):
        evidence = _finding_evidence(item)
        for key in ("raw_business_label", "business_label"):
            text = str(evidence.get(key) or "").strip()
            if text:
                candidates.add(text)
    if not candidates:
        return candidates
    for value in (
        record.get("raw_business_label"),
        record.get("business_label"),
        record.get("project_type"),
        values.get("项目类型"),
        values.get("业务"),
    ):
        text = str(value or "").strip()
        if text:
            candidates.add(text)
    return candidates


def _sanitize_display_text(value: Any, unsafe_values: set[str], *, fallback: str = "") -> str:
    text = str(value or "").strip()
    if text and text in unsafe_values:
        return fallback
    return text


def _sanitize_display_values(values: Dict[str, Any], unsafe_values: set[str]) -> Dict[str, Any]:
    sanitized = dict(values)
    for key in ("项目类型", "业务"):
        if key in sanitized:
            sanitized[key] = _sanitize_display_text(
                sanitized.get(key),
                unsafe_values,
                fallback=UNKNOWN_BUSINESS_LABEL,
            )
    return sanitized


def _record_business_id(record: Dict[str, Any]) -> str:
    direct = str(record.get("business_id") or "").strip()
    if direct:
        return direct
    if "canonical_record" not in record or record.get("canonical_record") is None:
        return ""
    canonical_record = record.get("canonical_record")
    if not isinstance(canonical_record, Mapping):
        raise ValueError("canonical_record must be an object")
    raw_business_identity = canonical_record.get("business_identity")
    if raw_business_identity is not None and not isinstance(raw_business_identity, Mapping):
        raise ValueError("canonical_record.business_identity must be an object")
    business_identity = {} if raw_business_identity is None else dict(raw_business_identity)
    nested = str(business_identity.get("business_id") or "").strip()
    if nested:
        return nested
    raw_source_identity = canonical_record.get("source_identity")
    if raw_source_identity is not None and not isinstance(raw_source_identity, Mapping):
        raise ValueError("canonical_record.source_identity must be an object")
    source_identity = {} if raw_source_identity is None else dict(raw_source_identity)
    source_business_id = str(source_identity.get("business_id") or "").strip()
    if source_business_id:
        return source_business_id
    return ""


def _project_kind_for_business(record_family: str, business_id: str) -> str | None:
    if not business_id or business_id == "all":
        return None
    profile = resolve_projection_profile(record_family, business_id)
    return profile.output_kind if profile is not None else None


def _canonical_record_mapping(record: Dict[str, Any]) -> Dict[str, Any]:
    if "canonical_record" not in record or record.get("canonical_record") is None:
        return {}
    canonical_record = record.get("canonical_record")
    if not isinstance(canonical_record, Mapping):
        raise ValueError("canonical_record must be an object")
    return dict(canonical_record)


def _canonical_fields_mapping(canonical_record: Mapping[str, Any]) -> Dict[str, Any]:
    if "canonical_fields" not in canonical_record or canonical_record.get("canonical_fields") is None:
        return {}
    canonical_fields = canonical_record.get("canonical_fields")
    if not isinstance(canonical_fields, Mapping):
        raise ValueError("canonical_record.canonical_fields must be an object")
    return dict(canonical_fields)


def scope_business_ids(scope) -> list[str]:
    normalized_scope = normalize_record_scope(record_scope_to_dict(scope))
    return resolve_scope_business_ids(normalized_scope)


def normalize_request_scope(
    payload: Dict[str, Any] | None,
    *,
    require_explicit_scope: bool,
):
    if payload is None:
        raw_payload = {}
    elif not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    else:
        raw_payload = dict(payload)
    if require_explicit_scope:
        if "scope" not in raw_payload:
            raise ValueError("scope is required")
        scope_input = raw_payload.get("scope")
    else:
        scope_input = raw_payload.get("scope", raw_payload)
    normalized_scope = normalize_record_scope(scope_input)
    return raw_payload, normalized_scope, record_scope_to_dict(normalized_scope)


def _state_counts(rows: list[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "").strip()
        if not state:
            continue
        counts[state] = counts.get(state, 0) + 1
    return counts


def _missing_fields_from_findings(record: Dict[str, Any]) -> list[dict[str, str]]:
    raw_fields: list[Any] = []
    for finding in _record_findings(record):
        finding_type = str(finding.get("type") or "").strip()
        if finding_type not in {"export_field_missing", "canonical_field_missing"}:
            continue
        evidence = _finding_evidence(finding)
        missing_fields = evidence.get("missing_fields")
        if missing_fields is None:
            continue
        if not isinstance(missing_fields, list):
            raise ValueError("evidence.missing_fields must be a list")
        raw_fields.extend(missing_fields)
    return normalize_missing_fields(raw_fields)


def _field_missing_acknowledgement(record: Dict[str, Any]) -> dict[str, Any]:
    if str(record.get("state") or "").strip() != "field_missing":
        return {
            "acknowledged": False,
            "missing_fields_hash": "",
            "revision_id": int(record.get("revision_id") or 0),
            "missing_fields": [],
        }
    acknowledged_payload = record.get("acknowledged_payload_json")
    if acknowledged_payload is None:
        acknowledged_payload = {}
    elif not isinstance(acknowledged_payload, Mapping):
        raise ValueError("acknowledged_payload_json must be an object")
    field_missing_ack = acknowledged_payload.get("field_missing")
    if field_missing_ack is None:
        field_missing_ack = {}
    elif not isinstance(field_missing_ack, Mapping):
        raise ValueError("acknowledged_payload_json.field_missing must be an object")
    if "missing_fields" in field_missing_ack and field_missing_ack.get("missing_fields") is not None:
        ack_missing_fields = field_missing_ack.get("missing_fields")
        if not isinstance(ack_missing_fields, list):
            raise ValueError("acknowledged_payload_json.field_missing.missing_fields must be a list")
        missing_fields_source = ack_missing_fields
    else:
        missing_fields_source = _missing_fields_from_findings(record)
    missing_fields = normalize_missing_fields(
        missing_fields_source
    )
    resolved_hash = str(field_missing_ack.get("missing_fields_hash") or "").strip()
    if not resolved_hash and missing_fields:
        resolved_hash = missing_fields_hash(missing_fields)
    return {
        "acknowledged": bool(field_missing_ack.get("acknowledged")),
        "missing_fields_hash": resolved_hash,
        "revision_id": int(field_missing_ack.get("revision_id") or record.get("revision_id") or 0),
        "missing_fields": missing_fields,
    }


def _has_source_artifact_problem(record: Dict[str, Any]) -> bool:
    problem_types = {"source_artifact_invalid", "source_artifact_missing"}
    if str(record.get("last_error_type") or "").strip() in problem_types:
        return True
    for item in _record_findings(record):
        if str(item.get("type") or "").strip() in problem_types:
            return True
    return False


def _record_attention(record: Dict[str, Any], ack: Dict[str, Any]) -> dict[str, Any]:
    state = str(record.get("state") or "").strip()
    if state != "field_missing":
        return {"requires_attention": False, "suppressed": False, "reason": ""}
    if _has_source_artifact_problem(record):
        return {"requires_attention": True, "suppressed": False, "reason": "source_artifact_unavailable"}
    if bool(ack.get("acknowledged")):
        return {"requires_attention": False, "suppressed": True, "reason": "acknowledged"}
    return {"requires_attention": True, "suppressed": False, "reason": "field_missing"}


def _canonical_export_projection_ready(record: Dict[str, Any]) -> bool:
    canonical_record = _canonical_record_mapping(record)
    if not canonical_record:
        return False
    canonical_fields = _canonical_fields_mapping(canonical_record)
    if not canonical_fields:
        return False
    try:
        project_canonical_record_to_export_payload(canonical_record, fail_on_missing=True)
    except ExportProjectionError:
        return False
    return True


class RecordsService:
    """Own record-list filtering, pagination, and display projection."""

    def __init__(
        self,
        *,
        repository: PipelineRepository | None = None,
        store=None,
        db_path: str,
        managed_artifact_roots: tuple[str, ...] = (),
    ) -> None:
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository
        self.db_path = db_path
        self._configured_artifact_roots = tuple(str(item or "").strip() for item in managed_artifact_roots if str(item or "").strip())

    def _managed_artifact_roots(self) -> tuple[str, ...]:
        data_dir = os.path.dirname(os.path.abspath(self.db_path))
        app_home = os.path.dirname(data_dir)
        roots = [*self._configured_artifact_roots, os.path.join(app_home, "archive")]
        return tuple(dict.fromkeys(os.path.abspath(root) for root in roots if root))

    def row_from_record(
        self,
        record: Dict[str, Any],
        *,
        values: Dict[str, Any] | None = None,
        local_artifact_path: str | None = None,
    ) -> Dict[str, Any]:
        raw_display_values = values if values is not None else _build_record_display_values(record, project_kind=None)
        unsafe_business_labels = _unsafe_business_label_candidates(record, raw_display_values)
        display_values = _sanitize_display_values(raw_display_values, unsafe_business_labels)
        artifact_path = (
            _resolve_record_artifact_path(record, managed_roots=self._managed_artifact_roots())
            if local_artifact_path is None
            else local_artifact_path
        )
        evidence_verdict = _build_record_evidence_verdict(
            record,
            managed_roots=self._managed_artifact_roots(),
            managed_provenance_path=artifact_path,
        )
        legacy_artifact_fields = _record_artifact_legacy_fields_from_verdict(evidence_verdict)
        top_level_fields = _build_record_top_level_fields(record)
        record_state = str(record.get("state") or "").strip()
        export_projection_ready = (
            _canonical_export_projection_ready(record)
            if record_state == "ready"
            else False
        )
        canonical_ready = record_state == "ready"
        evidence_status = str(evidence_verdict.get("status") or "").strip()
        export_eligible = export_projection_ready and export_evidence_verdict_accepted(evidence_verdict)
        field_missing_ack = _field_missing_acknowledgement(record)
        status_detail = _record_status_detail(record)
        if record_state == "ready" and not export_projection_ready and not status_detail:
            status_detail = CANONICAL_EXPORT_BLOCKED_STATUS_DETAIL
        return {
            "record_id": record["record_id"],
            "business_id": _record_business_id(record),
            "business_label": _sanitize_display_text(
                record.get("raw_business_label"),
                unsafe_business_labels,
                fallback=UNKNOWN_BUSINESS_LABEL,
            ),
            "project_code": record["project_code"],
            "project_name": record["project_name"],
            "project_type_code": _normalize_project_type_code(
                display_values.get("项目类型") or record.get("project_type")
            ),
            "project_type_label": _normalize_project_type_label(
                display_values.get("项目类型") or record.get("project_type")
            ),
            "exchange_code": _normalize_exchange_code(display_values.get("交易所") or record.get("exchange")),
            "exchange_label": _normalize_exchange_label(str(display_values.get("交易所") or record.get("exchange") or "")),
            "listing_date": str(record.get("listing_date") or ""),
            "state": record_state,
            "status_label": _record_status_label(record),
            "status_detail": status_detail,
            "field_missing_acknowledgement": field_missing_ack,
            "attention": _record_attention(record, field_missing_ack),
            "canonical_ready": canonical_ready,
            "evidence_status": evidence_status,
            "export_eligible": export_eligible,
            "exportable": export_eligible,
            "archive_path": record["archive_path"],
            "source_file": record["source_file"],
            "artifact_status": legacy_artifact_fields["artifact_status"],
            "artifact_missing_reason": legacy_artifact_fields["artifact_missing_reason"],
            "evidence_verdict": evidence_verdict,
            "has_local_artifact": legacy_artifact_fields["has_local_artifact"],
            "local_artifact_name": legacy_artifact_fields["local_artifact_name"],
            "updated_at": record.get("updated_at", ""),
            "seller": top_level_fields["seller"],
            "price": top_level_fields["price"],
            "display_values": display_values,
        }

    def list_records(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        _, normalized_scope, scope = normalize_request_scope(payload, require_explicit_scope=False)
        requested_state = str(normalized_scope.state or "").strip().lower()
        states = _normalize_record_states(requested_state)
        if states is None:
            states = list(BROWSABLE_RECORD_STATES)
        page = _coerce_limit(normalized_scope.page, default=1, maximum=9999)
        page_size = _coerce_limit(normalized_scope.page_size, default=50, maximum=200)
        keyword = str(normalized_scope.keyword or "").strip().lower()
        exchange_code = _normalize_exchange_code(normalized_scope.exchange)
        date_from = str(normalized_scope.date_from or "").strip()
        date_to = str(normalized_scope.date_to or "").strip()
        requested_business_id = str(normalized_scope.business_id or "").strip()
        project_kind = _project_kind_for_business(normalized_scope.record_family, requested_business_id)
        use_mixed_display = project_kind is None and requested_business_id in {"", "all"}
        superseding_index = None
        if states is None or any(state in SUPERSEDABLE_RECORD_STATES for state in states):
            superseding_index = build_superseding_record_index(
                self.repository.iter_latest_records(record_family=normalized_scope.record_family, sort="recent")
            )
        records = self.repository.iter_latest_records(
            states=states,
            date_from=date_from or None,
            date_to=date_to or None,
            record_family=normalized_scope.record_family,
            sort="recent",
        )
        filtered_rows: list[Dict[str, Any]] = []
        display_rows: list[Dict[str, Any]] = []
        for record in records:
            record_state = str(record.get("state") or "").strip()
            if states is not None and record_state not in states:
                continue
            if superseding_index is not None and is_superseded_failed_record(record, superseding_index):
                continue
            record_business_id = _record_business_id(record)
            if requested_business_id not in {"", "all"} and record_business_id != requested_business_id:
                continue
            search_values = _build_record_display_values(record, project_kind=project_kind)
            values = _build_mixed_record_display_values(record) if use_mixed_display else search_values
            record_exchange_code = _normalize_exchange_code(
                values.get("交易所") or search_values.get("交易所") or record.get("exchange") or ""
            )
            if exchange_code not in {"", "all"} and record_exchange_code != exchange_code:
                continue
            search_blob = " ".join(
                [
                    str(record.get("project_code") or ""),
                    str(record.get("project_name") or ""),
                    str(record.get("project_type") or ""),
                    str(record.get("exchange") or ""),
                    str(record.get("listing_date") or ""),
                    str(record.get("state") or ""),
                ]
                + [str(search_values.get(column) or "") for column in search_values]
                + [str(values.get(column) or "") for column in values]
            ).lower()
            if keyword and keyword not in search_blob:
                continue
            local_artifact_path = _resolve_record_artifact_path(record, managed_roots=self._managed_artifact_roots())
            display_rows.append(values)
            filtered_rows.append(
                self.row_from_record(record, values=values, local_artifact_path=local_artifact_path)
            )
        total_count = len(filtered_rows)
        offset = max(0, (page - 1) * page_size)
        rows = filtered_rows[offset : offset + page_size]
        page_count = (total_count + page_size - 1) // page_size if total_count else 0
        filtered_state_counts = _state_counts(filtered_rows)
        page_state_counts = _state_counts(rows)
        if project_kind:
            ordered_columns = list(get_output_columns_for_kind(project_kind))
        elif use_mixed_display:
            ordered_columns = [
                column
                for column in _mixed_record_display_columns(normalized_scope.record_family)
                if any(row.get(column) not in (None, "") for row in display_rows)
            ]
        else:
            ordered_columns = ordered_export_headers(display_rows)
        return {
            "db_path": self.db_path,
            "scope": scope,
            "display_columns": ordered_columns,
            "keyword": keyword,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "page_count": page_count,
            "has_more": page < page_count,
            "summary": {
                "filtered_state_counts": filtered_state_counts,
                "page_state_counts": page_state_counts,
                "total_count": total_count,
                "visible_count": len(rows),
                "page": page,
                "page_size": page_size,
                "page_count": page_count,
            },
            "rows": rows,
        }
