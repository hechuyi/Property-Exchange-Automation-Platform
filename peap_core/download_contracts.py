"""Downloader manifest and materialization contracts.

These contracts define the types used across the download pipeline:
- DownloadTaskManifest: metadata about a download task
- DownloadDriverCapabilities: capabilities of a specific download driver
- DownloadCandidateEntry: an entry in the candidate list from a downloader
- DownloadArtifact: a downloaded artifact (file) from the downloader
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DownloadDriverCapabilities:
    """Capabilities of a download driver."""

    supports_list_only: bool = False
    supports_prefetched_candidates: bool = False


@dataclass(frozen=True)
class DownloadTaskManifest:
    """Manifest metadata for a download task."""

    source_id: str
    project_type: str
    task_id: str
    display_name: str
    list_endpoint: str = ""
    detail_route: str = ""
    date_field_candidates: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DownloadCandidateEntry:
    """A candidate entry from a downloader listing.

    Represents a single row/item from a downloader that may be
    materialied into a DownloadArtifact.
    """

    project_code: str
    source_id: str
    detail_key: str = ""
    date_field: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_code": self.project_code,
            "source_id": self.source_id,
            "detail_key": self.detail_key,
            "date_field": self.date_field,
            "extra": self.extra,
        }


@dataclass(frozen=True)
class DownloadArtifact:
    """A downloaded artifact from the downloader.

    Represents a file or data blob that was downloaded and saved
    by the downloader for a specific candidate entry.
    """

    source_id: str
    project_code: str
    file_path: str
    artifact_type: str = "html"
    size_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "project_code": self.project_code,
            "file_path": self.file_path,
            "artifact_type": self.artifact_type,
            "size_bytes": self.size_bytes,
            "extra": self.extra,
        }
