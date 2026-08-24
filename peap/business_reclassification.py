"""Controlled planning and application of historical business reclassification."""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from peap_core.record_state_policy import classify_record_state
from peap_core.source_catalog import canonical_source_code

from .business_classifier import BusinessClassification, classify_record_business
from .failed_record_supersession import reprocess_source_path
from .streaming_ingest import (
    StreamingIngestDependencies,
    _assemble_ingested_record,
    _parse_diagnostic_findings,
)
from .streaming_models import IngestedRecord
from .streaming_postprocess import (
    RecordPostprocessContext,
    apply_postprocess_context,
    normalize_record_payload,
    run_record_postprocess,
)
from .streaming_store import (
    StreamingStore,
    _business_reclassification_proof_fingerprint,
    _json_payload_sha256,
    _regular_file_sha256,
)
from .write_coordinator import WriteCoordinator

_ORIGINAL_STATES = frozenset({"ready", "field_missing"})
_TARGET_STATES = frozenset({"ready", "field_missing"})
_EQUITY_PROJECT_CODE_PATTERN = re.compile(r"^(?:(?:G|T|Q)320|CP)", re.IGNORECASE)
_RECLASSIFICATION_RECORD_FAMILY = "listing"
_RECLASSIFICATION_SOURCE_BUSINESS_ID = "capital_increase"
_RECLASSIFICATION_TARGET_BUSINESS_ID = "equity_transfer"


def _validate_reclassification_scope(
    *,
    source_business_id: str,
    target_business_id: str,
) -> None:
    """Keep this maintenance path limited to its reviewed migration."""
    if (
        source_business_id != _RECLASSIFICATION_SOURCE_BUSINESS_ID
        or target_business_id != _RECLASSIFICATION_TARGET_BUSINESS_ID
    ):
        raise ValueError(
            "business reclassification scope is fixed to "
            "listing: capital_increase -> equity_transfer"
        )


@dataclass(frozen=True)
class BusinessReclassificationRuntime:
    store: StreamingStore
    parser: Callable[[str], Mapping[str, Any]]
    rules_config: Mapping[str, Any]

    @classmethod
    def for_store(
        cls,
        store: StreamingStore,
        *,
        parser: Callable[[str], Mapping[str, Any]] | None = None,
        rules_config: Mapping[str, Any] | None = None,
    ) -> "BusinessReclassificationRuntime":
        return cls(
            store=store,
            parser=parser or StreamingIngestDependencies().parser,
            rules_config=dict(rules_config or {}),
        )


@dataclass(frozen=True)
class _PlanEntry:
    item: dict[str, Any]
    original_snapshot: dict[str, Any]
    target_snapshot: dict[str, Any] | None
    proof: dict[str, Any] | None
    target_record: IngestedRecord | None


def _source_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = record.get("source_identity_json")
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("source_identity_json must be an object")
    return dict(payload)


def _canonical_source(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(canonical_source_code(text, allow_substring=True) or text).strip().lower()


def _record_source_id(record: Mapping[str, Any]) -> str:
    identity = _source_identity(record)
    return _canonical_source(
        identity.get("source_id")
        or identity.get("exchange")
        or record.get("exchange")
    )


def _record_source_url(record: Mapping[str, Any]) -> str:
    identity = _source_identity(record)
    parser_payload = record.get("parser_payload")
    postprocess_payload = record.get("postprocess_payload")
    containers = (
        identity,
        parser_payload if isinstance(parser_payload, Mapping) else {},
        postprocess_payload if isinstance(postprocess_payload, Mapping) else {},
    )
    for container in containers:
        for field in ("source_url", "page_url", "detail_url"):
            value = str(container.get(field) or "").strip()
            if value:
                return value
    return ""


def _project_code(payload: Mapping[str, Any]) -> str:
    return str(payload.get("项目编号") or payload.get("project_code") or "").strip().upper()


def _record_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    source_identity = _source_identity(record)
    parser_payload = dict(record.get("parser_payload") or {})
    postprocess_payload = dict(record.get("postprocess_payload") or {})
    canonical_record = dict(record.get("canonical_record") or {})
    return {
        "record_id": str(record.get("record_id") or ""),
        "revision_id": int(record.get("revision_id") or 0),
        "revision_hash": str(record.get("revision_hash") or ""),
        "state": str(record.get("state") or ""),
        "record_family": str(record.get("record_family") or ""),
        "business_id": str(record.get("business_id") or ""),
        "business_key": str(record.get("business_key") or ""),
        "project_code": str(record.get("project_code") or "").strip().upper(),
        "exchange": str(record.get("exchange") or "").strip(),
        "source_id": _record_source_id(record),
        "source_file": str(record.get("source_file") or "").strip(),
        "archive_path": str(record.get("archive_path") or "").strip(),
        "identity_anchor": str(record.get("identity_anchor") or ""),
        "source_identity_sha256": _json_payload_sha256(source_identity),
        "parser_payload_sha256": _json_payload_sha256(parser_payload),
        "postprocess_payload_sha256": _json_payload_sha256(postprocess_payload),
        "canonical_record_sha256": _json_payload_sha256(canonical_record),
    }


def _same_target_scope(
    record: Mapping[str, Any],
    *,
    original: Mapping[str, Any],
    proposed_business_id: str,
) -> bool:
    return all(
        (
            str(record.get("record_id") or "") != str(original.get("record_id") or ""),
            str(record.get("record_family") or "") == str(original.get("record_family") or ""),
            str(record.get("business_id") or "") == proposed_business_id,
            str(record.get("project_code") or "").strip().upper()
            == str(original.get("project_code") or "").strip().upper(),
            str(record.get("exchange") or "").strip()
            == str(original.get("exchange") or "").strip(),
            bool(_record_source_id(record)),
            _record_source_id(record) == _record_source_id(original),
        )
    )


def _public_proof(proof: Mapping[str, Any]) -> dict[str, Any]:
    payload = {str(key): value for key, value in proof.items() if str(key) != "parser_payload"}
    parser_payload = proof.get("parser_payload")
    payload["parser_payload_field_count"] = (
        len(parser_payload) if isinstance(parser_payload, Mapping) else 0
    )
    return payload


def _base_item(
    record: Mapping[str, Any],
    *,
    proposed_business_id: str,
) -> dict[str, Any]:
    return {
        "record_id": str(record.get("record_id") or ""),
        "project_code": str(record.get("project_code") or "").strip().upper(),
        "record_family": str(record.get("record_family") or ""),
        "stored_business_id": str(record.get("business_id") or ""),
        "proposed_business_id": proposed_business_id,
        "exchange": str(record.get("exchange") or "").strip(),
        "source_id": _record_source_id(record),
    }


def _blocked_entry(
    record: Mapping[str, Any],
    *,
    proposed_business_id: str,
    reason_code: str,
    original_snapshot: dict[str, Any],
) -> _PlanEntry:
    return _PlanEntry(
        item={
            **_base_item(record, proposed_business_id=proposed_business_id),
            "action": "blocked",
            "reason_code": reason_code,
            "apply_supported": False,
        },
        original_snapshot=original_snapshot,
        target_snapshot=None,
        proof=None,
        target_record=None,
    )


def _proof_common(
    *,
    evidence_kind: str,
    original: Mapping[str, Any],
    proposed_business_id: str,
    source_url: str,
    parser_payload: Mapping[str, Any],
) -> dict[str, Any]:
    proof: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "parser_payload": dict(parser_payload),
        "parser_payload_sha256": _json_payload_sha256(dict(parser_payload)),
        "record_family": str(original.get("record_family") or ""),
        "original_business_id": str(original.get("business_id") or ""),
        "proposed_business_id": proposed_business_id,
        "project_code": str(original.get("project_code") or "").strip().upper(),
        "source_id": _record_source_id(original),
        "exchange": str(original.get("exchange") or "").strip(),
        "source_url": source_url,
    }
    return proof


def _finish_proof(proof: dict[str, Any]) -> dict[str, Any]:
    proof["evidence_fingerprint"] = _business_reclassification_proof_fingerprint(proof)
    return proof


def _build_target_record(
    *,
    runtime: BusinessReclassificationRuntime,
    original: Mapping[str, Any],
    parser_payload: Mapping[str, Any],
    classification: BusinessClassification,
    source_path: str,
    source_url: str,
    target_record_id: str,
) -> IngestedRecord:
    context = RecordPostprocessContext(
        page_url=source_url,
        project_type_label=classification.project_type_label,
        project_type_fallback=classification.project_type_label,
        record_family=classification.record_family,
    )
    working_parser_payload = apply_postprocess_context(
        dict(parser_payload),
        context=context,
    )
    postprocess_payload, findings = run_record_postprocess(
        working_parser_payload,
        source_file=source_path,
        mapping_entries=runtime.store.list_mapping_entries(),
        rules_config=dict(runtime.rules_config),
        context=context,
    )
    postprocess_payload, findings = normalize_record_payload(
        parser_payload=working_parser_payload,
        postprocess_payload=postprocess_payload,
        findings=findings,
        context=context,
    )
    final_classification = classify_record_business(
        parser_payload=postprocess_payload,
        record_family_hint=classification.record_family,
        page_url=source_url,
        source_url=source_url,
    )
    if (
        final_classification.record_family != classification.record_family
        or final_classification.business_id != classification.business_id
    ):
        raise ValueError("postprocess classification differs from fresh parser classification")

    project_code = _project_code(postprocess_payload) or _project_code(working_parser_payload)
    project_name = str(
        postprocess_payload.get("项目名称")
        or working_parser_payload.get("项目名称")
        or original.get("project_name")
        or ""
    ).strip()
    source_identity = _source_identity(original)
    source_identity.update(
        {
            "record_family": classification.record_family,
            "business_id": classification.business_id,
            "business_id_hint": classification.business_id,
            "business_label_hint": classification.raw_business_label,
            "project_type_fallback": classification.project_type_label,
            "project_code": project_code,
            "project_name": project_name,
            "exchange": str(original.get("exchange") or "").strip(),
            "source_id": _record_source_id(original),
            "source_url": source_url,
            "original_evidence_path": source_path,
        }
    )
    findings = [*_parse_diagnostic_findings(dict(parser_payload)), *findings]
    state = classify_record_state(findings, had_conflict=False)
    return _assemble_ingested_record(
        record_id=target_record_id,
        project_code=project_code,
        project_name=project_name,
        project_type=classification.project_type_label,
        exchange=str(original.get("exchange") or "").strip(),
        listing_date=str(original.get("listing_date") or "").strip(),
        state=state,
        source_file=source_path,
        archive_path=source_path,
        parser_payload=working_parser_payload,
        postprocess_payload=postprocess_payload,
        findings=findings,
        source_identity=source_identity,
        record_family=classification.record_family,
        classification=classification,
    )


def _target_record_id(original_record_id: str, proposed_business_id: str) -> str:
    seed = f"business-reclassification\0{original_record_id}\0{proposed_business_id}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _selected_records(
    records: list[dict[str, Any]],
    *,
    record_ids: list[str] | None,
    limit: int | None,
    source_business_id: str,
) -> list[dict[str, Any]]:
    selected_ids = {str(item).strip() for item in (record_ids or []) if str(item).strip()}
    selected = [
        record
        for record in records
        if str(record.get("state") or "") in _ORIGINAL_STATES
        and str(record.get("record_family") or "") == "listing"
        and str(record.get("business_id") or "") == source_business_id
        and (not selected_ids or str(record.get("record_id") or "") in selected_ids)
    ]
    if selected_ids:
        found = {str(record.get("record_id") or "") for record in selected}
        missing = sorted(selected_ids - found)
        if missing:
            raise KeyError(f"selected repairable listing records not found: {', '.join(missing)}")
    if limit is not None:
        if int(limit) < 1:
            raise ValueError("limit must be positive")
        selected = selected[: int(limit)]
    return selected


def _build_plan_entries(
    *,
    runtime: BusinessReclassificationRuntime,
    record_ids: list[str] | None = None,
    limit: int | None = None,
    source_business_id: str = "capital_increase",
    target_business_id: str = "equity_transfer",
) -> tuple[list[_PlanEntry], int]:
    _validate_reclassification_scope(
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )
    all_records = runtime.store.iter_latest_records(sort="recent")
    selected = _selected_records(
        all_records,
        record_ids=record_ids,
        limit=limit,
        source_business_id=source_business_id,
    )
    entries: list[_PlanEntry] = []
    for original in selected:
        parser_payload = original.get("parser_payload")
        if not isinstance(parser_payload, Mapping):
            continue
        source_url = _record_source_url(original)
        cheap_classification = classify_record_business(
            parser_payload=parser_payload,
            record_family_hint=original.get("record_family"),
            business_id_hint=original.get("business_id"),
            page_url=source_url,
            source_url=source_url,
        )
        stored_business_id = str(original.get("business_id") or "")
        proposed_business_id = str(cheap_classification.business_id or "")
        if (
            proposed_business_id == stored_business_id == "capital_increase"
            and target_business_id == "equity_transfer"
            and _EQUITY_PROJECT_CODE_PATTERN.match(
                str(original.get("project_code") or "").strip()
            )
        ):
            proposed_business_id = target_business_id
        if proposed_business_id != target_business_id:
            continue

        original_snapshot = _record_snapshot(original)
        if (
            not original_snapshot["project_code"]
            or not original_snapshot["source_id"]
            or not original_snapshot["exchange"]
        ):
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="original_identity_incomplete",
                    original_snapshot=original_snapshot,
                )
            )
            continue

        matching_targets = [
            record
            for record in all_records
            if _same_target_scope(
                record,
                original=original,
                proposed_business_id=proposed_business_id,
            )
        ]
        classified_targets = [
            record
            for record in matching_targets
            if str(record.get("state") or "") in _TARGET_STATES
        ]
        if len(classified_targets) > 1:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="multiple_classified_targets",
                    original_snapshot=original_snapshot,
                )
            )
            continue

        source_path = reprocess_source_path(dict(original))
        if not source_path or not os.path.isfile(source_path):
            if len(classified_targets) != 1:
                entries.append(
                    _blocked_entry(
                        original,
                        proposed_business_id=proposed_business_id,
                        reason_code="source_missing_without_classified_target",
                        original_snapshot=original_snapshot,
                    )
                )
                continue
            target = classified_targets[0]
            target_parser_payload = target.get("parser_payload")
            if not isinstance(target_parser_payload, Mapping):
                entries.append(
                    _blocked_entry(
                        original,
                        proposed_business_id=proposed_business_id,
                        reason_code="classified_target_parser_payload_invalid",
                        original_snapshot=original_snapshot,
                    )
                )
                continue
            locked_classification = classify_record_business(
                parser_payload=target_parser_payload,
                record_family_hint=target.get("record_family"),
                page_url=source_url,
                source_url=source_url,
            )
            if locked_classification.business_id != proposed_business_id:
                entries.append(
                    _blocked_entry(
                        original,
                        proposed_business_id=proposed_business_id,
                        reason_code="classified_target_payload_disagrees",
                        original_snapshot=original_snapshot,
                    )
                )
                continue
            target_snapshot = _record_snapshot(target)
            proof = _proof_common(
                evidence_kind="locked_target_revision",
                original=original,
                proposed_business_id=proposed_business_id,
                source_url=source_url,
                parser_payload=target_parser_payload,
            )
            proof.update(
                {
                    "target_record_id": target_snapshot["record_id"],
                    "target_revision_id": target_snapshot["revision_id"],
                    "target_revision_hash": target_snapshot["revision_hash"],
                }
            )
            _finish_proof(proof)
            entries.append(
                _PlanEntry(
                    item={
                        **_base_item(original, proposed_business_id=proposed_business_id),
                        "action": "supersede_existing",
                        "reason_code": "locked_target_revision_evidence",
                        "apply_supported": True,
                        "target_record_id": target_snapshot["record_id"],
                        "original_snapshot": original_snapshot,
                        "target_snapshot": target_snapshot,
                        "proof": _public_proof(proof),
                    },
                    original_snapshot=original_snapshot,
                    target_snapshot=target_snapshot,
                    proof=proof,
                    target_record=None,
                )
            )
            continue

        try:
            source_sha256_before = _regular_file_sha256(
                source_path,
                field="business reclassification source",
            )
            fresh_payload_raw = runtime.parser(source_path)
            if not isinstance(fresh_payload_raw, Mapping):
                raise TypeError("parser result must be an object")
            fresh_parser_payload = dict(fresh_payload_raw)
            source_sha256_after = _regular_file_sha256(
                source_path,
                field="business reclassification source recheck",
            )
        except Exception:  # noqa: BLE001
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="fresh_parse_failed",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        if source_sha256_before != source_sha256_after:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="source_changed_during_fresh_parse",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        if _project_code(fresh_parser_payload) != original_snapshot["project_code"]:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="fresh_parser_project_code_mismatch",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        fresh_source_id = _canonical_source(
            fresh_parser_payload.get("source_id")
            or fresh_parser_payload.get("交易所")
            or fresh_parser_payload.get("exchange")
        )
        if fresh_source_id != original_snapshot["source_id"]:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="fresh_parser_source_mismatch",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        fresh_classification = classify_record_business(
            parser_payload=fresh_parser_payload,
            record_family_hint=original.get("record_family"),
            page_url=source_url,
            source_url=source_url,
        )
        if (
            fresh_classification.record_family != original.get("record_family")
            or fresh_classification.business_id != proposed_business_id
        ):
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="fresh_parser_classification_disagrees",
                    original_snapshot=original_snapshot,
                )
            )
            continue

        proof = _proof_common(
            evidence_kind="fresh_source_parse",
            original=original,
            proposed_business_id=proposed_business_id,
            source_url=source_url,
            parser_payload=fresh_parser_payload,
        )
        proof.update(
            {
                "source_path": os.path.abspath(source_path),
                "source_sha256": source_sha256_after,
            }
        )
        _finish_proof(proof)

        if classified_targets:
            target_snapshot = _record_snapshot(classified_targets[0])
            entries.append(
                _PlanEntry(
                    item={
                        **_base_item(original, proposed_business_id=proposed_business_id),
                        "action": "supersede_existing",
                        "reason_code": "fresh_source_parse_confirmed",
                        "apply_supported": True,
                        "target_record_id": target_snapshot["record_id"],
                        "original_snapshot": original_snapshot,
                        "target_snapshot": target_snapshot,
                        "proof": _public_proof(proof),
                    },
                    original_snapshot=original_snapshot,
                    target_snapshot=target_snapshot,
                    proof=proof,
                    target_record=None,
                )
            )
            continue

        non_ready_targets = [
            record
            for record in matching_targets
            if str(record.get("state") or "") not in _TARGET_STATES
        ]
        if len(non_ready_targets) > 1:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="multiple_non_ready_target_shells",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        target_snapshot = (
            _record_snapshot(non_ready_targets[0]) if non_ready_targets else None
        )
        target_id = (
            str(target_snapshot["record_id"])
            if target_snapshot is not None
            else _target_record_id(original_snapshot["record_id"], proposed_business_id)
        )
        try:
            target_record = _build_target_record(
                runtime=runtime,
                original=original,
                parser_payload=fresh_parser_payload,
                classification=fresh_classification,
                source_path=os.path.abspath(source_path),
                source_url=source_url,
                target_record_id=target_id,
            )
        except Exception:  # noqa: BLE001
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code="target_build_failed",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        target_state = str(getattr(target_record.state, "value", target_record.state) or "")
        if target_state not in _TARGET_STATES:
            entries.append(
                _blocked_entry(
                    original,
                    proposed_business_id=proposed_business_id,
                    reason_code=f"target_state_unsupported:{target_state}",
                    original_snapshot=original_snapshot,
                )
            )
            continue
        entries.append(
            _PlanEntry(
                item={
                    **_base_item(original, proposed_business_id=proposed_business_id),
                    "action": "create_target_needed",
                    "reason_code": "fresh_source_parse_confirmed",
                    "apply_supported": True,
                    "target_record_id": target_id,
                    "target_shell_record_id": (
                        target_snapshot["record_id"] if target_snapshot is not None else ""
                    ),
                    "original_snapshot": original_snapshot,
                    "target_snapshot": target_snapshot,
                    "proof": _public_proof(proof),
                },
                original_snapshot=original_snapshot,
                target_snapshot=target_snapshot,
                proof=proof,
                target_record=target_record,
            )
        )
    return entries, len(selected)


def _plan_payload(
    entries: list[_PlanEntry],
    *,
    scanned_count: int,
    mode: str,
    source_business_id: str,
    target_business_id: str,
) -> dict[str, Any]:
    _validate_reclassification_scope(
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )
    action_counts = Counter(str(entry.item.get("action") or "") for entry in entries)
    actionable_count = sum(bool(entry.item.get("apply_supported")) for entry in entries)
    return {
        "mode": mode,
        "applied": False,
        "scope": {
            "record_family": "listing",
            "source_business_id": source_business_id,
            "target_business_id": target_business_id,
            "original_states": sorted(_ORIGINAL_STATES),
            "target_states": sorted(_TARGET_STATES),
        },
        "summary": {
            "scanned_repairable_listing_count": scanned_count,
            "misclassification_candidate_count": len(entries),
            "actionable_count": actionable_count,
            "blocked_count": len(entries) - actionable_count,
            "action_counts": dict(sorted(action_counts.items())),
        },
        "items": [dict(entry.item) for entry in entries],
    }


def build_business_reclassification_plan(
    *,
    runtime: BusinessReclassificationRuntime,
    record_ids: list[str] | None = None,
    limit: int | None = None,
    source_business_id: str = "capital_increase",
    target_business_id: str = "equity_transfer",
) -> dict[str, Any]:
    """Build a read-only plan; this function performs no database or artifact writes."""

    entries, scanned_count = _build_plan_entries(
        runtime=runtime,
        record_ids=record_ids,
        limit=limit,
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )
    return _plan_payload(
        entries,
        scanned_count=scanned_count,
        mode="report_only",
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )


def apply_business_reclassification_plan(
    *,
    runtime: BusinessReclassificationRuntime,
    record_ids: list[str] | None = None,
    limit: int | None = None,
    source_business_id: str = "capital_increase",
    target_business_id: str = "equity_transfer",
) -> tuple[int, dict[str, Any]]:
    """Apply actionable entries through atomic store operations and one journal."""

    entries, scanned_count = _build_plan_entries(
        runtime=runtime,
        record_ids=record_ids,
        limit=limit,
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )
    plan = _plan_payload(
        entries,
        scanned_count=scanned_count,
        mode="apply",
        source_business_id=source_business_id,
        target_business_id=target_business_id,
    )
    actionable = [entry for entry in entries if bool(entry.item.get("apply_supported"))]
    if not actionable:
        return 0, {**plan, "applied": True, "results": []}

    coordinator = WriteCoordinator(store=runtime.store)

    def _run(operation):
        results: list[dict[str, Any]] = []
        for entry in actionable:
            record_id = str(entry.item.get("record_id") or "")
            try:
                result = runtime.store.consolidate_business_reclassification(
                    original_snapshot=entry.original_snapshot,
                    target_snapshot=entry.target_snapshot,
                    target_record=entry.target_record,
                    proof=entry.proof or {},
                )
                results.append(
                    {"record_id": record_id, "status": "succeeded", "result": result}
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "record_id": record_id,
                        "status": "failed",
                        "error": {
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                        },
                    }
                )
        operation.update_manifest({"plan": plan, "results": results})
        if any(item["status"] == "failed" for item in results):
            operation.fail(
                {
                    "code": "partial_failure",
                    "message": "one or more business reclassifications failed",
                }
            )
        else:
            operation.succeed()
        return results

    operation_result = coordinator.write_operation(
        "business_reclassification_repair",
        {"record_ids": [str(entry.item.get("record_id") or "") for entry in actionable]},
        lambda operation: {
            "operation_id": operation.operation_id,
            "results": _run(operation),
        },
    )
    operation_id = str(operation_result["operation_id"])
    results = list(operation_result["results"])
    failed = any(str(item.get("status") or "") == "failed" for item in results)
    payload = {
        **plan,
        "applied": True,
        "operation_id": operation_id,
        "results": results,
    }
    if failed:
        payload["error"] = {
            "code": "partial_failure",
            "message": "one or more business reclassifications failed",
        }
        return 4, payload
    return 0, payload


__all__ = [
    "BusinessReclassificationRuntime",
    "apply_business_reclassification_plan",
    "build_business_reclassification_plan",
]
