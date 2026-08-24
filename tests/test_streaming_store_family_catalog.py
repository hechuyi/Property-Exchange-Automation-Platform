from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from peap.streaming_store import _canonical_record_family
from peap_core.family_catalog import FamilyDescriptor

REPO_ROOT = Path(__file__).resolve().parents[1]


def _descriptor(family_id: str) -> FamilyDescriptor:
    return FamilyDescriptor(
        family_id=family_id,
        canonical_label=family_id.title(),
        aliases=(family_id, family_id.upper()),
        source_ids=(),
        business_ids=(),
        default_product_profile_id=f"desktop_{family_id}",
    )


def _canonical_record_family_tree() -> ast.FunctionDef:
    source_path = REPO_ROOT / "peap" / "streaming_store.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_canonical_record_family":
            return node
    raise AssertionError("_canonical_record_family not found")


class StreamingStoreFamilyCatalogTest(unittest.TestCase):
    def test_empty_family_defaults_to_listing(self) -> None:
        with patch("peap.streaming_store.get_family_descriptor") as get_family_descriptor:
            self.assertEqual(_canonical_record_family(""), "listing")

        get_family_descriptor.assert_not_called()

    def test_known_family_is_resolved_through_catalog(self) -> None:
        with patch(
            "peap.streaming_store.get_family_descriptor",
            return_value=_descriptor("listing"),
        ) as get_family_descriptor:
            self.assertEqual(_canonical_record_family("listing"), "listing")

        get_family_descriptor.assert_called_once_with("listing")

    def test_catalog_alias_returns_descriptor_family_id(self) -> None:
        with patch(
            "peap.streaming_store.get_family_descriptor",
            return_value=_descriptor("listing"),
        ) as get_family_descriptor:
            self.assertEqual(_canonical_record_family("LISTING_ALIAS"), "listing")

        get_family_descriptor.assert_called_once_with("LISTING_ALIAS")

    def test_unknown_family_error_is_not_swallowed(self) -> None:
        with patch(
            "peap.streaming_store.get_family_descriptor",
            side_effect=KeyError("unknown_family"),
        ) as get_family_descriptor:
            with self.assertRaises(KeyError):
                _canonical_record_family("unknown_family")

        get_family_descriptor.assert_called_once_with("unknown_family")

    def test_canonical_record_family_has_no_local_listing_deal_allowlist(self) -> None:
        function = _canonical_record_family_tree()
        local_allowlist_found = False
        for node in ast.walk(function):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(operator, ast.In) for operator in node.ops):
                continue
            for comparator in node.comparators:
                if not isinstance(comparator, ast.Set):
                    continue
                values = {
                    element.value
                    for element in comparator.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
                if values == {"listing", "deal"}:
                    local_allowlist_found = True

        self.assertFalse(local_allowlist_found)


if __name__ == "__main__":
    unittest.main()
