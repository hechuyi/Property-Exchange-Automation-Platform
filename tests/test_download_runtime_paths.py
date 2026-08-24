from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from peap.download_runtime import DownloadDriverRuntime, _task_path_relative_to_output_root


class _Capabilities:
    supports_list_only = True
    supports_prefetched_candidates = True


class _Spec:
    downloader_cls = object


class DownloadRuntimePathTest(unittest.TestCase):
    def _runtime(self, output_root: str, task_root: str) -> DownloadDriverRuntime:
        return DownloadDriverRuntime(
            downloader=object(),
            spec=_Spec(),  # type: ignore[arg-type]
            capabilities=_Capabilities(),  # type: ignore[arg-type]
            output_root=output_root,
            task_output_root=task_root,
        )

    def test_relative_download_path_is_normalized_inside_task_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            task = output / "sse__listing__equity_transfer"
            target = task / "2026年7月" / "P001.html"
            target.parent.mkdir(parents=True)
            target.write_text("snapshot", encoding="utf-8")

            relative = _task_path_relative_to_output_root(
                self._runtime(str(output), str(task)),
                "2026年7月/P001.html",
            )

            self.assertEqual(relative, "sse__listing__equity_transfer/2026年7月/P001.html")

    def test_task_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            output = Path(temp_dir)
            task = output / "sse__listing__equity_transfer"
            task.symlink_to(Path(outside), target_is_directory=True)
            external = Path(outside) / "2026年7月" / "P001.html"
            external.parent.mkdir(parents=True)
            external.write_text("external", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "symlinks"):
                _task_path_relative_to_output_root(
                    self._runtime(str(output), str(task)),
                    "2026年7月/P001.html",
                )

    def test_download_file_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            output = Path(temp_dir)
            task = output / "sse__listing__equity_transfer"
            task.mkdir()
            external = Path(outside) / "P001.html"
            external.write_text("external", encoding="utf-8")
            target = task / "P001.html"
            target.symlink_to(external)

            with self.assertRaisesRegex(ValueError, "symlinks"):
                _task_path_relative_to_output_root(
                    self._runtime(str(output), str(task)),
                    "P001.html",
                )


if __name__ == "__main__":
    unittest.main()
