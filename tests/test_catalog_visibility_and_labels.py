from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop_backend.app_service import AppService
from desktop_backend.domain.record_projection import build_mixed_record_display_values


class CatalogVisibilityAndLabelsTest(unittest.TestCase):
    def test_get_catalog_excludes_family_without_enabled_source_support(self) -> None:
        service = AppService.__new__(AppService)
        service.get_basic_settings = lambda: {}

        families = [
            SimpleNamespace(
                family_id="listing",
                canonical_label="Listing",
                source_ids=("sse",),
            ),
            SimpleNamespace(
                family_id="placeholder",
                canonical_label="Placeholder",
                source_ids=("future_source",),
            ),
        ]
        businesses = {
            "listing": [
                SimpleNamespace(
                    business_id="equity_transfer",
                    canonical_label="股权转让",
                )
            ],
            "placeholder": [
                SimpleNamespace(
                    business_id="future_business",
                    canonical_label="Future Business",
                )
            ],
        }
        sources = [
            SimpleNamespace(
                source_id="sse",
                canonical_label="上交所",
                supported_record_families=("listing",),
                enabled=True,
            ),
        ]

        with (
            patch("desktop_backend.app_service.list_family_descriptors", return_value=families),
            patch(
                "desktop_backend.app_service.list_business_descriptors",
                side_effect=lambda family_id: list(businesses[family_id]),
            ),
            patch("desktop_backend.app_service.list_source_descriptors", return_value=sources),
            patch("desktop_backend.app_service.iter_source_business_bindings", return_value=[]),
        ):
            data = service.get_catalog()

        self.assertEqual(
            [family["family_id"] for family in data["visible_families"]],
            ["listing"],
        )
        self.assertEqual(data["visibility"]["visible_families"], ["listing"])
        self.assertIn("listing", data["support_matrix"])
        self.assertIn("listing", data["surface_source_matrix"])
        self.assertNotIn("placeholder", data["support_matrix"])
        self.assertNotIn("placeholder", data["surface_source_matrix"])

    def test_deal_mixed_display_business_label_comes_from_business_catalog(self) -> None:
        patched_descriptor = SimpleNamespace(
            business_id="deal_equity_transfer",
            family_id="deal",
            canonical_label="测试成交标签",
        )

        with patch(
            "desktop_backend.domain.record_projection.get_business_descriptor",
            return_value=patched_descriptor,
            create=True,
        ):
            values = build_mixed_record_display_values(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "project_code": "D32026SH000001",
                    "project_name": "成交展示项目",
                    "exchange": "sse",
                    "canonical_record": {
                        "record_family": "deal",
                        "business_identity": {
                            "business_id": "deal_equity_transfer",
                        },
                        "canonical_fields": {
                            "project_code": "D32026SH000001",
                            "project_name": "成交展示项目",
                            "exchange": "上交所",
                        },
                    },
                }
            )

        self.assertEqual(values["业务"], "测试成交标签")


if __name__ == "__main__":
    unittest.main()
