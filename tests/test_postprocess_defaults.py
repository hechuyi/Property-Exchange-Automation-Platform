from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from peap.streaming_models import PostProcessFinding
from peap.streaming_postprocess import (
    _build_business_resolution_finding,
    finalize_streaming_payload,
    normalize_record_payload,
    reapply_optional_rule_findings,
)
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

    def _load_config(self, config_name: str):
        config_path = Path(self._repo_root()) / "peap_postprocess" / "ppe_config" / config_name
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = os.path.join(tmp_dir, "PEAP_DATA")
            os.makedirs(os.path.join(data_root, "outputs", "excel"), exist_ok=True)

            original = os.environ.get("PEAP_DATA_ROOT")
            os.environ["PEAP_DATA_ROOT"] = data_root
            try:
                return load_config(str(config_path))
            finally:
                if original is None:
                    os.environ.pop("PEAP_DATA_ROOT", None)
                else:
                    os.environ["PEAP_DATA_ROOT"] = original

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

    def test_all_shipped_postprocess_configs_only_enable_non_mapping_runtime_rules(self) -> None:
        expected_rule_ids = {
            "R006_derive_listing_times",
            "R010_filter_scrap_physical_asset",
            "R011_person_transferor_private",
            "R012_clear_invalid_group_placeholder",
        }
        retired_rule_ids = {
            "R001_group_mapping_fill",
            "R002_group_conflict_flag",
            "R005_normalize_source_type",
        }
        for config_name in ("postprocess.json", "postprocess_external_template.json"):
            config = self._load_config(config_name)
            self.assertEqual(
                {
                    rule_id
                    for rule_id, rule in dict(config.rules or {}).items()
                    if bool(rule.enabled)
                },
                expected_rule_ids,
            )
            for rule_id in retired_rule_ids:
                self.assertNotIn(rule_id, config.rules, msg=f"{config_name} should retire {rule_id}")

        yaml_path = Path(self._repo_root()) / "peap_postprocess" / "ppe_config" / "postprocess.yaml"
        yaml_content = yaml_path.read_text(encoding="utf-8")
        self.assertIn("R010_filter_scrap_physical_asset", yaml_content)
        self.assertIn("R012_clear_invalid_group_placeholder", yaml_content)
        self.assertIn("R011_person_transferor_private", yaml_content)
        self.assertIn("R006_derive_listing_times", yaml_content)
        for retired_rule_id in retired_rule_ids:
            self.assertNotIn(retired_rule_id, yaml_content)

    def test_business_user_guide_no_longer_describes_ppe_mapping_rules_as_runtime_behavior(self) -> None:
        guide_path = Path(self._repo_root()) / "docs" / "operations.md"
        guide_content = guide_path.read_text(encoding="utf-8")
        self.assertNotIn("R005_normalize_source_type", guide_content)
        self.assertNotIn("transferor_group_mapping_file", guide_content)
        self.assertNotIn("transferor_type_mapping_file", guide_content)
        self.assertNotIn("group_group_mapping_file", guide_content)
        self.assertNotIn("group_type_mapping_file", guide_content)

    def test_finalize_streaming_payload_rejects_scalar_findings(self) -> None:
        with self.assertRaisesRegex(TypeError, "findings"):
            finalize_streaming_payload(
                {"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
                findings="bad findings",  # type: ignore[arg-type]
            )

    def test_normalize_record_payload_rejects_mapping_findings(self) -> None:
        with self.assertRaisesRegex(TypeError, "findings"):
            normalize_record_payload(
                parser_payload={"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
                postprocess_payload={},
                findings={
                    "type": "mapping_missing",
                    "evidence": {"missing_fields": ["类型"]},
                },  # type: ignore[arg-type]
            )

    def test_reapply_optional_rule_findings_rejects_scalar_findings(self) -> None:
        with self.assertRaisesRegex(TypeError, "findings"):
            reapply_optional_rule_findings(
                parser_payload={"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
                postprocess_payload={},
                findings="bad findings",  # type: ignore[arg-type]
                source_file="streaming.html",
            )

    def test_finalize_streaming_payload_rejects_non_postprocess_finding_items(self) -> None:
        with self.assertRaisesRegex(TypeError, r"findings\[\*\]"):
            finalize_streaming_payload(
                {"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
                findings=[
                    {
                        "severity": "warn",
                        "type": "mapping_missing",
                        "message": "bad finding item",
                        "evidence": {"missing_fields": ["类型"]},
                    }
                ],  # type: ignore[list-item]
            )

    def test_business_resolution_finding_rejects_scalar_diagnostic_gap_codes(self) -> None:
        with self.assertRaisesRegex(TypeError, "diagnostic_gap_codes"):
            _build_business_resolution_finding(
                raw_business_label="",
                diagnostic_gap_codes="missing_type",  # type: ignore[arg-type]
            )

    def test_finalize_streaming_payload_accepts_none_findings(self) -> None:
        _, findings = finalize_streaming_payload(
            {"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
            findings=None,
        )

        self.assertEqual(findings, [])

    def test_finalize_streaming_payload_preserves_valid_findings(self) -> None:
        finding = PostProcessFinding(
            severity="warn",
            type="canonical_field_missing",
            message="valid finding",
            evidence={"missing_fields": ["project_code"]},
        )

        _, findings = finalize_streaming_payload(
            {"项目类型": "股权转让", "类型": "国资", "转让方": "测试公司"},
            findings=[finding],
        )

        self.assertEqual(findings, [finding])


if __name__ == "__main__":
    unittest.main()
