"""Shared Playwright browser-runtime helpers."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Iterable, Sequence


def _candidate_browser_paths(browser_name: str) -> tuple[str, ...]:
    normalized = str(browser_name or "chromium").strip().lower() or "chromium"
    if normalized != "chromium":
        return ()

    home_dir = os.path.expanduser("~")
    mac_candidates = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        os.path.join(
            home_dir,
            "Applications",
            "Google Chrome.app",
            "Contents",
            "MacOS",
            "Google Chrome",
        ),
        os.path.join(
            home_dir,
            "Applications",
            "Chromium.app",
            "Contents",
            "MacOS",
            "Chromium",
        ),
    )
    windows_candidates = (
        os.path.join(
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
    )
    linux_commands = (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("microsoft-edge"),
    )

    if sys.platform == "darwin":
        return tuple(path for path in mac_candidates if path)
    if sys.platform.startswith("win"):
        return tuple(path for path in windows_candidates if path)
    return tuple(path for path in linux_commands if path)


def resolve_preferred_browser_executable(
    browser_name: str,
    *,
    playwright_executable_path: str = "",
) -> tuple[str, str]:
    preferred = os.path.abspath(str(playwright_executable_path or "").strip()) if playwright_executable_path else ""
    if preferred and os.path.isfile(preferred):
        return preferred, "playwright"

    env_override = os.path.abspath(str(os.environ.get("PEAP_BROWSER_EXECUTABLE_PATH") or "").strip())
    if env_override and os.path.isfile(env_override):
        return env_override, "env"

    for candidate in _candidate_browser_paths(browser_name):
        absolute = os.path.abspath(str(candidate or "").strip())
        if absolute and os.path.isfile(absolute):
            return absolute, "system"

    return preferred, "playwright" if preferred else ""


def _is_missing_executable_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return "executable doesn't exist" in message or "failed to launch" in message and "executable" in message


async def launch_chromium_browser(
    playwright: Any,
    *,
    args: Sequence[str] | None = None,
    **launch_kwargs: Any,
):
    browser_type = playwright.chromium
    resolved_args: Iterable[str] = args if args is not None else launch_kwargs.pop("args", ())
    primary_kwargs = dict(launch_kwargs)
    if resolved_args:
        primary_kwargs["args"] = list(resolved_args)

    try:
        return await browser_type.launch(**primary_kwargs)
    except Exception as exc:  # noqa: BLE001
        if "executable_path" in primary_kwargs or not _is_missing_executable_error(exc):
            raise

        fallback_path, source = resolve_preferred_browser_executable("chromium")
        if not fallback_path or source not in {"env", "system"}:
            raise

        retry_kwargs = dict(primary_kwargs)
        retry_kwargs["executable_path"] = fallback_path
        return await browser_type.launch(**retry_kwargs)


def launch_chromium_browser_sync(
    playwright: Any,
    *,
    args: Sequence[str] | None = None,
    **launch_kwargs: Any,
):
    browser_type = playwright.chromium
    resolved_args: Iterable[str] = args if args is not None else launch_kwargs.pop("args", ())
    primary_kwargs = dict(launch_kwargs)
    if resolved_args:
        primary_kwargs["args"] = list(resolved_args)

    try:
        return browser_type.launch(**primary_kwargs)
    except Exception as exc:  # noqa: BLE001
        if "executable_path" in primary_kwargs or not _is_missing_executable_error(exc):
            raise

        fallback_path, source = resolve_preferred_browser_executable("chromium")
        if not fallback_path or source not in {"env", "system"}:
            raise

        retry_kwargs = dict(primary_kwargs)
        retry_kwargs["executable_path"] = fallback_path
        return browser_type.launch(**retry_kwargs)


__all__ = [
    "launch_chromium_browser",
    "launch_chromium_browser_sync",
    "resolve_preferred_browser_executable",
]
