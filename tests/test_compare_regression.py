from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from peap_postprocess.compare_regression import _default_output_path


class CompareRegressionTest(unittest.TestCase):
    def test_default_output_path_uses_injected_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(COMPARE_REPORT_DIR=tmp_dir)

            output_path = _default_output_path(config_obj=config)

            self.assertTrue(output_path.startswith(os.path.abspath(tmp_dir)))
            self.assertTrue(output_path.endswith(".jsonl"))
            self.assertIn("ppe_regression_compare_", os.path.basename(output_path))


if __name__ == "__main__":
    unittest.main()
