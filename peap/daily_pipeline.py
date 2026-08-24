"""Compatibility facade for the streaming daily pipeline."""

from __future__ import annotations

from .streaming_daily_pipeline import (
    StreamingDailyPipelineRunResult,
    run_streaming_daily_pipeline,
)

# Keep the historical result name importable while the streaming result is the
# only execution contract.
DailyPipelineRunResult = StreamingDailyPipelineRunResult


def run_daily_pipeline(
    args: object,
    *,
    config_obj: object,
    emit_console: bool = True,
) -> StreamingDailyPipelineRunResult:
    """Run the current daily pipeline through the streaming implementation."""

    return run_streaming_daily_pipeline(
        args,
        config_obj=config_obj,
        emit_console=emit_console,
    )


__all__ = [
    "DailyPipelineRunResult",
    "StreamingDailyPipelineRunResult",
    "run_daily_pipeline",
]
