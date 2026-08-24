from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import dataclass
from unittest.mock import patch

import peap_core.source_catalog as source_catalog
from peap.source_registry import SourceCapability, get_source, list_sources, register_source
from peap_core.source_catalog import (
    SourceDescriptor,
    canonical_source_code,
    get_source_descriptor,
    list_source_descriptors,
    record_families_for_source,
    resolve_source_descriptor,
)


class SourceRegistryTest(unittest.TestCase):
    def test_source_registry_is_a_compatibility_facade_over_shared_catalog(self) -> None:
        listing_sources = list_source_descriptors(record_family="listing")
        deal_sources = list_source_descriptors(record_family="deal")

        self.assertIs(SourceCapability, SourceDescriptor)
        self.assertEqual(list_sources(), listing_sources)
        self.assertEqual(list_sources(record_family="listing"), listing_sources)
        self.assertEqual(list_sources(record_family="deal"), deal_sources)
        self.assertIs(get_source("sse"), get_source_descriptor("sse"))
        self.assertEqual(record_families_for_source("sse"), ("listing", "deal"))

    def test_get_source_rejects_unknown_source(self) -> None:
        with self.assertRaises(KeyError):
            get_source("missing-source")

    def test_register_source_is_no_longer_the_canonical_mutation_path(self) -> None:
        with self.assertRaises(RuntimeError):
            register_source(
                SourceCapability(
                    source_id="unit-listing-source",
                    canonical_label="单元测试交易所",
                    site_label="Unit Listing Source",
                    aliases=("unit",),
                    supported_record_families=("listing",),
                    enabled=True,
                )
            )

    def test_new_listing_exchange_sources_are_catalogued_for_listing_only(self) -> None:
        listing_sources = {source.source_id for source in list_source_descriptors(record_family="listing")}
        deal_sources = {source.source_id for source in list_source_descriptors(record_family="deal")}

        self.assertGreaterEqual(listing_sources, {"shandong", "guangdong", "shenzhen"})
        self.assertTrue({"shandong", "guangdong", "shenzhen"}.isdisjoint(deal_sources))
        self.assertEqual(record_families_for_source("shandong"), ("listing",))
        self.assertEqual(record_families_for_source("guangdong"), ("listing",))
        self.assertEqual(record_families_for_source("shenzhen"), ("listing",))
        self.assertEqual(canonical_source_code("guangzhou"), "guangdong")
        self.assertEqual(canonical_source_code("广交所"), "guangdong")
        self.assertEqual(resolve_source_descriptor("guangzhou").source_id, "guangdong")
        self.assertEqual(resolve_source_descriptor("广交所").source_id, "guangdong")
        with self.assertRaises(KeyError):
            get_source_descriptor("guangzhou")

    def test_source_alias_canonicalization_uses_generic_catalog_path(self) -> None:
        self.assertFalse(hasattr(source_catalog, "canonical_guangdong_source_alias"))
        self.assertEqual(canonical_source_code("guangzhou", allow_substring=False), "guangdong")
        self.assertEqual(canonical_source_code("广交所", allow_substring=False), "guangdong")
        self.assertEqual(canonical_source_code("广东联合产权交易中心", allow_substring=False), "guangdong")
        self.assertEqual(
            canonical_source_code(
                "guangzhou",
                allow_substring=False,
                allowed_source_ids={"guangdong"},
            ),
            "guangdong",
        )
        self.assertEqual(
            canonical_source_code(
                "shanghai",
                allow_substring=False,
                allowed_source_ids={"guangdong"},
            ),
            "shanghai",
        )

    def test_record_family_filter_resolves_catalog_alias_before_filtering_sources(self) -> None:
        @dataclass(frozen=True)
        class SyntheticFamilyDescriptor:
            family_id: str

        canonical_listing_sources = list_source_descriptors(record_family="listing")

        with patch.object(
            source_catalog,
            "get_family_descriptor",
            return_value=SyntheticFamilyDescriptor(family_id="listing"),
            create=True,
        ) as get_family_descriptor:
            self.assertEqual(
                list_source_descriptors(record_family="LISTING_ALIAS"),
                canonical_listing_sources,
            )

        get_family_descriptor.assert_called_once_with("LISTING_ALIAS")

    def test_unknown_record_family_filter_returns_no_sources(self) -> None:
        self.assertEqual(list_source_descriptors(record_family="unknown-family"), [])

    def test_list_source_descriptors_does_not_gate_family_identity_with_raw_normalize_token(self) -> None:
        tree = ast.parse(inspect.getsource(source_catalog.list_source_descriptors))
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)

        raw_family_identity_gates = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "normalized_family"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_normalize_token"
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Name)
            and node.value.args[0].id == "record_family"
        ]
        self.assertEqual(raw_family_identity_gates, [])


if __name__ == "__main__":
    unittest.main()
