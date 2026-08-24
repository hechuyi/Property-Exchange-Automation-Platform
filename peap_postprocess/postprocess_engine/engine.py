"""Main execution pipeline for PostProcess Engine."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Set

from peap_core.business_catalog import resolve_business_descriptor
from peap_core.family_catalog import resolve_family_descriptor

from .adapters import ExcelCsvAdapter, TabularSheet
from .audit import AuditReportWriter
from .canonical import canonicalize_sheet
from .config import PPEConfig, resolve_mode
from .contracts import AuditRow, CanonicalRecord, ExecutionSummary, Finding, Patch, RuleResult
from .rules import RuleBinding, RuleRegistry

RECORD_FAMILY_FIELDS = ("record_family", "family", "记录族", "记录类型")
BUSINESS_ID_FAMILY_FIELDS = ("business_id", "business_id_hint", "业务ID", "业务类型ID")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _serialize_evidence(evidence: Dict[str, Any]) -> str:
    if not evidence:
        return ""
    try:
        return json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(evidence)


def _format_exc(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


def _normalize_record_family(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    descriptor = resolve_family_descriptor(text)
    return descriptor.family_id if descriptor is not None else ""


def _infer_record_family(record: CanonicalRecord) -> str:
    raw_fields = dict(record.raw_fields or {})
    for field_name in RECORD_FAMILY_FIELDS:
        family = _normalize_record_family(raw_fields.get(field_name))
        if family:
            return family
    for field_name in BUSINESS_ID_FAMILY_FIELDS:
        business_id = str(raw_fields.get(field_name) or "").strip()
        if not business_id:
            continue
        try:
            descriptor = resolve_business_descriptor(business_id)
        except KeyError:
            descriptor = None
        if descriptor is not None:
            return descriptor.family_id
        lowered = business_id.lower()
        if lowered.startswith("deal_") or lowered.startswith("deal "):
            return "deal"
    return ""


class PostProcessEngine:
    def __init__(
        self,
        *,
        adapter: ExcelCsvAdapter | None = None,
        registry: RuleRegistry | None = None,
        audit_writer: AuditReportWriter | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.adapter = adapter or ExcelCsvAdapter()
        self.registry = registry or RuleRegistry()
        self.audit_writer = audit_writer or AuditReportWriter()
        self.logger = logger or logging.getLogger("ppe")

    def run(self, config: PPEConfig, *, mode_override: str | None = None) -> ExecutionSummary:
        mode = resolve_mode(config, mode_override)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary = ExecutionSummary(run_id=run_id, mode=mode)
        audit_rows: List[AuditRow] = []

        files = self.adapter.discover_files(
            config.input_dir,
            config.include_globs,
            scan_recursive=config.scan_recursive,
            input_targets=config.input_targets,
        )
        files = self._exclude_files(files, config.exclude_dirs)
        summary.discovered_files = len(files)
        self.logger.info("PPE discovered files: %s", len(files))

        rule_plan_cache: Dict[str, List[RuleBinding]] = {}
        warning_cache: Set[str] = set()

        for file_path in files:
            try:
                sheets = self.adapter.read_file(file_path)
            except Exception as exc:  # noqa: BLE001
                summary.failed_files += 1
                error = f"{file_path}: read-failed: {_format_exc(exc)}"
                summary.errors.append(error)
                self.logger.exception("PPE read failed: %s", file_path)
                continue

            try:
                self._process_sheets(
                    sheets=sheets,
                    mode=mode,
                    run_id=run_id,
                    rules_config=config.rules,
                    rule_plan_cache=rule_plan_cache,
                    warning_cache=warning_cache,
                    summary=summary,
                    audit_rows=audit_rows,
                )
                summary.processed_files += 1
            except Exception as exc:  # noqa: BLE001
                summary.failed_files += 1
                error = f"{file_path}: process-failed: {_format_exc(exc)}"
                summary.errors.append(error)
                self.logger.exception("PPE process failed: %s", file_path)
                continue

            if mode == "apply":
                output_path = self.adapter.build_output_path(
                    source_file=file_path,
                    input_dir=config.input_dir,
                    output_dir=config.output_dir,
                    output_suffix=config.output_suffix,
                    overwrite=config.overwrite,
                )
                try:
                    self.adapter.write_file(output_path, sheets)
                    summary.output_files.append(output_path)
                except Exception as exc:  # noqa: BLE001
                    summary.failed_files += 1
                    error = f"{file_path}: write-failed: {_format_exc(exc)}"
                    summary.errors.append(error)
                    self.logger.exception("PPE write failed: %s", file_path)

        try:
            summary.audit_report = self.audit_writer.write(
                audit_dir=config.audit_dir,
                summary=summary,
                audit_rows=audit_rows,
            )
            self.logger.info("PPE audit report written: %s", summary.audit_report)
        except Exception as exc:  # noqa: BLE001
            summary.failed_files += 1
            error = f"{config.audit_dir}: audit-write-failed: {_format_exc(exc)}"
            summary.errors.append(error)
            self.logger.exception("PPE audit write failed")

        return summary

    def _exclude_files(self, files: List[str], exclude_dirs: List[str]) -> List[str]:
        if not exclude_dirs:
            return files

        normalized_prefixes = []
        excluded_names = {"postprocess_output"}
        for path in exclude_dirs:
            normalized = os.path.normcase(os.path.normpath(os.path.abspath(path)))
            normalized_prefixes.append(normalized.rstrip("\\/") + os.sep)

        filtered: List[str] = []
        for file_path in files:
            normalized_file = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
            parts = [part for part in normalized_file.split(os.sep) if part]
            if any(part in excluded_names for part in parts):
                continue
            if any(normalized_file.startswith(prefix) for prefix in normalized_prefixes):
                continue
            filtered.append(file_path)
        return filtered

    def _process_sheets(
        self,
        *,
        sheets: List[TabularSheet],
        mode: str,
        run_id: str,
        rules_config: Dict[str, Any],
        rule_plan_cache: Dict[str, List[RuleBinding]],
        warning_cache: Set[str],
        summary: ExecutionSummary,
        audit_rows: List[AuditRow],
    ) -> None:
        for sheet in sheets:
            filtered_rows: Set[int] = set()
            records = canonicalize_sheet(sheet)
            for record in records:
                summary.processed_rows += 1
                record_family = _infer_record_family(record)
                rule_plan = self._rule_plan_for_record_family(
                    rules_config=rules_config,
                    record_family=record_family,
                    rule_plan_cache=rule_plan_cache,
                    warning_cache=warning_cache,
                )
                self._process_record(
                    sheet=sheet,
                    record=record,
                    record_family=record_family,
                    mode=mode,
                    run_id=run_id,
                    rule_plan=rule_plan,
                    summary=summary,
                    audit_rows=audit_rows,
                    filtered_rows=filtered_rows,
                )
            if mode == "apply" and filtered_rows:
                self._drop_rows(sheet, filtered_rows)

    def _process_record(
        self,
        *,
        sheet: TabularSheet,
        record: CanonicalRecord,
        record_family: str,
        mode: str,
        run_id: str,
        rule_plan: List[RuleBinding],
        summary: ExecutionSummary,
        audit_rows: List[AuditRow],
        filtered_rows: Set[int],
    ) -> None:
        for binding in rule_plan:
            rule = binding.rule
            context: Dict[str, Any] = {
                "mode": mode,
                "run_id": run_id,
                "record_family": record_family,
                "rule_priority": binding.priority,
                "rule_params": dict(getattr(rule, "params", {}) or {}),
            }
            if not rule.applies(record, context):
                continue

            try:
                result = rule.apply(record, context)
            except Exception as exc:  # noqa: BLE001
                result = RuleResult(
                    findings=[
                        Finding(
                            rule_id=rule.rule_id(),
                            severity="error",
                            type="rule_error",
                            message=str(exc),
                            evidence={},
                        )
                    ],
                    stop_processing=True,
                )

            self._consume_rule_result(
                sheet=sheet,
                record=record,
                mode=mode,
                run_id=run_id,
                rule_id=rule.rule_id(),
                result=result,
                summary=summary,
                audit_rows=audit_rows,
                filtered_rows=filtered_rows,
            )

            if result.stop_processing:
                break

    def _rule_plan_for_record_family(
        self,
        *,
        rules_config: Dict[str, Any],
        record_family: str,
        rule_plan_cache: Dict[str, List[RuleBinding]],
        warning_cache: Set[str],
    ) -> List[RuleBinding]:
        cache_key = record_family or ""
        if cache_key not in rule_plan_cache:
            rule_plan, warnings = self.registry.build_plan(rules_config, record_family=record_family)
            for message in warnings:
                if message in warning_cache:
                    continue
                warning_cache.add(message)
                self.logger.warning(message)
            self.logger.info(
                "PPE enabled rules for record_family=%s: %s",
                record_family or "unknown",
                [binding.rule.rule_id() for binding in rule_plan],
            )
            rule_plan_cache[cache_key] = rule_plan
        return rule_plan_cache[cache_key]

    def _consume_rule_result(
        self,
        *,
        sheet: TabularSheet,
        record: CanonicalRecord,
        mode: str,
        run_id: str,
        rule_id: str,
        result: RuleResult,
        summary: ExecutionSummary,
        audit_rows: List[AuditRow],
        filtered_rows: Set[int],
    ) -> None:
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for patch in result.patches:
            summary.applied_patches += 1
            if mode == "apply" and patch.action == "filter_out_row":
                filtered_rows.add(int(record.row_index))
            elif mode == "apply":
                self._apply_patch(sheet, record.row_index, patch)
            audit_rows.append(
                AuditRow(
                    run_id=run_id,
                    timestamp=now_text,
                    file=record.file_name,
                    sheet=record.sheet_name,
                    row=record.row_index,
                    project_code=record.project_code,
                    rule_id=rule_id,
                    severity="info",
                    action=patch.action,
                    field=patch.field,
                    old_value=_as_text(patch.old_value),
                    new_value=_as_text(patch.new_value),
                    reason=patch.reason,
                    evidence="",
                )
            )

        for finding in result.findings:
            summary.findings += 1
            audit_rows.append(
                AuditRow(
                    run_id=run_id,
                    timestamp=now_text,
                    file=record.file_name,
                    sheet=record.sheet_name,
                    row=record.row_index,
                    project_code=record.project_code,
                    rule_id=finding.rule_id or rule_id,
                    severity=finding.severity,
                    action=finding.type,
                    field="",
                    old_value="",
                    new_value="",
                    reason=finding.message,
                    evidence=_serialize_evidence(finding.evidence),
                )
            )

    def _apply_patch(self, sheet: TabularSheet, row_index: int, patch: Patch) -> None:
        row_pos = int(row_index) - 2
        if row_pos < 0 or row_pos >= len(sheet.dataframe.index):
            return

        if patch.field not in sheet.dataframe.columns:
            sheet.dataframe[patch.field] = ""

        index_label = sheet.dataframe.index[row_pos]
        sheet.dataframe.at[index_label, patch.field] = _as_text(patch.new_value)

    def _drop_rows(self, sheet: TabularSheet, filtered_rows: Set[int]) -> None:
        row_positions = sorted({int(row_index) - 2 for row_index in filtered_rows})
        valid_positions = [pos for pos in row_positions if 0 <= pos < len(sheet.dataframe.index)]
        if not valid_positions:
            return
        index_labels = [sheet.dataframe.index[pos] for pos in valid_positions]
        sheet.dataframe.drop(index=index_labels, inplace=True)
        sheet.dataframe.reset_index(drop=True, inplace=True)
