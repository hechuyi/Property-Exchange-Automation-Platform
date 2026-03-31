from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def test_classifier_returns_ambiguous_for_conflicting_markers(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-ambiguous",
            document_kind="html",
            primary_text="冲突标题",
            dom="<html><head><title>北京产权交易所 山东产权交易中心</title></head><body>冲突页面</body></html>",
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "ambiguous")
        self.assertEqual(match.source_id, "")
        self.assertGreaterEqual(len(match.reasons), 2)

    def test_classifier_returns_unknown_for_unmatched_html(self) -> None:
        from peap_core import DecodedDocument
        from peap_parsers.source_classifier import classify_decoded_document

        document = DecodedDocument(
            snapshot_id="snap-unknown",
            document_kind="html",
            primary_text="plain page",
            dom="<html><head><title>plain page</title></head><body>nothing useful</body></html>",
            metadata={"content_type": "text/html"},
            decoder_version="snapshot_decoder/v1",
        )

        match = classify_decoded_document(document)

        self.assertEqual(match.status, "unknown")
        self.assertEqual(match.source_id, "")

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


if __name__ == "__main__":
    unittest.main()
