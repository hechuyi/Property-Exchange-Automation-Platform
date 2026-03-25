"""Rule registry and execution-plan builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .base import BaseRule
from .builtin import BUILTIN_RULE_CLASSES, BUILTIN_RULE_IDS


@dataclass(frozen=True)
class RuleBinding:
    rule: BaseRule
    priority: int


def _read_setting(setting: Any, key: str, default: Any) -> Any:
    if isinstance(setting, dict):
        return setting.get(key, default)
    return getattr(setting, key, default)


class RuleRegistry:
    """Holds available rule classes and resolves runtime plan from config."""

    def __init__(self) -> None:
        self._rule_classes: Dict[str, type[BaseRule]] = {
            rule_cls.rule_id(): rule_cls for rule_cls in BUILTIN_RULE_CLASSES
        }
        self._rule_order = {rule_id: idx for idx, rule_id in enumerate(BUILTIN_RULE_IDS)}

    def known_rule_ids(self) -> List[str]:
        return list(BUILTIN_RULE_IDS)

    def build_plan(self, rules_config: Dict[str, Any]) -> Tuple[List[RuleBinding], List[str]]:
        warnings: List[str] = []
        bindings: List[RuleBinding] = []

        for rule_id, setting in rules_config.items():
            rule_cls = self._rule_classes.get(rule_id)
            if rule_cls is None:
                warnings.append(f"Unknown rule id in config, skipped: {rule_id}")
                continue

            enabled = bool(_read_setting(setting, "enabled", True))
            if not enabled:
                continue

            priority = int(_read_setting(setting, "priority", 100))
            params = _read_setting(setting, "params", {})
            if not isinstance(params, dict):
                params = {}
            bindings.append(RuleBinding(rule=rule_cls(params=params), priority=priority))

        bindings.sort(
            key=lambda item: (
                item.priority,
                self._rule_order.get(item.rule.rule_id(), 10**6),
            )
        )
        return bindings, warnings
