"""Downloader capability and manifest models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DownloadDriverCapabilities:
    supports_list_only: bool = False
    supports_prefetched_candidates: bool = False


@dataclass(frozen=True)
class DownloadTaskManifest:
    source_id: str
    project_type: str
    task_id: str
    display_name: str
    list_endpoint: str = ""
    detail_route: str = ""
    date_field_candidates: tuple[str, ...] = field(default_factory=tuple)
