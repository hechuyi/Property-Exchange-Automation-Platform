"""Standalone runtime configuration for the pure desktop app."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Dict

DEFAULT_DOWNLOADER_DEFAULTS: Dict[str, Any] = {
    "exchange": "all",
    "project_type": "all",
    "concurrency": 4,
    "split_candidates": 150,
    "split_min_days": 1,
    "split_max_depth": 8,
    "split_mode": "fast",
    "resume": True,
    "save_json": False,
    "auto_split": False,
    "sse_ssl_verify": True,
    "sse_ssl_fallback_insecure": True,
    "sse_ca_bundle": None,
}

DEFAULT_DOWNLOADER_TASK_PAGE_SIZE: Dict[str, int] = {
    "sse:physical_asset": 20,
    "cbex:physical_asset": 16,
    "sse:equity_transfer": 20,
    "sse:capital_increase": 20,
    "sse:pre_disclosure": 20,
    "cbex:equity_transfer": 15,
    "cbex:capital_increase": 15,
    "cbex:pre_disclosure": 15,
    "tpre:physical_asset": 20,
    "tpre:equity_transfer": 20,
    "tpre:capital_increase": 20,
    "tpre:pre_disclosure": 20,
    "cquae:physical_asset": 10,
    "cquae:equity_transfer": 10,
    "cquae:capital_increase": 10,
    "cquae:pre_disclosure": 10,
}


def _home_dir() -> str:
    return os.path.expanduser("~")


def _default_workspace_root() -> str:
    explicit = (
        os.environ.get("PEAP_WORKSPACE_ROOT")
        or os.environ.get("PEAP_APP_HOME")
        or os.environ.get("PEAP_DOCUMENTS_HOME")
    )
    if explicit:
        return os.path.abspath(explicit)
    return os.path.abspath(os.path.join(_home_dir(), "Documents", "PEAP"))


def _legacy_platform_app_home() -> str:
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("LOCALAPPDATA") or os.path.join(_home_dir(), "AppData", "Local")
    elif sys.platform == "darwin":
        base_dir = os.path.join(_home_dir(), "Library", "Application Support")
    else:
        base_dir = os.path.join(_home_dir(), ".local", "share")
    return os.path.abspath(os.path.join(base_dir, "PEAP"))


def _legacy_documents_root() -> str:
    explicit = os.environ.get("PEAP_DOCUMENTS_HOME")
    if explicit:
        return os.path.abspath(explicit)
    return os.path.abspath(os.path.join(_home_dir(), "Documents", "PEAP"))


def _legacy_project_browser_cache(project_root: str) -> str:
    return os.path.abspath(os.path.join(str(project_root or ""), ".cache", "ms-playwright"))


def _resolve_env_path(env_name: str, default_value: str) -> str:
    override = os.environ.get(env_name)
    if override:
        return os.path.abspath(override)
    return os.path.abspath(default_value)


@dataclass
class AppConfig:
    """Runtime settings for the standalone desktop application."""

    APP_HOME: str
    PROJECT_ROOT: str
    DATA_ROOT: str
    CACHE_DIR: str
    HTML_FOLDER: str
    AUTO_HTML_FOLDER: str
    LOG_DIR: str
    OUTPUT_EXCEL_DIR: str
    ARCHIVE_ROOT: str
    DOWNLOAD_CHUNK_STATE_DIR: str
    PLAYWRIGHT_BROWSERS_PATH: str
    STREAMING_DB_PATH: str
    LOG_LEVEL: str = "INFO"
    LOG_TO_FILE: bool = True
    RUNTIME_CONFIG_FILE: str = ""
    DOWNLOADER_DEFAULTS: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DOWNLOADER_DEFAULTS))
    DOWNLOADER_TASK_PAGE_SIZE: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_DOWNLOADER_TASK_PAGE_SIZE))

    @classmethod
    def from_env(
        cls,
        *,
        app_home: str | None = None,
        project_root: str | None = None,
    ) -> "AppConfig":
        resolved_project_root = os.path.abspath(
            project_root or os.path.join(os.path.dirname(__file__), "..")
        )
        resolved_app_home = os.path.abspath(app_home or _default_workspace_root())
        data_root = _resolve_env_path("PEAP_DATA_ROOT", os.path.join(resolved_app_home, "data"))
        cache_root = _resolve_env_path("PEAP_CACHE_DIR", os.path.join(resolved_app_home, "cache"))
        logs_root = _resolve_env_path("PEAP_LOG_DIR", os.path.join(resolved_app_home, "logs"))
        export_root = _resolve_env_path("PEAP_EXPORT_ROOT", os.path.join(resolved_app_home, "exports"))
        archive_root = _resolve_env_path("PEAP_ARCHIVE_ROOT", os.path.join(resolved_app_home, "submission"))
        html_root = _resolve_env_path("PEAP_MANUAL_HTML_ROOT", os.path.join(resolved_app_home, "manual"))
        auto_html_root = _resolve_env_path("PEAP_AUTO_HTML_ROOT", archive_root)
        chunk_state_root = _resolve_env_path("PEAP_DOWNLOAD_CHUNK_STATE_DIR", os.path.join(cache_root, "download_chunks"))
        playwright_browsers_path = _resolve_env_path(
            "PEAP_PLAYWRIGHT_BROWSERS_PATH",
            os.path.join(cache_root, "ms-playwright"),
        )
        streaming_db = _resolve_env_path("PEAP_STREAMING_DB_PATH", os.path.join(data_root, "streaming_ingest.sqlite3"))
        config = cls(
            APP_HOME=resolved_app_home,
            PROJECT_ROOT=resolved_project_root,
            DATA_ROOT=data_root,
            CACHE_DIR=cache_root,
            HTML_FOLDER=html_root,
            AUTO_HTML_FOLDER=auto_html_root,
            LOG_DIR=logs_root,
            OUTPUT_EXCEL_DIR=export_root,
            ARCHIVE_ROOT=archive_root,
            DOWNLOAD_CHUNK_STATE_DIR=chunk_state_root,
            PLAYWRIGHT_BROWSERS_PATH=playwright_browsers_path,
            STREAMING_DB_PATH=streaming_db,
        )
        config.migrate_legacy_layout()
        config.ensure_directories()
        return config

    def ensure_directories(self) -> "AppConfig":
        for path_value in (
            self.APP_HOME,
            self.DATA_ROOT,
            self.CACHE_DIR,
            self.HTML_FOLDER,
            self.AUTO_HTML_FOLDER,
            self.LOG_DIR,
            self.OUTPUT_EXCEL_DIR,
            self.ARCHIVE_ROOT,
            self.DOWNLOAD_CHUNK_STATE_DIR,
            self.PLAYWRIGHT_BROWSERS_PATH,
            os.path.dirname(self.STREAMING_DB_PATH),
        ):
            os.makedirs(path_value, exist_ok=True)
        return self

    def migrate_legacy_layout(self) -> "AppConfig":
        if any(
            os.environ.get(env_name)
            for env_name in (
                "PEAP_DATA_ROOT",
                "PEAP_CACHE_DIR",
                "PEAP_LOG_DIR",
                "PEAP_MANUAL_HTML_ROOT",
                "PEAP_AUTO_HTML_ROOT",
                "PEAP_EXPORT_ROOT",
                "PEAP_ARCHIVE_ROOT",
                "PEAP_STREAMING_DB_PATH",
                "PEAP_PLAYWRIGHT_BROWSERS_PATH",
            )
        ):
            return self

        legacy_app_home = _legacy_platform_app_home()
        legacy_documents_root = _legacy_documents_root()

        migrations = (
            (os.path.join(legacy_app_home, "data"), self.DATA_ROOT),
            (os.path.join(legacy_app_home, "cache"), self.CACHE_DIR),
            (os.path.join(legacy_app_home, "logs"), self.LOG_DIR),
            (os.path.join(legacy_documents_root, "submission"), self.ARCHIVE_ROOT),
            (os.path.join(legacy_documents_root, "manual"), self.HTML_FOLDER),
            (os.path.join(legacy_documents_root, "exports"), self.OUTPUT_EXCEL_DIR),
            (_legacy_project_browser_cache(self.PROJECT_ROOT), self.PLAYWRIGHT_BROWSERS_PATH),
            (os.path.join(self.DATA_ROOT, "raw", "manual"), self.HTML_FOLDER),
            (os.path.join(self.DATA_ROOT, "raw", "auto"), self.ARCHIVE_ROOT),
        )
        for source_path, target_path in migrations:
            _merge_tree(source_path, target_path)
        return self

    def is_path_within_project_root(self, path_value: str) -> bool:
        target = os.path.abspath(str(path_value or ""))
        try:
            return os.path.commonpath([self.PROJECT_ROOT, target]) == self.PROJECT_ROOT
        except ValueError:
            return False


def _merge_tree(source_path: str, target_path: str) -> None:
    source = os.path.abspath(str(source_path or ""))
    target = os.path.abspath(str(target_path or ""))
    if not source or not target or source == target or not os.path.exists(source):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.isfile(source):
        if not os.path.exists(target):
            shutil.move(source, target)
        return
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(source):
        _merge_tree(os.path.join(source, name), os.path.join(target, name))
    try:
        if not os.listdir(source):
            os.rmdir(source)
    except OSError:
        pass
