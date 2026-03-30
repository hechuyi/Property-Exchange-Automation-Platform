from __future__ import annotations

import unittest

from peap.source_registry import SourceCapability, get_source, list_sources, register_source
from peap_core.source_catalog import (
    SourceDescriptor,
    get_source_descriptor,
    list_source_descriptors,
)


class SourceRegistryTest(unittest.TestCase):
    def test_source_registry_is_a_compatibility_facade_over_shared_catalog(self) -> None:
        listing_sources = list_source_descriptors(record_family="listing")

        self.assertIs(SourceCapability, SourceDescriptor)
        self.assertEqual(list_sources(), listing_sources)
        self.assertEqual(list_sources(record_family="listing"), listing_sources)
        self.assertEqual(list_sources(record_family="deal"), [])
        self.assertIs(get_source("sse"), get_source_descriptor("sse"))

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
                    supported_job_types=("download_ingest",),
                    downloader_key="unit-downloader",
                    adapter_key="unit-adapter",
                    enabled=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
