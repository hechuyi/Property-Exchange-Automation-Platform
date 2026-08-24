#!/usr/bin/env python3
"""Report or explicitly apply revision-locked business reclassification repairs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_backend.process_lock import ProcessLock, database_lock_path
from peap.business_reclassification import (
    BusinessReclassificationRuntime,
    apply_business_reclassification_plan,
    build_business_reclassification_plan,
)
from peap.streaming_store import StreamingStore
from peap_core.runtime_paths import resolve_runtime_workspace_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report or apply historical business-classification repairs"
    )
    parser.add_argument("--app-home", required=True, help="Application workspace root")
    parser.add_argument(
        "--record-id",
        action="append",
        default=[],
        help="Restrict to one record id; may be repeated",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum repairable listing records to scan",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply all actionable repairs in the selected scope; default is report-only",
    )
    return parser


def _readonly_store(db_path: str) -> StreamingStore:
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"streaming database not found: {resolved}")
    return StreamingStore(f"{resolved.as_uri()}?mode=ro", auto_migrate=False)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = resolve_runtime_workspace_paths(app_home=args.app_home)
    record_ids = list(args.record_id or []) or None
    if not args.apply:
        runtime = BusinessReclassificationRuntime.for_store(
            _readonly_store(paths.streaming_db_path)
        )
        payload = build_business_reclassification_plan(
            runtime=runtime,
            record_ids=record_ids,
            limit=args.limit,
        )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    with ProcessLock(
        database_lock_path(paths.streaming_db_path),
        label="business reclassification repair",
    ):
        store = StreamingStore(paths.streaming_db_path, auto_migrate=False)
        runtime = BusinessReclassificationRuntime.for_store(store)
        exit_code, payload = apply_business_reclassification_plan(
            runtime=runtime,
            record_ids=record_ids,
            limit=args.limit,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
