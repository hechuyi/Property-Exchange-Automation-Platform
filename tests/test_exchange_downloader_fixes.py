from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import tempfile
import types
import unittest
import urllib.error
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader
from peap.downloaders.common import (
    DetailUnavailableError,
    HttpFetchedText,
    archive_integrity_fields,
)
from peap.downloaders.cquae import (
    ChongqingProjectDownloader,
    _cquae_list_sources,
    _normalize_list_url,
)
from peap.downloaders.cquae import (
    _decode_html as _decode_cquae_html,
)
from peap.downloaders.deal_cbex import (
    CbexDealEquityTransferDownloader,
    CbexDealPhysicalAssetDownloader,
)
from peap.downloaders.deal_cbex import (
    _has_complete_snapshot_sidecar as _cbex_deal_resume_complete,
)
from peap.downloaders.deal_cquae import (
    ChongqingDealEquityTransferDownloader,
)
from peap.downloaders.deal_cquae import (
    _has_complete_snapshot_sidecar as _cquae_deal_resume_complete,
)
from peap.downloaders.deal_sse import (
    ShanghaiDealEquityTransferDownloader,
    ShanghaiDealPhysicalAssetDownloader,
)
from peap.downloaders.deal_sse import (
    _is_resume_complete as _sse_deal_resume_complete,
)
from peap.downloaders.deal_tpre import (
    TianjinDealCapitalIncreaseDownloader,
    TianjinDealEquityTransferDownloader,
)
from peap.downloaders.deal_tpre import (
    _is_resume_complete as _tpre_deal_resume_complete,
)
from peap.downloaders.sse_physical import (
    ShanghaiCapitalIncreaseDownloader,
    ShanghaiEquityTransferDownloader,
    ShanghaiPhysicalAssetDownloader,
    ShanghaiPreDisclosureDownloader,
)
from peap.downloaders.tpre import DownloadSummary, TpreProjectDownloader, _ListQuerySpec


def _write_complete_sidecar(html_path: str, payload: dict[str, object]) -> None:
    with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                **payload,
                "save_status": "complete",
                **archive_integrity_fields(html_path),
            },
            handle,
            ensure_ascii=False,
        )


def _http_fetched(html: str, source_url: str, *, status: int = 200) -> HttpFetchedText:
    return HttpFetchedText(
        html,
        source_url=source_url,
        final_url=source_url,
        http_status=status,
        raw_bytes=html.encode("utf-8"),
    )


class DealDownloaderResumeCompletenessTest(unittest.TestCase):
    def test_deal_resume_rejects_invalid_shell_evidence_even_with_complete_sidecar(self) -> None:
        predicates = (
            ("sse", "deal_physical_asset", _sse_deal_resume_complete),
            ("cbex", "deal_equity_transfer", _cbex_deal_resume_complete),
            ("tpre", "deal_equity_transfer", _tpre_deal_resume_complete),
            ("cquae", "deal_equity_transfer", _cquae_deal_resume_complete),
        )
        for label, business_id, predicate in predicates:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年4月", f"{label.upper()}2026TEST-成交项目.html")
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>invalid shell snapshot</body></html>")
                with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
                    json.dump({"save_status": "complete", "metadata": {"project_code": f"{label.upper()}2026TEST"}}, handle)
                with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                    json.dump({"schema_version": 1, "page_kind": "invalid_shell"}, handle)

                self.assertFalse(predicate(html_path, business_id=business_id))

    def test_tpre_deal_resume_rejects_complete_sidecar_with_missing_transferee_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "2026年6月", "G62024TJ1000011-天津增资成交项目.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>成交公告 G62024TJ1000011 天津增资成交项目</body></html>")
            _write_complete_sidecar(
                html_path,
                {
                    "metadata": {
                        "task_id": "tpre:deal:deal_capital_increase",
                        "record_family": "deal",
                        "business_id": "deal_capital_increase",
                        "source_id": "tpre",
                        "project_code": "G62024TJ1000011",
                    },
                    "detail_payload": {
                        "transferee_details": [],
                        "transferee_details_warning": "missing-project-code",
                    },
                },
            )

            self.assertFalse(
                _tpre_deal_resume_complete(
                    html_path,
                    business_id="deal_capital_increase",
                )
            )


class TpreDownloaderFixTest(unittest.TestCase):
    def test_cbex_rows_business_filter_and_duplicate_are_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                include_pre_disclosure=False,
            )
            summary = DownloadSummary()
            candidates = []
            seen: set[str] = set()
            source = types.SimpleNamespace(label="unit")
            rows = [
                {
                    "code": "G32026BJ100001-0",
                    "url": "/xm/ypl/2026/04/13/pre.html",
                    "title": "CBEX pre disclosure row",
                    "disclosuretime": "2026-04-13",
                },
                {
                    "code": "G32026BJ100002",
                    "url": "/xm/cqzr/2026/04/13/formal.html",
                    "title": "CBEX formal row",
                    "disclosuretime": "2026-04-13",
                },
                {
                    "code": "G32026BJ100002",
                    "url": "/xm/cqzr/2026/04/13/formal-duplicate.html",
                    "title": "CBEX duplicate row",
                    "disclosuretime": "2026-04-13",
                },
            ]

            downloader._rows_to_candidates(
                rows=rows,
                source=source,
                outdir=temp_dir,
                summary=summary,
                seen=seen,
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.listed_items, 3)
        self.assertEqual(summary.skipped_by_business_filter, 1)
        self.assertEqual(summary.skipped_by_duplicate, 1)
        self.assertEqual(len(candidates), 1)
        list_accounted = (
            summary.skipped_by_list_date
            + summary.skipped_by_resume
            + summary.skipped_by_duplicate
            + summary.skipped_by_business_filter
            + len(candidates)
        )
        self.assertEqual(summary.listed_items - list_accounted, 0)

    def test_cbex_prefetched_business_filter_and_duplicate_are_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                list_sources=[],
                include_pre_disclosure=False,
            )
            summary = DownloadSummary()
            candidates = []
            seen: set[str] = set()
            prefetched = [
                {
                    "uid": "pre-row",
                    "code": "G32026BJ100001-0",
                    "url": "https://www.cbex.com.cn/xm/ypl/2026/04/13/pre.html",
                    "project_name": "CBEX pre disclosure row",
                    "row": {"code": "G32026BJ100001-0", "url": "/xm/ypl/2026/04/13/pre.html"},
                },
                {
                    "uid": "formal-row",
                    "code": "G32026BJ100002",
                    "url": "https://www.cbex.com.cn/xm/cqzr/2026/04/13/formal.html",
                    "project_name": "CBEX formal row",
                    "disclosure_start": "2026-04-13",
                    "row": {"code": "G32026BJ100002", "url": "/xm/cqzr/2026/04/13/formal.html"},
                },
                {
                    "uid": "formal-row",
                    "code": "G32026BJ100002",
                    "url": "https://www.cbex.com.cn/xm/cqzr/2026/04/13/formal-duplicate.html",
                    "project_name": "CBEX duplicate row",
                    "disclosure_start": "2026-04-13",
                    "row": {"code": "G32026BJ100002", "url": "/xm/cqzr/2026/04/13/formal-duplicate.html"},
                },
            ]

            downloader._prefetched_to_candidates(
                prefetched_candidates=prefetched,
                outdir=temp_dir,
                summary=summary,
                seen=seen,
                cands=candidates,
                start=None,
                end=None,
            )

        self.assertEqual(summary.listed_items, 3)
        self.assertEqual(summary.skipped_by_business_filter, 1)
        self.assertEqual(summary.skipped_by_duplicate, 1)
        self.assertEqual(len(candidates), 1)

    def test_run_uses_submission_root_directly_without_type_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TpreProjectDownloader(
                html_root=temp_dir,
                output_type="股权转让",
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
            )
            captured: dict[str, str] = {}

            def fake_collect(**kwargs):
                captured["output_dir"] = kwargs["output_dir"]

            with patch.object(downloader, "_collect_list_candidates", side_effect=fake_collect):
                downloader.run(start_date="2026-03-10", end_date="2026-03-10", list_only=True)

            self.assertEqual(captured["output_dir"], temp_dir)

    def test_rows_to_candidates_accepts_t3_project_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TpreProjectDownloader(
                html_root=temp_dir,
                output_type="股权转让",
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
            )
            summary = DownloadSummary()
            candidates = []
            seen_codes: set[str] = set()
            row = {
                "projectCode": "T32025TJ1000018-5",
                "title": "喀什国金稳盈创业投资有限公司90%股权",
                "projectLink": "https://trade.tpre.cn/transaction-view/data/formal-project-details?id=demo",
                "startTime": "2026-03-10",
            }

            downloader._rows_to_candidates(
                rows=[row],
                query=_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL"),
                output_dir=temp_dir,
                summary=summary,
                candidates=candidates,
                seen_codes=seen_codes,
                start=None,
                end=None,
            )

            self.assertEqual(summary.skipped_by_missing_xmid, 0)
            self.assertEqual(len(summary.typed_errors), 0)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].project_code, "T32025TJ1000018-5")

    def test_rows_to_candidates_builds_canonical_submission_path_from_scanned_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TpreProjectDownloader(
                html_root=temp_dir,
                output_type="股权转让",
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
            )
            summary = DownloadSummary()
            candidates = []
            seen_codes: set[str] = set()
            row = {
                "projectCode": "T32025TJ1000018-5",
                "title": "喀什国金稳盈创业投资有限公司90%股权",
                "projectLink": "https://trade.tpre.cn/transaction-view/data/formal-project-details?id=demo",
                "startTime": "2026-03-10",
            }

            downloader._rows_to_candidates(
                rows=[row],
                query=_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL"),
                output_dir=temp_dir,
                summary=summary,
                candidates=candidates,
                seen_codes=seen_codes,
                start=None,
                end=None,
            )

            self.assertEqual(
                candidates[0].html_path,
                os.path.join(
                    temp_dir,
                    "2026年3月",
                    "T32025TJ1000018-5-喀什国金稳盈创业投资有限公司90%股权.html",
                ),
            )

    def test_tpre_collect_list_candidates_rejects_falsey_non_list_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for payload in (
                {"code": 0, "data": []},
                {"code": 0, "data": {"records": "", "total": 1}},
            ):
                with self.subTest(payload=payload):
                    downloader = TpreProjectDownloader(
                        html_root=temp_dir,
                        output_type="股权转让",
                        list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
                        max_pages=1,
                    )
                    with patch.object(downloader, "_query_list_page", return_value=payload):
                        summary = downloader.run(
                            start_date="2026-03-01",
                            end_date="2026-03-31",
                            list_only=True,
                        )

                    self.assertEqual(summary.detail_candidates, 0)
                    self.assertEqual(len(summary.typed_errors), 1)
                    self.assertEqual(summary.typed_errors[0].failure_kind, "validation")
                    self.assertIn("invalid-data", summary.typed_errors[0].raw_reason)

    def test_tpre_rejects_non_list_list_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(TypeError, "list_queries must be a list or None"):
                TpreProjectDownloader(
                    html_root=temp_dir,
                    output_type="股权转让",
                    list_queries="bad",  # type: ignore[arg-type]
                )

    def test_tpre_project_challenge_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes(
            (95, 95, 106, 115, 108, 95, 99, 108, 101, 97, 114, 97, 110, 99, 101, 95, 115)
        ).decode("ascii")
        page_url = "https://unit.invalid/transaction-view/data/formal-project-details?id=53442"
        project_code = "T32026TJ1000999"
        rendered_html = f"<html><body><script>{shell_marker}=1</script><p>{project_code}</p></body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TpreProjectDownloader(
                html_root=temp_dir,
                output_type="股权转让",
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
            )
            html_path = os.path.join(temp_dir, "challenge-shell.html")

            downloader._save_complete_page(
                rendered_html=rendered_html,
                page_url=page_url,
                html_path=html_path,
            )

            saved_html = open(html_path, encoding="utf-8").read()
            evidence_path = f"{html_path}.peap-evidence.json"
            evidence_text = open(evidence_path, encoding="utf-8").read()
            evidence = json.loads(evidence_text)

        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(
            evidence["source_url_hash"],
            "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["final_url_hash"],
            "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(saved_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256(project_code.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(page_url, evidence_text)
        self.assertNotIn(shell_marker, evidence_text)
        self.assertNotIn(saved_html, evidence_text)

    def test_tpre_project_save_json_resume_requires_valid_sidecar_before_skip(self) -> None:
        for sidecar_state, sidecar_bytes in (
            ("missing", None),
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年4月", "GR2026TJ100001-天津挂牌项目.html")
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>partial previous run</body></html>")
                if sidecar_bytes is not None:
                    with open(os.path.splitext(html_path)[0] + ".json", "wb") as handle:
                        handle.write(sidecar_bytes)

                downloader = TpreProjectDownloader(
                    html_root=temp_dir,
                    resume=True,
                    save_json=True,
                    output_type="实物资产",
                    list_queries=[],
                )
                summary = downloader.run(
                    start_date="2026-04-17",
                    end_date="2026-04-17",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "project_code": "GR2026TJ100001",
                            "project_name": "天津挂牌项目",
                            "page_url": "https://trade.tpre.cn/transaction-view/demo",
                            "disclosure_start": "2026-04-17",
                            "row": {
                                "projectCode": "GR2026TJ100001",
                                "title": "天津挂牌项目",
                                "startTime": "2026-04-17",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_tpre_project_resume_rejects_invalid_or_corrupt_evidence_sidecar(self) -> None:
        for evidence_state, evidence_bytes in (
            ("invalid-shell", b'{"page_kind": "invalid_shell"}'),
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(evidence_state=evidence_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年4月", "GR2026TJ100001-天津挂牌项目.html")
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>previous run</body></html>")
                with open(f"{html_path}.peap-evidence.json", "wb") as handle:
                    handle.write(evidence_bytes)

                downloader = TpreProjectDownloader(
                    html_root=temp_dir,
                    resume=True,
                    save_json=False,
                    output_type="实物资产",
                    list_queries=[],
                )
                summary = downloader.run(
                    start_date="2026-04-17",
                    end_date="2026-04-17",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "project_code": "GR2026TJ100001",
                            "project_name": "天津挂牌项目",
                            "page_url": "https://trade.tpre.cn/transaction-view/demo",
                            "disclosure_start": "2026-04-17",
                            "row": {
                                "projectCode": "GR2026TJ100001",
                                "title": "天津挂牌项目",
                                "startTime": "2026-04-17",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_tpre_item_saved_callback_failure_marks_detail_failed_without_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "2026年3月", "T32025TJ1000018-5-天津股权项目.html")
            downloader = TpreProjectDownloader(
                html_root=tmp_dir,
                output_type="股权转让",
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：T32025TJ1000018-5</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    200,
                )
            )
            downloader._save_complete_page = Mock()  # type: ignore[method-assign]
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_code="T32025TJ1000018-5",
                project_name="天津股权项目",
                page_url="https://trade.tpre.cn/transaction-view/data/formal-project-details?id=demo",
                html_path=html_path,
                row={"projectCode": "T32025TJ1000018-5", "startTime": "2026-03-10"},
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)

    def test_tpre_callback_failure_artifact_is_not_resume_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "2026年3月", "T32025TJ1000018-5-天津股权项目.html")
            downloader = TpreProjectDownloader(
                html_root=tmp_dir,
                output_type="股权转让",
                save_json=True,
                list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：T32025TJ1000018-5</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    200,
                )
            )
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_code="T32025TJ1000018-5",
                project_name="天津股权项目",
                page_url="https://trade.tpre.cn/transaction-view/data/formal-project-details?id=demo",
                html_path=html_path,
                row={"projectCode": "T32025TJ1000018-5", "startTime": "2026-03-10"},
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

            resume_downloader = TpreProjectDownloader(
                html_root=tmp_dir,
                resume=True,
                save_json=True,
                output_type="股权转让",
                list_queries=[],
            )
            resume_summary = resume_downloader.run(
                start_date="2026-03-10",
                end_date="2026-03-10",
                list_only=True,
                prefetched_candidates=[
                    {
                        "project_code": "T32025TJ1000018-5",
                        "project_name": "天津股权项目",
                        "page_url": "https://trade.tpre.cn/transaction-view/data/formal-project-details?id=demo",
                        "disclosure_start": "2026-03-10",
                        "row": {
                            "projectCode": "T32025TJ1000018-5",
                            "title": "天津股权项目",
                            "startTime": "2026-03-10",
                        },
                    }
                ],
            )

            self.assertEqual(summary.saved, 0)
            self.assertTrue(os.path.isfile(html_path))
            self.assertTrue(os.path.isdir(os.path.splitext(html_path)[0] + "_files"))
            self.assertTrue(os.path.isfile(os.path.splitext(html_path)[0] + ".json"))
            self.assertEqual(resume_summary.skipped_by_resume, 0)
            self.assertEqual(resume_summary.detail_candidates, 1)


class CquaeDownloaderFixTest(unittest.TestCase):
    def test_decode_html_rejects_lossy_fallback_after_strict_candidates_fail(self) -> None:
        with self.assertRaises(UnicodeDecodeError):
            _decode_cquae_html(b"\x80\x80\x80", "utf-8")

    def test_decode_html_uses_gb18030_when_charset_hint_is_wrong(self) -> None:
        html = "<html><body>重庆产权交易所</body></html>"

        self.assertEqual(_decode_cquae_html(html.encode("gb18030"), "utf-8"), html)

    def test_run_uses_submission_root_directly_without_type_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=[],
                output_type="股权转让",
            )
            captured: dict[str, str] = {}

            def fake_collect(**kwargs):
                captured["output_dir"] = kwargs["output_dir"]

            with patch.object(downloader, "_collect_list_candidates", side_effect=fake_collect):
                downloader.run(start_date="2026-03-10", end_date="2026-03-10", list_only=True)

            self.assertEqual(captured["output_dir"], temp_dir)

    def test_normalize_list_url_lowercases_project_path_and_quotes_query(self) -> None:
        raw_url = "https://www.cquae.com/Project?q=s&projectID=3&price=5000万-1亿&page=2"

        normalized = _normalize_list_url(raw_url)

        self.assertEqual(
            normalized,
            "https://www.cquae.com/project?q=s&projectID=3&price=5000%E4%B8%87-1%E4%BA%BF&page=2",
        )

    def test_extract_next_page_url_returns_normalized_url(self) -> None:
        downloader = ChongqingProjectDownloader(
            html_root="C:\\temp",
            list_sources=[],
        )
        html = """
        <html><body>
          <a class="CPageus" href="/Project?q=s&projectID=3&price=5000万-1亿&page=2">下一页></a>
        </body></html>
        """
        from bs4 import BeautifulSoup

        next_url = downloader._extract_next_page_url(
            soup=BeautifulSoup(html, "html.parser"),
            current_url="https://www.cquae.com/project?q=s&projectID=3&price=5000%E4%B8%87-1%E4%BA%BF&page=1",
        )

        self.assertEqual(
            next_url,
            "https://www.cquae.com/project?q=s&projectID=3&price=5000%E4%B8%87-1%E4%BA%BF&page=2",
        )

    def test_fetch_list_html_retries_retryable_http_error(self) -> None:
        downloader = ChongqingProjectDownloader(
            html_root="C:\\temp",
            list_sources=[],
        )
        fake_html = "<html></html>".encode("utf-8")

        class _Response:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload
                self.headers = self

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload

            def get_content_charset(self):
                return "utf-8"

        calls: list[str] = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    521,
                    "origin down",
                    hdrs=None,
                    fp=None,
                )
            return _Response(fake_html)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep"):
            html = downloader._fetch_list_html(
                "https://www.cquae.com/Project?q=s&projectID=1&priceID=32&nt=1&page=2"
            )

        self.assertEqual(html, "<html></html>")
        self.assertEqual(
            calls,
            [
                "https://www.cquae.com/project?q=s&projectID=1&priceID=32&nt=1&page=2",
                "https://www.cquae.com/project?q=s&projectID=1&priceID=32&nt=1&page=2",
            ],
        )

    def test_cquae_project_list_shell_without_items_records_typed_list_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=_cquae_list_sources("equity_transfer"),
                max_pages=1,
            )
            shell_html = "<html><head><title>访问验证</title></head><body>重庆产权交易网</body></html>"

            with patch.object(
                downloader,
                "_fetch_list_html",
                return_value=_http_fetched(
                    shell_html,
                    downloader.list_sources[0].list_url,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("no-list-items", summary.typed_errors[0].raw_reason)

    def test_cquae_project_official_zero_total_list_is_observed_without_list_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=_cquae_list_sources("equity_transfer"),
                max_pages=1,
            )
            empty_html = """
            <html>
              <head><title>项目中心- 重庆产权交易网</title></head>
              <body>
                <div class="n2_top">项目中心</div>
                <div>共找到 0 条(项目)记录</div>
              </body>
            </html>
            """

            with patch.object(
                downloader,
                "_fetch_list_html",
                return_value=_http_fetched(
                    empty_html,
                    downloader.list_sources[0].list_url,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.typed_errors, [])
        self.assertEqual(
            summary.list_page_observations,
            [
                {
                    "source_id": "cquae",
                    "source_label": "equity-formal",
                    "page_index": 1,
                    "status": "empty",
                    "declared_total": 0,
                    "parsed_items": 0,
                    "blocked": False,
                    "identity_valid": True,
                }
            ],
        )

    def test_cquae_project_positive_total_without_items_remains_list_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=_cquae_list_sources("equity_transfer"),
                max_pages=1,
            )
            broken_html = """
            <html>
              <head><title>项目中心- 重庆产权交易网</title></head>
              <body>
                <div class="n2_top">项目中心</div>
                <div>共找到 2 条(项目)记录</div>
              </body>
            </html>
            """

            with patch.object(
                downloader,
                "_fetch_list_html",
                return_value=_http_fetched(
                    broken_html,
                    downloader.list_sources[0].list_url,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("positive-total-without-items", summary.typed_errors[0].raw_reason)

    def test_cquae_project_identity_mismatch_with_item_selector_remains_list_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=_cquae_list_sources("equity_transfer"),
                max_pages=1,
            )
            foreign_html = """
            <html>
              <head><title>其它站点</title></head>
              <body>
                <div class="n2_List itcon"><a href="/Project/Show?id=53442">伪列表项</a></div>
              </body>
            </html>
            """

            with patch.object(
                downloader,
                "_fetch_list_html",
                return_value=_http_fetched(
                    foreign_html,
                    downloader.list_sources[0].list_url,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("identity-mismatch", summary.typed_errors[0].raw_reason)

    def test_cquae_rejects_non_list_list_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(TypeError, "list_sources must be a list or None"):
                ChongqingProjectDownloader(
                    html_root=temp_dir,
                    output_type="股权转让",
                    list_sources="bad",  # type: ignore[arg-type]
                )

    def test_cquae_resume_path_rejects_bad_cached_entry_shape(self) -> None:
        downloader = ChongqingProjectDownloader(
            html_root="/tmp/test",
            output_type="股权转让",
            list_sources=[],
        )
        downloader._resume_index = {"project-1": []}  # type: ignore[dict-item]

        with self.assertRaisesRegex(TypeError, "resume index entry must be a mapping"):
            downloader._resolve_resume_html_path(
                output_dir="/tmp/test",
                project_id="project-1",
                project_code="",
                project_name="demo",
                listing_date="2026-03-01",
            )

    def test_cquae_resume_index_rejects_corrupt_or_non_object_state(self) -> None:
        cases = (
            ("invalid-json", "{not json"),
            ("non-object", "[]"),
            ("invalid-entry", '{"project-1": []}'),
        )
        for state, content in cases:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                resume_index_path = os.path.join(temp_dir, ".cquae_resume_index.json")
                with open(resume_index_path, "w", encoding="utf-8") as handle:
                    handle.write(content)

                with self.assertRaisesRegex(ValueError, "cquae resume index"):
                    ChongqingProjectDownloader(
                        html_root=temp_dir,
                        output_type="股权转让",
                        list_sources=[],
                        resume=True,
                    ).run(
                        start_date="2026-03-01",
                        end_date="2026-03-31",
                        list_only=True,
                        prefetched_candidates=[],
                    )

    def test_cquae_project_challenge_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes(
            (95, 95, 106, 115, 108, 95, 99, 108, 101, 97, 114, 97, 110, 99, 101, 95, 115)
        ).decode("ascii")
        page_url = "https://unit.invalid/Project/Show?id=53442"
        rendered_html = f"<html><body><script>{shell_marker}=1</script><p>CQID53442</p></body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                list_sources=[],
                output_type="股权转让",
            )
            html_path = os.path.join(temp_dir, "challenge-shell.html")

            downloader._save_complete_page(
                rendered_html=rendered_html,
                page_url=page_url,
                html_path=html_path,
            )

            saved_html = open(html_path, encoding="utf-8").read()
            evidence_path = f"{html_path}.peap-evidence.json"
            evidence_text = open(evidence_path, encoding="utf-8").read()
            evidence = json.loads(evidence_text)

        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(
            evidence["source_url_hash"],
            "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["final_url_hash"],
            "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(saved_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256(b"CQID53442").hexdigest(),
        )
        self.assertNotIn(page_url, evidence_text)
        self.assertNotIn(shell_marker, evidence_text)
        self.assertNotIn(saved_html, evidence_text)

    def test_cquae_item_saved_callback_failure_marks_detail_failed_without_resume_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
            resume_index_path = os.path.join(tmp_dir, ".cquae_resume_index.json")
            downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                list_sources=[],
                output_type="股权转让",
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._resume_index_path = resume_index_path
            downloader._resume_index = {}
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：CQID53442</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    "https://www.cquae.com/Project/Show?id=53442",
                    200,
                )
            )
            downloader._save_complete_page = Mock()  # type: ignore[method-assign]
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_id="53442",
                project_name="重庆股权项目",
                page_url="https://www.cquae.com/Project/Show?id=53442",
                html_path=html_path,
                list_url="https://www.cquae.com/Project",
                row={"project_id": "53442", "disclosure_start": "2026-03-10"},
                project_code="",
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )
            downloader._save_resume_index()
            resume_index = json.load(open(resume_index_path, encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
        self.assertNotIn("53442", downloader._resume_index)
        self.assertNotIn("53442", resume_index)

    def test_cquae_home_redirect_detail_is_counted_as_unavailable_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "2026年4月", "CQID1332063-重庆股权项目.html")
            downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                list_sources=[],
                output_type="股权转让",
            )
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                side_effect=DetailUnavailableError(
                    reason="home_redirect",
                    final_url="https://www.cquae.com/",
                    title="首页 - 重庆产权交易网",
                    html_len=96944,
                    expected_identifier="1332063",
                )
            )
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_id="1332063",
                project_name="重庆股权项目",
                page_url="https://www.cquae.com/Project/Show?id=1332063",
                html_path=html_path,
                list_url="https://www.cquae.com/Project",
                row={"project_id": "1332063", "disclosure_start": "2026-04-10"},
                project_code="",
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.skipped_by_detail_unavailable, 1)
        self.assertEqual(summary.typed_errors, [])

    def test_cquae_home_redirect_unavailable_predicate_requires_home_url(self) -> None:
        homepage_html = "<html><body><h1>首页</h1><div>重庆产权交易网</div></body></html>"

        self.assertTrue(
            ChongqingProjectDownloader._is_home_redirect_detail_unavailable(
                html=homepage_html,
                title="首页 - 重庆产权交易网",
                current_url="https://www.cquae.com/",
            )
        )
        self.assertFalse(
            ChongqingProjectDownloader._is_home_redirect_detail_unavailable(
                html=homepage_html,
                title="首页 - 重庆产权交易网",
                current_url="https://www.cquae.com/Project/Show?id=1332063",
            )
        )

    def test_cquae_callback_failure_artifact_is_not_resume_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
            downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                list_sources=[],
                output_type="股权转让",
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：CQID53442</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    "https://www.cquae.com/Project/Show?id=53442",
                    200,
                )
            )
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_id="53442",
                project_name="重庆股权项目",
                page_url="https://www.cquae.com/Project/Show?id=53442",
                html_path=html_path,
                list_url="https://www.cquae.com/Project",
                row={"project_id": "53442", "disclosure_start": "2026-03-10"},
                project_code="",
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

            resume_downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                resume=True,
                save_json=False,
                output_type="股权转让",
                list_sources=[],
            )
            resume_summary = resume_downloader.run(
                start_date="2026-03-10",
                end_date="2026-03-10",
                list_only=True,
                prefetched_candidates=[
                    {
                        "project_id": "53442",
                        "project_code": "CQID53442",
                        "project_name": "重庆股权项目",
                        "page_url": "https://www.cquae.com/Project/Show?id=53442",
                        "disclosure_start": "2026-03-10",
                        "row": {
                            "project_id": "53442",
                            "project_code": "CQID53442",
                            "project_name": "重庆股权项目",
                            "disclosure_start": "2026-03-10",
                        },
                    }
                ],
            )

            self.assertEqual(summary.saved, 0)
            self.assertTrue(os.path.isfile(html_path))
            self.assertTrue(os.path.isdir(os.path.splitext(html_path)[0] + "_files"))
            self.assertEqual(resume_summary.skipped_by_resume, 0)
            self.assertEqual(resume_summary.detail_candidates, 1)

    def test_cquae_project_save_json_resume_requires_valid_sidecar_before_skip(self) -> None:
        for sidecar_state, sidecar_bytes in (
            ("missing", None),
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>partial previous run</body></html>")
                if sidecar_bytes is not None:
                    with open(os.path.splitext(html_path)[0] + ".json", "wb") as handle:
                        handle.write(sidecar_bytes)

                downloader = ChongqingProjectDownloader(
                    html_root=temp_dir,
                    resume=True,
                    save_json=True,
                    output_type="股权转让",
                    list_sources=[],
                )
                summary = downloader.run(
                    start_date="2026-03-10",
                    end_date="2026-03-10",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "project_id": "53442",
                            "project_code": "CQID53442",
                            "project_name": "重庆股权项目",
                            "page_url": "https://www.cquae.com/Project/Show?id=53442",
                            "disclosure_start": "2026-03-10",
                            "row": {
                                "project_id": "53442",
                                "project_code": "CQID53442",
                                "project_name": "重庆股权项目",
                                "disclosure_start": "2026-03-10",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_cquae_project_resume_rejects_invalid_or_corrupt_evidence_sidecar(self) -> None:
        for evidence_state, evidence_bytes in (
            ("invalid-shell", b'{"page_kind": "invalid_shell"}'),
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(evidence_state=evidence_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>previous run</body></html>")
                with open(f"{html_path}.peap-evidence.json", "wb") as handle:
                    handle.write(evidence_bytes)

                downloader = ChongqingProjectDownloader(
                    html_root=temp_dir,
                    resume=True,
                    save_json=False,
                    output_type="股权转让",
                    list_sources=[],
                )
                summary = downloader.run(
                    start_date="2026-03-10",
                    end_date="2026-03-10",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "project_id": "53442",
                            "project_code": "CQID53442",
                            "project_name": "重庆股权项目",
                            "page_url": "https://www.cquae.com/Project/Show?id=53442",
                            "disclosure_start": "2026-03-10",
                            "row": {
                                "project_id": "53442",
                                "project_code": "CQID53442",
                                "project_name": "重庆股权项目",
                                "disclosure_start": "2026-03-10",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_cquae_project_detail_final_path_resume_skips_complete_recomputed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            final_html_path = os.path.join(tmp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
            os.makedirs(os.path.splitext(final_html_path)[0] + "_files", exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "task_id": "cquae:listing:equity_transfer",
                    "source_id": "cquae",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "project_id": "53442",
                    "project_code": "CQID53442",
                },
            )

            stale_html_path = os.path.join(tmp_dir, "2026年3月", "53442-重庆股权项目.html")
            downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                list_sources=[],
                output_type="股权转让",
                resume=True,
                save_json=True,
                item_saved_callback=Mock(side_effect=RuntimeError("callback must not run")),
            )
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：CQID53442</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    "https://www.cquae.com/Project/Show?id=53442",
                    200,
                )
            )
            downloader._save_complete_page = Mock()  # type: ignore[method-assign]
            downloader._write_json = Mock()  # type: ignore[method-assign]
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                project_id="53442",
                project_name="重庆股权项目",
                page_url="https://www.cquae.com/Project/Show?id=53442",
                html_path=stale_html_path,
                list_url="https://www.cquae.com/Project",
                row={"project_id": "53442", "disclosure_start": "2026-03-10"},
                project_code="",
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.detail_failed, 0)
        downloader._save_complete_page.assert_not_called()
        downloader._write_json.assert_not_called()

    def test_cquae_run_accounts_detail_resume_skip_without_list_double_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            final_html_path = os.path.join(tmp_dir, "2026年3月", "CQID53442-重庆股权项目.html")
            os.makedirs(os.path.splitext(final_html_path)[0] + "_files", exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "task_id": "cquae:listing:equity_transfer",
                    "source_id": "cquae",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "project_id": "53442",
                    "project_code": "CQID53442",
                },
            )

            downloader = ChongqingProjectDownloader(
                html_root=tmp_dir,
                list_sources=[],
                output_type="股权转让",
                resume=True,
                save_json=True,
                item_saved_callback=Mock(side_effect=RuntimeError("callback must not run")),
            )
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：CQID53442</div>"
                    "<div>信息披露起始日期：2026-03-10</div>"
                    "</body></html>",
                    "https://www.cquae.com/Project/Show?id=53442",
                    200,
                )
            )
            downloader._save_complete_page = Mock()  # type: ignore[method-assign]
            downloader._write_json = Mock()  # type: ignore[method-assign]

            async def fake_download_candidates_concurrently(*, candidates, summary, start, end):
                page = types.SimpleNamespace(close=AsyncMock())
                context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
                for candidate in candidates:
                    await downloader._process_candidate(
                        candidate=candidate,
                        context=context,
                        summary=summary,
                        start=start,
                        end=end,
                        timeout_error_cls=TimeoutError,
                    )

            downloader._download_candidates_concurrently = fake_download_candidates_concurrently  # type: ignore[method-assign]

            summary = downloader.run(
                start_date="2026-03-10",
                end_date="2026-03-10",
                list_only=False,
                prefetched_candidates=[
                    {
                        "project_id": "53442",
                        "project_code": "",
                        "project_name": "重庆股权项目",
                        "page_url": "https://www.cquae.com/Project/Show?id=53442",
                        "disclosure_start": "2026-03-10",
                        "row": {
                            "project_id": "53442",
                            "project_name": "重庆股权项目",
                            "disclosure_start": "2026-03-10",
                        },
                    }
                ],
            )

        self.assertEqual(summary.listed_items, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.list_unaccounted, 0)
        self.assertEqual(summary.detail_unaccounted, 0)
        self.assertEqual(summary.detail_failed, 0)


class DealBrowserFetchFixTest(unittest.TestCase):
    class _FetcherContext:
        def __init__(self, fetch):
            self.fetch = fetch

        def __enter__(self):
            def fetch_with_evidence(url: str, rendered: bool = False):
                try:
                    value = self.fetch(url, rendered=rendered)
                except TypeError:
                    value = self.fetch(url)
                if isinstance(value, HttpFetchedText):
                    return value
                return _http_fetched(value, url)

            return fetch_with_evidence

        def __exit__(self, exc_type, exc, tb):
            return False

    class _RenderedAwareFetch:
        def __init__(self, raw_html: str, rendered_html: str) -> None:
            self.raw_html = raw_html
            self.rendered_html = rendered_html
            self.calls: list[tuple[str, bool]] = []

        def __call__(self, url: str, rendered: bool = False) -> str:
            self.calls.append((url, rendered))
            return self.rendered_html if rendered else self.raw_html


    def test_cbex_deal_extracts_official_textarea_source_rows_from_real_list_page(self) -> None:
        list_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head>
        <body>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2026BJ1001513&quot;,&quot;object&quot;:&quot;沪东中华造船（集团）有限公司部分报废设备（一）&quot;,&quot;evaluatevalue&quot;:&quot;15.141&quot;,&quot;objectprice&quot;:&quot;15.141&quot;,&quot;tradevalue&quot;:&quot;15.191&quot;,&quot;tradedate&quot;:&quot;2026-05-07&quot;,&quot;projectid&quot;:&quot;558160&quot;,&quot;id&quot;:&quot;39386-553787&quot;,&quot;buyername&quot;:&quot;上海梦兴圆贸易有限公司&quot;,&quot;projectlinkurl&quot;:&quot;https://otc.cbex.com/dzsw/detail/558160.html&quot;},&quot;bidinfolist&quot;:{&quot;utrmcemsbidinfo&quot;:[{&quot;bidder&quot;:&quot;上海梦兴圆贸易有限公司&quot;,&quot;bidprice&quot;:&quot;15.191&quot;}]}}
            </textarea>
          </div>
          <p class="public-title project-zz-title">成交结果公示</p>
          <table class="public-table public-table-text public-table-td5 public-table-zzcj">
            <thead><tr><th>项目编号</th><th>标的名称</th><th>转让标的评估值（万元）</th><th>转让底价（万元）</th><th>交易价格（万元）</th></tr></thead>
            <tbody id="list"></tbody>
          </table>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealPhysicalAssetDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 5, 8)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(lambda url: list_html),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-05-07", end_date="2026-05-07", list_only=True)

        self.assertEqual(summary.detail_candidates, 1)
        entry = summary.candidate_entries[0]
        self.assertEqual(entry["source_url"], "https://www.cbex.com.cn/xm/zczr/cjjggs/")
        self.assertEqual(entry["project_code"], "GR2026BJ1001513")
        self.assertEqual(entry["project_name"], "沪东中华造船（集团）有限公司部分报废设备（一）")
        self.assertEqual(entry["deal_date"], "2026-05-07")
        self.assertEqual(entry["deal_date_basis"], "deal_date")
        self.assertEqual(entry["row"]["source_kind"], "cbex_textarea_source")
        self.assertEqual(entry["row"]["tradevalue"], "15.191")
        self.assertEqual(entry["row"]["objectprice"], "15.141")

    def test_cbex_deal_discovers_static_index_pages_when_page_has_no_pagination_links(self) -> None:
        root_html = """
        <!doctype html><html><body>
          <script>var currentPage = 0; var countPage = 3; var pageSize = 15;</script>
          <div id="list"></div>
        </body></html>
        """
        target_page_html = """
        <!doctype html><html><body>
          <script>var currentPage = 1; var countPage = 3; var pageSize = 15;</script>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2026BJ1001024&quot;,&quot;object&quot;:&quot;天津市武清区花乡家园36号楼-4-101房产&quot;,&quot;tradevalue&quot;:&quot;167.849057&quot;,&quot;tradedate&quot;:&quot;2026-05-29&quot;,&quot;projectid&quot;:&quot;5581024&quot;}}
            </textarea>
          </div>
        </body></html>
        """
        stale_page_html = """
        <!doctype html><html><body>
          <script>var currentPage = 2; var countPage = 3; var pageSize = 15;</script>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2025BJ1006830&quot;,&quot;object&quot;:&quot;北京第二机床厂有限公司45台设备&quot;,&quot;tradevalue&quot;:&quot;140.00&quot;,&quot;tradedate&quot;:&quot;2025-12-23&quot;,&quot;projectid&quot;:&quot;5586830&quot;}}
            </textarea>
          </div>
        </body></html>
        """
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if url.endswith("index_1.html"):
                return target_page_html
            if url.endswith("index_2.html"):
                return stale_page_html
            return root_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealPhysicalAssetDownloader(html_root=temp_dir, max_pages=3)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 6, 5)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-05-29", end_date="2026-05-29", list_only=True)

        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.skipped_by_list_date, 1)
        self.assertTrue(any(url.endswith("/index_1.html") for url in fetched_urls))
        self.assertTrue(any(url.endswith("/index_2.html") for url in fetched_urls))
        self.assertEqual(summary.candidate_entries[0]["project_code"], "GR2026BJ1001024")
        self.assertEqual(summary.candidate_entries[0]["deal_date"], "2026-05-29")

    def test_cbex_deal_probes_next_static_index_page_when_count_page_lags_real_pages(self) -> None:
        root_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head><body>
          <script>var currentPage = 0; var countPage = 2; var pageSize = 15;</script>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2026BJ1002410&quot;,&quot;object&quot;:&quot;一拖（洛阳）柴油机有限公司拟处置部分资产&quot;,&quot;tradevalue&quot;:&quot;16.0&quot;,&quot;tradedate&quot;:&quot;2026-06-04&quot;}}
            </textarea>
          </div>
        </body></html>
        """
        first_index_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head><body>
          <script>var currentPage = 1; var countPage = 2; var pageSize = 15;</script>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2026BJ1002799&quot;,&quot;object&quot;:&quot;6月2日成交项目&quot;,&quot;tradevalue&quot;:&quot;12.0&quot;,&quot;tradedate&quot;:&quot;2026-06-02&quot;}}
            </textarea>
          </div>
        </body></html>
        """
        lagged_page_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head><body>
          <script>var currentPage = 2; var countPage = 3; var pageSize = 15;</script>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrmcemsproject&quot;:{&quot;projectcode&quot;:&quot;GR2026BJ1001024&quot;,&quot;object&quot;:&quot;天津市武清区花乡家园36号楼-4-101房产&quot;,&quot;tradevalue&quot;:&quot;167.849057&quot;,&quot;tradedate&quot;:&quot;2026-05-29&quot;}}
            </textarea>
          </div>
        </body></html>
        """
        not_found_html = "<!doctype html><html><head><title>北京产权交易所_404页面</title></head><body>404</body></html>"
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if url.endswith("index_2.html"):
                return lagged_page_html
            if url.endswith("index_1.html"):
                return first_index_html
            if url.endswith("index_3.html"):
                return not_found_html
            return root_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealPhysicalAssetDownloader(html_root=temp_dir, max_pages=6)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 6, 5)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-05-29", end_date="2026-05-29", list_only=True)

        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.candidate_entries[0]["project_code"], "GR2026BJ1001024")
        self.assertEqual(summary.candidate_entries[0]["deal_date"], "2026-05-29")
        self.assertTrue(any(url.endswith("/index_2.html") for url in fetched_urls))
        self.assertTrue(any(url.endswith("/index_3.html") for url in fetched_urls))

    def test_tpre_deal_filters_by_strict_real_deal_date_not_lookback_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            summary = DownloadSummary()
            candidates = []
            with patch.object(downloader, "_collection_date", return_value=dt.date(2026, 6, 5)):
                downloader._rows_to_candidates(
                    rows=[
                        {
                            "id": "old-row",
                            "projectCode": "T32026TJ1000001",
                            "projectName": "天津产权窗口外成交",
                            "contractSignTime": "2026-02-27",
                        },
                        {
                            "id": "target-row",
                            "projectCode": "T32026TJ1000002",
                            "projectName": "天津产权窗口内成交",
                            "contractSignTime": "2026-05-27",
                        },
                    ],
                    output_dir=temp_dir,
                    start=dt.date(2026, 5, 15),
                    end=dt.date(2026, 5, 30),
                    summary=summary,
                    seen_notice_ids=set(),
                    candidates=candidates,
                )

        self.assertEqual(summary.listed_items, 2)
        self.assertEqual(summary.skipped_by_list_date, 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].project_code, "T32026TJ1000002")
        self.assertEqual(summary.candidate_entries[0]["deal_date"], "2026-05-27")

    def test_cbex_deal_saves_original_official_list_page_for_textarea_source_candidate(self) -> None:
        raw_list_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head>
        <body>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrgcemsproject&quot;:{&quot;projectcode&quot;:&quot;G32026BJ1000085&quot;,&quot;objectprice&quot;:&quot;4998.8605万元&quot;,&quot;object&quot;:&quot;东北中小企业融资再担保股份有限公司34,690,000股股份（占总股本的1.1365%）&quot;,&quot;tradevalue&quot;:&quot;4998.8605&quot;,&quot;tradedate&quot;:&quot;2026-04-29&quot;,&quot;projectid&quot;:&quot;558328&quot;},&quot;utrgcemsobject&quot;:{&quot;objectevaluatevalue&quot;:&quot;4998.86&quot;}}
            </textarea>
          </div>
          <p class="public-title project-zz-title">成交结果公示</p>
          <table><thead><tr><th>项目编号</th><th>标的名称</th><th>转让标的评估值（万元）</th><th>转让底价（万元）</th><th>交易价格（万元）</th></tr></thead><tbody id="list"></tbody></table>
        </body></html>
        """
        rendered_list_html = """
        <!doctype html><html><head><title>北京产权交易所_成交结果公示</title></head>
        <body>
          <div style="display:none">
            <textarea class="source" rows="3" cols="100">
              {&quot;utrgcemsproject&quot;:{&quot;projectcode&quot;:&quot;G32026BJ1000085&quot;,&quot;objectprice&quot;:&quot;4998.8605万元&quot;,&quot;object&quot;:&quot;东北中小企业融资再担保股份有限公司34,690,000股股份（占总股本的1.1365%）&quot;,&quot;tradevalue&quot;:&quot;4998.8605&quot;,&quot;tradedate&quot;:&quot;2026-04-29&quot;,&quot;projectid&quot;:&quot;558328&quot;},&quot;utrgcemsobject&quot;:{&quot;objectevaluatevalue&quot;:&quot;4998.86&quot;}}
            </textarea>
          </div>
          <p class="public-title project-zz-title">成交结果公示</p>
          <table><thead><tr><th>项目编号</th><th>标的名称</th><th>转让标的评估值（万元）</th><th>转让底价（万元）</th><th>交易价格（万元）</th></tr></thead><tbody id="list"><tr><td>G32026BJ1000085</td><td>东北中小企业融资再担保股份有限公司34,690,000股股份（占总股本的1.1365%）</td><td>4998.86</td><td>4998.8605万元</td><td>4998.8605</td></tr></tbody></table>
        </body></html>
        """
        fetch = self._RenderedAwareFetch(raw_list_html, rendered_list_html)

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 5, 8)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-29", end_date="2026-04-29", list_only=False)

            self.assertEqual(summary.saved, 1)
            saved_path = os.path.join(temp_dir, next(iter(summary.downloaded_this_run)))
            saved_html = open(saved_path, encoding="utf-8").read()
            sidecar = json.load(open(os.path.splitext(saved_path)[0] + ".json", encoding="utf-8"))

        self.assertEqual(saved_html, rendered_list_html)
        self.assertIn("北京产权交易所_成交结果公示", saved_html)
        self.assertIn("<tr><td>G32026BJ1000085</td>", saved_html)
        self.assertNotIn("<tbody id=\"list\"></tbody>", saved_html)
        self.assertNotIn("CBEX Deal Notice", saved_html)
        self.assertNotIn("deal_detail_html", saved_html)
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026BJ1000085")
        self.assertEqual(sidecar["detail_url"], "https://www.cbex.com.cn/xm/cqzr/cjjggs/")
        self.assertEqual(sidecar["detail_payload"]["utrgcemsproject"]["tradevalue"], "4998.8605")
        self.assertIn(("https://www.cbex.com.cn/xm/cqzr/cjjggs/", True), fetch.calls)

    def test_cbex_deal_run_uses_browser_fetcher_for_list_and_detail_pages(self) -> None:
        list_html = """
        <html><body>
          <ul>
            <li>
              <a href="/xm/cqzr/2026/04/13/G32026BJ100001.html">CBEX股权成交公告</a>
              <span>G32026BJ100001 2026-04-13</span>
            </li>
          </ul>
        </body></html>
        """
        detail_html = "<html><body><h1>CBEX股权成交公告</h1><p>成交日期：2026-04-13</p></body></html>"
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if "/cjjggs/" in url:
                return list_html
            return detail_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_fetched, 1)
        self.assertEqual(len(summary.typed_errors), 0)
        self.assertTrue(any("/xm/cqzr/cjjggs/" in url for url in fetched_urls))
        self.assertTrue(any(url.endswith("/G32026BJ100001.html") for url in fetched_urls))

    def test_cbex_deal_rejects_list_page_rows_without_detail_link(self) -> None:
        list_html = """
        <html><body>
          <ul>
            <li>
              <a href="/xm/cqzr/2026/04/13/G32026BJ100001.html">CBEX有详情成交公告</a>
              <span>G32026BJ100001 2026-04-13</span>
            </li>
            <li class="deal-row">
              <span class="title">CBEX无详情成交公告</span>
              <span class="code">G32026BJ100002</span>
              <span class="date">2026-04-13</span>
              <strong>CBEX list-only payload</strong>
            </li>
          </ul>
        </body></html>
        """
        detail_html = "<html><body><strong>CBEX detail payload</strong><p>成交日期：2026-04-13</p></body></html>"
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if "/cjjggs/" in url:
                return list_html
            return detail_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

            saved_html = "\n".join(
                open(os.path.join(temp_dir, relpath), encoding="utf-8").read()
                for relpath in summary.downloaded_this_run
            )

        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_fetched, 1)
        self.assertEqual(len(summary.typed_errors), 0)
        self.assertEqual(sum(1 for url in fetched_urls if "/cjjggs/" in url), 1)
        self.assertEqual(sum(1 for url in fetched_urls if url.endswith("/G32026BJ100001.html")), 1)
        self.assertIn("CBEX detail payload", saved_html)
        self.assertNotIn("CBEX list-only payload", saved_html)

    def test_cbex_deal_does_not_materialize_list_page_candidate_from_code_and_date_only(self) -> None:
        list_html = """
        <html><body>
          <ul>
            <li class="deal-row">
              <span class="title">CBEX其他无详情成交公告</span>
              <span class="code">G32026BJ100001</span>
              <span class="date">2026-04-01</span>
            </li>
            <li class="deal-row">
              <span class="title">CBEX目标无详情成交公告</span>
              <span class="code">G32026BJ100002</span>
              <span class="date">2026-04-13</span>
            </li>
          </ul>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(lambda url: list_html),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.downloaded_this_run, set())

    def test_cbex_deal_prefetched_list_page_candidate_is_rejected(self) -> None:
        list_html = """
        <html><body>
          <ul>
            <li class="deal-row">
              <span class="title">CBEX无详情成交公告</span>
              <span class="code">G32026BJ100002</span>
              <span class="date">2026-04-13</span>
              <strong>CBEX prefetched list-only payload</strong>
            </li>
          </ul>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            collector = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(collector, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(collector, "_fetch_list_html", return_value=list_html),
            ):
                collect_summary = collector.run(start_date="2026-04-13", end_date="2026-04-13", list_only=True)

        self.assertEqual(collect_summary.detail_candidates, 0)
        self.assertEqual(collect_summary.candidate_entries, [])

    def test_cbex_deal_rejects_category_and_search_pages_as_details(self) -> None:
        downloader = CbexDealEquityTransferDownloader(html_root="/tmp/test")

        self.assertFalse(downloader._is_detail_url("https://www.cbex.com.cn/xm/cqzr/"))
        self.assertFalse(downloader._is_detail_url("https://www.cbex.com.cn/xm/cqzr/xm/"))
        self.assertFalse(downloader._is_detail_url("https://www.cbex.com.cn/xm/cqzr/zspl/"))
        self.assertFalse(downloader._is_detail_url("https://www.cbex.com.cn/xm/cqzr/cjjggs/"))
        self.assertTrue(
            downloader._is_detail_url("https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html")
        )

    def test_cbex_deal_saves_original_detail_html_and_sidecar_metadata(self) -> None:
        list_html = """
        <html><body>
          <ul>
            <li>
              <a href="/xm/cqzr/2026/04/13/G32026BJ100001.html">CBEX股权成交公告</a>
              <span>G32026BJ100001 2026-04-13</span>
            </li>
          </ul>
        </body></html>
        """
        detail_html = (
            "<!doctype html><html><head><title>北京产权交易所成交公告</title></head>"
            "<body><main><h1>CBEX原始详情页</h1><p>项目编号：G32026BJ100001</p>"
            "<p>成交日期：2026-04-13</p></main></body></html>"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)

            def fake_fetch(url: str) -> str:
                return list_html if "/cjjggs/" in url else detail_html

            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

            self.assertEqual(summary.saved, 1)
            saved_path = os.path.join(temp_dir, next(iter(summary.downloaded_this_run)))
            saved_html = open(saved_path, encoding="utf-8").read()
            json_path = os.path.splitext(saved_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(saved_html, detail_html)
        self.assertNotIn("CBEX Deal Notice", saved_html)
        self.assertNotIn("deal_detail_html", saved_html)
        self.assertEqual(sidecar["metadata"]["source_id"], "cbex")
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026BJ100001")
        self.assertEqual(sidecar["detail_url"], "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html")

    def test_cbex_deal_callback_failure_is_counted_as_save_failure(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        detail_html = (
            "<html><body><h1>北京成交项目</h1><p>项目编号：G32026BJ100001</p>"
            "<p>成交日期：2026-04-13</p></body></html>"
        )
        candidate = {
            "candidate_id": "G32026BJ100001",
            "project_code": "G32026BJ100001",
            "project_name": "北京成交项目",
            "source_url": page_url,
            "deal_date": "2026-04-13",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "row": {
                "project_code": "G32026BJ100001",
                "project_name": "北京成交项目",
                "source_url": page_url,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                item_saved_callback=callback_boom,
            )
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )
            sidecars = []
            for root, _, files in os.walk(temp_dir):
                sidecars.extend(os.path.join(root, name) for name in files if name.endswith(".json"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(summary.detail_unaccounted, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
        self.assertEqual(sidecars, [])

    def test_cbex_deal_complete_sidecar_failure_does_not_emit_item_saved_callback(self) -> None:
        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        detail_html = (
            "<html><body><h1>北京成交项目</h1><p>项目编号：G32026BJ100001</p>"
            "<p>成交日期：2026-04-13</p></body></html>"
        )
        candidate = {
            "candidate_id": "G32026BJ100001",
            "project_code": "G32026BJ100001",
            "project_name": "北京成交项目",
            "source_url": page_url,
            "deal_date": "2026-04-13",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "row": {
                "project_code": "G32026BJ100001",
                "project_name": "北京成交项目",
                "source_url": page_url,
            },
        }

        def fail_complete_sidecar(*, save_status: str, **_kwargs) -> None:
            if save_status == "complete":
                raise OSError("complete sidecar write failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            callback = Mock()
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                item_saved_callback=callback,
            )
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=fail_complete_sidecar),
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )

        callback.assert_not_called()
        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("complete sidecar write failed", summary.typed_errors[0].raw_reason)

    def test_cbex_deal_prefetched_resume_requires_valid_sidecar_before_skip(self) -> None:
        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        candidate = {
            "candidate_id": "G32026BJ100001",
            "project_code": "G32026BJ100001",
            "project_name": "北京成交项目",
            "source_url": page_url,
            "deal_date": "2026-04-13",
            "row": {
                "project_code": "G32026BJ100001",
                "project_name": "北京成交项目",
                "source_url": page_url,
            },
        }
        detail_html = "<html><body><h1>北京成交项目</h1><p>项目编号：G32026BJ100001</p><p>成交日期：2026-04-13</p></body></html>"

        for sidecar_state in ("missing", "corrupt", "non_object"):
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as temp_dir:
                html_path = os.path.join(temp_dir, "2026年4月", "G32026BJ100001-北京成交项目.html")
                json_path = os.path.splitext(html_path)[0] + ".json"
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>partial previous run</body></html>")
                if sidecar_state == "corrupt":
                    with open(json_path, "w", encoding="utf-8") as handle:
                        handle.write("{bad json")
                elif sidecar_state == "non_object":
                    with open(json_path, "w", encoding="utf-8") as handle:
                        json.dump([], handle)

                downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, resume=True)
                with (
                    patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                    patch.object(
                        downloader,
                        "_fetch_detail_html",
                        return_value=_http_fetched(detail_html, page_url),
                    ) as fetch_detail,
                ):
                    summary = downloader.run(
                        start_date="2026-04-13",
                        end_date="2026-04-13",
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(summary.skipped_by_resume, 0)
                self.assertEqual(summary.saved, 1)
                self.assertEqual(summary.detail_fetched, 1)
                fetch_detail.assert_called_once_with(page_url)
                sidecar = json.load(open(json_path, encoding="utf-8"))
                saved_html = open(html_path, encoding="utf-8").read()
                self.assertEqual(saved_html, detail_html)
                self.assertEqual(sidecar["metadata"]["project_code"], "G32026BJ100001")
                self.assertEqual(sidecar["detail_url"], page_url)

    def test_cbex_deal_detail_date_resume_skips_complete_recomputed_final_artifact(self) -> None:
        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        detail_html = "<html><body><h1>北京成交项目</h1><p>项目编号：G32026BJ100001</p><p>成交日期：2026-04-13</p></body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            final_html_path = os.path.join(temp_dir, "2026年4月", "G32026BJ100001-北京成交项目.html")
            final_json_path = os.path.splitext(final_html_path)[0] + ".json"
            os.makedirs(os.path.dirname(final_html_path), exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "metadata": {
                        "task_id": "cbex:deal:deal_equity_transfer",
                        "source_id": "cbex",
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "project_code": "G32026BJ100001",
                    }
                },
            )

            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                resume=True,
                item_saved_callback=Mock(side_effect=RuntimeError("callback must not run")),
            )
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ) as fetch_detail,
                patch.object(downloader, "_save_snapshot_html") as save_snapshot,
                patch.object(downloader, "_write_sidecar_json") as write_sidecar,
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[
                        {
                            "candidate_id": "G32026BJ100001",
                            "project_code": "G32026BJ100001",
                            "project_name": "北京成交项目",
                            "source_url": page_url,
                            "collection_date": "2026-05-20",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {
                                "project_code": "G32026BJ100001",
                                "project_name": "北京成交项目",
                                "source_url": page_url,
                            },
                        }
                    ],
                )

            sidecar = json.load(open(final_json_path, encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.detail_unaccounted, 0)
        fetch_detail.assert_called_once_with(page_url)
        save_snapshot.assert_not_called()
        write_sidecar.assert_not_called()
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026BJ100001")

    def test_cbex_deal_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes((67, 66, 69, 88, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101)).decode("ascii")
        rendered_html = f"<html><body>{shell_marker}</body></html>"
        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        candidate = {
            "candidate_id": "G32026BJ100001",
            "project_code": "G32026BJ100001",
            "project_name": "北京成交项目",
            "source_url": page_url,
            "deal_date": "2026-04-13",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "row": {
                "project_code": "G32026BJ100001",
                "project_name": "北京成交项目",
                "source_url": page_url,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(html_root=temp_dir, max_pages=1)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(rendered_html, page_url),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )

            evidence_paths = []
            html_paths = []
            json_paths = []
            for root, _, files in os.walk(temp_dir):
                html_paths.extend(
                    os.path.join(root, name) for name in files if name.endswith(".html")
                )
                json_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".json") and not name.endswith(".peap-evidence.json")
                )
                evidence_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".peap-evidence.json")
                )
            evidence = json.load(open(evidence_paths[0], encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(html_paths, [])
        self.assertEqual(json_paths, [])
        self.assertEqual(len(evidence_paths), 1)
        self.assertTrue(summary.typed_errors)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256("G32026BJ100001".encode("utf-8")).hexdigest(),
        )
        self.assertIn("project_name_hash", evidence["identity_hints"])
        self.assertNotIn(page_url, json.dumps(evidence, ensure_ascii=False))

    def test_cbex_valid_retry_clears_previous_invalid_shell_evidence(self) -> None:
        shell_marker = bytes(
            (67, 66, 69, 88, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101)
        ).decode("ascii")
        page_url = "https://www.cbex.com.cn/xm/cqzr/2026/04/13/G32026BJ100001.html"
        candidate = {
            "candidate_id": "G32026BJ100001",
            "project_code": "G32026BJ100001",
            "project_name": "北京成交项目",
            "source_url": page_url,
            "deal_date": "2026-04-13",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "row": {"project_code": "G32026BJ100001", "project_name": "北京成交项目"},
        }
        valid_html = "<html><body>成交公告 G32026BJ100001 北京成交项目 2026-04-13</body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            first = CbexDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(first, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(first, "_fetch_detail_html", return_value=_http_fetched(shell_marker, page_url)),
            ):
                first_summary = first.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )
            self.assertEqual(first_summary.saved, 0)
            first_evidence_paths = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".peap-evidence.json")
            ]
            self.assertEqual(len(first_evidence_paths), 1)

            second = CbexDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(second, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(second, "_fetch_detail_html", return_value=_http_fetched(valid_html, page_url)),
            ):
                second_summary = second.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )

            evidence_paths = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".peap-evidence.json")
            ]

        self.assertEqual(second_summary.saved, 1)
        self.assertEqual(evidence_paths, [])

    def test_cquae_deal_run_uses_browser_fetcher_for_list_and_detail_pages(self) -> None:
        list_html = """
        <html><body>
          <table>
            <tr>
              <td>
                <a href="/CquaeNews/cjgs/Show.cshtml?id=53442">CQUAE股权成交公告 G32026CQ100001</a>
              </td>
              <td>2026-04-13</td>
            </tr>
          </table>
        </body></html>
        """
        detail_html = "<html><body><h1>CQUAE股权成交公告</h1><p>成交日期：2026年4月13日</p></body></html>"
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if "List.cshtml" in url:
                return list_html
            return detail_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_fetched, 1)
        self.assertEqual(len(summary.typed_errors), 0)
        self.assertTrue(any("List.cshtml" in url for url in fetched_urls))
        self.assertTrue(any("Show.cshtml?id=53442" in url for url in fetched_urls))

    def test_cquae_deal_rejects_list_page_rows_without_detail_link(self) -> None:
        list_html = """
        <html><body>
          <table>
            <tr>
              <td>
                <a href="/CquaeNews/cjgs/Show.cshtml?id=53442">CQUAE有详情成交公告 G32026CQ100001</a>
              </td>
              <td>2026-04-13</td>
            </tr>
            <tr>
              <td>CQUAE无详情成交公告 G32026CQ100002</td>
              <td>2026-04-13</td>
              <td><strong>CQUAE list-only payload</strong></td>
            </tr>
          </table>
        </body></html>
        """
        detail_html = "<html><body><strong>CQUAE detail payload</strong><p>成交日期：2026-04-13</p></body></html>"
        fetched_urls: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched_urls.append(url)
            if "List.cshtml" in url:
                return list_html
            return detail_html

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

            saved_html = "\n".join(
                open(os.path.join(temp_dir, relpath), encoding="utf-8").read()
                for relpath in summary.downloaded_this_run
            )

        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_fetched, 1)
        self.assertEqual(len(summary.typed_errors), 0)
        self.assertEqual(sum(1 for url in fetched_urls if "List.cshtml" in url), 1)
        self.assertEqual(sum(1 for url in fetched_urls if "Show.cshtml?id=53442" in url), 1)
        self.assertIn("CQUAE detail payload", saved_html)
        self.assertNotIn("CQUAE list-only payload", saved_html)

    def test_cquae_deal_does_not_materialize_list_page_candidate_from_code_and_date_only(self) -> None:
        list_html = """
        <html><body>
          <table>
            <tr>
              <td>CQUAE其他无详情成交公告 G32026CQ100001</td>
              <td>2026-04-01</td>
            </tr>
            <tr>
              <td>CQUAE目标无详情成交公告 G32026CQ100002</td>
              <td>2026-04-13</td>
            </tr>
          </table>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(lambda url: list_html),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.downloaded_this_run, set())

    def test_cquae_deal_prefetched_list_page_candidate_is_rejected(self) -> None:
        list_html = """
        <html><body>
          <table>
            <tr>
              <td>CQUAE无详情成交公告 G32026CQ100002</td>
              <td>2026-04-13</td>
              <td><strong>CQUAE prefetched list-only payload</strong></td>
            </tr>
          </table>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            collector = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(collector, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(collector, "_fetch_list_html", return_value=list_html),
            ):
                collect_summary = collector.run(start_date="2026-04-13", end_date="2026-04-13", list_only=True)

        self.assertEqual(collect_summary.detail_candidates, 0)
        self.assertEqual(collect_summary.candidate_entries, [])

    def test_cquae_deal_rejects_listing_routes_as_details(self) -> None:
        downloader = ChongqingDealEquityTransferDownloader(html_root="/tmp/test")

        self.assertFalse(downloader._is_whitelisted_detail_url("https://www.cquae.com/CquaeNews/cjgs/"))
        self.assertFalse(downloader._is_whitelisted_detail_url("https://www.cquae.com/CquaeNews/cjgs/List.cshtml"))
        self.assertFalse(
            downloader._is_whitelisted_detail_url("https://www.cquae.com/CquaeNews/cjgs/List.cshtml?type=1")
        )
        self.assertTrue(
            downloader._is_whitelisted_detail_url("https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442")
        )

    def test_cquae_deal_saves_original_detail_html_and_sidecar_metadata(self) -> None:
        list_html = """
        <html><body>
          <table>
            <tr>
              <td>
                <a href="/CquaeNews/cjgs/Show.cshtml?id=53442">CQUAE股权成交公告 G32026CQ100001</a>
              </td>
              <td>2026-04-13</td>
            </tr>
          </table>
        </body></html>
        """
        detail_html = (
            "<!doctype html><html><head><title>重庆产权交易网成交公告</title></head>"
            "<body><main><h1>CQUAE原始详情页</h1><p>项目编号：G32026CQ100001</p>"
            "<p>成交日期：2026年4月13日</p></main></body></html>"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)

            def fake_fetch(url: str) -> str:
                return list_html if "List.cshtml" in url else detail_html

            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_open_browser_fetcher",
                    return_value=self._FetcherContext(fake_fetch),
                    create=True,
                ),
                patch("urllib.request.urlopen", side_effect=AssertionError("urllib must not be used")),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=False)

            self.assertEqual(summary.saved, 1)
            saved_path = os.path.join(temp_dir, next(iter(summary.downloaded_this_run)))
            saved_html = open(saved_path, encoding="utf-8").read()
            json_path = os.path.splitext(saved_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(saved_html, detail_html)
        self.assertNotIn("CQUAE Deal Notice", saved_html)
        self.assertNotIn("deal_detail_html", saved_html)
        self.assertEqual(sidecar["metadata"]["source_id"], "cquae")
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026CQ100001")
        self.assertEqual(sidecar["detail_url"], "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442")

    def test_cquae_deal_resume_requires_sidecar_for_prefetched_candidate(self) -> None:
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442"
        candidate = {
            "candidate_id": "G32026CQ100001",
            "project_code": "G32026CQ100001",
            "project_name": "重庆成交项目",
            "source_url": page_url,
            "collection_date": "2026-04-20",
            "deal_date": "2026-04-13",
            "deal_date_basis": "deal_date",
            "deal_date_is_imputed": False,
            "row": {
                "project_code": "G32026CQ100001",
                "project_name": "重庆成交项目",
                "source_url": page_url,
            },
        }
        detail_html = "<html><body><h1>重庆成交项目</h1><p>成交日期：2026-04-13</p></body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "2026年4月", "G32026CQ100001-重庆成交项目.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>partial previous run</body></html>")

            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir, resume=True)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ) as fetch_detail,
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[candidate],
                )

            json_path = os.path.splitext(html_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))
            saved_html = open(html_path, encoding="utf-8").read()

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_fetched, 1)
        fetch_detail.assert_called_once_with(page_url)
        self.assertEqual(saved_html, detail_html)
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026CQ100001")
        self.assertEqual(sidecar["detail_url"], page_url)

    def test_cquae_deal_detail_date_resume_skips_complete_recomputed_final_artifact(self) -> None:
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442"
        detail_html = "<html><body><h1>重庆成交项目</h1><p>成交日期：2026-04-13</p></body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            final_html_path = os.path.join(temp_dir, "2026年4月", "G32026CQ100001-重庆成交项目.html")
            final_json_path = os.path.splitext(final_html_path)[0] + ".json"
            os.makedirs(os.path.dirname(final_html_path), exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "metadata": {
                        "task_id": "cquae:deal:deal_equity_transfer",
                        "source_id": "cquae",
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "project_code": "G32026CQ100001",
                    }
                },
            )

            downloader = ChongqingDealEquityTransferDownloader(
                html_root=temp_dir,
                resume=True,
                item_saved_callback=Mock(side_effect=RuntimeError("callback must not run")),
            )
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ) as fetch_detail,
                patch.object(downloader, "_save_snapshot_html") as save_snapshot,
                patch.object(downloader, "_write_sidecar_json") as write_sidecar,
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-13",
                    prefetched_candidates=[
                        {
                            "candidate_id": "G32026CQ100001",
                            "project_code": "G32026CQ100001",
                            "project_name": "重庆成交项目",
                            "source_url": page_url,
                            "collection_date": "2026-05-20",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {
                                "project_code": "G32026CQ100001",
                                "project_name": "重庆成交项目",
                                "source_url": page_url,
                            },
                        }
                    ],
                )

            sidecar = json.load(open(final_json_path, encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.detail_unaccounted, 0)
        fetch_detail.assert_called_once_with(page_url)
        save_snapshot.assert_not_called()
        write_sidecar.assert_not_called()
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026CQ100001")

    def test_cquae_deal_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes((67, 81, 85, 65, 69, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101)).decode(
            "ascii"
        )
        detail_html = f"<html><body>{shell_marker}</body></html>"
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442"
        candidate = {
            "candidate_id": "G32026CQ100001",
            "project_code": "G32026CQ100001",
            "project_name": "重庆成交项目",
            "source_url": page_url,
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "project_code": "G32026CQ100001",
                "project_name": "重庆成交项目",
                "source_url": page_url,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_http_fetched(detail_html, page_url),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-13",
                    end_date="2026-04-20",
                    prefetched_candidates=[candidate],
                )

            evidence_paths = []
            html_paths = []
            json_paths = []
            for root, _, files in os.walk(temp_dir):
                html_paths.extend(
                    os.path.join(root, name) for name in files if name.endswith(".html")
                )
                json_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".json") and not name.endswith(".peap-evidence.json")
                )
                evidence_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".peap-evidence.json")
                )
            evidence = json.load(open(evidence_paths[0], encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(html_paths, [])
        self.assertEqual(json_paths, [])
        self.assertEqual(len(evidence_paths), 1)
        self.assertTrue(summary.typed_errors)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(detail_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256("G32026CQ100001".encode("utf-8")).hexdigest(),
        )
        self.assertIn("project_name_hash", evidence["identity_hints"])
        self.assertNotIn(page_url, json.dumps(evidence, ensure_ascii=False))

    def test_cquae_deal_does_not_count_parent_div_as_list_page_duplicate(self) -> None:
        list_html = """
        <html><body>
          <div class="news-list">
            <table>
              <tr>
                <td>
                  <a href="/CquaeNews/cjgs/Show.cshtml?id=53442">CQUAE成交公告 G32026CQ100001</a>
                </td>
                <td>2026-04-13</td>
              </tr>
              <tr>
                <td>
                  <a href="/CquaeNews/cjgs/Show.cshtml?id=53443">CQUAE成交公告 G32026CQ100002</a>
                </td>
                <td>2026-04-13</td>
              </tr>
            </table>
          </div>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_fetch_list_html", return_value=list_html),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=True)

        self.assertEqual(summary.listed_items, 2)
        self.assertEqual(summary.detail_candidates, 2)
        self.assertEqual(summary.skipped_by_duplicate, 0)
        self.assertEqual(
            [entry.get("source_url") for entry in summary.candidate_entries],
            [
                "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53442",
                "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=53443",
            ],
        )

    def test_tpre_deal_saves_rendered_detail_page_and_sidecar_payload(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：G32026TJ1000001</p>"
            "<p>项目名称：天津成交项目</p><p>成交日期：2026-03-31</p></main></body></html>"
        )
        detail_payload = {
            "data": {
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "contractSignTime": "2026-03-31",
                "transactionPrice": "1688",
            }
        }
        candidate = {
            "notice_id": "TPRE-ORIGINAL-001",
            "project_code": "G32026TJ1000001",
            "project_name": "天津成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-ORIGINAL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-ORIGINAL-001",
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-ORIGINAL-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    create=True,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 1)
            saved_path = os.path.join(temp_dir, next(iter(summary.downloaded_this_run)))
            saved_html = open(saved_path, encoding="utf-8").read()
            json_path = os.path.splitext(saved_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(saved_html, rendered_html)
        self.assertNotIn("TPRE Deal Notice", saved_html)
        self.assertNotIn("deal_detail", saved_html)
        self.assertEqual(sidecar["metadata"]["source_id"], "tpre")
        self.assertEqual(sidecar["metadata"]["source_url"], candidate["source_url"])
        self.assertEqual(sidecar["detail_payload"], detail_payload)

    def test_tpre_deal_callback_failure_is_counted_as_save_failure(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        rendered_html = (
            "<html><body><h1>成交公告</h1><p>项目编号：G32026TJ1000001</p>"
            "<p>项目名称：天津成交项目</p><p>成交日期：2026-03-31</p></body></html>"
        )
        detail_payload = {
            "data": {
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "contractSignTime": "2026-03-31",
            }
        }
        candidate = {
            "notice_id": "TPRE-CALLBACK-001",
            "project_code": "G32026TJ1000001",
            "project_name": "天津成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-CALLBACK-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {"id": "TPRE-CALLBACK-001", "projectCode": "G32026TJ1000001"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(
                html_root=temp_dir,
                item_saved_callback=callback_boom,
            )
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )
            sidecars = []
            for root, _, files in os.walk(temp_dir):
                sidecars.extend(os.path.join(root, name) for name in files if name.endswith(".json"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(summary.detail_unaccounted, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
        self.assertEqual(sidecars, [])

    def test_tpre_deal_prefetched_resume_requires_sidecar_before_skip(self) -> None:
        rendered_html = (
            "<html><body><h1>成交公告</h1><p>项目编号：G32026TJ1000001</p>"
            "<p>成交日期：2026-03-31</p></body></html>"
        )
        detail_payload = {
            "data": {
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "contractSignTime": "2026-03-31",
            }
        }
        candidate = {
            "notice_id": "TPRE-RESUME-001",
            "project_code": "G32026TJ1000001",
            "project_name": "天津成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-RESUME-001"
            ),
            "deal_date": "2026-03-31",
            "row": {"id": "TPRE-RESUME-001", "projectCode": "G32026TJ1000001"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "2026年3月", "G32026TJ1000001-天津成交项目.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>old html only</body></html>")

            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir, resume=True)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload) as query_detail,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                ) as fetch_rendered,
            ):
                summary = downloader.run(
                    start_date="2026-03-31",
                    end_date="2026-03-31",
                    prefetched_candidates=[candidate],
                )

            json_path = os.path.splitext(html_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(summary.saved, 1)
        query_detail.assert_called_once()
        fetch_rendered.assert_called_once()
        self.assertEqual(sidecar["metadata"]["project_code"], "G32026TJ1000001")

    def test_tpre_deal_detail_date_resume_skips_complete_recomputed_final_artifact(self) -> None:
        detail_payload = {
            "data": {
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "contractSignTime": "2026-03-31",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            final_html_path = os.path.join(temp_dir, "2026年3月", "G32026TJ1000001-天津成交项目.html")
            os.makedirs(os.path.dirname(final_html_path), exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "metadata": {
                        "task_id": "tpre:deal:deal_equity_transfer",
                        "source_id": "tpre",
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "project_code": "G32026TJ1000001",
                    }
                },
            )

            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir, resume=True)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload) as query_detail,
                patch.object(downloader, "_fetch_rendered_detail_html") as fetch_rendered,
                patch.object(downloader, "_save_snapshot_html") as save_snapshot,
                patch.object(downloader, "_write_sidecar_json") as write_sidecar,
            ):
                summary = downloader.run(
                    start_date="2026-03-31",
                    end_date="2026-03-31",
                    prefetched_candidates=[
                        {
                            "notice_id": "TPRE-RESUME-002",
                            "project_code": "G32026TJ1000001",
                            "project_name": "天津成交项目",
                            "source_url": (
                                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                                "?id=TPRE-RESUME-002"
                            ),
                            "collection_date": "2026-04-20",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {"id": "TPRE-RESUME-002", "projectCode": "G32026TJ1000001"},
                        }
                    ],
                )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.list_unaccounted, 0)
        self.assertEqual(summary.detail_unaccounted, 0)
        query_detail.assert_called_once()
        fetch_rendered.assert_not_called()
        save_snapshot.assert_not_called()
        write_sidecar.assert_not_called()

    def test_tpre_deal_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes((84, 80, 82, 69, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101)).decode("ascii")
        rendered_html = f"<html><body>{shell_marker}</body></html>"
        detail_payload = {
            "data": {
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "contractSignTime": "2026-03-31",
            }
        }
        page_url = (
            "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
            "?id=TPRE-ORIGINAL-001"
        )
        candidate = {
            "notice_id": "TPRE-ORIGINAL-001",
            "project_code": "G32026TJ1000001",
            "project_name": "天津成交项目",
            "source_url": page_url,
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-ORIGINAL-001",
                "projectCode": "G32026TJ1000001",
                "projectName": "天津成交项目",
                "projectLink": page_url,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, page_url),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            evidence_paths = []
            html_paths = []
            json_paths = []
            for root, _, files in os.walk(temp_dir):
                html_paths.extend(
                    os.path.join(root, name) for name in files if name.endswith(".html")
                )
                json_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".json") and not name.endswith(".peap-evidence.json")
                )
                evidence_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".peap-evidence.json")
                )
            evidence = json.load(open(evidence_paths[0], encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(html_paths, [])
        self.assertEqual(json_paths, [])
        self.assertEqual(len(evidence_paths), 1)
        self.assertTrue(summary.typed_errors)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256("G32026TJ1000001".encode("utf-8")).hexdigest(),
        )
        self.assertIn("project_name_hash", evidence["identity_hints"])
        self.assertNotIn(page_url, json.dumps(evidence, ensure_ascii=False))

    def test_tpre_capital_builds_renderable_page_url_from_notice_id_when_row_lacks_project_link(self) -> None:
        detail_payload = {"data": {"projectName": "天津增资成交项目"}}
        candidate = {
            "notice_id": "TPRE-CAPITAL-001",
            "project_code": "G62026TJ1000001",
            "project_name": "天津增资成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction/biz/increase/transaction/transferee/anmuas/"
                "result-notice/details?id=TPRE-CAPITAL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-CAPITAL-001",
                "projectCode": "G62026TJ1000001",
                "projectName": "天津增资成交项目",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(downloader, "_merge_capital_transferee_details", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(
                        "<html><body>成交公告 G62026TJ1000001 天津增资成交项目</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=TPRE-CAPITAL-001",
                    ),
                ) as fetch_rendered,
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(
            fetch_rendered.call_args.kwargs["page_url"],
            "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=TPRE-CAPITAL-001",
        )

    def test_tpre_capital_rejects_non_mapping_detail_payload_before_snapshot_write(self) -> None:
        candidate = {
            "notice_id": "TPRE-CAPITAL-BAD-PAYLOAD-001",
            "project_code": "G62026TJ1000999",
            "project_name": "天津增资成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-CAPITAL-BAD-PAYLOAD-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-CAPITAL-BAD-PAYLOAD-001",
                "projectCode": "G62026TJ1000999",
                "projectName": "天津增资成交项目",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-CAPITAL-BAD-PAYLOAD-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=[]),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    side_effect=AssertionError("snapshot must not be fetched for invalid detail_payload"),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "validation")
        self.assertIn("detail_payload", summary.typed_errors[0].raw_reason)
        self.assertIn("non-mapping", summary.typed_errors[0].raw_reason)

    def test_tpre_capital_merge_preserves_falsy_mapping_detail_payload(self) -> None:
        class FalsyDetailPayload(dict):
            def __bool__(self) -> bool:
                return False

        detail_payload = FalsyDetailPayload(
            {
                "data": {
                    "projectCode": "Z32026TJ0101",
                    "projectName": "保留 falsy mapping 增资成交项目",
                }
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            with patch.object(
                downloader,
                "_collect_capital_transferee_details",
                return_value=[{"investorName": "天津测试受让方"}],
            ) as collect_details:
                payload = downloader._merge_capital_transferee_details(
                    detail_payload=detail_payload,
                    project_code="Z32026TJ9999",
                    notice_id="TPRE-CAPITAL-FALSY-MAPPING-001",
                )

        self.assertEqual(payload["data"], detail_payload["data"])
        self.assertEqual(payload["transferee_details_project_code"], "Z32026TJ0101")
        self.assertEqual(payload["transferee_details"], [{"investorName": "天津测试受让方"}])
        collect_details.assert_called_once_with(project_code="Z32026TJ0101")

    def test_tpre_capital_merge_rejects_non_mapping_detail_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            with self.assertRaisesRegex(TypeError, "detail_payload"):
                downloader._merge_capital_transferee_details(
                    detail_payload=[],
                    project_code="Z32026TJ0101",
                    notice_id="TPRE-CAPITAL-NON-MAPPING-001",
                )

    def test_tpre_capital_bad_transferee_pagination_payload_marks_detail_failed(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：Z32026TJ0201</p>"
            "<p>项目名称：增资投资方明细坏分页</p></main></body></html>"
        )
        bad_payloads = {
            "non_dict_payload": [],
            "non_dict_record": {"data": {"records": ["bad"], "total": 1}},
            "invalid_total": {"data": {"records": [{"investor": "A"}], "total": "bad"}},
            "api_error": {"code": 500, "message": "failed"},
        }

        for case_name, bad_payload in bad_payloads.items():
            with self.subTest(case_name=case_name), tempfile.TemporaryDirectory() as temp_dir:
                candidate = {
                    "notice_id": f"TPRE-CAPITAL-BAD-PAGE-{case_name}",
                    "project_code": "Z32026TJ0201",
                    "project_name": "增资投资方明细坏分页",
                    "source_url": (
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                        f"?id=TPRE-CAPITAL-BAD-PAGE-{case_name}"
                    ),
                    "collection_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                    "row": {
                        "id": f"TPRE-CAPITAL-BAD-PAGE-{case_name}",
                        "projectCode": "Z32026TJ0201",
                        "projectName": "增资投资方明细坏分页",
                    },
                }
                downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir, page_size=2)
                downloader._notify_item_saved = Mock()
                with (
                    patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                    patch.object(
                        downloader,
                        "_query_capital_transferee_details_page",
                        return_value=bad_payload,
                    ),
                    patch.object(
                        downloader,
                        "_fetch_rendered_detail_html",
                        return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    ),
                ):
                    summary = downloader.run(
                        start_date="2026-03-01",
                        end_date="2026-04-30",
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(summary.saved, 0)
                self.assertEqual(summary.detail_failed, 1)
                self.assertEqual(summary.downloaded_this_run, set())
                self.assertEqual(summary.detail_unaccounted, 0)
                downloader._notify_item_saved.assert_not_called()
                self.assertEqual(len(summary.typed_errors), 1)
                self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
                self.assertIn("capital-transferee-merge-failed", summary.typed_errors[0].raw_reason)
                self.assertIn("transferee-pagination", summary.typed_errors[0].raw_reason)
                json_files = [
                    os.path.join(root, name)
                    for root, _, files in os.walk(temp_dir)
                    for name in files
                    if name.endswith(".json")
                ]
                self.assertEqual(len(json_files), 1)
                sidecar = json.load(open(json_files[0], encoding="utf-8"))
                self.assertEqual(sidecar["save_status"], "failed")
                self.assertNotEqual(sidecar["save_status"], "complete")
                self.assertIn("capital-transferee-merge-failed", sidecar["detail_payload_error"])
                self.assertIn("transferee-pagination", sidecar["detail_payload_error"])

    def test_tpre_deal_marks_failed_when_detail_api_is_unavailable_but_rendered_page_succeeds(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：G32026TJ1000099</p>"
            "<p>项目名称：API失败但前端页可用</p></main></body></html>"
        )
        candidate = {
            "notice_id": "TPRE-API-FAIL-001",
            "project_code": "G32026TJ1000099",
            "project_name": "API失败但前端页可用",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-API-FAIL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-API-FAIL-001",
                "projectCode": "G32026TJ1000099",
                "projectName": "API失败但前端页可用",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-API-FAIL-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            downloader._notify_item_saved = Mock()
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", side_effect=RuntimeError("detail-api-503")),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    create=True,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            json_files = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)
            json_path = json_files[0]
            sidecar = json.load(open(json_path, encoding="utf-8"))
            downloader._notify_item_saved.assert_not_called()

        self.assertEqual(sidecar["detail_payload"], {})
        self.assertIn("detail-api-503", sidecar["detail_payload_error"])
        self.assertEqual(sidecar["detail_url"], candidate["source_url"])
        self.assertEqual(sidecar["save_status"], "failed")
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
        self.assertIn("detail-payload-fetch-failed", summary.typed_errors[0].raw_reason)
        self.assertIn("detail-api-503", summary.typed_errors[0].raw_reason)

    def test_tpre_deal_detail_payload_error_marks_failed_without_saved_count(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：G32026TJ1000100</p>"
            "<p>项目名称：详情payload失败项目</p></main></body></html>"
        )
        candidate = {
            "notice_id": "TPRE-PAYLOAD-FAIL-001",
            "project_code": "G32026TJ1000100",
            "project_name": "详情payload失败项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-PAYLOAD-FAIL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-PAYLOAD-FAIL-001",
                "projectCode": "G32026TJ1000100",
                "projectName": "详情payload失败项目",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            downloader._notify_item_saved = Mock()
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", side_effect=RuntimeError("detail-api-503")),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(summary.detail_unaccounted, 0)
        downloader._notify_item_saved.assert_not_called()

    def test_tpre_deal_rejects_candidate_when_rendered_page_is_not_target_detail(self) -> None:
        project_name = "海城锐海<script>alert(1)</script>&风力发电有限公司100%股权"
        candidate = {
            "notice_id": "G32025TJ1000087",
            "project_code": "G32025TJ1000087",
            "project_name": project_name,
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=G32025TJ1000087"
            ),
            "collection_date": "2026-05-10",
            "deal_date": "2026-02-27",
            "deal_date_basis": "contractSignTime",
            "deal_date_is_imputed": False,
            "row": {
                "projectCode": "G32025TJ1000087",
                "projectName": project_name,
                "assessmentValue": 1410.0,
                "transferBasePrice": 1410.0,
                "transactionPrice": 1410.0,
                "contractSignTime": "2026-02-27",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
            downloader._notify_item_saved = Mock()
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 5, 10)),
                patch.object(downloader, "_query_detail_payload") as query_detail,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    side_effect=RuntimeError("tpre-deal-page-not-ready"),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-05-10",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            query_detail.assert_not_called()
            downloader._notify_item_saved.assert_not_called()
            archived_files = [
                os.path.relpath(os.path.join(root, name), temp_dir)
                for root, _, files in os.walk(temp_dir)
                for name in files
            ]

        self.assertEqual(archived_files, [])
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
        self.assertIn("rendered-page-fetch-failed", summary.typed_errors[0].raw_reason)
        self.assertIn("tpre-deal-page-not-ready", summary.typed_errors[0].raw_reason)

    def test_tpre_capital_rejects_candidate_when_only_synthetic_snapshot_would_remain(self) -> None:
        candidate = {
            "notice_id": "CAP-NO-DETAIL-API-SYNTH-1",
            "project_code": "Z32026TJ0999",
            "project_name": "无详情接口增资成交",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=CAP-NO-DETAIL-API-SYNTH-1"
            ),
            "collection_date": "2026-04-20",
            "deal_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "CAP-NO-DETAIL-API-SYNTH-1",
                "projectCode": "Z32026TJ0999",
                "projectName": "无详情接口增资成交",
                "transactionPrice": 3200.5,
                "contractSignTime": "2026-04-20",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/"
                    "transaction-announcement?id=CAP-NO-DETAIL-API-SYNTH-1"
                ),
            },
        }
        transferee_payload = {
            "transferee_details_project_code": "Z32026TJ0999",
            "transferee_details": [{"investor": "ONLY"}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            downloader._notify_item_saved = Mock()
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_merge_capital_transferee_details", return_value=transferee_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    side_effect=RuntimeError("tpre-capital-page-not-ready"),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            downloader._notify_item_saved.assert_not_called()
            archived_files = [
                os.path.relpath(os.path.join(root, name), temp_dir)
                for root, _, files in os.walk(temp_dir)
                for name in files
            ]

        self.assertEqual(archived_files, [])
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
        self.assertIn("rendered-page-fetch-failed", summary.typed_errors[0].raw_reason)
        self.assertIn("tpre-capital-page-not-ready", summary.typed_errors[0].raw_reason)

    def test_tpre_deal_source_files_do_not_reference_synthetic_snapshot_success_markers(self) -> None:
        forbidden_snapshot_title = "TPRE Deal " + "Synthetic Snapshot"
        forbidden_snapshot_source = "list_row_" + "fallback"
        sources = [
            os.path.join(os.path.dirname(__file__), "..", "peap", "downloaders", "deal_tpre.py"),
            __file__,
        ]

        for path in sources:
            with self.subTest(path=path):
                contents = open(path, encoding="utf-8").read()
                self.assertNotIn(forbidden_snapshot_title, contents)
                self.assertNotIn(forbidden_snapshot_source, contents)

    def test_tpre_capital_without_detail_api_marks_merge_failure_without_saved_count(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：Z32026TJ0011</p>"
            "<p>项目名称：无详情接口增资合并失败</p></main></body></html>"
        )
        candidate = {
            "notice_id": "TPRE-CAPITAL-NO-DETAIL-MERGE-FAIL-001",
            "project_code": "Z32026TJ0011",
            "project_name": "无详情接口增资合并失败",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-CAPITAL-NO-DETAIL-MERGE-FAIL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-CAPITAL-NO-DETAIL-MERGE-FAIL-001",
                "projectCode": "Z32026TJ0011",
                "projectName": "无详情接口增资合并失败",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-CAPITAL-NO-DETAIL-MERGE-FAIL-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload") as query_detail,
                patch.object(
                    downloader,
                    "_merge_capital_transferee_details",
                    side_effect=RuntimeError("transferee-merge-502"),
                ) as merge_transferees,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    create=True,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            query_detail.assert_not_called()
            merge_transferees.assert_called_once()
            json_files = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)
            json_path = json_files[0]
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(sidecar["detail_payload"], {})
        self.assertIn("capital-transferee-merge-failed", sidecar["detail_payload_error"])
        self.assertNotIn("detail-payload-fetch-failed", sidecar["detail_payload_error"])
        self.assertEqual(sidecar["save_status"], "failed")
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
        self.assertIn("capital-transferee-merge-failed", summary.typed_errors[0].raw_reason)
        self.assertNotIn("detail-payload-fetch-failed", summary.typed_errors[0].raw_reason)

    def test_tpre_capital_marks_failed_when_transferee_merge_fails(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：Z32026TJ0009</p>"
            "<p>项目名称：增资成交项目</p></main></body></html>"
        )
        detail_payload = {"data": {"projectCode": "Z32026TJ0009", "projectName": "增资成交项目"}}
        candidate = {
            "notice_id": "TPRE-CAPITAL-MERGE-FAIL-001",
            "project_code": "Z32026TJ0009",
            "project_name": "增资成交项目",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-CAPITAL-MERGE-FAIL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-CAPITAL-MERGE-FAIL-001",
                "projectCode": "Z32026TJ0009",
                "projectName": "增资成交项目",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-CAPITAL-MERGE-FAIL-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            downloader._notify_item_saved = Mock()
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_merge_capital_transferee_details",
                    side_effect=RuntimeError("transferee-merge-502"),
                ),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    create=True,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            json_files = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)
            json_path = json_files[0]
            sidecar = json.load(open(json_path, encoding="utf-8"))
            downloader._notify_item_saved.assert_not_called()

        self.assertEqual(sidecar["detail_payload"], detail_payload)
        self.assertIn("transferee-merge-502", sidecar["detail_payload_error"])
        self.assertEqual(sidecar["save_status"], "failed")
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "execute")
        self.assertIn("capital-transferee-merge-failed", summary.typed_errors[0].raw_reason)
        self.assertIn("transferee-merge-502", summary.typed_errors[0].raw_reason)

    def test_tpre_capital_combines_detail_and_merge_failures_for_single_failed_candidate(self) -> None:
        rendered_html = (
            "<!doctype html><html><head><title>天津产权交易中心成交公告</title></head>"
            "<body><main><h1>成交公告</h1><p>项目编号：Z32026TJ0010</p>"
            "<p>项目名称：增资成交项目双重失败</p></main></body></html>"
        )
        candidate = {
            "notice_id": "TPRE-CAPITAL-DOUBLE-FAIL-001",
            "project_code": "Z32026TJ0010",
            "project_name": "增资成交项目双重失败",
            "source_url": (
                "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                "?id=TPRE-CAPITAL-DOUBLE-FAIL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "TPRE-CAPITAL-DOUBLE-FAIL-001",
                "projectCode": "Z32026TJ0010",
                "projectName": "增资成交项目双重失败",
                "projectLink": (
                    "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
                    "?id=TPRE-CAPITAL-DOUBLE-FAIL-001"
                ),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=temp_dir)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", side_effect=RuntimeError("detail-api-503")),
                patch.object(
                    downloader,
                    "_merge_capital_transferee_details",
                    side_effect=RuntimeError("transferee-merge-502"),
                ) as merge_transferees,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, str(candidate["source_url"])),
                    create=True,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(summary.detail_fetched, 0)
            self.assertEqual(summary.downloaded_this_run, set())
            merge_transferees.assert_called_once()
            json_files = [
                os.path.join(root, name)
                for root, _, files in os.walk(temp_dir)
                for name in files
                if name.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)
            json_path = json_files[0]
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertIn("detail-api-503", sidecar["detail_payload_error"])
        self.assertIn("transferee-merge-502", sidecar["detail_payload_error"])
        self.assertEqual(sidecar["save_status"], "failed")
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertIn("detail-payload-fetch-failed", summary.typed_errors[0].raw_reason)
        self.assertIn("capital-transferee-merge-failed", summary.typed_errors[0].raw_reason)


class SseDownloaderFixTest(unittest.TestCase):
    def test_run_uses_submission_root_directly_without_type_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=temp_dir,
                output_type="实物资产",
            )
            captured: dict[str, str] = {}

            def fake_collect(**kwargs):
                captured["output_dir"] = kwargs["output_dir"]

            with patch.object(downloader, "_collect_list_candidates", side_effect=fake_collect):
                downloader.run(start_date="2026-03-10", end_date="2026-03-10", list_only=True)

            self.assertEqual(captured["output_dir"], temp_dir)

    def test_sse_query_list_page_uses_current_realright_endpoint(self):
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        captured = {}

        def fake_post_json(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return {"code": 200, "data": [], "extra": 0}

        downloader._post_json = fake_post_json
        downloader._query_list_page(page_index=2, list_project_type="ZICHANZHUANRANG", gplx="2")

        assert captured["url"] == "https://www.suaee.com/si/prjs/realright/list"
        assert captured["payload"]["pageNo"] == 2
        assert captured["payload"]["pageSize"] == downloader.page_size

    def test_sse_collect_list_candidates_accepts_current_uppercase_row_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiPhysicalAssetDownloader(html_root=tmp_dir)
            summary = DownloadSummary()
            candidates = []

            response_payload = {
                "code": 200,
                "data": [
                    {
                        "XMID": 113868,
                        "XMBH": "GR2026SH1000510",
                        "XMMC": "demo",
                        "PLKSRQ": 20260403,
                    }
                ],
                "extra": 1,
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            downloader._query_list_page = lambda **_: HttpFetchedText(
                response_bytes.decode("utf-8"),
                source_url="https://www.suaee.com/si/prjs/realright/list",
                final_url="https://www.suaee.com/si/prjs/realright/list",
                http_status=200,
                raw_bytes=response_bytes,
            )

            downloader._collect_list_candidates(
                output_dir=tmp_dir,
                summary=summary,
                candidates=candidates,
                start=dt.date(2026, 4, 3),
                end=dt.date(2026, 4, 3),
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].xmid, "113868")
            self.assertEqual(candidates[0].project_code, "GR2026SH1000510")

    def test_sse_collect_list_candidates_rejects_falsey_non_list_nested_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiPhysicalAssetDownloader(html_root=tmp_dir)
            summary = DownloadSummary()
            candidates = []
            response_payload = {
                "code": 0,
                "extra": 1,
                "data": {"data": ""},
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            downloader._query_list_page = lambda **_: HttpFetchedText(  # type: ignore[method-assign]
                response_bytes.decode("utf-8"),
                source_url="https://www.suaee.com/si/prjs/realright/list",
                final_url="https://www.suaee.com/si/prjs/realright/list",
                http_status=200,
                raw_bytes=response_bytes,
            )

            downloader._collect_list_candidates(
                output_dir=tmp_dir,
                summary=summary,
                candidates=candidates,
                start=None,
                end=None,
            )

            self.assertEqual(candidates, [])
            self.assertEqual(len(summary.typed_errors), 1)
            self.assertEqual(summary.typed_errors[0].failure_kind, "validation")
            self.assertIn("invalid-data", summary.typed_errors[0].raw_reason)

    def test_sse_physical_save_json_resume_requires_same_stem_sidecar_before_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(
                tmp_dir,
                "2026年4月",
                "GR2026SH1000435-4-上海实物资产.html",
            )
            os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>old html only</body></html>")

            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=tmp_dir,
                resume=True,
                save_json=True,
            )
            summary = downloader.run(
                start_date="2026-04-17",
                end_date="2026-04-17",
                list_only=True,
                prefetched_candidates=[
                    {
                        "xmid": "116183",
                        "project_code": "GR2026SH1000435-4",
                        "project_name": "上海实物资产",
                        "disclosure_start": "2026-04-17",
                        "row": {
                            "xmid": "116183",
                            "xmbh": "GR2026SH1000435-4",
                            "xmmc": "上海实物资产",
                            "plksrq": "2026-04-17",
                        },
                    }
                ],
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(len(summary.candidate_entries), 1)

    def test_sse_physical_resume_rejects_explicit_invalid_shell_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(
                tmp_dir,
                "2026年4月",
                "GR2026SH1000435-4-上海实物资产.html",
            )
            os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>old shell</body></html>")
            with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "page_kind": "invalid_shell"}, handle)

            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=tmp_dir,
                resume=True,
                save_json=False,
            )
            summary = downloader.run(
                start_date="2026-04-17",
                end_date="2026-04-17",
                list_only=True,
                prefetched_candidates=[
                    {
                        "xmid": "116183",
                        "project_code": "GR2026SH1000435-4",
                        "project_name": "上海实物资产",
                        "disclosure_start": "2026-04-17",
                        "row": {
                            "xmid": "116183",
                            "xmbh": "GR2026SH1000435-4",
                            "xmmc": "上海实物资产",
                            "plksrq": "2026-04-17",
                        },
                    }
                ],
            )

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(len(summary.candidate_entries), 1)

    def test_sse_physical_resume_rejects_corrupt_evidence_sidecar(self) -> None:
        for evidence_state, evidence_bytes in (
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(evidence_state=evidence_state), tempfile.TemporaryDirectory() as tmp_dir:
                html_path = os.path.join(
                    tmp_dir,
                    "2026年4月",
                    "GR2026SH1000435-4-上海实物资产.html",
                )
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>old shell</body></html>")
                with open(f"{html_path}.peap-evidence.json", "wb") as handle:
                    handle.write(evidence_bytes)

                downloader = ShanghaiPhysicalAssetDownloader(
                    html_root=tmp_dir,
                    resume=True,
                    save_json=False,
                )
                summary = downloader.run(
                    start_date="2026-04-17",
                    end_date="2026-04-17",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "xmid": "116183",
                            "project_code": "GR2026SH1000435-4",
                            "project_name": "上海实物资产",
                            "disclosure_start": "2026-04-17",
                            "row": {
                                "xmid": "116183",
                                "xmbh": "GR2026SH1000435-4",
                                "xmmc": "上海实物资产",
                                "plksrq": "2026-04-17",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_sse_physical_save_json_resume_rejects_corrupt_same_stem_sidecar(self) -> None:
        for sidecar_state, sidecar_bytes in (
            ("invalid-json", b"{not json"),
            ("invalid-utf8", b"\xff\xfe"),
            ("non-object", b'["not", "object"]'),
        ):
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as tmp_dir:
                html_path = os.path.join(
                    tmp_dir,
                    "2026年4月",
                    "GR2026SH1000435-4-上海实物资产.html",
                )
                os.makedirs(os.path.splitext(html_path)[0] + "_files", exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>old html with bad sidecar</body></html>")
                with open(os.path.splitext(html_path)[0] + ".json", "wb") as handle:
                    handle.write(sidecar_bytes)

                downloader = ShanghaiPhysicalAssetDownloader(
                    html_root=tmp_dir,
                    resume=True,
                    save_json=True,
                )
                summary = downloader.run(
                    start_date="2026-04-17",
                    end_date="2026-04-17",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "xmid": "116183",
                            "project_code": "GR2026SH1000435-4",
                            "project_name": "上海实物资产",
                            "disclosure_start": "2026-04-17",
                            "row": {
                                "xmid": "116183",
                                "xmbh": "GR2026SH1000435-4",
                                "xmmc": "上海实物资产",
                                "plksrq": "2026-04-17",
                            },
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.candidate_entries), 1)

    def test_sse_resolve_page_url_returns_live_detail_route(self):
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        url = downloader._resolve_page_url(row={}, xmid="113868")
        assert url == "https://www.suaee.com/xmzx.html#/zczrDetail?XMID=113868"

    def test_sse_equity_transfer_uses_equity_detail_route(self):
        downloader = ShanghaiEquityTransferDownloader(html_root="/tmp/test")
        url = downloader._resolve_page_url(row={}, xmid="113868")
        assert url == "https://www.suaee.com/xmzx.html#/Detail?XMID=113868&PLZT=2"

    def test_sse_capital_increase_uses_capital_detail_route(self):
        downloader = ShanghaiCapitalIncreaseDownloader(html_root="/tmp/test")
        url = downloader._resolve_page_url(row={}, xmid="113868")
        assert url == "https://www.suaee.com/xmzx.html#/qyzzDetail?XMID=113868&PLZT=2"

    def test_sse_pre_disclosure_routes_equity_and_capital_separately(self):
        downloader = ShanghaiPreDisclosureDownloader(html_root="/tmp/test")
        equity_url = downloader._resolve_page_url(row={"FCLASS": "GQ", "XMLX": "1"}, xmid="113868")
        capital_url = downloader._resolve_page_url(row={"FCLASS": "1C", "XMLX": "1"}, xmid="223344")
        assert equity_url == "https://www.suaee.com/xmzx.html#/Detail?XMID=113868&PLZT=1"
        assert capital_url == "https://www.suaee.com/xmzx.html#/qyzzDetail?XMID=223344&PLZT=1"

    def test_sse_deal_resolve_page_url_returns_original_notice_detail_route(self) -> None:
        downloader = ShanghaiDealPhysicalAssetDownloader(html_root="/tmp/test")
        url = downloader._resolve_page_url(row={"FCLASS": "SW"}, xmid="31285")
        self.assertEqual(
            url,
            "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
        )

    def test_sse_deal_equity_list_row_missing_project_code_does_not_use_notice_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealEquityTransferDownloader(html_root=tmp_dir, max_pages=1)
            downloader._query_list_page = lambda **_: {  # type: ignore[method-assign]
                "code": 200,
                "data": [
                    {
                        "GGID": "GGID-NOT-PROJECT-CODE",
                        "XMID": "XMID-DETAIL-ONLY",
                        "XMMC": "上海成交股权项目",
                    }
                ],
                "extra": 1,
            }

            summary = downloader.run(start_date=None, end_date=None, list_only=True)

        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.candidate_entries, [])
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("missing-project-code", summary.typed_errors[0].raw_reason)
        self.assertFalse(
            any(entry.get("project_code") == "GGID-NOT-PROJECT-CODE" for entry in summary.candidate_entries)
        )

    def test_sse_deal_saves_rendered_notice_page_and_sidecar_not_synthetic_html(self) -> None:
        saved: dict[str, object] = {}

        def capture_page(*, rendered_html: str, page_url: str, html_path: str) -> None:
            saved["rendered_html"] = rendered_html
            saved["page_url"] = page_url
            saved["html_path"] = html_path

        def capture_sidecar(
            *,
            json_path: str,
            metadata: dict[str, object],
            detail_payload: dict[str, object],
            save_status: str = "complete",
            **_transport: object,
        ) -> None:
            saved["json_path"] = json_path
            saved["metadata"] = metadata
            saved["detail_payload"] = detail_payload
            saved["save_status"] = save_status

        rendered_html = """
        <html><body>
          <div class="inside-title"><div class="fl">成交公告</div></div>
          <div>上海江南长兴造船有限责任公司部分资产(平面流水线)</div>
          <table><tr><td>项目编号</td><td>GR2026SH1000563</td><td>成交日期</td><td>2026-05-07</td></tr></table>
        </body></html>
        """
        detail_payload = {
            "data": [
                {
                    "XMBH": "GR2026SH1000563",
                    "XMMC": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                    "CJRQ": "2026-05-07",
                    "CJJG": "20.995922（万元）",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(
                        rendered_html,
                        "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
                    ),
                ),
                patch.object(downloader, "_save_complete_page", side_effect=capture_page),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(
                    start_date="2026-05-07",
                    end_date="2026-05-07",
                    prefetched_candidates=[
                        {
                            "notice_id": "31285",
                            "xmid": "31285",
                            "project_code": "GR2026SH1000563",
                            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                            "collection_date": "2026-05-08",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {
                                "XMID": "31285",
                                "XMBH": "GR2026SH1000563",
                                "XMMC": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                                "FCLASS": "SW",
                            },
                        }
                    ],
                )

        self.assertEqual(summary.saved, 1)
        self.assertIn("成交公告", str(saved["rendered_html"]))
        self.assertNotIn("SSE Deal Notice", str(saved["rendered_html"]))
        self.assertEqual(
            saved["page_url"],
            "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
        )
        self.assertEqual(saved["metadata"]["source_url"], saved["page_url"])
        self.assertEqual(saved["detail_payload"], detail_payload)
        self.assertEqual(saved["save_status"], "complete")

    def test_sse_deal_fetch_failure_does_not_save_api_payload_as_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_query_detail_payload", return_value={"data": [{"CJRQ": "2026-05-07"}]}),
                patch.object(downloader, "_fetch_rendered_detail_html", side_effect=RuntimeError("page-not-ready")),
                patch.object(downloader, "_save_complete_page") as save_page,
                patch.object(downloader, "_write_sidecar_json") as write_sidecar,
            ):
                summary = downloader.run(
                    start_date="2026-05-07",
                    end_date="2026-05-07",
                    prefetched_candidates=[
                        {
                            "notice_id": "31285",
                            "xmid": "31285",
                            "project_code": "GR2026SH1000563",
                            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                            "collection_date": "2026-05-08",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {"XMID": "31285", "XMBH": "GR2026SH1000563", "FCLASS": "SW"},
                        }
                    ],
                )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        save_page.assert_not_called()
        write_sidecar.assert_not_called()

    def test_sse_deal_prefetched_resume_requires_sidecar_before_skip(self) -> None:
        rendered_html = """
        <html><body>
          <div>成交公告</div>
          <div>GR2026SH1000563</div>
          <div>上海江南长兴造船有限责任公司部分资产(平面流水线)</div>
          <table><tr><td>成交日期</td><td>2026-05-07</td></tr></table>
        </body></html>
        """
        detail_payload = {"data": [{"XMBH": "GR2026SH1000563", "CJRQ": "2026-05-07"}]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(
                tmp_dir,
                "2026年5月",
                "GR2026SH1000563-上海江南长兴造船有限责任公司部分资产(平面流水线).html",
            )
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>old html only</body></html>")

            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir, resume=True)
            with (
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload) as query_detail,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(
                        rendered_html,
                        "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
                    ),
                ) as fetch_html,
            ):
                summary = downloader.run(
                    start_date="2026-05-07",
                    end_date="2026-05-07",
                    prefetched_candidates=[
                        {
                            "notice_id": "31285",
                            "xmid": "31285",
                            "project_code": "GR2026SH1000563",
                            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                            "deal_date": "2026-05-07",
                            "row": {"XMID": "31285", "XMBH": "GR2026SH1000563", "FCLASS": "SW"},
                        }
                    ],
                )

            json_path = os.path.splitext(html_path)[0] + ".json"
            sidecar = json.load(open(json_path, encoding="utf-8"))

        self.assertEqual(summary.skipped_by_resume, 0)
        self.assertEqual(summary.saved, 1)
        query_detail.assert_called_once()
        fetch_html.assert_called_once()
        self.assertEqual(sidecar["metadata"]["project_code"], "GR2026SH1000563")
        self.assertEqual(sidecar["detail_payload"], detail_payload)
        self.assertEqual(
            sidecar["archive_content_sha256"],
            "sha256:" + hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(sidecar["archive_content_bytes"], len(rendered_html.encode("utf-8")))

    def test_sse_deal_detail_date_resume_skips_complete_recomputed_final_artifact(self) -> None:
        detail_payload = {"data": [{"XMBH": "GR2026SH1000563", "CJRQ": "2026-05-07"}]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            final_html_path = os.path.join(
                tmp_dir,
                "2026年5月",
                "GR2026SH1000563-上海江南长兴造船有限责任公司部分资产(平面流水线).html",
            )
            os.makedirs(os.path.dirname(final_html_path), exist_ok=True)
            with open(final_html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>already complete</body></html>")
            _write_complete_sidecar(
                final_html_path,
                {
                    "metadata": {
                        "task_id": "sse:deal:deal_physical_asset",
                        "source_id": "sse",
                        "record_family": "deal",
                        "business_id": "deal_physical_asset",
                        "project_code": "GR2026SH1000563",
                    }
                },
            )

            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir, resume=True)
            with (
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(downloader, "_fetch_rendered_detail_html") as fetch_html,
                patch.object(downloader, "_save_complete_page") as save_page,
                patch.object(downloader, "_write_sidecar_json") as write_sidecar,
            ):
                summary = downloader.run(
                    start_date="2026-05-07",
                    end_date="2026-05-07",
                    prefetched_candidates=[
                        {
                            "notice_id": "31285",
                            "xmid": "31285",
                            "project_code": "GR2026SH1000563",
                            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                            "collection_date": "2026-06-08",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "row": {"XMID": "31285", "XMBH": "GR2026SH1000563", "FCLASS": "SW"},
                        }
                    ],
                )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_resume, 1)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.list_unaccounted, 0)
        self.assertEqual(summary.detail_unaccounted, 0)
        fetch_html.assert_not_called()
        save_page.assert_not_called()
        write_sidecar.assert_not_called()

    def test_sse_deal_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes(
            (60, 104, 49, 62, 83, 83, 69, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101, 60, 47, 104, 49, 62)
        ).decode("ascii")
        rendered_html = f"<html><body>{shell_marker}</body></html>"
        detail_payload = {
            "data": [
                {
                    "XMBH": "GR2026SH1000563",
                    "XMMC": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                    "CJRQ": "2026-05-07",
                }
            ]
        }
        page_url = "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1"

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_query_detail_payload", return_value=detail_payload),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_http_fetched(rendered_html, page_url),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-05-07",
                    end_date="2026-05-07",
                    prefetched_candidates=[
                        {
                            "notice_id": "31285",
                            "xmid": "31285",
                            "project_code": "GR2026SH1000563",
                            "project_name": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                            "collection_date": "2026-05-08",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                            "page_url": page_url,
                            "row": {
                                "XMID": "31285",
                                "XMBH": "GR2026SH1000563",
                                "XMMC": "上海江南长兴造船有限责任公司部分资产(平面流水线)",
                                "FCLASS": "SW",
                            },
                        }
                    ],
                )

            evidence_paths = []
            html_paths = []
            json_paths = []
            for root, _, files in os.walk(tmp_dir):
                html_paths.extend(
                    os.path.join(root, name) for name in files if name.endswith(".html")
                )
                json_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".json") and not name.endswith(".peap-evidence.json")
                )
                evidence_paths.extend(
                    os.path.join(root, name)
                    for name in files
                    if name.endswith(".peap-evidence.json")
                )
            evidence = json.load(open(evidence_paths[0], encoding="utf-8"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(html_paths, [])
        self.assertEqual(json_paths, [])
        self.assertEqual(len(evidence_paths), 1)
        self.assertTrue(summary.typed_errors)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256("GR2026SH1000563".encode("utf-8")).hexdigest(),
        )
        self.assertIn("project_name_hash", evidence["identity_hints"])
        self.assertNotIn(page_url, json.dumps(evidence, ensure_ascii=False))

    def test_sse_deal_save_complete_page_preserves_exact_rendered_html_without_asset_rewrite(self) -> None:
        rendered_html = (
            "<!doctype html><html><head>"
            '<script src="https://cdn.example.com/app.js">console.log("keep")</script>'
            '<link rel="stylesheet" href="https://cdn.example.com/site.css">'
            "</head><body>"
            '<img src="https://cdn.example.com/image.png">'
            "<main>成交公告</main>"
            "</body></html>"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir)
            html_path = os.path.join(tmp_dir, "deal.html")
            with (
                patch.object(downloader, "_download_asset", return_value=None) as download_asset,
                patch.object(downloader, "_rewrite_css_assets") as rewrite_css_assets,
            ):
                downloader._save_complete_page(
                    rendered_html=rendered_html,
                    page_url="https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1",
                    html_path=html_path,
                )

            saved_html = open(html_path, encoding="utf-8").read()

        self.assertEqual(saved_html, rendered_html)
        self.assertIn('<script src="https://cdn.example.com/app.js">console.log("keep")</script>', saved_html)
        self.assertIn('href="https://cdn.example.com/site.css"', saved_html)
        self.assertIn('src="https://cdn.example.com/image.png"', saved_html)
        self.assertFalse(os.path.exists(os.path.splitext(html_path)[0] + "_files"))
        download_asset.assert_not_called()
        rewrite_css_assets.assert_not_called()

    def test_sse_fetch_rendered_html_waits_for_real_detail_page_instead_of_shell(self) -> None:
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        broken_html = """
        <html><body>
          <div class="project-detail-top">
            <div class="title"></div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：</span></div>
          </div>
          <div class="project-price-box">
            <span class="project-price-name">转让底价：</span>
            <span class="project-price-num"><span class="fs30"></span><span></span></span>
          </div>
          <div class="xmjs-infor-box">
            <div class="infor-date"><ul><li><div class="text">公告开始</div><div class="numb"></div></li></ul></div>
          </div>
          <div class="detail-info">
            <table><tr><th>交易机构</th><td><span class="text">项目负责人</span><span class="text"></span></td></tr></table>
          </div>
          <div>Network Error</div>
        </body></html>
        """
        valid_html = """
        <html><body>
          <div class="project_code">项目编号：GR2026SH1000435-4</div>
          <div class="project-detail-top">
            <div class="title">中国石油天然气股份有限公司华东化工销售宁波高新区分公司部分资产（二手车辆）</div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：</span></div>
          </div>
          <div class="project-price-box">
            <span class="project-price-name">转让底价：</span>
            <span class="project-price-num"><span class="fs30">12.3</span><span>万元</span></span>
          </div>
          <div class="xmjs-infor-box">
            <div class="infor-date"><ul><li><div class="text">公告开始</div><div class="numb">2026-04-17</div></li></ul></div>
          </div>
          <div class="detail-info">
            <table><tr><th>交易机构</th><td><span class="text">项目负责人</span><span class="text">王某</span></td></tr></table>
          </div>
          <table class="xm-tab">
            <tr><td class="xmtd1">转让方名称</td><td class="xmtd2">中国石油天然气股份有限公司华东化工销售宁波高新区分公司</td></tr>
          </table>
        </body></html>
        """
        page = types.SimpleNamespace(
            goto=AsyncMock(return_value=types.SimpleNamespace(status=200)),
            wait_for_selector=AsyncMock(),
            wait_for_function=AsyncMock(),
            wait_for_timeout=AsyncMock(),
            content=AsyncMock(side_effect=[broken_html, valid_html]),
            title=AsyncMock(return_value="上海联合产权交易所"),
            url="https://www.suaee.com/xmzx.html#/zczrDetail?XMID=116183",
        )

        rendered, http_status = asyncio.run(
            downloader._fetch_rendered_html(
                page=page,
                page_url="https://www.suaee.com/xmzx.html#/zczrDetail?XMID=116183",
                expected_project_code="GR2026SH1000435-4",
            )
        )

        self.assertEqual(rendered, valid_html)
        self.assertEqual(http_status, 200)
        self.assertGreaterEqual(page.content.await_count, 2)

    def test_sse_process_candidate_rejects_shell_snapshot_and_does_not_save(self) -> None:
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        downloader._detail_retries = 0
        downloader._save_complete_page = Mock()
        downloader._notify_item_saved = Mock()
        broken_html = """
        <html><body>
          <div class="project-detail-top">
            <div class="title"></div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：</span></div>
          </div>
          <div class="project-price-box">
            <span class="project-price-name">转让底价：</span>
            <span class="project-price-num"><span class="fs30"></span><span></span></span>
          </div>
          <div class="xmjs-infor-box">
            <div class="infor-date"><ul><li><div class="text">公告开始</div><div class="numb"></div></li></ul></div>
          </div>
          <div class="detail-info">
            <table><tr><th>交易机构</th><td><span class="text">项目负责人</span><span class="text"></span></td></tr></table>
          </div>
          <div>Network Error</div>
        </body></html>
        """
        page = types.SimpleNamespace(
            goto=AsyncMock(return_value=types.SimpleNamespace(status=200)),
            wait_for_selector=AsyncMock(),
            wait_for_timeout=AsyncMock(),
            content=AsyncMock(side_effect=[broken_html] * 12),
            close=AsyncMock(),
        )
        context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
        summary = DownloadSummary()
        candidate = types.SimpleNamespace(
            xmid="116183",
            project_code="GR2026SH1000435-4",
            page_url="https://www.suaee.com/xmzx.html#/zczrDetail?XMID=116183",
            html_path="/tmp/GR2026SH1000435-4.html",
            row={"xmmc": "中国石油天然气股份有限公司华东化工销售宁波高新区分公司部分资产（二手车辆）"},
        )

        asyncio.run(
            downloader._process_candidate(
                candidate=candidate,
                context=context,
                summary=summary,
                start=None,
                end=None,
                timeout_error_cls=TimeoutError,
            )
        )

        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.saved, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        downloader._save_complete_page.assert_not_called()
        downloader._notify_item_saved.assert_not_called()

    def test_sse_item_saved_callback_failure_marks_detail_failed_without_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "GR2026SH1000435-4.html")
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=tmp_dir,
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._detail_retries = 0
            downloader._fetch_rendered_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：GR2026SH1000435-4</div>"
                    "<div>信息披露起始日期：2026-01-10</div>"
                    "</body></html>",
                    200,
                )
            )
            downloader._save_complete_page = Mock()  # type: ignore[method-assign]
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page))
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                xmid="116183",
                project_code="GR2026SH1000435-4",
                page_url="https://www.suaee.com/xmzx.html#/zczrDetail?XMID=116183",
                html_path=html_path,
                row={"xmmc": "上海实物资产", "disclosure_start": "2026-01-10"},
            )

            asyncio.run(
                downloader._process_candidate(
                    candidate=candidate,
                    context=context,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_error_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)

    def test_sse_list_contract_mismatch_records_typed_list_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=tmp_dir,
                list_query_specs=[("UNKNOWN", "2")],
                max_pages=1,
            )
            summary = downloader.run(
                start_date="2026-01-01",
                end_date="2026-01-31",
                list_only=True,
            )

        self.assertEqual(summary.pages_requested, 0)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("sse-list-contract-mismatch", summary.typed_errors[0].raw_reason)

    def test_sse_physical_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes((78, 101, 116, 119, 111, 114, 107, 32, 69, 114, 114, 111, 114)).decode("ascii")
        rendered_html = f"""
        <html><body>
          <div class="project_code">项目编号：GR2026SH1000435-4</div>
          <div class="project-detail-top">
            <div class="title">上海实物资产详情</div>
            <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：</span></div>
          </div>
          <div class="project-price-box">
            <span class="project-price-name">转让底价：</span>
            <span class="project-price-num"><span class="fs30"></span><span></span></span>
          </div>
          <div class="xmjs-infor-box">
            <div class="infor-date"><ul><li><div class="text">公告开始</div><div class="numb"></div></li></ul></div>
          </div>
          <div class="detail-info">
            <table><tr><th>交易机构</th><td><span class="text">项目负责人</span><span class="text"></span></td></tr></table>
          </div>
          <div>{shell_marker}</div>
        </body></html>
        """
        page_url = "https://www.suaee.com/xmzx.html#/zczrDetail?XMID=116183"

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiPhysicalAssetDownloader(html_root=tmp_dir)
            html_path = os.path.join(tmp_dir, "physical.html")
            downloader._save_complete_page(
                rendered_html=rendered_html,
                page_url=page_url,
                html_path=html_path,
            )

            evidence_path = f"{html_path}.peap-evidence.json"
            evidence = json.load(open(evidence_path, encoding="utf-8"))
            saved_html = open(html_path, encoding="utf-8").read()

        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(saved_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256("GR2026SH1000435-4".encode("utf-8")).hexdigest(),
        )
        evidence_text = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(page_url, evidence_text)
        self.assertNotIn(shell_marker, evidence_text)


class CbexDownloaderFixTest(unittest.TestCase):
    def test_run_uses_submission_root_directly_without_type_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                output_type="实物资产",
            )
            captured: dict[str, str] = {}

            async def fake_run_async(*, outdir, **kwargs):
                captured["output_dir"] = outdir

            with patch.object(downloader, "_run_async", side_effect=fake_run_async):
                downloader.run(start_date="2026-03-10", end_date="2026-03-10", list_only=True)

            self.assertEqual(captured["output_dir"], temp_dir)

    def test_api_with_retry_logs_only_three_retries_before_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                output_type="实物资产",
            )
            page = types.SimpleNamespace(wait_for_timeout=AsyncMock())
            source = types.SimpleNamespace(label="房屋土地")
            downloader._api_one = AsyncMock(side_effect=[RuntimeError("api-http-521")] * 4)
            downloader._warmup = AsyncMock()
            downloader.logger = Mock()

            with self.assertRaisesRegex(RuntimeError, "list-api-failed 房屋土地 p=1: api-http-521"):
                asyncio.run(
                    downloader._api_with_retry(
                        context=object(),
                        page=page,
                        source=source,
                        page_index=1,
                    )
                )

            self.assertEqual(downloader._api_one.await_count, 4)
            self.assertEqual(page.wait_for_timeout.await_count, 3)
            self.assertEqual(downloader._warmup.await_count, 3)
            self.assertEqual(downloader.logger.warning.call_count, 3)
            self.assertEqual(
                [call.args[1] for call in downloader.logger.warning.call_args_list],
                [1, 2, 3],
            )

    def test_api_with_retry_returns_after_third_retry_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                output_type="实物资产",
            )
            page = types.SimpleNamespace(wait_for_timeout=AsyncMock())
            source = types.SimpleNamespace(label="股权转让")
            expected = {"result": True}
            downloader._api_one = AsyncMock(
                side_effect=[
                    RuntimeError("api-http-521"),
                    RuntimeError("api-http-521"),
                    RuntimeError("api-http-521"),
                    expected,
                ]
            )
            downloader._warmup = AsyncMock()
            downloader.logger = Mock()

            result = asyncio.run(
                downloader._api_with_retry(
                    context=object(),
                    page=page,
                    source=source,
                    page_index=1,
                )
            )

            self.assertEqual(result, expected)
            self.assertEqual(downloader._api_one.await_count, 4)
            self.assertEqual(page.wait_for_timeout.await_count, 3)
            self.assertEqual(downloader._warmup.await_count, 3)
            self.assertEqual(downloader.logger.warning.call_count, 3)
            self.assertEqual(
                [call.args[1] for call in downloader.logger.warning.call_args_list],
                [1, 2, 3],
            )

    def test_cbex_physical_item_saved_callback_failure_marks_detail_failed_without_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "2026年5月", "GR2026BJ100001-北京实物资产.html")
            downloader = CbexPhysicalAssetDownloader(
                html_root=temp_dir,
                output_type="实物资产",
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
            )
            downloader._detail_retries = 0
            downloader._fetch_html = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    "<html><body>"
                    "<div>项目编号：GR2026BJ100001</div>"
                    "<div>信息披露起始日期：2026-05-10</div>"
                    "</body></html>",
                    200,
                )
            )
            downloader._save_complete_page = AsyncMock()  # type: ignore[method-assign]
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page), request=object())
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                uid="cbex-callback-1",
                code="GR2026BJ100001",
                url="https://www.cbex.com.cn/xm/zczr/2026/05/demo.html",
                html_path=html_path,
                row={"title": "北京实物资产", "disclosuretime": "2026-05-10"},
            )

            asyncio.run(
                downloader._process_candidate(
                    context=context,
                    candidate=candidate,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)

    def test_cbex_404_detail_is_counted_as_unavailable_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(html_root=temp_dir, output_type="预披露")
            downloader._detail_retries = 0
            downloader._fetch_html = AsyncMock(  # type: ignore[method-assign]
                side_effect=DetailUnavailableError(
                    reason="not_found_url",
                    final_url="https://www.cbex.com.cn/404ym/index.html",
                    expected_identifier="G32026BJ1000187-0",
                )
            )
            page = types.SimpleNamespace(close=AsyncMock())
            context = types.SimpleNamespace(new_page=AsyncMock(return_value=page), request=object())
            summary = DownloadSummary()
            candidate = types.SimpleNamespace(
                uid="G32026BJ1000187-0",
                code="G32026BJ1000187-0",
                url="https://www.cbex.com.cn/xm/cqzr/ypl/202604/t20260410_123.html",
                html_path=os.path.join(temp_dir, "G32026BJ1000187-0-demo.html"),
                row={"name": "CBEX stale pre-disclosure", "disclosuretime": "2026-04-10"},
            )

            asyncio.run(
                downloader._process_candidate(
                    context=context,
                    candidate=candidate,
                    summary=summary,
                    start=None,
                    end=None,
                    timeout_cls=TimeoutError,
                )
            )

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 0)
        self.assertEqual(summary.skipped_by_detail_unavailable, 1)
        self.assertEqual(summary.typed_errors, [])

    def test_cbex_fetch_html_raises_typed_unavailable_for_http_404(self) -> None:
        class FakeResponse:
            status = 404

        class FakePage:
            url = "https://www.cbex.com.cn/xm/cqzr/ypl/202604/t20260410_123.html"

            async def goto(self, *args, **kwargs):
                return FakeResponse()

        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test")

        with self.assertRaises(DetailUnavailableError) as caught:
            asyncio.run(
                downloader._fetch_html(
                    page=FakePage(),
                    url="https://www.cbex.com.cn/xm/cqzr/ypl/202604/t20260410_123.html",
                    code="G32026BJ1000187-0",
                )
            )

        self.assertEqual(caught.exception.reason, "not_found_status")

    def test_cbex_fetch_html_raises_typed_unavailable_for_official_404_route(self) -> None:
        class FakeResponse:
            status = 200

        class FakePage:
            url = "https://www.cbex.com.cn/404ym/index.html"

            async def goto(self, *args, **kwargs):
                return FakeResponse()

            async def wait_for_function(self, *args, **kwargs):
                return None

            async def wait_for_timeout(self, *args, **kwargs):
                return None

            async def content(self):
                return "<html><body><h1>404</h1><div>北京产权交易所</div></body></html>"

        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test")

        with self.assertRaises(DetailUnavailableError) as caught:
            asyncio.run(
                downloader._fetch_html(
                    page=FakePage(),
                    url="https://www.cbex.com.cn/xm/cqzr/ypl/202604/t20260410_123.html",
                    code="G32026BJ1000187-0",
                )
            )

        self.assertEqual(caught.exception.reason, "not_found_url")

    def test_fetch_html_rejects_cbex_physical_page_without_expected_identity(self) -> None:
        class FakePage:
            url = "https://www.cbex.com.cn/xm/zczr/2026/05/not-the-project.html"

            async def goto(self, *args, **kwargs):
                return types.SimpleNamespace(status=200)

            async def wait_for_function(self, *args, **kwargs):
                return None

            async def wait_for_timeout(self, *args, **kwargs):
                return None

            async def content(self):
                return (
                    "<html><body><h1>北京产权交易所资产转让</h1>"
                    "<div>项目编号</div><div>GR2026BJ9999999</div>"
                    "<div>项目名称：其他项目</div></body></html>"
                )

        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test")

        with self.assertRaisesRegex(RuntimeError, "detail-page-mismatch"):
            asyncio.run(
                downloader._fetch_html(
                    page=FakePage(),
                    url="https://www.cbex.com.cn/xm/zczr/2026/05/not-the-project.html",
                    code="GR2026BJ1001598",
                )
            )

    def test_cbex_physical_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes(
            (95, 95, 106, 115, 108, 95, 99, 108, 101, 97, 114, 97, 110, 99, 101, 95, 115)
        ).decode("ascii")
        html = f"<html><body><h1>CBEX</h1><script>{shell_marker}</script></body></html>"
        page_url = "https://www.cbex.com.cn/xm/zczr/detail-demo"

        class EmptyRequestContext:
            async def get(self, *_args, **_kwargs):  # pragma: no cover - should not be reached
                raise AssertionError("asset download should not be attempted")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexPhysicalAssetDownloader(html_root=temp_dir, output_type="实物资产")
            html_path = os.path.join(temp_dir, "cbex-physical.html")

            asyncio.run(
                downloader._save_complete_page(
                    html=html,
                    page_url=page_url,
                    html_path=html_path,
                    request_context=EmptyRequestContext(),
                )
            )

            saved_html = open(html_path, encoding="utf-8").read()
            evidence_path = f"{html_path}.peap-evidence.json"
            evidence = json.loads(open(evidence_path, encoding="utf-8").read())

        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(evidence["content_sha256"], "sha256:" + hashlib.sha256(saved_html.encode("utf-8")).hexdigest())
        self.assertEqual(
            evidence["identity_hints"]["artifact_stem_hash"],
            "sha256:" + hashlib.sha256("cbex-physical".encode("utf-8")).hexdigest(),
        )
        evidence_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(page_url, evidence_text)
        self.assertNotIn(shell_marker, evidence_text)
        self.assertNotIn(saved_html, evidence_text)


if __name__ == "__main__":
    unittest.main()
