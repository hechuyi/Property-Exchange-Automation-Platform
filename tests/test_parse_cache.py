from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from peap.constants import (
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_LISTED,
    TYPE_EQUITY_TRANSFER,
)
from peap.parse_cache import ParseCacheStore
from peap.parsing import COMPAT_PROFILE_FULL, build_parsed_project


class ParseCacheContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_parse_cache_rehydrates_standard_record(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "sample.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        parsed = build_parsed_project(
            file_path=html_file,
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P001",
                "\u9879\u76ee\u540d\u79f0": "\u793a\u4f8b\u9879\u76ee",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
        )
        store.put(parsed, compat_profile=COMPAT_PROFILE_FULL)
        store._conn.commit()

        cached = store.get(html_file, compat_profile=COMPAT_PROFILE_FULL)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.file_path, html_file)
        self.assertEqual(cached.exchange, "shenzhen")
        self.assertEqual(cached.encoding, "utf-8")
        self.assertEqual(cached.standard_record.project_code, "P001")
        self.assertEqual(cached.standard_record.project_name, "\u793a\u4f8b\u9879\u76ee")
        self.assertEqual(cached.standard_record.status, STATUS_LISTED)
        self.assertEqual(cached.standard_record.project_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(cached.project_code, "P001")
        self.assertEqual(cached.project_name, "\u793a\u4f8b\u9879\u76ee")
        self.assertEqual(cached.status, STATUS_LISTED)
        self.assertEqual(cached.project_type, TYPE_EQUITY_TRANSFER)
        self.assertFalse(cached.is_pre_disclosure)

    def test_parse_cache_prefers_structured_parsed_project_payload(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "structured.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        compat_payload = {
            KEY_PROJECT_CODE: "P002",
            "项目名称": "兼容名称",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        structured_payload = build_parsed_project(
            file_path=html_file,
            exchange="shenzhen",
            encoding="utf-8",
            data=compat_payload,
        ).to_cache_payload()
        structured_payload["standard_record"]["project_name"] = "结构化名称"

        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                compat_profile,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                COMPAT_PROFILE_FULL,
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(compat_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(structured_payload, ensure_ascii=False, sort_keys=True),
                "2026-03-19T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file, compat_profile=COMPAT_PROFILE_FULL)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.data["项目名称"], "兼容名称")
        self.assertEqual(cached.standard_record.project_name, "结构化名称")
        self.assertEqual(cached.project_name, "结构化名称")
        self.assertEqual(cached.to_compat_payload(include_raw=True)["项目名称"], "结构化名称")

    def test_parse_cache_merges_partial_structured_payload_with_compat_payload(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "partial-structured.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        compat_payload = {
            KEY_PROJECT_CODE: "P004",
            "项目名称": "兼容名称",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        structured_payload = {
            "compat_payload": compat_payload,
            "standard_record": {
                "project_name": "结构化名称",
            },
        }

        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                compat_profile,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                COMPAT_PROFILE_FULL,
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(compat_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(structured_payload, ensure_ascii=False, sort_keys=True),
                "2026-03-19T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file, compat_profile=COMPAT_PROFILE_FULL)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.project_code, "P004")
        self.assertEqual(cached.project_name, "结构化名称")
        self.assertEqual(cached.standard_record.project_type, TYPE_EQUITY_TRANSFER)

    def test_parse_cache_supports_legacy_payload_rows(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "legacy.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        legacy_payload = {
            KEY_PROJECT_CODE: "P003",
            "项目名称": "旧缓存项目",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                compat_profile,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                COMPAT_PROFILE_FULL,
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(legacy_payload, ensure_ascii=False, sort_keys=True),
                "",
                "2026-03-19T00:00:00",
            ),
        )
        store.flush()

        cached = store.get(html_file, compat_profile=COMPAT_PROFILE_FULL)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.project_code, "P003")
        self.assertEqual(cached.project_name, "旧缓存项目")
        self.assertEqual(cached.standard_record.project_type, TYPE_EQUITY_TRANSFER)

    def test_parse_cache_misses_when_run_signature_version_changes(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "versioned.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        parsed = build_parsed_project(
            file_path=html_file,
            exchange="shenzhen",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "P005",
                "项目名称": "版本化缓存项目",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            },
        )

        writer = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="full|decoder=v1|classifier=v1|family=v1|variant=v1|assembler=v1|normalizer=v1|policy=v1",
            commit_interval=1,
        )
        self.addCleanup(writer.close)
        writer.put(parsed, compat_profile=COMPAT_PROFILE_FULL)
        writer.flush()

        reader = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="full|decoder=v2|classifier=v1|family=v1|variant=v1|assembler=v1|normalizer=v1|policy=v1",
            commit_interval=1,
        )
        self.addCleanup(reader.close)


    def test_parser_pipeline_run_signature_includes_subsystem_versions(self) -> None:
        html_root = os.path.join(self.temp_dir.name, "pipeline-html")
        os.makedirs(html_root, exist_ok=True)
        captured: dict[str, str] = {}

        class _FakeCacheStore:
            def __init__(self, *, db_path: str, run_signature: str, logger=None, commit_interval: int = 50):
                captured["run_signature"] = run_signature

            @property
            def stats(self):
                return type("_Stats", (), {"hits": 0, "misses": 0, "writes": 0})()

            def flush(self) -> None:
                return None

            def close(self) -> None:
                return None

        from peap.pipeline import ParserPipeline

        with patch("peap.pipeline.ParseCacheStore", _FakeCacheStore), patch(
            "peap.pipeline.build_parser_signature",
            return_value="legacy-parser-signature",
        ):
            summary = ParserPipeline(
                html_root=html_root,
                dry_run=True,
                parse_cache_enabled=True,
                parse_cache_db=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            ).run()

        self.assertEqual(summary.processed, 0)
        self.assertIn("decoder=", captured["run_signature"])
        self.assertIn("classifier=", captured["run_signature"])
        self.assertIn("family=", captured["run_signature"])
        self.assertIn("variant=", captured["run_signature"])
        self.assertIn("assembler=", captured["run_signature"])
        self.assertIn("normalizer=", captured["run_signature"])
        self.assertIn("policy=", captured["run_signature"])
