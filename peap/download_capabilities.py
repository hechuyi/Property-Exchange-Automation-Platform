"""Downloader capability and manifest models.

This module re-exports types from peap_core.download_contracts for backward
compatibility. New code should import directly from peap_core.download_contracts.
"""

from __future__ import annotations

from peap_core.download_contracts import DownloadDriverCapabilities, DownloadTaskManifest

__all__ = ["DownloadDriverCapabilities", "DownloadTaskManifest"]
