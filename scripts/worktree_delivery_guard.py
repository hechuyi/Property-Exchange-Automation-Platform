from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, List, Sequence

SCRIPT_PATH = Path(__file__).resolve()
WORKTREE_ROOT = SCRIPT_PATH.parents[1]
WORKTREE_NAME = WORKTREE_ROOT.name
MAIN_REPO_ROOT = WORKTREE_ROOT.parent.parent if WORKTREE_ROOT.parent.name == ".worktrees" else WORKTREE_ROOT
WORKTREE_ROOT_TEXT = str(WORKTREE_ROOT)
MAIN_REPO_ROOT_TEXT = str(MAIN_REPO_ROOT)
_CONFIGURED_REPO_ROOTS = tuple(
    item.strip()
    for item in os.environ.get("PEAP_DELIVERY_PROTECTED_REPO_ROOTS", "").split(os.pathsep)
    if item.strip()
)
PROTECTED_REPO_ROOTS = tuple(dict.fromkeys((MAIN_REPO_ROOT_TEXT, *_CONFIGURED_REPO_ROOTS)))
MANIFEST_PATH = WORKTREE_ROOT / "logs" / "worktree_delivery_evidence.json"
MANIFEST_VERSION = 2

DESTRUCTIVE_TERMINAL_RULES = (
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "禁止执行 git reset --hard。"),
    (re.compile(r"\bgit\s+checkout\s+--\b"), "禁止执行 git checkout --。"),
    (re.compile(r"\bgit\s+clean\s+-f(?:d|x|dx|xfd)*\b"), "禁止执行 git clean 清理工作区。"),
    (re.compile(r"\brm\s+-rf\b"), "禁止执行 rm -rf。"),
)
REPO_LOCAL_COMMAND_HINT = re.compile(
    r"\b(npm|pnpm|yarn|uv|pytest|ruff|python|python3|node|git)\b|\.venv/bin/python"
)
WORKTREE_TERMINAL_ENTRY = re.compile(rf"\b(cd|pushd)\b[^\n]*{re.escape(WORKTREE_NAME)}\b")
WORKSPACE_PATH_PATTERN = re.compile(
    r"(?:"
    + "|".join(re.escape(root.rstrip("/\\")) for root in PROTECTED_REPO_ROOTS)
    + r")(?:[/\\][^\s\"']+)?"
)
REPORT_ENV_PATTERN = re.compile(
    r"\bPEAP_[A-Z0-9_]*REPORT_PATH=(['\"]?)(?P<path>/[^'\"\s]+)\1"
)
SCREENSHOT_DIR_ENV_PATTERN = re.compile(
    r"\bPEAP_[A-Z0-9_]*SCREENSHOT_DIR=(['\"]?)(?P<path>/[^'\"\s]+)\1"
)
REPORT_PATH_PATTERN = re.compile(r"(?P<path>/[^\s\"']+\.(?:md|json))", re.IGNORECASE)
IMAGE_PATH_PATTERN = re.compile(r"(?P<path>/[^\s\"']+\.(?:png|jpg|jpeg|webp))", re.IGNORECASE)
TEST_COMMAND_HINT = re.compile(
    r"\b(?:npm\s+test|pytest|uv\s+run\s+pytest|uv\s+run\s+python\s+-m\s+unittest|playwright|smoke|manual_import)\b",
    re.IGNORECASE,
)

REAL_TEST_MARKERS = (
    "npm test",
    "pytest",
    "playwright",
    "smoke",
    "manual_import",
    "report.md",
    '"ok": true',
    '"passed": true',
    "  passed",
)
SCREENSHOT_MARKERS = (
    "view_image",
    ".png",
    ".jpg",
    ".jpeg",
    "screenshot",
    "截图",
)
SHANGHAI_MARKERS = (
    "上海",
    "产权交易所",
    "suaee",
    "xmzx",
    "新页面",
    "上交所",
)

WRITE_SCOPED_TOOLS = {
    "apply_patch",
    "create_directory",
    "create_file",
    "edit_notebook_file",
    "install_python_packages",
    "mcp_pylance_mcp_s_pylanceInvokeRefactoring",
    "run_in_terminal",
    "vscode_renameSymbol",
}
EVIDENCE_BUCKETS = (
    "real_tests",
    "reports",
    "screenshots_reviewed",
    "shanghai_validation",
)


class HookPayloadError(ValueError):
    pass


class ManifestError(RuntimeError):
    pass


def _load_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HookPayloadError(
            f"malformed hook payload: stdin is not valid JSON ({exc.msg})"
        ) from exc
    if not isinstance(data, dict):
        raise HookPayloadError("malformed hook payload: top-level JSON value must be an object")
    return data


def _emit(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def _fail_payload_error(error: HookPayloadError) -> int:
    sys.stderr.write(f"{error}\n")
    sys.stderr.flush()
    return 2


def _collect_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _collect_strings(key)
            yield from _collect_strings(nested)
        return
    if isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _unique(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    unique_items: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items


def _extract_workspace_paths(value: Any) -> List[str]:
    candidates: List[str] = []
    for text in _collect_strings(value):
        candidates.extend(match.group(0) for match in WORKSPACE_PATH_PATTERN.finditer(text))
    return _unique(candidates)


def _path_is_within(path_text: str, root_text: str) -> bool:
    normalized_path = os.path.normcase(os.path.abspath(path_text))
    normalized_root = os.path.normcase(os.path.abspath(root_text))
    try:
        return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
    except ValueError:
        return False


def _paths_outside_worktree(paths: Iterable[str]) -> List[str]:
    offenders: List[str] = []
    for path_text in paths:
        in_protected_root = any(
            _path_is_within(path_text, root) for root in PROTECTED_REPO_ROOTS
        )
        in_worktree = _path_is_within(path_text, WORKTREE_ROOT_TEXT)
        if in_protected_root and not in_worktree:
            offenders.append(path_text)
    return _unique(offenders)


def _read_json_file(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read evidence manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"evidence manifest is not valid JSON: {path} ({exc.msg})"
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError(f"evidence manifest top-level JSON value must be an object: {path}")
    return data


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fresh_manifest() -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "worktree": WORKTREE_ROOT_TEXT,
        "manifest_path": str(MANIFEST_PATH),
        "sessions": {},
    }


def _session_template(*, session_id: str, timestamp: str, transcript_path: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "transcript_path": transcript_path,
        "evidence": {bucket: [] for bucket in EVIDENCE_BUCKETS},
    }


def _ensure_manifest_session(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    manifest = _read_json_file(MANIFEST_PATH, default=_fresh_manifest())
    if manifest.get("version") != MANIFEST_VERSION:
        manifest = _fresh_manifest()
    manifest["worktree"] = WORKTREE_ROOT_TEXT
    manifest["manifest_path"] = str(MANIFEST_PATH)
    sessions = manifest.setdefault("sessions", {})
    session_id = str(payload.get("sessionId") or "unknown")
    timestamp = str(payload.get("timestamp") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        session = _session_template(
            session_id=session_id,
            timestamp=timestamp,
            transcript_path=transcript_path,
        )
        sessions[session_id] = session
    session.setdefault("session_id", session_id)
    session.setdefault("created_at", timestamp)
    session["updated_at"] = timestamp or str(session.get("updated_at") or "")
    if transcript_path:
        session["transcript_path"] = transcript_path
    evidence = session.setdefault("evidence", {})
    for bucket in EVIDENCE_BUCKETS:
        bucket_items = evidence.get(bucket)
        if not isinstance(bucket_items, list):
            evidence[bucket] = []
    return manifest, session, session_id


def _append_unique_entry(
    session: dict[str, Any],
    bucket: str,
    entry: dict[str, Any],
    *,
    unique_fields: Sequence[str],
) -> bool:
    evidence = session.setdefault("evidence", {})
    entries = evidence.setdefault(bucket, [])
    entry_key = tuple(str(entry.get(field) or "") for field in unique_fields)
    for existing in entries:
        existing_key = tuple(str(existing.get(field) or "") for field in unique_fields)
        if existing_key == entry_key:
            for key, value in entry.items():
                if value not in (None, "", []):
                    existing[key] = value
            return False
    entries.append(entry)
    return True


def _tool_text(payload: dict[str, Any]) -> str:
    parts = list(_collect_strings(payload.get("tool_input") or {}))
    parts.extend(_collect_strings(payload.get("tool_response") or {}))
    return "\n".join(parts)


def _lower_text(value: Any) -> str:
    return "\n".join(_collect_strings(value)).lower()


def _has_any_marker(corpus: str, markers: Iterable[str]) -> bool:
    return any(marker.lower() in corpus for marker in markers)


def _extract_paths(text: str, pattern: re.Pattern[str], group_name: str = "path") -> List[str]:
    return _unique(match.group(group_name) for match in pattern.finditer(text))


def _extract_report_paths(text: str) -> List[str]:
    paths = _extract_paths(text, REPORT_ENV_PATTERN)
    paths.extend(_extract_paths(text, REPORT_PATH_PATTERN))
    return _unique(paths)


def _extract_image_paths(text: str) -> List[str]:
    return _extract_paths(text, IMAGE_PATH_PATTERN)


def _extract_screenshot_dirs(text: str) -> List[str]:
    return _extract_paths(text, SCREENSHOT_DIR_ENV_PATTERN)


def _response_excerpt(payload: dict[str, Any], *, limit: int = 400) -> str:
    text = " ".join(_collect_strings(payload.get("tool_response") or {})).strip()
    return text[:limit]


def _tool_response_text(payload: dict[str, Any]) -> str:
    return "\n".join(_collect_strings(payload.get("tool_response") or {}))


def _is_zero_status(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip() == "0"
    return False


def _is_nonzero_status(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        try:
            return int(stripped) != 0
        except ValueError:
            return False
    return False


def _has_success_status(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in {"returncode", "return_code", "exit_code", "status_code"}:
                if _is_zero_status(nested):
                    return True
            if normalized_key in {"ok", "passed", "success"} and nested is True:
                return True
            if normalized_key == "status" and isinstance(nested, str):
                if nested.lower() in {"ok", "pass", "passed", "success", "succeeded"}:
                    return True
            if _has_success_status(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_has_success_status(item) for item in value)
    return False


def _has_failure_status(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in {"returncode", "return_code", "exit_code", "status_code"}:
                if _is_nonzero_status(nested):
                    return True
            if _has_failure_status(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_has_failure_status(item) for item in value)
    return False


def _has_success_output_marker(response_text: str) -> bool:
    lowered = response_text.lower()
    return _has_any_marker(lowered, REAL_TEST_MARKERS) or bool(
        re.search(r"\b\d+\s+passed\b", lowered)
    )


def _record_report_entries(session: dict[str, Any], payload: dict[str, Any], text: str) -> bool:
    updated = False
    for path_text in _extract_report_paths(text):
        updated |= _append_unique_entry(
            session,
            "reports",
            {
                "source": str(payload.get("tool_name") or ""),
                "timestamp": str(payload.get("timestamp") or ""),
                "path": path_text,
            },
            unique_fields=("path",),
        )
    return updated


def _record_real_test_entry(session: dict[str, Any], payload: dict[str, Any], text: str) -> bool:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        return False
    response_text = _tool_response_text(payload)
    if not TEST_COMMAND_HINT.search(command):
        return False
    if _has_failure_status(payload.get("tool_response") or {}):
        return False
    if not _has_success_status(payload.get("tool_response") or {}) and not _has_success_output_marker(
        response_text
    ):
        return False
    report_paths = _extract_report_paths(text)
    screenshot_dirs = _extract_screenshot_dirs(text)
    return _append_unique_entry(
        session,
        "real_tests",
        {
            "source": str(payload.get("tool_name") or ""),
            "timestamp": str(payload.get("timestamp") or ""),
            "command": command,
            "response_excerpt": _response_excerpt(payload),
            "report_paths": report_paths,
            "screenshot_dirs": screenshot_dirs,
            "verified": True,
        },
        unique_fields=("command",),
    )


def _record_screenshot_review_entry(session: dict[str, Any], payload: dict[str, Any]) -> bool:
    file_path = str((payload.get("tool_input") or {}).get("filePath") or "")
    if not file_path:
        return False
    return _append_unique_entry(
        session,
        "screenshots_reviewed",
        {
            "source": "view_image",
            "timestamp": str(payload.get("timestamp") or ""),
            "path": file_path,
        },
        unique_fields=("path",),
    )


def _record_shanghai_validation_entry(session: dict[str, Any], payload: dict[str, Any], text: str) -> bool:
    if not _has_any_marker(text.lower(), SHANGHAI_MARKERS):
        return False
    command = str((payload.get("tool_input") or {}).get("command") or "")
    file_path = str((payload.get("tool_input") or {}).get("filePath") or "")
    return _append_unique_entry(
        session,
        "shanghai_validation",
        {
            "source": str(payload.get("tool_name") or ""),
            "timestamp": str(payload.get("timestamp") or ""),
            "command": command,
            "path": file_path,
            "response_excerpt": _response_excerpt(payload),
            "note": "自动检测到上海产权交易所新页面相关验证痕迹。",
        },
        unique_fields=("source", "command", "path"),
    )


def _scan_reports_for_derived_evidence(session: dict[str, Any]) -> bool:
    updated = False
    evidence = session.setdefault("evidence", {})
    for report_entry in evidence.get("reports", []):
        report_path_text = str(report_entry.get("path") or "")
        if not report_path_text:
            continue
        report_path = Path(report_path_text)
        if not report_path.exists() or not report_path.is_file():
            continue
        try:
            report_text = report_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lowered = report_text.lower()
        if _has_any_marker(lowered, SHANGHAI_MARKERS):
            updated |= _append_unique_entry(
                session,
                "shanghai_validation",
                {
                    "source": "report_scan",
                    "timestamp": str(session.get("updated_at") or ""),
                    "path": report_path_text,
                    "note": "报告内容包含上海产权交易所新页面验证痕迹。",
                },
                unique_fields=("source", "path"),
            )
    return updated


def _record_posttool_use(payload: dict[str, Any]) -> None:
    manifest, session, _ = _ensure_manifest_session(payload)
    tool_name = str(payload.get("tool_name") or "")
    text = _tool_text(payload)
    updated = False
    if tool_name == "run_in_terminal":
        updated |= _record_report_entries(session, payload, text)
        updated |= _record_real_test_entry(session, payload, text)
        updated |= _record_shanghai_validation_entry(session, payload, text)
    elif tool_name == "view_image":
        updated |= _record_screenshot_review_entry(session, payload)
        updated |= _record_shanghai_validation_entry(session, payload, text)
    else:
        updated |= _record_shanghai_validation_entry(session, payload, text)
    updated |= _scan_reports_for_derived_evidence(session)
    if updated:
        _write_json_file(MANIFEST_PATH, manifest)


def _existing_path(path_text: str) -> bool:
    if not path_text:
        return False
    return Path(path_text).exists()


def _valid_real_tests(session: dict[str, Any]) -> List[dict[str, Any]]:
    valid: List[dict[str, Any]] = []
    for entry in session.setdefault("evidence", {}).get("real_tests", []):
        if entry.get("verified") is True:
            valid.append(entry)
    return valid


def _valid_reports(session: dict[str, Any]) -> List[dict[str, Any]]:
    return [
        entry
        for entry in session.setdefault("evidence", {}).get("reports", [])
        if _existing_path(str(entry.get("path") or ""))
    ]


def _valid_screenshots(session: dict[str, Any]) -> List[dict[str, Any]]:
    return [
        entry
        for entry in session.setdefault("evidence", {}).get("screenshots_reviewed", [])
        if _existing_path(str(entry.get("path") or ""))
    ]


def _valid_shanghai_validation(session: dict[str, Any]) -> List[dict[str, Any]]:
    valid: List[dict[str, Any]] = []
    for entry in session.setdefault("evidence", {}).get("shanghai_validation", []):
        if _existing_path(str(entry.get("path") or "")):
            valid.append(entry)
            continue
        if str(entry.get("command") or ""):
            valid.append(entry)
            continue
        report_paths = [path for path in entry.get("report_paths") or [] if _existing_path(str(path))]
        if report_paths:
            valid.append(entry)
            continue
        screenshot_paths = [
            path
            for path in entry.get("screenshot_paths") or []
            if _existing_path(str(path))
        ]
        if screenshot_paths:
            valid.append(entry)
            continue
        screenshot_dirs = [
            path
            for path in entry.get("screenshot_dirs") or []
            if _existing_path(str(path))
        ]
        if screenshot_dirs:
            valid.append(entry)
    return valid


def _record_session_start(payload: dict[str, Any]) -> str:
    manifest, _, session_id = _ensure_manifest_session(payload)
    _write_json_file(MANIFEST_PATH, manifest)
    return session_id


def _session_start_output(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = _record_session_start(payload)
    note_command = (
        "python3 scripts/worktree_delivery_guard.py note "
        f"--session-id {session_id} "
        '--kind shanghai_validation '
        '--note "已亲自查看截图并确认上海产权交易所新页面支持没有破坏式回归，结果可交付"'
    )
    context = (
        f"当前任务强制绑定到 worktree: {WORKTREE_ROOT_TEXT}。"
        "围绕上海产权交易所新页面支持，必须优先做真实测试，不允许只靠读代码下结论。"
        "结束前必须拿到真实结果证据、亲自查看截图并分析是否发生破坏式更新，"
        "确认它能像其他解析器一样产出结果，并补做你判断必要的回归测试。"
        "最终目标是交付可用产品，不是只给问题列表或测试汇报。"
        "改动保持高内聚低耦合，避免侵入式修改；需要提速时优先并行使用 subagents。"
        f"本会话的自动证据清单写入 {MANIFEST_PATH}；真实测试、报告路径、view_image 截图复核会自动记账。"
        f"完成人工结论后，执行这条命令补一条上海验证结论：{note_command}。"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


def _subagent_start_output() -> dict[str, Any]:
    context = (
        f"你在支持 {WORKTREE_NAME} worktree 的真实交付验证。"
        "优先返回能直接支撑真实测试、截图取证、回归范围判断的具体命令、路径和证据。"
        "不要停留在代码静态结论。"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": context,
        }
    }


def _pretool_use_output(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if tool_name == "run_in_terminal" and isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")
        for pattern, reason in DESTRUCTIVE_TERMINAL_RULES:
            if pattern.search(command):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
        if REPO_LOCAL_COMMAND_HINT.search(command) and not WORKTREE_TERMINAL_ENTRY.search(command):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        "当前任务必须显式切到当前 worktree 再执行仓库命令。"
                    ),
                    "additionalContext": (
                        f"请先使用 cd/pushd 进入 {WORKTREE_ROOT_TEXT}，避免误在主仓库执行。"
                    ),
                }
            }

    if tool_name in WRITE_SCOPED_TOOLS:
        outside_paths = _paths_outside_worktree(_extract_workspace_paths(tool_input))
        if outside_paths:
            preview = ", ".join(outside_paths[:3])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "当前任务限定在当前 worktree。",
                    "additionalContext": f"检测到目标路径超出 worktree: {preview}",
                }
            }

    return {}


def _posttool_use_output(payload: dict[str, Any]) -> dict[str, Any]:
    _record_posttool_use(payload)
    return {}


def _stop_output(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("stop_hook_active"):
        return {}

    manifest, session, _ = _ensure_manifest_session(payload)
    _scan_reports_for_derived_evidence(session)
    _write_json_file(MANIFEST_PATH, manifest)

    missing: List[str] = []
    if not _valid_real_tests(session):
        missing.append("补做真实测试，并让 hook 自动写入 evidence manifest")
    if not _valid_reports(session):
        missing.append("补充真实测试报告文件，并让 manifest 记录可落地的报告路径")
    if not _valid_screenshots(session):
        missing.append("补充截图证据，并通过 view_image 亲自查看截图")
    if not _valid_shanghai_validation(session):
        missing.append("补充上海产权交易所新页面验证结论，并写入 evidence manifest")

    if missing:
        return {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": (
                    "；".join(missing)
                    + f"。证据清单路径: {MANIFEST_PATH}。最终目标是交付可用产品，不是只提交问题或测试汇报。"
                ),
            }
        }

    return {}


def _unknown_hook_output(payload: dict[str, Any], event_name: str) -> dict[str, Any]:
    if payload.get("tool_name") or payload.get("tool_input") or payload.get("tool_response"):
        diagnostic_event = event_name or "Unknown"
        return {
            "hookSpecificOutput": {
                "hookEventName": diagnostic_event,
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "未知 hookEventName 携带工具执行载荷；为避免门禁失效，fail closed。"
                ),
            }
        }

    return {
        "hookSpecificOutput": {
            "hookEventName": event_name or "Unknown",
            "decision": "block",
            "reason": "未知 hookEventName；为避免门禁失效，fail closed。",
        }
    }


def _cli_note(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Append delivery evidence into the worktree manifest.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--kind", choices=EVIDENCE_BUCKETS, default="shanghai_validation")
    parser.add_argument("--note", required=True)
    parser.add_argument("--path", default="")
    args = parser.parse_args(list(argv))

    payload = {
        "sessionId": args.session_id,
        "timestamp": "manual-note",
        "transcript_path": "",
    }
    manifest, session, _ = _ensure_manifest_session(payload)
    _append_unique_entry(
        session,
        args.kind,
        {
            "source": "manual_note",
            "timestamp": "manual-note",
            "note": args.note,
            "path": args.path,
        },
        unique_fields=("source", "note", "path"),
    )
    _write_json_file(MANIFEST_PATH, manifest)
    sys.stdout.write(json.dumps({"ok": True, "manifest_path": str(MANIFEST_PATH)}, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args:
        command = args[0]
        if command == "note":
            return _cli_note(args[1:])
        raise SystemExit(f"unsupported command: {command}")

    try:
        payload = _load_payload()
    except HookPayloadError as exc:
        return _fail_payload_error(exc)
    event_name = str(payload.get("hookEventName") or "")
    if not event_name and not payload:
        return _emit({})
    if event_name == "SessionStart":
        return _emit(_session_start_output(payload))
    if event_name == "SubagentStart":
        return _emit(_subagent_start_output())
    if event_name == "PreToolUse":
        return _emit(_pretool_use_output(payload))
    if event_name == "PostToolUse":
        return _emit(_posttool_use_output(payload))
    if event_name == "Stop":
        return _emit(_stop_output(payload))
    return _emit(_unknown_hook_output(payload, event_name))


if __name__ == "__main__":
    raise SystemExit(main())
