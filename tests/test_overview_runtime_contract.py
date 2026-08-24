import unittest

from desktop_backend.overview_contract import build_overview_view
from desktop_backend.runtime_contract import build_runtime_view


class OverviewRuntimeContractTests(unittest.TestCase):
    def test_build_runtime_view_rejects_non_boolean_runtime_flags(self) -> None:
        cases = [
            ("browser", "installed", "false"),
            ("install", "running", "false"),
            ("readiness", "ready", "false"),
            ("readiness", "download_ready", 1),
            ("readiness", "browser_runtime_ready", None),
        ]

        for section, field, value in cases:
            runtime = {
                "browser": {"installed": False},
                "install": {"running": False},
                "readiness": {
                    "ready": False,
                    "download_ready": False,
                    "browser_runtime_ready": False,
                },
            }
            runtime[section][field] = value

            with self.subTest(section=section, field=field):
                with self.assertRaisesRegex(ValueError, f"{section}.{field}"):
                    build_runtime_view(runtime)

    def test_build_runtime_view_rejects_non_mapping_runtime_sections(self) -> None:
        cases = [
            ("browser", ["installed", True]),
            ("install", ["running", False]),
            ("readiness", ["ready", True]),
        ]

        for section, value in cases:
            runtime = {
                "browser": {"installed": False},
                "install": {"running": False},
                "readiness": {
                    "ready": False,
                    "download_ready": False,
                    "browser_runtime_ready": False,
                },
            }
            runtime[section] = value

            with self.subTest(section=section):
                with self.assertRaisesRegex(ValueError, f"{section} must be an object"):
                    build_runtime_view(runtime)

    def test_build_runtime_view_rejects_corrupt_readiness_issues(self) -> None:
        for issues in ("broken", [False]):
            with self.subTest(issues=issues):
                with self.assertRaisesRegex(ValueError, "readiness.issues"):
                    build_runtime_view(
                        {
                            "browser": {"installed": False},
                            "install": {"running": False},
                            "readiness": {
                                "ready": False,
                                "download_ready": False,
                                "browser_runtime_ready": False,
                                "issues": issues,
                            },
                        }
                    )

    def test_build_runtime_view_preserves_boolean_runtime_flags(self) -> None:
        resource = build_runtime_view(
            {
                "browser": {"installed": False},
                "install": {"running": True},
                "readiness": {
                    "ready": True,
                    "download_ready": False,
                    "browser_runtime_ready": True,
                },
            }
        )

        self.assertFalse(resource["browser"]["installed"])
        self.assertTrue(resource["install"]["running"])
        self.assertTrue(resource["readiness"]["ready"])
        self.assertFalse(resource["readiness"]["download_ready"])
        self.assertTrue(resource["readiness"]["browser_runtime_ready"])

    def test_build_runtime_view_does_not_bridge_legacy_aliases(self) -> None:
        resource = build_runtime_view(
            {
                "browser_runtime": {
                    "installed": True,
                    "browser_name": "chromium",
                    "installation_source": "system",
                    "error": "",
                },
                "browser_install": {
                    "status": "running",
                    "browser_name": "chromium",
                    "running": True,
                },
                "product_readiness": {
                    "ready": True,
                    "download_ready": True,
                    "browser_runtime_ready": True,
                    "issues": [{"code": "legacy", "severity": "error", "message": "legacy"}],
                },
            }
        )

        self.assertFalse(resource["browser"]["installed"])
        self.assertEqual(resource["install"]["status"], "")
        self.assertFalse(resource["readiness"]["ready"])
        self.assertEqual(resource["readiness"]["issues"], [])

    def test_build_overview_view_does_not_bridge_legacy_flat_fields(self) -> None:
        resource = build_overview_view(
            {
                "record_state_counts": {"ready": 7},
                "pending_mapping_count": 3,
                "browser_runtime": {"installed": True, "browser_name": "chromium"},
                "browser_install": {"status": "running", "browser_name": "chromium", "running": True},
                "product_readiness": {"ready": True, "download_ready": True, "browser_runtime_ready": True},
                "raw_manual_root": "/tmp/legacy-manual",
            }
        )

        self.assertEqual(resource["record_summary"], {"state_counts": {}, "pending_mapping_count": 0})
        self.assertFalse(resource["runtime"]["browser"]["installed"])
        self.assertEqual(resource["defaults"]["manual_import_input_dir"], "")

    def test_build_overview_view_rejects_non_mapping_top_level_payload(self) -> None:
        self.assertEqual(build_overview_view(None)["record_summary"]["pending_mapping_count"], 0)

        with self.assertRaisesRegex(ValueError, "payload must be an object"):
            build_overview_view([])

    def test_build_overview_view_rejects_explicit_non_mapping_record_summary(self) -> None:
        self.assertEqual(build_overview_view({})["record_summary"]["pending_mapping_count"], 0)
        self.assertEqual(build_overview_view({"record_summary": None})["record_summary"]["pending_mapping_count"], 0)

        with self.assertRaisesRegex(ValueError, "record_summary must be an object"):
            build_overview_view({"record_summary": ["pending_mapping_count", 3]})

    def test_build_overview_view_rejects_explicit_non_mapping_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime must be an object"):
            build_overview_view({"runtime": []})

    def test_build_overview_view_rejects_explicit_non_mapping_state_counts(self) -> None:
        for value in (False, [], "ready"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "record_summary.state_counts must be an object"):
                    build_overview_view({"record_summary": {"state_counts": value}})

    def test_build_overview_view_rejects_explicit_non_mapping_defaults(self) -> None:
        with self.assertRaisesRegex(ValueError, "defaults must be an object"):
            build_overview_view({"defaults": []})

    def test_build_overview_view_rejects_explicit_non_mapping_default_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "defaults.default_scope must be an object"):
            build_overview_view({"defaults": {"default_scope": False}})

    def test_build_overview_view_rejects_explicit_non_mapping_visibility(self) -> None:
        with self.assertRaisesRegex(ValueError, "visibility must be an object"):
            build_overview_view({"visibility": []})

    def test_build_overview_view_rejects_malformed_visible_families(self) -> None:
        malformed_cases = [
            ("missing", {}),
            ("none", {"visible_families": None}),
            ("empty_list", {"visible_families": []}),
            ("string", {"visible_families": "deal"}),
        ]

        for label, visibility in malformed_cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "visibility.visible_families"):
                    build_overview_view({"visibility": visibility})

    def test_build_overview_view_preserves_structured_default_scope_object(self) -> None:
        resource = build_overview_view(
            {
                "record_summary": {"state_counts": {"ready": 7}, "pending_mapping_count": 3},
                "runtime": {
                    "browser": {
                        "installed": True,
                        "browser_name": "chromium",
                        "installation_source": "system",
                        "error": "",
                    },
                    "install": {"status": "idle", "browser_name": "chromium"},
                    "readiness": {"ready": True, "download_ready": True, "browser_runtime_ready": True},
                },
                "defaults": {
                    "manual_import_input_dir": "/tmp/manual",
                    "default_exchange": "cbex",
                    "default_project_type": "physical_asset",
                    "default_scope": {
                        "stored_preference": {
                            "record_family": "listing",
                            "business_id": "physical_asset",
                            "exchange": "cbex",
                        },
                        "effective_scope": {
                            "record_family": "listing",
                            "business_id": "physical_asset",
                            "business_label": "实物资产",
                            "exchange": "cbex",
                        },
                        "stale_resolution": {
                            "is_stale": False,
                            "reason": "",
                            "hint": "",
                        },
                    },
                },
            }
        )

        self.assertNotIn("default_exchange", resource["defaults"])
        self.assertNotIn("default_project_type", resource["defaults"])
        self.assertEqual(
            resource["defaults"]["default_scope"]["stored_preference"]["business_id"],
            "physical_asset",
        )
        self.assertEqual(
            resource["defaults"]["default_scope"]["effective_scope"]["business_label"],
            "实物资产",
        )

    def test_build_overview_view_preserves_business_re_evaluation_metrics_on_latest_job_and_progress(self) -> None:
        resource = build_overview_view(
            {
                "latest_job": {
                    "job_id": "job-business-re-eval",
                    "job_type": "business_re_evaluation",
                    "status": "success_with_warnings",
                    "summary": {
                        "pending_review_count": 3,
                        "accepted_completed_count": 7,
                        "skipped_count": 1,
                    },
                },
                "latest_progress": {
                    "job_status": "running",
                    "pending_review_count": 2,
                    "accepted_completed_count": 5,
                    "skipped_count": 1,
                },
            }
        )

        self.assertEqual(
            resource["latest_job"]["result"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
            ],
        )
        self.assertEqual(
            resource["latest_progress"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 2},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 5},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
            ],
        )

    def test_build_overview_view_rejects_non_mapping_latest_progress(self) -> None:
        with self.assertRaisesRegex(ValueError, "latest_progress must be an object"):
            build_overview_view({"latest_progress": ["phase_percent", 50]})

    def test_build_overview_view_rejects_non_list_recent_jobs(self) -> None:
        with self.assertRaisesRegex(ValueError, "recent_jobs must be a list"):
            build_overview_view({"recent_jobs": {"job_id": "job-1"}})

    def test_build_overview_view_rejects_non_mapping_jobs(self) -> None:
        cases = [
            ("latest_job", {"latest_job": []}, "latest_job must be an object"),
            ("recent_jobs[0]", {"recent_jobs": [[]]}, "recent_jobs\\[0\\] must be an object"),
        ]
        for field_name, payload, message in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, message):
                    build_overview_view(payload)

    def test_build_overview_view_rejects_non_mapping_explicit_job_progress(self) -> None:
        with self.assertRaisesRegex(ValueError, "latest_progress must be an object"):
            build_overview_view(
                {
                    "latest_job": {"job_id": "job-progress-contract", "status": "running"},
                    "latest_progress": ["phase_percent", 50],
                }
            )

    def test_build_overview_view_rejects_non_mapping_job_progress_builder_result(self) -> None:
        with self.assertRaisesRegex(ValueError, "build_job_progress result must be an object"):
            build_overview_view(
                {"latest_job": {"job_id": "job-progress-contract", "status": "running"}},
                build_job_progress=lambda job: [],
            )

    def test_build_overview_view_accepts_empty_job_progress_builder_result(self) -> None:
        resource = build_overview_view(
            {"latest_job": {"job_id": "job-progress-contract", "status": "running"}},
            build_job_progress=lambda job: {},
        )

        self.assertEqual(resource["latest_job"]["job_id"], "job-progress-contract")


if __name__ == "__main__":
    unittest.main()
