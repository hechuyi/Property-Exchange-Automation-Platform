"""Read-only administrative data-health helpers."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from peap_core.runtime_paths import RuntimeWorkspacePaths, resolve_runtime_workspace_paths
from scripts._paths import reject_forbidden_real_peap_path

from .artifact_truth import (
    ARTIFACT_EVIDENCE_REPORT_CLASSIFICATIONS,
    classify_artifact_evidence_verdict,
    resolve_artifact_evidence_verdict,
)
from .streaming_store import SCHEMA_VERSION


def _readonly_connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (str(table_name or ""),),
    ).fetchone()
    return row is not None


def _json_loads(raw: Any, *, default: Any) -> Any:
    if raw is None:
        return default
    expected_type: type[Any] | None = None
    expected_label = ""
    if isinstance(default, dict):
        expected_type = dict
        expected_label = "object"
    elif isinstance(default, list):
        expected_type = list
        expected_label = "list"
    if isinstance(raw, (dict, list)):
        if expected_type is not None and not isinstance(raw, expected_type):
            raise ValueError(f"expected JSON {expected_label}")
        return raw
    text = str(raw).strip()
    if not text:
        return default
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        if expected_type is not None:
            raise
        return default
    if expected_type is not None and not isinstance(payload, expected_type):
        raise ValueError(f"expected JSON {expected_label}")
    return payload


def _json_object(raw: Any) -> dict[str, Any]:
    return dict(_json_loads(raw, default={}))


def _json_list(raw: Any) -> list[Any]:
    return list(_json_loads(raw, default=[]))


_CONFLICT_SUFFIX_RE = re.compile(r"__conflict\d+(\.[^.]+)?$", re.IGNORECASE)
_FORBIDDEN_REAL_WORKSPACE_CLASSIFICATION = "forbidden_real_workspace"
_FORBIDDEN_REAL_WORKSPACE_REASON = "real_workspace_path_denied"
_REQUIRED_CORE_SCHEMA_TABLES = ("records", "jobs", "exports")


def _derive_archive_base_path(archive_path: str) -> str:
    path = str(archive_path or "").strip()
    if not path:
        return ""
    stem, ext = os.path.splitext(path)
    if not ext:
        return ""
    base_stem = _CONFLICT_SUFFIX_RE.sub("", stem)
    if base_stem == stem:
        return ""
    return f"{base_stem}{ext}"


def _path_exists_for_diagnostic(path: str) -> bool:
    candidate = str(path or "").strip()
    if not candidate:
        return False
    try:
        reject_forbidden_real_peap_path("archive_conflict.path_diagnostic", candidate)
    except ValueError:
        return False
    return os.path.exists(candidate)


def _first_forbidden_real_peap_path(*items: tuple[str, object]) -> tuple[str, str] | None:
    for name, path_value in items:
        candidate = str(path_value or "").strip()
        if not candidate:
            continue
        try:
            reject_forbidden_real_peap_path(name, candidate)
        except ValueError as exc:
            return name, str(exc)
    return None


def _append_forbidden_archive_conflict_record(
    payload: dict[str, Any],
    *,
    row: sqlite3.Row,
    source_identity: dict[str, Any],
    archive_conflict_path: str,
    forbidden_field: str,
    forbidden_message: str,
) -> None:
    old_hint_path = str(source_identity.get("original_source_file") or "").strip()
    base_canonical_path = _derive_archive_base_path(archive_conflict_path)
    payload["total_archive_conflict_findings"] += 1
    payload["counts"][_FORBIDDEN_REAL_WORKSPACE_CLASSIFICATION] += 1
    payload["records"].append(
        {
            "record_id": str(row["record_id"] or ""),
            "project_code": str(row["project_code"] or ""),
            "state": str(row["state"] or ""),
            "classification": _FORBIDDEN_REAL_WORKSPACE_CLASSIFICATION,
            "reason_code": _FORBIDDEN_REAL_WORKSPACE_REASON,
            "identity_confidence": "unresolved",
            "path_diagnostic": {
                "old_source_hint_path": old_hint_path,
                "old_hint_exists": False,
                "new_archive_path": archive_conflict_path,
                "new_exists": False,
                "base_canonical_path": base_canonical_path,
                "base_exists": False,
                "forbidden_path_field": forbidden_field,
                "forbidden_path_message": forbidden_message,
            },
        }
    )


def _build_archive_conflict_classification(conn: sqlite3.Connection) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "report_only",
        "total_archive_conflict_findings": 0,
        "counts": {
            "verified": 0,
            "present_unverified": 0,
            "stale_reference": 0,
            "undeclared": 0,
            "invalid_shell": 0,
            "identity_mismatch": 0,
            "shared_official_page": 0,
            _FORBIDDEN_REAL_WORKSPACE_CLASSIFICATION: 0,
        },
        "records": [],
    }
    if not (_table_exists(conn, "records") and _table_exists(conn, "record_revisions")):
        return payload

    rows = conn.execute(
        """
        SELECT
            records.record_id,
            records.record_family,
            records.business_id,
            records.state,
            records.project_code,
            records.exchange,
            records.source_file,
            records.archive_path,
            records.source_identity_json,
            revisions.findings_json
        FROM records
        JOIN record_revisions AS revisions
          ON revisions.revision_id = records.latest_revision_id
        """
    ).fetchall()
    for row in rows:
        findings = _json_list(row["findings_json"])
        archive_conflict_path = str(row["archive_path"] or "").strip()
        has_archive_conflict = False
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValueError(f"archive conflict finding at index {index} must be an object")
            if str(finding.get("type") or "").strip() != "archive_conflict":
                continue
            has_archive_conflict = True
            if "evidence" in finding:
                evidence = finding.get("evidence")
                if not isinstance(evidence, dict):
                    raise ValueError("archive_conflict evidence must be an object")
                archive_conflict_path = str(evidence.get("archive_path") or archive_conflict_path).strip()
            break
        if not has_archive_conflict:
            continue
        source_identity = _json_object(row["source_identity_json"])

        forbidden = _first_forbidden_real_peap_path(
            ("record.source_file", row["source_file"]),
            ("record.archive_path", row["archive_path"]),
            ("archive_conflict.archive_path", archive_conflict_path),
            ("source_identity.original_source_file", source_identity.get("original_source_file")),
        )
        if forbidden is not None:
            forbidden_field, forbidden_message = forbidden
            _append_forbidden_archive_conflict_record(
                payload,
                row=row,
                source_identity=source_identity,
                archive_conflict_path=archive_conflict_path,
                forbidden_field=forbidden_field,
                forbidden_message=forbidden_message,
            )
            continue

        record = dict(row)
        verdict = resolve_artifact_evidence_verdict(record)
        classification, reason_code = classify_artifact_evidence_verdict(record, verdict)
        old_hint_path = str(source_identity.get("original_source_file") or "").strip()
        old_hint_exists = _path_exists_for_diagnostic(old_hint_path)
        new_exists = _path_exists_for_diagnostic(archive_conflict_path)
        base_canonical_path = _derive_archive_base_path(archive_conflict_path)
        base_exists = _path_exists_for_diagnostic(base_canonical_path)

        payload["total_archive_conflict_findings"] += 1
        payload["counts"][classification] += 1
        payload["records"].append(
            {
                "record_id": str(row["record_id"] or ""),
                "project_code": str(row["project_code"] or ""),
                "state": str(row["state"] or ""),
                "classification": classification,
                "reason_code": reason_code,
                "identity_confidence": verdict.identity_confidence,
                "path_diagnostic": {
                    "old_source_hint_path": old_hint_path,
                    "old_hint_exists": old_hint_exists,
                    "new_archive_path": archive_conflict_path,
                    "new_exists": new_exists,
                    "base_canonical_path": base_canonical_path,
                    "base_exists": base_exists,
                },
            }
        )
    return payload


def _build_artifact_evidence_report(conn: sqlite3.Connection) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "report_only",
        "present_unverified_count": 0,
        "stale_reference_count": 0,
        "undeclared_count": 0,
        "invalid_shell_count": 0,
        "identity_mismatch_count": 0,
        "forbidden_real_workspace_count": 0,
        "records": [],
    }
    if not _table_exists(conn, "records"):
        return payload

    rows = conn.execute(
        """
        SELECT
            record_id,
            record_family,
            business_id,
            project_code,
            exchange,
            source_file,
            archive_path,
            source_identity_json
        FROM records
        ORDER BY record_id
        """
    ).fetchall()
    for row in rows:
        record = dict(row)
        authoritative_path = str(record.get("archive_path") or record.get("source_file") or "").strip()
        if authoritative_path:
            try:
                reject_forbidden_real_peap_path("record.authoritative_path", authoritative_path)
            except ValueError:
                payload["forbidden_real_workspace_count"] += 1
                payload["records"].append(
                    {
                        "record_id": str(record.get("record_id") or ""),
                        "classification": _FORBIDDEN_REAL_WORKSPACE_CLASSIFICATION,
                        "reason_code": _FORBIDDEN_REAL_WORKSPACE_REASON,
                        "identity_confidence": "unresolved",
                        "inspection_eligible": False,
                    }
                )
                continue
        verdict = resolve_artifact_evidence_verdict(record)
        classification, reason_code = classify_artifact_evidence_verdict(record, verdict)
        if classification not in ARTIFACT_EVIDENCE_REPORT_CLASSIFICATIONS:
            continue
        count_key = f"{classification}_count"
        payload[count_key] += 1
        payload["records"].append(
            {
                "record_id": str(record.get("record_id") or ""),
                "classification": classification,
                "reason_code": reason_code,
                "identity_confidence": verdict.identity_confidence,
                "inspection_eligible": bool(verdict.inspection_openable_path),
            }
        )
    return payload


def build_data_health_summary(*, app_home: str) -> tuple[int, dict[str, Any]]:
    config = resolve_runtime_workspace_paths(app_home=app_home)
    db_path = os.path.abspath(config.streaming_db_path)
    forbidden_payload = _forbidden_workspace_payload(config=config, app_home=app_home, db_path=db_path)
    if forbidden_payload is not None:
        return 2, forbidden_payload
    payload: dict[str, Any] = {
        "app_home": config.app_home,
        "db": {
            "path": db_path,
            "exists": os.path.exists(db_path),
        },
        "schema": {
            "expected_user_version": SCHEMA_VERSION,
            "user_version": 0,
            "matches": False,
        },
        "operation_journal": {
            "table_exists": False,
            "pending_count": 0,
            "failed_count": 0,
        },
        "counts": {
            "records": 0,
            "jobs": 0,
            "exports": 0,
        },
        "findings": [],
        "healthy": True,
        "result": "ok",
    }
    if not os.path.exists(db_path):
        payload["result"] = "db_missing"
        payload["findings"].append({"code": "db_missing", "message": "streaming database does not exist"})
        return 2, payload

    try:
        with _readonly_connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            user_version_row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(user_version_row[0] if user_version_row else 0)
            payload["schema"]["user_version"] = user_version
            payload["schema"]["matches"] = user_version == SCHEMA_VERSION
            operation_journal_exists = _table_exists(conn, "operation_journal")
            payload["operation_journal"]["table_exists"] = operation_journal_exists
            for table_name in _REQUIRED_CORE_SCHEMA_TABLES:
                if not _table_exists(conn, table_name):
                    payload["findings"].append(
                        {
                            "code": "required_table_missing",
                            "table": table_name,
                            "message": f"required core schema table is missing: {table_name}",
                        }
                    )
            for table_name, field_name in (("records", "records"), ("jobs", "jobs"), ("exports", "exports")):
                if _table_exists(conn, table_name):
                    payload["counts"][field_name] = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
            if operation_journal_exists:
                payload["operation_journal"]["pending_count"] = int(
                    conn.execute("SELECT COUNT(*) FROM operation_journal WHERE status = 'pending'").fetchone()[0]
                )
                payload["operation_journal"]["failed_count"] = int(
                    conn.execute("SELECT COUNT(*) FROM operation_journal WHERE status = 'failed'").fetchone()[0]
                )
            else:
                payload["findings"].append(
                    {"code": "operation_journal_missing", "message": "operation_journal table is missing"}
                )
            try:
                payload["artifact_evidence_report"] = _build_artifact_evidence_report(conn)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                payload["artifact_evidence_report"] = {
                    "mode": "uninspectable",
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                }
                payload["findings"].append(
                    {
                        "code": "corrupt_json",
                        "scope": "artifact_evidence_report",
                        "message": str(exc),
                    }
                )
            try:
                payload["archive_conflict_classification"] = _build_archive_conflict_classification(conn)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                payload["archive_conflict_classification"] = {
                    "mode": "uninspectable",
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                }
                payload["findings"].append(
                    {
                        "code": "corrupt_json",
                        "scope": "archive_conflict_classification",
                        "message": str(exc),
                    }
                )
            if user_version != SCHEMA_VERSION:
                payload["findings"].append(
                    {
                        "code": "schema_version_mismatch",
                        "message": f"user_version={user_version}, expected={SCHEMA_VERSION}",
                    }
                )
            artifact_report = payload.get("artifact_evidence_report")
            if (
                isinstance(artifact_report, dict)
                and int(artifact_report.get("forbidden_real_workspace_count") or 0) > 0
            ):
                payload["findings"].append(
                    {
                        "code": "forbidden_real_workspace",
                        "scope": "artifact_evidence_report",
                        "message": "artifact evidence references a forbidden real PEAP workspace path",
                    }
                )
            if payload["operation_journal"]["pending_count"] > 0:
                payload["findings"].append(
                    {
                        "code": "pending_operations",
                        "message": f"{payload['operation_journal']['pending_count']} pending operation journal rows",
                    }
                )
            if payload["operation_journal"]["failed_count"] > 0:
                payload["findings"].append(
                    {
                        "code": "failed_operations",
                        "message": f"{payload['operation_journal']['failed_count']} failed operation journal rows",
                    }
                )
    except sqlite3.DatabaseError as exc:
        payload["result"] = "db_uninspectable"
        payload["healthy"] = False
        payload["findings"].append({"code": "db_uninspectable", "message": str(exc)})
        return 2, payload

    finding_codes = {str(item.get("code") or "") for item in payload["findings"] if isinstance(item, dict)}
    if "corrupt_json" in finding_codes:
        payload["result"] = "corrupt_json"
        payload["healthy"] = False
        return 2, payload
    if "schema_version_mismatch" in finding_codes:
        payload["result"] = "schema_version_mismatch"
        payload["healthy"] = False
        return 2, payload
    if "forbidden_real_workspace" in finding_codes:
        payload["result"] = "forbidden_real_workspace"
        payload["healthy"] = False
        return 2, payload
    if "operation_journal_missing" in finding_codes:
        payload["result"] = "operation_journal_missing"
        payload["healthy"] = False
        return 2, payload
    if "required_table_missing" in finding_codes:
        payload["result"] = "schema_incomplete"
        payload["healthy"] = False
        return 2, payload
    if {"pending_operations", "failed_operations"} & finding_codes:
        payload["result"] = "operation_journal_unhealthy"
        payload["healthy"] = False
        return 2, payload

    return 0, payload


def _forbidden_workspace_payload(
    *,
    config: RuntimeWorkspacePaths,
    app_home: str,
    db_path: str,
) -> dict[str, Any] | None:
    for name, path_value in (
        ("app_home", app_home),
        ("runtime.app_home", config.app_home),
        ("runtime.data_root", config.data_root),
        ("runtime.archive_root", config.archive_root),
        ("runtime.export_root", config.export_root),
        ("runtime.cache_dir", config.cache_dir),
        ("runtime.streaming_db_path", db_path),
    ):
        try:
            reject_forbidden_real_peap_path(name, path_value)
        except ValueError as exc:
            return {
                "app_home": config.app_home,
                "db_path": db_path,
                "result": "forbidden_real_workspace",
                "error": {
                    "code": "forbidden_real_workspace",
                    "message": str(exc),
                },
            }
    return None
