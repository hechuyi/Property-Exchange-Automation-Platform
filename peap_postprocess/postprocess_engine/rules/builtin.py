"""Built-in rule set."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

from ..contracts import CanonicalRecord, Finding, Patch, RuleResult
from .base import BaseRule, RuleContext

FIELD_SOURCE_TYPE = "\u7c7b\u578b"
FIELD_OWNER_NATURE = "\u4f01\u4e1a\u6027\u8d28"
FIELD_ECONOMY_TYPE = "\u7ecf\u6d4e\u7c7b\u578b"
FIELD_REGULATOR = "\u56fd\u8d44\u76d1\u7ba1\u673a\u6784"
FIELD_GROUP_NAME = "\u96b6\u5c5e\u96c6\u56e2"
FIELD_GROUP_NAME_ALT = "\u6240\u5c5e\u96c6\u56e2"
FIELD_SELLER = "\u8f6c\u8ba9\u65b9"
FIELD_SELLER_NAME = "\u8f6c\u8ba9\u65b9\u540d\u79f0"
FIELD_FINANCING = "\u878d\u8d44\u65b9"
FIELD_FINANCING_NAME = "\u878d\u8d44\u65b9\u540d\u79f0"
FIELD_PROJECT_CODE = "\u9879\u76ee\u7f16\u53f7"
FIELD_LISTING_TIMES = "\u6302\u724c\u6b21\u6570"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", "", _clean(text))


def _first_existing(raw_fields: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        if key in raw_fields:
            return key
    return ""


def _first_non_empty(raw_fields: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = _clean(raw_fields.get(key))
        if value:
            return value
    return ""


def _normalize_column_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", _clean(name).replace("\ufeff", "").lower())


def _pick_row_value(row: Dict[str, Any], aliases: Iterable[str]) -> str:
    normalized = {_normalize_column_key(str(key)): row.get(key) for key in row.keys()}
    for alias in aliases:
        key = _normalize_column_key(alias)
        if key in normalized:
            return _clean(normalized[key])
    return ""


def _split_aliases(text: str) -> List[str]:
    value = _clean(text)
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;,，；|/]+", value) if item.strip()]


def _is_empty_like(value: Any) -> bool:
    text = _clean(value)
    if not text:
        return True
    if text.lower() in {"n/a", "na", "nil", "unknown", "pending"}:
        return True
    return text in {"-", "--", "—", "无", "暂无", "未披露", "待定"}


def _read_rows_from_data_file(
    path: str,
    *,
    sheet_name: str | None = None,
) -> List[Dict[str, Any]]:
    file_path = os.path.abspath(path)
    suffix = os.path.splitext(file_path)[1].lower()
    if suffix == ".json":
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            # Accept key-value map as one row per pair.
            rows = []
            for key, value in payload.items():
                rows.append({"key": key, "value": value})
            return rows
        return []

    if suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    elif suffix in {".xlsx", ".xls"}:
        if sheet_name in (None, ""):
            df = pd.read_excel(file_path, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str, keep_default_na=False)
    else:
        raise ValueError(f"unsupported table file extension: {suffix}")

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append({str(column): row.get(column, "") for column in df.columns})
    return rows


def _convert_listing_times_to_chinese(times: int) -> str:
    """Convert listing times number to Chinese format.
    
    E.g., 1 -> "首次挂牌", 2 -> "二次挂牌", 3 -> "三次挂牌", 12 -> "十二次挂牌", etc.
    """
    if times <= 0:
        return ""
    
    if times == 1:
        return "首次挂牌"
    
    # Chinese digit mapping
    digits = {
        1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
        6: "六", 7: "七", 8: "八", 9: "九", 0: "零"
    }
    
    # Handle single digit (2-9)
    if times < 10:
        return f"{digits[times]}次挂牌"
    
    # Handle 10-99
    if times < 100:
        tens = times // 10
        ones = times % 10
        if tens == 1:
            # 10-19: 十, 十一, 十二, ...
            if ones == 0:
                return "十次挂牌"
            else:
                return f"十{digits[ones]}次挂牌"
        else:
            # 20-99: 二十, 二十一, ...
            if ones == 0:
                return f"{digits[tens]}十次挂牌"
            else:
                return f"{digits[tens]}十{digits[ones]}次挂牌"
    
    # For times >= 100, use Arabic numeral format
    return f"{times}次挂牌"


def _is_valid_listing_times_value(value: str) -> bool:
    """Check if value is a valid listing times format (Chinese or numeric).
    
    Valid formats:
    - "首次挂牌" (first listing)
    - "{digit}次挂牌" (e.g., "二次挂牌", "三次挂牌")
    - "十{digit}次挂牌" or "十次挂牌" (10-19)
    - "{tens}十{ones}次挂牌" or "{tens}十次挂牌" (20-99)
    - Pure numbers (legacy compatibility)
    """
    value = _clean(value)
    if not value:
        return False
    
    # Check if it matches "N次挂牌" pattern with Chinese numerals
    # Matches: 首次挂牌, 二次挂牌, ..., 十次挂牌, 十一次挂牌, ..., 十九次挂牌, 二十次挂牌, etc.
    chinese_numeral_pattern = r"(首|[一二三四五六七八九]|十[一二三四五六七八九]?|[一二三四五六七八九]十[一二三四五六七八九]?)次挂牌"
    if re.fullmatch(chinese_numeral_pattern, value):
        return True
    
    # Check if it's a pure positive integer (legacy format)
    if re.fullmatch(r"\d+", value):
        try:
            return int(value) > 0
        except ValueError:
            return False
    
    return False


class _NoopRule(BaseRule):
    _id = ""

    @classmethod
    def rule_id(cls) -> str:
        return cls._id

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        return RuleResult()


class _GroupMappingSupport(BaseRule):
    _company_fields = [
        "company_name_primary",
        "company_name",
        "seller",
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        FIELD_FINANCING,
        FIELD_FINANCING_NAME,
    ]
    _group_fields = [
        FIELD_GROUP_NAME,
        FIELD_GROUP_NAME_ALT,
        "group_name",
    ]
    _mapping_company_keys = [
        "company",
        "company_name",
        "company_name_primary",
        "seller",
        "name",
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        FIELD_FINANCING,
        FIELD_FINANCING_NAME,
    ]
    _mapping_group_keys = [
        "group",
        "group_name",
        FIELD_GROUP_NAME,
        FIELD_GROUP_NAME_ALT,
        "\u96c6\u56e2\u540d\u79f0",
    ]

    def __init__(self, *, params: Dict[str, Any] | None = None) -> None:
        super().__init__(params=params)
        self._mapping_loaded = False
        self._mapping_error = ""
        self._mapping_error_reported = False
        self._mapping_single: Dict[str, str] = {}
        self._mapping_ambiguous: Dict[str, List[str]] = {}

    def _normalize_company(self, value: Any) -> str:
        case_sensitive = bool(self.params.get("case_sensitive", False))
        text = _normalize_space(str(value or ""))
        if not case_sensitive:
            text = text.lower()
        return text

    def _normalize_group(self, value: Any) -> str:
        return _normalize_space(str(value or ""))

    def _select_column(self, columns: List[str], aliases: List[str]) -> str:
        normalized = {_normalize_column_key(column): column for column in columns}
        for alias in aliases:
            key = _normalize_column_key(alias)
            if key in normalized:
                return normalized[key]
        return ""

    def _iter_mapping_rows_from_inline(self) -> Iterable[Tuple[str, str]]:
        inline = self.params.get("mapping_inline")
        if inline is None:
            return []
        rows: List[Tuple[str, str]] = []

        if isinstance(inline, dict):
            for company, group in inline.items():
                rows.append((_clean(company), _clean(group)))
            return rows

        if isinstance(inline, list):
            for item in inline:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    rows.append((_clean(item[0]), _clean(item[1])))
                    continue
                if not isinstance(item, dict):
                    continue
                company = _first_non_empty(item, self._mapping_company_keys)
                group = _first_non_empty(item, self._mapping_group_keys)
                rows.append((_clean(company), _clean(group)))
            return rows

        return rows

    def _iter_mapping_rows_from_dataframe(self, df: pd.DataFrame) -> Iterable[Tuple[str, str]]:
        if df is None or df.empty:
            return []
        columns = [str(column) for column in df.columns]
        company_col = self._select_column(columns, self._mapping_company_keys)
        group_col = self._select_column(columns, self._mapping_group_keys)

        if not company_col or not group_col:
            if len(columns) >= 2:
                company_col, group_col = columns[0], columns[1]
            else:
                raise ValueError("mapping file requires at least two columns: company/group")

        rows: List[Tuple[str, str]] = []
        for _, row in df.iterrows():
            company = _clean(row.get(company_col, ""))
            group = _clean(row.get(group_col, ""))
            if company and group:
                rows.append((company, group))
        return rows

    def _iter_mapping_rows_from_file(self) -> Iterable[Tuple[str, str]]:
        mapping_file = _clean(self.params.get("mapping_file"))
        if not mapping_file:
            return []
        file_path = os.path.abspath(mapping_file)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"mapping_file not found: {file_path}")

        suffix = os.path.splitext(file_path)[1].lower()
        if suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return [(_clean(k), _clean(v)) for k, v in payload.items()]
            if isinstance(payload, list):
                rows = []
                for item in payload:
                    if isinstance(item, dict):
                        company = _first_non_empty(item, self._mapping_company_keys)
                        group = _first_non_empty(item, self._mapping_group_keys)
                        if company and group:
                            rows.append((company, group))
                return rows
            raise ValueError("json mapping_file must be object or list")

        if suffix == ".csv":
            df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
            return self._iter_mapping_rows_from_dataframe(df)

        if suffix in {".xlsx", ".xls"}:
            sheet_name = self.params.get("mapping_sheet")
            if sheet_name in (None, ""):
                df = pd.read_excel(file_path, dtype=str, keep_default_na=False)
            else:
                df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str, keep_default_na=False)
            return self._iter_mapping_rows_from_dataframe(df)

        raise ValueError(f"unsupported mapping_file extension: {suffix}")

    def _load_mapping_once(self) -> None:
        if self._mapping_loaded:
            return
        self._mapping_loaded = True

        collected: Dict[str, set[str]] = {}
        try:
            for company, group in list(self._iter_mapping_rows_from_inline()) + list(
                self._iter_mapping_rows_from_file()
            ):
                company_name = _clean(company)
                group_name = _clean(group)
                if not company_name or not group_name:
                    continue
                normalized_company = self._normalize_company(company_name)
                if not normalized_company:
                    continue
                collected.setdefault(normalized_company, set()).add(group_name)
        except Exception as exc:  # noqa: BLE001
            self._mapping_error = str(exc) or exc.__class__.__name__
            return

        for key, group_set in collected.items():
            groups = sorted(group_set)
            if len(groups) == 1:
                self._mapping_single[key] = groups[0]
            elif len(groups) > 1:
                self._mapping_ambiguous[key] = groups

    def _resolve_company_name(self, record: CanonicalRecord) -> str:
        if _clean(record.company_name_primary):
            return _clean(record.company_name_primary)
        return _first_non_empty(record.raw_fields, self._company_fields)

    def _resolve_group_field(self, raw_fields: Dict[str, Any]) -> str:
        return _first_existing(raw_fields, self._group_fields) or FIELD_GROUP_NAME

    def _resolve_group_value(self, record: CanonicalRecord) -> str:
        if _clean(record.group_name):
            return _clean(record.group_name)
        return _first_non_empty(record.raw_fields, self._group_fields)

    def _lookup_mapping(self, company_name: str) -> Tuple[str, List[str]]:
        key = self._normalize_company(company_name)
        if not key:
            return "", []
        if key in self._mapping_ambiguous:
            return "", list(self._mapping_ambiguous[key])
        return _clean(self._mapping_single.get(key, "")), []

    def _build_mapping_error_finding(self) -> Finding:
        return Finding(
            rule_id=self.rule_id(),
            severity="warn",
            type="mapping_load_failed",
            message=f"group mapping load failed: {self._mapping_error}",
            evidence={},
        )


class R001GroupMappingFillRule(_GroupMappingSupport):
    _id = "R001_group_mapping_fill"

    @classmethod
    def rule_id(cls) -> str:
        return cls._id

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        self._load_mapping_once()
        patches: List[Patch] = []
        findings: List[Finding] = []

        if self._mapping_error and not self._mapping_error_reported:
            findings.append(self._build_mapping_error_finding())
            self._mapping_error_reported = True
            return RuleResult(patches=patches, findings=findings)

        company_name = self._resolve_company_name(record)
        if not company_name:
            return RuleResult(patches=patches, findings=findings)

        mapped_group, ambiguous_groups = self._lookup_mapping(company_name)
        if ambiguous_groups:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="group_mapping_ambiguous",
                    message=f"ambiguous group mapping for company: {company_name}",
                    evidence={"groups": ambiguous_groups},
                )
            )
            return RuleResult(patches=patches, findings=findings)

        if not mapped_group:
            if bool(self.params.get("emit_no_match", False)):
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="info",
                        type="group_mapping_no_match",
                        message=f"no group mapping match for company: {company_name}",
                        evidence={},
                    )
                )
            return RuleResult(patches=patches, findings=findings)

        current_group = self._resolve_group_value(record)
        if current_group:
            return RuleResult(patches=patches, findings=findings)

        target_field = self._resolve_group_field(record.raw_fields)
        patches.append(
            Patch(
                field=target_field,
                old_value=current_group,
                new_value=mapped_group,
                action="fill",
                reason="group_mapping_fill",
            )
        )
        return RuleResult(patches=patches, findings=findings)


class R002GroupConflictFlagRule(_GroupMappingSupport):
    _id = "R002_group_conflict_flag"

    @classmethod
    def rule_id(cls) -> str:
        return cls._id

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        self._load_mapping_once()
        patches: List[Patch] = []
        findings: List[Finding] = []

        if self._mapping_error and not self._mapping_error_reported:
            findings.append(self._build_mapping_error_finding())
            self._mapping_error_reported = True
            return RuleResult(patches=patches, findings=findings)

        company_name = self._resolve_company_name(record)
        if not company_name:
            return RuleResult(patches=patches, findings=findings)

        mapped_group, ambiguous_groups = self._lookup_mapping(company_name)
        if ambiguous_groups:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="group_mapping_ambiguous",
                    message=f"ambiguous group mapping for company: {company_name}",
                    evidence={"groups": ambiguous_groups},
                )
            )
            return RuleResult(patches=patches, findings=findings)
        if not mapped_group:
            return RuleResult(patches=patches, findings=findings)

        current_group = self._resolve_group_value(record)
        if not current_group:
            return RuleResult(patches=patches, findings=findings)

        if self._normalize_group(current_group) == self._normalize_group(mapped_group):
            return RuleResult(patches=patches, findings=findings)

        strategy = _clean(self.params.get("conflict_strategy", "keep_original_and_flag")).lower()
        if strategy not in {"keep_original_and_flag", "prefer_mapping"}:
            strategy = "keep_original_and_flag"

        if strategy == "prefer_mapping":
            target_field = self._resolve_group_field(record.raw_fields)
            patches.append(
                Patch(
                    field=target_field,
                    old_value=current_group,
                    new_value=mapped_group,
                    action="conflict_resolve",
                    reason="prefer_mapping",
                )
            )
            finding_type = "group_conflict_resolved"
        else:
            finding_type = "group_conflict"

        findings.append(
            Finding(
                rule_id=self.rule_id(),
                severity="warn",
                type=finding_type,
                message=(
                    f"group conflict company={company_name}, "
                    f"current={current_group}, mapped={mapped_group}, strategy={strategy}"
                ),
                evidence={
                    "company_name": company_name,
                    "current_group": current_group,
                    "mapped_group": mapped_group,
                    "strategy": strategy,
                },
            )
        )
        return RuleResult(patches=patches, findings=findings)


class R003CompanyNameNormalizeRule(_NoopRule):
    _id = "R003_company_name_normalize"
    _company_field_candidates = [
        "company_name_primary",
        "company_name",
        "标的企业名称",
        "标的企业",
        "企业名称",
        FIELD_SELLER_NAME,
        FIELD_FINANCING_NAME,
        FIELD_SELLER,
        FIELD_FINANCING,
        "seller",
    ]
    _label_prefixes = [
        "公司名称",
        "企业名称",
        "标的企业名称",
        "标的企业",
        "转让方名称",
        "转让方",
        "融资方名称",
        "融资方",
    ]

    @classmethod
    def _strip_label_prefix(cls, text: str) -> str:
        value = _clean(text)
        if not value:
            return ""
        for prefix in cls._label_prefixes:
            match = re.match(rf"^{re.escape(prefix)}\s*[:：]\s*(.+)$", value)
            if match:
                return _clean(match.group(1))
        return value

    @staticmethod
    def _normalize_company_name(text: str, *, remove_inner_spaces_for_cjk: bool) -> str:
        value = _clean(text)
        if not value:
            return ""

        value = value.replace("\u3000", " ")
        value = value.strip("“”\"'`")
        value = re.sub(r"\s*([()（）])\s*", r"\1", value)
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r"[;；，,。]+$", "", value).strip()

        if remove_inner_spaces_for_cjk and re.search(r"[\u4e00-\u9fff]", value):
            value = re.sub(r"\s+", "", value)
        return _clean(value)

    def _select_target_field(self, raw_fields: Dict[str, Any]) -> str:
        explicit_field = _clean(self.params.get("target_field"))
        if explicit_field:
            return explicit_field
        for field in self._company_field_candidates:
            if _clean(raw_fields.get(field)):
                return field
        return ""

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        target_field = self._select_target_field(raw_fields)
        if not target_field:
            return RuleResult()

        original = _clean(raw_fields.get(target_field))
        if not original:
            return RuleResult()

        skip_multi_value = bool(self.params.get("skip_multi_value", True))
        emit_skip_finding = bool(self.params.get("emit_multi_value_skip", False))
        if skip_multi_value:
            tokens = [item for item in re.split(r"[，,、；;/|]+", original) if _clean(item)]
            if len(tokens) > 1:
                if emit_skip_finding:
                    return RuleResult(
                        findings=[
                            Finding(
                                rule_id=self.rule_id(),
                                severity="info",
                                type="company_name_multi_value_skip",
                                message=f"skip company_name normalize for multi-value field: {target_field}",
                                evidence={"value": original},
                            )
                        ]
                    )
                return RuleResult()

        remove_inner_spaces_for_cjk = bool(self.params.get("remove_inner_spaces_for_cjk", True))
        normalized = self._strip_label_prefix(original)
        normalized = self._normalize_company_name(
            normalized,
            remove_inner_spaces_for_cjk=remove_inner_spaces_for_cjk,
        )

        alias_map = self.params.get("alias_map")
        if isinstance(alias_map, dict) and normalized:
            mapped = _clean(alias_map.get(_normalize_space(normalized).lower(), ""))
            if mapped:
                normalized = mapped

        patches: List[Patch] = []
        findings: List[Finding] = []
        if not normalized:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="company_name_empty_after_normalize",
                    message=f"company name becomes empty after normalize: field={target_field}",
                    evidence={"value": original},
                )
            )
            return RuleResult(patches=patches, findings=findings)

        if normalized != original:
            patches.append(
                Patch(
                    field=target_field,
                    old_value=original,
                    new_value=normalized,
                    action="normalize",
                    reason="normalize_company_name",
                )
            )

        return RuleResult(patches=patches, findings=findings)


class R004NormalizeValuesRule(_NoopRule):
    _id = "R004_normalize_values"
    _seller_fields = [FIELD_SELLER, FIELD_SELLER_NAME, FIELD_FINANCING, FIELD_FINANCING_NAME, "seller"]
    _remark_fields = ["备注", "remark"]

    @staticmethod
    def _split_top_level(text: str) -> List[str]:
        parts: List[str] = []
        buffer: List[str] = []
        depth = 0
        separators = {"，", ",", "、", "；", ";", "/", "|", "\n", "\t"}
        for ch in text:
            if ch in {"(", "（"}:
                depth += 1
                buffer.append(ch)
                continue
            if ch in {")", "）"}:
                depth = max(0, depth - 1)
                buffer.append(ch)
                continue
            if depth == 0 and ch in separators:
                token = _clean("".join(buffer))
                if token:
                    parts.append(token)
                buffer = []
                continue
            buffer.append(ch)

        token = _clean("".join(buffer))
        if token:
            parts.append(token)

        expanded: List[str] = []
        for item in parts:
            if ("(" not in item and "（" not in item) and any(k in item for k in ("和", "及", "与")):
                chunks = [x.strip() for x in re.split(r"\s*(?:和|及|与)\s*", item) if x.strip()]
                expanded.extend(chunks if chunks else [item])
            else:
                expanded.append(item)
        return expanded

    @staticmethod
    def _parse_seller_token(token: str) -> Tuple[str, str]:
        text = _clean(token).strip("；;，,")
        if not text:
            return "", ""

        ratio = ""
        match = re.match(r"^(.*?)[（(]\s*([0-9]+(?:\.[0-9]+)?%?)\s*[）)]$", text)
        if match:
            name = _clean(match.group(1))
            ratio = _clean(match.group(2))
            if ratio and not ratio.endswith("%"):
                ratio += "%"
            return name, ratio

        match_percent = re.search(r"([0-9]+(?:\.[0-9]+)?%)", text)
        if match_percent:
            ratio = _clean(match_percent.group(1))
            name = _clean(text.replace(match_percent.group(1), ""))
            return name, ratio

        return text, ""

    @staticmethod
    def _format_seller(name: str, ratio: str) -> str:
        name_text = _clean(name)
        ratio_text = _clean(ratio)
        if not name_text:
            return ""
        if not ratio_text:
            return name_text
        return f"{name_text}({ratio_text})"

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        seller_field = _first_existing(raw_fields, self._seller_fields)
        if not seller_field:
            return RuleResult()

        original = _clean(raw_fields.get(seller_field))
        if not original:
            return RuleResult()

        tokens = self._split_top_level(original)
        if not tokens:
            return RuleResult()

        merged: Dict[str, str] = {}
        ratio_conflicts: List[Tuple[str, str, str]] = []
        for token in tokens:
            name, ratio = self._parse_seller_token(token)
            if not name:
                continue
            key = _normalize_space(name).lower()
            prev_ratio = merged.get(key, "")
            if not prev_ratio and ratio:
                merged[key] = ratio
            elif prev_ratio and ratio and prev_ratio != ratio:
                ratio_conflicts.append((name, prev_ratio, ratio))
            elif key not in merged:
                merged[key] = ratio

        if not merged:
            return RuleResult()

        normalized_items = []
        # keep original order by scanning tokens again
        used = set()
        for token in tokens:
            name, _ratio = self._parse_seller_token(token)
            key = _normalize_space(name).lower()
            if not key or key in used or key not in merged:
                continue
            used.add(key)
            normalized_items.append(self._format_seller(name, merged[key]))

        normalized = "；".join([item for item in normalized_items if item])
        if not normalized:
            normalized = original

        patches: List[Patch] = []
        findings: List[Finding] = []
        if normalized != original:
            patches.append(
                Patch(
                    field=seller_field,
                    old_value=original,
                    new_value=normalized,
                    action="normalize",
                    reason="normalize_multi_seller",
                )
            )

        if len(normalized_items) > 1:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="info",
                    type="multi_seller_detected",
                    message=f"multi seller normalized count={len(normalized_items)}",
                    evidence={"field": seller_field, "value": normalized},
                )
            )
            if bool(self.params.get("append_multi_seller_note", False)):
                remark_field = _first_existing(raw_fields, self._remark_fields) or "备注"
                old_remark = _clean(raw_fields.get(remark_field))
                add_note = f"多转让方({len(normalized_items)})"
                if add_note not in old_remark:
                    new_remark = add_note if not old_remark else f"{old_remark}；{add_note}"
                    patches.append(
                        Patch(
                            field=remark_field,
                            old_value=old_remark,
                            new_value=new_remark,
                            action="annotate",
                            reason="multi_seller_note",
                        )
                    )

        for seller_name, left_ratio, right_ratio in ratio_conflicts:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="multi_seller_ratio_conflict",
                    message=f"seller ratio conflict: {seller_name} ({left_ratio} vs {right_ratio})",
                    evidence={"field": seller_field},
                )
            )

        return RuleResult(patches=patches, findings=findings)


class R005NormalizeSourceTypeRule(_NoopRule):
    _id = "R005_normalize_source_type"
    _source_field_candidates = [FIELD_SOURCE_TYPE, "source_type", "SOURCE_TYPE"]
    _context_field_candidates = [
        FIELD_SOURCE_TYPE,
        FIELD_OWNER_NATURE,
        FIELD_ECONOMY_TYPE,
        FIELD_REGULATOR,
        FIELD_GROUP_NAME,
        FIELD_SELLER,
        FIELD_FINANCING,
        FIELD_SELLER_NAME,
        FIELD_FINANCING_NAME,
        "seller",
    ]
    _group_field_candidates = [FIELD_GROUP_NAME, FIELD_GROUP_NAME_ALT, "group_name"]
    _company_field_candidates = [
        "company_name_primary",
        "company_name",
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        FIELD_FINANCING,
        FIELD_FINANCING_NAME,
        "seller",
    ]
    _ministry_field_candidates = ["主管部委", "部委", "部委名称", "ministry_name", "ministry"]
    _allowed = {"央企", "部委", "市属", "民营"}

    _entity_map_name_aliases = [
        "entity_name",
        "name",
        "group_name",
        "group",
        FIELD_GROUP_NAME,
        FIELD_GROUP_NAME_ALT,
        "集团名称",
        "ministry_name",
        "ministry",
        "主管部委",
        "部委",
        "部委名称",
        "名称",
    ]
    _entity_map_kind_aliases = ["entity_kind", "kind", "entity_type", "主体类型", "对象类型"]
    _entity_map_type_aliases = ["source_type", "type", "category", "类别", "类型"]
    _transferor_type_name_aliases = [
        "transferor_name",
        "transferor",
        "seller_name",
        "seller",
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        "company_name",
        "name",
    ]
    _transferor_group_name_aliases = [
        "transferor_name",
        "transferor",
        "seller_name",
        "seller",
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        "company_name",
        "name",
    ]
    _transferor_group_group_aliases = ["group_name", "group", FIELD_GROUP_NAME, FIELD_GROUP_NAME_ALT, "group"]
    _group_parent_group_aliases = [
        "group_name",
        "group",
        FIELD_GROUP_NAME,
        FIELD_GROUP_NAME_ALT,
        "child_group_name",
        "child_group",
    ]
    _group_parent_parent_aliases = ["parent_group_name", "parent_group", "parent_name", "group_parent"]
    _legacy_group_name_aliases = ["group_name", "group", FIELD_GROUP_NAME, FIELD_GROUP_NAME_ALT, "集团名称"]
    _legacy_ministry_name_aliases = ["ministry_name", "ministry", "主管部委", "部委", "部委名称"]
    _central_whitelist_aliases = ["entity_name", "name", "company_name", "group_name", "名称"]
    _ministry_name_aliases = ["ministry_name", "name", "部委", "部委名称"]
    _ministry_aliases_aliases = ["aliases", "alias", "别名", "同义词"]

    def __init__(self, *, params: Dict[str, Any] | None = None) -> None:
        super().__init__(params=params)
        self._tables_loaded = False
        self._table_error_reported = False
        self._table_errors: List[str] = []
        self._entity_type_single: Dict[Tuple[str, str], str] = {}
        self._entity_type_ambiguous: Dict[Tuple[str, str], List[str]] = {}
        self._transferor_group_single: Dict[str, str] = {}
        self._transferor_group_ambiguous: Dict[str, List[str]] = {}
        self._group_parent_single: Dict[str, str] = {}
        self._group_parent_ambiguous: Dict[str, List[str]] = {}
        self._central_whitelist: set[str] = set()
        self._ministry_alias_to_name: Dict[str, str] = {}

    @staticmethod
    def _normalize_entity(value: Any) -> str:
        return _normalize_space(str(value or "")).lower()

    def _normalize_source_type_value(self, value: str) -> str:
        raw = _clean(value)
        if raw in self._allowed:
            return raw
        return self._alias_map(raw)

    def _normalize_entity_kind(self, value: str, *, row: Dict[str, Any] | None = None) -> str:
        raw = _clean(value)
        key = _normalize_column_key(raw)
        kind_map = {
            "group": "group",
            "集团": "group",
            "company": "company",
            "公司": "company",
            "企业": "company",
            "transferor": "company",
            "seller": "company",
            "转让方": "company",
            "ministry": "ministry",
            "department": "ministry",
            "部委": "ministry",
            "主管部门": "ministry",
            "any": "any",
            "all": "any",
            "both": "any",
            "全部": "any",
        }
        if key in kind_map:
            return kind_map[key]

        if row:
            legacy_group = _pick_row_value(row, self._legacy_group_name_aliases)
            legacy_ministry = _pick_row_value(row, self._legacy_ministry_name_aliases)
            if legacy_ministry and not legacy_group:
                return "ministry"
        return "group"

    def _load_tables_once(self) -> None:
        if self._tables_loaded:
            return
        self._tables_loaded = True
        self._load_entity_type_mapping()
        self._load_transferor_type_mapping()
        self._load_transferor_group_mapping()
        self._load_group_group_mapping()
        self._load_central_whitelist()
        self._load_ministry_reference()

    def _resolve_table_path(self, *keys: str) -> str:
        for key in keys:
            value = _clean(self.params.get(key))
            if value:
                return value
        return ""

    def _resolve_sheet_name(self, *keys: str) -> str:
        for key in keys:
            value = _clean(self.params.get(key))
            if value:
                return value
        return ""

    def _load_entity_type_mapping(self) -> None:
        table_path = self._resolve_table_path("entity_type_mapping_file", "group_type_mapping_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"entity_type_mapping_file not found: {table_path}")
            return

        sheet_name = self._resolve_sheet_name("entity_type_mapping_sheet", "group_type_mapping_sheet")
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"entity_type_mapping_file load failed: {exc}")
            return

        bucket: Dict[Tuple[str, str], set[str]] = {}
        for row in rows:
            entity_name = _pick_row_value(row, self._entity_map_name_aliases)
            source_type = self._normalize_source_type_value(_pick_row_value(row, self._entity_map_type_aliases))
            if not entity_name or not source_type:
                continue
            entity_kind = self._normalize_entity_kind(_pick_row_value(row, self._entity_map_kind_aliases), row=row)
            entity_key = self._normalize_entity(entity_name)
            if not entity_key:
                continue
            bucket.setdefault((entity_kind, entity_key), set()).add(source_type)

        for map_key, source_types in bucket.items():
            options = sorted(source_types)
            if len(options) == 1:
                self._entity_type_single[map_key] = options[0]
            elif len(options) > 1:
                self._entity_type_ambiguous[map_key] = options

    def _merge_entity_type_mapping(
        self,
        *,
        entity_kind: str,
        entity_name: str,
        source_type: str,
        bucket: Dict[Tuple[str, str], set[str]],
    ) -> None:
        kind = _clean(entity_kind)
        name = _clean(entity_name)
        normalized_type = self._normalize_source_type_value(_clean(source_type))
        if not kind or not name or not normalized_type:
            return
        key = (kind, self._normalize_entity(name))
        if not key[1]:
            return
        bucket.setdefault(key, set()).add(normalized_type)

    def _load_transferor_type_mapping(self) -> None:
        table_path = self._resolve_table_path("transferor_type_mapping_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"transferor_type_mapping_file not found: {table_path}")
            return

        sheet_name = self._resolve_sheet_name("transferor_type_mapping_sheet")
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"transferor_type_mapping_file load failed: {exc}")
            return

        bucket: Dict[Tuple[str, str], set[str]] = {}
        for row in rows:
            transferor_name = _pick_row_value(row, self._transferor_type_name_aliases)
            source_type = _pick_row_value(row, self._entity_map_type_aliases)
            self._merge_entity_type_mapping(
                entity_kind="company",
                entity_name=transferor_name,
                source_type=source_type,
                bucket=bucket,
            )

        for map_key, source_types in bucket.items():
            options = sorted(source_types)
            if len(options) == 1:
                self._entity_type_single[map_key] = options[0]
            elif len(options) > 1:
                self._entity_type_ambiguous[map_key] = options

    def _load_transferor_group_mapping(self) -> None:
        table_path = self._resolve_table_path("transferor_group_mapping_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"transferor_group_mapping_file not found: {table_path}")
            return

        sheet_name = self._resolve_sheet_name("transferor_group_mapping_sheet")
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"transferor_group_mapping_file load failed: {exc}")
            return

        bucket: Dict[str, set[str]] = {}
        for row in rows:
            transferor_name = _pick_row_value(row, self._transferor_group_name_aliases)
            mapped_group = _pick_row_value(row, self._transferor_group_group_aliases)
            transferor_key = self._normalize_entity(transferor_name)
            if not transferor_key or not _clean(mapped_group):
                continue
            bucket.setdefault(transferor_key, set()).add(_clean(mapped_group))

        for transferor_key, groups in bucket.items():
            options = sorted(groups)
            if len(options) == 1:
                self._transferor_group_single[transferor_key] = options[0]
            elif len(options) > 1:
                self._transferor_group_ambiguous[transferor_key] = options

    def _load_group_group_mapping(self) -> None:
        table_path = self._resolve_table_path("group_group_mapping_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"group_group_mapping_file not found: {table_path}")
            return

        sheet_name = self._resolve_sheet_name("group_group_mapping_sheet")
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"group_group_mapping_file load failed: {exc}")
            return

        bucket: Dict[str, set[str]] = {}
        for row in rows:
            group_name = _pick_row_value(row, self._group_parent_group_aliases)
            parent_group = _pick_row_value(row, self._group_parent_parent_aliases)
            group_key = self._normalize_entity(group_name)
            if not group_key or not _clean(parent_group):
                continue
            bucket.setdefault(group_key, set()).add(_clean(parent_group))

        for group_key, parents in bucket.items():
            options = sorted(parents)
            if len(options) == 1:
                self._group_parent_single[group_key] = options[0]
            elif len(options) > 1:
                self._group_parent_ambiguous[group_key] = options

    def _lookup_transferor_group(self, transferor_name: str) -> Tuple[str, List[str]]:
        key = self._normalize_entity(transferor_name)
        if not key:
            return "", []
        if key in self._transferor_group_ambiguous:
            return "", list(self._transferor_group_ambiguous[key])
        return _clean(self._transferor_group_single.get(key, "")), []

    def _resolve_group_parent(self, group_name: str) -> Tuple[str, List[str], bool]:
        current = _clean(group_name)
        if not current:
            return "", [], False
        visited: set[str] = set()
        while current:
            key = self._normalize_entity(current)
            if not key:
                return current, [], False
            if key in visited:
                return current, [], True
            visited.add(key)
            if key in self._group_parent_ambiguous:
                return current, list(self._group_parent_ambiguous[key]), False
            parent = _clean(self._group_parent_single.get(key))
            if not parent:
                return current, [], False
            current = parent
        return "", [], False

    @staticmethod
    def _collect_transferor_candidates(company_value: str) -> List[str]:
        value = _clean(company_value)
        if not value:
            return []
        # Keep ASCII comma as part of entity name (for example "Co., Limited").
        parts = [item.strip() for item in re.split(r"[，、；;/|\s]+", value) if _clean(item)]
        cleaned: List[Tuple[str, float | None]] = []
        seen = set()
        for item in parts:
            ratio_value: float | None = None
            token = _clean(item)
            # "公司A(40%)" / "公司A（40）" / "公司A 40%" -> name + ratio
            enclosed = re.search(r"^(.*?)\s*[（(]\s*(\d+(?:\.\d+)?)\s*%?\s*[）)]\s*$", token)
            if enclosed:
                token = _clean(enclosed.group(1))
                ratio_value = float(enclosed.group(2))
            else:
                trailing = re.search(r"^(.*?)\s+(\d+(?:\.\d+)?)\s*%\s*$", token)
                if trailing:
                    token = _clean(trailing.group(1))
                    ratio_value = float(trailing.group(2))

            name = token.strip()
            if not name:
                continue
            key = _normalize_space(name).lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append((name, ratio_value))

        if len(cleaned) > 1:
            with_ratio = [item for item in cleaned if item[1] is not None]
            if with_ratio:
                top_name, _top_ratio = max(with_ratio, key=lambda item: item[1] or 0.0)
                return [top_name]

        return [name for name, _ratio in cleaned]

    def _lookup_entity_type(self, entity_name: str, entity_kind: str) -> Tuple[str, List[str]]:
        entity_key = self._normalize_entity(entity_name)
        if not entity_key:
            return "", []

        resolved_types: set[str] = set()
        ambiguous_types: set[str] = set()
        lookup_keys = [(entity_kind, entity_key)]
        if entity_kind != "any":
            lookup_keys.append(("any", entity_key))

        for map_key in lookup_keys:
            if map_key in self._entity_type_ambiguous:
                ambiguous_types.update(self._entity_type_ambiguous[map_key])
            if map_key in self._entity_type_single:
                resolved_types.add(self._entity_type_single[map_key])

        if ambiguous_types:
            return "", sorted(ambiguous_types)
        if len(resolved_types) == 1:
            return next(iter(resolved_types)), []
        if len(resolved_types) > 1:
            return "", sorted(resolved_types)
        return "", []

    def _load_central_whitelist(self) -> None:
        table_path = self._resolve_table_path("central_whitelist_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"central_whitelist_file not found: {table_path}")
            return

        sheet_name = _clean(self.params.get("central_whitelist_sheet"))
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"central_whitelist_file load failed: {exc}")
            return

        for row in rows:
            entity_name = _pick_row_value(row, self._central_whitelist_aliases)
            key = self._normalize_entity(entity_name)
            if key:
                self._central_whitelist.add(key)

    def _load_ministry_reference(self) -> None:
        table_path = self._resolve_table_path("ministry_reference_file")
        if not table_path:
            return
        if not os.path.exists(table_path):
            self._table_errors.append(f"ministry_reference_file not found: {table_path}")
            return

        sheet_name = _clean(self.params.get("ministry_reference_sheet"))
        try:
            rows = _read_rows_from_data_file(table_path, sheet_name=sheet_name or None)
        except Exception as exc:  # noqa: BLE001
            self._table_errors.append(f"ministry_reference_file load failed: {exc}")
            return

        alias_conflicts: List[str] = []
        for row in rows:
            ministry_name = _pick_row_value(row, self._ministry_name_aliases)
            if not ministry_name:
                continue
            aliases = [ministry_name, *_split_aliases(_pick_row_value(row, self._ministry_aliases_aliases))]
            for alias in aliases:
                key = self._normalize_entity(alias)
                if not key:
                    continue
                existed = self._ministry_alias_to_name.get(key)
                if existed and existed != ministry_name:
                    alias_conflicts.append(f"{alias}: {existed} vs {ministry_name}")
                    continue
                self._ministry_alias_to_name[key] = ministry_name
        for item in alias_conflicts:
            self._table_errors.append(f"ministry alias conflict: {item}")

    def _detect_ministries_from_text(self, text: str) -> List[str]:
        if not self._ministry_alias_to_name:
            return []
        merged = self._normalize_entity(text)
        if not merged:
            return []
        hits = []
        for alias_key, ministry_name in self._ministry_alias_to_name.items():
            if alias_key and alias_key in merged and ministry_name not in hits:
                hits.append(ministry_name)
        return hits

    def _canonicalize_ministry(self, text: str) -> str:
        value = _clean(text)
        if not value:
            return ""
        key = self._normalize_entity(value)
        return _clean(self._ministry_alias_to_name.get(key) or value)

    def _collect_ministry_candidates(
        self,
        *,
        raw_fields: Dict[str, Any],
        merged_text: str,
        group_value: str,
        company_value: str,
    ) -> List[str]:
        candidates: List[str] = []
        ministry_field = _first_existing(raw_fields, self._ministry_field_candidates)
        if ministry_field:
            raw_ministry = _clean(raw_fields.get(ministry_field))
            if raw_ministry:
                for item in _split_aliases(raw_ministry):
                    value = self._canonicalize_ministry(item)
                    if value and value not in candidates:
                        candidates.append(value)

        detected = self._detect_ministries_from_text(" ".join([merged_text, group_value, company_value]))
        for item in detected:
            value = self._canonicalize_ministry(item)
            if value and value not in candidates:
                candidates.append(value)
        return candidates

    @staticmethod
    def _infer(text: str) -> str:
        if any(k in text for k in ("民营", "私营", "民企", "非国有")):
            return "民营"
        if any(k in text for k in ("部委", "财政部", "国家部委", "中央其他部委")):
            return "部委"
        if any(k in text for k in ("央企", "国务院", "中央", "国务院国资委")):
            return "央企"
        if any(
            k in text
            for k in (
                "省属",
                "省级",
                "市属",
                "市级",
                "地方国资",
                "省国资委",
                "市国资委",
                "地方国资委",
            )
        ):
            return "市属"
        return ""

    @staticmethod
    def _alias_map(raw_value: str) -> str:
        alias_map = {
            "中央企业": "央企",
            "部委监管": "部委",
            "省属": "市属",
            "省级": "市属",
            "地方国资": "市属",
            "国有": "市属",
            "国资": "市属",
            "民企": "民营",
            "私企": "民营",
            "私营": "民营",
            "非国有": "民营",
        }
        for key, value in alias_map.items():
            if key in raw_value:
                return value
        return ""

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        self._load_tables_once()
        raw_fields = dict(record.raw_fields or {})
        target_field = _first_existing(raw_fields, self._source_field_candidates) or FIELD_SOURCE_TYPE
        raw_value = _clean(raw_fields.get(target_field))
        merged = " ".join(
            _clean(raw_fields.get(key)) for key in self._context_field_candidates if _clean(raw_fields.get(key))
        )
        group_field = _first_existing(raw_fields, self._group_field_candidates) or FIELD_GROUP_NAME
        group_value = _first_non_empty(raw_fields, self._group_field_candidates) or _clean(record.group_name)
        company_value = _first_non_empty(raw_fields, self._company_field_candidates) or _clean(
            record.company_name_primary
        )
        transferor_candidates = self._collect_transferor_candidates(company_value)

        patches: List[Patch] = []
        findings: List[Finding] = []
        if self._table_errors and not self._table_error_reported:
            for message in self._table_errors[:5]:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="source_type_table_error",
                        message=message,
                        evidence={},
                    )
                )
            self._table_error_reported = True

        base_group = group_value
        if not base_group and transferor_candidates:
            mapped_groups: List[str] = []
            for transferor_name in transferor_candidates:
                mapped_group, ambiguous_groups = self._lookup_transferor_group(transferor_name)
                if ambiguous_groups:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id(),
                            severity="warn",
                            type="transferor_group_mapping_ambiguous",
                            message=f"transferor group mapping ambiguous: transferor={transferor_name}",
                            evidence={"options": ambiguous_groups},
                        )
                    )
                    continue
                if mapped_group:
                    mapped_groups.append(mapped_group)
            unique_groups = sorted({item for item in mapped_groups if item})
            if len(unique_groups) == 1:
                base_group = unique_groups[0]
            elif len(unique_groups) > 1:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="transferor_group_conflict",
                        message=f"transferor group mapping conflict: {unique_groups}",
                        evidence={"transferors": transferor_candidates},
                    )
                )

        effective_group = base_group
        if base_group:
            resolved_group, ambiguous_parents, cycle_detected = self._resolve_group_parent(base_group)
            if ambiguous_parents:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="group_group_mapping_ambiguous",
                        message=f"group parent mapping ambiguous: group={base_group}",
                        evidence={"options": ambiguous_parents},
                    )
                )
            elif cycle_detected:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="group_group_mapping_cycle",
                        message=f"group parent mapping cycle detected: group={base_group}",
                        evidence={},
                    )
                )
            elif resolved_group:
                effective_group = resolved_group

        if effective_group and effective_group != group_value:
            patches.append(
                Patch(
                    field=group_field,
                    old_value=group_value,
                    new_value=effective_group,
                    action="fill" if not group_value else "normalize",
                    reason=(
                        "fill_group_by_transferor_mapping"
                        if not group_value
                        else "normalize_group_by_parent_mapping"
                    ),
                )
            )
            group_value = effective_group

        ministry_candidates = self._collect_ministry_candidates(
            raw_fields=raw_fields,
            merged_text=merged,
            group_value=group_value,
            company_value=company_value,
        )

        current_valid = raw_value if raw_value in self._allowed else ""
        if not current_valid and raw_value:
            current_valid = self._alias_map(raw_value)

        derived_type = ""
        derived_source = ""
        derived_ministry = ""
        mapping_conflict = False

        mapping_types: List[str] = []
        mapping_evidences: List[Dict[str, str]] = []
        mapped_ministry_hits: List[str] = []

        for transferor_name in transferor_candidates:
            mapped_type, ambiguous_types = self._lookup_entity_type(transferor_name, "company")
            if ambiguous_types:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="entity_type_mapping_ambiguous",
                        message=f"entity type mapping ambiguous: transferor={transferor_name}",
                        evidence={"entity_kind": "company", "options": ambiguous_types},
                    )
                )
                continue
            if mapped_type:
                mapping_types.append(mapped_type)
                mapping_evidences.append(
                    {"entity_kind": "company", "entity_name": transferor_name, "source_type": mapped_type}
                )

        if group_value and not mapping_types:
            mapped_type, ambiguous_types = self._lookup_entity_type(group_value, "group")
            if ambiguous_types:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="entity_type_mapping_ambiguous",
                        message=f"entity type mapping ambiguous: group={group_value}",
                        evidence={"entity_kind": "group", "options": ambiguous_types},
                    )
                )
            elif mapped_type:
                mapping_types.append(mapped_type)
                mapping_evidences.append(
                    {"entity_kind": "group", "entity_name": group_value, "source_type": mapped_type}
                )
            elif bool(self.params.get("emit_group_no_match", False)):
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="info",
                        type="entity_type_no_match",
                        message=f"entity type mapping not found: group={group_value}",
                        evidence={"entity_kind": "group"},
                    )
                )

        for ministry_name in ([] if mapping_types else ministry_candidates):
            mapped_type, ambiguous_types = self._lookup_entity_type(ministry_name, "ministry")
            if ambiguous_types:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="entity_type_mapping_ambiguous",
                        message=f"entity type mapping ambiguous: ministry={ministry_name}",
                        evidence={"entity_kind": "ministry", "options": ambiguous_types},
                    )
                )
                continue
            if mapped_type:
                mapping_types.append(mapped_type)
                mapping_evidences.append(
                    {"entity_kind": "ministry", "entity_name": ministry_name, "source_type": mapped_type}
                )
                if mapped_type == "部委" and ministry_name not in mapped_ministry_hits:
                    mapped_ministry_hits.append(ministry_name)
                continue
            if bool(self.params.get("emit_ministry_no_match", False)):
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="info",
                        type="entity_type_no_match",
                        message=f"entity type mapping not found: ministry={ministry_name}",
                        evidence={"entity_kind": "ministry"},
                    )
                )

        unique_mapping_types = sorted({item for item in mapping_types if item})
        if len(unique_mapping_types) == 1:
            derived_type = unique_mapping_types[0]
            derived_source = "entity_type_mapping"
        elif len(unique_mapping_types) > 1:
            mapping_conflict = True
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="source_type_evidence_conflict",
                    message=f"entity-type mapping conflict: {unique_mapping_types}",
                    evidence={"evidences": mapping_evidences},
                )
            )

        candidate_entities = [group_value, company_value, _clean(record.company_name_primary), *transferor_candidates]
        whitelist_hit = any(
            self._normalize_entity(item) in self._central_whitelist for item in candidate_entities if item
        )
        if whitelist_hit:
            if derived_type and derived_type != "央企":
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="source_type_evidence_conflict",
                        message=f"whitelist indicates 央企 but mapped evidence indicates {derived_type}",
                        evidence={"group": group_value, "company": company_value},
                    )
                )
            elif not derived_type and not mapping_conflict:
                derived_type = "央企"
                derived_source = "central_whitelist"

        inferred = self._infer(merged)
        if not derived_type and not mapping_conflict and inferred:
            derived_type = inferred
            derived_source = "keyword_infer"

        if derived_type == "部委":
            if len(mapped_ministry_hits) == 1:
                derived_ministry = mapped_ministry_hits[0]
            elif len(mapped_ministry_hits) > 1:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="ministry_ambiguous",
                        message=f"multiple ministries mapped: {mapped_ministry_hits}",
                        evidence={},
                    )
                )

            if not derived_ministry and len(ministry_candidates) == 1:
                derived_ministry = ministry_candidates[0]
            elif not derived_ministry and len(ministry_candidates) > 1:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="ministry_ambiguous",
                        message=f"multiple ministries detected: {ministry_candidates}",
                        evidence={},
                    )
                )

            ministry_field = _first_existing(raw_fields, self._ministry_field_candidates) or (
                _clean(self.params.get("ministry_field_name")) or "主管部委"
            )
            old_ministry = self._canonicalize_ministry(raw_fields.get(ministry_field))
            if derived_ministry and old_ministry and old_ministry != derived_ministry:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="ministry_conflict",
                        message=f"mapped ministry({derived_ministry}) conflicts with row ministry({old_ministry})",
                        evidence={},
                    )
                )
            elif not derived_ministry and bool(self.params.get("emit_ministry_missing", False)):
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="ministry_missing",
                        message="source_type is 部委 but ministry is unresolved",
                        evidence={"group": group_value, "company": company_value, "ministries": ministry_candidates},
                    )
                )

        conflict_strategy = _clean(self.params.get("conflict_strategy", "keep_original_and_flag")).lower()
        if conflict_strategy not in {"keep_original_and_flag", "prefer_mapping"}:
            conflict_strategy = "keep_original_and_flag"

        if current_valid:
            if derived_type and derived_type != current_valid:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="source_type_conflict",
                        message=(
                            f"source_type conflict current={current_valid} derived={derived_type} "
                            f"strategy={conflict_strategy}"
                        ),
                        evidence={"source": derived_source or "unknown"},
                    )
                )
                if conflict_strategy == "prefer_mapping":
                    patches.append(
                        Patch(
                            field=target_field,
                            old_value=raw_value,
                            new_value=derived_type,
                            action="normalize",
                            reason=f"source_type_prefer_mapping:{derived_source}",
                        )
                    )
        elif derived_type:
            patches.append(
                Patch(
                    field=target_field,
                    old_value=raw_value,
                    new_value=derived_type,
                    action="normalize",
                    reason=f"normalize_source_type:{derived_source}",
                )
            )
        elif raw_value and raw_value not in self._allowed and not self._alias_map(raw_value):
            patches.append(
                Patch(
                    field=target_field,
                    old_value=raw_value,
                    new_value="",
                    action="normalize",
                    reason="unsupported_source_type_clear",
                )
            )
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="source_type_unsupported",
                    message=f"unsupported source_type cleared: {raw_value}",
                    evidence={"field": target_field},
                )
            )

        if derived_type == "部委" and derived_ministry and bool(self.params.get("write_ministry_field", False)):
            ministry_field = _clean(self.params.get("ministry_field_name")) or "主管部委"
            old_ministry = _clean(raw_fields.get(ministry_field))
            if old_ministry != derived_ministry:
                patches.append(
                    Patch(
                        field=ministry_field,
                        old_value=old_ministry,
                        new_value=derived_ministry,
                        action="derive",
                        reason="derive_ministry_by_reference",
                    )
                )
        return RuleResult(patches=patches, findings=findings)


class R006DeriveListingTimesRule(_NoopRule):
    _id = "R006_derive_listing_times"
    _project_code_candidates = [FIELD_PROJECT_CODE, "project_code", "PROJECT_CODE"]
    _listing_field_candidates = [FIELD_LISTING_TIMES, "listing_times", "LISTING_TIMES"]

    @staticmethod
    def _derive_listing_times(project_code: str) -> str:
        code = _clean(project_code)
        if not code:
            return ""
        match = re.search(r"-(\d+)$", code)
        if match:
            times = int(match.group(1))
            # "-0" is pre-disclosure helper marker and must not be mapped to listing_times.
            return "" if times == 0 else _convert_listing_times_to_chinese(times)
        # No numeric suffix means first listing.
        return _convert_listing_times_to_chinese(1)

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        listing_field = _first_existing(raw_fields, self._listing_field_candidates) or FIELD_LISTING_TIMES
        current_value = _clean(raw_fields.get(listing_field))

        project_code = _clean(record.project_code)
        if not project_code:
            code_key = _first_existing(raw_fields, self._project_code_candidates)
            project_code = _clean(raw_fields.get(code_key)) if code_key else ""
        derived = self._derive_listing_times(project_code)
        if not derived:
            return RuleResult()

        patches: List[Patch] = []
        findings: List[Finding] = []
        if not current_value:
            patches.append(
                Patch(
                    field=listing_field,
                    old_value=current_value,
                    new_value=derived,
                    action="derive",
                    reason="derive_listing_times_from_project_code",
                )
            )
        elif current_value != derived:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="listing_times_conflict",
                    message=f"listing_times conflict current={current_value} derived={derived}",
                    evidence={"project_code": project_code, "field": listing_field},
                )
            )
        return RuleResult(patches=patches, findings=findings)


class R011PersonTransferorPrivateRule(_NoopRule):
    _id = "R011_person_transferor_private"
    _source_field_candidates = [FIELD_SOURCE_TYPE, "source_type", "SOURCE_TYPE"]
    _transferor_field_candidates = [
        FIELD_SELLER,
        FIELD_SELLER_NAME,
        FIELD_FINANCING,
        FIELD_FINANCING_NAME,
        "seller_name",
        "seller",
    ]
    _allowed_source_types = {"央企", "部委", "市属", "民营"}
    _organization_keywords = (
        "公司",
        "集团",
        "有限",
        "股份",
        "银行",
        "总社",
        "合作社",
        "政府",
        "委员会",
        "国资",
        "部",
        "局",
        "厅",
        "中心",
        "医院",
        "大学",
        "学院",
        "学校",
        "研究所",
        "基金",
        "合伙",
        "企业",
        "厂",
        "院",
    )

    @staticmethod
    def _split_transferor_tokens(text: str) -> List[str]:
        value = _clean(text)
        if not value:
            return []
        # Keep ASCII comma as part of entity name (for example "Co., Limited").
        return [item.strip() for item in re.split(r"[，、；;/|]+", value) if _clean(item)]

    @staticmethod
    def _parse_transferor_token(token: str) -> Tuple[str, float | None]:
        text = _clean(token)
        if not text:
            return "", None
        ratio_value: float | None = None

        enclosed = re.search(r"^(.*?)\s*[（(]\s*(\d+(?:\.\d+)?)\s*%?\s*[）)]\s*$", text)
        if enclosed:
            text = _clean(enclosed.group(1))
            ratio_value = float(enclosed.group(2))
        else:
            trailing = re.search(r"^(.*?)\s+(\d+(?:\.\d+)?)\s*%\s*$", text)
            if trailing:
                text = _clean(trailing.group(1))
                ratio_value = float(trailing.group(2))

        return _clean(text), ratio_value

    def _pick_primary_transferor(self, raw_value: str) -> str:
        tokens = self._split_transferor_tokens(raw_value)
        if not tokens:
            return ""

        parsed: List[Tuple[str, float | None]] = []
        seen = set()
        for token in tokens:
            name, ratio = self._parse_transferor_token(token)
            if not name:
                continue
            key = _normalize_space(name).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            parsed.append((name, ratio))

        if not parsed:
            return ""

        with_ratio = [item for item in parsed if item[1] is not None]
        if len(parsed) > 1 and with_ratio:
            top_name, _top_ratio = max(with_ratio, key=lambda item: item[1] or 0.0)
            return top_name
        return parsed[0][0]

    def _is_person_name(self, name: str) -> bool:
        value = _clean(name)
        if not value:
            return False
        token = re.sub(r"\s+", "", value)
        if not token:
            return False
        if re.fullmatch(r"[-—－_]{2,}", token):
            return False
        if any(keyword in token for keyword in self._organization_keywords):
            return False
        if re.search(r"\d", token):
            return False

        chinese_token = re.sub(r"[·•・．\.]", "", token)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", chinese_token):
            return True

        if re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", value):
            return True
        return False

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        source_field = _first_existing(raw_fields, self._source_field_candidates) or FIELD_SOURCE_TYPE
        source_value = _clean(raw_fields.get(source_field))

        override_existing = bool(self.params.get("override_existing", True))
        if source_value in self._allowed_source_types and source_value != "民营" and not override_existing:
            return RuleResult()
        if source_value == "民营":
            return RuleResult()

        transferor_field = _first_existing(raw_fields, self._transferor_field_candidates)
        if not transferor_field:
            return RuleResult()
        transferor_raw = _clean(raw_fields.get(transferor_field))
        if not transferor_raw:
            return RuleResult()

        primary_transferor = self._pick_primary_transferor(transferor_raw)
        if not self._is_person_name(primary_transferor):
            return RuleResult()

        patch_action = "fill" if not source_value else "normalize"
        patches = [
            Patch(
                field=source_field,
                old_value=source_value,
                new_value="民营",
                action=patch_action,
                reason="person_transferor_default_private",
            )
        ]
        findings = [
            Finding(
                rule_id=self.rule_id(),
                severity="info",
                type="person_transferor_marked_private",
                message=f"source_type marked as 民营 by person transferor: {primary_transferor}",
                evidence={"transferor": primary_transferor, "field": transferor_field},
            )
        ]
        return RuleResult(patches=patches, findings=findings)


class R012ClearInvalidGroupPlaceholderRule(_NoopRule):
    _id = "R012_clear_invalid_group_placeholder"
    _group_field_candidates = [FIELD_GROUP_NAME, FIELD_GROUP_NAME_ALT, "group_name"]

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        group_field = _first_existing(raw_fields, self._group_field_candidates)
        if not group_field:
            return RuleResult()

        group_value = _clean(raw_fields.get(group_field))
        if not group_value:
            return RuleResult()

        stripped = re.sub(r"\s+", "", group_value)
        min_length = int(self.params.get("min_placeholder_length", 3))
        if len(stripped) < max(min_length, 2):
            return RuleResult()
        if not re.fullmatch(r"[-—－_]{2,}", stripped):
            return RuleResult()

        patches = [
            Patch(
                field=group_field,
                old_value=group_value,
                new_value="",
                action="normalize",
                reason="clear_invalid_group_placeholder",
            )
        ]
        findings = [
            Finding(
                rule_id=self.rule_id(),
                severity="info",
                type="group_placeholder_cleared",
                message=f"group placeholder cleared: {group_value}",
                evidence={"field": group_field},
            )
        ]
        return RuleResult(patches=patches, findings=findings)


class R007RequiredFieldCheckRule(_NoopRule):
    _id = "R007_required_field_check"
    _default_specs = [
        {
            "name": "project_code",
            "aliases": [FIELD_PROJECT_CODE, "project_code", "PROJECT_CODE"],
            "severity": "error",
        },
        {
            "name": "company_name_primary",
            "aliases": [
                "company_name_primary",
                "company_name",
                "标的企业名称",
                "标的企业",
                "企业名称",
                FIELD_SELLER_NAME,
                FIELD_FINANCING_NAME,
                FIELD_SELLER,
                FIELD_FINANCING,
                "seller",
            ],
            "severity": "warn",
        },
    ]

    def _iter_specs(self) -> List[Dict[str, Any]]:
        custom = self.params.get("required_fields")
        if not isinstance(custom, list) or not custom:
            include_source_type = bool(self.params.get("include_source_type", False))
            specs = [dict(item) for item in self._default_specs]
            if include_source_type:
                specs.append(
                    {
                        "name": "source_type",
                        "aliases": [FIELD_SOURCE_TYPE, "source_type", "SOURCE_TYPE"],
                        "severity": "warn",
                    }
                )
            return specs

        specs: List[Dict[str, Any]] = []
        default_severity = _clean(self.params.get("default_severity", "warn")).lower()
        if default_severity not in {"info", "warn", "error"}:
            default_severity = "warn"
        for item in custom:
            if isinstance(item, str):
                key = _clean(item)
                if not key:
                    continue
                specs.append({"name": key, "aliases": [key], "severity": default_severity})
                continue
            if not isinstance(item, dict):
                continue
            name = _clean(item.get("name")) or _clean(item.get("field"))
            aliases = item.get("aliases")
            if not isinstance(aliases, list) or not aliases:
                alias = _clean(item.get("field")) or name
                aliases = [alias] if alias else []
            aliases = [_clean(alias) for alias in aliases if _clean(alias)]
            if not aliases:
                continue
            severity = _clean(item.get("severity", default_severity)).lower()
            if severity not in {"info", "warn", "error"}:
                severity = default_severity
            specs.append({"name": name or aliases[0], "aliases": aliases, "severity": severity})
        return specs

    @staticmethod
    def _resolve_value(record: CanonicalRecord, raw_fields: Dict[str, Any], aliases: List[str]) -> str:
        value = _first_non_empty(raw_fields, aliases)
        if value:
            return value
        normalized_aliases = {_normalize_column_key(alias) for alias in aliases}
        if "projectcode" in normalized_aliases:
            return _clean(record.project_code)
        if "companynameprimary" in normalized_aliases or "companyname" in normalized_aliases:
            return _clean(record.company_name_primary)
        return ""

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        specs = self._iter_specs()
        if not specs:
            return RuleResult()

        findings: List[Finding] = []
        stop_on_error = bool(self.params.get("stop_on_error", False))
        has_error = False

        for spec in specs:
            aliases = [alias for alias in spec.get("aliases", []) if alias]
            if not aliases:
                continue
            value = self._resolve_value(record, raw_fields, aliases)
            if not _is_empty_like(value):
                continue

            severity = _clean(spec.get("severity", "warn")).lower()
            if severity not in {"info", "warn", "error"}:
                severity = "warn"
            if severity == "error":
                has_error = True

            name = _clean(spec.get("name")) or aliases[0]
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity=severity,
                    type="required_field_missing",
                    message=f"required field missing: {name}",
                    evidence={"aliases": aliases, "row_index": record.row_index},
                )
            )

        return RuleResult(findings=findings, stop_processing=bool(findings and has_error and stop_on_error))


class R008ProjectCodeFormatCheckRule(_NoopRule):
    _id = "R008_project_code_format_check"
    _project_code_candidates = [FIELD_PROJECT_CODE, "project_code", "PROJECT_CODE"]
    _known_patterns = [
        re.compile(r"^(?:G[36R]|Q[36R])[A-Z0-9]{6,}(?:-\d+)?$"),
        re.compile(r"^(?:YQCQ|SDCQ)[A-Z0-9]{4,}(?:-\d+)?$"),
        re.compile(r"^[A-Z]{2,}[A-Z0-9]{4,}(?:-\d+)?$"),
    ]

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        project_code = _clean(record.project_code)
        if not project_code:
            code_field = _first_existing(raw_fields, self._project_code_candidates)
            project_code = _clean(raw_fields.get(code_field)) if code_field else ""
        if not project_code:
            return RuleResult()

        findings: List[Finding] = []
        hard_invalid = False
        try:
            min_length = max(1, int(self.params.get("min_length", 8)))
        except Exception:  # noqa: BLE001
            min_length = 8
        if len(project_code) < min_length:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="project_code_too_short",
                    message=f"project_code too short: {project_code}",
                    evidence={},
                )
            )

        if not re.fullmatch(r"[A-Za-z0-9\-]+", project_code):
            invalid_chars = sorted({ch for ch in project_code if not re.match(r"[A-Za-z0-9\-]", ch)})
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="project_code_invalid_chars",
                    message=f"project_code contains invalid chars: {project_code}",
                    evidence={"invalid_chars": invalid_chars},
                )
            )
            hard_invalid = True

        if project_code.count("-") > 1:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="project_code_invalid_dash_count",
                    message=f"project_code has more than one dash: {project_code}",
                    evidence={},
                )
            )
            hard_invalid = True

        if "-" in project_code and not re.search(r"-(\d+)$", project_code):
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="project_code_suffix_invalid",
                    message=f"project_code suffix is not numeric: {project_code}",
                    evidence={},
                )
            )
            hard_invalid = True

        emit_unrecognized = bool(self.params.get("emit_unrecognized", True))
        normalized = project_code.upper()
        if emit_unrecognized and not hard_invalid and not any(
            pattern.fullmatch(normalized) for pattern in self._known_patterns
        ):
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="info",
                    type="project_code_format_unrecognized",
                    message=f"project_code format not in known patterns: {project_code}",
                    evidence={},
                )
            )

        return RuleResult(findings=findings)


class R009ConsistencyValidateRule(_NoopRule):
    _id = "R009_consistency_validate"
    _project_code_candidates = [FIELD_PROJECT_CODE, "project_code", "PROJECT_CODE"]
    _listing_field_candidates = [FIELD_LISTING_TIMES, "listing_times", "LISTING_TIMES"]
    _source_field_candidates = [FIELD_SOURCE_TYPE, "source_type", "SOURCE_TYPE"]
    _ministry_field_candidates = ["主管部委", "部委", "部委名称", "ministry_name", "ministry"]
    _allowed_source_types = {"央企", "部委", "市属", "民营"}

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        findings: List[Finding] = []

        project_code = _clean(record.project_code)
        if not project_code:
            project_field = _first_existing(raw_fields, self._project_code_candidates)
            project_code = _clean(raw_fields.get(project_field)) if project_field else ""

        listing_field = _first_existing(raw_fields, self._listing_field_candidates) or FIELD_LISTING_TIMES
        listing_value = _clean(raw_fields.get(listing_field))

        if listing_value:
            if not _is_valid_listing_times_value(listing_value):
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="listing_times_invalid",
                        message=f"listing_times format invalid: {listing_value}",
                        evidence={"field": listing_field},
                    )
                )

        if project_code and listing_value:
            suffix_match = re.search(r"-(\d+)$", project_code)
            if suffix_match and int(suffix_match.group(1)) == 0:
                findings.append(
                    Finding(
                        rule_id=self.rule_id(),
                        severity="warn",
                        type="pre_disclosure_listing_times_non_empty",
                        message=(
                            "project_code indicates pre-disclosure (-0) "
                            f"but listing_times is non-empty: {listing_value}"
                        ),
                        evidence={"project_code": project_code, "field": listing_field},
                    )
                )
            else:
                derived_listing = R006DeriveListingTimesRule._derive_listing_times(project_code)
                if derived_listing and listing_value != derived_listing:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id(),
                            severity="warn",
                            type="listing_times_project_code_inconsistent",
                            message=(
                                "listing_times inconsistent with project_code: "
                                f"current={listing_value}, derived={derived_listing}"
                            ),
                            evidence={"project_code": project_code, "field": listing_field},
                        )
                    )

        source_field = _first_existing(raw_fields, self._source_field_candidates) or FIELD_SOURCE_TYPE
        source_type = _clean(raw_fields.get(source_field))
        if source_type and source_type not in self._allowed_source_types:
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="source_type_out_of_taxonomy",
                    message=f"source_type is outside 4-type taxonomy: {source_type}",
                    evidence={"field": source_field},
                )
            )

        ministry_field = _first_existing(raw_fields, self._ministry_field_candidates) or "主管部委"
        ministry_value = _clean(raw_fields.get(ministry_field))
        if ministry_value and source_type and source_type != "部委":
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="ministry_present_with_non_ministry_source_type",
                    message=f"ministry is present but source_type={source_type}",
                    evidence={"field": ministry_field},
                )
            )
        if (
            source_type == "部委"
            and not ministry_value
            and bool(self.params.get("emit_ministry_missing_for_source_type", False))
        ):
            findings.append(
                Finding(
                    rule_id=self.rule_id(),
                    severity="warn",
                    type="ministry_missing_for_ministry_source_type",
                    message="source_type=部委 but ministry field is empty",
                    evidence={"field": ministry_field},
                )
            )

        return RuleResult(findings=findings)


class R010FilterScrapPhysicalAssetRule(_NoopRule):
    _id = "R010_filter_scrap_physical_asset"
    _project_type_candidates = [
        "\u9879\u76ee\u7c7b\u578b",
        "\u6807\u7684\u7c7b\u578b",
        "\u4e1a\u52a1\u7c7b\u578b",
        "\u4ea4\u6613\u54c1\u7c7b",
        "project_type",
        "target_type",
        "trade_type",
        "PROJECT_TYPE",
    ]
    _project_name_candidates = [
        "\u9879\u76ee\u540d\u79f0",
        "\u6807\u7684\u540d\u79f0",
        "\u6807\u7684",
        "\u8d44\u4ea7\u540d\u79f0",
        "\u8f6c\u8ba9\u6807\u7684",
        "project_name",
        "target_name",
        "asset_name",
        "PROJECT_NAME",
    ]
    _text_candidates = [
        "\u9879\u76ee\u540d\u79f0",
        "\u6807\u7684\u540d\u79f0",
        "\u6807\u7684",
        "\u8d44\u4ea7\u540d\u79f0",
        "\u8f6c\u8ba9\u6807\u7684",
        "\u8d44\u4ea7\u63cf\u8ff0",
        "\u6807\u7684\u63cf\u8ff0",
        "\u9879\u76ee\u7b80\u4ecb",
        "\u9879\u76ee\u6982\u51b5",
        "\u5907\u6ce8",
        "\u7279\u522b\u4e8b\u9879",
        "remark",
        "notes",
        "description",
    ]
    _default_physical_asset_markers = ["\u5b9e\u7269\u8d44\u4ea7"]
    _default_scrap_keywords = [
        "\u62a5\u5e9f",
        "\u5e9f\u65e7",
        "\u62a5\u635f",
        "\u6dd8\u6c70",
        "\u62c6\u9664",
        "\u62c6\u89e3",
        "\u6b8b\u503c",
    ]
    _default_negative_keywords = [
        "\u975e\u62a5\u5e9f",
        "\u4e0d\u5c5e\u4e8e\u62a5\u5e9f",
        "\u4e0d\u662f\u62a5\u5e9f",
    ]

    def applies(self, record: CanonicalRecord, context: RuleContext) -> bool:  # noqa: ARG002
        # Keep backward compatibility safe: explicit opt-in only.
        return bool(self.params.get("active", False))

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        value = _clean(text)
        if not value:
            return False
        return any(keyword and keyword in value for keyword in keywords)

    @staticmethod
    def _normalize_keywords(values: Any, defaults: List[str]) -> List[str]:
        if not isinstance(values, list):
            return list(defaults)
        normalized: List[str] = []
        for item in values:
            keyword = _clean(item)
            if not keyword:
                continue
            # Ignore broken placeholders such as "??" from encoding issues.
            if re.fullmatch(r"[?？]+", keyword):
                continue
            normalized.append(keyword)
        if not normalized:
            return list(defaults)
        return normalized

    def _is_physical_asset_row(
        self,
        record: CanonicalRecord,
        raw_fields: Dict[str, Any],
        markers: List[str],
    ) -> bool:
        project_type = _pick_row_value(raw_fields, self._project_type_candidates)
        if self._contains_any(project_type, markers):
            return True
        if self._contains_any(record.file_name, markers):
            return True
        if self._contains_any(record.sheet_name, markers):
            return True
        return False

    def _collect_text(self, raw_fields: Dict[str, Any]) -> str:
        values: List[str] = []
        seen = set()
        for key in self._project_name_candidates + self._text_candidates:
            value = _pick_row_value(raw_fields, [key])
            if value:
                token = _normalize_space(value)
                if token in seen:
                    continue
                seen.add(token)
                values.append(value)

        if not values and bool(self.params.get("search_all_fields", True)):
            for value in raw_fields.values():
                text = _clean(value)
                if not text:
                    continue
                token = _normalize_space(text)
                if token in seen:
                    continue
                seen.add(token)
                values.append(text)
        return " ".join(values)

    @staticmethod
    def _first_hit(text: str, keywords: List[str]) -> str:
        for keyword in keywords:
            if keyword and keyword in text:
                return keyword
        return ""

    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:  # noqa: ARG002
        raw_fields = dict(record.raw_fields or {})
        markers = self._normalize_keywords(
            self.params.get("physical_asset_markers"),
            self._default_physical_asset_markers,
        )
        if not self._is_physical_asset_row(record, raw_fields, markers):
            return RuleResult()

        merged_text = self._collect_text(raw_fields)
        if not merged_text:
            return RuleResult()

        scrap_keywords = self._normalize_keywords(
            self.params.get("scrap_keywords"),
            self._default_scrap_keywords,
        )

        negative_keywords = self._normalize_keywords(
            self.params.get("negative_keywords"),
            self._default_negative_keywords,
        )

        matched_negative = self._first_hit(merged_text, negative_keywords)
        if matched_negative:
            return RuleResult()

        matched_keyword = self._first_hit(merged_text, scrap_keywords)
        if not matched_keyword:
            return RuleResult()

        severity = _clean(self.params.get("severity", "info")).lower()
        if severity not in {"info", "warn", "error"}:
            severity = "info"

        findings = [
            Finding(
                rule_id=self.rule_id(),
                severity=severity,  # type: ignore[arg-type]
                type="scrap_physical_asset_filtered",
                message=f"filtered scrap physical asset by keyword: {matched_keyword}",
                evidence={
                    "keyword": matched_keyword,
                    "project_code": record.project_code,
                    "file": record.file_name,
                    "sheet": record.sheet_name,
                },
            )
        ]
        patches = [
            Patch(
                field="__row__",
                old_value="",
                new_value="",
                action="filter_out_row",
                reason="filter_scrap_physical_asset",
            )
        ]
        return RuleResult(patches=patches, findings=findings, stop_processing=True)


BUILTIN_RULE_CLASSES = [
    R001GroupMappingFillRule,
    R002GroupConflictFlagRule,
    R003CompanyNameNormalizeRule,
    R004NormalizeValuesRule,
    R005NormalizeSourceTypeRule,
    R006DeriveListingTimesRule,
    R011PersonTransferorPrivateRule,
    R012ClearInvalidGroupPlaceholderRule,
    R007RequiredFieldCheckRule,
    R008ProjectCodeFormatCheckRule,
    R009ConsistencyValidateRule,
    R010FilterScrapPhysicalAssetRule,
]

BUILTIN_RULE_IDS = [rule_cls.rule_id() for rule_cls in BUILTIN_RULE_CLASSES]
