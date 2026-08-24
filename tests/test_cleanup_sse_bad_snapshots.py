from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import cleanup_sse_bad_snapshots as cleanup


def _create_sse_records_db(db_path: Path, snapshot_path: Path) -> None:
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
                latest_revision_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE record_revisions (
                revision_id TEXT PRIMARY KEY
            );
            """
        )
        conn.execute(
            """
            INSERT INTO record_revisions (revision_id)
            VALUES ('rev-unreadable')
            """
        )
        conn.execute(
            """
            INSERT INTO records (
                record_id,
                project_code,
                project_name,
                project_type,
                exchange,
                state,
                source_file,
                archive_path,
                latest_revision_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rec-unreadable",
                "G32024SH1000001",
                "Unreadable SSE snapshot",
                "实物资产",
                "shanghai",
                "ready",
                str(snapshot_path),
                "",
                "rev-unreadable",
            ),
        )


def test_main_reports_existing_sse_snapshot_that_cannot_be_read(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db_path = tmp_path / "streaming.sqlite3"
    archive_root = tmp_path / "archive"
    app_home = tmp_path / "app-home"
    snapshot_path = archive_root / "unreadable.html"
    archive_root.mkdir()
    app_home.mkdir()
    snapshot_path.write_text("<html>present but unreadable</html>", encoding="utf-8")
    _create_sse_records_db(db_path, snapshot_path)

    def fail_read_text(path: str) -> str:
        assert path == str(snapshot_path)
        raise OSError("permission denied")

    monkeypatch.setattr(cleanup, "_read_text", fail_read_text)
    monkeypatch.setattr(
        "sys.argv",
        [
            "cleanup_sse_bad_snapshots.py",
            "--db",
            str(db_path),
            "--archive-root",
            str(archive_root),
            "--app-home",
            str(app_home),
        ],
    )

    exit_code = cleanup.main()

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["bad_record_count"] == 0
    assert manifest["bad_snapshot_count"] == 0
    assert manifest["uninspectable_snapshot_count"] == 1
    assert manifest["uninspectable_record_count"] == 1
    assert manifest["uninspectable_snapshots"] == [
        {
            "record_id": "rec-unreadable",
            "revision_id": "rev-unreadable",
            "project_code": "G32024SH1000001",
            "project_name": "Unreadable SSE snapshot",
            "project_type": "实物资产",
            "exchange": "shanghai",
            "state": "ready",
            "path_field": "source_file",
            "path": str(snapshot_path),
            "error": {
                "type": "OSError",
                "message": "permission denied",
            },
        }
    ]
