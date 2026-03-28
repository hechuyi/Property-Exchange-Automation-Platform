from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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

    def _resolved_r005_params_for_json_config(self, config_name: str) -> dict[str, str]:
        config_path = Path(self._repo_root()) / "peap_postprocess" / "ppe_config" / config_name
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = os.path.join(tmp_dir, "PEAP_DATA")
            input_dir = os.path.join(data_root, "outputs", "excel")
            os.makedirs(input_dir, exist_ok=True)

            original = os.environ.get("PEAP_DATA_ROOT")
            os.environ["PEAP_DATA_ROOT"] = data_root
            try:
                config = load_config(str(config_path))
            finally:
                if original is None:
                    os.environ.pop("PEAP_DATA_ROOT", None)
                else:
                    os.environ["PEAP_DATA_ROOT"] = original

        rule = config.rules["R005_normalize_source_type"]
        return {
            key: str(rule.params[key])
            for key in (
                "transferor_type_mapping_file",
                "transferor_group_mapping_file",
            "group_group_mapping_file",
            "group_type_mapping_file",
        )
        }

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

    def test_all_shipped_postprocess_configs_use_the_same_mapping_template_names(self) -> None:
        expected_suffixes = {
            "transferor_type_mapping_file": "transferor_type_mapping.template.csv",
            "transferor_group_mapping_file": "transferor_group_mapping.template.csv",
            "group_group_mapping_file": "group_group_mapping.template.csv",
            "group_type_mapping_file": "group_type_mapping.template.csv",
        }

        for config_name in ("postprocess.json", "postprocess_external_template.json"):
            resolved = self._resolved_r005_params_for_json_config(config_name)
            for key, suffix in expected_suffixes.items():
                self.assertTrue(
                    resolved[key].endswith(suffix),
                    msg=f"{config_name} expected {key} to end with {suffix}, got {resolved[key]}",
                )
                self.assertTrue(os.path.isfile(resolved[key]), msg=f"{config_name} missing {key}: {resolved[key]}")

        yaml_path = Path(self._repo_root()) / "peap_postprocess" / "ppe_config" / "postprocess.yaml"
        yaml_content = yaml_path.read_text(encoding="utf-8")
        for key, suffix in expected_suffixes.items():
            self.assertIn(f"{key}: ../ppe_config/{suffix}", yaml_content)

    def test_business_user_guide_matches_shipped_mapping_template_names(self) -> None:
        guide_path = Path(self._repo_root()) / "peap_postprocess" / "ppe_business_user_guide.md"
        guide_content = guide_path.read_text(encoding="utf-8")
        expected_filenames = (
            "transferor_group_mapping.template.csv",
            "transferor_type_mapping.template.csv",
            "group_group_mapping.template.csv",
            "group_type_mapping.template.csv",
        )
        retired_filenames = (
            "transferor_group_mapping_template.csv",
            "transferor_type_mapping_template.csv",
            "group_group_mapping_template.csv",
            "group_type_mapping_template.csv",
        )

        for filename in expected_filenames:
            self.assertIn(filename, guide_content)
        for filename in retired_filenames:
            self.assertNotIn(filename, guide_content)


if __name__ == "__main__":
    unittest.main()
