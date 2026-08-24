"""Settings service — read and write basic / advanced settings.

Extracted from AppService to isolate the configuration concern.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from peap.surface_contract import SURFACE_EXPORT, SURFACE_ONE_CLICK, scope_supported_for_surface
from peap_core.business_catalog import get_business_descriptor
from peap_core.family_catalog import get_family_descriptor, list_family_descriptors

from ..domain.normalizers import (
    normalize_local_path,
    parse_bool,
    parse_local_path,
    parse_positive_int,
    resolve_directory_setting,
)
from ..product_errors import UserInputError
from ..record_scope import (
    RecordScope,
    RecordScopeValidationError,
    normalize_record_scope,
    resolve_scope_business_ids,
)
from ..repositories import PipelineRepository

_DEFAULT_SCOPE_SURFACES = (SURFACE_ONE_CLICK, SURFACE_EXPORT)
_SETTINGS_DECODE_ERROR_KEY = "__peap_settings_decode_error__"
_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY = "_deal_archive_root_explicit"


def _normalize_retention_count(value: Any, *, default: int = 20) -> int:
    try:
        count = int(value)
    except Exception as exc:
        raise UserInputError("retention_count must be a positive integer") from exc
    if count < 1:
        raise UserInputError("retention_count must be a positive integer")
    return count


def _resolve_postprocess_config_path(config_obj: object, raw_value: Any) -> str:
    if raw_value is not None and raw_value != "" and not isinstance(raw_value, str):
        raise UserInputError("postprocess_config must be a string")
    text = str(raw_value or "").strip()
    if not text:
        return _default_postprocess_config_path(config_obj)

    expanded = os.path.expanduser(text)
    candidate_roots = [
        os.path.abspath(str(getattr(config_obj, "PROJECT_ROOT", "") or "")),
        os.path.abspath(str(getattr(config_obj, "APP_HOME", "") or "")),
        os.path.abspath(str(os.getcwd())),
    ]
    candidates = [expanded, os.path.abspath(expanded)]
    for root in candidate_roots:
        if root:
            candidates.append(os.path.abspath(os.path.join(root, text)))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise UserInputError(f"postprocess_config not found: {text}")


def _default_postprocess_config_path(config_obj: object) -> str:
    candidate_roots = [
        os.path.abspath(str(getattr(config_obj, "PROJECT_ROOT", "") or "")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    ]
    for root in candidate_roots:
        if not root:
            continue
        candidate = os.path.join(root, "peap_postprocess", "ppe_config", "postprocess_external_template.json")
        if os.path.isfile(candidate):
            return candidate
    return ""


def _basic_path_setting(
    merged: Dict[str, Any],
    source: Dict[str, Any],
    *,
    field_name: str,
    default: str,
) -> str:
    if field_name not in source:
        return normalize_local_path(merged.get(field_name)) or default
    path = parse_local_path(source.get(field_name), field_name=field_name)
    if not path:
        raise ValueError(f"{field_name} is empty")
    return path


def _derived_deal_archive_root(archive_root: str) -> str:
    return os.path.join(str(archive_root), "deal")


def _same_local_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))

_LEGACY_DEFAULT_SCOPE_ALIAS_KEYS = ("default_project_type",)


def _coerce_scope_mapping(raw_value: Any) -> dict[str, Any]:
    return dict(raw_value) if isinstance(raw_value, dict) else dict(raw_value) if hasattr(raw_value, "items") else {}


def _stored_preference_request_mapping(raw_value: Any, *, field_name: str) -> dict[str, Any]:
    if raw_value is None:
        return {}
    if not hasattr(raw_value, "items"):
        raise ValueError(f"{field_name} must be an object")
    return dict(raw_value)


def _payload_object(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return dict(payload)


def _drop_legacy_scope_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    for key in _LEGACY_DEFAULT_SCOPE_ALIAS_KEYS:
        normalized.pop(key, None)
    return normalized


def _stored_preference_from_payload(payload: Dict[str, Any] | None, *, reject_non_object: bool = False) -> dict[str, Any]:
    source = {} if payload is None else _payload_object(payload)
    stored_preference = (
        _stored_preference_request_mapping(source.get("stored_preference"), field_name="stored_preference")
        if reject_non_object
        else _coerce_scope_mapping(source.get("stored_preference"))
        if "stored_preference" in source
        else {}
    )
    if not stored_preference and source.get("default_scope") is not None:
        default_scope = _coerce_scope_mapping(source.get("default_scope"))
        stored_preference = (
            _stored_preference_request_mapping(
                default_scope.get("stored_preference"),
                field_name="default_scope.stored_preference",
            )
            if reject_non_object
            else _coerce_scope_mapping(default_scope.get("stored_preference"))
            if "stored_preference" in default_scope
            else {}
        )
    if not stored_preference:
        stored_preference = {
            key: source.get(key)
            for key in ("record_family", "business_id", "business_label", "exchange")
            if key in source
        }
    if not stored_preference:
        return {}
    return {
        key: value
        for key, value in stored_preference.items()
        if key in {"record_family", "business_id", "business_label", "exchange"}
    }


def _scope_reason_code(exc: Exception) -> str:
    if isinstance(exc, RecordScopeValidationError):
        return exc.reason_code
    return str(exc or "").strip() or "invalid_scope"


def _stale_scope_hint(reason_code: str) -> str:
    if reason_code == "settings_payload_corrupt":
        return "repair or reset settings from a valid runtime configuration"
    if reason_code == "invalid_exchange":
        return "reselect a supported exchange in settings"
    if reason_code == "unknown_record_family":
        return "reselect a supported business family in settings"
    if reason_code == "unsupported_default_scope":
        return "reselect a supported business and exchange in settings"
    return "reselect a supported business in settings"


def _validate_default_scope_support(normalized_scope: RecordScope) -> None:
    business_ids = resolve_scope_business_ids(normalized_scope)
    for surface in _DEFAULT_SCOPE_SURFACES:
        if scope_supported_for_surface(
            record_family=normalized_scope.record_family,
            business_ids=business_ids,
            exchange=normalized_scope.exchange,
            surface=surface,
        ):
            continue
        raise RecordScopeValidationError(
            "unsupported_default_scope",
            (
                "stored_preference is not supported across the shared default "
                f"surfaces: {normalized_scope.record_family}/{normalized_scope.business_id}/{normalized_scope.exchange}"
            ),
        )


def _resolve_default_scope_state(payload: Dict[str, Any] | None, *, strict: bool = False) -> dict[str, Any]:
    stored_preference = _stored_preference_from_payload(payload)
    stale_default_metadata = {
        "is_stale": False,
        "reason": "",
        "hint": "",
    }
    if not stored_preference:
        return {
            "stored_preference": {},
            "effective_default_scope": {},
            "stale_default_metadata": stale_default_metadata,
        }
    try:
        normalized_scope = normalize_record_scope(stored_preference)
        _validate_default_scope_support(normalized_scope)
        family_descriptor = get_family_descriptor(normalized_scope.record_family)
        business_descriptor = None
        if normalized_scope.business_id not in {"", "all"}:
            business_descriptor = get_business_descriptor(
                normalized_scope.business_id,
                family_id=family_descriptor.family_id,
            )
        effective_default_scope = {
            "record_family": family_descriptor.family_id,
            "business_id": normalized_scope.business_id,
            "business_label": normalized_scope.business_label
            or (business_descriptor.canonical_label if business_descriptor is not None else ""),
            "exchange": str(normalized_scope.exchange or "all"),
        }
    except (KeyError, ValueError) as exc:
        reason_code = _scope_reason_code(exc)
        if strict:
            raise UserInputError(
                "invalid stored default-preference combination",
                details={
                    "stored_preference": dict(stored_preference),
                    "reason": reason_code,
                },
            ) from exc
        stale_default_metadata = {
            "is_stale": True,
            "reason": reason_code,
            "hint": _stale_scope_hint(reason_code),
        }
        effective_default_scope = {}
    if effective_default_scope and not effective_default_scope.get("business_label") and effective_default_scope.get("business_id") not in {"", "all"}:
        try:
            effective_default_scope["business_label"] = get_business_descriptor(
                str(effective_default_scope["business_id"]),
                family_id=str(effective_default_scope["record_family"]),
            ).canonical_label
        except KeyError:
            effective_default_scope["business_label"] = ""
    return {
        "stored_preference": stored_preference,
        "effective_default_scope": effective_default_scope,
        "stale_default_metadata": stale_default_metadata,
    }


def _default_stored_preference() -> dict[str, str]:
    families = [
        str(family.family_id or "").strip()
        for family in list_family_descriptors()
        if str(family.family_id or "").strip()
    ]
    default_family = families[0] if families else "listing"
    return {
        "record_family": default_family,
        "business_id": "all",
        "exchange": "all",
    }


def _scope_from_payload(payload: Dict[str, Any] | None) -> dict[str, Any]:
    return _resolve_default_scope_state(payload, strict=False)


def _corrupt_settings_scope_state() -> dict[str, Any]:
    return {
        "stored_preference": {},
        "effective_default_scope": {},
        "stale_default_metadata": {
            "is_stale": True,
            "reason": "settings_payload_corrupt",
            "hint": _stale_scope_hint("settings_payload_corrupt"),
        },
    }


def _has_corrupt_settings_scope_marker(payload: Dict[str, Any]) -> bool:
    metadata = payload.get("stale_default_metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("is_stale")) and str(metadata.get("reason") or "").strip() == "settings_payload_corrupt"


def _has_corrupt_stored_preference_shape(payload: Dict[str, Any]) -> bool:
    if "stored_preference" in payload:
        stored_preference = payload.get("stored_preference")
        if stored_preference is not None and not hasattr(stored_preference, "items"):
            return True
    if "default_scope" not in payload:
        return False
    default_scope = payload.get("default_scope")
    if default_scope is None or not hasattr(default_scope, "items"):
        return False
    if "stored_preference" not in default_scope:
        return False
    stored_preference = default_scope.get("stored_preference")
    return stored_preference is not None and not hasattr(stored_preference, "items")


def _candidate_stored_preference_for_write(
    current: Dict[str, Any],
    request: Dict[str, Any],
) -> dict[str, Any]:
    candidate = _stored_preference_from_payload(current)
    if any(key in request for key in ("stored_preference", "default_scope")):
        return _stored_preference_from_payload(request, reject_non_object=True)

    patch: dict[str, Any] = {}
    if "record_family" in request:
        patch["record_family"] = request["record_family"]
    if "default_business_id" in request:
        patch["business_id"] = request["default_business_id"]
    if "business_label" in request:
        patch["business_label"] = request["business_label"]
    if "exchange" in request:
        patch["exchange"] = request["exchange"]
    if not patch:
        return candidate
    if not candidate:
        return {}
    candidate.update(patch)
    return candidate


class SettingsService:
    """Encapsulates reading and writing application settings."""

    def __init__(
        self,
        *,
        config_obj,
        repository: PipelineRepository | None = None,
        store=None,
        app_home: str,
        default_archive_root: str,
        default_export_root: str,
    ):
        self.config = config_obj
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository
        self.app_home = app_home
        self.default_archive_root = default_archive_root
        self.default_export_root = default_export_root
        self.default_postprocess_config = _default_postprocess_config_path(config_obj)

    def _basic_settings_key(self) -> str:
        return "app.settings.basic"

    def _advanced_settings_key(self) -> str:
        return "app.settings.advanced"

    def _load_setting_with_decode_status(
        self,
        key: str,
        *,
        default: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        value = dict(self.repository.get_setting(key, default=default))
        decode_error = bool(value.pop(_SETTINGS_DECODE_ERROR_KEY, ""))
        return value, decode_error

    def effective_postprocess_config_path(self) -> str:
        advanced = self.get_advanced_settings()
        return str(advanced.get("postprocess_config") or self.default_postprocess_config or "").strip()

    def load_effective_rules_config(self) -> Dict[str, Any]:
        from peap.rules_config import load_effective_rules_config

        return load_effective_rules_config(self.effective_postprocess_config_path())

    def get_basic_settings(self) -> Dict[str, Any]:
        defaults = {
            "default_exchange": "all",
            "default_concurrency": int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            "archive_root": self.default_archive_root,
            "deal_archive_root": os.path.join(self.default_archive_root, "deal"),
            "export_root": self.default_export_root,
            "retention_count": 20,
            "workspace_root": self.app_home,
            "stored_preference": _default_stored_preference(),
        }
        value, decode_error = self._load_setting_with_decode_status(self._basic_settings_key(), default=defaults)
        merged = dict(defaults)
        merged.update(value)
        scope_state = (
            _corrupt_settings_scope_state()
            if decode_error or _has_corrupt_settings_scope_marker(value) or _has_corrupt_stored_preference_shape(value)
            else _scope_from_payload(merged)
        )
        merged.update(scope_state)
        merged = _drop_legacy_scope_aliases(merged)
        merged["archive_root"] = _basic_path_setting(
            merged,
            value,
            field_name="archive_root",
            default=self.default_archive_root,
        )
        stored_deal_archive_root = _basic_path_setting(
            merged,
            value,
            field_name="deal_archive_root",
            default=_derived_deal_archive_root(merged["archive_root"]),
        )
        explicit_marker = value.get(_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY)
        if explicit_marker is not None and not isinstance(explicit_marker, bool):
            raise ValueError(f"{_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY} must be a boolean")
        deal_archive_root_is_explicit = (
            explicit_marker
            if isinstance(explicit_marker, bool)
            else "deal_archive_root" in value
            and not _same_local_path(stored_deal_archive_root, _derived_deal_archive_root(merged["archive_root"]))
            and not _same_local_path(stored_deal_archive_root, _derived_deal_archive_root(self.default_archive_root))
        )
        merged["deal_archive_root"] = (
            stored_deal_archive_root
            if deal_archive_root_is_explicit
            else _derived_deal_archive_root(merged["archive_root"])
        )
        merged["export_root"] = _basic_path_setting(
            merged,
            value,
            field_name="export_root",
            default=self.default_export_root,
        )
        merged["retention_count"] = _normalize_retention_count(merged.get("retention_count", 20))
        merged["workspace_root"] = self.app_home
        merged.pop(_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY, None)
        return merged

    def get_advanced_settings(self) -> Dict[str, Any]:
        defaults = {
            "app_home": self.app_home,
            "streaming_db": str(getattr(self.config, "STREAMING_DB_PATH", "")),
            "save_json": False,
            "postprocess_config": self.default_postprocess_config,
            "log_dir": str(self.config.LOG_DIR),
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": self.default_archive_root,
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "archive_root": self.default_archive_root,
            "export_root": self.default_export_root,
            "retention_count": 20,
        }
        value, _ = self._load_setting_with_decode_status(self._advanced_settings_key(), default=defaults)
        merged = dict(defaults)
        merged.update(value)
        scope_state = _scope_from_payload(merged)
        merged.update(scope_state)
        merged = _drop_legacy_scope_aliases(merged)
        basic = self.get_basic_settings()
        merged["app_home"] = self.app_home
        merged["streaming_db"] = str(getattr(self.config, "STREAMING_DB_PATH", ""))
        if not str(merged.get("postprocess_config") or "").strip():
            merged["postprocess_config"] = self.default_postprocess_config
        merged["log_dir"] = str(self.config.LOG_DIR)
        merged["cache_dir"] = str(getattr(self.config, "CACHE_DIR", ""))
        merged["raw_auto_root"] = basic["archive_root"]
        merged["raw_manual_root"] = normalize_local_path(merged.get("raw_manual_root")) or str(getattr(self.config, "HTML_FOLDER", ""))
        merged["browser_cache_dir"] = str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))
        merged["archive_root"] = basic["archive_root"]
        merged["export_root"] = basic["export_root"]
        merged["retention_count"] = int(basic.get("retention_count") or 20)
        return merged

    def set_basic_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            "default_exchange": "all",
            "default_concurrency": int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            "archive_root": self.default_archive_root,
            "deal_archive_root": _derived_deal_archive_root(self.default_archive_root),
            _DEAL_ARCHIVE_ROOT_EXPLICIT_KEY: False,
            "export_root": self.default_export_root,
            "retention_count": 20,
            "workspace_root": self.app_home,
        }
        current, decode_error = self._load_setting_with_decode_status(self._basic_settings_key(), default=defaults)
        if decode_error or _has_corrupt_settings_scope_marker(current):
            raise UserInputError("basic settings are corrupt; repair or reset settings before updating")
        value = dict(defaults)
        value.update(current)
        request = _payload_object(payload)
        candidate_stored_preference = _candidate_stored_preference_for_write(current, request)
        value.update(_resolve_default_scope_state({"stored_preference": candidate_stored_preference}, strict=True))
        if "default_exchange" in request:
            raw_default_exchange = request["default_exchange"]
            if raw_default_exchange is not None and raw_default_exchange != "" and not isinstance(raw_default_exchange, str):
                raise ValueError("default_exchange must be a string")
            value["default_exchange"] = str(raw_default_exchange or "all").strip() or "all"
        if "default_concurrency" in request:
            value["default_concurrency"] = parse_positive_int(
                request.get("default_concurrency"),
                field_name="default_concurrency",
                default=int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            )
        current_archive_root = _basic_path_setting(
            value,
            current,
            field_name="archive_root",
            default=self.default_archive_root,
        )
        current_deal_archive_root = _basic_path_setting(
            value,
            current,
            field_name="deal_archive_root",
            default=_derived_deal_archive_root(current_archive_root),
        )
        explicit_marker = current.get(_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY)
        if explicit_marker is not None and not isinstance(explicit_marker, bool):
            raise ValueError(f"{_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY} must be a boolean")
        deal_archive_root_is_explicit = (
            explicit_marker
            if isinstance(explicit_marker, bool)
            else "deal_archive_root" in current
            and not _same_local_path(current_deal_archive_root, _derived_deal_archive_root(current_archive_root))
            and not _same_local_path(current_deal_archive_root, _derived_deal_archive_root(self.default_archive_root))
        )
        if "archive_root" in request:
            value["archive_root"] = resolve_directory_setting(request.get("archive_root"), setting_name="archive_root")
        if "deal_archive_root" in request:
            value["deal_archive_root"] = resolve_directory_setting(
                request.get("deal_archive_root"),
                setting_name="deal_archive_root",
            )
            deal_archive_root_is_explicit = True
        elif "archive_root" in request and not deal_archive_root_is_explicit:
            value["deal_archive_root"] = _derived_deal_archive_root(value["archive_root"])
        value[_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY] = bool(deal_archive_root_is_explicit)
        if "export_root" in request:
            value["export_root"] = resolve_directory_setting(request.get("export_root"), setting_name="export_root")
        if "retention_count" in request:
            value["retention_count"] = _normalize_retention_count(request.get("retention_count"))
        value["workspace_root"] = self.app_home
        value = _drop_legacy_scope_aliases(value)
        self.repository.set_setting(self._basic_settings_key(), value)
        response = dict(value)
        response.pop(_DEAL_ARCHIVE_ROOT_EXPLICIT_KEY, None)
        self.repository.add_audit_entry("settings_basic_updated", response)
        return response

    def set_advanced_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        value = self.get_advanced_settings()
        basic_source, _ = self._load_setting_with_decode_status(self._basic_settings_key(), default={
            "default_exchange": "all",
            "default_concurrency": int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            "archive_root": self.default_archive_root,
            "export_root": self.default_export_root,
            "retention_count": 20,
            "workspace_root": self.app_home,
        })
        basic_scope_state = _resolve_default_scope_state(basic_source, strict=True)
        allowed = {"save_json", "postprocess_config", "raw_manual_root"}
        request = _payload_object(payload)
        for key, raw_value in request.items():
            if key in allowed:
                if key == "postprocess_config":
                    value[key] = _resolve_postprocess_config_path(self.config, raw_value)
                elif key == "raw_manual_root":
                    value[key] = resolve_directory_setting(raw_value, setting_name=key)
                elif key == "save_json":
                    value[key] = parse_bool(raw_value, field_name="save_json")
                else:
                    value[key] = raw_value
        basic = self.get_basic_settings()
        value["app_home"] = self.app_home
        value["streaming_db"] = str(getattr(self.config, "STREAMING_DB_PATH", ""))
        if not str(value.get("postprocess_config") or "").strip():
            value["postprocess_config"] = self.default_postprocess_config
        value["log_dir"] = str(self.config.LOG_DIR)
        value["cache_dir"] = str(getattr(self.config, "CACHE_DIR", ""))
        value["raw_auto_root"] = basic["archive_root"]
        value["raw_manual_root"] = normalize_local_path(value.get("raw_manual_root")) or str(getattr(self.config, "HTML_FOLDER", ""))
        value["browser_cache_dir"] = str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))
        value["archive_root"] = basic["archive_root"]
        value["export_root"] = basic["export_root"]
        value["retention_count"] = int(basic.get("retention_count") or 20)
        value.update({key: basic_scope_state[key] for key in ("stored_preference", "effective_default_scope", "stale_default_metadata") if key in basic_scope_state})
        self.repository.set_setting(self._advanced_settings_key(), value)
        self.repository.add_audit_entry("settings_advanced_updated", value)
        return value
