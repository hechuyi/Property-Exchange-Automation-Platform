from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader
from peap.downloaders.common import DownloadSummary
from peap.downloaders.sse_physical import ShanghaiPhysicalAssetDownloader


class _FakePage:
    async def close(self) -> None:
        return None


class _FakeContext:
    request = object()

    async def new_page(self) -> _FakePage:
        return _FakePage()


def _write_snapshot(*, html_path: str, html: str) -> None:
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)


class PhysicalCallbackResumeStatusTest(unittest.TestCase):
    def test_sse_save_json_callback_failure_is_not_resume_complete(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        with tempfile.TemporaryDirectory() as temp_dir:
            prefetched = [
                {
                    "xmid": "sse-physical-callback-1",
                    "project_code": "G32026SH100001",
                    "project_name": "SSE physical callback fixture",
                    "page_url": "https://www.suaee.com/xmzx.html#/zczrDetail?XMID=sse-physical-callback-1",
                    "disclosure_start": "2026-05-10",
                    "row": {
                        "xmid": "sse-physical-callback-1",
                        "xmbh": "G32026SH100001",
                        "xmmc": "SSE physical callback fixture",
                        "plksrq": "2026-05-10",
                    },
                }
            ]
            first = ShanghaiPhysicalAssetDownloader(
                html_root=temp_dir,
                list_query_specs=[],
                resume=False,
                save_json=True,
                item_saved_callback=callback_boom,
            )
            first_summary = DownloadSummary()
            first_candidates = []
            first._build_prefetched_candidates(
                prefetched_candidates=prefetched,
                output_dir=temp_dir,
                summary=first_summary,
                candidates=first_candidates,
                start=None,
                end=None,
            )
            candidate = first_candidates[0]

            async def fake_fetch(**_kwargs) -> tuple[str, int]:
                return (
                    "<html><body>信息披露起始日期：2026-05-10 SSE physical callback fixture</body></html>",
                    200,
                )

            def fake_save(*, rendered_html: str, page_url: str, html_path: str) -> None:
                _write_snapshot(html_path=html_path, html=rendered_html)

            first._fetch_rendered_html = fake_fetch  # type: ignore[method-assign]
            first._save_complete_page = fake_save  # type: ignore[method-assign]

            asyncio.run(
                first._process_candidate(
                    candidate=candidate,
                    context=_FakeContext(),
                    summary=first_summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )
            self.assertEqual(first_summary.saved, 0)
            self.assertEqual(first_summary.detail_failed, 1)

            second = ShanghaiPhysicalAssetDownloader(
                html_root=temp_dir,
                list_query_specs=[],
                resume=True,
                save_json=True,
            )
            second_summary = DownloadSummary()
            second_candidates = []
            second._build_prefetched_candidates(
                prefetched_candidates=prefetched,
                output_dir=temp_dir,
                summary=second_summary,
                candidates=second_candidates,
                start=None,
                end=None,
            )

        self.assertEqual(second_summary.skipped_by_resume, 0)
        self.assertEqual(len(second_candidates), 1)

    def test_sse_save_json_resume_ignores_plain_status_marker_without_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefetched = [
                {
                    "xmid": "sse-physical-marker-only",
                    "project_code": "G32026SH100002",
                    "project_name": "SSE marker only fixture",
                    "page_url": "https://www.suaee.com/xmzx.html#/zczrDetail?XMID=sse-physical-marker-only",
                    "disclosure_start": "2026-05-10",
                    "row": {
                        "xmid": "sse-physical-marker-only",
                        "xmbh": "G32026SH100002",
                        "xmmc": "SSE marker only fixture",
                        "plksrq": "2026-05-10",
                    },
                }
            ]
            setup_downloader = ShanghaiPhysicalAssetDownloader(
                html_root=temp_dir,
                list_query_specs=[],
                save_json=False,
            )
            setup_summary = DownloadSummary()
            setup_candidates = []
            setup_downloader._build_prefetched_candidates(
                prefetched_candidates=prefetched,
                output_dir=temp_dir,
                summary=setup_summary,
                candidates=setup_candidates,
                start=None,
                end=None,
            )
            html_path = setup_candidates[0].html_path
            _write_snapshot(html_path=html_path, html="<html><body>legacy plain artifact</body></html>")
            setup_downloader._write_resume_status(
                html_path=html_path,
                source_url=str(prefetched[0]["page_url"]),
                http_status=200,
                save_status="complete",
            )

            resume_downloader = ShanghaiPhysicalAssetDownloader(
                html_root=temp_dir,
                list_query_specs=[],
                resume=True,
                save_json=True,
            )
            resume_summary = DownloadSummary()
            resume_candidates = []
            resume_downloader._build_prefetched_candidates(
                prefetched_candidates=prefetched,
                output_dir=temp_dir,
                summary=resume_summary,
                candidates=resume_candidates,
                start=None,
                end=None,
            )

        self.assertEqual(resume_summary.skipped_by_resume, 0)
        self.assertEqual(len(resume_candidates), 1)

    def test_cbex_plain_html_callback_failure_is_not_resume_complete(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        with tempfile.TemporaryDirectory() as temp_dir:
            prefetched = [
                {
                    "uid": "cbex-physical-callback-1",
                    "code": "GR2026BJ100001",
                    "url": "https://www.cbex.com.cn/xm/zczr/2026/05/callback.html",
                    "project_name": "CBEX physical callback fixture",
                    "disclosure_start": "2026-05-10",
                    "row": {
                        "code": "GR2026BJ100001",
                        "title": "CBEX physical callback fixture",
                        "disclosuretime": "2026-05-10",
                    },
                }
            ]
            first = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=False,
                save_json=False,
                item_saved_callback=callback_boom,
            )
            first_summary = DownloadSummary()
            first_candidates = []
            first._prefetched_to_candidates(
                prefetched_candidates=prefetched,
                outdir=temp_dir,
                summary=first_summary,
                seen=set(),
                cands=first_candidates,
                start=None,
                end=None,
            )
            candidate = first_candidates[0]

            async def fake_fetch(**_kwargs) -> tuple[str, int]:
                return (
                    "<html><body>项目编号 GR2026BJ100001 信息披露起始日期：2026-05-10</body></html>",
                    200,
                )

            async def fake_save(*, html: str, page_url: str, html_path: str, request_context) -> None:
                _write_snapshot(html_path=html_path, html=html)

            first._fetch_html = fake_fetch  # type: ignore[method-assign]
            first._save_complete_page = fake_save  # type: ignore[method-assign]

            asyncio.run(
                first._process_candidate(
                    context=_FakeContext(),
                    candidate=candidate,
                    summary=first_summary,
                    start=None,
                    end=None,
                    timeout_cls=TimeoutError,
                )
            )
            self.assertEqual(first_summary.saved, 0)
            self.assertEqual(first_summary.detail_failed, 1)

            second = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=False,
            )
            second_summary = DownloadSummary()
            second_candidates = []
            second._prefetched_to_candidates(
                prefetched_candidates=prefetched,
                outdir=temp_dir,
                summary=second_summary,
                seen=set(),
                cands=second_candidates,
                start=None,
                end=None,
            )

        self.assertEqual(second_summary.skipped_by_resume, 0)
        self.assertEqual(len(second_candidates), 1)


if __name__ == "__main__":
    unittest.main()
