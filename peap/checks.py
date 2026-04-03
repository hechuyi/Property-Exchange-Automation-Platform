"""Project self-check routines."""

import importlib
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .standard_model import LEGACY_PAYLOAD_KEYS
from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_TYPE, KEY_STATUS
from .excel_handler import (
    build_excel_schema_settings,
    get_excel_schema_status,
    get_output_schema_snapshot,
    load_excel_output_runtime,
    validate_configured_excel_output_schema,
    validate_excel_output_schema,
)
from .output_mapping import (
    get_output_mapping_contract,
    get_raw_fallback_contract,
    map_standard_to_excel_payload,
    validate_output_field_map,
)
from .parsing import build_parsed_project
from .pipeline import ParserPipeline, ParserPipelineSettings, build_parser_pipeline_settings


def _load_default_checks_config() -> object:
    from config import config as default_config

    return default_config


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    level: str = "error"


def _check_python_version() -> CheckResult:
    if sys.version_info >= (3, 11):
        return CheckResult("python-version", True, f"Python {sys.version.split()[0]} detected")
    return CheckResult("python-version", False, "Python 3.11+ is required")


def _check_import(module_name: str, required: bool = True) -> CheckResult:
    try:
        importlib.import_module(module_name)
        return CheckResult(f"import-{module_name}", True, "ok")
    except Exception as exc:
        if required:
            return CheckResult(
                f"import-{module_name}",
                False,
                f"missing dependency: {module_name} ({exc})",
            )
        return CheckResult(
            f"import-{module_name}",
            True,
            f"optional dependency unavailable: {module_name} ({exc})",
            level="warning",
        )


def _check_html_root(html_root: str) -> CheckResult:
    if os.path.isdir(html_root):
        return CheckResult("html-root", True, f"exists: {html_root}")
    return CheckResult("html-root", False, f"missing directory: {html_root}")


def _check_output_config(config_obj: object) -> List[CheckResult]:
    results: List[CheckResult] = []
    expected_output_keys = {"equity_transfer", "pre_disclosure", "physical_asset", "capital_increase"}
    expected_deal_keys = {"equity_transfer", "physical_asset", "capital_increase"}

    if expected_output_keys.issubset(set(config_obj.OUTPUT_FILES.keys())):
        results.append(CheckResult("output-files", True, "output file config complete"))
    else:
        missing = sorted(expected_output_keys - set(config_obj.OUTPUT_FILES.keys()))
        results.append(CheckResult("output-files", False, f"missing output keys: {missing}"))

    if expected_deal_keys.issubset(set(config_obj.DEAL_FILES.keys())):
        results.append(CheckResult("deal-files", True, "deal file config complete"))
    else:
        missing = sorted(expected_deal_keys - set(config_obj.DEAL_FILES.keys()))
        results.append(CheckResult("deal-files", False, f"missing deal keys: {missing}"))
    return results


def _check_dry_parse(
    html_root: str,
    *,
    pipeline_settings: ParserPipelineSettings,
    parse_cache_enabled: bool,
) -> CheckResult:
    pipeline = ParserPipeline(
        html_root=html_root,
        dry_run=True,
        limit=1,
        parse_cache_enabled=parse_cache_enabled,
        settings=pipeline_settings,
    )
    files = pipeline.collect_html_files()
    if not files:
        return CheckResult("dry-parse", True, "no sample html found; skipped", level="warning")

    summary = pipeline.run()
    if summary.failed == 0 and summary.succeeded >= 1:
        return CheckResult("dry-parse", True, f"parsed sample successfully: {files[0]}")
    return CheckResult("dry-parse", False, "sample parse failed")


def _validate_mapping_writer_contract(
    *,
    schema_snapshot: Optional[Dict[str, Any]] = None,
) -> List[str]:
    errors: List[str] = []
    schema = schema_snapshot if schema_snapshot is not None else get_output_schema_snapshot()
    mapping_contract = get_output_mapping_contract()
    raw_fallback_contract = {
        kind: set(field_names)
        for kind, field_names in get_raw_fallback_contract().items()
    }
    compat_keys = set(LEGACY_PAYLOAD_KEYS)

    required_internal_keys = {KEY_STATUS, KEY_PROJECT_TYPE, KEY_IS_PRE_DISCLOSURE}
    missing_internal_keys = sorted(required_internal_keys - set(schema["internal_keys"]))
    if missing_internal_keys:
        errors.append(f"missing writer internal keys: {missing_internal_keys}")

    for kind, columns in schema["output_columns"].items():
        field_candidates = schema["field_candidates"].get(kind, {})
        raw_fallback_fields = raw_fallback_contract.get(kind, set())
        mapped_fields = set(mapping_contract.get(kind, {}))
        available_payload_fields = compat_keys | raw_fallback_fields | mapped_fields

        for column_name in columns:
            if column_name == "ID":
                continue
            candidate_fields = field_candidates.get(column_name, [])
            if not candidate_fields:
                errors.append(f"writer column has no candidates: kind={kind}, column={column_name}")
                continue
            if any(candidate in available_payload_fields for candidate in candidate_fields):
                continue
            errors.append(
                f"writer column is not satisfiable by compatibility payload: "
                f"kind={kind}, column={column_name}, candidates={candidate_fields}"
            )

    return errors


def _check_output_contracts(*, config_obj: object) -> List[CheckResult]:
    results: List[CheckResult] = []
    schema_settings = build_excel_schema_settings(config_obj)
    runtime = load_excel_output_runtime(schema_settings)
    schema_snapshot = get_output_schema_snapshot(runtime=runtime)

    mapping_errors = validate_output_field_map()
    if mapping_errors:
        results.append(
            CheckResult("output-mapping-contract", False, "; ".join(mapping_errors))
        )
    else:
        results.append(CheckResult("output-mapping-contract", True, "standard field map is consistent"))

    active_schema_errors = validate_excel_output_schema(runtime=runtime)
    schema_status = get_excel_schema_status(runtime=runtime)
    if active_schema_errors:
        results.append(
            CheckResult("excel-schema-active", False, "; ".join(active_schema_errors))
        )
    else:
        source = schema_status.get("source", "default")
        results.append(CheckResult("excel-schema-active", True, f"active schema ok (source={source})"))

    configured_schema_errors = validate_configured_excel_output_schema(
        schema_path=schema_settings.schema_path,
    )
    if configured_schema_errors:
        results.append(
            CheckResult("excel-schema-configured", False, "; ".join(configured_schema_errors))
        )
    else:
        results.append(CheckResult("excel-schema-configured", True, "configured schema file is valid"))

    cross_contract_errors = _validate_mapping_writer_contract(schema_snapshot=schema_snapshot)
    if cross_contract_errors:
        results.append(
            CheckResult("mapping-writer-contract", False, "; ".join(cross_contract_errors))
        )
    else:
        results.append(CheckResult("mapping-writer-contract", True, "mapping and writer schema are aligned"))

    return results


def _check_standard_mapping_layer() -> CheckResult:
    equity_sample_raw = {
        "项目编号": "G32026BJ1000001",
        "项目名称": "样例项目",
        "项目类型": "股权转让",
        "状态": "挂牌",
        "挂牌开始日期": "2026/02/24",
        "挂牌截止日期": "2026/03/24",
        "近一年净利润": "1.23",
        "总资产": "100.5",
    }
    public_resource_sample_raw = {
        "交易所": "北交互联",
        "项目编号": "GR20260001",
        "项目名称": "成交样例项目",
        "交易方式": "网络竞价",
        "受让方名称": "样例受让方",
        "转让标的评估值": "88.00",
        "成交金额": "108.00",
        "成交日期": "2026/03/01",
    }
    try:
        equity_parsed = build_parsed_project(
            file_path="self-check://equity",
            exchange="shenzhen",
            encoding="utf-8",
            data=equity_sample_raw,
        )
        equity_mapped = map_standard_to_excel_payload(equity_parsed, "挂牌_股权转让.xlsx")
        if equity_mapped.get("项目编号") != "G32026BJ1000001" or equity_mapped.get("状态") != "挂牌":
            return CheckResult("standard-mapping-layer", False, "equity output mapping returned invalid payload")

        public_resource_parsed = build_parsed_project(
            file_path="self-check://public-resource",
            exchange="public_resource",
            encoding="utf-8",
            data=public_resource_sample_raw,
        )
        public_resource_mapped = map_standard_to_excel_payload(
            public_resource_parsed,
            "公共资源网四大交易所股权转让成交信息统计.xlsx",
        )
        if public_resource_mapped.get("交易方式") != "网络竞价":
            return CheckResult("standard-mapping-layer", False, "public-resource trade method missing")
        if public_resource_mapped.get("受让方名称") != "样例受让方":
            return CheckResult("standard-mapping-layer", False, "public-resource buyer name missing")
        if public_resource_mapped.get("转让标的评估值") != "88.00":
            return CheckResult("standard-mapping-layer", False, "public-resource valuation missing")
        if public_resource_mapped.get("成交金额") != "108.00":
            return CheckResult("standard-mapping-layer", False, "public-resource deal amount missing")
        if public_resource_mapped.get("成交日期") != "2026/03/01":
            return CheckResult("standard-mapping-layer", False, "public-resource trade date missing")
        return CheckResult("standard-mapping-layer", True, "ok")
    except Exception as exc:
        return CheckResult("standard-mapping-layer", False, f"mapping layer failed: {exc}")


def run_self_check(
    html_root: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    config_obj: object | None = None,
    pipeline_settings: ParserPipelineSettings | None = None,
    parse_cache_enabled: bool = True,
) -> List[CheckResult]:
    _ = logger  # reserved for future extension
    resolved_config = config_obj or _load_default_checks_config()
    html_root = html_root or resolved_config.HTML_FOLDER
    resolved_pipeline_settings = pipeline_settings or build_parser_pipeline_settings(resolved_config)

    results: List[CheckResult] = []
    results.append(_check_python_version())
    results.append(_check_import("bs4"))
    results.append(_check_import("pandas"))
    results.append(_check_import("openpyxl"))
    results.append(_check_import("chardet", required=False))
    results.append(_check_html_root(html_root))
    results.extend(_check_output_config(resolved_config))
    results.extend(_check_output_contracts(config_obj=resolved_config))
    results.append(_check_standard_mapping_layer())
    results.append(
        _check_dry_parse(
            html_root,
            pipeline_settings=resolved_pipeline_settings,
            parse_cache_enabled=bool(parse_cache_enabled),
        )
    )
    return results
