"""Local path picker and file-manager helpers for the desktop backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


class LocalPathError(RuntimeError):
    """Raised when a local path interaction fails."""


def _normalize_path(raw_value: object) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


def _run_command(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        message = str(completed.stderr or completed.stdout or "").strip()
        lowered = message.lower()
        if "user canceled" in lowered or "user cancelled" in lowered:
            return ""
        raise LocalPathError(message or f"command failed with exit code {completed.returncode}")
    return str(completed.stdout or "").strip()


def _existing_parent(path_value: str) -> str:
    current = _normalize_path(path_value)
    while current:
        if os.path.exists(current):
            return current
        parent = os.path.dirname(current)
        if not parent or parent == current:
            break
        current = parent
    return ""


def _default_location(current_path: str) -> str:
    normalized = _normalize_path(current_path)
    if not normalized:
        return ""
    if os.path.isdir(normalized):
        return normalized
    return os.path.dirname(normalized)


def _apple_script_string_literal(value: object) -> str:
    text = str(value or "")
    escaped = (
        text
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f"\"{escaped}\""


def _pick_local_path_macos(*, selection_kind: str, prompt: str, current_path: str) -> str:
    default_location = _default_location(current_path)
    default_clause = ""
    if default_location:
        default_clause = f" default location POSIX file {_apple_script_string_literal(default_location)}"
    if selection_kind == "directory":
        script = f"POSIX path of (choose folder with prompt {_apple_script_string_literal(prompt)}{default_clause})"
    else:
        script = f"POSIX path of (choose file with prompt {_apple_script_string_literal(prompt)}{default_clause})"
    return _run_command(["osascript", "-e", script])


def _windows_shell_executable() -> str:
    return str(shutil.which("powershell") or shutil.which("pwsh") or "powershell")


def _pick_local_path_windows(*, selection_kind: str, prompt: str, current_path: str) -> str:
    default_location = _default_location(current_path)
    if selection_kind == "directory":
        script = "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
                f"$dialog.Description = {json.dumps(prompt)}",
                f"$dialog.SelectedPath = {json.dumps(default_location)}",
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
                "  Write-Output $dialog.SelectedPath",
                "}",
            ]
        )
    else:
        script = "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
                f"$dialog.Title = {json.dumps(prompt)}",
                f"$dialog.InitialDirectory = {json.dumps(default_location)}",
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
                "  Write-Output $dialog.FileName",
                "}",
            ]
        )
    return _run_command([_windows_shell_executable(), "-NoProfile", "-STA", "-Command", script])


def _pick_local_path_linux(*, selection_kind: str, prompt: str, current_path: str) -> str:
    default_location = _default_location(current_path)
    zenity = shutil.which("zenity")
    if zenity:
        command = [zenity, "--file-selection", "--title", prompt]
        if selection_kind == "directory":
            command.append("--directory")
        if default_location:
            default_file = default_location if selection_kind == "directory" else os.path.join(default_location, "")
            command.extend(["--filename", default_file])
        return _run_command(command)
    kdialog = shutil.which("kdialog")
    if kdialog:
        if selection_kind == "directory":
            command = [kdialog, "--getexistingdirectory", default_location or os.getcwd(), "--title", prompt]
        else:
            command = [kdialog, "--getopenfilename", default_location or os.getcwd(), "--title", prompt]
        return _run_command(command)
    raise LocalPathError("当前系统未检测到可用的文件选择器")


def pick_local_path(*, selection_kind: str, current_path: str = "", prompt: str = "选择路径") -> str:
    normalized_kind = str(selection_kind or "directory").strip().lower()
    if normalized_kind not in {"directory", "file"}:
        raise LocalPathError(f"unsupported selection_kind: {selection_kind}")
    fake_path = str(os.environ.get("PEAP_FAKE_SELECTED_PATH") or "").strip()
    if os.environ.get("PEAP_FAKE_LOCAL_PATH_INTERACTIONS"):
        return _normalize_path(fake_path or current_path)
    if sys.platform == "darwin":
        return _pick_local_path_macos(selection_kind=normalized_kind, prompt=prompt, current_path=current_path)
    if sys.platform.startswith("win"):
        return _pick_local_path_windows(selection_kind=normalized_kind, prompt=prompt, current_path=current_path)
    return _pick_local_path_linux(selection_kind=normalized_kind, prompt=prompt, current_path=current_path)


def reveal_in_file_manager(path_value: str, *, reveal: bool = False) -> str:
    target = _normalize_path(path_value)
    if not target:
        raise LocalPathError("path is required")

    existing_target = _existing_parent(target)
    if not existing_target:
        raise LocalPathError(f"path not found: {target}")

    if os.environ.get("PEAP_FAKE_LOCAL_PATH_INTERACTIONS"):
        return target if os.path.exists(target) else existing_target

    if sys.platform == "darwin":
        if reveal and os.path.exists(target) and not os.path.isdir(target):
            _run_command(["open", "-R", target])
            return target
        opened_target = target if os.path.isdir(target) else existing_target
        _run_command(["open", opened_target])
        return opened_target

    if sys.platform.startswith("win"):
        if reveal and os.path.exists(target) and not os.path.isdir(target):
            _run_command(["explorer", f"/select,{target}"])
            return target
        opened_target = target if os.path.isdir(target) else existing_target
        _run_command(["explorer", opened_target])
        return opened_target

    opened_target = target if os.path.isdir(target) else existing_target
    _run_command(["xdg-open", opened_target])
    return opened_target
