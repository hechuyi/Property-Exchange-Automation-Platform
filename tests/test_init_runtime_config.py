from __future__ import annotations

import os
import tempfile
import unittest

from peap_core.runtime import load_json_object
from scripts.init_runtime_config import (
    default_template_path,
    main,
    write_runtime_config_from_template,
)


class InitRuntimeConfigTest(unittest.TestCase):
    def test_write_runtime_config_from_template_overrides_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "runtime.json")
            data_root = os.path.join(tmp_dir, "PEAP_DATA")

            written = write_runtime_config_from_template(
                output_path=output_path,
                template_path=default_template_path(),
                data_root=data_root,
            )

            payload = load_json_object(written, encoding="utf-8", label="runtime config")
            self.assertEqual(written, output_path)
            self.assertEqual(payload["paths"]["data_root"], data_root)

    def test_main_returns_error_when_output_exists_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "runtime.json")
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write("{}")

            exit_code = main(
                [
                    "--output",
                    output_path,
                    "--template",
                    default_template_path(),
                ]
            )

            self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
