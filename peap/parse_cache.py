"""Persistent parse cache for incremental parser runs."""

from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .parsing import ParsedProject

CACHE_SCHEMA_VERSION = "v2"
DECODER_VERSION = "snapshot_decoder/v1"
CLASSIFIER_VERSION = "source_classifier/v1"
PARSER_FAMILY_VERSION = "parser_family_runtime/v1"
PARSER_VARIANT_VERSION = "parser_variant_runtime/v1"
ASSEMBLER_VERSION = "record_assembler/v1"
NORMALIZER_VERSION = "record_normalizer/v1"
POLICY_VERSION = "policy_engine/v1"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_parser_signature() -> str:
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    target_files = [
        os.path.join(root_dir, "peap", "parsing.py"),
        os.path.join(root_dir, "peap", "finance_fallback.py"),
        os.path.join(root_dir, "peap", "group_fallback.py"),
        os.path.join(root_dir, "peap", "pre_disclosure_fallback.py"),
        os.path.join(root_dir, "peap", "pathing.py"),
        os.path.join(root_dir, "peap", "output_mapping.py"),
        os.path.join(root_dir, "peap", "targeting.py"),
        os.path.join(root_dir, "peap", "standard_model.py"),
        os.path.join(root_dir, "peap", "excel_handler.py"),
    ]
    target_files.extend(glob.glob(os.path.join(root_dir, "parsers", "*.py")))
    target_files = sorted({os.path.abspath(path) for path in target_files if os.path.isfile(path)})
    rows = [f"schema={CACHE_SCHEMA_VERSION}"]
    for path in target_files:
        stat = os.stat(path)
        rel = os.path.relpath(path, root_dir).replace("\\", "/")
        rows.append(f"{rel}|{stat.st_size}|{stat.st_mtime_ns}")
    return _sha256_text("\n".join(rows))


def build_runtime_version_signature() -> str:
    parts = [
        f"decoder={DECODER_VERSION}",
        f"classifier={CLASSIFIER_VERSION}",
        f"family={PARSER_FAMILY_VERSION}",
        f"variant={PARSER_VARIANT_VERSION}",
        f"assembler={ASSEMBLER_VERSION}",
        f"normalizer={NORMALIZER_VERSION}",
        f"policy={POLICY_VERSION}",
    ]
    return "|".join(parts)


    hits: int = 0
    misses: int = 0
    writes: int = 0


class ParseCacheStore:
    def __init__(
        self,
        *,
        db_path: str,
        run_signature: str,
        logger: Optional[logging.Logger] = None,
        commit_interval: int = 50,
    ):
        self.db_path = os.path.abspath(db_path)
        self.run_signature = str(run_signature)
        self.logger = logger or logging.getLogger("parser_v2")
        self.commit_interval = max(1, int(commit_interval))
        self._pending_writes = 0
        self._hits = 0
        self._misses = 0
        self._writes = 0

        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parse_cache (
                file_path TEXT NOT NULL,
                compat_profile TEXT NOT NULL,
                run_signature TEXT NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                file_size INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                encoding TEXT NOT NULL,
                data_json TEXT NOT NULL,
                parsed_json TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (file_path, compat_profile, run_signature)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS output_cache (
                file_path TEXT NOT NULL,
                compat_profile TEXT NOT NULL,
                run_signature TEXT NOT NULL,
                target_file TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                synced INTEGER NOT NULL DEFAULT 0,
                target_mtime_ns INTEGER NOT NULL DEFAULT -1,
                target_size INTEGER NOT NULL DEFAULT -1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (file_path, compat_profile, run_signature)
            )
            """
        )
        self._ensure_parse_cache_columns()
        self._ensure_output_cache_columns()
        self._conn.execute(
            "DELETE FROM parse_cache WHERE run_signature <> ?",
            (self.run_signature,),
        )
        self._conn.execute(
            "DELETE FROM output_cache WHERE run_signature <> ?",
            (self.run_signature,),
        )
        self._conn.commit()

    def _ensure_parse_cache_columns(self) -> None:
        existing = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(parse_cache)").fetchall()
            if len(row) >= 2
        }
        if "parsed_json" not in existing:
            try:
                self._conn.execute(
                    "ALTER TABLE parse_cache ADD COLUMN parsed_json TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _ensure_output_cache_columns(self) -> None:
        existing = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(output_cache)").fetchall()
            if len(row) >= 2
        }
        if "target_mtime_ns" not in existing:
            try:
                self._conn.execute(
                    "ALTER TABLE output_cache ADD COLUMN target_mtime_ns INTEGER NOT NULL DEFAULT -1"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "target_size" not in existing:
            try:
                self._conn.execute(
                    "ALTER TABLE output_cache ADD COLUMN target_size INTEGER NOT NULL DEFAULT -1"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def close(self) -> None:
        try:
            if self._pending_writes > 0:
                self._conn.commit()
        finally:
            self._conn.close()

    def flush(self) -> None:
        if self._pending_writes <= 0:
            return
        self._conn.commit()
        self._pending_writes = 0

    @property
    def stats(self) -> CacheStats:
        return CacheStats(hits=self._hits, misses=self._misses, writes=self._writes)

    def get(self, file_path: str, *, compat_profile: str) -> Optional[ParsedProject]:
        abs_path = os.path.abspath(file_path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            self._misses += 1
            return None

        row = self._conn.execute(
            """
            SELECT file_mtime_ns, file_size, exchange, encoding, data_json, parsed_json
            FROM parse_cache
            WHERE file_path = ? AND compat_profile = ? AND run_signature = ?
            """,
            (abs_path, compat_profile, self.run_signature),
        ).fetchone()
        if row is None:
            self._misses += 1
            return None

        cached_mtime_ns, cached_size, exchange, encoding, data_json, parsed_json = row
        if int(cached_mtime_ns) != int(stat.st_mtime_ns) or int(cached_size) != int(stat.st_size):
            self._misses += 1
            return None

        for raw_json in (str(parsed_json or ""), str(data_json or "")):
            if not raw_json:
                continue
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                parsed = ParsedProject.from_cache_payload(
                    file_path=file_path,
                    exchange=str(exchange),
                    encoding=str(encoding),
                    payload=payload,
                )
            except Exception:
                continue
            self._hits += 1
            return parsed

        self._conn.execute(
            """
            DELETE FROM parse_cache
            WHERE file_path = ? AND compat_profile = ? AND run_signature = ?
            """,
            (abs_path, compat_profile, self.run_signature),
        )
        self._pending_writes += 1
        self._misses += 1
        return None

    def put(self, parsed: ParsedProject, *, compat_profile: str) -> None:
        abs_path = os.path.abspath(parsed.file_path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            return

        legacy_payload = json.dumps(parsed.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parsed_payload = json.dumps(
            parsed.to_cache_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._conn.execute(
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
                abs_path,
                compat_profile,
                self.run_signature,
                int(stat.st_mtime_ns),
                int(stat.st_size),
                parsed.exchange,
                parsed.encoding,
                legacy_payload,
                parsed_payload,
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._pending_writes += 1
        self._writes += 1
        if self._pending_writes >= self.commit_interval:
            self.flush()

    def is_output_synced(
        self,
        file_path: str,
        *,
        compat_profile: str,
        target_file: str,
        payload_hash: str,
    ) -> bool:
        abs_path = os.path.abspath(file_path)
        target_abs = os.path.abspath(target_file)
        if not os.path.exists(target_abs):
            return False
        try:
            target_stat = os.stat(target_abs)
        except OSError:
            return False
        row = self._conn.execute(
            """
            SELECT target_file, payload_hash, synced, target_mtime_ns, target_size
            FROM output_cache
            WHERE file_path = ? AND compat_profile = ? AND run_signature = ?
            """,
            (abs_path, compat_profile, self.run_signature),
        ).fetchone()
        if row is None:
            return False
        cached_target, cached_hash, cached_synced, cached_target_mtime_ns, cached_target_size = row
        return (
            int(cached_synced) == 1
            and str(cached_target) == target_abs
            and str(cached_hash) == str(payload_hash)
            and int(cached_target_mtime_ns) == int(target_stat.st_mtime_ns)
            and int(cached_target_size) == int(target_stat.st_size)
        )

    def mark_output_pending(
        self,
        file_path: str,
        *,
        compat_profile: str,
        target_file: str,
        payload_hash: str,
    ) -> None:
        abs_path = os.path.abspath(file_path)
        target_abs = os.path.abspath(target_file)
        try:
            target_stat = os.stat(target_abs)
            target_mtime_ns = int(target_stat.st_mtime_ns)
            target_size = int(target_stat.st_size)
        except OSError:
            target_mtime_ns = -1
            target_size = -1
        self._conn.execute(
            """
            INSERT OR REPLACE INTO output_cache (
                file_path,
                compat_profile,
                run_signature,
                target_file,
                payload_hash,
                synced,
                target_mtime_ns,
                target_size,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                abs_path,
                compat_profile,
                self.run_signature,
                target_abs,
                str(payload_hash),
                target_mtime_ns,
                target_size,
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._pending_writes += 1
        if self._pending_writes >= self.commit_interval:
            self.flush()

    def mark_output_synced_batch(
        self,
        file_paths: list[str],
        *,
        compat_profile: str,
        target_file: str,
    ) -> None:
        if not file_paths:
            return
        target_abs = os.path.abspath(target_file)
        try:
            target_stat = os.stat(target_abs)
            target_mtime_ns = int(target_stat.st_mtime_ns)
            target_size = int(target_stat.st_size)
        except OSError:
            target_mtime_ns = -1
            target_size = -1
        normalized = [os.path.abspath(path) for path in file_paths]
        self._conn.executemany(
            """
            UPDATE output_cache
            SET synced = 1, target_mtime_ns = ?, target_size = ?, updated_at = ?
            WHERE file_path = ? AND compat_profile = ? AND run_signature = ?
            """,
            [
                (
                    target_mtime_ns,
                    target_size,
                    dt.datetime.now().isoformat(timespec="seconds"),
                    path,
                    compat_profile,
                    self.run_signature,
                )
                for path in normalized
            ],
        )
        self._pending_writes += 1
        if self._pending_writes >= self.commit_interval:
            self.flush()
