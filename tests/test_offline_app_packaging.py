from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_offline_app import (
    OfflineAppBuildError,
    _load_profile,
    _prune_python_bytecode,
    _safe_extract_tar,
)
from scripts.validate_offline_app import (
    REQUIRED_PROJECT_INVENTORY_FILES,
    OfflineAppValidationError,
    validate_bundle,
    validate_release_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str = "fixture\n", *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_manifest(project: Path) -> Path:
    entries = [
        {
            "path": relative,
            "size": (project / relative).stat().st_size,
            "sha256": _sha256(project / relative),
        }
        for relative in sorted(REQUIRED_PROJECT_INVENTORY_FILES)
    ]
    manifest = project / "DISTRIBUTION_MANIFEST.json"
    _write(
        manifest,
        json.dumps(
            {
                "schema_version": 1,
                "name": "PEAP",
                "profile": "runtime-source",
                "source_revision": "fixture-revision",
                "source_dirty": False,
                "files": entries,
            }
        ),
    )
    return manifest


def _synthetic_bundle(root: Path) -> Path:
    bundle = root / "PEAP Launcher.app"
    for relative in (
        "Contents/MacOS/PEAPLauncher",
        "Contents/Resources/run.sh",
        "Contents/Resources/initialize.sh",
    ):
        _write(bundle / relative, "#!/bin/sh\nexit 0\n", executable=True)
    _write(bundle / "Contents/Info.plist")
    _write(bundle / "Contents/Resources/release-id.txt", "fixture-release\n")
    _write(
        bundle / "Contents/Resources/OFFLINE_APP_MANIFEST.json",
        json.dumps(
            {
                "schema_version": 1,
                "profile": "offline-app",
                "platform": "macOS",
                "architecture": "arm64",
                "release_id": "fixture-release",
            }
        ),
    )

    project = bundle / "Contents/Resources/Project"
    for relative in REQUIRED_PROJECT_INVENTORY_FILES:
        _write(project / relative, "#!/bin/sh\nexit 0\n", executable=relative == "start.sh")
    _project_manifest(project)
    _write(project / "frontend/node_modules/.bin/vite", "#!/bin/sh\nexit 0\n", executable=True)

    runtime = bundle / "Contents/Resources/Runtime/arm64"
    for relative in ("python/bin/python3.11", "node/bin/node", "node/bin/npm", "node/bin/npx"):
        _write(runtime / relative, "#!/bin/sh\nexit 0\n", executable=True)
    (runtime / "ms-playwright/chromium-1208").mkdir(parents=True)
    (runtime / "ms-playwright/chromium_headless_shell-1208").mkdir()
    _write(
        runtime / "runtime-manifest.json",
        json.dumps(
            {
                "schema_version": 1,
                "platform": "macOS",
                "architecture": "arm64",
                "python": "python/bin/python3.11",
                "python_version": "3.11.9",
                "node": "node/bin/node",
                "node_version": "20.19.5",
                "npm": "node/bin/npm",
                "npx": "node/bin/npx",
                "playwright": "ms-playwright",
                "source_revision": "fixture-revision",
            }
        ),
    )
    return bundle


def test_validator_rejects_the_checked_in_launcher_shell() -> None:
    with pytest.raises(OfflineAppValidationError):
        validate_bundle(REPO_ROOT / "PEAP Launcher.app")


def test_validator_accepts_a_complete_synthetic_bundle(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)

    report = validate_bundle(bundle)

    assert report.architecture == "arm64"
    assert report.project_files == len(REQUIRED_PROJECT_INVENTORY_FILES)
    assert report.browser_directories == (
        "chromium-1208",
        "chromium_headless_shell-1208",
    )
    assert not report.executed_runtime_checks


@pytest.mark.parametrize(
    ("relative", "message"),
    (
        ("Contents/Resources/Project/frontend/node_modules/.bin/vite", "vite"),
        ("Contents/Resources/Runtime/arm64/node/bin/npm", "npm"),
        ("Contents/Resources/Runtime/arm64/ms-playwright/chromium-1208", "chromium"),
    ),
)
def test_validator_fails_closed_for_missing_bundle_resources(
    tmp_path: Path,
    relative: str,
    message: str,
) -> None:
    bundle = _synthetic_bundle(tmp_path)
    target = bundle / relative
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()

    with pytest.raises(OfflineAppValidationError, match=message):
        validate_bundle(bundle)


def test_validator_rejects_runtime_symlink_that_escapes_bundle(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    python_path = bundle / "Contents/Resources/Runtime/arm64/python/bin/python3.11"
    python_path.unlink()
    python_path.symlink_to(Path(sys.executable).resolve())

    with pytest.raises(OfflineAppValidationError, match="runtime symlink"):
        validate_bundle(bundle)


def test_validator_rejects_tampered_project_file(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    target = bundle / "Contents/Resources/Project/peap/cli.py"
    target.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    with pytest.raises(OfflineAppValidationError, match="SHA-256 mismatch: peap/cli.py"):
        validate_bundle(bundle)


@pytest.mark.parametrize("unsafe_path", ("../escape.py", "/tmp/escape.py", "peap\\escape.py"))
def test_validator_rejects_unsafe_project_inventory_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    bundle = _synthetic_bundle(tmp_path)
    manifest_path = bundle / "Contents/Resources/Project/DISTRIBUTION_MANIFEST.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = unsafe_path
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OfflineAppValidationError, match="unsafe file path"):
        validate_bundle(bundle)


def test_validator_rejects_duplicate_or_incomplete_project_inventory(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    manifest_path = bundle / "Contents/Resources/Project/DISTRIBUTION_MANIFEST.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"].append(dict(payload["files"][0]))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OfflineAppValidationError, match="duplicate file entry"):
        validate_bundle(bundle)

    payload["files"] = [
        entry for entry in payload["files"][:-1] if entry["path"] != "desktop_backend/app_backend.py"
    ]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OfflineAppValidationError, match="omits required files"):
        validate_bundle(bundle)


def test_validator_rejects_untracked_source_file_and_manifest_symlink(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    project = bundle / "Contents/Resources/Project"
    _write(project / "peap/untracked.py")
    with pytest.raises(OfflineAppValidationError, match="untracked=peap/untracked.py"):
        validate_bundle(bundle)

    (project / "peap/untracked.py").unlink()
    target = project / "peap/cli.py"
    target.unlink()
    target.symlink_to(project / "peap/download_runner.py")
    with pytest.raises(OfflineAppValidationError, match="must not use symlinks"):
        validate_bundle(bundle)


@pytest.mark.parametrize("release_id", ("../escape", "a/b", " spaced ", "a\nb", "-leading", "trailing-"))
def test_release_id_contract_rejects_unsafe_path_components(release_id: str) -> None:
    with pytest.raises(OfflineAppValidationError, match="release_id"):
        validate_release_id(release_id)


def test_validator_requires_matching_release_identity(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    (bundle / "Contents/Resources/release-id.txt").write_text("different-release\n", encoding="utf-8")

    with pytest.raises(OfflineAppValidationError, match="does not match"):
        validate_bundle(bundle)


def test_validator_cli_reports_blocked_bundle(tmp_path: Path) -> None:
    bundle = _synthetic_bundle(tmp_path)
    (bundle / "Contents/Resources/Runtime/arm64/node/bin/node").unlink()

    completed = subprocess.run(
        [sys.executable, "scripts/validate_offline_app.py", str(bundle)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout.startswith("BLOCKED:")
    assert "node/bin/node" in completed.stdout


def test_offline_profile_pins_versions_and_node_digest() -> None:
    profile = _load_profile()

    assert profile.python_version == "3.11.9"
    assert profile.node_version == "20.19.5"
    assert profile.minimum_macos == "14.0"
    assert profile.node_sha256 == "cfed7503d8d99fbcf2f52e408ec52f616058eb0867b34dbc3437259993ef5cba"
    assert profile.node_url.startswith("https://nodejs.org/dist/")


def test_safe_tar_extraction_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    script = (
        "import io, tarfile, sys; "
        "t=tarfile.open(sys.argv[1], 'w:gz'); "
        "i=tarfile.TarInfo('../escape'); i.size=1; "
        "t.addfile(i, io.BytesIO(b'x')); t.close()"
    )
    subprocess.run([sys.executable, "-c", script, str(archive)], check=True)

    with pytest.raises(OfflineAppBuildError, match="unsafe archive path"):
        _safe_extract_tar(archive, tmp_path / "extract", expected_root="node")


def test_prune_python_bytecode_removes_build_cache(tmp_path: Path) -> None:
    runtime = tmp_path / "python"
    _write(runtime / "lib/python3.11/__pycache__/module.cpython-311.pyc", "bytecode")
    _write(runtime / "lib/python3.11/module.pyc", "bytecode")

    _prune_python_bytecode(runtime)

    assert not (runtime / "lib/python3.11/__pycache__").exists()
    assert not (runtime / "lib/python3.11/module.pyc").exists()


def test_builder_contains_python_relocation_guard() -> None:
    builder = (REPO_ROOT / "scripts/build_offline_app.py").read_text(encoding="utf-8")
    assert "install_name_tool" in builder
    assert "@loader_path/libpython3.11.dylib" in builder


def test_launcher_template_never_falls_back_to_an_external_project() -> None:
    launcher = (
        REPO_ROOT / "packaging/launcher-template/Contents/MacOS/PEAPLauncher"
    ).read_text(encoding="utf-8")
    run_script = (
        REPO_ROOT / "packaging/launcher-template/Contents/Resources/run.sh"
    ).read_text(encoding="utf-8")

    assert "EXTERNAL_PROJECT" not in launcher
    assert 'BUNDLED_PROJECT="$APP_BUNDLE/Contents/Resources/Project"' in launcher
    assert "release-id.txt" in run_script
    assert "npm ci" not in run_script
    assert "playwright install" not in run_script
    assert ".previous-" not in run_script
    assert 'mv "$PROJECT_ROOT"' not in run_script
    assert 'if ! mkdir "$PROJECT_ROOT"; then' in run_script
    assert '"$observed_token" == "$INSTALL_OWNERSHIP_TOKEN"' in run_script
    assert 'PEAP_LAUNCHER_ALLOW_CUSTOM_ROOT' in run_script
    assert 'PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES' in run_script
    assert 'trap cleanup_run EXIT INT TERM HUP' in run_script
    assert '(cd "$PROJECT_ROOT" && exec bash start.sh) &' in run_script
    assert 'OPEN_FRONTEND_PID=$!' in run_script
    assert 'CONSOLE_USER="$(/usr/bin/stat -f%Su /dev/console 2>/dev/null || true)"' in launcher
    assert 'PEAP_LAUNCHER_MANAGED=1' in launcher
    assert 'env -i HOME=' in launcher
    assert 'NFSHomeDirectory: //p' in launcher
    assert 'HTTP_PROXY' in launcher and 'REQUESTS_CA_BUNDLE' in launcher
    assert '允许 PEAP Launcher 自动化 Terminal' in launcher
    initialize = (
        REPO_ROOT / "packaging/launcher-template/Contents/Resources/initialize.sh"
    ).read_text(encoding="utf-8")
    assert 'PEAP_BUNDLED_RUNTIME_READ_ONLY=1' in initialize
    assert 'PYTHONDONTWRITEBYTECODE=1' in initialize
    assert 'NPM_CONFIG_UPDATE_NOTIFIER=false' in initialize


def test_launcher_preserves_an_existing_incomplete_editable_source(tmp_path: Path) -> None:
    resources = tmp_path / "Resources"
    resources.mkdir()
    run_script = resources / "run.sh"
    run_script.write_bytes(
        (REPO_ROOT / "packaging/launcher-template/Contents/Resources/run.sh").read_bytes()
    )
    _write(resources / "release-id.txt", "fixture-release\n")
    bundled_project = resources / "Project"
    _write(bundled_project / "start.sh", "#!/bin/sh\nexit 0\n", executable=True)
    editable_project = tmp_path / "editable-source"
    _write(editable_project / "USER_EDIT.txt", "must survive\n")

    completed = subprocess.run(
        ["bash", str(run_script), str(bundled_project)],
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": "/usr/bin:/bin",
            "PEAP_LAUNCHER_NO_DIALOGS": "1",
            "PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES": "1",
            "PEAP_LAUNCHER_ALLOW_CUSTOM_ROOT": "1",
            "PEAP_LAUNCHER_PROJECT_ROOT": str(editable_project),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "不会覆盖或删除" in completed.stderr
    assert (editable_project / "USER_EDIT.txt").read_text(encoding="utf-8") == "must survive\n"
    assert {path.name for path in editable_project.iterdir()} == {"USER_EDIT.txt"}
    assert not tuple(tmp_path.glob("editable-source.previous-*"))


@pytest.mark.skipif(not Path("/usr/bin/ditto").is_file(), reason="requires macOS ditto")
def test_launcher_ignores_an_inherited_custom_root_without_explicit_opt_in(tmp_path: Path) -> None:
    resources = tmp_path / "Resources"
    resources.mkdir()
    run_script = resources / "run.sh"
    run_script.write_bytes(
        (REPO_ROOT / "packaging/launcher-template/Contents/Resources/run.sh").read_bytes()
    )
    _write(resources / "release-id.txt", "fixture-release\n")
    _write(resources / "initialize.sh", "initialize_peap_environment() { return 0; }\n")
    bundled_project = resources / "Project"
    for relative in (
        "DISTRIBUTION_MANIFEST.json",
        "start.sh",
        "pyproject.toml",
        "uv.lock",
        "desktop_backend/requirements.lock.txt",
        "frontend/package-lock.json",
        "scripts/_paths.py",
    ):
        _write(bundled_project / relative, executable=relative == "start.sh")
    _write(
        bundled_project / "frontend/node_modules/.bin/vite",
        "#!/bin/sh\nexit 0\n",
        executable=True,
    )
    home = tmp_path / "home"
    stale_root = tmp_path / "stale-test-root"
    _write(stale_root / "DO_NOT_TOUCH.txt", "preserve\n")
    completed = subprocess.run(
        ["bash", str(run_script), str(bundled_project)],
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "PEAP_LAUNCHER_INIT_ONLY": "1",
            "PEAP_LAUNCHER_NO_DIALOGS": "1",
            "PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES": "1",
            "PEAP_LAUNCHER_PROJECT_ROOT": str(stale_root),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    default_root = home / "Documents/PEAP/source/fixture-release"
    assert (default_root / ".peap-launcher-source-fixture-release").is_file()
    assert (stale_root / "DO_NOT_TOUCH.txt").read_text(encoding="utf-8") == "preserve\n"
    assert not (stale_root / ".peap-launcher-source-fixture-release").exists()


@pytest.mark.skipif(not Path("/usr/bin/ditto").is_file(), reason="requires macOS ditto")
def test_concurrent_launchers_atomically_reserve_a_new_source_directory(tmp_path: Path) -> None:
    resources = tmp_path / "Resources"
    resources.mkdir()
    run_script = resources / "run.sh"
    run_script.write_bytes(
        (REPO_ROOT / "packaging/launcher-template/Contents/Resources/run.sh").read_bytes()
    )
    _write(resources / "release-id.txt", "fixture-release\n")
    _write(
        resources / "initialize.sh",
        "initialize_peap_environment() { sleep 0.2; return 0; }\n",
    )
    bundled_project = resources / "Project"
    for relative in (
        "DISTRIBUTION_MANIFEST.json",
        "start.sh",
        "pyproject.toml",
        "uv.lock",
        "desktop_backend/requirements.lock.txt",
        "frontend/package-lock.json",
        "scripts/_paths.py",
    ):
        _write(bundled_project / relative, executable=relative == "start.sh")
    _write(
        bundled_project / "frontend/node_modules/.bin/vite",
        "#!/bin/sh\nexit 0\n",
        executable=True,
    )
    editable_project = tmp_path / "editable-source"
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "PEAP_LAUNCHER_INIT_ONLY": "1",
        "PEAP_LAUNCHER_NO_DIALOGS": "1",
        "PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES": "1",
        "PEAP_LAUNCHER_ALLOW_CUSTOM_ROOT": "1",
        "PEAP_LAUNCHER_PROJECT_ROOT": str(editable_project),
    }

    processes = [
        subprocess.Popen(
            ["bash", str(run_script), str(bundled_project)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]

    assert sorted(result[2] for result in results) == [0, 1]
    assert (editable_project / ".peap-launcher-source-fixture-release").is_file()
    assert not any(path.name.startswith("editable-source") for path in editable_project.iterdir())
    assert not tuple(editable_project.glob(".peap-launcher-install-*"))


def test_distribution_manifest_contains_offline_app_build_inputs() -> None:
    payload = json.loads(
        (REPO_ROOT / "packaging/distribution-manifest.json").read_text(encoding="utf-8")
    )
    includes = set(payload["include"])

    assert "desktop_backend/requirements.lock.txt" in includes
    assert "packaging/offline-app.json" in includes
    assert "packaging/launcher-template/**" in includes
    assert "scripts/build_offline_app.py" in includes
    assert "scripts/validate_offline_app.py" in includes
    assert "PEAP Launcher.app/**" in set(payload["exclude"])


def test_builder_installs_python_dependencies_inside_the_embedded_prefix() -> None:
    builder = (REPO_ROOT / "scripts/build_offline_app.py").read_text(encoding="utf-8")
    assert '"--prefix",' in builder
    assert '"--system",' not in builder


def test_validator_stops_playwright_context_manager_correctly() -> None:
    validator = (REPO_ROOT / "scripts/validate_offline_app.py").read_text(encoding="utf-8")
    assert "browser.close(); playwright.stop()" in validator
    assert "manager.stop()" not in validator


def test_builder_script_is_directly_invokable_from_a_source_staging_tree(tmp_path: Path) -> None:
    staging = tmp_path / "PEAP-source"
    (staging / "scripts").mkdir(parents=True)
    source_script = REPO_ROOT / "scripts/build_offline_app.py"
    validator_script = REPO_ROOT / "scripts/validate_offline_app.py"
    (staging / "scripts/build_offline_app.py").write_bytes(source_script.read_bytes())
    (staging / "scripts/validate_offline_app.py").write_bytes(validator_script.read_bytes())
    (staging / "scripts/build_offline_app.py").chmod(0o755)

    completed = subprocess.run(
        [sys.executable, "scripts/build_offline_app.py", "--help"],
        cwd=staging,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Build a self-contained PEAP macOS arm64 app" in completed.stdout
