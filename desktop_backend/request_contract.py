"""HTTP request contract helpers for shared record-scope DTOs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peap.surface_contract import SURFACE_ONE_CLICK, SURFACE_RECORDS, scope_supported_for_surface
from peap_core.business_catalog import get_business_descriptor
from peap_core.business_hint import resolve_explicit_business_scope
from peap_core.source_catalog import canonical_source_code

from .domain.normalizers import (
    normalize_mapping_payload,
    parse_bool,
    parse_local_path,
    parse_positive_int,
    parse_text,
    validate_mapping_payload,
    validate_streaming_job_dates,
)
from .legacy_contract import legacy_record_scope_page_size
from .record_scope import normalize_record_scope, record_scope_to_dict, resolve_scope_business_ids

RECORD_SCOPE_FIELDS = (
    "record_family",
    "state",
    "business_id",
    "business_label",
    "exchange",
    "keyword",
    "date_from",
    "date_to",
)
RECORD_SCOPE_PAGINATION_FIELDS = ("page", "page_size")
RECORD_SCOPE_KEYS = RECORD_SCOPE_FIELDS + RECORD_SCOPE_PAGINATION_FIELDS
ONE_CLICK_SERVER_OWNED_FIELDS = ("effective_default_scope", "stored_preference")


def _normalize_one_click_family_scope(
    scope: Mapping[str, Any],
    *,
    default_exchange: str,
) -> dict[str, Any]:
    record_family = parse_text(scope.get("record_family"), field_name="record_family")
    business_id = parse_text(scope.get("business_id"), field_name="business_id")
    business_label = parse_text(scope.get("business_label"), field_name="business_label")
    effective_exchange = parse_text(scope.get("exchange"), field_name="exchange") or parse_text(
        default_exchange,
        field_name="exchange",
        default="all",
    )
    if not record_family or not business_id or not effective_exchange:
        raise ValueError("family_scopes entries require record_family, business_id, and exchange")
    normalized_scope = normalize_record_scope(
        {
            "record_family": record_family,
            "business_id": business_id,
            "business_label": business_label,
            "exchange": effective_exchange,
        }
    )
    normalized = {
        "record_family": _text(normalized_scope.record_family),
        "business_id": _text(normalized_scope.business_id),
        "exchange": _canonical_exchange(normalized_scope.exchange or default_exchange),
    }
    business_label = normalized_scope.business_label
    if not business_label and normalized["business_id"]:
        try:
            business_label = get_business_descriptor(
                normalized["business_id"],
                family_id=normalized["record_family"],
            ).canonical_label
        except KeyError:
            business_label = ""
    if business_label:
        normalized["business_label"] = business_label
    return normalized


def _query_value(query: Mapping[str, list[str]] | None, name: str, default: str = "") -> str:
    if query is None:
        return default
    if name not in query:
        return default
    values = query.get(name)
    if not isinstance(values, list):
        raise ValueError(f"{name} query parameter must be a list of strings")
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{name} query parameter values must be strings")
    if not values:
        return default
    value = values[0].strip()
    return value if value else default


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _as_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    return dict(payload)


def _optional_positive_int(raw_value: Any, *, field_name: str) -> int | None:
    if raw_value in {None, ""}:
        return None
    return parse_positive_int(raw_value, field_name=field_name, default=1)


def _optional_pagination_int(raw_value: Any, *, field_name: str) -> int | None:
    if raw_value in {None, ""}:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {raw_value}") from exc


def _optional_text(raw_value: Any, *, field_name: str, default: str = "") -> str:
    if raw_value is None or raw_value == "":
        return default
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} must be a string")
    return raw_value.strip() or default


def _canonical_exchange(value: Any) -> str:
    normalized = canonical_source_code(value)
    return str(normalized or "all").strip() or "all"


def _validate_manual_import_scope_support(scope: Mapping[str, Any]) -> None:
    if not scope:
        return
    record_family = _text(scope.get("record_family"))
    business_id = _text(scope.get("business_id"))
    exchange = _canonical_exchange(scope.get("exchange") or "all")
    if scope_supported_for_surface(
        record_family=record_family,
        business_ids=[business_id],
        exchange=exchange,
        surface=SURFACE_RECORDS,
    ):
        return
    raise ValueError(
        f"manual-import scope is not supported by records source contract: "
        f"{record_family}:{business_id}@{exchange}"
    )


def _validate_one_click_scope_support(scope: Mapping[str, Any], *, context: str) -> None:
    normalized_scope = normalize_record_scope(
        {
            "record_family": scope.get("record_family"),
            "business_id": scope.get("business_id"),
            "business_label": scope.get("business_label"),
            "exchange": scope.get("exchange") or "all",
        }
    )
    business_ids = resolve_scope_business_ids(normalized_scope)
    if scope_supported_for_surface(
        record_family=normalized_scope.record_family,
        business_ids=business_ids,
        exchange=normalized_scope.exchange,
        surface=SURFACE_ONE_CLICK,
    ):
        return
    raise ValueError(
        f"{context} is not supported by one-click source contract: "
        f"{normalized_scope.record_family}:{normalized_scope.business_id}@{normalized_scope.exchange}"
    )


def _normalize_top_level_scope_fields(
    *,
    record_family: str,
    business_id: str,
    business_label: str,
    exchange: str,
) -> dict[str, str]:
    if not record_family and not business_id and not business_label and not exchange:
        return {}
    if not record_family:
        return {
            "record_family": "",
            "business_id": business_id,
            "business_label": business_label,
            "exchange": _canonical_exchange(exchange),
        }
    normalized_scope = normalize_record_scope(
        {
            "record_family": record_family,
            "business_id": business_id or "all",
            "business_label": business_label,
            "exchange": exchange or "all",
        }
    )
    return {
        "record_family": _text(normalized_scope.record_family),
        "business_id": _text(normalized_scope.business_id),
        "business_label": _text(normalized_scope.business_label),
        "exchange": _canonical_exchange(normalized_scope.exchange),
    }


def _family_scope_conflicts_top_level(
    scope: Mapping[str, Any],
    top_level_scope: Mapping[str, str],
) -> list[str]:
    conflicts: list[str] = []
    top_record_family = _text(top_level_scope.get("record_family"))
    top_business_id = _text(top_level_scope.get("business_id"))
    top_exchange = _canonical_exchange(top_level_scope.get("exchange"))
    if top_record_family and top_record_family != _text(scope.get("record_family")):
        conflicts.append("record_family")
    if top_business_id and top_business_id != "all" and top_business_id != _text(scope.get("business_id")):
        conflicts.append("business_id")
    if top_exchange not in {"", "all"} and top_exchange != _canonical_exchange(scope.get("exchange")):
        conflicts.append("exchange")
    return conflicts


def _scope_source(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw_payload = _as_mapping(payload)
    scope_candidate = raw_payload.get("scope")
    if isinstance(scope_candidate, Mapping):
        return dict(scope_candidate)
    if "scope" in raw_payload:
        raise ValueError("scope must be an object")
    return raw_payload


def _has_explicit_scope_fields(source: Mapping[str, Any]) -> bool:
    return any(field_name in source for field_name in RECORD_SCOPE_FIELDS)


def _one_click_scope_error_message(
    basic: Mapping[str, Any] | None,
    *,
    missing_fields: tuple[str, ...],
) -> str:
    source = _as_mapping(basic)
    effective_scope = _as_mapping(source.get("effective_default_scope"))
    stale_metadata = _as_mapping(source.get("stale_default_metadata"))
    reason = _text(stale_metadata.get("reason"))
    hint = _text(stale_metadata.get("hint"))
    fields_label = ", ".join(missing_fields[:-1])
    if fields_label:
        fields_label = f"{fields_label}, and {missing_fields[-1]}"
    else:
        fields_label = missing_fields[-1]
    verb = "is" if len(missing_fields) == 1 else "are"
    prefix = f"{fields_label} {verb} required for one-click request"
    if effective_scope:
        return (
            f"{prefix}; current settings expose an effective default scope, but requests must send "
            f"explicit canonical scope"
        )
    if parse_bool(
        stale_metadata.get("is_stale"),
        field_name="stale_default_metadata.is_stale",
    ):
        message = f"{prefix}; stored default scope is stale/unsupported"
        if reason:
            message += f" ({reason})"
        if hint:
            message += f"; {hint}"
        return message
    return f"{prefix}; no actionable default scope is configured in settings"


def _reject_server_owned_fields(
    source: Mapping[str, Any],
    *,
    context: str,
    field_names: tuple[str, ...],
) -> None:
    for field_name in field_names:
        if field_name in source:
            raise ValueError(f"{field_name} is server-owned in {context}")


def _reject_legacy_project_type(source: Mapping[str, Any], *, context: str) -> None:
    if "project_type" in source:
        raise ValueError(f"project_type is not supported in {context}; use business_id")


def _normalize_scope_mapping(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw_payload = _as_mapping(payload)
    scope_candidate = raw_payload.get("scope")
    if isinstance(scope_candidate, Mapping):
        source = dict(scope_candidate)
        if not source:
            raise ValueError("scope is required for export request; supply an explicit canonical scope")
    elif "scope" in raw_payload:
        raise ValueError("scope must be an object")
    else:
        source = raw_payload
        if not _has_explicit_scope_fields(source):
            raise ValueError("scope is required for export request; supply an explicit canonical scope")
    _reject_legacy_project_type(source, context="request scope")
    for field_name in RECORD_SCOPE_FIELDS:
        value = source.get(field_name)
        if value is not None and value != "" and not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
    scope_payload = {
        field_name: source.get(field_name)
        for field_name in RECORD_SCOPE_KEYS
        if field_name in source
    }
    scope_payload["exchange"] = _canonical_exchange(source.get("exchange"))
    normalized = record_scope_to_dict(normalize_record_scope(scope_payload))
    if not normalized.get("business_id"):
        normalized.pop("business_label", None)
    return normalized


def build_record_scope_payload_from_query(query: Mapping[str, list[str]] | None) -> dict[str, Any]:
    if _query_value(query, "project_type"):
        raise ValueError("project_type is not supported in record queries; use business_id")
    raw_page = _query_value(query, "page")
    page = _optional_pagination_int(raw_page, field_name="page")
    raw_limit = _query_value(query, "limit")
    raw_page_size = _query_value(query, "page_size")
    if raw_limit and raw_page_size:
        _optional_pagination_int(raw_limit, field_name="limit")
    page_size_field = "page_size" if raw_page_size else "limit"
    page_size = _optional_pagination_int(
        legacy_record_scope_page_size({"page_size": raw_page_size, "limit": raw_limit}),
        field_name=page_size_field,
    )
    scope_payload = {
        "record_family": _query_value(query, "record_family", "listing"),
        "state": _query_value(query, "state", "all"),
        "business_id": _query_value(query, "business_id"),
        "exchange": _canonical_exchange(_query_value(query, "exchange", "all")),
        "keyword": _query_value(query, "keyword"),
        "date_from": _query_value(query, "date_from"),
        "date_to": _query_value(query, "date_to"),
        "page": page if page is not None else 1,
        "page_size": page_size if page_size is not None else 50,
    }
    return record_scope_to_dict(normalize_record_scope(scope_payload))


def normalize_export_request_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw_payload = _as_mapping(payload)
    if "export_mode" in raw_payload:
        raise ValueError("export_mode is not supported in export request; use requested_export_mode")
    if "mode" in raw_payload:
        raise ValueError("mode is not supported in export request; use requested_export_mode")
    if "cursor_key" in raw_payload:
        raise ValueError("cursor_key is not supported in export request; cursor_id is server-owned")
    normalized_payload = {
        key: value
        for key, value in raw_payload.items()
        if key not in RECORD_SCOPE_KEYS and key not in {"scope", "limit"}
    }
    requested_export_mode = parse_text(
        raw_payload.get("requested_export_mode"),
        field_name="requested_export_mode",
        default="full",
    )
    requested_export_mode = requested_export_mode.lower()
    if requested_export_mode not in {"full", "incremental"}:
        raise ValueError("requested_export_mode must be full or incremental")
    if requested_export_mode:
        normalized_payload["requested_export_mode"] = requested_export_mode
    if "output_dir" in raw_payload and raw_payload.get("output_dir") not in (None, ""):
        if not isinstance(raw_payload.get("output_dir"), str):
            raise ValueError("output_dir must be a string")
        normalized_payload["output_dir"] = raw_payload["output_dir"].strip()
    normalized_payload["scope"] = _normalize_scope_mapping(raw_payload)
    return normalized_payload


def normalize_one_click_request(
    payload: Mapping[str, Any] | None,
    *,
    basic_settings: Mapping[str, Any] | None,
    advanced_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request = _scope_source(payload)
    _reject_legacy_project_type(request, context="one-click request")
    _reject_server_owned_fields(
        request,
        context="one-click request",
        field_names=ONE_CLICK_SERVER_OWNED_FIELDS,
    )
    basic = _as_mapping(basic_settings)
    advanced = _as_mapping(advanced_settings)
    requested_record_family = parse_text(request.get("record_family"), field_name="record_family")
    requested_business_id = parse_text(request.get("business_id"), field_name="business_id")
    requested_exchange = parse_text(request.get("exchange"), field_name="exchange")
    requested_business_label = parse_text(request.get("business_label"), field_name="business_label")
    raw_family_scopes = request.get("family_scopes")
    normalized_family_scopes: list[dict[str, Any]] = []
    if isinstance(raw_family_scopes, list):
        default_exchange = requested_exchange or basic.get("default_exchange") or "all"
        for raw_scope in raw_family_scopes:
            if not isinstance(raw_scope, Mapping):
                raise ValueError("family_scopes entries must be objects")
            normalized_family_scopes.append(
                _normalize_one_click_family_scope(
                    raw_scope,
                    default_exchange=default_exchange,
                )
            )
            _validate_one_click_scope_support(
                normalized_family_scopes[-1],
                context="family_scopes entry",
            )
    raw_record_families = request.get("record_families")
    normalized_record_families: list[str] = []
    if raw_record_families is not None and raw_record_families != "":
        if not isinstance(raw_record_families, list):
            raise ValueError("record_families must be a list of strings")
        for value in raw_record_families:
            normalized_value = parse_text(value, field_name="record_families")
            if normalized_value:
                normalized_record_families.append(normalized_value)
    if len(normalized_family_scopes) > 1 and normalized_record_families:
        raise ValueError("family_scopes and record_families cannot both express multi-family one-click scope")
    if len(normalized_record_families) == 1 and not requested_record_family:
        requested_record_family = normalized_record_families[0]
    normalized_top_scope = _normalize_top_level_scope_fields(
        record_family=requested_record_family,
        business_id=requested_business_id,
        business_label=requested_business_label,
        exchange=requested_exchange,
    )
    if len(normalized_family_scopes) == 1:
        single_scope = normalized_family_scopes[0]
        single_record_family = _text(single_scope.get("record_family"))
        single_business_id = _text(single_scope.get("business_id"))
        single_exchange = _canonical_exchange(single_scope.get("exchange"))
        conflicts = _family_scope_conflicts_top_level(single_scope, normalized_top_scope)
        if conflicts:
            raise ValueError(
                "family_scopes single entry conflicts with top-level "
                + ", ".join(conflicts)
            )
        requested_record_family = _text(normalized_top_scope.get("record_family")) or single_record_family
        requested_business_id = _text(normalized_top_scope.get("business_id")) or single_business_id
        requested_exchange = _canonical_exchange(normalized_top_scope.get("exchange") or single_exchange)
        requested_business_label = _text(normalized_top_scope.get("business_label")) or _text(single_scope.get("business_label"))
    elif len(normalized_family_scopes) > 1:
        conflicts = sorted(
            {
                conflict
                for scope in normalized_family_scopes
                for conflict in _family_scope_conflicts_top_level(scope, normalized_top_scope)
            }
        )
        if conflicts:
            raise ValueError(
                "family_scopes entries conflict with top-level "
                + ", ".join(conflicts)
            )
    has_multi_family = len(normalized_family_scopes) > 1 or len(normalized_record_families) > 1
    if len(normalized_record_families) > 1 and not normalized_family_scopes:
        raise ValueError("family_scopes are required for multi-family one-click request")
    # record_family, business_id, and exchange are required for one-click request.
    if not has_multi_family and (not requested_record_family or not requested_business_id or not requested_exchange):
        missing_fields = tuple(
            field_name
            for field_name, field_value in (
                ("record_family", requested_record_family),
                ("business_id", requested_business_id),
                ("exchange", requested_exchange),
            )
            if not field_value
        )
        raise ValueError(_one_click_scope_error_message(basic, missing_fields=missing_fields))
    if not normalized_family_scopes and (not requested_business_id or not requested_exchange):
        missing_fields = tuple(
            field_name
            for field_name, field_value in (
                ("business_id", requested_business_id),
                ("exchange", requested_exchange),
            )
            if not field_value
        )
        raise ValueError(_one_click_scope_error_message(basic, missing_fields=missing_fields))
    normalized: dict[str, Any] = {
        "start_date": parse_text(request.get("start_date"), field_name="start_date"),
        "end_date": parse_text(request.get("end_date"), field_name="end_date"),
        "exchange": _canonical_exchange(requested_exchange),
        "concurrency": parse_positive_int(
            request.get("concurrency"),
            field_name="concurrency",
            default=max(1, int(basic.get("default_concurrency") or 1)),
        ),
        "no_resume": parse_bool(request.get("no_resume"), field_name="no_resume"),
        "save_json": parse_bool(
            request.get("save_json"),
            field_name="save_json",
            default=parse_bool(advanced.get("save_json"), field_name="save_json"),
        ),
        "postprocess_config": parse_text(
            request.get("postprocess_config"),
            field_name="postprocess_config",
            default=parse_text(advanced.get("postprocess_config"), field_name="postprocess_config"),
        ),
        "verbose": parse_bool(request.get("verbose"), field_name="verbose"),
        "include_public_resource": parse_bool(
            request.get("include_public_resource"),
            field_name="include_public_resource",
            default=False,
        ),
    }
    if len(normalized_family_scopes) > 1:
        normalized["family_scopes"] = normalized_family_scopes
        normalized["record_families"] = [scope["record_family"] for scope in normalized_family_scopes]
    elif has_multi_family:
        normalized["record_families"] = normalized_record_families
        normalized["business_id"] = _text(requested_business_id)
    else:
        normalized_scope = normalize_record_scope(
            {
                "record_family": requested_record_family,
                "business_id": requested_business_id,
                "business_label": requested_business_label,
                "exchange": requested_exchange,
            }
        )
        normalized["record_family"] = _text(normalized_scope.record_family or requested_record_family)
        normalized["business_id"] = _text(normalized_scope.business_id or requested_business_id)
        if normalized_scope.business_label:
            normalized["business_label"] = normalized_scope.business_label
        _validate_one_click_scope_support(normalized, context="one-click scope")
    page_size = _optional_positive_int(request.get("page_size"), field_name="page_size")
    if page_size is not None:
        normalized["page_size"] = page_size
    max_pages = _optional_positive_int(request.get("max_pages"), field_name="max_pages")
    if max_pages is not None:
        normalized["max_pages"] = max_pages
    validate_streaming_job_dates(normalized)
    return normalized


def normalize_manual_import_request(
    payload: Mapping[str, Any] | None,
    *,
    basic_settings: Mapping[str, Any] | None,
    advanced_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request = _as_mapping(payload)
    advanced = _as_mapping(advanced_settings)
    _reject_legacy_project_type(request, context="manual-import request")
    input_dir = request.get("input_dir") if "input_dir" in request else advanced.get("raw_manual_root")
    if input_dir == "":
        input_dir = advanced.get("raw_manual_root")
    normalized = {
        "input_dir": parse_local_path(
            input_dir,
            field_name="input_dir",
        ),
    }
    explicit_scope = resolve_explicit_business_scope(request)
    if explicit_scope:
        _validate_manual_import_scope_support(explicit_scope)
        normalized.update(explicit_scope)
    return normalized


def normalize_archive_reprocess_request(
    payload: Mapping[str, Any] | None,
    *,
    default_input_dir: Any,
) -> dict[str, Any]:
    request = _as_mapping(payload)
    input_dir = request.get("input_dir") if "input_dir" in request else default_input_dir
    if input_dir == "":
        input_dir = default_input_dir
    return {
        "input_dir": parse_local_path(
            input_dir,
            field_name="input_dir",
        )
    }


def normalize_mapping_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    normalized = normalize_mapping_payload(request)
    validate_mapping_payload(normalized)
    normalized_request = {
        "source_name": _text(normalized.get("source_name")),
        "match_field": _text(normalized.get("match_field")),
        "target_field": _text(normalized.get("target_field")),
        "target_value": _text(normalized.get("target_value")),
        "rule_kind": _text(normalized.get("rule_kind")),
        "notes": parse_text(request.get("notes"), field_name="notes"),
        "confirm_overwrite": parse_bool(request.get("confirm_overwrite"), field_name="confirm_overwrite"),
    }
    entry_id = parse_text(request.get("entry_id"), field_name="entry_id")
    if entry_id:
        normalized_request["entry_id"] = entry_id
    return normalized_request


def normalize_mapping_update_request(entry_id: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized_entry_id = _text(entry_id)
    if not normalized_entry_id:
        raise ValueError("entry_id is required")
    request = _as_mapping(payload)
    body_entry_id = parse_text(request.get("entry_id"), field_name="entry_id")
    if body_entry_id and body_entry_id != normalized_entry_id:
        raise ValueError("entry_id in body must match route")
    normalized = normalize_mapping_request(request)
    normalized["entry_id"] = normalized_entry_id
    return normalized


def normalize_mapping_delete_request(entry_id: str) -> dict[str, Any]:
    normalized_entry_id = _text(entry_id)
    if not normalized_entry_id:
        raise ValueError("entry_id is required")
    return {"entry_id": normalized_entry_id}


def normalize_mapping_conflict_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    resolution_request = _as_mapping(request.get("selected_resolution"))
    normalized_resolution = normalize_mapping_request(resolution_request)
    return {
        "record_id": parse_text(request.get("record_id"), field_name="record_id"),
        "selected_resolution": {
            "field": parse_text(resolution_request.get("field"), field_name="field"),
            "label": parse_text(resolution_request.get("label"), field_name="label"),
            "title": parse_text(resolution_request.get("title"), field_name="title"),
            "notes": parse_text(resolution_request.get("notes"), field_name="notes"),
            **normalized_resolution,
        },
        "notes": parse_text(request.get("notes"), field_name="notes"),
        "confirm_overwrite": parse_bool(request.get("confirm_overwrite"), field_name="confirm_overwrite"),
    }


def normalize_mapping_record_selection_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    record_ids = request.get("record_ids")
    normalized_record_ids: list[str] = []
    if "record_ids" not in request:
        normalized_record_ids = []
    elif isinstance(record_ids, (list, tuple)):
        for value in record_ids:
            if not isinstance(value, str):
                raise ValueError("record_ids entries must be non-empty strings")
            normalized_value = _text(value)
            if not normalized_value:
                raise ValueError("record_ids entries must be non-empty strings")
            normalized_record_ids.append(normalized_value)
    else:
        raise ValueError("record_ids must be a list of non-empty strings")
    return {
        "record_ids": normalized_record_ids,
    }


def normalize_mapping_business_re_evaluation_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return normalize_mapping_record_selection_request(payload)


def normalize_mapping_undo_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    return {
        "startup_session_id": parse_text(request.get("startup_session_id"), field_name="startup_session_id"),
    }


def normalize_path_selection_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    return {
        "selection_kind": parse_text(request.get("selection_kind"), field_name="selection_kind", default="directory").lower() or "directory",
        "prompt": parse_text(request.get("prompt"), field_name="prompt", default="选择路径"),
        "current_path": parse_local_path(request.get("current_path"), field_name="current_path"),
    }


def normalize_path_open_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    return {
        "path": parse_local_path(request.get("path"), field_name="path"),
        "reveal": parse_bool(request.get("reveal"), field_name="reveal"),
    }


def normalize_export_history_download_request(
    payload: Mapping[str, Any] | None,
    *,
    default_output_dir: Any,
) -> dict[str, Any]:
    request = _as_mapping(payload)
    raw_output_dir = request.get("output_dir")
    if raw_output_dir is not None and not isinstance(raw_output_dir, str):
        raise ValueError("output_dir must be a string")
    # The service owns the default-root decision so a settings change cannot
    # leave the request contract and service contract out of sync.
    del default_output_dir
    return {"output_dir": raw_output_dir.strip() if isinstance(raw_output_dir, str) else ""}


def normalize_runtime_install_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    request = _as_mapping(payload)
    return {
        "browser_name": _optional_text(request.get("browser_name"), field_name="browser_name", default="chromium"),
        "trigger": _optional_text(request.get("trigger"), field_name="trigger", default="manual"),
    }
