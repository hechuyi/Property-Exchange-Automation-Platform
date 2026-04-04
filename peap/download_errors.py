"""Typed downloader error models and helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadError(Exception):
    error_code: str
    error_message: str
    stage: str
    failure_kind: str
    source_id: str = ""
    task_id: str = ""
    raw_reason: str = ""

    def __str__(self) -> str:
        return self.error_message

    def to_presenter_payload(self) -> dict[str, object]:
        details: dict[str, object] = {
            "source_id": self.source_id,
            "stage": self.stage,
            "failure_kind": self.failure_kind,
            "raw_reason": self.raw_reason,
        }
        if self.task_id:
            details["task_id"] = self.task_id
        return {
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_details": details,
        }



def _normalized_source_id(source_id: str) -> str:
    return str(source_id or "").strip().lower()



def _normalized_task_id(task_id: str) -> str:
    return str(task_id or "").strip().lower()



def _normalized_reason(raw_reason: str) -> str:
    return str(raw_reason or "").strip()



def collect_failed_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    normalized_source = _normalized_source_id(source_id)
    normalized_task_id = _normalized_task_id(task_id)
    normalized_reason = _normalized_reason(raw_reason)
    return DownloadError(
        error_code=f"{normalized_source}_collect_failed",
        error_message=f"{normalized_source}: collect-failed: {normalized_reason}",
        stage="prepare_tasks",
        failure_kind="collect",
        source_id=normalized_source,
        task_id=normalized_task_id,
        raw_reason=normalized_reason,
    )



def list_failed_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    normalized_source = _normalized_source_id(source_id)
    normalized_task_id = _normalized_task_id(task_id)
    normalized_reason = _normalized_reason(raw_reason)
    return DownloadError(
        error_code=f"{normalized_source}_list_failed",
        error_message=f"{normalized_source}: list-failed: {normalized_reason}",
        stage="prepare_tasks",
        failure_kind="list",
        source_id=normalized_source,
        task_id=normalized_task_id,
        raw_reason=normalized_reason,
    )



def execute_failed_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    normalized_source = _normalized_source_id(source_id)
    normalized_task_id = _normalized_task_id(task_id)
    normalized_reason = _normalized_reason(raw_reason)
    return DownloadError(
        error_code=f"{normalized_source}_execute_failed",
        error_message=f"{normalized_source}: execute-failed: {normalized_reason}",
        stage="save_pages",
        failure_kind="execute",
        source_id=normalized_source,
        task_id=normalized_task_id,
        raw_reason=normalized_reason,
    )



def save_failed_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    normalized_source = _normalized_source_id(source_id)
    normalized_task_id = _normalized_task_id(task_id)
    normalized_reason = _normalized_reason(raw_reason)
    return DownloadError(
        error_code=f"{normalized_source}_save_failed",
        error_message=f"{normalized_source}: save-failed: {normalized_reason}",
        stage="save_pages",
        failure_kind="save",
        source_id=normalized_source,
        task_id=normalized_task_id,
        raw_reason=normalized_reason,
    )



def invalid_candidate_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    normalized_source = _normalized_source_id(source_id)
    normalized_task_id = _normalized_task_id(task_id)
    normalized_reason = _normalized_reason(raw_reason)
    return DownloadError(
        error_code=f"{normalized_source}_invalid_candidate",
        error_message=f"{normalized_source}: invalid-candidate: {normalized_reason}",
        stage="prepare_tasks",
        failure_kind="validation",
        source_id=normalized_source,
        task_id=normalized_task_id,
        raw_reason=normalized_reason,
    )


__all__ = [
    "DownloadError",
    "collect_failed_error",
    "execute_failed_error",
    "invalid_candidate_error",
    "list_failed_error",
    "save_failed_error",
]
