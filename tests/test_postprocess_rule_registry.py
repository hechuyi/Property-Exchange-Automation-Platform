from __future__ import annotations

import os
import tempfile
import unittest

from peap_postprocess.postprocess_engine.config import RuleSettings, _parse_rules
from peap_postprocess.postprocess_engine.contracts import CanonicalRecord, RuleResult
from peap_postprocess.postprocess_engine.rules import BUILTIN_RULE_IDS
from peap_postprocess.postprocess_engine.rules.base import BaseRule
from peap_postprocess.postprocess_engine.rules.registry import RuleRegistry


class _FakeRuleA(BaseRule):
    @classmethod
    def rule_id(cls) -> str:
        return "rule_a"

    def apply(self, record: CanonicalRecord, context: dict[str, object]) -> RuleResult:  # noqa: ARG002
        return RuleResult()


class _FakeRuleB(BaseRule):
    @classmethod
    def rule_id(cls) -> str:
        return "rule_b"

    def apply(self, record: CanonicalRecord, context: dict[str, object]) -> RuleResult:  # noqa: ARG002
        return RuleResult()


class _FakeRuleC(BaseRule):
    @classmethod
    def rule_id(cls) -> str:
        return "rule_c"

    def apply(self, record: CanonicalRecord, context: dict[str, object]) -> RuleResult:  # noqa: ARG002
        return RuleResult()


class PostProcessRuleRegistryTest(unittest.TestCase):
    def test_known_rule_ids_match_builtin_order(self) -> None:
        registry = RuleRegistry()

        self.assertEqual(registry.known_rule_ids(), list(BUILTIN_RULE_IDS))

    def test_build_plan_orders_rules_and_filters_disabled_entries(self) -> None:
        registry = RuleRegistry()
        registry._rule_classes = {
            "rule_a": _FakeRuleA,
            "rule_b": _FakeRuleB,
            "rule_c": _FakeRuleC,
        }
        registry._rule_order = {
            "rule_b": 0,
            "rule_a": 1,
            "rule_c": 2,
        }

        plan, warnings = registry.build_plan(
            {
                "rule_c": {"enabled": True, "priority": 20, "params": {"gamma": "3"}},
                "rule_a": RuleSettings(enabled=True, priority=20, params={"alpha": "1"}),
                "rule_b": RuleSettings(enabled=False, priority=1, params={"beta": "2"}),
            }
        )

        self.assertEqual(
            [binding.rule.rule_id() for binding in plan],
            ["rule_a", "rule_c"],
        )
        self.assertEqual(plan[0].priority, 20)
        self.assertEqual(plan[0].rule.params, {"alpha": "1"})
        self.assertEqual(plan[1].rule.params, {"gamma": "3"})
        self.assertEqual(warnings, [])

    def test_build_plan_rejects_unknown_rule_id(self) -> None:
        registry = RuleRegistry()
        registry._rule_classes = {"rule_a": _FakeRuleA}
        registry._rule_order = {"rule_a": 0}

        with self.assertRaisesRegex(ValueError, "Unknown rule id.*unknown_rule"):
            registry.build_plan({"unknown_rule": {"enabled": True}})

    def test_build_plan_rejects_non_object_params_for_enabled_known_rule(self) -> None:
        registry = RuleRegistry()
        registry._rule_classes = {"rule_a": _FakeRuleA}
        registry._rule_order = {"rule_a": 0}

        with self.assertRaisesRegex(ValueError, "rules\\.rule_a\\.params must be an object"):
            registry.build_plan({"rule_a": {"enabled": True, "params": ["bad"]}})

    def test_build_plan_rejects_non_object_params_before_record_family_filtering(self) -> None:
        registry = RuleRegistry()
        registry._rule_classes = {"rule_a": _FakeRuleA}
        registry._rule_order = {"rule_a": 0}

        with self.assertRaisesRegex(ValueError, "rules\\.rule_a\\.params must be an object"):
            registry.build_plan(
                {
                    "rule_a": {
                        "enabled": True,
                        "params": ["bad"],
                        "record_families": ["deal"],
                    }
                },
                record_family="listing",
            )

    def test_parse_rules_list_mode_resets_defaults_and_resolves_path_params(self) -> None:
        first_rule_id, second_rule_id, third_rule_id = BUILTIN_RULE_IDS[:3]

        with tempfile.TemporaryDirectory() as tmp_dir:
            rules = _parse_rules(
                [
                    second_rule_id,
                    {
                        "id": first_rule_id,
                        "priority": 3,
                        "params": {
                            "mapping_file": os.path.join("mappings", "group.csv"),
                        },
                    },
                    {
                        "id": "unknown_rule",
                        "enabled": True,
                        "priority": 9,
                    },
                ],
                base_dir=tmp_dir,
            )

            self.assertTrue(rules[second_rule_id].enabled)
            self.assertEqual(rules[second_rule_id].priority, 10)
            self.assertTrue(rules[first_rule_id].enabled)
            self.assertEqual(rules[first_rule_id].priority, 3)
            self.assertEqual(
                rules[first_rule_id].params["mapping_file"],
                os.path.abspath(os.path.join(tmp_dir, "mappings", "group.csv")),
            )
            self.assertNotIn(third_rule_id, rules)
            self.assertIn("unknown_rule", rules)

            with self.assertRaisesRegex(ValueError, "Unknown rule id.*unknown_rule"):
                RuleRegistry().build_plan(rules)

    def test_parse_rules_rejects_list_rule_missing_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "rules\\[0\\]\\.id is required"):
            _parse_rules([{"enabled": True}], base_dir=os.getcwd())

    def test_parse_rules_rejects_non_object_rule_params(self) -> None:
        rule_id = BUILTIN_RULE_IDS[0]

        with self.assertRaisesRegex(ValueError, f"rules\\.{rule_id}\\.params must be an object"):
            _parse_rules({rule_id: {"params": ["bad"]}}, base_dir=os.getcwd())

    def test_rule_plan_filters_rules_by_record_family_scope(self) -> None:
        first_rule_id, second_rule_id = BUILTIN_RULE_IDS[:2]
        registry = RuleRegistry()

        rules = _parse_rules(
            [
                {
                    "id": first_rule_id,
                    "enabled": True,
                    "priority": 10,
                    "record_families": ["listing"],
                },
                {
                    "id": second_rule_id,
                    "enabled": True,
                    "priority": 20,
                    "record_families": ["deal"],
                },
            ],
            base_dir=os.getcwd(),
        )

        listing_plan, listing_warnings = registry.build_plan(rules, record_family="listing")
        deal_plan, deal_warnings = registry.build_plan(rules, record_family="deal")

        self.assertEqual([binding.rule.rule_id() for binding in listing_plan], [first_rule_id])
        self.assertEqual([binding.rule.rule_id() for binding in deal_plan], [second_rule_id])
        self.assertEqual(listing_warnings, [])
        self.assertEqual(deal_warnings, [])


if __name__ == "__main__":
    unittest.main()
