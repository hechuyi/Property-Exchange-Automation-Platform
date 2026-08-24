from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import cleanup_missing_source_records as cleanup


def test_main_reports_active_archive_only_missing_record(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db_path = tmp_path / "streaming.sqlite3"
    missing_archive_path = "/tmp/missing.html"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE records (
                record_id TEXT PRIMARY KEY,
                project_code TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                project_type TEXT NOT NULL DEFAULT '',
                exchange TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                archive_path TEXT NOT NULL DEFAULT '',
                latest_revision_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO records (
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
            )
            VALUES (
                'archive-only-active',
                'CODE001',
                'Archive only active record',
                'listing',
                'SSE',
                'active',
                '',
                '/tmp/missing.html',
                '',
                '2026-05-31T00:00:00'
            );
            """
        )

    monkeypatch.setattr(cleanup.os.path, "isfile", lambda path: path != missing_archive_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "cleanup_missing_source_records.py",
            "--db",
            str(db_path),
            "--app-home",
            str(tmp_path / "app-home"),
        ],
    )

    exit_code = cleanup.main()

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["missing_record_count"] == 1
    assert manifest["sample_records"] == [
        {
            "record_id": "archive-only-active",
            "project_code": "CODE001",
            "project_name": "Archive only active record",
            "project_type": "listing",
            "exchange": "SSE",
            "state": "active",
            "source_file": "",
            "archive_path": missing_archive_path,
            "created_at": "2026-05-31T00:00:00",
        }
    ]
