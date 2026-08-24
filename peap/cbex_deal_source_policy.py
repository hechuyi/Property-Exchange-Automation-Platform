"""CBEX deal source URL classification."""

from __future__ import annotations

import urllib.parse

CBEX_DEAL_ROUTE_PREFIXES = {
    "deal_equity_transfer": "/xm/cqzr/",
    "deal_physical_asset": "/xm/zczr/",
    "deal_capital_increase": "/xm/qyzz/",
}

CBEX_DEAL_LIST_SEGMENTS = {"cjjggs"}
CBEX_DEAL_NAVIGATION_LEAVES = {
    "",
    "404ym",
    "cqzr",
    "fwtd",
    "index",
    "jtysgj",
    "qt",
    "qyzz",
    "sb",
    "xm",
    "xmtj",
    "xzsyzc",
    "ypl",
    "zczr",
    "zspl",
}


def _parsed_path(source_url: str) -> str:
    raw = str(source_url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    host = parsed.netloc.lower()
    if host and "cbex.com" not in host:
        return ""
    return parsed.path.lower()


def cbex_deal_route_prefix_for_business(business_id: str) -> str:
    return CBEX_DEAL_ROUTE_PREFIXES.get(str(business_id or "").strip(), "")


def is_cbex_deal_detail_url(source_url: str, *, detail_path_prefix: str = "") -> bool:
    path = _parsed_path(source_url)
    if not path:
        return False
    prefix = str(detail_path_prefix or "").strip().lower()
    if prefix and not path.startswith(prefix):
        return False
    if not prefix and not any(path.startswith(value) for value in CBEX_DEAL_ROUTE_PREFIXES.values()):
        return False
    parts = [part for part in path.split("/") if part]
    if any(part in CBEX_DEAL_LIST_SEGMENTS for part in parts):
        return False
    leaf = parts[-1] if parts else ""
    leaf_stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    if leaf_stem in CBEX_DEAL_NAVIGATION_LEAVES:
        return False
    if leaf in {"index.html", "index.shtml"}:
        return False
    return leaf.endswith((".html", ".shtml"))


def is_cbex_deal_non_detail_page(source_url: str, *, business_id: str = "") -> bool:
    path = _parsed_path(source_url)
    if not path:
        return False
    parts = [part for part in path.split("/") if part]
    if any(part in CBEX_DEAL_LIST_SEGMENTS for part in parts):
        return False
    prefix = cbex_deal_route_prefix_for_business(business_id)
    if prefix:
        return path.startswith(prefix) and not is_cbex_deal_detail_url(source_url, detail_path_prefix=prefix)
    return any(path.startswith(value) for value in CBEX_DEAL_ROUTE_PREFIXES.values()) and not is_cbex_deal_detail_url(source_url)


__all__ = [
    "cbex_deal_route_prefix_for_business",
    "is_cbex_deal_detail_url",
    "is_cbex_deal_non_detail_page",
]
