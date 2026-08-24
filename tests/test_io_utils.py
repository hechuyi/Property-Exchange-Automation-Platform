from __future__ import annotations

from peap.io_utils import read_text_with_fallback


def test_read_text_with_fallback_rejects_lossy_decode_instead_of_replacement_text(tmp_path) -> None:
    source = tmp_path / "broken.html"
    source.write_bytes(b"\x80\x80\x80")

    assert read_text_with_fallback(str(source)) is None
