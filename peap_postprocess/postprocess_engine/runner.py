"""Structured PPE runner reused by CLI and orchestration layers."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from peap_core.cli_support import close_cli_logger, setup_cli_logger
from peap_core.runtime import normalize_path

from .config import PPEConfig, load_config
from .contracts import ExecutionSummary
from .engine import PostProcessEngine

SYSTEM_DIR = os.path.normpath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
DEFAULT_CONFIG_PATH = os.path.join(SYSTEM_DIR, "ppe_config", "postprocess_external_template.json")


def _default_data_root() -> str:
    raw = str(os.environ.get("PEAP_DATA_ROOT") or "").strip().strip('"').strip("'")
    if raw:
        return normalize_path(raw)
    project_root = os.path.normpath(os.path.abspath(os.path.join(SYSTEM_DIR, "..")))
    return os.path.abspath(os.path.join(project_root, "..", "PEAP_DATA"))


DEFAULT_LOG_DIR = os.path.join(_default_data_root(), "logs", "postprocess")


@dataclass
class PostProcessRunRequest:
    config_path: str = DEFAULT_CONFIG_PATH
    mode: str | None = None
    log_dir: str = DEFAULT_LOG_DIR
    log_file: str | None = None
    verbose: bool = False
    skip_unresolved_list: bool = False


@dataclass
class PostProcessRunResult:
    exit_code: int
    log_file: str
    summary: dict[str, Any] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    audit_report: str = ""
    unresolved_output_file: str = ""
    export_exit_code: int = 0
    errors: list[str] = field(default_factory=list)


class PostProcessEngineLike(Protocol):
    def run(self, config: PPEConfig, *, mode_override: str | None = None) -> ExecutionSummary: ...


def setup_postprocess_logger(
    *,
    verbose: bool,
    log_dir: str,
    log_file: str | None,
) -> tuple[logging.Logger, str]:
    logger, resolved_log_file = setup_cli_logger(
        name="ppe",
        verbose=verbose,
        log_dir=log_dir,
        log_file=log_file,
        default_log_dir=DEFAULT_LOG_DIR,
        file_prefix="postprocess",
    )
    logger.info("PPE log file: %s", resolved_log_file)
    return logger, resolved_log_file


def _setup_postprocess_logger(
    verbose: bool,
    log_dir: str,
    log_file: str | None,
) -> tuple[logging.Logger, str]:
    return setup_postprocess_logger(
        verbose=verbose,
        log_dir=log_dir,
        log_file=log_file,
    )


def _build_postprocess_engine(logger: logging.Logger) -> PostProcessEngineLike:
    return PostProcessEngine(logger=logger)


def _summary_to_dict(summary: ExecutionSummary) -> dict[str, Any]:
    return {
        "mode": summary.mode,
        "discovered_files": summary.discovered_files,
        "processed_files": summary.processed_files,
        "processed_rows": summary.processed_rows,
        "applied_patches": summary.applied_patches,
        "findings": summary.findings,
        "failed_files": summary.failed_files,
    }


def _extract_output_path(text: str) -> str:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("output="):
            return line.split("=", 1)[1].strip()
    return ""


def _export_unresolved_query_list(config_path: str, logger: logging.Logger) -> tuple[int, str]:
    builder = os.path.join(SYSTEM_DIR, "build_type_unresolved_mapping_list.py")
    if not os.path.exists(builder):
        logger.warning("Skip unresolved-list export: builder not found: %s", builder)
        print(f"Skip unresolved-list export: builder not found: {builder}")
        return 0, ""

    cmd = [sys.executable, builder, "--config", config_path]
    logger.info("Run unresolved-list export: %s", cmd)
    result = subprocess.run(cmd, capture_output=True, text=True)

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    output_path = _extract_output_path(stdout)
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if result.returncode != 0:
        logger.warning("Unresolved-list export failed with code=%s", result.returncode)
    else:
        logger.info("Unresolved-list export done")
    return int(result.returncode), output_path


def build_postprocess_run_request(args: object) -> PostProcessRunRequest:
    return PostProcessRunRequest(
        config_path=str(getattr(args, "config", DEFAULT_CONFIG_PATH)),
        mode=getattr(args, "mode", None),
        log_dir=str(getattr(args, "log_dir", DEFAULT_LOG_DIR)),
        log_file=getattr(args, "log_file", None),
        verbose=bool(getattr(args, "verbose", False)),
        skip_unresolved_list=bool(getattr(args, "skip_unresolved_list", False)),
    )


def run_postprocess_request(
    request: PostProcessRunRequest,
    *,
    emit_console: bool = True,
    config_loader: Callable[[str], PPEConfig] = load_config,
    engine_factory: Callable[[logging.Logger], PostProcessEngineLike] = _build_postprocess_engine,
    export_unresolved_list: Callable[[str, logging.Logger], tuple[int, str]] = _export_unresolved_query_list,
    logger_factory: Callable[[bool, str, str | None], tuple[logging.Logger, str]] = _setup_postprocess_logger,
    close_logger: Callable[[logging.Logger], None] = close_cli_logger,
) -> PostProcessRunResult:
    config_path = str(request.config_path or DEFAULT_CONFIG_PATH)
    log_dir = str(request.log_dir or DEFAULT_LOG_DIR)

    logger, log_path = logger_factory(
        bool(request.verbose),
        log_dir,
        request.log_file,
    )
    try:
        if emit_console:
            print(f"PPE log file: {log_path}")

        logger.info(
            "Run context: cwd=%s pid=%s python=%s",
            os.getcwd(),
            os.getpid(),
            sys.version.split()[0],
        )
        logger.info(
            "Run request: %s",
            json.dumps(asdict(request), ensure_ascii=False, sort_keys=True),
        )

        try:
            config = config_loader(config_path)
        except Exception as exc:  # noqa: BLE001
            message = f"Config load failed: {exc}"
            if emit_console:
                print(message)
            logger.exception("Config load failed")
            return PostProcessRunResult(
                exit_code=2,
                log_file=log_path,
                errors=[message],
            )

        engine = engine_factory(logger)
        summary = engine.run(config, mode_override=request.mode)
        summary_dict = _summary_to_dict(summary)

        if emit_console:
            print(
                "PPE summary: "
                f"mode={summary.mode}, "
                f"discovered_files={summary.discovered_files}, "
                f"processed_files={summary.processed_files}, "
                f"processed_rows={summary.processed_rows}, "
                f"applied_patches={summary.applied_patches}, "
                f"findings={summary.findings}, "
                f"failed_files={summary.failed_files}"
            )
            if summary.output_files:
                print("Output files (first 10):")
                for output_file in summary.output_files[:10]:
                    print(f"- {output_file}")
            if summary.audit_report:
                print(f"Audit report: {summary.audit_report}")
            if summary.errors:
                print("Top errors:")
                for error in summary.errors[:10]:
                    print(f"- {error}")

        export_exit = 0
        unresolved_output_file = ""
        if not bool(request.skip_unresolved_list):
            export_exit, unresolved_output_file = export_unresolved_list(config_path, logger)

        exit_code = 0 if summary.failed_files == 0 else 1
        if exit_code == 0 and export_exit != 0:
            exit_code = 1
        logger.info(
            "PPE finished: exit_code=%s run_id=%s mode=%s errors=%s export_exit=%s",
            exit_code,
            summary.run_id,
            summary.mode,
            len(summary.errors),
            export_exit,
        )
        return PostProcessRunResult(
            exit_code=exit_code,
            log_file=log_path,
            audit_report=summary.audit_report,
            output_files=list(summary.output_files),
            unresolved_output_file=unresolved_output_file,
            export_exit_code=export_exit,
            summary=summary_dict,
            errors=list(summary.errors),
        )
    finally:
        close_logger(logger)


def run_postprocess_cli_args(
    args: object,
    *,
    emit_console: bool = True,
) -> PostProcessRunResult:
    return run_postprocess_request(
        build_postprocess_run_request(args),
        emit_console=emit_console,
    )


def postprocess_result_to_summary_payload(result: PostProcessRunResult) -> dict[str, object]:
    return {
        "kind": "postprocess",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exit_code": result.exit_code,
        "log_file": result.log_file,
        "audit_report": result.audit_report,
        "output_files": list(result.output_files),
        "unresolved_output_file": result.unresolved_output_file,
        "export_exit_code": result.export_exit_code,
        "summary": dict(result.summary),
        "errors": list(result.errors),
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_LOG_DIR",
    "PostProcessRunRequest",
    "PostProcessRunResult",
    "build_postprocess_run_request",
    "postprocess_result_to_summary_payload",
    "run_postprocess_request",
    "run_postprocess_cli_args",
    "setup_postprocess_logger",
]
