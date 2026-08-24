from __future__ import annotations

import unittest

from peap.streaming_export import _default_cursor_id
from peap.streaming_models import ExportRequest


class ExportProjectionStateMachineTest(unittest.TestCase):
    def test_export_cursor_id_includes_record_family_dimension(self) -> None:
        listing_request = ExportRequest(
            record_family="listing",
            business_types=["equity_transfer"],
            date_from="2026-04-01",
            date_to="2026-04-01",
            exchange="cbex",
            requested_state="all",
            requested_export_mode="full",
            output_dir="/tmp/export",
        )
        deal_request = ExportRequest(
            record_family="deal",
            business_types=["equity_transfer"],
            date_from="2026-04-01",
            date_to="2026-04-01",
            exchange="cbex",
            requested_state="all",
            requested_export_mode="full",
            output_dir="/tmp/export",
        )

        self.assertNotEqual(_default_cursor_id(listing_request), _default_cursor_id(deal_request))


if __name__ == "__main__":
    unittest.main()
