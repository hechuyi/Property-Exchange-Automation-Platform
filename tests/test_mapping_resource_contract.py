from __future__ import annotations

import json
import unittest

from desktop_backend.mapping_resource_contract import build_mappings_resource


class _FalsyDict(dict):
    def __bool__(self) -> bool:
        return False


class MappingResourceContractTest(unittest.TestCase):
    def test_build_mappings_resource_rejects_legacy_pending_list_backlog(self) -> None:
        explicit_non_mapping_backlogs = (
            False,
            [],
            "not-a-backlog",
        )
        for backlog in explicit_non_mapping_backlogs:
            with self.subTest(backlog=backlog):
                with self.assertRaisesRegex(TypeError, "backlog"):
                    build_mappings_resource(entries=[], backlog=backlog)

    def test_build_mappings_resource_preserves_falsy_mapping_backlog(self) -> None:
        payload = build_mappings_resource(entries=[], backlog=_FalsyDict({"sections": []}))

        self.assertEqual(payload["sections"], [])

    def test_build_mappings_resource_rejects_non_object_section_items(self) -> None:
        with self.assertRaisesRegex(ValueError, r"sections\[\*\]\.items\[\*\]"):
            build_mappings_resource(
                entries=[],
                backlog={
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "cta_kind": "reprocess_pending",
                            "items": ["rec-mapping-gap"],
                        }
                    ],
                    "summary": {"actionable_count": 1},
                },
            )

    def test_build_mappings_resource_rejects_non_object_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, r"entries\[\*\]"):
            build_mappings_resource(
                entries=["bad-entry"],
                backlog={"sections": []},
            )

    def test_build_mappings_resource_rejects_missing_entries_instead_of_defaulting_empty(self) -> None:
        for entries in (None, False):
            with self.subTest(entries=entries):
                with self.assertRaisesRegex(ValueError, "entries"):
                    build_mappings_resource(
                        entries=entries,  # type: ignore[arg-type]
                        backlog={"sections": []},
                    )

    def test_build_mappings_resource_rejects_non_object_recommended_rule(self) -> None:
        with self.assertRaisesRegex(ValueError, r"recommended_rule"):
            build_mappings_resource(
                entries=[],
                backlog={
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "items": [
                                {
                                    "record_id": "rec-mapping-gap",
                                    "recommended_rule": ["transferor_group"],
                                }
                            ],
                        }
                    ],
                },
            )

    def test_build_mappings_resource_rejects_non_list_nested_mapping_fields(self) -> None:
        for field_name in ("gap_codes", "available_rule_kinds", "candidate_resolutions", "evidence_codes"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    build_mappings_resource(
                        entries=[],
                        backlog={
                            "sections": [
                                {
                                    "section_id": "mapping_gap_resolution",
                                    "title": "待映射补全",
                                    "count": 1,
                                    "items": [
                                        {
                                            "record_id": "rec-mapping-gap",
                                            field_name: {"value": "missing_type"},
                                        }
                                    ],
                                }
                            ],
                        },
                    )

    def test_build_mappings_resource_rejects_malformed_section_count(self) -> None:
        with self.assertRaisesRegex(ValueError, r"sections\[\*\]\.count"):
            build_mappings_resource(
                entries=[],
                backlog={
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": "many",
                            "items": [],
                        }
                    ],
                },
            )

    def test_build_mappings_resource_rejects_malformed_backlog_item_revision_id(self) -> None:
        with self.assertRaisesRegex(ValueError, r"items\[\*\]\.revision_id"):
            build_mappings_resource(
                entries=[],
                backlog={
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "items": [
                                {
                                    "record_id": "rec-mapping-gap",
                                    "revision_id": "bad",
                                }
                            ],
                        }
                    ],
                },
            )

    def test_build_mappings_resource_rejects_malformed_candidate_resolution_nodes(self) -> None:
        malformed_cases = [
            (["transferor_group"], r"candidate_resolutions\[\*\]"),
            ([{"title": "候选集团", "evidence_chain": {"source": "catalog"}}], r"evidence_chain"),
        ]
        for candidate_resolutions, error_pattern in malformed_cases:
            with self.subTest(error_pattern=error_pattern):
                with self.assertRaisesRegex(ValueError, error_pattern):
                    build_mappings_resource(
                        entries=[],
                        backlog={
                            "sections": [
                                {
                                    "section_id": "mapping_gap_resolution",
                                    "title": "待映射补全",
                                    "count": 1,
                                    "items": [
                                        {
                                            "record_id": "rec-mapping-gap",
                                            "candidate_resolutions": candidate_resolutions,
                                        }
                                    ],
                                }
                            ],
                        },
                    )

    def test_build_mappings_resource_sanitizes_raw_business_label_from_sections(self) -> None:
        payload = build_mappings_resource(
            entries=[],
            backlog={
                "sections": [
                    {
                        "section_id": "mapping_gap_resolution",
                        "title": "待映射补全",
                        "count": 1,
                        "cta_kind": "reprocess_pending",
                        "items": [
                            {
                                "record_id": "rec-sentinel",
                                "state": "pending_mapping",
                                "status_label": "待补映射",
                                "raw_business_label": "UNTRUSTED_EXTERNAL_TEXT",
                                "business_label": "UNTRUSTED_EXTERNAL_TEXT",
                                "source_name": "UNTRUSTED_EXTERNAL_TEXT",
                                "candidate_resolutions": [
                                    {
                                        "title": "UNTRUSTED_EXTERNAL_TEXT",
                                        "source_name": "UNTRUSTED_EXTERNAL_TEXT",
                                        "target_value": "安全集团",
                                        "evidence_chain": [
                                            "UNTRUSTED_EXTERNAL_TEXT",
                                            {
                                                "UNTRUSTED_EXTERNAL_TEXT": "安全集团",
                                                "label": "UNTRUSTED_EXTERNAL_TEXT",
                                                "source_name": "UNTRUSTED_EXTERNAL_TEXT",
                                                "raw_business_label": "安全集团",
                                                "target_value": "安全集团",
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "summary": {"actionable_count": 1},
            },
        )

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", encoded)
        item = payload["sections"][0]["items"][0]
        self.assertNotIn("raw_business_label", item)
        self.assertEqual(item["business_label"], "未识别项目类型")
        self.assertEqual(item["status_label"], "待补映射")
        self.assertEqual(
            item["candidate_resolutions"][0]["evidence_chain"],
            [{"target_value": "安全集团"}],
        )


if __name__ == "__main__":
    unittest.main()
