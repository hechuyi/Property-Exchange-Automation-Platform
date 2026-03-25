"""Regression compare helper for parser/PPE migration."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

FIELD_PROJECT_CODE = "\u9879\u76ee\u7f16\u53f7"
DEFAULT_COMPARE_FIELDS = ["\u6302\u724c\u6b21\u6570", "\u7c7b\u578b", "\u96b6\u5c5e\u96c6\u56e2"]
PROJECT_CODE_ALIASES = [FIELD_PROJECT_CODE, "project_code", "PROJECT_CODE", "project id", "project_id"]
FIELD_ALIASES = {
    "\u6302\u724c\u6b21\u6570": ["\u6302\u724c\u6b21\u6570", "listing_times", "LISTING_TIMES"],
    "\u7c7b\u578b": ["\u7c7b\u578b", "source_type", "SOURCE_TYPE"],
    "\u96b6\u5c5e\u96c6\u56e2": ["\u96b6\u5c5e\u96c6\u56e2", "\u6240\u5c5e\u96c6\u56e2", "group_name", "group"],
}


@dataclass(frozen=True)
class RowRecord:
    relative_path: str
    sheet_name: str
    row_index: int
    project_code: str
    raw_fields: Dict[str, str]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _normalize_key(text: str) -> str:
    return _clean(text).lower().replace(" ", "").replace("_", "").replace("-", "")


def _first_non_empty(raw_fields: Dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = _clean(raw_fields.get(alias))
        if value:
            return value

    normalized = {_normalize_key(key): _clean(value) for key, value in raw_fields.items()}
    for alias in aliases:
        value = _clean(normalized.get(_normalize_key(alias), ""))
        if value:
            return value
    return ""


def _normalize_relative_path(root_dir: str, file_path: str, candidate_suffix: str) -> str:
    relative = os.path.relpath(file_path, root_dir).replace("\\", "/")
    if not candidate_suffix:
        return relative

    base, ext = os.path.splitext(relative)
    if base.endswith(candidate_suffix):
        base = base[: -len(candidate_suffix)]
    return base + ext


def _discover_input_files(root_dir: str) -> List[str]:
    patterns = ["*.xlsx", "*.xls", "*.csv"]
    files: List[str] = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(root_dir, "**", pattern), recursive=True))
    return sorted(set(os.path.abspath(path) for path in files))


def _iter_sheet_frames(file_path: str) -> Iterable[tuple[str, pd.DataFrame]]:
    suffix = os.path.splitext(file_path)[1].lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
        yield ("csv", df)
        return
    if suffix in {".xlsx", ".xls"}:
        workbook = pd.read_excel(file_path, sheet_name=None, dtype=str, keep_default_na=False)
        for sheet_name, df in workbook.items():
            yield (str(sheet_name), df)
        return
    raise ValueError(f"unsupported extension: {suffix}")


def _load_records(root_dir: str, *, candidate_suffix: str) -> List[RowRecord]:
    records: List[RowRecord] = []
    for file_path in _discover_input_files(root_dir):
        relative = _normalize_relative_path(root_dir, file_path, candidate_suffix)
        for sheet_name, frame in _iter_sheet_frames(file_path):
            for idx, row in frame.iterrows():
                raw_fields = {
                    str(column).strip(): _clean(value) for column, value in row.to_dict().items()
                }
                if not any(raw_fields.values()):
                    continue
                project_code = _first_non_empty(raw_fields, PROJECT_CODE_ALIASES)
                records.append(
                    RowRecord(
                        relative_path=relative,
                        sheet_name=sheet_name,
                        row_index=int(idx) + 2,
                        project_code=project_code,
                        raw_fields=raw_fields,
                    )
                )

    records.sort(key=lambda item: (item.relative_path, item.sheet_name, item.row_index))
    return records


def _build_index(records: List[RowRecord]) -> Dict[str, RowRecord]:
    counters: Dict[str, int] = {}
    index: Dict[str, RowRecord] = {}
    for record in records:
        if record.project_code:
            base_key = f"project_code::{record.project_code}"
            counters[base_key] = counters.get(base_key, 0) + 1
            key = f"{base_key}::{counters[base_key]}"
        else:
            key = f"row::{record.relative_path}::{record.sheet_name}::{record.row_index}"
        index[key] = record
    return index


def _resolve_field_value(record: RowRecord, field: str) -> str:
    aliases = FIELD_ALIASES.get(field, [field])
    return _first_non_empty(record.raw_fields, aliases)


def compare_datasets(
    *,
    baseline_dir: str,
    candidate_dir: str,
    compare_fields: List[str],
    candidate_suffix: str,
) -> Dict[str, Any]:
    baseline_records = _load_records(baseline_dir, candidate_suffix="")
    candidate_records = _load_records(candidate_dir, candidate_suffix=candidate_suffix)

    baseline_index = _build_index(baseline_records)
    candidate_index = _build_index(candidate_records)
    keys = sorted(set(baseline_index.keys()) | set(candidate_index.keys()))

    diffs: List[Dict[str, Any]] = []
    missing_in_candidate = 0
    missing_in_baseline = 0

    for key in keys:
        baseline = baseline_index.get(key)
        candidate = candidate_index.get(key)

        if baseline is None and candidate is not None:
            missing_in_baseline += 1
            diffs.append(
                {
                    "key": key,
                    "diff_type": "missing_row_in_baseline",
                    "project_code": candidate.project_code,
                    "baseline_file": "",
                    "candidate_file": candidate.relative_path,
                    "sheet": candidate.sheet_name,
                    "row": candidate.row_index,
                }
            )
            continue

        if candidate is None and baseline is not None:
            missing_in_candidate += 1
            diffs.append(
                {
                    "key": key,
                    "diff_type": "missing_row_in_candidate",
                    "project_code": baseline.project_code,
                    "baseline_file": baseline.relative_path,
                    "candidate_file": "",
                    "sheet": baseline.sheet_name,
                    "row": baseline.row_index,
                }
            )
            continue

        if baseline is None or candidate is None:
            continue

        for field in compare_fields:
            old_value = _resolve_field_value(baseline, field)
            new_value = _resolve_field_value(candidate, field)
            if old_value == new_value:
                continue
            diffs.append(
                {
                    "key": key,
                    "diff_type": "field_diff",
                    "project_code": candidate.project_code or baseline.project_code,
                    "field": field,
                    "baseline_value": old_value,
                    "candidate_value": new_value,
                    "baseline_file": baseline.relative_path,
                    "candidate_file": candidate.relative_path,
                    "sheet": candidate.sheet_name,
                    "row": candidate.row_index,
                }
            )

    return {
        "baseline_rows": len(baseline_records),
        "candidate_rows": len(candidate_records),
        "missing_in_candidate": missing_in_candidate,
        "missing_in_baseline": missing_in_baseline,
        "field_diffs": sum(1 for item in diffs if item.get("diff_type") == "field_diff"),
        "total_diffs": len(diffs),
        "diffs": diffs,
    }


def _load_default_compare_config() -> object:
    from config import config as default_config

    return default_config


def _default_output_path(config_obj: object | None = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resolved_config = config_obj or _load_default_compare_config()
    report_dir = os.path.abspath(str(resolved_config.COMPARE_REPORT_DIR))
    return os.path.join(report_dir, f"ppe_regression_compare_{timestamp}.jsonl")


def _write_outputs(
    *,
    output_jsonl: str,
    output_summary: str,
    summary: Dict[str, Any],
) -> None:
    os.makedirs(os.path.dirname(output_jsonl), exist_ok=True)
    os.makedirs(os.path.dirname(output_summary), exist_ok=True)

    with open(output_jsonl, "w", encoding="utf-8") as handle:
        for item in summary["diffs"]:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    export_summary = {key: value for key, value in summary.items() if key != "diffs"}
    with open(output_summary, "w", encoding="utf-8") as handle:
        json.dump(export_summary, handle, ensure_ascii=False, indent=2)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare parser-full and PPE-applied outputs.")
    parser.add_argument("--baseline-dir", required=True, help="Directory for baseline output files.")
    parser.add_argument("--candidate-dir", required=True, help="Directory for PPE candidate output files.")
    parser.add_argument(
        "--fields",
        default=",".join(DEFAULT_COMPARE_FIELDS),
        help="Comma-separated fields to compare. Default: 挂牌次数,类型,隶属集团",
    )
    parser.add_argument(
        "--candidate-suffix",
        default="_postprocessed",
        help="Filename suffix used by PPE apply output. Default: _postprocessed",
    )
    parser.add_argument("--output-jsonl", default="", help="Path for detailed diff JSONL.")
    parser.add_argument("--output-summary", default="", help="Path for summary JSON.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None, *, config_obj: object | None = None) -> int:
    args = parse_args(argv)
    baseline_dir = os.path.abspath(args.baseline_dir)
    candidate_dir = os.path.abspath(args.candidate_dir)
    compare_fields = [_clean(item) for item in str(args.fields).split(",") if _clean(item)]
    if not compare_fields:
        compare_fields = list(DEFAULT_COMPARE_FIELDS)

    output_jsonl = (
        os.path.abspath(args.output_jsonl)
        if args.output_jsonl
        else _default_output_path(config_obj=config_obj)
    )
    if args.output_summary:
        output_summary = os.path.abspath(args.output_summary)
    else:
        output_summary = os.path.splitext(output_jsonl)[0] + ".summary.json"

    summary = compare_datasets(
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        compare_fields=compare_fields,
        candidate_suffix=str(args.candidate_suffix or ""),
    )
    _write_outputs(output_jsonl=output_jsonl, output_summary=output_summary, summary=summary)

    print(f"baseline_rows={summary['baseline_rows']}")
    print(f"candidate_rows={summary['candidate_rows']}")
    print(f"missing_in_candidate={summary['missing_in_candidate']}")
    print(f"missing_in_baseline={summary['missing_in_baseline']}")
    print(f"field_diffs={summary['field_diffs']}")
    print(f"total_diffs={summary['total_diffs']}")
    print(f"diff_jsonl={output_jsonl}")
    print(f"summary_json={output_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
