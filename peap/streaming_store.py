"""SQLite persistence for the streaming ingest pipeline."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from peap_core.record_identity import (
    FAILED_RECORD_STATES,
    build_identity_anchor,
    build_source_identity_payload,
)

from .streaming_models import IngestedRecord, ItemProgressEvent


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    downloaded_count INTEGER NOT NULL DEFAULT 0,
    persisted_count INTEGER NOT NULL DEFAULT 0,
    exception_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event_ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    project_code TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    business_key TEXT NOT NULL UNIQUE,
    record_family TEXT NOT NULL DEFAULT 'listing',
    identity_anchor TEXT NOT NULL DEFAULT '',
    source_identity_json TEXT NOT NULL DEFAULT '{}',
    project_code TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    project_type TEXT NOT NULL DEFAULT '',
    exchange TEXT NOT NULL DEFAULT '',
    listing_date TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    latest_revision_id INTEGER,
    last_error_type TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    revision_hash TEXT NOT NULL,
    parser_payload_json TEXT NOT NULL DEFAULT '{}',
    postprocess_payload_json TEXT NOT NULL DEFAULT '{}',
    findings_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES records(record_id)
);

CREATE TABLE IF NOT EXISTS exports (
    export_id TEXT PRIMARY KEY,
    cursor_key TEXT NOT NULL,
    mode TEXT NOT NULL,
    date_from TEXT NOT NULL DEFAULT '',
    date_to TEXT NOT NULL DEFAULT '',
    project_type TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_cursor_records (
    cursor_key TEXT NOT NULL,
    record_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    revision_hash TEXT NOT NULL,
    export_id TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    PRIMARY KEY (cursor_key, record_id)
);

CREATE TABLE IF NOT EXISTS mapping_entries (
    entry_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mapping_pending (
    pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    project_code TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rulepacks (
    rulepack_id TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inactive',
    scope_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_state_listing_date
    ON records(state, listing_date);
CREATE INDEX IF NOT EXISTS idx_revisions_record_created
    ON record_revisions(record_id, created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job_ts
    ON job_events(job_id, event_ts);
CREATE INDEX IF NOT EXISTS idx_mapping_pending_open
    ON mapping_pending(record_id, resolved_at);
"""


def _utcnow() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _record_business_key(project_code: str, source_file: str) -> str:
    code = str(project_code or "").strip().upper()
    if code:
        return code
    digest = hashlib.sha1(str(source_file or "").encode("utf-8")).hexdigest()
    return f"source:{digest}"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _unique_text_values(*values: Any) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [value]
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _candidate_identity_token(kind: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized_kind = str(kind or "").strip()
    if normalized_kind in {"project_code", "project_id"}:
        text = text.upper()
    return f"{normalized_kind}:{text}" if normalized_kind else text


def _record_identity_value(*payloads: Any, key: str) -> Any:
    for payload in payloads:
        if isinstance(payload, dict) and key in payload and str(payload.get(key) or "").strip():
            return payload.get(key)
    return ""


def _normalize_date_text(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    match = re.match(r"^(?P<year>\d{4})[-/.\u5e74](?P<month>\d{1,2})[-/.\u6708](?P<day>\d{1,2})", text)
    if match:
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    return (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )


def _build_failed_source_identity(
    *,
    project_code: str,
    source_file: str,
    state: str,
    payload: Dict[str, Any] | None,
) -> Dict[str, Any]:
    data = dict(payload or {})
    nested_source_identity = data.get("source_identity")
    if not isinstance(nested_source_identity, dict):
        nested_source_identity = {}
    parser_payload = data.get("parser_payload")
    if not isinstance(parser_payload, dict):
        parser_payload = {}
    postprocess_payload = data.get("postprocess_payload")
    if not isinstance(postprocess_payload, dict):
        postprocess_payload = {}

    original_evidence_path = _first_non_empty(
        data.get("original_evidence_path"),
        nested_source_identity.get("original_evidence_path"),
        nested_source_identity.get("original_source_file"),
        data.get("source_file"),
        parser_payload.get("source_file"),
        source_file,
    )
    source_url = _first_non_empty(
        data.get("source_url"),
        data.get("page_url"),
        nested_source_identity.get("source_url"),
        parser_payload.get("page_url"),
        postprocess_payload.get("page_url"),
    )
    resolved_project_code = _first_non_empty(
        data.get("project_code"),
        nested_source_identity.get("project_code"),
        parser_payload.get("project_code"),
        parser_payload.get("项目编号"),
        postprocess_payload.get("project_code"),
        postprocess_payload.get("项目编号"),
        project_code,
    )
    resolved_project_name = _first_non_empty(
        data.get("project_name"),
        nested_source_identity.get("project_name"),
        parser_payload.get("project_name"),
        parser_payload.get("项目名称"),
        postprocess_payload.get("project_name"),
        postprocess_payload.get("项目名称"),
    )
    resolved_exchange = _first_non_empty(
        data.get("exchange"),
        nested_source_identity.get("exchange"),
        parser_payload.get("exchange"),
        parser_payload.get("交易所"),
        postprocess_payload.get("exchange"),
        postprocess_payload.get("交易所"),
    )
    listing_date = _first_non_empty(
        data.get("listing_date"),
        nested_source_identity.get("listing_date"),
        parser_payload.get("listing_date"),
        parser_payload.get("挂牌开始日期"),
        parser_payload.get("预披露开始日期"),
        postprocess_payload.get("listing_date"),
        postprocess_payload.get("挂牌开始日期"),
        postprocess_payload.get("预披露开始日期"),
    )
    candidate_tokens = _unique_text_values(
        nested_source_identity.get("candidate_tokens"),
        data.get("candidate_tokens"),
    )
    for kind, value in (
        ("project_code", resolved_project_code),
        (
            "project_id",
            _first_non_empty(
                data.get("project_id"),
                nested_source_identity.get("project_id"),
                parser_payload.get("project_id"),
                postprocess_payload.get("project_id"),
            ),
        ),
        ("page_url", source_url),
    ):
        token = _candidate_identity_token(kind, value)
        if token and token not in candidate_tokens:
            candidate_tokens.append(token)

    source_identity = build_source_identity_payload(
        record_family=_first_non_empty(data.get("record_family"), nested_source_identity.get("record_family"), "listing"),
        source_file=original_evidence_path,
        source_url=source_url,
        project_code=resolved_project_code,
        project_name=resolved_project_name,
        exchange=resolved_exchange,
        listing_date=listing_date,
        candidate_tokens=candidate_tokens,
    )
    source_identity["original_evidence_path"] = original_evidence_path
    source_identity["original_source_file"] = _first_non_empty(
        nested_source_identity.get("original_source_file"),
        original_evidence_path,
    )
    source_identity["record_state"] = str(state or "").strip()
    return source_identity


def _merge_source_identity(existing: Dict[str, Any] | None, incoming: Dict[str, Any] | None) -> Dict[str, Any]:
    existing_data = dict(existing or {})
    incoming_data = dict(incoming or {})
    merged = dict(existing_data)
    for key in (
        "record_family",
        "original_evidence_path",
        "original_source_file",
        "source_url",
        "project_code",
        "project_name",
        "exchange",
        "listing_date",
        "record_state",
    ):
        merged[key] = _first_non_empty(merged.get(key), incoming_data.get(key))
    merged["candidate_tokens"] = _unique_text_values(
        list(existing_data.get("candidate_tokens") or []) + list(incoming_data.get("candidate_tokens") or [])
    )
    return merged


class StreamingStore:
    """Thin sqlite-backed store with JSON payload columns."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(str(db_path or "").strip())
        if not self.db_path:
            raise ValueError("db_path is empty")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema(conn)
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_SQL)
        existing_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(records)").fetchall()
        }
        migration_columns = [
            ("record_family", "TEXT NOT NULL DEFAULT 'listing'"),
            ("identity_anchor", "TEXT NOT NULL DEFAULT ''"),
            ("source_identity_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]
        for column_name, column_spec in migration_columns:
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE records ADD COLUMN {column_name} {column_spec}")
        self._backfill_failed_record_contracts(conn)

    def _backfill_failed_record_contracts(self, conn: sqlite3.Connection) -> None:
        failed_state_placeholders = ",".join("?" for _ in FAILED_RECORD_STATES)
        rows = conn.execute(
            f"""
            SELECT
                records.record_id,
                records.project_code,
                records.record_family,
                records.business_key,
                records.identity_anchor,
                records.source_identity_json,
                records.source_file,
                records.state,
                revisions.parser_payload_json
            FROM records
            LEFT JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE records.state IN ({failed_state_placeholders})
              AND (
                records.identity_anchor = ''
                OR records.source_identity_json = '{{}}'
                OR records.business_key NOT LIKE 'failed:%'
              )
            """,
            list(FAILED_RECORD_STATES),
        ).fetchall()
        for row in rows:
            payload = _json_loads(row["parser_payload_json"], default={})
            existing_source_identity = _json_loads(row["source_identity_json"], default={})
            if not isinstance(existing_source_identity, dict):
                existing_source_identity = {}
            source_identity = _merge_source_identity(
                existing_source_identity,
                _build_failed_source_identity(
                    project_code=str(row["project_code"] or ""),
                    source_file=str(row["source_file"] or ""),
                    state=str(row["state"] or ""),
                    payload=payload if isinstance(payload, dict) else {},
                ),
            )
            identity_anchor = _first_non_empty(
                str(row["identity_anchor"] or "").strip(),
                build_identity_anchor(record_state=str(row["state"] or ""), source_identity=source_identity),
            )
            conn.execute(
                """
                UPDATE records
                SET record_family = ?,
                    identity_anchor = ?,
                    source_identity_json = ?,
                    business_key = ?
                WHERE record_id = ?
                """,
                (
                    _first_non_empty(str(row["record_family"] or "").strip(), str(source_identity.get("record_family") or "").strip(), "listing"),
                    identity_anchor,
                    _json_dumps(source_identity),
                    f"failed:{identity_anchor}",
                    str(row["record_id"]),
                ),
            )

    def create_job(self, job_type: str, *, metadata: Dict[str, Any] | None = None) -> str:
        job_id = uuid.uuid4().hex
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, job_type, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, str(job_type), "running", _json_dumps(metadata or {}), now, now),
            )
        return job_id

    def update_job_counts(
        self,
        job_id: str,
        *,
        downloaded_inc: int = 0,
        persisted_inc: int = 0,
        exception_inc: int = 0,
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET downloaded_count = downloaded_count + ?,
                    persisted_count = persisted_count + ?,
                    exception_count = exception_count + ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    int(downloaded_inc),
                    int(persisted_inc),
                    int(exception_inc),
                    now,
                    job_id,
                ),
            )

    def finish_job(self, job_id: str, *, status: str, summary: Dict[str, Any] | None = None) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, summary_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (str(status), _json_dumps(summary or {}), now, job_id),
            )

    def interrupt_running_jobs(self, *, reason: str) -> list[str]:
        now = _utcnow()
        interrupted: list[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, summary_json
                FROM jobs
                WHERE status = 'running'
                """
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"] or "").strip()
                if not job_id:
                    continue
                summary = _json_loads(row["summary_json"], default={})
                if not isinstance(summary, dict):
                    summary = {}
                summary.update(
                    {
                        "status": "interrupted",
                        "message": str(reason),
                        "interrupted_at": now,
                    }
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, summary_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    ("interrupted", _json_dumps(summary), now, job_id),
                )
                conn.execute(
                    """
                    INSERT INTO job_events (
                        job_id, event_ts, stage, status, project_code, archive_path,
                        error_type, error_message, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        now,
                        "failed",
                        "interrupted",
                        "",
                        "",
                        "job_interrupted",
                        str(reason),
                        _json_dumps({"label": "任务已中断", "reason": str(reason)}),
                    ),
                )
                interrupted.append(job_id)
        return interrupted

    def append_event(self, event: ItemProgressEvent) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, project_code, archive_path,
                    error_type, error_message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.job_id,
                    now,
                    event.stage,
                    event.status,
                    event.project_code,
                    event.archive_path,
                    event.error_type,
                    event.error_message,
                    _json_dumps(event.payload),
                ),
            )
            conn.execute(
                """
                UPDATE jobs
                SET updated_at = ?
                WHERE job_id = ?
                """,
                (now, event.job_id),
            )

    def add_audit_entry(self, action: str, payload: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (event_ts, action, payload_json) VALUES (?, ?, ?)",
                (_utcnow(), str(action), _json_dumps(payload)),
            )

    def normalize_legacy_skip_parse_entries(self) -> Dict[str, int]:
        with self._connect() as conn:
            records_count = conn.execute(
                """
                UPDATE records
                SET state = 'skipped',
                    last_error_type = 'skip_parse',
                    updated_at = updated_at
                WHERE state <> 'skipped'
                  AND (
                    last_error_type = 'skip_parse'
                    OR last_error_message LIKE 'skip-cbex-otc-page:%'
                  )
                """
            ).rowcount
            revisions_count = conn.execute(
                """
                UPDATE record_revisions
                SET state = 'skipped'
                WHERE state <> 'skipped'
                  AND record_id IN (
                    SELECT record_id
                    FROM records
                    WHERE state = 'skipped' AND last_error_type = 'skip_parse'
                  )
                """
            ).rowcount
            events_count = conn.execute(
                """
                UPDATE job_events
                SET stage = 'skipped',
                    status = 'skipped',
                    error_type = 'skip_parse'
                WHERE status <> 'skipped'
                  AND (
                    error_type = 'skip_parse'
                    OR error_message LIKE 'skip-cbex-otc-page:%'
                  )
                """
            ).rowcount

            affected_job_rows = conn.execute(
                """
                SELECT DISTINCT job_id
                FROM job_events
                WHERE status = 'skipped' AND error_type = 'skip_parse'
                """
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in affected_job_rows]
            for job_id in job_ids:
                stats_row = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN stage = 'failed' THEN 1 ELSE 0 END) AS exception_count
                    FROM job_events
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE jobs
                    SET exception_count = ?
                    WHERE job_id = ?
                    """,
                    (
                        int(stats_row["exception_count"] or 0),
                        job_id,
                    ),
                )

        return {
            "records": int(records_count or 0),
            "revisions": int(revisions_count or 0),
            "events": int(events_count or 0),
            "jobs": len(job_ids),
        }

    def normalize_listing_dates(self) -> int:
        updated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, listing_date
                FROM records
                WHERE listing_date <> ''
                """
            ).fetchall()
            for row in rows:
                normalized = _normalize_date_text(str(row["listing_date"] or ""))
                if not normalized or normalized == str(row["listing_date"] or ""):
                    continue
                conn.execute(
                    """
                    UPDATE records
                    SET listing_date = ?
                    WHERE record_id = ?
                    """,
                    (normalized, row["record_id"]),
                )
                updated += 1
        return updated

    def normalize_required_mapping_states(self) -> Dict[str, int]:
        from .streaming_models import PostProcessFinding
        from .streaming_postprocess import normalize_record_payload

        updated_records = 0
        updated_findings = 0
        inserted_pending = 0
        resolved_pending = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    records.record_id,
                    records.project_code,
                    records.state,
                    revisions.revision_id,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json,
                    revisions.findings_json
                FROM records
                JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE records.state IN ('ready', 'pending_mapping', 'mapping_conflict', 'conflict')
                """
            ).fetchall()
            open_pending_rows = conn.execute(
                """
                SELECT record_id
                FROM mapping_pending
                WHERE resolved_at = ''
                """
            ).fetchall()
            open_pending = {str(row["record_id"]) for row in open_pending_rows}

            for row in rows:
                record_id = str(row["record_id"])
                parser_payload = _json_loads(row["parser_payload_json"], default={})
                postprocess_payload = _json_loads(row["postprocess_payload_json"], default={})
                raw_findings = _json_loads(row["findings_json"], default=[])
                findings = [
                    PostProcessFinding(
                        severity=str(item.get("severity") or "warn"),
                        type=str(item.get("type") or ""),
                        message=str(item.get("message") or ""),
                        evidence=dict(item.get("evidence") or {}),
                    )
                    for item in raw_findings
                    if isinstance(item, dict)
                ]
                normalized_payload, normalized_findings = normalize_record_payload(
                    parser_payload=parser_payload,
                    postprocess_payload=postprocess_payload,
                    findings=findings,
                )
                finding_types = {str(item.type or "") for item in normalized_findings}
                if "mapping_conflict" in finding_types:
                    new_state = "mapping_conflict"
                elif {"mapping_missing", "mapping_gap", "mapping_ambiguous", "project_type_unknown"} & finding_types:
                    new_state = "pending_mapping"
                else:
                    new_state = "ready"
                new_findings_json = _json_dumps([asdict(item) for item in normalized_findings])
                new_payload_json = _json_dumps(normalized_payload)

                if row["state"] != new_state:
                    conn.execute(
                        """
                        UPDATE records
                        SET state = ?
                        WHERE record_id = ?
                        """,
                        (new_state, record_id),
                    )
                    conn.execute(
                        """
                        UPDATE record_revisions
                        SET state = ?
                        WHERE revision_id = ?
                        """,
                        (new_state, int(row["revision_id"])),
                    )
                    updated_records += 1

                if (
                    str(row["postprocess_payload_json"] or "") != new_payload_json
                    or str(row["findings_json"] or "") != new_findings_json
                ):
                    conn.execute(
                        """
                        UPDATE record_revisions
                        SET postprocess_payload_json = ?,
                            findings_json = ?,
                            state = ?
                        WHERE revision_id = ?
                        """,
                        (new_payload_json, new_findings_json, new_state, int(row["revision_id"])),
                    )
                    updated_findings += 1

                if new_state in {"pending_mapping", "mapping_conflict"} and record_id not in open_pending:
                    conn.execute(
                        """
                        INSERT INTO mapping_pending (
                            record_id, revision_id, project_code, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            record_id,
                            int(row["revision_id"]),
                            str(row["project_code"] or ""),
                            new_payload_json,
                            _utcnow(),
                        ),
                    )
                    open_pending.add(record_id)
                    inserted_pending += 1
                if new_state not in {"pending_mapping", "mapping_conflict"} and record_id in open_pending:
                    conn.execute(
                        """
                        UPDATE mapping_pending
                        SET resolved_at = ?
                        WHERE record_id = ? AND resolved_at = ''
                        """,
                        (_utcnow(), record_id),
                    )
                    open_pending.remove(record_id)
                    resolved_pending += 1

        return {
            "records": updated_records,
            "revisions": updated_findings,
            "pending_inserted": inserted_pending,
            "pending_resolved": resolved_pending,
        }

    def set_setting(self, key: str, value: Dict[str, Any]) -> None:
        now = _utcnow()
        encoded = _json_dumps(value)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, encoded, now),
            )
            conn.execute(
                "INSERT INTO settings_revisions (key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, encoded, now),
            )

    def get_setting(self, key: str, *, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return dict(default or {})
        return _json_loads(row["value_json"], default=dict(default or {}))

    def upsert_mapping_entry(
        self,
        *,
        company_name: str,
        group_name: str = "",
        source_type: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        normalized = str(company_name or "").strip()
        if not normalized:
            raise ValueError("company_name is empty")
        metadata_payload = dict(metadata or {})
        match_field = str(metadata_payload.get("match_field") or "transferor").strip() or "transferor"
        target_field = str(
            metadata_payload.get("target_field") or ("group_name" if str(group_name or "").strip() else "source_type")
        ).strip() or "group_name"
        metadata_payload["match_field"] = match_field
        metadata_payload["target_field"] = target_field
        entry_key = "|".join([match_field, target_field, normalized.lower()])
        entry_id = hashlib.sha1(entry_key.encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mapping_entries (
                    entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    group_name = excluded.group_name,
                    source_type = excluded.source_type,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    entry_id,
                    normalized,
                    str(group_name or "").strip(),
                    str(source_type or "").strip(),
                    _json_dumps(metadata_payload),
                    now,
                    now,
                ),
            )
        return entry_id

    def list_mapping_entries(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry_id, company_name, group_name, source_type, metadata_json, created_at, updated_at
                FROM mapping_entries
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "entry_id": row["entry_id"],
                    "company_name": row["company_name"],
                    "group_name": row["group_name"],
                    "source_type": row["source_type"],
                    "metadata": _json_loads(row["metadata_json"], default={}),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def mark_mapping_pending(
        self,
        *,
        record_id: str,
        revision_id: int,
        project_code: str,
        payload: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT pending_id
                FROM mapping_pending
                WHERE record_id = ? AND resolved_at = ''
                ORDER BY pending_id DESC
                LIMIT 1
                """,
                (str(record_id),),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE mapping_pending
                    SET revision_id = ?,
                        project_code = ?,
                        payload_json = ?,
                        created_at = ?
                    WHERE pending_id = ?
                    """,
                    (
                        int(revision_id),
                        str(project_code or ""),
                        _json_dumps(payload),
                        _utcnow(),
                        int(existing["pending_id"]),
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO mapping_pending (
                    record_id, revision_id, project_code, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, int(revision_id), str(project_code or ""), _json_dumps(payload), _utcnow()),
            )

    def resolve_mapping_pending(self, record_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mapping_pending
                SET resolved_at = ?
                WHERE record_id = ? AND resolved_at = ''
                """,
                (_utcnow(), record_id),
            )

    def upsert_record(self, record: IngestedRecord) -> Dict[str, Any]:
        business_key = _record_business_key(record.project_code, record.source_file)
        record_family = str(record.record_family or "listing").strip() or "listing"
        now = _utcnow()
        listing_date = _normalize_date_text(record.listing_date)
        findings_json = [asdict(item) for item in record.findings]
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT record_id, latest_revision_id
                FROM records
                WHERE business_key = ?
                """,
                (business_key,),
            ).fetchone()
            record_id = existing["record_id"] if existing is not None else record.record_id or uuid.uuid4().hex
            latest_revision_id = existing["latest_revision_id"] if existing is not None else None
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id, business_key, record_family, identity_anchor, source_identity_json,
                        project_code, project_name, project_type,
                        exchange, listing_date, state, source_file, archive_path,
                        latest_revision_id, last_error_type, last_error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', '', ?, ?)
                    """,
                    (
                        record_id,
                        business_key,
                        record_family,
                        "",
                        "{}",
                        record.project_code,
                        record.project_name,
                        record.project_type,
                        record.exchange,
                        listing_date,
                        record.state,
                        record.source_file,
                        record.archive_path,
                        now,
                        now,
                    ),
                )

            revision_row = None
            if latest_revision_id is not None:
                revision_row = conn.execute(
                    """
                    SELECT revision_id, revision_hash
                    FROM record_revisions
                    WHERE revision_id = ?
                    """,
                    (latest_revision_id,),
                ).fetchone()
            changed = revision_row is None or revision_row["revision_hash"] != record.revision_hash
            revision_id = latest_revision_id
            if changed:
                cur = conn.execute(
                    """
                    INSERT INTO record_revisions (
                        record_id, revision_hash, parser_payload_json,
                        postprocess_payload_json, findings_json, state, source_file, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        record.revision_hash,
                        _json_dumps(record.parser_payload),
                        _json_dumps(record.postprocess_payload),
                        _json_dumps(findings_json),
                        record.state,
                        record.source_file,
                        now,
                    ),
                )
                revision_id = int(cur.lastrowid)
            elif revision_id is not None:
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET parser_payload_json = ?,
                        postprocess_payload_json = ?,
                        findings_json = ?,
                        state = ?,
                        source_file = ?
                    WHERE revision_id = ?
                    """,
                    (
                        _json_dumps(record.parser_payload),
                        _json_dumps(record.postprocess_payload),
                        _json_dumps(findings_json),
                        record.state,
                        record.source_file,
                        int(revision_id),
                    ),
                )

            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type,
                    exchange, listing_date, state, source_file, archive_path,
                    latest_revision_id, last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    business_key = excluded.business_key,
                    record_family = excluded.record_family,
                    identity_anchor = excluded.identity_anchor,
                    source_identity_json = excluded.source_identity_json,
                    project_code = excluded.project_code,
                    project_name = excluded.project_name,
                    project_type = excluded.project_type,
                    exchange = excluded.exchange,
                    listing_date = excluded.listing_date,
                    state = excluded.state,
                    source_file = excluded.source_file,
                    archive_path = excluded.archive_path,
                    latest_revision_id = excluded.latest_revision_id,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    business_key,
                    record_family,
                    "",
                    "{}",
                    record.project_code,
                    record.project_name,
                    record.project_type,
                    record.exchange,
                    listing_date,
                    record.state,
                    record.source_file,
                    record.archive_path,
                    revision_id,
                    "",
                    "",
                    now,
                    now,
                ),
            )
        return {
            "record_id": record_id,
            "revision_id": revision_id,
            "changed": changed,
            "business_key": business_key,
        }

    def upsert_failed_record(
        self,
        *,
        project_code: str,
        source_file: str,
        state: str,
        error_type: str,
        error_message: str,
        payload: Dict[str, Any] | None = None,
        severity: str = "error",
    ) -> Dict[str, Any]:
        source_identity = _build_failed_source_identity(
            project_code=project_code,
            source_file=source_file,
            state=state,
            payload=payload,
        )
        identity_anchor = build_identity_anchor(record_state=state, source_identity=source_identity)
        business_key = f"failed:{identity_anchor}"
        now = _utcnow()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT record_id, latest_revision_id, identity_anchor, source_identity_json, record_family
                FROM records
                WHERE business_key = ?
                """,
                (business_key,),
            ).fetchone()
            record_id = existing["record_id"] if existing is not None else uuid.uuid4().hex
            stored_identity_anchor = _first_non_empty(existing["identity_anchor"] if existing is not None else "", identity_anchor)
            stored_source_identity = source_identity
            stored_record_family = _first_non_empty(
                existing["record_family"] if existing is not None else "",
                source_identity.get("record_family"),
                "listing",
            )
            if existing is not None:
                existing_source_identity = _json_loads(existing["source_identity_json"], default={})
                if isinstance(existing_source_identity, dict) and existing_source_identity:
                    stored_source_identity = _merge_source_identity(existing_source_identity, source_identity)
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id, business_key, record_family, identity_anchor, source_identity_json,
                        project_code, project_name, project_type, exchange, listing_date,
                        state, source_file, archive_path, latest_revision_id,
                        last_error_type, last_error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        business_key,
                        stored_record_family,
                        stored_identity_anchor,
                        _json_dumps(stored_source_identity),
                        str(project_code or ""),
                        "",
                        "",
                        "",
                        "",
                        state,
                        source_file,
                        "",
                        error_type,
                        error_message,
                        now,
                        now,
                    ),
                )
            cur = conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    hashlib.sha1(f"{state}|{source_file}|{now}".encode("utf-8")).hexdigest(),
                    _json_dumps(payload or {}),
                    _json_dumps({}),
                    _json_dumps([{"severity": str(severity or "error"), "type": error_type, "message": error_message}]),
                    state,
                    source_file,
                    now,
                ),
            )
            revision_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    project_code = excluded.project_code,
                    state = excluded.state,
                    source_file = excluded.source_file,
                    latest_revision_id = excluded.latest_revision_id,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    record_family = CASE
                        WHEN record_family = '' THEN excluded.record_family
                        ELSE record_family
                    END,
                    identity_anchor = CASE
                        WHEN identity_anchor = '' THEN excluded.identity_anchor
                        ELSE identity_anchor
                    END,
                    source_identity_json = excluded.source_identity_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    business_key,
                    stored_record_family,
                    stored_identity_anchor,
                    _json_dumps(stored_source_identity),
                    str(project_code or ""),
                    "",
                    "",
                    "",
                    "",
                    state,
                    source_file,
                    "",
                    revision_id,
                    error_type,
                    error_message,
                    now,
                    now,
                ),
            )
        return {
            "record_id": record_id,
            "revision_id": revision_id,
            "business_key": business_key,
            "identity_anchor": stored_identity_anchor,
        }

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return {
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "downloaded_count": int(row["downloaded_count"]),
            "persisted_count": int(row["persisted_count"]),
            "exception_count": int(row["exception_count"]),
            "metadata": _json_loads(row["metadata_json"], default={}),
            "summary": _json_loads(row["summary_json"], default={}),
        }

    def list_jobs(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "job_type": row["job_type"],
                "status": row["status"],
                "downloaded_count": int(row["downloaded_count"]),
                "persisted_count": int(row["persisted_count"]),
                "exception_count": int(row["exception_count"]),
                "metadata": _json_loads(row["metadata_json"], default={}),
                "summary": _json_loads(row["summary_json"], default={}),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_job_events(self, job_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        self.get_job(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_ts, stage, status, project_code, archive_path,
                       error_type, error_message, payload_json
                FROM job_events
                WHERE job_id = ?
                ORDER BY event_id DESC
                LIMIT ?
                """,
                (job_id, int(limit)),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_ts": row["event_ts"],
                "stage": row["stage"],
                "status": row["status"],
                "project_code": row["project_code"],
                "archive_path": row["archive_path"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
                "payload": _json_loads(row["payload_json"], default={}),
            }
            for row in rows
        ]

    def get_job_event_counts(self, job_id: str) -> Dict[str, int]:
        self.get_job(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c
                FROM job_events
                WHERE job_id = ?
                GROUP BY status
                """,
                (job_id,),
            ).fetchall()
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM job_events
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        counts = {str(row["status"]): int(row["c"]) for row in rows}
        counts["total_count"] = int(total_row["c"]) if total_row is not None else 0
        return counts

    def count_pending_mappings(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT record_id) AS c FROM mapping_pending WHERE resolved_at = ''"
            ).fetchone()
        return int(row["c"]) if row is not None else 0

    def list_existing_project_codes(self, *, states: Iterable[str] | None = None) -> set[str]:
        clauses = ["project_code <> ''"]
        params: list[Any] = []
        if states:
            items = [str(item) for item in states if str(item or "").strip()]
            if items:
                clauses.append(f"state IN ({','.join('?' for _ in items)})")
                params.extend(items)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT project_code
                FROM records
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        return {str(row["project_code"] or "").strip().upper() for row in rows if str(row["project_code"] or "").strip()}

    def list_existing_candidate_tokens(self, *, states: Iterable[str] | None = None) -> set[str]:
        tokens = {_candidate_identity_token("project_code", code) for code in self.list_existing_project_codes(states=states)}
        tokens.discard("")
        allowed_project_codes: set[str] | None = None
        allowed_source_files: set[str] | None = None
        state_items = [str(item) for item in states if str(item or "").strip()] if states else []
        record_clauses = ["1=1"]
        record_params: list[Any] = []
        if state_items:
            record_clauses.append(f"records.state IN ({','.join('?' for _ in state_items)})")
            record_params.extend(state_items)
        with self._connect() as conn:
            record_rows = conn.execute(
                f"""
                SELECT
                    records.project_code,
                    records.source_file,
                    records.source_identity_json,
                    revisions.parser_payload_json,
                    revisions.postprocess_payload_json
                FROM records
                LEFT JOIN record_revisions AS revisions
                  ON revisions.revision_id = records.latest_revision_id
                WHERE {' AND '.join(record_clauses)}
                """,
                record_params,
            ).fetchall()
        if state_items:
            allowed_project_codes = {
                str(row["project_code"] or "").strip().upper()
                for row in record_rows
                if str(row["project_code"] or "").strip()
            }
            allowed_source_files = {
                str(row["source_file"] or "").strip()
                for row in record_rows
                if str(row["source_file"] or "").strip()
            }
            for row in record_rows:
                source_identity = _json_loads(row["source_identity_json"], default={})
                if not isinstance(source_identity, dict):
                    continue
                original_evidence_path = str(source_identity.get("original_evidence_path") or "").strip()
                original_source_file = str(source_identity.get("original_source_file") or "").strip()
                if original_evidence_path:
                    allowed_source_files.add(original_evidence_path)
                if original_source_file:
                    allowed_source_files.add(original_source_file)
        for row in record_rows:
            parser_payload = _json_loads(row["parser_payload_json"], default={})
            postprocess_payload = _json_loads(row["postprocess_payload_json"], default={})
            for kind in ("project_id", "page_url"):
                token = _candidate_identity_token(
                    kind,
                    _record_identity_value(postprocess_payload, parser_payload, key=kind),
                )
                if token:
                    tokens.add(token)
            source_identity = _json_loads(row["source_identity_json"], default={})
            if isinstance(source_identity, dict):
                for token in _unique_text_values(source_identity.get("candidate_tokens")):
                    normalized_token = str(token or "").strip()
                    if normalized_token:
                        tokens.add(normalized_token)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM job_events
                WHERE stage = 'downloaded'
                """
            ).fetchall()
        for row in rows:
            payload = _json_loads(row["payload_json"], default={})
            if not isinstance(payload, dict):
                continue
            if allowed_project_codes is not None and allowed_source_files is not None:
                project_code = str(payload.get("project_code") or "").strip().upper()
                source_file = str(payload.get("source_file") or "").strip()
                if project_code:
                    if project_code not in allowed_project_codes and (
                        not source_file or source_file not in allowed_source_files
                    ):
                        continue
                elif not source_file or source_file not in allowed_source_files:
                    continue
            for kind in ("project_code", "project_id", "page_url"):
                token = _candidate_identity_token(kind, payload.get(kind))
                if token:
                    tokens.add(token)
        return tokens

    def count_records_by_state(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        business_types: Iterable[str] | None = None,
        record_family: str | None = None,
    ) -> Dict[str, int]:
        clauses = ["1=1"]
        params: List[Any] = []
        normalized_column = (
            "replace(replace(replace(replace(replace(records.listing_date, '年', '-'), '月', '-'), '日', ''), '/', '-'), '.', '-')"
        )
        normalized_from = _normalize_date_text(date_from or "")
        normalized_to = _normalize_date_text(date_to or "")
        if normalized_from:
            clauses.append(f"{normalized_column} >= ?")
            params.append(normalized_from)
        if normalized_to:
            clauses.append(f"{normalized_column} <= ?")
            params.append(normalized_to)
        if business_types:
            items = [str(item) for item in business_types if str(item or "").strip()]
            if items:
                clauses.append(f"records.project_type IN ({','.join('?' for _ in items)})")
                params.extend(items)
        normalized_record_family = str(record_family or "").strip()
        if normalized_record_family:
            clauses.append("records.record_family = ?")
            params.append(normalized_record_family)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT records.state, COUNT(*) AS c
                FROM records
                WHERE {' AND '.join(clauses)}
                GROUP BY records.state
                """,
                params,
            ).fetchall()
        return {str(row["state"]): int(row["c"]) for row in rows}

    def list_pending_mappings(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pending_id, record_id, revision_id, project_code, payload_json, created_at
                FROM mapping_pending
                WHERE resolved_at = ''
                  AND pending_id IN (
                    SELECT MAX(pending_id)
                    FROM mapping_pending
                    WHERE resolved_at = ''
                    GROUP BY record_id
                  )
                ORDER BY pending_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "pending_id": int(row["pending_id"]),
                "record_id": row["record_id"],
                "revision_id": int(row["revision_id"]),
                "project_code": row["project_code"],
                "payload": _json_loads(row["payload_json"], default={}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_record(self, record_id: str) -> Dict[str, Any]:
        rows = self.iter_latest_records()
        for row in rows:
            if row["record_id"] == record_id:
                return row
        raise KeyError(record_id)

    def update_record_archive_path(self, record_id: str, archive_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE records
                SET archive_path = ?,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (str(archive_path or ""), _utcnow(), str(record_id)),
            )

    def update_record_source_file(self, record_id: str, source_file: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT project_code, business_key, latest_revision_id, identity_anchor, state
                FROM records
                WHERE record_id = ?
                """,
                (str(record_id),),
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            failed_identity = _first_non_empty(
                str(row["identity_anchor"] or "").strip(),
                "legacy-failed" if str(row["state"] or "").strip() in FAILED_RECORD_STATES else "",
            )
            business_key = (
                str(row["business_key"] or "").strip()
                if failed_identity
                else _record_business_key(str(row["project_code"] or ""), str(source_file or ""))
            )
            now = _utcnow()
            if failed_identity:
                conn.execute(
                    """
                    UPDATE records
                    SET source_file = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (str(source_file or ""), now, str(record_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE records
                    SET source_file = ?,
                        business_key = ?,
                        updated_at = ?
                    WHERE record_id = ?
                    """,
                    (str(source_file or ""), business_key, now, str(record_id)),
                )
            latest_revision_id = row["latest_revision_id"]
            if latest_revision_id is not None:
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET source_file = ?
                    WHERE revision_id = ?
                    """,
                    (str(source_file or ""), int(latest_revision_id)),
                )

    def update_downloaded_event_source_file(self, old_source_file: str, new_source_file: str) -> int:
        old_path = str(old_source_file or "").strip()
        new_path = str(new_source_file or "").strip()
        if not old_path or not new_path or old_path == new_path:
            return 0
        updated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, payload_json
                FROM job_events
                WHERE stage = 'downloaded'
                """
            ).fetchall()
            for row in rows:
                payload = _json_loads(row["payload_json"], default={})
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("source_file") or "").strip() != old_path:
                    continue
                payload["source_file"] = new_path
                conn.execute(
                    """
                    UPDATE job_events
                    SET payload_json = ?
                    WHERE event_id = ?
                    """,
                    (_json_dumps(payload), int(row["event_id"])),
                )
                updated += 1
        return updated

    def iter_latest_records(
        self,
        *,
        states: Iterable[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        business_type: str | None = None,
        record_family: str | None = None,
        limit: int | None = None,
        sort: str = "business",
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        normalized_column = (
            "replace(replace(replace(replace(replace(records.listing_date, '年', '-'), '月', '-'), '日', ''), '/', '-'), '.', '-')"
        )
        if states:
            items = [str(item) for item in states]
            clauses.append(f"records.state IN ({','.join('?' for _ in items)})")
            params.extend(items)
        if date_from:
            clauses.append(f"{normalized_column} >= ?")
            params.append(_normalize_date_text(date_from))
        if date_to:
            clauses.append(f"{normalized_column} <= ?")
            params.append(_normalize_date_text(date_to))
        if business_type:
            clauses.append("records.project_type = ?")
            params.append(str(business_type))
        if record_family:
            clauses.append("records.record_family = ?")
            params.append(str(record_family))

        order_clause = "records.project_type, records.project_code, records.updated_at"
        if str(sort or "").strip().lower() == "recent":
            order_clause = "records.updated_at DESC, records.record_id DESC"

        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(int(limit))

        query = f"""
            SELECT
                records.record_id,
                records.business_key,
                records.record_family,
                records.identity_anchor,
                records.source_identity_json,
                records.project_code,
                records.project_name,
                records.project_type,
                records.exchange,
                records.listing_date,
                records.state,
                records.source_file,
                records.archive_path,
                records.created_at,
                records.updated_at,
                revisions.revision_id,
                revisions.revision_hash,
                revisions.parser_payload_json,
                revisions.postprocess_payload_json,
                revisions.findings_json
            FROM records
            JOIN record_revisions AS revisions
              ON revisions.revision_id = records.latest_revision_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_clause}{limit_clause}
        """
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "record_id": row["record_id"],
                    "business_key": row["business_key"],
                    "record_family": row["record_family"],
                    "identity_anchor": row["identity_anchor"],
                    "source_identity_json": _json_loads(row["source_identity_json"], default={}),
                    "project_code": row["project_code"],
                    "project_name": row["project_name"],
                    "project_type": row["project_type"],
                    "exchange": row["exchange"],
                    "listing_date": row["listing_date"],
                    "state": row["state"],
                    "source_file": row["source_file"],
                    "archive_path": row["archive_path"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "revision_id": int(row["revision_id"]),
                    "revision_hash": row["revision_hash"],
                    "parser_payload": _json_loads(row["parser_payload_json"], default={}),
                    "postprocess_payload": _json_loads(row["postprocess_payload_json"], default={}),
                    "findings": _json_loads(row["findings_json"], default=[]),
                }
            )
        return out

    def get_exported_revision_map(self, cursor_key: str) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, revision_id, revision_hash, export_id, exported_at
                FROM export_cursor_records
                WHERE cursor_key = ?
                """,
                (cursor_key,),
            ).fetchall()
        return {
            row["record_id"]: {
                "revision_id": int(row["revision_id"]),
                "revision_hash": row["revision_hash"],
                "export_id": row["export_id"],
                "exported_at": row["exported_at"],
            }
            for row in rows
        }

    def mark_exported(
        self,
        *,
        export_id: str,
        cursor_key: str,
        mode: str,
        date_from: str | None,
        date_to: str | None,
        project_type: str,
        output_dir: str,
        summary: Dict[str, Any],
        records: Iterable[Dict[str, Any]],
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    export_id, cursor_key, mode, date_from, date_to,
                    project_type, output_dir, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    cursor_key,
                    mode,
                    str(date_from or ""),
                    str(date_to or ""),
                    project_type,
                    output_dir,
                    _json_dumps(summary),
                    now,
                ),
            )
            for item in records:
                conn.execute(
                    """
                    INSERT INTO export_cursor_records (
                        cursor_key, record_id, revision_id, revision_hash, export_id, exported_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cursor_key, record_id) DO UPDATE SET
                        revision_id = excluded.revision_id,
                        revision_hash = excluded.revision_hash,
                        export_id = excluded.export_id,
                        exported_at = excluded.exported_at
                    """,
                    (
                        cursor_key,
                        item["record_id"],
                        int(item["revision_id"]),
                        item["revision_hash"],
                        export_id,
                        now,
                    ),
                )
