#!/usr/bin/env python3
"""Plan SSE physical-asset refetches from a cleanup manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from typing import Any

from scripts._paths import resolve_cleanup_paths


class SourceIdentityJsonError(ValueError):
    def __init__(self, *, record_id: str, column: str, message: str) -> None:
        super().__init__(f"corrupt_json in {column} for record_id={record_id}: {message}")
        self.record_id = record_id
        self.column = column
        self.message = message

    def to_diagnostic(self) -> dict[str, str]:
        return {
            "diagnostic": "corrupt_json",
            "record_id": self.record_id,
            "column": self.column,
            "message": self.message,
        }


class CleanupManifestError(ValueError):
    def __init__(self, *, path: str, field: str, message: str) -> None:
        super().__init__(f"malformed_cleanup_manifest in {field} for path={path}: {message}")
        self.path = path
        self.field = field
        self.message = message

    def to_diagnostic(self) -> dict[str, str]:
        return {
            "diagnostic": "malformed_cleanup_manifest",
            "path": self.path,
            "field": self.field,
            "message": self.message,
        }


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _extract_xmid(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if not text:
            continue
        match = re.search(r"(?:XMID|xmid)=([0-9A-Za-z_-]+)", text)
        if match:
            return match.group(1)
        if re.fullmatch(r"[0-9]{4,}", text):
            return text
    return ""


def _candidate_tokens(source_identity: dict[str, Any]) -> list[str]:
    raw_tokens = source_identity.get("candidate_tokens")
    if isinstance(raw_tokens, list):
        return [_text(item) for item in raw_tokens if _text(item)]
    return []


def _parse_source_identity(row: sqlite3.Row) -> dict[str, Any]:
    raw_json = row["source_identity_json"]
    if raw_json is None or _text(raw_json) == "":
        return {}
    try:
        source_identity = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SourceIdentityJsonError(
            record_id=_text(row["record_id"]),
            column="source_identity_json",
            message=exc.msg,
        ) from exc
    if not isinstance(source_identity, dict):
        raise SourceIdentityJsonError(
            record_id=_text(row["record_id"]),
            column="source_identity_json",
            message="source_identity_json must be an object",
        )
    return source_identity


def _load_cleanup_source_paths(manifest_path: str) -> list[str]:
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise CleanupManifestError(
            path=manifest_path,
            field="$",
            message="manifest root must be a JSON object",
        )
    moves = manifest.get("moves", [])
    if not isinstance(moves, list):
        raise CleanupManifestError(
            path=manifest_path,
            field="moves",
            message="moves must be a list",
        )
    paths: list[str] = []
    for index, item in enumerate(moves):
        if not isinstance(item, dict):
            raise CleanupManifestError(
                path=manifest_path,
                field=f"moves[{index}]",
                message="move item must be an object",
            )
        source = _text(item.get("source"))
        if not source:
            raise CleanupManifestError(
                path=manifest_path,
                field=f"moves[{index}].source",
                message="move item must include a non-empty source",
            )
        paths.append(source)
    return sorted(set(paths))


def _existing_project_codes(db_path: str) -> set[str]:
    if not os.path.isfile(db_path):
        return set()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT project_code FROM records WHERE project_code <> ''").fetchall()
    return {_text(row[0]).upper() for row in rows if _text(row[0])}


def _load_source_rows(source_db: str, source_paths: list[str]) -> list[sqlite3.Row]:
    if not source_paths:
        return []
    placeholders = ",".join("?" for _ in source_paths)
    conn = sqlite3.connect(source_db)
    conn.row_factory = sqlite3.Row
    try:
        return list(
            conn.execute(
                f"""
                SELECT
                    record_id,
                    project_code,
                    project_name,
                    listing_date,
                    source_file,
                    source_identity_json
                FROM records
                WHERE source_file IN ({placeholders})
                ORDER BY listing_date, project_code, record_id
                """,
                source_paths,
            )
        )
    finally:
        conn.close()


def _build_candidates(
    *,
    source_db: str,
    manifest_path: str,
    current_db: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, list[str]]:
    source_paths = _load_cleanup_source_paths(manifest_path)
    existing_codes = _existing_project_codes(current_db)
    candidates: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    skipped_existing = 0
    rows = _load_source_rows(source_db, source_paths)
    matched_source_paths = {_text(row["source_file"]) for row in rows}
    unmatched_manifest_sources = sorted(set(source_paths) - matched_source_paths)

    for row in rows:
        project_code = _text(row["project_code"]).upper()
        if project_code and project_code in existing_codes:
            skipped_existing += 1
            continue
        source_identity = _parse_source_identity(row)

        source_url = _text(source_identity.get("source_url") or source_identity.get("page_url"))
        tokens = _candidate_tokens(source_identity)
        xmid = _extract_xmid(
            source_identity.get("project_id"),
            source_identity.get("xmid"),
            source_url,
            *tokens,
        )
        if not xmid or not source_url:
            missing.append(
                {
                    "project_code": project_code,
                    "project_name": _text(row["project_name"]),
                    "listing_date": _text(row["listing_date"]),
                    "source_file": _text(row["source_file"]),
                    "source_url": source_url,
                    "reason": "missing_xmid_or_source_url",
                }
            )
            continue

        project_name = _text(row["project_name"])
        listing_date = _text(row["listing_date"]) or _text(source_identity.get("listing_date"))
        candidates.append(
            {
                "xmid": xmid,
                "project_code": project_code,
                "project_name": project_name,
                "page_url": source_url,
                "disclosure_start": listing_date,
                "row": {
                    "xmid": xmid,
                    "XMID": xmid,
                    "xmbh": project_code,
                    "XMBH": project_code,
                    "xmmc": project_name,
                    "XMMC": project_name,
                    "plksrq": listing_date,
                    "PLKSRQ": listing_date,
                    "fclass": "SW",
                    "FCLASS": "SW",
                    "projectType": "ZICHANZHUANRANG",
                    "gplx": "2",
                    "xmlx": "",
                },
            }
        )

    return candidates, missing, skipped_existing, unmatched_manifest_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True,
                        help="per-run cleanup manifest path")
    parser.add_argument("--source-db", default=None,
                        help="database containing rows referenced by the manifest; default: current --db")
    parser.add_argument("--app-home", default=None,
                        help="default: AppConfig.APP_HOME")
    parser.add_argument("--db", default=None,
                        help="default: AppConfig.STREAMING_DB_PATH")
    parser.add_argument("--archive-root", default=None,
                        help="default: AppConfig.ARCHIVE_ROOT")
    args = resolve_cleanup_paths(parser.parse_args())

    manifest_path = os.path.abspath(args.manifest)
    current_db = os.path.abspath(args.db)
    source_db = os.path.abspath(args.source_db or args.db)
    archive_root = os.path.abspath(args.archive_root)

    for path in (manifest_path, source_db, current_db):
        if not os.path.isfile(path):
            raise SystemExit(f"required file not found: {path}")

    try:
        candidates, missing, skipped_existing, unmatched_manifest_sources = _build_candidates(
            source_db=source_db,
            manifest_path=manifest_path,
            current_db=current_db,
        )
    except (CleanupManifestError, SourceIdentityJsonError) as exc:
        print(json.dumps(exc.to_diagnostic(), ensure_ascii=False, indent=2), file=os.sys.stderr)
        return 1
    result: dict[str, Any] = {
        "manifest": manifest_path,
        "source_db": source_db,
        "db": current_db,
        "archive_root": archive_root,
        "candidate_count": len(candidates),
        "skipped_existing_count": skipped_existing,
        "missing_count": len(missing),
        "missing": missing[:20],
        "unmatched_manifest_source_count": len(unmatched_manifest_sources),
        "unmatched_manifest_sources": unmatched_manifest_sources[:20],
        "candidates": candidates,
        "next_action": "Submit the reviewed plan to the controlled operations process for a recorded decision.",
    }

    print(json.dumps({"mode": "report_only", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
