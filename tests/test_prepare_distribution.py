from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.prepare_distribution import (
    DEFAULT_OUTPUT,
    OUTPUT_MANIFEST_NAME,
    REPO_ROOT,
    DistributionError,
    _atomic_exchange_supported,
    _atomic_publish,
    _iter_files,
    _load_manifest,
    _publish_journal_path,
    _recover_publish_journal,
    _relative,
    _sha256,
    _validated_owned_output,
)

SOURCE_MANIFEST = {"name": "PEAP", "profile": "runtime-source"}


def test_runtime_source_manifest_is_macos_complete() -> None:
    manifest = _load_manifest()
    selected = {_relative(path) for path in _iter_files(manifest, DEFAULT_OUTPUT)}

    assert manifest["schema_version"] == 1
    assert manifest["name"] == "PEAP"
    assert manifest["profile"] == "runtime-source"
    assert manifest["platform"] == "macOS"
    assert manifest["offline_app"] is False
    assert manifest["bundled_runtimes"] is False

    runtime_sources = {
        path.relative_to(REPO_ROOT).as_posix()
        for root_name in (
            "desktop_backend",
            "peap",
            "peap_core",
            "peap_parsers",
            "peap_postprocess",
        )
        for path in (REPO_ROOT / root_name).rglob("*.py")
    }
    runtime_sources.update(
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("*.py", "*.sh")
        for path in (REPO_ROOT / "scripts").glob(pattern)
    )

    assert runtime_sources <= selected
    assert not {
        relative_path
        for relative_path in selected
        if Path(relative_path).suffix.lower() in {".bat", ".cmd", ".ps1"}
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("schema_version", 2),
        ("name", "Another Project"),
        ("profile", "offline-app"),
        ("platform", "Windows"),
        ("offline_app", True),
        ("bundled_runtimes", True),
    ),
)
def test_runtime_source_manifest_rejects_wrong_profile_metadata(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    payload = _load_manifest()
    payload[field] = invalid_value
    manifest_path = tmp_path / "distribution-manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with patch("scripts.prepare_distribution.MANIFEST_PATH", manifest_path):
        with pytest.raises(DistributionError, match=field):
            _load_manifest()


def _write_owned_output(root: Path) -> Path:
    payload_file = root / "peap" / "runtime.py"
    payload_file.parent.mkdir(parents=True)
    payload_file.write_text("value = 1\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "name": "PEAP",
        "profile": "runtime-source",
        "source_revision": "a" * 40,
        "source_dirty": False,
        "files": [
            {
                "path": "peap/runtime.py",
                "size": payload_file.stat().st_size,
                "sha256": _sha256(payload_file),
            }
        ],
    }
    (root / OUTPUT_MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return payload_file


def test_owned_distribution_output_is_replaceable(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    _write_owned_output(output)

    _validated_owned_output(output, SOURCE_MANIFEST)


def test_unrecognized_output_is_never_replaceable(tmp_path: Path) -> None:
    output = tmp_path / "Downloads"
    output.mkdir()
    (output / "personal.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DistributionError, match="unrecognized output directory"):
        _validated_owned_output(output, SOURCE_MANIFEST)


def test_extra_or_modified_output_content_blocks_recursive_replacement(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    payload_file = _write_owned_output(output)
    (output / "untracked.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DistributionError, match="files differ"):
        _validated_owned_output(output, SOURCE_MANIFEST)

    (output / "untracked.txt").unlink()
    payload_file.write_text("locally edited\n", encoding="utf-8")
    with pytest.raises(DistributionError, match="modified distribution output"):
        _validated_owned_output(output, SOURCE_MANIFEST)

    payload_file.write_text("value = 1\n", encoding="utf-8")
    (output / "empty-user-directory").mkdir()
    with pytest.raises(DistributionError, match="files differ"):
        _validated_owned_output(output, SOURCE_MANIFEST)


def test_symlink_in_output_blocks_recursive_replacement(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    _write_owned_output(output)
    (output / "linked").symlink_to(tmp_path)

    with pytest.raises(DistributionError, match="containing a symlink"):
        _validated_owned_output(output, SOURCE_MANIFEST)


def test_atomic_publish_restores_owned_output_when_install_fails(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    _write_owned_output(output)
    staging = tmp_path / ".PEAP-source.staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")

    real_replace = __import__("os").replace
    calls = {"count": 0}

    def fail_install(source: str | Path, destination: str | Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated install failure")
        real_replace(source, destination)

    with (
        patch("scripts.prepare_distribution._atomic_exchange_supported", return_value=False),
        patch("scripts.prepare_distribution.os.replace", side_effect=fail_install),
    ):
        with pytest.raises(OSError, match="simulated install failure"):
            _atomic_publish(staging=staging, output=output, replace_existing=True)

    assert output.is_dir()
    assert (output / "peap" / "runtime.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not staging.exists()
    assert not _publish_journal_path(output).exists()


@pytest.mark.skipif(
    sys.platform != "darwin" or not _atomic_exchange_supported(),
    reason="requires macOS atomic directory exchange",
)
def test_sigkill_after_atomic_exchange_never_hides_output_and_is_recoverable(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    _write_owned_output(output)
    staging = tmp_path / ".PEAP-source.staging-kill"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                (
                    "import os, signal",
                    "from pathlib import Path",
                    "import scripts.prepare_distribution as module",
                    f"output = Path({str(output)!r})",
                    f"staging = Path({str(staging)!r})",
                    "real_exchange = module._atomic_directory_exchange",
                    "def kill_after_exchange(source, destination):",
                    "    real_exchange(source, destination)",
                    "    os.kill(os.getpid(), signal.SIGKILL)",
                    "module._atomic_directory_exchange = kill_after_exchange",
                    "module._atomic_publish(staging=staging, output=output, replace_existing=True)",
                )
            ),
        ],
        cwd=repo_root,
        check=False,
    )

    assert child.returncode == -signal.SIGKILL
    assert output.is_dir()
    assert (output / "new.txt").read_text(encoding="utf-8") == "new"
    assert _publish_journal_path(output).is_file()

    _recover_publish_journal(output)

    assert output.is_dir()
    assert (output / "new.txt").read_text(encoding="utf-8") == "new"
    assert not staging.exists()
    assert not _publish_journal_path(output).exists()


def test_sigkill_between_fallback_renames_restores_old_output_from_journal(tmp_path: Path) -> None:
    output = tmp_path / "PEAP-source"
    _write_owned_output(output)
    staging = tmp_path / ".PEAP-source.staging-fallback-kill"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                (
                    "import os, signal",
                    "from pathlib import Path",
                    "import scripts.prepare_distribution as module",
                    f"output = Path({str(output)!r})",
                    f"staging = Path({str(staging)!r})",
                    "module._atomic_exchange_supported = lambda: False",
                    "real_replace = os.replace",
                    "calls = 0",
                    "def kill_after_old_move(source, destination):",
                    "    global calls",
                    "    calls += 1",
                    "    real_replace(source, destination)",
                    "    if calls == 1:",
                    "        os.kill(os.getpid(), signal.SIGKILL)",
                    "module.os.replace = kill_after_old_move",
                    "module._atomic_publish(staging=staging, output=output, replace_existing=True)",
                )
            ),
        ],
        cwd=repo_root,
        check=False,
    )

    assert child.returncode == -signal.SIGKILL
    assert not output.exists()
    assert _publish_journal_path(output).is_file()

    _recover_publish_journal(output)

    assert output.is_dir()
    assert (output / "peap" / "runtime.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not staging.exists()
    assert not _publish_journal_path(output).exists()
