"""Downloader chunk state persistence helpers."""

from __future__ import annotations

import datetime as dt
import os
import re

from peap_core.runtime import load_json_file, write_json_file_atomic

from .download_models import ChunkStateContext, DateChunk


def _safe_state_token(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "na"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", raw)


def _chunk_key(chunk: DateChunk) -> str:
    return f"{chunk.start_str}..{chunk.end_str}"


def _chunks_signature(chunks: list[DateChunk]) -> str:
    parts = [f"{chunk.start_str}..{chunk.end_str}:{int(chunk.estimated_candidates)}" for chunk in chunks]
    return "|".join(parts)


def _coerce_int(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return int(value)
    return int(str(value))


def resolve_chunk_state_path(args: object, *, default_dir: str) -> str:
    explicit = str(getattr(args, "chunk_state_file", "") or "").strip()
    if explicit:
        return os.path.abspath(explicit)
    plan_file = str(getattr(args, "split_plan_file", "") or "").strip()
    if plan_file:
        return os.path.abspath(f"{plan_file}.state.json")
    stem = (
        "chunk_state_"
        f"{_safe_state_token(getattr(args, 'exchange', None))}_"
        f"{_safe_state_token(getattr(args, 'project_type', None))}_"
        f"{_safe_state_token(getattr(args, 'start_date', None))}_"
        f"{_safe_state_token(getattr(args, 'end_date', None))}.json"
    )
    return os.path.abspath(os.path.join(default_dir, stem))


def load_chunk_state(path: str) -> ChunkStateContext:
    payload: dict[str, object] = {"version": 1, "tasks": {}}
    if os.path.isfile(path):
        raw = load_json_file(path, encoding="utf-8-sig")
        if isinstance(raw, dict):
            payload = raw
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        payload["tasks"] = {}
        tasks = payload["tasks"]
    for task_raw in list(tasks.values()):
        if not isinstance(task_raw, dict):
            continue
        chunks_raw = task_raw.get("chunks")
        if not isinstance(chunks_raw, dict):
            continue
        for chunk_raw in list(chunks_raw.values()):
            if not isinstance(chunk_raw, dict):
                continue
            status = str(chunk_raw.get("status") or "").strip().lower()
            if status == "running":
                chunk_raw["status"] = "failed"
    return ChunkStateContext(path=path, payload=payload)


def save_chunk_state(ctx: ChunkStateContext) -> None:
    write_json_file_atomic(
        ctx.path,
        ctx.payload,
        encoding="utf-8",
        ensure_ascii=False,
        sort_keys=False,
    )


def prepare_task_chunk_state(
    ctx: ChunkStateContext,
    *,
    task_id: str,
    chunks: list[DateChunk],
) -> dict[str, object]:
    tasks = ctx.payload.setdefault("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
        ctx.payload["tasks"] = tasks

    expected_keys = [_chunk_key(chunk) for chunk in chunks]
    expected_signature = _chunks_signature(chunks)

    task_state_raw = tasks.get(task_id)
    if not isinstance(task_state_raw, dict):
        task_state_raw = {}
        tasks[task_id] = task_state_raw

    existing_chunks = task_state_raw.get("chunks")
    if not isinstance(existing_chunks, dict):
        existing_chunks = {}

    if str(task_state_raw.get("plan_signature") or "") != expected_signature:
        existing_chunks = {}

    prepared: dict[str, object] = {
        "plan_signature": expected_signature,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "chunks": {},
    }
    prepared_chunks = prepared["chunks"]
    if not isinstance(prepared_chunks, dict):
        prepared_chunks = {}
        prepared["chunks"] = prepared_chunks

    for key in expected_keys:
        existing = existing_chunks.get(key)
        if existing is None:
            prepared_chunks[key] = {
                "status": "pending",
                "attempts": 0,
                "updated_at": None,
                "last_error": None,
            }
        else:
            prepared_chunks[key] = dict(existing)

    tasks[task_id] = prepared
    return prepared


def get_chunk_state(task_state: dict[str, object], chunk: DateChunk) -> dict[str, object]:
    chunks = task_state.get("chunks")
    if not isinstance(chunks, dict):
        chunks = {}
        task_state["chunks"] = chunks
    key = _chunk_key(chunk)
    state = chunks.get(key)
    if not isinstance(state, dict):
        state = {"status": "pending", "attempts": 0}
        chunks[key] = state
    return state


def update_chunk_state(
    task_state: dict[str, object],
    chunk: DateChunk,
    *,
    status: str,
    error: str | None = None,
    increment_attempts: bool = False,
) -> None:
    state = get_chunk_state(task_state, chunk)
    if increment_attempts:
        state["attempts"] = _coerce_int(state.get("attempts")) + 1
    state["status"] = str(status)
    state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    if error:
        state["last_error"] = str(error)
    elif status == "done":
        state.pop("last_error", None)
    task_state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
