from __future__ import annotations

import os
import re
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch

if "bs4" not in sys.modules:
    fake_bs4 = types.ModuleType("bs4")

    class _FakeTag:
        def __init__(self, attrs: dict[str, str], text: str) -> None:
            self._attrs = attrs
            self._text = text

        def get(self, key: str, default=None):
            return self._attrs.get(key, default)

        def get_text(self, sep: str = " ", strip: bool = False) -> str:
            text = self._text
            return text.strip() if strip else text

    class _FakeBeautifulSoup:
        def __init__(self, html: str, _parser: str) -> None:
            self._html = html

        def find_all(self, name=None, href=False):
            if not (name in (None, "a") or (isinstance(name, list) and "a" in name)):
                return []
            tags: list[_FakeTag] = []
            for match in re.finditer(r"<a(?P<attrs>[^>]*)>(?P<text>.*?)</a>", self._html, re.IGNORECASE | re.DOTALL):
                attrs_text = match.group("attrs")
                attrs = {
                    key.lower(): value
                    for key, value in re.findall(r'([a-zA-Z_:][\w:.-]*)\s*=\s*"([^"]*)"', attrs_text)
                }
                if href and "href" not in attrs:
                    continue
                tags.append(_FakeTag(attrs, re.sub(r"<[^>]+>", " ", match.group("text"))))
            return tags

        def select_one(self, selector: str):
            if selector != "a.CPageus[href]":
                return None
            for tag in self.find_all("a", href=True):
                class_names = str(tag.get("class") or "").split()
                if "CPageus" in class_names:
                    return tag
            return None

        def get_text(self, sep: str = " ", strip: bool = False) -> str:
            text = re.sub(r"<[^>]+>", " ", self._html)
            text = re.sub(r"\s+", sep, text)
            return text.strip() if strip else text

    fake_bs4.BeautifulSoup = _FakeBeautifulSoup
    sys.modules["bs4"] = fake_bs4

from peap.downloaders.cquae import (
    ChongqingProjectDownloader,
    _normalize_list_url,
)
from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader
from peap.downloaders.sse_physical import ShanghaiPhysicalAssetDownloader
from peap.downloaders.tpre import DownloadSummary, TpreProjectDownloader, _ListQuerySpec


class TpreDownloaderFixTest(unittest.TestCase):
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
            self.assertEqual(len(summary.errors), 0)
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


class CquaeDownloaderFixTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
