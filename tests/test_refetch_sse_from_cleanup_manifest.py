from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys


def _create_records_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE records (
                record_id TEXT PRIMARY KEY,
                project_code TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                listing_date TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                source_identity_json TEXT
            )
            """
        )


def _run_refetch_script(
    *,
    repo_root: str,
    manifest_path: str,
    source_db: str,
    current_db: str,
    archive_root: str,
    app_home: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            os.path.join(repo_root, "scripts", "refetch_sse_from_cleanup_manifest.py"),
            "--manifest",
            manifest_path,
            "--source-db",
            source_db,
            "--db",
            current_db,
            "--archive-root",
            archive_root,
            "--app-home",
            app_home,
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": repo_root},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_corrupt_source_identity_json_aborts_instead_of_planning_missing_identity(tmp_path) -> None:
    repo_root = os.getcwd()
    source_file = str(tmp_path / "archive" / "bad-source.html")
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": [{"source": source_file}]}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            INSERT INTO records (
                record_id,
                project_code,
                project_name,
                listing_date,
                source_file,
                source_identity_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "rec-corrupt-source-identity",
                "",
                "Corrupt SSE fixture",
                "2026-05-30",
                source_file,
                "{",
            ),
        )

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "corrupt_json" in combined_output
    assert "rec-corrupt-source-identity" in combined_output
    assert "source_identity_json" in combined_output
    assert "missing_xmid_or_source_url" not in combined_output


def test_non_object_source_identity_json_aborts_instead_of_empty_identity(tmp_path) -> None:
    repo_root = os.getcwd()
    source_file = str(tmp_path / "archive" / "bad-shape-source.html")
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": [{"source": source_file}]}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            INSERT INTO records (
                record_id,
                project_code,
                project_name,
                listing_date,
                source_file,
                source_identity_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "rec-non-object-source-identity",
                "",
                "Non-object SSE fixture",
                "2026-05-30",
                source_file,
                "[]",
            ),
        )

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "corrupt_json" in combined_output
    assert "rec-non-object-source-identity" in combined_output
    assert "source_identity_json must be an object" in combined_output
    assert "missing_xmid_or_source_url" not in combined_output


def test_manifest_source_without_record_is_reported_instead_of_empty_plan(tmp_path) -> None:
    repo_root = os.getcwd()
    missing_source_file = str(tmp_path / "archive" / "manifest-only-source.html")
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": [{"source": missing_source_file}]}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["candidate_count"] == 0
    assert manifest["missing_count"] == 0
    assert manifest["unmatched_manifest_source_count"] == 1
    assert manifest["unmatched_manifest_sources"] == [missing_source_file]


def test_manifest_moves_must_be_a_list_instead_of_silent_empty_plan(tmp_path) -> None:
    repo_root = os.getcwd()
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": {"source": "bad-shape.html"}}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "malformed_cleanup_manifest" in combined_output
    assert "moves" in combined_output
    assert "candidate_count" not in combined_output


def test_manifest_move_item_requires_source_instead_of_silent_empty_plan(tmp_path) -> None:
    repo_root = os.getcwd()
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": [{"destination": "missing-source.html"}]}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "malformed_cleanup_manifest" in combined_output
    assert "moves[0].source" in combined_output
    assert "candidate_count" not in combined_output


def test_null_source_identity_json_remains_valid_optional_empty_identity_path(tmp_path) -> None:
    repo_root = os.getcwd()
    source_file = str(tmp_path / "archive" / "missing-identity.html")
    manifest_path = str(tmp_path / "cleanup-manifest.json")
    source_db = str(tmp_path / "source.sqlite3")
    current_db = str(tmp_path / "current.sqlite3")
    archive_root = str(tmp_path / "archive")
    app_home = str(tmp_path / "app-home")

    os.makedirs(archive_root, exist_ok=True)
    os.makedirs(app_home, exist_ok=True)
    (tmp_path / "cleanup-manifest.json").write_text(
        json.dumps({"moves": [{"source": source_file}]}),
        encoding="utf-8",
    )
    _create_records_table(source_db)
    _create_records_table(current_db)
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            INSERT INTO records (
                record_id,
                project_code,
                project_name,
                listing_date,
                source_file,
                source_identity_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "rec-null-source-identity",
                "",
                "Null SSE fixture",
                "2026-05-30",
                source_file,
                None,
            ),
        )

    result = _run_refetch_script(
        repo_root=repo_root,
        manifest_path=manifest_path,
        source_db=source_db,
        current_db=current_db,
        archive_root=archive_root,
        app_home=app_home,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["missing_count"] == 1
    assert manifest["missing"][0]["reason"] == "missing_xmid_or_source_url"
