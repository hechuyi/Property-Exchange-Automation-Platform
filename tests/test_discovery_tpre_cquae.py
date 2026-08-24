from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

from peap.constants import TYPE_PHYSICAL_ASSET, TYPE_PRE_DISCLOSURE
from peap.download_archive_audit import audit_discovery_evidence_root
from peap.downloaders.common import HttpFetchedText
from peap.downloaders.cquae import (
    ChongqingProjectDownloader,
    _cquae_list_sources,
)
from peap.downloaders.cquae import (
    _ListSource as CquaeListSource,
)
from peap.downloaders.tpre import (
    TpreProjectDownloader,
    _ListQuerySpec,
    _tpre_list_queries,
)


class _UrlopenResponse:
    def __init__(
        self,
        raw_bytes: bytes,
        *,
        final_url: str,
        http_status: int,
        charset: str = "utf-8",
    ) -> None:
        self._raw_bytes = raw_bytes
        self._final_url = final_url
        self.status = http_status
        self._charset = charset
        self.headers = self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        return False

    def read(self) -> bytes:
        return self._raw_bytes

    def get_content_charset(self) -> str:
        return self._charset

    def geturl(self) -> str:
        return self._final_url

    def getcode(self) -> int:
        return self.status


def _fetched(
    raw_bytes: bytes,
    *,
    source_url: str,
    final_url: str | None = None,
    http_status: int = 200,
    decoded_text: str | None = None,
    charset_hint: str | None = None,
) -> HttpFetchedText:
    fetched = HttpFetchedText(
        raw_bytes.decode("utf-8") if decoded_text is None else decoded_text,
        source_url=source_url,
        final_url=final_url or source_url,
        http_status=http_status,
        raw_bytes=raw_bytes,
    )
    if charset_hint is not None:
        fetched.charset_hint = charset_hint
    return fetched


def _tpre_list_url(
    *,
    page: int,
    query: _ListQuerySpec | None = None,
    page_size: int = 2,
) -> str:
    selected = query or _tpre_list_queries("equity_transfer")[0]
    params = {
        "current": page,
        "size": page_size,
        "systemCode": selected.system_code,
        "bizTypeCode": selected.biz_type_code,
        **selected.extra_params,
    }
    return (
        "https://trade.tpre.cn/up/biz/project/anmuas/page?"
        + urllib.parse.urlencode(params)
    )


def _json_response(
    payload: object,
    *,
    page: int,
    query: _ListQuerySpec | None = None,
    page_size: int = 2,
    decoded_text: str | None = None,
) -> HttpFetchedText:
    normalized_payload = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = dict(payload["data"])
        data.setdefault("current", page)
        data.setdefault("size", page_size)
        normalized_payload = {**payload, "data": data}
    raw_bytes = json.dumps(
        normalized_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    source_url = _tpre_list_url(page=page, query=query, page_size=page_size)
    return _fetched(
        raw_bytes,
        source_url=source_url,
        final_url=f"{source_url}&transport=final",
        http_status=206,
        decoded_text=decoded_text,
    )


def _cquae_url(
    *,
    business_id: str = "equity_transfer",
    source_index: int = 0,
    page: int | None = None,
    overrides: dict[str, object] | None = None,
) -> str:
    source = _cquae_list_sources(business_id)[source_index]
    parsed = urllib.parse.urlsplit(source.list_url)
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if page is not None:
        params["page"] = str(page)
    params.update({key: str(value) for key, value in (overrides or {}).items()})
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), "")
    )


def _tpre_row(index: int) -> dict[str, str]:
    project_code = f"T32026TJ{index:06d}"
    return {
        "projectCode": project_code,
        "title": f"Tianjin project {index}",
        "projectLink": f"https://trade.tpre.cn/transaction-view/project?id={index}",
        "startTime": "2026-07-10",
    }


def _cquae_html(
    *,
    total: int | None,
    project_ids: tuple[int, ...],
    next_href: str = "",
) -> bytes:
    items = "".join(
        (
            '<div class="n2_List itcon">'
            f'<a class="P_List_A" href="/Project/Show?id={project_id}">'
            f"Chongqing project {project_id}</a>"
            '<span>挂牌开始日期：2026-07-10</span>'
            "</div>"
        )
        for project_id in project_ids
    )
    next_anchor = f'<a href="{next_href}">下一页</a>' if next_href else ""
    total_marker = f"<div>共找到 {total} 条(项目)记录</div>" if total is not None else ""
    html = (
        "<html><head><title>项目中心- 重庆产权交易网</title></head><body>"
        f"{total_marker}{items}{next_anchor}"
        "</body></html>"
    )
    return html.encode("utf-8")


def _only_manifest(root: str) -> tuple[Path, dict[str, object]]:
    manifest_paths = list(Path(root).glob("_evidence/**/discovery/**/manifest.json"))
    if len(manifest_paths) != 1:
        raise AssertionError(f"expected one discovery manifest, got {manifest_paths!r}")
    manifest_path = manifest_paths[0]
    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))


def _task_manifest(root: str) -> dict[str, object]:
    manifest_paths = list(Path(root).glob("_evidence/**/discovery/task_manifest.json"))
    if len(manifest_paths) != 1:
        raise AssertionError(f"expected one task manifest, got {manifest_paths!r}")
    return json.loads(manifest_paths[0].read_text(encoding="utf-8"))


class TpreDiscoveryEvidenceTest(unittest.TestCase):
    def _downloader(self, root: str, **kwargs) -> TpreProjectDownloader:
        return TpreProjectDownloader(
            html_root=root,
            page_size=2,
            list_queries=_tpre_list_queries("equity_transfer"),
            run_id="run-tpre-discovery",
            **kwargs,
        )

    def test_list_transport_preserves_raw_bytes_and_http_provenance(self) -> None:
        raw_bytes = b'{"code":0,"data":{"total":0,"records":[]}}'
        final_url = f"{_tpre_list_url(page=1)}&transport=final"
        response = _UrlopenResponse(
            raw_bytes,
            final_url=final_url,
            http_status=206,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch("peap.downloaders.tpre.urllib.request.urlopen", return_value=response):
                fetched = downloader._query_list_page(
                    page_index=1,
                    query=downloader.list_queries[0],
                )

        self.assertIsInstance(fetched, HttpFetchedText)
        self.assertEqual(fetched.raw_bytes, raw_bytes)
        self.assertIn("current=1", fetched.source_url)
        self.assertEqual(fetched.final_url, final_url)
        self.assertEqual(fetched.http_status, 206)

    def test_list_parsing_uses_raw_bytes_not_divergent_string_value(self) -> None:
        divergent_text = json.dumps(
            {
                "code": 0,
                "data": {
                    "current": 1,
                    "size": 2,
                    "pages": 0,
                    "total": 0,
                    "records": [],
                },
            },
            separators=(",", ":"),
        )
        response = _json_response(
            {
                "code": 0,
                "data": {
                    "pages": 1,
                    "total": 1,
                    "records": [_tpre_row(1)],
                },
            },
            page=1,
            decoded_text=divergent_text,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "complete")

    def test_unsupported_charset_archives_raw_and_fails_page_and_task(self) -> None:
        expected = _json_response(
            {
                "code": 0,
                "data": {"pages": 1, "total": 1, "records": [_tpre_row(1)]},
            },
            page=1,
        )
        response = _UrlopenResponse(
            expected.raw_bytes,
            final_url=expected.final_url,
            http_status=200,
            charset="x-peap-unsupported",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch("peap.downloaders.tpre.urllib.request.urlopen", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            raw_paths = list(manifest_path.parent.glob("page_*.raw.json"))
            sidecar = json.loads(
                next(manifest_path.parent.glob("page_*.meta.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual([path.read_bytes() for path in raw_paths], [expected.raw_bytes])
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_source_and_final_urls_preserve_authoritative_endpoint_and_price_filter(self) -> None:
        query = _tpre_list_queries("physical_asset")[0]
        valid_response = _json_response(
            {
                "code": 0,
                "data": {"pages": 1, "total": 1, "records": [_tpre_row(1)]},
            },
            page=1,
            query=query,
        )
        parsed_source = urllib.parse.urlsplit(valid_response.source_url)
        source_params = dict(urllib.parse.parse_qsl(parsed_source.query))
        source_params.pop("priceBegin")
        source_without_price = urllib.parse.urlunsplit(
            parsed_source._replace(query=urllib.parse.urlencode(source_params))
        )
        responses = {
            "source-missing-price": _fetched(
                valid_response.raw_bytes,
                source_url=source_without_price,
                final_url=valid_response.final_url,
            ),
            "final-wrong-path": _fetched(
                valid_response.raw_bytes,
                source_url=valid_response.source_url,
                final_url=(
                    "https://trade.tpre.cn/not-the-list-endpoint?"
                    + parsed_source.query
                ),
            ),
        }

        for label, response in responses.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = TpreProjectDownloader(
                    html_root=temp_dir,
                    page_size=2,
                    output_type=TYPE_PHYSICAL_ASSET,
                    list_queries=[query],
                    run_id=f"run-tpre-url-{label}",
                )
                with patch.object(downloader, "_query_list_page", return_value=response):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")

    def test_response_pagination_fields_are_required_and_match_request(self) -> None:
        base_data = {
            "current": 1,
            "size": 2,
            "pages": 1,
            "total": 1,
            "records": [_tpre_row(1)],
        }
        cases: dict[str, dict[str, object]] = {}
        for missing_field in ("current", "size", "pages"):
            data = dict(base_data)
            data.pop(missing_field)
            cases[f"missing-{missing_field}"] = data
        cases["size-mismatch"] = {**base_data, "size": 1}

        for label, data in cases.items():
            raw_bytes = json.dumps(
                {"code": 0, "data": data},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            source_url = _tpre_list_url(page=1)
            response = _fetched(
                raw_bytes,
                source_url=source_url,
                final_url=f"{source_url}&transport=final",
            )
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = self._downloader(temp_dir)
                with patch.object(downloader, "_query_list_page", return_value=response):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")

    def test_collect_rejects_empty_subset_and_drifted_query_configuration(self) -> None:
        authoritative = _tpre_list_queries("pre_disclosure")
        drifted = [
            _ListQuerySpec(
                label=authoritative[0].label,
                system_code=authoritative[0].system_code,
                biz_type_code="FORMAL",
                extra_params=dict(authoritative[0].extra_params),
            ),
            authoritative[1],
        ]
        cases = {
            "empty": [],
            "subset": authoritative[:1],
            "parameter-drift": drifted,
        }

        for label, queries in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = TpreProjectDownloader(
                    html_root=temp_dir,
                    page_size=2,
                    output_type=TYPE_PRE_DISCLOSURE,
                    list_queries=queries,
                    run_id=f"run-tpre-config-{label}",
                )
                fetch = Mock(side_effect=AssertionError("drifted config must not fetch"))
                with patch.object(downloader, "_query_list_page", fetch):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                fetch.assert_not_called()
                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")
                self.assertIsNotNone(summary.discovery_task_manifest)

    def test_successful_traversal_archives_every_page_and_completes_manifest(self) -> None:
        responses = [
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "current": 1,
                        "size": 2,
                        "pages": 2,
                        "total": 3,
                        "records": [_tpre_row(1), _tpre_row(2)],
                    },
                },
                page=1,
            ),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "current": 2,
                        "size": 2,
                        "pages": 2,
                        "total": 3,
                        "records": [_tpre_row(3)],
                    },
                },
                page=2,
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", side_effect=responses):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            task_manifest = _task_manifest(temp_dir)
            raw_paths = sorted(manifest_path.parent.glob("page_*.raw.json"))
            sidecars = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(manifest_path.parent.glob("page_*.meta.json"))
            ]

            self.assertEqual(summary.detail_candidates, 3)
            self.assertEqual(summary.typed_errors, [])
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertTrue(
                Path(temp_dir, summary.discovery_task_manifest["path"]).is_file()
            )
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "declared_pages_exhausted")
            self.assertEqual(manifest["archived_page_count"], 2)
            self.assertEqual(task_manifest["save_status"], "complete")
            self.assertEqual(task_manifest["expected_query_ids"], ["001-equity-formal"])
            self.assertEqual(task_manifest["candidate_count"], 3)
            self.assertEqual(
                len(task_manifest["candidate_fingerprints"]),
                len(summary.candidate_entries),
            )
            self.assertEqual([path.read_bytes() for path in raw_paths], [r.raw_bytes for r in responses])
            self.assertEqual([sidecar["parse_status"] for sidecar in sidecars], ["complete", "complete"])
            self.assertEqual(sidecars[0]["final_url"], responses[0].final_url)
            self.assertEqual(sidecars[0]["http_status"], 206)
            audit = audit_discovery_evidence_root(temp_dir)
            self.assertTrue(audit.ok, audit.to_dict())

    def test_malformed_api_response_keeps_raw_page_and_fails_page_then_query(self) -> None:
        raw_bytes = b'{"code":0,"data":{"records":'
        response = _fetched(
            raw_bytes,
            source_url=_tpre_list_url(page=1),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            task_manifest = _task_manifest(temp_dir)
            raw_path = next(manifest_path.parent.glob("page_*.raw.json"))
            sidecar_path = next(manifest_path.parent.glob("page_*.meta.json"))
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))

            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(raw_path.read_bytes(), raw_bytes)
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertIsNotNone(summary.discovery_task_manifest)

    def test_candidate_conversion_failure_cannot_leave_complete_task_manifest(self) -> None:
        response = _json_response(
            {
                "code": 0,
                "data": {
                    "pages": 1,
                    "total": 1,
                    "records": [_tpre_row(1)],
                },
            },
            page=1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with (
                patch.object(downloader, "_query_list_page", return_value=response),
                patch.object(
                    downloader,
                    "_rows_to_candidates",
                    side_effect=RuntimeError("candidate conversion stopped"),
                ),
            ):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            task_manifest = _task_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(task_manifest["candidate_count"], 0)

    def test_official_empty_api_query_completes_with_zero_rows(self) -> None:
        response = _json_response(
            {"code": 0, "data": {"pages": 0, "total": 0, "records": []}},
            page=1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "official_empty")
            self.assertEqual(manifest["observed_row_count"], 0)
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "complete")

    def test_api_row_without_stable_id_or_code_fails_page_and_query(self) -> None:
        response = _json_response(
            {
                "code": 0,
                "data": {
                    "pages": 1,
                    "total": 1,
                    "records": [
                        {
                            "title": "row without a stable identity",
                            "projectLink": "https://trade.tpre.cn/transaction-view/project",
                        }
                    ],
                },
            },
            page=1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            sidecar = json.loads(
                next(manifest_path.parent.glob("page_*.meta.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_repeated_api_page_fails_closed_without_accepting_candidates(self) -> None:
        first_rows = [_tpre_row(1), _tpre_row(2)]
        overlapping_rows = [_tpre_row(2), _tpre_row(3)]
        responses = [
            _json_response(
                {"code": 0, "data": {"pages": 2, "total": 4, "records": first_rows}},
                page=1,
            ),
            _json_response(
                {"code": 0, "data": {"pages": 2, "total": 4, "records": overlapping_rows}},
                page=2,
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_query_list_page", side_effect=responses):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(any("repeated" in error.raw_reason for error in summary.typed_errors))
            self.assertEqual(manifest["save_status"], "failed")

    def test_declared_total_or_page_limit_not_closed_fails_query(self) -> None:
        cases = (
            (
                "total-mismatch",
                None,
                [
                    _json_response(
                        {
                            "code": 0,
                            "data": {"pages": 2, "total": 3, "records": [_tpre_row(1), _tpre_row(2)]},
                        },
                        page=1,
                    ),
                    _json_response(
                        {"code": 0, "data": {"pages": 2, "total": 3, "records": []}},
                        page=2,
                    ),
                ],
            ),
            (
                "page-limit",
                1,
                [
                    _json_response(
                        {
                            "code": 0,
                            "data": {"pages": 2, "total": 3, "records": [_tpre_row(1), _tpre_row(2)]},
                        },
                        page=1,
                    )
                ],
            ),
        )
        for label, max_pages, responses in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = self._downloader(temp_dir, max_pages=max_pages)
                with patch.object(downloader, "_query_list_page", side_effect=responses):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                _manifest_path, manifest = _only_manifest(temp_dir)
                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(manifest["save_status"], "failed")

    def test_task_failure_discards_completed_query_when_an_expected_query_fails(self) -> None:
        queries = _tpre_list_queries("pre_disclosure")
        first_response = _json_response(
            {"code": 0, "data": {"pages": 1, "total": 1, "records": [_tpre_row(1)]}},
            page=1,
            query=queries[0],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = TpreProjectDownloader(
                html_root=temp_dir,
                page_size=2,
                output_type=TYPE_PRE_DISCLOSURE,
                list_queries=queries,
                run_id="run-tpre-task-failure",
            )
            with patch.object(
                downloader,
                "_query_list_page",
                side_effect=[first_response, RuntimeError("second query failed")],
            ):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            task_manifest = _task_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(
                task_manifest["expected_query_ids"],
                ["001-equity-prepare", "002-capital-prepare"],
            )


class CquaeDiscoveryEvidenceTest(unittest.TestCase):
    def _downloader(self, root: str, **kwargs) -> ChongqingProjectDownloader:
        return ChongqingProjectDownloader(
            html_root=root,
            page_size=2,
            list_sources=_cquae_list_sources("equity_transfer"),
            run_id="run-cquae-discovery",
            **kwargs,
        )

    def test_list_transport_preserves_raw_bytes_and_http_provenance(self) -> None:
        raw_bytes = _cquae_html(total=0, project_ids=())
        final_url = f"{_cquae_url()}&transport=final"
        response = _UrlopenResponse(
            raw_bytes,
            final_url=final_url,
            http_status=207,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch("peap.downloaders.cquae.urllib.request.urlopen", return_value=response):
                fetched = downloader._fetch_list_html(downloader.list_sources[0].list_url)

        self.assertIsInstance(fetched, HttpFetchedText)
        self.assertEqual(fetched.raw_bytes, raw_bytes)
        self.assertEqual(fetched.source_url, downloader.list_sources[0].list_url)
        self.assertEqual(fetched.final_url, final_url)
        self.assertEqual(fetched.http_status, 207)

    def test_browser_fallback_preserves_response_body_bytes(self) -> None:
        raw_bytes = _cquae_html(total=0, project_ids=())
        source_url = _cquae_url()
        final_url = f"{source_url}&browser=final"

        class _ApiResponse:
            status = 207
            url = final_url
            headers = {"content-type": "text/html; charset=utf-8"}

            async def body(self) -> bytes:
                return raw_bytes

        class _RequestContext:
            async def get(self, _url: str, *, timeout: int):
                self.timeout = timeout
                return _ApiResponse()

            async def dispose(self) -> None:
                return None

        request_context = _RequestContext()

        class _RequestFactory:
            async def new_context(self, **_kwargs):
                return request_context

        class _PlaywrightContext:
            async def __aenter__(self):
                self.request = _RequestFactory()
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
                return False

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch(
                "playwright.async_api.async_playwright",
                return_value=_PlaywrightContext(),
            ):
                fetched = asyncio.run(
                    downloader._fetch_list_html_via_browser_request_async(
                        source_url,
                        RuntimeError("urllib failed"),
                    )
                )

        self.assertIsInstance(fetched, HttpFetchedText)
        self.assertEqual(fetched.raw_bytes, raw_bytes)
        self.assertEqual(fetched.source_url, source_url)
        self.assertEqual(fetched.final_url, final_url)
        self.assertEqual(fetched.http_status, 207)

    def test_list_parsing_uses_raw_bytes_not_divergent_string_value(self) -> None:
        raw_bytes = _cquae_html(total=1, project_ids=(1,))
        divergent_text = _cquae_html(total=0, project_ids=()).decode("utf-8")
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
            decoded_text=divergent_text,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "complete")

    def test_vue_shell_switches_to_official_project_page_api(self) -> None:
        shell_bytes = (
            "<html><head><title>产权交易网</title></head><body><div id=app></div>"
            '<script src="/static/js/app.current.js"></script></body></html>'
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            source = downloader.list_sources[0]
            api_url = downloader._cquae_api_page_url(source, 1)
            payload = {
                "message": "ok",
                "state": "SUCCESS",
                "target": {
                    "page": 1,
                    "size": 2,
                    "totalClause": 2,
                    "totalPage": 1,
                    "result": [
                        {
                            "projectId": "208001",
                            "noticeName": "API project one",
                            "listStartDate": "2026-07-10 09:00:00",
                            "listEndDate": "2026-08-10 23:59:59",
                            "listingPrice": 100.5,
                            "projectTypeSubjectRemark": "OWNERSHIP",
                            "listingType": "OFFICIAL",
                        },
                        {
                            "projectId": "208002",
                            "noticeName": "API project two",
                            "listStartDate": "2026-07-11 09:00:00",
                            "listEndDate": "2026-08-11 23:59:59",
                            "listingPrice": 200,
                            "projectTypeSubjectRemark": "OWNERSHIP",
                            "listingType": "OFFICIAL",
                        },
                    ],
                },
            }
            api_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            responses = [
                _fetched(shell_bytes, source_url=source.list_url),
                _fetched(api_bytes, source_url=api_url),
            ]
            fetch = Mock(side_effect=responses)

            with patch.object(downloader, "_fetch_list_html", fetch):
                summary = downloader.run(
                    start_date="2026-07-01",
                    end_date="2026-07-31",
                    list_only=True,
                )

            manifest_path, manifest = _only_manifest(temp_dir)
            raw_paths = list(manifest_path.parent.glob("page_*.raw.json"))
            sidecar = json.loads(
                next(manifest_path.parent.glob("page_*.meta.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(summary.detail_candidates, 2)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual([path.read_bytes() for path in raw_paths], [api_bytes])
            self.assertEqual(sidecar["body_format"], "json")
            self.assertEqual(sidecar["request_metadata"]["transport"], "project_page_api")
            self.assertEqual(manifest["termination_reason"], "declared_pages_exhausted")
            self.assertTrue(
                all(
                    "/projectCenter/detail?" in entry["page_url"]
                    for entry in summary.candidate_entries
                )
            )

    def test_project_page_api_accepts_successful_empty_target_without_result(self) -> None:
        shell_bytes = (
            "<html><head><title>产权交易网</title></head><body><div id=app></div>"
            '<script src="/static/js/app.current.js"></script></body></html>'
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            source = downloader.list_sources[0]
            api_url = downloader._cquae_api_page_url(source, 1)
            payload = {
                "message": "ok",
                "state": "SUCCESS",
                "target": {
                    "page": 1,
                    "size": 2,
                    "totalClause": 0,
                    "totalPage": 0,
                },
            }
            api_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            with patch.object(
                downloader,
                "_fetch_list_html",
                side_effect=[
                    _fetched(shell_bytes, source_url=source.list_url),
                    _fetched(api_bytes, source_url=api_url),
                ],
            ):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(manifest["termination_reason"], "official_empty")
            self.assertEqual(manifest["observed_row_count"], 0)

    def test_unsupported_charset_archives_raw_and_fails_page_and_task(self) -> None:
        raw_bytes = _cquae_html(total=1, project_ids=(1,))
        response = _UrlopenResponse(
            raw_bytes,
            final_url=_cquae_url(),
            http_status=200,
            charset="x-peap-unsupported",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            downloader.list_fetch_attempts = 1
            with patch("peap.downloaders.cquae.urllib.request.urlopen", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            raw_paths = list(manifest_path.parent.glob("page_*.raw.html"))
            sidecar = json.loads(
                next(manifest_path.parent.glob("page_*.meta.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual([path.read_bytes() for path in raw_paths], [raw_bytes])
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_plain_string_legacy_response_is_rejected_with_typed_failure(self) -> None:
        legacy_html = _cquae_html(total=1, project_ids=(1,)).decode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=legacy_html):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertTrue(
                any("transport" in error.raw_reason for error in summary.typed_errors)
            )
            self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_source_and_final_urls_preserve_initial_filters_and_path(self) -> None:
        raw_bytes = _cquae_html(total=1, project_ids=(1,))
        valid_url = _cquae_url()
        parsed = urllib.parse.urlsplit(valid_url)
        source_params = dict(urllib.parse.parse_qsl(parsed.query))
        source_params.pop("nt")
        source_without_filter = urllib.parse.urlunsplit(
            parsed._replace(query=urllib.parse.urlencode(source_params))
        )
        responses = {
            "source-missing-filter": _fetched(
                raw_bytes,
                source_url=source_without_filter,
                final_url=valid_url,
            ),
            "final-wrong-path": _fetched(
                raw_bytes,
                source_url=valid_url,
                final_url=urllib.parse.urlunsplit(parsed._replace(path="/other")),
            ),
        }

        for label, response in responses.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = self._downloader(temp_dir)
                with patch.object(downloader, "_fetch_list_html", return_value=response):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")

    def test_next_url_cannot_change_project_or_business_filters(self) -> None:
        invalid_next_urls = {
            "project": _cquae_url(page=2, overrides={"projectID": 2}),
            "business": _cquae_url(page=2, overrides={"nt": 3}),
        }
        for label, invalid_next_url in invalid_next_urls.items():
            first_raw = _cquae_html(
                total=3,
                project_ids=(1, 2),
                next_href=invalid_next_url,
            )
            second_raw = _cquae_html(total=3, project_ids=(3,))
            responses = [
                _fetched(first_raw, source_url=_cquae_url()),
                _fetched(second_raw, source_url=invalid_next_url),
            ]
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = self._downloader(temp_dir)
                fetch = Mock(side_effect=responses)
                with patch.object(downloader, "_fetch_list_html", fetch):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")

    def test_next_url_requires_same_host_path_and_continuous_page(self) -> None:
        valid_page_two = urllib.parse.urlsplit(_cquae_url(page=2))
        invalid_next_urls = {
            "host": urllib.parse.urlunsplit(valid_page_two._replace(netloc="mirror.cquae.com")),
            "path": urllib.parse.urlunsplit(valid_page_two._replace(path="/other")),
            "page-gap": _cquae_url(page=3),
        }
        for label, invalid_next_url in invalid_next_urls.items():
            first_raw = _cquae_html(
                total=3,
                project_ids=(1, 2),
                next_href=invalid_next_url,
            )
            second_raw = _cquae_html(total=3, project_ids=(3,))
            responses = [
                _fetched(first_raw, source_url=_cquae_url()),
                _fetched(second_raw, source_url=invalid_next_url),
            ]
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = self._downloader(temp_dir)
                fetch = Mock(side_effect=responses)
                with patch.object(downloader, "_fetch_list_html", fetch):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_only_manifest(temp_dir)[1]["save_status"], "failed")

    def test_collect_rejects_empty_subset_and_drifted_source_configuration(self) -> None:
        authoritative = _cquae_list_sources("pre_disclosure")
        drifted = [
            CquaeListSource(
                label=authoritative[0].label,
                list_url=_cquae_url(
                    business_id="pre_disclosure",
                    source_index=0,
                    overrides={"projectID": 9},
                ),
            ),
            authoritative[1],
        ]
        cases = {
            "empty": [],
            "subset": authoritative[:1],
            "parameter-drift": drifted,
        }

        for label, sources in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                downloader = ChongqingProjectDownloader(
                    html_root=temp_dir,
                    page_size=2,
                    output_type=TYPE_PRE_DISCLOSURE,
                    list_sources=sources,
                    run_id=f"run-cquae-config-{label}",
                )
                fetch = Mock(side_effect=AssertionError("drifted config must not fetch"))
                with patch.object(downloader, "_fetch_list_html", fetch):
                    summary = downloader.run(start_date=None, end_date=None, list_only=True)

                fetch.assert_not_called()
                self.assertEqual(summary.detail_candidates, 0)
                self.assertTrue(summary.typed_errors)
                self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")
                self.assertIsNotNone(summary.discovery_task_manifest)

    def test_successful_html_traversal_archives_pages_and_completes_manifest(self) -> None:
        first_raw = _cquae_html(
            total=3,
            project_ids=(1, 2),
            next_href=_cquae_url(page=2),
        )
        second_raw = _cquae_html(total=3, project_ids=(3,))
        responses = [
            _fetched(
                first_raw,
                source_url=_cquae_url(),
                final_url=f"{_cquae_url()}&final=1",
            ),
            _fetched(
                second_raw,
                source_url=_cquae_url(page=2),
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", side_effect=responses):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            task_manifest = _task_manifest(temp_dir)
            raw_paths = sorted(manifest_path.parent.glob("page_*.raw.html"))
            sidecars = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(manifest_path.parent.glob("page_*.meta.json"))
            ]

            self.assertEqual(summary.detail_candidates, 3)
            self.assertEqual(summary.typed_errors, [])
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertTrue(
                Path(temp_dir, summary.discovery_task_manifest["path"]).is_file()
            )
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "declared_pages_exhausted")
            self.assertEqual(task_manifest["save_status"], "complete")
            self.assertEqual(task_manifest["expected_query_ids"], ["001-equity-formal"])
            self.assertEqual(task_manifest["candidate_count"], 3)
            self.assertEqual(
                len(task_manifest["candidate_fingerprints"]),
                len(summary.candidate_entries),
            )
            self.assertEqual([path.read_bytes() for path in raw_paths], [first_raw, second_raw])
            self.assertEqual([sidecar["parse_status"] for sidecar in sidecars], ["complete", "complete"])
            self.assertEqual(sidecars[0]["final_url"], responses[0].final_url)
            audit = audit_discovery_evidence_root(temp_dir)
            self.assertTrue(audit.ok, audit.to_dict())

    def test_malformed_html_keeps_raw_page_and_fails_page_then_query(self) -> None:
        raw_bytes = (
            "<html><head><title>项目中心- 重庆产权交易网</title></head>"
            "<body><div>共找到 2 条(项目)记录</div></body></html>"
        ).encode("utf-8")
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            task_manifest = _task_manifest(temp_dir)
            raw_path = next(manifest_path.parent.glob("page_*.raw.html"))
            sidecar_path = next(manifest_path.parent.glob("page_*.meta.json"))
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))

            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(raw_path.read_bytes(), raw_bytes)
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertIsNotNone(summary.discovery_task_manifest)

    def test_candidate_conversion_failure_cannot_leave_complete_task_manifest(self) -> None:
        raw_bytes = _cquae_html(total=1, project_ids=(1,))
        response = _fetched(raw_bytes, source_url=_cquae_url())

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with (
                patch.object(downloader, "_fetch_list_html", return_value=response),
                patch.object(
                    downloader,
                    "_list_rows_to_candidates",
                    side_effect=RuntimeError("candidate conversion stopped"),
                ),
            ):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            task_manifest = _task_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(task_manifest["candidate_count"], 0)

    def test_official_empty_html_query_completes_with_zero_rows(self) -> None:
        raw_bytes = _cquae_html(total=0, project_ids=())
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "official_empty")
            self.assertEqual(manifest["observed_row_count"], 0)
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "complete")

    def test_html_row_without_official_id_or_code_fails_page_and_query(self) -> None:
        raw_bytes = (
            "<html><head><title>项目中心- 重庆产权交易网</title></head><body>"
            "<div>共找到 1 条(项目)记录</div>"
            '<div class="n2_List itcon">'
            '<a class="P_List_A" href="/Project/Show">row without identity</a>'
            "</div></body></html>"
        ).encode("utf-8")
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            manifest_path, manifest = _only_manifest(temp_dir)
            sidecar = json.loads(
                next(manifest_path.parent.glob("page_*.meta.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_missing_total_short_page_completes_by_short_page_evidence(self) -> None:
        raw_bytes = _cquae_html(total=None, project_ids=(1,))
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(manifest["termination_reason"], "short_page")

    def test_missing_total_full_page_without_next_fails_closed(self) -> None:
        raw_bytes = _cquae_html(total=None, project_ids=(1, 2))
        response = _fetched(raw_bytes, source_url=_cquae_url())

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(_task_manifest(temp_dir)["save_status"], "failed")

    def test_repeated_html_page_fails_closed_without_accepting_candidates(self) -> None:
        first_raw = _cquae_html(
            total=4,
            project_ids=(1, 2),
            next_href=_cquae_url(page=2),
        )
        second_raw = _cquae_html(total=4, project_ids=(2, 3))
        responses = [
            _fetched(first_raw, source_url=_cquae_url()),
            _fetched(second_raw, source_url=_cquae_url(page=2)),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", side_effect=responses):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(any("repeated" in error.raw_reason for error in summary.typed_errors))
            self.assertEqual(manifest["save_status"], "failed")

    def test_declared_html_total_without_next_page_fails_closed(self) -> None:
        raw_bytes = _cquae_html(total=3, project_ids=(1, 2))
        response = _fetched(
            raw_bytes,
            source_url=_cquae_url(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = self._downloader(temp_dir)
            with patch.object(downloader, "_fetch_list_html", return_value=response):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            _manifest_path, manifest = _only_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(manifest["save_status"], "failed")

    def test_task_failure_discards_completed_source_when_an_expected_source_fails(self) -> None:
        sources = _cquae_list_sources("pre_disclosure")
        first_raw = _cquae_html(total=1, project_ids=(1,))
        first_response = _fetched(
            first_raw,
            source_url=sources[0].list_url,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = ChongqingProjectDownloader(
                html_root=temp_dir,
                page_size=2,
                output_type=TYPE_PRE_DISCLOSURE,
                list_sources=sources,
                run_id="run-cquae-task-failure",
            )
            with patch.object(
                downloader,
                "_fetch_list_html",
                side_effect=[first_response, RuntimeError("second source failed")],
            ):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            task_manifest = _task_manifest(temp_dir)
            self.assertEqual(summary.detail_candidates, 0)
            self.assertTrue(summary.typed_errors)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(
                task_manifest["expected_query_ids"],
                ["001-equity-pre", "002-capital-pre"],
            )


if __name__ == "__main__":
    unittest.main()
