from __future__ import annotations

import unittest

from peap.source_registry import SourceCapability, get_source, list_sources, register_source


class SourceRegistryTest(unittest.TestCase):
    def test_register_get_and_list_sources(self) -> None:
        capability = SourceCapability(
            source_id="unit-listing-source",
            site_label="Unit Listing Source",
            supported_record_families=("listing",),
            supported_job_types=("download_ingest",),
            downloader_key="unit-downloader",
            adapter_key="unit-adapter",
            enabled=True,
        )

        register_source(capability)

        self.assertIs(get_source("unit-listing-source"), capability)
        self.assertEqual(list_sources(), [capability])
        self.assertEqual(list_sources(record_family="listing"), [capability])
        self.assertEqual(list_sources(record_family="deal"), [])

    def test_get_source_rejects_unknown_source(self) -> None:
        with self.assertRaises(KeyError):
            get_source("missing-source")


if __name__ == "__main__":
    unittest.main()
