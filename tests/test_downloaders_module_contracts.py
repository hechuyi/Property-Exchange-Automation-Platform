from __future__ import annotations

import inspect
import unittest

from peap.download_tasks import build_task_registry


class DownloaderModuleContractsTest(unittest.TestCase):
    def test_all_registered_sources_expose_nonempty_manifest_surface(self) -> None:
        registry = build_task_registry()

        for task_id, spec in registry.items():
            with self.subTest(task_id=task_id):
                self.assertEqual(spec.manifest.task_id, task_id)
                self.assertNotEqual(spec.manifest.source_id, "")
                self.assertNotEqual(spec.manifest.list_endpoint, "")
                self.assertNotEqual(spec.manifest.detail_route, "")
                self.assertGreater(len(spec.manifest.date_field_candidates), 0)

    def test_all_registered_downloader_classes_share_keyword_run_protocol(self) -> None:
        registry = build_task_registry()

        for task_id, spec in registry.items():
            with self.subTest(task_id=task_id):
                parameters = inspect.signature(spec.downloader_cls.run).parameters
                self.assertIn("start_date", parameters)
                self.assertIn("end_date", parameters)
                self.assertIn("list_only", parameters)
                self.assertIn("prefetched_candidates", parameters)
                self.assertEqual(parameters["start_date"].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(parameters["end_date"].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(parameters["list_only"].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(parameters["prefetched_candidates"].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(parameters["list_only"].default, False)
                self.assertIsNone(parameters["prefetched_candidates"].default)


if __name__ == "__main__":
    unittest.main()
