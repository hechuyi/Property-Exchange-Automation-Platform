from __future__ import annotations

import tempfile
import unittest

from peap.streaming_export import record_to_export_payload, run_ready_export
from peap.streaming_models import ExportRequest
from peap.streaming_store import StreamingStore


class PipelineStateMachineSmokeTest(unittest.TestCase):
    def test_record_to_export_payload_smoke(self) -> None:
        payload = record_to_export_payload(
            {
                "canonical_projection": {"项目编号": "G32025SH1000194"},
                "parser_payload": {"项目编号": "G32025SH1000194"},
            }
        )

        self.assertIsInstance(payload, dict)
        self.assertTrue(payload is not None)

    def test_run_ready_export_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StreamingStore(f"{tmp_dir}/streaming.sqlite3", auto_migrate=True)
            result = run_ready_export(
                store,
                ExportRequest(output_dir=f"{tmp_dir}/exports"),
            )

        self.assertEqual(result.new_records, 0)
        self.assertEqual(result.changed_records, 0)
        self.assertIsInstance(result.artifacts, list)


if __name__ == "__main__":
    unittest.main()
