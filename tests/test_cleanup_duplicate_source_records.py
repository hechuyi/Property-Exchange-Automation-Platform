from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import cleanup_duplicate_source_records as cleanup


def _create_records_db(db_path: Path, source_file: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE records (
                record_id TEXT PRIMARY KEY,
                project_code TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                exchange TEXT NOT NULL DEFAULT '',
                listing_date TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                archive_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO records (
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "rec-newer",
                    "CODE001",
                    "Newer row",
                    "SSE",
                    "2026-05-01",
                    "parsed",
                    str(source_file),
                    str(source_file),
                    "2026-05-30T00:00:00",
                    "2026-05-31T00:00:00",
                ),
                (
                    "rec-older",
                    "CODE002",
                    "Older row",
                    "SSE",
                    "2026-05-01",
                    "parsed",
                    str(source_file),
                    str(source_file),
                    "2026-05-29T00:00:00",
                    "2026-05-30T00:00:00",
                ),
            ],
        )


def test_parse_failure_marks_duplicate_group_unresolved_without_stale_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "streaming.sqlite3"
    source_file = tmp_path / "corrupt.html"
    source_file.write_text("<html><broken", encoding="utf-8")
    _create_records_db(db_path, source_file)

    def fail_parse(path: str):
        assert path == str(source_file)
        raise ValueError("cannot parse corrupt source")

    monkeypatch.setattr(cleanup, "parse_file", fail_parse)

    plan = cleanup._plan(db_path)

    assert plan["stale_record_ids"] == []
    assert len(plan["groups"]) == 1
    group = plan["groups"][0]
    assert group["resolution_status"] == "unresolved_parse_error"
    assert group["keep_record_id"] == ""
    assert group["stale_record_ids"] == []
    assert group["parse_error"] == {
        "type": "ValueError",
        "message": "cannot parse corrupt source",
    }


def test_main_reports_zero_candidates_when_duplicate_group_parse_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db_path = tmp_path / "streaming.sqlite3"
    source_file = tmp_path / "corrupt.html"
    source_file.write_text("<html><broken", encoding="utf-8")
    _create_records_db(db_path, source_file)

    def fail_parse(path: str):
        assert path == str(source_file)
        raise ValueError("cannot parse corrupt source")

    monkeypatch.setattr(cleanup, "parse_file", fail_parse)
    monkeypatch.setattr("sys.argv", ["cleanup_duplicate_source_records.py", "--db", str(db_path)])

    exit_code = cleanup.main()

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["candidate_record_count"] == 0
    assert manifest["stale_record_ids"] == []
