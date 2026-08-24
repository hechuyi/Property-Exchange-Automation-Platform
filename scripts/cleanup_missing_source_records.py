#!/usr/bin/env python3
"""Report records whose source and archive files no longer exist."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from scripts._paths import open_readonly_sqlite_db, resolve_cleanup_paths


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_missing_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            record_id,
            project_code,
            project_name,
            project_type,
            exchange,
            state,
            source_file,
            archive_path,
            latest_revision_id,
            created_at
        FROM records
        WHERE source_file <> '' OR archive_path <> ''
        ORDER BY exchange, project_type, project_code, record_id
        """
    ).fetchall()
    missing: list[sqlite3.Row] = []
    for row in rows:
        source_file = _text(row["source_file"])
        archive_path = _text(row["archive_path"])
        source_exists = bool(source_file and os.path.isfile(source_file))
        archive_exists = bool(archive_path and os.path.isfile(archive_path))
        if source_exists or archive_exists:
            continue
        missing.append(row)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="default: AppConfig.STREAMING_DB_PATH")
    parser.add_argument("--app-home", default=None,
                        help="default: AppConfig.APP_HOME")
    args = resolve_cleanup_paths(parser.parse_args())

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        raise SystemExit(f"database not found: {db_path}")

    with open_readonly_sqlite_db(db_path) as conn:
        missing_rows = _load_missing_rows(conn)

    by_key: dict[str, int] = {}
    records = []
    for row in missing_rows:
        item = {
            "record_id": _text(row["record_id"]),
            "project_code": _text(row["project_code"]),
            "project_name": _text(row["project_name"]),
            "project_type": _text(row["project_type"]),
            "exchange": _text(row["exchange"]),
            "state": _text(row["state"]),
            "source_file": _text(row["source_file"]),
            "archive_path": _text(row["archive_path"]),
            "created_at": _text(row["created_at"]),
        }
        records.append(item)
        key = f"{item['exchange']}|{item['project_type']}|{item['state']}"
        by_key[key] = by_key.get(key, 0) + 1

    summary: dict[str, Any] = {
        "db_path": db_path,
        "missing_record_count": len(records),
        "by_exchange_type_state": dict(sorted(by_key.items())),
        "sample_records": records[:20],
    }
    summary["recovery_boundary"] = "report_only"
    summary["next_action"] = "Review this report through the controlled operations process before changing records or archives."
    print(json.dumps({"mode": "report_only", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
