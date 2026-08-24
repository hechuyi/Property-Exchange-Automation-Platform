#!/usr/bin/env python3
"""Validate the resources required by the PEAP offline macOS application.

The checked-in launcher is only a small shell wrapper.  This validator is the
single fail-closed contract used by the offline-app builder and by operators
checking a copied bundle.  It deliberately does not inspect user workspace
data; only resources inside the application bundle are considered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


class OfflineAppValidationError(RuntimeError):
    """Raised when an offline application is missing a required resource."""


@dataclass(frozen=True)
class BundleReport:
    """Structured validation result suitable for CLI and build logs."""

    bundle: str
    platform: str
    architecture: str
    project_files: int
    python_executable: str
    node_executable: str
    browser_directories: tuple[str, ...]
    executed_runtime_checks: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle": self.bundle,
            "platform": self.platform,
            "architecture": self.architecture,
            "project_files": self.project_files,
            "python_executable": self.python_executable,
            "node_executable": self.node_executable,
            "browser_directories": list(self.browser_directories),
            "executed_runtime_checks": self.executed_runtime_checks,
        }


REQUIRED_PROJECT_FILES = (
    "DISTRIBUTION_MANIFEST.json",
    "start.sh",
    "pyproject.toml",
    "uv.lock",
    "desktop_backend/requirements.lock.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/node_modules/.bin/vite",
    "scripts/_paths.py",
)
REQUIRED_PROJECT_INVENTORY_FILES = frozenset(
    {
        "start.sh",
        "pyproject.toml",
        "uv.lock",
        "config.py",
        "assets/excel_output_schema.json",
        "assets/runtime_config.json",
        "assets/runtime_config.template.json",
        "desktop_backend/app_backend.py",
        "desktop_backend/app_config.py",
        "desktop_backend/requirements.lock.txt",
        "desktop_backend/services/execution_service.py",
        "frontend/index.html",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/vite.config.js",
        "peap/cli.py",
        "peap/download_runner.py",
        "peap/streaming_store.py",
        "peap_core/runtime.py",
        "peap_core/source_catalog.py",
        "peap_parsers/parser_registry.py",
        "scripts/_paths.py",
    }
)
REQUIRED_LAUNCHER_FILES = (
    "Contents/MacOS/PEAPLauncher",
    "Contents/Resources/run.sh",
    "Contents/Resources/initialize.sh",
    "Contents/Info.plist",
)
BUILD_MANIFEST = "Contents/Resources/OFFLINE_APP_MANIFEST.json"
RELEASE_ID_MAX_LENGTH = 80
RELEASE_ID_PATTERN = re.compile(
    rf"[A-Za-z0-9](?:[A-Za-z0-9._-]{{0,{RELEASE_ID_MAX_LENGTH - 2}}}[A-Za-z0-9])?\Z"
)


def _fail(message: str) -> None:
    raise OfflineAppValidationError(message)


def validate_release_id(value: object, *, label: str = "release_id") -> str:
    """Return a path-safe, stable release identifier or fail closed."""

    if not isinstance(value, str) or not RELEASE_ID_PATTERN.fullmatch(value):
        _fail(
            f"{label} must be 1-{RELEASE_ID_MAX_LENGTH} ASCII characters, start and end "
            "with an alphanumeric character, and contain only letters, digits, '.', '_' or '-'"
        )
    return value


def _is_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_IXUSR) and path.is_file()


def _assert_inside(root: Path, path: Path, *, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        _fail(f"{label} escapes the application bundle: {path}")
        raise AssertionError from exc


def _assert_no_symlink_components(root: Path, path: Path, *, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        _fail(f"{label} is outside its declared root")
        raise AssertionError from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            _fail(f"cannot inspect {label}: {exc}")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"manifest-listed file must not use symlinks: {label}")


def _assert_tree_symlinks_contained(tree_root: Path, *, label: str) -> None:
    resolved_root = tree_root.resolve()
    for candidate in tree_root.rglob("*"):
        if not candidate.is_symlink():
            continue
        relative = candidate.relative_to(tree_root).as_posix()
        try:
            target = candidate.resolve(strict=True)
            target.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError) as exc:
            _fail(f"{label} symlink is broken or escapes its root: {relative}")
            raise AssertionError from exc


def _require_file(root: Path, relative: str, *, executable: bool = False) -> Path:
    path = root / relative
    _assert_inside(root, path, label=relative)
    if not path.is_file():
        _fail(f"missing required file: {relative}")
    if executable and not _is_executable(path):
        _fail(f"required file is not executable: {relative}")
    return path


def _require_directory(root: Path, relative: str) -> Path:
    path = root / relative
    _assert_inside(root, path, label=relative)
    if path.is_symlink() or not path.is_dir():
        _fail(f"missing required directory: {relative}")
    return path


def _runtime_manifest(runtime_root: Path) -> dict[str, object]:
    manifest_path = runtime_root / "runtime-manifest.json"
    if not manifest_path.is_file():
        _fail("missing required file: Contents/Resources/Runtime/arm64/runtime-manifest.json")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"runtime manifest is unreadable: {exc}")
    if not isinstance(payload, dict):
        _fail("runtime manifest must be a JSON object")
    if payload.get("schema_version") != 1:
        _fail("runtime manifest schema_version must be 1")
    if payload.get("architecture") != "arm64":
        _fail("runtime manifest architecture must be arm64")
    if payload.get("platform") != "macOS":
        _fail("runtime manifest platform must be macOS")
    return payload


def _build_manifest(root: Path) -> dict[str, object]:
    manifest_path = root / BUILD_MANIFEST
    if not manifest_path.is_file():
        _fail(f"missing required file: {BUILD_MANIFEST}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"offline app manifest is unreadable: {exc}")
    if not isinstance(payload, dict):
        _fail("offline app manifest must be a JSON object")
    if payload.get("schema_version") != 1 or payload.get("profile") != "offline-app":
        _fail("offline app manifest must identify the offline-app schema")
    if payload.get("platform") != "macOS" or payload.get("architecture") != "arm64":
        _fail("offline app manifest must target macOS arm64")
    minimum_macos = payload.get("minimum_macos")
    if minimum_macos is not None:
        if not isinstance(minimum_macos, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", minimum_macos):
            _fail("offline app manifest minimum_macos must be a dotted version string")
    validate_release_id(payload.get("release_id"), label="offline app manifest release_id")
    return payload


def _read_release_id(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"release identifier is unreadable: {exc}")
    if value.endswith("\n"):
        value = value[:-1]
    return validate_release_id(value, label="release-id.txt")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(f"cannot hash project file {path}: {exc}")
    return digest.hexdigest()


def _safe_inventory_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("project manifest contains an unsafe file path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("project manifest contains an unsafe file path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"project manifest contains an unsafe file path: {value!r}")
    if value == "DISTRIBUTION_MANIFEST.json" or value.startswith("frontend/node_modules/"):
        _fail(f"project manifest contains a generated file path: {value!r}")
    return value


def _project_manifest(project_root: Path) -> tuple[dict[str, object], dict[str, tuple[int, str]]]:
    manifest_path = _require_file(project_root, "DISTRIBUTION_MANIFEST.json")
    _assert_no_symlink_components(project_root, manifest_path, label="DISTRIBUTION_MANIFEST.json")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"project manifest is unreadable: {exc}")
    if not isinstance(payload, dict):
        _fail("project manifest must be a JSON object")
    if (
        payload.get("schema_version") != 1
        or payload.get("name") != "PEAP"
        or payload.get("profile") != "runtime-source"
    ):
        _fail("project manifest must identify the PEAP runtime-source schema")
    if not isinstance(payload.get("source_revision"), str) or not payload["source_revision"]:
        _fail("project manifest source_revision must be a non-empty string")
    if not isinstance(payload.get("source_dirty"), bool):
        _fail("project manifest source_dirty must be a boolean")
    raw_entries = payload.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        _fail("project manifest must contain a non-empty file inventory")

    expected: dict[str, tuple[int, str]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            _fail("project manifest contains an invalid file entry")
        relative = _safe_inventory_path(entry.get("path"))
        size = entry.get("size")
        digest = entry.get("sha256")
        if relative in expected:
            _fail(f"project manifest contains a duplicate file entry: {relative}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail(f"project manifest contains an invalid size for {relative}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or digest != digest.lower()
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _fail(f"project manifest contains an invalid SHA-256 for {relative}")
        expected[relative] = (size, digest)

    missing_inventory = sorted(REQUIRED_PROJECT_INVENTORY_FILES - set(expected))
    if missing_inventory:
        _fail("project manifest omits required files: " + ", ".join(missing_inventory))

    for relative, (expected_size, expected_digest) in expected.items():
        candidate = project_root / relative
        _assert_no_symlink_components(project_root, candidate, label=relative)
        if not candidate.is_file():
            _fail(f"project manifest file is missing: {relative}")
        if candidate.stat().st_size != expected_size:
            _fail(f"project manifest size mismatch: {relative}")
        if _sha256(candidate) != expected_digest:
            _fail(f"project manifest SHA-256 mismatch: {relative}")

    actual: set[str] = set()
    for candidate in project_root.rglob("*"):
        relative = candidate.relative_to(project_root).as_posix()
        if candidate.is_symlink():
            if not relative.startswith("frontend/node_modules/"):
                _fail(f"project source contains an untracked symlink: {relative}")
            continue
        if not candidate.is_file():
            continue
        if relative == "DISTRIBUTION_MANIFEST.json" or relative.startswith("frontend/node_modules/"):
            continue
        actual.add(relative)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing[:5]))
        if extra:
            detail.append("untracked=" + ",".join(extra[:5]))
        _fail("project files differ from DISTRIBUTION_MANIFEST.json: " + " ".join(detail))
    return payload, expected


def validate_project_source(
    project_root: str | os.PathLike[str],
) -> tuple[dict[str, object], int]:
    """Validate a runtime-source staging tree before it is embedded or run."""

    root_path = Path(project_root).expanduser()
    if root_path.is_symlink() or not root_path.is_dir():
        _fail(f"project staging must be a real directory: {project_root}")
    root = root_path.resolve()
    manifest, inventory = _project_manifest(root)
    _assert_tree_symlinks_contained(root, label="project")
    for relative in REQUIRED_PROJECT_FILES:
        if relative.startswith("frontend/node_modules/"):
            continue
        _require_file(
            root,
            relative,
            executable=relative.endswith("start.sh"),
        )
    return manifest, len(inventory)


def _required_version(manifest: dict[str, object], field: str) -> str:
    value = str(manifest.get(field) or "").strip()
    if not value:
        _fail(f"runtime manifest {field!r} must be a non-empty string")
    return value


def _browser_directories(browser_root: Path) -> tuple[str, ...]:
    directories = tuple(sorted(path.name for path in browser_root.iterdir() if path.is_dir()))
    chromium = tuple(path for path in directories if path.startswith("chromium-"))
    if not chromium:
        _fail("Playwright runtime contains no chromium-* directory")
    if not any(path.startswith("chromium_headless_shell-") for path in directories):
        _fail("Playwright runtime contains no chromium_headless_shell-* directory")
    return directories


def _run_checked(command: Iterable[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(
        tuple(command),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        suffix = detail[-1] if detail else f"exit={completed.returncode}"
        _fail(f"runtime check failed ({' '.join(command)}): {suffix}")


def validate_bundle(
    bundle: str | os.PathLike[str],
    *,
    execute_runtime_checks: bool = False,
    require_arm64_host: bool = False,
) -> BundleReport:
    """Validate an offline app and return a machine-readable report.

    ``execute_runtime_checks`` is optional so CI can validate a synthetic
    bundle without executing fake binaries.  Production builds always enable
    it.  ``require_arm64_host`` is used by the macOS builder; plain inspection
    remains portable for tests and diagnostics.
    """

    root = Path(bundle).expanduser().resolve()
    if not root.is_dir() or root.suffix != ".app":
        _fail(f"bundle must be an existing .app directory: {bundle}")

    for relative in REQUIRED_LAUNCHER_FILES:
        _require_file(
            root,
            relative,
            executable=relative.endswith("PEAPLauncher") or relative.endswith(".sh"),
        )
    release_id_path = _require_file(root, "Contents/Resources/release-id.txt")
    release_id = _read_release_id(release_id_path)
    build_manifest = _build_manifest(root)
    if build_manifest.get("release_id") != release_id:
        _fail("release-id.txt does not match the offline app manifest")

    project_root = _require_directory(root, "Contents/Resources/Project")
    project_manifest, project_files = validate_project_source(project_root)
    _require_file(project_root, "frontend/node_modules/.bin/vite", executable=True)

    runtime_root = _require_directory(root, "Contents/Resources/Runtime/arm64")
    _assert_tree_symlinks_contained(runtime_root, label="runtime")
    python_path = _require_file(runtime_root, "python/bin/python3.11", executable=True)
    node_path = _require_file(runtime_root, "node/bin/node", executable=True)
    _require_file(runtime_root, "node/bin/npm", executable=True)
    _require_file(runtime_root, "node/bin/npx", executable=True)
    browser_root = _require_directory(runtime_root, "ms-playwright")
    browser_directories = _browser_directories(browser_root)
    manifest = _runtime_manifest(runtime_root)
    python_version = _required_version(manifest, "python_version")
    node_version = _required_version(manifest, "node_version")
    for key, expected in (
        ("python", "python/bin/python3.11"),
        ("node", "node/bin/node"),
        ("npm", "node/bin/npm"),
        ("npx", "node/bin/npx"),
        ("playwright", "ms-playwright"),
    ):
        if manifest.get(key) != expected:
            _fail(f"runtime manifest {key!r} must be {expected!r}")
    if manifest.get("source_revision") != project_manifest.get("source_revision"):
        _fail("runtime and project manifests disagree on source_revision")

    host_architecture = platform.machine().lower()
    if require_arm64_host and host_architecture not in {"arm64", "aarch64"}:
        _fail(f"offline app requires Apple Silicon host, found {host_architecture}")

    if execute_runtime_checks:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{runtime_root / 'node' / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(project_root),
                "PLAYWRIGHT_BROWSERS_PATH": str(browser_root),
                "PEAP_PLAYWRIGHT_BROWSERS_PATH": str(browser_root),
                "PEAP_BUNDLED_RUNTIME_READ_ONLY": "1",
            }
        )
        _run_checked(
            (
                str(python_path),
                "-c",
                "import bs4, certifi, chardet, openpyxl, pandas, playwright, platform, yaml; "
                f"raise SystemExit(0 if platform.python_version() == {python_version!r} else 1)",
            ),
            env=env,
        )
        _run_checked(
            (
                str(node_path),
                "-e",
                f"if (process.versions.node !== {node_version!r}) process.exit(1)",
            ),
            env=env,
        )
        _run_checked(
            (
                str(python_path),
                "-c",
                "from playwright.sync_api import sync_playwright; "
                "playwright=sync_playwright().start(); "
                "browser=playwright.chromium.launch(headless=True); "
                "browser.close(); playwright.stop()",
            ),
            env=env,
        )

    return BundleReport(
        bundle=str(root),
        platform="macOS",
        architecture="arm64",
        project_files=project_files,
        python_executable=str(python_path),
        node_executable=str(node_path),
        browser_directories=browser_directories,
        executed_runtime_checks=execute_runtime_checks,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a PEAP offline macOS application bundle.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--execute", action="store_true", help="execute Python, Node and Playwright checks")
    parser.add_argument(
        "--require-arm64-host",
        action="store_true",
        help="also require the current host to be Apple Silicon",
    )
    args = parser.parse_args(argv)
    try:
        report = validate_bundle(
            args.bundle,
            execute_runtime_checks=args.execute,
            require_arm64_host=args.require_arm64_host,
        )
    except OfflineAppValidationError as exc:
        print(f"BLOCKED: {exc}")
        return 1
    print(json.dumps({"status": "ok", **report.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
