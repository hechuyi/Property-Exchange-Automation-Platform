"""Compatibility wrapper for shared record identity helpers."""

from peap_core.record_identity import (
    FAILED_RECORD_STATES,
    build_identity_anchor,
    build_source_identity_payload,
    is_failed_record_state,
    pick_reprocess_evidence_path,
)

__all__ = [
    "FAILED_RECORD_STATES",
    "build_identity_anchor",
    "build_source_identity_payload",
    "is_failed_record_state",
    "pick_reprocess_evidence_path",
]
