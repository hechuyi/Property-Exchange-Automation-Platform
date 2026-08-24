from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from html import unescape

from bs4 import BeautifulSoup

from peap_core import DecodedDocument, source_business_contract


@dataclass(frozen=True)
class RuleMatch:
    source_id: str
    page_kind: str
    confidence: float
    reason: str


GUANGDONG_PROJECT_CODE_PATTERN = re.compile(r"(?:G[36R]|Q[36R]|T[36R])\d{4}(?:GD|GZ)\d+(?:-\d+)?")
CBEX_PROJECT_CODE_PATTERN = re.compile(r"(?:[GQT][36R])\d{4}BJ\d+(?:-\d+)?", re.IGNORECASE)
CBEX_DATE_PATTERN = re.compile(
    r"^(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$"
)


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


def _content_location_headers(raw_text: str) -> str:
    locations = []
    for match in re.finditer(
        r"(?im)^\s*(?:snapshot-)?content-location:\s*(\S+)",
        str(raw_text or "")[:50000],
    ):
        locations.append(match.group(1))
    return " ".join(locations)


def _same_exchange_listing_source_for_deal(source_id: str) -> str:
    return {
        "cbex": "beijing",
        "sse": "shanghai",
        "tpre": "tianjin",
        "cquae": "chongqing",
    }.get(source_id, "")


def _filter_equivalent_listing_matches(matches: list[RuleMatch]) -> list[RuleMatch]:
    deal_sources = {match.source_id for match in matches if match.page_kind == "deal"}
    if not deal_sources:
        return matches

    equivalent_listing_sources = {
        listing_source
        for listing_source in (_same_exchange_listing_source_for_deal(source) for source in deal_sources)
        if listing_source
    }
    if not equivalent_listing_sources:
        return matches

    return [
        match
        for match in matches
        if not (match.page_kind == "listing" and match.source_id in equivalent_listing_sources)
    ]


def _filter_listing_matches_for_explicit_deal_metadata(
    matches: list[RuleMatch],
    *,
    document: DecodedDocument,
) -> list[RuleMatch]:
    metadata_record_family = str(document.metadata.get("record_family") or "").strip().lower()
    metadata_source_id = str(document.metadata.get("source_id") or "").strip().lower()
    metadata_source_alias = {
        "beijing": "cbex",
        "shanghai": "sse",
        "tianjin": "tpre",
        "chongqing": "cquae",
    }
    canonical_source_id = metadata_source_alias.get(metadata_source_id, metadata_source_id)
    if metadata_record_family != "deal":
        return matches
    if canonical_source_id not in {"cbex", "sse", "tpre", "cquae", "public_resource"}:
        return matches
    return [match for match in matches if match.page_kind != "listing"]


def _deal_route_markers_by_source() -> dict[str, tuple[str, ...]]:
    return {
        item.source_id: item.route_markers
        for item in source_business_contract.list_source_classifier_route_markers(record_family="deal")
    }


def _content_deal_route_markers_by_source() -> dict[str, tuple[str, ...]]:
    return {
        item.source_id: item.content_route_markers
        for item in source_business_contract.list_source_classifier_route_markers(record_family="deal")
    }


def _url_has_deal_route_marker(url_blob: str, source_id: str) -> bool:
    return any(marker in url_blob for marker in _deal_route_markers_by_source().get(source_id, ()))


def _url_has_content_deal_route_marker(url_blob: str, source_id: str) -> bool:
    return any(marker in url_blob for marker in _content_deal_route_markers_by_source().get(source_id, ()))


def _has_guangdong_rendered_listing_identity(
    *,
    soup: BeautifulSoup,
    content_snippet: str,
    url_blob: str,
) -> bool:
    if "new.gduaee.com" in url_blob or "new.gduaee.com" in content_snippet.lower():
        return True

    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or "").strip().lower()
        if name not in {"keywords", "description"}:
            continue
        content = str(meta.get("content") or "")
        if "广东联合产权交易中心" in content or "广州产权交易所" in content:
            return True

    return bool(
        re.search(
            r"window\.(?:TITLE|COMPANY)\s*=\s*['\"][^'\"]*(?:广东联合产权交易中心|广州产权交易所)",
            content_snippet,
        )
    )


def _has_public_resource_deal_match(match: RuleMatch | None) -> bool:
    return match is not None and match.source_id == "public_resource" and match.page_kind == "deal"


def _nonempty_fact(value: object) -> bool:
    if value is None:
        return False
    return bool(str(value).strip())


def _first_nonempty_fact(payload: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = payload.get(key)
        if _nonempty_fact(value):
            return value
    return None


def _valid_cbex_deal_date(value: object) -> bool:
    match = CBEX_DATE_PATTERN.fullmatch(str(value or "").strip())
    if match is None:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def _cbex_jsonobj_payloads(soup: BeautifulSoup) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for node in soup.find_all("textarea", id="jsonobj"):
        text = node.string if node.string is not None else node.get_text(" ", strip=False)
        try:
            payload = json.loads(unescape(str(text or "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _has_cbex_structured_deal_facts(soup: BeautifulSoup) -> bool:
    """Return true only for page-internal CBEX transaction facts.

    The offline archive may not retain the original URL or a trusted sidecar.
    A project code alone is shared by listing pages, so a deal requires a valid
    tradedate and tradevalue. Capital-increase payloads additionally need an
    investor row with both a name and an amount.
    """
    for payload in _cbex_jsonobj_payloads(soup):
        for project_key in ("utrgcemsproject", "utrmcemsproject", "utrzcemsproject"):
            project = payload.get(project_key)
            if not isinstance(project, dict):
                continue
            project_code = str(project.get("projectcode") or project.get("projectCode") or "").strip()
            if not CBEX_PROJECT_CODE_PATTERN.fullmatch(project_code):
                continue
            if not _valid_cbex_deal_date(_first_nonempty_fact(project, "tradedate", "tradeDate")):
                continue
            if not _nonempty_fact(_first_nonempty_fact(project, "tradevalue", "tradeValue")):
                continue
            if project_key != "utrzcemsproject":
                return True

            trade_list = payload.get("tradelist")
            entries = trade_list.get("utrzcemstrade") if isinstance(trade_list, dict) else None
            if not isinstance(entries, list):
                continue
            if any(
                isinstance(entry, dict)
                and _nonempty_fact(
                    _first_nonempty_fact(entry, "investorname", "investorName", "buyername", "buyerName")
                )
                and _nonempty_fact(
                    _first_nonempty_fact(entry, "pertradevalue", "tradevalue", "tradeValue", "bidprice")
                )
                for entry in entries
            ):
                return True
    return False


def _detect_deal_rule_match(
    *,
    soup: BeautifulSoup,
    url_blob: str,
    content_head: str,
    document: DecodedDocument,
) -> RuleMatch | None:
    content_head_lower = content_head.lower()
    metadata_source_id = str(document.metadata.get("source_id") or "").strip().lower()
    metadata_record_family = str(document.metadata.get("record_family") or "").strip().lower()
    metadata_source_alias = {
        "beijing": "cbex",
        "shanghai": "sse",
        "tianjin": "tpre",
        "chongqing": "cquae",
    }
    if metadata_record_family == "deal":
        canonical_source_id = metadata_source_alias.get(metadata_source_id, metadata_source_id)
        if canonical_source_id in {"cbex", "sse", "tpre", "cquae", "public_resource"}:
            return RuleMatch(
                canonical_source_id,
                "deal",
                0.995,
                "matched deal snapshot metadata source markers; record_family=deal",
            )

    if _has_cbex_structured_deal_facts(soup):
        return RuleMatch(
            "cbex",
            "deal",
            0.99,
            "matched CBEX structured transaction facts in textarea#jsonobj; record_family=deal",
        )

    if (
        "snapshot-content-location: https://www.ggzy.gov.cn/" in url_blob
        or "content-location: https://www.ggzy.gov.cn/" in url_blob
        or "www.ggzy.gov.cn/information/deal/html/" in url_blob
        or any(
            "ggzy.gov.cn" in str(part.get("content_location") or "").lower()
            for part in (document.metadata.get("html_parts") or ())
        )
    ):
        return RuleMatch(
            "public_resource",
            "deal",
            0.99,
            "matched public-resource deal markers; record_family=deal",
        )

    if "cbex.com" in url_blob and _url_has_deal_route_marker(url_blob, "cbex"):
        return RuleMatch("cbex", "deal", 0.99, "matched cbex deal route markers; record_family=deal")

    if "suaee.com" in url_blob and (
        _url_has_deal_route_marker(url_blob, "sse")
        or ("成交公告" in content_head and _url_has_content_deal_route_marker(url_blob, "sse"))
    ):
        return RuleMatch("sse", "deal", 0.99, "matched sse deal route markers; record_family=deal")

    if "tpre.cn" in url_blob and (
        _url_has_deal_route_marker(url_blob, "tpre")
        or ("成交公告" in content_head and _url_has_content_deal_route_marker(url_blob, "tpre"))
    ):
        return RuleMatch("tpre", "deal", 0.99, "matched tpre deal route markers; record_family=deal")

    if "cquae.com" in url_blob and (
        _url_has_deal_route_marker(url_blob, "cquae")
        or ("成交" in content_head and _url_has_content_deal_route_marker(url_blob, "cquae"))
    ):
        return RuleMatch("cquae", "deal", 0.99, "matched cquae deal route markers; record_family=deal")

    # CBEX deal detail pages often embed textarea#jsonobj and成交措辞 without obvious route context.
    if (
        soup.find("textarea", {"id": "jsonobj"}) is not None
        and "cbex.com" in url_blob
        and ("成交" in content_head or "cjjg" in content_head_lower)
    ):
        return RuleMatch("cbex", "deal", 0.96, "matched cbex deal textarea markers; record_family=deal")

    return None


def collect_source_rule_matches(document: DecodedDocument) -> list[RuleMatch]:
    raw_text = _document_text(document)
    soup = BeautifulSoup(str(document.dom or raw_text), "html.parser")
    content_head = raw_text[:10000]
    content_snippet = raw_text[:50000]
    snippet_lower = content_snippet.lower()
    html_parts = document.metadata.get("html_parts") or ()
    part_locations = " ".join(str(part.get("content_location") or "") for part in html_parts)
    url_blob = " ".join(
        (
            str(document.metadata.get("source_url") or ""),
            str(document.metadata.get("referrer_url") or ""),
            part_locations,
            _content_location_headers(raw_text),
        )
    ).lower()
    matches: list[RuleMatch] = []

    deal_match = _detect_deal_rule_match(
        soup=soup,
        url_blob=url_blob,
        content_head=content_head,
        document=document,
    )
    if deal_match is not None:
        matches.append(deal_match)

    if GUANGDONG_PROJECT_CODE_PATTERN.search(content_head):
        if "/portal/pro/index.jsp?proId=" in raw_text or re.search(r'orgEname\s*=\s*"G[DZ]\d*"', raw_text):
            matches.append(RuleMatch("guangdong", "listing", 0.96, "matched guangdong code and portal markers"))
    if re.search(
        r"saved from url=.*https?://(?:www\.)?(?:gduaee\.com|gz\.gemas\.com\.cn)",
        raw_text[:5000],
        flags=re.IGNORECASE,
    ):
        matches.append(RuleMatch("guangdong", "listing", 0.9, "matched guangdong saved-from url"))

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
        matches.append(RuleMatch("guangdong", "listing", 0.9, "matched guangdong title"))

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
    if "天津交易集团" in content_head and ("trade.tpre.cn" in snippet_lower or "otc.tpre.cn" in snippet_lower):
        matches.append(RuleMatch("tianjin", "listing", 0.88, "matched tianjin rendered shell markers"))
    if (
        not _has_public_resource_deal_match(deal_match)
        and _has_guangdong_rendered_listing_identity(
            soup=soup,
            content_snippet=content_snippet,
            url_blob=url_blob,
        )
        and GUANGDONG_PROJECT_CODE_PATTERN.search(content_snippet)
        and "项目编号" in content_snippet
    ):
        matches.append(RuleMatch("guangdong", "listing", 0.88, "matched guangdong rendered listing markers"))
    if (
        (
            "trade.tpre.cn" in snippet_lower
            or "otc.tpre.cn" in snippet_lower
            or "天津交易集团" in content_head
        )
        and re.search(r"(?:G[36R]|T[36R]|TR)\d{4}TJ\d+(?:-\d+)?", content_snippet)
        and "项目编号" in content_snippet
    ):
        matches.append(RuleMatch("tianjin", "listing", 0.86, "matched tianjin rendered listing markers"))
    if soup.find("div", {"class": "project_code"}) and "suaee.com" in snippet_lower:
        matches.append(RuleMatch("shanghai", "listing", 0.86, "matched shanghai project code and domain markers"))
    if "/portal/pro/index.jsp?proId=" in raw_text and re.search(r'orgEname\s*=\s*"G[DZ]\d*"', raw_text):
        matches.append(RuleMatch("guangdong", "listing", 0.86, "matched guangdong portal and org markers"))

    if "sotcbb.com" in url_blob:
        matches.append(RuleMatch("shenzhen", "listing", 0.7, "matched shenzhen domain fallback"))
    if "cbex.com" in url_blob:
        matches.append(RuleMatch("beijing", "listing", 0.7, "matched beijing domain fallback"))
    if "suaee.com" in url_blob:
        matches.append(RuleMatch("shanghai", "listing", 0.7, "matched shanghai domain fallback"))
    if "cquae.com" in url_blob:
        matches.append(RuleMatch("chongqing", "listing", 0.7, "matched chongqing domain fallback"))
    if "tpre.cn" in url_blob:
        matches.append(RuleMatch("tianjin", "listing", 0.7, "matched tianjin domain fallback"))
    if "sdcqjy.com" in url_blob:
        matches.append(RuleMatch("shandong", "listing", 0.7, "matched shandong domain fallback"))

    matches = _filter_equivalent_listing_matches(matches)
    return _filter_listing_matches_for_explicit_deal_metadata(matches, document=document)


__all__ = ["RuleMatch", "collect_source_rule_matches"]
