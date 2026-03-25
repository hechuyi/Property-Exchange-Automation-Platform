"""Top-level downloader run orchestration helpers."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import logging
import os
import sys
from dataclasses import dataclass, replace
from typing import Any, Callable

from .download_models import DownloadRunResult, TaskSplitPlan
from .download_reporting import (
    merge_totals,
    new_totals,
    print_aggregate_summary,
    totals_to_summary_dict,
)
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
    project_type: str = "all"
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
    sse_ssl_fallback_insecure: bool = True
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
    output_root: str
    tasks: list[DownloadTaskSpec]


def build_download_runner_settings(config_obj: object) -> DownloadRunnerSettings:
    path_within_project_root = getattr(config_obj, "is_path_within_project_root", None)
    return DownloadRunnerSettings(
        auto_html_root=default_auto_html_root(config_obj),
        manual_html_root=str(getattr(config_obj, "HTML_FOLDER", "") or ""),
        project_root=str(getattr(config_obj, "PROJECT_ROOT", "") or ""),
        download_chunk_state_dir=str(getattr(config_obj, "DOWNLOAD_CHUNK_STATE_DIR", "") or ""),
        is_path_within_project_root=path_within_project_root if callable(path_within_project_root) else None,
        task_registry_settings=build_download_task_registry_settings(config_obj),
    )


def default_auto_html_root(config_obj: object) -> str:
    return str(getattr(config_obj, "AUTO_HTML_FOLDER", getattr(config_obj, "HTML_FOLDER", "")))


def clone_download_request(request: DownloadRunRequest, **overrides: object) -> DownloadRunRequest:
    return replace(request, **overrides)


def build_download_run_request(
    args: object,
    *,
    config_obj: object,
) -> DownloadRunRequest:
    defaults = getattr(config_obj, "DOWNLOADER_DEFAULTS", {})
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
        project_type=str(getattr(args, "project_type", defaults.get("project_type", "all"))),
        list_tasks=bool(getattr(args, "list_tasks", False)),
        output_root=str(getattr(args, "output_root", None) or default_auto_html_root(config_obj)),
        force_manual_root=bool(getattr(args, "force_manual_root", False)),
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        page_size=getattr(args, "page_size", None),
        max_pages=getattr(args, "max_pages", None),
        concurrency=int(getattr(args, "concurrency", defaults.get("concurrency", 1))),
        resume=bool(getattr(args, "resume", defaults.get("resume", True))),
        save_json=bool(getattr(args, "save_json", defaults.get("save_json", False))),
        sse_ssl_verify=bool(getattr(args, "sse_ssl_verify", defaults.get("sse_ssl_verify", True))),
        sse_ssl_fallback_insecure=bool(
            getattr(args, "sse_ssl_fallback_insecure", defaults.get("sse_ssl_fallback_insecure", True))
        ),
        sse_ca_bundle=getattr(args, "sse_ca_bundle", defaults.get("sse_ca_bundle")),
        log_dir=str(getattr(args, "log_dir", getattr(config_obj, "LOG_DIR", ""))),
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
        }
        for task_id in sorted(registry)
    ]


def resolve_tasks(
    config_obj: object,
    exchange_arg: str,
    project_type_arg: str,
    *,
    settings: DownloadRunnerSettings | None = None,
) -> list[DownloadTaskSpec]:
    tasks: list[DownloadTaskSpec] = []
    for spec in task_registry(config_obj, settings=settings).values():
        if exchange_arg != "all" and spec.exchange_code != exchange_arg:
            continue
        if project_type_arg != "all" and spec.project_type != project_type_arg:
            continue
        tasks.append(spec)
    return tasks


def task_progress_label(spec: DownloadTaskSpec) -> str:
    exchange_labels = {
        "sse": "上交所",
        "cbex": "北交所",
        "tpre": "天交所",
        "cquae": "重交所",
    }
    project_labels = {
        "physical_asset": "挂牌实物资产",
        "equity_transfer": "挂牌股权转让",
        "capital_increase": "挂牌增资扩股",
        "pre_disclosure": "挂牌预披露",
    }
    exchange_text = exchange_labels.get(spec.exchange_code, spec.exchange_code)
    project_text = project_labels.get(spec.project_type, spec.project_type)
    return f"{exchange_text} - {project_text}"


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
        f"\"{exe}\" -m pip install playwright && "
        f"\"{exe}\" -m playwright install chromium"
    )
    print(message)
    logger.error(message)
    return False


def parse_date_arg(raw: str | None, name: str) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        return dt.datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid {name}: {raw!r} (expected YYYY-MM-DD)") from exc


def normalize_date_range_args(args: object, *, logger: logging.Logger) -> None:
    start = parse_date_arg(getattr(args, "start_date", None), "start-date")
    end = parse_date_arg(getattr(args, "end_date", None), "end-date")
    if start is None or end is None:
        return
    if start <= end:
        return

    original_start = getattr(args, "start_date", None)
    original_end = getattr(args, "end_date", None)
    args.start_date = end.isoformat()
    args.end_date = start.isoformat()
    message = (
        "Detected reversed date range, auto-corrected by swapping: "
        f"start-date={original_start} end-date={original_end} -> "
        f"start-date={args.start_date} end-date={args.end_date}"
    )
    print(message)
    logger.warning(message)


def build_downloader(
    spec: DownloadTaskSpec,
    *,
    args: object,
    output_root: str,
    logger: logging.Logger,
    resume_override: bool | None = None,
):
    page_size = getattr(args, "page_size", None)
    resolved_page_size = page_size if page_size is not None else spec.default_page_size
    resume_enabled = getattr(args, "resume", False) if resume_override is None else bool(resume_override)
    downloader_kwargs = {
        "html_root": output_root,
        "page_size": resolved_page_size,
        "max_pages": getattr(args, "max_pages", None),
        "concurrency": max(1, int(getattr(args, "concurrency", 1))),
        "resume": resume_enabled,
        "save_json": getattr(args, "save_json", False),
        "logger": logger,
    }
    if spec.exchange_code == "sse":
        ca_bundle = str(getattr(args, "sse_ca_bundle", "") or "").strip() or None
        downloader_kwargs.update(
            {
                "ssl_verify": bool(getattr(args, "sse_ssl_verify", True)),
                "ssl_fallback_insecure": bool(getattr(args, "sse_ssl_fallback_insecure", True)),
                "ssl_ca_bundle": ca_bundle,
            }
        )
    item_saved_callback = getattr(args, "item_saved_callback", None)
    if item_saved_callback is not None:
        downloader_kwargs["item_saved_callback"] = item_saved_callback
    return spec.downloader_cls(**downloader_kwargs)


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
    try:
        return downloader.run(
            start_date=start_date,
            end_date=end_date,
            list_only=list_only,
            prefetched_candidates=prefetched_candidates,
        )
    except TypeError:
        if prefetched_candidates is not None:
            raise
        if list_only:
            raise
        return downloader.run(start_date=start_date, end_date=end_date)


def _validate_output_root(
    args: object,
    *,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> str:
    resolved_settings = settings or build_download_runner_settings(config_obj)
    output_root = os.path.abspath(str(args.output_root))
    manual_root = os.path.abspath(str(resolved_settings.manual_html_root or getattr(config_obj, "HTML_FOLDER", "")))
    if output_root == manual_root and not args.force_manual_root:
        message = (
            "Refusing to write into manual html root. "
            f"Use another --output-root (default: {resolved_settings.auto_html_root or default_auto_html_root(config_obj)}) "
            "or pass --force-manual-root."
        )
        raise DownloadRunnerError(message)
    path_within_project_root = resolved_settings.is_path_within_project_root
    within_project_root = bool(path_within_project_root(output_root)) if path_within_project_root else False
    if within_project_root:
        message = (
            "Refusing to write downloader output under project root in rebuilt data-root mode. "
            f"output_root={output_root} project_root={resolved_settings.project_root or getattr(config_obj, 'PROJECT_ROOT', '')}"
        )
        raise DownloadRunnerError(message)
    return output_root


def _build_split_plan_scope(args: object) -> dict[str, object]:
    return {
        "exchange": getattr(args, "exchange", None),
        "project_type": getattr(args, "project_type", None),
        "start_date": getattr(args, "start_date", None),
        "end_date": getattr(args, "end_date", None),
        "split_candidates": int(getattr(args, "split_candidates", 0)),
        "split_min_days": int(getattr(args, "split_min_days", 0)),
        "split_max_depth": int(getattr(args, "split_max_depth", 0)),
        "split_mode": str(getattr(args, "split_mode", "")),
    }


def prepare_download_session(
    request: DownloadRunRequest,
    *,
    logger: logging.Logger,
    config_obj: object,
    settings: DownloadRunnerSettings | None = None,
) -> PreparedDownloadSession:
    resolved_settings = settings or build_download_runner_settings(config_obj)
    try:
        normalize_date_range_args(request, logger=logger)
    except ValueError as exc:
        print(str(exc))
        logger.error(str(exc))
        raise DownloadRunnerError(str(exc)) from exc

    logger.info(
        "Run args: %s",
        json.dumps(_json_safe(vars(request)), ensure_ascii=False, sort_keys=True),
    )

    try:
        output_root = _validate_output_root(request, config_obj=config_obj, settings=resolved_settings)
    except DownloadRunnerError as exc:
        print(str(exc))
        logger.error(str(exc))
        raise

    tasks = resolve_tasks(
        config_obj,
        str(getattr(request, "exchange", "all")),
        str(getattr(request, "project_type", "all")),
        settings=resolved_settings,
    )
    if not tasks:
        print("No downloader task matched current filters.")
        print("Use --list-tasks to inspect available tasks.")
        logger.error("No downloader task matched current filters.")
        logger.error("Use --list-tasks to inspect available tasks.")
        raise DownloadRunnerError("no downloader task matched current filters")
    if not ensure_runtime_dependencies(tasks, logger=logger):
        raise DownloadRunnerError("missing runtime dependency")

    return PreparedDownloadSession(
        settings=resolved_settings,
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
    output_root = prepared.output_root
    tasks = prepared.tasks

    any_failure = False
    totals = new_totals()
    total_errors: list[str] = []
    task_results: dict[str, dict[str, Any]] = {}
    loaded_plan_map: dict[str, TaskSplitPlan] = {}
    generated_plan_map: dict[str, TaskSplitPlan] = {}
    task_progress_callback = getattr(args, "task_progress_callback", None)

    try:
        loaded_plan_map = load_requested_split_plans(args, logger=logger)
        chunk_state_ctx = prepare_chunk_state_context(
            args,
            logger=logger,
            default_dir=str(resolved_settings.download_chunk_state_dir),
        )
    except DownloadTaskFlowError as exc:
        raise DownloadRunnerError(str(exc)) from exc

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
            args=args,
            logger=logger,
            output_root=output_root,
            loaded_plan_map=loaded_plan_map,
            chunk_state_ctx=chunk_state_ctx,
            build_downloader=build_downloader,
            run_downloader=run_downloader,
            run_downloader_with_prefetched=run_downloader_with_prefetched,
            parse_date_arg=parse_date_arg,
        )
        any_failure = any_failure or task_run.any_failure
        merge_totals(totals, task_run.totals)
        total_errors.extend(task_run.errors)
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

    if len(tasks) > 1 and not getattr(args, "split_plan_only", False):
        print_aggregate_summary(totals, total_errors, logger=logger)

    if getattr(args, "split_plan_only", False):
        print("Split plan generated. No download executed because --split-plan-only is set.")
        logger.info("Split plan generated. No download executed because --split-plan-only is set.")

    if getattr(args, "split_plan_file", None) and not getattr(args, "split_use_plan", False):
        try:
            save_split_plan_file(
                str(args.split_plan_file),
                tasks_to_plan=generated_plan_map,
                scope=_build_split_plan_scope(args),
            )
            print(f"Split plan saved: {args.split_plan_file}")
            logger.info("Split plan saved: %s", args.split_plan_file)
        except Exception as exc:  # noqa: BLE001
            any_failure = True
            total_errors.append(f"split-plan-save-failed: {exc}")
            print(f"Failed to save split plan file: {args.split_plan_file} ({exc})")
            logger.exception("Failed to save split plan file: %s", args.split_plan_file)

    return DownloadRunResult(
        exit_code=1 if any_failure else 0,
        task_count=len(tasks),
        aggregate_summary=totals_to_summary_dict(totals, total_errors),
        task_summaries=task_results,
        errors=list(total_errors),
        any_failure=any_failure,
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
