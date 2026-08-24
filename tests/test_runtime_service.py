from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop_backend.services.runtime_service import RuntimeService
from peap.product_profile import ProductProfile


class _FakeRuntimeDependencies:
    def __init__(self) -> None:
        self.status_calls: list[str] = []
        self.install_calls: list[str] = []
        self.status_installed: object = False
        self.install_installed: object = True

    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        self.status_calls.append(browser_name)
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "",
            "installed": self.status_installed,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        self.install_calls.append(browser_name)
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": self.install_installed,
            "error": "",
            "returncode": 0,
        }


class _FakeRuntimeRepository:
    def __init__(self) -> None:
        self.audit_entries: list[tuple[str, dict[str, object]]] = []

    def add_audit_entry(self, action: str, payload: dict[str, object]) -> None:
        self.audit_entries.append((action, payload))


def _wait_for_install_completion(service: RuntimeService) -> dict[str, object]:
    thread = service._runtime_install_thread
    if thread is not None:
        thread.join(timeout=2)
    state = service.get_runtime_install_state()
    if state["running"]:
        raise AssertionError("runtime install did not complete")
    return state


class RuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_dependencies = _FakeRuntimeDependencies()
        self.repository = _FakeRuntimeRepository()
        self.service = RuntimeService(
            config_obj=SimpleNamespace(),
            repository=self.repository,
            runtime_dependencies=self.runtime_dependencies,
        )

    def test_launch_browser_runtime_install_rejects_non_string_fields_before_runtime_probe(self) -> None:
        with self.assertRaisesRegex(ValueError, "browser_name"):
            self.service.launch_browser_runtime_install({"browser_name": {"name": "chromium"}})

        with self.assertRaisesRegex(ValueError, "trigger"):
            self.service.launch_browser_runtime_install({"trigger": {"source": "manual"}})

        self.assertEqual(self.runtime_dependencies.status_calls, [])

    def test_install_browser_runtime_rejects_non_string_browser_name_before_runtime_install(self) -> None:
        with self.assertRaisesRegex(ValueError, "browser_name"):
            self.service.install_browser_runtime({"browser_name": {"name": "chromium"}})

        self.assertEqual(self.runtime_dependencies.install_calls, [])

    def test_runtime_status_rejects_non_boolean_installed_flag(self) -> None:
        self.runtime_dependencies.status_installed = "false"

        with self.assertRaisesRegex(ValueError, "installed"):
            self.service.get_browser_runtime_status()

        self.runtime_dependencies.status_installed = False
        recovered = self.service.get_browser_runtime_status()
        self.assertFalse(recovered["installed"])
        self.assertEqual(self.runtime_dependencies.status_calls, ["chromium", "chromium"])

    def test_runtime_status_revalidates_cached_installed_flag(self) -> None:
        with self.service._browser_status_lock:
            self.service._browser_status_cache["chromium"] = (
                100.0,
                {"browser_name": "chromium", "installed": "false"},
            )

        with patch("desktop_backend.services.runtime_service.time.monotonic", return_value=100.1):
            with self.assertRaisesRegex(ValueError, "installed"):
                self.service.get_browser_runtime_status()

        self.assertEqual(self.runtime_dependencies.status_calls, [])

    def test_runtime_status_is_cached_until_ttl_expires(self) -> None:
        self.service.BROWSER_STATUS_CACHE_TTL_SEC = 5.0

        with patch(
            "desktop_backend.services.runtime_service.time.monotonic",
            side_effect=(100.0, 100.0, 100.1, 105.3, 105.3, 105.3),
        ):
            first = self.service.get_browser_runtime_status()
            second = self.service.get_browser_runtime_status()
            third = self.service.get_browser_runtime_status()

        self.assertEqual(first, second)
        self.assertEqual(third, first)
        self.assertEqual(self.runtime_dependencies.status_calls, ["chromium", "chromium"])

    def test_install_result_updates_cached_runtime_status(self) -> None:
        self.runtime_dependencies.status_installed = False
        self.runtime_dependencies.install_installed = True

        initial = self.service.get_browser_runtime_status()
        self.assertFalse(initial["installed"])
        self.assertEqual(self.runtime_dependencies.status_calls, ["chromium"])

        installed = self.service.install_browser_runtime({"browser_name": "chromium"})

        self.assertTrue(installed["installed"])
        refreshed = self.service.get_browser_runtime_status()
        self.assertTrue(refreshed["installed"])
        # The post-install cache update is authoritative; no stale probe is
        # returned and no second Playwright/runtime probe is needed.
        self.assertEqual(self.runtime_dependencies.status_calls, ["chromium"])

    def test_async_install_result_updates_cached_runtime_status(self) -> None:
        self.runtime_dependencies.status_installed = False
        self.runtime_dependencies.install_installed = True

        self.assertFalse(self.service.get_browser_runtime_status()["installed"])
        self.service.launch_browser_runtime_install(
            {"browser_name": "chromium", "trigger": "manual"}
        )
        state = _wait_for_install_completion(self.service)

        self.assertEqual(state["status"], "succeeded")
        self.assertTrue(self.service.get_browser_runtime_status()["installed"])
        self.assertEqual(self.runtime_dependencies.status_calls, ["chromium", "chromium"])

    def test_repeated_async_install_launch_returns_running_state_without_probe_or_deadlock(self) -> None:
        with self.service._lock:
            self.service._runtime_install_state = self.service._build_runtime_install_state(
                status="running",
                browser_name="chromium",
                trigger="manual",
                running=True,
            )

        results: list[dict[str, object]] = []
        thread = threading.Thread(
            target=lambda: results.append(
                self.service.launch_browser_runtime_install(
                    {"browser_name": "chromium", "trigger": "manual"}
                )
            ),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive(), "repeated launch deadlocked on runtime state lock")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "running")
        self.assertEqual(self.runtime_dependencies.status_calls, [])

    def test_product_readiness_rejects_non_boolean_browser_runtime_installed_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "browser_runtime.installed"):
            self.service.build_product_readiness(
                browser_runtime={
                    "browser_name": "chromium",
                    "installed": "true",
                    "error": "",
                }
            )

    def test_product_readiness_preserves_browser_runtime_installed_semantics(self) -> None:
        ready = self.service.build_product_readiness(
            browser_runtime={
                "browser_name": "chromium",
                "installed": True,
                "error": "",
            }
        )
        self.assertEqual(
            ready,
            {
                "ready": True,
                "download_ready": True,
                "browser_runtime_ready": True,
                "issues": [],
            },
        )

        missing = self.service.build_product_readiness(
            browser_runtime={
                "browser_name": "chromium",
                "installed": False,
                "error": "",
            }
        )
        self.assertFalse(missing["ready"])
        self.assertFalse(missing["download_ready"])
        self.assertFalse(missing["browser_runtime_ready"])
        self.assertEqual(len(missing["issues"]), 1)

    def test_product_profile_payload_rejects_string_source_ids(self) -> None:
        profile = ProductProfile(
            profile_id="desktop_listing",
            family_id="listing",
            source_ids="sse",
            postprocess_profile="postprocess_external",
            export_profile="ready_export",
            readiness_policy="browser_runtime_required",
        )

        with patch("desktop_backend.services.runtime_service.get_product_profile", return_value=profile):
            with self.assertRaisesRegex(ValueError, "source_ids"):
                self.service.product_profile_payload()

    def test_product_profile_payload_rejects_none_source_ids(self) -> None:
        profile = ProductProfile(
            profile_id="desktop_listing",
            family_id="listing",
            source_ids=None,
            postprocess_profile="postprocess_external",
            export_profile="ready_export",
            readiness_policy="browser_runtime_required",
        )

        with patch("desktop_backend.services.runtime_service.get_product_profile", return_value=profile):
            with self.assertRaisesRegex(ValueError, "source_ids"):
                self.service.product_profile_payload()

    def test_product_profile_payload_preserves_tuple_source_ids_as_list(self) -> None:
        profile = ProductProfile(
            profile_id="desktop_listing",
            family_id="listing",
            source_ids=("sse", "cbex"),
            postprocess_profile="postprocess_external",
            export_profile="ready_export",
            readiness_policy="browser_runtime_required",
        )

        with patch("desktop_backend.services.runtime_service.get_product_profile", return_value=profile):
            payload = self.service.product_profile_payload()

        self.assertEqual(payload["source_ids"], ["sse", "cbex"])

    def test_browser_install_result_rejects_non_boolean_installed_flag(self) -> None:
        self.runtime_dependencies.install_installed = {"value": True}

        with self.assertRaisesRegex(ValueError, "installed"):
            self.service.install_browser_runtime({"browser_name": "chromium"})

        self.assertEqual(self.repository.audit_entries, [])

    def test_launch_browser_runtime_install_marks_non_boolean_installed_result_failed(self) -> None:
        self.runtime_dependencies.status_installed = False
        self.runtime_dependencies.install_installed = "true"

        launched = self.service.launch_browser_runtime_install(
            {"browser_name": "chromium", "trigger": "manual"}
        )
        self.assertIn(launched["status"], {"running", "failed"})

        state = _wait_for_install_completion(self.service)

        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["running"])
        self.assertIn("browser_install_result.installed must be a boolean", state["message"])
        self.assertEqual(
            state["last_result"],
            {
                "browser_name": "chromium",
                "installed": False,
                "error": "browser_install_result.installed must be a boolean",
            },
        )
        self.assertEqual(
            self.repository.audit_entries,
            [
                (
                    "browser_runtime_install_async",
                    {
                        "browser_name": "chromium",
                        "trigger": "manual",
                        "installed": False,
                        "returncode": None,
                        "error": "browser_install_result.installed must be a boolean",
                    },
                )
            ],
        )

    def test_install_browser_runtime_preserves_installed_status_semantics(self) -> None:
        self.runtime_dependencies.install_installed = True
        installed = self.service.install_browser_runtime({"browser_name": "chromium"})
        self.assertTrue(installed["installed"])
        self.assertTrue(installed["product_readiness"]["ready"])
        self.assertEqual(self.service.get_runtime_install_state()["status"], "succeeded")

        self.runtime_dependencies.install_installed = False
        failed = self.service.install_browser_runtime({"browser_name": "chromium"})
        self.assertFalse(failed["installed"])
        self.assertFalse(failed["product_readiness"]["ready"])
        self.assertEqual(self.service.get_runtime_install_state()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
