"""Audit persisted download artifacts before chunk-state resume decisions."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Iterable

from peap_core.source_catalog import canonical_source_code
from scripts._paths import reject_forbidden_real_peap_path

from .artifact_truth import resolve_artifact_evidence_verdict
from .download_tasks import DownloadTaskSpec
from .downloaders.common import parse_loose_date

STALE_DOWNLOAD_OPERATION_KIND = "download_artifact_audit"
STALE_DOWNLOAD_OPERATION_CODE = "stale_download_record"
UNSAFE_DOWNLOAD_SKIP_EVIDENCE_STATUSES = {
    "stale_reference",
    "invalid_shell",
    "present_unverified",
    "identity_mismatch",
}


@dataclass(frozen=True)
class StaleDownloadArtifact:
    record_id: str
    task_id: str
    project_code: str
    project_name: str
    listing_date: dt.date | None
    source_file: str
    archive_path: str
    evidence_verdict: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "project_code": self.project_code,
            "project_name": self.project_name,
            "listing_date": self.listing_date.isoformat() if self.listing_date else "",
            "source_file": self.source_file,
            "archive_path": self.archive_path,
            "evidence_verdict": self.evidence_verdict,
        }


@dataclass(frozen=True)
class TaskArtifactAudit:
    task_id: str
    stale_records: tuple[StaleDownloadArtifact, ...] = ()
    dated_stale_records: dict[dt.date, tuple[StaleDownloadArtifact, ...]] = field(default_factory=dict)
    unresolved_stale_records: tuple[StaleDownloadArtifact, ...] = ()

    @property
    def stale_count(self) -> int:
        return len(self.stale_records)

    def intersects(self, start: dt.date, end: dt.date) -> bool:
        if self.unresolved_stale_records:
            return True
        return any(start <= item_date <= end for item_date in self.dated_stale_records)

    def to_dict(self, *, sample_limit: int = 10) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "stale_count": self.stale_count,
            "unresolved_stale_count": len(self.unresolved_stale_records),
            "stale_dates": [item.isoformat() for item in sorted(self.dated_stale_records)],
            "samples": [item.to_dict() for item in self.stale_records[:sample_limit]],
        }


@dataclass(frozen=True)
class DownloadArtifactAudit:
    by_task_id: dict[str, TaskArtifactAudit] = field(default_factory=dict)
    db_path: str = ""

    @property
    def stale_count(self) -> int:
        return sum(item.stale_count for item in self.by_task_id.values())

    def for_task(self, task_id: str) -> TaskArtifactAudit | None:
        return self.by_task_id.get(task_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "stale_count": self.stale_count,
            "tasks": {
                task_id: audit.to_dict()
                for task_id, audit in sorted(self.by_task_id.items())
                if audit.stale_count
            },
        }


def _request_value(args: object, name: str, default: str = "all") -> str:
    return str(getattr(args, name, default) or default).strip() or default


def _json_object(value: object, *, field_name: str = "JSON value") -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must decode to an object")
    return dict(value)


def _canonical_record_json_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("canonical_record_json must decode to an object")
    return dict(value)


def _canonical_scope_components(canonical_record: object) -> tuple[str, str, str]:
    record = _canonical_record_json_object(canonical_record)
    business_identity = _json_object(
        record.get("business_identity"),
        field_name="canonical_record_json.business_identity",
    )
    source_identity = _json_object(
        record.get("source_identity"),
        field_name="canonical_record_json.source_identity",
    )
    record_family = str(
        record.get("record_family")
        or business_identity.get("record_family")
        or source_identity.get("record_family")
        or ""
    ).strip()
    business_id = str(
        business_identity.get("business_id")
        or source_identity.get("business_id")
        or record.get("business_id")
        or ""
    ).strip()
    exchange = _task_source_id(source_identity.get("source_id") or source_identity.get("exchange") or "")
    return record_family, business_id, exchange


def _record_scope_components(row: sqlite3.Row) -> tuple[str, str, str]:
    canonical_family, canonical_business, canonical_exchange = _canonical_scope_components(
        row["canonical_record_json"]
    )
    exchange = _task_source_id(row["exchange"]) or canonical_exchange
    record_family = str(row["record_family"] or "").strip() or canonical_family
    business_id = str(row["business_id"] or "").strip() or canonical_business
    return exchange, record_family, business_id


def _row_with_canonical_identity(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    source_identity_json = _json_object(
        data.get("source_identity_json"),
        field_name="source_identity_json",
    )
    canonical_record = _canonical_record_json_object(data.get("canonical_record_json"))
    if not canonical_record:
        if source_identity_json:
            data["source_identity_json"] = source_identity_json
        return data
    business_identity = _json_object(
        canonical_record.get("business_identity"),
        field_name="canonical_record_json.business_identity",
    )
    source_identity = _json_object(
        canonical_record.get("source_identity"),
        field_name="canonical_record_json.source_identity",
    )
    if not source_identity_json:
        source_identity_json = dict(source_identity)
    elif source_identity:
        merged_source_identity = dict(source_identity)
        merged_source_identity.update(source_identity_json)
        source_identity_json = merged_source_identity
    if source_identity_json:
        data["source_identity_json"] = source_identity_json
    if not str(data.get("record_family") or "").strip():
        data["record_family"] = (
            canonical_record.get("record_family")
            or business_identity.get("record_family")
            or source_identity.get("record_family")
            or ""
        )
    if not str(data.get("business_id") or "").strip():
        data["business_id"] = (
            business_identity.get("business_id")
            or source_identity.get("business_id")
            or canonical_record.get("business_id")
            or ""
        )
    return data


def _matches_request_scope(
    *,
    args: object,
    exchange: str,
    record_family: str,
    business_id: str,
    listing_date: dt.date | None,
) -> bool:
    request_exchange = _request_value(args, "exchange")
    request_family = _request_value(args, "record_family")
    request_business = _request_value(args, "business_id")
    if request_exchange != "all" and exchange != request_exchange:
        return False
    if request_family != "all" and record_family != request_family:
        return False
    if request_business != "all" and business_id != request_business:
        return False

    start = parse_loose_date(getattr(args, "start_date", None))
    end = parse_loose_date(getattr(args, "end_date", None))
    if listing_date is None:
        return True
    if start is not None and listing_date < start:
        return False
    if end is not None and listing_date > end:
        return False
    return True


def _task_source_id(raw_exchange: object) -> str:
    return canonical_source_code(raw_exchange, allow_substring=False)


def _audit_is_read_only(args: object) -> bool:
    return any(
        bool(getattr(args, name, False))
        for name in ("dry_run", "validate_only", "validation_only", "split_plan_only")
    )


def _evidence_verdict_to_dict(verdict: object) -> dict[str, object]:
    text_fields = (
        "status",
        "logical_record_identity",
        "identity_confidence",
        "authoritative_path",
        "inspection_openable_path",
        "reason_code",
    )
    result: dict[str, object] = {}
    for field_name in text_fields:
        if not hasattr(verdict, field_name):
            raise TypeError(f"evidence verdict missing required field: {field_name}")
        value = getattr(verdict, field_name)
        if not isinstance(value, str):
            raise TypeError(f"evidence verdict {field_name} must be a string")
        result[field_name] = value

    if not hasattr(verdict, "safe_evidence"):
        raise TypeError("evidence verdict missing required field: safe_evidence")
    safe_evidence = verdict.safe_evidence
    if not isinstance(safe_evidence, Mapping):
        raise TypeError("evidence verdict safe_evidence must be a mapping")
    result["safe_evidence"] = dict(safe_evidence)
    return result


def _reject_forbidden_path_if_declared(name: str, path_value: object) -> None:
    candidate = str(path_value or "").strip()
    if candidate:
        reject_forbidden_real_peap_path(name, candidate)


def build_download_artifact_audit(
    config_obj: object,
    *,
    args: object,
    tasks: Iterable[DownloadTaskSpec],
) -> DownloadArtifactAudit:
    """Find records whose persisted artifact paths no longer exist locally.

    The audit is deliberately download-free. Its only persistent side effect is to mark
    affected canonical records as having a missing artifact, so browse/review surfaces
    stop presenting stale downloaded state as healthy.
    """

    raw_db_path = str(getattr(config_obj, "STREAMING_DB_PATH", "") or "").strip()
    if not raw_db_path:
        raise ValueError("STREAMING_DB_PATH must be configured before download artifact audit")

    db_path = os.path.abspath(raw_db_path)
    _reject_forbidden_path_if_declared("STREAMING_DB_PATH", db_path)
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"STREAMING_DB_PATH database not found: {db_path}")

    task_ids = {spec.task_id for spec in tasks}
    artifacts_by_task: dict[str, list[StaleDownloadArtifact]] = {}
    stale_record_ids: list[str] = []
    read_only = _audit_is_read_only(args)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT records.record_id AS record_id,
                   records.record_family AS record_family,
                   records.business_id AS business_id,
                   records.exchange AS exchange,
                   records.listing_date AS listing_date,
                   records.source_file AS source_file,
                   records.archive_path AS archive_path,
                   records.source_identity_json AS source_identity_json,
                   records.project_code AS project_code,
                   records.project_name AS project_name,
                   revisions.canonical_record_json AS canonical_record_json
            FROM records
            LEFT JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE COALESCE(records.source_file, '') <> ''
               OR COALESCE(records.archive_path, '') <> ''
            """
        ).fetchall()

        for row in rows:
            exchange, record_family, business_id = _record_scope_components(row)
            task_id = f"{exchange}:{record_family}:{business_id}"
            if task_id not in task_ids:
                continue
            listing_date = parse_loose_date(row["listing_date"])
            if not _matches_request_scope(
                args=args,
                exchange=exchange,
                record_family=record_family,
                business_id=business_id,
                listing_date=listing_date,
            ):
                continue
            source_file = str(row["source_file"] or "").strip()
            archive_path = str(row["archive_path"] or "").strip()
            _reject_forbidden_path_if_declared("record.source_file", source_file)
            _reject_forbidden_path_if_declared("record.archive_path", archive_path)
            audit_record = _row_with_canonical_identity(row)
            verdict = resolve_artifact_evidence_verdict(audit_record)
            if verdict.status not in UNSAFE_DOWNLOAD_SKIP_EVIDENCE_STATUSES:
                continue
            artifact = StaleDownloadArtifact(
                record_id=str(row["record_id"] or ""),
                task_id=task_id,
                project_code=str(row["project_code"] or ""),
                project_name="",
                listing_date=listing_date,
                source_file=source_file,
                archive_path=archive_path,
                evidence_verdict=_evidence_verdict_to_dict(verdict),
            )
            artifacts_by_task.setdefault(task_id, []).append(artifact)
            stale_record_ids.append(artifact.record_id)

        if stale_record_ids and not read_only:
            now = dt.datetime.utcnow().isoformat(timespec="seconds")
            conn.executemany(
                """
                UPDATE records
                SET artifact_status = 'missing',
                    last_operation_kind = ?,
                    last_operation_code = ?,
                    last_operation_message = ?,
                    last_operation_at = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                [
                    (
                        STALE_DOWNLOAD_OPERATION_KIND,
                        STALE_DOWNLOAD_OPERATION_CODE,
                        "Persisted download artifact path is missing; next download run must not trust stale chunk state.",
                        now,
                        now,
                        record_id,
                    )
                    for record_id in stale_record_ids
                ],
            )

    audits: dict[str, TaskArtifactAudit] = {}
    for task_id, artifacts in artifacts_by_task.items():
        dated: dict[dt.date, list[StaleDownloadArtifact]] = {}
        unresolved: list[StaleDownloadArtifact] = []
        for artifact in artifacts:
            if artifact.listing_date is None:
                unresolved.append(artifact)
            else:
                dated.setdefault(artifact.listing_date, []).append(artifact)
        audits[task_id] = TaskArtifactAudit(
            task_id=task_id,
            stale_records=tuple(artifacts),
            dated_stale_records={key: tuple(value) for key, value in dated.items()},
            unresolved_stale_records=tuple(unresolved),
        )

    return DownloadArtifactAudit(by_task_id=audits, db_path=db_path)
