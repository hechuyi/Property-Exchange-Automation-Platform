from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from peap_core import SnapshotEnvelope


class SourceClassifierTest(unittest.TestCase):
    def test_classifier_returns_matched_source_match_for_known_html(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-known",
            document_kind="html",
            primary_text="北交互联",
            dom="""
            <html>
              <head>
                <title>北京产权交易所</title>
                <meta name='keywords' content='北交互联' />
              </head>
              <body><textarea id='jsonobj'>{}</textarea></body>
            </html>
            """,
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "beijing")
        self.assertEqual(match.page_kind, "listing")
        self.assertTrue(match.reasons)
        self.assertEqual(match.classifier_version, "source_classifier/v1")

    def test_classifier_raises_ambiguous_for_conflicting_markers(self) -> None:
        from peap_core import DecodedDocument
        from peap_core.error_contracts import PipelineFailure
        from peap_parsers.source_classifier import (
            classify_decoded_document,
            detect_source_from_content,
        )

        html = "<html><head><title>北京产权交易所 山东产权交易中心</title></head><body>冲突页面</body></html>"
        document = DecodedDocument(
            snapshot_id="snap-ambiguous",
            document_kind="html",
            primary_text="冲突标题",
            dom=html,
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        with self.assertRaises(PipelineFailure) as ctx:
            classify_decoded_document(document)

        self.assertEqual(ctx.exception.code, "ambiguous_source_match")
        self.assertEqual(ctx.exception.recoverability, "permanent")
        self.assertIn("conflicting_sources", ctx.exception.context)
        self.assertIsNone(detect_source_from_content(html))

    def test_classifier_raises_no_match_for_unmatched_html(self) -> None:
        from peap_core import DecodedDocument
        from peap_core.error_contracts import PipelineFailure
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-unknown",
            document_kind="html",
            primary_text="plain page",
            dom="<html><head><title>plain page</title></head><body>nothing useful</body></html>",
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        with self.assertRaises(PipelineFailure) as ctx:
            classify_decoded_document(document)

        self.assertEqual(ctx.exception.code, "no_source_match")
        self.assertEqual(ctx.exception.recoverability, "permanent")

    def test_classifier_recognizes_public_resource_mhtml_by_rules(self) -> None:
        from peap_parsers.snapshot_decoder import decode_snapshot
        from peap_parsers.source_classifier import classify_decoded_document

        outer_html = '<html><body><div id="div_0502"><iframe src="cid:inner-html"></iframe></div><div id="platformName">全国公共资源交易平台</div></body></html>'
        inner_html = '<html><body><div class="detail">项目编号 P001</div></body></html>'
        mhtml = b"\r\n".join(
            [
                b"From: <Saved by Blink>",
                b"MIME-Version: 1.0",
                b'Content-Type: multipart/related; boundary="BOUNDARY"',
                b"",
                b"--BOUNDARY",
                b'Content-Type: text/html; charset="utf-8"',
                b"Content-Location: https://www.ggzy.gov.cn/information/deal/html/outer.html",
                b"",
                outer_html.encode("utf-8"),
                b"--BOUNDARY",
                b'Content-Type: text/html; charset="utf-8"',
                b"Content-ID: <inner-html>",
                b"Content-Location: https://www.ggzy.gov.cn/information/deal/html/inner.html",
                b"",
                inner_html.encode("utf-8"),
                b"--BOUNDARY--",
                b"",
            ]
        )


        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "fixture.mhtml"
            file_path.write_bytes(mhtml)
            snapshot = SnapshotEnvelope(
                snapshot_id="snap-public-resource",
                captured_at="2026-03-31T10:00:00Z",
                source_url="https://www.ggzy.gov.cn/information/deal/html/outer.html",
                referrer_url="https://www.ggzy.gov.cn/",
                content_type="multipart/related",
                http_status=200,
                storage_path=str(file_path),
                digest="sha256:mhtml",
                fetch_metadata={"method": "GET"},
            )
            document = decode_snapshot(snapshot)

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "public_resource")
        self.assertEqual(match.page_kind, "deal")

    def test_public_resource_deal_identity_suppresses_embedded_guangdong_listing_markers(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-public-resource-with-guangdong-marker",
            document_kind="html",
            primary_text="成交公告 项目编号：G32026GD0000081 广东联合产权交易中心",
            dom="""
            <html>
              <head>
                <meta name="keywords" content="广东联合产权交易中心" />
                <script>window.TITLE = '广东联合产权交易中心';</script>
              </head>
              <body>成交公告 项目编号：G32026GD0000081</body>
            </html>
            """,
            metadata={
                "content_type": "text/html",
                "source_url": "https://www.ggzy.gov.cn/information/deal/html/2026/06/03/notice.html",
            },
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "public_resource")
        self.assertEqual(match.page_kind, "deal")

    def test_classifier_recognizes_canonical_deal_sources_across_four_exchanges(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        fixtures = (
            (
                "cbex",
                "https://www.cbex.com/xm/cqzr/cjjggs/2026/04/notice.html",
                "<html><body><textarea id='jsonobj'>{\"projectCode\":\"G32026BJ1000001\"}</textarea></body></html>",
            ),
            (
                "sse",
                "https://www.suaee.com/notice/deal/20260401/1.html",
                "<html><head><title>上海联合产权交易所 成交公告</title></head><body>成交结果</body></html>",
            ),
            (
                "tpre",
                "https://otc.tpre.cn/transaction/biz/transaction-management/anmuas/result-notice/details?id=1",
                "<html><head><title>天津产权交易中心 成交公告</title></head><body>result-notice</body></html>",
            ),
            (
                "cquae",
                "https://www.cquae.com/CquaeNews/cjgs/2026-04/001.html",
                "<html><head><title>重庆产权交易 成交公告</title></head><body>cjgs</body></html>",
            ),
        )

        for source_id, source_url, dom in fixtures:
            with self.subTest(source_id=source_id):
                document = DecodedDocument(
                    snapshot_id=f"snap-deal-{source_id}",
                    document_kind="html",
                    primary_text=dom,
                    dom=dom,
                    metadata={"source_url": source_url, "content_type": "text/html"},
                    decoder_version="snapshot_decoder/v1",
                )

                match = classify_decoded_document(document)

                self.assertEqual(match.status, "matched")
                self.assertEqual(match.source_id, source_id)
                self.assertEqual(match.page_kind, "deal")
                self.assertTrue(any("record_family=deal" in reason for reason in match.reasons))

    def test_classifier_recognizes_tpre_rendered_listing_without_source_url_metadata(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-tpre-rendered-listing",
            document_kind="html",
            primary_text="",
            dom="""
            <html>
              <head>
                <title>天津交易集团</title>
                <style>
                  @font-face { src: url(https://trade.tpre.cn/transaction-view/fonts/element-icons.woff); }
                </style>
              </head>
              <body>
                <div id="app"></div>
                <script src="https://trade.tpre.cn/transaction-view/js/app.js"></script>
                <!-- The rendered project table can be far beyond the classifier snippet. -->
                <template id="late-rendered-content">
                <table class="project">
                  <tr><th>项目编号</th><td>G32026TJ1000008</td></tr>
                  <tr><th>项目名称</th><td>天津市城科智能热力有限公司100%股权</td></tr>
                </table>
                </template>
              </body>
            </html>
            """,
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "tianjin")
        self.assertEqual(match.page_kind, "listing")

    def test_classifier_recognizes_guangdong_rendered_listing_with_project_title(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-guangdong-rendered-listing",
            document_kind="html",
            primary_text="",
            dom="""
            <html>
              <head>
                <title>北京大唐永盛科技发展有限公司25.56%股权</title>
                <meta name="keywords" content="广东联合产权交易中心" />
                <meta name="description" content="广东联合产权交易中心" />
                <script>
                  window.COMPANY = '广东联合产权交易中心有限责任公司';
                  window.TITLE = '广东联合产权交易中心';
                </script>
              </head>
              <body>
                <a href="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=158463">项目中心</a>
                <table>
                  <tr><th>项目编号</th><td>G32026GD0000081</td></tr>
                  <tr><th>项目名称</th><td>北京大唐永盛科技发展有限公司25.56%股权</td></tr>
                </table>
              </body>
            </html>
            """,
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "guangdong")
        self.assertEqual(match.page_kind, "listing")

    def test_classifier_recognizes_sse_notice_detail_endpoint_as_deal(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-sse-notice-detail-deal",
            document_kind="html",
            primary_text="上海联合产权交易所 成交公告",
            dom="<html><head><title>上海联合产权交易所 成交公告</title></head><body>成交结果</body></html>",
            metadata={
                "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?noticeId=202604010001",
                "content_type": "text/html",
            },
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "sse")
        self.assertEqual(match.page_kind, "deal")

    def test_deal_route_markers_are_derived_from_source_business_contract(self) -> None:
        from peap_core import DecodedDocument
        from peap_core.source_business_contract import list_source_business_requirements
        from peap_parsers.source_classifier import classify_decoded_document

        original_requirements = list_source_business_requirements()
        patched_requirements = [
            replace(item, detail_route="/contract-classifier/sse/deal-detail")
            if item.source_id == "sse"
            and item.record_family == "deal"
            and item.business_id == "deal_equity_transfer"
            else item
            for item in original_requirements
        ]
        document = DecodedDocument(
            snapshot_id="snap-sse-contract-route-deal",
            document_kind="html",
            primary_text="上海联合产权交易所 成交公告",
            dom="<html><head><title>上海联合产权交易所 成交公告</title></head><body>成交结果</body></html>",
            metadata={
                "source_url": "https://www.suaee.com/contract-classifier/sse/deal-detail?noticeId=202604010001",
                "content_type": "text/html",
            },
            decoder_version="snapshot_decoder/v1",
        )

        with patch(
            "peap_core.source_business_contract.list_source_business_requirements",
            return_value=patched_requirements,
        ):
            match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "sse")
        self.assertEqual(match.page_kind, "deal")

    def test_classifier_does_not_treat_listing_page_with_deal_link_as_deal(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-sse-listing-with-deal-link",
            document_kind="html",
            primary_text="上海联合产权交易所 挂牌项目",
            dom="""
            <html>
              <head><title>上海联合产权交易所 挂牌项目</title></head>
              <body>
                <div class="project_code">G32026SH1000001</div>
                <a href="https://www.suaee.com/si/notice/getNoticeDetail?noticeId=deal">历史成交公告</a>
              </body>
            </html>
            """,
            metadata={
                "source_url": "https://www.suaee.com/project/listing/G32026SH1000001.html",
                "content_type": "text/html",
            },
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "matched")
        self.assertEqual(match.source_id, "shanghai")
        self.assertEqual(match.page_kind, "listing")

    def test_classifier_treats_cbex_cjggs_textarea_page_as_deal_not_listing(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-cbex-deal-textarea",
            document_kind="html",
            primary_text="北交互联 成交结果公告",
            dom="""
            <html>
              <head><title>北京产权交易所 成交结果公告</title></head>
              <body>
                <textarea id="jsonobj">{"projectCode":"G32026BJ1000009","dealDate":"2026-04-12"}</textarea>
              </body>
            </html>
            """,
            metadata={
                "source_url": "https://www.cbex.com/xm/cqzr/cjjggs/2026/04/notice-9.html",
                "content_type": "text/html",
            },
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.source_id, "cbex")
        self.assertEqual(match.page_kind, "deal")
        self.assertTrue(any("record_family=deal" in reason for reason in match.reasons))

    def test_classifier_uses_cbex_structured_transaction_facts_without_url_or_sidecar(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        payload = {
            "utrzcemsproject": {
                "projectcode": "G62026BJ1000004",
                "tradedate": "2026-06-12",
                "tradevalue": "1000",
                "object": "中航材智慧空港（广州）科技有限公司",
            },
            "tradelist": {
                "utrzcemstrade": [
                    {
                        "investorname": "锦程智行（成都）智能技术有限公司",
                        "pertradevalue": "200",
                    }
                ]
            },
        }
        document = DecodedDocument(
            snapshot_id="neutral-archive-name",
            document_kind="html",
            primary_text="",
            dom=(
                "<html><body><textarea id='jsonobj'>"
                + json.dumps(payload, ensure_ascii=False)
                + "</textarea></body></html>"
            ),
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.source_id, "cbex")
        self.assertEqual(match.page_kind, "deal")
        self.assertTrue(any("structured transaction facts" in reason for reason in match.reasons))

    def test_classifier_keeps_cbex_capital_listing_payload_as_listing(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        payload = {
            "utrzcemsproject": {
                "projectcode": "G62026BJ1000004",
                "publishdate": "2026-02-12",
                "objectpricestart": "1000",
                "object": "中航材智慧空港（广州）科技有限公司",
            }
        }
        document = DecodedDocument(
            snapshot_id="neutral-listing-name",
            document_kind="html",
            primary_text="",
            dom=(
                "<html><body><textarea id='jsonobj'>"
                + json.dumps(payload, ensure_ascii=False)
                + "</textarea></body></html>"
            ),
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.source_id, "beijing")
        self.assertEqual(match.page_kind, "listing")


if __name__ == "__main__":
    unittest.main()
