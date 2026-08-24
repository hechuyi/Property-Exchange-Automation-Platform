from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch

from peap_core.business_catalog import (
    BusinessDescriptor,
    get_business_descriptor,
    list_business_descriptors,
    resolve_business_descriptor,
    validate_business_descriptors,
    validate_family_business_alignment,
)
from peap_core.family_catalog import (
    FamilyDescriptor,
    get_family_descriptor,
    list_declared_source_business_support,
    list_family_descriptors,
    resolve_family_descriptor,
)
from peap_core.source_business_contract import (
    SourceBusinessRequirement,
    get_export_readiness_requirement,
    get_export_workbook_support,
    get_optional_postprocess_rule_requirement,
    get_scope_policy_descriptor,
    get_source_business_requirement,
    list_export_workbook_support,
    list_optional_postprocess_rule_requirements,
    list_scope_policy_descriptors,
    list_source_business_requirements,
    validate_source_business_requirements,
)
from peap_core.source_catalog import list_source_descriptors, source_ids_for_record_family


class FamilyBusinessCatalogTest(unittest.TestCase):
    def test_listing_and_deal_are_registered_family_descriptors(self) -> None:
        listing = get_family_descriptor("listing")
        expected_listing_source_ids = (
            "sse",
            "cbex",
            "tpre",
            "cquae",
            "shandong",
            "guangdong",
            "shenzhen",
        )
        expected_deal_source_ids = ("sse", "cbex", "tpre", "cquae")
        deal = get_family_descriptor("deal")

        self.assertEqual(listing.family_id, "listing")
        self.assertEqual(resolve_family_descriptor(" LISTING ").family_id, "listing")
        self.assertIn(listing, list_family_descriptors())
        self.assertEqual(listing.source_ids, expected_listing_source_ids)
        self.assertEqual(
            tuple(source.source_id for source in list_source_descriptors(record_family="listing")),
            expected_listing_source_ids,
        )
        self.assertEqual(listing.source_ids, source_ids_for_record_family("listing"))
        self.assertEqual(listing.default_product_profile_id, "desktop_listing")
        self.assertEqual(deal.family_id, "deal")
        self.assertEqual(resolve_family_descriptor(" deal ").family_id, "deal")
        self.assertIn(deal, list_family_descriptors())
        self.assertEqual(deal.source_ids, expected_deal_source_ids)
        self.assertEqual(
            tuple(source.source_id for source in list_source_descriptors(record_family="deal")),
            expected_deal_source_ids,
        )
        self.assertEqual(deal.source_ids, source_ids_for_record_family("deal"))
        self.assertEqual(deal.default_product_profile_id, "desktop_deal")

    def test_listing_family_source_compatibility_is_not_derived_from_source_catalog(self) -> None:
        import peap_core.family_catalog as family_catalog

        expected_listing_source_ids = (
            "sse",
            "cbex",
            "tpre",
            "cquae",
            "shandong",
            "guangdong",
            "shenzhen",
        )
        expected_deal_source_ids = ("sse", "cbex", "tpre", "cquae")

        with patch(
            "peap_core.source_catalog.source_ids_for_record_family",
            return_value=("bogus",),
        ):
            reloaded = importlib.reload(family_catalog)
            self.assertEqual(reloaded.get_family_descriptor("listing").source_ids, expected_listing_source_ids)
            self.assertEqual(reloaded.get_family_descriptor("deal").source_ids, expected_deal_source_ids)

        importlib.reload(family_catalog)

    def test_declared_source_business_support_is_source_specific(self) -> None:
        declared = {
            (item.family_id, item.business_id): item.source_ids
            for item in list_declared_source_business_support()
        }

        self.assertEqual(
            declared[("listing", "equity_transfer")],
            ("sse", "cbex", "tpre", "cquae", "shandong", "guangdong", "shenzhen"),
        )
        self.assertEqual(
            declared[("listing", "capital_increase")],
            ("sse", "cbex", "tpre", "cquae", "shandong", "guangdong", "shenzhen"),
        )
        self.assertEqual(
            declared[("listing", "physical_asset")],
            ("sse", "cbex", "tpre", "cquae"),
        )
        self.assertEqual(
            declared[("listing", "pre_disclosure")],
            ("sse", "cbex", "tpre", "cquae"),
        )
        self.assertEqual(
            declared[("deal", "deal_physical_asset")],
            ("sse", "cbex"),
        )
        self.assertEqual(
            declared[("deal", "deal_equity_transfer")],
            ("sse", "cbex", "tpre", "cquae"),
        )
        self.assertEqual(
            declared[("deal", "deal_capital_increase")],
            ("sse", "cbex", "tpre", "cquae"),
        )

    def test_regional_listing_business_scope_requirements_are_declared(self) -> None:
        expected = {
            ("shandong", "listing", "equity_transfer"): {"systemSource": "CQY"},
            ("shandong", "listing", "capital_increase"): {"systemSource": "CQY"},
            ("guangdong", "listing", "equity_transfer"): {"IN_CQLSGX": "'GQ100101'"},
            ("guangdong", "listing", "capital_increase"): {"IN_CQLSGX": "'1C100301'"},
            (
                "shenzhen",
                "listing",
                "equity_transfer",
            ): {
                "channelIds": ("3226",),
                "targetColumnIds": ("3961",),
                "projectSubjections": ("央属",),
            },
            (
                "shenzhen",
                "listing",
                "capital_increase",
            ): {
                "channelIds": ("3238",),
                "targetColumnIds": ("3966",),
                "projectSubjections": ("央属",),
            },
            ("tpre", "listing", "physical_asset"): {"priceBegin": 5000},
            (
                "cquae",
                "listing",
                "physical_asset",
            ): {"price": ("5000万-1亿", "1亿以上")},
        }

        for key, expected_filters in expected.items():
            with self.subTest(scope=key):
                descriptor = get_source_business_requirement(*key)
                expected_policy = (
                    "physical_asset_min_price_5000w"
                    if key[2] == "physical_asset"
                    else "central_soe_ministry_only"
                )
                self.assertEqual(descriptor.scope_policy, expected_policy)
                self.assertEqual(descriptor.required_query_filters, expected_filters)

    def test_scope_policy_descriptors_are_complete_display_metadata(self) -> None:
        descriptors = list_scope_policy_descriptors()
        descriptor_ids = {item.policy_id for item in descriptors}
        declared_policy_ids = {
            item.scope_policy
            for item in list_source_business_requirements()
            if item.scope_policy
        }

        self.assertEqual(descriptor_ids, declared_policy_ids)
        self.assertEqual(len(descriptors), len(descriptor_ids))
        for descriptor in descriptors:
            with self.subTest(policy_id=descriptor.policy_id):
                self.assertEqual(get_scope_policy_descriptor(descriptor.policy_id), descriptor)
                self.assertTrue(descriptor.label)
                self.assertTrue(descriptor.summary)
                self.assertNotEqual(descriptor.label, descriptor.policy_id)

    def test_source_business_requirements_reject_catalog_orphans(self) -> None:
        valid = get_source_business_requirement("shandong", "listing", "equity_transfer")
        cases = (
            SourceBusinessRequirement(
                source_id="not_registered",
                record_family=valid.record_family,
                business_id=valid.business_id,
                scope_policy=valid.scope_policy,
                required_query_filters=valid.required_query_filters,
            ),
            SourceBusinessRequirement(
                source_id=valid.source_id,
                record_family="deal",
                business_id=valid.business_id,
                scope_policy=valid.scope_policy,
                required_query_filters=valid.required_query_filters,
            ),
            SourceBusinessRequirement(
                source_id="shandong",
                record_family="listing",
                business_id="physical_asset",
                scope_policy=valid.scope_policy,
                required_query_filters=valid.required_query_filters,
            ),
        )

        for requirement in cases:
            with self.subTest(requirement=requirement):
                with self.assertRaises(ValueError):
                    validate_source_business_requirements((requirement,))

    def test_deal_capital_increase_export_readiness_requirements_are_declared(self) -> None:
        requirement = get_export_readiness_requirement("deal", "deal_capital_increase")

        self.assertEqual(requirement.record_family, "deal")
        self.assertEqual(requirement.business_id, "deal_capital_increase")
        self.assertTrue(requirement.requires_non_summary_investor)
        self.assertTrue(requirement.requires_investor_amount)
        self.assertFalse(requirement.requires_deal_price)

    def test_deal_price_and_date_audit_export_readiness_requirements_are_declared(self) -> None:
        for business_id in ("deal_physical_asset", "deal_equity_transfer"):
            with self.subTest(business_id=business_id):
                requirement = get_export_readiness_requirement("deal", business_id)

                self.assertTrue(requirement.requires_deal_price)
                self.assertEqual(
                    requirement.deal_date_policy,
                    "deal_date_or_collection_date_audit",
                )
                self.assertTrue(requirement.allows_collection_date_audit_fallback)

        capital_requirement = get_export_readiness_requirement("deal", "deal_capital_increase")
        self.assertFalse(capital_requirement.requires_deal_price)
        self.assertEqual(
            capital_requirement.deal_date_policy,
            "deal_date_or_collection_date_audit",
        )
        self.assertTrue(capital_requirement.allows_collection_date_audit_fallback)

    def test_physical_asset_scrap_filter_requirement_is_listing_only(self) -> None:
        requirement = get_optional_postprocess_rule_requirement(
            "listing",
            "physical_asset",
            "R010_filter_scrap_physical_asset",
        )

        self.assertEqual(requirement.record_family, "listing")
        self.assertEqual(requirement.business_id, "physical_asset")
        self.assertEqual(requirement.rule_id, "R010_filter_scrap_physical_asset")
        self.assertEqual(requirement.purpose, "scrap_disposal_filter")
        self.assertTrue(requirement.optional)
        self.assertTrue(requirement.listing_only)
        self.assertNotIn(
            ("deal", "physical_asset", "R010_filter_scrap_physical_asset"),
            {
                (item.record_family, item.business_id, item.rule_id)
                for item in list_optional_postprocess_rule_requirements()
            },
        )

    def test_core_listing_query_taxonomy_is_declared(self) -> None:
        expected = {
            ("sse", "physical_asset"): (
                {"endpoint": "/prjs/realright/list", "project_type": "ZICHANZHUANRANG", "gplx": "2"},
            ),
            ("sse", "equity_transfer"): (
                {"endpoint": "/prjs/equity/list", "project_type": "CHANQUAN", "gplx": "2", "XMLX": "2"},
            ),
            ("sse", "capital_increase"): (
                {"endpoint": "/prjs/capitalincrease/list", "project_type": "ZENGZI", "gplx": "2", "XMLX": "2"},
            ),
            ("sse", "pre_disclosure"): (
                {"endpoint": "/prjs/equity/list", "project_type": "CHANQUAN", "gplx": "1", "XMLX": "1"},
                {"endpoint": "/prjs/capitalincrease/list", "project_type": "ZENGZI", "gplx": "1", "XMLX": "1"},
            ),
            ("tpre", "equity_transfer"): (
                {"label": "equity-formal", "systemCode": "PROPERTY_RIGHT_TRANSFER", "bizTypeCode": "FORMAL"},
            ),
            ("tpre", "capital_increase"): (
                {
                    "label": "capital-formal",
                    "systemCode": "ENTERPRISE_CAPITAL_INCREASE",
                    "bizTypeCode": "FORMAL",
                },
            ),
            ("tpre", "physical_asset"): (
                {
                    "label": "physical-formal-5000plus",
                    "systemCode": "ENTERPRISE_ASSETS",
                    "bizTypeCode": "FORMAL",
                    "priceBegin": 5000,
                },
            ),
            ("tpre", "pre_disclosure"): (
                {"label": "equity-prepare", "systemCode": "PROPERTY_RIGHT_TRANSFER", "bizTypeCode": "PREPARE"},
                {
                    "label": "capital-prepare",
                    "systemCode": "ENTERPRISE_CAPITAL_INCREASE",
                    "bizTypeCode": "PREPARE",
                },
            ),
            ("cquae", "equity_transfer"): (
                {"label": "equity-formal", "q": "s", "projectID": 1, "nt": 1, "priceID": 32},
            ),
            ("cquae", "capital_increase"): (
                {"label": "capital-formal", "q": "s", "projectID": 2, "ly": 34, "nt": 1, "priceID": 33},
            ),
            ("cquae", "physical_asset"): (
                {"label": "physical-5000w-to-1y", "q": "s", "projectID": 3, "price": "5000万-1亿"},
                {"label": "physical-over-1y", "q": "s", "projectID": 3, "price": "1亿以上"},
            ),
            ("cquae", "pre_disclosure"): (
                {"label": "equity-pre", "q": "s", "projectID": 1, "nt": 3, "priceID": 35},
                {"label": "capital-pre", "q": "s", "projectID": 2, "ly": 34, "nt": 3, "priceID": 34},
            ),
            ("cbex", "physical_asset"): (
                {"label": "房屋土地", "businessType": "SW", "assetType": "house"},
                {"label": "交通运输工具", "businessType": "SW", "assetType": "transport"},
                {"label": "设备", "businessType": "SW", "assetType": "equipment"},
            ),
            ("cbex", "equity_transfer"): (
                {"label": "股权转让", "businessType": "JC", "include_pre_disclosure": False},
            ),
            ("cbex", "capital_increase"): (
                {"label": "增资扩股", "businessType": "GZ", "include_pre_disclosure": False},
            ),
            ("cbex", "pre_disclosure"): (
                {"label": "股权转让(预披露)", "businessType": "JC", "include_pre_disclosure": True},
                {"label": "增资扩股(预披露)", "businessType": "GZ", "include_pre_disclosure": True},
            ),
        }

        for (source_id, business_id), expected_specs in expected.items():
            with self.subTest(source_id=source_id, business_id=business_id):
                descriptor = get_source_business_requirement(source_id, "listing", business_id)
                self.assertEqual(tuple(dict(spec) for spec in descriptor.list_query_specs), expected_specs)
                self.assertTrue(descriptor.list_endpoint)

    def test_core_deal_task_taxonomy_is_declared(self) -> None:
        expected = {
            "sse:deal:deal_physical_asset": (
                "/si/notice/getDealNoticeList",
                "/si/notice/getNoticeDetail",
                "/si/notice/getNoticeDetail",
                "",
                "",
                ("CJRQ", "deal_date"),
                "CJRQ",
            ),
            "sse:deal:deal_equity_transfer": (
                "/si/notice/getDealNoticeList",
                "/si/notice/getNoticeDetail",
                "/si/notice/getNoticeDetail",
                "",
                "",
                ("CJRQ", "deal_date"),
                "CJRQ",
            ),
            "sse:deal:deal_capital_increase": (
                "/si/notice/getDealNoticeList",
                "/si/notice/getNoticeDetail",
                "/si/notice/getNoticeDetail",
                "",
                "",
                ("CJRQ", "deal_date"),
                "CJRQ",
            ),
            "cbex:deal:deal_physical_asset": (
                "/xm/zczr/cjjggs/",
                "/xm/zczr/",
                "/xm/zczr/",
                "",
                "",
                ("deal_date",),
                "deal_date",
            ),
            "cbex:deal:deal_equity_transfer": (
                "/xm/cqzr/cjjggs/",
                "/xm/cqzr/",
                "/xm/cqzr/",
                "",
                "",
                ("deal_date",),
                "deal_date",
            ),
            "cbex:deal:deal_capital_increase": (
                "/xm/qyzz/cjjggs/",
                "/xm/qyzz/",
                "/xm/qyzz/",
                "",
                "",
                ("deal_date",),
                "deal_date",
            ),
            "tpre:deal:deal_equity_transfer": (
                "/transaction/biz/transaction-management/anmuas/result-notice/page?bizType=PROPERTY_RIGHT_TRANSFER",
                "/transaction-view/data/common/transaction-announcement",
                "/transaction-view/data/common/transaction-announcement",
                "",
                "",
                ("contractSignTime", "deal_date"),
                "contractSignTime",
            ),
            "tpre:deal:deal_capital_increase": (
                "/transaction/biz/increase/transaction/anmuas/result-notice/page?bizType=ENTERPRISE_CAPITAL_INCREASE",
                "/transaction-view/data/common/transaction-announcement",
                "/transaction-view/data/common/transaction-announcement",
                "",
                "/transaction/biz/increase/transaction/transferee/anmuas/result-notice/details",
                ("deal_date",),
                "deal_date",
            ),
            "cquae:deal:deal_equity_transfer": (
                "/CquaeNews/cjgs/List.cshtml",
                "/CquaeNews/cjgs/",
                "/CquaeNews/cjgs/",
                "",
                "",
                ("deal_date",),
                "deal_date",
            ),
            "cquae:deal:deal_capital_increase": (
                "/CquaeNews/cjgs/List.cshtml?type=1",
                "/CquaeNews/cjgs/",
                "/CquaeNews/cjgs/",
                "",
                "",
                ("deal_date",),
                "deal_date",
            ),
        }

        declared = {
            f"{item.source_id}:{item.record_family}:{item.business_id}": item
            for item in list_source_business_requirements()
            if item.record_family == "deal"
        }

        self.assertEqual(set(declared), set(expected))
        self.assertNotIn("tpre:deal:deal_physical_asset", declared)
        self.assertNotIn("cquae:deal:deal_physical_asset", declared)
        for task_id, (
            list_endpoint,
            detail_route,
            render_page_route,
            detail_api_endpoint,
            transferee_details_endpoint,
            date_field_candidates,
            date_basis,
        ) in expected.items():
            with self.subTest(task_id=task_id):
                descriptor = declared[task_id]
                self.assertEqual(descriptor.list_endpoint, list_endpoint)
                self.assertEqual(descriptor.detail_route, detail_route)
                self.assertEqual(descriptor.render_page_route, render_page_route)
                self.assertEqual(descriptor.detail_api_endpoint, detail_api_endpoint)
                self.assertEqual(descriptor.transferee_details_endpoint, transferee_details_endpoint)
                self.assertEqual(descriptor.date_field_candidates, date_field_candidates)
                self.assertEqual(descriptor.date_basis, date_basis)

    def test_sse_deal_discovery_query_contracts_are_declared(self) -> None:
        sse_business_ids = (
            "deal_physical_asset",
            "deal_equity_transfer",
            "deal_capital_increase",
        )

        for business_id in sse_business_ids:
            with self.subTest(business_id=business_id):
                requirement = get_source_business_requirement(
                    "sse", "deal", business_id
                )
                self.assertEqual(requirement.discovery_query_ids, ("deal-notice-list",))

    def test_deal_export_workbook_support_and_unsupported_reasons_are_declared(self) -> None:
        expected_supported = {
            ("sse", "deal_physical_asset"): "上交所资产成交项目",
            ("cbex", "deal_physical_asset"): "北交所资产成交项目",
            ("sse", "deal_equity_transfer"): "上交所",
            ("cbex", "deal_equity_transfer"): "北交所",
            ("tpre", "deal_equity_transfer"): "天交所",
            ("cquae", "deal_equity_transfer"): "重交所",
            ("sse", "deal_capital_increase"): "上海联交所增资项目",
            ("cbex", "deal_capital_increase"): "北交所增资项目",
            ("tpre", "deal_capital_increase"): "天交所增资扩股项目成交",
            ("cquae", "deal_capital_increase"): "重交所增资扩股项目成交",
        }
        declared = {
            (item.source_id, item.business_id): item
            for item in list_export_workbook_support(record_family="deal")
        }

        self.assertEqual(
            set(declared),
            set(expected_supported) | {
                ("tpre", "deal_physical_asset"),
                ("cquae", "deal_physical_asset"),
            },
        )
        for key, sheet_name in expected_supported.items():
            with self.subTest(scope=key):
                descriptor = declared[key]
                self.assertTrue(descriptor.supported)
                self.assertEqual(descriptor.sheet_name, sheet_name)
                self.assertEqual(descriptor.unsupported_reason, "")

        for source_id in ("tpre", "cquae"):
            with self.subTest(source_id=source_id):
                descriptor = get_export_workbook_support(source_id, "deal", "deal_physical_asset")
                self.assertFalse(descriptor.supported)
                self.assertEqual(descriptor.sheet_name, "")
                self.assertEqual(descriptor.unsupported_reason, "source_has_no_deal_physical_workbook_sheet")

    def test_business_aliases_resolve_canonical_business_id_and_family(self) -> None:
        physical_asset = get_business_descriptor("physical_asset")
        deal_equity = get_business_descriptor("deal_equity_transfer")

        self.assertEqual(physical_asset.business_id, "physical_asset")
        self.assertEqual(physical_asset.family_id, "listing")
        self.assertEqual(resolve_business_descriptor("physical asset").business_id, "physical_asset")
        self.assertEqual(deal_equity.business_id, "deal_equity_transfer")
        self.assertEqual(deal_equity.family_id, "deal")
        self.assertEqual(
            resolve_business_descriptor("deal equity transfer").business_id,
            "deal_equity_transfer",
        )
        self.assertEqual(
            resolve_business_descriptor("physical asset", family_id="listing").business_id,
            "physical_asset",
        )
        self.assertEqual(
            list_business_descriptors(family_id="listing"),
            [
                get_business_descriptor("physical_asset"),
                get_business_descriptor("equity_transfer"),
                get_business_descriptor("capital_increase"),
                get_business_descriptor("pre_disclosure"),
            ],
        )
        self.assertEqual(
            list_business_descriptors(family_id="deal"),
            [
                get_business_descriptor("deal_physical_asset"),
                get_business_descriptor("deal_equity_transfer"),
                get_business_descriptor("deal_capital_increase"),
            ],
        )

    def test_business_lookup_rejects_wrong_family(self) -> None:
        with self.assertRaises(KeyError):
            resolve_business_descriptor("physical_asset", family_id="deal")

        with self.assertRaises(KeyError):
            get_business_descriptor("physical_asset", family_id="deal")

    def test_duplicate_business_id_catalog_entries_are_rejected(self) -> None:
        descriptors = (
            BusinessDescriptor(
                business_id="physical_asset",
                family_id="listing",
                canonical_label="Physical Asset",
                aliases=("physical asset", "shared-alias"),
            ),
            BusinessDescriptor(
                business_id="physical_asset",
                family_id="listing",
                canonical_label="Physical Asset Duplicate",
                aliases=("physical asset duplicate",),
            ),
        )

        with self.assertRaises(ValueError):
            validate_business_descriptors(descriptors)

    def test_duplicate_alias_across_businesses_is_rejected(self) -> None:
        descriptors = (
            BusinessDescriptor(
                business_id="physical_asset",
                family_id="listing",
                canonical_label="Physical Asset",
                aliases=("shared-alias",),
            ),
            BusinessDescriptor(
                business_id="equity_transfer",
                family_id="listing",
                canonical_label="Equity Transfer",
                aliases=("shared-alias",),
            ),
        )

        with self.assertRaises(ValueError):
            validate_business_descriptors(descriptors)

    def test_family_business_membership_mismatch_is_rejected(self) -> None:
        descriptors = validate_business_descriptors(
            (
                BusinessDescriptor(
                    business_id="physical_asset",
                    family_id="listing",
                    canonical_label="Physical Asset",
                    aliases=("physical asset",),
                ),
            )
        )
        mismatched_families = (
            FamilyDescriptor(
                family_id="listing",
                canonical_label="Listing",
                aliases=("listing",),
                source_ids=("sse",),
                business_ids=("equity_transfer",),
                default_product_profile_id="desktop_listing",
            ),
        )

        with self.assertRaises(ValueError):
            validate_family_business_alignment(descriptors, families=mismatched_families)

    def test_family_business_alignment_respects_explicit_empty_family_set(self) -> None:
        descriptors = validate_business_descriptors(
            (
                BusinessDescriptor(
                    business_id="physical_asset",
                    family_id="listing",
                    canonical_label="Physical Asset",
                    aliases=("physical asset",),
                ),
                BusinessDescriptor(
                    business_id="equity_transfer",
                    family_id="listing",
                    canonical_label="Equity Transfer",
                    aliases=("equity transfer",),
                ),
                BusinessDescriptor(
                    business_id="capital_increase",
                    family_id="listing",
                    canonical_label="Capital Increase",
                    aliases=("capital increase",),
                ),
                BusinessDescriptor(
                    business_id="pre_disclosure",
                    family_id="listing",
                    canonical_label="Pre Disclosure",
                    aliases=("pre disclosure",),
                ),
            )
        )

        with self.assertRaises(ValueError):
            validate_family_business_alignment(descriptors, families=())


if __name__ == "__main__":
    unittest.main()
