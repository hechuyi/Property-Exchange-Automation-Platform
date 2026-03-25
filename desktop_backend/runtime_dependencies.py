"""Runtime dependency helpers for the desktop backend."""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from typing import Any, Tuple


def _trim_output(raw_value: str, *, limit: int = 4000) -> str:
    text = str(raw_value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _driver_paths() -> Tuple[str, str]:
    from playwright._impl._driver import compute_driver_executable

    driver_executable, driver_cli = compute_driver_executable()
    return str(driver_executable), str(driver_cli)


def _driver_env() -> dict[str, str]:
    from playwright._impl._driver import get_driver_env

    return {str(key): str(value) for key, value in get_driver_env().items()}


@contextmanager
def _playwright_env(browser_cache_dir: str):
    cache_dir = os.path.abspath(str(browser_cache_dir or "").strip()) if browser_cache_dir else ""
    previous_pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    previous_peap = os.environ.get("PEAP_PLAYWRIGHT_BROWSERS_PATH")
    try:
        if cache_dir:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = cache_dir
            os.environ["PEAP_PLAYWRIGHT_BROWSERS_PATH"] = cache_dir
        yield
    finally:
        if previous_pw is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous_pw
        if previous_peap is None:
            os.environ.pop("PEAP_PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PEAP_PLAYWRIGHT_BROWSERS_PATH"] = previous_peap


def _browser_executable_path(browser_name: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        raw_path = getattr(browser_type, "executable_path", "")
        resolved = raw_path() if callable(raw_path) else raw_path
        return os.path.abspath(str(resolved or ""))


class RuntimeDependencyManager:
    """Inspect and provision local runtime dependencies used by the desktop app."""

    def __init__(self, *, browser_cache_dir: str = "") -> None:
        self.browser_cache_dir = os.path.abspath(str(browser_cache_dir or "")) if browser_cache_dir else ""

    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, Any]:
        result = {
            "browser_name": str(browser_name or "chromium"),
            "browser_cache_dir": self.browser_cache_dir,
            "driver_executable": "",
            "driver_cli": "",
            "executable_path": "",
            "installed": False,
            "error": "",
        }
        try:
            driver_executable, driver_cli = _driver_paths()
            result["driver_executable"] = driver_executable
            result["driver_cli"] = driver_cli
            with _playwright_env(self.browser_cache_dir):
                executable_path = _browser_executable_path(result["browser_name"])
            result["executable_path"] = executable_path
            result["installed"] = bool(executable_path) and os.path.isfile(executable_path)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
        return result

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, Any]:
        started_at = time.monotonic()
        result = self.get_browser_runtime_status(browser_name=browser_name)
        result.update(
            {
                "action": "install_browser_runtime",
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "duration_sec": 0.0,
            }
        )
        try:
            driver_executable, driver_cli = _driver_paths()
            command = [driver_executable, driver_cli, "install", result["browser_name"]]
            env = _driver_env()
            if self.browser_cache_dir:
                env["PLAYWRIGHT_BROWSERS_PATH"] = self.browser_cache_dir
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            result["driver_executable"] = driver_executable
            result["driver_cli"] = driver_cli
            result["returncode"] = int(completed.returncode)
            result["stdout"] = _trim_output(completed.stdout)
            result["stderr"] = _trim_output(completed.stderr)
            refreshed = self.get_browser_runtime_status(browser_name=browser_name)
            result.update(refreshed)
            if completed.returncode != 0 and not result["error"]:
                result["error"] = result["stderr"] or f"playwright install exited with {completed.returncode}"
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
        result["duration_sec"] = round(time.monotonic() - started_at, 3)
        return result
