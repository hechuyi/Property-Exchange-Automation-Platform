from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from peap_core import SnapshotEnvelope


class SnapshotDecoderTest(unittest.TestCase):
    def test_decode_html_snapshot_returns_dom_text_and_metadata(self) -> None:
        from peap_parsers.snapshot_decoder import decode_snapshot

        html = """
        <html>
          <head><title>北京产权交易所</title></head>
          <body><div id='content'>挂牌公告正文</div></body>
        </html>
        """
        snapshot = SnapshotEnvelope(
            snapshot_id="snap-html",
            captured_at="2026-03-31T10:00:00Z",
            source_url="https://example.invalid/listing/1",
            referrer_url="https://example.invalid/listings",
            content_type="text/html",
            http_status=200,
            storage_path="/tmp/snap-html.html",
            digest="sha256:html",
            fetch_metadata={"method": "GET"},
        )

        document = decode_snapshot(snapshot, raw_content=html)

        self.assertEqual(document.snapshot_id, "snap-html")
        self.assertEqual(document.document_kind, "html")
        self.assertIn("挂牌公告正文", document.primary_text)
        self.assertIn("<div id=\"content\">挂牌公告正文</div>", document.dom)
        self.assertEqual(document.metadata["content_type"], "text/html")
        self.assertEqual(document.metadata["storage_path"], "/tmp/snap-html.html")
        self.assertEqual(document.decoder_version, "snapshot_decoder/v1")

    def test_decode_mhtml_snapshot_exposes_html_parts_without_parser_private_logic(self) -> None:
        from peap_parsers.snapshot_decoder import decode_snapshot

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
                snapshot_id="snap-mhtml",
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

        self.assertEqual(document.document_kind, "mhtml")
        self.assertIn("项目编号 P001", document.primary_text)
        self.assertIn("项目编号 P001", document.dom)
        self.assertEqual(document.metadata["part_count"], 2)
        self.assertEqual(document.metadata["html_parts"][1]["content_id"], "inner-html")
        self.assertIn("iframe", document.metadata["outer_html"])


if __name__ == "__main__":
    unittest.main()
