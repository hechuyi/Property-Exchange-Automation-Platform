"""Runtime dependency and readiness service."""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import asdict
from typing import Any, Dict

from peap.product_profile import get_product_profile

from ..error_codes import ERROR_BROWSER_RUNTIME_MISSING
from ..repositories import PipelineRepository
from ..request_contract import normalize_runtime_install_request
from ..runtime_dependencies import RuntimeDependencyManager


def _timestamp_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _installed_flag(payload: Dict[str, Any], *, context: str) -> bool:
    installed = payload.get("installed")
    if not isinstance(installed, bool):
        raise ValueError(f"{context}.installed must be a boolean")
    return installed


def _source_ids(payload: Dict[str, Any], *, context: str) -> list[str]:
    if "source_ids" not in payload:
        return []
    source_ids = payload["source_ids"]
    if not isinstance(source_ids, (list, tuple)):
        raise ValueError(f"{context}.source_ids must be a list or tuple")
    return list(source_ids)


class RuntimeService:
    """Own browser runtime state, install lifecycle, and readiness payloads."""

    # Browser probing starts a Playwright driver process. Overview and SSE
    # requests are frequent, so briefly reuse the last probe result.
    BROWSER_STATUS_CACHE_TTL_SEC = 5.0

    def __init__(
        self,
        *,
        config_obj: object,
        repository: PipelineRepository | None = None,
        store: object | None = None,
        runtime_dependencies: RuntimeDependencyManager,
    ) -> None:
        self.config = config_obj
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository
        self.runtime_dependencies = runtime_dependencies
        self._lock = threading.Lock()
        self._browser_status_lock = threading.Lock()
        self._browser_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._runtime_install_thread: threading.Thread | None = None
        self._runtime_install_state: dict[str, Any] = self._build_runtime_install_state()

    def _build_runtime_install_state(self, **overrides: Any) -> Dict[str, Any]:
        state = {
            "status": "idle",
            "browser_name": "chromium",
            "trigger": "",
            "attempt_count": 0,
            "started_at": "",
            "updated_at": "",
            "completed_at": "",
            "message": "",
            "last_result": {},
            "running": False,
        }
        state.update(overrides)
        return state

    def get_runtime_install_state(self) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._runtime_install_state)
        last_result = state.get("last_result")
        if isinstance(last_result, dict):
            state["last_result"] = dict(last_result)
        return state

    def _cache_browser_runtime_status_locked(
        self,
        browser_name: str,
        status: Dict[str, Any],
    ) -> Dict[str, Any]:
        _installed_flag(status, context="browser_runtime_cache")
        cached = dict(status)
        self._browser_status_cache[str(browser_name or "chromium")] = (time.monotonic(), cached)
        return dict(cached)

    def _cache_browser_runtime_status(
        self,
        browser_name: str,
        status: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._browser_status_lock:
            return self._cache_browser_runtime_status_locked(browser_name, status)

    def _invalidate_browser_runtime_status(self, browser_name: str | None = None) -> None:
        with self._browser_status_lock:
            if browser_name is None:
                self._browser_status_cache.clear()
            else:
                self._browser_status_cache.pop(str(browser_name or "chromium"), None)

    def get_browser_runtime_status(
        self,
        *,
        browser_name: str = "chromium",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        normalized_browser_name = str(browser_name or "chromium").strip() or "chromium"
        with self._browser_status_lock:
            now = time.monotonic()
            cached = self._browser_status_cache.get(normalized_browser_name)
            if (
                not force_refresh
                and cached is not None
                and now - cached[0] < self.BROWSER_STATUS_CACHE_TTL_SEC
            ):
                status = dict(cached[1])
                _installed_flag(status, context="browser_runtime")
                return status
            # Serialize probes so concurrent SSE/overview requests cannot
            # spawn duplicate Playwright drivers.
            status = dict(
                self.runtime_dependencies.get_browser_runtime_status(
                    browser_name=normalized_browser_name,
                )
            )
            _installed_flag(status, context="browser_runtime")
            self._cache_browser_runtime_status_locked(normalized_browser_name, status)
        return status

    def build_product_readiness(self, *, browser_runtime: Dict[str, Any] | None = None) -> Dict[str, Any]:
        browser = dict(browser_runtime or self.get_browser_runtime_status())
        browser_installed = _installed_flag(browser, context="browser_runtime")
        browser_error = str(browser.get("error") or "").strip()
        issues: list[Dict[str, Any]] = []
        if not browser_installed:
            issues.append(
                {
                    "code": ERROR_BROWSER_RUNTIME_MISSING,
                    "severity": "error" if browser_error else "warning",
                    "message": browser_error or "Chromium runtime is not installed",
                }
            )
        return {
            "ready": browser_installed,
            "download_ready": browser_installed,
            "browser_runtime_ready": browser_installed,
            "issues": issues,
        }

    def product_profile_payload(self) -> Dict[str, Any]:
        payload = asdict(get_product_profile())
        payload["source_ids"] = _source_ids(payload, context="product_profile")
        return payload

    def runtime_payload(self) -> Dict[str, Any]:
        browser_runtime = self.get_browser_runtime_status()
        return {
            "browser": browser_runtime,
            "install": self.get_runtime_install_state(),
            "readiness": self.build_product_readiness(browser_runtime=browser_runtime),
        }

    def launch_browser_runtime_install(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized_payload = normalize_runtime_install_request(payload)
        browser_name = normalized_payload["browser_name"]
        trigger = normalized_payload["trigger"]
        with self._lock:
            install_running = self._runtime_install_state.get("status") == "running"
        if install_running:
            return self.get_runtime_install_state()

        browser_runtime = self.get_browser_runtime_status(
            browser_name=browser_name,
            force_refresh=True,
        )
        if _installed_flag(browser_runtime, context="browser_runtime"):
            ready_state = self._build_runtime_install_state(
                status="succeeded",
                browser_name=browser_name,
                trigger=trigger,
                updated_at=_timestamp_now(),
                completed_at=_timestamp_now(),
                message="Chromium already installed",
                last_result=browser_runtime,
                running=False,
            )
            with self._lock:
                ready_state["attempt_count"] = int(self._runtime_install_state.get("attempt_count", 0))
                self._runtime_install_state = ready_state
            return self.get_runtime_install_state()

        with self._lock:
            if self._runtime_install_state.get("status") == "running":
                state = dict(self._runtime_install_state)
                last_result = state.get("last_result")
                if isinstance(last_result, dict):
                    state["last_result"] = dict(last_result)
                return state
            attempt_count = int(self._runtime_install_state.get("attempt_count", 0)) + 1
            self._runtime_install_state = self._build_runtime_install_state(
                status="running",
                browser_name=browser_name,
                trigger=trigger,
                attempt_count=attempt_count,
                started_at=_timestamp_now(),
                updated_at=_timestamp_now(),
                message=f"Installing {browser_name}",
                last_result={},
                running=True,
            )
        self._invalidate_browser_runtime_status(browser_name)

        def _run_install() -> None:
            try:
                result = self.runtime_dependencies.install_browser_runtime(browser_name=browser_name)
                installed = _installed_flag(result, context="browser_install_result")
                status = "succeeded" if installed else "failed"
                message = "Chromium install completed" if installed else str(
                    result.get("error") or "Chromium install failed"
                )
                audit_payload = {
                    "browser_name": browser_name,
                    "trigger": trigger,
                    "installed": installed,
                    "returncode": result.get("returncode"),
                    "error": result.get("error", ""),
                }
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                message = str(exc)
                result = {
                    "browser_name": browser_name,
                    "installed": False,
                    "error": str(exc),
                }
                audit_payload = {
                    "browser_name": browser_name,
                    "trigger": trigger,
                    "installed": False,
                    "returncode": None,
                    "error": str(exc),
                }

            with self._lock:
                attempt_count = int(self._runtime_install_state.get("attempt_count", 0))
                self._runtime_install_state = self._build_runtime_install_state(
                    status=status,
                    browser_name=browser_name,
                    trigger=trigger,
                    attempt_count=attempt_count,
                    started_at=str(self._runtime_install_state.get("started_at") or ""),
                    updated_at=_timestamp_now(),
                    completed_at=_timestamp_now(),
                    message=message,
                    last_result=result,
                    running=False,
                )
                self._runtime_install_thread = None
            self._cache_browser_runtime_status(browser_name, result)
            self.repository.add_audit_entry("browser_runtime_install_async", audit_payload)

        thread = threading.Thread(
            target=_run_install,
            name=f"peap-browser-install-{int(time.time())}",
            daemon=True,
        )
        with self._lock:
            self._runtime_install_thread = thread
        thread.start()
        return self.get_runtime_install_state()

    def install_browser_runtime(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        browser_name = normalize_runtime_install_request(payload)["browser_name"]
        self._invalidate_browser_runtime_status(browser_name)
        result = self.runtime_dependencies.install_browser_runtime(browser_name=browser_name)
        installed = _installed_flag(result, context="browser_install_result")
        self._cache_browser_runtime_status(browser_name, result)
        self.repository.add_audit_entry(
            "browser_runtime_install",
            {
                "browser_name": browser_name,
                "installed": installed,
                "returncode": result.get("returncode"),
                "error": result.get("error", ""),
                "browser_cache_dir": result.get("browser_cache_dir", ""),
            },
        )
        enriched = dict(result)
        enriched["product_readiness"] = self.build_product_readiness(browser_runtime=result)
        with self._lock:
            attempt_count = int(self._runtime_install_state.get("attempt_count", 0))
            self._runtime_install_state = self._build_runtime_install_state(
                status="succeeded" if installed else "failed",
                browser_name=browser_name,
                trigger="sync",
                attempt_count=attempt_count,
                updated_at=_timestamp_now(),
                completed_at=_timestamp_now(),
                message="Chromium install completed"
                if installed
                else str(enriched.get("error") or "Chromium install failed"),
                last_result=enriched,
                running=False,
            )
        return enriched
