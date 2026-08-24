from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from desktop_backend.runtime_dependencies import RuntimeDependencyManager


class RuntimeDependencyManagerTest(unittest.TestCase):
    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=False)
    @patch("desktop_backend.runtime_dependencies._browser_executable_path", return_value="/tmp/chromium/chrome")
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    def test_status_reports_missing_browser_binary(
        self,
        _driver_paths,
        _browser_executable_path,
        _isfile,
    ) -> None:
        manager = RuntimeDependencyManager(browser_cache_dir="/tmp/browser-cache")
        result = manager.get_browser_runtime_status()

        self.assertEqual(result["browser_cache_dir"], "/tmp/browser-cache")
        self.assertEqual(result["executable_path"], "/tmp/chromium/chrome")
        self.assertFalse(result["installed"])
        self.assertEqual(result["error"], "")

    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=True)
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    def test_status_checks_browser_using_configured_cache_env(
        self,
        _driver_paths,
        _isfile,
    ) -> None:
        captured: dict[str, str] = {}

        def fake_executable_path(_browser_name: str) -> str:
            captured["playwright"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
            captured["peap"] = os.environ.get("PEAP_PLAYWRIGHT_BROWSERS_PATH", "")
            return "/tmp/browser-cache/chromium/chrome"

        with patch("desktop_backend.runtime_dependencies._browser_executable_path", side_effect=fake_executable_path):
            manager = RuntimeDependencyManager(browser_cache_dir="/tmp/browser-cache")
            result = manager.get_browser_runtime_status()

        self.assertEqual(captured["playwright"], "/tmp/browser-cache")
        self.assertEqual(captured["peap"], "/tmp/browser-cache")
        self.assertTrue(result["installed"])

    @patch.dict(os.environ, {"PEAP_BUNDLED_RUNTIME_READ_ONLY": "1"}, clear=False)
    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=False)
    @patch("desktop_backend.runtime_dependencies.resolve_preferred_browser_executable")
    @patch("desktop_backend.runtime_dependencies._browser_executable_path", return_value="/bundled/chromium/chrome")
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    def test_read_only_status_does_not_fallback_to_system_browser(
        self,
        _driver_paths,
        _browser_executable_path,
        resolve_mock,
        _isfile,
    ) -> None:
        manager = RuntimeDependencyManager(browser_cache_dir="/bundled/ms-playwright")

        result = manager.get_browser_runtime_status()

        resolve_mock.assert_not_called()
        self.assertEqual(result["executable_path"], "/bundled/chromium/chrome")
        self.assertEqual(result["installation_source"], "playwright")
        self.assertFalse(result["installed"])

    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=True)
    @patch("desktop_backend.runtime_dependencies._browser_executable_path", return_value="/tmp/chromium/chrome")
    @patch("desktop_backend.runtime_dependencies._driver_env", return_value={"PW_LANG_NAME": "python"})
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    @patch("desktop_backend.runtime_dependencies.subprocess.run")
    def test_install_uses_playwright_cache_and_refreshes_status(
        self,
        run_mock,
        _driver_paths,
        _driver_env,
        _browser_executable_path,
        _isfile,
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "downloaded"
        run_mock.return_value.stderr = ""

        manager = RuntimeDependencyManager(browser_cache_dir="/tmp/browser-cache")
        result = manager.install_browser_runtime()

        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["/tmp/driver", "/tmp/cli.js", "install", "chromium"])
        self.assertEqual(kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"], "/tmp/browser-cache")
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["installed"])
        self.assertEqual(result["error"], "")

    @patch.dict(os.environ, {"PEAP_BUNDLED_RUNTIME_READ_ONLY": "1"}, clear=False)
    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=True)
    @patch("desktop_backend.runtime_dependencies._browser_executable_path", return_value="/tmp/chromium/chrome")
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    @patch("desktop_backend.runtime_dependencies.subprocess.run")
    def test_read_only_bundle_install_is_a_noop_when_browser_is_present(
        self,
        run_mock,
        _driver_paths,
        _browser_executable_path,
        _isfile,
    ) -> None:
        manager = RuntimeDependencyManager(browser_cache_dir="/Applications/PEAP Launcher.app/Contents/Resources/Runtime/arm64/ms-playwright")

        result = manager.install_browser_runtime()

        run_mock.assert_not_called()
        self.assertTrue(result["installed"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["returncode"], 0)
        self.assertIn("already installed", result["message"])

    @patch.dict(os.environ, {"PEAP_BUNDLED_RUNTIME_READ_ONLY": "1"}, clear=False)
    @patch("desktop_backend.runtime_dependencies.os.path.isfile", return_value=False)
    @patch("desktop_backend.runtime_dependencies._browser_executable_path", return_value="/tmp/chromium/chrome")
    @patch("desktop_backend.runtime_dependencies._driver_paths", return_value=("/tmp/driver", "/tmp/cli.js"))
    @patch("desktop_backend.runtime_dependencies.subprocess.run")
    def test_read_only_bundle_install_fails_closed_when_browser_is_missing(
        self,
        run_mock,
        _driver_paths,
        _browser_executable_path,
        _isfile,
    ) -> None:
        manager = RuntimeDependencyManager(browser_cache_dir="/Applications/PEAP Launcher.app/Contents/Resources/Runtime/arm64/ms-playwright")

        result = manager.install_browser_runtime()

        run_mock.assert_not_called()
        self.assertFalse(result["installed"])
        self.assertTrue(result["skipped"])
        self.assertIsNone(result["returncode"])
        self.assertIn("read-only", result["error"])


if __name__ == "__main__":
    unittest.main()
