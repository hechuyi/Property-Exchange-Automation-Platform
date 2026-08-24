from __future__ import annotations

import unittest


class BusinessRuntimeTest(unittest.TestCase):
    def test_registry_resolves_source_family_business_binding(self) -> None:
        module = __import__(
            "peap.business_runtime",
            fromlist=[
                "SourceBusinessBinding",
                "build_source_business_registry",
                "get_source_business_binding",
            ],
        )

        registry = module.build_source_business_registry()
        binding = module.get_source_business_binding(
            "sse",
            record_family="listing",
            business_id="physical_asset",
        )

        self.assertIn(("sse", "listing", "physical_asset"), registry)
        self.assertIsInstance(binding, module.SourceBusinessBinding)
        self.assertEqual(binding.source_id, "sse")
        self.assertEqual(binding.record_family, "listing")
        self.assertEqual(binding.business_id, "physical_asset")
        self.assertEqual(binding.task_id, "sse:listing:physical_asset")

    def test_registry_registers_all_deal_source_business_bindings(self) -> None:
        module = __import__(
            "peap.business_runtime",
            fromlist=[
                "build_source_business_registry",
                "get_source_business_binding",
                "iter_source_business_bindings",
            ],
        )

        registry = module.build_source_business_registry()
        expected_task_ids = {
            "sse:deal:deal_physical_asset",
            "sse:deal:deal_equity_transfer",
            "sse:deal:deal_capital_increase",
            "cbex:deal:deal_physical_asset",
            "cbex:deal:deal_equity_transfer",
            "cbex:deal:deal_capital_increase",
            "tpre:deal:deal_equity_transfer",
            "tpre:deal:deal_capital_increase",
            "cquae:deal:deal_equity_transfer",
            "cquae:deal:deal_capital_increase",
        }
        actual_task_ids = {binding.task_id for binding in registry.values()}

        self.assertTrue(expected_task_ids.issubset(actual_task_ids))
        self.assertNotIn("tpre:deal:deal_physical_asset", actual_task_ids)
        self.assertNotIn("cquae:deal:deal_physical_asset", actual_task_ids)
        self.assertEqual(len(registry), 32)
        self.assertIn("shandong:listing:equity_transfer", actual_task_ids)
        self.assertIn("guangdong:listing:equity_transfer", actual_task_ids)
        self.assertIn("shenzhen:listing:equity_transfer", actual_task_ids)
        self.assertIn("shenzhen:listing:capital_increase", actual_task_ids)
        self.assertIn("shandong:listing:capital_increase", actual_task_ids)
        self.assertIn("guangdong:listing:capital_increase", actual_task_ids)
        binding = module.get_source_business_binding(
            "sse",
            record_family="deal",
            business_id="deal_equity_transfer",
        )
        self.assertEqual(binding.task_id, "sse:deal:deal_equity_transfer")

        deal_bindings = list(module.iter_source_business_bindings(record_family="deal"))
        self.assertEqual(len(deal_bindings), 10)
        self.assertTrue(all(binding.implemented for binding in deal_bindings))

        for source_id in ("tpre", "cquae"):
            with self.subTest(source_id=source_id):
                with self.assertRaises(KeyError):
                    module.get_source_business_binding(
                        source_id,
                        record_family="deal",
                        business_id="deal_physical_asset",
                    )

    def test_deal_runtime_bindings_align_with_family_and_source_catalogs(self) -> None:
        runtime = __import__(
            "peap.business_runtime",
            fromlist=["iter_source_business_bindings"],
        )
        source_catalog = __import__(
            "peap_core.source_catalog",
            fromlist=["get_source_descriptor"],
        )
        family_catalog = __import__(
            "peap_core.family_catalog",
            fromlist=["get_family_descriptor"],
        )

        deal_family = family_catalog.get_family_descriptor("deal")
        for binding in runtime.iter_source_business_bindings(record_family="deal"):
            source = source_catalog.get_source_descriptor(binding.source_id)
            self.assertIn("deal", source.supported_record_families)
            self.assertIn(binding.source_id, deal_family.source_ids)
            self.assertIn(binding.business_id, deal_family.business_ids)

    def test_unknown_source_family_business_binding_is_rejected(self) -> None:
        module = __import__(
            "peap.business_runtime",
            fromlist=["get_source_business_binding"],
        )

        with self.assertRaises(KeyError):
            module.get_source_business_binding(
                "sse",
                record_family="listing",
                business_id="unknown_business",
            )


if __name__ == "__main__":
    unittest.main()
