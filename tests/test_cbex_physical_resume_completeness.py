from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import types
import unittest
from pathlib import Path

from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader
from peap.downloaders.common import DownloadSummary, archive_integrity_fields
from peap.submission_layout import resolve_submission_snapshot_target


def _write_complete_sidecar(html_path: str) -> None:
    with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "task_id": "cbex:listing:physical_asset",
                "source_id": "cbex",
                "record_family": "listing",
                "business_id": "physical_asset",
                "save_status": "complete",
                "id": "cbex-physical-1",
                **archive_integrity_fields(html_path),
            },
            handle,
        )


class CbexPhysicalResumeCompletenessTest(unittest.TestCase):
    def test_save_json_detail_sidecar_contains_listing_identity_and_integrity(self) -> None:
        class FakePage:
            async def close(self) -> None:
                return None

        class FakeContext:
            request = object()

            async def new_page(self) -> FakePage:
                return FakePage()

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                save_json=True,
            )
            candidate = types.SimpleNamespace(
                uid="cbex-physical-1",
                code="GR2026BJ100001",
                url="https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                html_path=html_path,
                row={"disclosuretime": "2026-05-10"},
            )
            summary = DownloadSummary()
            rendered_html = (
                "<html><body>项目编号 GR2026BJ100001 信息披露起始日期：2026-05-10</body></html>"
            )

            async def fake_fetch(**_kwargs):
                return rendered_html, 206

            async def fake_save(*, html: str, page_url: str, html_path: str, request_context) -> None:
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write(html)
                os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)

            downloader._fetch_html = fake_fetch  # type: ignore[method-assign]
            downloader._save_complete_page = fake_save  # type: ignore[method-assign]

            asyncio.run(
                downloader._process_candidate(
                    context=FakeContext(),
                    candidate=candidate,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_cls=TimeoutError,
                )
            )

            self.assertEqual(summary.saved, 1)
            sidecar = json.loads(Path(os.path.splitext(html_path)[0] + ".json").read_text(encoding="utf-8"))
            self.assertTrue(
                {"task_id", "source_id", "record_family", "business_id", "save_status"}
                <= set(sidecar),
            )
            self.assertEqual(sidecar["task_id"], "cbex:listing:physical_asset")
            self.assertEqual(sidecar["source_id"], "cbex")
            self.assertEqual(sidecar["record_family"], "listing")
            self.assertEqual(sidecar["business_id"], "physical_asset")
            self.assertEqual(sidecar["save_status"], "complete")
            self.assertEqual(
                sidecar["archive_content_sha256"],
                "sha256:" + hashlib.sha256(Path(html_path).read_bytes()).hexdigest(),
            )
            self.assertEqual(sidecar["archive_content_bytes"], os.path.getsize(html_path))

    def test_save_json_resume_rejects_sidecar_without_record_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>legacy sidecar without family</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
            sidecar = {
                "task_id": "cbex:listing:physical_asset",
                "source_id": "cbex",
                "business_id": "physical_asset",
                "save_status": "complete",
                **archive_integrity_fields(html_path),
            }
            with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
                json.dump(sidecar, handle)

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=True,
            )

            self.assertFalse(downloader._resume_artifact_is_complete(html_path))

    def test_save_json_resume_does_not_skip_html_with_assets_but_missing_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>partial artifact without sidecar</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=True,
            )
            summary = DownloadSummary()
            candidates = []

            downloader._prefetched_to_candidates(
                prefetched_candidates=[
                    {
                        "uid": "cbex-physical-1",
                        "code": "GR2026BJ100001",
                        "url": "https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                        "project_name": "Beijing physical asset",
                        "disclosure_start": "2026-05-10",
                        "row": {"title": "Beijing physical asset"},
                    }
                ],
                outdir=temp_dir,
                summary=summary,
                seen=set(),
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(len(candidates), 1)

    def test_save_json_resume_ignores_plain_status_marker_without_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>plain artifact with status marker</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
            setup_downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                save_json=False,
            )
            setup_downloader._write_resume_status(
                html_path=html_path,
                save_status="complete",
                source_url="https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                http_status=200,
            )

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=True,
            )
            summary = DownloadSummary()
            candidates = []

            downloader._prefetched_to_candidates(
                prefetched_candidates=[
                    {
                        "uid": "cbex-physical-1",
                        "code": "GR2026BJ100001",
                        "url": "https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                        "project_name": "Beijing physical asset",
                        "disclosure_start": "2026-05-10",
                        "row": {"title": "Beijing physical asset"},
                    }
                ],
                outdir=temp_dir,
                summary=summary,
                seen=set(),
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(len(candidates), 1)

    def test_plain_html_resume_rejects_legacy_unverified_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>legacy complete artifact</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=False,
            )
            summary = DownloadSummary()
            candidates = []

            downloader._rows_to_candidates(
                rows=[
                    {
                        "code": "GR2026BJ100001",
                        "url": "/xm/zczr/2026/05/demo.html",
                        "title": "Beijing physical asset",
                        "disclosuretime": "2026-05-10",
                    }
                ],
                source=types.SimpleNamespace(label="unit"),
                outdir=temp_dir,
                summary=summary,
                seen=set(),
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(len(candidates), 1)

    def test_save_json_resume_skips_html_with_assets_and_valid_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>complete artifact</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
            _write_complete_sidecar(html_path)

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=True,
            )
            summary = DownloadSummary()
            candidates = []

            downloader._rows_to_candidates(
                rows=[
                    {
                        "code": "GR2026BJ100001",
                        "url": "/xm/zczr/2026/05/demo.html",
                        "title": "Beijing physical asset",
                        "disclosuretime": "2026-05-10",
                    }
                ],
                source=types.SimpleNamespace(label="unit"),
                outdir=temp_dir,
                summary=summary,
                seen=set(),
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(candidates, [])

    def test_save_json_resume_does_not_skip_corrupt_or_non_object_sidecar(self) -> None:
        cases = (
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "an", "object"]'),
        )
        for sidecar_state, sidecar_bytes in cases:
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path, _ = resolve_submission_snapshot_target(
                    archive_root=temp_dir,
                    project_code="GR2026BJ100001",
                    project_name="Beijing physical asset",
                    listing_date="2026-05-10",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>partial artifact with bad sidecar</body></html>")
                os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
                with open(os.path.splitext(html_path)[0] + ".json", "wb") as handle:
                    handle.write(sidecar_bytes)

                downloader = CbexPhysicalAssetDownloader(
                    html_root=temp_dir,
                    list_sources=[],
                    resume=True,
                    save_json=True,
                )
                summary = DownloadSummary()
                candidates = []

                downloader._prefetched_to_candidates(
                    prefetched_candidates=[
                        {
                            "uid": "cbex-physical-1",
                            "code": "GR2026BJ100001",
                            "url": "https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                            "project_name": "Beijing physical asset",
                            "disclosure_start": "2026-05-10",
                            "row": {"title": "Beijing physical asset"},
                        }
                    ],
                    outdir=temp_dir,
                    summary=summary,
                    seen=set(),
                    cands=candidates,
                    start=None,
                    end=None,
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(len(candidates), 1)

    def test_resume_does_not_skip_invalid_shell_evidence_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=temp_dir,
                project_code="GR2026BJ100001",
                project_name="Beijing physical asset",
                listing_date="2026-05-10",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>invalid shell artifact</body></html>")
            os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
            with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
                handle.write('{"id": "cbex-physical-1"}')
            with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                handle.write('{"page_kind": "invalid_shell"}')

            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                resume=True,
                save_json=True,
            )
            summary = DownloadSummary()
            candidates = []

            downloader._rows_to_candidates(
                rows=[
                    {
                        "code": "GR2026BJ100001",
                        "url": "/xm/zczr/2026/05/demo.html",
                        "title": "Beijing physical asset",
                        "disclosuretime": "2026-05-10",
                    }
                ],
                source=types.SimpleNamespace(label="unit"),
                outdir=temp_dir,
                summary=summary,
                seen=set(),
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(len(candidates), 1)

    def test_resume_does_not_skip_corrupt_evidence_sidecar(self) -> None:
        cases = (
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "an", "object"]'),
        )
        for evidence_state, evidence_bytes in cases:
            with self.subTest(evidence_state=evidence_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path, _ = resolve_submission_snapshot_target(
                    archive_root=temp_dir,
                    project_code="GR2026BJ100001",
                    project_name="Beijing physical asset",
                    listing_date="2026-05-10",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>artifact with bad evidence</body></html>")
                os.makedirs(f"{os.path.splitext(html_path)[0]}_files", exist_ok=True)
                with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
                    handle.write('{"id": "cbex-physical-1"}')
                with open(f"{html_path}.peap-evidence.json", "wb") as handle:
                    handle.write(evidence_bytes)

                downloader = CbexPhysicalAssetDownloader(
                    html_root=temp_dir,
                    list_sources=[],
                    resume=True,
                    save_json=True,
                )
                summary = DownloadSummary()
                candidates = []

                downloader._prefetched_to_candidates(
                    prefetched_candidates=[
                        {
                            "uid": "cbex-physical-1",
                            "code": "GR2026BJ100001",
                            "url": "https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                            "project_name": "Beijing physical asset",
                            "disclosure_start": "2026-05-10",
                            "row": {"title": "Beijing physical asset"},
                        }
                    ],
                    outdir=temp_dir,
                    summary=summary,
                    seen=set(),
                    cands=candidates,
                    start=None,
                    end=None,
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
