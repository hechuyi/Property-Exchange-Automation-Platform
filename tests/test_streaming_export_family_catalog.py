from __future__ import annotations

import ast
import inspect
import os
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from peap import streaming_export
from peap.export_projection import ExportProjectionError
from peap.streaming_export import run_ready_export
from peap.streaming_models import ExportRequest
from peap_core.family_catalog import FamilyDescriptor


class FakeStreamingStore:
    def __init__(self, records: list[dict[str, object]] | None = None) -> None:
        self.records = list(records or [])
        self.iter_latest_records_calls: list[dict[str, object]] = []
        self.mark_exported_calls: list[dict[str, object]] = []

    def get_exported_revision_map(self, cursor_id: str) -> dict[str, object]:
        return {}

    def get_export_cursor_value(self, cursor_id: str) -> dict[str, object]:
        return {}

    def iter_latest_records(self, **kwargs: object) -> list[dict[str, object]]:
        self.iter_latest_records_calls.append(dict(kwargs))
        return list(self.records)

    def mark_exported(self, **kwargs: object) -> None:
        self.mark_exported_calls.append(dict(kwargs))


def _descriptor(family_id: str) -> FamilyDescriptor:
    return FamilyDescriptor(
        family_id=family_id,
        canonical_label=family_id.title(),
        aliases=(family_id,),
        source_ids=("sse",),
        business_ids=("equity_transfer",) if family_id == "listing" else ("deal_equity_transfer",),
        default_product_profile_id=f"desktop_{family_id}",
    )


class StreamingExportFamilyCatalogTest(unittest.TestCase):
    def test_run_ready_export_normalizes_listing_family_through_catalog(self) -> None:
        store = FakeStreamingStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("peap.streaming_export.get_family_descriptor", create=True) as get_family_descriptor:
                get_family_descriptor.return_value = _descriptor("listing")
                with patch("peap.surface_contract.scope_supported_for_surface", return_value=True):
                    run_ready_export(
                        store,
                        ExportRequest(
                            record_family="listing",
                            cursor_id="test-cursor",
                            output_dir=os.path.join(temp_dir, "exports"),
                        ),
                        writer=lambda *_args: None,
                    )

        get_family_descriptor.assert_called_once_with("listing")
        self.assertEqual(
            [call["record_family"] for call in store.iter_latest_records_calls],
            ["listing", "listing"],
        )

    def test_run_ready_export_uses_canonical_family_for_surface_support_check(self) -> None:
        store = FakeStreamingStore()
        support_calls: list[dict[str, object]] = []

        def fake_scope_supported_for_surface(**kwargs: object) -> bool:
            support_calls.append(dict(kwargs))
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("peap.streaming_export.get_family_descriptor", create=True) as get_family_descriptor:
                get_family_descriptor.return_value = _descriptor("deal")
                with patch(
                    "peap.surface_contract.scope_supported_for_surface",
                    side_effect=fake_scope_supported_for_surface,
                ):
                    with self.assertRaises(ExportProjectionError):
                        run_ready_export(
                            store,
                            ExportRequest(
                                record_family="DEAL_ALIAS",
                                business_types=["deal_equity_transfer"],
                                output_dir=os.path.join(temp_dir, "exports"),
                            ),
                            writer=lambda *_args: None,
                        )

        get_family_descriptor.assert_called_once_with("DEAL_ALIAS")
        self.assertEqual([call["record_family"] for call in support_calls], ["deal"])

    def test_run_ready_export_rejects_catalog_family_without_export_projection_scope(self) -> None:
        store = FakeStreamingStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("peap.streaming_export.get_family_descriptor", create=True) as get_family_descriptor:
                get_family_descriptor.return_value = FamilyDescriptor(
                    family_id="metadata",
                    canonical_label="Metadata",
                    aliases=("metadata",),
                    source_ids=("sse",),
                    business_ids=(),
                    default_product_profile_id="desktop_metadata",
                )
                with self.assertRaises(ExportProjectionError):
                    run_ready_export(
                        store,
                        ExportRequest(
                            record_family="metadata",
                            output_dir=os.path.join(temp_dir, "exports"),
                        ),
                        writer=lambda *_args: None,
                    )

        get_family_descriptor.assert_called_once_with("metadata")
        self.assertEqual(store.mark_exported_calls, [])

    def test_run_ready_export_has_no_local_listing_deal_family_allowlist(self) -> None:
        source = textwrap.dedent(inspect.getsource(streaming_export.run_ready_export))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(operator, ast.NotIn) for operator in node.ops):
                continue
            for comparator in node.comparators:
                if not isinstance(comparator, ast.Set):
                    continue
                constants = {element.value for element in comparator.elts if isinstance(element, ast.Constant)}
                self.assertNotEqual(constants, {"listing", "deal"})


if __name__ == "__main__":
    unittest.main()
