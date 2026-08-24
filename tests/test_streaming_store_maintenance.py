from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import (
    StreamingStore,
    _drop_maintenance_canonical_supplements,
    _merge_maintenance_payload_with_canonical_projection,
    _repair_listing_record_contract,
    _resolve_business_kernel_fields,
)
from peap.streaming_store_maintenance import (
    _run_streaming_store_maintenance_steps,
    run_streaming_store_maintenance,
)


class _NoopMaintenanceStore:
    def __init__(self, post_manifest: dict | None = None) -> None:
        self._post_manifest = post_manifest or {}

    def normalize_legacy_skip_parse_entries(self) -> dict[str, int]:
        return {}

    def normalize_invalid_source_pages(self) -> dict[str, int]:
        return {}

    def purge_invalid_source_page_records(self) -> dict[str, int]:
        return {}

    def purge_quarantined_synthetic_failed_records(self) -> dict[str, int]:
        return {}

    def normalize_superseded_record_shells(self) -> dict[str, int]:
        return {}

    def normalize_deal_source_artifacts(self) -> dict[str, int]:
        return {}

    def normalize_listing_dates(self) -> int:
        return 0

    def normalize_business_kernel_fields(self) -> dict[str, int]:
        return {}

    def normalize_deal_export_readiness(self) -> dict[str, int]:
        return {}

    def normalize_required_mapping_states(self) -> dict[str, int]:
        return {}

    def normalize_optional_rule_findings(self, *, rules_config: dict | None = None) -> dict[str, int]:
        return {}

    def normalize_canonical_contracts(self) -> dict[str, int]:
        return {}

    def normalize_export_projection_readiness(self) -> dict[str, int]:
        return {}

    def build_maintenance_artifact_evidence_manifest(self) -> dict:
        return self._post_manifest


class StreamingStoreMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3", auto_migrate=True)

    def _audit_actions(self) -> list[str]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT action FROM audit_log ORDER BY audit_id").fetchall()
        return [str(row["action"] or "") for row in rows]

    def _record_manifest_by_id(self, manifest: dict) -> dict[str, dict]:
        return {str(row["record_id"]): dict(row) for row in list(manifest.get("records") or [])}

    def test_maintenance_projection_helpers_reject_explicit_non_mapping_inputs(self) -> None:
        canonical_record = {
            "canonical_fields": {"project_code": "GR2026SHMAINT0001"},
            "business_identity": {"business_id": "physical_asset"},
        }
        cases = [
            (
                "merge payload",
                lambda: _merge_maintenance_payload_with_canonical_projection([], canonical_record),
                "payload must be an object",
            ),
            (
                "merge canonical_record",
                lambda: _merge_maintenance_payload_with_canonical_projection({}, False),
                "canonical_record must be an object",
            ),
            (
                "merge canonical_fields",
                lambda: _merge_maintenance_payload_with_canonical_projection({}, {"canonical_fields": []}),
                r"canonical_record\.canonical_fields must be an object",
            ),
            (
                "drop payload",
                lambda: _drop_maintenance_canonical_supplements(
                    "",
                    original_postprocess_payload={},
                    canonical_record=canonical_record,
                ),
                "payload must be an object",
            ),
            (
                "drop original_postprocess_payload",
                lambda: _drop_maintenance_canonical_supplements(
                    {},
                    original_postprocess_payload=False,
                    canonical_record=canonical_record,
                ),
                "original_postprocess_payload must be an object",
            ),
            (
                "drop export_extras",
                lambda: _drop_maintenance_canonical_supplements(
                    {},
                    original_postprocess_payload={},
                    canonical_record={"export_extras": []},
                ),
                r"canonical_record\.export_extras must be an object",
            ),
        ]

        for name, call, error in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, error):
                    call()

    def test_business_kernel_resolution_rejects_explicit_non_mapping_canonical_contracts(self) -> None:
        cases = [
            (
                "canonical_record",
                False,
                "canonical_record must be an object",
            ),
            (
                "canonical_record",
                [],
                "canonical_record must be an object",
            ),
            (
                "business_identity",
                {"business_identity": []},
                r"canonical_record\.business_identity must be an object",
            ),
            (
                "canonical_fields",
                {"canonical_fields": []},
                r"canonical_record\.canonical_fields must be an object",
            ),
        ]

        for name, canonical_record, error in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, error):
                    _resolve_business_kernel_fields(
                        record_family="listing",
                        project_type="实物资产",
                        canonical_record=canonical_record,  # type: ignore[arg-type]
                    )

    def test_repair_listing_record_contract_rejects_explicit_non_mapping_canonical_contracts(self) -> None:
        def repair(canonical_record: object) -> None:
            _repair_listing_record_contract(
                record_id="record-1",
                record_family="listing",
                project_code="GR2026SHMAINT0002",
                project_name="",
                project_type="",
                exchange="",
                listing_date="2026-05-01",
                source_identity=None,
                parser_payload=None,
                postprocess_payload=None,
                canonical_record=canonical_record,  # type: ignore[arg-type]
            )

        cases = [
            (False, "canonical_record must be an object"),
            ([], "canonical_record must be an object"),
            ({"business_identity": []}, r"canonical_record\.business_identity must be an object"),
            ({"export_extras": []}, r"canonical_record\.export_extras must be an object"),
        ]

        for canonical_record, error in cases:
            with self.subTest(canonical_record=canonical_record):
                with self.assertRaisesRegex(ValueError, error):
                    repair(canonical_record)

    def test_maintenance_summary_rejects_explicit_non_mapping_manifest_counter_fields(self) -> None:
        field_cases = [
            ("artifact_evidence", {"artifact_evidence": []}, {}),
            ("source_evidence_missing", {}, {"source_evidence_missing": ""}),
            ("required_field_missing", {}, {"required_field_missing": False}),
        ]

        for field, manifest, post_manifest in field_cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, rf"maintenance manifest\.{field} must be an object"):
                    _run_streaming_store_maintenance_steps(
                        _NoopMaintenanceStore(post_manifest),
                        rules_config=None,
                        manifest=manifest,
                        mode="dry_run",
                        mutation_applied=False,
                        write_audit=False,
                    )

    def test_run_streaming_store_maintenance_defaults_to_dry_run_manifest_without_mutation(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "dry-run-source.bin")
        archive_file = os.path.join(self.temp_dir.name, "dry-run-archive.bin")
        payload = bytes((80, 69, 65, 80, 45, 100, 114, 121, 45, 114, 117, 110))
        with open(source_file, "wb") as handle:
            handle.write(payload)
        with open(archive_file, "wb") as handle:
            handle.write(payload)

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-dry-run",
                revision_hash="hash-maintenance-dry-run",
                project_code="G32026SH1999010",
                project_name="dry run record",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=source_file,
                archive_path=archive_file,
                parser_payload={"项目编号": "G32026SH1999010", "项目名称": "dry run record"},
                postprocess_payload={"项目编号": "G32026SH1999010", "项目名称": "dry run record"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="project type is unresolved",
                    )
                ],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET listing_date = '2026/03/21' WHERE record_id = ?",
                ("rec-maintenance-dry-run",),
            )

        summary = run_streaming_store_maintenance(self.store)
        record = self.store.get_record("rec-maintenance-dry-run")

        self.assertEqual(summary.mode, "dry_run")
        self.assertEqual(summary.required_mapping["records"], 1)
        self.assertEqual(summary.artifact_evidence["present_unverified"], 1)
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["listing_date"], "2026/03/21")
        self.assertEqual(self.store.count_pending_mappings(), 0)
        self.assertEqual(self._audit_actions(), [])
        self.assertTrue(os.path.exists(archive_file))
        with open(archive_file, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_run_streaming_store_maintenance_reports_source_evidence_missing_separately_from_skipped(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "stale-source.bin")
        stale_archive_file = os.path.join(self.temp_dir.name, "stale-archive.bin")
        with open(source_file, "wb") as handle:
            handle.write(bytes((80, 69, 65, 80, 45, 115, 116, 97, 108, 101)))

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-stale-evidence",
                revision_hash="hash-maintenance-stale-evidence",
                project_code="G32026SH1999011",
                project_name="stale evidence record",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=stale_archive_file,
                parser_payload={"项目编号": "G32026SH1999011", "项目名称": "stale evidence record", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1999011", "项目名称": "stale evidence record", "项目类型": "股权转让"},
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store)
        record = self.store.get_record("rec-maintenance-stale-evidence")

        self.assertEqual(summary.artifact_evidence["stale_reference"], 1)
        self.assertEqual(summary.source_evidence_missing["records"], 1)
        self.assertEqual(summary.skip_parse["records"], 0)
        self.assertEqual(record["state"], "ready")
        self.assertFalse(os.path.exists(stale_archive_file))

    def test_run_streaming_store_maintenance_reports_present_unverified_as_source_evidence_missing(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "present-unverified-source.bin")
        with open(source_file, "wb") as handle:
            handle.write(bytes((80, 69, 65, 80, 45, 117, 110, 118, 101, 114, 105, 102, 105, 101, 100)))

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-present-unverified",
                revision_hash="hash-maintenance-present-unverified",
                project_code="",
                project_name="present unverified evidence record",
                project_type="",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目名称": "present unverified evidence record"},
                postprocess_payload={"项目名称": "present unverified evidence record"},
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        manifest_record = summary.manifest["records"][0]

        self.assertEqual(summary.artifact_evidence["present_unverified"], 1)
        self.assertEqual(summary.source_evidence_missing["records"], 1)
        self.assertEqual(manifest_record["maintenance_status"], "source_evidence_present_unverified")

    def test_mutating_maintenance_summary_manifest_reflects_post_mutation_state(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-ready-deal-post-manifest.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-post-manifest-deal",
                revision_hash="hash-maintenance-post-manifest-deal",
                project_code="GR2026SH1002444",
                project_name="缺失源文件成交项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026SH1002444",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026SH1002444",
                        "project_name": "缺失源文件成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-07",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "120.00（万元）",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        manifest_record = self._record_manifest_by_id(summary.manifest)["rec-maintenance-post-manifest-deal"]
        record = self.store.get_record("rec-maintenance-post-manifest-deal")

        self.assertEqual(summary.required_field_missing["records"], 1)
        self.assertEqual(manifest_record["state"], record["state"])
        self.assertEqual(manifest_record["state"], "field_missing")
        self.assertEqual(summary.manifest["source_evidence_missing"], summary.source_evidence_missing)
        self.assertEqual(summary.manifest["required_field_missing"], summary.required_field_missing)
        self.assertEqual(manifest_record["state"], "field_missing")
        self.assertEqual(manifest_record["maintenance_status"], "source_evidence_stale_reference")

    def test_maintenance_manifest_excludes_terminal_failed_records_from_source_evidence_missing(self) -> None:
        missing_source = os.path.join(self.temp_dir.name, "terminal-failed-missing.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-terminal-failed",
                revision_hash="hash-maintenance-terminal-failed",
                project_code="G32026SH1999111",
                project_name="terminal failed record",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="parse_failed",
                source_file=missing_source,
                archive_path=missing_source,
                parser_payload={"项目编号": "G32026SH1999111", "项目名称": "terminal failed record", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1999111", "项目名称": "terminal failed record", "项目类型": "股权转让"},
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store)
        manifest_record = self._record_manifest_by_id(summary.manifest)["rec-maintenance-terminal-failed"]

        self.assertEqual(summary.source_evidence_missing["records"], 0)
        self.assertEqual(manifest_record["maintenance_status"], "parse_failed")

    def test_maintenance_manifest_maps_artifact_verdicts_to_data_health_classifications(self) -> None:
        fixture_rows = [
            (
                "rec-maintenance-stale-reference",
                "stale-reference.html",
                "missing-stale-reference.html",
                b"stale reference source bytes",
                "listing",
                {
                    "record_family": "listing",
                    "business_id": "asset_listing",
                    "project_code": "G32026TEST301",
                    "exchange": "sse",
                },
                "G32026TEST301",
                "source_evidence_stale_reference",
            ),
            (
                "rec-maintenance-undeclared",
                "",
                "",
                b"",
                "listing",
                {
                    "record_family": "listing",
                    "business_id": "asset_listing",
                    "project_code": "G32026TEST302",
                    "exchange": "sse",
                },
                "G32026TEST302",
                "source_evidence_undeclared",
            ),
            (
                "rec-maintenance-invalid-shell",
                "invalid-shell.html",
                "invalid-shell.html",
                b"<html><body><h1>SSE Deal Notice</h1></body></html>",
                "deal",
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "project_code": "G32026TEST303",
                    "exchange": "sse",
                },
                "G32026TEST303",
                "source_evidence_invalid_shell",
            ),
            (
                "rec-maintenance-present-unverified-table",
                "present-unverified.html",
                "present-unverified.html",
                b"present unverified source bytes",
                "listing",
                {"record_family": "listing", "project_code": "G32026TEST304", "exchange": "sse"},
                "G32026TEST304",
                "source_evidence_present_unverified",
            ),
            (
                "rec-maintenance-identity-mismatch",
                "identity-mismatch.html",
                "identity-mismatch.html",
                b"identity mismatch source bytes",
                "listing",
                {
                    "record_family": "listing",
                    "business_id": "asset_listing",
                    "project_code": "G32026TEST999",
                    "exchange": "sse",
                },
                "G32026TEST305",
                "source_evidence_identity_mismatch",
            ),
        ]

        for record_id, source_name, archive_name, payload, family, source_identity, project_code, _ in fixture_rows:
            source_file = os.path.join(self.temp_dir.name, source_name) if source_name else ""
            archive_path = os.path.join(self.temp_dir.name, archive_name) if archive_name else ""
            if source_file:
                with open(source_file, "wb") as handle:
                    handle.write(payload)
            self.store.upsert_record(
                IngestedRecord(
                    record_id=record_id,
                    revision_hash=f"hash-{record_id}",
                    project_code=project_code,
                    project_name="artifact classification fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=source_file,
                    archive_path=archive_path,
                    parser_payload={"project_code": project_code},
                    postprocess_payload={"project_code": project_code},
                    findings=[],
                    record_family=family,
                    source_identity=source_identity,
                )
            )

        summary = run_streaming_store_maintenance(self.store)
        records = self._record_manifest_by_id(summary.manifest)

        self.assertEqual(summary.source_evidence_missing["records"], 5)
        for record_id, *_fixture, expected_status in fixture_rows:
            row = records[record_id]
            data_health_classification = expected_status.removeprefix("source_evidence_")
            self.assertEqual(row["evidence_verdict"]["status"], data_health_classification)
            self.assertEqual(row["maintenance_status"], expected_status)

    def test_maintenance_manifest_counts_field_missing_and_source_evidence_missing_independently(self) -> None:
        missing_archive_file = os.path.join(self.temp_dir.name, "field-missing-stale-archive.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-field-missing-stale",
                revision_hash="hash-maintenance-field-missing-stale",
                project_code="G32026TEST306",
                project_name="field missing stale fixture",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-05-01",
                state="field_missing",
                source_file="",
                archive_path=missing_archive_file,
                parser_payload={"project_code": "G32026TEST306"},
                postprocess_payload={"project_code": "G32026TEST306"},
                findings=[],
                source_identity={
                    "record_family": "listing",
                    "business_id": "asset_listing",
                    "project_code": "G32026TEST306",
                    "exchange": "sse",
                },
            )
        )

        summary = run_streaming_store_maintenance(self.store)
        row = self._record_manifest_by_id(summary.manifest)["rec-maintenance-field-missing-stale"]

        self.assertEqual(summary.required_field_missing["records"], 1)
        self.assertEqual(summary.source_evidence_missing["records"], 1)
        self.assertEqual(row["evidence_verdict"]["status"], "stale_reference")
        self.assertEqual(row["maintenance_status"], "source_evidence_stale_reference")

    def test_run_streaming_store_maintenance_keeps_required_fields_distinct_from_source_evidence_missing(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "field-missing-source.bin")
        with open(source_file, "wb") as handle:
            handle.write(bytes((80, 69, 65, 80, 45, 102, 105, 101, 108, 100)))

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-field-missing-distinct",
                revision_hash="hash-maintenance-field-missing-distinct",
                project_code="DEAL-MISSING-DISTINCT",
                project_name="field missing record",
                project_type="资产转让",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"project_code": "DEAL-MISSING-DISTINCT", "project_type": "资产转让"},
                postprocess_payload={"project_code": "DEAL-MISSING-DISTINCT", "project_type": "资产转让"},
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "DEAL-MISSING-DISTINCT",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "DEAL-MISSING-DISTINCT",
                        "project_name": "field missing record",
                        "project_type": "资产转让",
                        "exchange": "cbex",
                        "deal_date": "",
                        "deal_price": "",
                    },
                },
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-field-missing-distinct")

        self.assertEqual(summary.mode, "mutate")
        self.assertEqual(summary.deal_export_readiness["records"], 1)
        self.assertEqual(summary.required_field_missing["records"], 1)
        self.assertEqual(summary.source_evidence_missing["records"], 0)
        self.assertEqual(summary.skip_parse["records"], 0)
        self.assertEqual(record["state"], "field_missing")

    def test_run_streaming_store_maintenance_does_not_define_evidence_verdict_or_touch_archive_files(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "delegated-source.bin")
        archive_file = os.path.join(self.temp_dir.name, "delegated-archive.bin")
        payload = bytes((80, 69, 65, 80, 45, 100, 101, 108, 101, 103, 97, 116, 101))
        with open(source_file, "wb") as handle:
            handle.write(payload)
        with open(archive_file, "wb") as handle:
            handle.write(payload)

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-delegates-evidence",
                revision_hash="hash-maintenance-delegates-evidence",
                project_code="G32026SH1999012",
                project_name="delegated evidence record",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=archive_file,
                parser_payload={"项目编号": "G32026SH1999012", "项目名称": "delegated evidence record", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1999012", "项目名称": "delegated evidence record", "项目类型": "股权转让"},
                findings=[],
            )
        )

        with mock.patch(
            "peap.streaming_store.resolve_artifact_evidence_verdict",
            wraps=__import__("peap.artifact_truth", fromlist=["resolve_artifact_evidence_verdict"]).resolve_artifact_evidence_verdict,
        ) as resolver:
            summary = run_streaming_store_maintenance(self.store)

        self.assertGreaterEqual(resolver.call_count, 1)
        self.assertEqual(summary.artifact_evidence["verified"], 1)
        self.assertTrue(os.path.exists(archive_file))
        with open(archive_file, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_maintenance_artifact_evidence_manifest_rejects_non_mapping_safe_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-safe-evidence.bin")
        with open(source_file, "wb") as handle:
            handle.write(b"bad safe evidence")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-bad-safe-evidence",
                revision_hash="hash-bad-safe-evidence",
                project_code="G32026SHBADSFE",
                project_name="bad safe evidence record",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHBADSFE", "项目名称": "bad safe evidence record", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SHBADSFE", "项目名称": "bad safe evidence record", "项目类型": "股权转让"},
                findings=[],
            )
        )
        verdict = mock.Mock(
            status="verified",
            reason_code="identity_verified_artifact_present",
            logical_record_identity="listing|asset_listing|shanghai|G32026SHBADSFE",
            identity_confidence="verified",
            authoritative_path=source_file,
            inspection_openable_path=source_file,
            safe_evidence=[],
        )

        with mock.patch("peap.streaming_store.resolve_artifact_evidence_verdict", return_value=verdict):
            with self.assertRaisesRegex(TypeError, "safe_evidence must be a mapping"):
                self.store.build_maintenance_artifact_evidence_manifest()

    def test_upsert_record_does_not_supersede_by_path_hash_identity(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "path-hash-source.bin")
        with open(source_file, "wb") as handle:
            handle.write(bytes((80, 69, 65, 80, 45, 112, 97, 116, 104)))

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-path-hash-original",
                revision_hash="hash-path-hash-original",
                project_code="",
                project_name="path hash original",
                project_type="",
                exchange="",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"project_name": "path hash original"},
                postprocess_payload={"project_name": "path hash original"},
                findings=[],
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-path-hash-new-logical",
                revision_hash="hash-path-hash-new-logical",
                project_code="",
                project_name="path hash new logical",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"project_name": "path hash new logical", "project_type": "股权转让"},
                postprocess_payload={"project_name": "path hash new logical", "project_type": "股权转让"},
                findings=[],
            )
        )

        records = self.store.iter_latest_records(sort="recent")
        self.assertEqual({row["record_id"] for row in records}, {"rec-path-hash-original", "rec-path-hash-new-logical"})

    def test_run_streaming_store_maintenance_normalizes_legacy_state_and_writes_audits(self) -> None:
        failed_source_file = os.path.join(self.temp_dir.name, "legacy-skip-parse.html")
        with open(failed_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy skip parse</body></html>")

        self.store.upsert_failed_record(
            project_code="FAILED-SKIP-001",
            source_file=failed_source_file,
            state="parse_failed",
            error_type="skip_parse",
            error_message="skip-cbex-otc-page",
            payload={"项目编号": "FAILED-SKIP-001"},
        )

        ready_source_file = os.path.join(self.temp_dir.name, "legacy-ready.html")
        with open(ready_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy ready</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-ready",
                revision_hash="hash-maintenance-ready",
                project_code="G32026SH1999004",
                project_name="历史未知类型项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=ready_source_file,
                archive_path=ready_source_file,
                parser_payload={"项目编号": "G32026SH1999004", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026SH1999004", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET listing_date = '2026/03/21' WHERE record_id = ?",
                ("rec-maintenance-ready",),
            )

        summary = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(summary.skip_parse["records"], 1)
        self.assertEqual(summary.listing_dates, 1)
        self.assertEqual(summary.required_mapping["records"], 1)
        pending_rows = self.store.iter_latest_records(states=["pending_review"])
        skipped_rows = self.store.iter_latest_records(states=["skipped"])
        self.assertEqual(len(pending_rows), 1)
        self.assertEqual(pending_rows[0]["listing_date"], "2026-03-21")
        self.assertTrue(
            any(
                str(item.get("type") or "") == "business_resolution_required"
                for item in list(pending_rows[0]["findings"] or [])
            )
        )
        self.assertEqual(self.store.count_pending_mappings(), 0)
        self.assertEqual(len(skipped_rows), 1)
        self.assertIn("legacy_skip_parse_normalized", self._audit_actions())
        self.assertIn("legacy_listing_dates_normalized", self._audit_actions())
        self.assertIn("legacy_required_mapping_normalized", self._audit_actions())

    def test_run_streaming_store_maintenance_backfills_known_project_type_to_business_kernel_fields(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-known-business.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy known business</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-business-backfill",
                revision_hash="hash-maintenance-business-backfill",
                project_code="GR2026BJ1000008",
                project_name="历史实物资产项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BJ1000008", "项目名称": "历史实物资产项目", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026BJ1000008", "项目名称": "历史实物资产项目", "项目类型": "实物资产"},
                findings=[],
            )
        )

        run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-business-backfill")

        self.assertIn("business_id", record)
        self.assertEqual(record["business_id"], "physical_asset")
        self.assertIn("raw_business_label", record)
        self.assertEqual(record["raw_business_label"], "实物资产")

    def test_normalize_business_kernel_fields_surfaces_invalid_canonical_record_json_instead_of_backfilling(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-bad-canonical-business.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy bad canonical business</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-business-bad-canonical",
                revision_hash="hash-maintenance-business-bad-canonical",
                project_code="GR2026BADKERNEL",
                project_name="坏 canonical 业务项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADKERNEL", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026BADKERNEL", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_business_kernel_fields()

    def test_normalize_business_kernel_fields_rejects_non_mapping_business_identity_instead_of_clearing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-bad-business-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy bad business identity</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-business-bad-identity",
                revision_hash="hash-maintenance-business-bad-identity",
                project_code="GR2026BADIDENTITY",
                project_name="坏 business identity 项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADIDENTITY", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026BADIDENTITY", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                (json.dumps({"business_identity": []}), int(created["revision_id"])),
            )

        with self.assertRaisesRegex(ValueError, r"canonical_record\.business_identity"):
            self.store.normalize_business_kernel_fields()

    def test_normalize_business_kernel_fields_revalidates_business_identity_before_revision_update(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-late-bad-business-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy late bad business identity</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-business-late-bad-identity",
                revision_hash="hash-maintenance-business-late-bad-identity",
                project_code="GR2026LATEBADIDENTITY",
                project_name="晚期坏 business identity 项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026LATEBADIDENTITY", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026LATEBADIDENTITY", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                (json.dumps({"business_identity": []}), int(created["revision_id"])),
            )

        with mock.patch("peap.streaming_store._resolve_business_kernel_fields", return_value=("physical_asset", "实物资产")):
            with self.assertRaisesRegex(ValueError, r"canonical_record\.business_identity"):
                self.store.normalize_business_kernel_fields()

    def test_normalize_business_kernel_fields_rejects_orphan_latest_revision_without_backfill(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-orphan-business-revision.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy orphan business revision</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-business-orphan-revision",
                revision_hash="hash-maintenance-business-orphan-revision",
                project_code="GR2026ORPHANKERNEL",
                project_name="缺失 revision 业务项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026ORPHANKERNEL", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026ORPHANKERNEL", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET business_id = ?, raw_business_label = ? WHERE record_id = ?",
                ("", "", "rec-maintenance-business-orphan-revision"),
            )
            conn.execute("DELETE FROM record_revisions WHERE revision_id = ?", (int(created["revision_id"]),))

        with self.assertRaisesRegex(RuntimeError, "missing latest revision"):
            self.store.normalize_business_kernel_fields()

        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT business_id, raw_business_label, latest_revision_id
                FROM records
                WHERE record_id = ?
                """,
                ("rec-maintenance-business-orphan-revision",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["business_id"], "")
        self.assertEqual(row["raw_business_label"], "")
        self.assertEqual(row["latest_revision_id"], created["revision_id"])

    def test_update_record_state_rejects_orphan_latest_revision_without_partial_record_update(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "state-orphan-revision.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>state orphan revision</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-state-orphan-revision",
                revision_hash="hash-state-orphan-revision",
                project_code="GR2026ORPHANSTATE",
                project_name="缺失 revision 状态项目",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026ORPHANSTATE", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026ORPHANSTATE", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute("DELETE FROM record_revisions WHERE revision_id = ?", (int(created["revision_id"]),))

        with self.assertRaisesRegex(RuntimeError, "missing latest revision"):
            self.store.update_record_state(
                "rec-state-orphan-revision",
                state="failed",
                error_type="orphan_revision",
                error_message="must not partially update",
            )

        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT state, last_error_type, last_error_message, latest_revision_id
                FROM records
                WHERE record_id = ?
                """,
                ("rec-state-orphan-revision",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "ready")
        self.assertEqual(row["last_error_type"], "")
        self.assertEqual(row["last_error_message"], "")
        self.assertEqual(row["latest_revision_id"], created["revision_id"])

    def test_run_streaming_store_maintenance_backfills_optional_rule_filtered_records(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-scrap.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy scrap</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-scrap",
                revision_hash="hash-maintenance-scrap",
                project_code="SCRAP-LEGACY-001",
                project_name="报废设备一批",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "SCRAP-LEGACY-001", "项目名称": "报废设备一批", "项目类型": "实物资产"},
                postprocess_payload={
                    "项目编号": "SCRAP-LEGACY-001",
                    "项目名称": "报废设备一批",
                    "项目类型": "实物资产",
                    "项目状态": "挂牌中",
                    "交易所": "beijing",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "测试转让方",
                    "类型": "国资",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"business_id": "physical_asset"},
                    "canonical_fields": {
                        "project_code": "SCRAP-LEGACY-001",
                        "project_name": "报废设备一批",
                        "project_type": "实物资产",
                        "status": "挂牌中",
                        "exchange": "beijing",
                        "start_date": "2026-03-21",
                        "price": "100.00",
                        "seller": "测试转让方",
                        "source_type": "国资",
                    },
                },
                findings=[
                    PostProcessFinding(
                        severity="info",
                        type="person_transferor_marked_private",
                        message="转让方识别为民营企业",
                    )
                ],
            )
        )

        summary = run_streaming_store_maintenance(
            self.store,
            mutate=True,
            rules_config={
                "R010_filter_scrap_physical_asset": {
                    "enabled": True,
                    "priority": 5,
                    "params": {"active": True, "severity": "info", "search_all_fields": True},
                }
            },
        )

        record = self.store.get_record("rec-maintenance-scrap")
        finding_types = {
            str(item.get("type") or "")
            for item in list(record.get("findings") or [])
            if isinstance(item, dict)
        }

        self.assertEqual(summary.optional_rules["revisions"], 1)
        self.assertEqual(record["state"], "skipped")
        self.assertIn("person_transferor_marked_private", finding_types)
        self.assertIn("rule_filtered", finding_types)
        self.assertIn("scrap_physical_asset_filtered", finding_types)
        self.assertIn("legacy_optional_rules_normalized", self._audit_actions())

    def test_normalize_required_mapping_states_surfaces_invalid_canonical_record_json_instead_of_syncing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-required-bad-canonical.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy required bad canonical</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-required-bad-canonical",
                revision_hash="hash-maintenance-required-bad-canonical",
                project_code="GR2026BADREQUIRED",
                project_name="坏 canonical 必填维护项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADREQUIRED", "项目名称": "坏 canonical 必填维护项目"},
                postprocess_payload={"项目编号": "GR2026BADREQUIRED", "项目名称": "坏 canonical 必填维护项目"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_required_mapping_states()

    def test_normalize_required_mapping_states_surfaces_invalid_findings_json_instead_of_recomputing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-required-bad-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy required bad findings</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-required-bad-findings",
                revision_hash="hash-maintenance-required-bad-findings",
                project_code="GR2026BADREQFIND",
                project_name="坏 findings 必填维护项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADREQFIND", "项目名称": "坏 findings 必填维护项目"},
                postprocess_payload={"项目编号": "GR2026BADREQFIND", "项目名称": "坏 findings 必填维护项目"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_required_mapping_states()

    def test_normalize_required_mapping_states_rejects_non_mapping_finding_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-required-bad-evidence.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy required bad evidence</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-required-bad-evidence",
                revision_hash="hash-maintenance-required-bad-evidence",
                project_code="GR2026BADREQEVID",
                project_name="坏 evidence 必填维护项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADREQEVID", "项目名称": "坏 evidence 必填维护项目", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "GR2026BADREQEVID", "项目名称": "坏 evidence 必填维护项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                (
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "mapping_gap",
                                "message": "bad persisted evidence",
                                "evidence": False,
                            }
                        ]
                    ),
                    int(created["revision_id"]),
                ),
            )

        with self.assertRaisesRegex(TypeError, r"evidence"):
            self.store.normalize_required_mapping_states()

    def test_normalize_required_mapping_states_surfaces_invalid_parser_payload_json_instead_of_recomputing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-required-bad-parser-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy required bad parser payload</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-required-bad-parser-payload",
                revision_hash="hash-maintenance-required-bad-parser-payload",
                project_code="GR2026BADREQPARSER",
                project_name="坏 parser payload 必填维护项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "GR2026BADREQPARSER", "项目名称": "坏 parser payload 必填维护项目"},
                postprocess_payload={"项目编号": "GR2026BADREQPARSER", "项目名称": "坏 parser payload 必填维护项目"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET parser_payload_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_required_mapping_states()

    def test_normalize_required_mapping_states_preserves_canonical_export_ready_listing(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "canonical-ready-missing-legacy-type.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>canonical ready listing</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-ready",
                revision_hash="hash-maintenance-canonical-ready",
                project_code="G32026SH1999222",
                project_name="canonical ready listing",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "G32026SH1999222",
                    "项目名称": "canonical ready listing",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32026SH1999222",
                    "项目名称": "canonical ready listing",
                    "项目类型": "股权转让",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1999222",
                        "project_name": "canonical ready listing",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "上交所",
                        "start_date": "2026-03-21",
                        "price": "108.00",
                        "seller": "上海测试公司",
                        "source_type": "国资",
                    },
                },
                findings=[],
            )
        )

        summary = self.store.normalize_required_mapping_states()
        record = self.store.get_record("rec-maintenance-canonical-ready")

        self.assertEqual(summary["records"], 0)
        self.assertEqual(record["state"], "ready")
        self.assertFalse(
            any(str(item.get("type") or "") in {"mapping_gap", "mapping_missing"} for item in record["findings"])
        )

    def test_required_mapping_normalization_cannot_promote_incomplete_listing_to_ready(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "mapping-resolved-missing-price.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>mapping resolved missing price</body></html>")
        record_id = "rec-mapping-resolved-missing-price"
        self.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash="hash-mapping-resolved-missing-price",
                project_code="G32026GD1000777",
                project_name="映射补齐但价格缺失项目",
                project_type="股权转让",
                exchange="guangdong",
                listing_date="2026-08-18",
                state="pending_mapping",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "G32026GD1000777",
                    "项目名称": "映射补齐但价格缺失项目",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "guangdong",
                    "挂牌开始日期": "2026-08-18",
                    "转让方": "广东测试公司",
                    "类型": "国资",
                },
                postprocess_payload={
                    "项目编号": "G32026GD1000777",
                    "项目名称": "映射补齐但价格缺失项目",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "guangdong",
                    "挂牌开始日期": "2026-08-18",
                    "转让方": "广东测试公司",
                    "类型": "国资",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"business_id": "equity_transfer"},
                    "canonical_fields": {
                        "project_code": "G32026GD1000777",
                        "project_name": "映射补齐但价格缺失项目",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "guangdong",
                        "start_date": "2026-08-18",
                        "price": "",
                        "seller": "广东测试公司",
                        "source_type": "国资",
                    },
                },
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "guangdong",
                    "business_id": "equity_transfer",
                },
            )
        )

        self.store.normalize_required_mapping_states()

        record = self.store.get_record(record_id)
        self.assertEqual(record["state"], "field_missing")
        self.assertEqual(
            [item["type"] for item in record["findings"]],
            ["canonical_field_missing"],
        )
        self.assertEqual(record["findings"][0]["evidence"]["missing_fields"], ["price"])

    def test_optional_rule_normalization_cannot_restore_incomplete_listing_to_ready(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "rule-disabled-missing-price.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>rule disabled missing price</body></html>")
        record_id = "rec-rule-disabled-missing-price"
        self.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash="hash-rule-disabled-missing-price",
                project_code="G32026GD1000778",
                project_name="规则解除但价格缺失项目",
                project_type="股权转让",
                exchange="guangdong",
                listing_date="2026-08-18",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "G32026GD1000778",
                    "项目名称": "规则解除但价格缺失项目",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "guangdong",
                    "挂牌开始日期": "2026-08-18",
                    "转让方": "广东测试公司",
                    "类型": "国资",
                },
                postprocess_payload={
                    "项目编号": "G32026GD1000778",
                    "项目名称": "规则解除但价格缺失项目",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "guangdong",
                    "挂牌开始日期": "2026-08-18",
                    "转让方": "广东测试公司",
                    "类型": "国资",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"business_id": "equity_transfer"},
                    "canonical_fields": {
                        "project_code": "G32026GD1000778",
                        "project_name": "规则解除但价格缺失项目",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "guangdong",
                        "start_date": "2026-08-18",
                        "price": "",
                        "seller": "广东测试公司",
                        "source_type": "国资",
                    },
                },
                findings=[
                    PostProcessFinding(
                        severity="info",
                        type="rule_filtered",
                        message="legacy optional rule finding",
                        evidence={"rule_id": "R010_filter_scrap_physical_asset"},
                    )
                ],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "guangdong",
                    "business_id": "equity_transfer",
                },
            )
        )

        self.store.normalize_optional_rule_findings(
            rules_config={
                "R010_filter_scrap_physical_asset": {
                    "enabled": False,
                    "priority": 5,
                    "params": {"active": False},
                }
            }
        )

        record = self.store.get_record(record_id)
        self.assertEqual(record["state"], "field_missing")
        self.assertEqual(
            [item["type"] for item in record["findings"]],
            ["canonical_field_missing"],
        )

    def test_run_streaming_store_maintenance_reapplies_optional_rules_for_skipped_records(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-scrap-skipped.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy skipped scrap</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-skipped-optional",
                revision_hash="hash-maintenance-skipped-optional",
                project_code="GR2026SH1000521",
                project_name="星科金朋半导体(江阴)有限公司部分资产（报废设备资产包4）",
                project_type="实物资产",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "GR2026SH1000521",
                    "项目名称": "星科金朋半导体(江阴)有限公司部分资产（报废设备资产包4）",
                    "项目类型": "实物资产",
                    "类型": "央企",
                },
                postprocess_payload={
                    "项目编号": "GR2026SH1000521",
                    "项目名称": "星科金朋半导体(江阴)有限公司部分资产（报废设备资产包4）",
                    "项目类型": "实物资产",
                    "类型": "央企",
                    "挂牌次数": "首次挂牌",
                    "listing_times": "1",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="listing_times_conflict",
                        message="listing_times conflict current=首次挂牌 derived=1",
                        evidence={"field": "挂牌次数"},
                    ),
                    PostProcessFinding(
                        severity="warn",
                        type="rule_filtered",
                        message="rule filtered record: R010_filter_scrap_physical_asset",
                        evidence={"rule_id": "R010_filter_scrap_physical_asset"},
                    ),
                ],
            )
        )

        summary = run_streaming_store_maintenance(
            self.store,
            mutate=True,
            rules_config={
                "R006_derive_listing_times": {
                    "enabled": True,
                    "priority": 3,
                    "params": {},
                },
                "R010_filter_scrap_physical_asset": {
                    "enabled": True,
                    "priority": 5,
                    "params": {"active": True, "severity": "info", "search_all_fields": True},
                },
            },
        )

        record = self.store.get_record("rec-maintenance-skipped-optional")
        findings = list(record.get("findings") or [])
        finding_types = {str(item.get("type") or "") for item in findings if isinstance(item, dict)}
        finding_messages = [str(item.get("message") or "") for item in findings if isinstance(item, dict)]

        self.assertEqual(summary.optional_rules["revisions"], 1)
        self.assertEqual(record["state"], "skipped")
        self.assertIn("rule_filtered", finding_types)
        self.assertNotIn("listing_times_conflict", finding_types)
        self.assertFalse(any("derived=1" in message for message in finding_messages))

    def test_normalize_optional_rule_findings_surfaces_invalid_canonical_record_json_instead_of_syncing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-optional-bad-canonical.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy optional bad canonical</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-optional-bad-canonical",
                revision_hash="hash-maintenance-optional-bad-canonical",
                project_code="SCRAP-BAD-CANONICAL",
                project_name="报废设备一批",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "SCRAP-BAD-CANONICAL", "项目名称": "报废设备一批", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "SCRAP-BAD-CANONICAL", "项目名称": "报废设备一批", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_optional_rule_findings(
                rules_config={
                    "R010_filter_scrap_physical_asset": {
                        "enabled": True,
                        "priority": 5,
                        "params": {"active": True, "severity": "info", "search_all_fields": True},
                    }
                },
            )

    def test_normalize_optional_rule_findings_surfaces_invalid_findings_json_instead_of_recomputing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-optional-bad-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy optional bad findings</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-optional-bad-findings",
                revision_hash="hash-maintenance-optional-bad-findings",
                project_code="SCRAP-BAD-FINDINGS",
                project_name="报废设备一批",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "SCRAP-BAD-FINDINGS", "项目名称": "报废设备一批", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "SCRAP-BAD-FINDINGS", "项目名称": "报废设备一批", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_optional_rule_findings(
                rules_config={
                    "R010_filter_scrap_physical_asset": {
                        "enabled": True,
                        "priority": 5,
                        "params": {"active": True, "severity": "info", "search_all_fields": True},
                    }
                },
            )

    def test_normalize_optional_rule_findings_rejects_non_mapping_finding_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-optional-bad-evidence.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy optional bad evidence</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-optional-bad-evidence",
                revision_hash="hash-maintenance-optional-bad-evidence",
                project_code="SCRAP-BAD-EVIDENCE",
                project_name="报废设备坏 evidence",
                project_type="实物资产",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "SCRAP-BAD-EVIDENCE", "项目名称": "报废设备坏 evidence", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "SCRAP-BAD-EVIDENCE", "项目名称": "报废设备坏 evidence", "项目类型": "实物资产"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                (
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "mapping_gap",
                                "message": "bad persisted evidence",
                                "evidence": [],
                            }
                        ]
                    ),
                    int(created["revision_id"]),
                ),
            )

        with self.assertRaisesRegex(TypeError, r"evidence"):
            self.store.normalize_optional_rule_findings(
                rules_config={
                    "R010_filter_scrap_physical_asset": {
                        "enabled": True,
                        "priority": 5,
                        "params": {"active": True, "severity": "info", "search_all_fields": True},
                    }
                },
            )

    def test_run_streaming_store_maintenance_repairs_canonical_export_fields_from_payloads(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-canonical-gap.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy canonical gap</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-gap",
                revision_hash="hash-maintenance-canonical-gap",
                project_code="GR2025SH1001496-14",
                project_name="淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                project_type="实物资产",
                exchange="上交所",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "GR2025SH1001496-14",
                    "project_name": "淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "exchange": "上交所",
                    "seller": "淮安市淮阴医院有限公司",
                    "price": "1.5",
                    "start_date": "2025/12/17",
                    "source_type": "央企",
                    "group_name": "中国华润有限公司",
                    "项目编号": "GR2025SH1001496-14",
                    "项目名称": "淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                    "项目类型": "实物资产",
                    "项目状态": "挂牌",
                    "交易所": "上交所",
                    "类型": "央企",
                },
                postprocess_payload={
                    "project_code": "GR2025SH1001496-14",
                    "project_name": "淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "exchange": "上交所",
                    "seller": "淮安市淮阴医院有限公司",
                    "price": "1.5",
                    "start_date": "2025/12/17",
                    "source_type": "央企",
                    "group_name": "中国华润有限公司",
                    "项目编号": "GR2025SH1001496-14",
                    "项目名称": "淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                    "项目类型": "实物资产",
                    "项目状态": "挂牌",
                    "交易所": "上交所",
                    "类型": "央企",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "GR2025SH1001496-14"},
                    "canonical_fields": {
                        "project_code": "GR2025SH1001496-14",
                        "project_name": "淮安市淮阴医院有限公司部分资产（全自动生化分析仪）",
                        "project_type": "实物资产",
                        "status": "",
                        "exchange": "上交所",
                        "start_date": "",
                        "price": "",
                        "seller": "",
                        "source_type": "",
                        "group_name": "",
                    },
                },
                canonical_projection={},
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-canonical-gap")
        canonical_fields = record["canonical_record"]["canonical_fields"]

        self.assertEqual(summary.canonical_contracts["records"], 1)
        self.assertEqual(record["listing_date"], "2025-12-17")
        self.assertEqual(canonical_fields["status"], "挂牌")
        self.assertEqual(canonical_fields["start_date"], "2025/12/17")
        self.assertEqual(canonical_fields["price"], "1.5")
        self.assertEqual(canonical_fields["seller"], "淮安市淮阴医院有限公司")
        self.assertEqual(canonical_fields["source_type"], "央企")
        self.assertEqual(canonical_fields["group_name"], "中国华润有限公司")
        self.assertEqual(record["canonical_projection"]["挂牌开始日期"], "2025/12/17")
        self.assertEqual(record["canonical_projection"]["挂牌价格"], "1.5")
        self.assertEqual(record["canonical_projection"]["转让方"], "淮安市淮阴医院有限公司")
        self.assertIn("legacy_canonical_contract_normalized", self._audit_actions())

    def test_normalize_canonical_contracts_surfaces_invalid_source_identity_json_instead_of_repairing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-canonical-bad-source-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy canonical bad source identity</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-bad-source-identity",
                revision_hash="hash-maintenance-canonical-bad-source-identity",
                project_code="GR2025SHBADIDENTITY",
                project_name="坏身份挂牌项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "GR2025SHBADIDENTITY",
                    "project_name": "坏身份挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                postprocess_payload={
                    "project_code": "GR2025SHBADIDENTITY",
                    "project_name": "坏身份挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "GR2025SHBADIDENTITY"},
                    "canonical_fields": {
                        "project_code": "GR2025SHBADIDENTITY",
                        "project_name": "坏身份挂牌项目",
                        "project_type": "实物资产",
                        "status": "",
                        "start_date": "",
                    },
                },
                canonical_projection={},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", "rec-maintenance-canonical-bad-source-identity"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_canonical_contracts()

    def test_normalize_canonical_contracts_surfaces_invalid_canonical_record_json_instead_of_repairing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-canonical-bad-record-json.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy canonical bad record json</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-bad-record-json",
                revision_hash="hash-maintenance-canonical-bad-record-json",
                project_code="GR2025SHBADRECORDJSON",
                project_name="坏 canonical JSON 挂牌项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "GR2025SHBADRECORDJSON",
                    "project_name": "坏 canonical JSON 挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                postprocess_payload={
                    "project_code": "GR2025SHBADRECORDJSON",
                    "project_name": "坏 canonical JSON 挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "GR2025SHBADRECORDJSON"},
                    "canonical_fields": {"project_code": "GR2025SHBADRECORDJSON"},
                },
                canonical_projection={},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_canonical_contracts()

    def test_normalize_canonical_contracts_surfaces_invalid_canonical_fields_shape_instead_of_repairing(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-canonical-bad-fields-shape.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy canonical bad fields shape</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-bad-fields-shape",
                revision_hash="hash-maintenance-canonical-bad-fields-shape",
                project_code="GR2025SHBADFIELDS",
                project_name="坏 canonical_fields 挂牌项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "GR2025SHBADFIELDS",
                    "project_name": "坏 canonical_fields 挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                postprocess_payload={
                    "project_code": "GR2025SHBADFIELDS",
                    "project_name": "坏 canonical_fields 挂牌项目",
                    "project_type": "实物资产",
                    "status": "挂牌",
                    "start_date": "2025/12/17",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "GR2025SHBADFIELDS"},
                    "canonical_fields": {"project_code": "GR2025SHBADFIELDS"},
                },
                canonical_projection={},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                (
                    json.dumps(
                        {
                            "record_family": "listing",
                            "business_identity": {"project_code": "GR2025SHBADFIELDS"},
                            "canonical_fields": [],
                        },
                        ensure_ascii=False,
                    ),
                    int(created["revision_id"]),
                ),
            )

        with self.assertRaisesRegex(ValueError, "canonical_record.canonical_fields must be an object"):
            self.store.normalize_canonical_contracts()

    def test_run_streaming_store_maintenance_replaces_wrong_canonical_fields(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-canonical-wrong.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy canonical wrong</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-canonical-wrong",
                revision_hash="hash-maintenance-canonical-wrong",
                project_code="GR2025SH1001496-15",
                project_name="测试资产",
                project_type="实物资产",
                exchange="上交所",
                listing_date="",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "GR2025SH1001496-15",
                    "project_name": "测试资产",
                    "seller": "正确转让方",
                    "price": "7.5",
                    "挂牌开始日期": "2026/03/01",
                },
                postprocess_payload={
                    "project_code": "GR2025SH1001496-15",
                    "project_name": "测试资产",
                    "seller": "正确转让方",
                    "price": "7.5",
                    "挂牌开始日期": "2026/03/01",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "GR2025SH1001496-15"},
                    "canonical_fields": {
                        "project_code": "GR2025SH1001496-15",
                        "project_name": "测试资产",
                        "project_type": "实物资产",
                        "status": "挂牌",
                        "exchange": "上交所",
                        "start_date": "1990/01/01",
                        "price": "999",
                        "seller": "错误转让方",
                        "source_type": "央企",
                        "group_name": "集团",
                    },
                },
                canonical_projection={
                    "挂牌开始日期": "1990/01/01",
                    "挂牌价格": "999",
                    "转让方": "错误转让方",
                },
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-canonical-wrong")
        canonical_fields = record["canonical_record"]["canonical_fields"]

        self.assertEqual(summary.canonical_contracts["records"], 1)
        self.assertEqual(record["listing_date"], "2026-03-01")
        self.assertEqual(canonical_fields["start_date"], "2026/03/01")
        self.assertEqual(canonical_fields["price"], "7.5")
        self.assertEqual(canonical_fields["seller"], "正确转让方")
        self.assertEqual(record["canonical_projection"]["挂牌开始日期"], "2026/03/01")
        self.assertEqual(record["canonical_projection"]["挂牌价格"], "7.5")
        self.assertEqual(record["canonical_projection"]["转让方"], "正确转让方")

    def test_run_streaming_store_maintenance_projects_standard_fields_into_contract_export_extras(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-standard-only-fields.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy standard only fields</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-standard-only",
                revision_hash="hash-maintenance-standard-only",
                project_code="G62025BJ1000073",
                project_name="北京电控产业发展有限公司增资项目",
                project_type="增资扩股",
                exchange="beijing",
                listing_date="2025-11-18",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "G62025BJ1000073",
                    "project_name": "北京电控产业发展有限公司增资项目",
                    "business_type": "增资扩股",
                    "exchange": "beijing",
                    "seller": "北京电控产业发展有限公司",
                    "price": "不超过30000万元",
                    "share_ratio": "不超过16.7%",
                    "industry": "科技推广和应用服务业",
                    "start_date": "2025/11/18",
                    "end_date": "2026/03/03",
                    "contact": "席经理",
                    "profit": -8235.96,
                    "region": "北京市",
                    "项目编号": "G62025BJ1000073",
                    "项目名称": "北京电控产业发展有限公司增资项目",
                    "项目类型": "增资扩股",
                },
                postprocess_payload={
                    "project_code": "G62025BJ1000073",
                    "project_name": "北京电控产业发展有限公司增资项目",
                    "business_type": "增资扩股",
                    "exchange": "beijing",
                    "seller": "北京电控产业发展有限公司",
                    "price": "不超过30000万元",
                    "share_ratio": "不超过16.7%",
                    "industry": "科技推广和应用服务业",
                    "start_date": "2025/11/18",
                    "end_date": "2026/03/03",
                    "contact": "席经理",
                    "profit": -8235.96,
                    "region": "北京市",
                    "项目编号": "G62025BJ1000073",
                    "项目名称": "北京电控产业发展有限公司增资项目",
                    "项目类型": "增资扩股",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G62025BJ1000073",
                        "business_id": "capital_increase",
                        "raw_business_label": "增资扩股",
                    },
                    "canonical_fields": {
                        "project_code": "G62025BJ1000073",
                        "project_name": "北京电控产业发展有限公司增资项目",
                        "project_type": "增资扩股",
                        "status": "挂牌",
                        "exchange": "beijing",
                        "start_date": "2025/11/18",
                        "price": "不超过30000万元",
                        "seller": "北京电控产业发展有限公司",
                        "source_type": "",
                        "group_name": "",
                    },
                    "export_extras": {},
                },
                canonical_projection={},
                findings=[],
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-standard-only")
        export_extras = record["canonical_record"]["export_extras"]

        self.assertEqual(summary.canonical_contracts["records"], 1)
        self.assertEqual(export_extras["融资金额"], "不超过30000万元")
        self.assertEqual(export_extras["持股比例"], "不超过16.7%")
        self.assertEqual(export_extras["所属行业"], "科技推广和应用服务业")
        self.assertEqual(export_extras["披露截止日期"], "2026/03/03")
        self.assertEqual(export_extras["经办人"], "席经理")
        self.assertEqual(export_extras["近一年净利润（万）"], -8235.96)
        self.assertEqual(export_extras["所在地区"], "北京市")

    def test_run_streaming_store_maintenance_reclassifies_legacy_ready_deal_missing_export_fields(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-ready-deal-missing-fields.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy ready deal missing fields</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-ready-deal-missing",
                revision_hash="hash-maintenance-ready-deal-missing",
                project_code="XZSYZC",
                project_name="行政事业资产",
                project_type="资产转让",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "project_code": "XZSYZC",
                    "project_name": "行政事业资产",
                    "project_type": "资产转让",
                    "exchange": "cbex",
                    "status": "已录入",
                },
                postprocess_payload={
                    "project_code": "XZSYZC",
                    "project_name": "行政事业资产",
                    "project_type": "资产转让",
                    "exchange": "cbex",
                    "status": "已录入",
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "XZSYZC",
                        "business_id": "deal_physical_asset",
                        "raw_business_label": "资产转让",
                    },
                    "canonical_fields": {
                        "project_code": "XZSYZC",
                        "project_name": "行政事业资产",
                        "project_type": "资产转让",
                        "status": "已录入",
                        "exchange": "cbex",
                        "start_date": "2026-05-08",
                        "price": "",
                        "seller": "",
                        "deal_date": "",
                        "deal_date_basis": "collection_date",
                        "deal_date_is_imputed": True,
                        "collection_date": "2026-05-08",
                        "deal_price": "",
                    },
                    "policy_state": {"findings": []},
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-ready-deal-missing")
        finding_types = {
            str(item.get("type") or "")
            for item in list(record.get("findings") or [])
            if isinstance(item, dict)
        }

        self.assertEqual(summary.deal_export_readiness["records"], 1)
        self.assertEqual(record["state"], "field_missing")
        self.assertIn("canonical_field_missing", finding_types)
        self.assertEqual(
            record["canonical_record"]["policy_state"]["findings"],
            ["canonical_field_missing"],
        )
        self.assertIn("legacy_deal_export_readiness_normalized", self._audit_actions())

    def test_run_streaming_store_maintenance_restores_unitless_wan_deal_price_to_ready(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-unitless-deal-price.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>交易价格（万元） 425.19</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-unitless-wan",
                revision_hash="hash-maintenance-unitless-wan",
                project_code="GR2026BJ1001765",
                project_name="无单位金额成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001765",
                    "project_name": "无单位金额成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001765",
                    "project_name": "无单位金额成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BJ1001765",
                        "business_id": "deal_physical_asset",
                        "raw_business_label": "实物资产",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BJ1001765",
                        "project_name": "无单位金额成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-08",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "425.19",
                        "deal_price_unit_basis": "missing",
                    },
                    "policy_state": {"findings": ["canonical_field_missing"]},
                },
                canonical_projection={},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="canonical_field_missing",
                        message="Missing required canonical fields for export: deal_price_unit_basis",
                        evidence={"missing_fields": ["deal_price_unit_basis"]},
                    )
                ],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-unitless-wan")
        fields = record["canonical_record"]["canonical_fields"]

        self.assertEqual(summary.deal_export_readiness["records"], 1)
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["findings"], [])
        self.assertEqual(fields["deal_price"], "425.19")
        self.assertEqual(fields["deal_price_raw"], "425.19")
        self.assertEqual(fields["deal_price_unit"], "万元")
        self.assertEqual(fields["deal_price_unit_hint"], "交易价格（万元）")
        self.assertEqual(fields["deal_price_unit_basis"], "field_unit_wan")

    def test_run_streaming_store_maintenance_rejects_corrupt_deal_source_before_price_repair(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "corrupt-unitless-deal-price.html")
        with open(source_file, "wb") as handle:
            handle.write("<html><body>交易价格（万元） 425.19".encode("utf-8") + b"\x80</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-corrupt-unitless-wan",
                revision_hash="hash-maintenance-corrupt-unitless-wan",
                project_code="GR2026BJ1001766",
                project_name="坏编码金额成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001766",
                    "project_name": "坏编码金额成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001766",
                    "project_name": "坏编码金额成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BJ1001766",
                        "business_id": "deal_physical_asset",
                        "raw_business_label": "实物资产",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BJ1001766",
                        "project_name": "坏编码金额成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-08",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "425.19",
                        "deal_price_unit_basis": "missing",
                    },
                    "policy_state": {"findings": ["canonical_field_missing"]},
                },
                canonical_projection={},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="canonical_field_missing",
                        message="Missing required canonical fields for export: deal_price_unit_basis",
                        evidence={"missing_fields": ["deal_price_unit_basis"]},
                    )
                ],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-corrupt-unitless-wan")
        fields = record["canonical_record"]["canonical_fields"]

        self.assertEqual(summary.deal_source_artifacts["records"], 1)
        self.assertEqual(record["state"], "field_missing")
        self.assertEqual(record["artifact_status"], "invalid")
        self.assertEqual(record["last_error_type"], "source_artifact_invalid")
        self.assertEqual([item["type"] for item in record["findings"]], ["source_artifact_invalid"])
        self.assertEqual(record["findings"][0]["evidence"]["reason_code"], "source_artifact_decode_failed")
        self.assertNotIn("deal_price_unit_hint", fields)
        self.assertEqual(fields["deal_price_unit_basis"], "missing")

    def test_normalize_deal_export_readiness_surfaces_invalid_canonical_record_json_instead_of_reclassifying(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-deal-bad-canonical.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy deal bad canonical</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-canonical",
                revision_hash="hash-maintenance-deal-bad-canonical",
                project_code="GR2026BADDEALJSON",
                project_name="坏 canonical 成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "project_code": "GR2026BADDEALJSON"},
                postprocess_payload={"record_family": "deal", "project_code": "GR2026BADDEALJSON"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BADDEALJSON",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADDEALJSON",
                        "project_name": "坏 canonical 成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_deal_export_readiness()

    def test_normalize_deal_export_readiness_surfaces_invalid_findings_json_instead_of_reclassifying(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-deal-bad-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy deal bad findings</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-findings",
                revision_hash="hash-maintenance-deal-bad-findings",
                project_code="GR2026BADDEALFIND",
                project_name="坏 findings 成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "project_code": "GR2026BADDEALFIND"},
                postprocess_payload={"record_family": "deal", "project_code": "GR2026BADDEALFIND"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BADDEALFIND",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADDEALFIND",
                        "project_name": "坏 findings 成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_deal_export_readiness()

    def test_normalize_deal_export_readiness_rejects_non_mapping_finding_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-deal-bad-evidence.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy deal bad evidence</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-evidence",
                revision_hash="hash-maintenance-deal-bad-evidence",
                project_code="GR2026BADDEALEVID",
                project_name="坏 evidence 成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "project_code": "GR2026BADDEALEVID"},
                postprocess_payload={"record_family": "deal", "project_code": "GR2026BADDEALEVID"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BADDEALEVID",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADDEALEVID",
                        "project_name": "坏 evidence 成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                (
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "business_resolution_required",
                                "message": "bad persisted evidence",
                                "evidence": "",
                            }
                        ]
                    ),
                    int(created["revision_id"]),
                ),
            )

        with self.assertRaisesRegex(TypeError, r"evidence"):
            self.store.normalize_deal_export_readiness()

    def test_run_streaming_store_maintenance_keeps_deal_business_id_from_project_type_alias(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-deal-project-type-alias.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>交易价格（万元） 425.19</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-project-type-alias",
                revision_hash="hash-maintenance-deal-project-type-alias",
                project_code="GR2026BJ1001765",
                project_name="项目类型别名成交项目",
                project_type="实物资产",
                exchange="北交所",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001765",
                    "project_name": "项目类型别名成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "business_id": "deal_physical_asset",
                    "project_code": "GR2026BJ1001765",
                    "project_name": "项目类型别名成交项目",
                    "project_type": "实物资产",
                    "deal_price": "425.19",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "cbex"},
                    "business_identity": {
                        "project_code": "GR2026BJ1001765",
                        "business_id": "deal_physical_asset",
                        "raw_business_label": "实物资产",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BJ1001765",
                        "project_name": "项目类型别名成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-08",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "425.19",
                        "deal_price_unit_basis": "default_wan",
                    },
                    "policy_state": {"findings": []},
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-deal-project-type-alias")

        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["findings"], [])

    def test_run_streaming_store_maintenance_reclassifies_synthetic_sse_deal_archive(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-sse-synthetic-deal.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write(
                '<html><body><h1>SSE Deal Notice</h1>'
                '<script id="deal_detail" type="application/json">'
                '{"data":[{"XMBH":"GR2026SH1000563","CJJG":"20.995922（万元）"}]}'
                "</script></body></html>"
            )

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-sse-synthetic",
                revision_hash="hash-maintenance-sse-synthetic",
                project_code="GR2026SH1000563",
                project_name="上海江南长兴造船有限责任公司部分资产",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "sse",
                    "project_code": "GR2026SH1000563",
                    "project_name": "上海江南长兴造船有限责任公司部分资产",
                    "deal_price": "20.995922（万元）",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "sse",
                    "project_code": "GR2026SH1000563",
                    "project_name": "上海江南长兴造船有限责任公司部分资产",
                    "deal_price": "20.995922（万元）",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026SH1000563",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026SH1000563",
                        "project_name": "上海江南长兴造船有限责任公司部分资产",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-07",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "20.995922（万元）",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-sse-synthetic")

        self.assertEqual(summary.deal_source_artifacts["records"], 1)
        self.assertEqual(record["state"], "field_missing")
        self.assertEqual(record["artifact_status"], "invalid")
        self.assertEqual(record["last_error_type"], "source_artifact_invalid")
        self.assertEqual([item["type"] for item in record["findings"]], ["source_artifact_invalid"])
        self.assertEqual(record["canonical_record"]["policy_state"]["findings"], ["source_artifact_invalid"])

    def test_run_streaming_store_maintenance_reclassifies_synthetic_non_sse_deal_archive(self) -> None:
        cases = [
            ("cbex", "CBEX Deal Notice", "北交所"),
            ("cquae", "CQUAE Deal Notice", "重交所"),
            ("tpre", "TPRE Deal Notice", "天交所"),
        ]
        for source_id, title, exchange in cases:
            source_file = os.path.join(self.temp_dir.name, f"legacy-{source_id}-synthetic-deal.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write(
                    f"<html><body><h1>{title}</h1>"
                    '<script id="deal_detail" type="application/json">'
                    '{"data":[{"XMBH":"G32026TEST0001","CJJG":"20.995922（万元）"}]}'
                    "</script></body></html>"
                )

            self.store.upsert_record(
                IngestedRecord(
                    record_id=f"rec-maintenance-{source_id}-synthetic",
                    revision_hash=f"hash-maintenance-{source_id}-synthetic",
                    project_code=f"G32026TEST-{source_id}",
                    project_name=f"{exchange}历史假壳成交项目",
                    project_type="实物资产",
                    exchange=exchange,
                    listing_date="2026-05-07",
                    state="ready",
                    source_file=source_file,
                    archive_path=source_file,
                    parser_payload={
                        "record_family": "deal",
                        "source_id": source_id,
                        "project_code": f"G32026TEST-{source_id}",
                        "project_name": f"{exchange}历史假壳成交项目",
                        "deal_price": "20.995922（万元）",
                    },
                    postprocess_payload={
                        "record_family": "deal",
                        "source_id": source_id,
                        "project_code": f"G32026TEST-{source_id}",
                        "project_name": f"{exchange}历史假壳成交项目",
                        "deal_price": "20.995922（万元）",
                    },
                    canonical_record={
                        "record_family": "deal",
                        "source_identity": {"source_id": source_id},
                        "business_identity": {
                            "project_code": f"G32026TEST-{source_id}",
                            "business_id": "deal_physical_asset",
                        },
                        "canonical_fields": {
                            "project_code": f"G32026TEST-{source_id}",
                            "project_name": f"{exchange}历史假壳成交项目",
                            "project_type": "实物资产",
                            "status": "成交",
                            "deal_date": "2026-05-07",
                            "deal_price": "20.995922（万元）",
                        },
                    },
                    canonical_projection={},
                    findings=[],
                    record_family="deal",
                )
            )

        summary = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(summary.deal_source_artifacts["records"], len(cases))
        for source_id, _title, _exchange in cases:
            record = self.store.get_record(f"rec-maintenance-{source_id}-synthetic")
            self.assertEqual(record["state"], "field_missing")
            self.assertEqual(record["artifact_status"], "invalid")
            self.assertEqual(record["last_error_type"], "source_artifact_invalid")
            self.assertEqual([item["type"] for item in record["findings"]], ["source_artifact_invalid"])
            self.assertEqual(record["canonical_record"]["policy_state"]["findings"], ["source_artifact_invalid"])

    def test_run_streaming_store_maintenance_reclassifies_ready_deal_missing_source_file(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-ready-deal.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-missing-source-file",
                revision_hash="hash-maintenance-deal-missing-source-file",
                project_code="GR2026SH1002346",
                project_name="缺失源文件成交项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026SH1002346",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026SH1002346",
                        "project_name": "缺失源文件成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-07",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "120.00（万元）",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        record = self.store.get_record("rec-maintenance-deal-missing-source-file")

        self.assertEqual(summary.deal_source_artifacts["records"], 1)
        self.assertEqual(record["state"], "field_missing")
        self.assertEqual(record["artifact_status"], "missing")
        self.assertEqual(record["last_error_type"], "source_artifact_missing")
        self.assertEqual([item["type"] for item in record["findings"]], ["source_artifact_missing"])

    def test_normalize_deal_source_artifacts_surfaces_invalid_source_identity_json_instead_of_source_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-bad-source-identity-deal.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-source-identity",
                revision_hash="hash-maintenance-deal-bad-source-identity",
                project_code="GR2026BADIDENTITY",
                project_name="坏身份成交项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026BADIDENTITY",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADIDENTITY",
                        "project_name": "坏身份成交项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", "rec-maintenance-deal-bad-source-identity"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_deal_source_artifacts()

    def test_normalize_deal_source_artifacts_surfaces_invalid_canonical_record_json_instead_of_source_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-bad-canonical-deal.html")
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-canonical-artifact",
                revision_hash="hash-maintenance-deal-bad-canonical-artifact",
                project_code="GR2026BADCANONART",
                project_name="坏 canonical 成交源文件项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026BADCANONART",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADCANONART",
                        "project_name": "坏 canonical 成交源文件项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_deal_source_artifacts()

    def test_normalize_deal_source_artifacts_surfaces_invalid_findings_json_instead_of_rewriting(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-bad-findings-deal.html")
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-findings-artifact",
                revision_hash="hash-maintenance-deal-bad-findings-artifact",
                project_code="GR2026BADFINDART",
                project_name="坏 findings 成交源文件项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026BADFINDART",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADFINDART",
                        "project_name": "坏 findings 成交源文件项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_deal_source_artifacts()

    def test_normalize_deal_source_artifacts_rejects_non_mapping_existing_finding_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-bad-evidence-deal.html")
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-evidence-artifact",
                revision_hash="hash-maintenance-deal-bad-evidence-artifact",
                project_code="GR2026BADEVIDART",
                project_name="坏 evidence 成交源文件项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026BADEVIDART",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADEVIDART",
                        "project_name": "坏 evidence 成交源文件项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                (
                    json.dumps(
                        [
                            {
                                "severity": "error",
                                "type": "source_artifact_missing",
                                "message": "Deal source artifact is missing",
                                "evidence": False,
                            }
                        ]
                    ),
                    int(created["revision_id"]),
                ),
            )

        with self.assertRaisesRegex(TypeError, r"evidence"):
            self.store.normalize_deal_source_artifacts()

    def test_normalize_deal_source_artifacts_rejects_non_mapping_existing_finding_item(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "missing-bad-finding-item-deal.html")
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-deal-bad-finding-item-artifact",
                revision_hash="hash-maintenance-deal-bad-finding-item-artifact",
                project_code="GR2026BADITEMART",
                project_name="坏 finding item 成交源文件项目",
                project_type="实物资产",
                exchange="上交所",
                listing_date="2026-05-07",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "deal", "source_id": "sse"},
                postprocess_payload={"record_family": "deal", "source_id": "sse"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "business_identity": {
                        "project_code": "GR2026BADITEMART",
                        "business_id": "deal_physical_asset",
                    },
                    "canonical_fields": {
                        "project_code": "GR2026BADITEMART",
                        "project_name": "坏 finding item 成交源文件项目",
                        "project_type": "实物资产",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                (json.dumps([False]), int(created["revision_id"])),
            )

        with self.assertRaisesRegex((TypeError, ValueError), r"findings"):
            self.store.normalize_deal_source_artifacts()

    def test_run_streaming_store_maintenance_purges_quarantined_synthetic_failed_deal_shells(self) -> None:
        failed = self.store.upsert_failed_record(
            project_code="G32025TJ1000102",
            source_file=os.path.join(self.temp_dir.name, "archive", "missing-synthetic.html"),
            state="parse_failed",
            error_type="synthetic_archive_quarantined",
            error_message="历史成交归档为人造 synthetic/list-row fallback snapshot",
            payload={
                "record_family": "deal",
                "source_id": "tpre",
                "business_id": "deal_equity_transfer",
                "project_code": "G32025TJ1000102",
            },
        )
        keep_source = os.path.join(self.temp_dir.name, "archive", "real-parse-failed.html")
        os.makedirs(os.path.dirname(keep_source), exist_ok=True)
        with open(keep_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>real failed source</body></html>")
        kept = self.store.upsert_failed_record(
            project_code="G32026TJ1000008",
            source_file=keep_source,
            state="parse_failed",
            error_type="exchange-detect-failed",
            error_message="exchange-detect-failed",
            payload={"record_family": "listing", "source_id": "tpre"},
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(summary.quarantined_synthetic_failures["records"], 1)
        with self.assertRaises(KeyError):
            self.store.get_record(failed["record_id"])
        self.assertEqual(self.store.get_record(kept["record_id"])["state"], "parse_failed")
        self.assertIn("legacy_quarantined_synthetic_failures_purged", self._audit_actions())

    def test_run_streaming_store_maintenance_marks_failed_shell_superseded_by_ready_record(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "archive", "tianjin.html")
        os.makedirs(os.path.dirname(source_file), exist_ok=True)
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>天津交易集团</title></head><body>G32026TJ1000008</body></html>")

        failed = self.store.upsert_failed_record(
            project_code="G32026TJ1000008",
            source_file=source_file,
            state="parse_failed",
            error_type="exchange-detect-failed",
            error_message="exchange-detect-failed",
            payload={
                "record_family": "listing",
                "source_id": "tpre",
                "project_code": "G32026TJ1000008",
                "original_source_file": source_file,
            },
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-ready-tianjin",
                revision_hash="hash-maintenance-ready-tianjin",
                project_code="G32026TJ1000008",
                project_name="天津市城科智能热力有限公司100%股权",
                project_type="股权转让",
                exchange="天交所",
                listing_date="2026-05-18",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "listing", "source_id": "tpre"},
                postprocess_payload={"record_family": "listing", "source_id": "tpre"},
                findings=[],
                source_identity={
                    "record_family": "listing",
                    "source_id": "tpre",
                    "project_code": "G32026TJ1000008",
                    "original_source_file": source_file,
                },
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)
        failed_record = self.store.get_record(failed["record_id"])
        second = self.store.normalize_superseded_record_shells()

        self.assertEqual(summary.superseded_record_shells["records"], 1)
        self.assertEqual(second["records"], 0)
        self.assertEqual(failed_record["state"], "skipped")
        self.assertEqual(failed_record["last_error_type"], "superseded_by_record")
        self.assertIn("superseded_record_shell_backfill", self._audit_actions())

    def test_normalize_superseded_record_shells_surfaces_invalid_canonical_record_json_instead_of_backfilling(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "archive", "superseded-bad-canonical.html")
        os.makedirs(os.path.dirname(source_file), exist_ok=True)
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>天津交易集团</title></head><body>G32026BADSUPER</body></html>")

        failed = self.store.upsert_failed_record(
            project_code="G32026BADSUPER",
            source_file=source_file,
            state="parse_failed",
            error_type="exchange-detect-failed",
            error_message="exchange-detect-failed",
            payload={
                "record_family": "listing",
                "source_id": "tpre",
                "project_code": "G32026BADSUPER",
                "original_source_file": source_file,
            },
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-ready-bad-super",
                revision_hash="hash-maintenance-ready-bad-super",
                project_code="G32026BADSUPER",
                project_name="坏 canonical supersede 项目",
                project_type="股权转让",
                exchange="天交所",
                listing_date="2026-05-18",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"record_family": "listing", "source_id": "tpre"},
                postprocess_payload={"record_family": "listing", "source_id": "tpre"},
                findings=[],
                source_identity={
                    "record_family": "listing",
                    "source_id": "tpre",
                    "project_code": "G32026BADSUPER",
                    "original_source_file": source_file,
                },
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(failed["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_superseded_record_shells()

    def test_run_streaming_store_maintenance_purges_legacy_cbex_deal_category_pages(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-cbex-category.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_房屋土地</title></head><body></body></html>")

        ready_source_file = os.path.join(self.temp_dir.name, "legacy-cbex-ready-category.html")
        with open(ready_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_项目推介</title></head><body></body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-cbex-category",
                revision_hash="hash-maintenance-cbex-category",
                project_code="FWTD",
                project_name="房屋土地",
                project_type="实物资产",
                exchange="cbex",
                listing_date="2026-05-08",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/fwtd/",
                    "project_code": "FWTD",
                    "project_name": "房屋土地",
                    "project_type": "实物资产",
                    "status": "成交",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/fwtd/",
                    "project_code": "FWTD",
                    "project_name": "房屋土地",
                    "project_type": "实物资产",
                    "status": "成交",
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "FWTD",
                        "business_id": "deal_physical_asset",
                        "raw_business_label": "实物资产",
                    },
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/fwtd/",
                    },
                    "canonical_fields": {
                        "project_code": "FWTD",
                        "project_name": "房屋土地",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_price": "",
                    },
                },
                canonical_projection={},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="canonical_field_missing",
                        message="Missing required canonical fields for export: deal_price",
                        evidence={"missing_fields": ["deal_price"]},
                    )
                ],
                record_family="deal",
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-cbex-ready-category",
                revision_hash="hash-maintenance-cbex-ready-category",
                project_code="XMTJ",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=ready_source_file,
                archive_path=ready_source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ",
                    "project_name": "项目推介",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ",
                    "project_name": "项目推介",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {
                        "project_code": "XMTJ",
                        "project_name": "项目推介",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(summary.invalid_source_pages["records"], 2)
        self.assertEqual(summary.purged_invalid_source_pages["records"], 2)
        with self.assertRaises(KeyError):
            self.store.get_record("rec-maintenance-cbex-category")
        with self.assertRaises(KeyError):
            self.store.get_record("rec-maintenance-cbex-ready-category")
        self.assertIn("legacy_invalid_source_pages_normalized", self._audit_actions())
        self.assertIn("legacy_invalid_source_pages_purged", self._audit_actions())

        second = run_streaming_store_maintenance(self.store, mutate=True)
        self.assertEqual(second.invalid_source_pages["records"], 0)
        self.assertEqual(second.purged_invalid_source_pages["records"], 0)

    def test_normalize_invalid_source_pages_surfaces_invalid_source_identity_json_instead_of_parser_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "cbex-bad-source-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_项目推介</title></head><body></body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-bad-source-identity",
                revision_hash="hash-maintenance-bad-source-identity",
                project_code="XMTJ-BAD-IDENTITY",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-IDENTITY",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-IDENTITY",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {
                        "project_code": "XMTJ-BAD-IDENTITY",
                        "project_name": "项目推介",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", "rec-maintenance-bad-source-identity"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_invalid_source_pages()

    def test_normalize_invalid_source_pages_surfaces_invalid_parser_payload_json_instead_of_parser_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "cbex-bad-parser-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_项目推介</title></head><body></body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-bad-parser-payload-source-page",
                revision_hash="hash-maintenance-bad-parser-payload-source-page",
                project_code="XMTJ-BAD-PARSER-PAYLOAD",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-PARSER-PAYLOAD",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-PARSER-PAYLOAD",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {
                        "project_code": "XMTJ-BAD-PARSER-PAYLOAD",
                        "project_name": "项目推介",
                        "status": "成交",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET parser_payload_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_invalid_source_pages()

    def test_normalize_invalid_source_pages_surfaces_invalid_canonical_record_json_instead_of_canonical_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "cbex-bad-canonical.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_项目推介</title></head><body></body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-bad-canonical-source-page",
                revision_hash="hash-maintenance-bad-canonical-source-page",
                project_code="XMTJ-BAD-CANONICAL",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-CANONICAL",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-CANONICAL",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {"project_code": "XMTJ-BAD-CANONICAL"},
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_invalid_source_pages()

    def test_normalize_invalid_source_pages_surfaces_invalid_findings_json_instead_of_rule_filtered_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "cbex-bad-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>北京产权交易所_项目推介</title></head><body></body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-bad-findings-source-page",
                revision_hash="hash-maintenance-bad-findings-source-page",
                project_code="XMTJ-BAD-FINDINGS",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-FINDINGS",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-BAD-FINDINGS",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {"project_code": "XMTJ-BAD-FINDINGS"},
                },
                canonical_projection={},
                findings=[PostProcessFinding(severity="warn", type="rule_filtered", message="already filtered")],
                record_family="deal",
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.normalize_invalid_source_pages()

    def test_run_streaming_store_maintenance_purges_invalid_source_page_without_touching_same_project_records(
        self,
    ) -> None:
        invalid_source_file = os.path.join(self.temp_dir.name, "missing-sse-shell.html")
        ready_source_file = os.path.join(self.temp_dir.name, "ready-sse-deal.html")
        listing_source_file = os.path.join(self.temp_dir.name, "ready-listing.html")
        with open(ready_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>correct deal source</body></html>")
        with open(listing_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>correct listing source</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-deal-business-copy",
                revision_hash="hash-invalid-deal-business-copy",
                project_code="GR2026SH1000434-3",
                project_name="错误业务类型成交副本",
                project_type="产权转让",
                exchange="sse",
                listing_date="2026-05-08",
                state="skipped",
                source_file=invalid_source_file,
                archive_path=invalid_source_file,
                parser_payload={"record_family": "deal", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                postprocess_payload={"record_family": "deal", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "canonical_fields": {"project_code": "GR2026SH1000434-3"},
                },
                canonical_projection={},
                findings=[PostProcessFinding(severity="error", type="invalid_source_page", message="bad shell")],
                record_family="deal",
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ready-deal-same-project",
                revision_hash="hash-ready-deal-same-project",
                project_code="GR2026SH1000434-3",
                project_name="正确成交记录",
                project_type="实物资产",
                exchange="sse",
                listing_date="2026-05-08",
                state="ready",
                source_file=ready_source_file,
                archive_path=ready_source_file,
                parser_payload={"record_family": "deal", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                postprocess_payload={"record_family": "deal", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {"source_id": "sse"},
                    "canonical_fields": {
                        "project_code": "GR2026SH1000434-3",
                        "project_name": "正确成交记录",
                        "project_type": "实物资产",
                        "status": "成交",
                        "deal_date": "2026-05-08",
                        "deal_date_basis": "deal_date",
                        "deal_date_is_imputed": False,
                        "deal_price": "1.00",
                    },
                },
                canonical_projection={},
                findings=[],
                record_family="deal",
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ready-listing-same-project",
                revision_hash="hash-ready-listing-same-project",
                project_code="GR2026SH1000434-3",
                project_name="正确挂牌记录",
                project_type="实物资产",
                exchange="sse",
                listing_date="2026-04-03",
                state="ready",
                source_file=listing_source_file,
                archive_path=listing_source_file,
                parser_payload={"record_family": "listing", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                postprocess_payload={"record_family": "listing", "source_id": "sse", "project_code": "GR2026SH1000434-3"},
                canonical_record={
                    "record_family": "listing",
                    "source_identity": {"source_id": "sse"},
                    "canonical_fields": {"project_code": "GR2026SH1000434-3"},
                },
                canonical_projection={},
                findings=[],
                record_family="listing",
            )
        )
        self.store.update_record_state(
            "rec-invalid-deal-business-copy",
            state="skipped",
            error_type="invalid_source_page",
            error_message="wrong business copy",
        )

        summary = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(summary.purged_invalid_source_pages["records"], 1)
        with self.assertRaises(KeyError):
            self.store.get_record("rec-invalid-deal-business-copy")
        self.assertEqual(self.store.get_record("rec-ready-deal-same-project")["record_family"], "deal")
        self.assertEqual(self.store.get_record("rec-ready-listing-same-project")["record_family"], "listing")

    def test_purge_invalid_source_page_records_surfaces_invalid_source_identity_json_instead_of_parser_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "purge-bad-source-identity.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-purge-bad-source-identity",
                revision_hash="hash-purge-bad-source-identity",
                project_code="XMTJ-PURGE-BAD-IDENTITY",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-IDENTITY",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-IDENTITY",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {"project_code": "XMTJ-PURGE-BAD-IDENTITY"},
                },
                canonical_projection={},
                findings=[PostProcessFinding(severity="error", type="invalid_source_page", message="bad shell")],
                record_family="deal",
            )
        )
        self.store.update_record_state(
            "rec-purge-bad-source-identity",
            state="skipped",
            error_type="invalid_source_page",
            error_message="bad shell",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", "rec-purge-bad-source-identity"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.purge_invalid_source_page_records()

    def test_purge_invalid_source_page_records_surfaces_source_identity_json_shape_error_instead_of_deleting(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "purge-bad-source-identity-shape.html")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-purge-bad-source-identity-shape",
                revision_hash="hash-purge-bad-source-identity-shape",
                project_code="XMTJ-PURGE-BAD-IDENTITY-SHAPE",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-IDENTITY-SHAPE",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-IDENTITY-SHAPE",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {"project_code": "XMTJ-PURGE-BAD-IDENTITY-SHAPE"},
                },
                canonical_projection={},
                findings=[PostProcessFinding(severity="error", type="invalid_source_page", message="bad shell")],
                record_family="deal",
            )
        )
        self.store.update_record_state(
            "rec-purge-bad-source-identity-shape",
            state="skipped",
            error_type="invalid_source_page",
            error_message="bad shell",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("[]", "rec-purge-bad-source-identity-shape"),
            )

        with self.assertRaisesRegex(ValueError, "source_identity_json must be an object"):
            self.store.purge_invalid_source_page_records()

        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT state, source_identity_json FROM records WHERE record_id = ?",
                ("rec-purge-bad-source-identity-shape",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "skipped")
        self.assertEqual(row["source_identity_json"], "[]")

    def test_purge_invalid_source_page_records_surfaces_invalid_canonical_record_json_instead_of_parser_fallback(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "purge-bad-canonical.html")
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-purge-bad-canonical",
                revision_hash="hash-purge-bad-canonical",
                project_code="XMTJ-PURGE-BAD-CANONICAL",
                project_name="项目推介",
                project_type="",
                exchange="cbex",
                listing_date="2026-05-08",
                state="skipped",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-CANONICAL",
                },
                postprocess_payload={
                    "record_family": "deal",
                    "source_id": "cbex",
                    "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    "project_code": "XMTJ-PURGE-BAD-CANONICAL",
                },
                canonical_record={
                    "record_family": "deal",
                    "source_identity": {
                        "source_id": "cbex",
                        "source_url": "https://www.cbex.com.cn/xm/zczr/xmtj/",
                    },
                    "canonical_fields": {"project_code": "XMTJ-PURGE-BAD-CANONICAL"},
                },
                canonical_projection={},
                findings=[PostProcessFinding(severity="error", type="invalid_source_page", message="bad shell")],
                record_family="deal",
            )
        )
        self.store.update_record_state(
            "rec-purge-bad-canonical",
            state="skipped",
            error_type="invalid_source_page",
            error_message="bad shell",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.purge_invalid_source_page_records()

    def test_run_streaming_store_maintenance_is_idempotent(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "idempotent.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>idempotent</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-idempotent",
                revision_hash="hash-maintenance-idempotent",
                project_code="G32026SH1999005",
                project_name="历史未知类型项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1999005", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026SH1999005", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )

        first = run_streaming_store_maintenance(self.store, mutate=True)
        first_audits = list(self._audit_actions())
        second = run_streaming_store_maintenance(self.store, mutate=True)

        self.assertEqual(first.required_mapping["records"], 1)
        self.assertEqual(second.skip_parse["records"], 0)
        self.assertEqual(second.listing_dates, 0)
        self.assertEqual(second.canonical_contracts["records"], 0)
        self.assertEqual(second.deal_export_readiness["records"], 0)
        self.assertEqual(second.required_mapping["records"], 0)
        self.assertEqual(second.optional_rules["records"], 0)
        self.assertEqual(self._audit_actions(), first_audits)


if __name__ == "__main__":
    unittest.main()
