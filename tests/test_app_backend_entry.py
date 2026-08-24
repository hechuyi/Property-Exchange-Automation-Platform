from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


class DesktopPackagingRetirementTest(unittest.TestCase):
    def test_packaged_backend_wrapper_is_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "desktop_backend_entry.py").exists())

    def test_packaging_scripts_are_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "desktop_app" / "build_backend_sidecar.js").exists())
        self.assertFalse((REPO_ROOT / "desktop_app" / "package_desktop.js").exists())
        self.assertFalse((REPO_ROOT / "scripts" / "package_desktop.js").exists())

    def test_packaging_lockfile_is_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "desktop_backend" / "requirements.build.lock.txt").exists())


class AppBackendEntrypointTest(unittest.TestCase):
    def test_main_bootstraps_current_schema_before_serving_fresh_workspace(self) -> None:
        from desktop_backend import app_backend
        from desktop_backend.app_config import AppConfig
        from peap.streaming_store import SCHEMA_VERSION

        class FakeServer:
            def __init__(self, *_args, **_kwargs) -> None:
                self.closed = False

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def server_close(self) -> None:
                self.closed = True

        with TemporaryDirectory() as temp_dir:
            app_home = str(Path(temp_dir) / "app_home")
            docs_home = str(Path(temp_dir) / "docs_home")
            with patch.dict(
                "os.environ",
                {
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": docs_home,
                },
                clear=False,
            ):
                config = AppConfig.from_env(project_root=temp_dir)
                self.assertFalse(Path(config.STREAMING_DB_PATH).exists())

                with patch.object(app_backend, "ThreadingHTTPServer", FakeServer):
                    exit_code = app_backend.main(["--host", "127.0.0.1", "--port", "0"])

                self.assertEqual(exit_code, 0)
                self.assertTrue(Path(config.STREAMING_DB_PATH).exists())
                self.assertEqual(app_backend.AppService(config_obj=config)._schema_ready, True)
                with sqlite3.connect(config.STREAMING_DB_PATH) as conn:
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
