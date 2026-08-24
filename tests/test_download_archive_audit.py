from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from peap.download_archive_audit import (
    audit_download_archive_root,
    audit_download_verify_report,
)


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_html_with_sidecar(
    root: str,
    *,
    task_component: str = "sse__listing__equity_transfer",
    html_name: str = "P001-project.html",
    html: str = "<html><body>P001 project</body></html>",
    sidecar_overrides: dict[str, object] | None = None,
) -> str:
    html_path = os.path.join(root, task_component, "2026年7月", html_name)
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    payload: dict[str, object] = {
        "task_id": "sse:listing:equity_transfer",
        "source_id": "sse",
        "record_family": "listing",
        "business_id": "equity_transfer",
        "source_url": "https://www.suaee.com/project/listing/P001-project.html",
        "http_status": 200,
        "save_status": "complete",
        "archive_content_sha256": _sha256_text(html),
        "archive_content_bytes": len(html.encode("utf-8")),
    }
    payload.update(sidecar_overrides or {})
    with open(os.path.splitext(html_path)[0] + ".json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return html_path


def _write_html_with_status_marker(
    root: str,
    *,
    task_component: str = "sse__listing__equity_transfer",
    html_name: str = "P001-project.html",
    html: str = "<html><body>P001 project</body></html>",
    marker_overrides: dict[str, object] | None = None,
) -> str:
    html_path = os.path.join(root, task_component, "2026年7月", html_name)
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    payload: dict[str, object] = {
        "task_id": "sse:listing:equity_transfer",
        "source_id": "sse",
        "record_family": "listing",
        "business_id": "equity_transfer",
        "source_url": "https://www.suaee.com/project/listing/P001-project.html",
        "http_status": 200,
        "save_status": "complete",
        "archive_content_sha256": _sha256_text(html),
        "archive_content_bytes": len(html.encode("utf-8")),
    }
    payload.update(marker_overrides or {})
    with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return html_path


class DownloadArchiveAuditTest(unittest.TestCase):
    def test_archive_audit_accepts_public_resource_mhtml_with_independent_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = "MIME-Version: 1.0\nContent-Type: multipart/related\n"
            _write_html_with_sidecar(
                temp_dir,
                task_component="public_resource__deal__deal_equity_transfer",
                html_name="D32026PR000001-project.mhtml",
                html=body,
                sidecar_overrides={
                    "task_id": "public_resource:deal:deal_equity_transfer",
                    "source_id": "public_resource",
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_url": "https://www.ggzy.gov.cn/information/deal/html/a/example.html",
                },
            )

            result = audit_download_archive_root(temp_dir)

        self.assertTrue(result.ok)
        self.assertEqual(result.html_count, 1)
        self.assertEqual(result.sidecar_count, 1)

    def test_archive_audit_accepts_complete_sidecar_and_ignores_evidence_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(temp_dir)
            evidence_dir = os.path.join(temp_dir, "sse__listing__equity_transfer", "_evidence")
            os.makedirs(evidence_dir, exist_ok=True)
            with open(os.path.join(evidence_dir, "list_page_1.json"), "w", encoding="utf-8") as handle:
                handle.write('{"list": "not a detail sidecar"}')

            result = audit_download_archive_root(temp_dir)

        self.assertTrue(result.ok)
        self.assertEqual(result.html_count, 1)
        self.assertEqual(result.issue_count, 0)

    def test_archive_audit_accepts_direct_scoped_task_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scoped_root = os.path.join(temp_dir, "cbex__deal__deal_equity_transfer")
            _write_html_with_sidecar(
                scoped_root,
                task_component="",
                sidecar_overrides={
                    "task_id": "cbex:deal:deal_equity_transfer",
                    "source_id": "cbex",
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                },
            )

            result = audit_download_archive_root(scoped_root)

        self.assertTrue(result.ok)
        self.assertEqual(result.issue_count, 0)

    def test_archive_audit_rejects_flat_deal_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            flat_root = os.path.join(temp_dir, "deal")
            _write_html_with_sidecar(flat_root, task_component="")

            result = audit_download_archive_root(flat_root)

        self.assertFalse(result.ok)
        self.assertIn("archive_scope_missing", {issue.code for issue in result.issues})

    def test_archive_audit_requires_all_sidecar_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(temp_dir, sidecar_overrides={"record_family": ""})

            result = audit_download_archive_root(temp_dir)

        self.assertFalse(result.ok)
        self.assertIn("record_family_missing", {issue.code for issue in result.issues})

    def test_archive_audit_rejects_invalid_shell_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = _write_html_with_sidecar(temp_dir)
            with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                json.dump({"page_kind": "invalid_shell"}, handle)

            result = audit_download_archive_root(temp_dir)

        self.assertFalse(result.ok)
        self.assertIn("invalid_shell_evidence", {issue.code for issue in result.issues})

    def test_archive_audit_accepts_complete_status_marker_when_detail_sidecar_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_status_marker(temp_dir)

            result = audit_download_archive_root(temp_dir)

        self.assertTrue(result.ok)
        self.assertEqual(result.html_count, 1)
        self.assertEqual(result.sidecar_count, 1)
        self.assertEqual(result.issue_count, 0)

    def test_archive_audit_requires_detail_sidecar_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_status_marker(temp_dir)

            result = audit_download_archive_root(temp_dir, require_detail_sidecar=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.html_count, 1)
        self.assertEqual(result.sidecar_count, 0)
        self.assertEqual(result.issue_count, 1)
        self.assertEqual(result.issues[0].code, "missing_detail_sidecar")

    def test_archive_audit_fails_on_missing_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(temp_dir, sidecar_overrides={"source_url": ""})
            _write_html_with_status_marker(
                temp_dir,
                html_name="P002-marker.html",
                marker_overrides={"source_url": ""},
            )

            result = audit_download_archive_root(temp_dir)

        self.assertFalse(result.ok)
        codes = [issue.code for issue in result.issues]
        self.assertEqual(codes.count("source_url_missing"), 2)

    def test_archive_audit_fails_on_non_absolute_source_url(self) -> None:
        for value in ("/project/P001", "project/P001", "file:///tmp/P001.html"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                _write_html_with_sidecar(
                    temp_dir,
                    sidecar_overrides={"source_url": value},
                )

                result = audit_download_archive_root(temp_dir)

            self.assertFalse(result.ok)
            self.assertIn("source_url_invalid", {issue.code for issue in result.issues})

    def test_archive_audit_fails_on_invalid_http_status(self) -> None:
        for value in (None, "", 0, 99, 302, 404, 503, 600, "ok"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                _write_html_with_sidecar(temp_dir, sidecar_overrides={"http_status": value})

                result = audit_download_archive_root(temp_dir)

            self.assertFalse(result.ok)
            self.assertIn("invalid_http_status", {issue.code for issue in result.issues})

    def test_archive_audit_fails_on_missing_or_mismatched_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(
                temp_dir,
                html_name="P001-good.html",
            )
            _write_html_with_sidecar(
                temp_dir,
                html_name="P002-bad-hash.html",
                sidecar_overrides={"archive_content_sha256": "sha256:" + "0" * 64},
            )
            missing_sidecar = os.path.join(
                temp_dir,
                "sse__listing__equity_transfer",
                "2026年7月",
                "P003-missing-sidecar.html",
            )
            os.makedirs(os.path.dirname(missing_sidecar), exist_ok=True)
            with open(missing_sidecar, "w", encoding="utf-8") as handle:
                handle.write("<html><body>missing sidecar</body></html>")

            result = audit_download_archive_root(temp_dir)

        self.assertFalse(result.ok)
        self.assertEqual(result.html_count, 3)
        self.assertIn("archive_hash_mismatch", {issue.code for issue in result.issues})
        self.assertIn("missing_sidecar", {issue.code for issue in result.issues})

    def test_archive_audit_fails_on_partial_artifact_and_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(
                temp_dir,
                sidecar_overrides={"source_id": "cbex"},
            )
            part_dir = os.path.join(
                temp_dir,
                "sse__listing__equity_transfer",
                "2026年7月",
                "P001-project_files.part",
            )
            os.makedirs(part_dir, exist_ok=True)
            with open(os.path.join(part_dir, "asset.png"), "wb") as handle:
                handle.write(b"partial")

            result = audit_download_archive_root(temp_dir)

        self.assertFalse(result.ok)
        self.assertIn("partial_artifact_leftover", {issue.code for issue in result.issues})
        self.assertIn("source_id_mismatch", {issue.code for issue in result.issues})

    def test_duplicate_verify_report_fails_when_second_run_saved_or_diffed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "duplicate_verify.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "results": [
                            {
                                "task_id": "sse:listing:equity_transfer",
                                "second_summary": {"saved": 1, "skipped_by_resume": 0},
                                "snapshot_diff": {
                                    "added": ["new.html"],
                                    "removed": [],
                                    "changed_content": [],
                                    "touched_same_content": [],
                                },
                                "integrity_failures": [],
                            }
                        ]
                    },
                    handle,
                    ensure_ascii=False,
                )

            result = audit_download_verify_report(report_path)

        self.assertFalse(result.ok)
        self.assertIn("second_run_saved", {issue.code for issue in result.issues})
        self.assertIn("snapshot_added", {issue.code for issue in result.issues})

    def test_cli_audits_archive_root_as_json_and_uses_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_html_with_sidecar(temp_dir)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "peap.download_archive_audit",
                    "archive",
                    temp_dir,
                ],
                check=False,
                cwd=os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["html_count"], 1)
        self.assertEqual(payload["issue_count"], 0)

    def test_cli_returns_nonzero_for_archive_audit_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_sidecar = os.path.join(
                temp_dir,
                "sse__listing__equity_transfer",
                "2026年7月",
                "P003-missing-sidecar.html",
            )
            os.makedirs(os.path.dirname(missing_sidecar), exist_ok=True)
            with open(missing_sidecar, "w", encoding="utf-8") as handle:
                handle.write("<html><body>missing sidecar</body></html>")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "peap.download_archive_audit",
                    "archive",
                    temp_dir,
                ],
                check=False,
                cwd=os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["issues"][0]["code"], "missing_sidecar")

    def test_cli_audits_duplicate_verify_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "duplicate_verify.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "manifest": {"effective_total_html": 1},
                        "results": [
                            {
                                "task_id": "sse:listing:equity_transfer",
                                "second_summary": {"saved": 0},
                                "snapshot_diff": {
                                    "added": [],
                                    "removed": [],
                                    "changed_content": [],
                                    "touched_same_content": [],
                                },
                                "integrity_failures": [],
                            }
                        ],
                    },
                    handle,
                    ensure_ascii=False,
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "peap.download_archive_audit",
                    "duplicate-report",
                    report_path,
                ],
                check=False,
                cwd=os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["html_count"], 1)


if __name__ == "__main__":
    unittest.main()
