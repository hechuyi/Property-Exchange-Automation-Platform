from __future__ import annotations

import json
import os
import threading
import time
import unittest
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

from desktop_backend.runtime_dependencies import playwright_env
from peap.browser_runtime import launch_chromium_browser_sync

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
PLAYWRIGHT_BROWSERS_PATH = Path(
    os.environ.get("PEAP_PLAYWRIGHT_BROWSERS_PATH")
    or os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    or str(REPO_ROOT / "cache" / "ms-playwright")
)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


@dataclass
class FakeDesktopApiState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    saved_basic_payloads: list[dict[str, Any]] = field(default_factory=list)
    one_click_payloads: list[dict[str, Any]] = field(default_factory=list)
    manual_import_payloads: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.catalog = {
            "active_profile": {"profile_id": "desktop_listing"},
            "visible_families": [
                {
                    "family_id": "listing",
                    "family_label": "挂牌业务",
                    "businesses": [
                        {
                            "business_id": "physical_asset",
                            "business_label": "实物资产",
                            "supported_surfaces": ["records", "one_click", "export"],
                        },
                        {
                            "business_id": "equity_transfer",
                            "business_label": "股权转让",
                            "supported_surfaces": ["records", "one_click", "export"],
                        },
                        {
                            "business_id": "capital_increase",
                            "business_label": "增资扩股",
                            "supported_surfaces": ["records", "one_click", "export"],
                        },
                        {
                            "business_id": "pre_disclosure",
                            "business_label": "预披露",
                            "supported_surfaces": ["records", "one_click", "export"],
                        },
                    ],
                },
            ],
            "support_matrix": {
                "listing": {
                    "physical_asset": {"records": True, "one_click": True, "export": True},
                    "equity_transfer": {"records": True, "one_click": True, "export": True},
                    "capital_increase": {"records": True, "one_click": True, "export": True},
                    "pre_disclosure": {"records": True, "one_click": True, "export": True},
                }
            },
            "sources": [
                {"source_id": "cbex", "source_label": "北交所", "record_families": ["listing"]},
                {"source_id": "sse", "source_label": "上交所", "record_families": ["listing"]},
            ],
            "surface_source_matrix": {
                "listing": {
                    "physical_asset": {"records": ["cbex", "sse"], "one_click": ["cbex", "sse"], "export": ["cbex", "sse"]},
                    "equity_transfer": {"records": ["cbex", "sse"], "one_click": ["cbex", "sse"], "export": ["cbex", "sse"]},
                    "capital_increase": {"records": ["cbex", "sse"], "one_click": ["cbex", "sse"], "export": ["cbex", "sse"]},
                    "pre_disclosure": {"records": ["cbex", "sse"], "one_click": ["cbex", "sse"], "export": ["cbex", "sse"]},
                }
            },
            "default_scope": {},
            "visibility": {
                "mode": "listing_only",
                "visible_families": ["listing"],
            },
        }
        self.basic_settings = {
            "effective_default_scope": {},
            "stored_preference": {},
            "stale_default_metadata": {
                "is_stale": False,
                "reason": "",
                "hint": "",
            },
            "default_exchange": "all",
            "default_concurrency": 4,
            "paths": {
                "workspace_root": "/tmp/workspace",
                "archive_root": "/tmp/archive",
                "export_root": "/tmp/export",
            },
        }
        self.advanced_settings = {
            "effective_default_scope": {},
            "stored_preference": {},
            "stale_default_metadata": {
                "is_stale": False,
                "reason": "",
                "hint": "",
            },
            "processing": {
                "save_json": False,
                "postprocess_config": "/tmp/postprocess.json",
            },
            "ingest_paths": {
                "raw_manual_root": "/tmp/manual",
                "raw_auto_root": "/tmp/archive",
            },
            "runtime_paths": {
                "app_home": "/tmp/workspace",
                "streaming_db": "/tmp/streaming.sqlite3",
                "log_dir": "/tmp/logs",
                "cache_dir": "/tmp/cache",
                "browser_cache_dir": str(PLAYWRIGHT_BROWSERS_PATH),
                "archive_root": "/tmp/archive",
                "export_root": "/tmp/export",
            },
        }
        self.runtime = {
            "browser": {
                "installed": True,
                "browser_name": "chromium",
                "installation_source": "bundled",
                "error": "",
            },
            "install": {
                "status": "idle",
                "browser_name": "chromium",
                "trigger": "",
                "attempt_count": 0,
                "started_at": "",
                "updated_at": "",
                "completed_at": "",
                "message": "",
                "running": False,
            },
            "readiness": {
                "ready": True,
                "download_ready": True,
                "browser_runtime_ready": True,
                "issues": [],
            },
        }

    def _business_label(self, record_family: str, business_id: str) -> str:
        for family in self.catalog["visible_families"]:
            if family.get("family_id") != record_family:
                continue
            for business in family.get("businesses", []):
                if business.get("business_id") == business_id:
                    return str(business.get("business_label") or "")
        return ""

    @staticmethod
    def _clone(value: Any) -> Any:
        return json.loads(json.dumps(value))

    def _basic_response_unlocked(self) -> dict[str, Any]:
        return self._clone(self.basic_settings)

    def basic_response(self) -> dict[str, Any]:
        with self.lock:
            return self._basic_response_unlocked()

    def advanced_response(self) -> dict[str, Any]:
        with self.lock:
            payload = self._clone(self.advanced_settings)
            payload["effective_default_scope"] = self._clone(self.basic_settings["effective_default_scope"])
            payload["stored_preference"] = self._clone(self.basic_settings["stored_preference"])
            payload["stale_default_metadata"] = self._clone(self.basic_settings["stale_default_metadata"])
            return payload

    def save_basic(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.saved_basic_payloads.append(self._clone(payload))
            self.basic_settings["default_exchange"] = str(payload.get("default_exchange") or "all")
            try:
                self.basic_settings["default_concurrency"] = int(payload.get("default_concurrency") or 0)
            except Exception:
                self.basic_settings["default_concurrency"] = 0
            paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
            self.basic_settings["paths"] = {
                "workspace_root": "/tmp/workspace",
                "archive_root": str(paths.get("archive_root") or "/tmp/archive"),
                "export_root": str(paths.get("export_root") or "/tmp/export"),
            }

            stored_preference = payload.get("stored_preference") if isinstance(payload.get("stored_preference"), dict) else {}
            record_family = str(stored_preference.get("record_family") or "listing")
            business_id = str(stored_preference.get("business_id") or "").strip()
            exchange = str(stored_preference.get("exchange") or "").strip()
            if business_id and exchange:
                self.basic_settings["stored_preference"] = {
                    "record_family": record_family,
                    "business_id": business_id,
                    "exchange": exchange,
                }
                self.basic_settings["effective_default_scope"] = {
                    "record_family": record_family,
                    "business_id": business_id,
                    "business_label": self._business_label(record_family, business_id),
                    "exchange": exchange,
                }
            else:
                self.basic_settings["stored_preference"] = {}
                self.basic_settings["effective_default_scope"] = {}
            self.basic_settings["stale_default_metadata"] = {
                "is_stale": False,
                "reason": "",
                "hint": "",
            }
            return self._basic_response_unlocked()

    def overview_response(self) -> dict[str, Any]:
        with self.lock:
            return {
                "record_summary": {
                    "state_counts": {
                        "ready": 0,
                        "pending_review": 0,
                        "pending_mapping": 0,
                        "mapping_conflict": 0,
                        "parse_failed": 0,
                        "postprocess_failed": 0,
                    },
                    "pending_mapping_count": 0,
                },
                "latest_job": None,
                "latest_progress": {},
                "recent_jobs": [],
                "runtime": self._clone(self.runtime),
                "defaults": {
                    "manual_import_input_dir": "/tmp/manual",
                    "default_scope": self._clone(self.basic_settings["effective_default_scope"]),
                },
                "visibility": self._clone(self.catalog["visibility"]),
            }

    def launch_one_click(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self.lock:
            request = self._clone(payload)
            raw_family_scopes = request.get("family_scopes")
            normalized_family_scopes = []
            if isinstance(raw_family_scopes, list):
                for scope in raw_family_scopes:
                    if not isinstance(scope, dict):
                        continue
                    normalized_scope = {
                        "record_family": str(scope.get("record_family") or "").strip(),
                        "business_id": str(scope.get("business_id") or "").strip(),
                        "exchange": str(scope.get("exchange") or request.get("exchange") or "").strip(),
                    }
                    business_label = str(scope.get("business_label") or "").strip()
                    if business_label:
                        normalized_scope["business_label"] = business_label
                    if all(normalized_scope.get(key) for key in ("record_family", "business_id", "exchange")):
                        normalized_family_scopes.append(normalized_scope)
            request_record_family = str(request.get("record_family") or "")
            request_business_id = str(request.get("business_id") or "")
            request_exchange = str(request.get("exchange") or "")
            raw_record_families = request.get("record_families")
            has_multi_family = isinstance(raw_record_families, list) and len(raw_record_families) > 0
            has_family_scopes = len(normalized_family_scopes) > 0
            if not has_multi_family and (not request_record_family or not request_business_id or not request_exchange):
                if not has_family_scopes:
                    return 400, _error(
                        "invalid_request",
                        "record_family, business_id, and exchange are required for one-click request",
                    )
            if not has_family_scopes and (not request_business_id or not request_exchange):
                return 400, _error(
                    "invalid_request",
                    "business_id and exchange are required for one-click request",
                )
            if has_family_scopes:
                request["family_scopes"] = normalized_family_scopes
                request["exchange"] = request_exchange
            elif has_multi_family:
                request["record_families"] = [str(f).strip() for f in raw_record_families if str(f).strip()]
            else:
                request["record_family"] = request_record_family
            request["business_id"] = request_business_id
            request["exchange"] = request_exchange
            self.one_click_payloads.append(request)
            return 202, _ok(
                {
                    "job_id": f"job-{len(self.one_click_payloads)}",
                    "job_type": "one_click",
                    "record_family": request_record_family or (normalized_family_scopes[0]["record_family"] if has_family_scopes else (request.get("record_families", ["listing"])[0] if has_multi_family else "listing")),
                    "business_id": request_business_id or (normalized_family_scopes[0]["business_id"] if has_family_scopes else ""),
                }
            )

    def launch_manual_import(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self.lock:
            request = self._clone(payload)
            self.manual_import_payloads.append(request)
            response = {
                "job_id": f"manual-{len(self.manual_import_payloads)}",
                "job_type": "manual_import",
                "input_dir": str(request.get("input_dir") or ""),
            }
            if request.get("record_family"):
                response["record_family"] = str(request.get("record_family"))
            if request.get("business_id"):
                response["business_id"] = str(request.get("business_id"))
            explicit_scope = {
                key: str(request.get(key) or "").strip()
                for key in ("record_family", "business_id", "business_label", "exchange")
                if str(request.get(key) or "").strip()
            }
            if explicit_scope:
                response["scope"] = explicit_scope
            return 202, _ok(response)


class FrontendSmokeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, state: FakeDesktopApiState, **kwargs) -> None:
        self._state = state
        super().__init__(*args, directory=str(FRONTEND_ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/catalog":
            self._send_json(200, _ok(self._state.catalog))
            return
        if path == "/api/settings/basic":
            self._send_json(200, _ok(self._state.basic_response()))
            return
        if path == "/api/settings/advanced":
            self._send_json(200, _ok(self._state.advanced_response()))
            return
        if path == "/api/runtime/dependencies":
            self._send_json(200, _ok(self._state.runtime))
            return
        if path == "/api/overview":
            self._send_json(200, _ok(self._state.overview_response()))
            return
        if path == "/api/jobs":
            _ = parse_qs(urlparse(self.path).query)
            self._send_json(200, _ok({"jobs": []}))
            return
        if path.startswith("/api/jobs/") and path.endswith("/events"):
            self._send_json(200, _ok({"events": []}))
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(raw_body or "{}")
        if path == "/api/settings/basic":
            self._send_json(200, _ok(self._state.save_basic(payload)))
            return
        if path == "/api/jobs/one-click":
            status, response = self._state.launch_one_click(payload)
            self._send_json(status, response)
            return
        if path == "/api/jobs/manual-import":
            status, response = self._state.launch_manual_import(payload)
            self._send_json(status, response)
            return
        self._send_json(404, _error("not_found", f"Unhandled endpoint: {path}"))


class FrontendFreshSettingsOneClickSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeDesktopApiState()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *args, **kwargs: FrontendSmokeHandler(*args, state=self.state, **kwargs),
        )
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _wait_until(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("condition was not satisfied before timeout")

    def test_fresh_settings_can_establish_actionable_default_scope_and_launch_one_click(self) -> None:
        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-business")

                    business_options = page.locator("#settings-default-business option")
                    self.assertEqual(business_options.count(), 6)
                    self.assertEqual(
                        [business_options.nth(index).get_attribute("value") for index in range(business_options.count())],
                        [
                            "",
                            "all",
                            "physical_asset",
                            "equity_transfer",
                            "capital_increase",
                            "pre_disclosure",
                        ],
                    )

                    family_selector = page.locator("#settings-default-family")
                    if family_selector.count() and family_selector.is_enabled():
                        family_selector.select_option("listing")

                    page.select_option("#settings-default-business", "equity_transfer")
                    page.select_option("#settings-default-scope-exchange", "sse")
                    page.click("#btn-settings-basic-save")
                    self._wait_until(lambda: bool(self.state.saved_basic_payloads))

                    self.assertEqual(
                        self.state.saved_basic_payloads[-1].get("stored_preference"),
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                    )
                    page.locator(".sidebar-nav-link[data-panel='overview']").click()
                    page.wait_for_selector("#btn-oneclick")
                    page.click("#btn-oneclick")
                    page.wait_for_selector("#oneclick-confirm")
                    self._wait_until(
                        lambda: page.locator("#oneclick-defaults").inner_text()
                        != "正在读取业务目录..."
                    )

                    defaults_text = page.locator("#oneclick-defaults").inner_text()
                    self.assertIn("将执行 1 个业务范围", defaults_text)
                    self.assertTrue(page.locator("#oneclick-confirm").is_enabled())

                    page.click("#oneclick-confirm")
                    self._wait_until(lambda: len(self.state.one_click_payloads) >= 1)
                    payload = self.state.one_click_payloads[0]
                    self.assertEqual(
                        payload.get("family_scopes"),
                        [
                            {
                                "record_family": "listing",
                                "business_id": "all",
                                "exchange": "all",
                            }
                        ],
                    )
                    self.assertNotIn("record_family", payload)
                finally:
                    browser.close()

    def test_settings_all_business_default_launches_one_click_with_business_id_all(self) -> None:
        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-business")

                    page.select_option("#settings-default-business", "all")
                    page.select_option("#settings-default-scope-exchange", "all")
                    page.click("#btn-settings-basic-save")
                    self._wait_until(lambda: bool(self.state.saved_basic_payloads))

                    self.assertEqual(
                        self.state.saved_basic_payloads[-1].get("stored_preference"),
                        {
                            "record_family": "listing",
                            "business_id": "all",
                            "exchange": "all",
                        },
                    )

                    page.locator(".sidebar-nav-link[data-panel='overview']").click()
                    page.wait_for_selector("#btn-oneclick")
                    page.click("#btn-oneclick")
                    page.wait_for_selector("#oneclick-confirm")
                    self._wait_until(
                        lambda: page.locator("#oneclick-defaults").inner_text()
                        != "正在读取业务目录..."
                    )

                    defaults_text = page.locator("#oneclick-defaults").inner_text()
                    self.assertIn("将执行 1 个业务范围", defaults_text)
                    self.assertTrue(page.locator("#oneclick-confirm").is_enabled())

                    page.click("#oneclick-confirm")
                    self._wait_until(lambda: len(self.state.one_click_payloads) >= 1)
                    payload = self.state.one_click_payloads[0]
                    self.assertEqual(
                        payload.get("family_scopes"),
                        [
                            {
                                "record_family": "listing",
                                "business_id": "all",
                                "exchange": "all",
                            }
                        ],
                    )
                    self.assertNotIn("record_family", payload)
                finally:
                    browser.close()

    def test_multi_family_one_click_launches_per_family_requests_with_resolved_scope(self) -> None:
        self.state.catalog["active_profile"] = {"profile_id": "desktop_multi_family"}
        self.state.catalog["visible_families"] = [
            *self.state.catalog["visible_families"],
            {
                "family_id": "deal",
                "family_label": "成交业务",
                "businesses": [
                    {
                        "business_id": "deal_equity_transfer",
                        "business_label": "股权转让成交",
                        "supported_surfaces": ["records", "one_click", "export"],
                    },
                    {
                        "business_id": "deal_physical_asset",
                        "business_label": "实物资产成交",
                        "supported_surfaces": ["records", "one_click", "export"],
                    },
                ],
            },
        ]
        self.state.catalog["support_matrix"]["deal"] = {
            "deal_equity_transfer": {"records": True, "one_click": True, "export": True},
            "deal_physical_asset": {"records": True, "one_click": True, "export": True},
        }
        self.state.catalog["surface_source_matrix"]["deal"] = {
            "deal_equity_transfer": {"records": ["sse"], "one_click": ["sse"], "export": ["sse"]},
            "deal_physical_asset": {"records": ["sse"], "one_click": ["sse"], "export": ["sse"]},
        }
        self.state.catalog["visibility"] = {
            "mode": "multi_family",
            "visible_families": ["listing", "deal"],
        }
        self.state.basic_settings["stored_preference"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        self.state.basic_settings["effective_default_scope"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "business_label": "股权转让",
            "exchange": "sse",
        }

        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.wait_for_selector("#btn-oneclick")
                    page.click("#btn-oneclick")
                    page.wait_for_selector("#oneclick-confirm")
                    self._wait_until(
                        lambda: page.locator("#oneclick-defaults").inner_text()
                        != "正在读取业务目录..."
                    )

                    page.click("#oneclick-confirm")
                    self._wait_until(lambda: len(self.state.one_click_payloads) >= 1)

                    self.assertEqual(len(self.state.one_click_payloads), 1)
                    payload = self.state.one_click_payloads[0]
                    self.assertNotIn("record_families", payload)
                    self.assertEqual(
                        payload.get("family_scopes"),
                        [
                            {
                                "record_family": "listing",
                                "business_id": "all",
                                "exchange": "all",
                            },
                            {
                                "record_family": "deal",
                                "business_id": "all",
                                "exchange": "all",
                            },
                        ],
                    )
                    self.assertNotIn("record_family", payload)
                finally:
                    browser.close()

    def test_settings_default_exchange_edit_does_not_mutate_stored_preference_scope(self) -> None:
        self.state.basic_settings["stored_preference"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        self.state.basic_settings["effective_default_scope"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "business_label": "股权转让",
            "exchange": "sse",
        }
        self.state.basic_settings["default_exchange"] = "sse"

        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-business")
                    page.select_option("#settings-default-exchange", "cbex")
                    page.click("#btn-settings-basic-save")
                    self._wait_until(lambda: len(self.state.saved_basic_payloads) >= 1)

                    self.assertEqual(self.state.saved_basic_payloads[-1]["default_exchange"], "cbex")
                    self.assertEqual(
                        self.state.saved_basic_payloads[-1]["stored_preference"],
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                    )
                finally:
                    browser.close()

    def test_settings_can_clear_shared_default_scope_without_touching_scalar_defaults(self) -> None:
        self.state.basic_settings["stored_preference"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        self.state.basic_settings["effective_default_scope"] = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "business_label": "股权转让",
            "exchange": "sse",
        }
        self.state.basic_settings["default_exchange"] = "cbex"

        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-business")
                    page.select_option("#settings-default-business", "")
                    page.click("#btn-settings-basic-save")
                    self._wait_until(lambda: len(self.state.saved_basic_payloads) >= 1)

                    self.assertEqual(self.state.saved_basic_payloads[-1]["stored_preference"], {})
                    self.assertEqual(self.state.saved_basic_payloads[-1]["default_exchange"], "cbex")
                finally:
                    browser.close()

    def test_settings_family_switch_defaults_to_all_and_survives_panel_navigation_before_save(self) -> None:
        self.state.catalog["active_profile"] = {"profile_id": "desktop_multi_family"}
        self.state.catalog["visible_families"] = [
            *self.state.catalog["visible_families"],
            {
                "family_id": "deal",
                "family_label": "成交业务",
                "businesses": [
                    {
                        "business_id": "equity_transfer",
                        "business_label": "股权成交",
                        "supported_surfaces": ["records", "export"],
                    },
                    {
                        "business_id": "physical_asset",
                        "business_label": "资产成交",
                        "supported_surfaces": ["records", "export"],
                    },
                ],
            },
        ]
        self.state.catalog["support_matrix"]["deal"] = {
            "equity_transfer": {"records": True, "export": True},
            "physical_asset": {"records": True, "export": True},
        }
        self.state.catalog["visibility"] = {
            "mode": "multi_family",
            "visible_families": ["listing", "deal"],
        }

        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-family")
                    page.select_option("#settings-default-family", "deal")

                    self.assertEqual(page.locator("#settings-default-business").input_value(), "all")
                    self.assertEqual(page.locator("#settings-default-scope-exchange").input_value(), "all")

                    page.locator(".sidebar-nav-link[data-panel='overview']").click()
                    page.wait_for_selector("#btn-oneclick")
                    page.locator(".sidebar-nav-link[data-panel='settings']").click()
                    page.wait_for_selector("#settings-default-business")

                    self.assertEqual(page.locator("#settings-default-family").input_value(), "deal")
                    self.assertEqual(page.locator("#settings-default-business").input_value(), "all")
                    self.assertEqual(page.locator("#settings-default-scope-exchange").input_value(), "all")
                finally:
                    browser.close()

    def test_manual_import_modal_can_submit_explicit_scope_over_real_ui(self) -> None:
        self.state.basic_settings["stored_preference"] = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "exchange": "cbex",
        }
        self.state.basic_settings["effective_default_scope"] = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "business_label": "实物资产",
            "exchange": "cbex",
        }
        self.state.basic_settings["default_exchange"] = "sse"

        with playwright_env(str(PLAYWRIGHT_BROWSERS_PATH)):
            with sync_playwright() as playwright:
                browser = launch_chromium_browser_sync(playwright, headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.base_url + "/index.html", wait_until="domcontentloaded")
                    page.click("#btn-import")
                    page.wait_for_selector("#manual-import-confirm")
                    page.fill("#manual-import-dir", "/tmp/manual-explicit-scope")
                    page.select_option("#manual-import-business", "equity_transfer")
                    page.select_option("#manual-import-exchange", "sse")
                    page.click("#manual-import-confirm")
                    self._wait_until(lambda: len(self.state.manual_import_payloads) == 1)

                    self.assertEqual(
                        self.state.manual_import_payloads[0],
                        {
                            "input_dir": "/tmp/manual-explicit-scope",
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "business_label": "股权转让",
                            "exchange": "sse",
                        },
                    )
                finally:
                    browser.close()
