"""Universal fallback extraction for pre-disclosure pages."""

import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

KEY_PROJECT_CODE = "项目编号"
KEY_PROJECT_NAME = "项目名称"
KEY_PROJECT_TYPE = "项目类型"
KEY_IS_PRE = "是否预披露"

KEY_START = "挂牌开始日期"
KEY_END = "挂牌截止日期"
KEY_PRE_START = "预披露开始日期"
KEY_PRE_END = "预披露截止日期"
KEY_INDUSTRY = "所属行业"
KEY_SELLER = "转让方"
KEY_GROUP = "隶属集团"
KEY_AGENCY = "受托机构"
KEY_PRICE = "挂牌价格"
KEY_SHARE_RATIO = "持股比例"
KEY_REMARK = "备注"
_PRICE_PLACEHOLDERS = {"-", "--", "—", "-万元", "--万元", "暂无", "待定", "以正式披露为准。"}
_EMPTY_TEXT_VALUES = {"", "-", "--", "—", "暂无", "无"}
_GROUP_LABELS = {
    "国家出资企业或主管部门名称",
    "国家出资企业/主管部门名称",
    "所属集团或主管部门名称",
    "主管部门名称",
    "上级单位",
    "批准单位名称",
}


def _is_empty(v: Any) -> bool:
    return v in (None, "", "-", "--", "—", "暂无")


def _is_pre_disclosure(data: Dict[str, Any]) -> bool:
    if bool(data.get(KEY_IS_PRE)):
        return True
    if str(data.get(KEY_PROJECT_TYPE) or "") == "预披露":
        return True
    code = str(data.get(KEY_PROJECT_CODE) or "")
    return code.endswith("-0")


def _norm(text: str) -> str:
    s = (text or "").replace("\xa0", " ").strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("：", "").replace(":", "")
    return s


def _clean(text: str) -> str:
    return (text or "").replace("\xa0", " ").strip()


def _clean_date(text: str) -> str:
    t = _clean(text)
    if not t:
        return ""
    if " " in t:
        t = t.split(" ")[0]
    if len(t) == 8 and t.isdigit():
        return f"{t[:4]}/{t[4:6]}/{t[6:]}"
    return t.replace("-", "/")


def _clean_ratio(text: str) -> str:
    ratio = _clean(text).replace("％", "%")
    if ratio in {"", "-", "--", "—", "暂无"}:
        return ""
    if ratio.endswith("%"):
        return ratio
    if re.fullmatch(r"\d+(?:\.\d+)?", ratio):
        return f"{ratio}%"
    return ratio


def _append_remark(data: Dict[str, Any], note: str) -> None:
    text = _clean(note)
    if not text:
        return
    existing = _clean(str(data.get(KEY_REMARK) or ""))
    if not existing or existing in {"None", "nan"}:
        data[KEY_REMARK] = text
        return
    if text in existing:
        return
    data[KEY_REMARK] = f"{existing}；{text}"


def _is_seller_label(label: str) -> bool:
    text = str(label or "").strip()
    if not (text.startswith("转让方") and text.endswith("名称")):
        return False
    middle = text[len("转让方") : -len("名称")]
    if not middle:
        return True
    return bool(re.fullmatch(r"[一二三四五六七八九十\d]+", middle))


def _normalize_seller_name(raw: Any) -> str:
    name = _clean(str(raw or ""))
    if not name:
        return ""
    # Keep note text stable: remove trailing ratio suffixs like "(19.84%)".
    name = re.sub(r"[（(]\s*\d+(?:\.\d+)?%\s*[）)]\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip("，,;； ")
    return "" if name in _EMPTY_TEXT_VALUES else name


def _normalize_group_name(raw: Any) -> str:
    group = _clean(str(raw or ""))
    group = re.sub(r"\s+", " ", group).strip("，,;； ")
    return "" if group in _EMPTY_TEXT_VALUES else group


def _split_sellers_from_field(seller_field: Any) -> List[str]:
    text = _clean(str(seller_field or ""))
    if not text:
        return []
    items: List[str] = []
    for part in re.split(r"[，、;；]\s*", text):
        name = _normalize_seller_name(part)
        if name:
            items.append(name)
    return items


def _extract_pre_seller_group_notes(
    pairs: List[Tuple[str, str]],
    seller_field: Any,
    default_group: Any,
) -> List[str]:
    base_sellers = _split_sellers_from_field(seller_field)
    # Strict guard: only annotate when parsed seller field itself is multi-seller.
    if len(base_sellers) <= 1:
        return []

    sellers: List[Dict[str, str]] = [{"name": name, "group": ""} for name in base_sellers]
    seller_idx: Dict[str, int] = {re.sub(r"\s+", "", s["name"]): i for i, s in enumerate(sellers)}
    current_idx: Optional[int] = None
    seen_groups: List[str] = []

    def match_seller_index(name: str) -> Optional[int]:
        normalized = re.sub(r"\s+", "", name)
        if normalized in seller_idx:
            return seller_idx[normalized]
        for key, idx in seller_idx.items():
            if normalized and (normalized in key or key in normalized):
                return idx
        return None

    for label, value in pairs:
        key = str(label or "").strip()
        if not key:
            continue

        if _is_seller_label(key):
            seller_name = _normalize_seller_name(value)
            if not seller_name:
                continue
            current_idx = match_seller_index(seller_name)
            continue

        if key in _GROUP_LABELS:
            group_name = _normalize_group_name(value)
            if not group_name:
                continue
            if group_name not in seen_groups:
                seen_groups.append(group_name)
            if current_idx is not None and not sellers[current_idx].get("group"):
                sellers[current_idx]["group"] = group_name

    default_group_name = _normalize_group_name(default_group)
    if default_group_name and default_group_name not in seen_groups:
        seen_groups.append(default_group_name)

    # If page only discloses one shared group, attach it to all sellers.
    if len(seen_groups) == 1:
        shared_group = seen_groups[0]
        for seller in sellers:
            if not seller.get("group"):
                seller["group"] = shared_group

    notes: List[str] = []
    seen_note = set()
    for seller in sellers:
        seller_name = _normalize_seller_name(seller.get("name"))
        group_name = _normalize_group_name(seller.get("group") or default_group_name)
        if not seller_name or not group_name:
            continue
        note = f"{seller_name}隶属{group_name}"
        if note in seen_note:
            continue
        seen_note.add(note)
        notes.append(note)

    return notes


def _collect_pairs(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    for content in soup.find_all("div", class_="project_content"):
        spans = [s.get_text(" ", strip=True) for s in content.find_all("span")]
        i = 0
        while i < len(spans):
            label = _clean(spans[i])
            if label.endswith("：") or label.endswith(":"):
                if i + 1 < len(spans):
                    pairs.append((_norm(label), _clean(spans[i + 1])))
                    i += 2
                    continue
            i += 1

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        texts = [_clean(c.get_text(" ", strip=True)) for c in cells]
        if len(texts) < 2:
            continue
        norms = [_norm(t) for t in texts]
        for idx, label in enumerate(norms[:-1]):
            if not label:
                continue
            value = texts[idx + 1]
            if value:
                pairs.append((label, value))

    return pairs


def apply_pre_disclosure_fallback(
    data: Dict[str, Any],
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
) -> None:
    if not isinstance(data, dict) or not html_content:
        return
    if not _is_pre_disclosure(data):
        return

    if soup is None:
        soup = BeautifulSoup(html_content, "html.parser")
    pairs = _collect_pairs(soup)

    by_label: Dict[str, str] = {}
    for label, value in pairs:
        if label and value and label not in by_label:
            by_label[label] = value

    def first_value(*labels: str) -> Optional[str]:
        for label in labels:
            if label in by_label and _clean(by_label[label]):
                return _clean(by_label[label])
        return None

    if _is_empty(data.get(KEY_START)):
        v = first_value("预披露起始日期", "预披露开始日期", "信息披露起始日期", "挂牌起始日期", "挂牌开始日期")
        if v:
            data[KEY_START] = _clean_date(v)
    if _is_empty(data.get(KEY_END)):
        v = first_value("预披露截止日期", "预披露结束日期", "信息披露期满日期", "挂牌期满日期", "挂牌截止日期")
        if v:
            data[KEY_END] = _clean_date(v)

    if _is_empty(data.get(KEY_PRE_START)) and not _is_empty(data.get(KEY_START)):
        data[KEY_PRE_START] = data.get(KEY_START)
    if _is_empty(data.get(KEY_PRE_END)) and not _is_empty(data.get(KEY_END)):
        data[KEY_PRE_END] = data.get(KEY_END)

    industry = str(data.get(KEY_INDUSTRY) or "").strip()
    if _is_empty(industry) or industry in {"住所", "注册地(住所)", "注册地"}:
        v = first_value("所属行业", "标的企业所属行业", "增资企业所属行业")
        if v:
            data[KEY_INDUSTRY] = v

    if _is_empty(data.get(KEY_SELLER)):
        v = first_value("转让方", "转让方名称", "增资企业名称", "融资方", "融资方名称", "增资方名称")
        if v:
            data[KEY_SELLER] = v

    if _is_empty(data.get(KEY_GROUP)):
        v = first_value(
            "国家出资企业或主管部门名称",
            "国家出资企业/主管部门名称",
            "所属集团或主管部门名称",
            "主管部门名称",
            "上级单位",
        )
        if v:
            data[KEY_GROUP] = v

    if _is_empty(data.get(KEY_AGENCY)):
        v = first_value("受托机构名称", "受托机构")
        if v:
            data[KEY_AGENCY] = v

    if _is_empty(data.get(KEY_PRICE)):
        v = first_value("转让底价", "挂牌价格", "拟募集资金总额", "拟增资价格")
        if v:
            data[KEY_PRICE] = v
    price_text = str(data.get(KEY_PRICE) or "").strip()
    if price_text in _PRICE_PLACEHOLDERS:
        data[KEY_PRICE] = ""


    if _is_empty(data.get(KEY_SHARE_RATIO)):
        v = first_value("拟募集资金对应持股比例", "拟募集资金对应持股比例(%)", "持股比例")
        if v:
            ratio = _clean_ratio(v)
            if ratio:
                data[KEY_SHARE_RATIO] = ratio

    seller_group_notes = _extract_pre_seller_group_notes(
        pairs,
        data.get(KEY_SELLER),
        data.get(KEY_GROUP),
    )
    for note in seller_group_notes:
        _append_remark(data, note)

    # For pre-disclosure pages where parser misses title block.
    if _is_empty(data.get(KEY_PROJECT_NAME)):
        name_node = soup.find("div", class_="project_xmmc")
        if name_node:
            name = _clean(name_node.get_text(" ", strip=True))
            if name:
                data[KEY_PROJECT_NAME] = name
