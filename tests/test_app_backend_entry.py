from __future__ import annotations

import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
