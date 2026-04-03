"""Shared downloader helpers and summary contract."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..constants import (
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..download_errors import DownloadError


@dataclass
class DownloadSummary:
    pages_requested: int = 0
    listed_items: int = 0
    detail_fetched: int = 0
    saved: int = 0
    skipped_by_list_date: int = 0
    skipped_by_detail_date: int = 0
    date_missing_skipped: int = 0
    skipped_by_resume: int = 0
    skipped_by_duplicate: int = 0
    skipped_by_missing_xmid: int = 0
    detail_candidates: int = 0
    detail_failed: int = 0
    list_unaccounted: int = 0
    detail_unaccounted: int = 0
    candidate_dates: list[str] = field(default_factory=list)
    candidate_entries: list[dict[str, Any]] = field(default_factory=list)
    typed_errors: list[DownloadError] = field(default_factory=list)



def parse_loose_date(value: Any) -> Optional[dt.date]:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raw_numeric = str(int(value)).strip()
        if len(raw_numeric) == 8 and raw_numeric.isdigit():
            try:
                return dt.datetime.strptime(raw_numeric, "%Y%m%d").date()
            except ValueError:
                return None
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return dt.datetime.utcfromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        if len(raw) == 8:
            try:
                return dt.datetime.strptime(raw, "%Y%m%d").date()
            except ValueError:
                return None
        try:
            return parse_loose_date(int(raw))
        except ValueError:
            return None

    raw = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", r"\1-\2-\3", raw)
    raw = raw.replace("/", "-").replace(".", "-")
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    if " " in raw:
        raw = raw.split(" ", 1)[0]

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        return dt.date(year, month, day)
    except ValueError:
        return None



def parse_bound(raw: Optional[str], name: str) -> Optional[dt.date]:
    if raw in (None, ""):
        return None
    parsed = parse_loose_date(raw)
    if parsed is None:
        raise ValueError(f"invalid {name}: {raw!r} (expected YYYY-MM-DD)")
    return parsed



def in_date_range(value: Optional[dt.date], start: Optional[dt.date], end: Optional[dt.date]) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True



def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(name or "").strip())
    return cleaned or "unknown"


def project_type_key(output_type: str) -> str:
    normalized = str(output_type or "").strip()
    mapping = {
        TYPE_EQUITY_TRANSFER: "equity_transfer",
        TYPE_PHYSICAL_ASSET: "physical_asset",
        TYPE_CAPITAL_INCREASE: "capital_increase",
        TYPE_PRE_DISCLOSURE: "pre_disclosure",
    }
    return mapping.get(normalized, "equity_transfer")


__all__ = [
    "DownloadSummary",
    "in_date_range",
    "parse_bound",
    "parse_loose_date",
    "project_type_key",
    "safe_filename",
]
