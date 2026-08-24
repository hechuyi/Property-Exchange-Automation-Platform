"""Parser for public-resource-platform equity transfer deal MHTML exports."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from peap_parsers.public_resource import parse_mhtml_file

from .output_contract import (
    KIND_PUBLIC_RESOURCE,
    PUBLIC_RESOURCE_OUTPUT_FILENAME,
    get_output_columns_for_kind,
)
from .output_mapping import map_standard_to_excel_payload
from .public_resource_attribution import normalize_public_resource_exchange
from .standard_model import build_standard_project

DEFAULT_INPUT_SUBDIR = "公共资源网四大交易所股权转让成交信息统计"
DEFAULT_OUTPUT_FILENAME = PUBLIC_RESOURCE_OUTPUT_FILENAME

OUTPUT_COLUMNS = get_output_columns_for_kind(KIND_PUBLIC_RESOURCE)


@dataclass(frozen=True)
class ParseFailure:
    file_path: str
    error: str


@dataclass(frozen=True)
class ParseSummary:
    input_dir: str
    output_file: str
    total_files: int
    success_count: int
    failed: List[ParseFailure]
    exchange_counts: Dict[str, int]


@dataclass(frozen=True)
class PublicResourceDealSettings:
    input_dir: str = ""
    output_file: str = ""


def build_public_resource_deal_settings(config_obj: object) -> PublicResourceDealSettings:
    return PublicResourceDealSettings(
        input_dir=os.path.join(str(config_obj.DATA_ROOT), "raw", "manual", DEFAULT_INPUT_SUBDIR),
        output_file=os.path.join(str(config_obj.OUTPUT_EXCEL_DIR), DEFAULT_OUTPUT_FILENAME),
    )


def _load_default_public_resource_config() -> object:
    from config import config as default_config

    return default_config


def _resolve_public_resource_deal_settings(
    settings: Optional[PublicResourceDealSettings] = None,
    *,
    config_obj: object | None = None,
) -> PublicResourceDealSettings:
    if settings is not None:
        return settings
    resolved_config = config_obj or _load_default_public_resource_config()
    return build_public_resource_deal_settings(resolved_config)


def default_input_dir(
    settings: Optional[PublicResourceDealSettings] = None,
    *,
    config_obj: object | None = None,
) -> str:
    return str(_resolve_public_resource_deal_settings(settings, config_obj=config_obj).input_dir)


def default_output_file(
    settings: Optional[PublicResourceDealSettings] = None,
    *,
    config_obj: object | None = None,
) -> str:
    return str(_resolve_public_resource_deal_settings(settings, config_obj=config_obj).output_file)


def _normalize_exchange(source_label: str, original_link: str, project_code: str) -> str:
    del project_code
    return normalize_public_resource_exchange(source_label, original_link)


def _normalize_output_row(raw_row: Dict[str, str]) -> Dict[str, str]:
    standard = build_standard_project(raw_row)
    mapped = map_standard_to_excel_payload(standard, DEFAULT_OUTPUT_FILENAME)
    return {column_name: str(mapped.get(column_name, "") or "") for column_name in OUTPUT_COLUMNS}


def build_workbook(
    input_dir: str = "",
    output_file: str = "",
    *,
    settings: Optional[PublicResourceDealSettings] = None,
    config_obj: object | None = None,
) -> ParseSummary:
    resolved_settings = _resolve_public_resource_deal_settings(settings, config_obj=config_obj)
    input_path = Path(os.path.abspath(str(input_dir or resolved_settings.input_dir or "").strip()))
    if not input_path.is_dir():
        raise FileNotFoundError(f"input dir not found: {input_path}")

    files = sorted(input_path.glob("*.mhtml"), key=lambda item: item.name)
    rows: List[Dict[str, str]] = []
    failures: List[ParseFailure] = []
    exchange_counts: Dict[str, int] = {}

    for file_path in files:
        try:
            raw_row = parse_mhtml_file(str(file_path))
            row = _normalize_output_row(raw_row)
        except Exception as exc:
            failures.append(ParseFailure(file_path=str(file_path), error=str(exc)))
            continue
        rows.append(row)
        exchange = row["交易所"]
        exchange_counts[exchange] = exchange_counts.get(exchange, 0) + 1

    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if not frame.empty:
        frame = frame.fillna("")
        frame = frame.astype(str)
        frame = frame.sort_values(
            by=["成交日期", "交易所", "项目编号"],
            kind="stable",
        ).reset_index(drop=True)

    output_path = Path(
        os.path.abspath(str(output_file or resolved_settings.output_file or "").strip())
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(output_path, index=False)

    return ParseSummary(
        input_dir=str(input_path),
        output_file=str(output_path),
        total_files=len(files),
        success_count=len(rows),
        failed=failures,
        exchange_counts=exchange_counts,
    )


def _build_arg_parser(
    *,
    settings: Optional[PublicResourceDealSettings] = None,
    config_obj: object | None = None,
) -> argparse.ArgumentParser:
    resolved_settings = _resolve_public_resource_deal_settings(settings, config_obj=config_obj)
    parser = argparse.ArgumentParser(
        description="Build equity-transfer deal workbook from public-resource-platform MHTML files."
    )
    parser.add_argument(
        "--input-dir",
        default=default_input_dir(resolved_settings),
        help="Directory containing public-resource-platform .mhtml files",
    )
    parser.add_argument(
        "--output-file",
        default=default_output_file(resolved_settings),
        help="Target xlsx file path",
    )
    return parser


def main(
    argv: Optional[Iterable[str]] = None,
    *,
    settings: Optional[PublicResourceDealSettings] = None,
    config_obj: object | None = None,
) -> int:
    parser = _build_arg_parser(settings=settings, config_obj=config_obj)
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary = build_workbook(
        input_dir=str(args.input_dir),
        output_file=str(args.output_file),
        settings=settings,
        config_obj=config_obj,
    )

    print(f"input_dir={summary.input_dir}")
    print(f"output_file={summary.output_file}")
    print(f"total_files={summary.total_files}")
    print(f"success_count={summary.success_count}")
    print(f"failed_count={len(summary.failed)}")
    if summary.exchange_counts:
        parts = [f"{name}:{count}" for name, count in sorted(summary.exchange_counts.items())]
        print(f"exchange_counts={', '.join(parts)}")
    if summary.failed:
        print("top_failures=")
        for item in summary.failed[:10]:
            print(f"- {item.file_path}: {item.error}")
        return 1
    return 0
