"""Universal fallback extraction for group/parent organization."""

import re
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

KEY_GROUP = "\u96b6\u5c5e\u96c6\u56e2"

_EMPTY = {"", "-", "--", "\u2014", "\u65e0", "\u6682\u65e0", "N/A", "NA", "null", "None"}

_GROUP_LABELS = (
    "\u56fd\u5bb6\u51fa\u8d44\u4f01\u4e1a\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
    "\u6240\u5c5e\u96c6\u56e2\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
    "\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
    "\u4e0a\u7ea7\u5355\u4f4d",
    "\u4e0a\u7ea7\u4e3b\u7ba1\u5355\u4f4d",
    "\u5b9e\u9645\u63a7\u5236\u4eba",
    "\u63a7\u80a1\u80a1\u4e1c",
    "\u6bcd\u516c\u53f8",
)

_INVALID_GROUP_VALUES = {
    "\u8f6c\u8ba9\u65b9\u540d\u79f0",
    "\u8f6c\u8ba9\u65b9",
    "\u62df\u8f6c\u8ba9\u4ea7(\u80a1)\u6743\u6bd4\u4f8b(%)",
    "\u62df\u8f6c\u8ba9\u6bd4\u4f8b",
    "\u6301\u6709\u4ea7(\u80a1)\u6743\u6bd4\u4f8b(%)",
    "\u9879\u76ee\u7f16\u53f7",
    "\u9879\u76ee\u540d\u79f0",
    "\u56fd\u8d44\u76d1\u7ba1\u673a\u6784",
    "\u6240\u5c5e\u96c6\u56e2\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
    "\u6279\u51c6\u5355\u4f4d\u540d\u79f0",
}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip() in _EMPTY


def _clean(text: str) -> str:
    return (text or "").replace("\xa0", " ").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", _clean(text))


def _valid_group_text(text: str) -> bool:
    t = _clean(text)
    if not t or t in _EMPTY:
        return False
    if _norm(t) in _INVALID_GROUP_VALUES:
        return False
    # Exclude regulator-category values.
    if "\u76d1\u7ba1" in t and ("\u4f01\u4e1a" in t or "\u90e8\u59d4" in t):
        return False
    if len(t) < 2:
        return False
    return True


def extract_group_from_html(
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
    include_approval_unit: bool = False,
) -> Optional[str]:
    if soup is None:
        soup = BeautifulSoup(html_content, "html.parser")

    labels = _GROUP_LABELS
    if include_approval_unit:
        labels = _GROUP_LABELS + ("\u6279\u51c6\u5355\u4f4d\u540d\u79f0",)

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            texts = [_clean(c.get_text(" ", strip=True)) for c in cells]
            norms = [_norm(t) for t in texts]
            for idx, label in enumerate(norms):
                if label in labels:
                    if idx + 1 < len(texts) and _valid_group_text(texts[idx + 1]):
                        return texts[idx + 1]

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"([\u4e00-\u9fa5A-Za-z（）()·]{4,60}?(?:集团有限公司|集团公司|有限公司|总局|委员会|部|局))所属单位", page_text)
    if m:
        candidate = _clean(m.group(1))
        if _valid_group_text(candidate):
            return candidate

    return None


def apply_group_fallback(
    data: Dict[str, Any],
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
) -> None:
    if not isinstance(data, dict) or not html_content:
        return
    if not _is_empty(data.get(KEY_GROUP)):
        return
    project_code = str(data.get("\u9879\u76ee\u7f16\u53f7") or "").strip()
    is_pre_disclosure = project_code.endswith("-0")
    group = extract_group_from_html(
        html_content,
        soup=soup,
        include_approval_unit=is_pre_disclosure,
    )
    if group:
        data[KEY_GROUP] = group
