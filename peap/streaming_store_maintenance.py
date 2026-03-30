"""Explicit maintenance orchestration for legacy store normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .streaming_store import StreamingStore


@dataclass(frozen=True)
class StreamingStoreMaintenanceSummary:
    skip_parse: dict[str, int]
    listing_dates: int
    required_mapping: dict[str, int]


def _has_changes(summary: dict[str, Any]) -> bool:
    return any(int(summary.get(key, 0)) > 0 for key in summary)


def run_streaming_store_maintenance(store: StreamingStore) -> StreamingStoreMaintenanceSummary:
    skip_parse = store.normalize_legacy_skip_parse_entries()
    if _has_changes(skip_parse):
        store.add_audit_entry("legacy_skip_parse_normalized", skip_parse)

    listing_dates = int(store.normalize_listing_dates() or 0)
    if listing_dates > 0:
        store.add_audit_entry("legacy_listing_dates_normalized", {"records": listing_dates})

    required_mapping = store.normalize_required_mapping_states()
    if _has_changes(required_mapping):
        store.add_audit_entry("legacy_required_mapping_normalized", required_mapping)

    return StreamingStoreMaintenanceSummary(
        skip_parse={key: int(value or 0) for key, value in skip_parse.items()},
        listing_dates=listing_dates,
        required_mapping={key: int(value or 0) for key, value in required_mapping.items()},
    )


__all__ = ["StreamingStoreMaintenanceSummary", "run_streaming_store_maintenance"]
