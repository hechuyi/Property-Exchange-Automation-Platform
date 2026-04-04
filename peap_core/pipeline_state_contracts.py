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
    """Status of a download/ingest job.

    These values must match exactly what StreamingStore persists.
    Canonical set:
      - starting: job created but worker has not yet entered pipeline
      - running: worker is actively processing the job
      - success: job completed normally
      - success_with_warnings: job completed but some items had non-fatal issues
      - failed: job terminated abnormally (startup crash, exception, etc.)
      - interrupted: job was explicitly cancelled/interrupted
    """

    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    SUCCESS_WITH_WARNINGS = "success_with_warnings"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobStage(str, Enum):
    """Stages in the download/ingest job pipeline.

    STARTUP: thread/bootstrap/runtime-init failures before download/parse/export begins.
    The rest are actual processing stages.
    FAILED is a job STATUS, not a stage - use JobStatus.FAILED for terminal failures.
    """

    STARTUP = "startup"
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
