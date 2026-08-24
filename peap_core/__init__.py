"""Shared runtime helpers and parser-subsystem contracts for PEAP entrypoints and scripts."""

from .cli_support import read_summary_json, setup_cli_logger, write_summary_json
from .page_parse_contracts import Diagnostic, EvidenceRef, PageParseResult, SourceMatch
from .record_contracts import AssembledRecordCandidate, CanonicalRecord
from .runtime import (
    load_json_file,
    load_json_object,
    load_runtime_config,
    normalize_path,
    read_optional_json_object,
    resolve_path,
    resolve_runtime_config_file,
    write_json_file,
    write_json_file_atomic,
)
from .snapshot_contracts import DecodedDocument, SnapshotEnvelope

__all__ = [
    "AssembledRecordCandidate",
    "CanonicalRecord",
    "DecodedDocument",
    "Diagnostic",
    "EvidenceRef",
    "PageParseResult",
    "SnapshotEnvelope",
    "SourceMatch",
    "load_json_file",
    "load_json_object",
    "load_runtime_config",
    "normalize_path",
    "read_optional_json_object",
    "read_summary_json",
    "resolve_path",
    "resolve_runtime_config_file",
    "setup_cli_logger",
    "write_json_file_atomic",
    "write_json_file",
    "write_summary_json",
]
