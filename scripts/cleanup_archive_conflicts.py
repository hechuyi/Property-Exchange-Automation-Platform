#!/usr/bin/env python3
"""Report legacy ``__conflictN`` archive snapshots without mutating files.

This script is intentionally report-only. Conflict recovery must be handled by
the journaled archive-recovery path, because selecting a winner by filesystem
mtime or deleting duplicate snapshots loses evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from scripts._paths import resolve_cleanup_paths

CONFLICT_SUFFIX_RE = re.compile(r"__conflict\d+(?=\.[^.]+$)", re.IGNORECASE)
HTML_EXTENSIONS = (".html", ".htm", ".mhtml")


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _uninspectable_path(error: OSError, fallback_path: Path) -> dict[str, Any]:
    raw_path = getattr(error, "filename", None) or fallback_path
    message = error.strerror or next((str(arg) for arg in error.args if arg), str(error))
    return {
        "path": str(Path(raw_path).resolve()),
        "error": {
            "type": error.__class__.__name__,
            "message": message,
        },
    }


def _iter_snapshot_files(archive_root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    matches: list[Path] = []
    uninspectable_paths: list[dict[str, Any]] = []

    def record_uninspectable(error: OSError) -> None:
        uninspectable_paths.append(_uninspectable_path(error, archive_root))

    for root, dir_names, file_names in os.walk(archive_root, onerror=record_uninspectable):
        dir_names.sort()
        for file_name in sorted(file_names):
            if file_name.lower().endswith(HTML_EXTENSIONS):
                matches.append(Path(root, file_name).resolve())
    return matches, uninspectable_paths


def _canonical_path(path: Path) -> Path:
    return Path(CONFLICT_SUFFIX_RE.sub("", str(path))).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_evidence(path: Path) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "size": int(stat.st_size) if stat is not None else 0,
        "content_sha256": _sha256(path) if exists and path.is_file() else "",
    }


def plan_archive_conflicts(archive_root: str | os.PathLike[str]) -> dict[str, Any]:
    root = Path(archive_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"archive root not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"archive root is not a directory: {root}")
    groups: dict[Path, list[Path]] = {}
    snapshot_files, uninspectable_paths = _iter_snapshot_files(root)
    for path in snapshot_files:
        groups.setdefault(_canonical_path(path), []).append(path)

    actions: list[dict[str, Any]] = []
    for canonical, paths in sorted(groups.items(), key=lambda item: str(item[0])):
        conflict_paths = [path for path in paths if path != canonical]
        if not conflict_paths:
            continue
        evidence = [_snapshot_evidence(path) for path in sorted(paths, key=lambda item: str(item))]
        unique_hashes = sorted({str(item.get("content_sha256") or "") for item in evidence if item.get("content_sha256")})
        actions.append(
            {
                "canonical_path": str(canonical),
                "classification": "archive_conflict_requires_journaled_recovery",
                "recommended_action": "controlled_operations_review_required",
                "group_paths": evidence,
                "path_count": len(evidence),
                "unique_content_sha256": unique_hashes,
            }
        )
    return {
        "mode": "report_only",
        "destructive": False,
        "generated_at": _timestamp(),
        "archive_root": str(root),
        "conflict_group_count": len(actions),
        "uninspectable_path_count": len(uninspectable_paths),
        "uninspectable_paths": uninspectable_paths,
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", default=None, help="default: AppConfig.ARCHIVE_ROOT")
    parser.add_argument("--db", default=None, help="accepted for legacy callers; never modified")
    args = parser.parse_args()
    args = resolve_cleanup_paths(args)
    try:
        manifest = plan_archive_conflicts(args.archive_root)
    except OSError as exc:
        raise SystemExit(str(exc)) from exc
    manifest["db_path"] = str(Path(args.db).resolve()) if getattr(args, "db", None) else ""
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
