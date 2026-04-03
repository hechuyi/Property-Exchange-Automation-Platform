"""Single-item ingest pipeline for downloaded HTML snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from peap_core.record_identity import build_source_identity_payload

from .io_utils import read_text_with_fallback
from .streaming_models import IngestedRecord, ItemSavedPayload, PostProcessFinding, RecordState
from .streaming_postprocess import (
    BUSINESS_PROJECT_TYPES,
    normalize_record_payload,
    run_record_postprocess,
)
from .export_projection import project_canonical_record_to_compat_payload
from .streaming_store import StreamingStore
from .submission_layout import resolve_submission_snapshot_target

LISTING_DATE_FIELDS = ("挂牌开始日期", "预披露开始日期", "披露开始日期", "信息披露起始日期")
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

def _first_non_empty(payload: Dict[str, Any], fields: Iterable[str]) -> str:
    for field_name in fields:
        value = str(payload.get(field_name) or "").strip()
        if value:
            return value
    return ""


def _default_parse_file(file_path: str) -> Dict[str, Any]:
    from peap.parsing import parse_file

    parsed = parse_file(file_path)
    payload = parsed.to_compat_payload(include_raw=True)
    payload["项目编号"] = parsed.project_code
    payload["项目名称"] = parsed.project_name
    payload["项目类型"] = parsed.project_type
    payload["状态"] = parsed.status
    payload["交易所"] = parsed.exchange
    if "类型" not in payload and parsed.standard_record.source_type:
        payload["类型"] = parsed.standard_record.source_type
    return payload


def _is_skip_parse(exc: Exception) -> bool:
    return exc.__class__.__name__ == "SkipParse"


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
) -> Dict[str, Any]:
    seller = str(postprocess_payload.get("转让方") or parser_payload.get("转让方") or "").strip()
    source_type = str(postprocess_payload.get("类型") or parser_payload.get("类型") or "").strip()
    group_name = str(postprocess_payload.get("隶属集团") or parser_payload.get("隶属集团") or "").strip()
    status = str(postprocess_payload.get("项目状态") or parser_payload.get("项目状态") or "").strip()
    price = postprocess_payload.get("挂牌价格") or parser_payload.get("挂牌价格")
    diagnostic_payload = [
        {
            "severity": str(item.severity),
            "type": str(item.type),
            "message": str(item.message),
            "evidence": dict(item.evidence or {}),
        }
        for item in findings
    ]
    return {
        "record_id": record_id,
        "record_family": str(source_identity.get("record_family") or "listing"),
        "source_identity": dict(source_identity),
        "business_identity": {"project_code": project_code},
        "canonical_fields": {
            "project_code": project_code,
            "project_name": project_name,
            "project_type": project_type,
            "status": status,
            "exchange": exchange,
            "start_date": listing_date,
            "price": price,
            "seller": seller,
            "source_type": source_type,
            "group_name": group_name,
        },
        "field_provenance": {},
        "diagnostics": diagnostic_payload,
        "normalizer_version": "streaming_ingest/v1",
        "policy_state": {
            "findings": [str(item.type) for item in findings],
        },
    }


def _compute_revision_hash(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


    if not os.path.exists(base_path):
        return base_path, False
    root, ext = os.path.splitext(base_path)
    index = 1
    while True:
        candidate = f"{root}__conflict{index}{ext}"
        if not os.path.exists(candidate):
            return candidate, True
        index += 1


def _canonical_archive_target(
    *,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    source_file: str,
) -> tuple[str, bool]:
    ext = os.path.splitext(os.path.abspath(source_file))[1] or ".html"
    return resolve_submission_snapshot_target(
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        ext=ext,
        current_path=source_file,
    )


def copy_snapshot_to_archive(
    *,
    source_file: str,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
) -> tuple[str, bool]:
    source_abs = os.path.abspath(source_file)
    target_file, had_conflict = _canonical_archive_target(
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        source_file=source_abs,
    )

    shutil.copy2(source_abs, target_file)
    source_assets_dir = f"{os.path.splitext(source_abs)[0]}_files"
    if os.path.isdir(source_assets_dir):
        target_assets_dir = f"{os.path.splitext(target_file)[0]}_files"
        if os.path.isdir(target_assets_dir):
            shutil.rmtree(target_assets_dir, ignore_errors=True)
        shutil.copytree(source_assets_dir, target_assets_dir)
        _rewrite_archived_asset_references(
            target_file=target_file,
            source_file=source_abs,
        )
    return target_file, had_conflict


def materialize_snapshot_to_archive(
    *,
    source_file: str,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
) -> tuple[str, bool]:
    source_abs = os.path.abspath(source_file)
    target_file, had_conflict = _canonical_archive_target(
        archive_root=archive_root,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        source_file=source_abs,
    )
    if os.path.normcase(source_abs) == os.path.normcase(os.path.abspath(target_file)):
        return source_abs, False
    if not _is_path_within_root(source_abs, archive_root):
        return copy_snapshot_to_archive(
            source_file=source_abs,
            archive_root=archive_root,
            project_code=project_code,
            project_name=project_name,
            listing_date=listing_date,
        )

    source_assets_dir = f"{os.path.splitext(source_abs)[0]}_files"
    target_assets_dir = f"{os.path.splitext(target_file)[0]}_files"
    shutil.move(source_abs, target_file)
    if os.path.isdir(source_assets_dir):
        if os.path.isdir(target_assets_dir):
            shutil.rmtree(target_assets_dir, ignore_errors=True)
        shutil.move(source_assets_dir, target_assets_dir)
        _rewrite_archived_asset_references(
            target_file=target_file,
            source_file=source_abs,
        )
    return target_file, had_conflict


def _is_path_within_root(path_value: str, root_value: str) -> bool:
    target = os.path.abspath(str(path_value or ""))
    root = os.path.abspath(str(root_value or ""))
    if not target or not root:
        return False
    try:
        return os.path.commonpath([target, root]) == root
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
    if encoding == "utf-8/replace":
        encoding = "utf-8"
    with open(target_file, "w", encoding=encoding) as handle:
        handle.write(updated_content)


def classify_record_state(findings: Iterable[PostProcessFinding], *, had_conflict: bool = False) -> RecordState:
    finding_types = {str(item.type) for item in findings}
    if "mapping_conflict" in finding_types:
        return "mapping_conflict"
    if {"mapping_missing", "mapping_gap", "mapping_ambiguous", "project_type_unknown"} & finding_types:
        return "pending_mapping"
    if had_conflict:
        return "conflict"
    return "ready"


def _resolve_project_type_label(*values: Any) -> str:
    for raw_value in values:
        text = str(raw_value or "").strip()
        if not text or text in {"未知", "unknown", "UNKNOWN"}:
            continue
        if text in BUSINESS_PROJECT_TYPES:
            return text
        mapped = PROJECT_TYPE_FALLBACKS.get(text.lower()) or PROJECT_TYPE_FALLBACKS.get(text)
        if mapped:
            return mapped
    return ""


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
        rules_config: Dict[str, Any] | None = None,
        dependencies: StreamingIngestDependencies | None = None,
    ) -> None:
        self.store = store
        self.archive_root = os.path.abspath(str(archive_root or "").strip())
        self.rules_config = dict(rules_config or {})
        self.dependencies = dependencies or StreamingIngestDependencies()
        os.makedirs(self.archive_root, exist_ok=True)

    def ingest(self, item: ItemSavedPayload) -> Dict[str, Any]:
        source_file = os.path.abspath(item.source_file)
        try:
            parser_payload = self.dependencies.parser(source_file)
        except Exception as exc:
            payload = {"source_file": source_file, "project_code": item.project_code}
            if _is_skip_parse(exc):
                result = self.store.upsert_failed_record(
                    project_code=item.project_code,
                    source_file=source_file,
                    state="skipped",
                    error_type="skip_parse",
                    error_message=str(exc),
                    payload=payload,
                    severity="info",
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
            )
            return {
                "state": "parse_failed",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": error_type,
                "error_message": error_message,
                "archive_path": "",
            }
        project_code = str(parser_payload.get("项目编号") or item.project_code or "").strip()
        project_name = str(parser_payload.get("项目名称") or item.project_name or "").strip()
        exchange = str(parser_payload.get("交易所") or item.exchange or "").strip()
        listing_date = _first_non_empty(parser_payload, LISTING_DATE_FIELDS) or str(item.listing_date or "").strip()
        row_payload = item.extra.get("row")
        row = row_payload if isinstance(row_payload, dict) else {}
        page_url = str(item.page_url or item.extra.get("page_url") or row.get("page_url") or "").strip()
        project_id = str(item.extra.get("project_id") or row.get("project_id") or "").strip()
        if page_url and not str(parser_payload.get("page_url") or "").strip():
            parser_payload["page_url"] = page_url
        if project_id and not str(parser_payload.get("project_id") or "").strip():
            parser_payload["project_id"] = project_id

        try:
            postprocess_payload, findings = self.dependencies.postprocess(
                parser_payload,
                source_file=source_file,
                mapping_entries=self.store.list_mapping_entries(),
                rules_config=self.rules_config,
            )
            postprocess_payload, findings = normalize_record_payload(
                parser_payload=parser_payload,
                postprocess_payload=postprocess_payload,
                findings=findings,
            )
            if page_url and not str(postprocess_payload.get("page_url") or "").strip():
                postprocess_payload["page_url"] = page_url
            if project_id and not str(postprocess_payload.get("project_id") or "").strip():
                postprocess_payload["project_id"] = project_id
            project_type = _resolve_project_type_label(
                postprocess_payload.get("项目类型"),
                parser_payload.get("项目类型"),
                item.extra.get("project_type"),
                item.extra.get("project_type_label"),
                postprocess_payload.get("项目类型"),
                parser_payload.get("项目类型"),
                item.extra.get("project_type_fallback"),
            )
            if project_type and str(postprocess_payload.get("项目类型") or "").strip() != project_type:
                postprocess_payload["项目类型"] = project_type
                postprocess_payload, findings = normalize_record_payload(
                    parser_payload=parser_payload,
                    postprocess_payload=postprocess_payload,
                    findings=[finding for finding in findings if str(finding.type or "") != "project_type_unknown"],
                )
        except Exception as exc:
            payload = {"source_file": source_file, "project_code": project_code, "parser_payload": parser_payload}
            result = self.store.upsert_failed_record(
                project_code=project_code,
                source_file=source_file,
                state="postprocess_failed",
                error_type="postprocess_failed",
                error_message=str(exc),
                payload=payload,
            )
            return {
                "state": "postprocess_failed",
                "record_id": result["record_id"],
                "revision_id": result["revision_id"],
                "error_type": "postprocess_failed",
                "error_message": str(exc),
                "archive_path": "",
            }

        project_type = _resolve_project_type_label(
            postprocess_payload.get("项目类型"),
            parser_payload.get("项目类型"),
            item.extra.get("project_type"),
            item.extra.get("project_type_label"),
            parser_payload.get("项目类型"),
            item.extra.get("project_type_fallback"),
        )
        archive_path, had_conflict = materialize_snapshot_to_archive(
            source_file=source_file,
            archive_root=self.archive_root,
            project_code=project_code or "unknown",
            project_name=project_name,
            listing_date=listing_date,
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
        source_identity = build_source_identity_payload(
            record_family="listing",
            source_file=source_file,
            source_url=page_url,
            project_code=project_code,
            project_name=project_name,
            exchange=exchange,
            listing_date=listing_date,
            candidate_tokens=candidate_tokens,
        )
        canonical_record = _build_canonical_record_payload(
            record_id=uuid.uuid4().hex,
            project_code=project_code,
            project_name=project_name,
            project_type=project_type,
            exchange=exchange,
            listing_date=listing_date,
            source_identity=source_identity,
            parser_payload=parser_payload,
            postprocess_payload=postprocess_payload,
            findings=findings,
        )
        canonical_projection = dict(postprocess_payload.get("canonical_projection") or {})
        if not canonical_projection:
            canonical_projection = project_canonical_record_to_compat_payload(canonical_record)
        record = IngestedRecord(
            record_id=uuid.uuid4().hex,
            revision_hash=_compute_revision_hash(postprocess_payload),
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
            findings=list(findings),
            source_identity=source_identity,
            canonical_record=canonical_record,
            canonical_projection=canonical_projection,
        )
        stored = self.store.upsert_record(record)
        if state == "pending_mapping":
            self.store.mark_mapping_pending(
                record_id=stored["record_id"],
                revision_id=int(stored["revision_id"]),
                project_code=project_code,
                payload=postprocess_payload,
            )
        else:
            self.store.resolve_mapping_pending(stored["record_id"])
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
