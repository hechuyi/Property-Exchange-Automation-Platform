"""Single source of truth for public desktop-backend HTTP error codes."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus


@dataclass(frozen=True)
class ErrorCodeSpec:
    code: str
    http_status: HTTPStatus
    description: str


ERROR_INVALID_INPUT = "invalid_input"
ERROR_INVALID_REQUEST = "invalid_request"
ERROR_INVALID_PATH_SELECTION_KIND = "invalid_path_selection_kind"
ERROR_LOCAL_PATH_REQUIRED = "local_path_required"
ERROR_LOCAL_PATH_PICKER_FAILED = "local_path_picker_failed"
ERROR_LOCAL_PATH_OPEN_FAILED = "local_path_open_failed"
ERROR_NOT_FOUND = "not_found"
ERROR_RECORD_ARTIFACT_NOT_FOUND = "record_artifact_not_found"
ERROR_RECORD_ARTIFACT_OPEN_FAILED = "record_artifact_open_failed"
ERROR_MUTATING_JOB_IN_PROGRESS = "mutating_job_in_progress"
ERROR_BROWSER_RUNTIME_MISSING = "browser_runtime_missing"
ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND = "manual_import_input_dir_not_found"
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_INTERNAL_ERROR = "internal_error"
ERROR_STATE_CONFLICT = "state_conflict"
ERROR_DEPENDENCY_NOT_READY = "dependency_not_ready"
ERROR_SCHEMA_NOT_READY = "schema_not_ready"
ERROR_PRODUCT_ERROR = "product_error"


PUBLIC_ERROR_CODE_REGISTRY: dict[str, ErrorCodeSpec] = {
    ERROR_INVALID_INPUT: ErrorCodeSpec(ERROR_INVALID_INPUT, HTTPStatus.BAD_REQUEST, "Request input is malformed."),
    ERROR_INVALID_REQUEST: ErrorCodeSpec(ERROR_INVALID_REQUEST, HTTPStatus.BAD_REQUEST, "Request is not allowed in current business context."),
    ERROR_INVALID_PATH_SELECTION_KIND: ErrorCodeSpec(ERROR_INVALID_PATH_SELECTION_KIND, HTTPStatus.BAD_REQUEST, "Path picker kind is unsupported."),
    ERROR_LOCAL_PATH_REQUIRED: ErrorCodeSpec(ERROR_LOCAL_PATH_REQUIRED, HTTPStatus.BAD_REQUEST, "A local filesystem path is required."),
    ERROR_LOCAL_PATH_PICKER_FAILED: ErrorCodeSpec(ERROR_LOCAL_PATH_PICKER_FAILED, HTTPStatus.INTERNAL_SERVER_ERROR, "Local path picker failed."),
    ERROR_LOCAL_PATH_OPEN_FAILED: ErrorCodeSpec(ERROR_LOCAL_PATH_OPEN_FAILED, HTTPStatus.BAD_REQUEST, "Opening a local path failed."),
    ERROR_NOT_FOUND: ErrorCodeSpec(ERROR_NOT_FOUND, HTTPStatus.NOT_FOUND, "Requested resource was not found."),
    ERROR_RECORD_ARTIFACT_NOT_FOUND: ErrorCodeSpec(ERROR_RECORD_ARTIFACT_NOT_FOUND, HTTPStatus.NOT_FOUND, "Record artifact file was not found."),
    ERROR_RECORD_ARTIFACT_OPEN_FAILED: ErrorCodeSpec(ERROR_RECORD_ARTIFACT_OPEN_FAILED, HTTPStatus.BAD_REQUEST, "Record artifact could not be opened."),
    ERROR_MUTATING_JOB_IN_PROGRESS: ErrorCodeSpec(ERROR_MUTATING_JOB_IN_PROGRESS, HTTPStatus.CONFLICT, "Another mutating job is already running."),
    ERROR_BROWSER_RUNTIME_MISSING: ErrorCodeSpec(ERROR_BROWSER_RUNTIME_MISSING, HTTPStatus.CONFLICT, "Browser runtime is not ready."),
    ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND: ErrorCodeSpec(ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND, HTTPStatus.BAD_REQUEST, "Manual import input directory does not exist."),
    ERROR_UNAUTHORIZED: ErrorCodeSpec(ERROR_UNAUTHORIZED, HTTPStatus.UNAUTHORIZED, "Desktop API token is missing or invalid."),
    ERROR_INTERNAL_ERROR: ErrorCodeSpec(ERROR_INTERNAL_ERROR, HTTPStatus.INTERNAL_SERVER_ERROR, "Unhandled internal server error."),
    ERROR_STATE_CONFLICT: ErrorCodeSpec(ERROR_STATE_CONFLICT, HTTPStatus.CONFLICT, "Product state conflicts with requested action."),
    ERROR_DEPENDENCY_NOT_READY: ErrorCodeSpec(ERROR_DEPENDENCY_NOT_READY, HTTPStatus.SERVICE_UNAVAILABLE, "A required dependency is not ready."),
    ERROR_SCHEMA_NOT_READY: ErrorCodeSpec(ERROR_SCHEMA_NOT_READY, HTTPStatus.SERVICE_UNAVAILABLE, "Database schema has not been initialized."),
    ERROR_PRODUCT_ERROR: ErrorCodeSpec(ERROR_PRODUCT_ERROR, HTTPStatus.INTERNAL_SERVER_ERROR, "Generic product-level failure."),
}
