#!/usr/bin/env python3
"""Plan recovery for active DB records whose archive snapshot files are missing."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from scripts._paths import open_readonly_sqlite_db, resolve_cleanup_paths


class CorruptJsonColumn(ValueError):
    def __init__(self, *, record_id: Any, column: str, raw: str, message: str) -> None:
        super().__init__(f"corrupt_json in {column} for record {record_id}: {message}")
        self.record_id = record_id
        self.column = column
        self.raw = raw
        self.message = message

    def diagnostic(self) -> dict[str, Any]:
        return {
            "error": "corrupt_json",
            "record_id": self.record_id,
            "column": self.column,
            "message": self.message,
        }


def _json_loads(value: Any, *, record_id: Any, column: str) -> Any:
    if value is None or value == "":
        return {}
    try:
        return json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise CorruptJsonColumn(
            record_id=record_id,
            column=column,
            raw=str(value),
            message=str(exc),
        ) from exc


def _page_url(row: sqlite3.Row) -> str:
    source_identity = _json_loads(
        row["source_identity_json"],
        record_id=row["record_id"],
        column="source_identity_json",
    )
    parser_payload = _json_loads(
        row["parser_payload_json"],
        record_id=row["record_id"],
        column="parser_payload_json",
    )
    postprocess_payload = _json_loads(
        row["postprocess_payload_json"],
        record_id=row["record_id"],
        column="postprocess_payload_json",
    )
    return str(
        parser_payload.get("page_url")
        or postprocess_payload.get("page_url")
        or source_identity.get("source_url")
        or ""
    ).strip()


def _sse_xmid(page_url: str) -> str:
    parsed = urllib.parse.urlparse(page_url)
    query = urllib.parse.parse_qs(parsed.query)
    value = (query.get("XMID") or query.get("xmid") or [""])[0]
    if value:
        return str(value).strip()
    fragment = urllib.parse.urlparse(parsed.fragment)
    query = urllib.parse.parse_qs(fragment.query)
    return str((query.get("XMID") or query.get("xmid") or [""])[0]).strip()


def _missing_rows(db_path: Path) -> list[sqlite3.Row]:
    with open_readonly_sqlite_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                r.record_id,
                r.project_code,
                r.project_name,
                r.project_type,
                r.exchange,
                r.listing_date,
                r.state,
                r.source_file,
                r.source_identity_json,
                rr.parser_payload_json,
                rr.postprocess_payload_json
            FROM records AS r
            LEFT JOIN record_revisions AS rr
              ON rr.revision_id = r.latest_revision_id
            WHERE r.source_file <> ''
            ORDER BY r.exchange, r.project_code
            """
        ).fetchall()
    return [row for row in rows if not os.path.exists(str(row["source_file"] or ""))]


def _build_entries(rows: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cbex_entries: list[dict[str, Any]] = []
    sse_entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        page_url = _page_url(row)
        code = str(row["project_code"] or "").strip()
        project_name = str(row["project_name"] or "").strip()
        listing_date = str(row["listing_date"] or "").strip()
        exchange = str(row["exchange"] or "").strip()
        if not page_url:
            skipped.append(
                {
                    "record_id": row["record_id"],
                    "project_code": code,
                    "exchange": exchange,
                    "source_file": row["source_file"],
                    "reason": "missing_page_url",
                }
            )
            continue
        if "suaee.com" in page_url:
            xmid = _sse_xmid(page_url)
            sse_entries.append(
                {
                    "xmid": xmid or code,
                    "project_code": code,
                    "project_name": project_name,
                    "page_url": page_url,
                    "disclosure_start": listing_date,
                    "row": {},
                }
            )
        elif "cbex.com" in page_url:
            cbex_entries.append(
                {
                    "uid": code or page_url,
                    "code": code,
                    "project_name": project_name,
                    "url": page_url,
                    "disclosure_start": listing_date,
                    "row": {},
                }
            )
        else:
            skipped.append(
                {
                    "record_id": row["record_id"],
                    "project_code": code,
                    "exchange": exchange,
                    "source_file": row["source_file"],
                    "page_url": page_url,
                    "reason": "unsupported_url",
                }
            )
    return cbex_entries, sse_entries, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", default=None,
                        help="default: AppConfig.ARCHIVE_ROOT")
    parser.add_argument("--db", default=None,
                        help="default: AppConfig.STREAMING_DB_PATH")
    args = resolve_cleanup_paths(parser.parse_args())

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    rows = _missing_rows(db_path)
    try:
        cbex_entries, sse_entries, skipped = _build_entries(rows)
    except CorruptJsonColumn as exc:
        print(json.dumps(exc.diagnostic(), ensure_ascii=False), file=sys.stderr)
        return 2

    result: dict[str, Any] = {
        "mode": "report_only",
        "missing_count": len(rows),
        "cbex_count": len(cbex_entries),
        "sse_count": len(sse_entries),
        "skipped": skipped,
        "candidates": {
            "cbex": cbex_entries,
            "sse": sse_entries,
        },
        "next_action": "Submit the reviewed plan to the controlled operations process for a recorded decision.",
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
