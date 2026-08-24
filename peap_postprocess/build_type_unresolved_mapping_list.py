#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build unresolved source-type query list from PPE tables."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd


def _add_project_root_to_syspath() -> str:
    system_dir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.abspath(os.path.join(system_dir, ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return system_dir


SYSTEM_DIR = os.path.normpath(_add_project_root_to_syspath())
DEFAULT_CONFIG_PATH = os.path.join(SYSTEM_DIR, "ppe_config", "postprocess_external_template.json")
DEFAULT_OUTPUT_FILENAME = "类型未解析_待查询映射.xlsx"

from peap_postprocess.postprocess_engine.adapters import ExcelCsvAdapter  # noqa: E402
from peap_postprocess.postprocess_engine.config import load_config  # noqa: E402
from peap_postprocess.postprocess_engine.engine import PostProcessEngine  # noqa: E402

TYPE_ALIASES = ["类型", "source_type", "SOURCE_TYPE"]
GROUP_ALIASES = ["隶属集团", "所属集团", "group_name", "group", "集团名称"]
TRANSFEROR_ALIASES = [
    "转让方名称",
    "转让方",
    "融资方名称",
    "融资方",
    "seller_name",
    "seller",
    "company_name_primary",
    "company_name",
]
PROJECT_CODE_ALIASES = ["项目编号", "project_code", "PROJECT_CODE"]
ALLOWED_TYPES = {"央企", "部委", "市属", "民营"}


def _clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", _clean(text)).lower()


def _alias_map(raw_value: str) -> str:
    raw = _clean(raw_value)
    mapping = {
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
    for key, value in mapping.items():
        if key in raw:
            return value
    return ""


def _resolve_first(row: Dict[str, str], aliases: Iterable[str]) -> str:
    for key in aliases:
        if key in row and _clean(row.get(key)):
            return _clean(row.get(key))
    return ""


def _parse_int(value: object) -> int:
    text = _clean(value)
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:  # noqa: BLE001
        return 0


def _split_transferors(text: str) -> List[str]:
    value = _clean(text)
    if not value:
        return []
    # Keep ASCII comma as part of company/group names.
    parts = [item.strip() for item in re.split(r"[，、；;/|]+", value) if _clean(item)]
    names: List[str] = []
    seen = set()
    for item in parts:
        name = re.sub(r"[（(]\s*\d+(?:\.\d+)?%?\s*[）)]$", "", item).strip()
        if not name:
            continue
        key = _normalize_key(name)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _split_groups(text: str) -> List[str]:
    value = _clean(text)
    if not value:
        return []
    # Keep ASCII comma as part of company/group names.
    parts = [item.strip() for item in re.split(r"[，、；;/|]+", value) if _clean(item)]
    names: List[str] = []
    seen = set()
    for item in parts:
        key = _normalize_key(item)
        if key in seen:
            continue
        seen.add(key)
        names.append(item)
    return names

@dataclass
class CandidateStats:
    display_name: str = ""
    count: int = 0
    files: set[str] | None = None
    project_codes: List[str] | None = None

    def __post_init__(self) -> None:
        if self.files is None:
            self.files = set()
        if self.project_codes is None:
            self.project_codes = []

    def add(self, *, file_name: str, project_code: str) -> None:
        self.count += 1
        if file_name:
            self.files.add(file_name)
        code = _clean(project_code)
        if code and code not in self.project_codes:
            if len(self.project_codes) < 5:
                self.project_codes.append(code)


@dataclass
class MappingSnapshot:
    transferor_type_names: set[str]
    transferor_group_map: Dict[str, set[str]]
    group_type_names: set[str]
    group_parent_map: Dict[str, set[str]]
    table_rows: Dict[str, int]
    table_paths: Dict[str, str]


def _read_rows(path: str) -> List[Dict[str, str]]:
    file_path = Path(str(path)).resolve()
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(file_path, dtype=str, keep_default_na=False)
    else:
        return []
    return [{str(col): _clean(row.get(col)) for col in df.columns} for _, row in df.iterrows()]


def _resolve_r005_table_paths(config) -> Dict[str, str]:
    paths = {
        "transferor_type": "",
        "transferor_group": "",
        "group_group": "",
        "group_type": "",
    }
    rule = (config.rules or {}).get("R005_normalize_source_type")
    params = dict((rule.params if rule else {}) or {})
    paths["transferor_type"] = _clean(params.get("transferor_type_mapping_file"))
    paths["transferor_group"] = _clean(params.get("transferor_group_mapping_file"))
    paths["group_group"] = _clean(params.get("group_group_mapping_file"))
    paths["group_type"] = _clean(params.get("group_type_mapping_file") or params.get("entity_type_mapping_file"))
    return paths


def _build_mapping_snapshot(config) -> MappingSnapshot:
    paths = _resolve_r005_table_paths(config)
    transferor_type_names: set[str] = set()
    transferor_group_map: Dict[str, set[str]] = {}
    group_type_names: set[str] = set()
    group_parent_map: Dict[str, set[str]] = {}
    table_rows = {name: 0 for name in paths.keys()}

    transferor_type_path = paths.get("transferor_type", "")
    if transferor_type_path and os.path.exists(transferor_type_path):
        rows = _read_rows(transferor_type_path)
        table_rows["transferor_type"] = len(rows)
        for row in rows:
            name = _resolve_first(row, ["transferor_name", "transferor", "seller_name", "seller", "name"])
            source_type = _resolve_first(row, ["source_type", "type", "category"])
            if _normalize_key(name) and source_type:
                transferor_type_names.add(_normalize_key(name))

    transferor_group_path = paths.get("transferor_group", "")
    if transferor_group_path and os.path.exists(transferor_group_path):
        rows = _read_rows(transferor_group_path)
        table_rows["transferor_group"] = len(rows)
        for row in rows:
            transferor_name = _resolve_first(row, ["transferor_name", "transferor", "seller_name", "seller", "name"])
            group_name = _resolve_first(row, ["group_name", "group"])
            transferor_key = _normalize_key(transferor_name)
            if not transferor_key or not _clean(group_name):
                continue
            transferor_group_map.setdefault(transferor_key, set()).add(_clean(group_name))

    group_group_path = paths.get("group_group", "")
    if group_group_path and os.path.exists(group_group_path):
        rows = _read_rows(group_group_path)
        table_rows["group_group"] = len(rows)
        for row in rows:
            group_name = _resolve_first(row, ["group_name", "group", "child_group_name", "child_group"])
            parent_group_name = _resolve_first(row, ["parent_group_name", "parent_group", "parent_name", "group_parent"])
            group_key = _normalize_key(group_name)
            if not group_key or not _clean(parent_group_name):
                continue
            group_parent_map.setdefault(group_key, set()).add(_clean(parent_group_name))

    group_type_path = paths.get("group_type", "")
    if group_type_path and os.path.exists(group_type_path):
        rows = _read_rows(group_type_path)
        table_rows["group_type"] = len(rows)
        for row in rows:
            group_name = _resolve_first(row, ["group_name", "group", "entity_name", "name"])
            source_type = _resolve_first(row, ["source_type", "type", "category"])
            if _normalize_key(group_name) and source_type:
                group_type_names.add(_normalize_key(group_name))

    return MappingSnapshot(
        transferor_type_names=transferor_type_names,
        transferor_group_map=transferor_group_map,
        group_type_names=group_type_names,
        group_parent_map=group_parent_map,
        table_rows=table_rows,
        table_paths=paths,
    )


def _resolve_group_chain(group_name: str, snapshot: MappingSnapshot) -> Tuple[str, bool]:
    current = _clean(group_name)
    visited: set[str] = set()
    while current:
        key = _normalize_key(current)
        if not key:
            break
        if key in visited:
            return current, True
        visited.add(key)
        parents = snapshot.group_parent_map.get(key, set())
        if len(parents) != 1:
            return current, False
        current = next(iter(parents))
    return current, False


def _has_group_type(group_name: str, snapshot: MappingSnapshot) -> bool:
    direct_key = _normalize_key(group_name)
    if direct_key and direct_key in snapshot.group_type_names:
        return True
    resolved_group, _cycle = _resolve_group_chain(group_name, snapshot)
    resolved_key = _normalize_key(resolved_group)
    return bool(resolved_key and resolved_key in snapshot.group_type_names)


def _build_company_todo_views(
    unresolved_df: pd.DataFrame,
    snapshot: MappingSnapshot,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    row_columns = [
        "company_name",
        "need_mapping",
        "project_code",
        "file",
        "sheet",
        "row",
        "transferor_raw",
        "group_raw",
    ]
    summary_columns = [
        "company_name",
        "need_mapping",
        "unresolved_rows",
        "sample_project_codes",
        "sample_files",
    ]

    if unresolved_df.empty:
        return pd.DataFrame(columns=row_columns), pd.DataFrame(columns=summary_columns)

    rows: List[Dict[str, str]] = []
    for _, row in unresolved_df.iterrows():
        project_code = _clean(row.get("project_code"))
        file_name = _clean(row.get("file"))
        sheet_name = _clean(row.get("sheet"))
        row_num = str(row.get("row", "") or "").strip()
        transferor_raw = _clean(row.get("transferor_raw"))
        group_raw = _clean(row.get("group_raw"))
        preferred_kind = _clean(row.get("preferred_query_kind"))
        preferred_name = _clean(row.get("preferred_query_name"))

        transferor_list = [item.strip() for item in str(row.get("transferor_candidates", "")).split(";") if _clean(item)]
        group_list = [item.strip() for item in str(row.get("group_candidates", "")).split(";") if _clean(item)]
        transferor_keys = {_normalize_key(item) for item in transferor_list if _normalize_key(item)}
        mapped_groups: set[str] = set()
        for key in transferor_keys:
            mapped_groups.update(snapshot.transferor_group_map.get(key, set()))

        company_name = preferred_name or (transferor_list[0] if transferor_list else (group_list[0] if group_list else ""))
        if not company_name:
            company_name = project_code or f"{file_name}:{sheet_name}:{row_num}"

        need_mapping = "类型"
        if preferred_kind == "transferor":
            transferor_key = _normalize_key(company_name)
            has_transferor_type = bool(transferor_key and transferor_key in snapshot.transferor_type_names)
            has_transferor_group = bool(transferor_key and transferor_key in snapshot.transferor_group_map)
            has_group_type = any(_has_group_type(group_name, snapshot) for group_name in group_list)
            has_mapped_group_type = any(_has_group_type(group_name, snapshot) for group_name in mapped_groups)

            if not has_transferor_type and not has_transferor_group:
                need_mapping = "集团或类型"
            elif has_transferor_type or has_group_type or has_mapped_group_type:
                need_mapping = "类型"
            else:
                need_mapping = "类型"
        elif preferred_kind == "group":
            has_group_type = any(_has_group_type(group_name, snapshot) for group_name in ([company_name] + group_list))
            need_mapping = "类型" if has_group_type else "类型"
        else:
            need_mapping = "集团或类型" if transferor_list else "类型"

        rows.append(
            {
                "company_name": company_name,
                "need_mapping": need_mapping,
                "project_code": project_code,
                "file": file_name,
                "sheet": sheet_name,
                "row": row_num,
                "transferor_raw": transferor_raw,
                "group_raw": group_raw,
            }
        )

    row_df = pd.DataFrame(rows, columns=row_columns)

    def _join_samples(series: pd.Series, limit: int) -> str:
        seen: List[str] = []
        for value in series.astype(str):
            text = _clean(value)
            if not text or text in seen:
                continue
            seen.append(text)
            if len(seen) >= limit:
                break
        return ";".join(seen)

    summary_df = (
        row_df.groupby(["company_name", "need_mapping"], as_index=False)
        .agg(
            unresolved_rows=("project_code", "count"),
            sample_project_codes=("project_code", lambda x: _join_samples(x, 5)),
            sample_files=("file", lambda x: _join_samples(x, 3)),
        )
        .sort_values(by=["unresolved_rows", "company_name"], ascending=[False, True])
    )

    return row_df, summary_df


def _build_outputs(
    *,
    input_files: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    adapter = ExcelCsvAdapter()
    unresolved_rows: List[Dict[str, object]] = []
    transferor_stats: Dict[str, CandidateStats] = {}
    group_stats: Dict[str, CandidateStats] = {}

    for file_path in input_files:
        file_name = os.path.basename(file_path)
        sheets = adapter.read_file(file_path)
        for sheet in sheets:
            df = sheet.dataframe
            for idx, src_row in df.iterrows():
                row_num = int(idx) + 2
                row_dict: Dict[str, str] = {
                    str(column).strip(): _clean(value) for column, value in src_row.to_dict().items()
                }

                if not any(_clean(value) for value in row_dict.values()):
                    continue

                raw_type = _resolve_first(row_dict, TYPE_ALIASES)
                canonical_type = raw_type if raw_type in ALLOWED_TYPES else _alias_map(raw_type)
                if canonical_type:
                    continue

                transferor_raw = _resolve_first(row_dict, TRANSFEROR_ALIASES)
                group_raw = _resolve_first(row_dict, GROUP_ALIASES)
                project_code = _resolve_first(row_dict, PROJECT_CODE_ALIASES)

                transferor_list = _split_transferors(transferor_raw)
                group_list = _split_groups(group_raw)

                preferred_kind = ""
                preferred_name = ""
                if transferor_list:
                    preferred_kind = "transferor"
                    preferred_name = transferor_list[0]
                elif group_list:
                    preferred_kind = "group"
                    preferred_name = group_list[0]

                unresolved_rows.append(
                    {
                        "file": file_name,
                        "sheet": sheet.sheet_name,
                        "row": row_num,
                        "project_code": project_code,
                        "final_type_raw": raw_type,
                        "transferor_raw": transferor_raw,
                        "group_raw": group_raw,
                        "transferor_candidates": ";".join(transferor_list),
                        "group_candidates": ";".join(group_list),
                        "preferred_query_kind": preferred_kind,
                        "preferred_query_name": preferred_name,
                    }
                )

                for transferor_name in transferor_list:
                    key = _normalize_key(transferor_name)
                    if not key:
                        continue
                    stats = transferor_stats.get(key)
                    if stats is None:
                        stats = CandidateStats(display_name=transferor_name)
                        transferor_stats[key] = stats
                    stats.add(file_name=file_name, project_code=project_code)

                for group_name in group_list:
                    key = _normalize_key(group_name)
                    if not key:
                        continue
                    stats = group_stats.get(key)
                    if stats is None:
                        stats = CandidateStats(display_name=group_name)
                        group_stats[key] = stats
                    stats.add(file_name=file_name, project_code=project_code)

    unresolved_df = pd.DataFrame(unresolved_rows)
    if unresolved_df.empty:
        unresolved_df = pd.DataFrame(
            columns=[
                "file",
                "sheet",
                "row",
                "project_code",
                "final_type_raw",
                "transferor_raw",
                "group_raw",
                "transferor_candidates",
                "group_candidates",
                "preferred_query_kind",
                "preferred_query_name",
            ]
        )
    else:
        unresolved_df.sort_values(by=["file", "sheet", "row"], inplace=True)

    transferor_rows = []
    for stats in transferor_stats.values():
        transferor_rows.append(
            {
                "transferor_name": stats.display_name,
                "unresolved_rows": stats.count,
                "files": ";".join(sorted(stats.files or [])),
                "sample_project_codes": ";".join(stats.project_codes or []),
            }
        )
    transferor_df = pd.DataFrame(transferor_rows)
    if transferor_df.empty:
        transferor_df = pd.DataFrame(
            columns=["transferor_name", "unresolved_rows", "files", "sample_project_codes"]
        )
    else:
        transferor_df.sort_values(by=["unresolved_rows", "transferor_name"], ascending=[False, True], inplace=True)

    group_rows = []
    for stats in group_stats.values():
        group_rows.append(
            {
                "group_name": stats.display_name,
                "unresolved_rows": stats.count,
                "files": ";".join(sorted(stats.files or [])),
                "sample_project_codes": ";".join(stats.project_codes or []),
            }
        )
    group_df = pd.DataFrame(group_rows)
    if group_df.empty:
        group_df = pd.DataFrame(columns=["group_name", "unresolved_rows", "files", "sample_project_codes"])
    else:
        group_df.sort_values(by=["unresolved_rows", "group_name"], ascending=[False, True], inplace=True)

    return unresolved_df, transferor_df, group_df


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build unresolved source-type query workbook.")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="PPE config path; defaults to the external-output template",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output xlsx path; default writes under the configured output_dir",
    )
    return parser


def _build_output_candidate_paths(config, input_files: List[str], adapter: ExcelCsvAdapter) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()
    for source_file in input_files:
        output_path = adapter.build_output_path(
            source_file=source_file,
            input_dir=config.input_dir,
            output_dir=config.output_dir,
            output_suffix=config.output_suffix,
            overwrite=config.overwrite,
        )
        normalized = str(Path(output_path).resolve())
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _resolve_source_files(config, input_files: List[str], adapter: ExcelCsvAdapter) -> List[str]:
    output_candidates = _build_output_candidate_paths(config, input_files, adapter)
    existing_outputs = [path for path in output_candidates if os.path.exists(path)]
    return existing_outputs or input_files


def _cleanup_legacy_outputs(output_path: Path) -> None:
    pattern = re.compile(r"^类型未解析_待查询映射_(?:latest|\d{8}_\d{6})\.xlsx$", re.IGNORECASE)
    for candidate in output_path.parent.glob("类型未解析_待查询映射*.xlsx"):
        if candidate.resolve() == output_path.resolve():
            continue
        if not pattern.match(candidate.name):
            continue
        try:
            candidate.unlink()
        except Exception:
            continue


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = ExcelCsvAdapter()
    engine = PostProcessEngine(adapter=adapter)

    input_files = adapter.discover_files(
        config.input_dir,
        config.include_globs,
        scan_recursive=config.scan_recursive,
        input_targets=config.input_targets,
    )
    input_files = engine._exclude_files(input_files, config.exclude_dirs)
    source_files = _resolve_source_files(config, input_files, adapter)
    unresolved_df, transferor_df, group_df = _build_outputs(input_files=source_files)
    mapping_snapshot = _build_mapping_snapshot(config)
    mapping_todo_rows_df, mapping_todo_df = _build_company_todo_views(unresolved_df, mapping_snapshot)

    output_path = Path(args.output).resolve() if _clean(args.output) else (
        Path(config.output_dir) / DEFAULT_OUTPUT_FILENAME
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_outputs(output_path)

    pending_transferor_df = pd.DataFrame(
        {
            "transferor_name": transferor_df["transferor_name"] if not transferor_df.empty else [],
            "source_type": "",
            "notes": (
                "from unresolved_rows=" + transferor_df["unresolved_rows"].astype(str)
                if not transferor_df.empty
                else []
            ),
        }
    )
    pending_group_df = pd.DataFrame(
        {
            "group_name": group_df["group_name"] if not group_df.empty else [],
            "source_type": "",
            "notes": (
                "from unresolved_rows=" + group_df["unresolved_rows"].astype(str) if not group_df.empty else []
            ),
        }
    )
    pending_group_group_df = pd.DataFrame(
        {
            "group_name": group_df["group_name"] if not group_df.empty else [],
            "parent_group_name": "",
            "notes": (
                "alias_or_parent_from_unresolved_rows=" + group_df["unresolved_rows"].astype(str)
                if not group_df.empty
                else []
            ),
        }
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        mapping_todo_df.to_excel(writer, sheet_name="mapping_todo", index=False)
        mapping_todo_rows_df.to_excel(writer, sheet_name="mapping_todo_rows", index=False)
        unresolved_df.to_excel(writer, sheet_name="unresolved_rows", index=False)
        transferor_df.to_excel(writer, sheet_name="transferor_candidates", index=False)
        group_df.to_excel(writer, sheet_name="group_candidates", index=False)
        pending_transferor_df.to_excel(writer, sheet_name="pending_transferor_type", index=False)
        pending_group_df.to_excel(writer, sheet_name="pending_group_type", index=False)
        pending_group_group_df.to_excel(writer, sheet_name="pending_group_group", index=False)
    print(f"source_files={len(source_files)}")
    print(f"unresolved_rows={len(unresolved_df)}")
    print(f"mapping_todo_companies={len(mapping_todo_df)}")
    print(f"transferor_candidates={len(transferor_df)}")
    print(f"group_candidates={len(group_df)}")
    print(
        "mapping_tables="
        f"transferor_type:{mapping_snapshot.table_rows.get('transferor_type', 0)},"
        f"transferor_group:{mapping_snapshot.table_rows.get('transferor_group', 0)},"
        f"group_group:{mapping_snapshot.table_rows.get('group_group', 0)},"
        f"group_type:{mapping_snapshot.table_rows.get('group_type', 0)}"
    )
    print(f"output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
