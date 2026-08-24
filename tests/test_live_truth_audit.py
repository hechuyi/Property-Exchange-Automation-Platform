from __future__ import annotations

import ast
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.live_truth_audit as live_truth_audit
from peap.downloaders.common import DownloadSummary
from peap_core.family_catalog import FamilyDescriptor
from scripts.live_truth_audit import _finalize_download_probe, _record_anomalies


def _descriptor(family_id: str) -> FamilyDescriptor:
    return FamilyDescriptor(
        family_id=family_id,
        canonical_label=family_id.title(),
        aliases=(family_id, family_id.upper()),
        source_ids=(),
        business_ids=(),
        default_product_profile_id=f"desktop_{family_id}",
    )


def _source_tree() -> ast.AST:
    source_path = Path(__file__).resolve().parents[1] / "scripts" / "live_truth_audit.py"
    return ast.parse(source_path.read_text(encoding="utf-8"))


def _main_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "temp_root": "/tmp/peap-live-truth-audit-test",
        "executed_task_count": 1,
        "downloaded_items": 1,
        "ingest": {"attempted": 1, "ok": 1, "failed": 0, "failures": []},
        "db": {"counts": {}, "states": {}, "scopes": []},
        "db_anomalies": {},
        "exports": [{"record_family": "listing", "ok": True}],
        "forbidden_hits": [],
        "task_reports": [{"task_id": "task-ok", "ok": True}],
    }
    report.update(overrides)
    return report


def _main_exit_code_for(report: dict[str, object]) -> int:
    with (
        patch.object(sys, "argv", ["live_truth_audit.py"]),
        patch.object(live_truth_audit, "run_audit", return_value=report),
    ):
        return live_truth_audit.main()


def _spawn_background_sleep(pid_path: str) -> dict[str, int]:
    child = subprocess.Popen(["/bin/sleep", "30"])
    Path(pid_path).write_text(str(child.pid), encoding="utf-8")
    return {"child_pid": child.pid}


class LiveTruthAuditFamilyCatalogTest(unittest.TestCase):
    def test_record_anomalies_accepts_family_resolved_by_catalog(self) -> None:
        record = {
            "record_id": "record-1",
            "record_family": "archive",
            "business_id": "archived_record",
            "source_identity_json": {"source_id": "legacy"},
            "canonical_record": {"record_family": "archive"},
            "state": "draft",
        }

        with patch(
            "scripts.live_truth_audit.get_family_descriptor",
            return_value=_descriptor("archive"),
            create=True,
        ) as get_family_descriptor:
            anomalies = _record_anomalies([record])

        self.assertNotIn("invalid_family", anomalies)
        get_family_descriptor.assert_called_once_with("archive")

    def test_record_anomalies_rejects_unknown_family_from_catalog(self) -> None:
        record = {
            "record_id": "record-1",
            "record_family": "unknown_family",
            "business_id": "archived_record",
            "source_identity_json": {"source_id": "legacy"},
            "canonical_record": {"record_family": "unknown_family"},
            "state": "draft",
        }

        with patch(
            "scripts.live_truth_audit.get_family_descriptor",
            side_effect=KeyError("unknown_family"),
            create=True,
        ) as get_family_descriptor:
            anomalies = _record_anomalies([record])

        self.assertIn("invalid_family", anomalies)
        get_family_descriptor.assert_called_once_with("unknown_family")

    def test_record_anomalies_does_not_keep_local_listing_deal_allowlist(self) -> None:
        tree = _source_tree()
        local_allowlist_found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(operator, ast.NotIn) for operator in node.ops):
                continue
            for comparator in node.comparators:
                if not isinstance(comparator, ast.Set):
                    continue
                values = {
                    element.value
                    for element in comparator.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
                if values == {"listing", "deal"}:
                    local_allowlist_found = True

        self.assertFalse(local_allowlist_found)

    def test_export_loop_iterates_catalog_families_instead_of_listing_deal_tuple(self) -> None:
        tree = _source_tree()
        hardcoded_loop_found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != "record_family":
                continue
            if not isinstance(node.iter, ast.Tuple):
                continue
            values = {
                element.value
                for element in node.iter.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if values == {"listing", "deal"}:
                hardcoded_loop_found = True

        self.assertFalse(hardcoded_loop_found)


class LiveTruthAuditMainExitCodeTest(unittest.TestCase):
    def test_main_returns_nonzero_when_ingest_failed_even_if_download_tasks_ok(self) -> None:
        exit_code = _main_exit_code_for(
            _main_report(
                ingest={"attempted": 1, "ok": 0, "failed": 1, "failures": [{"ok": False}]}
            )
        )

        self.assertNotEqual(0, exit_code)

    def test_main_returns_nonzero_when_export_failed_even_if_download_tasks_ok(self) -> None:
        exit_code = _main_exit_code_for(
            _main_report(exports=[{"record_family": "listing", "ok": False}])
        )

        self.assertNotEqual(0, exit_code)

    def test_main_returns_nonzero_when_db_anomalies_exist_even_if_download_tasks_ok(self) -> None:
        exit_code = _main_exit_code_for(
            _main_report(db_anomalies={"missing_artifact": [{"record_id_hash": "abc"}]})
        )

        self.assertNotEqual(0, exit_code)


class LiveTruthAuditDownloadFinalizerTest(unittest.TestCase):
    def test_finalizer_requires_discovery_manifest_when_listing_saved_is_zero(self) -> None:
        summary = DownloadSummary(saved=0)
        spec = type(
            "Spec",
            (),
            {
                "task_id": "sse:listing:equity_transfer",
                "exchange_code": "sse",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "manifest": type("Manifest", (), {"source_id": "sse"})(),
            },
        )()

        with tempfile.TemporaryDirectory() as temp_dir:
            report_fields, ingestable_downloads = _finalize_download_probe(
                summary,
                spec=spec,
                archive_root=temp_dir,
            )

        self.assertFalse(report_fields["ok"])
        self.assertEqual(
            report_fields["archive_audit"]["issues"][0]["code"],
            "discovery_task_manifest_reference_missing",
        )
        self.assertEqual(ingestable_downloads, [])

    def test_finalizer_fails_closed_when_summary_has_unaccounted_counts(self) -> None:
        summary = DownloadSummary()
        summary.listed_items = 1
        summary.list_unaccounted = 1
        spec = type(
            "Spec",
            (),
            {
                "task_id": "sse:listing:equity_transfer",
                "exchange_code": "sse",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "manifest": type("Manifest", (), {"source_id": "sse"})(),
            },
        )()

        report_fields, ingestable_downloads = _finalize_download_probe(
            summary,
            spec=spec,
            archive_root="/tmp/nonexistent-live-truth-audit",
        )

        self.assertFalse(report_fields["ok"])
        self.assertEqual(report_fields["typed_errors"][0]["error_code"], "sse_unaccounted_download_candidates")
        self.assertEqual(ingestable_downloads, [])

    def test_finalizer_fails_closed_when_archive_audit_fails(self) -> None:
        spec = type(
            "Spec",
            (),
            {
                "task_id": "sse:listing:physical_asset",
                "exchange_code": "sse",
                "record_family": "listing",
                "business_id": "physical_asset",
                "manifest": type("Manifest", (), {"source_id": "sse"})(),
            },
        )()
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "2026年7月", "P001-missing-sidecar.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            Path(html_path).write_text("<html><body>P001</body></html>", encoding="utf-8")
            summary = DownloadSummary(saved=1)
            summary.downloaded_this_run.add(os.path.relpath(html_path, temp_dir))

            report_fields, ingestable_downloads = _finalize_download_probe(
                summary,
                spec=spec,
                archive_root=temp_dir,
            )

        self.assertFalse(report_fields["ok"])
        self.assertIn(
            "missing_detail_sidecar",
            {issue["code"] for issue in report_fields["archive_audit"]["issues"]},
        )
        self.assertEqual(ingestable_downloads, [])


class LiveTruthAuditTaskIsolationTest(unittest.TestCase):
    def test_audit_download_loop_uses_process_isolation_not_sigalrm(self) -> None:
        tree = _source_tree()
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called_attributes = {
            (node.func.value.id, node.func.attr)
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            )
        }

        self.assertIn("_run_download_task_isolated", called_names)
        self.assertNotIn("_task_timeout", called_names)
        self.assertNotIn(("signal", "alarm"), called_attributes)

    def test_isolated_process_returns_json_serializable_result(self) -> None:
        outcome = live_truth_audit._run_in_isolated_process(
            sum,
            ([2, 3, 5],),
            timeout_seconds=5,
        )

        self.assertEqual(outcome, {"status": "ok", "result": 10})

    def test_isolated_process_reports_child_exception(self) -> None:
        outcome = live_truth_audit._run_in_isolated_process(
            int,
            ("not-an-integer",),
            timeout_seconds=5,
        )

        self.assertEqual(outcome["status"], "error")
        self.assertEqual(outcome["error_type"], "ValueError")
        self.assertIn("invalid literal", outcome["error"])
        self.assertTrue(outcome["trace_tail"])

    def test_normal_worker_exit_cleans_background_process_from_task_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "child.pid"

            outcome = live_truth_audit._run_in_isolated_process(
                _spawn_background_sleep,
                (str(child_pid_path),),
                timeout_seconds=5,
                termination_grace_seconds=0.1,
            )

            self.assertEqual(outcome["status"], "ok")
            child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
            self.assertEqual(outcome["result"], {"child_pid": child_pid})
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_timeout_kills_task_process_group_including_term_ignoring_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            shell_pid_path = Path(temp_dir) / "shell.pid"
            child_pid_path = Path(temp_dir) / "child.pid"
            command = [
                "/bin/sh",
                "-c",
                (
                    f"echo $$ > {shlex.quote(str(shell_pid_path))}; "
                    "trap '' TERM; "
                    "sleep 30 & "
                    f"echo $! > {shlex.quote(str(child_pid_path))}; "
                    "wait"
                ),
            ]

            started_at = time.monotonic()
            outcome = live_truth_audit._run_in_isolated_process(
                subprocess.call,
                (command,),
                timeout_seconds=2,
                termination_grace_seconds=0.1,
            )
            elapsed = time.monotonic() - started_at

            self.assertEqual(outcome["status"], "error")
            self.assertEqual(outcome["error_type"], "TaskTimeoutError")
            self.assertLess(elapsed, 5)
            self.assertTrue(shell_pid_path.is_file())
            self.assertTrue(child_pid_path.is_file())
            process_ids = {
                int(shell_pid_path.read_text(encoding="utf-8").strip()),
                int(child_pid_path.read_text(encoding="utf-8").strip()),
            }
            deadline = time.monotonic() + 2
            surviving = set(process_ids)
            while surviving and time.monotonic() < deadline:
                for process_id in tuple(surviving):
                    try:
                        os.kill(process_id, 0)
                    except ProcessLookupError:
                        surviving.remove(process_id)
                if surviving:
                    time.sleep(0.02)
            self.assertEqual(surviving, set())


if __name__ == "__main__":
    unittest.main()
