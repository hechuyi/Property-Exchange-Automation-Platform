from __future__ import annotations

import os
import tempfile
import unittest

from peap.product_profile import (
    DEFAULT_PRODUCT_PROFILE_ID,
    ProductProfile,
    get_default_postprocess_config_path,
    get_product_profile,
    list_product_profiles,
    validate_product_profiles,
)
from peap_core.family_catalog import get_family_descriptor
from peap_postprocess.postprocess_engine.config import load_config


class ProductProfileTest(unittest.TestCase):
    def test_listing_and_deal_product_profiles_are_shipped(self) -> None:
        profiles = list_product_profiles()

        self.assertEqual([profile.profile_id for profile in profiles], ["desktop_listing", "desktop_deal"])
        self.assertEqual(DEFAULT_PRODUCT_PROFILE_ID, "desktop_listing")

    def test_desktop_listing_profile_has_fixed_kernel_configuration(self) -> None:
        profile = get_product_profile()
        family = get_family_descriptor("listing")

        self.assertEqual(profile.profile_id, "desktop_listing")
        self.assertEqual(profile.record_family, family.family_id)
        self.assertEqual(profile.source_ids, family.source_ids)
        self.assertEqual(profile.postprocess_profile, "postprocess_external")
        self.assertEqual(profile.export_profile, "ready_export")
        self.assertEqual(profile.readiness_policy, "browser_runtime_required")

    def test_desktop_deal_profile_source_scope_matches_deal_family(self) -> None:
        profile = get_product_profile("desktop_deal")
        family = get_family_descriptor("deal")

        self.assertEqual(profile.profile_id, "desktop_deal")
        self.assertEqual(profile.record_family, family.family_id)
        self.assertEqual(profile.source_ids, ("sse", "cbex", "tpre", "cquae"))
        self.assertEqual(profile.source_ids, family.source_ids)
        self.assertEqual(profile.postprocess_profile, "postprocess_external")
        self.assertEqual(profile.export_profile, "ready_export")
        self.assertEqual(profile.readiness_policy, "browser_runtime_required")

    def test_product_profile_can_be_resolved_by_record_family_without_listing_default(self) -> None:
        profile = get_product_profile(record_family="deal")

        self.assertEqual(profile.profile_id, "desktop_deal")
        self.assertEqual(profile.record_family, "deal")

        with self.assertRaises(ValueError):
            get_product_profile("desktop_listing", record_family="deal")

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            get_product_profile("unknown-profile")

    def test_product_profile_registry_rejects_family_profile_drift(self) -> None:
        family = get_family_descriptor("listing")
        mismatched_profiles = (
            ProductProfile(
                profile_id="desktop_listing_shadow",
                family_id=family.family_id,
                source_ids=family.source_ids,
                postprocess_profile="postprocess_external",
                export_profile="ready_export",
                readiness_policy="browser_runtime_required",
            ),
        )

        with self.assertRaises(ValueError):
            validate_product_profiles(mismatched_profiles)

        with self.assertRaises(ValueError):
            validate_product_profiles(
                (
                    ProductProfile(
                        profile_id=family.default_product_profile_id,
                        family_id=family.family_id,
                        source_ids=("bogus",),
                        postprocess_profile="postprocess_external",
                        export_profile="ready_export",
                        readiness_policy="browser_runtime_required",
                    ),
                )
            )

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

            self.assertNotIn("R005_normalize_source_type", config.rules)
            self.assertEqual(
                {
                    rule_id
                    for rule_id, rule in dict(config.rules or {}).items()
                    if bool(rule.enabled)
                },
                {
                    "R006_derive_listing_times",
                    "R010_filter_scrap_physical_asset",
                    "R011_person_transferor_private",
                    "R012_clear_invalid_group_placeholder",
                },
            )
            self.assertTrue(
                all(
                    rule.record_families == ("listing",)
                    for rule in dict(config.rules or {}).values()
                    if bool(rule.enabled)
                )
            )


if __name__ == "__main__":
    unittest.main()
