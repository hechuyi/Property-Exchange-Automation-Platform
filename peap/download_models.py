"""Shared downloader dataclasses for split planning and chunk state."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .download_errors import DownloadError
from .download_reporting import new_totals


SPLIT_PLAN_UNRESOLVED_POLICY_SKIP = "skip_unresolved"


class TaskTypedErrorList(list[DownloadError]):
    """Explicit typed downloader error side-channel for task and run models."""


@dataclass(frozen=True)
class DateChunk:
    start: dt.date
    end: dt.date
    estimated_candidates: int

    @property
    def start_str(self) -> str:
        return self.start.isoformat()

    @property
    def end_str(self) -> str:
        return self.end.isoformat()

    @staticmethod
    def from_dict(payload: dict[str, object]) -> "DateChunk":
        start_raw = str(payload.get("start") or "")
        end_raw = str(payload.get("end") or "")
        estimated_raw = payload.get("estimated_candidates")
        if estimated_raw in (None, ""):
            estimated = 0
        elif isinstance(estimated_raw, (int, float, str, bytes, bytearray)):
            estimated = int(estimated_raw)
        else:
            estimated = int(str(estimated_raw))
        return DateChunk(
            start=dt.datetime.strptime(start_raw, "%Y-%m-%d").date(),
            end=dt.datetime.strptime(end_raw, "%Y-%m-%d").date(),
            estimated_candidates=estimated,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start_str,
            "end": self.end_str,
            "estimated_candidates": int(self.estimated_candidates),
        }


@dataclass(frozen=True)
class SplitPlanResolvedBasis:
    date_fields: tuple[str, ...]
    unresolved_candidate_policy: str

    @staticmethod
    def from_dict(payload: dict[str, object]) -> "SplitPlanResolvedBasis":
        if "date_fields" not in payload:
            raise ValueError("resolved_basis.date_fields is required")
        raw_fields = payload.get("date_fields")
        if not isinstance(raw_fields, (list, tuple)):
            raise ValueError("resolved_basis.date_fields must be a list")
        date_fields_values: list[str] = []
        for value in raw_fields:
            if not isinstance(value, str) or not value:
                raise ValueError("resolved_basis.date_fields must contain non-empty strings")
            date_fields_values.append(value)
        date_fields = tuple(date_fields_values)
        if not date_fields:
            raise ValueError("resolved_basis.date_fields must be a non-empty list")

        if "unresolved_candidate_policy" not in payload:
            raise ValueError("resolved_basis.unresolved_candidate_policy is required")
        policy = str(payload.get("unresolved_candidate_policy") or "")
        if not policy:
            raise ValueError("resolved_basis.unresolved_candidate_policy is required")
        if policy != SPLIT_PLAN_UNRESOLVED_POLICY_SKIP:
            raise ValueError(f"unsupported unresolved candidate policy: {policy}")
        return SplitPlanResolvedBasis(
            date_fields=date_fields,
            unresolved_candidate_policy=policy,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "date_fields": list(self.date_fields),
            "unresolved_candidate_policy": self.unresolved_candidate_policy,
        }


@dataclass
class TaskSplitPlan:
    chunks: list[DateChunk]
    candidate_entries: list[dict[str, object]]
    resolved_basis: SplitPlanResolvedBasis


@dataclass
class ChunkStateContext:
    path: str
    payload: dict[str, object]


@dataclass
class DownloadCollectResult:
    candidate_entries: list[dict[str, object]]
    typed_errors: TaskTypedErrorList = field(default_factory=TaskTypedErrorList)
    chunks: list[DateChunk] = field(default_factory=list)
    resolved_basis: SplitPlanResolvedBasis | None = None
    generated_plan: TaskSplitPlan | None = None
    any_failure: bool = False


@dataclass
class DownloadMaterializeResult:
    saved_count: int = 0
    typed_errors: TaskTypedErrorList = field(default_factory=TaskTypedErrorList)
    any_failure: bool = False
    totals: dict[str, int] = field(default_factory=new_totals)
    chunk_count: int = 0
    downloaded_this_run: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        current_saved = int(self.totals.get("saved", 0) or 0)
        if self.saved_count:
            self.totals["saved"] = int(self.saved_count)
        else:
            self.saved_count = current_saved

    def __bool__(self) -> bool:
        return self.any_failure


@dataclass
class DownloadTaskRunResult:
    any_failure: bool
    totals: dict[str, int]
    typed_errors: TaskTypedErrorList = field(default_factory=TaskTypedErrorList)
    task_result: dict[str, Any] | None = None
    generated_plan: TaskSplitPlan | None = None


@dataclass
class DownloadRunResult:
    exit_code: int
    task_count: int
    aggregate_summary: dict[str, int]
    task_summaries: dict[str, dict[str, Any]]
    typed_errors: TaskTypedErrorList = field(default_factory=TaskTypedErrorList)
    any_failure: bool = False
