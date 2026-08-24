"""Downloader runtime helpers shared by orchestration entrypoints."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .download_capabilities import DownloadDriverCapabilities
from .download_tasks import DownloadTaskSpec
from .submission_layout import scoped_archive_root


@dataclass(frozen=True)
class DownloadDriverRuntime:
    downloader: object
    spec: DownloadTaskSpec
    capabilities: DownloadDriverCapabilities
    output_root: str = ""
    task_output_root: str = ""


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
        output_root=str(getattr(runtime, "output_root", "") or ""),
        task_output_root=str(getattr(runtime, "task_output_root", "") or ""),
    )


def _task_path_relative_to_output_root(runtime: DownloadDriverRuntime, path: str) -> str:
    output_root = os.path.abspath(runtime.output_root)
    task_output_root = os.path.abspath(runtime.task_output_root)
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("download runtime produced an empty task-relative path")

    if os.path.isabs(raw_path):
        absolute_path = os.path.abspath(raw_path)
    else:
        output_relative_path = os.path.abspath(os.path.join(output_root, raw_path))
        try:
            already_output_relative = (
                os.path.commonpath([task_output_root, output_relative_path])
                == task_output_root
            )
        except ValueError:
            already_output_relative = False
        absolute_path = (
            output_relative_path
            if already_output_relative
            else os.path.abspath(os.path.join(task_output_root, raw_path))
        )

    try:
        inside_task_root = (
            os.path.commonpath([task_output_root, absolute_path]) == task_output_root
        )
    except ValueError:
        inside_task_root = False
    if not inside_task_root:
        raise ValueError(
            "download runtime produced a path outside its scoped task output root: "
            f"{raw_path}"
        )
    _assert_runtime_path_contained(
        absolute_path,
        task_output_root,
        label="download runtime output",
        allow_root_symlink=False,
    )
    return os.path.relpath(absolute_path, output_root)


def _assert_runtime_path_contained(
    path: str,
    root: str,
    *,
    label: str,
    allow_root_symlink: bool = False,
) -> None:
    """Reject symlink-based escapes from a downloader's scoped output root."""

    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    if not allow_root_symlink and os.path.islink(root_abs):
        raise ValueError(f"{label} must not use symlinks: {root}")
    try:
        if os.path.commonpath((root_abs, path_abs)) != root_abs:
            raise ValueError(f"{label} is outside its scoped task root: {path}")
    except ValueError as exc:
        if str(exc).startswith(f"{label} is outside"):
            raise
        raise ValueError(f"{label} is on a different volume: {path}") from exc

    # Existing components, including the leaf, must be real files/directories
    # in the task root.  A symlink can otherwise satisfy the lexical check and
    # redirect a download or a manifest to an unrelated workspace.
    current = path_abs
    while True:
        if current != root_abs and os.path.lexists(current) and os.path.islink(current):
            raise ValueError(f"{label} must not use symlinks: {path}")
        if current == root_abs:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    root_real = os.path.realpath(root_abs)
    path_real = os.path.realpath(path_abs)
    try:
        if os.path.commonpath((root_real, path_real)) != root_real:
            raise ValueError(f"{label} resolves outside its scoped task root: {path}")
    except ValueError as exc:
        if str(exc).startswith(f"{label} resolves outside"):
            raise
        raise ValueError(f"{label} resolves on a different volume: {path}") from exc


def _normalize_summary_paths(runtime: DownloadDriverRuntime, summary: object) -> object:
    if (
        not runtime.output_root
        or not runtime.task_output_root
        or os.path.abspath(runtime.output_root) == os.path.abspath(runtime.task_output_root)
    ):
        return summary

    _assert_runtime_path_contained(
        runtime.task_output_root,
        runtime.output_root,
        label="download runtime task root",
        allow_root_symlink=True,
    )

    downloaded_this_run = getattr(summary, "downloaded_this_run", None)
    if isinstance(downloaded_this_run, set):
        summary.downloaded_this_run = {  # type: ignore[attr-defined]
            _task_path_relative_to_output_root(runtime, path)
            if isinstance(path, str)
            else path
            for path in downloaded_this_run
        }

    discovery_reference = getattr(summary, "discovery_task_manifest", None)
    if isinstance(discovery_reference, Mapping):
        reference_path = discovery_reference.get("path")
        if isinstance(reference_path, str) and reference_path.strip():
            normalized_reference = dict(discovery_reference)
            normalized_reference["path"] = _task_path_relative_to_output_root(
                runtime,
                reference_path,
            )
            summary.discovery_task_manifest = normalized_reference  # type: ignore[attr-defined]
    return summary


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
    task_output_root = scoped_archive_root(
        os.path.abspath(output_root),
        source_id=spec.exchange_code,
        record_family=spec.record_family,
        business_id=spec.business_id,
    )
    downloader_kwargs: dict[str, Any] = {
        "html_root": task_output_root,
        "page_size": resolved_page_size,
        "max_pages": getattr(args, "max_pages", None),
        "concurrency": max(1, int(getattr(args, "concurrency", 1))),
        "resume": resume_enabled,
        "save_json": getattr(args, "save_json", False),
        "logger": logger,
    }
    if spec.record_family == "listing":
        downloader_kwargs["run_id"] = str(getattr(args, "run_id", "") or "").strip() or None
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
        output_root=os.path.abspath(output_root),
        task_output_root=task_output_root,
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
        summary = runtime.downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=True,
            prefetched_candidates=prefetched_candidates,
        )
    elif prefetched_candidates is not None:
        if not supports_prefetched_candidates:
            raise ValueError(
                "run_download_driver received prefetched_candidates for a driver without prefetched_candidates support"
            )
        summary = runtime.downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=False,
            prefetched_candidates=prefetched_candidates,
        )
    else:
        summary = runtime.downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=False,
            prefetched_candidates=None,
        )
    return _normalize_summary_paths(runtime, summary)


__all__ = ["DownloadDriverRuntime", "build_download_driver", "run_download_driver"]
