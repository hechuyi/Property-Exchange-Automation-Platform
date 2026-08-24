#!/usr/bin/env python3
"""Report stale DB records that point to another record's archive file."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from peap.parsing import parse_file
from scripts._paths import open_readonly_sqlite_db, resolve_cleanup_paths


def _load_rows(db_path: Path) -> list[sqlite3.Row]:
    with open_readonly_sqlite_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT
                record_id,
                project_code,
                project_name,
                exchange,
                listing_date,
                state,
                source_file,
                archive_path,
                created_at,
                updated_at
            FROM records
            WHERE source_file <> ''
            ORDER BY source_file, updated_at DESC, created_at DESC
            """
        ).fetchall()


def _plan(db_path: Path) -> dict[str, Any]:
    rows = _load_rows(db_path)
    paths = [str(row["source_file"] or "").strip() for row in rows if str(row["source_file"] or "").strip()]
    duplicates = [path for path, count in Counter(paths).items() if count > 1]
    groups: list[dict[str, Any]] = []
    stale_ids: list[str] = []

    for path in sorted(duplicates):
        group_rows = [row for row in rows if str(row["source_file"] or "").strip() == path]
        actual_code = ""
        actual_name = ""
        try:
            parsed = parse_file(path)
            actual_code = str(parsed.project_code or "").strip()
            actual_name = str(parsed.project_name or "").strip()
        except Exception as exc:  # noqa: BLE001
            groups.append(
                {
                    "source_file": path,
                    "actual_project_code": actual_code,
                    "actual_project_name": actual_name,
                    "resolution_status": "unresolved_parse_error",
                    "parse_error": {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                    "keep_record_id": "",
                    "stale_record_ids": [],
                    "records": [
                        {
                            "record_id": str(row["record_id"]),
                            "project_code": str(row["project_code"] or ""),
                            "project_name": str(row["project_name"] or ""),
                            "exchange": str(row["exchange"] or ""),
                            "listing_date": str(row["listing_date"] or ""),
                            "state": str(row["state"] or ""),
                        }
                        for row in group_rows
                    ],
                }
            )
            continue
        matching = [
            row
            for row in group_rows
            if actual_code and str(row["project_code"] or "").strip().upper() == actual_code.upper()
        ]
        keep_id = str(matching[0]["record_id"]) if matching else str(group_rows[0]["record_id"])
        stale = [row for row in group_rows if str(row["record_id"]) != keep_id]
        stale_ids.extend(str(row["record_id"]) for row in stale)
        groups.append(
            {
                "source_file": path,
                "actual_project_code": actual_code,
                "actual_project_name": actual_name,
                "parse_error": "",
                "keep_record_id": keep_id,
                "stale_record_ids": [str(row["record_id"]) for row in stale],
                "records": [
                    {
                        "record_id": str(row["record_id"]),
                        "project_code": str(row["project_code"] or ""),
                        "project_name": str(row["project_name"] or ""),
                        "exchange": str(row["exchange"] or ""),
                        "listing_date": str(row["listing_date"] or ""),
                        "state": str(row["state"] or ""),
                    }
                    for row in group_rows
                ],
            }
        )
    return {"groups": groups, "stale_record_ids": sorted(set(stale_ids))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="default: AppConfig.STREAMING_DB_PATH")
    args = resolve_cleanup_paths(parser.parse_args())

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    planned = _plan(db_path)
    stale_ids = list(planned["stale_record_ids"])
    manifest = {
        "mode": "report_only",
        "db_path": str(db_path),
        "duplicate_group_count": len(planned["groups"]),
        "candidate_record_count": len(stale_ids),
        "recovery_boundary": "report_only",
        "next_action": "Review this report through the controlled operations process before changing records or archives.",
        **planned,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
