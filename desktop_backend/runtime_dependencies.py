"""Runtime dependency helpers for the desktop backend."""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from typing import Any, Tuple

from peap.browser_runtime import resolve_preferred_browser_executable


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


def _bundled_runtime_is_read_only() -> bool:
    """Return whether the app is running from a packaged, immutable runtime.

    The offline macOS bundle keeps Playwright under ``Contents/Resources``.
    That tree may be signed and/or installed in ``/Applications`` where the
    user cannot write to it.  In that mode a browser install request must never
    invoke Playwright's downloader, since it would either fail on permissions
    or mutate the application bundle.
    """

    return os.environ.get("PEAP_BUNDLED_RUNTIME_READ_ONLY") == "1"


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


@contextmanager
def playwright_env(browser_cache_dir: str):
    with _playwright_env(browser_cache_dir):
        yield


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
            "installation_source": "",
            "error": "",
        }
        try:
            driver_executable, driver_cli = _driver_paths()
            result["driver_executable"] = driver_executable
            result["driver_cli"] = driver_cli
            with _playwright_env(self.browser_cache_dir):
                playwright_path = _browser_executable_path(result["browser_name"])
                if _bundled_runtime_is_read_only():
                    # In a packaged app, accepting a system Chrome fallback
                    # would mask a damaged/incomplete bundle and make behavior
                    # depend on whatever happens to be installed on the host.
                    executable_path = playwright_path
                    installation_source = "playwright" if playwright_path else ""
                else:
                    executable_path, installation_source = resolve_preferred_browser_executable(
                        result["browser_name"],
                        playwright_executable_path=playwright_path,
                    )
            result["executable_path"] = executable_path
            result["installation_source"] = installation_source
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
                "skipped": False,
            }
        )
        if _bundled_runtime_is_read_only():
            # A distributed app already contains the browser.  Treat an
            # already-present runtime as a successful no-op; if it is missing,
            # fail closed with an actionable error and never touch the bundle
            # or the network.
            result["skipped"] = True
            if result.get("installed"):
                result["returncode"] = 0
                result["message"] = "Bundled Chromium is already installed; no download is required"
            else:
                result["error"] = (
                    result.get("error")
                    or "Bundled Chromium is unavailable and the packaged runtime is read-only; reinstall the app"
                )
                result["message"] = result["error"]
            result["duration_sec"] = round(time.monotonic() - started_at, 3)
            return result
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
