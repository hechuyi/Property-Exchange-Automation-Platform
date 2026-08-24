"""Universal finance fallback extraction from raw HTML tables."""

import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

KEY_PROFIT = "近一年净利润"
KEY_ASSET = "总资产"

_MISSING_VALUES = {"", "-", "--", "—", "暂无", "N/A", "NA", "null", "None"}
_PROFIT_LABELS = ("净利润", "净利", "netprofit")
_ASSET_LABELS = ("资产总额", "资产总计", "总资产", "资产合计", "assettotal")
_MONEY_UNIT_RE = r"(?:万|万元|亿|亿元)"

# ParserOutput uses English standard-field names while the compatibility
# payload used by the fallback uses Chinese names.  Treat both as the same
# metric before scraping HTML so a valid structured value is not replaced by
# a less reliable table guess.
_METRIC_ALIASES = {
    "profit": (KEY_PROFIT, "profit", "net_profit", "近一年净利润（万）"),
    "asset": (KEY_ASSET, "asset_total", "total_assets", "总资产（万）"),
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text in _MISSING_VALUES


def _sync_metric_alias(data: Dict[str, Any], metric: str) -> bool:
    """Copy a populated standard alias to the compatibility key if needed."""
    aliases = _METRIC_ALIASES[metric]
    canonical = aliases[0]
    if not _is_missing(data.get(canonical)):
        return True
    for alias in aliases[1:]:
        if alias not in data or _is_missing(data.get(alias)):
            continue
        data[canonical] = data[alias]
        return True
    return False


def _clean_cell(text: str) -> str:
    return (text or "").replace("\xa0", " ").strip()


def _normalize_label(text: str) -> str:
    s = _clean_cell(text)
    s = re.sub(r"\s+", "", s)
    s = s.replace("：", "").replace(":", "")
    return s.lower()


def _detect_metric(label: str) -> Optional[str]:
    normalized_label = _normalize_label(label)
    if not normalized_label:
        return None
    if any(keyword in normalized_label for keyword in _PROFIT_LABELS):
        return "profit"
    if any(keyword in normalized_label for keyword in _ASSET_LABELS):
        return "asset"
    return None


def _parse_period(text: str) -> Optional[int]:
    s = _clean_cell(text)
    if not s:
        return None

    # Full date like 2025-12-31 / 2025年12月31日
    m = re.search(r"(20\d{2})[\/\-.年](\d{1,2})[\/\-.月](\d{1,2})", s)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        return year * 10000 + month * 100 + day

    # Year-only like 2024年度. Avoid matching monetary numbers like 2048.60.
    y = re.search(r"(?<!\d)(20\d{2})(?![\d.])", s)
    if y:
        return int(y.group(1)) * 10000 + 1231

    return None


def _is_valid_metric_text(value: str) -> bool:
    s = _clean_cell(value)
    if not s:
        return False
    if s in {"-", "--", "—"}:
        return False
    if re.fullmatch(rf"-?[\d,]+(?:\.\d+)?(?:\s*{_MONEY_UNIT_RE})?", s):
        return True
    return False


def _to_number_or_text(value: str) -> Any:
    s = _clean_cell(value)
    if s in {"-", "--", "—"}:
        return s
    m = re.fullmatch(r"-?[\d,]+(?:\.\d+)?", s)
    if m:
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return s
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return s
    tail = s[m.end() :].strip()
    if re.fullmatch(rf"{_MONEY_UNIT_RE}?", tail):
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return s
    return s


def _detect_scope_priority(text: str) -> int:
    normalized = _normalize_label(text)
    if not normalized:
        return 0
    # 口径：年度优先（2） > 最近一期（1） > 未知（0）
    if "年度审计报告" in normalized:
        return 2
    if "年度" in normalized and (
        "审计" in normalized or "财务报表" in normalized or "财务数据" in normalized
    ):
        return 2
    if "最近一期" in normalized:
        return 1
    if "财务报表" in normalized or "财务数据" in normalized:
        return 1
    return 0


def _nearest_scope_priority(rows: List[List[str]], row_index: int) -> int:
    """Find the nearest explicit annual/recent-period marker for a row."""
    for search_index in range(row_index, -1, -1):
        priority = _detect_scope_priority(" ".join(rows[search_index]))
        if priority:
            return priority
    return 0


def _extract_table_records(rows: List[List[str]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    records: List[Tuple[int, int, Dict[str, Any]]] = []

    # Pattern: title row, then metric headers, then values.
    for ridx in range(len(rows) - 2):
        header_row = rows[ridx + 1]
        value_row = rows[ridx + 2]

        # A single annual marker often governs several year blocks.  Looking
        # back only two rows fails when another year's block starts later (or
        # when the source lists years out of order), causing the first year to
        # win over the latest year.  Use the nearest explicit marker instead.
        scope_priority = _nearest_scope_priority(rows, ridx + 1)

        period = 0
        for search_idx in range(max(0, ridx - 2), ridx + 1):
            for cell in rows[search_idx]:
                parsed = _parse_period(cell)
                if parsed is not None and parsed > period:
                    period = parsed

        metric_cols: Dict[str, int] = {}
        for cidx, label in enumerate(header_row):
            metric = _detect_metric(label)
            if metric is not None:
                metric_cols[metric] = cidx
        if not metric_cols:
            continue

        # In annual blocks, prefer the year shown in the value row (e.g. "2024年度")
        # over noisy mixed title rows that may also contain recent-period dates.
        if scope_priority == 2 and value_row:
            row_period = _parse_period(value_row[0])
            if row_period is not None:
                period = row_period

        metrics: Dict[str, Any] = {}
        for metric, col in metric_cols.items():
            value_col = _metric_value_index(header_row, value_row, col)
            if value_col < len(value_row) and _is_valid_metric_text(value_row[value_col]):
                metrics[metric] = _to_number_or_text(value_row[value_col])
        if metrics:
            records.append((scope_priority, period, metrics))

    return records


def _extract_year_matrix_records(rows: List[List[str]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    records: List[Tuple[int, int, Dict[str, Any]]] = []
    for ridx, row in enumerate(rows):
        if not row:
            continue
        first = _normalize_label(row[0])
        if "项目/年度" not in first and first != "项目年度":
            continue

        year_cols: Dict[int, int] = {}
        for cidx in range(1, len(row)):
            period = _parse_period(row[cidx])
            if period is not None:
                year_cols[cidx] = period
        if not year_cols:
            continue

        scope_priority = 0
        for search_idx in range(max(0, ridx - 2), min(len(rows), ridx + 2)):
            scope_priority = max(scope_priority, _detect_scope_priority(" ".join(rows[search_idx])))

        for data_idx in range(ridx + 1, len(rows)):
            data_row = rows[data_idx]
            if not data_row:
                continue
            row_first = _normalize_label(data_row[0])
            if "最近一期" in row_first or "财务报表" in row_first or "财务数据" in row_first:
                break
            metric = _detect_metric(data_row[0])
            if metric is None:
                continue
            for col_idx, period in year_cols.items():
                if col_idx >= len(data_row):
                    continue
                value = data_row[col_idx]
                if _is_valid_metric_text(value):
                    records.append((scope_priority, period, {metric: _to_number_or_text(value)}))
    return records


def _extract_period_header_records(rows: List[List[str]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    records: List[Tuple[int, int, Dict[str, Any]]] = []
    for ridx in range(len(rows) - 1):
        header_row = rows[ridx]
        value_row = rows[ridx + 1]
        if not header_row or not value_row:
            continue

        period = _parse_period(header_row[0]) if header_row else None
        if period is None:
            for search_idx in range(max(0, ridx - 2), ridx):
                for cell in rows[search_idx]:
                    parsed = _parse_period(cell)
                    if parsed is not None and (period is None or parsed > period):
                        period = parsed
        if period is None:
            continue

        # Allow split annual blocks to inherit the nearest annual/recent marker.
        scope_priority = _nearest_scope_priority(rows, ridx)

        if scope_priority == 2 and value_row:
            row_period = _parse_period(value_row[0])
            if row_period is not None:
                period = row_period

        metrics: Dict[str, Any] = {}
        for cidx in range(0, len(header_row)):
            metric = _detect_metric(header_row[cidx])
            if metric is None:
                continue
            value_idx = _metric_value_index(header_row, value_row, cidx)
            if value_idx < 0 or value_idx >= len(value_row):
                continue
            if _is_valid_metric_text(value_row[value_idx]):
                metrics[metric] = _to_number_or_text(value_row[value_idx])
        if metrics:
            records.append((scope_priority, period, metrics))
    return records


def _extract_statement_date_records(rows: List[List[str]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    records: List[Tuple[int, int, Dict[str, Any]]] = []
    for ridx in range(len(rows) - 1):
        header_row = rows[ridx]
        value_row = rows[ridx + 1]
        if not header_row or not value_row:
            continue

        if "报表日期" not in _normalize_label(header_row[0]):
            continue

        period = _parse_period(value_row[0]) if value_row else None
        if period is None:
            continue

        metrics: Dict[str, Any] = {}
        max_idx = min(len(header_row), len(value_row))
        for cidx in range(1, max_idx):
            metric = _detect_metric(header_row[cidx])
            if metric is None:
                continue
            raw_val = value_row[cidx]
            if _is_valid_metric_text(raw_val):
                metrics[metric] = _to_number_or_text(raw_val)
        if metrics:
            # Explicit current-period statement block.
            records.append((1, period, metrics))
    return records


def _metric_value_index(header_row: List[str], value_row: List[str], column_index: int) -> int:
    """Resolve a metric column when a value row omits a leading period cell.

    CBEX annual tables commonly render a header such as
    ``2025年度 | 资产总计 | 负债总计`` followed by a value row
    ``533.53 | 0.3``.  The values are therefore one column to the left of
    their header positions. Rows that include the period in both lines keep
    the original index.
    """
    if not header_row or not value_row:
        return column_index
    if len(header_row) == len(value_row) + 1:
        header_period = _parse_period(header_row[0])
        value_period = _parse_period(value_row[0])
        if header_period is not None and value_period is None:
            return column_index - 1
        # Preserve the boundary correction for layouts where only the last
        # header cell has no corresponding value cell.
        if column_index >= len(value_row):
            return column_index - 1
    return column_index


def _pick_target_scope_and_period(records: List[Tuple[int, int, Dict[str, Any]]]) -> Optional[Tuple[int, int]]:
    if not records:
        return None

    scopes = [scope for scope, _, _ in records]
    if 2 in scopes:
        target_scope = 2
    elif 1 in scopes:
        target_scope = 1
    else:
        target_scope = max(scopes)

    target_period = max(period for scope, period, _ in records if scope == target_scope)
    return (target_scope, target_period)


def _is_numeric_zero(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return abs(float(value)) < 1e-12
    text = _clean_cell(str(value))
    if not text:
        return False
    if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", text):
        try:
            return abs(float(text.replace(",", ""))) < 1e-12
        except ValueError:
            return False
    return False


def _drop_unreliable_zero_finance(metrics: Dict[str, Any]) -> Dict[str, Any]:
    if not metrics:
        return metrics
    numeric_values = [v for v in metrics.values() if isinstance(v, (int, float)) or _is_numeric_zero(v)]
    if not numeric_values:
        return metrics
    if all(_is_numeric_zero(v) for v in numeric_values):
        # 全部可识别财务值都是 0，判定为无效填报。
        return {}
    return metrics


def extract_latest_finance_from_html(
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
) -> Dict[str, Any]:
    if soup is None:
        soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")

    all_records: List[Tuple[int, int, Dict[str, Any]]] = []
    for table in tables:
        rows: List[List[str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            row = [_clean_cell(c.get_text(" ", strip=True)) for c in cells]
            if any(row):
                rows.append(row)
        if rows:
            all_records.extend(_extract_table_records(rows))
            all_records.extend(_extract_year_matrix_records(rows))
            all_records.extend(_extract_period_header_records(rows))
            all_records.extend(_extract_statement_date_records(rows))

    target = _pick_target_scope_and_period(all_records)
    if target is None:
        return {}
    target_scope, target_period = target

    merged_metrics: Dict[str, Any] = {}
    for scope, period, metrics in all_records:
        if scope != target_scope or period != target_period:
            continue
        for metric, value in metrics.items():
            if metric not in merged_metrics or _is_missing(merged_metrics.get(metric)):
                merged_metrics[metric] = value

    merged_metrics = _drop_unreliable_zero_finance(merged_metrics)

    result: Dict[str, Any] = {}
    if "profit" in merged_metrics and not _is_missing(merged_metrics["profit"]):
        result[KEY_PROFIT] = merged_metrics["profit"]
    if "asset" in merged_metrics and not _is_missing(merged_metrics["asset"]):
        result[KEY_ASSET] = merged_metrics["asset"]
    return result


def _extract_best_finance_loose(
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
) -> Dict[str, Any]:
    if soup is None:
        soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")

    all_records: List[Tuple[int, int, Dict[str, Any]]] = []
    for table in tables:
        rows: List[List[str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            row = [_clean_cell(c.get_text(" ", strip=True)) for c in cells]
            if any(row):
                rows.append(row)
        if rows:
            all_records.extend(_extract_table_records(rows))
            all_records.extend(_extract_year_matrix_records(rows))
            all_records.extend(_extract_period_header_records(rows))
            all_records.extend(_extract_statement_date_records(rows))

    ranked: Dict[str, List[Tuple[int, int, Any]]] = {}
    for scope, period, metrics in all_records:
        for metric, value in metrics.items():
            ranked.setdefault(metric, []).append((scope, period, value))

    result: Dict[str, Any] = {}
    for metric, output_key in (("profit", KEY_PROFIT), ("asset", KEY_ASSET)):
        candidates = sorted(ranked.get(metric, []), key=lambda x: (x[0], x[1]), reverse=True)
        for _, _, value in candidates:
            if not _is_missing(value):
                result[output_key] = value
                break
    return result


def apply_finance_fallback(
    data: Dict[str, Any],
    html_content: str,
    *,
    soup: Optional[BeautifulSoup] = None,
) -> None:
    if not isinstance(data, dict) or not html_content:
        return

    project_code = str(data.get("项目编号") or "").strip()
    is_pre_disclosure = project_code.endswith("-0")

    # A parser may have already populated ``profit``/``asset_total`` in its
    # standard payload. Synchronize those aliases before deciding whether an
    # HTML fallback is needed; otherwise the fallback can overwrite a correct
    # structured value (for example, asset_total=533.53 with debt=0.3).
    need_profit = not _sync_metric_alias(data, "profit")
    need_asset = not _sync_metric_alias(data, "asset")
    if not (need_profit or need_asset):
        return

    if is_pre_disclosure:
        extracted = extract_latest_finance_from_html(html_content, soup=soup)
    else:
        extracted = _extract_best_finance_loose(html_content, soup=soup)
    if need_profit and KEY_PROFIT in extracted:
        data[KEY_PROFIT] = extracted[KEY_PROFIT]
    if need_asset and KEY_ASSET in extracted:
        data[KEY_ASSET] = extracted[KEY_ASSET]

    # Normalize meaningless placeholders to blank for downstream output.
    if _is_missing(data.get(KEY_PROFIT)):
        data[KEY_PROFIT] = ""
    if _is_missing(data.get(KEY_ASSET)):
        data[KEY_ASSET] = ""
