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

    def test_build_plan_orders_rules_and_filters_unknown_or_disabled_entries(self) -> None:
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
                "unknown_rule": {"enabled": True},
                "rule_c": {"enabled": True, "priority": 20, "params": ["bad"]},
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
        self.assertEqual(plan[1].rule.params, {})
        self.assertEqual(warnings, ["Unknown rule id in config, skipped: unknown_rule"])

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
            self.assertFalse(rules[third_rule_id].enabled)
            self.assertIn("unknown_rule", rules)

            plan, warnings = RuleRegistry().build_plan(rules)

        self.assertEqual(
            [binding.rule.rule_id() for binding in plan],
            [first_rule_id, second_rule_id],
        )
        self.assertEqual(warnings, ["Unknown rule id in config, skipped: unknown_rule"])


if __name__ == "__main__":
    unittest.main()
