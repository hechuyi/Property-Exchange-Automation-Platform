#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深圳联合产权交易所解析器
"""

import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping
from typing import Any, Dict, List

from .base import WebPageParser


class ShenzhenParser(WebPageParser):
    """深圳联合产权交易所解析器"""

    _GOV_PROJECT_CODE_RE = re.compile(
        r"[GQ][36R]\d{4}SZ\d+(?:-\d+)?",
        re.IGNORECASE,
    )

    _SIDECAR_LABEL_KEYS = (
        "label",
        "name",
        "fieldName",
        "fieldTitle",
        "title",
        "key",
        "displayName",
        "columnName",
        "itemName",
        "fieldLabel",
    )
    _SIDECAR_VALUE_KEYS = (
        "value",
        "fieldValue",
        "content",
        "text",
        "val",
        "fieldText",
        "dataValue",
        "defaultValue",
    )
    _SIDECAR_EMPTY_VALUES = {"", "-", "--", "—", "——", "/", "暂无", "无", "null", "None"}
    _SIDECAR_FORM_KEYS = ("form", "forms", "formData", "formList")
    _SIDECAR_FIELD_ALIASES = (
        (
            "项目编号",
            ("gzwCode", "国资监测编号", "项目编号", "projectNo", "projectCode", "xmbh"),
            "text",
            False,
        ),
        ("项目名称", ("项目名称", "标的名称", "projectName", "title", "xmmc"), "text", False),
        ("project_id", ("packageId", "package_id", "contentId"), "text", False),
        ("page_url", ("page_url", "source_url", "detail_url"), "text", False),
        ("转让方", ("转让方名称", "转让方", "出让方名称", "sellerName", "seller"), "text", False),
        ("融资方", ("融资方名称", "增资企业名称", "增资企业", "融资方"), "text", False),
        ("国资监管机构", ("国资监管机构", "stateAssetSupervisor", "monitorName", "monitornamezw"), "text", False),
        (
            "国家出资企业或主管部门名称",
            (
                "国家出资企业或主管部门名称",
                "国家出资企业/主管部门名称",
                "所属集团或主管部门名称",
                "国家出资企业",
                "主管部门名称",
                "stateFundedDepartment",
                "hqName",
                "hqname",
            ),
            "text",
            False,
        ),
        ("所属行业", ("所属行业", "行业", "industry", "industrycodezw"), "text", False),
        ("所在地区", ("所在地区", "标的企业所在地区", "增资企业所在地区", "region", "zonecityzw"), "text", False),
        ("近一年净利润", ("近一年净利润", "净利润", "auditnetprofit", "stmtnetprofit"), "number", False),
        ("总资产", ("总资产", "资产总额", "资产总计", "assetTotal", "auditasset", "stmtasset"), "number", False),
        ("融资金额", ("融资金额", "拟募集资金", "拟募集资金金额", "financeAmount"), "text", False),
        ("持股比例", ("持股比例", "拟募集资金对应持股比例", "拟投资金额对应持股比例"), "text", False),
    )

    @classmethod
    def _sidecar_text(cls, value: Any) -> str:
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return ""
        text = re.sub(r"\s+", " ", str(value)).strip()
        return "" if text in cls._SIDECAR_EMPTY_VALUES else text

    @staticmethod
    def _sidecar_label_key(value: Any) -> str:
        return re.sub(r"[\s:：()（）/／、_-]+", "", str(value or "")).strip().lower()

    @classmethod
    def _sidecar_number(cls, value: Any) -> Any:
        text = cls._sidecar_text(value).replace(",", "")
        if not text:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        try:
            return float(match.group(0))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sidecar_save_status_is_complete(
        sidecar: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> bool:
        for container in (sidecar, metadata):
            if "save_status" not in container:
                continue
            save_status = str(container.get("save_status") or "").strip().lower()
            if save_status != "complete":
                return False
        return True

    def _load_sidecar_document(self) -> Mapping[str, Any] | None:
        self._sidecar_hash_verified = False
        source_file = self.source_file
        if not source_file:
            return None
        sidecar_path = os.path.splitext(source_file)[0] + ".json"
        if os.path.islink(sidecar_path):
            return None
        if not os.path.isfile(sidecar_path):
            return None
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(sidecar, Mapping):
            return None
        metadata = sidecar.get("metadata") if isinstance(sidecar.get("metadata"), Mapping) else {}
        record_family = str(metadata.get("record_family") or sidecar.get("record_family") or "").strip().lower()
        source_id = str(metadata.get("source_id") or sidecar.get("source_id") or "").strip().lower()
        if record_family == "deal" or (source_id and source_id not in {"shenzhen", "sz"}):
            return None
        if not self._sidecar_save_status_is_complete(sidecar, metadata):
            return None
        expected_hash = str(sidecar.get("archive_content_sha256") or "").strip().removeprefix("sha256:")
        expected_bytes = sidecar.get("archive_content_bytes")
        try:
            if expected_hash:
                digest = hashlib.sha256()
                with open(source_file, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest().lower() != expected_hash.lower():
                    return None
                self._sidecar_hash_verified = True
            if expected_bytes not in (None, "") and os.path.getsize(source_file) != int(expected_bytes):
                return None
        except (OSError, TypeError, ValueError):
            return None
        return sidecar

    def _iter_sidecar_mappings(self, value: Any) -> Iterator[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from self._iter_sidecar_mappings(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._iter_sidecar_mappings(child)

    def _iter_sidecar_scopes(self, sidecar: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
        for mapping in self._iter_sidecar_mappings(sidecar):
            has_form = any(isinstance(mapping.get(key), (Mapping, list)) for key in self._SIDECAR_FORM_KEYS)
            has_package = isinstance(mapping.get("portalTPackage"), Mapping)
            if has_form or has_package:
                yield mapping

    def _iter_sidecar_form_entries(self, scope: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
        for form_key in self._SIDECAR_FORM_KEYS:
            form = scope.get(form_key)
            if not isinstance(form, (Mapping, list)):
                continue
            for entry in self._iter_sidecar_mappings(form):
                if any(key in entry for key in self._SIDECAR_LABEL_KEYS) and any(
                    key in entry for key in self._SIDECAR_VALUE_KEYS
                ):
                    yield entry

    def _sidecar_entry_pair(self, entry: Mapping[str, Any]) -> tuple[str, Any]:
        label = ""
        value: Any = None
        for key in self._SIDECAR_LABEL_KEYS:
            candidate = self._sidecar_text(entry.get(key))
            if candidate:
                label = candidate
                break
        for key in self._SIDECAR_VALUE_KEYS:
            if key in entry:
                candidate = entry.get(key)
                if self._sidecar_text(candidate):
                    value = candidate
                    break
        return label, value

    def _sidecar_scope_pairs(self, scope: Mapping[str, Any]) -> list[tuple[str, Any]]:
        pairs: list[tuple[str, Any]] = []
        for entry in self._iter_sidecar_form_entries(scope):
            label, value = self._sidecar_entry_pair(entry)
            if label and self._sidecar_text(value):
                pairs.append((label, value))
        package = scope.get("portalTPackage")
        if isinstance(package, Mapping):
            for key, value in package.items():
                if self._sidecar_text(value):
                    pairs.append((str(key), value))
        return pairs

    def _sidecar_metadata_pairs(self, sidecar: Mapping[str, Any]) -> list[tuple[str, Any]]:
        pairs: list[tuple[str, Any]] = []
        for key in ("page_url", "source_url", "detail_url"):
            value = sidecar.get(key)
            if self._sidecar_text(value):
                pairs.append((key, value))
        return pairs

    def _pick_sidecar_field(self, pairs: list[tuple[str, Any]], aliases: tuple[str, ...]) -> Any:
        alias_keys = tuple(self._sidecar_label_key(alias) for alias in aliases)
        for alias in alias_keys:
            for label, value in pairs:
                label_key = self._sidecar_label_key(label)
                if label_key == alias:
                    return value
        for alias in alias_keys:
            for label, value in pairs:
                label_key = self._sidecar_label_key(label)
                if label_key.endswith(alias):
                    return value
        return None

    def _sidecar_values(self, pairs: list[tuple[str, Any]], aliases: tuple[str, ...]) -> list[str]:
        alias_keys = {self._sidecar_label_key(alias) for alias in aliases}
        values: list[str] = []
        for label, value in pairs:
            if self._sidecar_label_key(label) not in alias_keys:
                continue
            text = self._sidecar_text(value)
            if text and text not in values:
                values.append(text)
        return values

    def _sidecar_project_type(self, pairs: list[tuple[str, Any]]) -> str:
        tender_category = self._sidecar_label_key(
            self._pick_sidecar_field(pairs, ("tenderCategory", "交易品类"))
        )
        tender_code = self._sidecar_text(
            self._pick_sidecar_field(pairs, ("tenderCategoryCode", "交易品类代码"))
        ).upper()
        project_category = self._sidecar_label_key(
            self._pick_sidecar_field(pairs, ("projectTenderCategory", "项目交易类别"))
        )

        if tender_code == "CQJY_GQ" or tender_category in {"股权", "股权转让"}:
            return "股权转让"
        if (
            tender_code == "CQJY_ZZKG"
            or tender_category in {"增资", "增资扩股"}
            or project_category == "企业增资"
        ):
            return "增资扩股"
        return ""

    @classmethod
    def _sidecar_project_codes(cls, values: list[str]) -> tuple[str, ...]:
        pattern = re.compile(
            r"(?:[GQ][36R]\d{4}[A-Z]{2}\d+(?:-\d+)?|CQ\d{8,}(?:-\d+)?)",
            re.IGNORECASE,
        )
        codes: list[str] = []
        for value in values:
            for match in pattern.findall(value):
                code = match.upper()
                if code not in codes:
                    codes.append(code)
        return tuple(codes)

    @classmethod
    def _sidecar_identity_text(cls, value: Any) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", cls._sidecar_text(value).lower())

    def _select_sidecar_scope(
        self,
        sidecar: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], list[tuple[str, Any]]] | None:
        code_aliases = ("gzwCode", "国资监测编号", "projectCode", "项目编号", "projectNo", "xmbh")
        name_aliases = ("projectName", "标的名称", "项目名称", "title", "xmmc")
        candidates: list[
            tuple[
                Mapping[str, Any],
                list[tuple[str, Any]],
                tuple[str, ...],
                tuple[str, ...],
            ]
        ] = []
        for scope in self._iter_sidecar_scopes(sidecar):
            pairs = self._sidecar_scope_pairs(scope)
            if not pairs:
                continue
            codes = self._sidecar_project_codes(self._sidecar_values(pairs, code_aliases))
            names = tuple(
                dict.fromkeys(
                    normalized
                    for value in self._sidecar_values(pairs, name_aliases)
                    if (normalized := self._sidecar_identity_text(value))
                )
            )
            candidates.append((scope, pairs, codes, names))
        if not candidates:
            return None

        html_codes = self._sidecar_project_codes([self._sidecar_text(self.data.get("项目编号"))])
        html_code = html_codes[0] if html_codes else ""
        html_name = self._sidecar_identity_text(self.data.get("项目名称"))
        if not html_code and not html_name:
            if bool(getattr(self, "_sidecar_hash_verified", False)) and len(candidates) == 1:
                scope, pairs, _codes, _names = candidates[0]
                return scope, pairs
            return None

        ranked: list[
            tuple[
                int,
                Mapping[str, Any],
                list[tuple[str, Any]],
                tuple[str, ...],
                tuple[str, ...],
            ]
        ] = []
        for scope, pairs, codes, names in candidates:
            score = 0
            if html_code and codes:
                if html_code not in codes:
                    continue
                score += 1
            if html_name and names:
                if html_name not in names:
                    continue
                score += 1
            if score:
                ranked.append((score, scope, pairs, codes, names))
        if not ranked:
            return None

        highest_score = max(candidate[0] for candidate in ranked)
        best = [candidate for candidate in ranked if candidate[0] == highest_score]
        if not bool(getattr(self, "_sidecar_hash_verified", False)):
            if not html_code:
                return None
            best = [candidate for candidate in best if html_code in candidate[3]]
            if not best:
                return None
        if len(best) > 1:
            identities = {(candidate[3], candidate[4]) for candidate in best}
            fingerprints = {
                tuple(
                    (self._sidecar_label_key(label), self._sidecar_text(value))
                    for label, value in candidate[2]
                )
                for candidate in best
            }
            if len(identities) != 1 or len(fingerprints) != 1:
                return None
        _score, scope, pairs, _codes, _names = best[0]
        return scope, pairs

    def _apply_package_view_sidecar_fields(self) -> None:
        sidecar = self._load_sidecar_document()
        if sidecar is None:
            return
        selected = self._select_sidecar_scope(sidecar)
        if selected is None:
            return
        _scope, pairs = selected
        pairs = [*pairs, *self._sidecar_metadata_pairs(sidecar)]
        for target, aliases, mode, overwrite in self._SIDECAR_FIELD_ALIASES:
            raw_value = self._pick_sidecar_field(pairs, aliases)
            value = self._sidecar_number(raw_value) if mode == "number" else self._sidecar_text(raw_value)
            if value in (None, ""):
                continue
            if overwrite or not self._sidecar_text(self.data.get(target)):
                self.data[target] = value

        project_type = self._sidecar_project_type(pairs)
        if project_type:
            self.data["项目类型"] = project_type

        canonical_code = self._sidecar_text(
            self._pick_sidecar_field(pairs, ("gzwCode", "国资监测编号"))
        ).upper()
        if self._GOV_PROJECT_CODE_RE.fullmatch(canonical_code):
            source_code = self._sidecar_text(
                self._pick_sidecar_field(pairs, ("projectCode", "项目编号", "projectNo", "xmbh"))
            ).upper()
            if source_code and source_code != canonical_code:
                self.data["source_project_code"] = source_code
            self.data["项目编号"] = canonical_code

        state_funded = self._sidecar_text(self.data.get("国家出资企业或主管部门名称"))
        if state_funded and not self._sidecar_text(self.data.get("隶属集团")):
            self.data["隶属集团"] = state_funded
        if state_funded:
            self.data["state_funded_department"] = state_funded
        group_name = self._sidecar_text(self.data.get("隶属集团"))
        if group_name:
            self.data["group_name"] = group_name
        supervisor = self._sidecar_text(self.data.get("国资监管机构"))
        if supervisor:
            self.data["state_asset_supervisor"] = supervisor
        financing = self._sidecar_text(self.data.get("融资方"))
        if financing and not self._sidecar_text(self.data.get("转让方")):
            self.data["转让方"] = financing
        seller = self._sidecar_text(self.data.get("转让方"))
        if seller:
            self.data["seller"] = seller
        page_url = self._sidecar_text(self.data.get("page_url"))
        if page_url:
            self.data["source_url"] = page_url
    
    def parse(self) -> Dict[str, Any]:
        """解析深圳联交所网页"""
        project_code_pattern = re.compile(
            r'(?:[GQ][36R]\d{4}[A-Z]{2}\d+(?:-\d+)?|CQ\d{8,}(?:-\d+)?)',
            re.IGNORECASE,
        )

        def pick_project_code(text: str) -> str:
            match = project_code_pattern.search(str(text or ""))
            return match.group(0).upper() if match else ""

        def normalize_ratio(raw_ratio: Any) -> str:
            ratio = str(raw_ratio or "").strip().replace("％", "%")
            ratio = re.sub(r"\s+", "", ratio)
            if not ratio:
                return ""

            # Prefer explicit percentage in mixed texts like: "2185.203万股（占总股本的8.184%）".
            percent_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", ratio)
            if percent_matches:
                candidate = percent_matches[-1]
                try:
                    number = float(candidate)
                    if 0 <= number <= 100:
                        return f"{candidate}%"
                except (TypeError, ValueError):
                    pass
                return ""

            if re.fullmatch(r"\d+(?:\.\d+)?%?", ratio):
                try:
                    number = float(ratio.rstrip("%"))
                except (TypeError, ValueError):
                    return ""
                if 0 <= number <= 100:
                    return ratio if ratio.endswith("%") else f"{ratio}%"
                return ""
            return ""

        def is_seller_label(label_text: str) -> bool:
            norm = re.sub(r"\s+", "", str(label_text or ""))
            return bool(re.fullmatch(r"转让方(?:[一二三四五六七八九十\d]+)?名称", norm))

        def is_financing_label(label_text: str) -> bool:
            norm = re.sub(r"\s+", "", str(label_text or ""))
            return norm in {"融资方", "融资方名称", "增资方名称", "增资企业名称", "增资企业"}

        def is_ratio_label(label_text: str) -> bool:
            norm = re.sub(r"\s+", "", str(label_text or ""))
            if "拟转让" in norm and "比例" in norm:
                return True
            return norm in {"比例(%)", "比例（%）", "拟转让比例"}

        def is_capital_ratio_label(label_text: str) -> bool:
            norm = re.sub(r"\s+", "", str(label_text or ""))
            if "拟募集资金对应持股比例" in norm and "说明" not in norm:
                return True
            if "拟投资金额对应持股比例" in norm:
                return True
            return norm in {
                "持股比例",
                "持股比例(%)",
                "持股比例（%）",
                "拟募集资金对应持股比例或股份数",
                "持股比例或股份数",
            }

        def normalize_capital_ratio(raw_ratio: Any) -> str:
            ratio = str(raw_ratio or "").strip().replace("％", "%")
            ratio = re.sub(r"\s+", "", ratio)
            if not ratio or ratio in {"-", "--", "—", "暂无"}:
                return ""

            # Keep qualifier semantics for capital increase pages (e.g. 不超过29%).
            qualified = re.search(r"(?:不超过|不低于|小于等于|大于等于|小于|大于)?\d+(?:\.\d+)?%", ratio)
            if qualified:
                return qualified.group(0)
            if re.fullmatch(r"\d+(?:\.\d+)?", ratio):
                return f"{ratio}%"
            return ratio

        def split_inline_sellers(seller_text: str) -> List[str]:
            text = str(seller_text or "").strip()
            if not text:
                return []

            matches = re.findall(
                r"转让方[一二三四五六七八九十\d]+\s*[:：]\s*(.*?)(?=(?:\s*转让方[一二三四五六七八九十\d]+\s*[:：])|$)",
                text,
            )
            if not matches:
                return [text]

            result: List[str] = []
            for item in matches:
                value = re.sub(r"\s+", " ", str(item or "")).strip(" ；;，,")
                if value:
                    result.append(value)
            return result

        def parse_price_to_wan(raw_price: Any):
            text = str(raw_price or "").strip()
            if not text:
                return None
            match = re.search(
                r"(?:挂牌(?:价|金额)|转让底价)[：:\s]*([\d,]+(?:\.\d+)?)\s*(万元|万|元)",
                text,
            )
            if match:
                value = float(match.group(1).replace(",", ""))
                unit = match.group(2)
                return value / 10000 if unit == "元" else value

            match = re.search(r"([\d,]+(?:\.\d+)?)\s*(万元|万|元)", text)
            if match:
                value = float(match.group(1).replace(",", ""))
                unit = match.group(2)
                return value / 10000 if unit == "元" else value
            return self.clean_price(text)

        def normalize_region_text(raw_region: Any) -> str:
            region = str(raw_region or "").strip()
            if not region:
                return ""
            region = self.clean_region(region)
            region = re.sub(r"^中国(?:（[^）]{1,20}）)?", "", str(region or "").strip())
            return region.strip()

        def parse_financing_amount(raw_text: Any) -> str:
            text = str(raw_text or "").strip()
            if not text:
                return ""
            match = re.search(
                r"(?:不超过|不低于|约|不少于|不高于)?\s*[\d,]+(?:\.\d+)?\s*(?:亿元|万元|万|元)",
                text,
            )
            if match:
                return re.sub(r"\s+", "", match.group(0))
            return re.sub(r"\s+", "", text)

        def extract_profit_from_tables_prefer_annual(all_tables) -> Any:
            candidates: List[Dict[str, Any]] = []
            section_priority = 0
            section_year = -1

            def parse_number(raw_value: Any):
                text = str(raw_value or "").strip().replace(",", "")
                if not text or text in {"-", "--", "—", "——", "暂无"}:
                    return None
                if re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text):
                    return None
                match = re.search(r"-?\d+(?:\.\d+)?", text)
                if not match:
                    return None
                if (
                    "." not in match.group(0)
                    and re.search(r"[/-]|年", text)
                    and re.search(r"20\d{2}", match.group(0))
                ):
                    return None
                try:
                    return float(match.group(0))
                except (TypeError, ValueError):
                    return None

            for table in all_tables:
                rows = table.find_all('tr')
                for row_idx, row in enumerate(rows):
                    cells = row.find_all(['td', 'th'])
                    row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
                    if not row_text:
                        continue

                    if '年度审计报告' in row_text or ('年度' in row_text and ('审计' in row_text or '财务' in row_text)):
                        section_priority = 2
                        years = re.findall(r"(20\d{2})", row_text)
                        section_year = max((int(y) for y in years), default=section_year)
                    elif '财务报表' in row_text or '最近一期' in row_text:
                        section_priority = 1
                        years = re.findall(r"(20\d{2})", row_text)
                        section_year = max((int(y) for y in years), default=section_year)

                    for col_idx, cell in enumerate(cells):
                        label = cell.get_text(strip=True)
                        if label != '净利润':
                            continue

                        value = None
                        if col_idx + 1 < len(cells):
                            value = parse_number(cells[col_idx + 1].get_text(strip=True))

                        if value is None and row_idx + 1 < len(rows):
                            next_cells = rows[row_idx + 1].find_all(['td', 'th'])
                            candidate_indices = [col_idx]
                            if col_idx > 0 and len(next_cells) == len(cells) - 1:
                                candidate_indices.append(col_idx - 1)
                            for idx in candidate_indices:
                                if idx < len(next_cells):
                                    value = parse_number(next_cells[idx].get_text(strip=True))
                                    if value is not None:
                                        break

                        if value is None:
                            continue

                        candidates.append(
                            {
                                'value': value,
                                'priority': section_priority,
                                'year': section_year,
                            }
                        )

            if not candidates:
                return None
            best = max(candidates, key=lambda item: (item['priority'], item['year']))
            return best['value']

        def upsert_seller(seller_name: str, table_idx: int, row_idx: int) -> Dict[str, Any]:
            seller_name = str(seller_name or "").strip()
            existing = next((s for s in sellers_info if s.get('name') == seller_name), None)
            if existing is not None:
                return existing
            seller = {'name': seller_name, 'table': table_idx, 'row': row_idx}
            sellers_info.append(seller)
            return seller

        def normalize_name_for_match(raw_name: Any) -> str:
            name = str(raw_name or "").strip()
            if not name:
                return ""
            # Keep only core name features for matching between "转让方名称" and "股权结构" tables.
            name = re.sub(r"\s+", "", name)
            name = name.replace("（", "(").replace("）", ")")
            return name

        def append_remark(note: str) -> None:
            text = str(note or "").strip()
            if not text:
                return
            existing = str(self.data.get('备注') or "").strip()
            if not existing or existing in {"nan", "None"}:
                self.data['备注'] = text
                return
            if text in existing:
                return
            self.data['备注'] = f"{existing}；{text}"

        # 提取项目编号（从标题中）
        title = self.soup.find('title')
        if title:
            code = pick_project_code(title.text)
            if code:
                self.data['项目编号'] = code
        
        # 如果标题中没有，查找页面中的国资监测编号
        if '项目编号' not in self.data:
            for elem in self.soup.find_all(string=re.compile(r'国资监测编号')):
                code = pick_project_code(elem)
                if code:
                    self.data['项目编号'] = code
                    break
        
        # 兜底：从整页文本提取项目编号（兼容无“-挂牌次数”后缀的编号）
        if '项目编号' not in self.data:
            page_text = self.soup.get_text(" ", strip=True)
            code = pick_project_code(page_text)
            if code:
                self.data['项目编号'] = code
        
        # 提取项目名称
        is_capital_project = False
        project_name = self.soup.find('div', class_='title', id='js_projectName')
        if project_name:
            if '项目编号' not in self.data:
                code = pick_project_code(project_name.get_text(" ", strip=True))
                if code:
                    self.data['项目编号'] = code
            # 去掉括号中的编号
            name = re.sub(r'\(.*?\)', '', project_name.text)
            project_name_text = name.strip()
            self.data['项目名称'] = project_name_text
            if "增资" in project_name_text:
                is_capital_project = True
        
        # 从div中提取挂牌日期（深圳网页特定格式）
        gpqsrq = self.soup.find('span', id='gpqsrq')
        if gpqsrq:
            self.data['挂牌开始日期'] = self.clean_date(gpqsrq.get_text(strip=True))
        
        gpqmrq = self.soup.find('span', id='gpqmrq')
        if gpqmrq:
            self.data['挂牌截止日期'] = self.clean_date(gpqmrq.get_text(strip=True))

        # 旧模板日期字段（无 gpqsrq/gpqmrq）
        if '挂牌开始日期' not in self.data:
            start_span = self.soup.find('span', id='js_registerForm')
            if start_span:
                self.data['挂牌开始日期'] = self.clean_date(start_span.get_text(strip=True))
        if '挂牌截止日期' not in self.data:
            end_span = self.soup.find('span', id='js_registerTo')
            if end_span:
                self.data['挂牌截止日期'] = self.clean_date(end_span.get_text(strip=True))

        # 头部信息区兜底：日期、地区、挂牌价
        for info in self.soup.select('.bdinfo-item, .vab-info_bd-item'):
            info_text = info.get_text(" ", strip=True)
            if not info_text:
                continue

            if '挂牌起始日期' in info_text and '挂牌开始日期' not in self.data:
                match = re.search(r'挂牌起始日期[：:]\s*([0-9/\-]+)', info_text)
                if match:
                    self.data['挂牌开始日期'] = self.clean_date(match.group(1))

            if '挂牌截止日期' in info_text and '挂牌截止日期' not in self.data:
                match = re.search(r'挂牌截止日期[：:]\s*([0-9/\-]+)', info_text)
                if match:
                    self.data['挂牌截止日期'] = self.clean_date(match.group(1))

            if '标的位置' in info_text and '所在地区' not in self.data:
                match = re.search(r'标的位置[：:]\s*(.+)', info_text)
                if match:
                    self.data['所在地区'] = self.clean_region(match.group(1).strip())

            if '挂牌价' in info_text and '挂牌价格' not in self.data:
                parsed_price = parse_price_to_wan(info_text)
                if parsed_price not in (None, ""):
                    self.data['挂牌价格'] = parsed_price
            elif '挂牌金额' in info_text and '挂牌价格' not in self.data:
                parsed_price = parse_price_to_wan(info_text)
                if parsed_price not in (None, ""):
                    self.data['挂牌价格'] = parsed_price
        
        # 从表格中提取数据
        tables = self.soup.find_all('table')
        # 兼容多模板：净利润统一在遍历结束后按“年度优先，其次最近一期”规则提取。
        
        # 用于存储转让方和对应的持股比例
        sellers_info: List[Dict[str, Any]] = []
        shareholder_ratio_map: Dict[str, str] = {}
        has_ambiguous_total_ratio = False

        for table_idx, table in enumerate(tables):
            rows = table.find_all('tr')
            in_shareholder_section = False
            for row_idx, row in enumerate(rows):
                cells = row.find_all(['td', 'th'])
                row_texts = [c.get_text(strip=True) for c in cells]
                normalized_row = [re.sub(r"\s+", "", str(t or "")) for t in row_texts]

                if any('前十位股东名称' in t for t in normalized_row) and any('持有比例' in t for t in normalized_row):
                    in_shareholder_section = True
                    continue

                if in_shareholder_section and len(cells) >= 2:
                    holder_name = str(row_texts[0] or "").strip()
                    holder_ratio = normalize_ratio(row_texts[1] if len(row_texts) > 1 else "")
                    if holder_name and holder_ratio:
                        shareholder_ratio_map[normalize_name_for_match(holder_name)] = holder_ratio

                for col_idx, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    
                    # 项目编号/国资监测编号（兼容深圳页面出现CQ编号）
                    if (
                        ('项目编号' in text or '国资监测编号' in text)
                        and col_idx + 1 < len(cells)
                        and '项目编号' not in self.data
                    ):
                        code_text = cells[col_idx + 1].get_text(strip=True)
                        code = pick_project_code(code_text) or pick_project_code(f"{text} {code_text}")
                        if code:
                            self.data['项目编号'] = code
                    
                    # 转让底价
                    if '转让底价' in text and col_idx + 1 < len(cells):
                        self.data['挂牌价格'] = self.clean_price(cells[col_idx + 1].get_text(strip=True))
                    
                    # 所属行业
                    elif '所属行业' in text and col_idx + 1 < len(cells):
                        self.data['所属行业'] = cells[col_idx + 1].get_text(strip=True)
                    
                    # 挂牌日期
                    elif '挂牌起始日期' in text and col_idx + 1 < len(cells):
                        self.data['挂牌开始日期'] = self.clean_date(cells[col_idx + 1].get_text(strip=True))
                    elif '挂牌截止日期' in text and col_idx + 1 < len(cells):
                        self.data['挂牌截止日期'] = self.clean_date(cells[col_idx + 1].get_text(strip=True))
                    
                    # 所在地区 - 深交所使用"所在地区"标签
                    elif ('所在地区' in text or '标的企业所在地区' in text) and col_idx + 1 < len(cells):
                        region_text = cells[col_idx + 1].get_text(strip=True)
                        self.data['所在地区'] = normalize_region_text(region_text)

                    # 融资方（增资扩股）
                    elif is_financing_label(text) and col_idx + 1 < len(cells):
                        company_name = cells[col_idx + 1].get_text(strip=True)
                        if company_name:
                            is_capital_project = True
                            self.data['融资方'] = company_name
                            # 增资项目输出会复用“转让方”列，保持兼容。
                            if not self.data.get('转让方'):
                                self.data['转让方'] = company_name

                    # 持股比例（增资扩股）
                    elif is_capital_ratio_label(text) and col_idx + 1 < len(cells):
                        is_capital_project = True
                        ratio_raw = cells[col_idx + 1].get_text(strip=True)
                        ratio_value = normalize_capital_ratio(ratio_raw)
                        if ratio_value and not self.data.get('持股比例'):
                            self.data['持股比例'] = ratio_value
                     
                    # 转让方（处理多转让方情况）- 收集所有转让方名称
                    elif is_seller_label(text) and col_idx + 1 < len(cells):
                        seller_raw = cells[col_idx + 1].get_text(strip=True)
                        seller_names = split_inline_sellers(seller_raw)
                        for seller_name in seller_names:
                            upsert_seller(seller_name, table_idx, row_idx)
                            # 同时设置第一个转让方（兼容旧逻辑）
                            if '转让方' not in self.data:
                                self.data['转让方'] = seller_name
                    
                    # 查找拟转让产(股)权比例（在转让方名称后面的几行）
                    elif is_ratio_label(text) and col_idx + 1 < len(cells):
                        ratio = normalize_ratio(cells[col_idx + 1].get_text(strip=True))
                        if not ratio:
                            continue
                        table_sellers = [s for s in sellers_info if s.get('table') == table_idx]
                        # 多转让方但只出现一条100%时通常是合计占比，不应绑定到单个转让方。
                        if ratio in {'100%', '100.0%', '100.00%'} and len(table_sellers) > 1 and not any(s.get('ratio') for s in table_sellers):
                            has_ambiguous_total_ratio = True
                            continue
                        # 找到同一表格中最近的转让方（还没有比例的）
                        for seller in reversed(sellers_info):
                            if seller['table'] == table_idx and not seller.get('ratio'):
                                seller['ratio'] = ratio
                                break
                    
                    # 隶属集团
                    elif '国家出资企业' in text and col_idx + 1 < len(cells):
                        hq_name = cells[col_idx + 1].get_text(strip=True)
                        self.data['隶属集团'] = hq_name
                        # 找到对应的转让方并关联所属集团
                        for seller in sellers_info:
                            if seller['row'] < row_idx <= seller['row'] + 5 and 'hq' not in seller:
                                seller['hq'] = hq_name
                                break
                    
                    # 经办人
                    elif '联系人' in text:
                        # 联系人的值可能在当前单元格或下一列
                        if col_idx + 1 < len(cells):
                            contact = cells[col_idx + 1].get_text(strip=True)
                        else:
                            # 如果当前单元格包含"联系人："，提取后面的内容
                            contact = text
                        # 提取第一个联系人姓名
                        match = re.search(r'联系人[：:]\s*([\u4e00-\u9fa5·]{2,10}?)(?=\s*(?:联系电话|电话|$))', contact)
                        if match:
                            self.data['经办人'] = match.group(1)
                        else:
                            # 如果没有"联系人："前缀，提取第一个中文姓名
                            match = re.search(r'[\u4e00-\u9fa5·]{2,4}', contact)
                            if match:
                                self.data['经办人'] = match.group(0)
                    
                    # 净利润通过统一后处理提取，避免被版式差异干扰

        # 头部价格字段补抓（新模板常见）
        if '挂牌价格' not in self.data:
            for selector in ('#gpj', '.bd-price-area', '.vab-price', 'span.js_listingPriceUnits'):
                node = self.soup.select_one(selector)
                if not node:
                    continue
                parsed_price = parse_price_to_wan(node.get_text(" ", strip=True))
                if parsed_price not in (None, ""):
                    self.data['挂牌价格'] = parsed_price
                    break

        # 新模板联系方式兜底（如: <span id="contactName">李炫陶</span>）
        if '经办人' not in self.data or not str(self.data.get('经办人') or '').strip():
            contact_node = (
                self.soup.select_one('#contactName')
                or self.soup.select_one('[id*=contactName]')
                or self.soup.select_one('.lxfs-value#contactName')
            )
            if contact_node:
                contact_name = str(contact_node.get_text(strip=True) or '').strip()
                match = re.search(r'[\u4e00-\u9fa5·]{2,10}', contact_name)
                if match:
                    self.data['经办人'] = match.group(0)
        
        # 处理多转让方情况 - 将收集到的转让方信息合并
        if sellers_info:
            hq_strings = []  # 收集所属集团信息
            seller_strings = []
            for seller in sellers_info:
                seller_name = str(seller.get('name') or '').strip()
                if not seller_name:
                    continue
                ratio = normalize_ratio(seller.get('ratio'))
                if len(sellers_info) > 1 and ratio:
                    seller_strings.append(f"{seller_name}({ratio})")
                else:
                    seller_strings.append(seller_name)
                # 收集所属集团
                if 'hq' in seller and seller['hq']:
                    hq_strings.append(f"{seller['name']}隶属{seller['hq']}")
            if seller_strings:
                self.data['转让方'] = '，'.join(seller_strings) if len(sellers_info) > 1 else seller_strings[0]
            # 多转让方备注按“各转让方隶属集团”追加（满足人工复核口径）。
            if len(sellers_info) > 1 and hq_strings:
                unique_pairs = list(dict.fromkeys(hq_strings))
                if unique_pairs:
                    append_remark('；'.join(unique_pairs))

            # 特殊场景：仅披露合计拟转让比例（例如100%），无法确认各转让方对应比例。
            if len(sellers_info) > 1 and has_ambiguous_total_ratio:
                matched_ratios = []
                for seller in sellers_info:
                    seller_name = str(seller.get('name') or '').strip()
                    if not seller_name:
                        continue
                    ref_ratio = shareholder_ratio_map.get(normalize_name_for_match(seller_name))
                    if ref_ratio:
                        matched_ratios.append(f"{seller_name}持股{ref_ratio}")
                note = "多转让方仅披露合计拟转让比例，未明确各转让方拟转让比例，请人工校对"
                if matched_ratios:
                    note = f"{note}；股权结构参考：{'，'.join(matched_ratios)}"
                append_remark(note)

        # 增资项目兜底：由项目名称推断融资方。
        if is_capital_project and not self.data.get('融资方'):
            inferred_name = str(self.data.get('项目名称') or "").strip()
            for suffix in ("增资扩股项目", "增资项目", "增资扩股", "增资"):
                if inferred_name.endswith(suffix):
                    inferred_name = inferred_name[: -len(suffix)].strip()
                    break
            if inferred_name:
                self.data['融资方'] = inferred_name
                if not self.data.get('转让方'):
                    self.data['转让方'] = inferred_name
        
        profit_value = extract_profit_from_tables_prefer_annual(tables)
        if profit_value is not None:
            self.data['近一年净利润'] = profit_value

        # 深交所增资项目输出“融资金额”需保留网页原文口径。
        if is_capital_project and not str(self.data.get('融资金额') or '').strip():
            financing_node = self.soup.select_one('#gpj') or self.soup.select_one('.oldPrice') or self.soup.select_one('.price-value')
            if financing_node:
                raw_financing = parse_financing_amount(financing_node.get_text(" ", strip=True))
                if raw_financing:
                    self.data['融资金额'] = raw_financing

        # 联系人优先使用页面联系人组件（如“王经理”），覆盖正文中的“王先生”描述。
        contact_node = (
            self.soup.select_one('#contactName')
            or self.soup.select_one('[id*=contactName]')
            or self.soup.select_one('.lxfs-value#contactName')
        )
        if contact_node:
            contact_name = str(contact_node.get_text(strip=True) or '').strip()
            if contact_name:
                self.data['经办人'] = contact_name

        self._apply_package_view_sidecar_fields()

        if self.data.get('所在地区'):
            self.data['所在地区'] = normalize_region_text(self.data.get('所在地区'))
        
        # 设置交易所
        self.data['交易所'] = '深交所'
        
        return self.data
