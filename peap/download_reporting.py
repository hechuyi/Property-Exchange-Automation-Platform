"""Downloader summary formatting and aggregation helpers."""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Mapping
from typing import Any

from .download_errors import (
    DownloadError,
    duplicate_download_target_error,
    summary_failure_count_error,
    unaccounted_candidates_error,
)

SUMMARY_FIELDS = (
    ("pages", "pages_requested"),
    ("listed", "listed_items"),
    ("detail_fetched", "detail_fetched"),
    ("saved", "saved"),
    ("list_date_skipped", "skipped_by_list_date"),
    ("detail_date_skipped", "skipped_by_detail_date"),
    ("date_missing_skipped", "date_missing_skipped"),
    ("resume_skipped", "skipped_by_resume"),
    ("duplicate_skipped", "skipped_by_duplicate"),
    ("business_filter_skipped", "skipped_by_business_filter"),
    ("missing_xmid_skipped", "skipped_by_missing_xmid"),
    ("detail_unavailable_skipped", "skipped_by_detail_unavailable"),
    ("detail_candidates", "detail_candidates"),
    ("detail_failed", "detail_failed"),
    ("list_unaccounted", "list_unaccounted"),
    ("detail_unaccounted", "detail_unaccounted"),
)

SUMMARY_FAILURE_COUNT_FIELDS = ("detail_failed", "list_failed", "save_failed", "errors")
SUMMARY_UNACCOUNTED_COUNT_FIELDS = ("list_unaccounted", "detail_unaccounted")


def new_totals() -> dict[str, int]:
    return {field: 0 for field, _ in SUMMARY_FIELDS}


def summary_to_dict(summary: object, *, errors: int | None = None) -> dict[str, int]:
    payload = {field: int(getattr(summary, attr, 0) or 0) for field, attr in SUMMARY_FIELDS}
    payload["errors"] = _summary_error_count(summary) if errors is None else int(errors)
    return payload


def totals_to_summary_dict(totals: dict[str, int], *, errors: int | None = None) -> dict[str, int]:
    payload = {field: int(totals.get(field, 0) or 0) for field, _ in SUMMARY_FIELDS}
    payload["errors"] = int(totals.get("errors", 0) if errors is None else errors)
    return payload


def classify_terminal_download_summary(
    summary: dict[str, Any] | None,
    *,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, str]:
    """Classify zero-download terminal states that need operator diagnostics."""
    if summary is None:
        source: dict[str, Any] = {}
    elif isinstance(summary, Mapping):
        source = dict(summary)
    else:
        raise TypeError("summary must be a mapping or None")

    def _int(key: str) -> int:
        try:
            return int(source.get(key) or 0)
        except Exception:
            return 0

    listed = _int("listed")
    saved = _int("saved")
    detail_candidates = _int("detail_candidates")
    detail_unavailable_skipped = _int("detail_unavailable_skipped")
    detail_failed = _int("detail_failed")
    errors = _int("errors")
    list_date_skipped = _int("list_date_skipped")
    business_filter_skipped = _int("business_filter_skipped")
    duplicate_skipped = _int("duplicate_skipped")
    resume_skipped = _int("resume_skipped")
    missing_xmid_skipped = _int("missing_xmid_skipped")
    list_unaccounted = _int("list_unaccounted")
    date_range = ""
    if str(start_date or "").strip() or str(end_date or "").strip():
        date_range = f" {str(start_date or '').strip() or '开始日期不限'}..{str(end_date or '').strip() or '结束日期不限'}"

    if detail_unavailable_skipped > 0 and detail_failed <= 0 and errors <= 0:
        message = (
            f"有 {detail_unavailable_skipped} 个详情页由交易所返回官方不可用状态，已按详情不可用跳过；"
            "这不是下载器执行失败，但需要在结果中显式核对。"
        )
        return {
            "warning_code": "detail_pages_unavailable_skipped",
            "warning_message": message,
            "terminal_label": message,
        }

    if listed > 0 and saved == 0 and detail_candidates == 0:
        if list_date_skipped >= listed and list_unaccounted <= 0:
            message = (
                f"已列出 {listed} 条，{list_date_skipped} 条因披露日期不在{date_range or '所选日期范围'}被跳过，"
                "未生成详情下载任务；如目标日期较早，请提高最大页数或按月分段重试。"
            )
            return {
                "warning_code": "all_listed_rows_outside_date_range",
                "warning_message": message,
                "terminal_label": message,
            }
        if list_unaccounted > 0:
            message = f"已列出 {listed} 条，但有 {list_unaccounted} 条列表记录未被解释，未生成详情下载任务。"
            return {
                "warning_code": "listed_rows_unaccounted",
                "warning_message": message,
                "terminal_label": message,
            }
        accounted_without_candidates = (
            list_date_skipped
            + business_filter_skipped
            + duplicate_skipped
            + resume_skipped
            + missing_xmid_skipped
        )
        if accounted_without_candidates >= listed:
            parts = []
            if list_date_skipped > 0:
                parts.append(f"日期范围跳过 {list_date_skipped} 条")
            if business_filter_skipped > 0:
                parts.append(f"业务范围过滤 {business_filter_skipped} 条")
            if duplicate_skipped > 0:
                parts.append(f"重复跳过 {duplicate_skipped} 条")
            if resume_skipped > 0:
                parts.append(f"已存在跳过 {resume_skipped} 条")
            if missing_xmid_skipped > 0:
                parts.append(f"缺少项目编号跳过 {missing_xmid_skipped} 条")
            detail = "，".join(parts) if parts else "列表记录均已按规则跳过"
            message = f"已列出 {listed} 条，{detail}，未生成详情下载任务。"
            return {
                "warning_code": "listed_rows_accounted_without_candidates",
                "warning_message": message,
                "terminal_label": message,
            }

    return {}


def merge_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for field, _ in SUMMARY_FIELDS:
        target[field] += int(source.get(field, 0) or 0)


def _validate_download_errors(raw: object, *, name: str) -> list[DownloadError]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"{name} must be a list")
    for item in raw:
        if not isinstance(item, DownloadError):
            raise TypeError(f"{name} must contain DownloadError items")
    return raw


def summary_typed_errors(summary: object) -> list[DownloadError]:
    return _validate_download_errors(
        getattr(summary, "typed_errors", None),
        name="summary.typed_errors",
    )


def summary_downloaded_this_run(summary: object) -> set[str]:
    raw = getattr(summary, "downloaded_this_run", None)
    if raw is None:
        return set()
    if not isinstance(raw, set):
        raise TypeError("summary.downloaded_this_run must be a set")
    for item in raw:
        if not isinstance(item, str):
            raise TypeError("summary.downloaded_this_run must contain str items")
    return raw


def summary_list_page_observations(summary: object) -> list[dict[str, Any]]:
    raw = getattr(summary, "list_page_observations", None)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("summary.list_page_observations must be a list")
    observations: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise TypeError("summary.list_page_observations[*] must be a mapping")
        observations.append(dict(item))
    return observations


def validate_discovery_task_manifest_reference(
    value: object,
    *,
    name: str = "discovery_task_manifest",
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    identity: dict[str, str] = {}
    for key in ("source_id", "task_id", "run_id"):
        text = str(value.get(key) or "").strip()
        if not text:
            raise ValueError(f"{name} {key} must be a non-empty string")
        identity[key] = text
    path = str(value.get("path") or "").strip()
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or os.path.isabs(path)
        or ntpath.isabs(path)
    ):
        raise ValueError(f"{name} path must be a non-empty relative path")
    normalized_path = os.path.normpath(path)
    if (
        normalized_path in {"", ".", os.pardir}
        or normalized_path.startswith(os.pardir + os.sep)
        or normalized_path.startswith("../")
        or ntpath.splitdrive(normalized_path)[0]
    ):
        raise ValueError(f"{name} path must stay inside the output root")
    path_parts = tuple(part for part in normalized_path.split(os.sep) if part)
    if len(path_parts) < 3 or path_parts[-2:] != ("discovery", "task_manifest.json"):
        raise ValueError(f"{name} path must point to discovery/task_manifest.json")
    sha256 = str(value.get("sha256") or "").strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", sha256) is None:
        raise ValueError(f"{name} discovery manifest sha256 is invalid")
    byte_count = value.get("bytes")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
        raise ValueError(f"{name} bytes must be a positive integer")
    return {
        **identity,
        "path": normalized_path,
        "sha256": sha256,
        "bytes": byte_count,
    }


def summary_discovery_task_manifest(summary: object) -> dict[str, Any]:
    raw = getattr(summary, "discovery_task_manifest", None)
    if raw is None:
        return {}
    return validate_discovery_task_manifest_reference(
        raw,
        name="summary.discovery_task_manifest",
    )


def summary_duplicate_samples(summary: object) -> list[dict[str, Any]]:
    raw = getattr(summary, "duplicate_samples", None)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("summary.duplicate_samples must be a list")
    samples: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise TypeError("summary.duplicate_samples[*] must be a mapping")
        samples.append(dict(item))
    return samples


def summary_count_fields(summary: object, fields: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for attr in fields:
        try:
            value = int(getattr(summary, attr, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            counts[attr] = value
    return counts


def summary_failure_counts(summary: object) -> dict[str, int]:
    return summary_count_fields(summary, SUMMARY_FAILURE_COUNT_FIELDS)


def summary_unaccounted_counts(summary: object) -> dict[str, int]:
    return summary_count_fields(summary, SUMMARY_UNACCOUNTED_COUNT_FIELDS)


def summary_duplicate_download_target_error(
    summary: object,
    *,
    source_id: str,
    task_id: str,
) -> DownloadError | None:
    try:
        saved = int(getattr(summary, "saved", 0) or 0)
    except (TypeError, ValueError):
        saved = 0
    downloaded_this_run = summary_downloaded_this_run(summary)
    unique_targets = len(downloaded_this_run)
    if saved <= 0 or unique_targets <= 0 or saved <= unique_targets:
        return None
    return duplicate_download_target_error(
        source_id=source_id,
        task_id=task_id,
        raw_reason=f"saved={saved} unique_download_targets={unique_targets}",
    )


def append_synthetic_summary_failure_error(
    summary: object,
    *,
    source_id: str,
    task_id: str,
) -> DownloadError | None:
    typed_errors = summary_typed_errors(summary)
    if typed_errors:
        return None

    error = summary_duplicate_download_target_error(
        summary,
        source_id=source_id,
        task_id=task_id,
    )
    if error is None:
        unaccounted_counts = summary_unaccounted_counts(summary)
        if unaccounted_counts:
            error = unaccounted_candidates_error(
                source_id=source_id,
                task_id=task_id,
                raw_reason=(
                    f"list_unaccounted={unaccounted_counts.get('list_unaccounted', 0)} "
                    f"detail_unaccounted={unaccounted_counts.get('detail_unaccounted', 0)}"
                ),
            )
    if error is None:
        failure_counts = summary_failure_counts(summary)
        if not failure_counts:
            return None
        error = summary_failure_count_error(
            source_id=source_id,
            task_id=task_id,
            raw_reason=" ".join(f"{field}={value}" for field, value in sorted(failure_counts.items())),
        )

    raw = getattr(summary, "typed_errors", None)
    if raw is None:
        summary.typed_errors = []
        raw = summary.typed_errors
    if not isinstance(raw, list):
        raise TypeError("summary.typed_errors must be a list")
    raw.append(error)
    return error


def accumulate(
    summary: object,
    totals: dict[str, int],
    total_typed_errors: list[DownloadError] | None = None,
    downloaded_this_run: set[str] | None = None,
) -> None:
    typed_errors = summary_typed_errors(summary) if total_typed_errors is not None else []
    summary_downloads = summary_downloaded_this_run(summary) if downloaded_this_run is not None else set()
    for field, attr in SUMMARY_FIELDS:
        totals[field] += int(getattr(summary, attr, 0) or 0)
    if total_typed_errors is not None:
        total_typed_errors.extend(typed_errors)
    if downloaded_this_run is not None:
        downloaded_this_run.update(summary_downloads)


def _display_errors(summary: object) -> list[str]:
    return [item.error_message for item in summary_typed_errors(summary)]


def _summary_error_count(summary: object) -> int:
    return len(_display_errors(summary))


def print_summary(prefix: str, summary: object, *, logger=None, error_limit: int = 5) -> None:
    summary_dict = summary_to_dict(summary)
    message = (
        f"{prefix} "
        f"pages={summary_dict['pages']}, "
        f"listed={summary_dict['listed']}, "
        f"detail_fetched={summary_dict['detail_fetched']}, "
        f"saved={summary_dict['saved']}, "
        f"list_date_skipped={summary_dict['list_date_skipped']}, "
        f"detail_date_skipped={summary_dict['detail_date_skipped']}, "
        f"date_missing_skipped={summary_dict['date_missing_skipped']}, "
        f"resume_skipped={summary_dict['resume_skipped']}, "
        f"duplicate_skipped={summary_dict['duplicate_skipped']}, "
        f"business_filter_skipped={summary_dict['business_filter_skipped']}, "
        f"missing_xmid_skipped={summary_dict['missing_xmid_skipped']}, "
        f"detail_unavailable_skipped={summary_dict['detail_unavailable_skipped']}, "
        f"detail_candidates={summary_dict['detail_candidates']}, "
        f"detail_failed={summary_dict['detail_failed']}, "
        f"list_unaccounted={summary_dict['list_unaccounted']}, "
        f"detail_unaccounted={summary_dict['detail_unaccounted']}, "
        f"errors={summary_dict['errors']}"
    )
    print(message)
    if logger is not None:
        logger.info(message)
    errors = _display_errors(summary)
    if errors:
        display_limit = max(0, int(error_limit))
        displayed = errors[:display_limit]
        remaining = max(len(errors) - len(displayed), 0)
        suffix = f"; {remaining} more not shown" if remaining else ""
        header = f"{prefix} errors (first {len(displayed)} of {len(errors)}{suffix}):"
        print(header)
        if logger is not None:
            logger.warning(header)
        for error in displayed:
            item = f"- {error}"
            print(item)
            if logger is not None:
                logger.warning(item)


def print_aggregate_summary(totals: dict[str, int], *, logger=None) -> None:
    summary_dict = totals_to_summary_dict(totals)
    message = (
        "=== Aggregate summary === "
        f"pages={summary_dict['pages']}, "
        f"listed={summary_dict['listed']}, "
        f"detail_fetched={summary_dict['detail_fetched']}, "
        f"saved={summary_dict['saved']}, "
        f"list_date_skipped={summary_dict['list_date_skipped']}, "
        f"detail_date_skipped={summary_dict['detail_date_skipped']}, "
        f"date_missing_skipped={summary_dict['date_missing_skipped']}, "
        f"resume_skipped={summary_dict['resume_skipped']}, "
        f"duplicate_skipped={summary_dict['duplicate_skipped']}, "
        f"business_filter_skipped={summary_dict['business_filter_skipped']}, "
        f"missing_xmid_skipped={summary_dict['missing_xmid_skipped']}, "
        f"detail_unavailable_skipped={summary_dict['detail_unavailable_skipped']}, "
        f"detail_candidates={summary_dict['detail_candidates']}, "
        f"detail_failed={summary_dict['detail_failed']}, "
        f"list_unaccounted={summary_dict['list_unaccounted']}, "
        f"detail_unaccounted={summary_dict['detail_unaccounted']}, "
        f"errors={summary_dict['errors']}"
    )
    print(message)
    if logger is not None:
        logger.info(message)


def build_task_result(
    *,
    display_name: str,
    summary: dict[str, int],
    typed_errors: list[DownloadError] | None = None,
    chunk_count: int | None = None,
    new_downloads: list[str] | None = None,
    discovery_task_manifest: dict[str, Any] | None = None,
    list_page_observations: list[dict[str, Any]] | None = None,
    duplicate_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "display_name": display_name,
        "summary": summary,
    }
    if typed_errors is not None and not isinstance(typed_errors, list):
        raise TypeError("typed_errors must be a list")
    raw_typed_errors = _validate_download_errors(typed_errors, name="typed_errors")
    if raw_typed_errors:
        payload["typed_errors"] = raw_typed_errors
    if chunk_count is not None:
        payload["chunk_count"] = int(chunk_count)
    if new_downloads is not None:
        if not isinstance(new_downloads, list):
            raise TypeError("new_downloads must be a list or None")
        for item in new_downloads:
            if not isinstance(item, str):
                raise TypeError("new_downloads must contain str items")
        payload["new_downloads"] = sorted(new_downloads)
    if discovery_task_manifest is not None:
        payload["discovery_task_manifest"] = validate_discovery_task_manifest_reference(
            discovery_task_manifest,
        )
    if list_page_observations is not None:
        if not isinstance(list_page_observations, list):
            raise TypeError("list_page_observations must be a list or None")
        observations: list[dict[str, Any]] = []
        for item in list_page_observations:
            if not isinstance(item, Mapping):
                raise TypeError("list_page_observations must contain mapping items")
            observations.append(dict(item))
        if observations:
            payload["list_page_observations"] = observations
    if duplicate_samples is not None:
        if not isinstance(duplicate_samples, list):
            raise TypeError("duplicate_samples must be a list or None")
        samples: list[dict[str, Any]] = []
        for item in duplicate_samples:
            if not isinstance(item, Mapping):
                raise TypeError("duplicate_samples must contain mapping items")
            samples.append(dict(item))
        if samples:
            payload["duplicate_samples"] = samples
    return payload


def summary_metadata_to_dict(summary: object) -> dict[str, Any]:
    metadata = {
        "new_downloads": sorted(summary_downloaded_this_run(summary)),
    }
    observations = summary_list_page_observations(summary)
    if observations:
        metadata["list_page_observations"] = observations
    discovery_manifest = summary_discovery_task_manifest(summary)
    if discovery_manifest:
        metadata["discovery_task_manifest"] = discovery_manifest
    duplicate_samples = summary_duplicate_samples(summary)
    if duplicate_samples:
        metadata["duplicate_samples"] = duplicate_samples
    return metadata
