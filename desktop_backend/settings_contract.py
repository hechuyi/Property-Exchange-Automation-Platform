"""HTTP request/response contract helpers for settings resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain.normalizers import parse_bool
from .record_scope import normalize_record_scope, record_scope_to_dict

SERVER_OWNED_SCOPE_FIELDS = ("effective_default_scope", "stale_default_metadata")
_MISSING = object()


def _dict(value: Any, *, field_name: str = "value") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _view_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _request_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _view_int(value: Any, *, field_name: str, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _view_positive_int(value: Any, default: int, *, field_name: str) -> int:
    parsed = _view_int(value, field_name=field_name, default=default)
    if parsed <= 0:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return parsed


def _parse_positive_request_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return parsed


def _optional_request_text(value: Any, *, field_name: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()


def _scope_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    source = _view_dict(value, field_name=field_name)
    return {
        "record_family": _text(source.get("record_family")),
        "business_id": _text(source.get("business_id")),
        "business_label": _text(source.get("business_label")),
        "exchange": _text(source.get("exchange")),
    }


def _reject_server_owned_fields(source: dict[str, Any]) -> None:
    for field_name in SERVER_OWNED_SCOPE_FIELDS:
        if field_name in source:
            raise ValueError(f"{field_name} is server-owned in basic settings update")
    default_scope = _dict(source.get("default_scope"), field_name="default_scope")
    for field_name in ("effective_scope", "effective_default_scope", "stale_resolution", "stale_default_metadata"):
        if field_name in default_scope:
            raise ValueError(f"{field_name} is server-owned in basic settings update")


def _normalize_stored_preference(value: Any) -> dict[str, Any]:
    source = _dict(value, field_name="stored_preference")
    scope = record_scope_to_dict(
        normalize_record_scope(
            {
                "record_family": source.get("record_family"),
                "business_id": source.get("business_id"),
                "business_label": source.get("business_label"),
                "exchange": source.get("exchange"),
            }
        )
    )
    return {
        key: scope[key]
        for key in ("record_family", "business_id", "business_label", "exchange")
        if key in scope
    }


def _stale_metadata(value: Any, *, field_name: str) -> dict[str, Any]:
    source = _view_dict(value, field_name=field_name)
    return {
        "is_stale": bool(source.get("is_stale")),
        "reason": _text(source.get("reason")),
        "hint": _text(source.get("hint")),
    }


def _first_present(source: Mapping[str, Any], *field_names: str) -> Any:
    for field_name in field_names:
        if field_name in source:
            return source[field_name]
    return _MISSING


def _view_default_scope_field(
    source: Mapping[str, Any],
    default_scope: Mapping[str, Any],
    *,
    field_name: str,
    legacy_field_names: tuple[str, ...],
) -> Any:
    if field_name in source:
        return source[field_name]
    legacy_value = _first_present(default_scope, *legacy_field_names)
    if legacy_value is not _MISSING:
        return legacy_value
    return None


def _new_default_scope_view(source: dict[str, Any]) -> dict[str, Any]:
    default_scope = _view_dict(source.get("default_scope"), field_name="default_scope")
    return {
        "effective_default_scope": _scope_dict(
            _view_default_scope_field(
                source,
                default_scope,
                field_name="effective_default_scope",
                legacy_field_names=("effective_scope", "effective_default_scope"),
            ),
            field_name="effective_default_scope",
        ),
        "stored_preference": _scope_dict(
            _view_default_scope_field(
                source,
                default_scope,
                field_name="stored_preference",
                legacy_field_names=("stored_preference",),
            ),
            field_name="stored_preference",
        ),
        "stale_default_metadata": _stale_metadata(
            _view_default_scope_field(
                source,
                default_scope,
                field_name="stale_default_metadata",
                legacy_field_names=("stale_resolution", "stale_default_metadata"),
            ),
            field_name="stale_default_metadata",
        ),
    }


def build_basic_settings_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _view_dict(payload, field_name="settings payload")
    view = _new_default_scope_view(source)
    view.update(
        {
            "default_exchange": _text(source.get("default_exchange"), "all"),
            "default_concurrency": _view_int(
                source.get("default_concurrency"),
                field_name="default_concurrency",
                default=0,
            ),
            "retention_count": _view_positive_int(
                source.get("retention_count"),
                20,
                field_name="retention_count",
            ),
            "paths": {
                "workspace_root": _text(source.get("workspace_root")),
                "archive_root": _text(source.get("archive_root")),
                "export_root": _text(source.get("export_root")),
            },
        },
    )
    return view


def build_advanced_settings_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _view_dict(payload, field_name="settings payload")
    view = _new_default_scope_view(source)
    view.update(
        {
            "processing": {
                "save_json": parse_bool(source.get("save_json"), field_name="save_json"),
                "postprocess_config": _text(source.get("postprocess_config")),
            },
            "ingest_paths": {
                "raw_manual_root": _text(source.get("raw_manual_root")),
                "raw_auto_root": _text(source.get("raw_auto_root")),
            },
            "runtime_paths": {
                "app_home": _text(source.get("app_home")),
                "streaming_db": _text(source.get("streaming_db")),
                "log_dir": _text(source.get("log_dir")),
                "cache_dir": _text(source.get("cache_dir")),
                "browser_cache_dir": _text(source.get("browser_cache_dir")),
                "archive_root": _text(source.get("archive_root")),
                "export_root": _text(source.get("export_root")),
            },
        }
    )
    return view


def normalize_basic_settings_update(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _request_dict(payload, field_name="settings payload")
    _reject_server_owned_fields(source)
    defaults = _dict(source.get("defaults"), field_name="defaults")
    paths = _dict(source.get("paths"), field_name="paths")
    default_scope = _dict(source.get("default_scope"), field_name="default_scope")
    explicit_stored_preference = "stored_preference" in source or "stored_preference" in default_scope
    stored_preference = (
        _request_dict(source.get("stored_preference"), field_name="stored_preference")
        if "stored_preference" in source
        else {}
    )
    if not stored_preference:
        stored_preference = (
            _request_dict(default_scope.get("stored_preference"), field_name="default_scope.stored_preference")
            if "stored_preference" in default_scope
            else {}
        )
    normalized = {
        "default_exchange": source.get("default_exchange", defaults.get("default_exchange")),
        "default_concurrency": source.get("default_concurrency", defaults.get("default_concurrency")),
        "retention_count": source.get("retention_count", defaults.get("retention_count")),
        "archive_root": source.get("archive_root", paths.get("archive_root")),
        "export_root": source.get("export_root", paths.get("export_root")),
    }
    if explicit_stored_preference:
        normalized["stored_preference"] = _normalize_stored_preference(stored_preference) if stored_preference else {}
    elif stored_preference:
        normalized["stored_preference"] = _normalize_stored_preference(stored_preference)
    return {
        key: _parse_positive_request_int(value, field_name="default_concurrency")
        if key == "default_concurrency"
        else _optional_request_text(value, field_name="default_exchange")
        if key == "default_exchange"
        else _optional_request_text(value, field_name=key)
        if key in {"archive_root", "export_root"}
        else value
        for key, value in normalized.items()
        if value is not None
    }


def normalize_advanced_settings_update(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _request_dict(payload, field_name="settings payload")
    processing = _dict(source.get("processing"), field_name="processing")
    ingest_paths = _dict(source.get("ingest_paths"), field_name="ingest_paths")
    normalized = {
        "save_json": processing.get("save_json", source.get("save_json")),
        "postprocess_config": processing.get("postprocess_config", source.get("postprocess_config")),
        "raw_manual_root": ingest_paths.get("raw_manual_root", source.get("raw_manual_root")),
        "raw_auto_root": ingest_paths.get("raw_auto_root", source.get("raw_auto_root")),
    }
    return {
        key: parse_bool(value, field_name="save_json")
        if key == "save_json"
        else _optional_request_text(value, field_name=key)
        if key in {"postprocess_config", "raw_manual_root", "raw_auto_root"}
        else value
        for key, value in normalized.items()
        if value is not None
    }
