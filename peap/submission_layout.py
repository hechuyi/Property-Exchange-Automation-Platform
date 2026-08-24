"""Shared helpers for direct-to-submission snapshot layout."""

from __future__ import annotations

import filecmp
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


def submission_year_dir_name(date_text: str) -> str:
    text = str(date_text or "").strip()
    match = re.match(r"^(?P<year>\d{4})", text)
    if not match:
        return "unknown_year"
    return str(int(match.group('year')))


def is_deal_archive_root(archive_root: str) -> bool:
    basename = os.path.basename(os.path.normpath(str(archive_root or "")))
    if basename == "deal":
        return True
    scope_parts = basename.split("__")
    return len(scope_parts) >= 3 and scope_parts[1] == "deal"


def archive_task_component(*, source_id: str, record_family: str, business_id: str) -> str:
    components = []
    for field_name, value in (
        ("source_id", source_id),
        ("record_family", record_family),
        ("business_id", business_id),
    ):
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
        normalized = normalized.strip("._-")
        if not normalized:
            raise ValueError(f"archive task scope requires {field_name}")
        components.append(normalized)
    return "__".join(components)


def scoped_archive_root(
    archive_root: str,
    *,
    source_id: str,
    record_family: str,
    business_id: str,
) -> str:
    root = os.path.abspath(str(archive_root or ""))
    component = archive_task_component(
        source_id=source_id,
        record_family=record_family,
        business_id=business_id,
    )
    if os.path.basename(os.path.normpath(root)) == component:
        return root
    return os.path.join(root, component)


def next_available_submission_path(base_path: str) -> tuple[str, bool]:
    if os.path.islink(base_path):
        raise ValueError(f"submission path must not use symlinks: {base_path}")
    if not os.path.lexists(base_path):
        return base_path, False
    root, ext = os.path.splitext(base_path)
    index = 1
    while True:
        candidate = f"{root}__conflict{index}{ext}"
        if os.path.islink(candidate):
            raise ValueError(f"submission path must not use symlinks: {candidate}")
        if not os.path.lexists(candidate):
            return candidate, True
        index += 1


def validate_archive_member_path(
    path: str,
    archive_root: str,
    *,
    require_file: bool = False,
    require_directory: bool = False,
) -> str:
    """Validate one archive member against lexical and real-path escapes.

    The configured archive root itself may be a symlink (a common way to put
    data on another volume), but every existing component below that root must
    be a real directory or file.  This prevents a month directory or target
    file from redirecting archive writes outside the configured root.
    """

    root_abs = os.path.abspath(os.fspath(archive_root))
    path_abs = os.path.abspath(os.fspath(path))
    try:
        if os.path.commonpath((root_abs, path_abs)) != root_abs:
            raise ValueError(f"archive member path escapes archive root: {path_abs}")
    except ValueError as exc:
        if str(exc).startswith("archive member path escapes"):
            raise
        raise ValueError(f"archive member path is on a different volume: {path_abs}") from exc

    current = path_abs
    while True:
        if current != root_abs and os.path.lexists(current) and os.path.islink(current):
            raise ValueError(f"archive member path must not use symlinks: {path_abs}")
        if current == root_abs:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    root_real = os.path.realpath(root_abs)
    path_real = os.path.realpath(path_abs)
    try:
        if os.path.commonpath((root_real, path_real)) != root_real:
            raise ValueError(f"archive member path resolves outside archive root: {path_abs}")
    except ValueError as exc:
        if str(exc).startswith("archive member path resolves outside"):
            raise
        raise ValueError(
            f"archive member path resolves on a different volume: {path_abs}"
        ) from exc

    if require_file and not os.path.isfile(path_abs):
        raise ValueError(f"archive member is not a regular file: {path_abs}")
    if require_directory and not os.path.isdir(path_abs):
        raise ValueError(f"archive member is not a directory: {path_abs}")
    return path_abs


def _is_conflict_variant_for_target(current_path: str, target_path: str) -> bool:
    """Recognize a current snapshot already materialized under the target stem."""
    current_root, current_ext = os.path.splitext(os.path.abspath(current_path))
    target_root, target_ext = os.path.splitext(os.path.abspath(target_path))
    if os.path.normcase(current_ext) != os.path.normcase(target_ext):
        return False
    suffix = current_root[len(target_root) :]
    return current_root.startswith(f"{target_root}__conflict") and bool(
        re.fullmatch(r"__conflict[1-9][0-9]*", suffix)
    )


def _build_snapshot_target_path(
    *,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    ext: str = ".html",
) -> str:
    normalized_ext = str(ext or "").strip() or ".html"
    if not normalized_ext.startswith("."):
        normalized_ext = f".{normalized_ext}"
    file_name = safe_submission_name(
        f"{project_code}-{project_name}" if str(project_name or "").strip() else str(project_code or "")
    ) + normalized_ext
    if is_deal_archive_root(archive_root):
        date_dir = os.path.join(os.path.abspath(archive_root), submission_year_dir_name(listing_date))
    else:
        date_dir = os.path.join(os.path.abspath(archive_root), submission_month_dir_name(listing_date))
    return os.path.join(date_dir, file_name)


def resolve_submission_snapshot_target(
    *,
    archive_root: str,
    project_code: str,
    project_name: str,
    listing_date: str,
    ext: str = ".html",
    current_path: str | None = None,
    reuse_current_conflict: bool = False,
) -> tuple[str, bool]:
    root_path = os.path.abspath(os.fspath(archive_root))
    os.makedirs(root_path, exist_ok=True)
    if not os.path.isdir(root_path):
        raise ValueError(f"archive root is not a directory: {root_path}")
    target_path = _build_snapshot_target_path(
        archive_root=root_path,
        project_code=project_code,
        project_name=project_name,
        listing_date=listing_date,
        ext=ext,
    )
    target_path = validate_archive_member_path(target_path, root_path)
    date_dir = os.path.dirname(target_path)
    validate_archive_member_path(date_dir, root_path)
    os.makedirs(date_dir, exist_ok=True)
    validate_archive_member_path(date_dir, root_path, require_directory=True)
    validate_archive_member_path(target_path, root_path)
    if current_path and os.path.islink(os.path.abspath(current_path)):
        raise ValueError(f"current snapshot must not be a symlink: {current_path}")
    if current_path and os.path.normcase(os.path.abspath(current_path)) == os.path.normcase(os.path.abspath(target_path)):
        return target_path, False
    if not current_path or not os.path.isfile(current_path) or not os.path.exists(target_path):
        return target_path, False
    if reuse_current_conflict and _is_conflict_variant_for_target(current_path, target_path):
        return os.path.abspath(current_path), False
    if os.path.isfile(target_path) and filecmp.cmp(current_path, target_path, shallow=False):
        return target_path, False
    return next_available_submission_path(target_path)
