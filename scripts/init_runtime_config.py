#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create an external runtime config from the checked-in template."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable


def _add_project_root_to_syspath() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


_PROJECT_ROOT = _add_project_root_to_syspath()

from peap_core.runtime import load_json_object, normalize_path, write_json_file_atomic


def default_template_path() -> str:
    return os.path.join(_PROJECT_ROOT, "assets", "runtime_config.template.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an external runtime config from assets/runtime_config.template.json",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Target runtime config json file path",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="Optional data root override written into paths.data_root",
    )
    parser.add_argument(
        "--template",
        default=default_template_path(),
        help="Optional runtime config template path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output file if it already exists",
    )
    return parser


def write_runtime_config_from_template(
    *,
    output_path: str,
    template_path: str | None = None,
    data_root: str | None = None,
    force: bool = False,
) -> str:
    resolved_template = normalize_path(template_path or default_template_path())
    if not resolved_template or not os.path.isfile(resolved_template):
        raise FileNotFoundError(f"runtime config template not found: {resolved_template}")

    resolved_output = normalize_path(output_path)
    if not resolved_output:
        raise ValueError("output path is empty")
    if os.path.exists(resolved_output) and not force:
        raise FileExistsError(f"output file already exists: {resolved_output}")

    payload = load_json_object(
        resolved_template,
        encoding="utf-8-sig",
        label="runtime config template",
    )
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("runtime config template missing paths object")

    payload["paths"] = dict(paths)
    if data_root is not None and str(data_root).strip():
        payload["paths"]["data_root"] = normalize_path(data_root)

    return write_json_file_atomic(
        resolved_output,
        payload,
        encoding="utf-8",
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        output_path = write_runtime_config_from_template(
            output_path=args.output,
            template_path=args.template,
            data_root=args.data_root,
            force=bool(args.force),
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to create runtime config: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote runtime config: {output_path}")
    if args.data_root:
        print(f"Configured data_root: {normalize_path(args.data_root)}")
    print(f"Set PEAP_RUNTIME_CONFIG_FILE={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
