from __future__ import annotations

import importlib
import unittest
from dataclasses import FrozenInstanceError

from bs4 import BeautifulSoup


class SnapshotContractsTest(unittest.TestCase):
    def _load_snapshot_contracts(self):
        try:
            return importlib.import_module("peap_core.snapshot_contracts")
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised during RED step
            self.fail(f"peap_core.snapshot_contracts is missing: {exc}")

    def test_snapshot_envelope_is_frozen_and_serializes_capture_metadata(self) -> None:
        contracts = self._load_snapshot_contracts()

        envelope = contracts.SnapshotEnvelope(
            snapshot_id="snap-001",
            captured_at="2026-03-30T10:00:00Z",
            source_url="https://example.invalid/listing/1",
            referrer_url="https://example.invalid/listings",
            content_type="text/html",
            http_status=200,
            storage_path="/tmp/snapshots/snap-001.html",
            digest="sha256:abc123",
            fetch_metadata={
                "method": "GET",
                "headers": {"accept": "text/html"},
            },
        )

        with self.assertRaises(FrozenInstanceError):
            envelope.snapshot_id = "snap-002"

        payload = envelope.to_dict()
        self.assertEqual(payload["snapshot_id"], "snap-001")
        self.assertEqual(payload["digest"], "sha256:abc123")
        self.assertEqual(payload["captured_at"], "2026-03-30T10:00:00Z")
        self.assertEqual(payload["storage_path"], "/tmp/snapshots/snap-001.html")
        self.assertEqual(payload["fetch_metadata"]["headers"]["accept"], "text/html")

    def test_decoded_document_round_trips_snapshot_scoped_decoded_shape(self) -> None:
        contracts = self._load_snapshot_contracts()
        peap_core = importlib.import_module("peap_core")

        document = contracts.DecodedDocument(
            snapshot_id="snap-001",
            document_kind="html",
            primary_text="挂牌公告正文",
            dom="<html><body>挂牌公告正文</body></html>",
            embedded_json=({"projectCode": "P001"},),
            links=("https://example.invalid/detail/1",),
            attachments=("https://example.invalid/files/notice.pdf",),
            metadata={"title": "挂牌公告"},
            decoder_version="decoder/v1",
        )

        payload = document.to_dict()
        self.assertEqual(payload["snapshot_id"], "snap-001")
        self.assertEqual(payload["document_kind"], "html")
        self.assertEqual(payload["primary_text"], "挂牌公告正文")
        self.assertEqual(payload["embedded_json"][0]["projectCode"], "P001")
        self.assertEqual(payload["links"], ["https://example.invalid/detail/1"])
        self.assertEqual(payload["attachments"], ["https://example.invalid/files/notice.pdf"])
        self.assertEqual(payload["metadata"]["title"], "挂牌公告")
        self.assertEqual(peap_core.SnapshotEnvelope, contracts.SnapshotEnvelope)
        self.assertEqual(peap_core.DecodedDocument, contracts.DecodedDocument)

    def test_decoded_document_normalizes_live_dom_objects_to_markup_text(self) -> None:
        contracts = self._load_snapshot_contracts()
        soup = BeautifulSoup("<html><body><div id='root'>ok</div></body></html>", "html.parser")

        document = contracts.DecodedDocument(
            snapshot_id="snap-002",
            document_kind="html",
            primary_text="ok",
            dom=soup,
        )

        payload = document.to_dict()
        self.assertIsInstance(document.dom, str)
        self.assertIn("<div id=\"root\">ok</div>", document.dom)
        self.assertEqual(payload["dom"], document.dom)


if __name__ == "__main__":
    unittest.main()
