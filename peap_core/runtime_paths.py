"""Read-only workspace path resolution shared by core maintenance tools."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .runtime import normalize_path


@dataclass(frozen=True)
class RuntimeWorkspacePaths:
    """Resolved PEAP workspace paths without directory creation or migration."""

    app_home: str
    project_root: str
    data_root: str
    cache_dir: str
    archive_root: str
    export_root: str
    streaming_db_path: str


def _workspace_root(app_home: str | None) -> str:
    return normalize_path(
        app_home
        or os.environ.get("PEAP_WORKSPACE_ROOT")
        or os.environ.get("PEAP_APP_HOME")
        or os.environ.get("PEAP_DOCUMENTS_HOME")
        or os.path.join(os.path.expanduser("~"), "Documents", "PEAP")
    )


def _env_path(name: str, default: str) -> str:
    return normalize_path(os.environ.get(name) or default)


def resolve_runtime_workspace_paths(
    *,
    app_home: str | None = None,
    project_root: str | None = None,
) -> RuntimeWorkspacePaths:
    """Resolve runtime paths using the desktop ``PEAP_*`` environment contract.

    This intentionally does not create directories, migrate legacy layouts, or
    import the desktop adapter. It is suitable for report-only core commands.
    """

    resolved_home = _workspace_root(app_home)
    resolved_project_root = normalize_path(
        project_root or os.path.join(os.path.dirname(__file__), "..")
    )
    data_root = _env_path("PEAP_DATA_ROOT", os.path.join(resolved_home, "data"))
    cache_dir = _env_path("PEAP_CACHE_DIR", os.path.join(resolved_home, "cache"))
    archive_root = _env_path("PEAP_ARCHIVE_ROOT", os.path.join(resolved_home, "archive"))
    export_root = _env_path("PEAP_EXPORT_ROOT", os.path.join(resolved_home, "exports"))
    streaming_db_path = _env_path(
        "PEAP_STREAMING_DB_PATH",
        os.path.join(data_root, "streaming_ingest.sqlite3"),
    )
    return RuntimeWorkspacePaths(
        app_home=resolved_home,
        project_root=resolved_project_root,
        data_root=data_root,
        cache_dir=cache_dir,
        archive_root=archive_root,
        export_root=export_root,
        streaming_db_path=streaming_db_path,
    )


__all__ = ["RuntimeWorkspacePaths", "resolve_runtime_workspace_paths"]
