from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from path_isolation import assert_peap_env_under_temp, isolated_peap_env

import peap.artifact_truth as artifact_truth
from peap.artifact_truth import (
    EvidenceVerdict,
    classify_artifact_evidence_verdict,
    declared_artifact_is_available,
    declared_artifact_is_missing,
    resolve_artifact_evidence_verdict,
    resolve_declared_artifact_presence,
)


class ArtifactTruthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.env_temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.env_temp_dir.cleanup)
        self.env_patch = mock.patch.dict(os.environ, isolated_peap_env(self.env_temp_dir.name))
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        assert_peap_env_under_temp(self, self.env_temp_dir.name)

    def _write_sidecar(self, artifact_path: str, payload: dict[str, object]) -> None:
        with open(f"{artifact_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)

    def test_archive_path_is_authoritative_over_existing_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.html")
            missing_archive = os.path.join(tmp_dir, "missing-archive.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            presence = resolve_declared_artifact_presence(
                source_file=source_file,
                archive_path=missing_archive,
            )

            self.assertEqual(presence.status, "missing")
            self.assertEqual(presence.authoritative_path, missing_archive)
            self.assertEqual(presence.checked_paths, (missing_archive,))

    def test_source_file_is_used_when_archive_path_is_undeclared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            presence = resolve_declared_artifact_presence(source_file=source_file)

            self.assertEqual(presence.status, "available")
            self.assertEqual(presence.authoritative_path, source_file)

    def test_no_declared_paths_is_undeclared_not_available(self) -> None:
        presence = resolve_declared_artifact_presence()

        self.assertEqual(presence.status, "undeclared")
        self.assertFalse(presence.available)
        self.assertFalse(presence.missing)

    def test_non_string_artifact_path_is_rejected_instead_of_stringified(self) -> None:
        invalid_paths: tuple[object, ...] = (False, 123, ["artifact.html"], {"path": "artifact.html"})

        for invalid_path in invalid_paths:
            with self.subTest(invalid_path=invalid_path):
                with self.assertRaises(TypeError):
                    resolve_declared_artifact_presence(source_file=invalid_path)

    def test_pathlike_artifact_path_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source.txt"
            source_file.write_text("source evidence", encoding="utf-8")

            presence = resolve_declared_artifact_presence(source_file=source_file)

            self.assertEqual(presence.status, "available")
            self.assertEqual(presence.authoritative_path, str(source_file))

    def test_numeric_project_code_is_rejected_as_invalid_identity_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "archive.txt")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            with self.assertRaisesRegex(TypeError, "project_code must be a string"):
                resolve_artifact_evidence_verdict(
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                        "project_code": 123,
                        "source_identity": {"project_code": "456"},
                        "archive_path": archive_path,
                    }
                )

    def test_non_mapping_record_is_rejected_instead_of_coerced_to_dict(self) -> None:
        invalid_records: tuple[object, ...] = (
            [],
            [("record_family", "deal")],
        )

        for invalid_record in invalid_records:
            with self.subTest(invalid_record=invalid_record):
                with self.assertRaisesRegex(TypeError, "record must be a mapping"):
                    resolve_artifact_evidence_verdict(invalid_record)

    def test_none_record_can_still_use_overrides(self) -> None:
        verdict = resolve_artifact_evidence_verdict(
            record=None,
            record_family="deal",
            business_id="deal_equity_transfer",
            exchange="sse",
            project_code="PRJ-001",
        )

        self.assertEqual(verdict.status, "undeclared")
        self.assertEqual(verdict.reason_code, "artifact_path_undeclared")

    def test_legacy_boolean_helpers_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            missing_archive = os.path.join(tmp_dir, "missing.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            self.assertTrue(declared_artifact_is_available(source_file=source_file))
            self.assertFalse(declared_artifact_is_missing(source_file=source_file))
            self.assertFalse(
                declared_artifact_is_available(
                    source_file=source_file,
                    archive_path=missing_archive,
                )
            )
            self.assertTrue(
                declared_artifact_is_missing(
                    source_file=source_file,
                    archive_path=missing_archive,
                )
            )

    def test_missing_authoritative_archive_with_existing_source_returns_stale_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            missing_archive = os.path.join(tmp_dir, "missing-archive.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "source_file": source_file,
                    "archive_path": missing_archive,
                }
            )

            self.assertEqual(verdict.status, "stale_reference")
            self.assertEqual(verdict.authoritative_path, missing_archive)
            self.assertEqual(verdict.inspection_openable_path, "")

    def test_no_declared_artifact_path_returns_undeclared(self) -> None:
        verdict = resolve_artifact_evidence_verdict(
            {
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "exchange": "sse",
                "project_code": "PRJ-001",
            }
        )

        self.assertEqual(verdict.status, "undeclared")
        self.assertEqual(verdict.authoritative_path, "")
        self.assertEqual(verdict.reason_code, "artifact_path_undeclared")

    def test_existing_source_with_unresolved_identity_returns_present_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "source_file": source_file,
                }
            )

            self.assertEqual(verdict.status, "present_unverified")
            self.assertEqual(verdict.identity_confidence, "unresolved")
            self.assertEqual(verdict.inspection_openable_path, source_file)

    def test_invalid_source_identity_json_is_not_silently_treated_as_empty_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            with self.assertRaises(json.JSONDecodeError):
                resolve_artifact_evidence_verdict(
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                        "project_code": "PRJ-001",
                        "source_identity_json": "{",
                        "source_file": source_file,
                    }
                )

    def test_classification_surfaces_invalid_source_identity_json_instead_of_treating_it_as_empty(self) -> None:
        verdict = EvidenceVerdict(
            status="verified",
            logical_record_identity="logical:fixture",
            identity_confidence="verified",
            authoritative_path="/tmp/archive.txt",
            inspection_openable_path="/tmp/archive.txt",
            reason_code="identity_verified_artifact_present",
            safe_evidence={},
        )

        with self.assertRaises(json.JSONDecodeError):
            classify_artifact_evidence_verdict(
                {"project_code": "PRJ-001", "source_identity_json": "{"},
                verdict,
            )

    def test_non_object_source_identity_json_is_not_treated_as_empty_identity(self) -> None:
        with self.assertRaises(TypeError):
            classify_artifact_evidence_verdict(
                {"project_code": "PRJ-001", "source_identity_json": "[]"},
                EvidenceVerdict(
                    status="verified",
                    logical_record_identity="logical:fixture",
                    identity_confidence="verified",
                    authoritative_path="/tmp/archive.txt",
                    inspection_openable_path="/tmp/archive.txt",
                    reason_code="identity_verified_artifact_present",
                    safe_evidence={},
                ),
            )

    def test_non_object_source_identity_is_not_treated_as_empty_identity(self) -> None:
        with self.assertRaises(TypeError):
            classify_artifact_evidence_verdict(
                {"project_code": "PRJ-001", "source_identity": ["not", "an", "object"]},
                EvidenceVerdict(
                    status="verified",
                    logical_record_identity="logical:fixture",
                    identity_confidence="verified",
                    authoritative_path="/tmp/archive.txt",
                    inspection_openable_path="/tmp/archive.txt",
                    reason_code="identity_verified_artifact_present",
                    safe_evidence={},
                ),
            )

    def test_verified_identity_plus_existing_authoritative_path_returns_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "archive.txt")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("source evidence")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "verified")
            self.assertEqual(verdict.identity_confidence, "verified")
            self.assertEqual(verdict.authoritative_path, archive_path)
            self.assertEqual(verdict.inspection_openable_path, archive_path)

    def test_invalid_sidecar_json_is_not_silently_ignored_as_verified_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "archive.txt")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("source evidence")
            with open(f"{archive_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                handle.write("{")

            with self.assertRaises(json.JSONDecodeError):
                resolve_artifact_evidence_verdict(
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                        "project_code": "PRJ-001",
                        "archive_path": archive_path,
                    }
                )

    def test_non_object_sidecar_json_is_not_silently_ignored_as_verified_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "archive.txt")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("source evidence")
            with open(f"{archive_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                handle.write("[]")

            with self.assertRaises(TypeError):
                resolve_artifact_evidence_verdict(
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                        "project_code": "PRJ-001",
                        "archive_path": archive_path,
                    }
                )

    def test_shared_official_page_sidecar_returns_explicit_shared_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "shared.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("shared official page fixture")
            content_sha256 = artifact_truth._sha256_file(archive_path)
            self._write_sidecar(
                archive_path,
                {
                    "schema_version": 1,
                    "page_kind": "shared_official_page",
                    "content_sha256": content_sha256,
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "PRJ-001",
                    },
                    "source_locator_hash": "sha256:1111",
                    "final_locator_hash": "sha256:2222",
                },
            )

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "shared_official_page")
            self.assertEqual(verdict.reason_code, "shared_official_page_explicit")
            self.assertEqual(verdict.identity_confidence, "verified")
            self.assertEqual(verdict.safe_evidence["page_kind"], "shared_official_page")
            self.assertEqual(verdict.safe_evidence["content_sha256"], content_sha256)

    def test_invalid_shell_sidecar_returns_invalid_shell_without_body_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "invalid-shell.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("downloaded shell without current hard-coded marker")
            content_sha256 = artifact_truth._sha256_file(archive_path)
            self._write_sidecar(
                archive_path,
                {
                    "schema_version": 1,
                    "page_kind": "invalid_shell",
                    "content_sha256": content_sha256,
                    "identity_hints": {
                        "project_code_hash": "sha256:"
                        + hashlib.sha256(b"PRJ-001").hexdigest(),
                        "project_name_hash": "sha256:"
                        + hashlib.sha256("fixture project".encode("utf-8")).hexdigest(),
                    },
                    "source_url_hash": "sha256:1111",
                    "final_url_hash": "sha256:2222",
                },
            )

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "project_name": "fixture project",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "invalid_shell")
            self.assertEqual(verdict.reason_code, "invalid_shell_sidecar_explicit")
            self.assertEqual(verdict.safe_evidence["page_kind"], "invalid_shell")
            self.assertEqual(verdict.safe_evidence["source_url_hash"], "sha256:1111")
            self.assertEqual(verdict.safe_evidence["final_url_hash"], "sha256:2222")

    def test_unaccepted_invalid_shell_sidecar_is_not_treated_as_verified(self) -> None:
        cases = [
            {
                "content_sha256": "sha256:not-the-artifact",
                "source_url_hash": "sha256:1111",
                "final_url_hash": "sha256:2222",
            },
            {
                "source_url_hash": "https://example.invalid/polluted-locator",
                "final_url_hash": "sha256:2222",
            },
        ]
        for index, sidecar_overrides in enumerate(cases):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    archive_path = os.path.join(tmp_dir, f"invalid-shell-{index}.html")
                    with open(archive_path, "w", encoding="utf-8") as handle:
                        handle.write("downloaded shell without current hard-coded marker")
                    content_sha256 = artifact_truth._sha256_file(archive_path)
                    payload = {
                        "schema_version": 1,
                        "page_kind": "invalid_shell",
                        "content_sha256": content_sha256,
                        "identity_hints": {
                            "project_code_hash": "sha256:"
                            + hashlib.sha256(b"PRJ-001").hexdigest(),
                        },
                        "source_url_hash": "sha256:1111",
                        "final_url_hash": "sha256:2222",
                    }
                    payload.update(sidecar_overrides)
                    self._write_sidecar(archive_path, payload)

                    verdict = resolve_artifact_evidence_verdict(
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                            "project_code": "PRJ-001",
                            "project_name": "fixture project",
                            "archive_path": archive_path,
                        }
                    )

                    self.assertEqual(verdict.status, "present_unverified")
                    self.assertEqual(verdict.reason_code, "invalid_shell_metadata_unaccepted")

    def test_unaccepted_shared_official_page_sidecar_is_not_treated_as_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "shared.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("shared official page fixture")
            self._write_sidecar(
                archive_path,
                {
                    "schema_version": 1,
                    "page_kind": "shared_official_page",
                    "content_sha256": "sha256:not-the-artifact",
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "PRJ-001",
                    },
                    "source_locator_hash": "sha256:1111",
                    "final_locator_hash": "sha256:2222",
                },
            )

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "present_unverified")
            self.assertEqual(verdict.reason_code, "shared_official_page_metadata_unaccepted")

    def test_sidecar_acceptance_rejects_non_object_identity_components(self) -> None:
        with self.assertRaisesRegex(TypeError, "identity_components"):
            artifact_truth._shared_official_page_sidecar_is_accepted(
                {
                    "schema_version": 1,
                    "page_kind": "shared_official_page",
                    "content_sha256": "sha256:artifact",
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "PRJ-001",
                    },
                    "source_locator_hash": "sha256:1111",
                    "final_locator_hash": "sha256:2222",
                },
                content_sha256="sha256:artifact",
                identity_components=[],
            )

    def test_sidecar_resolution_requires_identity_components_contract(self) -> None:
        class BrokenIdentity:
            logical_record_identity = "logical:fixture"
            identity_confidence = "verified"

        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "invalid-shell.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("downloaded shell without current hard-coded marker")
            content_sha256 = artifact_truth._sha256_file(archive_path)
            self._write_sidecar(
                archive_path,
                {
                    "schema_version": 1,
                    "page_kind": "invalid_shell",
                    "content_sha256": content_sha256,
                    "identity_hints": {
                        "project_code_hash": "sha256:" + hashlib.sha256(b"PRJ-001").hexdigest(),
                    },
                    "source_url_hash": "sha256:1111",
                    "final_url_hash": "sha256:2222",
                },
            )

            with mock.patch("peap.artifact_truth.resolve_logical_record_identity", return_value=BrokenIdentity()):
                with self.assertRaisesRegex(TypeError, "logical record identity components"):
                    resolve_artifact_evidence_verdict(
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                            "project_code": "PRJ-001",
                            "archive_path": archive_path,
                        }
                    )

    def test_numeric_sidecar_content_sha256_is_treated_as_text_not_invalid_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "shared.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("shared official page fixture")
            self._write_sidecar(
                archive_path,
                {
                    "schema_version": 1,
                    "page_kind": "shared_official_page",
                    "content_sha256": 123,
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "PRJ-001",
                    },
                    "source_locator_hash": "sha256:1111",
                    "final_locator_hash": "sha256:2222",
                },
            )

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "present_unverified")
            self.assertEqual(verdict.reason_code, "shared_official_page_metadata_unaccepted")

    def test_sse_deal_shell_fixture_returns_invalid_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, "archive.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("<h1>SSE Deal Notice</h1>")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

            self.assertEqual(verdict.status, "invalid_shell")
            self.assertEqual(verdict.reason_code, "sse_deal_notice_shell")
            self.assertEqual(verdict.inspection_openable_path, archive_path)

    def test_invalid_shell_detection_reads_only_first_64_kib(self) -> None:
        read_sizes: list[int] = []

        class RecordingReader:
            def __enter__(self) -> "RecordingReader":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                read_sizes.append(size)
                return b"source evidence"

        with mock.patch("peap.artifact_truth._sha256_file", return_value="sha256:abc"):
            with mock.patch("peap.artifact_truth.os.path.isfile", return_value=True):
                with mock.patch.object(artifact_truth, "open", return_value=RecordingReader()):
                    verdict = resolve_artifact_evidence_verdict(
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                            "project_code": "PRJ-001",
                            "archive_path": "/tmp/declared-existing.txt",
                        }
                    )

        self.assertEqual(verdict.status, "verified")
        self.assertEqual(read_sizes, [64 * 1024])

    def test_managed_provenance_can_be_openable_for_stale_reference_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            missing_archive = os.path.join(tmp_dir, "missing-archive.txt")
            managed_provenance = os.path.join(tmp_dir, "managed-provenance.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source evidence")
            with open(managed_provenance, "w", encoding="utf-8") as handle:
                handle.write("managed provenance")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "source_file": source_file,
                    "archive_path": missing_archive,
                    "managed_provenance_path": managed_provenance,
                }
            )

            self.assertEqual(verdict.status, "stale_reference")
            self.assertEqual(verdict.authoritative_path, missing_archive)
            self.assertEqual(verdict.inspection_openable_path, managed_provenance)

    def test_safe_evidence_contains_hashes_and_locators_without_raw_page_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = os.path.join(tmp_dir, "source.txt")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("unique source evidence body")

            verdict = resolve_artifact_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "source_file": source_file,
                }
            )

            self.assertEqual(verdict.safe_evidence["content_sha256"][:7], "sha256:")
            serialized_safe_evidence = repr(verdict.safe_evidence)
            self.assertIn(source_file, serialized_safe_evidence)
            self.assertNotIn("unique source evidence body", serialized_safe_evidence)


if __name__ == "__main__":
    unittest.main()
