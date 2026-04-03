"""Downloader runtime helpers shared by orchestration entrypoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .download_capabilities import DownloadDriverCapabilities
from .download_tasks import DownloadTaskSpec


@dataclass(frozen=True)
class DownloadDriverRuntime:
    downloader: object
    spec: DownloadTaskSpec
    capabilities: DownloadDriverCapabilities


def _copy_capabilities(capabilities: object) -> DownloadDriverCapabilities:
    return DownloadDriverCapabilities(
        supports_list_only=bool(getattr(capabilities, "supports_list_only", False)),
        supports_prefetched_candidates=bool(
            getattr(capabilities, "supports_prefetched_candidates", False)
        ),
    )


def _require_download_runtime(runtime: object) -> DownloadDriverRuntime:
    downloader = getattr(runtime, "downloader", None)
    spec = getattr(runtime, "spec", None)
    capabilities = getattr(runtime, "capabilities", None)
    if downloader is None or spec is None or capabilities is None:
        raise ValueError("run_download_driver requires an explicit download runtime contract")
    if not isinstance(spec, DownloadTaskSpec):
        raise ValueError("run_download_driver requires an explicit download runtime contract")
    if not isinstance(downloader, spec.downloader_cls):
        raise ValueError("run_download_driver requires an explicit download runtime contract")
    return DownloadDriverRuntime(
        downloader=downloader,
        spec=spec,
        capabilities=_copy_capabilities(capabilities),
    )


def build_download_driver(
    spec: DownloadTaskSpec,
    *,
    args: object,
    output_root: str,
    logger: logging.Logger,
    resume_override: bool | None = None,
):
    page_size = getattr(args, "page_size", None)
    resolved_page_size = page_size if page_size is not None else spec.default_page_size
    resume_enabled = getattr(args, "resume", False) if resume_override is None else bool(resume_override)
    downloader_kwargs: dict[str, Any] = {
        "html_root": output_root,
        "page_size": resolved_page_size,
        "max_pages": getattr(args, "max_pages", None),
        "concurrency": max(1, int(getattr(args, "concurrency", 1))),
        "resume": resume_enabled,
        "save_json": getattr(args, "save_json", False),
        "logger": logger,
    }
    if spec.exchange_code == "sse":
        ca_bundle = str(getattr(args, "sse_ca_bundle", "") or "").strip() or None
        downloader_kwargs.update(
            {
                "ssl_verify": bool(getattr(args, "sse_ssl_verify", True)),
                "ssl_ca_bundle": ca_bundle,
            }
        )
    item_saved_callback = getattr(args, "item_saved_callback", None)
    if item_saved_callback is not None:
        downloader_kwargs["item_saved_callback"] = item_saved_callback
    downloader = spec.downloader_cls(**downloader_kwargs)
    return DownloadDriverRuntime(
        downloader=downloader,
        spec=spec,
        capabilities=_copy_capabilities(spec.capabilities),
    )


def run_download_driver(
    downloader,
    *,
    start_date: str | None,
    end_date: str | None,
    list_only: bool,
    prefetched_candidates: list[dict[str, object]] | None,
):
    runtime = _require_download_runtime(downloader)
    supports_list_only = runtime.capabilities.supports_list_only
    supports_prefetched_candidates = runtime.capabilities.supports_prefetched_candidates

    if list_only:
        if not supports_list_only:
            raise ValueError("run_download_driver received list_only=True for a driver without list_only support")
        if prefetched_candidates is not None and not supports_prefetched_candidates:
            raise ValueError(
                "run_download_driver received prefetched_candidates for a driver without prefetched_candidates support"
            )
        return runtime.downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=True,
            prefetched_candidates=prefetched_candidates,
        )

    if prefetched_candidates is not None:
        if not supports_prefetched_candidates:
            raise ValueError(
                "run_download_driver received prefetched_candidates for a driver without prefetched_candidates support"
            )
        return runtime.downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=False,
            prefetched_candidates=prefetched_candidates,
        )

    return runtime.downloader.run(
        start_date=start_date,
        end_date=end_date,
        list_only=False,
        prefetched_candidates=None,
    )


__all__ = ["DownloadDriverRuntime", "build_download_driver", "run_download_driver"]
