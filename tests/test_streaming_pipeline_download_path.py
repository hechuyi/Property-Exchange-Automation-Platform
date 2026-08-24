from __future__ import annotations

import argparse
import types
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from peap.streaming_daily_pipeline import _build_download_request


@dataclass(frozen=True)
class _FamilyBusinessDownloadRunRequest:
    exchange: str
    record_family: str
    business_id: str
    list_tasks: bool
    output_root: str
    force_manual_root: bool
    start_date: str | None
    end_date: str | None
    page_size: int | None
    max_pages: int | None
    concurrency: int
    resume: bool
    save_json: bool
    sse_ssl_verify: bool
    sse_ca_bundle: str | None
    log_dir: str
    log_file: str | None
    verbose: bool
    auto_split: bool
    split_candidates: int
    split_min_days: int
    split_max_depth: int
    split_plan_only: bool
    split_plan_file: str | None
    split_use_plan: bool
    split_mode: str
    chunk_state_file: str | None
    item_saved_callback: object = None


class StreamingPipelineDownloadPathTest(unittest.TestCase):
    def test_build_download_request_forwards_family_business_scope(self) -> None:
        config = SimpleNamespace(
            LOG_DIR="/tmp/logs",
            DOWNLOADER_DEFAULTS={
                "concurrency": 2,
                "split_candidates": 10,
                "split_min_days": 1,
                "split_max_depth": 3,
                "split_mode": "fast",
                "sse_ssl_verify": True,
                "sse_ca_bundle": None,
            },
        )
        args = argparse.Namespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            concurrency=4,
            page_size=25,
            max_pages=None,
            no_resume=False,
            save_json=True,
            verbose=True,
        )
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FamilyBusinessDownloadRunRequest

        with patch.dict("sys.modules", {"peap.download_runner": fake_download_runner}):
            request = _build_download_request(
                args,
                start_text="2026-01-01",
                end_text="2026-01-02",
                config_obj=config,
                output_root="/tmp/archive",
            )

        self.assertEqual(request.exchange, "sse")
        self.assertEqual(request.record_family, "listing")
        self.assertEqual(request.business_id, "physical_asset")


if __name__ == "__main__":
    unittest.main()
