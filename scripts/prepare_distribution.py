#!/usr/bin/env python3
"""Stage a clean, runtime-oriented source tree for a later distribution.

This command only copies files selected by packaging/distribution-manifest.json
and writes a content manifest. It intentionally does not build, install, or
package dependencies.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "packaging" / "distribution-manifest.json"
DEFAULT_OUTPUT = REPO_ROOT / "release" / "PEAP-source"
OUTPUT_MANIFEST_NAME = "DISTRIBUTION_MANIFEST.json"
_PUBLISH_JOURNAL_SCHEMA = 1
_RENAME_SWAP = 0x00000002
_EXPECTED_PROFILE_METADATA: dict[str, object] = {
    "schema_version": 1,
    "name": "PEAP",
    "profile": "runtime-source",
    "platform": "macOS",
    "offline_app": False,
    "bundled_runtimes": False,
}
_NON_MACOS_RUNTIME_SUFFIXES = frozenset({".bat", ".cmd", ".ps1"})


class DistributionError(RuntimeError):
    """Raised when the source tree cannot be staged safely."""


@dataclass(frozen=True)
class _SourceSnapshot:
    revision: str
    dirty: bool
    status: str
    files: tuple[tuple[str, int, str], ...]
    digest: str


def _load_manifest() -> dict[str, object]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DistributionError(f"cannot load {MANIFEST_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DistributionError("distribution manifest must contain a JSON object")
    for field, expected in _EXPECTED_PROFILE_METADATA.items():
        actual = payload.get(field)
        if type(actual) is not type(expected) or actual != expected:
            raise DistributionError(
                f"distribution manifest {field} must be {expected!r}, got {actual!r}"
            )
    includes = payload.get("include")
    excludes = payload.get("exclude")
    if (
        not isinstance(includes, list)
        or not includes
        or not all(isinstance(item, str) and item for item in includes)
    ):
        raise DistributionError("distribution manifest include must be a string list")
    if (
        not isinstance(excludes, list)
        or not excludes
        or not all(isinstance(item, str) and item for item in excludes)
    ):
        raise DistributionError("distribution manifest exclude must be a string list")
    if len(set(includes)) != len(includes) or len(set(excludes)) != len(excludes):
        raise DistributionError("distribution manifest patterns must not contain duplicates")
    return payload


def _git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DistributionError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _pattern_matches(path: str, pattern: str) -> bool:
    """Match a manifest pattern without allowing a directory prefix leak."""

    normalized = pattern.rstrip("/")
    if fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, normalized):
        return True
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return False


def _is_excluded(relative_path: str, patterns: Iterable[str]) -> bool:
    return any(_pattern_matches(relative_path, pattern) for pattern in patterns)


def _iter_files(manifest: dict[str, object], output_root: Path) -> list[Path]:
    includes = [str(item) for item in manifest["include"]]
    excludes = [str(item) for item in manifest["exclude"]]
    if output_root.is_relative_to(REPO_ROOT):
        output_relative = _relative(output_root)
        excludes.extend((f"{output_relative}/**", output_relative))

    selected: set[Path] = set()
    for pattern in includes:
        matches = sorted(REPO_ROOT.glob(pattern))
        if not matches:
            raise DistributionError(f"manifest include has no matches: {pattern}")
        for match in matches:
            if match.is_symlink():
                raise DistributionError(f"symlink is not allowed in distribution input: {_relative(match)}")
            if match.is_file():
                candidates = (match,)
            elif match.is_dir():
                candidates = (item for item in match.rglob("*") if item.is_file())
            else:
                continue
            for candidate in candidates:
                if candidate.is_symlink():
                    raise DistributionError(
                        f"symlink is not allowed in distribution input: {_relative(candidate)}"
                    )
                relative_path = _relative(candidate)
                if not _is_excluded(relative_path, excludes):
                    if candidate.suffix.lower() in _NON_MACOS_RUNTIME_SUFFIXES:
                        raise DistributionError(
                            f"non-macOS runtime input is not allowed: {relative_path}"
                        )
                    selected.add(candidate)

    if not selected:
        raise DistributionError("distribution manifest selected no files")
    return sorted(selected, key=_relative)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_state() -> tuple[str, bool]:
    revision = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=all"))
    return revision, dirty


def _source_status() -> str:
    return _git("status", "--porcelain=v1", "--untracked-files=all")


def _source_snapshot(manifest: dict[str, object], output_root: Path) -> _SourceSnapshot:
    revision, dirty = _source_state()
    status = _source_status()
    files = _iter_files(manifest, output_root)
    entries: list[tuple[str, int, str]] = []
    digest = hashlib.sha256()
    for source in files:
        relative_path = _relative(source)
        size = source.stat().st_size
        file_digest = _sha256(source)
        entries.append((relative_path, size, file_digest))
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return _SourceSnapshot(
        revision=revision,
        dirty=dirty,
        status=status,
        files=tuple(entries),
        digest=digest.hexdigest(),
    )


def _assert_source_unchanged(before: _SourceSnapshot, after: _SourceSnapshot) -> None:
    if before != after:
        raise DistributionError(
            "source tree changed while staging; refusing to publish a mixed revision"
        )


@contextmanager
def _output_lock(output: Path):
    """Hold an exclusive same-parent lock across validation and replacement."""

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - distribution profile is macOS-only
        raise DistributionError("exclusive output locking requires fcntl on macOS") from exc
    lock_path = output.parent / f".{output.name}.distribution.lock"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise DistributionError(f"cannot create distribution output lock: {lock_path}") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise DistributionError(f"cannot acquire distribution output lock: {lock_path}") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _publish_journal_path(output: Path) -> Path:
    return output.parent / f".{output.name}.distribution-publish.json"


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DistributionError(f"cannot inspect publish path: {path}: {exc}") from exc
    return int(metadata.st_dev), int(metadata.st_ino)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise DistributionError(f"cannot open publish directory for sync: {path}: {exc}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise DistributionError(f"cannot sync publish directory: {path}: {exc}") from exc
    finally:
        os.close(descriptor)


def _write_publish_journal(path: Path, payload: dict[str, object]) -> None:
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # os.rename is used for the journal itself so tests and callers that
        # inject failures into the two publication renames do not mistake a
        # journal update for a content swap.
        os.rename(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _remove_publish_journal(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DistributionError(f"cannot remove publish journal: {path}: {exc}") from exc
    _fsync_directory(path.parent)


def _journal_payload(
    *,
    output: Path,
    staging: Path,
    backup: Path | None,
    method: str,
    phase: str,
) -> dict[str, object]:
    return {
        "schema_version": _PUBLISH_JOURNAL_SCHEMA,
        "output": str(output),
        "staging": str(staging),
        "backup": None if backup is None else str(backup),
        "method": method,
        "phase": phase,
        "output_before": _path_identity(output),
        "staging_before": _path_identity(staging),
    }


def _validate_journal_payload(path: Path, output: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DistributionError(f"cannot read publish journal: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _PUBLISH_JOURNAL_SCHEMA:
        raise DistributionError(f"invalid publish journal: {path}")
    if payload.get("output") != str(output):
        raise DistributionError(f"publish journal targets a different output: {path}")
    for field in ("staging", "backup", "method", "phase"):
        if field not in payload:
            raise DistributionError(f"publish journal is missing {field}: {path}")
    staging_text = payload.get("staging")
    if not isinstance(staging_text, str) or not staging_text:
        raise DistributionError(f"publish journal has an invalid staging path: {path}")
    staging = Path(staging_text)
    if staging.parent != output.parent:
        raise DistributionError(f"publish journal staging path escapes output parent: {path}")
    backup_text = payload.get("backup")
    if backup_text is not None:
        if not isinstance(backup_text, str) or not backup_text:
            raise DistributionError(f"publish journal has an invalid backup path: {path}")
        backup = Path(backup_text)
        if backup.parent != output.parent:
            raise DistributionError(f"publish journal backup path escapes output parent: {path}")
    if payload.get("method") not in {"exchange", "backup", "single"}:
        raise DistributionError(f"publish journal has an invalid method: {path}")
    if payload.get("phase") not in {"prepared", "old_moved", "installed", "exchanged"}:
        raise DistributionError(f"publish journal has an invalid phase: {path}")
    return payload


def _remove_tree_if_identity(path: Path, expected: tuple[int, int] | None) -> None:
    if expected is None:
        if path.exists() or path.is_symlink():
            raise DistributionError(f"refusing to remove unexpected publish path: {path}")
        return
    current = _path_identity(path)
    if current != expected:
        raise DistributionError(f"publish path changed before cleanup: {path}")
    if path.is_symlink() or not path.is_dir():
        raise DistributionError(f"publish path is not a directory: {path}")
    shutil.rmtree(path)


def _atomic_directory_exchange(source: Path, destination: Path) -> None:
    """Exchange two directories without an observable missing-output window."""

    function = None
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renamex_np", None)
        if function is not None:
            function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            result = function(
                os.fsencode(source),
                os.fsencode(destination),
                _RENAME_SWAP,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), str(source), str(destination))
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renameat2", None)
        if function is not None:
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            result = function(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                _RENAME_SWAP,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), str(source), str(destination))
    raise OSError(errno.ENOTSUP, "atomic directory exchange is unavailable")


def _atomic_exchange_supported() -> bool:
    if sys.platform == "darwin":
        return hasattr(ctypes.CDLL(None, use_errno=True), "renamex_np")
    if sys.platform.startswith("linux"):
        return hasattr(ctypes.CDLL(None, use_errno=True), "renameat2")
    return False


def _recover_publish_journal(output: Path) -> None:
    journal = _publish_journal_path(output)
    if not journal.exists():
        return
    payload = _validate_journal_payload(journal, output)
    staging = Path(str(payload["staging"]))
    backup_text = payload.get("backup")
    backup = Path(str(backup_text)) if isinstance(backup_text, str) else None
    method = str(payload["method"])
    output_before = tuple(payload.get("output_before") or ()) or None
    staging_before = tuple(payload.get("staging_before") or ()) or None

    if method == "exchange":
        output_identity = _path_identity(output)
        staging_identity = _path_identity(staging)
        exchanged = output_identity == staging_before and (
            staging_identity is None or output_before is None or staging_identity == output_before
        )
        untouched = output_identity == output_before and staging_identity == staging_before
        if exchanged:
            if staging_identity is not None:
                _remove_tree_if_identity(staging, output_before)
        elif untouched:
            _remove_tree_if_identity(staging, staging_before)
        else:
            raise DistributionError(f"publish journal has an ambiguous exchange state: {journal}")
        _remove_publish_journal(journal)
        return

    if method == "single":
        output_identity = _path_identity(output)
        if output_identity == staging_before and _path_identity(staging) is None:
            _remove_publish_journal(journal)
            return
        if output_identity is None and _path_identity(staging) == staging_before:
            _remove_tree_if_identity(staging, staging_before)
            _remove_publish_journal(journal)
            return
        raise DistributionError(f"publish journal has an ambiguous single-rename state: {journal}")

    if backup is None:
        raise DistributionError(f"publish journal backup path is required: {journal}")
    output_identity = _path_identity(output)
    staging_identity = _path_identity(staging)
    backup_identity = _path_identity(backup)
    installed = output_identity == staging_before and staging_identity is None
    old_moved = output_identity is None and backup_identity == output_before and staging_identity == staging_before
    untouched = output_identity == output_before and backup_identity is None and staging_identity == staging_before
    if installed:
        if backup_identity is not None:
            _remove_tree_if_identity(backup, output_before)
        _remove_publish_journal(journal)
        return
    if old_moved:
        os.replace(backup, output)
        _fsync_directory(output.parent)
        _remove_tree_if_identity(staging, staging_before)
        _remove_publish_journal(journal)
        return
    if untouched:
        _remove_tree_if_identity(staging, staging_before)
        _remove_publish_journal(journal)
        return
    raise DistributionError(f"publish journal has an ambiguous backup state: {journal}")


def _atomic_publish(
    *,
    staging: Path,
    output: Path,
    replace_existing: bool,
    _force_backup: bool = False,
) -> None:
    staging = staging.absolute()
    output = output.absolute()
    _recover_publish_journal(output)
    journal = _publish_journal_path(output)
    backup: Path | None = None
    method = "exchange" if replace_existing and not _force_backup and _atomic_exchange_supported() else (
        "backup" if replace_existing else "single"
    )

    if method == "exchange":
        payload = _journal_payload(
            output=output,
            staging=staging,
            backup=None,
            method=method,
            phase="prepared",
        )
        _write_publish_journal(journal, payload)
        exchanged = False
        try:
            try:
                _atomic_directory_exchange(staging, output)
            except OSError as exc:
                if exc.errno not in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
                _remove_publish_journal(journal)
                return _atomic_publish(
                    staging=staging,
                    output=output,
                    replace_existing=True,
                    _force_backup=True,
                )
            exchanged = True
            payload["phase"] = "exchanged"
            _write_publish_journal(journal, payload)
            _remove_tree_if_identity(staging, tuple(payload.get("output_before") or ()) or None)
            _remove_publish_journal(journal)
            return
        except Exception:
            if exchanged:
                # The output is already the complete new tree. Leave the
                # journal and old tree for deterministic recovery on the next
                # invocation instead of creating a missing-output window.
                raise
            try:
                _remove_publish_journal(journal)
            except Exception:
                pass
            raise

    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}" if replace_existing else None
    payload = _journal_payload(
        output=output,
        staging=staging,
        backup=backup,
        method=method,
        phase="prepared",
    )
    _write_publish_journal(journal, payload)
    old_moved = False
    installed = False
    try:
        if replace_existing:
            assert backup is not None
            os.replace(output, backup)
            old_moved = True
            payload["phase"] = "old_moved"
            _write_publish_journal(journal, payload)
        os.replace(staging, output)
        installed = True
        payload["phase"] = "installed"
        _write_publish_journal(journal, payload)
        if backup is not None and backup.exists():
            _remove_tree_if_identity(backup, tuple(payload.get("output_before") or ()) or None)
        _remove_publish_journal(journal)
    except Exception as publish_exc:
        if installed:
            # The complete new tree is visible. Keep the journal so a future
            # invocation can finish cleanup without ever hiding output.
            raise publish_exc
        if old_moved and backup is not None and backup.exists() and not output.exists():
            try:
                os.replace(backup, output)
                _fsync_directory(output.parent)
                _remove_tree_if_identity(staging, tuple(payload.get("staging_before") or ()) or None)
                _remove_publish_journal(journal)
            except Exception as restore_exc:
                raise ExceptionGroup(
                    "distribution replacement failed and rollback failed",
                    [publish_exc, restore_exc],
                ) from None
        else:
            try:
                _remove_tree_if_identity(staging, tuple(payload.get("staging_before") or ()) or None)
                _remove_publish_journal(journal)
            except Exception as cleanup_exc:
                raise ExceptionGroup(
                    "distribution publication failed and cleanup failed",
                    [publish_exc, cleanup_exc],
                ) from None
        raise


def _copy_files(files: list[Path], output_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for source in files:
        relative_path = Path(_relative(source))
        destination = output_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        entries.append(
            {
                "path": relative_path.as_posix(),
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )
    return entries


def _write_output_manifest(
    output_root: Path,
    source_manifest: dict[str, object],
    *,
    revision: str,
    dirty: bool,
    files: list[dict[str, object]],
) -> None:
    payload = {
        "schema_version": 1,
        "name": source_manifest.get("name", "PEAP"),
        "profile": source_manifest.get("profile", "runtime-source"),
        "source_revision": revision,
        "source_dirty": dirty,
        "files": files,
    }
    (output_root / OUTPUT_MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validated_owned_output(
    output_root: Path,
    source_manifest: dict[str, object],
) -> None:
    """Refuse to recursively delete anything except an intact PEAP staging tree."""

    marker = output_root / OUTPUT_MANIFEST_NAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DistributionError(
            f"refusing to replace unrecognized output directory: {output_root}"
        ) from exc
    if not isinstance(payload, dict):
        raise DistributionError(f"invalid output manifest in {output_root}")
    if (
        payload.get("schema_version") != 1
        or payload.get("name") != source_manifest.get("name", "PEAP")
        or payload.get("profile") != source_manifest.get("profile", "runtime-source")
    ):
        raise DistributionError(f"output manifest does not identify this distribution: {marker}")

    raw_entries = payload.get("files")
    if not isinstance(raw_entries, list):
        raise DistributionError(f"output manifest has no valid file inventory: {marker}")
    expected: dict[str, tuple[int, str]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise DistributionError(f"output manifest has an invalid file entry: {marker}")
        path_text = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(path_text, str)
            or not path_text
            or Path(path_text).is_absolute()
            or ".." in Path(path_text).parts
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or path_text in expected
        ):
            raise DistributionError(f"output manifest has an unsafe file entry: {marker}")
        expected[path_text] = (size, digest)

    actual: dict[str, Path] = {}
    actual_directories: set[str] = set()
    for candidate in output_root.rglob("*"):
        if candidate.is_symlink():
            raise DistributionError(f"refusing to replace output containing a symlink: {candidate}")
        if candidate.is_dir():
            actual_directories.add(candidate.relative_to(output_root).as_posix())
            continue
        if not candidate.is_file() or candidate == marker:
            continue
        actual[candidate.relative_to(output_root).as_posix()] = candidate
    expected_directories = {
        parent.as_posix()
        for relative_path in expected
        for parent in Path(relative_path).parents
        if parent != Path(".")
    }
    if set(actual) != set(expected) or actual_directories != expected_directories:
        raise DistributionError(
            f"refusing to replace output whose files differ from its manifest: {output_root}"
        )
    for relative_path, candidate in actual.items():
        size, digest = expected[relative_path]
        if candidate.stat().st_size != size or _sha256(candidate) != digest:
            raise DistributionError(
                f"refusing to replace modified distribution output: {candidate}"
            )


def stage_distribution(*, output: Path, allow_dirty: bool, force: bool, dry_run: bool) -> int:
    output = output.expanduser().resolve()
    if output == REPO_ROOT or REPO_ROOT.is_relative_to(output):
        raise DistributionError("output must be a staging directory, not the repository or one of its parents")
    manifest = _load_manifest()
    with _output_lock(output):
        _recover_publish_journal(output)
        before = _source_snapshot(manifest, output)
        if before.dirty and not allow_dirty:
            raise DistributionError(
                "worktree has uncommitted or untracked changes; review/commit them first "
                "or pass --allow-dirty for a local preview"
            )

        if dry_run:
            print(f"Would stage {len(before.files)} files from {before.revision[:12]} into {output}")
            if before.dirty:
                print("Source worktree: dirty (allowed by --allow-dirty)")
            return 0

        replace_existing = output.exists()
        if output.exists():
            if not force:
                raise DistributionError(f"output already exists: {output} (use --force to replace it)")
            if output.is_file() or output.is_symlink():
                raise DistributionError(f"output is not a directory: {output}")
            _validated_owned_output(output, manifest)

        temporary_output = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
        )
        try:
            files = [REPO_ROOT / relative_path for relative_path, _size, _digest in before.files]
            entries = _copy_files(files, temporary_output)
            _write_output_manifest(
                temporary_output,
                manifest,
                revision=before.revision,
                dirty=before.dirty,
                files=entries,
            )
            after = _source_snapshot(manifest, output)
            _assert_source_unchanged(before, after)
            if replace_existing:
                _validated_owned_output(output, manifest)
            _atomic_publish(
                staging=temporary_output,
                output=output,
                replace_existing=replace_existing,
            )
        except Exception:
            if temporary_output.exists():
                shutil.rmtree(temporary_output, ignore_errors=True)
            raise
    print(f"Staged {len(entries)} files into {output}")
    print(f"Source revision: {before.revision}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"staging directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow local staging from an uncommitted worktree",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing staging directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report the selected files without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return stage_distribution(
            output=args.output,
            allow_dirty=bool(args.allow_dirty),
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )
    except DistributionError as exc:
        print(f"Distribution staging failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
