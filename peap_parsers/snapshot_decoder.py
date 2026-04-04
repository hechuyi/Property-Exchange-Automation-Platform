from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Sequence

from bs4 import BeautifulSoup

from peap_core import DecodedDocument, SnapshotEnvelope

DECODER_VERSION = "snapshot_decoder/v1"


@dataclass(frozen=True)
class HtmlPart:
    content_id: str
    content_location: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {
            "content_id": self.content_id,
            "content_location": self.content_location,
            "text": self.text,
        }


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _normalize_content_id(value: object) -> str:
    return str(value or "").strip().strip("<>").strip()


def _decode_part_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    encodings = [part.get_content_charset(), "utf-8", "gb18030", "latin-1"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def load_html_parts_from_mhtml(file_path: str | Path) -> list[HtmlPart]:
    path = Path(file_path)
    with path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    html_parts: list[HtmlPart] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_type() != "text/html":
            continue
        html_parts.append(
            HtmlPart(
                content_id=_normalize_content_id(part.get("Content-ID")),
                content_location=_clean_text(part.get("Content-Location")),
                text=_decode_part_payload(part),
            )
        )
    return html_parts


def resolve_mhtml_result_html(html_parts: Sequence[HtmlPart]) -> tuple[str, str]:
    if not html_parts:
        raise ValueError("missing html parts")

    outer_html = html_parts[0].text
    part_by_cid = {part.content_id: part for part in html_parts if part.content_id}

    outer_soup = BeautifulSoup(outer_html, "html.parser")
    iframe = outer_soup.select_one("#div_0502 iframe") or outer_soup.find("iframe")
    if iframe is not None:
        src = _clean_text(iframe.get("src"))
        if src.lower().startswith("cid:"):
            cid = _normalize_content_id(src[4:])
            matched = part_by_cid.get(cid)
            if matched is not None:
                return outer_html, matched.text

    if len(html_parts) >= 2:
        return outer_html, html_parts[-1].text
    raise ValueError("missing result html part")


def _decode_text_content(raw_content: str | bytes) -> str:
    if isinstance(raw_content, str):
        return raw_content
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_content.decode("utf-8", errors="replace")


def _looks_like_mhtml(snapshot: SnapshotEnvelope, raw_content: str | bytes | None) -> bool:
    content_type = str(snapshot.content_type or "").lower()
    storage_path = str(snapshot.storage_path or "").lower()
    if "multipart/related" in content_type or storage_path.endswith((".mhtml", ".mht")):
        return True
    if raw_content is None:
        return False
    probe = raw_content.decode("latin-1", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
    lowered = probe.lower()
    return "mime-version:" in lowered and "content-type: multipart/related" in lowered


def _build_metadata(snapshot: SnapshotEnvelope, **extra: Any) -> dict[str, Any]:
    metadata = {
        "content_type": snapshot.content_type,
        "storage_path": snapshot.storage_path,
        "source_url": snapshot.source_url,
        "referrer_url": snapshot.referrer_url,
    }
    metadata.update(extra)
    return metadata


def decode_snapshot(snapshot: SnapshotEnvelope, *, raw_content: str | bytes | None = None) -> DecodedDocument:
    if _looks_like_mhtml(snapshot, raw_content):
        if raw_content is not None and isinstance(raw_content, bytes):
            path = Path(snapshot.storage_path)
            path.write_bytes(raw_content)
        html_parts = load_html_parts_from_mhtml(snapshot.storage_path)
        outer_html, inner_html = resolve_mhtml_result_html(html_parts)
        normalized_dom = str(BeautifulSoup(inner_html, "html.parser"))
        primary_text = BeautifulSoup(inner_html, "html.parser").get_text(" ", strip=True)
        return DecodedDocument(
            snapshot_id=snapshot.snapshot_id,
            document_kind="mhtml",
            primary_text=primary_text,
            dom=normalized_dom,
            metadata=_build_metadata(
                snapshot,
                part_count=len(html_parts),
                html_parts=tuple(part.to_dict() for part in html_parts),
                outer_html=outer_html,
            ),
            decoder_version=DECODER_VERSION,
        )

    if raw_content is None:
        raw_content = Path(snapshot.storage_path).read_text(encoding="utf-8")
    html = _decode_text_content(raw_content)
    normalized_dom = str(BeautifulSoup(html, "html.parser"))
    primary_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return DecodedDocument(
        snapshot_id=snapshot.snapshot_id,
        document_kind="html",
        primary_text=primary_text,
        dom=normalized_dom,
        metadata=_build_metadata(snapshot),
        decoder_version=DECODER_VERSION,
    )


__all__ = [
    "DECODER_VERSION",
    "HtmlPart",
    "decode_snapshot",
    "load_html_parts_from_mhtml",
    "resolve_mhtml_result_html",
]
