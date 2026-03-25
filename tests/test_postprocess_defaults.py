from __future__ import annotations

import os
import tempfile
import unittest

from peap_postprocess.build_type_unresolved_mapping_list import (
    DEFAULT_CONFIG_PATH as BUILDER_DEFAULT_CONFIG_PATH,
)
from peap_postprocess.build_type_unresolved_mapping_list import (
    _build_arg_parser,
)
from peap_postprocess.postprocess_engine.config import load_config
from peap_postprocess.postprocess_engine.runner import (
    DEFAULT_CONFIG_PATH as RUNNER_DEFAULT_CONFIG_PATH,
)


class PostProcessDefaultsTest(unittest.TestCase):
    def _repo_root(self) -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_unresolved_builder_defaults_to_external_template(self) -> None:
        parser = _build_arg_parser()
        args = parser.parse_args([])

        self.assertEqual(
            os.path.abspath(args.config),
            os.path.abspath(BUILDER_DEFAULT_CONFIG_PATH),
        )
        self.assertEqual(
            os.path.abspath(args.config),
            os.path.abspath(RUNNER_DEFAULT_CONFIG_PATH),
        )
        self.assertTrue(str(args.config).endswith("postprocess_external_template.json"))

    def test_external_template_resolves_paths_under_peap_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = os.path.join(tmp_dir, "PEAP_DATA")
            input_dir = os.path.join(data_root, "outputs", "excel")
            os.makedirs(input_dir, exist_ok=True)

            original = os.environ.get("PEAP_DATA_ROOT")
            os.environ["PEAP_DATA_ROOT"] = data_root
            try:
                config = load_config(RUNNER_DEFAULT_CONFIG_PATH)
            finally:
                if original is None:
                    os.environ.pop("PEAP_DATA_ROOT", None)
                else:
                    os.environ["PEAP_DATA_ROOT"] = original

            expected_output_dir = os.path.join(data_root, "outputs", "postprocess")
            expected_audit_dir = os.path.join(data_root, "outputs", "postprocess_audit")
            repo_root = self._repo_root()

            self.assertEqual(config.input_dir, os.path.abspath(input_dir))
            self.assertEqual(config.output_dir, os.path.abspath(expected_output_dir))
            self.assertEqual(config.audit_dir, os.path.abspath(expected_audit_dir))
            self.assertIn(os.path.abspath(expected_output_dir), config.exclude_dirs)
            self.assertFalse(
                os.path.normcase(config.output_dir).startswith(os.path.normcase(repo_root + os.sep))
            )
            self.assertFalse(
                os.path.normcase(config.audit_dir).startswith(os.path.normcase(repo_root + os.sep))
            )


if __name__ == "__main__":
    unittest.main()
