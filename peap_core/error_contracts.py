"""Typed runtime failure contracts for the pipeline.

All runtime failures must expose these minimum fields:
- code: machine-readable error code
- component: which pipeline component raised the failure
- stage: which pipeline stage the failure occurred in
- recoverability: whether the failure is retryable
- message: human-readable error message
- context: additional structured context about the failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Recoverability = Literal["retryable", "permanent", "blocked"]


@dataclass(frozen=True)
class PipelineFailure:
    """Typed runtime failure contract.

    All pipeline failures (downloader, parser, normalizer, etc.) must expose
    these minimum fields so that error classification and recovery can work
    uniformly across the runtime.
    """

    code: str
    component: str
    stage: str
    recoverability: Recoverability
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "component": self.component,
            "stage": self.stage,
            "recoverability": self.recoverability,
            "message": self.message,
            "context": self.context,
        }
