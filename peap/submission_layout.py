"""Shared helpers for direct-to-submission snapshot layout."""

from __future__ import annotations

import os
import re


def safe_submission_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "unnamed"


def submission_month_dir_name(date_text: str) -> str:
    text = str(date_text or "").strip()
    match = re.match(r"^(?P<year>\d{4})[-/年.](?P<month>\d{1,2})", text)
    if not match:
        return "unknown_month"
    return f"{int(match.group('year'))}年{int(match.group('month'))}月"


def next_available_submission_path(base_path: str) -> tuple[str, bool]:
    if not os.path.exists(base_path):
        return base_path, False
    root, ext = os.path.splitext(base_path)
    index = 1
    while True:
        candidate = f"{root}__conflict{index}{ext}"
        if not os.path.exists(candidate):
            return candidate, True
        index += 1


def resolve_submission_snapshot_target(
    *,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    ext: str = ".html",
    current_path: str | None = None,
) -> tuple[str, bool]:
    normalized_ext = str(ext or "").strip() or ".html"
    if not normalized_ext.startswith("."):
        normalized_ext = f".{normalized_ext}"
    file_name = safe_submission_name(
        f"{project_code}-{project_name}" if str(project_name or "").strip() else str(project_code or "")
    ) + normalized_ext
    month_dir = os.path.join(os.path.abspath(archive_root), submission_month_dir_name(listing_date))
    os.makedirs(month_dir, exist_ok=True)
    target_path = os.path.join(month_dir, file_name)
    if current_path and os.path.normcase(os.path.abspath(current_path)) == os.path.normcase(os.path.abspath(target_path)):
        return target_path, False
    return next_available_submission_path(target_path)
