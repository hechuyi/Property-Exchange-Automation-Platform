from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from desktop_backend.app_backend import _overview_stream_frame, build_handler


class _FakeOverviewStreamService:
    def __init__(self, *, running_calls: int = 1) -> None:
        self._overview_call_count = 0
        self._running_calls = running_calls

    def overview(self):
        self._overview_call_count += 1
        if self._overview_call_count <= self._running_calls:
            return {
                "record_summary": {
                    "state_counts": {"ready": 1},
                    "pending_mapping_count": 0,
                },
                "runtime": {
                    "browser": {"installed": True},
                    "install": {"status": "idle"},
                    "readiness": {"ready": True, "issues": []},
                },
                "latest_job": {
                    "job_id": "job-1",
                    "job_type": "one_click",
                    "status": "running",
                    "downloaded_count": 1,
                    "persisted_count": 0,
                    "exception_count": 0,
                    "metadata": {
                        "scope": {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "business_label": "股权转让",
                            "exchange": "sse",
                        }
                    },
                    "summary": {},
                    "created_at": "2026-04-22 10:00:00",
                    "updated_at": "2026-04-22 10:00:01",
                },
                "recent_jobs": [
                    {
                        "job_id": "job-1",
                        "job_type": "one_click",
                        "status": "running",
                        "downloaded_count": 1,
                        "persisted_count": 0,
                        "exception_count": 0,
                        "metadata": {},
                        "summary": {},
                        "created_at": "2026-04-22 10:00:00",
                        "updated_at": "2026-04-22 10:00:01",
                    }
                ],
                "visibility": {"mode": "listing_only", "visible_families": ["listing"]},
                "defaults": {},
            }
        return {
            "record_summary": {
                "state_counts": {"ready": 2},
                "pending_mapping_count": 0,
            },
            "runtime": {
                "browser": {"installed": True},
                "install": {"status": "idle"},
                "readiness": {"ready": True, "issues": []},
            },
            "latest_job": {
                "job_id": "job-1",
                "job_type": "one_click",
                "status": "success",
                "downloaded_count": 2,
                "persisted_count": 2,
                "exception_count": 0,
                "metadata": {},
                "summary": {"imported_count": 2},
                "created_at": "2026-04-22 10:00:00",
                "updated_at": "2026-04-22 10:00:02",
            },
            "recent_jobs": [
                {
                    "job_id": "job-1",
                    "job_type": "one_click",
                    "status": "success",
                    "downloaded_count": 2,
                    "persisted_count": 2,
                    "exception_count": 0,
                    "metadata": {},
                    "summary": {"imported_count": 2},
                    "created_at": "2026-04-22 10:00:00",
                    "updated_at": "2026-04-22 10:00:02",
                }
            ],
            "visibility": {"mode": "listing_only", "visible_families": ["listing"]},
            "defaults": {},
        }

    def build_job_progress(self, job):
        if str((job or {}).get("status") or "") == "running":
            return {
                "job_status": "running",
                "phase_code": "download",
                "phase_label": "抓取中",
                "phase_percent": 50,
                "metrics": [{"key": "downloaded_count", "label": "已下载", "value": 1}],
            }
        return {
            "job_status": "success",
            "phase_code": "complete",
            "phase_label": "已完成",
            "phase_percent": 100,
            "metrics": [{"key": "persisted_count", "label": "已归档", "value": 2}],
        }

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if self._overview_call_count <= self._running_calls and job_id == "job-1":
            return [
                {
                    "event_id": 1,
                    "event_ts": "2026-04-22 10:00:01",
                    "stage": "download",
                    "status": "running",
                    "project_code": "G32026SH1000001",
                    "archive_path": "",
                    "error_type": "",
                    "error_message": "",
                    "payload": {
                        "label": "正在抓取",
                        "summary_payload": {
                            "summary": {"saved": 1},
                            "phase_percent": 50,
                        },
                    },
                }
            ]
        return []


class _FakeTerminalOverviewStreamService(_FakeOverviewStreamService):
    def __init__(self, terminal_status: str) -> None:
        super().__init__()
        self._terminal_status = terminal_status

    def overview(self):
        payload = super().overview()
        payload["latest_job"] = {
            "job_id": "job-terminal",
            "job_type": "download_ingest",
            "status": self._terminal_status,
            "downloaded_count": 1,
            "persisted_count": 0,
            "exception_count": 1,
            "metadata": {},
            "summary": {},
            "created_at": "2026-04-22 10:00:00",
            "updated_at": "2026-04-22 10:00:02",
        }
        return payload

    def build_job_progress(self, job):
        return {
            "job_status": self._terminal_status,
            "phase_code": "completed_with_warnings",
            "phase_label": "已完成，但有待处理项",
            "phase_percent": 100,
        }

    def get_job_events(self, job_id: str, *, limit: int = 200):
        return [
            {
                "event_id": "terminal-event",
                "stage": "failed",
                "status": "failed",
                "error_type": "job_interrupted",
                "error_message": "desktop backend restarted before task completed",
                "payload": {},
            }
        ]


class OverviewStreamHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(_FakeOverviewStreamService(), api_token="test-token"))
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._shutdown_server)

    def _shutdown_server(self) -> None:
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        if hasattr(self, "server_thread"):
            self.server_thread.join(timeout=5)

    def test_overview_stream_emits_running_then_terminal_snapshot(self) -> None:
        request = Request(
            f"{self.base_url}/api/overview/stream?token=test-token",
            method="GET",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "text/event-stream")
            frames: list[dict] = []
            while True:
                line = response.readline()
                if not line:
                    break
                decoded = line.decode("utf-8").strip()
                if not decoded.startswith("data: "):
                    continue
                frames.append(json.loads(decoded[6:]))
                if len(frames) == 2:
                    break

        self.assertEqual(frames[0]["overview"]["latest_job"]["status"], "running")
        self.assertEqual(len(frames[0]["events"]), 1)
        self.assertEqual(frames[0]["events"][0]["record_family"], "listing")
        self.assertEqual(frames[0]["events"][0]["business_id"], "equity_transfer")
        self.assertEqual(frames[1]["overview"]["latest_job"]["status"], "success")
        self.assertEqual(frames[1]["events"], [])

    def test_overview_stream_suppresses_unchanged_running_frames(self) -> None:
        service = _FakeOverviewStreamService(running_calls=3)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            build_handler(service, api_token="test-token"),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=5)))

        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/overview/stream?token=test-token",
            method="GET",
        )
        with urlopen(request, timeout=5) as response:
            frames: list[dict] = []
            while len(frames) < 2:
                decoded = response.readline().decode("utf-8").strip()
                if decoded.startswith("data: "):
                    frames.append(json.loads(decoded[6:]))

        self.assertEqual(frames[0]["overview"]["latest_job"]["status"], "running")
        self.assertEqual(frames[1]["overview"]["latest_job"]["status"], "success")
        self.assertGreaterEqual(service._overview_call_count, 4)

    def test_overview_stream_frame_deduplicates_event_ids_and_exposes_cursor(self) -> None:
        service = _FakeOverviewStreamService()
        original_get_job_events = service.get_job_events

        def duplicate_events(job_id: str, *, limit: int = 200):
            events = original_get_job_events(job_id, limit=limit)
            return [*events, *events]

        service.get_job_events = duplicate_events  # type: ignore[method-assign]
        frame, status = _overview_stream_frame(service)

        self.assertEqual(status, "running")
        self.assertEqual(frame["job_id"], "job-1")
        self.assertEqual(frame["event_cursor"], "1")
        self.assertEqual([event["event_id"] for event in frame["events"]], ["1"])

    def test_overview_stream_does_not_emit_historical_events_for_terminal_latest_job_statuses(self) -> None:
        for terminal_status in ("success_with_warnings", "interrupted"):
            with self.subTest(terminal_status=terminal_status):
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    build_handler(_FakeTerminalOverviewStreamService(terminal_status), api_token="test-token"),
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(lambda server=server, thread=thread: (server.shutdown(), server.server_close(), thread.join(timeout=5)))

                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/overview/stream?token=test-token",
                    method="GET",
                )
                with urlopen(request, timeout=5) as response:
                    decoded = ""
                    while not decoded.startswith("data: "):
                        decoded = response.readline().decode("utf-8").strip()
                    frame = json.loads(decoded[6:])

                self.assertEqual(frame["overview"]["latest_job"]["status"], terminal_status)
                self.assertEqual(frame["events"], [])

    def test_overview_stream_rejects_missing_query_token_when_api_token_is_configured(self) -> None:
        request = Request(f"{self.base_url}/api/overview/stream", method="GET")
        with self.assertRaisesRegex(Exception, "HTTP Error 401") as context:
            urlopen(request, timeout=5)

        self.assertIn("401", str(context.exception))

    def test_overview_stream_rejects_unknown_query_key_even_with_valid_token(self) -> None:
        request = Request(f"{self.base_url}/api/overview/stream?token=test-token&x=1", method="GET")
        with self.assertRaisesRegex(Exception, "HTTP Error 400") as context:
            urlopen(request, timeout=5)

        self.assertIn("400", str(context.exception))
        response = context.exception
        body = json.loads(response.read().decode("utf-8"))
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertEqual(body["error"]["details"]["unknown_query_keys"], ["x"])
        self.assertEqual(body["error"]["details"]["allowed_query_keys"], ["token"])


if __name__ == "__main__":
    unittest.main()
