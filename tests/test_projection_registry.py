from __future__ import annotations

import unittest

from peap.projection_registry import resolve_projection_profile


class ProjectionRegistryTest(unittest.TestCase):
    def test_business_alias_resolution_never_crosses_record_family(self) -> None:
        listing = resolve_projection_profile("listing", "股权转让")
        deal = resolve_projection_profile("deal", "股权转让")
        listing_only_alias = resolve_projection_profile("deal", "挂牌产权交易")
        deal_only_alias = resolve_projection_profile("listing", "成交股权转让")

        self.assertIsNotNone(listing)
        self.assertIsNotNone(deal)
        assert listing is not None
        assert deal is not None
        self.assertEqual(listing.profile_id, "listing/equity_transfer")
        self.assertEqual(deal.profile_id, "deal/equity_transfer")
        self.assertIsNone(listing_only_alias)
        self.assertIsNone(deal_only_alias)

    def test_resolves_deal_projection_profile_from_family_specific_business_id(self) -> None:
        profile = resolve_projection_profile("deal", "deal_equity_transfer")

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.record_family, "deal")
        self.assertEqual(profile.business_id, "deal_equity_transfer")
        self.assertEqual(profile.output_kind, "deal_equity_transfer")
        self.assertEqual(profile.output_stem, "成交_股权转让")

    def test_resolves_deal_projection_profile_from_project_type_label(self) -> None:
        profile = resolve_projection_profile("deal", "股权转让")

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.record_family, "deal")
        self.assertEqual(profile.business_id, "deal_equity_transfer")

    def test_deal_profiles_use_family_specific_output_kinds(self) -> None:
        equity = resolve_projection_profile("deal", "deal_equity_transfer")
        physical = resolve_projection_profile("deal", "deal_physical_asset")
        capital = resolve_projection_profile("deal", "deal_capital_increase")

        self.assertIsNotNone(equity)
        self.assertIsNotNone(physical)
        self.assertIsNotNone(capital)
        assert equity is not None
        assert physical is not None
        assert capital is not None

        self.assertEqual(equity.output_kind, "deal_equity_transfer")
        self.assertEqual(physical.output_kind, "deal_physical_asset")
        self.assertEqual(capital.output_kind, "deal_capital_increase")
        self.assertEqual(equity.output_stem, "成交_股权转让")
        self.assertEqual(physical.output_stem, "成交_实物资产")
        self.assertEqual(capital.output_stem, "成交_增资扩股")


if __name__ == "__main__":
    unittest.main()
