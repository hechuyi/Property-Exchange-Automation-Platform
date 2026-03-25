#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parser for public-resource-platform MHTML deal pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Dict, List, Sequence
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import WebPageParser

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
    encodings = [part.get_content_charset(), "utf-8", "gb18030", "latin-1"]
    for encoding in encodings:
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
    return source or host or "全国公共资源交易平台"


def _extract_table_rows(inner_html: str) -> Dict[str, str]:
    soup = BeautifulSoup(inner_html, "html.parser")
    table = soup.find("table", class_="detail_Table")
    if table is None:
        raise ValueError("detail_Table not found")

    row_map: Dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
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


class PublicResourceParser(WebPageParser):
    """公共资源网 MHTML 成交详情解析器。"""

    def parse(self) -> Dict[str, object]:
        row = parse_mhtml_file(self.require_source_file())
        trade_mode = str(row.get("交易方式") or "").strip()
        buyer = str(row.get("受让方名称") or "").strip()
        valuation = str(row.get("转让标的评估值") or "").strip()
        trade_date = str(row.get("成交日期") or "").strip()
        amount = str(row.get("成交金额") or "").strip()

        remark_parts = []
        if trade_mode:
            remark_parts.append(f"交易方式={trade_mode}")
        if buyer:
            remark_parts.append(f"受让方={buyer}")
        if valuation:
            remark_parts.append(f"评估值={valuation}")
        if trade_date:
            remark_parts.append(f"成交日期={trade_date}")

        self.data["项目编号"] = str(row.get("项目编号") or "").strip()
        self.data["项目名称"] = str(row.get("项目名称") or "").strip()
        self.data["交易所"] = str(row.get("交易所") or "").strip()
        self.data["挂牌价格"] = amount
        self.data["成交金额"] = amount
        self.data["交易方式"] = trade_mode
        self.data["受让方名称"] = buyer
        self.data["转让标的评估值"] = valuation
        self.data["成交日期"] = trade_date
        self.data["__source_exchange"] = "public_resource"
        if remark_parts:
            self.data["备注"] = "; ".join(remark_parts)
        return self.data
