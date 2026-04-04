"""End-to-end execution pipeline for parsing and exporting."""

import datetime as dt
import glob
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from peap_postprocess.compare_regression import DEFAULT_COMPARE_FIELDS
from .constants import (
    KEY_PROJECT_CODE,
    SKIP_FILE_NAME,
    SKIP_FOLDER_SUFFIX,
    TYPE_PRE_DISCLOSURE,
)
from .excel_handler import (
    ExcelBatchWriter,
    ExcelOutputRuntime,
    ExcelSchemaSettings,
    build_excel_schema_settings,
    load_excel_output_runtime,
    normalize_excel_target_path,
)
from .output_mapping import map_standard_to_excel_payload
from .parse_cache import (
    ParseCacheStore,
    build_parser_signature,
    build_runtime_version_signature,
)
from .parsing import (
    ParsedProject,
    ParseError,
    SkipParse,
    parse_file,
)
from .targeting import OutputTargetSettings, build_output_target_settings, decide_output_file


def _should_process_file(file_path: str) -> bool:
    if SKIP_FOLDER_SUFFIX in file_path:
        return False
    if file_path.endswith(SKIP_FILE_NAME):
        return False
    return True


def _normalize_path(value: str) -> str:
    return str(value or "").lower().replace(" ", "")


def _is_pre_disclosure_path(file_path: str) -> bool:
    return TYPE_PRE_DISCLOSURE in _normalize_path(file_path)


def _is_invalid_parsed_project(parsed: ParsedProject) -> bool:
    project_name = parsed.project_name
    project_code = parsed.project_code
    if "404椤甸潰" in project_name:
        return True
    if not project_code and "404" in project_name:
        return True
    return False


def _is_invalid_detail_page(data: Dict[str, Any]) -> bool:
    project_name = str(data.get("项目名称") or "").strip()
    project_code = str(data.get(KEY_PROJECT_CODE) or "").strip()
    # Keep this conservative: only skip explicit source-side 404 pages.
    if "404页面" in project_name:
        return True
    # Extra guard for variants like "...404..." with no project code.
    if not project_code and "404" in project_name:
        return True
    return False


def _hash_output_payload(data: Dict[str, Any]) -> str:
    try:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = repr(data)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_move(src_path: str, dst_path: str) -> str:
    if not os.path.exists(src_path):
        return src_path

    src_abs = os.path.abspath(src_path)
    dst_abs = os.path.abspath(dst_path)
    if src_abs == dst_abs:
        return dst_path

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    final_path = dst_path
    if os.path.exists(final_path):
        base, ext = os.path.splitext(final_path)
        index = 1
        while os.path.exists(f"{base}__dup{index}{ext}"):
            index += 1
        final_path = f"{base}__dup{index}{ext}"

    shutil.move(src_path, final_path)
    return final_path


def _archive_pre_disclosure_html(
    *,
    html_root: str,
    source_file: str,
    status: str,
    logger: logging.Logger,
) -> None:
    if _is_pre_disclosure_path(source_file):
        return

    status_text = str(status or "挂牌")
    pre_dir = os.path.join(html_root, f"{status_text}_{TYPE_PRE_DISCLOSURE}")
    target_file = os.path.join(pre_dir, os.path.basename(source_file))
    moved_file = _safe_move(source_file, target_file)

    source_assets_dir = f"{os.path.splitext(source_file)[0]}_files"
    if os.path.isdir(source_assets_dir):
        target_assets_dir = f"{os.path.splitext(moved_file)[0]}_files"
        _safe_move(source_assets_dir, target_assets_dir)

    logger.info("预披露网页归档: %s -> %s", source_file, moved_file)


@dataclass
class RunSummary:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    compare_diffs: int = 0
    parse_cache_hits: int = 0
    parse_cache_misses: int = 0
    parse_cache_writes: int = 0
    excel_upsert_skipped: int = 0
    compare_report_file: str = ""
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParserPipelineSettings:
    parse_cache_db: str = ""
    compare_report_dir: str = ""
    output_target_settings: Optional[OutputTargetSettings] = None
    excel_schema_settings: Optional[ExcelSchemaSettings] = None
    excel_output_runtime: Optional[ExcelOutputRuntime] = None


def build_parser_pipeline_settings(config_obj: object) -> ParserPipelineSettings:
    excel_schema_settings = build_excel_schema_settings(config_obj)
    return ParserPipelineSettings(
        parse_cache_db=str(getattr(config_obj, "PARSER_CACHE_DB", "") or ""),
        compare_report_dir=str(getattr(config_obj, "COMPARE_REPORT_DIR", "") or ""),
        output_target_settings=build_output_target_settings(config_obj),
        excel_schema_settings=excel_schema_settings,
        excel_output_runtime=load_excel_output_runtime(excel_schema_settings),
    )


class ParserPipeline:
    def __init__(
        self,
        html_root: str,
        dry_run: bool = False,
        limit: Optional[int] = None,
        batch_flush_interval: int = 50,
        compare_report_file: Optional[str] = None,
        compare_fields: Optional[List[str]] = None,
        parse_cache_enabled: bool = True,
        parse_cache_db: str = "",
        compare_report_dir: str = "",
        output_target_settings: Optional[OutputTargetSettings] = None,
        excel_schema_settings: Optional[ExcelSchemaSettings] = None,
        excel_output_runtime: Optional[ExcelOutputRuntime] = None,
        settings: Optional[ParserPipelineSettings] = None,
        progress_interval: int = 50,
        logger: Optional[logging.Logger] = None,
    ):
        resolved_settings = settings or ParserPipelineSettings()
        self.html_root = html_root
        self.dry_run = dry_run
        self.limit = limit
        self.batch_flush_interval = max(0, int(batch_flush_interval))
        self.compare_report_file = compare_report_file
        self.compare_fields = compare_fields or list(DEFAULT_COMPARE_FIELDS)
        self.parse_cache_enabled = bool(parse_cache_enabled)
        self.parse_cache_db = str(parse_cache_db or resolved_settings.parse_cache_db)
        self.compare_report_dir = str(compare_report_dir or resolved_settings.compare_report_dir)
        self.output_target_settings = (
            output_target_settings
            or resolved_settings.output_target_settings
            or OutputTargetSettings()
        )
        self.excel_schema_settings = (
            excel_schema_settings
            or resolved_settings.excel_schema_settings
        )
        self.excel_output_runtime = (
            excel_output_runtime
            or resolved_settings.excel_output_runtime
        )
        self.progress_interval = max(0, int(progress_interval))
        self.logger = logger or logging.getLogger("parser_v2")

    def collect_html_files(self) -> List[str]:
        patterns = ("*.html", "*.htm", "*.mhtml")
        all_files: List[str] = []
        for file_pattern in patterns:
            pattern = os.path.join(self.html_root, "**", file_pattern)
            all_files.extend(glob.glob(pattern, recursive=True))

        html_files = [path for path in all_files if _should_process_file(path)]
        html_files = sorted(set(html_files))
        if self.limit is not None:
            return html_files[: self.limit]
        return html_files

    def run(self) -> RunSummary:
        summary = RunSummary()
        excel_output_runtime = self.excel_output_runtime
        if excel_output_runtime is None and self.excel_schema_settings is not None:
            excel_output_runtime = load_excel_output_runtime(self.excel_schema_settings)
        batch_writer = None if self.dry_run else ExcelBatchWriter(
            runtime=excel_output_runtime,
            logger=self.logger,
        )
        parse_cache_store: Optional[ParseCacheStore] = None
        pending_counts_by_target: Dict[str, int] = {}
        pending_sync_files_by_target: Dict[str, List[str]] = {}
        pre_archive_actions: List[tuple[str, str, str]] = []
        run_started = dt.datetime.now()

        if self.parse_cache_enabled and self.parse_cache_db:
            try:
                parser_signature = build_parser_signature()
                runtime_signature = build_runtime_version_signature()
                run_signature = f"{runtime_signature}|{parser_signature}|no-mapping"
                parse_cache_store = ParseCacheStore(
                    db_path=self.parse_cache_db,
                    run_signature=run_signature,
                    logger=self.logger,
                    commit_interval=max(10, self.batch_flush_interval or 50),
                )
                self.logger.info(
                    "Parse cache enabled: db=%s parser_sig=%s",
                    os.path.abspath(self.parse_cache_db),
                    parser_signature[:12],
                )
            except Exception as exc:
                parse_cache_store = None
                self.logger.warning("Parse cache disabled due to init error: %s", exc)
        elif self.parse_cache_enabled:
            self.logger.warning("Parse cache enabled but no parse_cache_db configured; disabling parse cache")
        else:
            self.logger.info("Parse cache disabled by args")

        def _parse_with_cache(file_path: str) -> ParsedProject:
            if parse_cache_store is not None:
                try:
                    cached = parse_cache_store.get(file_path)
                    if cached is not None:
                        return cached
                except Exception as exc:
                    self.logger.warning("Parse cache read failed: %s (%s)", file_path, exc)
            parsed_fresh = parse_file(file_path)
            if parse_cache_store is not None:
                try:
                    parse_cache_store.put(parsed_fresh)
                except Exception as exc:
                    self.logger.warning("Parse cache write failed: %s (%s)", file_path, exc)
            return parsed_fresh

        def _flush_batch(*, final: bool) -> None:
            if batch_writer is None or not pending_counts_by_target:
                return

            current_pending_counts = dict(pending_counts_by_target)
            current_pending_sync = {k: list(v) for k, v in pending_sync_files_by_target.items()}
            flush_errors = batch_writer.flush()
            failed_targets = set(flush_errors.keys())

            remaining_archive_actions: List[tuple[str, str, str]] = []
            for source_file, status, target_file in pre_archive_actions:
                if target_file in failed_targets:
                    remaining_archive_actions.append((source_file, status, target_file))
                    continue
                try:
                    _archive_pre_disclosure_html(
                        html_root=self.html_root,
                        source_file=source_file,
                        status=status,
                        logger=self.logger,
                    )
                except Exception as exc:
                    self.logger.warning("预披露网页归档失败: %s (%s)", source_file, exc)
            pre_archive_actions[:] = remaining_archive_actions

            if parse_cache_store is not None and current_pending_sync:
                for target_file, synced_files in current_pending_sync.items():
                    if target_file in failed_targets:
                        continue
                    try:
                        parse_cache_store.mark_output_synced_batch(
                            synced_files,
                            target_file=target_file,
                        )
                    except Exception as exc:
                        self.logger.warning(
                            "Parse cache output sync mark failed: target=%s error=%s",
                            target_file,
                            exc,
                        )

            pending_counts_by_target.clear()
            pending_sync_files_by_target.clear()
            if failed_targets:
                for target_file, count in current_pending_counts.items():
                    if target_file in failed_targets:
                        pending_counts_by_target[target_file] = count
                for target_file, file_paths in current_pending_sync.items():
                    if target_file in failed_targets:
                        pending_sync_files_by_target[target_file] = file_paths

            if not flush_errors:
                return

            if not final:
                for target_file, message in flush_errors.items():
                    self.logger.warning(
                        "批量落盘检查点失败(将继续重试): target=%s, error=%s",
                        target_file,
                        message,
                    )
                return

            for target_file, message in flush_errors.items():
                failed_count = current_pending_counts.get(target_file, 0)
                if failed_count <= 0:
                    failed_count = 1
                summary.failed += failed_count
                summary.succeeded = max(0, summary.succeeded - failed_count)
                error_message = f"{target_file}: batch-flush-failed: {message}"
                summary.errors.append(error_message)
                self.logger.error("Failed: %s", error_message)

        html_files = self.collect_html_files()
        if not html_files:
            self.logger.warning("No HTML files found under %s", self.html_root)
            return summary

        self.logger.info("Found %s HTML files to process", len(html_files))

        def _log_progress(*, force: bool = False) -> None:
            if not force and (self.progress_interval <= 0 or summary.processed % self.progress_interval != 0):
                return
            elapsed_seconds = max(1.0, (dt.datetime.now() - run_started).total_seconds())
            speed = summary.processed / elapsed_seconds
            remaining = max(0, len(html_files) - summary.processed)
            eta_seconds = int(remaining / speed) if speed > 0 else -1
            cache_hits = summary.parse_cache_hits
            cache_misses = summary.parse_cache_misses
            cache_writes = summary.parse_cache_writes
            if parse_cache_store is not None:
                stats = parse_cache_store.stats
                cache_hits = stats.hits
                cache_misses = stats.misses
                cache_writes = stats.writes
            eta_text = "unknown" if eta_seconds < 0 else str(dt.timedelta(seconds=eta_seconds))
            self.logger.info(
                (
                    "Progress: %s/%s (%.1f%%) succeeded=%s failed=%s "
                    "upsert_skipped=%s cache_hits=%s cache_misses=%s cache_writes=%s "
                    "speed=%.2f files/s eta=%s"
                ),
                summary.processed,
                len(html_files),
                (summary.processed * 100.0 / len(html_files)) if html_files else 100.0,
                summary.succeeded,
                summary.failed,
                summary.excel_upsert_skipped,
                cache_hits,
                cache_misses,
                cache_writes,
                speed,
                eta_text,
            )

        try:
            for file_path in html_files:
                summary.processed += 1
                try:
                    parsed = _parse_with_cache(file_path)
                    if _is_invalid_parsed_project(parsed):
                        self.logger.warning("Skip invalid detail page (404): %s", file_path)
                        summary.succeeded += 1
                        continue

                    target_file = decide_output_file(
                        parsed,
                        settings=self.output_target_settings,
                    )
                    if not target_file:
                        raise ParseError(f"target-file-undetermined: {file_path}")
                    target_file = normalize_excel_target_path(target_file)
                    if not target_file:
                        raise ParseError(f"target-file-invalid: {file_path}")

                    output_payload = map_standard_to_excel_payload(
                        parsed,
                        target_file,
                    )
                    output_hash = _hash_output_payload(output_payload)

                    if self.dry_run:
                        self.logger.info(
                            "Dry run: %s -> %s [%s]",
                            file_path,
                            target_file,
                            parsed.exchange,
                        )
                        summary.succeeded += 1
                        continue

                    if batch_writer is None:
                        raise ParseError("batch-writer-unavailable")
                    if parse_cache_store is not None:
                        try:
                            if parse_cache_store.is_output_synced(
                                file_path,
                                target_file=target_file,
                                payload_hash=output_hash,
                            ):
                                summary.excel_upsert_skipped += 1
                                summary.succeeded += 1
                                self.logger.debug(
                                    "Skip unchanged upsert: %s -> %s",
                                    file_path,
                                    target_file,
                                )
                                continue
                        except Exception as exc:
                            self.logger.warning(
                                "Parse cache output check failed: %s (%s)",
                                file_path,
                                exc,
                            )

                    ok = batch_writer.upsert(
                        output_payload,
                        target_file,
                        source_file=file_path,
                        exchange=parsed.exchange,
                    )
                    if not ok:
                        raise ParseError(f"excel-save-failed: source={file_path}, target={target_file}")
                    pending_counts_by_target[target_file] = pending_counts_by_target.get(target_file, 0) + 1
                    pending_sync_files_by_target.setdefault(target_file, []).append(file_path)
                    if parse_cache_store is not None:
                        try:
                            parse_cache_store.mark_output_pending(
                                file_path,
                                target_file=target_file,
                                payload_hash=output_hash,
                            )
                        except Exception as exc:
                            self.logger.warning(
                                "Parse cache pending output mark failed: %s (%s)",
                                file_path,
                                exc,
                            )

                    if parsed.is_pre_disclosure:
                        pre_archive_actions.append((file_path, parsed.status, target_file))

                    summary.succeeded += 1
                    if (
                        batch_writer is not None
                        and self.batch_flush_interval > 0
                        and summary.processed % self.batch_flush_interval == 0
                    ):
                        _flush_batch(final=False)
                except Exception as exc:
                    if isinstance(exc, SkipParse):
                        summary.succeeded += 1
                        self.logger.info("Skip parse: %s", exc)
                        continue
                    summary.failed += 1
                    error_message = f"{file_path}: {exc}"
                    summary.errors.append(error_message)
                    self.logger.error("Failed: %s", error_message)
                finally:
                    _log_progress()
        finally:
            if not self.dry_run and batch_writer is not None:
                _flush_batch(final=True)
            if parse_cache_store is not None:
                parse_cache_store.flush()
                cache_stats = parse_cache_store.stats
                summary.parse_cache_hits = cache_stats.hits
                summary.parse_cache_misses = cache_stats.misses
                summary.parse_cache_writes = cache_stats.writes
                parse_cache_store.close()
            _log_progress(force=True)

        return summary

