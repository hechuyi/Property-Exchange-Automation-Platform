"""HTTP response contract helpers for runtime dependency resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_MISSING = object()


def _section(source: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = source.get(field, _MISSING)
    if value is _MISSING:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _bool_field(source: Mapping[str, Any], field: str, *, context: str) -> bool:
    value = source.get(field, _MISSING)
    if value is _MISSING:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{context}.{field} must be a boolean")
    return value


def _issues(value: Any, *, context: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{context}[{index}] must be an object")
        normalized.append(
            {
                "code": str(item.get("code") or "").strip(),
                "severity": str(item.get("severity") or "").strip(),
                "message": str(item.get("message") or "").strip(),
            }
        )
    return normalized


def _optional_issues(source: Mapping[str, Any], field: str, *, context: str) -> list[dict[str, Any]]:
    if field not in source:
        return []
    return _issues(source.get(field), context=f"{context}.{field}")


def build_runtime_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        source: dict[str, Any] = {}
    elif not isinstance(payload, Mapping):
        raise ValueError("runtime payload must be an object")
    else:
        source = dict(payload)
    browser = _section(source, "browser")
    install = _section(source, "install")
    readiness = _section(source, "readiness")
    return {
        "browser": {
            "installed": _bool_field(browser, "installed", context="browser"),
            "browser_name": str(browser.get("browser_name") or "").strip(),
            "installation_source": str(browser.get("installation_source") or "").strip(),
            "error": str(browser.get("error") or "").strip(),
        },
        "install": {
            "status": str(install.get("status") or "").strip(),
            "browser_name": str(install.get("browser_name") or "").strip(),
            "trigger": str(install.get("trigger") or "").strip(),
            "attempt_count": int(install.get("attempt_count") or 0),
            "started_at": str(install.get("started_at") or "").strip(),
            "updated_at": str(install.get("updated_at") or "").strip(),
            "completed_at": str(install.get("completed_at") or "").strip(),
            "message": str(install.get("message") or "").strip(),
            "running": _bool_field(install, "running", context="install"),
        },
        "readiness": {
            "ready": _bool_field(readiness, "ready", context="readiness"),
            "download_ready": _bool_field(readiness, "download_ready", context="readiness"),
            "browser_runtime_ready": _bool_field(readiness, "browser_runtime_ready", context="readiness"),
            "issues": _optional_issues(readiness, "issues", context="readiness"),
        },
    }
