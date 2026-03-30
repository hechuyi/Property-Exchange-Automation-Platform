from __future__ import annotations

import os
import tempfile
import unittest

from peap.product_profile import (
    DEFAULT_PRODUCT_PROFILE_ID,
    get_default_postprocess_config_path,
    get_product_profile,
    list_product_profiles,
)
from peap_core.source_catalog import list_source_descriptors
from peap_postprocess.postprocess_engine.config import load_config


class ProductProfileTest(unittest.TestCase):
    def test_desktop_listing_is_the_only_shipped_product_profile(self) -> None:
        profiles = list_product_profiles()

        self.assertEqual([profile.profile_id for profile in profiles], ["desktop_listing"])
        self.assertEqual(DEFAULT_PRODUCT_PROFILE_ID, "desktop_listing")

    def test_desktop_listing_profile_has_fixed_kernel_configuration(self) -> None:
        profile = get_product_profile()
        expected_source_ids = tuple(
            source.source_id for source in list_source_descriptors(record_family="listing")
        )

        self.assertEqual(profile.profile_id, "desktop_listing")
        self.assertEqual(profile.record_family, "listing")
        self.assertEqual(profile.source_ids, expected_source_ids)
        self.assertEqual(profile.parser_compat, "listing_v1")
        self.assertEqual(profile.postprocess_profile, "postprocess_external")
        self.assertEqual(profile.export_profile, "ready_export")
        self.assertEqual(profile.readiness_policy, "browser_runtime_required")

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            get_product_profile("unknown-profile")

    def test_default_postprocess_profile_bundle_is_loadable_and_self_contained(self) -> None:
        config_path = get_default_postprocess_config_path()

        self.assertTrue(config_path)
        self.assertTrue(os.path.isfile(config_path))

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = os.path.join(tmp_dir, "PEAP_DATA")
            os.makedirs(os.path.join(data_root, "outputs", "excel"), exist_ok=True)
            original = os.environ.get("PEAP_DATA_ROOT")
            os.environ["PEAP_DATA_ROOT"] = data_root
            try:
                config = load_config(config_path)
            finally:
                if original is None:
                    os.environ.pop("PEAP_DATA_ROOT", None)
                else:
                    os.environ["PEAP_DATA_ROOT"] = original

            rule = config.rules["R005_normalize_source_type"]
            for key in (
                "transferor_type_mapping_file",
                "transferor_group_mapping_file",
                "group_group_mapping_file",
                "group_type_mapping_file",
            ):
                resolved_path = str(rule.params[key])
                self.assertTrue(os.path.isfile(resolved_path), msg=f"missing bundled asset: {key} -> {resolved_path}")


if __name__ == "__main__":
    unittest.main()
