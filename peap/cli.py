"""CLI entrypoint for v2."""

import argparse
import json
from typing import Iterable, Optional

from .failure_repair import build_failure_repair_plan
from .operations_admin import build_data_health_summary


def _build_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PEAP maintenance CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data_health = subparsers.add_parser("data-health", help="Inspect schema and operation-journal health")
    data_health.add_argument("--app-home", required=True, help="Application home directory")

    repair_failures = subparsers.add_parser("repair-failures", help="Plan failed-record repair actions")
    repair_failures.add_argument("--app-home", required=True, help="Application home directory")
    repair_failures.add_argument("--state", action="append", default=[], help="Record state to include; may be repeated")
    repair_failures.add_argument("--record-id", action="append", default=[], help="Specific record id to include; may be repeated")
    repair_failures.add_argument("--limit", type=int, default=None, help="Maximum records to inspect")
    return parser


def _emit_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _run_admin_cli(argv: list[str]) -> int:
    parser = _build_admin_parser()
    args = parser.parse_args(argv)
    if args.command == "data-health":
        exit_code, payload = build_data_health_summary(app_home=args.app_home)
        _emit_json(payload)
        return exit_code
    if args.command == "repair-failures":
        exit_code, payload = 0, build_failure_repair_plan(
            app_home=args.app_home,
            states=args.state or None,
            record_ids=args.record_id or None,
            limit=args.limit,
        )
        _emit_json(payload)
        return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    import sys

    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    return _run_admin_cli(argv_list)


def build_parser(config_obj: object | None = None) -> argparse.ArgumentParser:
    _ = config_obj  # Kept for compatibility with callers that supplied parser config.
    return _build_admin_parser()


if __name__ == "__main__":
    raise SystemExit(main())
