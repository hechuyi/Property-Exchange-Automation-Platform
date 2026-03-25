"""Application-facing service layer for the desktop app."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict

from peap.output_contract import (
    KIND_CAPITAL,
    KIND_EQUITY,
    KIND_PHYSICAL,
    KIND_PRE,
    clone_field_candidates,
    get_output_columns_for_kind,
)
from peap.streaming_export import ordered_export_headers, record_to_export_payload, run_ready_export
from peap.streaming_ingest import StreamingIngestRunner, copy_snapshot_to_archive
from peap.streaming_models import ExportRequest, ItemProgressEvent, ItemSavedPayload
from peap.streaming_store import StreamingStore

from .runtime_dependencies import RuntimeDependencyManager


def _namespace(**kwargs):
    return argparse.Namespace(**kwargs)


def _timestamp_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


RECORD_STATE_LABELS = {
    "ready": "已录入",
    "pending_mapping": "待补映射",
    "skipped": "已跳过",
    "parse_failed": "解析失败",
    "postprocess_failed": "处理失败",
    "conflict": "归档重名",
}

JOB_TYPE_LABELS = {
    "one_click": "一键执行",
    "download_ingest": "历史区间任务",
    "export_excel": "导出 Excel",
    "manual_import": "手动导入解析",
    "mapping_refresh": "映射回刷",
}

JOB_PHASE_LABELS = {
    "prepare_tasks": "正在扫描网页",
    "save_pages": "正在保存网页",
    "manual_import_scan": "正在整理手动导入文件",
    "reprocessing": "正在重处理记录",
    "exporting": "正在导出 Excel",
}

PROJECT_TYPE_LABELS = {
    "equity_transfer": "股权转让",
    "physical_asset": "实物资产",
    "capital_increase": "增资扩股",
    "pre_disclosure": "预披露",
}

PROJECT_TYPE_TO_KIND = {
    "股权转让": KIND_EQUITY,
    "实物资产": KIND_PHYSICAL,
    "增资扩股": KIND_CAPITAL,
    "预披露": KIND_PRE,
}

EXCHANGE_LABELS = {
    "beijing": "北交所",
    "cbex": "北交所",
    "北京产权交易所": "北交所",
    "北交所": "北交所",
    "北交互联": "北交所",
    "shanghai": "上交所",
    "sse": "上交所",
    "上海联合产权交易所": "上交所",
    "上交所": "上交所",
    "tianjin": "天交所",
    "tpre": "天交所",
    "天津产权交易中心": "天交所",
    "天交所": "天交所",
    "chongqing": "重交所",
    "cquae": "重交所",
    "重庆联交所": "重交所",
    "重交所": "重交所",
}

MAPPING_MATCH_FIELDS = {
    "transferor": ("转让方", "融资方", "转让方名称", "融资方名称", "company_name_primary"),
    "group": ("隶属集团", "集团名称", "group_name"),
}


def _default_postprocess_config_path(config_obj: object) -> str:
    candidate_roots = [
        os.path.abspath(str(getattr(config_obj, "PROJECT_ROOT", "") or "")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    ]
    for root in candidate_roots:
        if not root:
            continue
        candidate = os.path.join(root, "peap_postprocess", "ppe_config", "postprocess_external_template.json")
        if os.path.isfile(candidate):
            return candidate
    return ""


def _status_label(state: str) -> str:
    return RECORD_STATE_LABELS.get(str(state or "").strip(), str(state or "").strip() or "未知")


def _job_type_label(job_type: str) -> str:
    return JOB_TYPE_LABELS.get(str(job_type or "").strip(), str(job_type or "").strip() or "任务")


def _normalize_record_states(raw_state: str) -> list[str] | None:
    state = str(raw_state or "all").strip().lower()
    if state in {"", "all"}:
        return None
    return [state]


def _coerce_limit(raw_value: Any, *, default: int = 50, maximum: int = 200) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(1, min(value, maximum))


def _coerce_int(raw_value: Any, *, default: int = 0) -> int:
    try:
        return int(raw_value)
    except Exception:
        return default


def _summary_count(summary: Dict[str, Any], key: str) -> int:
    return _coerce_int(summary.get(key), default=0)


def _normalize_exchange_label(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    lowered = text.lower()
    if lowered in EXCHANGE_LABELS:
        return EXCHANGE_LABELS[lowered]
    if text in EXCHANGE_LABELS:
        return EXCHANGE_LABELS[text]
    for key, label in EXCHANGE_LABELS.items():
        key_text = str(key)
        if key_text and key_text in lowered:
            return label
    return text


def _resolve_project_type_filter(raw_value: str) -> tuple[str | None, str | None]:
    value = str(raw_value or "all").strip()
    if value in {"", "all"}:
        return None, None
    if value in PROJECT_TYPE_LABELS:
        label = PROJECT_TYPE_LABELS[value]
        return label, PROJECT_TYPE_TO_KIND.get(label)
    return value, PROJECT_TYPE_TO_KIND.get(value)


def _first_value(payload: Dict[str, Any], fields: list[str]) -> str:
    for field in fields:
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    return ""


def _normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _path_within_root(path_value: str, root_value: str) -> bool:
    target = os.path.abspath(str(path_value or ""))
    root = os.path.abspath(str(root_value or ""))
    if not target or not root:
        return False
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        return False


def _record_matches_mapping_source(record: Dict[str, Any], *, match_field: str, source_name: str) -> bool:
    normalized_source = _normalize_match_text(source_name)
    if not normalized_source:
        return False
    fields = MAPPING_MATCH_FIELDS.get(str(match_field or "").strip().lower(), MAPPING_MATCH_FIELDS["transferor"])
    payloads = [
        dict(record.get("postprocess_payload") or {}),
        dict(record.get("parser_payload") or {}),
    ]
    for payload in payloads:
        for field_name in fields:
            if _normalize_match_text(payload.get(field_name)) == normalized_source:
                return True
    return False


def _normalize_mapping_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    source_name = str(payload.get("source_name") or payload.get("company_name") or "").strip()
    match_field = str(payload.get("match_field") or "transferor").strip() or "transferor"
    target_value = str(payload.get("target_value") or "").strip()
    target_field = str(payload.get("target_field") or "").strip() or (
        "group_name" if str(payload.get("group_name") or target_value).strip() and not str(payload.get("source_type") or "").strip() else "source_type"
    )
    group_name = str(payload.get("group_name") or (target_value if target_field == "group_name" else "")).strip()
    source_type = str(payload.get("source_type") or (target_value if target_field == "source_type" else "")).strip()
    return {
        "source_name": source_name,
        "match_field": match_field,
        "target_field": target_field,
        "target_value": target_value,
        "group_name": group_name,
        "source_type": source_type,
    }


def _validate_mapping_payload(normalized: Dict[str, str]) -> None:
    source_name = str(normalized.get("source_name") or "").strip()
    target_value = str(normalized.get("target_value") or "").strip()
    match_field = str(normalized.get("match_field") or "").strip()
    target_field = str(normalized.get("target_field") or "").strip()
    if not source_name:
        raise ValueError("source_name is required")
    if not target_value:
        raise ValueError("target_value is required")
    if match_field not in MAPPING_MATCH_FIELDS:
        raise ValueError(f"invalid match_field: {match_field}")
    if target_field not in {"group_name", "source_type"}:
        raise ValueError(f"invalid target_field: {target_field}")


def _discover_import_files(input_dir: str) -> list[str]:
    root = os.path.abspath(str(input_dir or "").strip())
    if not root or not os.path.isdir(root):
        return []
    matches: list[str] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names.sort()
        for file_name in sorted(file_names):
            lowered = file_name.lower()
            if lowered.endswith((".html", ".htm", ".mhtml")):
                matches.append(os.path.join(current_root, file_name))
    return matches


def _build_record_display_values(record: Dict[str, Any], *, project_kind: str | None) -> Dict[str, Any]:
    payload = record_to_export_payload(record)
    payload["交易所"] = _normalize_exchange_label(str(payload.get("交易所") or record.get("exchange") or ""))

    if not project_kind:
        return payload

    field_candidates = clone_field_candidates().get(project_kind, {})
    columns = [column for column in get_output_columns_for_kind(project_kind) if column != "ID"]
    values: Dict[str, Any] = {}
    for column in columns:
        candidates = list(field_candidates.get(column) or [column])
        values[column] = _first_value(payload, candidates)
    return values


def _record_status_detail(record: Dict[str, Any]) -> str:
    state = str(record.get("state") or "").strip()
    findings = list(record.get("findings") or [])
    archive_path = str(record.get("archive_path") or "").strip()
    archive_conflict_path = ""
    for finding in findings:
        if str(finding.get("type") or "").strip() == "archive_conflict":
            archive_conflict_path = str((finding.get("evidence") or {}).get("archive_path") or archive_path).strip()
            break
    if state == "conflict":
        if archive_conflict_path:
            return f"归档文件同名，已另存为 {os.path.basename(archive_conflict_path)}"
        if archive_path:
            return f"归档文件同名，当前文件为 {os.path.basename(archive_path)}"
        return "归档文件同名"
    if state == "pending_mapping":
        messages = [str(item.get("message") or "").strip() for item in findings if str(item.get("message") or "").strip()]
        if messages:
            return messages[0]
        return "缺少映射规则，暂不能进入导出"
    if state in {"parse_failed", "postprocess_failed"}:
        return str(record.get("last_error_message") or "").strip()
    if state == "skipped":
        raw_message = str(record.get("last_error_message") or "").strip()
        return raw_message or "当前网页按规则跳过，不进入录入"
    if archive_conflict_path:
        return f"归档文件曾同名，当前文件为 {os.path.basename(archive_conflict_path)}"
    return ""


class AppService:
    """Thin orchestration layer consumed by the local desktop API."""

    def __init__(self, *, config_obj: object, runtime_dependencies: RuntimeDependencyManager | None = None) -> None:
        self.config = config_obj
        self.app_home = str(getattr(config_obj, "APP_HOME", getattr(config_obj, "DATA_ROOT", "")))
        self.db_path = os.path.abspath(
            str(
                getattr(config_obj, "STREAMING_DB_PATH", "")
                or os.path.join(str(config_obj.LOG_DIR), "streaming_ingest.sqlite3")
            )
        )
        self.default_archive_root = os.path.abspath(
            str(getattr(config_obj, "ARCHIVE_ROOT", "") or os.path.join(str(config_obj.DATA_ROOT), "outputs", "submission"))
        )
        self.default_export_root = os.path.abspath(str(getattr(config_obj, "OUTPUT_EXCEL_DIR", "")))
        self.default_postprocess_config = _default_postprocess_config_path(config_obj)
        self.store = StreamingStore(self.db_path)
        self.runtime_dependencies = runtime_dependencies or RuntimeDependencyManager(
            browser_cache_dir=str(getattr(config_obj, "PLAYWRIGHT_BROWSERS_PATH", "")),
        )
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._thread_state = threading.local()
        self._active_mutating_jobs: set[str] = set()
        self._runtime_install_thread: threading.Thread | None = None
        self._runtime_install_state: dict[str, Any] = self._build_runtime_install_state()
        self._archive_repair_attempted = False
        interrupted_jobs = self.store.interrupt_running_jobs(reason="desktop backend restarted before task completed")
        if interrupted_jobs:
            self.store.add_audit_entry(
                "running_jobs_interrupted_on_startup",
                {"job_ids": interrupted_jobs, "count": len(interrupted_jobs)},
            )

    def _normalize_legacy_views(self) -> None:
        summary = self.store.normalize_legacy_skip_parse_entries()
        if any(int(summary.get(key, 0)) > 0 for key in ("records", "revisions", "events")):
            self.store.add_audit_entry("legacy_skip_parse_normalized", summary)
        normalized_dates = self.store.normalize_listing_dates()
        if normalized_dates > 0:
            self.store.add_audit_entry("legacy_listing_dates_normalized", {"records": normalized_dates})
        normalized_states = self.store.normalize_required_mapping_states()
        if any(int(normalized_states.get(key, 0)) > 0 for key in normalized_states):
            self.store.add_audit_entry("legacy_required_mapping_normalized", normalized_states)

    def _repair_missing_archives_once(self) -> None:
        with self._lock:
            if self._archive_repair_attempted:
                return
            self._archive_repair_attempted = True

        archive_root = self.get_basic_settings()["archive_root"]
        managed_raw_root = os.path.join(str(self.config.DATA_ROOT), "raw")
        repaired = 0
        failed = 0
        collapsed = 0
        removed_raw = 0
        rewired_download_events = 0
        for record in self.store.iter_latest_records(sort="recent"):
            archive_path = str(record.get("archive_path") or "").strip()
            source_file = str(record.get("source_file") or "").strip()
            archive_exists = bool(archive_path) and os.path.isfile(archive_path)
            source_exists = bool(source_file) and os.path.isfile(source_file)
            if archive_exists:
                if archive_path != source_file:
                    self.store.update_record_source_file(str(record["record_id"]), archive_path)
                    rewired_download_events += self.store.update_downloaded_event_source_file(source_file, archive_path)
                    collapsed += 1
                    if source_exists and _path_within_root(source_file, managed_raw_root):
                        try:
                            os.remove(source_file)
                            assets_dir = f"{os.path.splitext(source_file)[0]}_files"
                            if os.path.isdir(assets_dir):
                                import shutil

                                shutil.rmtree(assets_dir, ignore_errors=True)
                            removed_raw += 1
                        except OSError:
                            pass
                continue
            if not source_exists:
                if archive_path:
                    failed += 1
                continue
            if _path_within_root(source_file, archive_root):
                self.store.update_record_archive_path(str(record["record_id"]), source_file)
                repaired += 1
                continue
            try:
                repaired_path, _ = copy_snapshot_to_archive(
                    source_file=source_file,
                    archive_root=archive_root,
                    project_code=str(record.get("project_code") or "unknown"),
                    project_name=str(record.get("project_name") or ""),
                    listing_date=str(record.get("listing_date") or ""),
                )
                self.store.update_record_archive_path(str(record["record_id"]), repaired_path)
                self.store.update_record_source_file(str(record["record_id"]), repaired_path)
                rewired_download_events += self.store.update_downloaded_event_source_file(source_file, repaired_path)
                repaired += 1
                if _path_within_root(source_file, managed_raw_root):
                    try:
                        os.remove(source_file)
                        assets_dir = f"{os.path.splitext(source_file)[0]}_files"
                        if os.path.isdir(assets_dir):
                            import shutil

                            shutil.rmtree(assets_dir, ignore_errors=True)
                        removed_raw += 1
                    except OSError:
                        pass
            except Exception:
                failed += 1
        if repaired or failed or collapsed or removed_raw or rewired_download_events:
            self.store.add_audit_entry(
                "missing_archive_repair",
                {
                    "archive_root": archive_root,
                    "repaired": repaired,
                    "failed": failed,
                    "collapsed": collapsed,
                    "removed_raw": removed_raw,
                    "rewired_download_events": rewired_download_events,
                },
            )

    def _basic_settings_key(self) -> str:
        return "app.settings.basic"

    def _advanced_settings_key(self) -> str:
        return "app.settings.advanced"

    def _effective_postprocess_config_path(self) -> str:
        advanced = self.get_advanced_settings()
        return str(advanced.get("postprocess_config") or self.default_postprocess_config or "").strip()

    def _load_effective_rules_config(self) -> Dict[str, Any]:
        from peap.streaming_daily_pipeline import _load_rules_config

        return _load_rules_config(self._effective_postprocess_config_path())

    def _build_ingest_runner(self, *, archive_root: str | None = None) -> StreamingIngestRunner:
        resolved_archive_root = archive_root or self.get_basic_settings()["archive_root"]
        return StreamingIngestRunner(
            store=self.store,
            archive_root=resolved_archive_root,
            rules_config=self._load_effective_rules_config(),
        )

    def _build_runtime_install_state(self, **overrides: Any) -> Dict[str, Any]:
        state = {
            "status": "idle",
            "browser_name": "chromium",
            "trigger": "",
            "attempt_count": 0,
            "started_at": "",
            "updated_at": "",
            "completed_at": "",
            "message": "",
            "last_result": {},
            "running": False,
        }
        state.update(overrides)
        return state

    def _get_runtime_install_state(self) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._runtime_install_state)
        last_result = state.get("last_result")
        if isinstance(last_result, dict):
            state["last_result"] = dict(last_result)
        return state

    def _build_product_readiness(self, *, browser_runtime: Dict[str, Any] | None = None) -> Dict[str, Any]:
        browser = dict(browser_runtime or self.runtime_dependencies.get_browser_runtime_status())
        browser_installed = bool(browser.get("installed"))
        browser_error = str(browser.get("error") or "").strip()
        issues: list[Dict[str, Any]] = []
        if not browser_installed:
            issues.append(
                {
                    "code": "browser_runtime_missing",
                    "severity": "error" if browser_error else "warning",
                    "message": browser_error or "Chromium runtime is not installed",
                }
            )
        return {
            "ready": browser_installed,
            "download_ready": browser_installed,
            "browser_runtime_ready": browser_installed,
            "issues": issues,
        }

    def get_basic_settings(self) -> Dict[str, Any]:
        defaults = {
            "default_exchange": "all",
            "default_project_type": "all",
            "default_concurrency": int(self.config.DOWNLOADER_DEFAULTS["concurrency"]),
            "archive_root": self.default_archive_root,
            "export_root": self.default_export_root,
            "workspace_root": self.app_home,
        }
        value = self.store.get_setting(self._basic_settings_key(), default=defaults)
        merged = dict(defaults)
        merged.update(value)
        merged["archive_root"] = self.default_archive_root
        merged["export_root"] = self.default_export_root
        merged["workspace_root"] = self.app_home
        return merged

    def get_advanced_settings(self) -> Dict[str, Any]:
        defaults = {
            "app_home": self.app_home,
            "streaming_db": self.db_path,
            "save_json": False,
            "postprocess_config": self.default_postprocess_config,
            "log_dir": str(self.config.LOG_DIR),
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": self.default_archive_root,
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "archive_root": self.default_archive_root,
            "export_root": self.default_export_root,
        }
        value = self.store.get_setting(self._advanced_settings_key(), default=defaults)
        merged = dict(defaults)
        merged.update(value)
        merged["app_home"] = self.app_home
        merged["streaming_db"] = self.db_path
        if not str(merged.get("postprocess_config") or "").strip():
            merged["postprocess_config"] = self.default_postprocess_config
        merged["log_dir"] = str(self.config.LOG_DIR)
        merged["cache_dir"] = str(getattr(self.config, "CACHE_DIR", ""))
        merged["raw_auto_root"] = self.default_archive_root
        merged["raw_manual_root"] = str(getattr(self.config, "HTML_FOLDER", ""))
        merged["browser_cache_dir"] = str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))
        merged["archive_root"] = self.default_archive_root
        merged["export_root"] = self.default_export_root
        return merged

    def set_basic_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        value = self.get_basic_settings()
        for key in ("default_exchange", "default_project_type", "default_concurrency"):
            if key in dict(payload or {}):
                value[key] = dict(payload or {})[key]
        value["archive_root"] = self.default_archive_root
        value["export_root"] = self.default_export_root
        value["workspace_root"] = self.app_home
        self.store.set_setting(self._basic_settings_key(), value)
        self.store.add_audit_entry("settings_basic_updated", value)
        return value

    def set_advanced_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        value = self.get_advanced_settings()
        allowed = {"save_json", "postprocess_config"}
        for key, raw_value in dict(payload or {}).items():
            if key in allowed:
                value[key] = raw_value
        value["app_home"] = self.app_home
        value["streaming_db"] = self.db_path
        if not str(value.get("postprocess_config") or "").strip():
            value["postprocess_config"] = self.default_postprocess_config
        value["log_dir"] = str(self.config.LOG_DIR)
        value["cache_dir"] = str(getattr(self.config, "CACHE_DIR", ""))
        value["raw_auto_root"] = self.default_archive_root
        value["raw_manual_root"] = str(getattr(self.config, "HTML_FOLDER", ""))
        value["browser_cache_dir"] = str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))
        value["archive_root"] = self.default_archive_root
        value["export_root"] = self.default_export_root
        self.store.set_setting(self._advanced_settings_key(), value)
        self.store.add_audit_entry("settings_advanced_updated", value)
        return value

    def health(self) -> Dict[str, Any]:
        browser_runtime = self.runtime_dependencies.get_browser_runtime_status()
        product_readiness = self._build_product_readiness(browser_runtime=browser_runtime)
        browser_install = self._get_runtime_install_state()
        return {
            "ok": True,
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "archive_root": self.get_basic_settings()["archive_root"],
            "export_root": self.get_basic_settings()["export_root"],
            "app_home": self.app_home,
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": self.get_basic_settings()["archive_root"],
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "log_dir": str(self.config.LOG_DIR),
            "browser_runtime": browser_runtime,
            "browser_install": browser_install,
            "product_readiness": product_readiness,
        }

    def readiness(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "app_home": self.app_home,
        }

    def overview(self) -> Dict[str, Any]:
        self._normalize_legacy_views()
        self._repair_missing_archives_once()
        basic = self.get_basic_settings()
        jobs = self.store.list_jobs(limit=5)
        latest = jobs[0] if jobs else None
        browser_runtime = self.runtime_dependencies.get_browser_runtime_status()
        product_readiness = self._build_product_readiness(browser_runtime=browser_runtime)
        browser_install = self._get_runtime_install_state()
        record_state_counts = self.store.count_records_by_state()
        return {
            "archive_root": basic["archive_root"],
            "export_root": basic["export_root"],
            "db_path": self.db_path,
            "workspace_root": self.app_home,
            "app_home": self.app_home,
            "cache_dir": str(getattr(self.config, "CACHE_DIR", "")),
            "raw_auto_root": basic["archive_root"],
            "raw_manual_root": str(getattr(self.config, "HTML_FOLDER", "")),
            "browser_cache_dir": str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", "")),
            "browser_runtime": browser_runtime,
            "browser_install": browser_install,
            "product_readiness": product_readiness,
            "pending_mapping_count": self.store.count_pending_mappings(),
            "record_state_counts": record_state_counts,
            "latest_job": latest,
            "latest_progress": self._build_latest_progress(latest),
            "recent_jobs": jobs,
        }

    def get_runtime_dependencies(self) -> Dict[str, Any]:
        browser_runtime = self.runtime_dependencies.get_browser_runtime_status()
        return {
            "browser": browser_runtime,
            "browser_install": self._get_runtime_install_state(),
            "product_readiness": self._build_product_readiness(browser_runtime=browser_runtime),
        }

    def _build_latest_progress(self, latest_job: Dict[str, Any] | None) -> Dict[str, Any]:
        if not latest_job:
            return {
                "phase_code": "",
                "phase_label": "暂无任务",
                "job_status": "",
                "downloaded_count": 0,
                "persisted_count": 0,
                "exception_count": 0,
                "pending_mapping_count": 0,
                "skipped_count": 0,
                "archive_pending_count": 0,
                "archive_completed_count": 0,
                "current_task_label": "",
                "task_index": 0,
                "task_total": 0,
                "phase_percent": 0,
                "latest_stage_code": "",
                "latest_stage_label": "",
                "latest_stage_summary": {},
            }

        job_id = str(latest_job.get("job_id") or "")
        status_counts = self.store.get_job_event_counts(job_id) if job_id else {}
        recent_events = self.store.list_job_events(job_id, limit=40) if job_id else []
        latest_phase_event = next(
            (event for event in recent_events if str(event.get("stage") or "") in JOB_PHASE_LABELS),
            None,
        )
        latest_stage_event = next(
            (
                event
                for event in recent_events
                if str(event.get("stage") or "") in {"prepare_tasks", "save_pages"}
            ),
            None,
        )
        latest_phase_payload = dict(latest_phase_event.get("payload") or {}) if latest_phase_event is not None else {}
        latest_stage_payload = dict(latest_stage_event.get("payload") or {}) if latest_stage_event is not None else {}
        latest_stage_summary = dict(latest_stage_payload.get("summary") or {})

        phase_code = ""
        phase_label = "任务进行中"
        job_status = str(latest_job.get("status") or "")
        downloaded_count = int(latest_job.get("downloaded_count") or 0)
        persisted_count = int(latest_job.get("persisted_count") or 0)
        exception_count = int(latest_job.get("exception_count") or 0)
        pending_mapping_count = int(status_counts.get("pending_mapping", 0))
        skipped_count = int(status_counts.get("skipped", 0))
        archive_pending_count = max(downloaded_count - persisted_count - skipped_count - exception_count, 0)
        current_task_label = str(latest_phase_payload.get("task_label") or "").strip()
        task_index = _coerce_int(latest_phase_payload.get("task_index"), default=0)
        task_total = _coerce_int(latest_phase_payload.get("task_total"), default=0)
        phase_percent = _coerce_int(latest_phase_payload.get("phase_percent"), default=0)
        detail_candidates = _summary_count(latest_stage_summary, "detail_candidates")
        detail_date_skipped = _summary_count(latest_stage_summary, "detail_date_skipped")
        detail_fetched = _summary_count(latest_stage_summary, "detail_fetched")

        if job_status == "success":
            phase_code = "completed"
            phase_label = "已完成"
            phase_percent = 100
        elif job_status == "success_with_warnings":
            phase_code = "completed_with_warnings"
            phase_label = "已完成，但有待处理项"
            phase_percent = 100
        elif job_status == "interrupted":
            phase_code = "interrupted"
            phase_label = "已中断"
            phase_percent = 100
        elif job_status == "failed":
            phase_code = "failed"
            phase_label = "执行失败"
            phase_percent = 100
        elif job_status == "running":
            if archive_pending_count > 0:
                phase_code = "archive_pending"
                phase_label = "正在存档"
                current_task_label = ""
                task_index = 0
                task_total = max(downloaded_count, 0)
                if downloaded_count > 0:
                    archived_ratio = (persisted_count + skipped_count + exception_count) / max(downloaded_count, 1)
                    phase_percent = min(98, 70 + int(archived_ratio * 25))
                else:
                    phase_percent = max(phase_percent, 70)
            elif latest_phase_event is not None and str(latest_phase_event.get("status") or "") == "running":
                phase_code = str(latest_phase_event.get("stage") or "")
                phase_label = str(latest_phase_payload.get("label") or JOB_PHASE_LABELS.get(phase_code) or phase_label)
            elif latest_phase_event is not None:
                phase_code = str(latest_phase_event.get("stage") or "")
                phase_label = str(latest_phase_payload.get("label") or JOB_PHASE_LABELS.get(phase_code) or phase_label)
            elif downloaded_count <= 0:
                phase_code = "prepare_tasks"
                phase_label = JOB_PHASE_LABELS["prepare_tasks"]
                phase_percent = max(phase_percent, 5)
        else:
            if downloaded_count <= 0:
                phase_code = "prepare_tasks"
                phase_label = JOB_PHASE_LABELS["prepare_tasks"]
                phase_percent = max(phase_percent, 5)

        if phase_code == "prepare_tasks" and phase_percent <= 0:
            phase_percent = 12
        elif phase_code == "save_pages" and phase_percent <= 0:
            phase_percent = 48
        elif phase_code == "exporting" and phase_percent <= 0:
            phase_percent = 92

        if (
            phase_code in {"completed", "completed_with_warnings"}
            and latest_stage_event is not None
            and latest_stage_summary
            and downloaded_count <= 0
            and persisted_count <= 0
        ):
            if detail_candidates > 0 and detail_date_skipped >= detail_candidates and detail_fetched <= 0:
                phase_label = "本次没有符合日期条件的网页"
            elif _summary_count(latest_stage_summary, "duplicate_skipped") > 0 and detail_fetched <= 0:
                phase_label = "所选日期网页已存在，无需重复下载"
            elif detail_candidates <= 0 and detail_fetched <= 0:
                phase_label = "本次未发现新网页"
            else:
                phase_label = "本次未产生可录入记录"

        return {
            "phase_code": phase_code,
            "phase_label": phase_label,
            "job_status": job_status,
            "downloaded_count": downloaded_count,
            "persisted_count": persisted_count,
            "exception_count": exception_count,
            "pending_mapping_count": pending_mapping_count,
            "skipped_count": skipped_count,
            "archive_pending_count": archive_pending_count,
            "archive_completed_count": persisted_count,
            "current_task_label": current_task_label,
            "task_index": task_index,
            "task_total": task_total,
            "phase_percent": phase_percent,
            "job_id": job_id,
            "latest_stage_code": str(latest_stage_event.get("stage") or "") if latest_stage_event is not None else "",
            "latest_stage_label": str(latest_stage_payload.get("label") or "") if latest_stage_event is not None else "",
            "latest_stage_summary": latest_stage_summary,
        }

    def list_records(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._normalize_legacy_views()
        self._repair_missing_archives_once()
        payload = dict(payload or {})
        states = _normalize_record_states(str(payload.get("state") or "all"))
        page = _coerce_limit(payload.get("page"), default=1, maximum=9999)
        page_size = _coerce_limit(payload.get("page_size") or payload.get("limit"), default=50, maximum=200)
        keyword = str(payload.get("keyword") or "").strip().lower()
        date_from = str(payload.get("date_from") or "").strip()
        date_to = str(payload.get("date_to") or "").strip()
        project_type_label, project_kind = _resolve_project_type_filter(str(payload.get("project_type") or "all"))
        records = self.store.iter_latest_records(
            states=states,
            date_from=date_from or None,
            date_to=date_to or None,
            sort="recent",
        )
        filtered_rows: list[Dict[str, Any]] = []
        export_rows: list[Dict[str, Any]] = []
        for record in records:
            record_project_type = str(record.get("project_type") or "").strip()
            if project_type_label and record_project_type and record_project_type != project_type_label:
                continue
            values = _build_record_display_values(record, project_kind=project_kind)
            search_blob = " ".join(
                [
                    str(record.get("project_code") or ""),
                    str(record.get("project_name") or ""),
                    str(record.get("project_type") or ""),
                    str(record.get("exchange") or ""),
                    str(record.get("listing_date") or ""),
                    str(record.get("state") or ""),
                ]
                + [str(values.get(column) or "") for column in values]
            ).lower()
            if keyword and keyword not in search_blob:
                continue
            export_rows.append(values)
            filtered_rows.append(
                {
                    "record_id": record["record_id"],
                    "project_code": record["project_code"],
                    "project_name": record["project_name"],
                    "project_type": record["project_type"],
                    "exchange": _normalize_exchange_label(str(record.get("exchange") or "")),
                    "listing_date": str(record.get("listing_date") or ""),
                    "state": record["state"],
                    "status_label": _status_label(str(record["state"])),
                    "status_detail": _record_status_detail(record),
                    "archive_path": record["archive_path"],
                    "source_file": record["source_file"],
                    "updated_at": record.get("updated_at", ""),
                    "values": values,
                }
            )
        total_count = len(filtered_rows)
        offset = max(0, (page - 1) * page_size)
        rows = filtered_rows[offset : offset + page_size]
        page_count = (total_count + page_size - 1) // page_size if total_count else 0
        return {
            "db_path": self.db_path,
            "columns": (
                [column for column in get_output_columns_for_kind(project_kind) if column != "ID"]
                if project_kind
                else ordered_export_headers(export_rows)
            ),
            "project_type": project_type_label or "all",
            "keyword": keyword,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "page_count": page_count,
            "has_more": page < page_count,
            "summary": {
                "visible_count": len(rows),
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "page_count": page_count,
                "state_counts": {
                    state: sum(1 for row in rows if str(row.get("state") or "") == state)
                    for state in sorted({str(row.get("state") or "") for row in rows})
                },
            },
            "rows": rows,
        }

    def launch_browser_runtime_install(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        browser_name = str(payload.get("browser_name") or "chromium").strip() or "chromium"
        trigger = str(payload.get("trigger") or "manual").strip() or "manual"
        browser_runtime = self.runtime_dependencies.get_browser_runtime_status(browser_name=browser_name)
        if bool(browser_runtime.get("installed")):
            ready_state = self._build_runtime_install_state(
                status="succeeded",
                browser_name=browser_name,
                trigger=trigger,
                updated_at=_timestamp_now(),
                completed_at=_timestamp_now(),
                message="Chromium already installed",
                last_result=browser_runtime,
                running=False,
            )
            with self._lock:
                ready_state["attempt_count"] = int(self._runtime_install_state.get("attempt_count", 0))
                self._runtime_install_state = ready_state
            return self._get_runtime_install_state()

        with self._lock:
            if self._runtime_install_state.get("status") == "running":
                state = dict(self._runtime_install_state)
                last_result = state.get("last_result")
                if isinstance(last_result, dict):
                    state["last_result"] = dict(last_result)
                return state
            attempt_count = int(self._runtime_install_state.get("attempt_count", 0)) + 1
            self._runtime_install_state = self._build_runtime_install_state(
                status="running",
                browser_name=browser_name,
                trigger=trigger,
                attempt_count=attempt_count,
                started_at=_timestamp_now(),
                updated_at=_timestamp_now(),
                message=f"Installing {browser_name}",
                last_result={},
                running=True,
            )

        def _run_install() -> None:
            try:
                result = self.runtime_dependencies.install_browser_runtime(browser_name=browser_name)
                installed = bool(result.get("installed"))
                status = "succeeded" if installed else "failed"
                message = "Chromium install completed" if installed else str(result.get("error") or "Chromium install failed")
                audit_payload = {
                    "browser_name": browser_name,
                    "trigger": trigger,
                    "installed": installed,
                    "returncode": result.get("returncode"),
                    "error": result.get("error", ""),
                }
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                message = str(exc)
                result = {
                    "browser_name": browser_name,
                    "installed": False,
                    "error": str(exc),
                }
                audit_payload = {
                    "browser_name": browser_name,
                    "trigger": trigger,
                    "installed": False,
                    "returncode": None,
                    "error": str(exc),
                }

            with self._lock:
                attempt_count = int(self._runtime_install_state.get("attempt_count", 0))
                self._runtime_install_state = self._build_runtime_install_state(
                    status=status,
                    browser_name=browser_name,
                    trigger=trigger,
                    attempt_count=attempt_count,
                    started_at=str(self._runtime_install_state.get("started_at") or ""),
                    updated_at=_timestamp_now(),
                    completed_at=_timestamp_now(),
                    message=message,
                    last_result=result,
                    running=False,
                )
                self._runtime_install_thread = None
            self.store.add_audit_entry("browser_runtime_install_async", audit_payload)

        thread = threading.Thread(
            target=_run_install,
            name=f"peap-browser-install-{int(time.time())}",
            daemon=True,
        )
        with self._lock:
            self._runtime_install_thread = thread
        thread.start()
        return self._get_runtime_install_state()

    def install_browser_runtime(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        browser_name = str(payload.get("browser_name") or "chromium").strip() or "chromium"
        result = self.runtime_dependencies.install_browser_runtime(browser_name=browser_name)
        self.store.add_audit_entry(
            "browser_runtime_install",
            {
                "browser_name": browser_name,
                "installed": result.get("installed", False),
                "returncode": result.get("returncode"),
                "error": result.get("error", ""),
                "browser_cache_dir": result.get("browser_cache_dir", ""),
            },
        )
        enriched = dict(result)
        enriched["product_readiness"] = self._build_product_readiness(browser_runtime=result)
        with self._lock:
            attempt_count = int(self._runtime_install_state.get("attempt_count", 0))
            self._runtime_install_state = self._build_runtime_install_state(
                status="succeeded" if enriched.get("installed") else "failed",
                browser_name=browser_name,
                trigger="sync",
                attempt_count=attempt_count,
                updated_at=_timestamp_now(),
                completed_at=_timestamp_now(),
                message="Chromium install completed" if enriched.get("installed") else str(enriched.get("error") or "Chromium install failed"),
                last_result=enriched,
                running=False,
            )
        return enriched

    def list_jobs(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        self._normalize_legacy_views()
        return self.store.list_jobs(limit=limit)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        self._normalize_legacy_views()
        data = self.store.get_job(job_id)
        data["events"] = self.store.list_job_events(job_id, limit=100)
        return data

    def get_job_events(self, job_id: str, *, limit: int = 200) -> list[Dict[str, Any]]:
        self._normalize_legacy_views()
        return self.store.list_job_events(job_id, limit=limit)

    def list_pending_mappings(self) -> list[Dict[str, Any]]:
        self._normalize_legacy_views()
        return self.store.list_pending_mappings(limit=200)

    def list_mapping_entries(self) -> list[Dict[str, Any]]:
        self._normalize_legacy_views()
        return self.store.list_mapping_entries()

    def _start_background_thread(self, *, name: str, target) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        with self._lock:
            self._threads[thread.name] = thread

    def _reserve_mutating_job(self, job_type: str) -> None:
        normalized = str(job_type or "").strip() or "task"
        with self._lock:
            if self._active_mutating_jobs:
                active_job_type = sorted(self._active_mutating_jobs)[0]
                raise RuntimeError(f"已有执行中的任务：{_job_type_label(active_job_type)}")
            self._active_mutating_jobs.add(normalized)

    def _release_mutating_job(self, job_type: str) -> None:
        normalized = str(job_type or "").strip() or "task"
        with self._lock:
            self._active_mutating_jobs.discard(normalized)

    @contextmanager
    def _mutating_job_scope(self, job_type: str):
        self._reserve_mutating_job(job_type)
        try:
            yield
        finally:
            self._release_mutating_job(job_type)

    def _thread_job_stack(self) -> list[str]:
        stack = getattr(self._thread_state, "mutating_jobs", None)
        if stack is None:
            stack = []
            self._thread_state.mutating_jobs = stack
        return stack

    @contextmanager
    def _thread_job_scope(self, job_type: str):
        normalized = str(job_type or "").strip() or "task"
        stack = self._thread_job_stack()
        stack.append(normalized)
        try:
            yield
        finally:
            stack.pop()

    def _current_thread_holds_mutating_job(self, job_type: str) -> bool:
        normalized = str(job_type or "").strip() or "task"
        return normalized in self._thread_job_stack()

    def _find_records_for_mapping_refresh(self, *, match_field: str, source_name: str) -> list[Dict[str, Any]]:
        records = self.store.iter_latest_records(states=["ready", "pending_mapping"], limit=5000, sort="recent")
        return [
            record
            for record in records
            if _record_matches_mapping_source(record, match_field=match_field, source_name=source_name)
        ]

    def _find_pending_mapping_records(self) -> list[Dict[str, Any]]:
        return self.store.iter_latest_records(states=["pending_mapping"], limit=5000, sort="recent")

    def _find_existing_mapping_entry(self, *, source_name: str, match_field: str, target_field: str) -> Dict[str, Any] | None:
        normalized_source = _normalize_match_text(source_name)
        if not normalized_source:
            return None
        for entry in self.store.list_mapping_entries():
            metadata = dict(entry.get("metadata") or {})
            if _normalize_match_text(entry.get("company_name")) != normalized_source:
                continue
            if str(metadata.get("match_field") or "transferor").strip() != str(match_field or "").strip():
                continue
            if str(metadata.get("target_field") or "").strip() != str(target_field or "").strip():
                continue
            return dict(entry)
        return None

    def preview_mapping_upsert(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_mapping_payload(payload)
        _validate_mapping_payload(normalized)
        source_name = normalized["source_name"]
        match_field = normalized["match_field"]
        target_field = normalized["target_field"]
        group_name = normalized["group_name"]
        source_type = normalized["source_type"]
        target_value = group_name if target_field == "group_name" else source_type
        existing_entry = self._find_existing_mapping_entry(
            source_name=source_name,
            match_field=match_field,
            target_field=target_field,
        )
        affected_records = self._find_records_for_mapping_refresh(match_field=match_field, source_name=source_name)
        affected_pending_count = sum(1 for item in affected_records if str(item.get("state") or "") == "pending_mapping")

        mode = "create"
        conflict = False
        if existing_entry is not None:
            existing_target = (
                str(existing_entry.get("group_name") or "").strip()
                if target_field == "group_name"
                else str(existing_entry.get("source_type") or "").strip()
            )
            mode = "update" if existing_target == target_value else "overwrite"
            conflict = mode == "overwrite"

        return {
            "conflict": conflict,
            "mode": mode,
            "existing_entry": existing_entry or {},
            "affected_count": len(affected_records),
            "affected_pending_count": affected_pending_count,
            "match_field": match_field,
            "target_field": target_field,
            "target_value": target_value,
            "source_name": source_name,
        }

    def _run_mapping_refresh_job(self, *, job_id: str, record_ids: list[str]) -> None:
        refreshed = 0
        pending = 0
        skipped = 0
        failed = 0
        for index, record_id in enumerate(record_ids, start=1):
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status="running",
                    project_code=record_id,
                    payload={
                        "label": "正在重处理记录",
                        "task_index": index,
                        "task_total": len(record_ids),
                        "task_label": record_id,
                        "phase_percent": int(index * 100 / max(len(record_ids), 1)),
                    },
                )
            )
            try:
                result = self.reprocess_record(record_id)
                state = str(result.get("state") or "")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.store.update_job_counts(job_id, downloaded_inc=1, exception_inc=1)
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="reprocessing",
                        status="failed",
                        project_code=record_id,
                        error_type="mapping_refresh_failed",
                        error_message=str(exc),
                        payload={"label": "重处理失败", "record_id": record_id},
                    )
                )
                continue

            refreshed += 1
            if state == "pending_mapping":
                pending += 1
            elif state == "skipped":
                skipped += 1
            elif state not in {"ready", "conflict"}:
                failed += 1
            self.store.update_job_counts(
                job_id,
                downloaded_inc=1,
                persisted_inc=1 if state in {"ready", "pending_mapping", "conflict"} else 0,
                exception_inc=1 if state not in {"ready", "pending_mapping", "conflict", "skipped"} else 0,
            )
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status=state or "done",
                    project_code=str(result.get("project_code") or record_id),
                    archive_path=str(result.get("archive_path") or ""),
                    payload={
                        "label": "映射回刷完成" if state in {"ready", "conflict"} else "映射回刷仍有待处理项",
                        "record_id": record_id,
                        "state": state,
                    },
                )
            )

        final_status = "success"
        if failed > 0:
            final_status = "success_with_warnings" if refreshed > 0 else "failed"
        elif pending > 0 or skipped > 0:
            final_status = "success_with_warnings"
        self.store.finish_job(
            job_id,
            status=final_status,
            summary={
                "refreshed_count": refreshed,
                "pending_mapping_count": pending,
                "skipped_count": skipped,
                "failed_count": failed,
            },
        )

    def _launch_mapping_refresh_job(
        self,
        *,
        source_name: str,
        match_field: str,
        target_field: str,
        entry_id: str,
    ) -> Dict[str, Any]:
        affected_records = self._find_records_for_mapping_refresh(match_field=match_field, source_name=source_name)
        if not affected_records:
            return {"job_id": "", "job_type": "mapping_refresh", "affected_count": 0}
        job_id = self.store.create_job(
            "mapping_refresh",
            metadata={
                "entry_id": entry_id,
                "source_name": source_name,
                "match_field": match_field,
                "target_field": target_field,
                "affected_count": len(affected_records),
            },
        )
        record_ids = [str(item["record_id"]) for item in affected_records]

        def _run_mapping_refresh_wrapper() -> None:
            try:
                with self._thread_job_scope("mapping_refresh"):
                    self._run_mapping_refresh_job(
                        job_id=job_id,
                        record_ids=record_ids,
                    )
            finally:
                self._release_mutating_job("mapping_refresh")

        self._start_background_thread(
            name=f"peap-mapping-refresh-{int(time.time())}",
            target=_run_mapping_refresh_wrapper,
        )
        return {"job_id": job_id, "job_type": "mapping_refresh", "affected_count": len(affected_records)}

    def launch_pending_mapping_refresh(self, _payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        background_launched = False
        self._reserve_mutating_job("mapping_refresh")
        try:
            affected_records = self._find_pending_mapping_records()
            if not affected_records:
                return {"job_id": "", "job_type": "mapping_refresh", "affected_count": 0}
            job_id = self.store.create_job(
                "mapping_refresh",
                metadata={
                    "scope": "pending_mapping",
                    "affected_count": len(affected_records),
                },
            )
            record_ids = [str(item["record_id"]) for item in affected_records]

            def _run_pending_mapping_refresh_wrapper() -> None:
                try:
                    with self._thread_job_scope("mapping_refresh"):
                        self._run_mapping_refresh_job(
                            job_id=job_id,
                            record_ids=record_ids,
                        )
                finally:
                    self._release_mutating_job("mapping_refresh")

            self._start_background_thread(
                name=f"peap-pending-mapping-refresh-{int(time.time())}",
                target=_run_pending_mapping_refresh_wrapper,
            )
            background_launched = True
            return {"job_id": job_id, "job_type": "mapping_refresh", "affected_count": len(affected_records)}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh")

    def _ingest_manual_import_file(self, file_path: str) -> Dict[str, Any]:
        runner = self._build_ingest_runner(
            archive_root=self.get_basic_settings()["archive_root"],
        )
        return runner.ingest(ItemSavedPayload(source_file=str(file_path)))

    def _run_manual_import_job(self, *, job_id: str, files: list[str]) -> None:
        imported = 0
        pending = 0
        skipped = 0
        failed = 0
        for index, file_path in enumerate(files, start=1):
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status="running",
                    payload={
                        "label": "正在解析手动导入网页",
                        "task_index": index,
                        "task_total": len(files),
                        "task_label": os.path.basename(file_path),
                        "phase_percent": int(index * 100 / max(len(files), 1)),
                    },
                )
            )
            try:
                result = self._ingest_manual_import_file(file_path)
                state = str(result.get("state") or "")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.store.update_job_counts(job_id, downloaded_inc=1, exception_inc=1)
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="reprocessing",
                        status="failed",
                        error_type="manual_import_failed",
                        error_message=str(exc),
                        payload={"label": "手动导入失败", "source_file": file_path},
                    )
                )
                continue

            imported += 1
            if state == "pending_mapping":
                pending += 1
            elif state == "skipped":
                skipped += 1
            elif state not in {"ready", "conflict"}:
                failed += 1
            self.store.update_job_counts(
                job_id,
                downloaded_inc=1,
                persisted_inc=1 if state in {"ready", "pending_mapping", "conflict"} else 0,
                exception_inc=1 if state not in {"ready", "pending_mapping", "conflict", "skipped"} else 0,
            )
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status=state or "done",
                    project_code=str(result.get("project_code") or ""),
                    archive_path=str(result.get("archive_path") or ""),
                    payload={"label": "手动导入完成", "source_file": file_path, "state": state},
                )
            )

        final_status = "success"
        if failed > 0:
            final_status = "success_with_warnings" if imported > 0 else "failed"
        elif pending > 0 or skipped > 0:
            final_status = "success_with_warnings"
        self.store.finish_job(
            job_id,
            status=final_status,
            summary={
                "imported_count": imported,
                "pending_mapping_count": pending,
                "skipped_count": skipped,
                "failed_count": failed,
            },
        )

    def launch_manual_import(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._reserve_mutating_job("manual_import")
        try:
            input_dir = os.path.abspath(
                str(payload.get("input_dir") or self.get_advanced_settings().get("raw_manual_root") or "").strip()
            )
            if not input_dir or not os.path.isdir(input_dir):
                raise FileNotFoundError(input_dir or "manual import directory is empty")
            files = _discover_import_files(input_dir)
            job_id = self.store.create_job(
                "manual_import",
                metadata={"input_dir": input_dir, "discovered_count": len(files)},
            )
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="manual_import_scan",
                    status="done",
                    payload={"label": "已整理待导入网页", "discovered_count": len(files)},
                )
            )
            if files:
                def _run_manual_import_wrapper() -> None:
                    try:
                        self._run_manual_import_job(job_id=job_id, files=files)
                    finally:
                        self._release_mutating_job("manual_import")

                self._start_background_thread(
                    name=f"peap-manual-import-{int(time.time())}",
                    target=_run_manual_import_wrapper,
                )
            else:
                self.store.finish_job(
                    job_id,
                    status="success",
                    summary={"imported_count": 0, "pending_mapping_count": 0, "skipped_count": 0, "failed_count": 0},
                )
                self._release_mutating_job("manual_import")
            return {
                "job_id": job_id,
                "job_type": "manual_import",
                "input_dir": input_dir,
                "discovered_count": len(files),
            }
        except Exception:
            self._release_mutating_job("manual_import")
            raise

    def upsert_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        background_launched = False
        self._reserve_mutating_job("mapping_refresh")
        try:
            normalized = _normalize_mapping_payload(payload)
            _validate_mapping_payload(normalized)
            source_name = normalized["source_name"]
            match_field = normalized["match_field"]
            target_field = normalized["target_field"]
            group_name = normalized["group_name"]
            source_type = normalized["source_type"]
            preview = self.preview_mapping_upsert(payload)
            if preview.get("conflict") and not bool(payload.get("confirm_overwrite")):
                raise ValueError("mapping overwrite requires confirmation")
            entry_id = self.store.upsert_mapping_entry(
                company_name=source_name,
                group_name=group_name,
                source_type=source_type,
                metadata={
                    key: value
                    for key, value in payload.items()
                    if key not in {"company_name", "group_name", "source_type", "source_name", "target_value", "confirm_overwrite"}
                }
                | {
                    "match_field": match_field,
                    "target_field": target_field,
                },
            )
            self.store.add_audit_entry(
                "mapping_upserted",
                {
                    "entry_id": entry_id,
                    "source_name": source_name,
                    "match_field": match_field,
                    "target_field": target_field,
                    "group_name": group_name,
                    "source_type": source_type,
                },
            )
            refresh_payload = self._launch_mapping_refresh_job(
                source_name=source_name,
                match_field=match_field,
                target_field=target_field,
                entry_id=entry_id,
            )
            background_launched = bool(refresh_payload.get("job_id"))
            return {"entry_id": entry_id, **preview, **refresh_payload}
        finally:
            if not background_launched:
                self._release_mutating_job("mapping_refresh")

    def _reprocess_record(self, record_id: str) -> Dict[str, Any]:
        record = self.store.get_record(record_id)
        archive_root = self.get_basic_settings()["archive_root"]
        runner = self._build_ingest_runner(archive_root=archive_root)
        preferred_source = str(record.get("archive_path") or "").strip()
        if not preferred_source or not os.path.isfile(preferred_source):
            preferred_source = str(record["source_file"])
        result = runner.ingest(
            ItemSavedPayload(
                source_file=preferred_source,
                page_url=str(record["parser_payload"].get("page_url") or ""),
                project_code=str(record["project_code"]),
                project_name=str(record["project_name"]),
                exchange=str(record["exchange"]),
                listing_date=str(record["listing_date"]),
                extra={
                    "project_type_fallback": str(record.get("project_type") or ""),
                },
            )
        )
        self.store.add_audit_entry("record_reprocessed", {"record_id": record_id, "result": result})
        return result

    def reprocess_record(self, record_id: str) -> Dict[str, Any]:
        if self._current_thread_holds_mutating_job("mapping_refresh"):
            return self._reprocess_record(record_id)
        with self._mutating_job_scope("mapping_refresh"):
            return self._reprocess_record(record_id)

    def run_export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._normalize_legacy_views()
        self._repair_missing_archives_once()
        with self._mutating_job_scope("export_excel"):
            request = ExportRequest(
                date_from=str(payload.get("date_from") or "").strip() or None,
                date_to=str(payload.get("date_to") or "").strip() or None,
                business_types=list(payload.get("business_types") or []),
                mode=str(payload.get("mode") or "rebuild"),
                cursor_key=str(payload.get("cursor_key") or ""),
                output_dir=str(payload.get("output_dir") or self.get_basic_settings()["export_root"]),
            )
            job_id = self.store.create_job(
                "export_excel",
                metadata={
                    "date_from": request.date_from or "",
                    "date_to": request.date_to or "",
                    "business_types": list(request.business_types or []),
                    "output_dir": request.output_dir,
                },
            )
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="exporting",
                    status="running",
                    payload={"label": "正在导出 Excel"},
                )
            )
            try:
                result = run_ready_export(self.store, request)
            except Exception as exc:  # noqa: BLE001
                summary = {
                    "job_id": job_id,
                    "job_type": "export_excel",
                    "export_id": "",
                    "cursor_key": request.cursor_key,
                    "new_records": 0,
                    "changed_records": 0,
                    "artifacts": [],
                    "status": "failed",
                    "message": f"导出失败：{exc}",
                }
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status="failed",
                        error_type="export_failed",
                        error_message=str(exc),
                        payload={"label": "导出失败"},
                    )
                )
                self.store.finish_job(job_id, status="failed", summary=summary)
                self.store.add_audit_entry("manual_export", summary)
                return summary

            artifacts = [item.file_path for item in result.artifacts]
            export_status = "completed" if artifacts else "empty"
            message = f"导出完成，共生成 {len(artifacts)} 个文件"
            if not artifacts:
                state_counts = self.store.count_records_by_state(
                    date_from=request.date_from,
                    date_to=request.date_to,
                    business_types=request.business_types,
                )
                pending_count = int(state_counts.get("pending_mapping", 0))
                skipped_count = int(state_counts.get("skipped", 0))
                if pending_count > 0:
                    message = f"当前条件下没有可导出的记录；待补映射 {pending_count} 条"
                elif skipped_count > 0:
                    message = f"当前条件下没有可导出的记录；已跳过 {skipped_count} 条"
                else:
                    message = "当前条件下没有可导出的记录"
            summary = {
                "job_id": job_id,
                "job_type": "export_excel",
                "export_id": result.export_id,
                "cursor_key": result.cursor_key,
                "new_records": result.new_records,
                "changed_records": result.changed_records,
                "artifacts": artifacts,
                "status": export_status,
                "message": message,
            }
            self.store.update_job_counts(
                job_id,
                downloaded_inc=int(result.new_records) + int(result.changed_records),
                persisted_inc=len(artifacts),
                exception_inc=0,
            )
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="exporting",
                    status="done" if artifacts else "empty",
                    payload={
                        "label": "导出完成" if artifacts else "当前没有可导出的记录",
                        "artifacts": artifacts,
                        "new_records": int(result.new_records),
                        "changed_records": int(result.changed_records),
                    },
                )
            )
            self.store.finish_job(
                job_id,
                status="success" if artifacts else "success_with_warnings",
                summary=summary,
            )
            self.store.add_audit_entry("manual_export", summary)
            return summary

    def launch_one_click(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._launch_streaming_job(payload, job_type="one_click", auto_export=False)

    def _launch_streaming_job(
        self,
        payload: Dict[str, Any],
        *,
        job_type: str,
        auto_export: bool,
    ) -> Dict[str, Any]:
        from peap.streaming_daily_pipeline import run_streaming_daily_pipeline

        self._normalize_legacy_views()
        self._repair_missing_archives_once()
        self._reserve_mutating_job(job_type)
        basic = self.get_basic_settings()
        advanced = self.get_advanced_settings()
        response: dict[str, Any] = {"job_id": "", "db_path": self.db_path}
        ready = threading.Event()

        def _job_created(job_id: str, db_path: str) -> None:
            response["job_id"] = job_id
            response["db_path"] = db_path
            ready.set()

        args = _namespace(
            start_date=str(payload.get("start_date") or ""),
            end_date=str(payload.get("end_date") or ""),
            exchange=str(payload.get("exchange") or basic["default_exchange"]),
            project_type=str(payload.get("project_type") or basic["default_project_type"]),
            concurrency=int(payload.get("concurrency") or basic["default_concurrency"]),
            page_size=payload.get("page_size"),
            max_pages=payload.get("max_pages"),
            with_refresh=False,
            no_resume=bool(payload.get("no_resume", False)),
            save_json=bool(payload.get("save_json", advanced.get("save_json", False))),
            postprocess_config=str(
                payload.get("postprocess_config")
                or advanced.get("postprocess_config")
                or self.default_postprocess_config
                or ""
            ),
            verbose=bool(payload.get("verbose", False)),
            streaming_db=self.db_path,
            no_auto_export=not auto_export,
        )

        def _run() -> None:
            try:
                run_streaming_daily_pipeline(
                    args,
                    config_obj=self.config,
                    emit_console=False,
                    job_created_callback=_job_created,
                    job_type=job_type,
                    archive_root=basic["archive_root"],
                    export_root=basic["export_root"],
                    auto_export=auto_export,
                )
            finally:
                self._release_mutating_job(job_type)

        try:
            thread = threading.Thread(target=_run, name=f"peap-{job_type}-{int(time.time())}", daemon=True)
            thread.start()
            with self._lock:
                self._threads[thread.name] = thread
            ready.wait(timeout=2.0)
            response["job_type"] = job_type
            return response
        except Exception:
            self._release_mutating_job(job_type)
            raise
