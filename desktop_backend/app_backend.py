"""Local HTTP API for the desktop application."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from collections.abc import Mapping as MappingABC
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from peap.migrations import MigrationRunner

from .action_contract import (
    build_export_action_view,
    build_mapping_conflict_resolution_view,
    build_mapping_delete_view,
    build_mapping_preview_view,
    build_mapping_save_view,
    build_mapping_undo_view,
    build_path_open_view,
    build_path_selection_view,
    build_record_field_missing_ack_view,
    build_record_reprocess_view,
    build_record_reveal_view,
    build_runtime_install_action_view,
    build_streaming_job_launch_view,
)
from .app_config import AppConfig
from .app_service import AppService
from .error_codes import ERROR_INVALID_REQUEST, ERROR_UNAUTHORIZED
from .http_contract import (
    build_error_payload,
    build_job_events_envelope,
    build_not_found_payload,
    build_success_payload,
    normalize_job_event_limit,
)
from .job_contract import build_job_view
from .job_event_contract import build_job_event_view
from .mapping_resource_contract import build_mappings_resource
from .middleware.error_handler import handle_exception
from .overview_contract import build_overview_view
from .process_lock import ProcessLock, ProcessLockError, database_lock_path
from .request_contract import (
    build_record_scope_payload_from_query,
    normalize_archive_reprocess_request,
    normalize_export_history_download_request,
    normalize_export_request_payload,
    normalize_manual_import_request,
    normalize_mapping_business_re_evaluation_request,
    normalize_mapping_conflict_request,
    normalize_mapping_delete_request,
    normalize_mapping_record_selection_request,
    normalize_mapping_request,
    normalize_mapping_undo_request,
    normalize_mapping_update_request,
    normalize_path_open_request,
    normalize_path_selection_request,
    normalize_runtime_install_request,
)
from .review_problem_contract import normalize_review_problem_query
from .runtime_contract import build_runtime_view
from .settings_contract import (
    build_advanced_settings_view,
    build_basic_settings_view,
    normalize_advanced_settings_update,
    normalize_basic_settings_update,
)

DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token"


def _load_config(*, app_home: str | None = None):
    return AppConfig.from_env(app_home=app_home)


def _run_startup_migrations(config: AppConfig) -> int:
    return MigrationRunner.run(config.STREAMING_DB_PATH)


def _write_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = str(handler.headers.get("Origin") or "").strip()
    if origin == "null":
        handler.send_header("Access-Control-Allow-Origin", "null")
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", f"Content-Type, {DESKTOP_API_TOKEN_HEADER}")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any | None) -> None:
    if int(status) == int(HTTPStatus.NO_CONTENT):
        handler.send_response(status)
        handler.send_header("Content-Length", "0")
        _write_cors_headers(handler)
        handler.end_headers()
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _write_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    size = int(handler.headers.get("Content-Length", "0") or "0")
    if size <= 0:
        return {}
    raw = handler.rfile.read(size)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be a JSON object") from exc
    if isinstance(data, dict):
        return data
    raise ValueError("request body must be a JSON object")


def _header_value(headers: Mapping[str, Any] | None, name: str) -> str:
    if headers is None:
        return ""
    if hasattr(headers, "get"):
        value = headers.get(name)  # type: ignore[call-arg]
        if value is not None:
            return str(value)
    needle = str(name).lower()
    for key, value in headers.items():
        if str(key).lower() == needle:
            return str(value)
    return ""


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    if values is None:
        return default
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"invalid query values for {name}")
    if len(values) > 1:
        raise ValueError(f"multiple values for {name}")
    if not values:
        return default
    value = str(values[0] or "").strip()
    return value if value else default


def _match_route_template(route: str, template: str) -> dict[str, str] | None:
    route_parts = str(route or "").split("/")
    template_parts = str(template or "").split("/")
    if len(route_parts) != len(template_parts):
        return None
    variables: dict[str, str] = {}
    for route_part, template_part in zip(route_parts, template_parts, strict=True):
        if template_part.startswith("{") and template_part.endswith("}"):
            name = template_part[1:-1]
            value = unquote(str(route_part or "")).strip()
            if not name or not value:
                return None
            variables[name] = value
            continue
        if route_part != template_part:
            return None
    return variables


def _route_param(route: str, template: str, name: str) -> str:
    variables = _match_route_template(route, template)
    if variables is None:
        return ""
    return str(variables.get(name) or "").strip()


def _parse_job_id(path: str) -> tuple[str, bool]:
    route = urlparse(path).path
    job_id = _route_param(route, "/api/jobs/{job_id}/events", "job_id")
    if job_id:
        return job_id, True
    job_id = _route_param(route, "/api/jobs/{job_id}", "job_id")
    if job_id:
        return job_id, False
    return "", False


def _parse_job_retry_id(path: str) -> str:
    return _route_param(urlparse(path).path, "/api/jobs/{job_id}/retry", "job_id")


def _parse_mapping_entry_id(path: str) -> str:
    entry_id = _route_param(urlparse(path).path, "/api/mappings/{entry_id}", "entry_id")
    if entry_id in {"preview", "resolve-conflict", "reprocess-pending", "re-evaluate-business", "undo"}:
        return ""
    return entry_id


def _parse_export_history_id(path: str) -> str:
    return _route_param(urlparse(path).path, "/api/exports/history/{export_id}", "export_id")


def _parse_export_history_action_id(path: str, action: str) -> str:
    return _route_param(urlparse(path).path, f"/api/exports/history/{{export_id}}/{action}", "export_id")


def _parse_record_action_id(path: str, action: str) -> str:
    return _route_param(urlparse(path).path, f"/api/records/{{record_id}}/{action}", "record_id")


def _parse_record_field_missing_ack_id(path: str) -> str:
    return _route_param(
        urlparse(path).path,
        "/api/records/{record_id}/field-missing/acknowledge",
        "record_id",
    )


def _route_allowed_query_keys(method_name: str, route: str) -> set[str] | None:
    method = str(method_name or "").upper()
    if method == "OPTIONS":
        return set()
    if method == "GET":
        if route in {"/api/ready", "/api/health", "/api/catalog", "/api/overview"}:
            return set()
        if route == "/api/overview/stream":
            return {"token"}
        if route == "/api/jobs":
            return {"limit"}
        job_id, is_events_route = _parse_job_id(route)
        if job_id:
            return {"limit"} if is_events_route else set()
        if route == "/api/mappings":
            return set()
        if route == "/api/review-problems":
            return {
                "problem_kind",
                "record_family",
                "business_id",
                "exchange",
                "state",
                "keyword",
                "date_from",
                "date_to",
                "page",
                "page_size",
            }
        if route == "/api/records":
            return {
                "record_family",
                "state",
                "business_id",
                "business_label",
                "exchange",
                "keyword",
                "date_from",
                "date_to",
                "page",
                "page_size",
                "limit",
                "_",
                "_t",
                "_ts",
            }
        if route in {"/api/settings/basic", "/api/settings/advanced", "/api/runtime/dependencies"}:
            return set()
        if route == "/api/exports/history":
            return {"limit"}
        if _parse_export_history_id(route):
            return set()
        return None
    if method in {"POST", "PUT"}:
        if route in {
            "/api/jobs/one-click",
            "/api/jobs/download-ingest",
            "/api/jobs/manual-import",
            "/api/jobs/archive-reprocess",
            "/api/exports",
            "/api/mappings",
            "/api/mappings/preview",
            "/api/mappings/resolve-conflict",
            "/api/mappings/reprocess-pending",
            "/api/mappings/re-evaluate-business",
            "/api/mappings/undo",
            "/api/system/select-path",
            "/api/system/open-path",
            "/api/settings/basic",
            "/api/settings/advanced",
            "/api/runtime/install-browser",
        }:
            return set()
        if _parse_job_retry_id(route):
            return set()
        if _parse_export_history_action_id(route, "open") or _parse_export_history_action_id(route, "download"):
            return set()
        if (
            _parse_record_action_id(route, "reprocess")
            or _parse_record_action_id(route, "reveal-folder")
            or _parse_record_field_missing_ack_id(route)
        ):
            return set()
        if method == "PUT" and _parse_mapping_entry_id(route):
            return set()
        return None
    if method == "DELETE":
        if _parse_mapping_entry_id(route):
            return set()
        return None
    return None


def _validate_query_keys(method_name: str, route: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]] | None:
    allowed = _route_allowed_query_keys(method_name, route)
    if allowed is None:
        return None
    unknown = sorted(str(key) for key in query if str(key) not in allowed)
    if not unknown:
        return None
    return (
        HTTPStatus.BAD_REQUEST,
        build_error_payload(
            error_code=ERROR_INVALID_REQUEST,
            message="unknown query keys",
            details={
                "unknown_query_keys": unknown,
                "allowed_query_keys": sorted(allowed),
            },
        ),
    )


def _not_found(resource: str, resource_id: str = "") -> tuple[int, dict[str, Any]]:
    return HTTPStatus.NOT_FOUND, build_not_found_payload(resource=resource, resource_id=resource_id)


def _success(status: int, data: Any, *, meta: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    return status, build_success_payload(data=data, meta=meta)


def _parse_jobs_limit(raw_value: str) -> int:
    if raw_value in {None, ""}:
        return 20
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid limit: {raw_value!r}") from exc
    if value <= 0:
        raise ValueError(f"invalid limit: {raw_value!r}")
    return max(1, min(value, 200))


def _job_events_items(raw_events: Any) -> list[Any]:
    if raw_events is None or isinstance(raw_events, (str, bytes, bytearray)) or isinstance(raw_events, MappingABC):
        raise ValueError("items must be a list")
    return list(raw_events)


def _build_job_list_view(service: AppService, *, limit: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in service.list_jobs(limit=limit):
        build_job_view(job)
        jobs.append(build_job_view(job, progress=service.build_job_progress(job)))
    return jobs


_TERMINAL_JOB_STATUSES = frozenset(
    {
        "success",
        "success_with_warnings",
        "interrupted",
        "failed",
        "cancelled",
        "canceled",
        "aborted",
        "error",
    }
)


def _job_identity_from_overview(overview: Mapping[str, Any]) -> tuple[str, str]:
    latest_job = overview.get("latest_job")
    if not isinstance(latest_job, Mapping):
        return "", ""
    return (
        str(latest_job.get("job_id") or "").strip(),
        str(latest_job.get("status") or "").strip(),
    )


def _dedupe_job_events(raw_events: list[Any]) -> list[Any]:
    unique_events: list[Any] = []
    seen_event_ids: set[str] = set()
    for raw_event in raw_events:
        event_id = ""
        if isinstance(raw_event, MappingABC):
            event_id = str(raw_event.get("event_id") or "").strip()
        if event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
        unique_events.append(raw_event)
    return unique_events


def _overview_stream_frame(service: AppService) -> tuple[dict[str, Any], str]:
    raw_overview = service.overview()
    raw_latest_job = raw_overview.get("latest_job") if isinstance(raw_overview, MappingABC) else None
    overview = build_overview_view(raw_overview, build_job_progress=service.build_job_progress)
    latest_job_id, latest_status = _job_identity_from_overview(overview)
    raw_events = []
    if latest_job_id and latest_status not in _TERMINAL_JOB_STATUSES:
        raw_events = _dedupe_job_events(list(service.get_job_events(latest_job_id, limit=200)))
    events = [build_job_event_view(event, parent_job=raw_latest_job) for event in raw_events]
    return {
        "overview": overview,
        "job_id": latest_job_id,
        "event_cursor": str(events[0].get("event_id") or "") if events else "",
        "events": events,
    }, latest_status


def _write_overview_stream(handler: BaseHTTPRequestHandler, service: AppService) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    _write_cors_headers(handler)
    handler.end_headers()

    last_payload: bytes | None = None
    last_write_at = time.monotonic()
    for _ in range(120):
        frame, latest_status = _overview_stream_frame(service)
        payload = f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8")
        if payload != last_payload:
            try:
                handler.wfile.write(payload)
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            last_payload = payload
            last_write_at = time.monotonic()
        elif time.monotonic() - last_write_at >= 15.0:
            try:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            last_write_at = time.monotonic()
        if not latest_status or latest_status in _TERMINAL_JOB_STATUSES:
            return
        time.sleep(0.25)


def dispatch_api_request(
    service: AppService,
    *,
    method: str,
    path: str,
    headers: Mapping[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    api_token: str = "",
) -> tuple[int, dict[str, Any] | None]:
    parsed = urlparse(path)
    route = parsed.path
    query = parse_qs(parsed.query)
    method_name = str(method or "").upper()
    mapping_entry_id = _parse_mapping_entry_id(route)
    if method_name == "OPTIONS":
        return HTTPStatus.NO_CONTENT, None
    query_error = _validate_query_keys(method_name, route, query)
    if query_error is not None:
        return query_error
    if not _is_authorized(headers, path, api_token=api_token):
        return HTTPStatus.UNAUTHORIZED, build_error_payload(
            error_code=ERROR_UNAUTHORIZED,
            message=ERROR_UNAUTHORIZED,
        )

    try:
        if method_name == "GET":
            if route == "/api/ready":
                return _success(HTTPStatus.OK, service.readiness())
            if route == "/api/health":
                return _success(HTTPStatus.OK, service.health())
            if route == "/api/catalog":
                return _success(HTTPStatus.OK, service.get_catalog())
            if route == "/api/overview":
                return _success(
                    HTTPStatus.OK,
                    build_overview_view(service.overview(), build_job_progress=service.build_job_progress),
                )
            if route == "/api/jobs":
                limit = _parse_jobs_limit(_query_value(query, "limit", "20"))
                return _success(HTTPStatus.OK, {"jobs": _build_job_list_view(service, limit=limit)})
            job_id, is_events_route = _parse_job_id(route)
            if job_id:
                if is_events_route:
                    parent_job = service.get_job(job_id)
                    event_limit = normalize_job_event_limit(_query_value(query, "limit", "200"))
                    raw_events = _job_events_items(service.get_job_events(job_id, limit=event_limit + 1))
                    truncated = len(raw_events) > event_limit
                    visible_events = raw_events[:event_limit] if truncated else raw_events
                    events = [
                        build_job_event_view(event, parent_job=parent_job)
                        for event in visible_events
                    ]
                    total_count = service.count_job_events(job_id) if truncated else len(visible_events)
                    return _success(HTTPStatus.OK, build_job_events_envelope(events, total_count=total_count))
                job = dict(service.get_job(job_id))
                job.pop("events", None)
                return _success(HTTPStatus.OK, build_job_view(job, progress=service.build_job_progress(job)))
            if route == "/api/mappings":
                payload = build_mappings_resource(entries=service.list_mapping_entries(), backlog=service.list_pending_mappings())
                payload["undo"] = service.mapping_undo_state()
                return _success(HTTPStatus.OK, payload)
            if route == "/api/review-problems":
                return _success(HTTPStatus.OK, service.list_review_problems(normalize_review_problem_query(query)))
            if route == "/api/records":
                return _success(
                    HTTPStatus.OK,
                    service.list_records(build_record_scope_payload_from_query(query)),
                )
            if route == "/api/settings/basic":
                return _success(HTTPStatus.OK, build_basic_settings_view(service.get_basic_settings()))
            if route == "/api/settings/advanced":
                return _success(HTTPStatus.OK, build_advanced_settings_view(service.get_advanced_settings()))
            if route == "/api/runtime/dependencies":
                return _success(HTTPStatus.OK, build_runtime_view(service.get_runtime_dependencies()))
            if route == "/api/exports/history":
                limit = _parse_jobs_limit(_query_value(query, "limit", "100"))
                return _success(HTTPStatus.OK, service.list_exports_history(limit=limit))
            export_id = _parse_export_history_id(route)
            if export_id:
                return _success(HTTPStatus.OK, service.get_export_history_detail(export_id))
        if method_name in {"POST", "PUT"}:
            if body is None:
                request_body: dict[str, Any] = {}
            elif isinstance(body, Mapping):
                request_body = dict(body)
            else:
                raise ValueError("request body must be a JSON object")
            if route == "/api/jobs/one-click":
                return _success(HTTPStatus.ACCEPTED, build_streaming_job_launch_view(service.launch_one_click(request_body)))
            if route == "/api/jobs/download-ingest":
                return _success(HTTPStatus.ACCEPTED, build_streaming_job_launch_view(service.launch_download_ingest(request_body)))
            if route == "/api/jobs/manual-import":
                normalized_body = normalize_manual_import_request(
                    request_body,
                    basic_settings=service.get_basic_settings(),
                    advanced_settings=service.get_advanced_settings(),
                )
                return _success(HTTPStatus.ACCEPTED, build_streaming_job_launch_view(service.launch_manual_import(normalized_body)))
            if route == "/api/jobs/archive-reprocess":
                normalized_body = normalize_archive_reprocess_request(
                    request_body,
                    default_input_dir=service.get_basic_settings().get("archive_root") or "",
                )
                return _success(HTTPStatus.ACCEPTED, build_streaming_job_launch_view(service.launch_archive_reprocess(normalized_body)))
            retry_job_id = _parse_job_retry_id(route)
            if retry_job_id:
                return _success(HTTPStatus.ACCEPTED, build_streaming_job_launch_view(service.retry_job(retry_job_id)))
            if route == "/api/exports":
                return _success(
                    HTTPStatus.OK,
                    build_export_action_view(service.run_export(normalize_export_request_payload(request_body))),
                )
            export_id = _parse_export_history_action_id(route, "open")
            if export_id:
                return _success(HTTPStatus.OK, service.open_export_history(export_id))
            export_id = _parse_export_history_action_id(route, "download")
            if export_id:
                normalized_body = normalize_export_history_download_request(
                    request_body,
                    default_output_dir=service.get_basic_settings().get("export_root") or "",
                )
                return _success(HTTPStatus.OK, service.download_export_history(export_id, output_dir=normalized_body["output_dir"]))
            if route == "/api/mappings":
                return _success(HTTPStatus.OK, build_mapping_save_view(service.upsert_mapping(normalize_mapping_request(request_body))))
            if method_name == "PUT" and mapping_entry_id:
                normalized_body = normalize_mapping_update_request(mapping_entry_id, request_body)
                return _success(HTTPStatus.OK, build_mapping_save_view(service.update_mapping(mapping_entry_id, normalized_body)))
            if route == "/api/mappings/preview":
                return _success(HTTPStatus.OK, build_mapping_preview_view(service.preview_mapping_upsert(normalize_mapping_request(request_body))))
            if route == "/api/mappings/resolve-conflict":
                return _success(HTTPStatus.OK, build_mapping_conflict_resolution_view(service.resolve_mapping_conflict(normalize_mapping_conflict_request(request_body))))
            if route == "/api/mappings/reprocess-pending":
                normalized_body = normalize_mapping_record_selection_request(request_body)
                return _success(HTTPStatus.OK, build_streaming_job_launch_view(service.launch_pending_mapping_refresh(normalized_body)))
            if route == "/api/mappings/re-evaluate-business":
                normalized_body = normalize_mapping_business_re_evaluation_request(request_body)
                return _success(HTTPStatus.OK, build_streaming_job_launch_view(service.launch_business_re_evaluation(normalized_body)))
            if route == "/api/mappings/undo":
                normalized_body = normalize_mapping_undo_request(request_body)
                return _success(
                    HTTPStatus.OK,
                    build_mapping_undo_view(
                        service.undo_last_mapping_operation(startup_session_id=normalized_body.get("startup_session_id", ""))
                    ),
                )
            if route == "/api/system/select-path":
                return _success(HTTPStatus.OK, build_path_selection_view(service.choose_local_path(normalize_path_selection_request(request_body))))
            if route == "/api/system/open-path":
                return _success(HTTPStatus.OK, build_path_open_view(service.open_local_path(normalize_path_open_request(request_body))))
            record_id = _parse_record_action_id(route, "reprocess")
            if record_id:
                return _success(HTTPStatus.OK, build_record_reprocess_view(service.reprocess_record(record_id), record_id=record_id))
            record_id = _parse_record_action_id(route, "reveal-folder")
            if record_id:
                return _success(HTTPStatus.OK, build_record_reveal_view(service.reveal_record_folder(record_id)))
            record_id = _parse_record_field_missing_ack_id(route)
            if record_id:
                return _success(
                    HTTPStatus.OK,
                    build_record_field_missing_ack_view(service.acknowledge_field_missing(record_id)),
                )
            if route == "/api/settings/basic":
                return _success(
                    HTTPStatus.OK,
                    build_basic_settings_view(service.set_basic_settings(normalize_basic_settings_update(request_body))),
                )
            if route == "/api/settings/advanced":
                return _success(
                    HTTPStatus.OK,
                    build_advanced_settings_view(service.set_advanced_settings(normalize_advanced_settings_update(request_body))),
                )
            if route == "/api/runtime/install-browser":
                return _success(HTTPStatus.ACCEPTED, build_runtime_install_action_view(service.launch_browser_runtime_install(normalize_runtime_install_request(request_body))))
        if method_name == "DELETE" and mapping_entry_id:
            normalize_mapping_delete_request(mapping_entry_id)
            return _success(HTTPStatus.OK, build_mapping_delete_view(service.delete_mapping(mapping_entry_id)))
    except KeyError:
        resource_id = ""
        resource = "job"
        if route.startswith("/api/jobs/"):
            resource_id, _ = _parse_job_id(route)
            if not resource_id:
                resource_id = _parse_job_retry_id(route)
        elif route.startswith("/api/mappings/"):
            resource = "mapping"
            resource_id = _parse_mapping_entry_id(route)
        elif route.startswith("/api/exports/history/"):
            resource = "export"
            resource_id = (
                _parse_export_history_id(route)
                or _parse_export_history_action_id(route, "open")
                or _parse_export_history_action_id(route, "download")
            )
        else:
            record_id = _parse_record_action_id(route, "reprocess")
            if record_id:
                resource = "record"
                resource_id = record_id
            record_id = _parse_record_action_id(route, "reveal-folder") or _parse_record_field_missing_ack_id(route)
            if record_id:
                resource = "record"
                resource_id = record_id
        return _not_found(resource, resource_id)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)
    return _not_found("endpoint", route)


def _is_authorized(headers: Mapping[str, Any] | None, path: str, *, api_token: str) -> bool:
    route = urlparse(path).path
    if route == "/api/ready":
        return True
    if not isinstance(api_token, str):
        return False
    expected_token = api_token.strip()
    if not expected_token:
        return True
    provided_token = _header_value(headers, DESKTOP_API_TOKEN_HEADER).strip()
    parsed = urlparse(path)
    route = parsed.path
    if not provided_token and route == "/api/overview/stream":
        # EventSource cannot set custom headers, so the overview SSE endpoint accepts token via query.
        qs = parse_qs(parsed.query)
        token_values = qs.get("token", [])
        provided_token = str(token_values[0]).strip() if token_values else ""
    if not provided_token:
        return False
    return secrets.compare_digest(provided_token, expected_token)


def build_handler(service: AppService, *, api_token: str = ""):
    class AppHandler(BaseHTTPRequestHandler):
        server_version = "PEAPAppBackend/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            _json_response(self, HTTPStatus.NO_CONTENT, {})

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/api/overview/stream":
                parsed = urlparse(self.path)
                query_error = _validate_query_keys("GET", parsed.path, parse_qs(parsed.query))
                if query_error is not None:
                    status, payload = query_error
                    return _json_response(self, status, payload)
                if not _is_authorized(self.headers, self.path, api_token=api_token):
                    return _json_response(
                        self,
                        HTTPStatus.UNAUTHORIZED,
                        build_error_payload(
                            error_code=ERROR_UNAUTHORIZED,
                            message=ERROR_UNAUTHORIZED,
                        ),
                    )
                return _write_overview_stream(self, service)
            status, payload = dispatch_api_request(
                service,
                method="GET",
                path=self.path,
                headers=self.headers,
                api_token=api_token,
            )
            return _json_response(self, status, payload)

        def do_POST(self) -> None:  # noqa: N802
            self._handle_write()

        def do_PUT(self) -> None:  # noqa: N802
            self._handle_write()

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle_write()

        def _handle_write(self) -> None:
            try:
                payload = _read_json(self)
                status, response_payload = dispatch_api_request(
                    service,
                    method=self.command,
                    path=self.path,
                    headers=self.headers,
                    body=payload,
                    api_token=api_token,
                )
            except Exception as exc:  # noqa: BLE001
                status, response_payload = handle_exception(exc)
            return _json_response(self, status, response_payload)

    return AppHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PEAP desktop app backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=42679)
    parser.add_argument("--app-home", default=os.environ.get("PEAP_APP_HOME"))
    parser.add_argument("--api-token", default=os.environ.get("PEAP_APP_API_TOKEN", ""))
    parser.add_argument(
        "--install-browser",
        action="store_true",
        help="Install the default Playwright browser runtime and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Resolve the writable app-home without creating directories first.  This
    # closes the race where two direct backend launches both migrate the same
    # SQLite workspace before either one starts listening on the port.
    probe_config = AppConfig.from_env(
        app_home=args.app_home,
        ensure_dirs=False,
        migrate_legacy=False,
    )
    process_lock = ProcessLock(
        database_lock_path(probe_config.STREAMING_DB_PATH),
        label="PEAP backend",
    )
    try:
        process_lock.acquire()
    except ProcessLockError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    try:
        config = _load_config(app_home=args.app_home)
        _run_startup_migrations(config)
        service = AppService(config_obj=config)
        if bool(args.install_browser):
            result = service.install_browser_runtime({"browser_name": "chromium"})
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result.get("installed") else 1
        server = ThreadingHTTPServer(
            (args.host, int(args.port)),
            build_handler(service, api_token=str(args.api_token or "")),
        )
        print(f"PEAP app backend listening on http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        process_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
