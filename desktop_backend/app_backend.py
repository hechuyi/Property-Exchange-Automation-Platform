"""Local HTTP API for the desktop application."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .app_config import AppConfig
from .app_service import AppService, AppUserFacingError
from .http_contract import (
    build_capacity_envelope,
    build_job_events_envelope,
    build_not_found_payload,
    normalize_job_event_limit,
)
from .product_errors import ProductError

DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token"


def _load_config(*, app_home: str | None = None):
    return AppConfig.from_env(app_home=app_home)


def _write_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = str(handler.headers.get("Origin") or "").strip()
    if origin == "null":
        handler.send_header("Access-Control-Allow-Origin", "null")
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", f"Content-Type, {DESKTOP_API_TOKEN_HEADER}")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
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
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict):
        return data
    return {}


def _header_value(headers: Mapping[str, Any] | None, name: str) -> str:
    if headers is None:
        return ""
    if hasattr(headers, "get"):
        try:
            value = headers.get(name)  # type: ignore[call-arg]
        except Exception:
            value = None
        if value is not None:
            return str(value)
    needle = str(name).lower()
    try:
        items = headers.items()
    except Exception:
        return ""
    for key, value in items:
        if str(key).lower() == needle:
            return str(value)
    return ""


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name) or []
    if not values:
        return default
    value = str(values[0] or "").strip()
    return value if value else default


def _parse_job_id(path: str) -> tuple[str, bool]:
    parts = [part for part in urlparse(path).path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "jobs":
        if len(parts) == 4 and parts[3] == "events":
            return parts[2], True
        if len(parts) == 3:
            return parts[2], False
    return "", False


def _not_found(resource: str, resource_id: str = "") -> tuple[int, dict[str, Any]]:
    return HTTPStatus.NOT_FOUND, build_not_found_payload(resource=resource, resource_id=resource_id)


def _parse_jobs_limit(raw_value: str) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, 200))


def dispatch_api_request(
    service: AppService,
    *,
    method: str,
    path: str,
    headers: Mapping[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    api_token: str = "",
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route = parsed.path
    query = parse_qs(parsed.query)
    method_name = str(method or "").upper()
    if method_name == "OPTIONS":
        return HTTPStatus.NO_CONTENT, {}
    if not _is_authorized(headers, route, api_token=api_token):
        return HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}

    try:
        if method_name == "GET":
            if route == "/api/ready":
                return HTTPStatus.OK, service.readiness()
            if route == "/api/health":
                return HTTPStatus.OK, service.health()
            if route == "/api/overview":
                return HTTPStatus.OK, service.overview()
            if route == "/api/jobs":
                limit = _parse_jobs_limit(_query_value(query, "limit", "20"))
                return HTTPStatus.OK, {"jobs": service.list_jobs(limit=limit)}
            job_id, is_events_route = _parse_job_id(route)
            if job_id:
                if is_events_route:
                    service.get_job(job_id)
                    event_limit = normalize_job_event_limit(_query_value(query, "limit", "200"))
                    raw_events = list(service.get_job_events(job_id, limit=event_limit + 1))
                    truncated = len(raw_events) > event_limit
                    events = raw_events[:event_limit] if truncated else raw_events
                    total_count = len(raw_events)
                    return HTTPStatus.OK, build_job_events_envelope(events, total_count=total_count)
                job = dict(service.get_job(job_id))
                job.pop("events", None)
                return HTTPStatus.OK, job
            if route == "/api/mappings":
                pending_payload = service.list_pending_mappings()
                if isinstance(pending_payload, dict):
                    payload = dict(pending_payload)
                else:
                    pending_items = list(pending_payload or [])
                    payload = build_capacity_envelope(pending_items, total_count=len(pending_items), item_key="pending")
                payload["entries"] = service.list_mapping_entries()
                return HTTPStatus.OK, payload
            if route == "/api/records":
                limit = _query_value(query, "limit", "50")
                payload = {
                    "state": _query_value(query, "state", "all"),
                    "project_type": _query_value(query, "project_type", "all"),
                    "record_family": _query_value(query, "record_family", "listing"),
                    "date_from": _query_value(query, "date_from"),
                    "date_to": _query_value(query, "date_to"),
                    "keyword": _query_value(query, "keyword"),
                    "limit": limit,
                    "page": _query_value(query, "page", "1"),
                    "page_size": _query_value(query, "page_size", limit),
                }
                return HTTPStatus.OK, service.list_records(payload)
            if route == "/api/settings/basic":
                return HTTPStatus.OK, service.get_basic_settings()
            if route == "/api/settings/advanced":
                return HTTPStatus.OK, service.get_advanced_settings()
            if route == "/api/runtime/dependencies":
                return HTTPStatus.OK, service.get_runtime_dependencies()
        if method_name in {"POST", "PUT"}:
            request_body = dict(body or {})
            if route == "/api/jobs/one-click":
                return HTTPStatus.ACCEPTED, service.launch_one_click(request_body)
            if route == "/api/jobs/manual-import":
                return HTTPStatus.ACCEPTED, service.launch_manual_import(request_body)
            if route == "/api/exports":
                return HTTPStatus.OK, service.run_export(request_body)
            if route == "/api/mappings":
                return HTTPStatus.OK, service.upsert_mapping(request_body)
            if route == "/api/mappings/preview":
                return HTTPStatus.OK, service.preview_mapping_upsert(request_body)
            if route == "/api/mappings/resolve-conflict":
                return HTTPStatus.OK, service.resolve_mapping_conflict(request_body)
            if route == "/api/mappings/reprocess-pending":
                return HTTPStatus.OK, service.launch_pending_mapping_refresh(request_body)
            if route.startswith("/api/records/") and route.endswith("/reprocess"):
                record_id = route.split("/")[3]
                return HTTPStatus.OK, service.reprocess_record(record_id)
            if route == "/api/settings/basic":
                return HTTPStatus.OK, service.set_basic_settings(request_body)
            if route == "/api/settings/advanced":
                return HTTPStatus.OK, service.set_advanced_settings(request_body)
            if route == "/api/runtime/install-browser":
                return HTTPStatus.ACCEPTED, service.launch_browser_runtime_install(request_body)
    except AppUserFacingError as exc:
        payload = {
            "error": exc.message,
            "error_code": exc.error_code,
        }
        if exc.details:
            payload["details"] = exc.details
        return exc.http_status, payload
    except ProductError as exc:
        return exc.status_code, exc.to_payload()
    except KeyError:
        resource_id = ""
        resource = "job"
        if route.startswith("/api/jobs/"):
            resource_id, _ = _parse_job_id(route)
        elif route.startswith("/api/records/") and route.endswith("/reprocess"):
            resource = "record"
            resource_id = route.split("/")[3]
        return _not_found(resource, resource_id)
    except Exception as exc:  # noqa: BLE001
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)}
    return _not_found("endpoint", route)


def _is_authorized(headers: Mapping[str, Any] | None, path: str, *, api_token: str) -> bool:
    if path == "/api/ready":
        return True
    expected_token = str(api_token or "").strip()
    if not expected_token:
        return True
    provided_token = _header_value(headers, DESKTOP_API_TOKEN_HEADER).strip()
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

        def _handle_write(self) -> None:
            payload = _read_json(self)
            status, response_payload = dispatch_api_request(
                service,
                method=self.command,
                path=self.path,
                headers=self.headers,
                body=payload,
                api_token=api_token,
            )
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
    config = _load_config(app_home=args.app_home)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
