#!/usr/bin/env python3
"""Report bad Shanghai SPA shell snapshots without mutating files or records."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from bs4 import BeautifulSoup

from peap.downloaders.sse_physical import ShanghaiPhysicalAssetDownloader
from scripts._paths import open_readonly_sqlite_db, resolve_cleanup_paths

SHANGHAI_EXCHANGES = {"shanghai", "上交所"}
SHANGHAI_PHYSICAL = "实物资产"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _is_bad_sse_shell_snapshot(html_text: str, *, expected_project_code: str) -> bool:
    if "Network Error" not in html_text:
        return False
    if "上海联合产权交易所" not in html_text and "suaee.com" not in html_text:
        return False
    if ShanghaiPhysicalAssetDownloader._is_real_detail_page(
        html_text=html_text,
        expected_project_code=expected_project_code,
    ):
        return False
    soup = BeautifulSoup(html_text, "html.parser")
    if soup.select_one(".project-detail-top") is None:
        return False
    title = ShanghaiPhysicalAssetDownloader._extract_detail_title(soup)
    has_content = ShanghaiPhysicalAssetDownloader._has_meaningful_detail_content(soup)
    return not title or not has_content


def _load_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            """
            SELECT
                r.record_id,
                r.project_code,
                r.project_name,
                r.project_type,
                r.exchange,
                r.state,
                r.source_file,
                r.archive_path,
                rr.revision_id
            FROM records r
            LEFT JOIN record_revisions rr ON rr.revision_id = r.latest_revision_id
            WHERE r.exchange IN ('shanghai', '上交所')
              AND r.project_type = '实物资产'
            ORDER BY r.exchange, r.project_code, r.record_id
            """
        )
    )


def _uninspectable_snapshot(
    row: sqlite3.Row,
    *,
    path_field: str,
    path: str,
    error: OSError,
) -> dict[str, Any]:
    return {
        "record_id": _text(row["record_id"]),
        "revision_id": row["revision_id"],
        "project_code": _text(row["project_code"]),
        "project_name": _text(row["project_name"]),
        "project_type": _text(row["project_type"]),
        "exchange": _text(row["exchange"]),
        "state": _text(row["state"]),
        "path_field": path_field,
        "path": path,
        "error": {
            "type": error.__class__.__name__,
            "message": str(error),
        },
    }


def _find_bad_records(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    bad_paths: set[str] = set()
    uninspectable_snapshots: list[dict[str, Any]] = []
    for row in _load_rows(conn):
        paths = [
            ("source_file", _text(row["source_file"])),
            ("archive_path", _text(row["archive_path"])),
        ]
        matched_paths: list[str] = []
        inspected_paths: set[str] = set()
        for path_field, path in paths:
            if not path or path in inspected_paths:
                continue
            inspected_paths.add(path)
            if not os.path.isfile(path):
                continue
            try:
                html_text = _read_text(path)
            except OSError as exc:
                uninspectable_snapshots.append(
                    _uninspectable_snapshot(row, path_field=path_field, path=path, error=exc)
                )
                continue
            if _is_bad_sse_shell_snapshot(html_text, expected_project_code=_text(row["project_code"])):
                matched_paths.append(path)
                bad_paths.add(path)
        if not matched_paths:
            continue
        candidates.append(
            {
                "record_id": _text(row["record_id"]),
                "revision_id": row["revision_id"],
                "project_code": _text(row["project_code"]),
                "project_name": _text(row["project_name"]),
                "project_type": _text(row["project_type"]),
                "exchange": _text(row["exchange"]),
                "state": _text(row["state"]),
                "source_file": _text(row["source_file"]),
                "archive_path": _text(row["archive_path"]),
                "bad_paths": matched_paths,
            }
        )
    return candidates, sorted(bad_paths), uninspectable_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="default: AppConfig.STREAMING_DB_PATH")
    parser.add_argument("--archive-root", default=None,
                        help="default: AppConfig.ARCHIVE_ROOT")
    parser.add_argument("--app-home", default=None,
                        help="default: AppConfig.APP_HOME")
    args = resolve_cleanup_paths(parser.parse_args())

    db_path = os.path.abspath(args.db)
    archive_root = os.path.abspath(args.archive_root)
    if not os.path.isfile(db_path):
        raise SystemExit(f"database not found: {db_path}")

    with open_readonly_sqlite_db(db_path) as conn:
        candidates, bad_paths, uninspectable_snapshots = _find_bad_records(conn)

    summary = {
        "db_path": db_path,
        "archive_root": archive_root,
        "bad_record_count": len(candidates),
        "bad_snapshot_count": len(bad_paths),
        "uninspectable_record_count": len(
            {item["record_id"] for item in uninspectable_snapshots if item["record_id"]}
        ),
        "uninspectable_snapshot_count": len({item["path"] for item in uninspectable_snapshots}),
        "by_exchange_state": {},
        "sample_records": candidates[:10],
        "candidate_snapshot_paths": bad_paths,
        "uninspectable_snapshots": uninspectable_snapshots[:20],
        "recovery_boundary": "report_only",
        "next_action": "Review this report through the controlled operations process before quarantining or removing snapshots.",
    }
    by_key: dict[str, int] = {}
    for item in candidates:
        key = f"{item['exchange']}|{item['project_type']}|{item['state']}"
        by_key[key] = by_key.get(key, 0) + 1
    summary["by_exchange_state"] = dict(sorted(by_key.items()))

    print(json.dumps({"mode": "report_only", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
