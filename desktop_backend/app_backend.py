"""Local HTTP API for the desktop application."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app_config import AppConfig
from .app_service import AppService

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


def build_handler(service: AppService, *, api_token: str = ""):
    class AppHandler(BaseHTTPRequestHandler):
        server_version = "PEAPAppBackend/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            _json_response(self, HTTPStatus.NO_CONTENT, {})

        def _is_authorized(self, path: str) -> bool:
            if path == "/api/ready":
                return True
            expected_token = str(api_token or "").strip()
            if not expected_token:
                return True
            provided_token = str(self.headers.get(DESKTOP_API_TOKEN_HEADER) or "").strip()
            if not provided_token:
                return False
            return secrets.compare_digest(provided_token, expected_token)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if not self._is_authorized(path):
                return _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            try:
                if path == "/api/ready":
                    return _json_response(self, HTTPStatus.OK, service.readiness())
                if path == "/api/health":
                    return _json_response(self, HTTPStatus.OK, service.health())
                if path == "/api/overview":
                    return _json_response(self, HTTPStatus.OK, service.overview())
                if path == "/api/jobs":
                    limit = int((query.get("limit") or ["20"])[0])
                    return _json_response(self, HTTPStatus.OK, {"jobs": service.list_jobs(limit=limit)})
                if path.startswith("/api/jobs/") and path.endswith("/events"):
                    job_id = path.split("/")[3]
                    return _json_response(self, HTTPStatus.OK, {"events": service.get_job_events(job_id)})
                if path.startswith("/api/jobs/"):
                    job_id = path.split("/")[3]
                    return _json_response(self, HTTPStatus.OK, service.get_job(job_id))
                if path == "/api/mappings":
                    return _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "entries": service.list_mapping_entries(),
                            "pending": service.list_pending_mappings(),
                        },
                    )
                if path == "/api/records":
                    return _json_response(
                        self,
                        HTTPStatus.OK,
                        service.list_records(
                            {
                                "state": (query.get("state") or ["all"])[0],
                                "project_type": (query.get("project_type") or ["all"])[0],
                                "date_from": (query.get("date_from") or [""])[0],
                                "date_to": (query.get("date_to") or [""])[0],
                                "keyword": (query.get("keyword") or [""])[0],
                                "limit": (query.get("limit") or ["50"])[0],
                                "page": (query.get("page") or ["1"])[0],
                                "page_size": (query.get("page_size") or [(query.get("limit") or ["50"])[0]])[0],
                            }
                        ),
                    )
                if path == "/api/settings/basic":
                    return _json_response(self, HTTPStatus.OK, service.get_basic_settings())
                if path == "/api/settings/advanced":
                    return _json_response(self, HTTPStatus.OK, service.get_advanced_settings())
                if path == "/api/runtime/dependencies":
                    return _json_response(self, HTTPStatus.OK, service.get_runtime_dependencies())
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            self._handle_write()

        def do_PUT(self) -> None:  # noqa: N802
            self._handle_write()

        def _handle_write(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if not self._is_authorized(path):
                return _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            try:
                payload = _read_json(self)
                if path == "/api/jobs/one-click":
                    return _json_response(self, HTTPStatus.ACCEPTED, service.launch_one_click(payload))
                if path == "/api/jobs/manual-import":
                    return _json_response(self, HTTPStatus.ACCEPTED, service.launch_manual_import(payload))
                if path == "/api/exports":
                    return _json_response(self, HTTPStatus.OK, service.run_export(payload))
                if path == "/api/mappings":
                    return _json_response(self, HTTPStatus.OK, service.upsert_mapping(payload))
                if path == "/api/mappings/preview":
                    return _json_response(self, HTTPStatus.OK, service.preview_mapping_upsert(payload))
                if path == "/api/mappings/reprocess-pending":
                    return _json_response(self, HTTPStatus.OK, service.launch_pending_mapping_refresh(payload))
                if path.startswith("/api/records/") and path.endswith("/reprocess"):
                    record_id = path.split("/")[3]
                    return _json_response(self, HTTPStatus.OK, service.reprocess_record(record_id))
                if path == "/api/settings/basic":
                    return _json_response(self, HTTPStatus.OK, service.set_basic_settings(payload))
                if path == "/api/settings/advanced":
                    return _json_response(self, HTTPStatus.OK, service.set_advanced_settings(payload))
                if path == "/api/runtime/install-browser":
                    return _json_response(self, HTTPStatus.ACCEPTED, service.launch_browser_runtime_install(payload))
            except KeyError:
                return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

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
