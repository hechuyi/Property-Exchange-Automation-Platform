from __future__ import annotations

import unittest

from peap_core.pipeline_state_contracts import JobStage, JobStatus, RecordState
from peap_core.record_state_policy import (
    BACKLOG_OWNING_STATES,
    classify_record_state,
    state_requires_mapping_pending,
)


class PipelineStateContractsTest(unittest.TestCase):
    def test_record_and_job_state_enums_keep_expected_members(self) -> None:
        self.assertEqual(RecordState.PENDING_REVIEW.value, "pending_review")
        self.assertEqual(RecordState.FIELD_MISSING.value, "field_missing")
        self.assertEqual(JobStatus.SUCCESS_WITH_WARNINGS.value, "success_with_warnings")
        self.assertEqual(JobStage.STARTUP.value, "startup")
        self.assertFalse(hasattr(JobStage, "FAILED"))

    def test_pending_review_is_not_a_mapping_pending_backlog_owner(self) -> None:
        self.assertNotIn(RecordState.PENDING_REVIEW, BACKLOG_OWNING_STATES)
        self.assertFalse(state_requires_mapping_pending(RecordState.PENDING_REVIEW))
        self.assertFalse(state_requires_mapping_pending("pending_review"))

    def test_business_resolution_findings_stay_review_work_not_mapping_queue_work(self) -> None:
        state = classify_record_state([{"type": "business_resolution_required"}])

        self.assertEqual(state, RecordState.PENDING_REVIEW)
        self.assertFalse(state_requires_mapping_pending(state))

    def test_optional_rule_errors_enter_pending_review_not_ready(self) -> None:
        state = classify_record_state([{"type": "rule_error"}])

        self.assertEqual(state, RecordState.PENDING_REVIEW)
        self.assertFalse(state_requires_mapping_pending(state))

    def test_export_missing_findings_are_field_missing_not_ready(self) -> None:
        for finding_type in ("export_field_missing", "canonical_field_missing"):
            with self.subTest(finding_type=finding_type):
                state = classify_record_state([{"type": finding_type}])

                self.assertEqual(state, RecordState.FIELD_MISSING)
                self.assertFalse(state_requires_mapping_pending(state))


if __name__ == "__main__":
    unittest.main()
