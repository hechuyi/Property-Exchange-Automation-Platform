from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest

from desktop_backend.repositories.pipeline_repository import PipelineRepository
from peap.streaming_models import IngestedRecord
from peap.streaming_store import StreamingStore


class PipelineRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3", auto_migrate=True)
        self.repository = PipelineRepository(store=self.store)

    def _store_reprocess_record(
        self,
        *,
        record_id: str,
        record_family: str,
        business_id: str,
        state: str,
        project_code: str = "G32026CQ1000062",
        project_name: str = "重庆成交身份修复测试",
        source_id: str = "cquae",
        exchange: str = "重交所",
        source_url: str = "",
        project_id: str = "",
        sidecar_integrity: str = "hash_and_bytes",
    ) -> dict[str, object]:
        source_file = os.path.join(
            self.temp_dir.name,
            f"{project_code}-{record_family}-{business_id}.html",
        )
        artifact_bytes = f"<html><body>{project_code}</body></html>".encode()
        with open(source_file, "wb") as handle:
            handle.write(artifact_bytes)
        sidecar: dict[str, object] = {
            "save_status": "complete",
            "metadata": {
                "record_family": "deal",
                "source_id": source_id,
                "project_code": project_code,
            },
        }
        if sidecar_integrity in {"hash", "hash_and_bytes"}:
            sidecar["archive_content_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
        if sidecar_integrity in {"bytes", "hash_and_bytes"}:
            sidecar["archive_content_bytes"] = len(artifact_bytes)
        with open(os.path.splitext(source_file)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle, ensure_ascii=False)
        candidate_tokens = [f"project_code:{project_code}"]
        if project_id:
            candidate_tokens.append(f"project_id:{project_id}")
        if source_url:
            candidate_tokens.append(f"page_url:{source_url}")
        return self.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash=f"hash-{record_id}",
                record_family=record_family,
                project_code=project_code,
                project_name=project_name,
                project_type="股权转让",
                exchange=exchange,
                listing_date="2026-07-02",
                state=state,
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": project_code,
                    "page_url": source_url,
                    "project_id": project_id,
                },
                postprocess_payload={"项目编号": project_code},
                findings=[],
                canonical_record={
                    "record_family": record_family,
                    "business_identity": {
                        "record_family": record_family,
                        "business_id": business_id,
                    },
                    "canonical_fields": {
                        "project_code": project_code,
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": record_family,
                    "business_id": business_id,
                    "source_id": source_id,
                    "exchange": exchange,
                    "source_url": source_url,
                    "project_id": project_id,
                    "project_code": project_code,
                    "candidate_tokens": candidate_tokens,
                    "original_source_file": source_file,
                    "original_evidence_path": source_file,
                },
            )
        )

    def _store_shenzhen_alias_pair(
        self,
        *,
        case: str,
        alias_code: str,
        canonical_code: str,
        project_id: str,
        package_id: str,
        project_name: str,
        original_source_id: str = "shenzhen",
        replacement_source_id: str = "shenzhen",
        replacement_project_id: str | None = None,
        sidecar_mutator=None,
        original_has_file: bool = False,
        original_state: str = "pending_mapping",
        replacement_state: str = "ready",
    ) -> tuple[dict[str, object], dict[str, object], str]:
        """Create a Shenzhen-shaped CQ alias and canonical listing pair."""
        page_url = (
            "https://www.sotcbb.com/bdDetail.htm?contentId="
            f"{package_id}&channelId=3961&id=2430975"
        )
        original_id = f"{case}-original"
        replacement_id = f"{case}-replacement"
        original_path = os.path.join(self.temp_dir.name, f"{case}-alias.html")
        replacement_path = os.path.join(self.temp_dir.name, f"{case}-canonical.html")
        artifact_bytes = (
            f"<html><body>{canonical_code}-{project_name}</body></html>".encode("utf-8")
        )
        if original_has_file:
            with open(original_path, "wb") as handle:
                handle.write(artifact_bytes)
        with open(replacement_path, "wb") as handle:
            handle.write(artifact_bytes)
        sidecar: dict[str, object] = {
            "save_status": "complete",
            "source_id": replacement_source_id,
            "metadata": {
                "record_family": "listing",
                "source_id": replacement_source_id,
                "project_code": canonical_code,
                "project_name": project_name,
            },
            "archive_content_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "archive_content_bytes": len(artifact_bytes),
            "detail_payload": {
                "data": {
                    "portalTPackage": {
                        "gzwCode": canonical_code,
                        "projectCode": alias_code,
                        "packageId": package_id,
                        "projectName": project_name,
                    }
                }
            },
        }
        if sidecar_mutator is not None:
            sidecar_mutator(sidecar)
        with open(os.path.splitext(replacement_path)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle, ensure_ascii=False)

        replacement_id_value = replacement_project_id or project_id

        def _record(
            *,
            record_id: str,
            project_code: str,
            source_id: str,
            source_file: str,
            parser_payload: dict[str, object],
            state: str,
            revision_suffix: str,
        ) -> dict[str, object]:
            candidate_tokens = [f"project_code:{project_code}"]
            if record_id == original_id:
                candidate_tokens.append(f"project_id:{project_id}")
            else:
                candidate_tokens.extend(
                    [
                        f"project_code:{alias_code}",
                        f"project_id:{replacement_id_value}",
                        f"page_url:{page_url}",
                    ]
                )
            if record_id == original_id:
                candidate_tokens.append(f"page_url:{page_url}")
            source_identity = {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "source_id": source_id,
                "exchange": "深交所" if source_id == "shenzhen" else "上交所",
                "source_url": page_url,
                "project_id": project_id if record_id == original_id else replacement_id_value,
                "project_code": project_code,
                "project_name": project_name,
                "candidate_tokens": candidate_tokens,
                "original_source_file": source_file,
                "original_evidence_path": source_file,
            }
            return self.store.upsert_record(
                IngestedRecord(
                    record_id=record_id,
                    revision_hash=f"hash-{case}-{revision_suffix}",
                    record_family="listing",
                    project_code=project_code,
                    project_name=project_name,
                    project_type="股权转让",
                    exchange=source_identity["exchange"],
                    listing_date="2026-08-13",
                    state=state,
                    source_file=source_file,
                    archive_path=source_file,
                    parser_payload=parser_payload,
                    postprocess_payload=dict(parser_payload),
                    findings=[],
                    canonical_record={
                        "record_family": "listing",
                        "business_identity": {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                        },
                        "canonical_fields": {
                            "project_code": project_code,
                            "project_name": project_name,
                            "project_type": "股权转让",
                        },
                        "source_identity": source_identity,
                    },
                    source_identity=source_identity,
                )
            )

        original_parser = {
            "项目编号": alias_code,
            "project_code": alias_code,
            "项目名称": project_name,
            "project_name": project_name,
            "项目类型": "股权转让",
            "page_url": page_url,
            "source_url": page_url,
            "project_id": project_id,
        }
        replacement_parser = {
            "项目编号": canonical_code,
            "project_code": canonical_code,
            "项目名称": project_name,
            "project_name": project_name,
            "项目类型": "股权转让",
            "page_url": page_url,
            "source_url": page_url,
            "project_id": replacement_id_value,
            "source_project_code": alias_code,
        }
        original = _record(
            record_id=original_id,
            project_code=alias_code,
            source_id=original_source_id,
            source_file=original_path,
            parser_payload=original_parser,
            state=original_state,
            revision_suffix="original",
        )
        replacement = _record(
            record_id=replacement_id,
            project_code=canonical_code,
            source_id=replacement_source_id,
            source_file=replacement_path,
            parser_payload=replacement_parser,
            state=replacement_state,
            revision_suffix="replacement",
        )
        self.store.mark_mapping_pending(
            record_id=original_id,
            revision_id=int(original["revision_id"]),
            project_code=alias_code,
            payload={"missing": "source_type"},
        )
        return original, replacement, replacement_path

    def _reprocess_atomic_snapshot(
        self,
        *,
        original_record_id: str,
        replacement_record_id: str,
    ) -> tuple[object, ...]:
        with sqlite3.connect(self.store.db_path) as conn:
            records = conn.execute(
                """
                SELECT record_id, state, latest_revision_id, last_error_type,
                       last_error_message, last_operation_kind, last_operation_code,
                       last_operation_message, last_operation_at, updated_at
                FROM records
                WHERE record_id IN (?, ?)
                ORDER BY record_id
                """,
                (original_record_id, replacement_record_id),
            ).fetchall()
            revisions = conn.execute(
                """
                SELECT record_id, revision_id, state, findings_json, canonical_record_json
                FROM record_revisions
                WHERE record_id IN (?, ?)
                ORDER BY record_id, revision_id
                """,
                (original_record_id, replacement_record_id),
            ).fetchall()
            pending = conn.execute(
                """
                SELECT record_id, revision_id, resolved_at
                FROM mapping_pending
                WHERE record_id IN (?, ?)
                ORDER BY record_id, pending_id
                """,
                (original_record_id, replacement_record_id),
            ).fetchall()
            audit = conn.execute(
                "SELECT audit_id, action, payload_json FROM audit_log ORDER BY audit_id"
            ).fetchall()
        return records, revisions, pending, audit

    def test_reprocess_result_with_new_family_retires_original_and_resolves_mapping_atomically(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-backlog",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-backlog",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-deal-field-missing",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-backlog",
            result={"record_id": "rec-deal-field-missing", "state": "field_missing"},
        )

        original_record = self.store.get_record("rec-listing-backlog")
        replacement = self.store.get_record("rec-deal-field-missing")
        self.assertEqual(original_record["state"], "skipped")
        self.assertEqual(original_record["last_error_type"], "superseded_by_record")
        self.assertEqual(original_record["last_operation_kind"], "reprocess")
        self.assertEqual(original_record["last_operation_code"], "ok")
        self.assertEqual(
            original_record["findings"][0]["evidence"]["superseded_by_record_id"],
            "rec-deal-field-missing",
        )
        self.assertEqual(replacement["record_family"], "deal")
        self.assertEqual(replacement["state"], "field_missing")
        self.assertEqual(self.store.count_pending_mappings(), 0)
        with sqlite3.connect(self.store.db_path) as conn:
            pending = conn.execute(
                "SELECT resolved_at FROM mapping_pending WHERE record_id = ?",
                ("rec-listing-backlog",),
            ).fetchone()
            audit_rows = conn.execute(
                "SELECT action, payload_json FROM audit_log WHERE action IN (?, ?) ORDER BY audit_id",
                ("record_superseded_by_reprocess", "record_reprocessed"),
            ).fetchall()
        self.assertIsNotNone(pending)
        self.assertTrue(str(pending[0] or ""))
        self.assertEqual([row[0] for row in audit_rows], [
            "record_superseded_by_reprocess",
            "record_reprocessed",
        ])
        supersede_audit = json.loads(audit_rows[0][1])
        self.assertEqual(supersede_audit["previous_record_family"], "listing")
        self.assertEqual(supersede_audit["superseded_by_record_family"], "deal")
        self.assertEqual(supersede_audit["resolved_mapping_pending"], 1)

    def test_reprocess_result_allows_cquae_listing_to_deal_capital_transition(self) -> None:
        project_code = "G62026CQ1000006"
        original = self._store_reprocess_record(
            record_id="rec-cquae-listing-capital",
            record_family="listing",
            business_id="capital_increase",
            state="pending_mapping",
            project_code=project_code,
        )
        self.store.mark_mapping_pending(
            record_id="rec-cquae-listing-capital",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-cquae-deal-capital",
            record_family="deal",
            business_id="deal_capital_increase",
            state="field_missing",
            project_code=project_code,
        )

        self.repository.record_reprocessed(
            record_id="rec-cquae-listing-capital",
            result={"record_id": "rec-cquae-deal-capital", "state": "field_missing"},
        )

        self.assertEqual(self.store.get_record("rec-cquae-listing-capital")["state"], "skipped")
        self.assertEqual(self.store.get_record("rec-cquae-deal-capital")["state"], "field_missing")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_reprocess_result_requires_integrity_bound_sidecars_for_same_cquae_source(self) -> None:
        project_code = "G32026CQ1000098"
        original = self._store_reprocess_record(
            record_id="rec-cquae-unbound-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=project_code,
            sidecar_integrity="none",
        )
        self.store.mark_mapping_pending(
            record_id="rec-cquae-unbound-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-cquae-unbound-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=project_code,
            sidecar_integrity="none",
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id="rec-cquae-unbound-listing",
            replacement_record_id="rec-cquae-unbound-deal",
        )

        with self.assertRaisesRegex(ValueError, "sidecar evidence"):
            self.repository.record_reprocessed(
                record_id="rec-cquae-unbound-listing",
                result={"record_id": "rec-cquae-unbound-deal", "state": "field_missing"},
            )

        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id="rec-cquae-unbound-listing",
                replacement_record_id="rec-cquae-unbound-deal",
            ),
            before,
        )

    def test_reprocess_result_rejects_same_project_code_across_exchanges_atomically(self) -> None:
        project_code = "G32026CQ1000099"
        original = self._store_reprocess_record(
            record_id="rec-cross-exchange-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=project_code,
            source_id="cquae",
            exchange="重交所",
        )
        self.store.mark_mapping_pending(
            record_id="rec-cross-exchange-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-cross-exchange-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=project_code,
            source_id="cquae",
            exchange="上交所",
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id="rec-cross-exchange-listing",
            replacement_record_id="rec-cross-exchange-deal",
        )

        with self.assertRaisesRegex(ValueError, "source scope"):
            self.repository.record_reprocessed(
                record_id="rec-cross-exchange-listing",
                result={"record_id": "rec-cross-exchange-deal", "state": "field_missing"},
            )

        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id="rec-cross-exchange-listing",
                replacement_record_id="rec-cross-exchange-deal",
            ),
            before,
        )

    def test_reprocess_result_rejects_cross_source_strong_tokens_atomically(self) -> None:
        cases = (
            ("page_url", "https://shared.example.test/item/42"),
            ("project_id", "SHARED-PROJECT-ID-42"),
        )
        for index, (identity_field, identity_value) in enumerate(cases, start=1):
            with self.subTest(identity_field=identity_field):
                original_id = f"rec-cross-source-listing-{index}"
                replacement_id = f"rec-cross-source-deal-{index}"
                original_code = f"G32026BJ10008{index}"
                replacement_code = f"G32026SH10008{index}"
                identity_kwargs = {
                    "source_url" if identity_field == "page_url" else identity_field: identity_value
                }
                original = self._store_reprocess_record(
                    record_id=original_id,
                    record_family="listing",
                    business_id="equity_transfer",
                    state="pending_mapping",
                    project_code=original_code,
                    source_id="cbex",
                    exchange="北交所",
                    **identity_kwargs,
                )
                self.store.mark_mapping_pending(
                    record_id=original_id,
                    revision_id=int(original["revision_id"]),
                    project_code=original_code,
                    payload={"missing": "source_type"},
                )
                self._store_reprocess_record(
                    record_id=replacement_id,
                    record_family="deal",
                    business_id="deal_equity_transfer",
                    state="field_missing",
                    project_code=replacement_code,
                    source_id="sse",
                    exchange="上交所",
                    **identity_kwargs,
                )
                before = self._reprocess_atomic_snapshot(
                    original_record_id=original_id,
                    replacement_record_id=replacement_id,
                )

                with self.assertRaisesRegex(ValueError, "source scope|project_code mismatch"):
                    self.repository.record_reprocessed(
                        record_id=original_id,
                        result={"record_id": replacement_id, "state": "field_missing"},
                    )

                self.assertEqual(
                    self._reprocess_atomic_snapshot(
                        original_record_id=original_id,
                        replacement_record_id=replacement_id,
                    ),
                    before,
                )

    def test_reprocess_result_rejects_same_source_shared_page_url_with_different_project_codes(self) -> None:
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54750"
        original_code = "G32026CQ1000102"
        replacement_code = "G32026CQ1000103"
        original = self._store_reprocess_record(
            record_id="rec-same-source-url-code-mismatch-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=original_code,
            source_url=page_url,
        )
        self.store.mark_mapping_pending(
            record_id="rec-same-source-url-code-mismatch-listing",
            revision_id=int(original["revision_id"]),
            project_code=original_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-same-source-url-code-mismatch-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=replacement_code,
            source_url=page_url,
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id="rec-same-source-url-code-mismatch-listing",
            replacement_record_id="rec-same-source-url-code-mismatch-deal",
        )

        with self.assertRaisesRegex(ValueError, "project_code mismatch"):
            self.repository.record_reprocessed(
                record_id="rec-same-source-url-code-mismatch-listing",
                result={
                    "record_id": "rec-same-source-url-code-mismatch-deal",
                    "state": "field_missing",
                },
            )

        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id="rec-same-source-url-code-mismatch-listing",
                replacement_record_id="rec-same-source-url-code-mismatch-deal",
            ),
            before,
        )

    def test_reprocess_result_rejects_same_source_shared_project_id_with_different_project_codes(self) -> None:
        project_id = "CQUAE-SHARED-PROJECT-ID-42"
        original_code = "G32026CQ1000104"
        replacement_code = "G32026CQ1000105"
        original = self._store_reprocess_record(
            record_id="rec-same-source-id-code-mismatch-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=original_code,
            project_id=project_id,
        )
        self.store.mark_mapping_pending(
            record_id="rec-same-source-id-code-mismatch-listing",
            revision_id=int(original["revision_id"]),
            project_code=original_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-same-source-id-code-mismatch-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=replacement_code,
            project_id=project_id,
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id="rec-same-source-id-code-mismatch-listing",
            replacement_record_id="rec-same-source-id-code-mismatch-deal",
        )

        with self.assertRaisesRegex(ValueError, "project_code mismatch"):
            self.repository.record_reprocessed(
                record_id="rec-same-source-id-code-mismatch-listing",
                result={
                    "record_id": "rec-same-source-id-code-mismatch-deal",
                    "state": "field_missing",
                },
            )

        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id="rec-same-source-id-code-mismatch-listing",
                replacement_record_id="rec-same-source-id-code-mismatch-deal",
            ),
            before,
        )

    def test_reprocess_result_allows_same_source_shared_page_url_with_same_project_code(self) -> None:
        project_code = "G32026CQ1000106"
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54750"
        original = self._store_reprocess_record(
            record_id="rec-same-source-url-same-code-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=project_code,
            source_url=page_url,
        )
        self.store.mark_mapping_pending(
            record_id="rec-same-source-url-same-code-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-same-source-url-same-code-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=project_code,
            source_url=page_url,
        )

        self.repository.record_reprocessed(
            record_id="rec-same-source-url-same-code-listing",
            result={
                "record_id": "rec-same-source-url-same-code-deal",
                "state": "field_missing",
            },
        )

        self.assertEqual(self.store.get_record("rec-same-source-url-same-code-listing")["state"], "skipped")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_reprocess_result_allows_same_listing_business_correction_with_verified_identity(self) -> None:
        project_code = "G32026CQ1000107"
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54751"
        original = self._store_reprocess_record(
            record_id="rec-listing-business-correction-original",
            record_family="listing",
            business_id="physical_asset",
            state="pending_mapping",
            project_code=project_code,
            source_url=page_url,
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-business-correction-original",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-listing-business-correction-replacement",
            record_family="listing",
            business_id="equity_transfer",
            state="ready",
            project_code=project_code,
            source_url=page_url,
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-business-correction-original",
            result={
                "record_id": "rec-listing-business-correction-replacement",
                "state": "ready",
            },
        )

        self.assertEqual(
            self.store.get_record("rec-listing-business-correction-original")["state"],
            "skipped",
        )
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_reprocess_result_accepts_discovery_code_sidecar_with_strong_package_identity(self) -> None:
        project_code = "G32026SZ1000109"
        source_project_code = "CQ2026081300099"
        project_name = "深圳项目规范名称"
        page_url = "https://www.sotcbb.com/bdDetail.htm?contentId=package-identity-109"
        original = self._store_reprocess_record(
            record_id="rec-listing-discovery-sidecar-original",
            record_family="listing",
            business_id="physical_asset",
            state="pending_mapping",
            project_code=project_code,
            project_name=project_name,
            source_id="shenzhen",
            exchange="深交所",
            source_url=page_url,
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-discovery-sidecar-original",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        replacement = self._store_reprocess_record(
            record_id="rec-listing-discovery-sidecar-replacement",
            record_family="listing",
            business_id="equity_transfer",
            state="ready",
            project_code=project_code,
            project_name=project_name,
            source_id="shenzhen",
            exchange="深交所",
            source_url=page_url,
        )

        for record in (original, replacement):
            stored_record = self.store.get_record(str(record["record_id"]))
            sidecar_path = os.path.splitext(str(stored_record["source_file"]))[0] + ".json"
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
            sidecar.update(
                {
                    "project_code": "DISCLOSURE_package-identity-109",
                    "project_name": f"{project_name}(国资监测编号{project_code})",
                    "detail_payload": {
                        "data": {
                            "portalTPackage": {
                                "gzwCode": project_code,
                                "projectCode": source_project_code,
                                "projectName": project_name,
                            }
                        }
                    },
                }
            )
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump(sidecar, handle, ensure_ascii=False)

        self.repository.record_reprocessed(
            record_id="rec-listing-discovery-sidecar-original",
            result={
                "record_id": "rec-listing-discovery-sidecar-replacement",
                "state": "ready",
            },
        )

        self.assertEqual(
            self.store.get_record("rec-listing-discovery-sidecar-original")["state"],
            "skipped",
        )

    def test_reprocess_result_rejects_same_listing_business_correction_without_sidecar_evidence(self) -> None:
        project_code = "G32026CQ1000108"
        original = self._store_reprocess_record(
            record_id="rec-listing-business-correction-no-sidecar-original",
            record_family="listing",
            business_id="physical_asset",
            state="pending_mapping",
            project_code=project_code,
            sidecar_integrity="none",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-business-correction-no-sidecar-original",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-listing-business-correction-no-sidecar-replacement",
            record_family="listing",
            business_id="equity_transfer",
            state="ready",
            project_code=project_code,
            sidecar_integrity="none",
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id="rec-listing-business-correction-no-sidecar-original",
            replacement_record_id="rec-listing-business-correction-no-sidecar-replacement",
        )

        with self.assertRaisesRegex(ValueError, "sidecar evidence"):
            self.repository.record_reprocessed(
                record_id="rec-listing-business-correction-no-sidecar-original",
                result={
                    "record_id": "rec-listing-business-correction-no-sidecar-replacement",
                    "state": "ready",
                },
            )

        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id="rec-listing-business-correction-no-sidecar-original",
                replacement_record_id="rec-listing-business-correction-no-sidecar-replacement",
            ),
            before,
        )

    def test_reprocess_result_requires_strong_binding_for_declared_source_alias(self) -> None:
        project_code = "G32026CQ1000100"
        original = self._store_reprocess_record(
            record_id="rec-cquae-alias-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=project_code,
            source_id="cquae",
            sidecar_integrity="bytes",
        )
        self.store.mark_mapping_pending(
            record_id="rec-cquae-alias-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-cquae-alias-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=project_code,
            source_id="chongqing",
            sidecar_integrity="bytes",
        )

        with self.assertRaisesRegex(ValueError, "source scope"):
            self.repository.record_reprocessed(
                record_id="rec-cquae-alias-listing",
                result={"record_id": "rec-cquae-alias-deal", "state": "field_missing"},
            )

        self.assertEqual(self.store.get_record("rec-cquae-alias-listing")["state"], "pending_mapping")
        self.assertEqual(self.store.count_pending_mappings(), 1)

    def test_reprocess_result_allows_declared_source_alias_with_shared_page_url(self) -> None:
        project_code = "G32026CQ1000101"
        page_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54750"
        original = self._store_reprocess_record(
            record_id="rec-cquae-alias-strong-listing",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
            project_code=project_code,
            source_id="cquae",
            source_url=page_url,
            sidecar_integrity="bytes",
        )
        self.store.mark_mapping_pending(
            record_id="rec-cquae-alias-strong-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-cquae-alias-strong-deal",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="field_missing",
            project_code=project_code,
            source_id="chongqing",
            source_url=page_url,
            sidecar_integrity="bytes",
        )

        self.repository.record_reprocessed(
            record_id="rec-cquae-alias-strong-listing",
            result={"record_id": "rec-cquae-alias-strong-deal", "state": "field_missing"},
        )

        self.assertEqual(self.store.get_record("rec-cquae-alias-strong-listing")["state"], "skipped")
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_reprocess_result_allows_verified_shenzhen_cq_alias_pairs(self) -> None:
        pairs = (
            (
                "CQ2026081300003",
                "G32026SZ1000064",
                "2149abca5aa34171b31dfeb9176f30b7",
                "兰州倚能假日影城有限责任公司40%股权",
            ),
            (
                "CQ2026072300011",
                "G32026SZ1000053",
                "66a073948b184781b61bebd44de7afc8",
                "北京中创鸿星创业投资基金管理有限公司17.1%股权",
            ),
            (
                "CQ2026051100001",
                "G32026SZ1000030",
                "c0c1049772ce43fa8dd0cc11ace4b7cf",
                "武汉西建众力新型建材有限公司70%股权",
            ),
            (
                "CQ2026072400009",
                "G32026SZ1000054",
                "9448ccf7aaaa4385b223b61a0a74c2cd",
                "杭州艾美依航空制造装备有限公司1.1204%股权",
            ),
        )
        for index, (alias_code, canonical_code, project_id, project_name) in enumerate(pairs, start=1):
            with self.subTest(alias_code=alias_code):
                original, replacement, _ = self._store_shenzhen_alias_pair(
                    case=f"shenzhen-positive-{index}",
                    alias_code=alias_code,
                    canonical_code=canonical_code,
                    project_id=project_id,
                    package_id=project_id,
                    project_name=project_name,
                    original_has_file=False,
                    original_state="mapping_conflict" if index == 1 else "pending_mapping",
                )

                self.repository.record_reprocessed(
                    record_id=str(original["record_id"]),
                    result={"record_id": str(replacement["record_id"]), "state": "ready"},
                )

                original_record = self.store.get_record(str(original["record_id"]))
                self.assertEqual(original_record["state"], "skipped")
                self.assertEqual(original_record["last_error_type"], "superseded_by_record")
                self.assertEqual(self.store.count_pending_mappings(), 0)
                with sqlite3.connect(self.store.db_path) as conn:
                    audit = conn.execute(
                        """
                        SELECT payload_json
                        FROM audit_log
                        WHERE action = 'record_superseded_by_reprocess'
                        ORDER BY audit_id DESC
                        LIMIT 1
                        """
                    ).fetchone()
                self.assertIsNotNone(audit)
                audit_payload = json.loads(audit[0])
                self.assertEqual(audit_payload["identity_relationship_basis"], "source_project_code_alias")
                self.assertEqual(
                    audit_payload["identity_relationship_value"],
                    f"{alias_code}->{canonical_code}",
                )

    def test_reprocess_result_rejects_unverified_shenzhen_alias_without_side_effects(self) -> None:
        cases = (
            (
                "wrong-gzw-code",
                lambda sidecar: sidecar["detail_payload"]["data"]["portalTPackage"].update(
                    {"gzwCode": "G32026SZ1999999"}
                ),
                {},
            ),
            (
                "wrong-source-project-code",
                lambda sidecar: sidecar["detail_payload"]["data"]["portalTPackage"].update(
                    {"projectCode": "CQ2026081200001"}
                ),
                {},
            ),
            (
                "wrong-package-id",
                lambda sidecar: sidecar["detail_payload"]["data"]["portalTPackage"].update(
                    {"packageId": "wrong-package-id"}
                ),
                {},
            ),
            (
                "wrong-project-name",
                lambda sidecar: sidecar["detail_payload"]["data"]["portalTPackage"].update(
                    {"projectName": "不相关项目"}
                ),
                {},
            ),
            (
                "wrong-hash",
                lambda sidecar: sidecar.update({"archive_content_sha256": "0" * 64}),
                {},
            ),
            (
                "wrong-bytes",
                lambda sidecar: sidecar.update(
                    {"archive_content_bytes": int(sidecar["archive_content_bytes"]) + 1}
                ),
                {},
            ),
            (
                "incomplete-status",
                lambda sidecar: sidecar.update({"save_status": "pending"}),
                {},
            ),
            (
                "nested-incomplete-status",
                lambda sidecar: sidecar["metadata"].update({"save_status": "pending"}),
                {},
            ),
            (
                "wrong-project-id",
                None,
                {"replacement_project_id": "wrong-project-id"},
            ),
        )
        for case, sidecar_mutator, options in cases:
            with self.subTest(case=case):
                original, replacement, _ = self._store_shenzhen_alias_pair(
                    case=f"shenzhen-negative-{case}",
                    alias_code="CQ2026081300003",
                    canonical_code="G32026SZ1000064",
                    project_id="2149abca5aa34171b31dfeb9176f30b7",
                    package_id="2149abca5aa34171b31dfeb9176f30b7",
                    project_name="兰州倚能假日影城有限责任公司40%股权",
                    sidecar_mutator=sidecar_mutator,
                    **options,
                )
                before = self._reprocess_atomic_snapshot(
                    original_record_id=str(original["record_id"]),
                    replacement_record_id=str(replacement["record_id"]),
                )

                with self.assertRaisesRegex(ValueError, "project_code mismatch|source scope|sidecar evidence|identity relationship"):
                    self.repository.record_reprocessed(
                        record_id=str(original["record_id"]),
                        result={"record_id": str(replacement["record_id"]), "state": "ready"},
                    )

                self.assertEqual(
                    self._reprocess_atomic_snapshot(
                        original_record_id=str(original["record_id"]),
                        replacement_record_id=str(replacement["record_id"]),
                    ),
                    before,
                )

    def test_reprocess_result_rejects_shenzhen_alias_cross_scope_and_generic_same_identity(self) -> None:
        original, replacement, _ = self._store_shenzhen_alias_pair(
            case="shenzhen-negative-cross-source",
            alias_code="CQ2026081300003",
            canonical_code="G32026SZ1000064",
            project_id="2149abca5aa34171b31dfeb9176f30b7",
            package_id="2149abca5aa34171b31dfeb9176f30b7",
            project_name="兰州倚能假日影城有限责任公司40%股权",
            replacement_source_id="sse",
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id=str(original["record_id"]),
            replacement_record_id=str(replacement["record_id"]),
        )
        with self.assertRaisesRegex(ValueError, "project_code mismatch|source scope|identity relationship"):
            self.repository.record_reprocessed(
                record_id=str(original["record_id"]),
                result={"record_id": str(replacement["record_id"]), "state": "ready"},
            )
        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id=str(original["record_id"]),
                replacement_record_id=str(replacement["record_id"]),
            ),
            before,
        )

        generic_original, generic_replacement, _ = self._store_shenzhen_alias_pair(
            case="shenzhen-negative-generic-identity",
            alias_code="G32026SZ1000001",
            canonical_code="G32026SZ1000002",
            project_id="generic-project-id",
            package_id="generic-project-id",
            project_name="通用异码项目",
        )
        before_generic = self._reprocess_atomic_snapshot(
            original_record_id=str(generic_original["record_id"]),
            replacement_record_id=str(generic_replacement["record_id"]),
        )
        with self.assertRaisesRegex(ValueError, "project_code mismatch|identity relationship"):
            self.repository.record_reprocessed(
                record_id=str(generic_original["record_id"]),
                result={"record_id": str(generic_replacement["record_id"]), "state": "ready"},
            )
        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id=str(generic_original["record_id"]),
                replacement_record_id=str(generic_replacement["record_id"]),
            ),
            before_generic,
        )

    def test_shenzhen_alias_state_gate_rejects_non_ready_replacement_atomically(self) -> None:
        for replacement_state in ("field_missing", "parse_failed"):
            with self.subTest(replacement_state=replacement_state):
                original, replacement, _ = self._store_shenzhen_alias_pair(
                    case=f"shenzhen-negative-replacement-{replacement_state}",
                    alias_code="CQ2026081300003",
                    canonical_code="G32026SZ1000064",
                    project_id="2149abca5aa34171b31dfeb9176f30b7",
                    package_id="2149abca5aa34171b31dfeb9176f30b7",
                    project_name="兰州倚能假日影城有限责任公司40%股权",
                    replacement_state=replacement_state,
                )
                before = self._reprocess_atomic_snapshot(
                    original_record_id=str(original["record_id"]),
                    replacement_record_id=str(replacement["record_id"]),
                )

                with self.assertRaisesRegex(ValueError, "invalid source_project_code_alias state"):
                    self.repository.record_reprocessed(
                        record_id=str(original["record_id"]),
                        result={"record_id": str(replacement["record_id"]), "state": replacement_state},
                    )

                self.assertEqual(
                    self._reprocess_atomic_snapshot(
                        original_record_id=str(original["record_id"]),
                        replacement_record_id=str(replacement["record_id"]),
                    ),
                    before,
                )

        original, replacement, _ = self._store_shenzhen_alias_pair(
            case="shenzhen-negative-original-ready",
            alias_code="CQ2026081300003",
            canonical_code="G32026SZ1000064",
            project_id="2149abca5aa34171b31dfeb9176f30b7",
            package_id="2149abca5aa34171b31dfeb9176f30b7",
            project_name="兰州倚能假日影城有限责任公司40%股权",
            original_state="ready",
        )
        before = self._reprocess_atomic_snapshot(
            original_record_id=str(original["record_id"]),
            replacement_record_id=str(replacement["record_id"]),
        )
        with self.assertRaisesRegex(ValueError, "invalid source_project_code_alias state"):
            self.repository.record_reprocessed(
                record_id=str(original["record_id"]),
                result={"record_id": str(replacement["record_id"]), "state": "ready"},
            )
        self.assertEqual(
            self._reprocess_atomic_snapshot(
                original_record_id=str(original["record_id"]),
                replacement_record_id=str(replacement["record_id"]),
            ),
            before,
        )

    def test_reprocess_result_with_failed_sibling_keeps_original_mapping_open(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-failed-reprocess",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-failed-reprocess",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-deal-parse-failed",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="parse_failed",
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-failed-reprocess",
            result={"record_id": "rec-deal-parse-failed", "state": "parse_failed"},
        )

        original_record = self.store.get_record("rec-listing-failed-reprocess")
        self.assertEqual(original_record["state"], "pending_mapping")
        self.assertEqual(original_record["last_error_type"], "")
        self.assertEqual(self.store.count_pending_mappings(), 1)
        with sqlite3.connect(self.store.db_path) as conn:
            supersede_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("record_superseded_by_reprocess",),
            ).fetchone()[0]
        self.assertEqual(supersede_audits, 0)

    def test_reprocess_result_with_postprocess_failed_sibling_keeps_original_mapping_open(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-postprocess-failed-reprocess",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-postprocess-failed-reprocess",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-deal-postprocess-failed",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="postprocess_failed",
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-postprocess-failed-reprocess",
            result={
                "record_id": "rec-deal-postprocess-failed",
                "state": "postprocess_failed",
            },
        )

        original_record = self.store.get_record("rec-listing-postprocess-failed-reprocess")
        self.assertEqual(original_record["state"], "pending_mapping")
        self.assertEqual(original_record["last_error_type"], "")
        self.assertEqual(self.store.count_pending_mappings(), 1)
        with sqlite3.connect(self.store.db_path) as conn:
            supersede_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("record_superseded_by_reprocess",),
            ).fetchone()[0]
        self.assertEqual(supersede_audits, 0)

    def test_reprocess_result_with_skipped_sibling_keeps_original_mapping_open(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-skipped-reprocess",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-skipped-reprocess",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-deal-skipped",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="skipped",
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-skipped-reprocess",
            result={"record_id": "rec-deal-skipped", "state": "skipped"},
        )

        original_record = self.store.get_record("rec-listing-skipped-reprocess")
        self.assertEqual(original_record["state"], "pending_mapping")
        self.assertEqual(original_record["last_error_type"], "")
        self.assertEqual(self.store.count_pending_mappings(), 1)
        with sqlite3.connect(self.store.db_path) as conn:
            supersede_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("record_superseded_by_reprocess",),
            ).fetchone()[0]
        self.assertEqual(supersede_audits, 0)

    def test_reprocess_result_with_unknown_sibling_state_keeps_original_mapping_open(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-unknown-state-reprocess",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-unknown-state-reprocess",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )
        self._store_reprocess_record(
            record_id="rec-deal-unknown-state",
            record_family="deal",
            business_id="deal_equity_transfer",
            state="unknown_reprocess_state",
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-unknown-state-reprocess",
            result={
                "record_id": "rec-deal-unknown-state",
                "state": "unknown_reprocess_state",
            },
        )

        self.assertEqual(
            self.store.get_record("rec-listing-unknown-state-reprocess")["state"],
            "pending_mapping",
        )
        self.assertEqual(self.store.count_pending_mappings(), 1)
        with sqlite3.connect(self.store.db_path) as conn:
            supersede_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("record_superseded_by_reprocess",),
            ).fetchone()[0]
        self.assertEqual(supersede_audits, 0)

    def test_reprocess_result_with_same_record_id_does_not_retire_original(self) -> None:
        original = self._store_reprocess_record(
            record_id="rec-listing-same-result",
            record_family="listing",
            business_id="equity_transfer",
            state="pending_mapping",
        )
        self.store.mark_mapping_pending(
            record_id="rec-listing-same-result",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )

        self.repository.record_reprocessed(
            record_id="rec-listing-same-result",
            result={"record_id": "rec-listing-same-result", "state": "pending_mapping"},
        )

        original_record = self.store.get_record("rec-listing-same-result")
        self.assertEqual(original_record["state"], "pending_mapping")
        self.assertEqual(original_record["last_error_type"], "")
        self.assertEqual(self.store.count_pending_mappings(), 1)

    def test_postprocess_refresh_success_does_not_override_missing_artifact_status(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "pipeline-repo-overlay.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>pipeline repository overlay</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-pipeline-overlay",
                revision_hash="hash-pipeline-overlay",
                project_code="G32026SH1000999",
                project_name="仓储层覆盖测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000999"},
                postprocess_payload={"项目编号": "G32026SH1000999"},
                findings=[],
            )
        )
        self.store.record_operation_result(
            "rec-pipeline-overlay",
            kind="reprocess",
            code="source_missing",
            message="source file missing",
            artifact_status="missing",
        )

        self.repository.record_postprocess_refreshed(
            record_id="rec-pipeline-overlay",
            result={"record_id": "rec-pipeline-overlay", "state": "ready"},
        )

        record = self.store.get_record("rec-pipeline-overlay")

        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["artifact_status"], "missing")
        self.assertEqual(record["last_operation_kind"], "postprocess_refresh")
        self.assertEqual(record["last_operation_code"], "ok")

    def test_create_job_rejects_false_metadata_instead_of_persisting_empty_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.repository.create_job("manual_import", metadata=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_create_job_rejects_false_job_type_instead_of_persisting_empty_job_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_type"):
            self.repository.create_job(False, metadata={"source": "test"})  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_create_job_rejects_false_job_id_instead_of_generating_random_job_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_id"):
            self.repository.create_job("manual_import", metadata={"source": "test"}, job_id=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_add_audit_entry_rejects_false_payload_instead_of_persisting_empty_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            self.repository.add_audit_entry("settings_basic_updated", False)  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(audit_count, 0)

    def test_add_audit_entry_rejects_false_action_instead_of_persisting_empty_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "action"):
            self.repository.add_audit_entry(False, {"source": "test"})  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(audit_count, 0)

    def test_set_setting_rejects_false_value_instead_of_persisting_empty_setting(self) -> None:
        with self.assertRaisesRegex(ValueError, "value"):
            self.repository.set_setting("ui.basic", False)  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            setting_count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
            revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
        self.assertEqual(setting_count, 0)
        self.assertEqual(revision_count, 0)

    def test_set_setting_rejects_false_key_instead_of_persisting_empty_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "key"):
            self.repository.set_setting(False, {"default_exchange": "all"})  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            setting_count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
            revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
        self.assertEqual(setting_count, 0)
        self.assertEqual(revision_count, 0)

    def test_get_setting_rejects_false_default_instead_of_reading_empty_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "default must be an object"):
            self.repository.get_setting("ui.basic", default=False)  # type: ignore[arg-type]

    def test_get_setting_returns_default_for_missing_setting(self) -> None:
        default = {"default_exchange": "fallback"}

        payload = self.repository.get_setting("ui.basic", default=default)

        self.assertEqual(payload, {"default_exchange": "fallback"})
        self.assertIsNot(payload, default)

    def test_interrupt_running_jobs_rejects_false_reason_before_mutating_jobs(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)

        with self.assertRaisesRegex(ValueError, "reason must be text"):
            self.repository.interrupt_running_jobs(reason=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.get_job(job_id)["status"], "running")

    def test_interrupt_job_rejects_false_job_id_instead_of_returning_not_interrupted(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_id must be text"):
            self.repository.interrupt_job(False, reason="operator stop")  # type: ignore[arg-type]

    def test_interrupt_job_rejects_false_reason_before_mutating_job(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)

        with self.assertRaisesRegex(ValueError, "reason must be text"):
            self.repository.interrupt_job(job_id, reason=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.get_job(job_id)["status"], "running")

    def test_finish_job_rejects_false_summary_instead_of_persisting_empty_summary(self) -> None:
        job_id = self.store.create_job("one_click")

        with self.assertRaisesRegex(ValueError, "summary"):
            self.repository.finish_job(job_id, status="failed", summary=False)  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "starting")
        self.assertEqual(job["summary"], {})

    def test_finish_job_rejects_false_status_instead_of_persisting_false_text(self) -> None:
        job_id = self.store.create_job("one_click")

        with self.assertRaisesRegex(ValueError, "status must be text"):
            self.repository.finish_job(job_id, status=False, summary={"message": "stop"})  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "starting")
        self.assertEqual(job["summary"], {})

    def test_save_mapping_rule_rejects_false_metadata_instead_of_persisting_default_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.repository.save_mapping_rule(
                source_name="测试主体",
                group_name="测试集团",
                source_type="国资",
                rule_kind="group",
                match_field="transferor",
                target_field="group_name",
                metadata=False,  # type: ignore[arg-type]
            )

        self.assertEqual(self.store.list_mapping_entries(), [])

    def test_update_record_source_to_archive_rejects_empty_archive_path_before_partial_rewire(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "pipeline-repo-source.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>pipeline repository source</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-pipeline-rewire-empty-archive",
                revision_hash="hash-pipeline-rewire-empty-archive",
                project_code="G32026REWIREEMPTY",
                project_name="仓储层空归档拒绝项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026REWIREEMPTY"},
                postprocess_payload={"项目编号": "G32026REWIREEMPTY"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "archive_path"):
            self.repository.update_record_source_to_archive(
                record_id="rec-pipeline-rewire-empty-archive",
                previous_source_file=source_file,
                archive_path="",
            )

        record = self.store.get_record("rec-pipeline-rewire-empty-archive")
        self.assertEqual(record["source_file"], source_file)
        self.assertEqual(record["archive_path"], source_file)

    def test_set_record_archive_path_rejects_empty_archive_path_before_store_call(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "pipeline-repo-set-archive.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>pipeline repository set archive</body></html>")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-pipeline-set-archive",
                revision_hash="hash-pipeline-set-archive",
                project_code="G32026SETARCHIVE",
                project_name="仓储层设置归档项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SETARCHIVE"},
                postprocess_payload={"项目编号": "G32026SETARCHIVE"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "archive_path"):
            self.repository.set_record_archive_path(
                record_id="rec-pipeline-set-archive",
                archive_path="",
            )

        record = self.store.get_record("rec-pipeline-set-archive")
        self.assertEqual(record["archive_path"], source_file)


if __name__ == "__main__":
    unittest.main()
