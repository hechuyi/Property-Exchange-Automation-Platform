from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class RecoverMissingArchiveFilesContractTest(unittest.TestCase):
    def test_allows_missing_optional_payload_json_and_uses_source_identity_url(self) -> None:
        repo_root = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "streaming.sqlite3")
            missing_archive = os.path.join(tmp, "archive", "missing.html")
            os.makedirs(os.path.dirname(missing_archive), exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE records (
                        record_id TEXT PRIMARY KEY,
                        business_key TEXT NOT NULL,
                        project_code TEXT NOT NULL DEFAULT '',
                        project_name TEXT NOT NULL DEFAULT '',
                        project_type TEXT NOT NULL DEFAULT '',
                        exchange TEXT NOT NULL DEFAULT '',
                        listing_date TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL,
                        source_file TEXT NOT NULL DEFAULT '',
                        source_identity_json TEXT NOT NULL DEFAULT '{}',
                        latest_revision_id INTEGER
                    );
                    CREATE TABLE record_revisions (
                        revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        record_id TEXT NOT NULL,
                        revision_hash TEXT NOT NULL,
                        parser_payload_json TEXT DEFAULT '',
                        postprocess_payload_json TEXT DEFAULT '',
                        state TEXT NOT NULL,
                        source_file TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
                cursor = conn.execute(
                    """
                    INSERT INTO record_revisions (
                        record_id,
                        revision_hash,
                        parser_payload_json,
                        postprocess_payload_json,
                        state,
                        source_file
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rec-empty-json",
                        "rev-empty-json",
                        "",
                        None,
                        "parsed",
                        missing_archive,
                    ),
                )
                revision_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id,
                        business_key,
                        project_code,
                        project_name,
                        exchange,
                        listing_date,
                        state,
                        source_file,
                        source_identity_json,
                        latest_revision_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rec-empty-json",
                        "bk-empty-json",
                        "PRJ002",
                        "Empty JSON fixture",
                        "CBEX",
                        "2026-05-01",
                        "parsed",
                        missing_archive,
                        json.dumps({"source_url": "https://www.cbex.com/project/PRJ002.html"}),
                        revision_id,
                    ),
                )

            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"),
                    "--archive-root",
                    os.path.join(tmp, "archive"),
                    "--db",
                    db_path,
                ],
                cwd=repo_root,
                env={
                    **os.environ,
                    "PYTHONPATH": repo_root,
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["cbex_count"], 1)
            self.assertEqual(manifest["candidates"]["cbex"][0]["code"], "PRJ002")
            self.assertEqual(result.stderr, "")

    def test_rejects_present_corrupt_parser_payload_json_instead_of_falling_back_to_candidate_success(
        self,
    ) -> None:
        repo_root = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "streaming.sqlite3")
            missing_archive = os.path.join(tmp, "archive", "missing.html")
            os.makedirs(os.path.dirname(missing_archive), exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE records (
                        record_id TEXT PRIMARY KEY,
                        business_key TEXT NOT NULL,
                        project_code TEXT NOT NULL DEFAULT '',
                        project_name TEXT NOT NULL DEFAULT '',
                        project_type TEXT NOT NULL DEFAULT '',
                        exchange TEXT NOT NULL DEFAULT '',
                        listing_date TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL,
                        source_file TEXT NOT NULL DEFAULT '',
                        source_identity_json TEXT NOT NULL DEFAULT '{}',
                        latest_revision_id INTEGER
                    );
                    CREATE TABLE record_revisions (
                        revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        record_id TEXT NOT NULL,
                        revision_hash TEXT NOT NULL,
                        parser_payload_json TEXT NOT NULL DEFAULT '{}',
                        postprocess_payload_json TEXT NOT NULL DEFAULT '{}',
                        state TEXT NOT NULL,
                        source_file TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
                cursor = conn.execute(
                    """
                    INSERT INTO record_revisions (
                        record_id,
                        revision_hash,
                        parser_payload_json,
                        postprocess_payload_json,
                        state,
                        source_file
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rec-corrupt-json",
                        "rev-corrupt-json",
                        "{",
                        "{}",
                        "parsed",
                        missing_archive,
                    ),
                )
                revision_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id,
                        business_key,
                        project_code,
                        project_name,
                        exchange,
                        listing_date,
                        state,
                        source_file,
                        source_identity_json,
                        latest_revision_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rec-corrupt-json",
                        "bk-corrupt-json",
                        "PRJ001",
                        "Corrupt JSON fixture",
                        "CBEX",
                        "2026-05-01",
                        "parsed",
                        missing_archive,
                        json.dumps({"source_url": "https://www.cbex.com/project/PRJ001.html"}),
                        revision_id,
                    ),
                )

            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(repo_root, "scripts", "recover_missing_archive_files.py"),
                    "--archive-root",
                    os.path.join(tmp, "archive"),
                    "--db",
                    db_path,
                ],
                cwd=repo_root,
                env={
                    **os.environ,
                    "PYTHONPATH": repo_root,
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertIn("corrupt_json", output)
            self.assertIn("parser_payload_json", output)
            self.assertNotIn('"cbex_count": 1', result.stdout)


if __name__ == "__main__":
    unittest.main()
