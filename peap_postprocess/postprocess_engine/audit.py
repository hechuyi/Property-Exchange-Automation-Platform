"""Writer for audit workbook outputs."""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime
from typing import List

import pandas as pd

from .contracts import AuditRow, ExecutionSummary

AUDIT_COLUMNS = [
    "run_id",
    "timestamp",
    "file",
    "sheet",
    "row",
    "project_code",
    "rule_id",
    "severity",
    "action",
    "field",
    "old_value",
    "new_value",
    "reason",
    "evidence",
]

SUMMARY_COLUMNS = [
    "run_id",
    "mode",
    "generated_at",
    "discovered_files",
    "processed_files",
    "processed_rows",
    "applied_patches",
    "findings",
    "failed_files",
    "errors",
]


def _rows_to_dataframe(rows: List[AuditRow]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=AUDIT_COLUMNS)
    return pd.DataFrame([asdict(row) for row in rows], columns=AUDIT_COLUMNS)


class AuditReportWriter:
    def write(
        self,
        *,
        audit_dir: str,
        summary: ExecutionSummary,
        audit_rows: List[AuditRow],
    ) -> str:
        os.makedirs(audit_dir, exist_ok=True)
        output_path = os.path.abspath(os.path.join(audit_dir, f"audit_{summary.run_id}.xlsx"))

        all_df = _rows_to_dataframe(audit_rows)
        changes_df = all_df[all_df["field"].astype(str) != ""].copy()
        finding_df = all_df[all_df["field"].astype(str) == ""].copy()
        lower_action = finding_df["action"].astype(str).str.lower()
        lower_reason = finding_df["reason"].astype(str).str.lower()
        lower_severity = finding_df["severity"].astype(str).str.lower()

        conflicts_df = finding_df[(lower_action.str.contains("conflict")) | (lower_reason.str.contains("conflict"))]
        no_match_df = finding_df[(lower_action.str.contains("no_match")) | (lower_reason.str.contains("no_match"))]
        ambiguous_df = finding_df[
            (lower_action.str.contains("ambiguous")) | (lower_reason.str.contains("ambiguous"))
        ]
        errors_df = finding_df[lower_severity == "error"].copy()
        if summary.errors:
            engine_errors = pd.DataFrame(
                [
                    {
                        "run_id": summary.run_id,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "file": "",
                        "sheet": "",
                        "row": 0,
                        "project_code": "",
                        "rule_id": "engine",
                        "severity": "error",
                        "action": "runtime_error",
                        "field": "",
                        "old_value": "",
                        "new_value": "",
                        "reason": err,
                        "evidence": "",
                    }
                    for err in summary.errors
                ],
                columns=AUDIT_COLUMNS,
            )
            errors_df = pd.concat([errors_df, engine_errors], ignore_index=True)

        summary_df = pd.DataFrame(
            [
                {
                    "run_id": summary.run_id,
                    "mode": summary.mode,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "discovered_files": summary.discovered_files,
                    "processed_files": summary.processed_files,
                    "processed_rows": summary.processed_rows,
                    "applied_patches": summary.applied_patches,
                    "findings": summary.findings,
                    "failed_files": summary.failed_files,
                    "errors": len(summary.errors),
                }
            ],
            columns=SUMMARY_COLUMNS,
        )

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="summary", index=False)
            changes_df.to_excel(writer, sheet_name="changes", index=False)
            finding_df.to_excel(writer, sheet_name="findings_all", index=False)
            conflicts_df.to_excel(writer, sheet_name="conflicts", index=False)
            no_match_df.to_excel(writer, sheet_name="no_match", index=False)
            ambiguous_df.to_excel(writer, sheet_name="ambiguous", index=False)
            errors_df.to_excel(writer, sheet_name="errors", index=False)

        return output_path
