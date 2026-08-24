"""Explicit migration entry points for the streaming sqlite store."""

from __future__ import annotations

import sqlite3

from .streaming_store import (
    SCHEMA_VERSION,
    _apply_schema_migrations,
    _connection_kwargs,
    _ensure_db_parent_dir,
    _normalize_db_path,
)


class MigrationRunner:
    @staticmethod
    def run(db_path: str) -> int:
        normalized_path = _normalize_db_path(db_path)
        _ensure_db_parent_dir(normalized_path)
        with sqlite3.connect(**_connection_kwargs(normalized_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            _apply_schema_migrations(conn)
            version_row = conn.execute("PRAGMA user_version").fetchone()
            return int(version_row[0] if version_row else SCHEMA_VERSION)


__all__ = ["MigrationRunner"]
