"""Bounded live audit for artifact truth and downstream consumers.

This script intentionally uses a temporary PEAP app home. It may contact live
exchange endpoints, but it must not read from or write to the current user's
real PEAP workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import signal
import sqlite3
import tempfile
import time
import traceback
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.artifact_truth import resolve_artifact_evidence_verdict
from peap.business_runtime import iter_source_business_bindings
from peap.download_reporting import (
    append_synthetic_summary_failure_error,
    summary_discovery_task_manifest,
    summary_downloaded_this_run,
    summary_typed_errors,
)
from peap.download_runner import audit_download_run_archives
from peap.download_runtime import build_download_driver, run_download_driver
from peap.download_tasks import build_task_registry
from peap.export_evidence_policy import export_evidence_verdict_accepted
from peap.migrations import MigrationRunner
from peap.streaming_export import run_ready_export
from peap.streaming_ingest import StreamingIngestRunner
from peap.streaming_models import ExportRequest, ItemSavedPayload
from peap.streaming_store import StreamingStore
from peap_core.family_catalog import get_family_descriptor, list_family_descriptors
from peap_core.source_business_contract import get_source_business_requirement
from scripts._paths import is_forbidden_real_peap_path


class TaskTimeoutError(TimeoutError):
    pass


_TASK_PROCESS_POLL_SECONDS = 0.05
_TASK_TERMINATION_GRACE_SECONDS = 2.0


def _isolated_process_entry(
    connection: Connection,
    target: Any,
    target_args: tuple[object, ...],
) -> None:
    try:
        os.setsid()
        result = target(*target_args)
        payload: dict[str, object] = {"status": "ok", "result": result}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "trace_tail": traceback.format_exc().splitlines()[-3:],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        connection.send_bytes(encoded)
    finally:
        connection.close()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_isolated_process_group(
    process: multiprocessing.Process,
    *,
    process_group_id: int,
    grace_seconds: float,
) -> bool:
    group_signalled = False
    if process_group_id:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
            group_signalled = True
        except ProcessLookupError:
            pass

    if not group_signalled and process.is_alive():
        process.terminate()

    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while time.monotonic() < deadline:
        process.join(timeout=_TASK_PROCESS_POLL_SECONDS)
        group_alive = group_signalled and _process_group_exists(process_group_id)
        if not process.is_alive() and not group_alive:
            return True

    if group_signalled and _process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.is_alive():
        process.kill()
    process.join()
    reap_deadline = time.monotonic() + max(0.5, float(grace_seconds))
    while process_group_id and _process_group_exists(process_group_id):
        if time.monotonic() >= reap_deadline:
            return False
        time.sleep(_TASK_PROCESS_POLL_SECONDS)
    return True


def _isolated_process_error(
    error_type: str,
    error: str,
    *,
    child_exitcode: int | None,
) -> dict[str, object]:
    return {
        "status": "error",
        "error_type": error_type,
        "error": error[:300],
        "trace_tail": [],
        "child_exitcode": child_exitcode,
    }


def _run_in_isolated_process(
    target: Any,
    target_args: tuple[object, ...],
    *,
    timeout_seconds: float,
    termination_grace_seconds: float = _TASK_TERMINATION_GRACE_SECONDS,
) -> dict[str, object]:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_process_entry,
        args=(send_connection, target, target_args),
    )
    try:
        process.start()
    except Exception:
        receive_connection.close()
        send_connection.close()
        process.close()
        raise
    process_group_id = int(process.pid or 0)
    send_connection.close()

    deadline = (
        time.monotonic() + float(timeout_seconds)
        if float(timeout_seconds) > 0
        else None
    )
    encoded: bytes | None = None
    timed_out = False
    try:
        while True:
            if receive_connection.poll(0):
                try:
                    encoded = receive_connection.recv_bytes()
                except EOFError:
                    encoded = None
                break

            process.join(timeout=0)
            if not process.is_alive():
                if receive_connection.poll(_TASK_PROCESS_POLL_SECONDS):
                    try:
                        encoded = receive_connection.recv_bytes()
                    except EOFError:
                        encoded = None
                break

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                poll_seconds = min(_TASK_PROCESS_POLL_SECONDS, remaining)
            else:
                poll_seconds = _TASK_PROCESS_POLL_SECONDS
            if receive_connection.poll(poll_seconds):
                try:
                    encoded = receive_connection.recv_bytes()
                except EOFError:
                    encoded = None
                break

        while encoded is not None and process.is_alive():
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                process.join(timeout=min(_TASK_PROCESS_POLL_SECONDS, remaining))
            else:
                process.join(timeout=_TASK_PROCESS_POLL_SECONDS)

        if timed_out:
            _terminate_isolated_process_group(
                process,
                process_group_id=process_group_id,
                grace_seconds=termination_grace_seconds,
            )
            return _isolated_process_error(
                TaskTimeoutError.__name__,
                f"task exceeded timeout: {timeout_seconds:g}s",
                child_exitcode=process.exitcode,
            )

        process.join()
        child_exitcode = process.exitcode
        if encoded is None:
            return _isolated_process_error(
                "ChildProcessError",
                f"task process exited without a result: exitcode={child_exitcode}",
                child_exitcode=child_exitcode,
            )
        try:
            payload = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _isolated_process_error(
                "ChildProcessError",
                f"task process returned invalid JSON: {exc}",
                child_exitcode=child_exitcode,
            )
        if not isinstance(payload, dict) or payload.get("status") not in {"ok", "error"}:
            return _isolated_process_error(
                "ChildProcessError",
                "task process returned an invalid result envelope",
                child_exitcode=child_exitcode,
            )
        return payload
    finally:
        receive_connection.close()
        cleanup_ok = True
        try:
            if process.is_alive() or (
                process_group_id and _process_group_exists(process_group_id)
            ):
                cleanup_ok = _terminate_isolated_process_group(
                    process,
                    process_group_id=process_group_id,
                    grace_seconds=termination_grace_seconds,
                )
        finally:
            process.close()
        if not cleanup_ok:
            raise RuntimeError(
                f"task process group survived cleanup: pgid={process_group_id}"
            )


def _sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _under_real_peap(path_value: object) -> bool:
    return is_forbidden_real_peap_path(str(path_value or ""))


def _assert_not_real_paths(mapping: dict[str, str]) -> None:
    offenders = {key: value for key, value in mapping.items() if _under_real_peap(value)}
    if offenders:
        raise RuntimeError(f"refusing real PEAP paths: {offenders}")


def _summary_payload(summary: object) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(summary).items()
        if isinstance(value, (str, int, float, bool, type(None)))
    }


def _spec_source_id(spec: object) -> str:
    manifest = getattr(spec, "manifest", None)
    source_id = str(getattr(manifest, "source_id", "") or "").strip()
    if source_id:
        return source_id
    return str(getattr(spec, "exchange_code", "") or "").strip()


def _typed_error_payloads(errors: list[object]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for error in errors:
        if hasattr(error, "to_presenter_payload"):
            payload = error.to_presenter_payload()
            if isinstance(payload, dict):
                payloads.append(dict(payload))
                continue
        payloads.append({"error_code": "", "error_message": str(error or "")})
    return payloads


def _finalize_download_probe(
    summary: object,
    *,
    spec: object,
    archive_root: str,
) -> tuple[dict[str, object], list[str]]:
    append_synthetic_summary_failure_error(
        summary,
        source_id=_spec_source_id(spec),
        task_id=str(getattr(spec, "task_id", "") or ""),
    )
    typed_errors = summary_typed_errors(summary)
    downloaded = sorted(summary_downloaded_this_run(summary))
    discovery_task_manifest = summary_discovery_task_manifest(summary)
    task_id = str(getattr(spec, "task_id", "") or "")
    task_result: dict[str, object] = {
        "new_downloads": downloaded,
        "summary": {"saved": int(getattr(summary, "saved", 0) or 0)},
    }
    if discovery_task_manifest:
        task_result["discovery_task_manifest"] = discovery_task_manifest
    archive_audit = audit_download_run_archives(
        output_root=str(archive_root),
        task_results={task_id: task_result},
        failed_task_specs=[spec] if typed_errors else [],
        require_detail_sidecar=True,
        required_discovery_task_ids=(
            {task_id}
            if not typed_errors and str(getattr(spec, "record_family", "") or "") == "listing"
            else set()
        ),
    )
    archive_ok = not archive_audit or bool(archive_audit.get("ok"))
    ok = not typed_errors and archive_ok
    report: dict[str, object] = {
        "ok": ok,
        "summary": _summary_payload(summary),
        "downloaded_count": len(downloaded),
    }
    if typed_errors:
        report["typed_errors"] = _typed_error_payloads(list(typed_errors))
    if archive_audit:
        report["archive_audit"] = archive_audit
    return report, downloaded if ok else []


def _download_task_worker(
    spec: object,
    run_args: SimpleNamespace,
    output_root: str,
    start_date: str,
    end_date: str,
) -> dict[str, object]:
    logger = logging.getLogger("peap-live-truth-audit-task")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    runtime = build_download_driver(
        spec,
        args=run_args,
        output_root=output_root,
        logger=logger,
        resume_override=False,
    )
    summary = run_download_driver(
        runtime,
        start_date=start_date,
        end_date=end_date,
        list_only=False,
        prefetched_candidates=None,
    )
    report_fields, ingestable_downloads = _finalize_download_probe(
        summary,
        spec=spec,
        archive_root=output_root,
    )
    return {
        "report": report_fields,
        "ingestable_downloads": ingestable_downloads,
    }


def _run_download_task_isolated(
    spec: object,
    run_args: SimpleNamespace,
    *,
    output_root: str,
    start_date: str,
    end_date: str,
    timeout_seconds: float,
) -> dict[str, object]:
    return _run_in_isolated_process(
        _download_task_worker,
        (spec, run_args, output_root, start_date, end_date),
        timeout_seconds=timeout_seconds,
    )


def _sqlite_counts(db_path: Path) -> dict[str, object]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            "records": "SELECT COUNT(*) AS c FROM records",
            "record_revisions": "SELECT COUNT(*) AS c FROM record_revisions",
            "exports": "SELECT COUNT(*) AS c FROM exports",
            "export_cursor_records": "SELECT COUNT(*) AS c FROM export_cursor_records",
        }
        counts = {name: int(conn.execute(sql).fetchone()["c"]) for name, sql in tables.items()}
        states = {
            row["state"]: int(row["c"])
            for row in conn.execute(
                "SELECT state, COUNT(*) AS c FROM records GROUP BY state ORDER BY state"
            )
        }
        scopes = [
            {
                "record_family": row["record_family"],
                "business_id": row["business_id"],
                "exchange": row["exchange"],
                "count": int(row["c"]),
            }
            for row in conn.execute(
                """
                SELECT record_family, business_id, exchange, COUNT(*) AS c
                FROM records
                GROUP BY record_family, business_id, exchange
                ORDER BY record_family, business_id, exchange
                """
            )
        ]
    return {"counts": counts, "states": states, "scopes": scopes}


def _record_anomalies(records: list[dict[str, Any]]) -> dict[str, list[dict[str, object]]]:
    anomalies: dict[str, list[dict[str, object]]] = {
        "real_path": [],
        "missing_artifact": [],
        "invalid_family": [],
        "missing_scope_identity": [],
        "scope_mismatch": [],
        "ready_without_export_accepted_evidence": [],
        "raw_truth_source_gap": [],
    }
    for record in records:
        record_ref = {
            "record_id_hash": _sha(record.get("record_id")),
            "source_file_hash": _sha(record.get("source_file")),
            "archive_path_hash": _sha(record.get("archive_path")),
        }
        source_file = str(record.get("source_file") or "")
        archive_path = str(record.get("archive_path") or "")
        if _under_real_peap(source_file) or _under_real_peap(archive_path):
            anomalies["real_path"].append(record_ref)
        if archive_path and not os.path.isfile(archive_path):
            anomalies["missing_artifact"].append(record_ref)
        family = str(record.get("record_family") or "").strip()
        try:
            get_family_descriptor(family)
        except KeyError:
            anomalies["invalid_family"].append(record_ref | {"record_family": family})
        source_identity = record.get("source_identity_json")
        if not isinstance(source_identity, dict):
            source_identity = {}
        business_id = str(record.get("business_id") or "").strip()
        source_id = str(source_identity.get("source_id") or record.get("exchange") or "").strip()
        if not source_id or not business_id:
            anomalies["missing_scope_identity"].append(
                record_ref | {"has_source_id": bool(source_id), "has_business_id": bool(business_id)}
            )
        canonical = record.get("canonical_record")
        canonical_identity = canonical.get("business_identity") if isinstance(canonical, dict) else {}
        canonical_family = str(canonical.get("record_family") or "").strip() if isinstance(canonical, dict) else ""
        canonical_business = (
            str(canonical_identity.get("business_id") or "").strip()
            if isinstance(canonical_identity, dict)
            else ""
        )
        if canonical_family and canonical_family != family:
            anomalies["scope_mismatch"].append(record_ref | {"kind": "family"})
        if canonical_business and business_id and canonical_business != business_id:
            anomalies["scope_mismatch"].append(record_ref | {"kind": "business_id"})
        verdict = resolve_artifact_evidence_verdict(record)
        if str(record.get("state") or "") == "ready" and not export_evidence_verdict_accepted(verdict):
            anomalies["ready_without_export_accepted_evidence"].append(
                record_ref
                | {
                    "evidence_status": verdict.status,
                    "reason_code": verdict.reason_code,
                }
            )
        if not getattr(verdict, "authoritative_path", "") and not archive_path:
            anomalies["raw_truth_source_gap"].append(record_ref | {"evidence_status": verdict.status})
    return {key: value for key, value in anomalies.items() if value}


def _walk_forbidden(root: Path) -> list[str]:
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            if _under_real_peap(path):
                hits.append(str(path))
    return hits


def _build_env(root: Path) -> dict[str, str]:
    return {
        "PEAP_APP_HOME": str(root / "app-home"),
        "PEAP_DATA_ROOT": str(root / "data"),
        "PEAP_ARCHIVE_ROOT": str(root / "archive"),
        "PEAP_EXPORT_ROOT": str(root / "exports"),
        "PEAP_CACHE_DIR": str(root / "cache"),
        "PEAP_STREAMING_DB_PATH": str(root / "data" / "streaming_ingest.sqlite3"),
    }


def run_audit(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.temp_root or tempfile.mkdtemp(prefix="peap-live-truth-audit-")).absolute()
    env = _build_env(root)
    _assert_not_real_paths(env)

    task_reports: list[dict[str, object]] = []
    downloaded_items: list[tuple[str, str, str, str]] = []

    with patch.dict(os.environ, env, clear=False):
        config = AppConfig.from_env(project_root=os.getcwd())
        _assert_not_real_paths(
            {
                "ARCHIVE_ROOT": str(config.ARCHIVE_ROOT),
                "OUTPUT_EXCEL_DIR": str(config.OUTPUT_EXCEL_DIR),
                "STREAMING_DB_PATH": str(config.STREAMING_DB_PATH),
            }
        )
        MigrationRunner.run(config.STREAMING_DB_PATH)
        registry = build_task_registry(config)
        task_ids = sorted(registry)
        if args.task_id:
            requested = {item.strip() for item in args.task_id.split(",") if item.strip()}
            task_ids = [task_id for task_id in task_ids if task_id in requested]
        if args.source_id:
            requested_sources = {item.strip() for item in args.source_id.split(",") if item.strip()}
            task_ids = [task_id for task_id in task_ids if registry[task_id].exchange_code in requested_sources]
        if args.record_family:
            requested_families = {item.strip() for item in args.record_family.split(",") if item.strip()}
            task_ids = [task_id for task_id in task_ids if registry[task_id].record_family in requested_families]
        if args.business_id:
            requested_businesses = {item.strip() for item in args.business_id.split(",") if item.strip()}
            task_ids = [task_id for task_id in task_ids if registry[task_id].business_id in requested_businesses]
        if args.task_limit:
            task_ids = task_ids[: int(args.task_limit)]
        for task_id in task_ids:
            spec = registry[task_id]
            requirement = get_source_business_requirement(
                spec.exchange_code,
                spec.record_family,
                spec.business_id,
            )
            run_args = SimpleNamespace(
                page_size=args.page_size,
                max_pages=args.max_pages,
                concurrency=1,
                resume=False,
                save_json=True,
                sse_ssl_verify=True,
                sse_ca_bundle=None,
            )
            report: dict[str, object] = {
                "task_id": task_id,
                "scope_policy": requirement.scope_policy,
                "required_query_filters": dict(requirement.required_query_filters),
                "list_endpoint": requirement.list_endpoint,
            }
            try:
                outcome = _run_download_task_isolated(
                    spec,
                    run_args,
                    output_root=str(config.ARCHIVE_ROOT),
                    start_date=args.start_date,
                    end_date=args.end_date,
                    timeout_seconds=float(args.task_timeout_sec),
                )
                if outcome.get("status") != "ok":
                    report.update(
                        {
                            "ok": False,
                            "error_type": str(outcome.get("error_type") or "ChildProcessError"),
                            "error": str(outcome.get("error") or "task process failed")[:300],
                            "trace_tail": list(outcome.get("trace_tail") or []),
                            "child_exitcode": outcome.get("child_exitcode"),
                        }
                    )
                else:
                    task_result = outcome.get("result")
                    if not isinstance(task_result, dict):
                        raise TypeError("task process result must be a JSON object")
                    report_fields = task_result.get("report")
                    if not isinstance(report_fields, dict):
                        raise TypeError("task process report must be a JSON object")
                    ingestable_downloads = task_result.get("ingestable_downloads")
                    if not isinstance(ingestable_downloads, list) or not all(
                        isinstance(path, str) for path in ingestable_downloads
                    ):
                        raise TypeError("task process ingestable_downloads must be a string list")
                    for relative_path in ingestable_downloads:
                        downloaded_items.append(
                            (
                                str(Path(config.ARCHIVE_ROOT) / relative_path),
                                spec.exchange_code,
                                spec.record_family,
                                spec.business_id,
                            )
                        )
                    report.update(report_fields)
            except Exception as exc:  # noqa: BLE001
                report.update(
                    {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                        "trace_tail": traceback.format_exc().splitlines()[-3:],
                    }
                )
            task_reports.append(report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))

        store = StreamingStore(config.STREAMING_DB_PATH, auto_migrate=True)
        ingest_runner = StreamingIngestRunner(store=store, archive_root=str(config.ARCHIVE_ROOT))
        ingest_reports: list[dict[str, object]] = []
        for source_file, exchange, record_family, business_id in downloaded_items:
            if not source_file.endswith(".html"):
                continue
            try:
                result = ingest_runner.ingest(
                    ItemSavedPayload(
                        source_file=source_file,
                        exchange=exchange,
                        extra={
                            "source_id": exchange,
                            "record_family": record_family,
                            "business_id": business_id,
                        },
                    )
                )
                ingest_reports.append(
                    {
                        "ok": True,
                        "source_file_hash": _sha(source_file),
                        "state": result.get("state"),
                        "record_id_hash": _sha(result.get("record_id")),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                ingest_reports.append(
                    {
                        "ok": False,
                        "source_file_hash": _sha(source_file),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )

        records = store.iter_latest_records()
        anomalies = _record_anomalies(records)
        export_reports: list[dict[str, object]] = []
        for record_family in [descriptor.family_id for descriptor in list_family_descriptors()]:
            try:
                export_result = run_ready_export(
                    store,
                    ExportRequest(
                        output_dir=str(config.OUTPUT_EXCEL_DIR),
                        record_family=record_family,
                        requested_export_mode="full",
                        requested_state="all",
                        exchange="all",
                        retention_count=50,
                    ),
                )
                export_reports.append(
                    {
                        "record_family": record_family,
                        "ok": True,
                        "new_records": export_result.new_records,
                        "changed_records": export_result.changed_records,
                        "field_missing_blocked_records": export_result.field_missing_blocked_records,
                        "artifact_count": len(export_result.artifacts),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                export_reports.append(
                    {
                        "record_family": record_family,
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )

        service = AppService(config_obj=config)
        records_payload = service.list_records({"page": 1, "page_size": 20})
        review_payload = service.list_review_problems(
            {
                "page": 1,
                "page_size": 20,
                "state": "all",
                "record_family": "all",
                "business_id": "all",
                "exchange": "all",
                "problem_kind": "all",
                "keyword": "",
                "date_from": "",
                "date_to": "",
            }
        )
        service_report = {
            "records_total": records_payload.get("total"),
            "review_total": review_payload.get("total"),
            "records_rows": len(records_payload.get("records") or []),
            "review_rows": len(review_payload.get("records") or []),
        }

        report = {
            "temp_root": str(root),
            "env": env,
            "date_window": {"start_date": args.start_date, "end_date": args.end_date},
            "declared_task_count": len(list(iter_source_business_bindings())),
            "executed_task_count": len(task_reports),
            "task_reports": task_reports,
            "downloaded_items": len(downloaded_items),
            "ingest": {
                "attempted": len(ingest_reports),
                "ok": sum(1 for item in ingest_reports if item.get("ok")),
                "failed": sum(1 for item in ingest_reports if not item.get("ok")),
                "failures": [item for item in ingest_reports if not item.get("ok")][:20],
            },
            "db": _sqlite_counts(Path(config.STREAMING_DB_PATH)),
            "db_anomalies": anomalies,
            "exports": export_reports,
            "service": service_report,
            "forbidden_hits": _walk_forbidden(root),
        }
        report_path = root / "live_truth_audit_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report_path": str(report_path)}, ensure_ascii=False))
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-05-01")
    parser.add_argument("--end-date", default="2026-05-26")
    parser.add_argument("--page-size", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--task-limit", type=int, default=0)
    parser.add_argument("--task-id", default="", help="comma-separated task ids")
    parser.add_argument("--source-id", default="", help="comma-separated source ids")
    parser.add_argument("--record-family", default="", help="comma-separated record families")
    parser.add_argument("--business-id", default="", help="comma-separated business ids")
    parser.add_argument("--task-timeout-sec", type=int, default=180)
    parser.add_argument("--temp-root", default="")
    args = parser.parse_args()
    report = run_audit(args)
    failures = [
        item["task_id"]
        for item in report["task_reports"]  # type: ignore[index]
        if not item.get("ok")
    ]
    ingest_failed = bool(report["ingest"].get("failed"))  # type: ignore[index,union-attr]
    export_failures = [
        item
        for item in report["exports"]  # type: ignore[index]
        if not item.get("ok")
    ]
    db_anomalies = report["db_anomalies"]  # type: ignore[index]
    summary = {
        "temp_root": report["temp_root"],
        "executed_task_count": report["executed_task_count"],
        "downloaded_items": report["downloaded_items"],
        "ingest": report["ingest"],
        "db": report["db"],
        "db_anomaly_counts": {key: len(value) for key, value in db_anomalies.items()},
        "exports": report["exports"],
        "forbidden_hits": report["forbidden_hits"],
        "failed_tasks": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures or ingest_failed or export_failures or db_anomalies or report["forbidden_hits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
