"""Parser for public-resource-platform equity transfer deal MHTML exports."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

from .output_contract import (
    KIND_PUBLIC_RESOURCE,
    PUBLIC_RESOURCE_OUTPUT_FILENAME,
    get_output_columns_for_kind,
)
from .output_mapping import map_standard_to_excel_payload
from .standard_model import build_standard_project

DEFAULT_INPUT_SUBDIR = "公共资源网四大交易所股权转让成交信息统计"
DEFAULT_OUTPUT_FILENAME = PUBLIC_RESOURCE_OUTPUT_FILENAME

OUTPUT_COLUMNS = get_output_columns_for_kind(KIND_PUBLIC_RESOURCE)

EXPECTED_TABLE_LABELS = {
    "项目编号",
    "项目名称",
    "交易方式",
    "受让方名称",
    "转让标的评估值或账面净值",
    "成交金额",
    "成交日期",
}


@dataclass(frozen=True)
class HtmlPart:
    content_id: str
    content_location: str
    text: str


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
    resolved_config = config_obj or _load_default_public_resource_config()
    return settings or build_public_resource_deal_settings(resolved_config)


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


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _normalize_trade_date(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    if " " in text:
        text = text.split(" ", 1)[0]
    if "T" in text:
        text = text.split("T", 1)[0]

    text = (
        text.replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace(".", "/")
        .replace("-", "/")
    )

    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}/{text[4:6]}/{text[6:8]}"

    match = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}/{int(m):02d}/{int(d):02d}"

    return text


def _normalize_content_id(value: object) -> str:
    return str(value or "").strip().strip("<>").strip()


def _decode_part_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    candidate_encodings = [
        part.get_content_charset(),
        "utf-8",
        "gb18030",
        "latin-1",
    ]
    for encoding in candidate_encodings:
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _load_html_parts(file_path: Path) -> List[HtmlPart]:
    with file_path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)
    html_parts: List[HtmlPart] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_type() != "text/html":
            continue
        html_parts.append(
            HtmlPart(
                content_id=_normalize_content_id(part.get("Content-ID")),
                content_location=_clean_text(part.get("Content-Location")),
                text=_decode_part_payload(part),
            )
        )
    return html_parts


def _resolve_result_html(html_parts: Sequence[HtmlPart]) -> tuple[str, str]:
    if not html_parts:
        raise ValueError("missing html parts")

    outer_html = html_parts[0].text
    part_by_cid = {part.content_id: part for part in html_parts if part.content_id}

    outer_soup = BeautifulSoup(outer_html, "html.parser")
    iframe = outer_soup.select_one("#div_0502 iframe") or outer_soup.find("iframe")
    if iframe is not None:
        src = _clean_text(iframe.get("src"))
        if src.lower().startswith("cid:"):
            cid = _normalize_content_id(src[4:])
            matched = part_by_cid.get(cid)
            if matched is not None:
                return outer_html, matched.text

    if len(html_parts) >= 2:
        return outer_html, html_parts[-1].text

    raise ValueError("missing result html part")


def _extract_source_label(outer_html: str) -> str:
    soup = BeautifulSoup(outer_html, "html.parser")
    node = soup.select_one("#platformName")
    if node is not None:
        return _clean_text(node.get_text(" ", strip=True))
    return ""


def _extract_original_link(inner_soup: BeautifulSoup) -> str:
    for anchor in inner_soup.find_all("a", href=True):
        href = _clean_text(anchor.get("href"))
        if href:
            return href
    return ""


def _normalize_exchange(source_label: str, original_link: str, project_code: str) -> str:
    source = _clean_text(source_label)
    code = _clean_text(project_code).upper()
    host = (urlparse(original_link).netloc or "").lower()

    if "cbex.com" in host or "北交互联" in source:
        return "北交互联"
    if "shggzy.com" in host or "suaee.com" in host or "上海联合产权交易所" in source:
        return "上海联合产权交易所"
    if "tpre.cn" in host or source == "天津市公共资源交易平台交易系统" or "TJ" in code:
        return "天津产权交易中心"
    if "ygp.gdzwfw.gov.cn" in host or "深圳联合产权交易所" in source:
        return "深圳联合产权交易所"
    if (
        "cquae.com" in host
        or source == "重庆市"
        or code.startswith("CQ")
        or code.startswith("N0")
        or code.isdigit()
    ):
        return "重庆联合产权交易所"
    return source or host


def _extract_table_rows(inner_html: str) -> Dict[str, str]:
    soup = BeautifulSoup(inner_html, "html.parser")
    table = soup.find("table", class_="detail_Table")
    if table is None:
        raise ValueError("detail_Table not found")

    row_map: Dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = [
            _clean_text(cell.get_text(" ", strip=True))
            for cell in tr.find_all(["th", "td"])
        ]
        if len(cells) < 2:
            continue
        row_map[cells[0]] = cells[1]

    missing_labels = sorted(EXPECTED_TABLE_LABELS - set(row_map))
    if missing_labels:
        raise ValueError(f"missing table labels: {missing_labels}")
    return row_map


def parse_mhtml_file(file_path: str) -> Dict[str, str]:
    path = Path(file_path)
    html_parts = _load_html_parts(path)
    outer_html, inner_html = _resolve_result_html(html_parts)

    source_label = _extract_source_label(outer_html)
    inner_soup = BeautifulSoup(inner_html, "html.parser")
    original_link = _extract_original_link(inner_soup)
    row_map = _extract_table_rows(inner_html)
    exchange = _normalize_exchange(
        source_label=source_label,
        original_link=original_link,
        project_code=row_map["项目编号"],
    )

    return {
        "交易所": exchange,
        "项目编号": row_map["项目编号"],
        "项目名称": row_map["项目名称"],
        "交易方式": row_map["交易方式"],
        "受让方名称": row_map["受让方名称"],
        "转让标的评估值": row_map["转让标的评估值或账面净值"],
        "成交金额": row_map["成交金额"],
        "成交日期": _normalize_trade_date(row_map["成交日期"]),
    }


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

    output_path = Path(os.path.abspath(str(output_file or resolved_settings.output_file or "").strip()))
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


