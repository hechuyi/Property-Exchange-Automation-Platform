from __future__ import annotations

import unittest
from typing import get_args

from desktop_backend.record_scope import RecordScope, normalize_record_scope, record_scope_to_dict, resolve_listing_business_types
from peap.streaming_models import ExportRequest, IngestedRecord, ItemProgressEvent, RecordFamily


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

    def test_record_scope_defaults_to_listing_all_and_pagination_defaults(self) -> None:
        scope = normalize_record_scope(None)

        self.assertEqual(scope, RecordScope(record_family="listing", state="all", project_type="all", keyword="", date_from="", date_to="", page=1, page_size=50))
        self.assertEqual(record_scope_to_dict(scope), {
            "record_family": "listing",
            "state": "all",
            "project_type": "all",
            "keyword": "",
            "date_from": "",
            "date_to": "",
            "page": 1,
            "page_size": 50,
        })

    def test_resolve_listing_business_types_returns_defaults_for_listing_and_empty_for_deal(self) -> None:
        listing_scope = normalize_record_scope({"record_family": "listing"})
        deal_scope = normalize_record_scope({"record_family": "deal"})

        self.assertEqual(
            resolve_listing_business_types(listing_scope),
            ["股权转让", "实物资产", "增资扩股", "预披露"],
        )
        self.assertEqual(resolve_listing_business_types(deal_scope), [])


if __name__ == "__main__":
    unittest.main()
