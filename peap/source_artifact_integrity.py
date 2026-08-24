"""Source artifact integrity checks used before canonical/export trust."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceArtifactIssue:
    error_type: str
    message: str
    evidence: dict[str, Any]


def inspect_deal_source_artifact(
    *,
    source_file: str,
    source_id: str,
    project_code: str = "",
) -> SourceArtifactIssue | None:
    path = str(source_file or "").strip()
    if not path or not os.path.isfile(path):
        return SourceArtifactIssue(
            error_type="source_artifact_missing",
            message="Deal source artifact is missing",
            evidence={
                "reason_code": "source_artifact_missing",
                "source_file": path,
                "project_code": str(project_code or "").strip(),
            },
        )

    read_result = read_deal_source_artifact_text(
        source_file=path,
        project_code=project_code,
        max_chars=8192,
    )
    if isinstance(read_result, SourceArtifactIssue):
        return read_result
    head = read_result

    synthetic_markers = {
        "sse": "SSE Deal Notice",
        "上交所": "SSE Deal Notice",
        "cbex": "CBEX Deal Notice",
        "北交所": "CBEX Deal Notice",
        "cquae": "CQUAE Deal Notice",
        "重交所": "CQUAE Deal Notice",
        "tpre": "TPRE Deal Notice",
        "天交所": "TPRE Deal Notice",
    }
    normalized_source = str(source_id or "").strip().lower()
    marker = synthetic_markers.get(normalized_source)
    if marker and marker in head:
        return SourceArtifactIssue(
            error_type="source_artifact_invalid",
            message="Deal artifact is a synthetic shell, not a rendered original page",
            evidence={
                "reason_code": "source_artifact_invalid",
                "source_file": path,
                "project_code": str(project_code or "").strip(),
                "detector": f"{normalized_source}_deal_notice_synthetic_shell",
            },
        )
    return None


def read_deal_source_artifact_text(
    *,
    source_file: str,
    project_code: str = "",
    max_chars: int | None = None,
) -> str | SourceArtifactIssue:
    path = str(source_file or "").strip()
    if not path or not os.path.isfile(path):
        return SourceArtifactIssue(
            error_type="source_artifact_missing",
            message="Deal source artifact is missing",
            evidence={
                "reason_code": "source_artifact_missing",
                "source_file": path,
                "project_code": str(project_code or "").strip(),
            },
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read() if max_chars is None else handle.read(max_chars)
    except UnicodeDecodeError as exc:
        return SourceArtifactIssue(
            error_type="source_artifact_invalid",
            message="Deal source artifact is not valid UTF-8",
            evidence={
                "reason_code": "source_artifact_decode_failed",
                "source_file": path,
                "project_code": str(project_code or "").strip(),
                "encoding": "utf-8",
                "decode_error": str(exc),
            },
        )
    except OSError as exc:
        return SourceArtifactIssue(
            error_type="source_artifact_missing",
            message="Deal source artifact cannot be read",
            evidence={
                "reason_code": "source_artifact_missing",
                "source_file": path,
                "project_code": str(project_code or "").strip(),
                "os_error": str(exc),
            },
        )


def source_artifact_issue_finding(issue: SourceArtifactIssue) -> dict[str, Any]:
    return {
        "severity": "error",
        "type": issue.error_type,
        "message": issue.message,
        "evidence": dict(issue.evidence),
    }


__all__ = [
    "SourceArtifactIssue",
    "inspect_deal_source_artifact",
    "read_deal_source_artifact_text",
    "source_artifact_issue_finding",
]
