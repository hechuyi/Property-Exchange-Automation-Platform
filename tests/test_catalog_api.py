from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.app_service import AppService
from peap.product_profile import get_product_profile
from peap_core.source_business_contract import SourceBusinessRequirement


class FakeCatalogService:
    def get_catalog(self) -> dict[str, object]:
        return {
            "active_profile": {"profile_id": "desktop_listing"},
            "visible_families": [
                {
                    "family_id": "listing",
                    "family_label": "Listing",
                    "businesses": [
                        {
                            "business_id": "equity_transfer",
                            "business_label": "股权转让",
                            "supported_surfaces": ["records", "one_click", "export"],
                        }
                    ],
                }
            ],
            "support_matrix": {
                "listing": {
                    "equity_transfer": {"records": True, "one_click": True, "export": True},
                }
            },
            "default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        }


class CatalogApiBackendTests(unittest.TestCase):
    def _assert_ok(self, payload: dict[str, object]) -> dict[str, object]:
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]  # type: ignore[return-value]

    def test_get_catalog_is_the_only_static_truth_source_for_visible_families(self) -> None:
        service = FakeCatalogService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/catalog",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(data["active_profile"]["profile_id"], "desktop_listing")
        self.assertEqual([item["family_id"] for item in data["visible_families"]], ["listing"])
        self.assertEqual(data["default_scope"]["record_family"], "listing")
        self.assertEqual(data["default_scope"]["business_id"], "equity_transfer")
        self.assertIn("support_matrix", data)
        self.assertNotIn("product_profile", data)

    def test_get_catalog_uses_business_catalog_labels_not_project_type_constants(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        with patch("desktop_backend.domain.constants.PROJECT_TYPE_LABELS", {"equity_transfer": "WRONG"}):
            data = service.get_catalog()

        listing = data["visible_families"][0]
        businesses = {item["business_id"]: item for item in listing["businesses"]}

        self.assertEqual(businesses["equity_transfer"]["business_label"], "股权转让")
        self.assertEqual(data["default_scope"], {})

    def test_get_catalog_does_not_invent_second_default_scope_truth_when_backend_default_is_missing(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {
            "effective_default_scope": {},
            "stored_preference": {
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "not_a_real_exchange",
            },
            "stale_default_metadata": {
                "is_stale": True,
                "reason": "invalid_exchange",
            },
        }

        data = service.get_catalog()

        self.assertEqual(data["default_scope"], {})

    def test_get_catalog_exposes_deal_businesses_as_executable_after_surfaces_are_complete(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        data = service.get_catalog()
        active_profile = get_product_profile(data["active_profile"]["profile_id"])
        self.assertEqual(active_profile.profile_id, data["active_profile"]["profile_id"])

        families = {item["family_id"]: item for item in data["visible_families"]}
        listing_businesses = {item["business_id"]: item for item in families["listing"]["businesses"]}
        deal_businesses = {item["business_id"]: item for item in families["deal"]["businesses"]}

        self.assertEqual(
            listing_businesses["equity_transfer"]["supported_surfaces"],
            ["records", "one_click", "export"],
        )
        self.assertEqual(
            data["support_matrix"]["listing"]["equity_transfer"],
            {"records": True, "one_click": True, "export": True},
        )
        for business_id in (
            "deal_equity_transfer",
            "deal_capital_increase",
        ):
            self.assertEqual(
                deal_businesses[business_id]["supported_surfaces"],
                ["records", "one_click", "export"],
            )
            self.assertEqual(
                data["support_matrix"]["deal"][business_id],
                {"records": True, "one_click": True, "export": True},
            )

        self.assertEqual(
            deal_businesses["deal_physical_asset"]["supported_surfaces"],
            ["records", "one_click", "export"],
        )
        self.assertEqual(
            data["support_matrix"]["deal"]["deal_physical_asset"],
            {"records": True, "one_click": True, "export": True},
        )
        self.assertEqual(
            data["surface_source_matrix"]["deal"]["deal_physical_asset"],
            {
                "records": ["cbex", "sse"],
                "one_click": ["cbex", "sse"],
                "export": ["cbex", "sse"],
            },
        )

    def test_get_catalog_splits_records_export_capability_from_one_click_runtime_executability(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        families = [
            SimpleNamespace(
                family_id="deal",
                canonical_label="Deal",
                source_ids=("sse", "cbex"),
            ),
        ]
        businesses = {
            "deal": [
                SimpleNamespace(
                    business_id="deal_equity_transfer",
                    canonical_label="Deal Equity Transfer",
                )
            ],
        }
        sources = [
            SimpleNamespace(source_id="sse", canonical_label="上交所", supported_record_families=("deal",)),
            SimpleNamespace(source_id="cbex", canonical_label="北交所", supported_record_families=("deal",)),
        ]
        runtime_bindings = [
            SimpleNamespace(
                source_id="sse",
                record_family="deal",
                business_id="deal_equity_transfer",
                implemented=True,
            ),
        ]

        with (
            patch("desktop_backend.app_service.list_family_descriptors", return_value=families),
            patch("desktop_backend.app_service.list_business_descriptors", side_effect=lambda family_id: list(businesses[family_id])),
            patch("desktop_backend.app_service.list_source_descriptors", return_value=sources),
            patch("desktop_backend.app_service.iter_source_business_bindings", return_value=runtime_bindings),
        ):
            data = service.get_catalog()

        self.assertEqual(
            data["support_matrix"]["deal"]["deal_equity_transfer"],
            {"records": True, "one_click": True, "export": True},
        )
        family = data["visible_families"][0]
        self.assertEqual(family["family_id"], "deal")
        self.assertEqual(family["businesses"][0]["supported_surfaces"], ["records", "one_click", "export"])
        self.assertEqual(
            data["surface_source_matrix"]["deal"]["deal_equity_transfer"],
            {"records": ["cbex", "sse"], "one_click": ["sse"], "export": ["cbex", "sse"]},
        )

    def test_get_catalog_exposes_new_listing_source_scope_surfaces(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        data = service.get_catalog()

        self.assertIn("shandong", data["surface_source_matrix"]["listing"]["equity_transfer"]["one_click"])
        self.assertIn("guangdong", data["surface_source_matrix"]["listing"]["equity_transfer"]["one_click"])
        self.assertIn("shenzhen", data["surface_source_matrix"]["listing"]["equity_transfer"]["one_click"])
        self.assertIn("shenzhen", data["surface_source_matrix"]["listing"]["capital_increase"]["one_click"])
        self.assertIn("shandong", data["surface_source_matrix"]["listing"]["capital_increase"]["one_click"])
        self.assertIn("guangdong", data["surface_source_matrix"]["listing"]["capital_increase"]["one_click"])
        for surface in ("records", "one_click", "export"):
            self.assertIn(
                "shandong",
                data["surface_source_matrix"]["listing"]["capital_increase"][surface],
            )
            self.assertIn(
                "guangdong",
                data["surface_source_matrix"]["listing"]["capital_increase"][surface],
            )
        for surface in ("records", "one_click", "export"):
            self.assertNotIn(
                "shandong",
                data["surface_source_matrix"]["listing"]["physical_asset"][surface],
            )
            self.assertNotIn(
                "guangdong",
                data["surface_source_matrix"]["listing"]["pre_disclosure"][surface],
            )

    def test_get_catalog_exposes_source_business_scope_requirements(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        data = service.get_catalog()

        backend_only_field = "_".join(("required", "query", "filters"))
        central_label = "央企范围限定"
        central_summary = "仅覆盖中央企业及其所属单位项目。"
        physical_label = "实物资产金额门槛"
        physical_summary = "仅覆盖挂牌价不低于 5000 万元的实物资产项目。"
        requirements = data["source_business_requirements"]
        self.assertEqual(
            requirements["listing"]["equity_transfer"]["shandong"],
            {
                "scope_policy": "central_soe_ministry_only",
                "scope_policy_label": central_label,
                "scope_policy_summary": central_summary,
            },
        )
        self.assertEqual(
            requirements["listing"]["capital_increase"]["guangdong"],
            {
                "scope_policy": "central_soe_ministry_only",
                "scope_policy_label": central_label,
                "scope_policy_summary": central_summary,
            },
        )
        self.assertEqual(
            requirements["listing"]["physical_asset"]["tpre"],
            {
                "scope_policy": "physical_asset_min_price_5000w",
                "scope_policy_label": physical_label,
                "scope_policy_summary": physical_summary,
            },
        )
        self.assertEqual(
            requirements["listing"]["physical_asset"]["cquae"],
            {
                "scope_policy": "physical_asset_min_price_5000w",
                "scope_policy_label": physical_label,
                "scope_policy_summary": physical_summary,
            },
        )
        for businesses in requirements.values():
            for sources in businesses.values():
                for requirement in sources.values():
                    self.assertNotIn(backend_only_field, requirement)
        self.assertNotIn("cbex", requirements.get("listing", {}).get("equity_transfer", {}))

    def test_get_catalog_filters_orphan_source_business_scope_requirements(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}
        orphan_requirements = [
            SourceBusinessRequirement(
                "not_registered",
                "listing",
                "equity_transfer",
                "central_soe_ministry_only",
                {},
            ),
            SourceBusinessRequirement(
                "shandong",
                "listing",
                "physical_asset",
                "central_soe_ministry_only",
                {},
            ),
            SourceBusinessRequirement(
                "shandong",
                "deal",
                "deal_equity_transfer",
                "central_soe_ministry_only",
                {},
            ),
            SourceBusinessRequirement(
                "shandong",
                "listing",
                "equity_transfer",
                "not_registered_policy",
                {},
            ),
        ]

        with patch("desktop_backend.app_service.list_source_business_requirements", return_value=orphan_requirements):
            data = service.get_catalog()

        self.assertEqual(data["source_business_requirements"], {})

    def test_get_catalog_keeps_all_registered_families_instead_of_filtering_listing_only(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        families = [
            SimpleNamespace(family_id="listing", canonical_label="Listing"),
            SimpleNamespace(family_id="deal", canonical_label="Deal"),
        ]
        businesses = {
            "listing": [SimpleNamespace(business_id="equity_transfer", canonical_label="Equity Transfer")],
            "deal": [SimpleNamespace(business_id="deal_notice", canonical_label="Deal Notice")],
        }
        sources = [
            SimpleNamespace(source_id="sse", canonical_label="上交所", supported_record_families=("listing",)),
            SimpleNamespace(source_id="deal_board", canonical_label="成交板块", supported_record_families=("deal",)),
        ]

        with (
            patch("desktop_backend.app_service.list_family_descriptors", return_value=families),
            patch("desktop_backend.app_service.list_business_descriptors", side_effect=lambda family_id: list(businesses[family_id])),
            patch("desktop_backend.app_service.list_source_descriptors", return_value=sources),
        ):
            data = service.get_catalog()

        self.assertEqual([item["family_id"] for item in data["visible_families"]], ["listing", "deal"])
        self.assertEqual([item["source_id"] for item in data["sources"]], ["sse", "deal_board"])
        deal_family = next(item for item in data["visible_families"] if item["family_id"] == "deal")
        self.assertEqual(deal_family["businesses"][0]["business_id"], "deal_notice")


if __name__ == "__main__":
    unittest.main()
