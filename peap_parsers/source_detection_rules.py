from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from peap_core import DecodedDocument


@dataclass(frozen=True)
class RuleMatch:
    source_id: str
    page_kind: str
    confidence: float
    reason: str


def _document_text(document: DecodedDocument) -> str:
    outer_html = str(document.metadata.get("outer_html") or "")
    html_parts = document.metadata.get("html_parts") or ()
    part_locations = " ".join(str(part.get("content_location") or "") for part in html_parts)
    return "\n".join(
        (
            outer_html,
            str(document.dom or ""),
            str(document.primary_text or ""),
            str(document.metadata.get("source_url") or ""),
            str(document.metadata.get("referrer_url") or ""),
            part_locations,
        )
    )


def collect_source_rule_matches(document: DecodedDocument) -> list[RuleMatch]:
    raw_text = _document_text(document)
    soup = BeautifulSoup(str(document.dom or raw_text), "html.parser")
    content_head = raw_text[:10000]
    content_snippet = raw_text[:50000]
    snippet_lower = content_snippet.lower()
    matches: list[RuleMatch] = []

    if (
        "snapshot-content-location: https://www.ggzy.gov.cn/" in snippet_lower
        or "content-location: https://www.ggzy.gov.cn/" in snippet_lower
        or "www.ggzy.gov.cn/information/deal/html/" in snippet_lower
        or any("ggzy.gov.cn" in str(part.get("content_location") or "").lower() for part in (document.metadata.get("html_parts") or ()))
    ):
        matches.append(RuleMatch("public_resource", "deal", 0.99, "matched public-resource mhtml markers"))

    if re.search(r"(?:G[36R]|Q[36R])\d{4}(?:GD|GZ)\d+(?:-\d+)?", content_head):
        if "/portal/pro/index.jsp?proId=" in raw_text or re.search(r'orgEname\s*=\s*"G[DZ]\d*"', raw_text):
            matches.append(RuleMatch("guangzhou", "listing", 0.96, "matched guangzhou code and portal markers"))
    if re.search(
        r"saved from url=.*https?://(?:www\.)?(?:gduaee\.com|gz\.gemas\.com\.cn)",
        raw_text[:5000],
        flags=re.IGNORECASE,
    ):
        matches.append(RuleMatch("guangzhou", "listing", 0.9, "matched guangzhou saved-from url"))

    if (
        soup.find("textarea", {"id": "jsonobj"}) is not None
        or re.search(r"saved from url=.*https?://(?:www\.)?cbex\.com", raw_text[:5000], flags=re.IGNORECASE)
        or "utrgcemsproject" in snippet_lower
        or "otc.cbex.com" in snippet_lower
    ):
        matches.append(RuleMatch("beijing", "listing", 0.95, "matched beijing textarea or cbex markers"))

    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    if "深圳联合产权交易所" in title_text:
        matches.append(RuleMatch("shenzhen", "listing", 0.9, "matched shenzhen title"))
    if "北京产权交易所" in title_text:
        matches.append(RuleMatch("beijing", "listing", 0.9, "matched beijing title"))
    if "上海联合产权交易所" in title_text:
        matches.append(RuleMatch("shanghai", "listing", 0.9, "matched shanghai title"))
    if "重庆产权交易" in title_text:
        matches.append(RuleMatch("chongqing", "listing", 0.9, "matched chongqing title"))
    if "天津产权交易中心" in title_text:
        matches.append(RuleMatch("tianjin", "listing", 0.9, "matched tianjin title"))
    if "山东产权交易" in title_text:
        matches.append(RuleMatch("shandong", "listing", 0.9, "matched shandong title"))
    if "广东联合产权交易中心" in title_text or "广州产权交易所" in title_text:
        matches.append(RuleMatch("guangzhou", "listing", 0.9, "matched guangzhou title"))

    if "sdcqjy.com" in snippet_lower:
        matches.append(RuleMatch("shandong", "listing", 0.85, "matched shandong domain"))
    if (
        (
            "山东产权交易中心" in content_head
            or "山东产权交易集团" in content_head
            or "山东产权" in content_head
        )
        and "cbex.com" not in snippet_lower
        and soup.find("textarea", {"id": "jsonobj"}) is None
    ):
        matches.append(RuleMatch("shandong", "listing", 0.88, "matched shandong guarded content markers"))

    if soup.find("div", {"id": "js_projectName"}) or "深圳联合产权交易所" in content_head:
        matches.append(RuleMatch("shenzhen", "listing", 0.84, "matched shenzhen content markers"))
    if "北京产权交易所" in content_head:
        matches.append(RuleMatch("beijing", "listing", 0.84, "matched beijing content markers"))
    if "重庆产权交易" in content_head:
        matches.append(RuleMatch("chongqing", "listing", 0.84, "matched chongqing content markers"))
    if "上海联合产权交易所" in content_head:
        matches.append(RuleMatch("shanghai", "listing", 0.84, "matched shanghai content markers"))
    if soup.find("div", {"class": "project_code"}) and "suaee.com" in snippet_lower:
        matches.append(RuleMatch("shanghai", "listing", 0.86, "matched shanghai project code and domain markers"))
    if "/portal/pro/index.jsp?proId=" in raw_text and re.search(r'orgEname\s*=\s*"G[DZ]\d*"', raw_text):
        matches.append(RuleMatch("guangzhou", "listing", 0.86, "matched guangzhou portal and org markers"))

    if "sotcbb.com" in snippet_lower:
        matches.append(RuleMatch("shenzhen", "listing", 0.7, "matched shenzhen domain fallback"))
    if "cbex.com" in snippet_lower:
        matches.append(RuleMatch("beijing", "listing", 0.7, "matched beijing domain fallback"))
    if "suaee.com" in snippet_lower:
        matches.append(RuleMatch("shanghai", "listing", 0.7, "matched shanghai domain fallback"))
    if "cquae.com" in snippet_lower:
        matches.append(RuleMatch("chongqing", "listing", 0.7, "matched chongqing domain fallback"))
    if "tpre.cn" in snippet_lower:
        matches.append(RuleMatch("tianjin", "listing", 0.7, "matched tianjin domain fallback"))
    if "sdcqjy.com" in snippet_lower:
        matches.append(RuleMatch("shandong", "listing", 0.7, "matched shandong domain fallback"))

    return matches


__all__ = ["RuleMatch", "collect_source_rule_matches"]
