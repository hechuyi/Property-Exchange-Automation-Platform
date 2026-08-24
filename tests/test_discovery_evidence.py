from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peap.download_archive_audit import audit_discovery_evidence_root
from peap.downloaders.common import HttpFetchedText
from peap.downloaders.discovery_evidence import (
    DiscoveryEvidenceError,
    DiscoveryQueryEvidence,
    DiscoveryTaskEvidence,
)
from peap.downloaders.listing_exchanges import GuangdongEquityTransferDownloader


def _response(body: str, *, page: int = 1) -> HttpFetchedText:
    url = f"https://exchange.example/api/projects?page={page}"
    return HttpFetchedText(
        body,
        source_url=url,
        final_url=url,
        http_status=200,
        raw_bytes=body.encode("utf-8"),
    )


class DiscoveryQueryEvidenceTest(unittest.TestCase):
    def test_capture_fails_closed_when_query_directory_is_replaced_by_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-symlink-query",
                query_id="equity",
            )
            query_dir = Path(query.query_dir)
            query_dir.rename(query_dir.with_name("query-real"))
            Path(query_dir).symlink_to(Path(outside), target_is_directory=True)

            with self.assertRaisesRegex(DiscoveryEvidenceError, "symlinks"):
                query.capture_page(
                    page_index=1,
                    response=_response('{"rows":[]}'),
                    body_format="json",
                )

    def test_capture_fails_closed_when_page_sidecar_is_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-symlink-sidecar",
                query_id="equity",
            )
            sidecar_path = Path(query.query_dir) / "page_000001.meta.json"
            target = Path(outside) / "sidecar.json"
            target.write_text("{}", encoding="utf-8")
            sidecar_path.symlink_to(target)

            with self.assertRaisesRegex(DiscoveryEvidenceError, "symlinks"):
                query.capture_page(
                    page_index=1,
                    response=_response('{"rows":[]}'),
                    body_format="json",
                )

    def test_capture_rejects_text_that_has_no_original_response_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
            )
            response = HttpFetchedText(
                '{"rows":[]}',
                source_url="https://exchange.example/api/projects",
                final_url="https://exchange.example/api/projects",
                http_status=200,
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "original response bytes"):
                query.capture_page(
                    page_index=1,
                    response=response,
                    body_format="json",
                )

    def test_complete_query_archives_raw_pages_and_auditable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-1",
                query_id="equity-gplx-2",
                authoritative_total=True,
            )

            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"},{"id":"B"}]}'),
                body_format="json",
                extracted_row_count=2,
                row_identity_values=("A", "B"),
                declared_total_items=3,
                declared_total_pages=2,
            )
            query.record_page(
                page_index=2,
                response=_response('{"rows":[{"id":"C"}]}', page=2),
                body_format="json",
                extracted_row_count=1,
                row_identity_values=("C",),
                declared_total_items=3,
                declared_total_pages=2,
            )
            manifest_path = Path(
                query.complete(termination_reason="declared_pages_exhausted")
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["coverage_status"], "complete")
            self.assertEqual(manifest["archived_page_count"], 2)
            self.assertEqual(manifest["observed_row_count"], 3)
            self.assertEqual(manifest["declared_total_items"], 3)
            self.assertEqual(manifest["declared_total_pages"], 2)

            page_path = manifest_path.parent / "page_000001.raw.json"
            sidecar_path = manifest_path.parent / "page_000001.meta.json"
            self.assertEqual(
                page_path.read_text(encoding="utf-8"),
                '{"rows":[{"id":"A"},{"id":"B"}]}',
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["source_url"], "https://exchange.example/api/projects?page=1")
            self.assertEqual(sidecar["http_status"], 200)
            self.assertEqual(sidecar["save_status"], "complete")
            self.assertTrue(str(sidecar["archive_content_sha256"]).startswith("sha256:"))

            audit = audit_discovery_evidence_root(
                temp_dir,
                require_task_manifest=False,
            )
            self.assertTrue(audit.ok, audit.to_dict())
            self.assertEqual(audit.manifest_count, 1)
            self.assertEqual(audit.page_count, 2)

    def test_complete_fails_closed_when_declared_page_count_is_not_covered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="tpre",
                task_id="tpre:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
            )
            query.record_page(
                page_index=1,
                response=_response('{"records":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=20,
                declared_total_pages=2,
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "declared pages"):
                query.complete(termination_reason="declared_pages_exhausted")

            manifest = json.loads(Path(query.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(manifest["coverage_status"], "failed")

    def test_short_page_termination_requires_an_actually_short_last_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="guangdong",
                task_id="guangdong:listing:equity_transfer",
                run_id="run-1",
                query_id="listing",
                page_size=2,
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"},{"id":"B"}]}'),
                body_format="json",
                extracted_row_count=2,
                row_identity_values=("A", "B"),
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "not short"):
                query.complete(termination_reason="short_page")

    def test_next_link_termination_requires_the_adapter_to_record_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="cquae",
                task_id="cquae:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
            )
            query.record_page(
                page_index=1,
                response=_response("<html>one item</html>"),
                body_format="html",
                extracted_row_count=1,
                row_identity_values=("A",),
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "next_link_present"):
                query.complete(termination_reason="next_link_absent")

    def test_complete_fails_closed_when_authoritative_total_does_not_match_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="cbex",
                task_id="cbex:listing:physical_asset",
                run_id="run-1",
                query_id="house",
                authoritative_total=True,
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"}]}'),
                body_format="jsonp",
                extracted_row_count=1,
                row_identity_values=("A",),
                declared_total_items=2,
                declared_total_pages=1,
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "declared total"):
                query.complete(termination_reason="declared_pages_exhausted")

    def test_duplicate_nonempty_page_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:physical_asset",
                run_id="run-1",
                query_id="asset",
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"}]}'),
                body_format="json",
                extracted_row_count=1,
                row_identity_values=("A",),
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "repeated page identity"):
                query.record_page(
                    page_index=2,
                    response=_response('{"rows":[{"id":"A"}]}', page=2),
                    body_format="json",
                    extracted_row_count=1,
                    row_identity_values=("A",),
                )

    def test_partial_cross_page_identity_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="cbex",
                task_id="cbex:listing:physical_asset",
                run_id="run-1",
                query_id="house",
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"},{"id":"B"}]}'),
                body_format="jsonp",
                extracted_row_count=2,
                row_identity_values=("A", "B"),
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "overlapping row identities"):
                query.record_page(
                    page_index=2,
                    response=_response(
                        '{"rows":[{"id":"B"},{"id":"C"}]}',
                        page=2,
                    ),
                    body_format="jsonp",
                    extracted_row_count=2,
                    row_identity_values=("B", "C"),
                )

            sidecar = json.loads(
                (Path(query.query_dir) / "page_000002.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["save_status"], "complete")
            self.assertEqual(sidecar["parse_status"], "complete")
            self.assertEqual(sidecar["duplicate_identity_count"], 1)

    def test_nonempty_page_requires_one_identity_for_every_extracted_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="tpre",
                task_id="tpre:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
            )
            query.capture_page(
                page_index=1,
                response=_response('{"records":[{"id":"A"},{"id":null}]}'),
                body_format="json",
            )

            with self.assertRaisesRegex(DiscoveryEvidenceError, "identified rows"):
                query.complete_page(
                    page_index=1,
                    extracted_row_count=2,
                    row_identity_values=("A",),
                )

    def test_unfinalized_context_writes_failed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "network stopped"):
                with DiscoveryQueryEvidence(
                    root=temp_dir,
                    source_id="cquae",
                    task_id="cquae:listing:pre_disclosure",
                    run_id="run-1",
                    query_id="pre",
                ) as query:
                    query.record_page(
                        page_index=1,
                        response=_response("<html>page</html>"),
                        body_format="html",
                        extracted_row_count=1,
                        row_identity_values=("A",),
                    )
                    raise RuntimeError("network stopped")

            manifest = json.loads(Path(query.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(manifest["termination_reason"], "exception")

    def test_schema_failure_keeps_the_raw_response_with_failed_parse_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="cquae",
                task_id="cquae:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
            )
            query.capture_page(
                page_index=1,
                response=_response("<html><body>changed layout</body></html>"),
                body_format="html",
            )
            query.fail_page(
                page_index=1,
                reason="schema_invalid",
                details={"selector": "div.n2_List.itcon"},
            )
            query.fail(termination_reason="schema_invalid")

            manifest_path = Path(query.manifest_path)
            sidecar = json.loads(
                (manifest_path.parent / "page_000001.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["save_status"], "complete")
            self.assertEqual(sidecar["parse_status"], "failed")
            self.assertEqual(sidecar["parse_failure_reason"], "schema_invalid")
            self.assertEqual(
                (manifest_path.parent / "page_000001.raw.html").read_text(encoding="utf-8"),
                "<html><body>changed layout</body></html>",
            )
            self.assertTrue(str(sidecar["archive_content_sha256"]).startswith("sha256:"))

    def test_audit_rejects_tampered_raw_page_and_partial_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-1",
                query_id="equity",
                authoritative_total=True,
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"}]}'),
                body_format="json",
                extracted_row_count=1,
                row_identity_values=("A",),
                declared_total_items=1,
                declared_total_pages=1,
            )
            manifest_path = Path(
                query.complete(termination_reason="declared_pages_exhausted")
            )
            (manifest_path.parent / "page_000001.raw.json").write_text(
                '{"rows":[]}', encoding="utf-8"
            )
            (manifest_path.parent / "orphan.part").write_text("partial", encoding="utf-8")

            audit = audit_discovery_evidence_root(
                temp_dir,
                require_task_manifest=False,
            )
            self.assertFalse(audit.ok)
            self.assertIn("discovery_page_hash_mismatch", {issue.code for issue in audit.issues})
            self.assertIn("partial_artifact_leftover", {issue.code for issue in audit.issues})


class DiscoveryTaskEvidenceTest(unittest.TestCase):
    def test_task_constructor_fails_closed_on_symlinked_discovery_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            task_root = Path(temp_dir) / "_evidence" / "run-symlink-task" / "sse__listing__equity_transfer"
            task_root.mkdir(parents=True)
            (task_root / "discovery").symlink_to(Path(outside), target_is_directory=True)

            with self.assertRaisesRegex(DiscoveryEvidenceError, "symlinks"):
                DiscoveryTaskEvidence(
                    root=temp_dir,
                    source_id="sse",
                    task_id="sse:listing:equity_transfer",
                    run_id="run-symlink-task",
                    expected_query_ids=("equity",),
                )

    def test_candidate_subset_rejects_manifest_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-subset-symlink",
                expected_query_ids=("CHANQUAN-gplx-2",),
            )
            query = task.query("CHANQUAN-gplx-2", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()
            reference = task.manifest_reference()
            manifest_path = Path(task.manifest_path)
            external_manifest = Path(outside) / "manifest.json"
            external_manifest.write_bytes(manifest_path.read_bytes())
            manifest_path.unlink()
            manifest_path.symlink_to(external_manifest)

            with self.assertRaisesRegex(DiscoveryEvidenceError, "symlinks"):
                from peap.downloaders.discovery_evidence import verify_discovery_candidate_subset

                verify_discovery_candidate_subset(
                    root=temp_dir,
                    reference=reference,
                    candidate_entries=[],
                    expected_source_id="sse",
                    expected_task_id="sse:listing:equity_transfer",
                    expected_run_id="run-subset-symlink",
                )

    def test_unsafe_run_ids_do_not_collide_after_component_sanitization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run/a",
                expected_query_ids=("equity",),
            )
            second = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run_a",
                expected_query_ids=("equity",),
            )

            self.assertNotEqual(first.discovery_dir, second.discovery_dir)

    def test_task_manifest_reference_is_relative_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-reference",
                expected_query_ids=("equity",),
            )
            query = task.query("equity", authoritative_total=True, page_size=20)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            reference = task.manifest_reference()
            manifest_path = Path(temp_dir, reference["path"])
            manifest_bytes = manifest_path.read_bytes()

            self.assertFalse(Path(reference["path"]).is_absolute())
            self.assertEqual(manifest_path, Path(task.manifest_path))
            self.assertEqual(reference["bytes"], len(manifest_bytes))
            self.assertEqual(
                reference["sha256"],
                "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            )

    def test_task_manifest_reference_binds_scope_and_cannot_be_rebased_after_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-owned",
                expected_query_ids=("equity",),
            )
            query = task.query("equity", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            reference = task.manifest_reference()
            self.assertEqual(reference.get("source_id"), "sse")
            self.assertEqual(reference.get("task_id"), "sse:listing:equity_transfer")
            self.assertEqual(reference.get("run_id"), "run-owned")

            Path(task.manifest_path).write_text("{}", encoding="utf-8")
            self.assertEqual(task.manifest_reference(), reference)

    def test_task_manifest_binds_candidates_and_only_allows_a_discovered_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-candidates",
                expected_query_ids=("equity",),
            )
            query = task.query("equity", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"},{"id":"B"}]}'),
                body_format="json",
                extracted_row_count=2,
                row_identity_values=("A", "B"),
                declared_total_items=2,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            candidates = [
                {"project_code": "A", "page_url": "https://exchange.example/A"},
                {"project_code": "B", "page_url": "https://exchange.example/B"},
            ]
            try:
                task.complete(candidate_entries=candidates)
            except TypeError as exc:
                self.fail(f"task manifest must accept discovered candidates: {exc}")

            manifest = json.loads(Path(task.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest.get("candidate_count"), 2)
            self.assertEqual(len(manifest.get("candidate_fingerprints", [])), 2)

            from peap.downloaders import discovery_evidence

            verify_subset = getattr(
                discovery_evidence,
                "verify_discovery_candidate_subset",
                None,
            )
            self.assertTrue(callable(verify_subset))
            assert verify_subset is not None
            verify_subset(
                root=temp_dir,
                reference=task.manifest_reference(),
                candidate_entries=[candidates[1]],
                expected_source_id="sse",
                expected_task_id="sse:listing:equity_transfer",
                expected_run_id="run-candidates",
            )
            with self.assertRaisesRegex(DiscoveryEvidenceError, "not present"):
                verify_subset(
                    root=temp_dir,
                    reference=task.manifest_reference(),
                    candidate_entries=[
                        {
                            "project_code": "INJECTED",
                            "page_url": "https://exchange.example/injected",
                        }
                    ],
                    expected_source_id="sse",
                    expected_task_id="sse:listing:equity_transfer",
                    expected_run_id="run-candidates",
                )

    def test_discovery_audit_requires_a_task_manifest_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query = DiscoveryQueryEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-query-only",
                query_id="equity",
                authoritative_total=True,
            )
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")

            audit = audit_discovery_evidence_root(temp_dir)

            self.assertFalse(audit.ok)
            self.assertIn(
                "discovery_task_manifest_missing",
                {issue.code for issue in audit.issues},
            )

    def test_task_manifest_fails_closed_when_an_expected_query_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="cbex",
                task_id="cbex:listing:pre_disclosure",
                run_id="run-1",
                expected_query_ids=("equity-pre", "capital-pre"),
            )
            query = task.query("equity-pre", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="jsonp",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")

            with self.assertRaisesRegex(DiscoveryEvidenceError, "missing expected queries"):
                task.complete()

            manifest = json.loads(Path(task.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["coverage_status"], "failed")
            self.assertEqual(manifest["missing_query_ids"], ["capital-pre"])

    def test_complete_task_manifest_binds_every_expected_query_for_strict_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:pre_disclosure",
                run_id="run-1",
                expected_query_ids=("CHANQUAN-gplx-1", "ZENGZI-gplx-1"),
            )
            for index, query_id in enumerate(task.expected_query_ids, start=1):
                query = task.query(query_id, authoritative_total=True)
                query.record_page(
                    page_index=1,
                    response=_response('{"rows":[]}', page=index),
                    body_format="json",
                    extracted_row_count=0,
                    declared_total_items=0,
                    declared_total_pages=1,
                )
                query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            audit = audit_discovery_evidence_root(
                temp_dir,
                require_task_manifest=True,
            )
            self.assertTrue(audit.ok, audit.to_dict())
            self.assertEqual(audit.task_count, 1)
            self.assertEqual(audit.manifest_count, 2)

    def test_strict_audit_accepts_sse_deal_discovery_contracts(self) -> None:
        business_ids = (
            "deal_physical_asset",
            "deal_equity_transfer",
            "deal_capital_increase",
        )
        for business_id in business_ids:
            with (
                self.subTest(business_id=business_id),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                task = DiscoveryTaskEvidence(
                    root=temp_dir,
                    source_id="sse",
                    task_id=f"sse:deal:{business_id}",
                    run_id=f"run-{business_id}",
                    expected_query_ids=("deal-notice-list",),
                )
                query = task.query("deal-notice-list", authoritative_total=True)
                query.record_page(
                    page_index=1,
                    response=_response('{"rows":[]}'),
                    body_format="json",
                    extracted_row_count=0,
                    declared_total_items=0,
                    declared_total_pages=1,
                )
                query.complete(termination_reason="declared_pages_exhausted")
                task.complete()

                audit = audit_discovery_evidence_root(temp_dir)

                self.assertTrue(audit.ok, audit.to_dict())
                self.assertEqual(audit.task_count, 1)
                self.assertEqual(audit.manifest_count, 1)

    def test_strict_audit_rejects_sse_deal_query_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:deal:deal_equity_transfer",
                run_id="run-deal-query-mismatch",
                expected_query_ids=("wrong-deal-query",),
            )
            query = task.query("wrong-deal-query", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            audit = audit_discovery_evidence_root(temp_dir)

            self.assertFalse(audit.ok)
            issues = {issue.code: issue for issue in audit.issues}
            self.assertNotIn("discovery_registry_scope_invalid", issues)
            self.assertEqual(
                issues["discovery_registry_query_set_mismatch"].details,
                {
                    "registry_expected": ["deal-notice-list"],
                    "manifest_expected": ["wrong-deal-query"],
                },
            )

    def test_strict_audit_rejects_task_manifest_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:deal:deal_equity_transfer",
                run_id="run-deal-source-mismatch",
                expected_query_ids=("deal-notice-list",),
            )
            query = task.query("deal-notice-list", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            manifest_path = Path(task.manifest_path)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["source_id"] = "cbex"
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            audit = audit_discovery_evidence_root(temp_dir)

            self.assertFalse(audit.ok)
            self.assertIn(
                "discovery_registry_scope_invalid",
                {issue.code for issue in audit.issues},
            )

    def test_strict_audit_rejects_self_declared_query_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:pre_disclosure",
                run_id="run-subset",
                expected_query_ids=("CHANQUAN-gplx-1",),
            )
            query = task.query("CHANQUAN-gplx-1", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[]}'),
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            audit = audit_discovery_evidence_root(
                temp_dir,
                require_task_manifest=True,
            )

            self.assertFalse(audit.ok)
            self.assertIn(
                "discovery_registry_query_set_mismatch",
                {issue.code for issue in audit.issues},
            )

    def test_strict_audit_rejects_an_inconsistent_candidate_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id="sse:listing:equity_transfer",
                run_id="run-candidate-audit",
                expected_query_ids=("CHANQUAN-gplx-2",),
            )
            query = task.query("CHANQUAN-gplx-2", authoritative_total=True)
            query.record_page(
                page_index=1,
                response=_response('{"rows":[{"id":"A"}]}'),
                body_format="json",
                extracted_row_count=1,
                row_identity_values=("A",),
                declared_total_items=1,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete(
                candidate_entries=[
                    {"project_code": "A", "page_url": "https://example.test/A"}
                ]
            )
            payload = json.loads(Path(task.manifest_path).read_text(encoding="utf-8"))
            payload["candidate_count"] = 2
            Path(task.manifest_path).write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            audit = audit_discovery_evidence_root(temp_dir)

            self.assertFalse(audit.ok)
            self.assertIn(
                "discovery_candidate_set_invalid",
                {issue.code for issue in audit.issues},
            )


class ListingDiscoveryIntegrationTest(unittest.TestCase):
    def test_generic_listing_downloader_archives_the_raw_list_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = GuangdongEquityTransferDownloader(
                html_root=temp_dir,
                page_size=20,
                run_id="run-live-list",
            )
            body = json.dumps(
                {
                    "data": [
                        {
                            "XMID": "G001",
                            "XMBH": "G001",
                            "XMMC": "Guangdong project",
                            "KSRQ": "2026-07-10",
                            "FCLASS": "GQ",
                            "CQLSGX": "GQ100101",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            response = HttpFetchedText(
                body,
                source_url="https://new.gduaee.com/si/prjs/equity/list",
                final_url="https://new.gduaee.com/si/prjs/equity/list",
                http_status=200,
                raw_bytes=body.encode("utf-8"),
            )

            with patch.object(downloader, "_fetch_list_page", return_value=response):
                summary = downloader.run(
                    start_date="2026-07-01",
                    end_date="2026-07-13",
                    list_only=True,
                )

            self.assertEqual(summary.pages_requested, 1)
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertTrue(
                Path(temp_dir, summary.discovery_task_manifest["path"]).is_file()
            )
            task_manifest = json.loads(
                Path(temp_dir, summary.discovery_task_manifest["path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(task_manifest.get("candidate_count"), 1)
            manifest_paths = list(Path(temp_dir).glob("_evidence/**/discovery/**/manifest.json"))
            self.assertEqual(len(manifest_paths), 1)
            manifest = json.loads(manifest_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["termination_reason"], "short_page")
            audit = audit_discovery_evidence_root(
                temp_dir,
                require_task_manifest=True,
            )
            self.assertTrue(audit.ok, audit.to_dict())
            self.assertEqual(audit.task_count, 1)


if __name__ == "__main__":
    unittest.main()
