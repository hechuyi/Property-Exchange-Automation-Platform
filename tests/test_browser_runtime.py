from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from peap.browser_runtime import (
    launch_chromium_browser,
    launch_chromium_browser_sync,
    resolve_preferred_browser_executable,
)


class BrowserRuntimeResolutionTest(unittest.TestCase):
    @patch(
        "peap.browser_runtime._candidate_browser_paths",
        return_value=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ),
    )
    @patch("peap.browser_runtime.os.path.isfile")
    def test_resolve_preferred_browser_executable_falls_back_to_system_browser(
        self,
        isfile_mock,
        _candidate_paths,
    ) -> None:
        def fake_isfile(path: str) -> bool:
            return path == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        isfile_mock.side_effect = fake_isfile

        path, source = resolve_preferred_browser_executable(
            "chromium",
            playwright_executable_path="/tmp/browser-cache/chromium/chrome",
        )

        self.assertEqual(path, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        self.assertEqual(source, "system")


class BrowserRuntimeLaunchTest(unittest.IsolatedAsyncioTestCase):
    @patch(
        "peap.browser_runtime.resolve_preferred_browser_executable",
        return_value=("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "system"),
    )
    async def test_launch_chromium_browser_retries_with_system_browser(self, _resolve_browser) -> None:
        expected_browser = object()
        launch_mock = AsyncMock(
            side_effect=[
                RuntimeError("browserType.launch: Executable doesn't exist at /tmp/browser-cache/chrome"),
                expected_browser,
            ]
        )
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=launch_mock,
            )
        )

        browser = await launch_chromium_browser(
            playwright,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        self.assertIs(browser, expected_browser)
        first_call = launch_mock.await_args_list[0]
        second_call = launch_mock.await_args_list[1]
        self.assertNotIn("executable_path", first_call.kwargs)
        self.assertEqual(
            second_call.kwargs["executable_path"],
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        self.assertEqual(
            second_call.kwargs["args"],
            ["--disable-blink-features=AutomationControlled"],
        )


class BrowserRuntimeLaunchSyncTest(unittest.TestCase):
    @patch(
        "peap.browser_runtime.resolve_preferred_browser_executable",
        return_value=("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "system"),
    )
    def test_launch_chromium_browser_sync_retries_with_system_browser(self, _resolve_browser) -> None:
        expected_browser = object()
        launch_mock = unittest.mock.Mock(
            side_effect=[
                RuntimeError("browserType.launch: Executable doesn't exist at /tmp/browser-cache/chrome"),
                expected_browser,
            ]
        )
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=launch_mock,
            )
        )

        browser = launch_chromium_browser_sync(
            playwright,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        self.assertIs(browser, expected_browser)
        first_call = launch_mock.call_args_list[0]
        second_call = launch_mock.call_args_list[1]
        self.assertNotIn("executable_path", first_call.kwargs)
        self.assertEqual(
            second_call.kwargs["executable_path"],
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        self.assertEqual(
            second_call.kwargs["args"],
            ["--disable-blink-features=AutomationControlled"],
        )


if __name__ == "__main__":
    unittest.main()
