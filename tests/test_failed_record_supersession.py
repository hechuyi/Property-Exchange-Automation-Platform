from __future__ import annotations

import json
import unittest

import pytest

from peap.failed_record_supersession import (
    build_superseding_record_index,
    find_superseding_record,
    source_identity_dict,
)


class FailedRecordSupersessionTest(unittest.TestCase):
    def test_blank_source_identity_json_can_use_source_identity(self) -> None:
        identity = source_identity_dict(
            {
                "source_identity_json": "  ",
                "source_identity": {
                    "project_code": "PRJ-FALLBACK",
                    "original_source_file": "/tmp/fallback.html",
                },
            }
        )

        self.assertEqual(identity["project_code"], "PRJ-FALLBACK")
        self.assertEqual(identity["original_source_file"], "/tmp/fallback.html")

    def test_invalid_source_identity_json_is_not_silently_replaced_by_source_identity(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            source_identity_dict(
                {
                    "source_identity_json": "{",
                    "source_identity": {
                        "project_code": "PRJ-FALLBACK",
                        "original_source_file": "/tmp/fallback.html",
                    },
                }
            )

    def test_source_identity_dict_rejects_non_object_source_identity_json(self) -> None:
        with self.assertRaises(TypeError):
            source_identity_dict(
                {
                    "source_identity_json": "[]",
                    "source_identity": {
                        "project_code": "PRJ-FALLBACK",
                        "original_source_file": "/tmp/fallback.html",
                    },
                }
            )

    def test_source_identity_dict_rejects_non_object_source_identity(self) -> None:
        with self.assertRaises(TypeError):
            source_identity_dict({"source_identity": ["not", "an", "object"]})


def _record(
    *,
    record_id: str,
    record_family: str,
    state: str,
    project_code: str = "G32026CQ1000062",
    source_id: str = "cquae",
    exchange: str = "重交所",
    source_file: str = "/archive/G32026CQ1000062.html",
    business_id: str = "deal_equity_transfer",
    candidate_tokens: list[str] | None = None,
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "record_family": record_family,
        "state": state,
        "project_code": project_code,
        "business_id": business_id,
        "exchange": exchange,
        "source_file": source_file,
        "archive_path": source_file,
        "source_identity": {
            "record_family": record_family,
            "source_id": source_id,
            "project_code": project_code,
            "business_id": business_id,
            "original_source_file": source_file,
            "candidate_tokens": candidate_tokens or [],
        },
    }


def test_deal_reingest_is_not_superseded_by_listing_backlog_record() -> None:
    listing_backlog = _record(
        record_id="listing-backlog",
        record_family="listing",
        state="pending_mapping",
    )
    deal_reingest = _record(
        record_id="deal-reingest",
        record_family="deal",
        state="field_missing",
    )

    index = build_superseding_record_index([listing_backlog, deal_reingest])

    assert find_superseding_record(deal_reingest, index) is None


def test_same_family_canonical_record_still_supersedes_failed_shell() -> None:
    canonical = _record(record_id="canonical", record_family="deal", state="ready")
    failed_shell = _record(record_id="failed-shell", record_family="deal", state="field_missing")

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) == canonical


def test_skipped_record_cannot_supersede_failed_shell() -> None:
    skipped = _record(record_id="skipped", record_family="deal", state="skipped")
    failed_shell = _record(record_id="failed-shell", record_family="deal", state="field_missing")

    index = build_superseding_record_index([skipped, failed_shell])

    assert find_superseding_record(failed_shell, index) is None


def test_different_project_code_cannot_fall_back_to_same_path() -> None:
    canonical = _record(
        record_id="canonical-other-project",
        record_family="deal",
        state="ready",
        project_code="G32026CQ9999999",
    )
    failed_shell = _record(
        record_id="failed-shell",
        record_family="deal",
        state="parse_failed",
        project_code="G32026CQ1000062",
    )

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) is None


def test_mismatched_candidate_token_cannot_fall_back_to_same_path() -> None:
    canonical = _record(
        record_id="canonical-other-candidate",
        record_family="deal",
        state="ready",
        project_code="",
        candidate_tokens=["project_id:OTHER"],
    )
    failed_shell = _record(
        record_id="failed-shell",
        record_family="deal",
        state="parse_failed",
        project_code="",
        candidate_tokens=["project_id:EXPECTED"],
    )

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) is None


def test_path_only_fallback_rejects_different_sources() -> None:
    canonical = _record(
        record_id="canonical-cbex",
        record_family="deal",
        state="ready",
        project_code="",
        source_id="cbex",
        exchange="北交所",
    )
    failed_shell = _record(
        record_id="failed-cquae",
        record_family="deal",
        state="parse_failed",
        project_code="",
        source_id="cquae",
        exchange="重交所",
    )

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) is None


def test_path_only_fallback_rejects_different_businesses() -> None:
    canonical = _record(
        record_id="canonical-capital-increase",
        record_family="deal",
        state="ready",
        project_code="",
        business_id="deal_capital_increase",
    )
    failed_shell = _record(
        record_id="failed-equity-transfer",
        record_family="deal",
        state="parse_failed",
        project_code="",
        business_id="deal_equity_transfer",
    )

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) is None


def test_path_only_fallback_accepts_normalized_source_aliases() -> None:
    canonical = _record(
        record_id="canonical-tpre",
        record_family="deal",
        state="ready",
        project_code="",
        source_id="",
        exchange="天津产权交易中心",
    )
    failed_shell = _record(
        record_id="failed-tpre",
        record_family="deal",
        state="parse_failed",
        project_code="",
        source_id="tpre",
        exchange="天交所",
    )

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) == canonical


@pytest.mark.parametrize("state", ["pending_mapping", "mapping_conflict", "conflict"])
def test_parsed_non_ready_state_can_supersede_failed_shell(state: str) -> None:
    canonical = _record(record_id=f"canonical-{state}", record_family="deal", state=state)
    failed_shell = _record(record_id="failed-shell", record_family="deal", state="parse_failed")

    index = build_superseding_record_index([canonical, failed_shell])

    assert find_superseding_record(failed_shell, index) == canonical


def test_supersession_lookup_is_repeatable_without_mutating_index() -> None:
    canonical = _record(record_id="canonical", record_family="deal", state="ready")
    failed_shell = _record(record_id="failed-shell", record_family="deal", state="parse_failed")
    index = build_superseding_record_index([canonical, failed_shell])
    before = json.dumps(index, ensure_ascii=False, sort_keys=True)

    assert find_superseding_record(failed_shell, index) == canonical
    assert find_superseding_record(failed_shell, index) == canonical
    assert json.dumps(index, ensure_ascii=False, sort_keys=True) == before


if __name__ == "__main__":
    unittest.main()
