from __future__ import annotations

import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from desktop_backend.record_scope import (
    RecordScope,
    normalize_record_scope,
    record_scope_to_dict,
    resolve_listing_business_ids,
)
from peap.streaming_models import ExportRequest, IngestedRecord, ItemProgressEvent, RecordFamily
from peap_core.family_catalog import FamilyDescriptor


class RecordScopeTest(unittest.TestCase):
    def test_record_family_literal_allows_listing_and_deal_only(self) -> None:
        self.assertEqual(set(get_args(RecordFamily)), {"listing", "deal"})

    def test_streaming_models_default_record_family_is_listing(self) -> None:
        progress = ItemProgressEvent(job_id="job-1", stage="downloaded", status="running")
        record = IngestedRecord(
            record_id="rec-1",
            revision_hash="rev-1",
            project_code="CODE-1",
            project_name="示例项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file="/tmp/raw.html",
            archive_path="/tmp/archive.html",
            parser_payload={},
            postprocess_payload={},
            findings=[],
        )
        request = ExportRequest()

        self.assertEqual(progress.record_family, "listing")
        self.assertEqual(record.record_family, "listing")
        self.assertEqual(request.record_family, "listing")

    def test_record_scope_defaults_to_listing_all_business_scope_and_pagination_defaults(self) -> None:
        scope = normalize_record_scope(None)

        self.assertEqual(scope, RecordScope(record_family="listing", state="all", business_id="all", exchange="all", keyword="", date_from="", date_to="", page=1, page_size=50))
        self.assertEqual(record_scope_to_dict(scope), {
            "record_family": "listing",
            "state": "all",
            "business_id": "all",
            "exchange": "all",
            "keyword": "",
            "date_from": "",
            "date_to": "",
            "page": 1,
            "page_size": 50,
        })

    def test_record_scope_rejects_non_object_payload_instead_of_defaulting(self) -> None:
        for payload in (False, [], "record-family=listing"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "record scope"):
                    normalize_record_scope(payload)

    def test_record_scope_rejects_non_string_text_fields_instead_of_stringifying(self) -> None:
        for field in (
            "record_family",
            "state",
            "business_id",
            "business_label",
            "exchange",
            "keyword",
            "date_from",
            "date_to",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    normalize_record_scope({field: False})

    def test_record_scope_rejects_legacy_project_type_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "project_type"):
            normalize_record_scope({"project_type": "股权转让"})

    def test_record_scope_rejects_unknown_record_family_instead_of_falling_back(self) -> None:
        with self.assertRaises(ValueError):
            normalize_record_scope({"record_family": "unknown_family"})

    def test_record_scope_uses_family_catalog_descriptor_for_non_default_family(self) -> None:
        with patch(
            "desktop_backend.record_scope.get_family_descriptor",
            return_value=FamilyDescriptor(
                family_id="archive",
                canonical_label="Archive",
                aliases=("archive",),
                source_ids=(),
                business_ids=(),
                default_product_profile_id="desktop_archive",
            ),
        ):
            scope = normalize_record_scope({"record_family": "archive"})

        self.assertEqual(scope.record_family, "archive")

    def test_record_scope_family_normalization_does_not_hardcode_known_families(self) -> None:
        source = Path("desktop_backend/record_scope.py").read_text(encoding="utf-8")

        self.assertNotIn('family in {"", "listing"}', source)
        self.assertNotIn('if family == "deal"', source)

    def test_record_scope_rejects_unknown_business_id_instead_of_round_tripping_raw_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_id"):
            normalize_record_scope({"record_family": "listing", "business_id": "not_a_real_business"})

    def test_record_scope_rejects_invalid_exchange_instead_of_copying_free_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "exchange"):
            normalize_record_scope(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "not_a_real_exchange",
                }
            )

    def test_record_scope_rejects_mismatched_business_id_and_business_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_label"):
            normalize_record_scope(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "business_label": "股权转让",
                }
            )

    def test_record_scope_rejects_invalid_page_instead_of_defaulting(self) -> None:
        with self.assertRaisesRegex(ValueError, "page"):
            normalize_record_scope({"page": "not-a-number"})

    def test_record_scope_rejects_invalid_page_size_instead_of_defaulting(self) -> None:
        with self.assertRaisesRegex(ValueError, "page_size"):
            normalize_record_scope({"page_size": "not-a-number"})

    def test_resolve_listing_business_ids_returns_family_default_businesses_for_listing_and_deal(self) -> None:
        listing_scope = normalize_record_scope({"record_family": "listing"})
        deal_scope = normalize_record_scope({"record_family": "deal"})

        self.assertEqual(
            resolve_listing_business_ids(listing_scope),
            ["physical_asset", "equity_transfer", "capital_increase", "pre_disclosure"],
        )
        self.assertEqual(
            resolve_listing_business_ids(deal_scope),
            ["deal_physical_asset", "deal_equity_transfer", "deal_capital_increase"],
        )

    def test_resolve_listing_business_ids_uses_canonical_business_scope(self) -> None:
        scope = normalize_record_scope({"record_family": "listing", "business_id": "equity_transfer"})

        self.assertEqual(resolve_listing_business_ids(scope), ["equity_transfer"])

    def test_resolve_listing_business_ids_keeps_explicit_deal_business_scope(self) -> None:
        scope = normalize_record_scope({"record_family": "deal", "business_id": "deal_equity_transfer"})

        self.assertEqual(resolve_listing_business_ids(scope), ["deal_equity_transfer"])


if __name__ == "__main__":
    unittest.main()
