from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_backend.repositories import PipelineRepository
from desktop_backend.review_problem_contract import normalize_review_problem_query
from desktop_backend.services.review_problem_service import ReviewProblemService
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import StreamingStore
from peap_core.family_catalog import FamilyDescriptor


class FakeReviewProblemRepository:
    def __init__(self, records):
        self.records = list(records)
        self.last_states = None
        self.last_limit = None

    def iter_latest_records(self, **kwargs):
        self.last_states = kwargs.get("states")
        self.last_limit = kwargs.get("limit")
        states = set(kwargs.get("states") or [])
        records = list(self.records)
        if states:
            records = [record for record in records if record.get("state") in states]
        record_family = kwargs.get("record_family")
        business_id = kwargs.get("business_id")
        exchange = kwargs.get("exchange")
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        if record_family:
            records = [record for record in records if record.get("record_family") == record_family]
        if business_id:
            records = [record for record in records if record.get("business_id") == business_id]
        if exchange:
            records = [record for record in records if record.get("exchange") == exchange]
        if date_from:
            records = [record for record in records if str(record.get("updated_at") or "")[:10] >= date_from]
        if date_to:
            records = [record for record in records if str(record.get("updated_at") or "")[:10] <= date_to]
        if kwargs.get("limit") is not None:
            records = records[: int(kwargs["limit"])]
        return records


class ReviewProblemContractTest(unittest.TestCase):
    def test_query_normalization_accepts_record_families_from_catalog(self) -> None:
        families = [
            FamilyDescriptor(
                family_id="archive",
                canonical_label="Archive",
                aliases=("archive",),
                source_ids=("legacy",),
                business_ids=("archived_record",),
                default_product_profile_id="desktop_archive",
            )
        ]

        with patch("desktop_backend.review_problem_contract.list_family_descriptors", return_value=families):
            query = normalize_review_problem_query({"record_family": ["archive"]})

        self.assertEqual(query["record_family"], "archive")

    def test_review_problem_contract_does_not_keep_local_record_family_allowlist(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "desktop_backend" / "review_problem_contract.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        local_allowlist_found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not isinstance(node.left, ast.Name) or node.left.id != "record_family":
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
                if values == {"all", "listing", "deal"}:
                    local_allowlist_found = True

        self.assertFalse(local_allowlist_found)

    def test_query_normalization_validates_enums_dates_and_clamps_pagination(self) -> None:
        query = normalize_review_problem_query(
            {
                "problem_kind": ["export_fields_missing"],
                "record_family": ["deal"],
                "state": ["field_missing"],
                "date_from": ["2026-05-01"],
                "date_to": ["2026-05-17"],
                "page": ["0"],
                "page_size": ["500"],
            }
        )

        self.assertEqual(query["page"], 1)
        self.assertEqual(query["page_size"], 200)
        self.assertEqual(query["problem_kind"], "export_fields_missing")

        with self.assertRaisesRegex(ValueError, "invalid problem_kind"):
            normalize_review_problem_query({"problem_kind": ["business_resolution"]})
        with self.assertRaisesRegex(ValueError, "invalid date_from"):
            normalize_review_problem_query({"date_from": ["2026-99-99"]})
        with self.assertRaisesRegex(ValueError, "invalid date_from"):
            normalize_review_problem_query({"date_from": ["20260517"]})
        with self.assertRaisesRegex(ValueError, "invalid date_to"):
            normalize_review_problem_query({"date_to": ["2026-W20-7"]})

    def test_review_projection_derives_record_family_from_catalog_without_listing_default(self) -> None:
        def descriptor(family_id: str, label: str) -> FamilyDescriptor:
            return FamilyDescriptor(
                family_id=family_id,
                canonical_label=label,
                aliases=(family_id,),
                source_ids=(),
                business_ids=(),
                default_product_profile_id=f"desktop_{family_id}",
            )

        descriptors = {
            "ARCHIVE_ALIAS": descriptor("archive", "Archive Records"),
            "CATALOG_ALIAS": descriptor("catalog_family", "Catalog Family"),
        }
        calls: list[str] = []

        def fake_get_family_descriptor(value: str) -> FamilyDescriptor:
            calls.append(value)
            if value not in descriptors:
                raise KeyError(value)
            return descriptors[value]

        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "rec-catalog-family",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "CATALOG_ALIAS",
                        "project_code": "CAT",
                        "project_name": "目录族项目",
                        "updated_at": "2026-05-17T12:00:00+08:00",
                        "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                    }
                ]
            )
        )

        with patch(
            "desktop_backend.services.review_problem_service.get_family_descriptor",
            create=True,
            side_effect=fake_get_family_descriptor,
        ):
            row = service.row_from_record(
                {
                    "record_id": "rec-synthetic-family",
                    "revision_id": 1,
                    "state": "pending_review",
                    "record_family": "ARCHIVE_ALIAS",
                    "project_code": "ARC",
                    "project_name": "归档族项目",
                    "updated_at": "2026-05-17T13:00:00+08:00",
                    "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                }
            )
            payload = service.list_review_problems(normalize_review_problem_query({}))
            unknown = service.row_from_record(
                {
                    "record_id": "rec-unknown-family",
                    "revision_id": 1,
                    "state": "pending_review",
                    "record_family": "mystery_family",
                    "project_code": "UNK",
                    "project_name": "未知族项目",
                    "updated_at": "2026-05-17T14:00:00+08:00",
                    "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                }
            )

        self.assertEqual(row["record_family"], "archive")
        self.assertEqual(row["record_family_label"], "Archive Records")
        self.assertEqual(payload["rows"][0]["record_family"], "catalog_family")
        self.assertEqual(payload["rows"][0]["record_family_label"], "Catalog Family")
        self.assertEqual(unknown["record_family"], "mystery_family")
        self.assertEqual(unknown["record_family_label"], "mystery_family")
        self.assertNotEqual(unknown["record_family"], "listing")
        self.assertEqual(calls, ["ARCHIVE_ALIAS", "CATALOG_ALIAS", "mystery_family"])

    def test_review_projection_derives_record_family_from_source_identity(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        row = service.row_from_record(
            {
                "record_id": "rec-source-family",
                "revision_id": 1,
                "state": "pending_review",
                "project_code": "SRC",
                "project_name": "源身份项目",
                "updated_at": "2026-05-17T15:00:00+08:00",
                "canonical_record": {
                    "business_identity": {},
                    "source_identity": {"record_family": "deal"},
                },
                "findings": [
                    {
                        "type": "business_resolution_required",
                        "evidence": {"reason_code": "other"},
                    }
                ],
            }
        )

        self.assertEqual(row["record_family"], "deal")
        self.assertEqual(row["record_family_label"], "Deal")
        self.assertNotEqual(row["record_family"], "listing")

    def test_review_projection_filters_business_id_from_source_identity(self) -> None:
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "rec-source-business",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "deal",
                        "business_id": "",
                        "project_code": "SRC-BIZ",
                        "project_name": "源身份业务项目",
                        "updated_at": "2026-05-17T15:10:00+08:00",
                        "canonical_record": {
                            "business_identity": {},
                            "source_identity": {
                                "record_family": "deal",
                                "business_id": "deal_equity_transfer",
                            },
                        },
                        "findings": [
                            {
                                "type": "business_resolution_required",
                                "evidence": {"reason_code": "other"},
                            }
                        ],
                    },
                    {
                        "record_id": "rec-distractor-business",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "deal",
                        "business_id": "deal_capital_increase",
                        "project_code": "DST-BIZ",
                        "project_name": "干扰业务项目",
                        "updated_at": "2026-05-17T15:05:00+08:00",
                        "findings": [
                            {
                                "type": "business_resolution_required",
                                "evidence": {"reason_code": "other"},
                            }
                        ],
                    },
                ]
            )
        )

        payload = service.list_review_problems(
            normalize_review_problem_query(
                {
                    "record_family": ["deal"],
                    "business_id": ["deal_equity_transfer"],
                    "state": ["pending_review"],
                }
            )
        )

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["rows"][0]["record_id"], "rec-source-business")
        self.assertEqual(payload["rows"][0]["business_id"], "deal_equity_transfer")

    def test_review_projection_classifies_once_per_record_and_sanitizes_related_findings(self) -> None:
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "rec-field",
                        "revision_id": 2,
                        "state": "field_missing",
                        "record_family": "listing",
                        "project_code": "G1",
                        "project_name": "字段缺失项目",
                        "exchange": "sse",
                        "source_file": "field.html",
                        "updated_at": "2026-05-17T11:00:00+08:00",
                        "findings": [
                            {
                                "type": "canonical_field_missing",
                                "message": "UNTRUSTED_EXTERNAL_TEXT",
                                "evidence": {
                                    "missing_fields": [{"field": "project_name", "label": "项目名称"}],
                                    "raw_html": "UNTRUSTED_EXTERNAL_TEXT",
                                    "ocr_text": "UNTRUSTED_EXTERNAL_TEXT",
                                },
                            },
                            {
                                "type": "business_resolution_required",
                                "message": "UNTRUSTED_EXTERNAL_TEXT",
                                "evidence": {
                                    "reason_code": "unrecognized_business",
                                    "raw_business_label": "未知",
                                    "browser_transcript": "UNTRUSTED_EXTERNAL_TEXT",
                                },
                            },
                        ],
                    },
                    {
                        "record_id": "rec-review",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "deal",
                        "project_code": "D1",
                        "project_name": "成交项目",
                        "exchange": "cbex",
                        "source_file": "deal.html",
                        "updated_at": "2026-05-17T10:00:00+08:00",
                        "findings": [
                            {
                                "type": "business_resolution_required",
                                "message": "缺少投资方",
                                "evidence": {"reason_code": "deal_capital_increase_missing_investor_amount"},
                            }
                        ],
                    },
                ]
            )
        )

        payload = service.list_review_problems(normalize_review_problem_query({}))

        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(payload["summary"]["export_fields_missing_count"], 1)
        self.assertEqual(payload["summary"]["deal_data_incomplete_count"], 1)
        field_row = payload["rows"][0]
        self.assertEqual(field_row["problem_kind"], "export_fields_missing")
        self.assertEqual(len(field_row["evidence"]["related_findings"]), 2)
        self.assertEqual(field_row["evidence"]["related_finding_count"], 2)
        self.assertEqual(field_row["actions"]["primary_action_kind"], "none")
        self.assertEqual(field_row["evidence"]["missing_fields"], [{"field": "project_name", "label": "项目名称"}])
        self.assertEqual(
            field_row["evidence"]["related_findings"][0],
            {
                "finding_type": "canonical_field_missing",
                "severity": "warning",
                "reason_code": "",
                "fields": [{"field": "project_name", "label": "项目名称"}],
            },
        )
        self.assertEqual(
            field_row["evidence"]["artifact_evidence_verdict"],
            {
                "status": field_row["evidence_verdict"]["status"],
                "reason_code": field_row["evidence_verdict"]["reason_code"],
            },
        )
        serialized_evidence = repr(field_row["evidence"])
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", serialized_evidence)
        self.assertNotIn("message", field_row["evidence"])
        self.assertNotIn("raw_business_label", field_row["evidence"])
        self.assertFalse(any("message" in item for item in field_row["evidence"]["related_findings"]))
        self.assertFalse(any("evidence" in item for item in field_row["evidence"]["related_findings"]))

    def test_review_projection_classifies_source_artifact_problem_separately_from_missing_fields(self) -> None:
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "rec-invalid-source",
                        "revision_id": 9,
                        "state": "field_missing",
                        "record_family": "deal",
                        "business_id": "deal_physical_asset",
                        "project_code": "GR2026SH1000563",
                        "project_name": "假网页成交项目",
                        "exchange": "sse",
                        "source_file": "/tmp/synthetic.html",
                        "archive_path": "/tmp/synthetic.html",
                        "updated_at": "2026-05-21T10:46:12+08:00",
                        "findings": [
                            {
                                "severity": "error",
                                "type": "source_artifact_invalid",
                                "message": "SSE deal artifact is a synthetic API shell, not a rendered original page",
                                "evidence": {
                                    "reason_code": "source_artifact_invalid",
                                    "detector": "sse_deal_notice_synthetic_shell",
                                },
                            }
                        ],
                    }
                ]
            )
        )

        payload = service.list_review_problems(normalize_review_problem_query({}))
        row = payload["rows"][0]

        self.assertEqual(row["problem_kind"], "source_artifact_unavailable")
        self.assertEqual(row["problem_label"], "原网页不可用")
        self.assertEqual(row["reason_code"], "source_artifact_invalid")
        self.assertEqual(payload["summary"]["source_artifact_unavailable_count"], 1)
        self.assertEqual(payload["summary"]["export_fields_missing_count"], 0)
        self.assertIn("不是完整原网页", row["business_explanation"])
        self.assertIn("重新下载", row["suggested_review"])

    def test_review_projection_rejects_explicit_non_list_findings(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        with self.assertRaisesRegex(TypeError, "record.findings must be a list"):
            service.row_from_record(
                {
                    "record_id": "bad-findings-shape",
                    "revision_id": 1,
                    "state": "pending_review",
                    "record_family": "listing",
                    "project_code": "BAD",
                    "project_name": "坏 findings 形状",
                    "updated_at": "2026-05-17T12:00:00+08:00",
                    "findings": {"type": "business_resolution_required", "evidence": {"reason_code": "unrecognized_business"}},
                }
            )

    def test_review_projection_rejects_non_mapping_finding_evidence_instead_of_empty_context(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        with self.assertRaisesRegex(TypeError, r"finding\.evidence must be an object"):
            service.row_from_record(
                {
                    "record_id": "bad-evidence-shape",
                    "revision_id": 1,
                    "state": "pending_review",
                    "record_family": "listing",
                    "project_code": "BAD-EVIDENCE",
                    "project_name": "坏 evidence 形状",
                    "updated_at": "2026-05-17T12:00:00+08:00",
                    "findings": [
                        {
                            "type": "business_resolution_required",
                            "evidence": [],
                        }
                    ],
                }
            )

    def test_review_projection_rejects_non_list_missing_fields_evidence_instead_of_iterating_text(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        with self.assertRaisesRegex(TypeError, r"finding\.evidence\.missing_fields must be a list"):
            service.row_from_record(
                {
                    "record_id": "bad-missing-fields-shape",
                    "revision_id": 1,
                    "state": "field_missing",
                    "record_family": "listing",
                    "project_code": "BAD-MISSING",
                    "project_name": "坏 missing_fields 形状",
                    "updated_at": "2026-05-17T12:00:00+08:00",
                    "findings": [
                        {
                            "type": "canonical_field_missing",
                            "evidence": {"missing_fields": "project_name"},
                        }
                    ],
                }
            )

    def test_review_projection_rejects_non_mapping_canonical_record_instead_of_unknown_family(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        with self.assertRaisesRegex(TypeError, "canonical_record must be an object"):
            service.row_from_record(
                {
                    "record_id": "bad-canonical-shape",
                    "revision_id": 1,
                    "state": "pending_review",
                    "project_code": "BAD-CANONICAL",
                    "project_name": "坏 canonical_record 形状",
                    "canonical_record": [],
                    "updated_at": "2026-05-17T12:00:00+08:00",
                    "findings": [
                        {
                            "type": "business_resolution_required",
                            "evidence": {"reason_code": "other"},
                        }
                    ],
                }
            )

    def test_review_projection_rejects_non_mapping_canonical_business_identity_instead_of_empty_business(self) -> None:
        service = ReviewProblemService(repository=FakeReviewProblemRepository([]))

        with self.assertRaisesRegex(TypeError, r"canonical_record\.business_identity must be an object"):
            service.row_from_record(
                {
                    "record_id": "bad-canonical-business-identity",
                    "revision_id": 1,
                    "state": "pending_review",
                    "project_code": "BAD-CANONICAL-BUSINESS",
                    "project_name": "坏 business_identity 形状",
                    "canonical_record": {
                        "record_family": "deal",
                        "business_identity": [],
                    },
                    "updated_at": "2026-05-17T12:00:00+08:00",
                    "findings": [
                        {
                            "type": "business_resolution_required",
                            "evidence": {"reason_code": "other"},
                        }
                    ],
                }
            )

    def test_review_projection_classifies_pending_review_problem_kinds(self) -> None:
        records = [
            {
                "record_id": "project-type",
                "state": "pending_review",
                "record_family": "listing",
                "project_code": "A",
                "project_name": "项目类型未知",
                "updated_at": "2026-05-17T12:00:00+08:00",
                "findings": [
                    {
                        "type": "business_resolution_required",
                        "evidence": {"reason_code": "unrecognized_business", "raw_business_label": "产权转让"},
                    }
                ],
            },
            {
                "record_id": "family",
                "state": "pending_review",
                "record_family": "unknown",
                "project_code": "B",
                "project_name": "业务族冲突",
                "updated_at": "2026-05-17T11:00:00+08:00",
                "findings": [
                    {
                        "type": "business_resolution_required",
                        "evidence": {
                            "reason_code": "business_family_conflict",
                            "payload_record_family": "listing",
                            "context_record_family": "deal",
                        },
                    }
                ],
            },
            {
                "record_id": "manual",
                "state": "pending_review",
                "record_family": "deal",
                "project_code": "C",
                "project_name": "人工复核",
                "updated_at": "2026-05-17T10:00:00+08:00",
                "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
            },
            {
                "record_id": "missing-type",
                "state": "pending_review",
                "record_family": "listing",
                "project_code": "D",
                "project_name": "缺少业务类型",
                "updated_at": "2026-05-17T09:00:00+08:00",
                "findings": [
                    {
                        "type": "business_resolution_required",
                        "message": "缺少业务类型，需完成人工归类后再继续处理",
                        "evidence": {"blocker_kind": "business_resolution", "diagnostic_gap_codes": ["missing_type"]},
                    }
                ],
            },
        ]
        service = ReviewProblemService(repository=FakeReviewProblemRepository(records))

        payload = service.list_review_problems(normalize_review_problem_query({}))

        by_id = {row["record_id"]: row for row in payload["rows"]}
        self.assertEqual(by_id["project-type"]["problem_kind"], "project_type_unresolved")
        self.assertEqual(by_id["family"]["problem_kind"], "business_family_unresolved")
        self.assertEqual(by_id["family"]["evidence"]["payload_record_family"], "listing")
        self.assertEqual(by_id["family"]["evidence"]["context_record_family"], "deal")
        self.assertEqual(by_id["manual"]["problem_kind"], "manual_review_unclassified")
        self.assertEqual(by_id["missing-type"]["problem_kind"], "project_type_unresolved")
        self.assertEqual(by_id["missing-type"]["reason_code"], "business_resolution_required")
        self.assertEqual(payload["summary"]["project_type_unresolved_count"], 2)
        self.assertEqual(payload["summary"]["business_family_unresolved_count"], 1)
        self.assertEqual(payload["summary"]["manual_review_unclassified_count"], 1)

    def test_review_projection_excludes_superseded_pending_review_shells(self) -> None:
        source_file = "/tmp/peap/archive/TA2026BJ1000944-2.html"
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "old-review-shell",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "listing",
                        "project_code": "TA2026BJ1000944-2",
                        "project_name": "旧待复核壳",
                        "source_file": source_file,
                        "archive_path": source_file,
                        "updated_at": "2026-05-20T15:47:00+08:00",
                        "findings": [
                            {
                                "type": "business_resolution_required",
                                "message": "缺少业务类型，需完成人工归类后再继续处理",
                                "evidence": {"blocker_kind": "business_resolution", "diagnostic_gap_codes": ["missing_type"]},
                            }
                        ],
                    },
                    {
                        "record_id": "new-ready-record",
                        "revision_id": 2,
                        "state": "ready",
                        "record_family": "listing",
                        "project_code": "TA2026BJ1000944-2",
                        "project_name": "新可用记录",
                        "source_file": source_file,
                        "archive_path": source_file,
                        "updated_at": "2026-05-20T15:48:00+08:00",
                    },
                    {
                        "record_id": "still-review",
                        "revision_id": 3,
                        "state": "pending_review",
                        "record_family": "listing",
                        "project_code": "UNRESOLVED",
                        "project_name": "仍需复核",
                        "source_file": "/tmp/peap/archive/unresolved.html",
                        "archive_path": "/tmp/peap/archive/unresolved.html",
                        "updated_at": "2026-05-20T15:49:00+08:00",
                        "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                    },
                ]
            )
        )

        payload = service.list_review_problems(normalize_review_problem_query({}))

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["rows"][0]["record_id"], "still-review")

    def test_review_projection_filters_keyword_and_paginates_newest_first(self) -> None:
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "old",
                        "state": "pending_review",
                        "record_family": "listing",
                        "business_id": "asset_transfer",
                        "exchange": "sse",
                        "project_code": "OLD",
                        "project_name": "旧项目",
                        "source_file": "old.html",
                        "updated_at": "2026-05-16T12:00:00+08:00",
                        "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                    },
                    {
                        "record_id": "new",
                        "state": "pending_review",
                        "record_family": "listing",
                        "business_id": "asset_transfer",
                        "exchange": "sse",
                        "project_code": "NEW",
                        "project_name": "目标项目",
                        "source_file": "target.html",
                        "updated_at": "2026-05-17T12:00:00+08:00",
                        "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
                    },
                ]
            )
        )

        payload = service.list_review_problems(
            normalize_review_problem_query(
                {
                    "record_family": ["listing"],
                    "business_id": ["asset_transfer"],
                    "exchange": ["sse"],
                    "keyword": ["项目"],
                    "page": ["2"],
                    "page_size": ["1"],
                }
            )
        )

        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["returned_count"], 1)
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["rows"][0]["record_id"], "old")

    def test_review_projection_filters_before_pagination_without_repository_limit(self) -> None:
        records = [
            {
                "record_id": f"manual-{index:04d}",
                "state": "pending_review",
                "record_family": "listing",
                "project_code": f"M{index:04d}",
                "project_name": "普通复核",
                "updated_at": f"2026-05-17T10:{index % 60:02d}:00+08:00",
                "findings": [{"type": "business_resolution_required", "evidence": {"reason_code": "other"}}],
            }
            for index in range(5001)
        ]
        records.append(
            {
                "record_id": "target-export-fields",
                "state": "field_missing",
                "record_family": "listing",
                "project_code": "TARGET",
                "project_name": "目标字段缺失",
                "updated_at": "2026-05-16T10:00:00+08:00",
                "findings": [{"type": "canonical_field_missing", "evidence": {"missing_fields": ["项目名称"]}}],
            }
        )
        repository = FakeReviewProblemRepository(records)
        service = ReviewProblemService(repository=repository)

        payload = service.list_review_problems(
            normalize_review_problem_query(
                {
                    "problem_kind": ["export_fields_missing"],
                    "keyword": ["TARGET"],
                    "page_size": ["1"],
                }
            )
        )

        self.assertEqual(payload["total_count"], 1)
        self.assertIsNone(repository.last_limit)
        self.assertEqual(payload["summary"]["export_fields_missing_count"], 1)
        self.assertEqual(payload["rows"][0]["record_id"], "target-export-fields")

    def test_review_projection_date_filters_use_updated_at_with_real_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StreamingStore(f"{tmp_dir}/review.sqlite3", auto_migrate=True)
            old_listing_recent_update = IngestedRecord(
                record_id="recent-update",
                revision_hash="hash-recent",
                project_code="RECENT",
                project_name="最近更新",
                project_type="未知",
                exchange="shanghai",
                listing_date="2026-04-01",
                state="pending_review",
                source_file=f"{tmp_dir}/recent.html",
                archive_path=f"{tmp_dir}/recent.html",
                parser_payload={"项目编号": "RECENT", "项目名称": "最近更新"},
                postprocess_payload={"项目编号": "RECENT", "项目名称": "最近更新", "项目类型": "未知"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="business_resolution_required",
                        message="unknown",
                        evidence={"reason_code": "unrecognized_business", "raw_business_label": "未知"},
                    )
                ],
                record_family="listing",
            )
            store.upsert_record(old_listing_recent_update)
            with store._connect() as conn:
                conn.execute(
                    "UPDATE records SET updated_at = ? WHERE record_id = ?",
                    ("2026-05-17T12:00:00+08:00", "recent-update"),
                )

            service = ReviewProblemService(repository=PipelineRepository(store=store))
            payload = service.list_review_problems(
                normalize_review_problem_query(
                    {
                        "date_from": ["2026-05-17"],
                        "date_to": ["2026-05-17"],
                    }
                )
            )

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["rows"][0]["record_id"], "recent-update")

    def test_review_projection_sanitizes_raw_business_label_from_project_type_problem(self) -> None:
        service = ReviewProblemService(
            repository=FakeReviewProblemRepository(
                [
                    {
                        "record_id": "raw-business-label",
                        "revision_id": 1,
                        "state": "pending_review",
                        "record_family": "listing",
                        "project_code": "RAW",
                        "project_name": "项目类型复核",
                        "updated_at": "2026-05-17T12:00:00+08:00",
                        "findings": [
                            {
                                "type": "business_resolution_required",
                                "evidence": {
                                    "reason_code": "unrecognized_business",
                                    "raw_business_label": "UNTRUSTED_EXTERNAL_TEXT",
                                },
                            }
                        ],
                    }
                ]
            )
        )

        payload = service.list_review_problems(normalize_review_problem_query({}))
        row = payload["rows"][0]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", serialized)
        self.assertEqual(row["problem_kind"], "project_type_unresolved")
        self.assertEqual(row["reason_code"], "unrecognized_business")
        self.assertEqual(row["raw_business_label"], "项目类型未识别")
        self.assertIn("项目类型未识别", row["business_explanation"])
        self.assertIn("系统目录中没有可用匹配", row["business_explanation"])

    def test_review_projection_recovers_managed_source_identity_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            managed_file = f"{tmp_dir}/artifact.html"
            with open(managed_file, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            service = ReviewProblemService(
                repository=FakeReviewProblemRepository(
                    [
                        {
                            "record_id": "rec-review-artifact",
                            "revision_id": 1,
                            "state": "pending_review",
                            "record_family": "listing",
                            "project_code": "G1",
                            "project_name": "文件关联恢复",
                            "project_type": "未知",
                            "exchange": "cbex",
                            "source_file": "",
                            "archive_path": "",
                            "source_identity_json": {"original_source_file": managed_file},
                            "findings": [
                                {
                                    "severity": "warn",
                                    "type": "business_resolution_required",
                                    "message": "unknown",
                                    "evidence": {"reason_code": "unrecognized_business", "raw_business_label": "未知"},
                                }
                            ],
                            "updated_at": "2026-05-17T12:00:00",
                        }
                    ]
                ),
                managed_artifact_roots=(tmp_dir,),
            )

            payload = service.list_review_problems(normalize_review_problem_query({}))

        row = payload["rows"][0]
        self.assertTrue(row["has_local_artifact"])
        self.assertEqual(row["local_artifact_name"], "artifact.html")
        self.assertEqual(row["artifact_status"], "available")
        self.assertEqual(row["evidence_verdict"]["status"], "undeclared")
        self.assertEqual(row["evidence_verdict"]["reason_code"], "artifact_path_undeclared")
        self.assertEqual(row["evidence_verdict"]["inspection_openable_path"], managed_file)
