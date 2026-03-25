"""Shared downloader dataclasses for split planning and chunk state."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any


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


@dataclass
class TaskSplitPlan:
    chunks: list[DateChunk]
    candidate_entries: list[dict[str, object]]


@dataclass
class ChunkStateContext:
    path: str
    payload: dict[str, object]


@dataclass
class DownloadTaskRunResult:
    any_failure: bool
    totals: dict[str, int]
    errors: list[str]
    task_result: dict[str, Any] | None = None
    generated_plan: TaskSplitPlan | None = None


@dataclass
class DownloadRunResult:
    exit_code: int
    task_count: int
    aggregate_summary: dict[str, int]
    task_summaries: dict[str, dict[str, Any]]
    errors: list[str]
    any_failure: bool
