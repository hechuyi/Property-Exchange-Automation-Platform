"""Downloader summary formatting and aggregation helpers."""

from __future__ import annotations

from typing import Any

from .download_errors import DownloadError

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
    ("missing_xmid_skipped", "skipped_by_missing_xmid"),
    ("detail_candidates", "detail_candidates"),
    ("detail_failed", "detail_failed"),
    ("list_unaccounted", "list_unaccounted"),
    ("detail_unaccounted", "detail_unaccounted"),
)


def new_totals() -> dict[str, int]:
    return {field: 0 for field, _ in SUMMARY_FIELDS}


def summary_to_dict(summary: object) -> dict[str, int]:
    payload = {field: int(getattr(summary, attr, 0) or 0) for field, attr in SUMMARY_FIELDS}
    payload["errors"] = _summary_error_count(summary)
    return payload


def totals_to_summary_dict(totals: dict[str, int]) -> dict[str, int]:
    payload = {field: int(totals[field]) for field, _ in SUMMARY_FIELDS}
    return payload


def merge_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for field, _ in SUMMARY_FIELDS:
        target[field] += int(source.get(field, 0) or 0)


def accumulate(
    summary: object,
    totals: dict[str, int],
    total_typed_errors: list[DownloadError] | None = None,
) -> None:
    for field, attr in SUMMARY_FIELDS:
        totals[field] += int(getattr(summary, attr, 0) or 0)
    if total_typed_errors is not None:
        total_typed_errors.extend(getattr(summary, "typed_errors", []) or [])


def _display_errors(summary: object) -> list[str]:
    typed_errors = [item for item in list(getattr(summary, "typed_errors", []) or []) if isinstance(item, DownloadError)]
    return [item.error_message for item in typed_errors]


def _summary_error_count(summary: object) -> int:
    return len(_display_errors(summary))


def print_summary(prefix: str, summary: object, *, logger=None) -> None:
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
        f"missing_xmid_skipped={summary_dict['missing_xmid_skipped']}, "
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
        header = f"{prefix} errors (first 20):"
        print(header)
        if logger is not None:
            logger.warning(header)
        for error in errors[:20]:
            item = f"- {error}"
            print(item)
            if logger is not None:
                logger.warning(item)


def print_aggregate_summary(totals: dict[str, int], *, logger=None) -> None:
    message = (
        "=== Aggregate summary === "
        f"pages={totals['pages']}, "
        f"listed={totals['listed']}, "
        f"detail_fetched={totals['detail_fetched']}, "
        f"saved={totals['saved']}, "
        f"list_date_skipped={totals['list_date_skipped']}, "
        f"detail_date_skipped={totals['detail_date_skipped']}, "
        f"date_missing_skipped={totals['date_missing_skipped']}, "
        f"resume_skipped={totals['resume_skipped']}, "
        f"duplicate_skipped={totals['duplicate_skipped']}, "
        f"missing_xmid_skipped={totals['missing_xmid_skipped']}, "
        f"detail_candidates={totals['detail_candidates']}, "
        f"detail_failed={totals['detail_failed']}, "
        f"list_unaccounted={totals['list_unaccounted']}, "
        f"detail_unaccounted={totals['detail_unaccounted']}"
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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "display_name": display_name,
        "summary": summary,
    }
    raw_typed_errors = list(typed_errors or [])
    if raw_typed_errors:
        payload["typed_errors"] = raw_typed_errors
    if chunk_count is not None:
        payload["chunk_count"] = int(chunk_count)
    return payload
