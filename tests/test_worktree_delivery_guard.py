from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.worktree_delivery_guard as guard

SCRIPT = Path("scripts/worktree_delivery_guard.py")


def _run_guard(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_empty_stdin_remains_hook_noop() -> None:
    result = _run_guard("")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {}


def test_malformed_json_payload_fails_closed_instead_of_empty_success() -> None:
    result = _run_guard("{not json")

    assert result.returncode != 0
    assert "malformed" in result.stderr.lower()
    assert result.stdout != "{}"


def test_non_object_payload_fails_closed_instead_of_empty_success() -> None:
    result = _run_guard("[]")

    assert result.returncode != 0
    assert "object" in result.stderr.lower()
    assert result.stdout != "{}"


def test_unknown_safety_hook_payload_denies_instead_of_empty_success() -> None:
    result = _run_guard(
        json.dumps(
            {
                "hookEventName": "PreToolUseTypo",
                "tool_name": "run_in_terminal",
                "tool_input": {"command": "git reset --hard"},
            }
        )
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload != {}
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unverified_test_command_string_does_not_count_as_real_test_proof(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = tmp_path / "worktree_delivery_evidence.json"
    monkeypatch.setattr(guard, "MANIFEST_PATH", manifest_path)
    guard._record_session_start({"sessionId": "session-1", "timestamp": "start"})

    guard._record_posttool_use(
        {
            "hookEventName": "PostToolUse",
            "sessionId": "session-1",
            "timestamp": "2026-05-31T00:00:00Z",
            "tool_name": "run_in_terminal",
            "tool_input": {"command": ".venv/bin/python -m pytest tests/test_worktree_delivery_guard.py -q"},
            "tool_response": {},
        }
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = manifest["sessions"]["session-1"]
    assert session["evidence"]["real_tests"] == []


def test_verified_success_status_counts_as_real_test_proof(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "worktree_delivery_evidence.json"
    monkeypatch.setattr(guard, "MANIFEST_PATH", manifest_path)

    guard._record_posttool_use(
        {
            "hookEventName": "PostToolUse",
            "sessionId": "session-1",
            "timestamp": "2026-05-31T00:00:00Z",
            "tool_name": "run_in_terminal",
            "tool_input": {"command": ".venv/bin/python -m pytest tests/test_worktree_delivery_guard.py -q"},
            "tool_response": {"returncode": 0, "stdout": "5 passed"},
        }
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = manifest["sessions"]["session-1"]
    real_tests = session["evidence"]["real_tests"]
    assert len(real_tests) == 1
    assert real_tests[0]["verified"] is True
    assert guard._valid_real_tests(session) == real_tests


def test_explicit_nonzero_pytest_status_overrides_passed_text_marker(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = tmp_path / "worktree_delivery_evidence.json"
    monkeypatch.setattr(guard, "MANIFEST_PATH", manifest_path)
    guard._record_session_start({"sessionId": "session-1", "timestamp": "start"})

    guard._record_posttool_use(
        {
            "hookEventName": "PostToolUse",
            "sessionId": "session-1",
            "timestamp": "2026-05-31T00:00:00Z",
            "tool_name": "run_in_terminal",
            "tool_input": {"command": "uv run pytest tests/test_worktree_delivery_guard.py -q"},
            "tool_response": {"returncode": 1, "stdout": "1 failed, 5 passed"},
        }
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = manifest["sessions"]["session-1"]
    assert session["evidence"]["real_tests"] == []
    assert guard._valid_real_tests(session) == []


@pytest.mark.parametrize(
    "report_text",
    [
        '"ok": true',
        "failed: 3\nprevious: 5 passed\nreport.md",
    ],
)
def test_report_scan_does_not_derive_real_tests_from_weak_report_text(
    tmp_path: Path, report_text: str
) -> None:
    fake_report = tmp_path / "fake_report.md"
    fake_report.write_text(report_text, encoding="utf-8")
    session = {
        "updated_at": "2026-05-31T00:00:00Z",
        "evidence": {
            "reports": [
                {
                    "source": "run_in_terminal",
                    "timestamp": "2026-05-31T00:00:00Z",
                    "path": str(fake_report),
                }
            ],
            "real_tests": [],
        },
    }

    guard._scan_reports_for_derived_evidence(session)

    assert session["evidence"]["real_tests"] == []
    assert guard._valid_real_tests(session) == []


def test_valid_real_tests_rejects_unverified_report_scan_entries(
    tmp_path: Path,
) -> None:
    fake_report = tmp_path / "fake_report.md"
    fake_report.write_text("failed: 3\nprevious: 5 passed", encoding="utf-8")
    session = {
        "evidence": {
            "real_tests": [
                {
                    "source": "report_scan",
                    "timestamp": "2026-05-31T00:00:00Z",
                    "command": f"report:{fake_report}",
                    "response_excerpt": fake_report.read_text(encoding="utf-8"),
                    "report_paths": [str(fake_report)],
                }
            ]
        }
    }

    assert guard._valid_real_tests(session) == []


def test_existing_corrupt_manifest_fails_closed_instead_of_reinitializing(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = tmp_path / "worktree_delivery_evidence.json"
    manifest_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(guard, "MANIFEST_PATH", manifest_path)

    with pytest.raises(guard.ManifestError):
        guard._record_session_start({"sessionId": "session-1", "timestamp": "start"})

    assert manifest_path.read_text(encoding="utf-8") == "{not json"


def test_existing_non_object_manifest_fails_closed_instead_of_reinitializing(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = tmp_path / "worktree_delivery_evidence.json"
    manifest_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(guard, "MANIFEST_PATH", manifest_path)

    with pytest.raises(guard.ManifestError):
        guard._record_session_start({"sessionId": "session-1", "timestamp": "start"})

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == []


def test_shanghai_validation_rejects_pure_note_without_reviewable_evidence() -> None:
    session = {
        "evidence": {
            "shanghai_validation": [
                {
                    "source": "manual_note",
                    "timestamp": "manual-note",
                    "note": "已确认上海产权交易所新页面支持没有破坏式回归。",
                }
            ]
        }
    }

    assert guard._valid_shanghai_validation(session) == []
