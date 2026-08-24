from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from peap_postprocess.postprocess_engine.adapters import TabularSheet
from peap_postprocess.postprocess_engine.config import PPEConfig, RuleSettings
from peap_postprocess.postprocess_engine.engine import PostProcessEngine


class _FakeAdapter:
    def __init__(self, sheet: TabularSheet) -> None:
        self._sheet = sheet

    def discover_files(self, input_dir, include_globs, *, scan_recursive=True, input_targets=None):  # noqa: ANN001, ARG002
        return [self._sheet.file_path]

    def read_file(self, file_path):  # noqa: ANN001, ARG002
        return [self._sheet]


class _FakeAuditWriter:
    def write(self, *, audit_dir, summary, audit_rows):  # noqa: ANN001, ARG002
        return "/tmp/ppe-audit.csv"


class PostProcessEngineFamilyScopeTest(unittest.TestCase):
    def test_run_builds_rule_plan_from_each_records_inferred_family(self) -> None:
        sheet = TabularSheet(
            file_path="/tmp/family_scope.csv",
            file_name="family_scope.csv",
            sheet_name="Sheet1",
            dataframe=pd.DataFrame(
                [
                    {
                        "record_family": "listing",
                        "项目编号": "G32026SH1000801-2",
                        "项目类型": "股权转让",
                    },
                    {
                        "record_family": "deal",
                        "项目编号": "D32026SH1000802-2",
                        "项目类型": "股权转让",
                    },
                    {
                        "项目编号": "U32026SH1000803-2",
                        "项目类型": "股权转让",
                    },
                ]
            ),
        )
        engine = PostProcessEngine(
            adapter=_FakeAdapter(sheet),
            audit_writer=_FakeAuditWriter(),
            logger=SimpleNamespace(
                info=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            ),
        )
        config = PPEConfig(
            input_dir="/tmp",
            output_dir="/tmp/out",
            audit_dir="/tmp/audit",
            mode="plan",
            rules={
                "R006_derive_listing_times": RuleSettings(
                    enabled=True,
                    priority=10,
                    params={},
                    record_families=("listing",),
                )
            },
        )

        summary = engine.run(config)

        self.assertEqual(summary.processed_rows, 3)
        self.assertEqual(summary.applied_patches, 1)


if __name__ == "__main__":
    unittest.main()
