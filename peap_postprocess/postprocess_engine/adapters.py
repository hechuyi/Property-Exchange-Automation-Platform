"""Adapter layer for reading/writing Excel and CSV files."""

from __future__ import annotations

import glob
import os
import time
import zipfile
from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class TabularSheet:
    file_path: str
    file_name: str
    sheet_name: str
    dataframe: pd.DataFrame


def _ensure_text_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    data = df.copy()
    data = data.fillna("")
    for column in data.columns:
        data[column] = data[column].map(lambda value: "" if value is None else str(value))
    return data


class ExcelCsvAdapter:
    """File adapter that normalizes tabular data as string-only dataframes."""

    def discover_files(
        self,
        input_dir: str,
        include_globs: List[str],
        *,
        scan_recursive: bool = True,
        input_targets: List[str] | None = None,
    ) -> List[str]:
        results: List[str] = []
        seen = set()

        if input_targets:
            for target in input_targets:
                value = str(target or "").strip()
                if not value:
                    continue
                pattern = value if os.path.isabs(value) else os.path.join(input_dir, value)
                for path in glob.glob(pattern, recursive=True):
                    abs_path = os.path.normpath(os.path.abspath(path))
                    if not os.path.isfile(abs_path):
                        continue
                    if abs_path in seen:
                        continue
                    seen.add(abs_path)
                    results.append(abs_path)
            results.sort()
            return results

        for pattern in include_globs:
            search_path = os.path.join(input_dir, "**", pattern) if scan_recursive else os.path.join(input_dir, pattern)
            for path in glob.glob(search_path, recursive=scan_recursive):
                abs_path = os.path.normpath(os.path.abspath(path))
                if not os.path.isfile(abs_path):
                    continue
                if abs_path in seen:
                    continue
                seen.add(abs_path)
                results.append(abs_path)
        results.sort()
        return results

    def read_file(self, file_path: str) -> List[TabularSheet]:
        suffix = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path)

        if suffix == ".csv":
            df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
            return [
                TabularSheet(
                    file_path=file_path,
                    file_name=file_name,
                    sheet_name="Sheet1",
                    dataframe=_ensure_text_dataframe(df),
                )
            ]

        if suffix in {".xlsx", ".xls"}:
            retry_delays = [0.0, 0.2, 0.6]
            for attempt, delay in enumerate(retry_delays, start=1):
                if delay > 0:
                    time.sleep(delay)
                try:
                    workbook = pd.ExcelFile(file_path)
                    sheets: List[TabularSheet] = []
                    for sheet_name in workbook.sheet_names:
                        df = workbook.parse(sheet_name=sheet_name, dtype=str, keep_default_na=False)
                        sheets.append(
                            TabularSheet(
                                file_path=file_path,
                                file_name=file_name,
                                sheet_name=str(sheet_name),
                                dataframe=_ensure_text_dataframe(df),
                            )
                        )
                    return sheets
                except (EOFError, zipfile.BadZipFile):
                    if attempt == len(retry_delays):
                        raise
                    continue

        raise ValueError(f"unsupported input file: {file_path}")

    def build_output_path(
        self,
        *,
        source_file: str,
        input_dir: str,
        output_dir: str,
        output_suffix: str,
        overwrite: bool,
    ) -> str:
        if overwrite:
            return os.path.normpath(os.path.abspath(source_file))

        rel_path = os.path.relpath(source_file, input_dir)
        rel_dir = os.path.dirname(rel_path)
        name, ext = os.path.splitext(os.path.basename(rel_path))
        output_name = f"{name}{output_suffix}{ext}"
        return os.path.normpath(os.path.abspath(os.path.join(output_dir, rel_dir, output_name)))

    def write_file(self, output_path: str, sheets: List[TabularSheet]) -> None:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        suffix = os.path.splitext(output_path)[1].lower()

        if suffix == ".csv":
            if not sheets:
                pd.DataFrame().to_csv(output_path, index=False, encoding="utf-8-sig")
                return
            sheets[0].dataframe.to_csv(output_path, index=False, encoding="utf-8-sig")
            return

        if suffix in {".xlsx", ".xls"}:
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                if not sheets:
                    pd.DataFrame().to_excel(writer, sheet_name="Sheet1", index=False)
                    return
                for sheet in sheets:
                    safe_name = str(sheet.sheet_name or "Sheet1")
                    sheet.dataframe.to_excel(writer, sheet_name=safe_name[:31], index=False)
            return

        raise ValueError(f"unsupported output file: {output_path}")
