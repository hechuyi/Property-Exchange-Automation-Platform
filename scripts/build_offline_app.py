#!/usr/bin/env python3
"""Build a self-contained Apple Silicon PEAP application bundle.

This command is a packaging operation, not a development bootstrap.  It stages
the runtime-source profile, installs locked dependencies into the staging
directory, embeds relocatable Python/Node/Playwright runtimes, and validates
the complete bundle before publication.  No runtime or user data is written
back to the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

try:
    from scripts.validate_offline_app import (
        BUILD_MANIFEST,
        OfflineAppValidationError,
        validate_bundle,
        validate_project_source,
        validate_release_id,
    )
except ModuleNotFoundError:  # direct execution from a source staging tree
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.validate_offline_app import (  # type: ignore[no-redef]
        BUILD_MANIFEST,
        OfflineAppValidationError,
        validate_bundle,
        validate_project_source,
        validate_release_id,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "packaging" / "offline-app.json"
TEMPLATE_ROOT = REPO_ROOT / "packaging" / "launcher-template"
DEFAULT_OUTPUT = REPO_ROOT / "release" / "PEAP Launcher.app"


class OfflineAppBuildError(RuntimeError):
    """Raised when an offline application cannot be built reproducibly."""


_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
_MINOS_PATTERN = re.compile(r"\bminos\s+([0-9]+(?:\.[0-9]+){1,2})\b")


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value).split("."))


def _assert_runtime_macos_compatibility(*, runtime_root: Path, minimum_macos: str) -> None:
    """Fail a build whose embedded Mach-O files need a newer macOS release."""

    if sys.platform != "darwin":
        return
    xcrun = shutil.which("xcrun")
    if xcrun:
        try:
            objdump = _run([xcrun, "--find", "llvm-objdump"]).strip()
        except OfflineAppBuildError:
            objdump = ""
    else:
        objdump = ""
    otool = shutil.which("otool")
    if not objdump and not otool:
        raise OfflineAppBuildError("llvm-objdump or otool is required to verify embedded macOS runtimes")
    declared = _version_tuple(minimum_macos)
    for candidate in runtime_root.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle:
                if handle.read(4) not in _MACHO_MAGICS:
                    continue
        except OSError as exc:
            raise OfflineAppBuildError(f"cannot inspect runtime binary {candidate}: {exc}") from exc
        if objdump:
            output = _run([objdump, "--macho", "--private-headers", candidate])
        else:
            output = _run([otool, "-arch", "all", "-l", candidate])
        for raw_version in _MINOS_PATTERN.findall(output):
            if _version_tuple(raw_version) > declared:
                raise OfflineAppBuildError(
                    f"embedded runtime {candidate.relative_to(runtime_root)} requires macOS {raw_version}, "
                    f"but profile minimum_macos is {minimum_macos}"
                )


@dataclass(frozen=True)
class OfflineAppProfile:
    python_version: str
    node_version: str
    node_url: str
    node_sha256: str
    node_archive_root: str
    minimum_macos: str


def _load_profile(path: Path = PROFILE_PATH) -> OfflineAppProfile:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineAppBuildError(f"cannot load offline app profile {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise OfflineAppBuildError("offline app profile schema_version must be 1")
    if payload.get("platform") != "macOS" or payload.get("architecture") != "arm64":
        raise OfflineAppBuildError("offline app profile must target macOS arm64")
    node_archive = payload.get("node_archive")
    if not isinstance(node_archive, Mapping):
        raise OfflineAppBuildError("offline app profile node_archive must be an object")
    values = {
        "python_version": payload.get("python_version"),
        "node_version": payload.get("node_version"),
        "node_url": node_archive.get("url"),
        "node_sha256": node_archive.get("sha256"),
        "node_archive_root": node_archive.get("root"),
        "minimum_macos": payload.get("minimum_macos"),
    }
    missing = sorted(key for key, value in values.items() if not str(value or "").strip())
    if missing:
        raise OfflineAppBuildError("offline app profile has empty fields: " + ", ".join(missing))
    sha256 = str(values["node_sha256"]).lower()
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise OfflineAppBuildError("offline app profile node_archive.sha256 is invalid")
    return OfflineAppProfile(**{key: str(value) for key, value in values.items()})


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    rendered = tuple(str(item) for item in command)
    completed = subprocess.run(
        rendered,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip()
        detail = output.splitlines()[-1] if output else f"exit={completed.returncode}"
        raise OfflineAppBuildError(f"command failed ({' '.join(rendered)}): {detail}")
    return completed.stdout.strip()


def _require_builder_host() -> None:
    system = platform.system()
    architecture = platform.machine().lower()
    if system != "Darwin" or architecture not in {"arm64", "aarch64"}:
        raise OfflineAppBuildError(
            f"offline app builds require an Apple Silicon macOS host; found {system}/{architecture}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_verified(*, url: str, sha256: str, destination: Path, offline: bool) -> Path:
    if destination.is_file() and _sha256(destination) == sha256:
        return destination
    if destination.exists():
        destination.unlink()
    if offline:
        raise OfflineAppBuildError(f"offline build cache is missing verified archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.download-{os.getpid()}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        actual = _sha256(temporary)
        if actual != sha256:
            raise OfflineAppBuildError(
                f"download checksum mismatch for {url}: expected={sha256} actual={actual}"
            )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _safe_extract_tar(archive: Path, destination: Path, *, expected_root: str) -> Path:
    def normalized(parts: Sequence[str]) -> tuple[str, ...]:
        result: list[str] = []
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not result:
                    raise OfflineAppBuildError("archive path escapes extraction root")
                result.pop()
                continue
            result.append(part)
        return tuple(result)

    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute():
                raise OfflineAppBuildError(f"unsafe archive path: {member.name}")
            try:
                normalized_path = normalized(path.parts)
            except OfflineAppBuildError as exc:
                raise OfflineAppBuildError(f"unsafe archive path: {member.name}") from exc
            if not normalized_path or normalized_path[0] != expected_root:
                raise OfflineAppBuildError(f"archive member is outside expected root: {member.name}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise OfflineAppBuildError(f"unsupported archive member type: {member.name}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute():
                    raise OfflineAppBuildError(
                        f"unsafe archive link: {member.name} -> {member.linkname}"
                    )
                link_parts = (
                    (*normalized_path[:-1], *link.parts)
                    if member.issym()
                    else link.parts
                )
                try:
                    normalized_link = normalized(link_parts)
                except OfflineAppBuildError as exc:
                    raise OfflineAppBuildError(
                        f"unsafe archive link: {member.name} -> {member.linkname}"
                    ) from exc
                if not normalized_link or normalized_link[0] != expected_root:
                    raise OfflineAppBuildError(
                        f"unsafe archive link: {member.name} -> {member.linkname}"
                    )
        handle.extractall(destination)
    root = destination / expected_root
    if not root.is_dir():
        raise OfflineAppBuildError(f"Node archive is missing expected root: {expected_root}")
    return root


def _copytree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise OfflineAppBuildError(f"runtime directory does not exist: {source}")
    shutil.copytree(source, destination, symlinks=True)


def _validate_staged_source(project_root: Path) -> None:
    try:
        validate_project_source(project_root)
    except OfflineAppValidationError as exc:
        raise OfflineAppBuildError(f"source staging failed validation: {exc}") from exc


def _prepare_source_staging(
    *,
    destination: Path,
    source_staging: Path | None,
    allow_dirty: bool,
) -> None:
    if source_staging is not None:
        source = source_staging.expanduser().resolve()
        _validate_staged_source(source)
        _copytree(source, destination)
        return
    command: list[str | os.PathLike[str]] = (
        [sys.executable, REPO_ROOT / "scripts" / "prepare_distribution.py", "--output", destination]
    )
    if allow_dirty:
        command.append("--allow-dirty")
    _run(command, cwd=REPO_ROOT)
    _validate_staged_source(destination)


def _prepare_python_runtime(
    *,
    destination: Path,
    project_root: Path,
    profile: OfflineAppProfile,
    supplied_runtime: Path | None,
    offline: bool,
) -> Path:
    if supplied_runtime is not None:
        _copytree(supplied_runtime.expanduser().resolve(), destination)
    else:
        uv = shutil.which("uv")
        if not uv:
            raise OfflineAppBuildError("uv is required to assemble the Python runtime")
        install_command = [uv, "python", "install", profile.python_version]
        if offline:
            install_command.append("--offline")
        _run(install_command, cwd=REPO_ROOT)
        find_command = [
            uv,
            "python",
            "find",
            "--no-project",
            "--managed-python",
            "--resolve-links",
            profile.python_version,
        ]
        if offline:
            find_command.append("--offline")
        python_source = Path(_run(find_command, cwd=REPO_ROOT)).resolve()
        if not python_source.is_file():
            raise OfflineAppBuildError(f"uv returned an invalid Python interpreter: {python_source}")
        _copytree(python_source.parent.parent, destination)

    python_path = destination / "bin" / "python3.11"
    if not python_path.is_file():
        raise OfflineAppBuildError("Python runtime must contain bin/python3.11")
    uv = shutil.which("uv")
    if not uv:
        raise OfflineAppBuildError("uv is required to install locked Python dependencies")
    sync_command = [
        uv,
        "pip",
        "sync",
        "--python",
        python_path,
        "--prefix",
        destination,
        project_root / "desktop_backend" / "requirements.lock.txt",
    ]
    if offline:
        sync_command.append("--offline")
    _run(sync_command, cwd=project_root)
    return python_path


def _prune_python_bytecode(runtime_root: Path) -> None:
    """Remove copied build-time bytecode from the relocatable runtime."""

    for cache_dir in sorted(runtime_root.rglob("__pycache__"), reverse=True):
        if cache_dir.is_dir() and not cache_dir.is_symlink():
            shutil.rmtree(cache_dir)
    for bytecode in runtime_root.rglob("*.pyc"):
        if bytecode.is_file() and not bytecode.is_symlink():
            bytecode.unlink()


def _relocate_python_shared_library(runtime_root: Path) -> None:
    """Make the embedded Python shared library self-contained.

    uv's managed interpreter records its original absolute install name in
    ``libpython``.  That path only exists on the build machine, so rewrite it
    to the adjacent library before the bundle is signed.
    """

    if sys.platform != "darwin":
        return
    library = runtime_root / "lib" / "libpython3.11.dylib"
    if not library.is_file():
        raise OfflineAppBuildError("embedded Python is missing lib/libpython3.11.dylib")
    otool = shutil.which("otool")
    install_name_tool = shutil.which("install_name_tool")
    if not otool or not install_name_tool:
        raise OfflineAppBuildError("otool and install_name_tool are required to relocate embedded Python")
    dependencies = _run([otool, "-L", library]).splitlines()[1:]
    old_install_name = ""
    for line in dependencies:
        candidate = line.strip().split(" (compatibility", 1)[0].strip()
        if candidate.endswith("/libpython3.11.dylib") and not candidate.startswith("@"):
            old_install_name = candidate
            break
    if old_install_name:
        _run(
            [
                install_name_tool,
                "-change",
                old_install_name,
                "@loader_path/libpython3.11.dylib",
                library,
            ]
        )
    current_id = _run([otool, "-D", library]).splitlines()[1].strip()
    if current_id and not current_id.startswith("@"):
        _run(
            [
                install_name_tool,
                "-id",
                "@loader_path/libpython3.11.dylib",
                library,
            ]
        )


def _prune_build_metadata(runtime_root: Path) -> None:
    """Remove installer provenance files that embed temporary build paths."""

    for metadata in runtime_root.rglob("direct_url.json"):
        if metadata.is_file() and not metadata.is_symlink():
            metadata.unlink()


def _prepare_node_runtime(
    *,
    destination: Path,
    profile: OfflineAppProfile,
    supplied_runtime: Path | None,
    cache_dir: Path,
    offline: bool,
) -> None:
    if supplied_runtime is not None:
        _copytree(supplied_runtime.expanduser().resolve(), destination)
    else:
        archive_name = PurePosixPath(profile.node_url).name
        archive = _download_verified(
            url=profile.node_url,
            sha256=profile.node_sha256,
            destination=cache_dir / archive_name,
            offline=offline,
        )
        with tempfile.TemporaryDirectory(prefix="peap-node-extract-") as temp_dir:
            extracted = _safe_extract_tar(
                archive,
                Path(temp_dir) / "node",
                expected_root=profile.node_archive_root,
            )
            _copytree(extracted, destination)
    for relative in ("bin/node", "bin/npm", "bin/npx"):
        if not (destination / relative).is_file():
            raise OfflineAppBuildError(f"Node runtime is missing {relative}")


def _runtime_environment(*, runtime_root: Path, project_root: Path) -> dict[str, str]:
    browser_root = runtime_root / "ms-playwright"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{runtime_root / 'node' / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(project_root),
            "PLAYWRIGHT_BROWSERS_PATH": str(browser_root),
            "PEAP_PLAYWRIGHT_BROWSERS_PATH": str(browser_root),
        }
    )
    return env


def _prepare_frontend(*, runtime_root: Path, project_root: Path, offline: bool) -> None:
    npm_path = runtime_root / "node" / "bin" / "npm"
    command: list[str | os.PathLike[str]] = [npm_path, "ci", "--ignore-scripts"]
    if offline:
        command.append("--offline")
    _run(command, cwd=project_root / "frontend", env=_runtime_environment(runtime_root=runtime_root, project_root=project_root))


def _prepare_playwright(
    *,
    runtime_root: Path,
    project_root: Path,
    python_path: Path,
    supplied_browsers: Path | None,
    offline: bool,
) -> None:
    destination = runtime_root / "ms-playwright"
    if supplied_browsers is not None:
        _copytree(supplied_browsers.expanduser().resolve(), destination)
        return
    if offline:
        raise OfflineAppBuildError("offline mode requires --playwright-browsers")
    destination.mkdir(parents=True)
    _run(
        [python_path, "-m", "playwright", "install", "chromium"],
        cwd=project_root,
        env=_runtime_environment(runtime_root=runtime_root, project_root=project_root),
    )


def _source_revision(project_root: Path) -> str:
    try:
        payload = json.loads(
            (project_root / "DISTRIBUTION_MANIFEST.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    return str(payload.get("source_revision") or "unknown")


def _write_runtime_manifest(
    *,
    runtime_root: Path,
    profile: OfflineAppProfile,
    source_revision: str,
) -> None:
    payload = {
        "schema_version": 1,
        "platform": "macOS",
        "architecture": "arm64",
        "python": "python/bin/python3.11",
        "python_version": profile.python_version,
        "node": "node/bin/node",
        "node_version": profile.node_version,
        "npm": "node/bin/npm",
        "npx": "node/bin/npx",
        "playwright": "ms-playwright",
        "source_revision": source_revision,
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    (runtime_root / "runtime-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_bundle_metadata(
    *,
    bundle_root: Path,
    profile: OfflineAppProfile,
    version: str,
    build_number: str,
    release_id: str,
) -> None:
    try:
        release_id = validate_release_id(release_id)
    except OfflineAppValidationError as exc:
        raise OfflineAppBuildError(str(exc)) from exc
    plist_path = bundle_root / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["CFBundleShortVersionString"] = version
    payload["CFBundleVersion"] = build_number
    payload["LSMinimumSystemVersion"] = profile.minimum_macos
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    resources = bundle_root / "Contents" / "Resources"
    (resources / "release-id.txt").write_text(release_id + "\n", encoding="utf-8")
    (bundle_root / BUILD_MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "offline-app",
                "platform": "macOS",
                "architecture": "arm64",
                "minimum_macos": profile.minimum_macos,
                "release_id": release_id,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for relative in (
        "Contents/MacOS/PEAPLauncher",
        "Contents/Resources/run.sh",
        "Contents/Resources/initialize.sh",
        "Contents/Resources/Project/start.sh",
    ):
        path = bundle_root / relative
        path.chmod(path.stat().st_mode | 0o755)


def _codesign(bundle_root: Path, identity: str) -> None:
    codesign = shutil.which("codesign")
    if not codesign:
        raise OfflineAppBuildError("codesign is unavailable on this host")
    _run([codesign, "--force", "--deep", "--options", "runtime", "--sign", identity, bundle_root])
    _run([codesign, "--verify", "--deep", "--strict", bundle_root])


def _assert_owned_existing_output(output: Path) -> None:
    try:
        validate_bundle(output, execute_runtime_checks=False)
    except OfflineAppValidationError as exc:
        raise OfflineAppBuildError(
            f"refusing to replace an app that is unrecognized, incomplete or modified: {output}: {exc}"
        ) from exc


def build_offline_app(
    *,
    output: Path,
    source_staging: Path | None = None,
    python_runtime: Path | None = None,
    node_runtime: Path | None = None,
    playwright_browsers: Path | None = None,
    cache_dir: Path,
    allow_dirty: bool = False,
    offline: bool = False,
    force: bool = False,
    version: str,
    build_number: str,
    release_id: str,
    codesign_identity: str | None = "-",
) -> Path:
    try:
        release_id = validate_release_id(release_id)
    except OfflineAppValidationError as exc:
        raise OfflineAppBuildError(str(exc)) from exc
    _require_builder_host()
    profile = _load_profile()
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise OfflineAppBuildError(f"output already exists; pass --force to replace it: {output}")
    if output.exists() and (not output.is_dir() or output.suffix != ".app"):
        raise OfflineAppBuildError(f"refusing to replace non-app output: {output}")
    if output.exists():
        _assert_owned_existing_output(output)

    with tempfile.TemporaryDirectory(dir=output.parent, prefix=f".{output.stem}.build-") as temp_dir:
        temporary_root = Path(temp_dir)
        bundle_root = temporary_root / output.name
        _copytree(TEMPLATE_ROOT, bundle_root)
        resources = bundle_root / "Contents" / "Resources"
        project_root = resources / "Project"
        runtime_root = resources / "Runtime" / "arm64"
        runtime_root.mkdir(parents=True)
        _prepare_source_staging(
            destination=project_root,
            source_staging=source_staging,
            allow_dirty=allow_dirty,
        )
        python_path = _prepare_python_runtime(
            destination=runtime_root / "python",
            project_root=project_root,
            profile=profile,
            supplied_runtime=python_runtime,
            offline=offline,
        )
        _prepare_node_runtime(
            destination=runtime_root / "node",
            profile=profile,
            supplied_runtime=node_runtime,
            cache_dir=cache_dir.expanduser().resolve(),
            offline=offline,
        )
        _prepare_frontend(runtime_root=runtime_root, project_root=project_root, offline=offline)
        _prepare_playwright(
            runtime_root=runtime_root,
            project_root=project_root,
            python_path=python_path,
            supplied_browsers=playwright_browsers,
            offline=offline,
        )
        _relocate_python_shared_library(runtime_root / "python")
        _prune_python_bytecode(runtime_root / "python")
        _prune_build_metadata(runtime_root / "python")
        _assert_runtime_macos_compatibility(
            runtime_root=runtime_root,
            minimum_macos=profile.minimum_macos,
        )
        revision = _source_revision(project_root)
        _write_runtime_manifest(
            runtime_root=runtime_root,
            profile=profile,
            source_revision=revision,
        )
        _write_bundle_metadata(
            bundle_root=bundle_root,
            profile=profile,
            version=version,
            build_number=build_number,
            release_id=release_id,
        )
        try:
            validate_bundle(
                bundle_root,
                execute_runtime_checks=True,
                require_arm64_host=True,
            )
        except OfflineAppValidationError as exc:
            raise OfflineAppBuildError(f"assembled bundle failed validation: {exc}") from exc
        if codesign_identity is not None:
            _codesign(bundle_root, codesign_identity)

        if output.exists():
            backup = output.parent / f".{output.name}.previous-{os.getpid()}"
            if backup.exists():
                raise OfflineAppBuildError(f"temporary backup path already exists: {backup}")
            os.replace(output, backup)
            try:
                os.replace(bundle_root, output)
            except Exception:
                os.replace(backup, output)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(bundle_root, output)
    return output


def main(argv: list[str] | None = None) -> int:
    now = datetime.now(UTC)
    default_version = f"{now.year}.{now.month}.{now.day}"
    default_build = now.strftime("%Y%m%d%H%M%S")
    parser = argparse.ArgumentParser(description="Build a self-contained PEAP macOS arm64 app.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-staging", type=Path)
    parser.add_argument("--python-runtime", type=Path)
    parser.add_argument("--node-runtime", type=Path)
    parser.add_argument("--playwright-browsers", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "peap-build")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", default=default_version)
    parser.add_argument("--build-number", default=default_build)
    parser.add_argument("--release-id", default=f"{default_build}-offline-arm64")
    parser.add_argument(
        "--codesign-identity",
        default="-",
        help="codesign identity; use an empty value to skip signing",
    )
    args = parser.parse_args(argv)
    try:
        output = build_offline_app(
            output=args.output,
            source_staging=args.source_staging,
            python_runtime=args.python_runtime,
            node_runtime=args.node_runtime,
            playwright_browsers=args.playwright_browsers,
            cache_dir=args.cache_dir,
            allow_dirty=args.allow_dirty,
            offline=args.offline,
            force=args.force,
            version=str(args.version),
            build_number=str(args.build_number),
            release_id=str(args.release_id),
            codesign_identity=str(args.codesign_identity) or None,
        )
    except OfflineAppBuildError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
