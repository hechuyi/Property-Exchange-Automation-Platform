from __future__ import annotations

import ast
import datetime as dt
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _module(relative_path: str) -> ast.Module:
    return ast.parse(_read(relative_path), filename=relative_path)


def _function_names(relative_path: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(_module(relative_path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _dataclass_fields(relative_path: str, class_name: str) -> set[str]:
    tree = _module(relative_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            }
    raise AssertionError(f"{class_name} not found in {relative_path}")


def _js_function_body(source: str, function_name: str) -> str:
    marker = f"function {function_name}"
    start = source.find(marker)
    assert start >= 0, f"{function_name} not found"
    parameter_start = source.find("(", start)
    assert parameter_start >= 0, f"{function_name} parameters not found"
    parameter_depth = 0
    parameter_end = -1
    for index in range(parameter_start, len(source)):
        char = source[index]
        if char == "(":
            parameter_depth += 1
        elif char == ")":
            parameter_depth -= 1
            if parameter_depth == 0:
                parameter_end = index
                break
    assert parameter_end >= 0, f"{function_name} parameters did not close"
    brace_start = source.find("{", parameter_end)
    assert brace_start >= 0, f"{function_name} body not found"
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start + 1 : index]
    raise AssertionError(f"{function_name} body did not close")


def _literal_string_collection(relative_path: str, name: str) -> set[str]:
    tree = _module(relative_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.args:
            value = value.args[0]
        if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
            return {element.value for element in value.elts if isinstance(element, ast.Constant) and isinstance(element.value, str)}
    return set()


def _downloader_sidecar_page_kinds() -> set[str]:
    kinds: set[str] = set()
    for path in (REPO_ROOT / "peap" / "downloaders").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if ".peap-evidence.json" not in source:
            continue
        kinds.update(re.findall(r'"page_kind"\s*:\s*"([^"]+)"', source))
    return kinds


def test_record_identity_has_single_core_owner() -> None:
    assert not (REPO_ROOT / "peap" / "record_identity.py").exists()
    assert (REPO_ROOT / "peap_core" / "record_identity.py").is_file()


def test_artifact_truth_verdict_stays_evidence_only() -> None:
    fields = _dataclass_fields("peap/artifact_truth.py", "EvidenceVerdict")

    assert fields == {
        "status",
        "logical_record_identity",
        "identity_confidence",
        "authoritative_path",
        "inspection_openable_path",
        "reason_code",
        "safe_evidence",
    }

    forbidden_decision_fields = {
        "action",
        "actions",
        "download_action",
        "downloadable",
        "export_action",
        "export_eligible",
        "exportable",
        "recoverable",
        "recovery_action",
        "reprocess_action",
        "reprocessable",
    }
    assert fields.isdisjoint(forbidden_decision_fields)

    artifact_truth_functions = _function_names("peap/artifact_truth.py")
    assert not any(
        name.startswith(("export_", "reprocess_", "recover_", "recovery_", "download_"))
        for name in artifact_truth_functions
    )


def test_artifact_truth_consumes_downloader_sidecar_page_kinds() -> None:
    downloader_page_kinds = _downloader_sidecar_page_kinds()
    consumed_page_kinds = _literal_string_collection(
        "peap/artifact_truth.py",
        "ARTIFACT_TRUTH_CONSUMED_SIDECAR_PAGE_KINDS",
    )

    assert downloader_page_kinds
    assert downloader_page_kinds <= consumed_page_kinds


def test_export_evidence_policy_is_the_only_export_acceptance_owner() -> None:
    policy_functions = _function_names("peap/export_evidence_policy.py")
    assert "export_evidence_verdict_accepted" in policy_functions

    consumer_imports = {
        "peap/streaming_export.py": "from .export_evidence_policy import export_evidence_verdict_accepted",
        "desktop_backend/services/records_service.py": (
            "from peap.export_evidence_policy import export_evidence_verdict_accepted"
        ),
    }
    for relative_path, expected_import in consumer_imports.items():
        source = _read(relative_path)
        assert expected_import in source
        assert "_evidence_verdict_accepted_for_export" not in _function_names(relative_path)


def test_download_collect_existing_skip_is_gated_by_verified_artifact_evidence() -> None:
    store_source = _read("peap/streaming_store.py")
    pipeline_source = _read("peap/streaming_daily_pipeline.py")

    assert "resolve_artifact_evidence_verdict(record, **overrides).status == \"verified\"" in store_source
    assert "require_existing_artifact=True" in pipeline_source

    project_codes_call = re.search(
        r"store\.list_existing_project_codes\((?P<body>.*?)\n\s*\)",
        pipeline_source,
        flags=re.S,
    )
    candidate_tokens_call = re.search(
        r"store\.list_existing_candidate_tokens\((?P<body>.*?)\n\s*\)",
        pipeline_source,
        flags=re.S,
    )
    assert project_codes_call is not None
    assert candidate_tokens_call is not None
    assert "require_existing_artifact=True" in project_codes_call.group("body")
    assert "require_existing_artifact=True" in candidate_tokens_call.group("body")


def test_frontend_contracts_do_not_derive_local_artifact_truth_from_legacy_fields() -> None:
    records_contract = _read("frontend/src/contracts/records.js")
    records_row_body = _js_function_body(records_contract, "normalizeRecordRow")
    assert "const evidenceVerdict = normalizeEvidenceVerdict(source.evidence_verdict)" in records_row_body
    assert "basename(evidenceVerdict.inspection_openable_path)" in records_row_body
    assert "source.has_local_artifact" not in records_row_body
    assert "source.local_artifact_name" not in records_row_body

    review_contract = _read("frontend/src/contracts/reviewProblems.js")
    review_row_body = _js_function_body(review_contract, "normalizeReviewProblemRow")
    assert "const evidenceVerdict = normalizeEvidenceVerdict(source.evidence_verdict)" in review_row_body
    assert "basename(evidenceVerdict.inspection_openable_path)" in review_row_body
    assert "source.has_local_artifact" not in review_row_body
    assert "source.local_artifact_name" not in review_row_body


def test_frontend_legacy_artifact_regression_tests_are_present() -> None:
    records_tests = _read("frontend/tests/recordsContract.test.mjs")
    assert (
        "normalizeRecordsResource does not create local artifact affordance from legacy fields "
        "without usable evidence verdict"
    ) in records_tests
    assert (
        "normalizeRecordsResource derives local artifact name from usable verdict path instead "
        "of legacy name"
    ) in records_tests

    review_tests = _read("frontend/tests/reviewProblemsContract.test.mjs")
    assert (
        "normalizeReviewProblemsResource does not infer local artifact presence from legacy fields "
        "without usable evidence verdict"
    ) in review_tests
    assert (
        "normalizeReviewProblemsResource derives source display from verdict path instead of "
        "legacy local artifact name"
    ) in review_tests


def test_non_split_stale_download_audit_disables_resume_skip(tmp_path) -> None:
    from peap.download_artifact_audit import (
        UNSAFE_DOWNLOAD_SKIP_EVIDENCE_STATUSES,
        DownloadArtifactAudit,
        StaleDownloadArtifact,
        TaskArtifactAudit,
    )
    from peap.download_task_flow import run_download_task
    from peap.download_tasks import build_task_registry
    from peap.downloaders.common import DownloadSummary

    assert {"stale_reference", "invalid_shell"} <= UNSAFE_DOWNLOAD_SKIP_EVIDENCE_STATUSES

    spec = build_task_registry()["sse:listing:physical_asset"]
    build_calls: list[dict[str, object]] = []

    def build_downloader(*args, **kwargs):
        build_calls.append(kwargs)
        return object()

    artifact = StaleDownloadArtifact(
        record_id="rec-truth-source-audit",
        task_id=spec.task_id,
        project_code="XM-TRUTH-SOURCE",
        project_name="truth source audit fixture",
        listing_date=dt.date(2026, 5, 8),
        source_file=str(tmp_path / "stale-reference.html"),
        archive_path=str(tmp_path / "stale-reference.html"),
        evidence_verdict={
            "status": "stale_reference",
            "reason_code": "authoritative_artifact_missing",
        },
    )
    audit = DownloadArtifactAudit(
        by_task_id={
            spec.task_id: TaskArtifactAudit(
                task_id=spec.task_id,
                stale_records=(artifact,),
                dated_stale_records={dt.date(2026, 5, 8): (artifact,)},
            )
        }
    )

    result = run_download_task(
        spec,
        args=SimpleNamespace(
            auto_split=False,
            resume=True,
            start_date="2026-05-01",
            end_date="2026-05-31",
        ),
        logger=logging.getLogger("truth_source_contract_test"),
        output_root=str(tmp_path),
        loaded_plan_map={},
        chunk_state_ctx=None,
        build_downloader=build_downloader,
        run_downloader=lambda *args, **kwargs: DownloadSummary(saved=0),
        run_downloader_with_prefetched=lambda *args, **kwargs: None,
        parse_date_arg=lambda raw, _name: raw,
        artifact_audit=audit,
    )

    assert result.any_failure is False
    assert build_calls
    assert build_calls[0]["resume_override"] is False


def test_split_fast_zero_candidate_stale_audit_runs_with_resume_disabled(tmp_path) -> None:
    from peap.download_artifact_audit import StaleDownloadArtifact, TaskArtifactAudit
    from peap.download_execution import execute_split_task
    from peap.download_models import (
        SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
        DateChunk,
        SplitPlanResolvedBasis,
    )
    from peap.download_tasks import build_task_registry
    from peap.downloaders.common import DownloadSummary

    spec = build_task_registry()["sse:listing:physical_asset"]
    build_calls: list[dict[str, object]] = []
    run_calls: list[dict[str, object]] = []

    def build_downloader(*args, **kwargs):
        build_calls.append(kwargs)
        return object()

    def run_with_prefetched(*args, **kwargs):
        run_calls.append(kwargs)
        return DownloadSummary(saved=1)

    artifact = StaleDownloadArtifact(
        record_id="rec-split-zero-stale",
        task_id=spec.task_id,
        project_code="XM-SPLIT-ZERO-STALE",
        project_name="split zero stale audit fixture",
        listing_date=dt.date(2026, 5, 8),
        source_file=str(tmp_path / "split-zero-stale.html"),
        archive_path=str(tmp_path / "split-zero-stale.html"),
        evidence_verdict={
            "status": "stale_reference",
            "reason_code": "authoritative_artifact_missing",
        },
    )

    result = execute_split_task(
        spec=spec,
        args=SimpleNamespace(split_mode="fast", resume=True),
        logger=logging.getLogger("truth_source_contract_test"),
        output_root=str(tmp_path),
        chunks=[
            DateChunk(
                start=dt.date(2026, 5, 1),
                end=dt.date(2026, 5, 31),
                estimated_candidates=0,
            )
        ],
        candidate_entries=[],
        resolved_basis=SplitPlanResolvedBasis(
            date_fields=("listing_date",),
            unresolved_candidate_policy=SPLIT_PLAN_UNRESOLVED_POLICY_SKIP,
        ),
        build_downloader=build_downloader,
        run_downloader_with_prefetched=run_with_prefetched,
        task_chunk_state=None,
        chunk_state_ctx=None,
        artifact_audit=TaskArtifactAudit(
            task_id=spec.task_id,
            stale_records=(artifact,),
            dated_stale_records={dt.date(2026, 5, 8): (artifact,)},
        ),
    )

    assert result.any_failure is False
    assert result.totals["saved"] == 1
    assert build_calls
    assert run_calls
    assert build_calls[0]["resume_override"] is False


def test_records_status_detail_blocks_raw_finding_and_error_text_from_dto_and_ui() -> None:
    from desktop_backend.services.records_service import RecordsService

    service = RecordsService(
        repository=object(),
        db_path="/tmp/test-streaming.sqlite3",
    )

    with (
        patch(
            "desktop_backend.services.records_service._build_record_evidence_verdict",
            return_value={
                "status": "undeclared",
                "logical_record_identity": "",
                "identity_confidence": "unresolved",
                "authoritative_path": "",
                "inspection_openable_path": "",
                "reason_code": "artifact_path_not_declared",
                "safe_evidence": {},
            },
        ),
        patch(
            "desktop_backend.services.records_service._build_record_top_level_fields",
            return_value={"seller": "", "price": ""},
        ),
    ):
        error_row = service.row_from_record(
            {
                "record_id": "rec-raw-error",
                "project_code": "XM-RAW-ERROR",
                "project_name": "raw error fixture",
                "project_type": "physical_asset",
                "business_id": "physical_asset",
                "record_family": "listing",
                "exchange": "shanghai",
                "listing_date": "2026-05-08",
                "state": "parse_failed",
                "last_error_message": "UNTRUSTED_EXTERNAL_TEXT",
                "archive_path": "",
                "source_file": "",
                "updated_at": "2026-05-08T10:00:00",
            },
            values={"项目类型": "实物资产", "交易所": "上交所"},
            local_artifact_path="",
        )
        finding_row = service.row_from_record(
            {
                "record_id": "rec-raw-finding",
                "project_code": "XM-RAW-FINDING",
                "project_name": "raw finding fixture",
                "project_type": "physical_asset",
                "business_id": "physical_asset",
                "record_family": "listing",
                "exchange": "shanghai",
                "listing_date": "2026-05-08",
                "state": "field_missing",
                "findings": [
                    {
                        "severity": "warn",
                        "type": "export_field_missing",
                        "message": "UNTRUSTED_EXTERNAL_TEXT",
                    }
                ],
                "archive_path": "",
                "source_file": "",
                "updated_at": "2026-05-08T10:01:00",
            },
            values={"项目类型": "实物资产", "交易所": "上交所"},
            local_artifact_path="",
        )

    assert error_row["status_detail"] == "解析失败，暂不能进入录入"
    assert finding_row["status_detail"] == "导出必填字段缺失，暂不能进入导出"
    assert "UNTRUSTED_EXTERNAL_TEXT" not in error_row["status_detail"]
    assert "UNTRUSTED_EXTERNAL_TEXT" not in finding_row["status_detail"]

    records_contract = _read("frontend/src/contracts/records.js")
    records_row_body = _js_function_body(records_contract, "normalizeRecordRow")
    assert "status_detail: String(source.status_detail || \"\").trim()" in records_row_body
    assert "source.last_error_message" not in records_row_body
    assert "source.findings" not in records_row_body
