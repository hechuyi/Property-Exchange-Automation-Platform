"""Path resolution helper for maintenance/cleanup scripts.

Defaults reuse desktop AppConfig as the single source of truth — same
workspace_root / per-path env var precedence as the desktop runtime
(PEAP_WORKSPACE_ROOT / PEAP_APP_HOME / PEAP_DOCUMENTS_HOME, default
~/Documents/PEAP). Path resolution must NOT have filesystem side effects,
so AppConfig is constructed with ensure_dirs=False and migrate_legacy=False;
desktop startup remains responsible for directory creation and legacy
migration. CLI values still win when supplied.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Optional

from desktop_backend.app_config import AppConfig


def _protected_workspace_roots() -> tuple[Path, ...]:
    """Return safety roots without embedding one developer's absolute path."""

    candidates = [Path.home() / "Documents" / "PEAP"]
    # ``HOME`` is routinely overridden by isolated test runners and launchers.
    # Resolve the OS account home independently so a temporary HOME cannot make
    # the real workspace look like a safe target.
    try:
        import pwd

        account_home = str(pwd.getpwuid(os.getuid()).pw_dir or "").strip()
    except (ImportError, KeyError, AttributeError, OSError):
        account_home = ""
    if account_home:
        candidates.append(Path(account_home) / "Documents" / "PEAP")
    configured = os.environ.get("PEAP_PROTECTED_WORKSPACE_ROOTS", "")
    candidates.extend(Path(item) for item in configured.split(os.pathsep) if item.strip())

    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        normalized = _normalize_path_without_filesystem_probe(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(normalized)
    return tuple(roots)

_FIELD_RESOLVERS = {
    "archive_root": lambda c: c.ARCHIVE_ROOT,
    "db": lambda c: c.STREAMING_DB_PATH,
    "app_home": lambda c: c.APP_HOME,
}


def _normalize_path_without_filesystem_probe(path_value: str | Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(path_value or "")))
    return Path(os.path.normcase(os.path.realpath(os.path.abspath(os.path.normpath(expanded)))))


def _is_forbidden_real_peap_path(path_value: str | Path) -> bool:
    resolved = _normalize_path_without_filesystem_probe(path_value)
    for forbidden in _protected_workspace_roots():
        if resolved == forbidden:
            return True
        try:
            if forbidden in resolved.parents:
                return True
        except RuntimeError:
            return False
    return False


def is_forbidden_real_peap_path(path_value: str | Path) -> bool:
    """Return whether a path falls under a protected real PEAP workspace."""

    if not str(path_value or "").strip():
        return False
    return _is_forbidden_real_peap_path(path_value)


def reject_forbidden_real_peap_path(name: str, path_value: str | Path) -> None:
    if _is_forbidden_real_peap_path(path_value):
        raise ValueError(f"refusing to use real PEAP workspace path for {name}: {path_value}")


def _reject_forbidden_real_peap_paths(args: argparse.Namespace) -> None:
    for name in _FIELD_RESOLVERS:
        if not hasattr(args, name):
            continue
        path_value = getattr(args, name)
        if path_value in (None, ""):
            continue
        reject_forbidden_real_peap_path(name, path_value)


def resolve_cleanup_paths(
    args: argparse.Namespace,
    *,
    config: Optional[AppConfig] = None,
    report_only: bool = True,
) -> argparse.Namespace:
    """Fill argparse path attributes from AppConfig defaults when caller passed None.

    Mutates and returns args. AppConfig.from_env() is invoked without
    side effects (ensure_dirs=False, migrate_legacy=False) so calling
    this helper never creates directories or moves legacy files. Scripts
    that need those side effects should request them explicitly when they
    actually do work, not as part of path resolution.  Report-only callers may
    inspect the real default workspace; a caller that can write/delete must
    pass ``report_only=False`` to retain the protected-root rejection.
    """
    needs_config = any(
        hasattr(args, name) and getattr(args, name) in (None, "")
        for name in _FIELD_RESOLVERS
    )
    if not needs_config:
        if not report_only:
            _reject_forbidden_real_peap_paths(args)
        return args
    resolved = config or AppConfig.from_env(ensure_dirs=False, migrate_legacy=False)
    for name, fn in _FIELD_RESOLVERS.items():
        if hasattr(args, name) and getattr(args, name) in (None, ""):
            setattr(args, name, fn(resolved))
    if not report_only:
        _reject_forbidden_real_peap_paths(args)
    return args


def open_readonly_sqlite_db(db_path: str | Path) -> sqlite3.Connection:
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"database not found: {resolved}")
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
