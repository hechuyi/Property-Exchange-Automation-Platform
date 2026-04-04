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
from peap.parsing import build_parsed_project


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
        store.put(parsed)
        store._conn.commit()

        cached = store.get(html_file)

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

        raw_data = {
            KEY_PROJECT_CODE: "P002",
            "项目名称": "兼容名称",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        structured_payload = build_parsed_project(
            file_path=html_file,
            exchange="shenzhen",
            encoding="utf-8",
            data=raw_data,
        ).to_cache_payload()
        structured_payload["standard_record"]["project_name"] = "结构化名称"

        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(raw_data, ensure_ascii=False, sort_keys=True),
                json.dumps(structured_payload, ensure_ascii=False, sort_keys=True),
                "2026-03-19T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.standard_record.project_name, "结构化名称")
        self.assertEqual(cached.project_name, "结构化名称")

    def test_parse_cache_standard_record_fields_from_parsed_json(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "partial-structured.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        legacy_data = {
            KEY_PROJECT_CODE: "P004",
            "项目名称": "兼容名称",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        structured_payload = {
            "standard_record": {
                "project_name": "结构化名称",
                "project_code": "P004",
                "status": STATUS_LISTED,
                "project_type": TYPE_EQUITY_TRANSFER,
            },
        }

        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(legacy_data, ensure_ascii=False, sort_keys=True),
                json.dumps(structured_payload, ensure_ascii=False, sort_keys=True),
                "2026-03-19T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.project_code, "P004")
        self.assertEqual(cached.project_name, "结构化名称")
        self.assertEqual(cached.standard_record.project_type, TYPE_EQUITY_TRANSFER)

    def test_parse_cache_standard_record_in_parsed_json(self) -> None:
        html_file = os.path.join(self.temp_dir.name, "legacy.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache.sqlite3"),
            run_signature="test-signature",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        standard_record_payload = {
            "standard_record": {
                "project_code": "P003",
                "project_name": "旧缓存项目",
                "status": STATUS_LISTED,
                "project_type": TYPE_EQUITY_TRANSFER,
            },
        }
        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path,
                run_signature,
                file_mtime_ns,
                file_size,
                exchange,
                encoding,
                data_json,
                parsed_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                "",
                json.dumps(standard_record_payload, ensure_ascii=False, sort_keys=True),
                "2026-03-19T00:00:00",
            ),
        )
        store.flush()

        cached = store.get(html_file)

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
        writer.put(parsed)
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


class ParseCacheSignatureRegressionTest(unittest.TestCase):
    """Regression tests for build_parser_signature correctness."""

    def test_build_parser_signature_changes_on_parser_subsystem_touch(self) -> None:
        """Touching peap/parser_subsystem.py must change the parser signature."""
        import time

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        subsystem_path = os.path.join(root_dir, "peap", "parser_subsystem.py")

        if not os.path.isfile(subsystem_path):
            self.skipTest("peap/parser_subsystem.py not found")

        from peap.parse_cache import build_parser_signature

        sig_before = build_parser_signature()
        time.sleep(0.01)
        os.utime(subsystem_path, None)
        sig_after = build_parser_signature()

        self.assertNotEqual(
            sig_before,
            sig_after,
            "Parser signature must change when parser_subsystem.py is modified"
        )

    def test_build_parser_signature_changes_on_io_utils_touch(self) -> None:
        """Touching peap/io_utils.py must change the parser signature."""
        import time

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        io_utils_path = os.path.join(root_dir, "peap", "io_utils.py")

        if not os.path.isfile(io_utils_path):
            self.skipTest("peap/io_utils.py not found")

        from peap.parse_cache import build_parser_signature

        sig_before = build_parser_signature()
        time.sleep(0.01)
        os.utime(io_utils_path, None)
        sig_after = build_parser_signature()

        self.assertNotEqual(
            sig_before,
            sig_after,
            "Parser signature must change when io_utils.py is modified"
        )

    def test_build_parser_signature_changes_on_peap_parsers_touch(self) -> None:
        """Touching any peap_parsers/*.py file must change the parser signature."""
        import glob
        import time

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        peap_parsers_dir = os.path.join(root_dir, "peap_parsers")

        if not os.path.isdir(peap_parsers_dir):
            self.skipTest("peap_parsers directory not found")

        # Find a non-init parser file
        parser_files = [
            f for f in glob.glob(os.path.join(peap_parsers_dir, "*.py"))
            if not os.path.basename(f).startswith("__")
        ]

        if not parser_files:
            self.skipTest("No peap_parsers/*.py files found")

        # Pick the first parser file
        test_file = parser_files[0]

        from peap.parse_cache import build_parser_signature

        sig_before = build_parser_signature()
        time.sleep(0.01)
        os.utime(test_file, None)
        sig_after = build_parser_signature()

        self.assertNotEqual(
            sig_before,
            sig_after,
            f"Parser signature must change when {os.path.basename(test_file)} is modified"
        )


class ParseCacheRegressionTest(unittest.TestCase):
    """Regression tests for parse cache contract violations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_parse_cache_store_stats_returns_proper_cache_stats_type(self) -> None:
        """Regression: ParseCacheStore.stats must return a proper CacheStats type.

        Currently CacheStats is not properly defined as a dataclass - the @dataclass
        decorator is missing, causing the type annotation to be broken.
        """
        from peap.parse_cache import CacheStats

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ParseCacheStore(
                db_path=os.path.join(tmp_dir, "parse_cache_stats.sqlite3"),
                run_signature="test-signature-stats",
                commit_interval=1,
            )
            self.addCleanup(store.close)

            stats = store.stats

            # stats must be a proper CacheStats instance
            self.assertIsInstance(stats, CacheStats)

            # Must have the required fields as proper attributes
            self.assertTrue(hasattr(stats, "hits"))
            self.assertTrue(hasattr(stats, "misses"))
            self.assertTrue(hasattr(stats, "writes"))

            # Values must be integers
            self.assertIsInstance(stats.hits, int)
            self.assertIsInstance(stats.misses, int)
            self.assertIsInstance(stats.writes, int)

    def test_build_parser_signature_includes_parser_subsystem_file(self) -> None:
        """Regression: build_parser_signature must include peap/parser_subsystem.py.

        Currently parser_subsystem.py is not in the signature calculation,
        so changes to that file don't invalidate the cache.
        """
        import glob

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        subsystem_path = os.path.join(root_dir, "peap", "parser_subsystem.py")

        # If the file exists, it MUST be included in the signature calculation
        if os.path.isfile(subsystem_path):
            # The fixed implementation uses peap_parsers/*.py and includes parser_subsystem.py
            target_files = [
                os.path.join(root_dir, "peap", "parsing.py"),
                os.path.join(root_dir, "peap", "parser_subsystem.py"),
                os.path.join(root_dir, "peap", "finance_fallback.py"),
                os.path.join(root_dir, "peap", "group_fallback.py"),
                os.path.join(root_dir, "peap", "pre_disclosure_fallback.py"),
                os.path.join(root_dir, "peap", "pathing.py"),
                os.path.join(root_dir, "peap", "output_mapping.py"),
                os.path.join(root_dir, "peap", "targeting.py"),
                os.path.join(root_dir, "peap", "standard_model.py"),
                os.path.join(root_dir, "peap", "excel_handler.py"),
            ]
            target_files.extend(glob.glob(os.path.join(root_dir, "peap_parsers", "*.py")))
            target_files = sorted({os.path.abspath(path) for path in target_files if os.path.isfile(path)})

            # parser_subsystem.py MUST be in the list
            self.assertIn(
                subsystem_path,
                target_files,
                "build_parser_signature must include peap/parser_subsystem.py. "
                "Changes to parser_subsystem.py will not invalidate the parse cache."
            )

    def test_build_parser_signature_includes_peap_parsers_directory(self) -> None:
        """Regression: build_parser_signature must include peap_parsers/* files.

        Currently it uses 'parsers/*.py' instead of 'peap_parsers/*.py'.
        """
        import glob

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        peap_parsers_dir = os.path.join(root_dir, "peap_parsers")
        parsers_dir = os.path.join(root_dir, "parsers")

        if os.path.isdir(peap_parsers_dir):
            peap_parsers_files = glob.glob(os.path.join(peap_parsers_dir, "*.py"))

            # Check what glob pattern is being used
            current_pattern = os.path.join(root_dir, "parsers", "*.py")
            current_files = glob.glob(current_pattern)

            # If peap_parsers exists but parsers doesn't have the right files, regression exists
            self.assertNotEqual(
                len(peap_parsers_files),
                0,
                f"build_parser_signature does not include peap_parsers/*.py. "
                f"Found {len(peap_parsers_files)} files in peap_parsers/ that are not being tracked."
            )

    def test_parse_cache_rejects_compat_payload_without_standard_record(self) -> None:
        """Regression: parsed_json={"compat_payload": {...}} rows must be safe miss.

        Before the fix, rows with only compat_payload (no standard_record) would
        return a ParsedProject with all-empty fields instead of None, causing
        dangerous false cache hits.
        """
        html_file = os.path.join(self.temp_dir.name, "compat_only.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache_compat.sqlite3"),
            run_signature="test-signature-compat",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        compat_payload = {
            "compat_payload": {
                KEY_PROJECT_CODE: "POLD001",
                "项目名称": "旧格式项目",
                KEY_STATUS: STATUS_LISTED,
                KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
            }
        }
        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path, run_signature, file_mtime_ns, file_size,
                exchange, encoding, data_json, parsed_json, source_fingerprint, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature-compat",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                "",
                json.dumps(compat_payload, ensure_ascii=False, sort_keys=True),
                "",
                "2025-01-01T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file)

        self.assertIsNone(
            cached,
            "parsed_json with compat_payload (no standard_record) must return None, "
            "not a ParsedProject with empty fields"
        )

    def test_parse_cache_empty_parsed_json_returns_none(self) -> None:
        """Legacy rows with data_json but empty parsed_json must be safe miss."""
        html_file = os.path.join(self.temp_dir.name, "empty_parsed.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache_empty.sqlite3"),
            run_signature="test-signature-empty",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        legacy_data = {
            KEY_PROJECT_CODE: "POLD002",
            "项目名称": "旧数据项目",
            KEY_STATUS: STATUS_LISTED,
            KEY_PROJECT_TYPE: TYPE_EQUITY_TRANSFER,
        }
        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path, run_signature, file_mtime_ns, file_size,
                exchange, encoding, data_json, parsed_json, source_fingerprint, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature-empty",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                json.dumps(legacy_data, ensure_ascii=False, sort_keys=True),
                "",
                "",
                "2025-01-01T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file)

        self.assertIsNone(
            cached,
            "parsed_json='' with legacy data_json must return None"
        )

    def test_parse_cache_standard_record_payload_returns_valid_project(self) -> None:
        """New-format rows with standard_record must deserialize correctly."""
        html_file = os.path.join(self.temp_dir.name, "standard_record.html")
        with open(html_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")

        store = ParseCacheStore(
            db_path=os.path.join(self.temp_dir.name, "parse_cache_standard.sqlite3"),
            run_signature="test-signature-standard",
            commit_interval=1,
        )
        self.addCleanup(store.close)

        standard_payload = {
            "standard_record": {
                "project_code": "PNEW001",
                "project_name": "新格式项目",
                "status": STATUS_LISTED,
                "project_type": TYPE_EQUITY_TRANSFER,
            }
        }
        stat = os.stat(html_file)
        store._conn.execute(
            """
            INSERT OR REPLACE INTO parse_cache (
                file_path, run_signature, file_mtime_ns, file_size,
                exchange, encoding, data_json, parsed_json, source_fingerprint, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                os.path.abspath(html_file),
                "test-signature-standard",
                int(stat.st_mtime_ns),
                int(stat.st_size),
                "shenzhen",
                "utf-8",
                "",
                json.dumps(standard_payload, ensure_ascii=False, sort_keys=True),
                "",
                "2025-01-01T00:00:00",
            ),
        )
        store._conn.commit()

        cached = store.get(html_file)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.project_code, "PNEW001")
        self.assertEqual(cached.project_name, "新格式项目")
        self.assertEqual(cached.status, STATUS_LISTED)
        self.assertEqual(cached.project_type, TYPE_EQUITY_TRANSFER)
        self.assertEqual(cached.standard_record.project_code, "PNEW001")
        self.assertEqual(cached.standard_record.project_name, "新格式项目")
