"""Record and job state enums for the pipeline.

These enums define the canonical states used throughout the pipeline:
- RecordState: the state of a record in the streaming pipeline
- JobStatus: the status of a download/ingest job
- JobStage: the current stage of a job in the pipeline

IMPORTANT: mapping_conflict and pending_review are states, NOT exceptions.
They represent normal workflow states that records can be in.
"""

from __future__ import annotations

from enum import Enum


class RecordState(str, Enum):
    """State of a record in the streaming pipeline.

    These are workflow states, NOT exceptions. A record in MAPPING_CONFLICT
    or PENDING_REVIEW state is not an error - it just needs human review.
    """

    READY = "ready"
    PENDING_MAPPING = "pending_mapping"
    MAPPING_CONFLICT = "mapping_conflict"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    PENDING_REVIEW = "pending_review"
    PARSED_FAILED = "parse_failed"
    POSTPROCESS_FAILED = "postprocess_failed"


class JobStatus(str, Enum):
    """Status of a download/ingest job."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class JobStage(str, Enum):
    """Stages in the download/ingest job pipeline."""

    DOWNLOADED = "downloaded"
    QUEUED_FOR_PARSE = "queued_for_parse"
    PREPARE_TASKS = "prepare_tasks"
    SAVE_PAGES = "save_pages"
    MANUAL_IMPORT_SCAN = "manual_import_scan"
    REPROCESSING = "reprocessing"
    REFRESH_HISTORY = "refresh_history"
    EXPORTING = "exporting"
    PARSED = "parsed"
    POSTPROCESSED = "postprocessed"
    PERSISTED = "persisted"
    SKIPPED = "skipped"
    FAILED = "failed"
