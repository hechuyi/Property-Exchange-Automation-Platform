from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from desktop_backend.app_backend import build_handler


class FakeService:
    def readiness(self):
        return {"ok": True, "mode": "ready"}

    def health(self):
        return {"ok": True, "mode": "health"}

    def overview(self):
        return {"ok": True, "mode": "overview"}

    def preview_mapping_upsert(self, payload):
        return {"ok": True, "mode": "mapping_preview", "payload": payload}

    def launch_pending_mapping_refresh(self, payload):
        return {"ok": True, "mode": "pending_refresh", "payload": payload}


class AppBackendHandlerTest(unittest.TestCase):
    def test_ready_endpoint_returns_lightweight_readiness_payload(self) -> None:
        service = FakeService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/ready", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"ok": True, "mode": "ready"})

    def test_non_ready_requests_require_desktop_api_token(self) -> None:
        service = FakeService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/overview",
            method="GET",
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=2)

        self.assertEqual(raised.exception.code, 401)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"], "unauthorized")

    def test_non_ready_requests_accept_matching_desktop_api_token(self) -> None:
        service = FakeService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/overview",
            method="GET",
            headers={"X-PEAP-Desktop-Token": "test-token"},
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"ok": True, "mode": "overview"})

    def test_mapping_preview_endpoint_uses_service_preview_flow(self) -> None:
        service = FakeService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/mappings/preview",
            method="POST",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            data=json.dumps({"source_name": "华润", "target_value": "央企"}).encode("utf-8"),
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["mode"], "mapping_preview")
        self.assertEqual(payload["payload"]["source_name"], "华润")

    def test_pending_mapping_refresh_endpoint_uses_service_batch_launcher(self) -> None:
        service = FakeService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/mappings/reprocess-pending",
            method="POST",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            data=json.dumps({"scope": "pending"}).encode("utf-8"),
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["mode"], "pending_refresh")
        self.assertEqual(payload["payload"]["scope"], "pending")
