from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend import local_paths


class LocalPathsTests(unittest.TestCase):
    def test_pick_local_path_macos_uses_osascript_folder_picker(self) -> None:
        with patch("desktop_backend.local_paths._run_command", return_value="/tmp/chosen") as mocked:
            result = local_paths._pick_local_path_macos(
                selection_kind="directory",
                prompt="选择待导入目录",
                current_path="/tmp/current/input",
            )

        self.assertEqual(result, "/tmp/chosen")
        command = mocked.call_args.args[0]
        self.assertEqual(command[:2], ["osascript", "-e"])
        self.assertIn("choose folder", command[2])
        self.assertIn("default location", command[2])
        self.assertNotIn("\\u", command[2])

    def test_pick_local_path_macos_escapes_unicode_and_quotes_for_applescript(self) -> None:
        with patch("desktop_backend.local_paths._run_command", return_value="/tmp/chosen") as mocked:
            local_paths._pick_local_path_macos(
                selection_kind="directory",
                prompt='选择"归档"目录',
                current_path='/tmp/历史 "归档"/input',
            )

        command = mocked.call_args.args[0]
        self.assertEqual(command[:2], ["osascript", "-e"])
        self.assertIn('with prompt "选择\\"归档\\"目录"', command[2])
        self.assertIn('default location POSIX file "/tmp/历史 \\"归档\\""', command[2])
        self.assertNotIn("\\u", command[2])

    def test_pick_local_path_windows_uses_sta_powershell_dialog(self) -> None:
        with (
            patch("desktop_backend.local_paths._windows_shell_executable", return_value="pwsh"),
            patch("desktop_backend.local_paths._run_command", return_value=r"C:\chosen" ) as mocked,
        ):
            result = local_paths._pick_local_path_windows(
                selection_kind="directory",
                prompt="选择目录",
                current_path=r"C:\current\input",
            )

        self.assertEqual(result, r"C:\chosen")
        command = mocked.call_args.args[0]
        self.assertEqual(command[:4], ["pwsh", "-NoProfile", "-STA", "-Command"])
        self.assertIn("FolderBrowserDialog", command[4])
        self.assertIn("SelectedPath", command[4])

    def test_windows_shell_executable_falls_back_to_pwsh(self) -> None:
        with patch("desktop_backend.local_paths.shutil.which", side_effect=[None, "pwsh"]):
            self.assertEqual(local_paths._windows_shell_executable(), "pwsh")

    def test_fake_local_path_interactions_are_explicit_no_gui_test_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chosen = os.path.join(temp_dir, "chosen")
            os.mkdir(chosen)
            with patch.dict(
                os.environ,
                {
                    "PEAP_FAKE_LOCAL_PATH_INTERACTIONS": "1",
                    "PEAP_FAKE_SELECTED_PATH": chosen,
                },
                clear=False,
            ):
                self.assertEqual(
                    local_paths.pick_local_path(selection_kind="directory", current_path=temp_dir),
                    chosen,
                )
                self.assertEqual(
                    local_paths.reveal_in_file_manager(chosen, reveal=True),
                    chosen,
                )


if __name__ == "__main__":
    unittest.main()
