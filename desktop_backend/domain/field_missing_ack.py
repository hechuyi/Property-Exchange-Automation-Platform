"""Backend import shim for the shared field_missing DTO contract."""

from __future__ import annotations

from peap_core.field_missing_contract import (
    build_field_missing_ack_payload,
    missing_fields_hash,
    normalize_missing_fields,
)

__all__ = [
    "build_field_missing_ack_payload",
    "missing_fields_hash",
    "normalize_missing_fields",
]
