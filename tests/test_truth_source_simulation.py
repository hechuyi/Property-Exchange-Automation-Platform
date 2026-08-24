from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from desktop_backend.review_problem_contract import normalize_review_problem_query
from peap.download_artifact_audit import build_download_artifact_audit
from peap.download_tasks import build_task_registry
from peap.migrations import MigrationRunner
from peap.streaming_models import IngestedRecord, PostProcessFinding

REPO_ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "UNTRUSTED_EXTERNAL_TEXT"


class _FakeRuntimeDependencies:
    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/peap-truth-simulation/browser-cache",
            "driver_executable": "/tmp/peap-truth-simulation/driver",
            "driver_cli": "/tmp/peap-truth-simulation/cli.js",
            "executable_path": "/tmp/peap-truth-simulation/chrome",
            "installed": True,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return self.get_browser_runtime_status(browser_name=browser_name) | {"returncode": 0}


def _write_file(path: Path, text: str = "safe local fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_invalid_shell_sidecar(path: Path, *, project_code: str, project_name: str) -> None:
    content_sha256 = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = {
        "schema_version": 1,
        "page_kind": "invalid_shell",
        "content_sha256": content_sha256,
        "identity_hints": {
            "project_code_hash": "sha256:" + hashlib.sha256(project_code.encode("utf-8")).hexdigest(),
            "project_name_hash": "sha256:" + hashlib.sha256(project_name.encode("utf-8")).hexdigest(),
        },
        "source_url_hash": "sha256:1111",
        "final_url_hash": "sha256:2222",
    }
    path.with_name(path.name + ".peap-evidence.json").write_text(
        json.dumps(sidecar, ensure_ascii=False),
        encoding="utf-8",
    )


def _canonical_record(*, family: str = "listing", business_id: str = "physical_asset") -> dict[str, object]:
    return {
        "record_family": family,
        "business_identity": {
            "record_family": family,
            "business_id": business_id,
            "raw_business_label": "实物资产",
        },
        "canonical_fields": {},
    }


def _source_identity(*, family: str = "listing", business_id: str = "physical_asset", exchange: str = "sse", project_code: str) -> dict[str, str]:
    return {
        "record_family": family,
        "business_id": business_id,
        "source_id": exchange,
        "exchange": exchange,
        "project_code": project_code,
    }


def _record(
    *,
    record_id: str,
    state: str,
    archive_path: Path | str,
    project_code: str | None = None,
    project_name: str | None = None,
    project_type: str = "实物资产",
    source_identity: dict[str, str] | None = None,
    canonical_record: dict[str, object] | None = None,
    findings: list[PostProcessFinding] | None = None,
    postprocess_payload: dict[str, object] | None = None,
) -> IngestedRecord:
    resolved_project_code = project_code or f"CODE-{record_id}"
    resolved_project_name = project_name or f"项目-{record_id}"
    archive_text = str(archive_path)
    return IngestedRecord(
        record_id=record_id,
        revision_hash=f"hash-{record_id}",
        project_code=resolved_project_code,
        project_name=resolved_project_name,
        project_type=project_type,
        exchange="sse",
        listing_date="2026-05-01",
        state=state,
        source_file=archive_text,
        archive_path=archive_text,
        parser_payload={"项目编号": resolved_project_code, "项目名称": resolved_project_name},
        postprocess_payload=postprocess_payload
        or {"项目编号": resolved_project_code, "项目名称": resolved_project_name, "项目类型": project_type},
        findings=findings or [],
        record_family="listing",
        source_identity=source_identity or {},
        canonical_record=canonical_record or {},
    )


def _make_service(tmp_path: Path) -> tuple[AppService, dict[str, str]]:
    app_home = tmp_path / "app-home"
    data_root = tmp_path / "data"
    archive_root = tmp_path / "archive"
    export_root = tmp_path / "export"
    cache_dir = tmp_path / "cache"
    db_path = data_root / "streaming.sqlite3"
    env = {
        "PEAP_APP_HOME": str(app_home),
        "PEAP_DATA_ROOT": str(data_root),
        "PEAP_ARCHIVE_ROOT": str(archive_root),
        "PEAP_EXPORT_ROOT": str(export_root),
        "PEAP_CACHE_DIR": str(cache_dir),
        "PEAP_STREAMING_DB_PATH": str(db_path),
    }
    with patch.dict(os.environ, env, clear=False):
        config = AppConfig.from_env(project_root=str(REPO_ROOT))
    MigrationRunner.run(config.STREAMING_DB_PATH)
    return AppService(config_obj=config, runtime_dependencies=_FakeRuntimeDependencies()), env


def _normalize_with_frontend_contracts(payloads: dict[str, object]) -> dict[str, object]:
    script = """
import fs from "node:fs";
import { normalizeRecordsResource } from "./frontend/src/contracts/records.js";
import { normalizeReviewProblemsResource } from "./frontend/src/contracts/reviewProblems.js";
import { normalizeMappingsResource } from "./frontend/src/contracts/mappings.js";
import {
  normalizeExportHistoryActionResult,
  normalizeExportHistoryCollection,
  normalizeExportHistoryDetail,
} from "./frontend/src/contracts/exportHistory.js";

const payloads = JSON.parse(fs.readFileSync(0, "utf8"));
const normalized = {
  records: normalizeRecordsResource(payloads.records),
  review: normalizeReviewProblemsResource(payloads.review),
  mappings: normalizeMappingsResource(payloads.mappings),
  exportHistory: {
    list: normalizeExportHistoryCollection(payloads.exportHistory.list),
    liveDetail: normalizeExportHistoryDetail(payloads.exportHistory.liveDetail),
    retainedDetail: normalizeExportHistoryDetail(payloads.exportHistory.retainedDetail),
    openAction: normalizeExportHistoryActionResult(payloads.exportHistory.openAction),
    downloadAction: normalizeExportHistoryActionResult(payloads.exportHistory.downloadAction),
  },
};
process.stdout.write(JSON.stringify(normalized));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(payloads, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_realistic_truth_source_simulation_fixture_keeps_owner_outputs_consistent(tmp_path: Path) -> None:
    service, env = _make_service(tmp_path)
    archive_root = Path(env["PEAP_ARCHIVE_ROOT"])

    verified_path = _write_file(archive_root / "verified.html")
    stale_path = archive_root / "missing-stale.html"
    invalid_shell_path = _write_file(archive_root / "invalid-shell.html")
    _write_invalid_shell_sidecar(
        invalid_shell_path,
        project_code="CODE-invalid-shell",
        project_name="项目-invalid-shell",
    )
    present_unverified_path = _write_file(archive_root / "present-unverified.html")
    identity_mismatch_path = _write_file(archive_root / "identity-mismatch.html")
    field_missing_acked_path = _write_file(archive_root / "field-missing-acked.html")
    field_missing_unacked_path = _write_file(archive_root / "field-missing-unacked.html")
    review_path = _write_file(archive_root / "review.html")
    mapping_path = _write_file(archive_root / "mapping.html")
    export_root = Path(env["PEAP_EXPORT_ROOT"])
    export_live_path = _write_file(export_root / "live-export.xlsx", "xlsx")
    export_retained_path = _write_file(export_root / "retained-export.xlsx", "xlsx")
    export_download_dir = Path(env["PEAP_CACHE_DIR"]) / "download-export-history"

    service.store.upsert_record(
        _record(
            record_id="verified",
            state="ready",
            archive_path=verified_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-verified"),
        )
    )
    service.store.upsert_record(
        _record(
            record_id="stale-reference",
            state="ready",
            archive_path=stale_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-stale-reference"),
        )
    )
    service.store.upsert_record(
        _record(
            record_id="invalid-shell",
            state="ready",
            archive_path=invalid_shell_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-invalid-shell"),
        )
    )
    service.store.upsert_record(
        _record(
            record_id="present-unverified",
            state="ready",
            archive_path=present_unverified_path,
            project_type="未知",
        )
    )
    service.store.upsert_record(
        _record(
            record_id="identity-mismatch",
            state="ready",
            archive_path=identity_mismatch_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-from-source"),
        )
    )
    service.store.upsert_record(
        _record(
            record_id="field-missing-acked",
            state="field_missing",
            archive_path=field_missing_acked_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-field-missing-acked"),
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="canonical_field_missing",
                    message=SENTINEL,
                    evidence={"missing_fields": [{"field": "project_name", "label": "项目名称"}], "raw_html": SENTINEL},
                )
            ],
        )
    )
    service.store.acknowledge_field_missing(
        "field-missing-acked",
        missing_fields=[{"field": "project_name", "label": "项目名称", "kind": "canonical"}],
    )
    service.store.upsert_record(
        _record(
            record_id="field-missing-unacked",
            state="field_missing",
            archive_path=field_missing_unacked_path,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-field-missing-unacked"),
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="export_field_missing",
                    message=SENTINEL,
                    evidence={"missing_fields": ["挂牌价格"], "ocr_text": SENTINEL},
                )
            ],
        )
    )
    service.store.upsert_record(
        _record(
            record_id="review-raw-label",
            state="pending_review",
            archive_path=review_path,
            project_type="未知",
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message=SENTINEL,
                    evidence={"reason_code": "unrecognized_business", "raw_business_label": SENTINEL},
                )
            ],
        )
    )
    service.store.upsert_record(
        _record(
            record_id="mapping-raw-label",
            state="pending_mapping",
            archive_path=mapping_path,
            project_type=SENTINEL,
            postprocess_payload={
                "项目编号": "CODE-mapping-raw-label",
                "项目名称": "项目-mapping-raw-label",
                "项目类型": SENTINEL,
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"], "raw_business_label": SENTINEL},
                )
            ],
        )
    )

    records_payload = service.list_records({"state": "all", "page_size": 200})
    review_payload = service.list_review_problems(normalize_review_problem_query({}))
    mapping_payload = service.list_pending_mappings()

    service.store.mark_exported(
        export_id="exp-a-retained",
        cursor_id="cursor-truth-source",
        requested_export_mode="incremental",
        date_from="2026-05-01",
        date_to="2026-05-31",
        project_type="all",
        output_dir=str(export_root),
        summary={
            "artifacts": [str(export_retained_path)],
            "requested_export_mode": "incremental",
            "revision_watermark": 7,
            "retention_count": 1,
            "legacy_openability_text": SENTINEL,
        },
        records=[],
        retention_count=1,
    )
    service.store.mark_exported(
        export_id="exp-z-live",
        cursor_id="cursor-truth-source",
        requested_export_mode="incremental",
        date_from="2026-05-01",
        date_to="2026-05-31",
        project_type="all",
        output_dir=str(export_root),
        summary={
            "artifacts": [str(export_live_path)],
            "requested_export_mode": "incremental",
            "revision_watermark": 8,
            "retention_count": 1,
            "legacy_retention_text": SENTINEL,
        },
        records=[],
        retention_count=1,
    )
    export_history_list_payload = service.list_exports_history(limit=10)
    export_history_live_detail_payload = service.get_export_history_detail("exp-z-live")
    export_history_retained_detail_payload = service.get_export_history_detail("exp-a-retained")
    export_history_open_payload = service.open_export_history("exp-a-retained")
    export_history_download_payload = service.download_export_history(
        "exp-a-retained",
        output_dir=str(export_download_dir),
    )

    audit_missing_path = archive_root / "download-audit-missing.html"
    service.store.upsert_record(
        _record(
            record_id="download-audit-stale-reference",
            state="ready",
            archive_path=audit_missing_path,
            project_code="CODE-download-audit",
            project_name=SENTINEL,
            canonical_record=_canonical_record(),
            source_identity=_source_identity(project_code="CODE-download-audit"),
        )
    )
    download_audit = build_download_artifact_audit(
        service.config,
        args=SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            start_date="2026-05-01",
            end_date="2026-05-31",
            dry_run=True,
        ),
        tasks=[build_task_registry()["sse:listing:physical_asset"]],
    )
    download_audit_payload = download_audit.to_dict()

    backend_payloads = {
        "records": records_payload,
        "review": review_payload,
        "mappings": mapping_payload,
        "exportHistory": {
            "list": export_history_list_payload,
            "liveDetail": export_history_live_detail_payload,
            "retainedDetail": export_history_retained_detail_payload,
            "openAction": export_history_open_payload,
            "downloadAction": export_history_download_payload,
        },
        "downloadAudit": download_audit_payload,
    }

    encoded_backend = json.dumps(backend_payloads, ensure_ascii=False, sort_keys=True)
    assert SENTINEL not in encoded_backend

    rows_by_id = {row["record_id"]: row for row in records_payload["rows"]}
    assert rows_by_id["verified"]["evidence_verdict"]["status"] == "verified"
    assert rows_by_id["stale-reference"]["evidence_verdict"]["status"] == "stale_reference"
    assert rows_by_id["invalid-shell"]["evidence_verdict"]["status"] == "invalid_shell"
    assert rows_by_id["present-unverified"]["evidence_verdict"]["status"] == "present_unverified"
    assert rows_by_id["identity-mismatch"]["evidence_verdict"]["status"] == "identity_mismatch"
    assert rows_by_id["field-missing-acked"]["field_missing_acknowledgement"]["acknowledged"] is True
    assert rows_by_id["field-missing-acked"]["attention"]["suppressed"] is True
    assert rows_by_id["field-missing-unacked"]["field_missing_acknowledgement"]["acknowledged"] is False
    assert rows_by_id["field-missing-unacked"]["attention"]["requires_attention"] is True

    unsafe_ready_codes = service.store.list_existing_project_codes(
        states=["ready"],
        record_family="listing",
        business_id="physical_asset",
        source_id="sse",
        require_existing_artifact=True,
    )
    assert unsafe_ready_codes == {"CODE-VERIFIED"}

    review_rows_by_id = {row["record_id"]: row for row in review_payload["rows"]}
    assert review_rows_by_id["review-raw-label"]["raw_business_label"] == "项目类型未识别"
    assert "raw_business_label" not in review_rows_by_id["review-raw-label"]["evidence"]

    mapping_items = [
        item
        for section in mapping_payload["sections"]
        for item in section["items"]
    ]
    mapping_item = next(item for item in mapping_items if item["record_id"] == "mapping-raw-label")
    assert mapping_item["business_label"] == "未识别项目类型"
    assert "raw_business_label" not in mapping_item

    export_rows_by_id = {
        row["export_id"]: row
        for row in export_history_list_payload["rows"]
    }
    assert export_rows_by_id["exp-z-live"]["openable"] is True
    assert export_rows_by_id["exp-z-live"]["retention_status"] == "available"
    assert export_rows_by_id["exp-a-retained"]["openable"] is False
    assert export_rows_by_id["exp-a-retained"]["pruned_by_retention"] is True
    assert export_rows_by_id["exp-a-retained"]["retention_status"] == "pruned_by_retention"
    assert export_history_live_detail_payload["existing_artifacts"] == [str(export_live_path)]
    assert export_history_live_detail_payload["openable"] is True
    assert export_history_retained_detail_payload["artifacts"] == [str(export_retained_path)]
    assert export_history_retained_detail_payload["existing_artifacts"] == []
    assert export_history_retained_detail_payload["openable"] is False
    assert export_history_retained_detail_payload["pruned_by_retention"] is True
    assert export_history_retained_detail_payload["retention_status"] == "pruned_by_retention"
    assert export_history_open_payload["opened"] is False
    assert export_history_download_payload["downloaded"] is False
    assert not export_download_dir.exists()

    audit_task = download_audit_payload["tasks"]["sse:listing:physical_asset"]
    audit_sample = next(
        sample
        for sample in audit_task["samples"]
        if sample["record_id"] == "download-audit-stale-reference"
    )
    assert audit_sample["evidence_verdict"]["status"] == "stale_reference"
    assert audit_sample["evidence_verdict"]["reason_code"] == "authoritative_artifact_missing"

    frontend_payloads = _normalize_with_frontend_contracts(backend_payloads)
    encoded_frontend = json.dumps(frontend_payloads, ensure_ascii=False, sort_keys=True)
    assert SENTINEL not in encoded_frontend
    frontend_rows_by_id = {row["record_id"]: row for row in frontend_payloads["records"]["rows"]}
    assert frontend_rows_by_id["verified"]["has_local_artifact"] is True
    assert frontend_rows_by_id["verified"]["local_artifact_name"] == "verified.html"
    assert frontend_rows_by_id["stale-reference"]["has_local_artifact"] is False
    assert frontend_rows_by_id["stale-reference"]["local_artifact_name"] == ""
    assert frontend_rows_by_id["identity-mismatch"]["evidence_status"] == "identity_mismatch"
    assert frontend_payloads["exportHistory"]["list"]["rows"][0]["export_id"] == "exp-z-live"
    assert frontend_payloads["exportHistory"]["liveDetail"]["openable"] is True
    assert frontend_payloads["exportHistory"]["retainedDetail"]["openable"] is False
    assert frontend_payloads["exportHistory"]["retainedDetail"]["retention_status"] == "pruned_by_retention"
    assert frontend_payloads["exportHistory"]["openAction"]["opened"] is False
    assert frontend_payloads["exportHistory"]["downloadAction"]["downloaded"] is False

    with patch.dict(os.environ, {key: "" for key in env}, clear=False):
        for key in env:
            assert os.environ.get(key) == ""
