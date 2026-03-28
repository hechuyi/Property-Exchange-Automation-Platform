from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class AppBackendEntryTest(unittest.TestCase):
    def test_packaged_entrypoint_is_runnable_as_top_level_script(self) -> None:
        entry_path = REPO_ROOT / "desktop_backend_entry.py"

        result = subprocess.run(
            [sys.executable, str(entry_path), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("PEAP desktop app backend", result.stdout)

    def test_sidecar_builder_targets_packaged_entrypoint_wrapper(self) -> None:
        build_script = (REPO_ROOT / "desktop_app" / "build_backend_sidecar.js").read_text(encoding="utf-8")

        self.assertIn('"desktop_backend_entry.py"', build_script)
        self.assertNotIn('"desktop_backend", "app_backend.py"', build_script)

    def test_sidecar_builder_collects_peap_postprocess_data_files(self) -> None:
        build_script = (REPO_ROOT / "desktop_app" / "build_backend_sidecar.js").read_text(encoding="utf-8")

        self.assertRegex(
            build_script,
            re.compile(r'"--collect-data"\s*,\s*"peap_postprocess"'),
        )

    def test_sidecar_builder_uses_uv_locked_environment_instead_of_pip_requirements(self) -> None:
        build_script = (REPO_ROOT / "desktop_app" / "build_backend_sidecar.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('["sync", "--locked", "--group", "build"]', build_script)
        self.assertRegex(
            build_script,
            re.compile(r'"run"\s*,\s*"--group"\s*,\s*"build"\s*,\s*"pyinstaller"'),
        )
        self.assertNotIn("requirements.lock.txt", build_script)
        self.assertNotIn(".venv-desktop", build_script)


if __name__ == "__main__":
    unittest.main()
