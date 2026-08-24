"""Single-item ingest pipeline for downloaded HTML snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping

from peap_core.error_contracts import PipelineFailure
from peap_core.family_catalog import get_family_descriptor
from peap_core.record_identity import build_source_identity_payload
from peap_core.record_state_policy import classify_record_state
from peap_core.source_catalog import canonical_source_code

from .business_classifier import BusinessClassification, classify_record_business
from .cbex_deal_source_policy import is_cbex_deal_non_detail_page
from .constants import STATUS_LISTED
from .io_utils import read_text_with_fallback
from .pipeline_payload_projection import (
    build_export_extras_from_payload,
    normalize_pipeline_payload,
)
from .standard_model import build_standard_project
from .streaming_models import IngestedRecord, ItemSavedPayload, PostProcessFinding, RecordState
from .streaming_postprocess import (
    RecordPostprocessContext,
    apply_postprocess_context,
    normalize_record_payload,
    resolve_project_type_label,
    run_record_postprocess,
)
from .streaming_store import StreamingStore
from .submission_layout import resolve_submission_snapshot_target, validate_archive_member_path

LISTING_DATE_FIELDS = ("挂牌开始日期", "预披露开始日期", "披露开始日期", "信息披露起始日期")
DEAL_DATE_FIELDS = (
    "deal_date",
    "dealDate",
    "成交日期",
    "CJRQ",
    "contractSignTime",
    "contract_sign_time",
    "合同签订日期",
    "签约日期",
)
DEAL_COLLECTION_DATE_FIELDS = (
    "collection_date",
    "collectionDate",
    "采集日期",
    "fbsj",
    "publishDate",
)
IMPUTED_DEAL_DATE_REMARK_SUFFIX = "成交日期缺失，按采集日填列"
DEAL_EFFECTIVE_DATE_MISSING_FINDING = "deal_effective_date_missing"
LEGACY_NON_PRODUCT_SOURCE_IDS = frozenset({"public_resource"})


def _json_script_payload(*, html: str, script_id: str) -> Dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    script_node = soup.find("script", id=script_id)
    if script_node is None:
        return {}
    script_text = script_node.string if script_node.string is not None else script_node.get_text(" ", strip=False)
    parsed = json.loads(script_text)
    if isinstance(parsed, dict):
        return dict(parsed)
    return {}


def _load_deal_sidecar_payload(file_path: str | None) -> Dict[str, Any]:
    if not file_path:
        return {}
    json_path = os.path.splitext(str(file_path))[0] + ".json"
    if os.path.islink(json_path):
        raise ValueError(f"deal_sidecar_symlink: {json_path}")
    if not os.path.isfile(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"deal_sidecar_invalid_json: {json_path}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"deal_sidecar_unreadable: {json_path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"deal_sidecar_invalid_schema: {json_path}: root must be an object")
    return dict(parsed)


def _json_script_tag_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _append_in_memory_deal_sidecar_scripts(html: str, parse_metadata: Dict[str, Any]) -> str:
    scripts = ""
    if parse_metadata.get("deal_metadata") and not _json_script_payload(html=html, script_id="deal_metadata"):
        scripts += (
            '<script id="deal_metadata" type="application/json">'
            + _json_script_tag_text(parse_metadata["deal_metadata"])
            + "</script>"
        )
    if parse_metadata.get("deal_detail") and not _json_script_payload(html=html, script_id="deal_detail"):
        scripts += (
            '<script id="deal_detail" type="application/json">'
            + _json_script_tag_text(parse_metadata["deal_detail"])
            + "</script>"
        )
    if not scripts:
        return html
    marker = "</body>"
    lower_html = html.lower()
    index = lower_html.rfind(marker)
    if index >= 0:
        return html[:index] + scripts + html[index:]
    return html + scripts


def _extract_default_parse_metadata(html: str, *, file_path: str | None = None) -> Dict[str, Any]:
    sidecar = _load_deal_sidecar_payload(file_path)
    sidecar_metadata = sidecar.get("metadata") if isinstance(sidecar.get("metadata"), dict) else {}
    sidecar_detail = sidecar.get("detail_payload") if isinstance(sidecar.get("detail_payload"), dict) else {}
    metadata = _json_script_payload(html=html, script_id="deal_metadata")
    if sidecar and file_path:
        from .parsing import (
            _deal_sidecar_is_trusted,
            _explicit_html_project_names,
            _normalize_identity_text,
            _sidecar_has_archive_integrity,
            _sidecar_integrity_matches,
        )

        sidecar_family = str(
            sidecar_metadata.get("record_family") or sidecar.get("record_family") or ""
        ).strip().lower()
        sidecar_is_trusted = _sidecar_has_archive_integrity(sidecar) and _sidecar_integrity_matches(
            file_path,
            sidecar,
        )
        if sidecar_family == "deal":
            sidecar_is_trusted = _deal_sidecar_is_trusted(
                file_path=file_path,
                content=html,
                payload=sidecar,
            )
            if sidecar_is_trusted:
                html_names = {
                    _normalize_identity_text(value)
                    for value in _explicit_html_project_names(html)
                    if _normalize_identity_text(value)
                }
                sidecar_name = _normalize_identity_text(sidecar_metadata.get("project_name"))
                if sidecar_name and html_names and sidecar_name not in html_names:
                    sidecar_metadata = dict(sidecar_metadata)
                    sidecar_metadata.pop("project_name", None)
        if not sidecar_is_trusted:
            sidecar_metadata = {}
            sidecar_detail = {}
    if sidecar_metadata:
        metadata = {**sidecar_metadata, **metadata}
    source_url = str(
        metadata.get("source_url")
        or metadata.get("detail_url")
        or metadata.get("page_url")
        or ""
    ).strip()
    source_id = str(metadata.get("source_id") or "").strip()
    record_family = str(metadata.get("record_family") or "").strip()
    payload: Dict[str, Any] = {}
    if source_url:
        payload["source_url"] = source_url
    if source_id:
        payload["source_id"] = source_id
    if record_family:
        payload["record_family"] = record_family
    if metadata:
        payload["deal_metadata"] = metadata
    if sidecar_detail:
        payload["deal_detail"] = dict(sidecar_detail)
    return payload


def _thaw_registry_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_registry_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_registry_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_registry_value(item) for item in value]
    return value


def _normalize_registry_metadata_date(value: Any) -> str:
    text = str(value or "").strip()
    if " " in text:
        text = text.split(" ", 1)[0]
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text.replace("年", "/").replace("月", "/").replace("日", "")
    text = text.replace("-", "/").replace(".", "/")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}/{text[4:6]}/{text[6:8]}"
    return text


def _build_registry_parse_context(*, file_path: str, content: str):
    from bs4 import BeautifulSoup

    from peap_core import DecodedDocument
    from peap_parsers.source_classifier import classify_decoded_document

    parse_metadata = _extract_default_parse_metadata(content, file_path=file_path)
    parse_content = _append_in_memory_deal_sidecar_scripts(content, parse_metadata)
    soup = BeautifulSoup(parse_content, "html.parser")
    document = DecodedDocument(
        snapshot_id=os.path.basename(file_path) or "snapshot",
        document_kind="html",
        primary_text=soup.get_text(" ", strip=True),
        dom=str(soup),
        metadata=parse_metadata,
        decoder_version="streaming_ingest/default/v1",
    )
    source_match = classify_decoded_document(document)
    if source_match.source_id in LEGACY_NON_PRODUCT_SOURCE_IDS:
        raise PipelineFailure(
            code="unsupported_product_source",
            component="streaming_ingest",
            stage="parse",
            recoverability="permanent",
            message=f"unsupported_product_source: {source_match.source_id}",
            context={"source_id": source_match.source_id, "file_path": file_path},
        )
    return document, source_match, parse_metadata


def _build_registry_parse_payload(*, file_path: str, content: str) -> Dict[str, Any]:
    from peap_parsers.base import ParserContext
    from peap_parsers.builtin_registry import build_builtin_registry
    from peap_parsers.family_runtime import parse_document_with_registry

    document, source_match, parse_metadata = _build_registry_parse_context(
        file_path=file_path,
        content=content,
    )
    page_result = parse_document_with_registry(
        document=document,
        source_match=source_match,
        registry=build_builtin_registry(),
        context=ParserContext(source_file=file_path),
    )
    payload: Dict[str, Any] = {}
    for fact in page_result.facts:
        if not isinstance(fact, Mapping):
            continue
        fact_payload = dict(fact)
        field_name = str(fact_payload.get("field") or "").strip()
        if not field_name:
            continue
        value = fact_payload.get("value")
        if value in (None, ""):
            continue
        payload[field_name] = _thaw_registry_value(value)
    diagnostics = [item.to_dict() for item in page_result.diagnostics]
    if diagnostics:
        payload["parse_diagnostics"] = diagnostics

    deal_metadata = parse_metadata.get("deal_metadata")
    if isinstance(deal_metadata, dict):
        for key in (
            "record_family",
            "source_id",
            "source_url",
            "business_id",
            "project_code",
            "project_name",
            "deal_date",
            "deal_date_basis",
            "deal_date_is_imputed",
            "deal_date_remark_suffix",
            "remark_suffix",
            "collection_date",
        ):
            value = deal_metadata.get(key)
            if value in (None, ""):
                continue
            if key == "source_id":
                value = _canonical_source_id(value)
            elif key in {"deal_date", "collection_date"}:
                value = _normalize_registry_metadata_date(value)
            payload[str(key)] = value

    payload.setdefault("source_id", source_match.source_id)
    payload.setdefault("record_family", source_match.page_kind)
    return payload


def _canonical_source_id(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        return str(canonical_source_code(text) or text).strip()
    return ""


def _canonical_exchange_value(value: Any) -> str:
    text = str(value or "").strip()
    return str(
        canonical_source_code(
            text,
            allow_substring=False,
            allowed_source_ids={"guangdong"},
        )
        or text
    ).strip()


def _first_non_empty(payload: Dict[str, Any], fields: Iterable[str]) -> str:
    value, _field_name = _first_non_empty_with_field(payload, fields)
    return value


def _first_non_empty_with_field(payload: Dict[str, Any], fields: Iterable[str]) -> tuple[str, str]:
    for field_name in fields:
        value = str(payload.get(field_name) or "").strip()
        if value:
            return value, str(field_name)
    return "", ""


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _payload_marks_deal_date_as_collection_date(payload: Dict[str, Any]) -> bool:
    basis = str(payload.get("deal_date_basis") or payload.get("dealDateBasis") or "").strip()
    return _coerce_bool(payload.get("deal_date_is_imputed") or payload.get("dealDateIsImputed")) and basis in {
        "",
        "collection_date",
    }


def _merge_record_payloads(
    *,
    parser_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
) -> Dict[str, Any]:
    merged = _require_object_payload(parser_payload, field="parser_payload")
    postprocessed = _require_object_payload(postprocess_payload, field="postprocess_payload")
    for key, value in postprocessed.items():
        if value in (None, ""):
            continue
        merged[str(key)] = value
    return merged


def _require_object_payload(value: Any, *, field: str) -> Dict[str, Any]:
    if value is None:
        raise ValueError(f"{field} must be an object, got null")
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object, got {type(value).__name__}")
    return dict(value)


def _parse_diagnostic_findings(parser_payload: Mapping[str, Any]) -> list[PostProcessFinding]:
    raw_diagnostics = parser_payload.get("parse_diagnostics")
    if raw_diagnostics in (None, ""):
        return []
    if not isinstance(raw_diagnostics, list):
        raise TypeError("parser_payload.parse_diagnostics must be a list")
    findings: list[PostProcessFinding] = []
    for index, item in enumerate(raw_diagnostics):
        if not isinstance(item, Mapping):
            raise TypeError(f"parser_payload.parse_diagnostics[{index}] must be an object")
        diagnostic = dict(item)
        diagnostic_type = str(diagnostic.get("type") or "").strip()
        if not diagnostic_type:
            raise ValueError(f"parser_payload.parse_diagnostics[{index}].type is required")
        severity = str(diagnostic.get("severity") or "warn").strip().lower()
        if severity not in {"info", "warn", "error"}:
            raise ValueError(f"parser_payload.parse_diagnostics[{index}].severity is invalid: {severity}")
        findings.append(
            PostProcessFinding(
                severity=severity,  # type: ignore[arg-type]
                type=diagnostic_type,
                message=str(diagnostic.get("message") or diagnostic_type).strip(),
                evidence={
                    "stage": str(diagnostic.get("stage") or "parse").strip(),
                    "recoverability": str(diagnostic.get("recoverability") or "").strip(),
                    "evidence_refs": _thaw_registry_value(diagnostic.get("evidence_refs") or []),
                },
            )
        )
    return findings


def _authoritative_record_family(*values: Any) -> str:
    for value in values:
        family = str(value or "").strip()
        if not family:
            continue
        return get_family_descriptor(family).family_id
    return ""


def _has_record_family_authority_context(*values: Any) -> bool:
    return any(str(value or "").strip() for value in values)


def _record_family_authority_missing_finding(
    *,
    stage: str,
    classified_record_family: str,
    parser_payload: Mapping[str, Any],
    source_identity: Mapping[str, Any] | None = None,
) -> PostProcessFinding:
    evidence: Dict[str, Any] = {
        "stage": str(stage or "").strip(),
        "classified_record_family": str(classified_record_family or "").strip(),
        "parser_record_family": str(parser_payload.get("record_family") or "").strip(),
    }
    if source_identity is not None:
        evidence["source_identity_record_family"] = str(source_identity.get("record_family") or "").strip()
    return PostProcessFinding(
        severity="warn",
        type="record_family_authority_missing",
        message="record_family is missing from authoritative ingest metadata; classifier default cannot make record ready",
        evidence=evidence,
    )


def _append_record_family_authority_missing_finding(
    findings: Iterable[PostProcessFinding],
    *,
    stage: str,
    authoritative_record_family: str,
    classified_record_family: str,
    parser_payload: Mapping[str, Any],
    source_identity: Mapping[str, Any] | None = None,
) -> list[PostProcessFinding]:
    findings_list = list(findings)
    if authoritative_record_family:
        return findings_list
    if any(str(item.type or "") == "record_family_authority_missing" for item in findings_list):
        return findings_list
    findings_list.append(
        _record_family_authority_missing_finding(
            stage=stage,
            classified_record_family=classified_record_family,
            parser_payload=parser_payload,
            source_identity=source_identity,
        )
    )
    findings_list.append(
        PostProcessFinding(
            severity="warn",
            type="business_resolution_required",
            message="record_family authority is missing; manual review is required before export",
            evidence={
                "reason": "record_family_authority_missing",
                "stage": str(stage or "").strip(),
                "classified_record_family": str(classified_record_family or "").strip(),
            },
        )
    )
    return findings_list


def _finding_evidence_payload(finding: PostProcessFinding) -> Dict[str, Any]:
    evidence = finding.evidence
    if not isinstance(evidence, Mapping):
        raise TypeError(f"finding.evidence must be a mapping, got {type(evidence).__name__}")
    return dict(evidence)


def _resolve_effective_listing_date(
    *,
    listing_date: str,
    source_identity: Dict[str, Any],
    parser_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    record_family: str = "",
) -> str:
    checked_parser_payload = _require_object_payload(parser_payload, field="parser_payload")
    checked_postprocess_payload = _require_object_payload(postprocess_payload, field="postprocess_payload")
    merged_payload = _merge_record_payloads(
        parser_payload=checked_parser_payload,
        postprocess_payload=checked_postprocess_payload,
    )
    standard = build_standard_project(merged_payload)
    normalized_family = str(record_family or source_identity.get("record_family") or "").strip().lower()
    if normalized_family == "deal":
        imputed_collection_date = ""
        for payload in (checked_postprocess_payload, checked_parser_payload, merged_payload):
            for field_name in DEAL_DATE_FIELDS:
                value = str(payload.get(field_name) or "").strip()
                if value:
                    if _payload_marks_deal_date_as_collection_date(payload):
                        imputed_collection_date = imputed_collection_date or value
                        continue
                    return value
        for payload in (checked_postprocess_payload, checked_parser_payload, merged_payload):
            for field_name in DEAL_COLLECTION_DATE_FIELDS:
                value = str(payload.get(field_name) or "").strip()
                if value:
                    return value
        return (
            str(source_identity.get("deal_date") or "").strip()
            or str(source_identity.get("collection_date") or "").strip()
            or imputed_collection_date
        )
    return (
        str(listing_date or "").strip()
        or str(source_identity.get("listing_date") or "").strip()
        or str(standard.start_date or "").strip()
    )


def _append_deal_effective_date_missing_finding(
    findings: Iterable[PostProcessFinding],
    *,
    record_family: str,
    effective_listing_date: str,
    project_code: str,
    parser_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    source_identity: Dict[str, Any],
) -> List[PostProcessFinding]:
    findings_list = list(findings)
    if str(record_family or "").strip().lower() != "deal":
        return findings_list
    if str(effective_listing_date or "").strip():
        return findings_list
    if any(str(item.type or "") == DEAL_EFFECTIVE_DATE_MISSING_FINDING for item in findings_list):
        return findings_list
    findings_list.append(
        PostProcessFinding(
            severity="warn",
            type=DEAL_EFFECTIVE_DATE_MISSING_FINDING,
            message="deal record has no real deal_date or collection_date; listing/start/end date fallback suppressed",
            evidence={
                "project_code": str(project_code or "").strip(),
                "listing_date_candidate": str(source_identity.get("listing_date") or "").strip(),
                "parser_listing_date": _first_non_empty(parser_payload, LISTING_DATE_FIELDS),
                "standard_start_date": str(build_standard_project(_merge_record_payloads(
                    parser_payload=parser_payload,
                    postprocess_payload=postprocess_payload,
                )).start_date or "").strip(),
                "standard_end_date": str(build_standard_project(_merge_record_payloads(
                    parser_payload=parser_payload,
                    postprocess_payload=postprocess_payload,
                )).end_date or "").strip(),
            },
        )
    )
    return findings_list


def _default_parse_file(file_path: str) -> Dict[str, Any]:
    from peap.parsing import parse_file

    # The registry path below is intentionally fast, but it must preserve the
    # same evidence boundary as ``parse_file``.  Otherwise a symlink can be
    # consumed before the legacy parser's source-file guard is reached.
    source_path = str(file_path)
    if os.path.islink(source_path) or (
        os.path.lexists(source_path) and not os.path.isfile(source_path)
    ):
        raise ValueError(
            "source_snapshot_invalid: source snapshot must be a regular "
            f"non-symlink file: {source_path}"
        )

    read_result = read_text_with_fallback(file_path)
    if read_result is not None:
        registry_payload = _build_registry_parse_payload(
            file_path=file_path,
            content=read_result.content,
        )
        if registry_payload:
            normalized = dict(registry_payload)
            project_code = str(
                normalized.get("项目编号")
                or normalized.get("project_code")
                or ""
            ).strip()
            project_name = str(
                normalized.get("项目名称")
                or normalized.get("project_name")
                or ""
            ).strip()
            project_type = str(
                normalized.get("项目类型")
                or normalized.get("business_type")
                or normalized.get("project_type")
                or ""
            ).strip()
            status = str(normalized.get("项目状态") or normalized.get("status") or "").strip()
            exchange = str(normalized.get("交易所") or normalized.get("exchange") or "").strip()
            return normalize_pipeline_payload(
                normalized,
                standard_payload=build_standard_project(normalized).to_standard_dict(),
                project_code=project_code,
                project_name=project_name,
                project_type=project_type,
                status=status,
                exchange=exchange,
            )

    parsed = parse_file(file_path)
    parsed_data = _require_object_payload(getattr(parsed, "data", None), field="data")
    return normalize_pipeline_payload(
        parsed_data,
        standard_payload=parsed.standard_record.to_standard_dict(),
        project_code=parsed.project_code,
        project_name=parsed.project_name,
        project_type=parsed.project_type,
        status=parsed.status,
        exchange=parsed.exchange,
    )


def _is_skip_parse(exc: Exception) -> bool:
    return exc.__class__.__name__ == "SkipParse"


def _is_invalid_cbex_deal_source_page(*, source_url: str, source_id: str, record_family: str, business_id: str = "") -> bool:
    if str(record_family or "").strip().lower() != "deal":
        return False
    normalized_source = _canonical_source_id(source_id)
    if normalized_source != "cbex":
        return False
    return is_cbex_deal_non_detail_page(source_url, business_id=business_id)


def _classify_failure(exc: Exception) -> tuple[str, str]:
    message = str(exc or "").strip()
    if ":" in message:
        code, _, _ = message.partition(":")
        normalized = code.strip()
        if normalized:
            return normalized, message
    return "parse_failed", message


def _resolve_candidate_tokens(*, project_code: str, project_id: str, page_url: str) -> list[str]:
    tokens: list[str] = []
    if project_code:
        tokens.append(f"project_code:{project_code.upper()}")
    if project_id:
        tokens.append(f"project_id:{project_id.upper()}")
    if page_url:
        tokens.append(f"page_url:{page_url}")
    return tokens


def _candidate_tokens_payload(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{field}[{index}] must be text")
        out.append(item.strip())
    return out


def _build_canonical_record_payload(
    *,
    record_id: str,
    project_code: str,
    project_name: str,
    project_type: str,
    exchange: str,
    listing_date: str,
    source_identity: Dict[str, Any],
    parser_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    findings: Iterable[PostProcessFinding],
    classification: BusinessClassification | None = None,
) -> Dict[str, Any]:
    merged_payload = _merge_record_payloads(
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
    )
    standard = build_standard_project(merged_payload)
    record_family = str(source_identity.get("record_family") or "listing").strip().lower() or "listing"
    resolved_project_type = (
        str(getattr(classification, "project_type_label", "") or "").strip()
        or str(project_type or "").strip()
        or str(standard.business_type or "").strip()
    )
    effective_listing_date = str(listing_date or "").strip()
    if record_family != "deal":
        effective_listing_date = effective_listing_date or str(standard.start_date or "").strip()
    seller = str(standard.seller or "").strip()
    source_type = str(standard.source_type or "").strip()
    group_name = str(standard.group_name or "").strip()
    status = str(standard.status or "").strip()
    if not status and record_family == "listing":
        status = STATUS_LISTED
    price = standard.price
    listing_times = postprocess_payload.get("挂牌次数")
    if listing_times in (None, ""):
        listing_times = merged_payload.get("挂牌次数")
    if listing_times in (None, ""):
        listing_times = standard.listing_times
    deal_date = _first_non_empty(
        merged_payload,
        ("deal_date", "dealDate", "成交日期", "CJRQ", "contractSignTime", "contract_sign_time"),
    )
    deal_date_basis = _first_non_empty(merged_payload, ("deal_date_basis", "dealDateBasis"))
    deal_price, deal_price_unit_hint = _first_non_empty_with_field(merged_payload, ("deal_price", "dealPrice", "CJJG", "cjjg", "tradevalue", "transactionPrice", "dealAmount", "交易价格", "交易价格（万元）", "交易价格（元）", "交易价格（亿元）", "成交金额", "成交金额（万元）", "成交金额（元）", "成交金额（亿元）", "成交价格", "成交价"))
    deal_price_unit_hint = _first_non_empty(merged_payload, ("deal_price_unit_hint", "dealPriceUnitHint", "price_unit_hint")) or deal_price_unit_hint
    valuation = _first_non_empty(merged_payload, ("valuation", "valuationValue", "DWPGZ", "PGZ", "DJPGZ", "转让标的评估值", "转让标的评估结果", "评估值"))
    reserve_price = _first_non_empty(merged_payload, ("reserve_price", "reservePrice", "ZRDJ", "ZRDANJ", "转让底价", "转让底价（万元）", "挂牌底价"))
    collection_date = _first_non_empty(merged_payload, DEAL_COLLECTION_DATE_FIELDS)
    deal_date_is_imputed_value = merged_payload.get("deal_date_is_imputed")
    if deal_date_is_imputed_value is None:
        deal_date_is_imputed_value = merged_payload.get("dealDateIsImputed")
    if isinstance(deal_date_is_imputed_value, bool):
        deal_date_is_imputed = deal_date_is_imputed_value
    else:
        bool_text = str(deal_date_is_imputed_value or "").strip().lower()
        if bool_text in {"true", "1", "yes", "y"}:
            deal_date_is_imputed = True
        elif bool_text in {"false", "0", "no", "n"}:
            deal_date_is_imputed = False
        else:
            deal_date_is_imputed = False
    if (
        record_family == "deal"
        and deal_date
        and deal_date_is_imputed
        and deal_date_basis in {"", "collection_date"}
    ):
        collection_date = collection_date or deal_date
        deal_date = ""
        deal_date_basis = "collection_date"
    if record_family == "deal" and not deal_date and collection_date:
        deal_date_basis = deal_date_basis or "collection_date"
        deal_date_is_imputed = True
    diagnostic_payload = [
        {
            "severity": str(item.severity),
            "type": str(item.type),
            "message": str(item.message),
            "evidence": _finding_evidence_payload(item),
        }
        for item in findings
    ]
    export_extras = build_export_extras_from_payload(
        merged_payload,
        record_family=str(source_identity.get("record_family") or "listing"),
        project_type=resolved_project_type,
        business_id=str(getattr(classification, "business_id", "") or "").strip(),
    )
    business_identity = {
        "project_code": project_code,
    }
    if classification is not None:
        if str(classification.business_id or "").strip():
            business_identity["business_id"] = str(classification.business_id).strip()
        if str(classification.raw_business_label or "").strip():
            business_identity["raw_business_label"] = str(classification.raw_business_label).strip()
    canonical_fields = {
        "project_code": project_code or str(standard.project_code or "").strip(),
        "project_name": project_name or str(standard.project_name or "").strip(),
        "project_type": resolved_project_type,
        "status": status,
        "exchange": exchange or str(standard.exchange or "").strip(),
        "start_date": effective_listing_date,
        "price": price,
        "seller": seller,
        "source_type": source_type,
        "group_name": group_name,
        "listing_times": listing_times,
    }
    if record_family == "deal":
        from .deal_amounts import apply_deal_price_amount_fields

        canonical_fields.update(
            {
                "deal_date": deal_date,
                "deal_date_basis": deal_date_basis or ("collection_date" if deal_date_is_imputed else "deal_date"),
                "deal_date_is_imputed": bool(deal_date_is_imputed),
                "deal_price": deal_price,
                "deal_price_unit_hint": deal_price_unit_hint,
                "valuation": valuation,
                "reserve_price": reserve_price,
            }
        )
        canonical_fields = apply_deal_price_amount_fields(canonical_fields)
        if collection_date:
            canonical_fields["collection_date"] = collection_date
    return {
        "record_id": record_id,
        "record_family": str(source_identity.get("record_family") or "listing"),
        "source_identity": dict(source_identity),
        "business_identity": business_identity,
        "canonical_fields": canonical_fields,
        "export_extras": export_extras,
        "field_provenance": {},
        "diagnostics": diagnostic_payload,
        "normalizer_version": "streaming_ingest/v1",
        "policy_state": {
            "findings": [str(item.type) for item in findings],
        },
    }


def _build_canonical_projection_payload(canonical_record: Dict[str, Any]) -> Dict[str, Any]:
    from .export_projection import project_canonical_record_to_export_payload

    raw_canonical_fields = canonical_record.get("canonical_fields")
    if raw_canonical_fields is None:
        canonical_fields = {}
    elif isinstance(raw_canonical_fields, Mapping):
        canonical_fields = dict(raw_canonical_fields)
    else:
        raise ValueError(f"canonical_fields must be an object when present, got {type(raw_canonical_fields).__name__}")

    payload, _ = project_canonical_record_to_export_payload(canonical_record, fail_on_missing=False)
    if str(canonical_record.get("record_family") or "").strip().lower() == "deal":
        basis = str(canonical_fields.get("deal_date_basis") or "").strip()
        is_imputed = str(canonical_fields.get("deal_date_is_imputed") or "").strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
        }
        collection_date = str(canonical_fields.get("collection_date") or "").strip()
        if collection_date and (basis == "collection_date" or is_imputed):
            payload.setdefault("成交日期", collection_date)
            remark = str(payload.get("备注") or "").strip()
            if IMPUTED_DEAL_DATE_REMARK_SUFFIX not in remark:
                payload["备注"] = (
                    f"{remark}；{IMPUTED_DEAL_DATE_REMARK_SUFFIX}"
                    if remark
                    else IMPUTED_DEAL_DATE_REMARK_SUFFIX
                )
    return dict(payload)


def _append_export_projection_findings(
    findings: Iterable[PostProcessFinding],
    canonical_record: Dict[str, Any],
) -> list[PostProcessFinding]:
    from .export_projection import append_export_projection_findings

    return list(append_export_projection_findings(findings, canonical_record))


def _compute_revision_hash(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _assemble_ingested_record(
    *,
    record_id: str,
    project_code: str,
    project_name: str,
    project_type: str,
    exchange: str,
    listing_date: str,
    state: RecordState,
    source_file: str,
    archive_path: str,
    parser_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    findings: Iterable[PostProcessFinding],
    source_identity: Dict[str, Any],
    record_family: str = "listing",
    classification: BusinessClassification | None = None,
) -> IngestedRecord:
    effective_source_identity = _require_object_payload(source_identity, field="source_identity")
    effective_record_family = str(
        getattr(classification, "record_family", "") or record_family or effective_source_identity.get("record_family") or "listing"
    ).strip() or "listing"
    if not str(effective_source_identity.get("record_family") or "").strip():
        effective_source_identity["record_family"] = effective_record_family
    effective_listing_date = _resolve_effective_listing_date(
        listing_date=listing_date,
        source_identity=effective_source_identity,
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
        record_family=effective_record_family,
    )
    if effective_record_family == "deal":
        effective_source_identity["listing_date"] = effective_listing_date
    elif effective_listing_date:
        effective_source_identity["listing_date"] = effective_listing_date
    if classification is not None and str(classification.business_id or "").strip():
        effective_source_identity.setdefault("business_id", str(classification.business_id).strip())
    findings_list = _append_deal_effective_date_missing_finding(
        findings,
        record_family=effective_record_family,
        effective_listing_date=effective_listing_date,
        project_code=project_code,
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
        source_identity=effective_source_identity,
    )
    canonical_record = _build_canonical_record_payload(
        record_id=record_id,
        project_code=project_code,
        project_name=project_name,
        project_type=project_type,
        exchange=exchange,
        listing_date=effective_listing_date,
        source_identity=effective_source_identity,
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
        findings=findings_list,
        classification=classification,
    )
    had_conflict = str(state or "").strip() == "conflict"
    current_state = classify_record_state(findings_list, had_conflict=had_conflict)
    if current_state.value == "ready":
        findings_list = _append_export_projection_findings(findings_list, canonical_record)
    state = classify_record_state(findings_list, had_conflict=had_conflict)
    if findings_list:
        canonical_record["diagnostics"] = [
            {
                "severity": str(item.severity),
                "type": str(item.type),
                "message": str(item.message),
                "evidence": _finding_evidence_payload(item),
            }
            for item in findings_list
        ]
        canonical_record["policy_state"] = {
            "findings": [str(item.type) for item in findings_list],
        }
    canonical_projection = _build_canonical_projection_payload(canonical_record)
    return IngestedRecord(
        record_id=record_id,
        revision_hash=_compute_revision_hash(postprocess_payload),
        project_code=project_code,
        project_name=project_name,
        project_type=project_type,
        exchange=exchange,
        listing_date=effective_listing_date,
        state=state,
        source_file=source_file,
        archive_path=archive_path,
        parser_payload=parser_payload,
        postprocess_payload=postprocess_payload,
        findings=findings_list,
        record_family=effective_record_family,
        source_identity=effective_source_identity,
        canonical_record=canonical_record,
        canonical_projection=canonical_projection,
    )


def _canonical_archive_target(
    *,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    source_file: str,
    record_family: str = "listing",
    business_id: str = "",
    source_id: str = "",
    reuse_current_conflict: bool = False,
) -> tuple[str, bool]:
    normalized_family = str(record_family or "").strip().lower()
    normalized_business_id = str(business_id or "").strip().lower()
    normalized_source_id = _canonical_source_id(source_id).lower()
    archive_project_code = str(project_code or "").strip()
    scope_parts: list[str] = []
    default_listing_family = str(get_family_descriptor("listing").family_id or "").strip().lower()
    if normalized_family == "all":
        archive_family = ""
    else:
        try:
            archive_family = str(get_family_descriptor(normalized_family).family_id or "").strip().lower()
        except KeyError:
            archive_family = normalized_family
    if archive_family and archive_family != default_listing_family:
        scope_parts.append(archive_family)
        if normalized_business_id and normalized_business_id != "all":
            scope_parts.append(normalized_business_id)
        if normalized_source_id and normalized_source_id != "all":
            scope_parts.append(normalized_source_id)
    if archive_project_code and scope_parts:
        archive_project_code = f"{archive_project_code}__{'__'.join(scope_parts)}"
    ext = os.path.splitext(os.path.abspath(source_file))[1] or ".html"
    return resolve_submission_snapshot_target(
        archive_root=archive_root,
        project_code=archive_project_code or project_code,
        project_name=project_name,
        listing_date=listing_date,
        ext=ext,
        current_path=source_file,
        reuse_current_conflict=reuse_current_conflict,
    )


def _snapshot_companion_paths(html_path: str) -> Dict[str, str]:
    html_path = os.path.abspath(html_path)
    return {
        "sidecar": os.path.splitext(html_path)[0] + ".json",
        "save_status": f"{html_path}.peap-save-status.json",
        "evidence": f"{html_path}.peap-evidence.json",
    }


def _assert_tree_has_no_symlinks(path: str, *, label: str) -> None:
    if os.path.islink(path):
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not os.path.isdir(path):
        return
    for current_dir, dirnames, filenames in os.walk(path, followlinks=False):
        for name in (*dirnames, *filenames):
            candidate = os.path.join(current_dir, name)
            if os.path.islink(candidate):
                raise ValueError(f"{label} must not contain symlinks: {candidate}")


def _validate_source_snapshot_bundle(source_file: str) -> tuple[Dict[str, str], str]:
    source_abs = os.path.abspath(source_file)
    if os.path.islink(source_abs) or not os.path.isfile(source_abs):
        raise ValueError(
            "source_snapshot_invalid: source snapshot must be a regular "
            f"non-symlink file: {source_abs}"
        )

    source_companions = _snapshot_companion_paths(source_abs)
    for companion_path in source_companions.values():
        if os.path.islink(companion_path):
            raise ValueError(
                "source_snapshot_invalid: source snapshot companion must not be "
                f"a symlink: {companion_path}"
            )
        if os.path.lexists(companion_path) and not os.path.isfile(companion_path):
            raise ValueError(
                "source_snapshot_invalid: source snapshot companion must be a "
                f"regular file: {companion_path}"
            )

    source_assets_dir = f"{os.path.splitext(source_abs)[0]}_files"
    if os.path.lexists(source_assets_dir):
        if os.path.islink(source_assets_dir):
            raise ValueError(
                "source_snapshot_invalid: source snapshot assets must not be a "
                f"symlink: {source_assets_dir}"
            )
        if not os.path.isdir(source_assets_dir):
            raise ValueError(
                "source_snapshot_invalid: source snapshot assets are not a "
                f"directory: {source_assets_dir}"
            )
        try:
            _assert_tree_has_no_symlinks(source_assets_dir, label="source snapshot assets")
        except ValueError as exc:
            raise ValueError(f"source_snapshot_invalid: {exc}") from exc
    return source_companions, source_assets_dir


def _validate_archive_bundle_members(html_path: str, archive_root: str) -> None:
    validate_archive_member_path(html_path, archive_root)
    for companion_path in _snapshot_companion_paths(html_path).values():
        validate_archive_member_path(companion_path, archive_root)
        if os.path.lexists(companion_path) and not os.path.isfile(companion_path):
            raise ValueError(f"archive companion member is not a regular file: {companion_path}")
    assets_dir = f"{os.path.splitext(os.path.abspath(html_path))[0]}_files"
    validate_archive_member_path(assets_dir, archive_root)
    if os.path.lexists(assets_dir):
        if not os.path.isdir(assets_dir):
            raise ValueError(f"archive assets member is not a directory: {assets_dir}")
        _assert_tree_has_no_symlinks(assets_dir, label="archive assets directory")


def _read_json_mapping(path: str) -> Dict[str, Any] | None:
    if os.path.islink(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _file_content_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _companion_matches_snapshot(*, companion_path: str, html_path: str, kind: str) -> bool:
    if os.path.islink(companion_path) or os.path.islink(html_path):
        return False
    if not os.path.isfile(companion_path):
        return False
    payload = _read_json_mapping(companion_path)
    if payload is None:
        return False
    expected_hash = str(payload.get("archive_content_sha256") or "").strip().removeprefix("sha256:")
    expected_bytes = payload.get("archive_content_bytes")
    if kind == "evidence" and not expected_hash:
        expected_hash = str(payload.get("content_sha256") or "").strip().removeprefix("sha256:")
    try:
        if expected_hash and _file_content_sha256(html_path).lower() != expected_hash.lower():
            return False
        if expected_bytes not in (None, "") and os.path.getsize(html_path) != int(expected_bytes):
            return False
    except (OSError, TypeError, ValueError):
        return False
    return True


def _refresh_companion_integrity(*, companion_path: str, html_path: str, kind: str) -> None:
    payload = _read_json_mapping(companion_path)
    if payload is None:
        raise ValueError(f"archive companion must be a JSON object: {companion_path}")
    digest = _file_content_sha256(html_path)
    payload["archive_content_sha256"] = digest
    payload["archive_content_bytes"] = os.path.getsize(html_path)
    if kind == "evidence" and "content_sha256" in payload:
        old_hash = str(payload.get("content_sha256") or "")
        payload["content_sha256"] = f"sha256:{digest}" if old_hash.startswith("sha256:") else digest
    temp_path = f"{companion_path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temp_path, companion_path)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def _cleanup_stale_snapshot_companions(html_path: str) -> None:
    """Keep only managed companions whose integrity binds them to this snapshot."""
    for kind, companion_path in _snapshot_companion_paths(html_path).items():
        if not os.path.isfile(companion_path):
            continue
        payload = _read_json_mapping(companion_path)
        has_integrity = payload is not None and any(
            payload.get(key) not in (None, "")
            for key in ("archive_content_sha256", "archive_content_bytes", "content_sha256")
        )
        is_current = has_integrity and _companion_matches_snapshot(
            companion_path=companion_path,
            html_path=html_path,
            kind=kind,
        )
        if not is_current:
            try:
                os.remove(companion_path)
            except FileNotFoundError:
                pass


def _bind_trusted_legacy_deal_sidecar(html_path: str) -> None:
    sidecar_path = _snapshot_companion_paths(html_path)["sidecar"]
    payload = _read_json_mapping(sidecar_path)
    if payload is None:
        return

    from .parsing import _deal_sidecar_is_trusted, _sidecar_has_archive_integrity

    if _sidecar_has_archive_integrity(payload):
        return
    read_result = read_text_with_fallback(html_path)
    if read_result is None or not _deal_sidecar_is_trusted(
        file_path=html_path,
        content=read_result.content,
        payload=payload,
    ):
        return
    _refresh_companion_integrity(
        companion_path=sidecar_path,
        html_path=html_path,
        kind="sidecar",
    )


def copy_snapshot_to_archive(
    *,
    source_file: str,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    record_family: str = "listing",
    business_id: str = "",
    source_id: str = "",
) -> tuple[str, bool]:
    source_abs = os.path.abspath(source_file)
    source_companions, source_assets_dir = _validate_source_snapshot_bundle(source_abs)
    target_file, had_conflict = _canonical_archive_target(
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        source_file=source_abs,
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    target_file = validate_archive_member_path(target_file, archive_root)
    _validate_archive_bundle_members(target_file, archive_root)
    if not had_conflict and os.path.isfile(target_file):
        return target_file, False

    target_dir = os.path.dirname(target_file)
    target_base = os.path.splitext(os.path.basename(target_file))[0]
    temp_work_dir = os.path.join(target_dir, f".{target_base}.{uuid.uuid4().hex}.tmp")
    os.makedirs(temp_work_dir, exist_ok=False)
    staged_file = os.path.join(temp_work_dir, os.path.basename(target_file))
    target_companions = _snapshot_companion_paths(target_file)
    staged_companions = _snapshot_companion_paths(staged_file)
    transferable_companions = {
        kind
        for kind, path in source_companions.items()
        if _companion_matches_snapshot(companion_path=path, html_path=source_abs, kind=kind)
    }
    target_assets_dir = f"{os.path.splitext(target_file)[0]}_files"
    staged_assets_dir = os.path.join(temp_work_dir, f"{target_base}_files")
    target_file_backup = f"{target_file}.{uuid.uuid4().hex}.bak"
    target_assets_backup_dir = f"{target_assets_dir}.{uuid.uuid4().hex}.bak"
    backed_up_file = False
    backed_up_assets = False
    installed_file = False
    installed_assets = False
    companion_backups: Dict[str, str] = {}
    installed_companions: set[str] = set()
    try:
        shutil.copy2(source_abs, staged_file)
        for kind in transferable_companions:
            shutil.copy2(source_companions[kind], staged_companions[kind])
        if os.path.isdir(source_assets_dir):
            shutil.copytree(source_assets_dir, staged_assets_dir)
            _rewrite_archived_asset_references(
                target_file=staged_file,
                source_file=source_abs,
            )
        for kind in transferable_companions:
            _refresh_companion_integrity(
                companion_path=staged_companions[kind],
                html_path=staged_file,
                kind=kind,
            )

        if os.path.isdir(staged_assets_dir):
            if os.path.isdir(target_assets_dir):
                os.replace(target_assets_dir, target_assets_backup_dir)
                backed_up_assets = True
            shutil.move(staged_assets_dir, target_assets_dir)
            installed_assets = True
        for kind, target_companion in target_companions.items():
            validate_archive_member_path(target_companion, archive_root)
            if os.path.exists(target_companion):
                backup_path = f"{target_companion}.{uuid.uuid4().hex}.bak"
                os.replace(target_companion, backup_path)
                companion_backups[kind] = backup_path
        validate_archive_member_path(target_file, archive_root)
        if os.path.exists(target_file):
            os.replace(target_file, target_file_backup)
            backed_up_file = True
        os.replace(staged_file, target_file)
        installed_file = True
        for kind in transferable_companions:
            os.replace(staged_companions[kind], target_companions[kind])
            installed_companions.add(kind)
        if backed_up_file:
            try:
                os.remove(target_file_backup)
            except OSError:
                pass
        for backup_path in companion_backups.values():
            try:
                os.remove(backup_path)
            except OSError:
                pass
        if backed_up_assets:
            shutil.rmtree(target_assets_backup_dir, ignore_errors=True)
    except Exception:
        if installed_file and os.path.isfile(target_file):
            os.remove(target_file)
        if backed_up_file and os.path.isfile(target_file_backup):
            os.replace(target_file_backup, target_file)
        for kind in installed_companions:
            target_companion = target_companions[kind]
            if os.path.isfile(target_companion):
                os.remove(target_companion)
        for kind, backup_path in companion_backups.items():
            if os.path.isfile(backup_path):
                os.replace(backup_path, target_companions[kind])
        if installed_assets and os.path.isdir(target_assets_dir):
            shutil.rmtree(target_assets_dir, ignore_errors=True)
        if backed_up_assets:
            if os.path.isdir(target_assets_backup_dir):
                os.replace(target_assets_backup_dir, target_assets_dir)
        raise
    finally:
        shutil.rmtree(temp_work_dir, ignore_errors=True)
    return target_file, had_conflict


def _discard_materialized_source_bundle(
    *,
    source_file: str,
    source_companions: Mapping[str, str],
    source_assets_dir: str,
) -> None:
    """Best-effort cleanup after a complete archive copy has been committed.

    The destination is authoritative once `copy_snapshot_to_archive()` returns.
    Cleanup deliberately happens afterward so a failure can leave duplicate
    evidence but cannot leave the canonical archive as a partial bundle.
    """

    transferable_companions = {
        path
        for kind, path in source_companions.items()
        if _companion_matches_snapshot(companion_path=path, html_path=source_file, kind=kind)
    }
    for companion_path in transferable_companions:
        try:
            os.remove(companion_path)
        except OSError:
            pass
    if os.path.isdir(source_assets_dir):
        shutil.rmtree(source_assets_dir, ignore_errors=True)
    try:
        os.remove(source_file)
    except OSError:
        pass


def materialize_snapshot_to_archive(
    *,
    source_file: str,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    record_family: str = "listing",
    business_id: str = "",
    source_id: str = "",
    preserve_source: bool = False,
    reuse_current_conflict: bool = False,
) -> tuple[str, bool]:
    source_abs = os.path.abspath(source_file)
    source_companions, source_assets_dir = _validate_source_snapshot_bundle(source_abs)
    target_file, had_conflict = _canonical_archive_target(
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        source_file=source_abs,
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
        reuse_current_conflict=reuse_current_conflict,
    )
    target_file = validate_archive_member_path(target_file, archive_root)
    _validate_archive_bundle_members(target_file, archive_root)
    if os.path.normcase(source_abs) == os.path.normcase(os.path.abspath(target_file)):
        if preserve_source and str(record_family or "").strip().lower() == "deal":
            _bind_trusted_legacy_deal_sidecar(source_abs)
        _cleanup_stale_snapshot_companions(source_abs)
        return source_abs, False
    if not had_conflict and os.path.isfile(target_file):
        return target_file, False
    if preserve_source or not _is_path_within_root(source_abs, archive_root):
        return copy_snapshot_to_archive(
            source_file=source_abs,
            archive_root=archive_root,
            project_code=project_code,
            project_name=project_name,
            listing_date=listing_date,
            record_family=record_family,
            business_id=business_id,
            source_id=source_id,
        )

    archived_path, copied_had_conflict = copy_snapshot_to_archive(
        source_file=source_abs,
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        record_family=record_family,
        business_id=business_id,
        source_id=source_id,
    )
    _discard_materialized_source_bundle(
        source_file=source_abs,
        source_companions=source_companions,
        source_assets_dir=source_assets_dir,
    )
    return archived_path, copied_had_conflict


def _is_path_within_root(path_value: str, root_value: str) -> bool:
    target = os.path.abspath(str(path_value or ""))
    root = os.path.abspath(str(root_value or ""))
    if not target or not root:
        return False
    try:
        if os.path.commonpath([target, root]) != root:
            return False
        return os.path.commonpath(
            [os.path.realpath(target), os.path.realpath(root)]
        ) == os.path.realpath(root)
    except ValueError:
        return False


def _rewrite_archived_asset_references(*, target_file: str, source_file: str) -> None:
    source_base = os.path.splitext(os.path.basename(source_file))[0]
    target_base = os.path.splitext(os.path.basename(target_file))[0]
    if not source_base or source_base == target_base:
        return
    source_ref = f"{source_base}_files/"
    target_ref = f"{target_base}_files/"

    read_result = read_text_with_fallback(target_file)
    if read_result is None or source_ref not in read_result.content:
        return

    updated_content = read_result.content.replace(source_ref, target_ref)
    encoding = read_result.encoding
    with open(target_file, "w", encoding=encoding) as handle:
        handle.write(updated_content)


@dataclass
class StreamingIngestDependencies:
    parser: Callable[[str], Dict[str, Any]] = _default_parse_file
    postprocess: Callable[..., tuple[Dict[str, Any], List[PostProcessFinding]]] = run_record_postprocess


class StreamingIngestRunner:
    """Run parse -> postprocess -> archive -> persist for one downloaded item."""

    def __init__(
        self,
        *,
        store: StreamingStore,
        archive_root: str,
        archive_roots_by_family: Mapping[str, str] | None = None,
        rules_config: Dict[str, Any] | None = None,
        dependencies: StreamingIngestDependencies | None = None,
    ) -> None:
        self.store = store
        self.archive_root = os.path.abspath(str(archive_root or "").strip())
        self.archive_roots_by_family: Dict[str, str] = {}
        if archive_roots_by_family is not None:
            if not isinstance(archive_roots_by_family, Mapping):
                raise ValueError("archive_roots_by_family must be an object")
            for raw_family, raw_root in archive_roots_by_family.items():
                try:
                    family_id = get_family_descriptor(str(raw_family or "").strip()).family_id
                except KeyError as exc:
                    raise ValueError(f"unknown archive root record_family: {raw_family}") from exc
                root = str(raw_root or "").strip()
                if root:
                    self.archive_roots_by_family[family_id] = os.path.abspath(root)
        if rules_config is None:
            self.rules_config = {}
        elif not isinstance(rules_config, Mapping):
            raise ValueError(f"rules_config must be an object, got {type(rules_config).__name__}")
        else:
            self.rules_config = dict(rules_config)
        self.dependencies = dependencies or StreamingIngestDependencies()
        os.makedirs(self.archive_root, exist_ok=True)
        for family_archive_root in self.archive_roots_by_family.values():
            os.makedirs(family_archive_root, exist_ok=True)

    def _run_postprocess_pipeline(
        self,
        *,
        parser_payload: Dict[str, Any],
        source_file: str,
        page_url: str = "",
        project_id: str = "",
        project_type_hint: str = "",
        project_type_label: str = "",
        project_type_fallback: str = "",
        record_family: str = "",
    ) -> tuple[Dict[str, Any], Dict[str, Any], List[PostProcessFinding], str]:
        context = RecordPostprocessContext(
            page_url=str(page_url or "").strip(),
            project_id=str(project_id or "").strip(),
            project_type_hint=str(project_type_hint or "").strip(),
            project_type_label=str(project_type_label or "").strip(),
            project_type_fallback=str(project_type_fallback or "").strip(),
            record_family=str(record_family or "").strip(),
        )
        working_parser_payload = apply_postprocess_context(parser_payload, context=context)

        postprocess_payload, findings = self.dependencies.postprocess(
            working_parser_payload,
            source_file=source_file,
            mapping_entries=self.store.list_mapping_entries(),
            rules_config=self.rules_config,
            context=context,
        )
        postprocess_payload, findings = normalize_record_payload(
            parser_payload=working_parser_payload,
            postprocess_payload=postprocess_payload,
            findings=findings,
            context=context,
        )

        project_type = resolve_project_type_label(
            postprocess_payload.get("项目类型"),
            working_parser_payload.get("项目类型"),
            context.project_type_hint,
            context.project_type_label,
            context.project_type_fallback,
        )
        if project_type:
            postprocess_payload["项目类型"] = project_type

        postprocess_payload.pop("canonical_projection", None)

        return working_parser_payload, postprocess_payload, list(findings), project_type

    def ingest(
        self,
        item: ItemSavedPayload,
        *,
        _connection: sqlite3.Connection | None = None,
    ) -> Dict[str, Any]:
        source_file = os.path.abspath(item.source_file)
        try:
            _validate_source_snapshot_bundle(source_file)
            requested_source_id = _canonical_source_id(item.extra.get("source_id"), item.exchange)
            if requested_source_id in LEGACY_NON_PRODUCT_SOURCE_IDS:
                raise PipelineFailure(
                    code="unsupported_product_source",
                    component="streaming_ingest",
                    stage="parse",
                    recoverability="permanent",
                    message=f"unsupported_product_source: {requested_source_id}",
                    context={"source_id": requested_source_id, "file_path": source_file},
                )
            raw_parser_payload = self.dependencies.parser(source_file)
        except Exception as exc:
            payload = {
                "source_file": source_file,
                "project_code": item.project_code,
                "exchange": item.exchange,
                "source_id": item.extra.get("source_id") or item.exchange,
                "record_family": item.extra.get("record_family") or "listing",
                "business_id_hint": item.extra.get("business_id") or "",
                "business_label_hint": item.extra.get("business_label") or "",
                "project_type_fallback": item.extra.get("project_type_fallback")
                or item.extra.get("project_type_label")
                or "",
                "listing_date": item.listing_date,
                "page_url": item.page_url or item.extra.get("page_url") or "",
                "candidate_tokens": _candidate_tokens_payload(
                    item.extra.get("candidate_tokens"),
                    field="item.extra.candidate_tokens",
                ),
            }
            if _is_skip_parse(exc):
                result = self.store.upsert_failed_record(
                    project_code=item.project_code,
                    source_file=source_file,
                    state="skipped",
                    error_type="skip_parse",
                    error_message=str(exc),
                    payload=payload,
                    severity="info",
                    _connection=_connection,
                )
                return {
                    "state": "skipped",
                    "record_id": result["record_id"],
                    "revision_id": result["revision_id"],
                    "error_type": "skip_parse",
                    "error_message": str(exc),
                    "archive_path": "",
                    "project_code": item.project_code,
                }
            error_type, error_message = _classify_failure(exc)
            result = self.store.upsert_failed_record(
                project_code=item.project_code,
                source_file=source_file,
                state="parse_failed",
                error_type=error_type,
                error_message=error_message,
                payload=payload,
                _connection=_connection,
            )
            return {
                "state": "parse_failed",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": error_type,
                "error_message": error_message,
                "archive_path": "",
            }
        parser_payload = _require_object_payload(raw_parser_payload, field="parser_payload")
        parse_findings = _parse_diagnostic_findings(parser_payload)
        project_code = str(parser_payload.get("项目编号") or "").strip()
        project_name = str(parser_payload.get("项目名称") or item.project_name or "").strip()
        exchange = _canonical_exchange_value(parser_payload.get("交易所") or item.exchange)
        listing_date = _first_non_empty(parser_payload, LISTING_DATE_FIELDS) or str(item.listing_date or "").strip()
        page_url = str(
            item.page_url
            or item.extra.get("page_url")
            or parser_payload.get("page_url")
            or parser_payload.get("source_url")
            or ""
        ).strip()
        source_id = _canonical_source_id(
            parser_payload.get("source_id"),
            parser_payload.get("交易所"),
            item.extra.get("source_id"),
            item.exchange,
        )
        if source_id in LEGACY_NON_PRODUCT_SOURCE_IDS:
            message = f"unsupported_product_source: {source_id}"
            payload = {
                "source_file": source_file,
                "project_code": project_code,
                "exchange": item.exchange,
                "source_id": source_id,
                "record_family": str(parser_payload.get("record_family") or item.extra.get("record_family") or "deal"),
                "parser_payload": parser_payload,
            }
            result = self.store.upsert_failed_record(
                project_code=project_code,
                source_file=source_file,
                state="parse_failed",
                error_type="unsupported_product_source",
                error_message=message,
                payload=payload,
                _connection=_connection,
            )
            return {
                "state": "parse_failed",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": "unsupported_product_source",
                "error_message": message,
                "archive_path": "",
            }
        metadata_record_family = str(item.extra.get("record_family") or parser_payload.get("record_family") or "").strip()
        authoritative_record_family = _authoritative_record_family(
            item.extra.get("record_family"),
            parser_payload.get("record_family"),
        )
        has_record_family_authority_context = _has_record_family_authority_context(
            item.extra.get("record_family"),
            parser_payload.get("record_family"),
            parser_payload.get("项目状态"),
            parser_payload.get("status"),
            parser_payload.get("source_id"),
            item.extra.get("source_id"),
            item.exchange,
            exchange,
        )
        metadata_business_id = str(item.extra.get("business_id") or parser_payload.get("business_id") or "").strip()
        if _is_invalid_cbex_deal_source_page(
            source_url=page_url,
            source_id=source_id,
            record_family=metadata_record_family,
            business_id=metadata_business_id,
        ):
            message = f"CBEX deal source URL is not a detail page: {page_url}"
            payload = {
                "source_file": source_file,
                "source_url": page_url,
                "project_code": project_code,
                "project_name": project_name,
                "record_family": "deal",
                "source_id": "cbex",
                "business_id_hint": metadata_business_id,
                "parser_payload": parser_payload,
            }
            result = self.store.upsert_failed_record(
                project_code=project_code,
                source_file=source_file,
                state="skipped",
                error_type="invalid_source_page",
                error_message=message,
                payload=payload,
                severity="info",
                _connection=_connection,
            )
            return {
                "state": "skipped",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": "invalid_source_page",
                "error_message": message,
                "archive_path": "",
                "project_code": project_code,
            }
        project_id = str(item.extra.get("project_id") or parser_payload.get("project_id") or "").strip()
        initial_classification = classify_record_business(
            parser_payload=parser_payload,
            record_family_hint=item.extra.get("record_family") or "",
            business_id_hint=item.extra.get("business_id") or "",
            business_label_hint=item.extra.get("business_label") or "",
            project_type_hint=item.extra.get("project_type") or "",
            project_type_label=item.extra.get("project_type_label") or "",
            project_type_fallback=item.extra.get("project_type_fallback") or "",
            page_url=page_url,
        )

        try:
            parser_payload, postprocess_payload, findings, project_type = self._run_postprocess_pipeline(
                parser_payload=parser_payload,
                source_file=source_file,
                page_url=page_url,
                project_id=project_id,
                project_type_hint=str(item.extra.get("project_type") or ""),
                project_type_label=initial_classification.project_type_label
                or str(item.extra.get("project_type_label") or ""),
                project_type_fallback=initial_classification.project_type_label
                or str(item.extra.get("project_type_fallback") or ""),
                record_family=authoritative_record_family or initial_classification.record_family,
            )
            findings = [*parse_findings, *findings]
        except Exception as exc:
            payload = {"source_file": source_file, "project_code": project_code, "parser_payload": parser_payload}
            result = self.store.upsert_failed_record(
                project_code=project_code,
                source_file=source_file,
                state="postprocess_failed",
                error_type="postprocess_failed",
                error_message=str(exc),
                payload=payload,
                _connection=_connection,
            )
            return {
                "state": "postprocess_failed",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": "postprocess_failed",
                "error_message": str(exc),
                "archive_path": "",
            }

        final_classification = classify_record_business(
            parser_payload=postprocess_payload,
            record_family_hint=authoritative_record_family,
            business_id_hint=item.extra.get("business_id") or initial_classification.business_id,
            business_label_hint=item.extra.get("business_label") or initial_classification.raw_business_label,
            project_type_hint=item.extra.get("project_type") or "",
            project_type_label=item.extra.get("project_type_label") or initial_classification.project_type_label,
            project_type_fallback=item.extra.get("project_type_fallback") or initial_classification.project_type_label,
            page_url=page_url,
        )
        project_type = final_classification.project_type_label or resolve_project_type_label(
            postprocess_payload.get("项目类型"),
            parser_payload.get("项目类型"),
            item.extra.get("project_type"),
            item.extra.get("project_type_label"),
            parser_payload.get("项目类型"),
            item.extra.get("project_type_fallback"),
        )
        source_id = _canonical_source_id(
            item.extra.get("source_id")
            or postprocess_payload.get("source_id")
            or parser_payload.get("source_id")
            or exchange
            or item.exchange
        )
        listing_date = _resolve_effective_listing_date(
            listing_date=listing_date,
            source_identity={
                "record_family": final_classification.record_family,
                "listing_date": listing_date,
                "deal_date": str(postprocess_payload.get("deal_date") or parser_payload.get("deal_date") or ""),
                "collection_date": str(
                    postprocess_payload.get("collection_date")
                    or parser_payload.get("collection_date")
                    or ""
                ),
            },
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
            record_family=final_classification.record_family,
        )
        findings = _append_deal_effective_date_missing_finding(
            findings,
            record_family=final_classification.record_family,
            effective_listing_date=listing_date,
            project_code=project_code,
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
            source_identity={"listing_date": listing_date},
        )
        findings = _append_record_family_authority_missing_finding(
            findings,
            stage="ingest",
            authoritative_record_family=authoritative_record_family
            or ("__context_present__" if has_record_family_authority_context else ""),
            classified_record_family=final_classification.record_family,
            parser_payload=parser_payload,
        )
        target_archive_root = self.archive_roots_by_family.get(
            final_classification.record_family,
            self.archive_root,
        )
        archive_path, had_conflict = materialize_snapshot_to_archive(
            source_file=source_file,
            archive_root=target_archive_root,
            project_code=project_code or "unknown",
            project_name=project_name,
            listing_date=listing_date,
            record_family=final_classification.record_family,
            business_id=final_classification.business_id,
            source_id=source_id,
            preserve_source=item.extra.get("preserve_source_artifact") is True,
            reuse_current_conflict=item.extra.get("reuse_current_conflict") is True,
        )
        state = classify_record_state(findings, had_conflict=had_conflict)
        if had_conflict:
            findings = list(findings) + [
                PostProcessFinding(
                    severity="warn",
                    type="archive_conflict",
                    message=f"archive naming conflict for project_code={project_code}",
                    evidence={"archive_path": archive_path},
                )
            ]

        candidate_tokens = _resolve_candidate_tokens(
            project_code=project_code,
            project_id=project_id,
            page_url=page_url,
        )
        source_project_code = str(parser_payload.get("source_project_code") or "").strip().upper()
        source_project_token = f"project_code:{source_project_code}" if source_project_code else ""
        if source_project_token and source_project_token not in candidate_tokens:
            candidate_tokens.append(source_project_token)
        # The archived path is the stable identity path. Keep the pre-materialize
        # path separately so reprocess can still locate the original evidence.
        source_identity = build_source_identity_payload(
            record_family=final_classification.record_family,
            source_file=archive_path,
            source_url=page_url,
            project_code=project_code,
            project_name=project_name,
            exchange=exchange,
            listing_date=listing_date,
            candidate_tokens=candidate_tokens,
            business_id_hint=final_classification.business_id or str(item.extra.get("business_id") or ""),
            business_label_hint=final_classification.raw_business_label or str(item.extra.get("business_label") or ""),
            project_type_fallback=project_type or str(item.extra.get("project_type_fallback") or ""),
        )
        source_identity["original_evidence_path"] = source_file
        if final_classification.business_id:
            source_identity["business_id"] = str(final_classification.business_id).strip()
        if source_id:
            source_identity["source_id"] = source_id
        record = _assemble_ingested_record(
            record_id=uuid.uuid4().hex,
            project_code=project_code,
            project_name=project_name,
            project_type=project_type,
            exchange=exchange,
            listing_date=listing_date,
            state=state,
            source_file=archive_path,
            archive_path=archive_path,
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
            findings=findings,
            source_identity=source_identity,
            record_family=final_classification.record_family,
            classification=final_classification,
        )
        state = record.state
        findings = record.findings
        stored = self.store.upsert_record_with_mapping_pending(
            record,
            _connection=_connection,
        )
        return {
            "state": state,
            "record_id": stored["record_id"],
            "revision_id": int(stored["revision_id"]),
            "changed": bool(stored["changed"]),
            "archive_path": archive_path,
            "project_code": project_code,
            "project_name": project_name,
            "project_type": project_type,
            "listing_date": listing_date,
            "findings": [finding.__dict__ for finding in findings],
        }

    def refresh_postprocess(self, record_id: str) -> Dict[str, Any]:
        record = self.store.get_record(record_id)
        parser_payload = _require_object_payload(record.get("parser_payload"), field="parser_payload")
        project_code = str(record.get("project_code") or parser_payload.get("项目编号") or "").strip()
        project_name = str(record.get("project_name") or parser_payload.get("项目名称") or "").strip()
        source_identity_payload = record.get("source_identity_json")
        if source_identity_payload is None:
            source_identity = {}
        elif not isinstance(source_identity_payload, Mapping):
            raise ValueError(f"source_identity_json must be an object, got {type(source_identity_payload).__name__}")
        else:
            source_identity = dict(source_identity_payload)
        exchange = str(record.get("exchange") or parser_payload.get("交易所") or "").strip()
        listing_date = str(record.get("listing_date") or _first_non_empty(parser_payload, LISTING_DATE_FIELDS) or "").strip()
        source_file = str(record.get("source_file") or record.get("archive_path") or "").strip()
        archive_path = str(record.get("archive_path") or source_file).strip()
        page_url = str(parser_payload.get("page_url") or "").strip()
        project_id = str(parser_payload.get("project_id") or "").strip()
        authoritative_record_family = _authoritative_record_family(
            source_identity.get("record_family"),
            record.get("record_family"),
            parser_payload.get("record_family"),
        )
        has_record_family_authority_context = _has_record_family_authority_context(
            source_identity.get("record_family"),
            record.get("record_family"),
            parser_payload.get("record_family"),
            parser_payload.get("项目状态"),
            parser_payload.get("status"),
            source_identity.get("source_id"),
            parser_payload.get("source_id"),
            source_identity.get("exchange"),
            record.get("exchange"),
            exchange,
        )
        initial_classification = classify_record_business(
            parser_payload=parser_payload,
            record_family_hint=authoritative_record_family,
            business_id_hint=str(source_identity.get("business_id_hint") or record.get("business_id") or ""),
            business_label_hint=str(source_identity.get("business_label_hint") or ""),
            project_type_hint=str(source_identity.get("business_label_hint") or ""),
            project_type_label=str(record.get("project_type") or ""),
            project_type_fallback=str(
                source_identity.get("project_type_fallback")
                or record.get("project_type")
                or ""
            ),
            page_url=page_url or str(source_identity.get("source_url") or ""),
        )

        parser_payload, postprocess_payload, findings, project_type = self._run_postprocess_pipeline(
            parser_payload=parser_payload,
            source_file=source_file or archive_path,
            page_url=page_url,
            project_id=project_id,
            project_type_hint=initial_classification.project_type_label or str(source_identity.get("business_label_hint") or ""),
            project_type_label=initial_classification.project_type_label or str(source_identity.get("business_label_hint") or ""),
            project_type_fallback=initial_classification.project_type_label or str(
                source_identity.get("project_type_fallback")
                or record.get("project_type")
                or ""
            ),
            record_family=authoritative_record_family or initial_classification.record_family,
        )
        final_classification = classify_record_business(
            parser_payload=postprocess_payload,
            record_family_hint=authoritative_record_family,
            business_id_hint=str(source_identity.get("business_id_hint") or record.get("business_id") or initial_classification.business_id),
            business_label_hint=str(source_identity.get("business_label_hint") or initial_classification.raw_business_label),
            project_type_hint=initial_classification.project_type_label,
            project_type_label=str(record.get("project_type") or initial_classification.project_type_label),
            project_type_fallback=str(
                source_identity.get("project_type_fallback")
                or record.get("project_type")
                or initial_classification.project_type_label
                or ""
            ),
            page_url=page_url or str(source_identity.get("source_url") or ""),
        )
        if final_classification.record_family:
            source_identity["record_family"] = final_classification.record_family
        if final_classification.business_id:
            source_identity["business_id_hint"] = final_classification.business_id
            source_identity["business_id"] = final_classification.business_id
        if final_classification.raw_business_label:
            source_identity["business_label_hint"] = final_classification.raw_business_label
        if final_classification.project_type_label:
            source_identity["project_type_fallback"] = final_classification.project_type_label
        source_id = _canonical_source_id(source_identity.get("source_id"), source_identity.get("exchange"), exchange)
        if source_id:
            source_identity["source_id"] = source_id
        if exchange and not str(source_identity.get("exchange") or "").strip():
            source_identity["exchange"] = exchange
        project_type = final_classification.project_type_label or project_type
        findings = _append_record_family_authority_missing_finding(
            findings,
            stage="refresh_postprocess",
            authoritative_record_family=authoritative_record_family
            or ("__context_present__" if has_record_family_authority_context else ""),
            classified_record_family=final_classification.record_family,
            parser_payload=parser_payload,
            source_identity=source_identity,
        )
        state = classify_record_state(findings, had_conflict=str(record.get("state") or "").strip() == "conflict")
        refreshed_record = _assemble_ingested_record(
            record_id=str(record.get("record_id") or record_id),
            project_code=project_code,
            project_name=project_name,
            project_type=project_type,
            exchange=exchange,
            listing_date=listing_date,
            state=state,
            source_file=source_file,
            archive_path=archive_path,
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
            findings=findings,
            source_identity=source_identity,
            record_family=final_classification.record_family,
            classification=final_classification,
        )
        state = refreshed_record.state
        stored = self.store.upsert_record_with_mapping_pending(
            refreshed_record,
            preserve_operational_overlay=True,
        )
        return {
            "state": state,
            "record_id": stored["record_id"],
            "revision_id": int(stored["revision_id"]),
            "changed": bool(stored["changed"]),
            "archive_path": archive_path,
            "project_code": project_code,
            "project_name": project_name,
            "project_type": project_type,
            "listing_date": listing_date,
            "findings": [finding.__dict__ for finding in refreshed_record.findings],
        }
