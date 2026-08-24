from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peap.downloaders.snapshot_utils import remove_snapshot


class SnapshotUtilsTest(unittest.TestCase):
    def test_remove_snapshot_preserves_html_when_assets_delete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "page.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            assets_dir = Path(temp_dir) / "page_files"
            assets_dir.mkdir()
            (assets_dir / "old.css").write_text("body {}", encoding="utf-8")

            with (
                patch(
                    "peap.downloaders.snapshot_utils.shutil.rmtree",
                    side_effect=OSError("locked"),
                ),
                self.assertRaises(OSError),
            ):
                remove_snapshot(str(html_path))

            self.assertTrue(html_path.is_file())


if __name__ == "__main__":
    unittest.main()
