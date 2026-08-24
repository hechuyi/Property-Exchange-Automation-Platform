"""Top-level downloader run orchestration helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass, replace
from types import SimpleNamespace
from typing import Any, Callable

from peap_core.source_catalog import get_source_descriptor

from .download_archive_audit import (
    DownloadArchiveAuditIssue,
    audit_discovery_evidence_root,
    audit_download_archive_root,
)
from .download_artifact_audit import build_download_artifact_audit
from .download_errors import archive_audit_failed_error, collect_failed_error
from .download_models import DownloadRunResult, TaskSplitPlan, TaskTypedErrorList
from .download_reporting import (
    merge_totals,
    new_totals,
    print_aggregate_summary,
    totals_to_summary_dict,
    validate_discovery_task_manifest_reference,
)
from .download_runtime import build_download_driver, run_download_driver
from .download_split_planning import save_split_plan_file
from .download_task_flow import (
    DownloadTaskFlowError,
    load_requested_split_plans,
    prepare_chunk_state_context,
    run_download_task,
)
from .download_tasks import (
    DownloadTaskRegistrySettings,
    DownloadTaskSpec,
    build_download_task_registry_settings,
    build_task_registry,
)


class DownloadRunnerError(RuntimeError):
    """Raised when downloader setup or top-level execution cannot continue."""


@dataclass
class DownloadRunRequest:
    exchange: str = "all"
    record_family: str = "listing"
    business_id: str = "all"
    list_tasks: bool = False
    output_root: str = ""
    force_manual_root: bool = False
    start_date: str | None = None
    end_date: str | None = None
    page_size: int | None = None
    max_pages: int | None = None
    concurrency: int = 1
    resume: bool = True
    save_json: bool = False
    sse_ssl_verify: bool = True
    sse_ca_bundle: str | None = None
    log_dir: str = ""
    log_file: str | None = None
    verbose: bool = False
    auto_split: bool = False
    split_candidates: int = 0
    split_min_days: int = 0
    split_max_depth: int = 0
    split_plan_only: bool = False
    split_plan_file: str | None = None
    split_use_plan: bool = False
    split_mode: str = "fast"
    chunk_state_file: str | None = None
    item_saved_callback: Callable[[dict[str, object]], None] | None = None
    task_progress_callback: Callable[[dict[str, object]], None] | None = None


@dataclass(frozen=True)
class DownloadRunnerSettings:
    auto_html_root: str = ""
    manual_html_root: str = ""
    project_root: str = ""
    download_chunk_state_dir: str = ""
    is_path_within_project_root: Callable[[str], bool] | None = None
    task_registry_settings: DownloadTaskRegistrySettings | None = None


@dataclass(frozen=True)
class PreparedDownloadSession:
    settings: DownloadRunnerSettings
    request: object
    output_root: str
    tasks: list[DownloadTaskSpec]


def build_download_runner_settings(config_obj: object) -> DownloadRunnerSettings:
    path_within_project_root = getattr(config_obj, "is_path_within_project_root", None)
    auto_html_root = str(getattr(config_obj, "AUTO_HTML_FOLDER", "") or "")
    manual_html_root = str(getattr(config_obj, "HTML_FOLDER", "") or "")
    project_root = str(getattr(config_obj, "PROJECT_ROOT", "") or "")
    download_chunk_state_dir = str(getattr(config_obj, "DOWNLOAD_CHUNK_STATE_DIR", "") or "")
    return DownloadRunnerSettings(
        auto_html_root=auto_html_root,
        manual_html_root=manual_html_root,
        project_root=project_root,
        download_chunk_state_dir=download_chunk_state_dir,
        is_path_within_project_root=path_within_project_root if callable(path_within_project_root) else None,
        task_registry_settings=build_download_task_registry_settings(config_obj),
    )


def clone_download_request(request: DownloadRunRequest, **overrides: object) -> DownloadRunRequest:
    return replace(request, **overrides)


def build_download_run_request(
    args: object,
    *,
    config_obj: object,
) -> DownloadRunRequest:
    defaults = config_obj.DOWNLOADER_DEFAULTS
    split_plan_only = bool(getattr(args, "split_plan_only", False))
    split_use_plan = bool(getattr(args, "split_use_plan", False))
    split_plan_file = getattr(args, "split_plan_file", None)
    auto_split = bool(getattr(args, "auto_split", defaults.get("auto_split", False)))
    if split_plan_only or split_use_plan:
        auto_split = True
    if split_use_plan and not split_plan_file:
        raise ValueError("--split-use-plan requires --split-plan-file")

    return DownloadRunRequest(
        exchange=str(getattr(args, "exchange", defaults.get("exchange", "all"))),
        record_family=str(getattr(args, "record_family", defaults.get("record_family", "listing"))),
        business_id=str(getattr(args, "business_id", defaults.get("business_id", "all"))),
        list_tasks=bool(getattr(args, "list_tasks", False)),
        output_root=str(getattr(args, "output_root", None) or ""),
        force_manual_root=bool(getattr(args, "force_manual_root", False)),
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        page_size=getattr(args, "page_size", None),
        max_pages=getattr(args, "max_pages", None),
        concurrency=int(getattr(args, "concurrency", defaults.get("concurrency", 1))),
        resume=bool(getattr(args, "resume", defaults.get("resume", True))),
        save_json=bool(getattr(args, "save_json", defaults.get("save_json", False))),
        sse_ssl_verify=bool(getattr(args, "sse_ssl_verify", defaults.get("sse_ssl_verify", True))),
        sse_ca_bundle=getattr(args, "sse_ca_bundle", defaults.get("sse_ca_bundle")),
        log_dir=str(getattr(args, "log_dir", "")),
        log_file=getattr(args, "log_file", None),
        verbose=bool(getattr(args, "verbose", False)),
        auto_split=auto_split,
        split_candidates=int(getattr(args, "split_candidates", defaults.get("split_candidates", 0))),
        split_min_days=int(getattr(args, "split_min_days", defaults.get("split_min_days", 0))),
        split_max_depth=int(getattr(args, "split_max_depth", defaults.get("split_max_depth", 0))),
        split_plan_only=split_plan_only,
        split_plan_file=str(split_plan_file).strip() or None if split_plan_file is not None else None,
        split_use_plan=split_use_plan,
        split_mode=str(getattr(args, "split_mode", defaults.get("split_mode", "fast"))),
        chunk_state_file=getattr(args, "chunk_state_file", None),
        item_saved_callback=getattr(args, "item_saved_callback", None),
        task_progress_callback=getattr(args, "task_progress_callback", None),
    )


def task_registry(
    config_obj: object,
    *,
    settings: DownloadRunnerSettings | None = None,
) -> dict[str, DownloadTaskSpec]:
    return build_task_registry(
        config_obj,
        settings=None if settings is None else settings.task_registry_settings,
    )


def build_task_list_payload(
    config_obj: object,
    *,
    settings: DownloadRunnerSettings | None = None,
) -> list[dict[str, Any]]:
    registry = task_registry(config_obj, settings=settings)
    return [
        {
            "task_id": task_id,
            "display_name": registry[task_id].display_name,
            "default_page_size": registry[task_id].default_page_size,
            "source_id": registry[task_id].manifest.source_id,
            "record_family": registry[task_id].manifest.record_family,
            "business_id": registry[task_id].manifest.business_id,
            "list_endpoint": registry[task_id].manifest.list_endpoint,
            "detail_route": registry[task_id].manifest.detail_route,
            "render_page_route": registry[task_id].manifest.render_page_route,
            "detail_api_endpoint": registry[task_id].manifest.detail_api_endpoint,
            "transferee_details_endpoint": registry[task_id].manifest.transferee_details_endpoint,
            "date_field_candidates": list(registry[task_id].manifest.date_field_candidates),
            "supports_list_only": registry[task_id].capabilities.supports_list_only,
            "supports_prefetched_candidates": registry[task_id].capabilities.supports_prefetched_candidates,
        }
        for task_id in sorted(registry)
    ]


def resolve_tasks(
    config_obj: object,
    exchange_arg: str,
    record_family_arg: str,
    business_id_arg: str,
    *,
    settings: DownloadRunnerSettings | None = None,
) -> list[DownloadTaskSpec]:
    tasks: list[DownloadTaskSpec] = []
    for spec in task_registry(config_obj, settings=settings).values():
        if exchange_arg != "all" and spec.exchange_code != exchange_arg:
            continue
        if record_family_arg != "all" and spec.record_family != record_family_arg:
            continue
        if business_id_arg != "all" and spec.business_id != business_id_arg:
            continue
        tasks.append(spec)
    return tasks


def task_progress_label(spec: DownloadTaskSpec) -> str:
    try:
        exchange_text = get_source_descriptor(spec.exchange_code).canonical_label
    except KeyError:
        exchange_text = spec.exchange_code
    return f"{exchange_text} - {spec.progress_label}"


def _task_progress_label(spec: DownloadTaskSpec) -> str:
    return task_progress_label(spec)


def ensure_runtime_dependencies(tasks: list[DownloadTaskSpec], *, logger: logging.Logger) -> bool:
    if not tasks:
        return True

    try:
        if importlib.util.find_spec("playwright") is not None:
            return True
    except ModuleNotFoundError:
        pass

    exe = sys.executable
    message = (
        "Missing runtime dependency 'playwright' for current interpreter. "
        f"python={exe} | install with: "
        "uv sync && "
        f"\"{exe}\" -m playwright install chromium"
    )
    print(message)
    logger.error(message)
    return False


def _reject_non_executable_tasks(tasks: list[DownloadTaskSpec], *, logger: logging.Logger) -> None:
    blocked_tasks = [spec for spec in tasks if not bool(getattr(spec, "implemented", True))]
    if not blocked_tasks:
        return
    task_ids = ", ".join(spec.task_id for spec in blocked_tasks)
    message = f"Downloader task is not executable yet: {task_ids}"
    print(message)
    logger.error(message)
    raise DownloadRunnerError(message)


def parse_date_arg(raw: str | None, name: str) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        return dt.datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid {name}: {raw!r} (expected YYYY-MM-DD)") from exc


def _copy_request_object(request: object) -> object:
    if isinstance(request, DownloadRunRequest):
        return clone_download_request(request)
    if is_dataclass(request):
        return replace(request)
    if hasattr(request, "__dict__"):
        return SimpleNamespace(**vars(request))
    raise TypeError(f"unsupported download request object: {type(request)!r}")


def normalize_date_range_args(args: object, *, logger: logging.Logger) -> object:
    normalized_args = _copy_request_object(args)
    start = parse_date_arg(getattr(normalized_args, "start_date", None), "start-date")
    end = parse_date_arg(getattr(normalized_args, "end_date", None), "end-date")
    if start is None or end is None:
        return normalized_args
    if start <= end:
        return normalized_args

    original_start = getattr(normalized_args, "start_date", None)
    original_end = getattr(normalized_args, "end_date", None)
    raise ValueError(
        "start-date must be on or before end-date: "
        f"start-date={original_start} end-date={original_end}"
    )


def build_downloader(
    spec: DownloadTaskSpec,
    *,
    args: object,
    output_root: str,
    logger: logging.Logger,
    resume_override: bool | None = None,
):
    return build_download_driver(
        spec,
        args=args,
        output_root=output_root,
        logger=logger,
        resume_override=resume_override,
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if callable(value):
        return f"<callable:{getattr(value, '__name__', value.__class__.__name__)}>"
    return str(value)


def run_downloader(downloader, *, start_date: str | None, end_date: str | None, list_only: bool):
    return run_downloader_with_prefetched(
        downloader,
        start_date=start_date,
        end_date=end_date,
        list_only=list_only,
        prefetched_candidates=None,
    )


def run_downloader_with_prefetched(
    downloader,
    *,
    start_date: str | None,
    end_date: str | None,
    list_only: bool,
    prefetched_candidates: list[dict[str, object]] | None,
):
    return run_download_driver(
        downloader,
        start_date=start_date,
        end_date=end_date,
        list_only=list_only,
        prefetched_candidates=prefetched_candidates,
    )


def _validate_output_root(
    args: object,
    *,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> str:
    resolved_settings = settings or build_download_runner_settings(config_obj)
    raw_output_root = str(args.output_root or "")
    if not raw_output_root:
        message = (
            "output-root is required. "
            f"Use --output-root (default: {resolved_settings.auto_html_root or ''})"
        )
        raise DownloadRunnerError(message)
    output_root = os.path.abspath(raw_output_root)
    manual_root = os.path.abspath(str(resolved_settings.manual_html_root or ""))
    if output_root == manual_root and not args.force_manual_root:
        message = (
            "Refusing to write into manual html root. "
            f"Use another --output-root (default: {resolved_settings.auto_html_root or ''}) "
            "or pass --force-manual-root."
        )
        raise DownloadRunnerError(message)
    path_within_project_root = resolved_settings.is_path_within_project_root
    within_project_root = bool(path_within_project_root(output_root)) if path_within_project_root else False
    if within_project_root:
        message = (
            "Refusing to write downloader output under project root in rebuilt data-root mode. "
            f"output_root={output_root} project_root={resolved_settings.project_root or ''}"
        )
        raise DownloadRunnerError(message)
    return output_root


def _build_split_plan_scope(args: object) -> dict[str, object]:
    return {
        "source_id": getattr(args, "exchange", None),
        "record_family": getattr(args, "record_family", None),
        "business_id": getattr(args, "business_id", None),
        "start_date": getattr(args, "start_date", None),
        "end_date": getattr(args, "end_date", None),
        "split_candidates": int(getattr(args, "split_candidates", 0)),
        "split_min_days": int(getattr(args, "split_min_days", 0)),
        "split_max_depth": int(getattr(args, "split_max_depth", 0)),
        "split_mode": str(getattr(args, "split_mode", "")),
    }


def _task_archive_root_from_new_download(output_root: str, relpath: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", str(relpath or "").strip()) if part and part != "."]
    for index, part in enumerate(parts):
        if len(part.split("__")) >= 3:
            return os.path.abspath(os.path.join(output_root, *parts[: index + 1]))
    return os.path.abspath(output_root) if parts else ""


def _task_archive_root_from_spec(output_root: str, spec: DownloadTaskSpec) -> str:
    component = f"{spec.exchange_code}__{spec.record_family}__{spec.business_id}"
    return os.path.abspath(os.path.join(output_root, component))


def _is_path_inside_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:
        return False


def _task_saved_count(task_result: dict[str, Any]) -> int:
    summary = task_result.get("summary")
    if not isinstance(summary, Mapping):
        return 0
    value = summary.get("saved", 0)
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _task_discovery_reference(task_result: Mapping[str, object]) -> object:
    if "discovery_task_manifest" in task_result:
        return task_result.get("discovery_task_manifest")
    metadata = task_result.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get("discovery_task_manifest")
    return None


def audit_download_run_archives(
    *,
    output_root: str,
    task_results: dict[str, dict[str, Any]],
    failed_task_specs: list[DownloadTaskSpec] | None = None,
    require_detail_sidecar: bool = False,
    required_discovery_task_ids: set[str] | None = None,
    expected_discovery_run_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    output_root_abs = os.path.abspath(output_root)
    roots: set[str] = set()
    issues: list[DownloadArchiveAuditIssue] = []
    discovery_results: list[dict[str, Any]] = []
    required_discovery = {
        str(task_id or "").strip()
        for task_id in (required_discovery_task_ids or set())
        if str(task_id or "").strip()
    }
    expected_runs = {
        str(task_id or "").strip(): str(run_id or "").strip()
        for task_id, run_id in dict(expected_discovery_run_ids or {}).items()
        if str(task_id or "").strip() and str(run_id or "").strip()
    }

    for spec in failed_task_specs or []:
        root = _task_archive_root_from_spec(output_root_abs, spec)
        if os.path.isdir(root):
            roots.add(root)

    for task_id, task_result in sorted(task_results.items()):
        raw_new_downloads = task_result.get("new_downloads")
        saved_count = _task_saved_count(task_result)
        if raw_new_downloads is None:
            if saved_count > 0:
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="saved_without_new_download_manifest",
                        path=output_root_abs,
                        message="task_result.summary.saved is positive but task_result.new_downloads is missing",
                        task_id=str(task_id),
                        details={"saved": saved_count},
                    )
                )
            continue
        if not isinstance(raw_new_downloads, list):
            issues.append(
                DownloadArchiveAuditIssue(
                    code="invalid_new_downloads_contract",
                    path=output_root_abs,
                    message="task_result.new_downloads must be a list",
                    task_id=str(task_id),
                )
            )
            continue
        if saved_count > 0 and not raw_new_downloads:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="saved_without_new_download_manifest",
                    path=output_root_abs,
                    message="task_result.summary.saved is positive but task_result.new_downloads is empty",
                    task_id=str(task_id),
                    details={"saved": saved_count},
                )
            )
            continue
        for relpath in raw_new_downloads:
            if not isinstance(relpath, str):
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="invalid_new_downloads_contract",
                        path=output_root_abs,
                        message="task_result.new_downloads must contain string paths",
                        task_id=str(task_id),
                    )
                )
                continue
            download_path = os.path.abspath(os.path.join(output_root_abs, relpath))
            if not _is_path_inside_root(download_path, output_root_abs):
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="new_download_outside_output_root",
                        path=download_path,
                        message="task_result.new_downloads points outside output_root",
                        task_id=str(task_id),
                        details={"relpath": relpath},
                    )
                )
                continue
            if not os.path.isfile(download_path):
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="new_download_missing",
                        path=download_path,
                        message="task_result.new_downloads points to a missing archive html file",
                        task_id=str(task_id),
                        details={"relpath": relpath},
                    )
                )
                continue
            root = _task_archive_root_from_new_download(output_root_abs, relpath)
            if root:
                roots.add(root)
            else:
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="archive_task_root_unresolved",
                        path=download_path,
                        message="new download path does not include a task archive root component",
                        task_id=str(task_id),
                    )
                )

    for task_id in sorted(set(task_results) | required_discovery):
        task_result = task_results.get(task_id)
        raw_reference = (
            _task_discovery_reference(task_result)
            if isinstance(task_result, Mapping)
            else None
        )
        if raw_reference is None:
            if task_id in required_discovery:
                issues.append(
                    DownloadArchiveAuditIssue(
                        code="discovery_task_manifest_reference_missing",
                        path=output_root_abs,
                        message="current listing task has no discovery task manifest reference",
                        task_id=task_id,
                    )
                )
            continue
        try:
            reference = validate_discovery_task_manifest_reference(
                raw_reference,
                name="task_result.discovery_task_manifest",
            )
        except (TypeError, ValueError) as exc:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="invalid_discovery_task_manifest_reference",
                    path=output_root_abs,
                    message=str(exc),
                    task_id=task_id,
                )
            )
            continue

        expected_run_id = expected_runs.get(task_id)
        if expected_run_id and reference["run_id"] != expected_run_id:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_run_mismatch",
                    path=output_root_abs,
                    message="discovery task manifest does not belong to the authorized run",
                    task_id=task_id,
                    details={
                        "expected_run_id": expected_run_id,
                        "actual_run_id": reference["run_id"],
                    },
                )
            )

        manifest_path = os.path.abspath(
            os.path.join(output_root_abs, str(reference["path"]))
        )
        if not _is_path_inside_root(manifest_path, output_root_abs):
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_outside_output_root",
                    path=manifest_path,
                    message="discovery task manifest reference escapes output_root",
                    task_id=task_id,
                )
            )
            continue
        if not os.path.isfile(manifest_path):
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_missing",
                    path=manifest_path,
                    message="referenced discovery task manifest does not exist",
                    task_id=task_id,
                )
            )
            continue

        actual_hash = _sha256_file(manifest_path)
        if actual_hash != reference["sha256"]:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_hash_mismatch",
                    path=manifest_path,
                    message="task result hash does not match discovery task manifest",
                    task_id=task_id,
                    details={"expected": reference["sha256"], "actual": actual_hash},
                )
            )
        actual_bytes = os.path.getsize(manifest_path)
        if actual_bytes != reference["bytes"]:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_bytes_mismatch",
                    path=manifest_path,
                    message="task result byte count does not match discovery task manifest",
                    task_id=task_id,
                    details={"expected": reference["bytes"], "actual": actual_bytes},
                )
            )

        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest_payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            manifest_payload = None
        expected_source_id = task_id.split(":", 1)[0]
        manifest_scope_matches = (
            isinstance(manifest_payload, Mapping)
            and str(manifest_payload.get("source_id") or "").strip()
            == expected_source_id
            and str(manifest_payload.get("task_id") or "").strip() == task_id
            and str(manifest_payload.get("run_id") or "").strip()
            == str(reference["run_id"])
            and str(reference["source_id"]) == expected_source_id
            and str(reference["task_id"]) == task_id
        )
        if not manifest_scope_matches:
            issues.append(
                DownloadArchiveAuditIssue(
                    code="discovery_task_manifest_scope_mismatch",
                    path=manifest_path,
                    message="discovery task manifest does not belong to the current task",
                    task_id=task_id,
                )
            )

        discovery_dir = os.path.dirname(manifest_path)
        if (
            os.path.basename(manifest_path) != "task_manifest.json"
            or os.path.basename(discovery_dir) != "discovery"
        ):
            issues.append(
                DownloadArchiveAuditIssue(
                    code="invalid_discovery_task_manifest_location",
                    path=manifest_path,
                    message="discovery task manifest is not at discovery/task_manifest.json",
                    task_id=task_id,
                )
            )
            continue
        discovery_result = audit_discovery_evidence_root(
            os.path.dirname(discovery_dir),
            require_task_manifest=True,
        ).to_dict()
        discovery_results.append(discovery_result)
        for issue in discovery_result.get("issues", []):
            if not isinstance(issue, dict):
                continue
            issues.append(
                DownloadArchiveAuditIssue(
                    code=str(issue.get("code") or ""),
                    path=str(issue.get("path") or ""),
                    message=str(issue.get("message") or ""),
                    task_id=str(issue.get("task_id") or task_id),
                    details=(
                        issue.get("details")
                        if isinstance(issue.get("details"), dict)
                        else {}
                    ),
                )
            )

    root_results = [
        audit_download_archive_root(
            root,
            require_detail_sidecar=require_detail_sidecar,
        ).to_dict()
        for root in sorted(roots)
    ]
    for root_result in root_results:
        for issue in root_result.get("issues", []):
            if isinstance(issue, dict):
                issues.append(
                    DownloadArchiveAuditIssue(
                        code=str(issue.get("code") or ""),
                        path=str(issue.get("path") or ""),
                        message=str(issue.get("message") or ""),
                        task_id=str(issue.get("task_id") or ""),
                        details=issue.get("details") if isinstance(issue.get("details"), dict) else {},
                    )
                )

    if not roots and not discovery_results and not issues:
        return {}
    return {
        "ok": not issues,
        "root_count": len(root_results),
        "html_count": sum(int(item.get("html_count", 0) or 0) for item in root_results),
        "sidecar_count": sum(int(item.get("sidecar_count", 0) or 0) for item in root_results),
        "discovery_task_count": sum(
            int(item.get("task_count", 0) or 0) for item in discovery_results
        ),
        "discovery_manifest_count": sum(
            int(item.get("manifest_count", 0) or 0) for item in discovery_results
        ),
        "discovery_page_count": sum(
            int(item.get("page_count", 0) or 0) for item in discovery_results
        ),
        "issue_count": len(issues),
        "issues": [issue.to_dict() for issue in issues],
        "roots": root_results,
        "discovery_roots": discovery_results,
    }


def _audit_new_download_archive_roots(
    *,
    output_root: str,
    task_results: dict[str, dict[str, Any]],
    failed_task_specs: list[DownloadTaskSpec] | None = None,
    require_detail_sidecar: bool = False,
    required_discovery_task_ids: set[str] | None = None,
    expected_discovery_run_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return audit_download_run_archives(
        output_root=output_root,
        task_results=task_results,
        failed_task_specs=failed_task_specs,
        require_detail_sidecar=require_detail_sidecar,
        required_discovery_task_ids=required_discovery_task_ids,
        expected_discovery_run_ids=expected_discovery_run_ids,
    )


def prepare_download_session(
    request: DownloadRunRequest,
    *,
    logger: logging.Logger,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> PreparedDownloadSession:
    resolved_settings = settings or build_download_runner_settings(config_obj)
    try:
        normalized_request = normalize_date_range_args(request, logger=logger)
    except ValueError as exc:
        print(str(exc))
        logger.error(str(exc))
        raise DownloadRunnerError(str(exc)) from exc

    normalized_request.run_id = f"run-{uuid.uuid4().hex}"

    logger.info(
        "Run args: %s",
        json.dumps(_json_safe(vars(normalized_request)), ensure_ascii=False, sort_keys=True),
    )

    try:
        output_root = _validate_output_root(normalized_request, config_obj=config_obj, settings=resolved_settings)
    except DownloadRunnerError as exc:
        print(str(exc))
        logger.error(str(exc))
        raise

    tasks = resolve_tasks(
        config_obj,
        str(getattr(normalized_request, "exchange", "all")),
        str(getattr(normalized_request, "record_family", "listing")),
        str(getattr(normalized_request, "business_id", "all")),
        settings=resolved_settings,
    )
    if not tasks:
        print("No downloader task matched current filters.")
        print("Use --list-tasks to inspect available tasks.")
        logger.error("No downloader task matched current filters.")
        logger.error("Use --list-tasks to inspect available tasks.")
        raise DownloadRunnerError("no downloader task matched current filters")
    _reject_non_executable_tasks(tasks, logger=logger)
    if not ensure_runtime_dependencies(tasks, logger=logger):
        raise DownloadRunnerError("missing runtime dependency")

    return PreparedDownloadSession(
        settings=resolved_settings,
        request=normalized_request,
        output_root=output_root,
        tasks=tasks,
    )


def _run_download_session_core(
    args: object,
    *,
    logger: logging.Logger,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> DownloadRunResult:
    prepared = prepare_download_session(
        args,
        logger=logger,
        config_obj=config_obj,
        settings=settings,
    )
    resolved_settings = prepared.settings
    normalized_args = prepared.request
    output_root = prepared.output_root
    tasks = prepared.tasks

    any_failure = False
    totals = new_totals()
    total_typed_errors = TaskTypedErrorList()
    task_results: dict[str, dict[str, Any]] = {}
    failed_task_specs: list[DownloadTaskSpec] = []
    loaded_plan_map: dict[str, TaskSplitPlan] = {}
    generated_plan_map: dict[str, TaskSplitPlan] = {}
    task_progress_callback = getattr(normalized_args, "task_progress_callback", None)

    try:
        loaded_plan_map = load_requested_split_plans(normalized_args, logger=logger)
        chunk_state_ctx = prepare_chunk_state_context(
            normalized_args,
            logger=logger,
            default_dir=str(resolved_settings.download_chunk_state_dir),
        )
    except DownloadTaskFlowError as exc:
        raise DownloadRunnerError(str(exc)) from exc

    artifact_audit = (
        build_download_artifact_audit(
            config_obj,
            args=normalized_args,
            tasks=tasks,
        )
        if not getattr(normalized_args, "split_plan_only", False)
        else None
    )
    if artifact_audit is not None and artifact_audit.stale_count:
        message = (
            "Detected stale download records with missing local artifacts: "
            f"records={artifact_audit.stale_count}"
        )
        print(message)
        logger.warning(message)

    for index, spec in enumerate(tasks, start=1):
        if callable(task_progress_callback):
            task_progress_callback(
                {
                    "task_id": spec.task_id,
                    "task_label": _task_progress_label(spec),
                    "display_name": spec.display_name,
                    "task_index": index,
                    "task_total": len(tasks),
                    "status": "running",
                }
            )
        task_run = run_download_task(
            spec,
            args=normalized_args,
            logger=logger,
            output_root=output_root,
            loaded_plan_map=loaded_plan_map,
            chunk_state_ctx=chunk_state_ctx,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            run_downloader_with_prefetched=run_downloader_with_prefetched,
            parse_date_arg=parse_date_arg,
            artifact_audit=artifact_audit,
        )
        any_failure = any_failure or task_run.any_failure
        if task_run.any_failure:
            failed_task_specs.append(spec)
        merge_totals(totals, task_run.totals)
        total_typed_errors.extend(task_run.typed_errors)
        if task_run.generated_plan is not None:
            generated_plan_map[spec.task_id] = task_run.generated_plan
        if task_run.task_result is not None:
            task_results[spec.task_id] = task_run.task_result
        if callable(task_progress_callback):
            task_progress_callback(
                {
                    "task_id": spec.task_id,
                    "task_label": _task_progress_label(spec),
                    "display_name": spec.display_name,
                    "task_index": index,
                    "task_total": len(tasks),
                    "status": "failed" if task_run.any_failure else "done",
                    "summary": task_run.task_result.get("summary", {}) if task_run.task_result is not None else {},
                }
            )

    if len(tasks) > 1 and not getattr(normalized_args, "split_plan_only", False):
        print_aggregate_summary(totals, logger=logger)

    if getattr(normalized_args, "split_plan_only", False):
        print("Split plan generated. No download executed because --split-plan-only is set.")
        logger.info("Split plan generated. No download executed because --split-plan-only is set.")

    if getattr(normalized_args, "split_plan_file", None) and not getattr(normalized_args, "split_use_plan", False):
        try:
            save_split_plan_file(
                str(normalized_args.split_plan_file),
                tasks_to_plan=generated_plan_map,
                scope=_build_split_plan_scope(normalized_args),
            )
            print(f"Split plan saved: {normalized_args.split_plan_file}")
            logger.info("Split plan saved: %s", normalized_args.split_plan_file)
        except Exception as exc:  # noqa: BLE001
            any_failure = True
            total_typed_errors.append(
                collect_failed_error(
                    source_id=str(getattr(normalized_args, "exchange", "") or ""),
                    task_id="",
                    raw_reason=f"split-plan-save-failed: {exc}",
                )
            )
            print(f"Failed to save split plan file: {normalized_args.split_plan_file} ({exc})")
            logger.exception("Failed to save split plan file: %s", normalized_args.split_plan_file)

    expected_discovery_run_ids: dict[str, str] = {}
    for spec in tasks:
        if spec.record_family != "listing":
            continue
        loaded_reference = (
            loaded_plan_map[spec.task_id].discovery_task_manifest
            if spec.task_id in loaded_plan_map
            else None
        )
        expected_discovery_run_ids[spec.task_id] = str(
            (loaded_reference or {}).get("run_id")
            or getattr(normalized_args, "run_id", "")
        ).strip()

    archive_audit = (
        {}
        if getattr(normalized_args, "split_plan_only", False)
        else _audit_new_download_archive_roots(
            output_root=output_root,
            task_results=task_results,
            failed_task_specs=failed_task_specs,
            require_detail_sidecar=bool(getattr(normalized_args, "save_json", False)),
            required_discovery_task_ids={
                spec.task_id
                for spec in tasks
                if spec.record_family == "listing"
            },
            expected_discovery_run_ids=expected_discovery_run_ids,
        )
    )
    if archive_audit and not bool(archive_audit.get("ok")):
        any_failure = True
        total_typed_errors.append(
            archive_audit_failed_error(
                source_id=str(getattr(normalized_args, "exchange", "") or ""),
                task_id="archive_audit",
                raw_reason=f"download-archive-audit-failed: issues={archive_audit.get('issue_count', 0)}",
            )
        )
        message = f"Download archive audit failed: issues={archive_audit.get('issue_count', 0)}"
        print(message)
        logger.error(message)

    return DownloadRunResult(
        exit_code=1 if any_failure else 0,
        task_count=len(tasks),
        aggregate_summary=totals_to_summary_dict(totals, errors=len(total_typed_errors)),
        task_summaries=task_results,
        typed_errors=total_typed_errors,
        any_failure=any_failure,
        archive_audit=archive_audit,
    )


def run_download_request(
    request: DownloadRunRequest,
    *,
    logger: logging.Logger,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> DownloadRunResult:
    return _run_download_session_core(
        request,
        logger=logger,
        config_obj=config_obj,
        settings=settings,
    )


def run_download_cli_args(
    args: object,
    *,
    logger: logging.Logger,
    config_obj: object,
) -> DownloadRunResult:
    try:
        request = build_download_run_request(args, config_obj=config_obj)
    except ValueError as exc:
        print(str(exc))
        logger.error(str(exc))
        raise DownloadRunnerError(str(exc)) from exc
    settings = build_download_runner_settings(config_obj)
    return run_download_request(
        request,
        logger=logger,
        config_obj=config_obj,
        settings=settings,
    )


def run_download_session(
    args: object,
    *,
    logger: logging.Logger,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> DownloadRunResult:
    return _run_download_session_core(
        args,
        logger=logger,
        config_obj=config_obj,
        settings=settings,
    )
