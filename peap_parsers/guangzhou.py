#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guangzhou/Guangdong UEE parser."""

import re
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .base import ParserOutput, WebPageParser


class GuangzhouParser(WebPageParser):
    """广州产权交易所（广东联合产权交易中心）解析器。"""

    KEY_PROJECT_CODE = "\u9879\u76ee\u7f16\u53f7"
    KEY_PROJECT_NAME = "\u9879\u76ee\u540d\u79f0"
    KEY_LISTING_PRICE = "\u6302\u724c\u4ef7\u683c"
    KEY_LISTING_START = "\u6302\u724c\u5f00\u59cb\u65e5\u671f"
    KEY_LISTING_END = "\u6302\u724c\u622a\u6b62\u65e5\u671f"
    KEY_REGION = "\u6240\u5728\u5730\u533a"
    KEY_INDUSTRY = "\u6240\u5c5e\u884c\u4e1a"
    KEY_SELLER = "\u8f6c\u8ba9\u65b9"
    KEY_GROUP = "\u96b6\u5c5e\u96c6\u56e2"
    KEY_CONTACT = "\u7ecf\u529e\u4eba"
    KEY_AGENCY = "\u53d7\u6258\u673a\u6784"
    KEY_SHARE_RATIO = "\u6301\u80a1\u6bd4\u4f8b"
    KEY_REMARK = "\u5907\u6ce8"
    KEY_PROFIT = "\u8fd1\u4e00\u5e74\u51c0\u5229\u6da6"
    KEY_EXCHANGE = "\u4ea4\u6613\u6240"

    KEY_ECONOMIC_TYPE = "\u7ecf\u6d4e\u7c7b\u578b"
    KEY_OWNERSHIP = "\u4f01\u4e1a\u6027\u8d28"

    EXCHANGE_NAME = "\u5e7f\u4ea4\u6240"
    PROJECT_CODE_PATTERN = re.compile(r"(?:G[36R]|Q[36R])\d{4}(?:GD|GZ)\d+(?:-\d+)?")
    DATE_RANGE_PATTERN = re.compile(r"(\d{4}[/-]\d{2}[/-]\d{2})\s*[至到]\s*(\d{4}[/-]\d{2}[/-]\d{2})")
    SCRIPT_VAR_PATTERN = re.compile(r'var\s+([A-Za-z_]\w*)\s*=\s*"([^"]*)"\s*;')
    REMOTE_BASE = "https://www.gduaee.com"
    EMPTY_TOKENS = {"", "-", "--", "\u2014", "\u6682\u65e0", "/", "\uff0f"}
    BRACKET_CODE_PATTERN = re.compile(r"[\[【(（]\s*[0-9A-Za-z]{8,20}\s*[\]】)）]")

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").replace("\xa0", " "))

    @staticmethod
    def _cell_text(cell) -> str:
        return cell.get_text(" ", strip=True)

    @classmethod
    def _trim_bracket_code(cls, text: str) -> str:
        value = str(text or "").replace("\xa0", " ").strip()
        if not value:
            return ""

        # Typical noisy suffix in source pages, e.g. "[91110000710925243K]".
        match = cls.BRACKET_CODE_PATTERN.search(value)
        if match and match.start() > 0:
            value = value[: match.start()].strip()

        value = re.split(
            r"(?:\u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801|\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801|\u7ec4\u7ec7\u673a\u6784\u4ee3\u7801|\u6ce8\u518c\u53f7)",
            value,
            maxsplit=1,
        )[0].strip(" \t\u3000,，;；")
        return value

    @classmethod
    def _sanitize_group(cls, text: str) -> str:
        value = cls._trim_bracket_code(text)
        if not value:
            return ""
        for stop in (
            "\u6240\u5c5e\u884c\u4e1a",
            "\u6240\u5728\u5730\u533a",
            "\u4f01\u4e1a\u6027\u8d28",
            "\u7ecf\u6d4e\u7c7b\u578b",
            "\u56fd\u8d44\u76d1\u7ba1\u673a\u6784",
        ):
            pos = value.find(stop)
            if pos > 0:
                value = value[:pos].strip()
                break
        return value

    @classmethod
    def _sanitize_industry(cls, text: str) -> str:
        value = cls._trim_bracket_code(text)
        if not value:
            return ""

        for stop in (
            "\u6240\u5728\u5730\u533a",
            "\u4f01\u4e1a\u6027\u8d28",
            "\u7ecf\u6d4e\u7c7b\u578b",
            "\u56fd\u5bb6\u51fa\u8d44\u4f01\u4e1a",
            "\u6240\u5c5e\u96c6\u56e2",
            "\u4e3b\u7ba1\u90e8\u95e8",
        ):
            pos = value.find(stop)
            if pos > 0:
                value = value[:pos].strip()
                break
        return value

    def _normalize_group_industry(self) -> None:
        group = str(self.data.get(self.KEY_GROUP) or "")
        industry = str(self.data.get(self.KEY_INDUSTRY) or "")

        cleaned_group = self._sanitize_group(group)
        cleaned_industry = self._sanitize_industry(industry)

        if self.KEY_GROUP in self.data or cleaned_group:
            self.data[self.KEY_GROUP] = cleaned_group
        if self.KEY_INDUSTRY in self.data or cleaned_industry:
            self.data[self.KEY_INDUSTRY] = cleaned_industry

    @staticmethod
    def _to_float(text: str):
        cleaned = (text or "").replace(",", "").replace("\xa0", "").strip()
        cleaned = cleaned.replace("\u4e07\u5143", "").replace("\u4e07", "")
        if not cleaned or cleaned in {"---", "--", "-", "\u2014"}:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _extract_script_vars(self) -> Dict[str, str]:
        script_text = "\n".join(script.get_text("\n", strip=False) for script in self.soup.find_all("script"))
        return {k: v.strip() for k, v in self.SCRIPT_VAR_PATTERN.findall(script_text)}

    @classmethod
    def _is_empty_value(cls, value: Any) -> bool:
        return str(value or "").strip() in cls.EMPTY_TOKENS

    def _build_remote_tab_url(self, script_vars: Dict[str, str], tab: str) -> str:
        pro_id = str(script_vars.get("proId") or "").strip()
        sys_ename = str(script_vars.get("sysEname") or "").strip()
        if not pro_id or not sys_ename or not tab:
            return ""

        query = urlencode(
            {
                "proId": pro_id,
                "packId": str(script_vars.get("packId") or "").strip(),
                "orgEname": str(script_vars.get("orgEname") or "").strip(),
                "orgId": str(script_vars.get("proOrgId") or "").strip(),
            }
        )
        return f"{self.REMOTE_BASE}/portal/pro/{sys_ename}/{tab}.jsp?{query}"

    def _fetch_remote_tab(self, url: str) -> str:
        if not url:
            return ""
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,*/*",
                    "Referer": f"{self.REMOTE_BASE}/",
                },
            )
            with urlopen(req, timeout=4) as resp:
                raw = resp.read()
        except Exception:
            return ""

        for encoding in ("utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(encoding)
            except Exception:
                continue
        return raw.decode("utf-8", errors="ignore")

    def _load_local_tab_cache(self, tab: str) -> str:
        source_file = self.source_file
        if not source_file or not tab:
            return ""

        source = Path(source_file)
        stem = source.stem
        candidates = [
            source.parent / f"{stem}_{tab}.html",
            source.parent / f"{stem}_{tab}.jsp",
            source.parent / f"{stem}.async" / f"{tab}.html",
            source.parent / f"{stem}.async" / f"{tab}.jsp",
            source.parent / f"{stem}_files" / f"{tab}.html",
            source.parent / f"{stem}_files" / f"{tab}.jsp",
        ]

        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                raw = candidate.read_bytes()
            except Exception:
                continue
            for encoding in ("utf-8", "gb18030", "gbk"):
                try:
                    return raw.decode(encoding)
                except Exception:
                    continue
            try:
                return raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
        return ""

    def _extract_contact_from_contracts_html(self, html: str) -> str:
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        label_candidates = {
            "\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba",
            "\u9879\u76ee\u53d7\u7406\u8054\u7cfb\u4eba",
            "\u4e1a\u52a1\u8054\u7cfb\u4eba",
            "\u7ecf\u529e\u4eba",
            "\u8054\u7cfb\u4eba",
        }
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            for idx, cell in enumerate(cells):
                label = self._norm(self._cell_text(cell))
                if label not in label_candidates or idx + 1 >= len(cells):
                    continue
                raw = self._cell_text(cells[idx + 1]).strip()
                if self._is_empty_value(raw):
                    continue
                contact = self.extract_contact_person(raw) or raw
                if not self._is_empty_value(contact):
                    return str(contact).strip()

        page_text = soup.get_text("\n", strip=True)
        patterns = (
            r"(?:\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba|\u9879\u76ee\u53d7\u7406\u8054\u7cfb\u4eba|\u4e1a\u52a1\u8054\u7cfb\u4eba|\u7ecf\u529e\u4eba|\u8054\u7cfb\u4eba)[\uff1a:]\s*([^\n]{1,60})",
            r"(?:\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba|\u9879\u76ee\u53d7\u7406\u8054\u7cfb\u4eba|\u4e1a\u52a1\u8054\u7cfb\u4eba)\s+([^\n]{1,40})",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, page_text):
                raw = match.group(1).strip()
                raw = re.split(r"(?:\u7535\u8bdd|\u624b\u673a|\u8054\u7cfb\u65b9\u5f0f|[0-9]{6,})", raw)[0].strip(" \t\u3000\uff0c,;；")
                if self._is_empty_value(raw):
                    continue
                contact = self.extract_contact_person(raw) or raw
                if not self._is_empty_value(contact):
                    return str(contact).strip()
        return ""

    def _extract_industry_profit_from_html(self, html: str) -> None:
        if not html:
            return
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row_idx, row in enumerate(rows):
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                for col_idx, cell in enumerate(cells):
                    label = self._norm(self._cell_text(cell))
                    if not label:
                        continue
                    next_text = self._cell_text(cells[col_idx + 1]).strip() if col_idx + 1 < len(cells) else ""

                    if (
                        (self.KEY_INDUSTRY not in self.data or self._is_empty_value(self.data.get(self.KEY_INDUSTRY)))
                        and label in {"\u6240\u5c5e\u884c\u4e1a", "\u6807\u7684\u6240\u5c5e\u884c\u4e1a", "\u589e\u8d44\u4f01\u4e1a\u6240\u5c5e\u884c\u4e1a"}
                        and not self._is_empty_value(next_text)
                    ):
                        self.data[self.KEY_INDUSTRY] = next_text
                        continue

                    if (
                        (self.KEY_PROFIT not in self.data or self._is_empty_value(self.data.get(self.KEY_PROFIT)))
                        and label in {"\u51c0\u5229\u6da6", "\u8fd1\u4e00\u5e74\u51c0\u5229\u6da6"}
                    ):
                        number = self._to_float(next_text)
                        if number is not None:
                            self.data[self.KEY_PROFIT] = number
                            continue
                        for step in (1, 2, 3):
                            if row_idx + step >= len(rows):
                                break
                            next_row_cells = rows[row_idx + step].find_all(["th", "td"])
                            target_indices = [col_idx]
                            if col_idx > 0 and len(next_row_cells) == len(cells) - 1:
                                target_indices.append(col_idx - 1)
                            for target_idx in target_indices:
                                if target_idx >= len(next_row_cells):
                                    continue
                                candidate = self._cell_text(next_row_cells[target_idx]).strip()
                                number = self._to_float(candidate)
                                if number is not None:
                                    self.data[self.KEY_PROFIT] = number
                                    break
                if self.KEY_PROFIT in self.data and not self._is_empty_value(self.data.get(self.KEY_PROFIT)):
                    break

    def _extract_profit_prefer_annual_from_tables(self) -> Any:
        candidates = []
        section_priority = 0
        section_year = -1

        for table in self.soup.find_all("table"):
            rows = table.find_all("tr")
            for row_idx, row in enumerate(rows):
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                row_text = " ".join(self._cell_text(c) for c in cells)
                if not row_text:
                    continue

                if "\u5e74\u5ea6\u5ba1\u8ba1\u62a5\u544a" in row_text or (
                    "\u5e74\u5ea6" in row_text and ("\u5ba1\u8ba1" in row_text or "\u8d22\u52a1" in row_text)
                ):
                    section_priority = 2
                    years = re.findall(r"(20\d{2})", row_text)
                    section_year = max((int(y) for y in years), default=section_year)
                elif "\u8d22\u52a1\u62a5\u8868" in row_text or "\u6700\u8fd1\u4e00\u671f" in row_text:
                    section_priority = 1
                    years = re.findall(r"(20\d{2})", row_text)
                    section_year = max((int(y) for y in years), default=section_year)

                for col_idx, cell in enumerate(cells):
                    label = self._norm(self._cell_text(cell))
                    if label != "\u51c0\u5229\u6da6":
                        continue

                    value = None
                    next_text = self._cell_text(cells[col_idx + 1]).strip() if col_idx + 1 < len(cells) else ""
                    value = self._to_float(next_text)

                    if value is None and row_idx + 1 < len(rows):
                        next_row_cells = rows[row_idx + 1].find_all(["th", "td"])
                        candidate_indices = [col_idx]
                        if col_idx > 0 and len(next_row_cells) == len(cells) - 1:
                            candidate_indices.append(col_idx - 1)
                        for idx in candidate_indices:
                            if idx >= len(next_row_cells):
                                continue
                            value = self._to_float(self._cell_text(next_row_cells[idx]).strip())
                            if value is not None:
                                break

                    if value is None:
                        continue
                    candidates.append(
                        {
                            "value": value,
                            "priority": section_priority,
                            "year": section_year,
                        }
                    )

        if not candidates:
            return None
        best = max(candidates, key=lambda item: (item["priority"], item["year"]))
        return best["value"]

    def _extract_group_from_html(self, html: str) -> str:
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        group_labels = {
            "\u56fd\u5bb6\u51fa\u8d44\u4f01\u4e1a\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
            "\u6240\u5c5e\u96c6\u56e2\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
            "\u6279\u51c6\u5355\u4f4d\u540d\u79f0",
        }

        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            for idx, cell in enumerate(cells):
                label = self._norm(self._cell_text(cell))
                if label not in group_labels or idx + 1 >= len(cells):
                    continue
                candidate = self._cell_text(cells[idx + 1]).strip()
                if self._is_empty_value(candidate):
                    continue
                if len(candidate) >= 4 and any(token in candidate for token in ("\u96c6\u56e2", "\u516c\u53f8", "\u90e8", "\u59d4", "\u5c40")):
                    return candidate
        return ""

    def _supplement_from_remote_tabs(self, script_vars: Dict[str, str]) -> None:
        seller = str(self.data.get(self.KEY_SELLER) or "").strip()
        group = str(self.data.get(self.KEY_GROUP) or "").strip()
        group_same_as_seller = bool(group and seller and self._norm(group) == self._norm(seller))

        needs_contact = self.KEY_CONTACT not in self.data or self._is_empty_value(self.data.get(self.KEY_CONTACT))
        needs_industry = self.KEY_INDUSTRY not in self.data or self._is_empty_value(self.data.get(self.KEY_INDUSTRY))
        needs_profit = self.KEY_PROFIT not in self.data or self._is_empty_value(self.data.get(self.KEY_PROFIT))
        needs_group = self.KEY_GROUP not in self.data or self._is_empty_value(self.data.get(self.KEY_GROUP)) or group_same_as_seller

        if not (needs_contact or needs_industry or needs_profit or needs_group):
            return

        tabs = []
        if needs_contact:
            tabs.append("contracts")
        if needs_industry or needs_profit or needs_group:
            tabs.extend(["compInfo", "home"])
        if needs_group:
            tabs.extend(["seller", "pubInfoCon"])

        fetched: Dict[str, str] = {}
        for tab in tabs:
            if tab in fetched:
                continue
            html = self._load_local_tab_cache(tab)
            if not html:
                url = self._build_remote_tab_url(script_vars, tab)
                if not url:
                    continue
                html = self._fetch_remote_tab(url)
            if html:
                fetched[tab] = html

        if needs_contact and "contracts" in fetched:
            contact = self._extract_contact_from_contracts_html(fetched["contracts"])
            if not self._is_empty_value(contact):
                self.data[self.KEY_CONTACT] = contact

        if needs_industry or needs_profit:
            for tab in ("compInfo", "home"):
                html = fetched.get(tab)
                if not html:
                    continue
                self._extract_industry_profit_from_html(html)
                has_industry = self.KEY_INDUSTRY in self.data and not self._is_empty_value(self.data.get(self.KEY_INDUSTRY))
                has_profit = self.KEY_PROFIT in self.data and not self._is_empty_value(self.data.get(self.KEY_PROFIT))
                if has_industry and has_profit:
                    break

        if needs_group:
            existing_group = str(self.data.get(self.KEY_GROUP) or "").strip()
            for tab in ("seller", "home", "pubInfoCon", "compInfo"):
                html = fetched.get(tab)
                if not html:
                    continue
                candidate = self._extract_group_from_html(html)
                if self._is_empty_value(candidate):
                    continue
                if self._is_empty_value(existing_group):
                    self.data[self.KEY_GROUP] = candidate
                    break
                if seller and self._norm(existing_group) == self._norm(seller):
                    self.data[self.KEY_GROUP] = candidate
                    break
                if "\u4e2d\u56fd" in candidate and "\u4e2d\u56fd" not in existing_group:
                    self.data[self.KEY_GROUP] = candidate
                    break

    def _extract_contact_from_text(self) -> str:
        page_text = self.soup.get_text("\n", strip=True)
        patterns = (
            r"(?:\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba|\u9879\u76ee\u53d7\u7406\u8054\u7cfb\u4eba|\u4e1a\u52a1\u8054\u7cfb\u4eba|\u7ecf\u529e\u4eba|\u8054\u7cfb\u4eba)[\uff1a:]\s*([^\n]{1,60})",
            r"(?:\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba|\u9879\u76ee\u53d7\u7406\u8054\u7cfb\u4eba|\u4e1a\u52a1\u8054\u7cfb\u4eba)\s+([^\n]{1,30})",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, page_text):
                raw = match.group(1).strip()
                raw = re.split(r"(?:\u7535\u8bdd|\u624b\u673a|\u8054\u7cfb\u65b9\u5f0f|[0-9]{6,})", raw)[0].strip(" \t\u3000\uff0c,;；")
                if not raw:
                    continue
                contact = self.extract_contact_person(raw) or raw
                if contact and len(contact) <= 20:
                    return contact
        return ""

    def _extract_agency_from_text(self) -> str:
        page_text = self.soup.get_text("\n", strip=True)
        patterns = (
            r"(?:\u53d7\u6258\u673a\u6784(?:\u540d\u79f0)?|\u4ea4\u6613\u673a\u6784(?:\u540d\u79f0)?)[\uff1a:]\s*([^\n]{2,80})",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, page_text):
                raw = match.group(1).strip()
                raw = re.split(r"(?:\u8054\u7cfb\u4eba|\u7535\u8bdd|\u5730\u5740)", raw)[0].strip(" \t\u3000\uff0c,;；")
                if raw and len(raw) <= 60:
                    return raw
        return ""

    def _extract_from_top_summary(self) -> None:
        header = self.soup.select_one("div.pro-xiangmu h4")
        if header:
            header_title = header.get("title")
            header_text = (header_title or self._cell_text(header)).strip()
            header_text = re.sub(r"\s*围观数.*$", "", header_text)
            if header_text:
                self.data[self.KEY_PROJECT_NAME] = header_text

        price_elem = self.soup.select_one("span.price")
        if price_elem:
            price = self.clean_price(self._cell_text(price_elem))
            if price is not None:
                self.data[self.KEY_LISTING_PRICE] = price

        for li in self.soup.select("ul.base_info li"):
            text = self._cell_text(li)
            if not text:
                continue
            norm = self._norm(text)

            if "\u9879\u76ee\u7f16\u53f7" in norm:
                code_match = self.PROJECT_CODE_PATTERN.search(text)
                if code_match:
                    self.data[self.KEY_PROJECT_CODE] = code_match.group(0)
                continue

            if norm.startswith("\u8f6c\u8ba9\u65b9\uff1a"):
                seller = text.split("\uff1a", 1)[-1].strip()
                if seller:
                    self.data[self.KEY_SELLER] = seller
                continue

            if norm.startswith("\u4f01\u4e1a\u6027\u8d28\uff1a"):
                ownership = text.split("\uff1a", 1)[-1].strip()
                if ownership:
                    self.data[self.KEY_OWNERSHIP] = ownership
                continue

            if "\u6302\u724c\u8d77\u6b62\u65e5\u671f" in norm:
                date_match = self.DATE_RANGE_PATTERN.search(text)
                if date_match:
                    self.data[self.KEY_LISTING_START] = self.clean_date(date_match.group(1))
                    self.data[self.KEY_LISTING_END] = self.clean_date(date_match.group(2))

    @staticmethod
    def _normalize_ratio_text(raw_ratio: str) -> str:
        ratio = str(raw_ratio or "").strip().replace("\uff05", "%")
        ratio = re.sub(r"\s+", "", ratio)
        if not ratio:
            return ""
        if re.fullmatch(r"\d+(?:\.\d+)?%?", ratio):
            return ratio if ratio.endswith("%") else f"{ratio}%"
        match = re.search(r"(\d+(?:\.\d+)?)", ratio)
        if match:
            return f"{match.group(1)}%"
        return ""

    @staticmethod
    def _is_seller_label(norm_label: str) -> bool:
        return bool(re.fullmatch(r"\u8f6c\u8ba9\u65b9(?:[\u4e00-\u9fa5\d]+)?\u540d\u79f0", str(norm_label or "")))

    @staticmethod
    def _is_transfer_ratio_label(norm_label: str) -> bool:
        label = str(norm_label or "")
        if "\u62df\u8f6c\u8ba9" in label and "\u6bd4\u4f8b" in label:
            return True
        return label in {"\u6bd4\u4f8b(%)", "\u6bd4\u4f8b\uff08%\uff09", "\u62df\u8f6c\u8ba9\u6bd4\u4f8b"}

    def _extract_multi_seller_text(self) -> str:
        sellers = []

        def upsert_seller(name: str):
            seller_name = str(name or "").strip()
            if not seller_name:
                return None
            existing = next(
                (s for s in sellers if self._norm(s.get("name", "")) == self._norm(seller_name)),
                None,
            )
            if existing is not None:
                return existing
            seller = {"name": seller_name, "ratio": ""}
            sellers.append(seller)
            return seller

        for table in self.soup.find_all("table"):
            current_seller = None
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                for idx, cell in enumerate(cells):
                    raw_label = self._cell_text(cell)
                    label = self._norm(raw_label)
                    if not label:
                        continue
                    next_text = self._cell_text(cells[idx + 1]).strip() if idx + 1 < len(cells) else ""

                    if self._is_seller_label(label) and next_text:
                        current_seller = upsert_seller(next_text)
                        continue

                    if self._is_transfer_ratio_label(label) and current_seller:
                        ratio_text = next_text
                        if not ratio_text:
                            match = re.search(r"[\uff1a:]\s*([^\s]+)", raw_label)
                            if match:
                                ratio_text = match.group(1)
                        ratio = self._normalize_ratio_text(ratio_text)
                        if ratio:
                            current_seller["ratio"] = ratio

        if len(sellers) <= 1:
            return ""

        if not any(str(s.get("ratio") or "").strip() for s in sellers):
            return ""

        parts = []
        for seller in sellers:
            seller_name = str(seller.get("name") or "").strip()
            if not seller_name:
                continue
            ratio = self._normalize_ratio_text(seller.get("ratio", ""))
            parts.append(f"{seller_name}({ratio})" if ratio else seller_name)
        return "\uff0c".join(parts)

    def _extract_seller_ratio(self) -> None:
        seller = str(self.data.get(self.KEY_SELLER) or "").strip()
        if not seller or self.KEY_SHARE_RATIO in self.data:
            return

        seller_norm = self._norm(seller)
        for table in self.soup.find_all("table"):
            for row in table.find_all("tr"):
                values = [self._cell_text(c) for c in row.find_all(["th", "td"])]
                if not values:
                    continue
                norm_values = [self._norm(v) for v in values]
                row_text = " ".join(norm_values)
                if seller_norm not in row_text:
                    continue

                ratio_like = []
                numeric = []
                for value in values:
                    raw = value.strip()
                    cleaned = raw.replace("%", "")
                    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
                        continue
                    number = float(cleaned)
                    numeric.append(cleaned)
                    if 0 <= number <= 100 and ("." in cleaned or "%" in raw):
                        ratio_like.append(cleaned)

                if ratio_like:
                    self.data[self.KEY_SHARE_RATIO] = ratio_like[0]
                    return
                if numeric:
                    self.data[self.KEY_SHARE_RATIO] = numeric[-1]
                    return

    def _extract_from_tables(self) -> None:
        for table in self.soup.find_all("table"):
            rows = table.find_all("tr")
            for row_idx, row in enumerate(rows):
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                for col_idx, cell in enumerate(cells):
                    label = self._norm(self._cell_text(cell))
                    if not label:
                        continue
                    next_text = self._cell_text(cells[col_idx + 1]) if col_idx + 1 < len(cells) else ""

                    if label == "\u6807\u7684\u4f01\u4e1a\u540d\u79f0" and next_text and self.KEY_PROJECT_NAME not in self.data:
                        self.data[self.KEY_PROJECT_NAME] = next_text
                        continue

                    if label == "\u9879\u76ee\u540d\u79f0" and next_text and self.KEY_PROJECT_NAME not in self.data:
                        self.data[self.KEY_PROJECT_NAME] = next_text
                        continue

                    if label == "\u6240\u5728\u5730\u533a" and next_text:
                        self.data[self.KEY_REGION] = self.clean_region(re.sub(r"\s*-\s*", "", next_text))
                        continue

                    if label == "\u6240\u5c5e\u884c\u4e1a" and next_text:
                        self.data[self.KEY_INDUSTRY] = next_text
                        continue

                    if label == "\u7ecf\u6d4e\u7c7b\u578b" and next_text:
                        self.data[self.KEY_ECONOMIC_TYPE] = next_text
                        continue

                    if label == "\u4f01\u4e1a\u6027\u8d28" and next_text:
                        self.data[self.KEY_OWNERSHIP] = next_text
                        continue

                    if label in {
                        "\u56fd\u5bb6\u51fa\u8d44\u4f01\u4e1a\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
                        "\u6240\u5c5e\u96c6\u56e2\u6216\u4e3b\u7ba1\u90e8\u95e8\u540d\u79f0",
                    } and next_text:
                        self.data[self.KEY_GROUP] = next_text
                        continue

                    if label in {
                        "\u62df\u52df\u96c6\u8d44\u91d1\u5bf9\u5e94\u6301\u80a1\u6bd4\u4f8b\uff08%\uff09",
                        "\u62df\u52df\u96c6\u8d44\u91d1\u5bf9\u5e94\u6301\u80a1\u6bd4\u4f8b(%)",
                    } and next_text and self.KEY_SHARE_RATIO not in self.data:
                        self.data[self.KEY_SHARE_RATIO] = next_text
                        continue

                    if label == "\u56fd\u8d44\u76d1\u7ba1\u673a\u6784" and next_text:
                        continue

                    if label in {
                        "\u4ea4\u6613\u673a\u6784\u8054\u7cfb\u4eba",
                        "\u4e1a\u52a1\u8054\u7cfb\u4eba",
                        "\u8054\u7cfb\u4eba",
                    } and next_text:
                        self.data[self.KEY_CONTACT] = self.extract_contact_person(next_text) or next_text
                        continue

                    if label == "\u8f6c\u8ba9\u65b9\u540d\u79f0" and next_text and self.KEY_SELLER not in self.data:
                        self.data[self.KEY_SELLER] = next_text
                        continue

                    if label in {
                        "\u53d7\u6258\u673a\u6784",
                        "\u53d7\u6258\u673a\u6784\u540d\u79f0",
                        "\u4ea4\u6613\u673a\u6784",
                        "\u4ea4\u6613\u673a\u6784\u540d\u79f0",
                    } and next_text and self.KEY_AGENCY not in self.data:
                        self.data[self.KEY_AGENCY] = next_text
                        continue

                    if label == "\u51c0\u5229\u6da6" and self.KEY_PROFIT not in self.data:
                        for step in (1, 2):
                            if row_idx + step >= len(rows):
                                break
                            next_row_cells = rows[row_idx + step].find_all(["th", "td"])
                            if col_idx >= len(next_row_cells):
                                continue
                            candidate = self._cell_text(next_row_cells[col_idx])
                            number = self._to_float(candidate)
                            if number is not None:
                                self.data[self.KEY_PROFIT] = number
                                break

    def _build_standard_payload(self) -> Dict[str, Any]:
        return self.build_standard_payload_from_data(
            {
                "project_code": self.KEY_PROJECT_CODE,
                "project_name": self.KEY_PROJECT_NAME,
                "exchange": self.KEY_EXCHANGE,
                "source_type": "类型",
                "seller": ("融资方", self.KEY_SELLER),
                "group_name": self.KEY_GROUP,
                "industry": self.KEY_INDUSTRY,
                "region": self.KEY_REGION,
                "contact": self.KEY_CONTACT,
                "agency": self.KEY_AGENCY,
                "price": ("融资金额", self.KEY_LISTING_PRICE),
                "start_date": self.KEY_LISTING_START,
                "end_date": self.KEY_LISTING_END,
                "profit": self.KEY_PROFIT,
                "share_ratio": self.KEY_SHARE_RATIO,
                "listing_times": "挂牌次数",
                "remark": self.KEY_REMARK,
            }
        )

    def parse(self) -> ParserOutput:
        script_vars = self._extract_script_vars()

        self._extract_from_top_summary()
        self._extract_from_tables()

        if self.KEY_PROJECT_CODE not in self.data:
            code = script_vars.get("jcNo", "")
            match = self.PROJECT_CODE_PATTERN.search(code)
            if match:
                self.data[self.KEY_PROJECT_CODE] = match.group(0)

        if self.KEY_PROJECT_NAME not in self.data:
            pro_name = script_vars.get("proName", "").strip()
            if pro_name:
                self.data[self.KEY_PROJECT_NAME] = pro_name

        if self.KEY_LISTING_PRICE not in self.data:
            price = self.clean_price(script_vars.get("utrPrice", ""))
            if price is not None:
                self.data[self.KEY_LISTING_PRICE] = price

        if self.KEY_LISTING_START not in self.data:
            start = script_vars.get("pubStartTime", "").strip()
            if start:
                self.data[self.KEY_LISTING_START] = self.clean_date(start)

        if self.KEY_LISTING_END not in self.data:
            end = script_vars.get("pubEndTime", "").strip()
            if end:
                self.data[self.KEY_LISTING_END] = self.clean_date(end)

        if self.KEY_PROJECT_CODE not in self.data:
            full_text = self.soup.get_text(" ", strip=True)
            match = self.PROJECT_CODE_PATTERN.search(full_text)
            if match:
                self.data[self.KEY_PROJECT_CODE] = match.group(0)

        if self.KEY_SELLER not in self.data:
            project_name = str(self.data.get(self.KEY_PROJECT_NAME) or "")
            if project_name:
                seller_name = project_name.replace("\u589e\u8d44\u9879\u76ee", "").replace("\u589e\u8d44", "").strip()
                if seller_name:
                    self.data[self.KEY_SELLER] = seller_name

        self._extract_seller_ratio()
        multi_seller_text = self._extract_multi_seller_text()
        if multi_seller_text:
            self.data[self.KEY_SELLER] = multi_seller_text

        if self.KEY_CONTACT not in self.data or not str(self.data.get(self.KEY_CONTACT) or "").strip():
            contact = self._extract_contact_from_text()
            if contact:
                self.data[self.KEY_CONTACT] = contact

        self._supplement_from_remote_tabs(script_vars)
        self._normalize_group_industry()

        preferred_profit = self._extract_profit_prefer_annual_from_tables()
        if preferred_profit is not None:
            self.data[self.KEY_PROFIT] = preferred_profit



        if self.KEY_AGENCY not in self.data or not str(self.data.get(self.KEY_AGENCY) or "").strip():
            agency = self._extract_agency_from_text()
            if agency:
                self.data[self.KEY_AGENCY] = agency

        # 广交所备注默认留空，避免把长披露正文误写入备注列。
        self.data.pop(self.KEY_REMARK, None)

        self.data[self.KEY_EXCHANGE] = self.EXCHANGE_NAME
        return self.build_parser_output(standard_payload=self._build_standard_payload())
