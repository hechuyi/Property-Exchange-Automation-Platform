from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService


class FakeRuntimeDependencies:
    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
            "returncode": 0,
        }


class OverviewRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.service = AppService(
            config_obj=self.config,
            runtime_dependencies=FakeRuntimeDependencies(),
        )

    def test_overview_exposes_source_backed_family_visibility_without_static_profile_truth(self) -> None:
        overview = self.service.overview()

        self.assertIn("visibility", overview)
        self.assertEqual(overview["visibility"]["mode"], "multi_family")
        self.assertEqual(overview["visibility"]["visible_families"], ["listing", "deal"])
        self.assertNotIn("product_profile", overview)
        self.assertNotIn("default_project_type", overview)


if __name__ == "__main__":
    unittest.main()
