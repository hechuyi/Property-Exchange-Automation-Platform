from __future__ import annotations

from peap.source_artifact_integrity import inspect_deal_source_artifact


def test_inspect_deal_source_artifact_rejects_invalid_utf8_source_as_invalid(tmp_path) -> None:
    source_file = tmp_path / "broken-source.html"
    source_file.write_bytes(b"<html>\x80</html>")

    issue = inspect_deal_source_artifact(
        source_file=str(source_file),
        source_id="sse",
        project_code="GR2026SH1000001",
    )

    assert issue is not None
    assert issue.error_type == "source_artifact_invalid"
    assert issue.evidence["reason_code"] == "source_artifact_decode_failed"
    assert issue.evidence["encoding"] == "utf-8"
