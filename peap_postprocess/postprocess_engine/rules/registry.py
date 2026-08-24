"""Rule registry and execution-plan builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from peap_core.family_catalog import resolve_family_descriptor

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


def _normalize_record_family(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    descriptor = resolve_family_descriptor(text)
    return descriptor.family_id if descriptor is not None else text.lower()


def _record_families_for_setting(setting: Any) -> tuple[str, ...]:
    families = _read_setting(setting, "record_families", ())
    if isinstance(families, str):
        raw_values = [families]
    elif isinstance(families, (list, tuple, set)):
        raw_values = list(families)
    else:
        raw_values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        family = _normalize_record_family(value)
        if not family or family in seen:
            continue
        seen.add(family)
        normalized.append(family)
    return tuple(normalized)


class RuleRegistry:
    """Holds available rule classes and resolves runtime plan from config."""

    def __init__(self) -> None:
        self._rule_classes: Dict[str, type[BaseRule]] = {
            rule_cls.rule_id(): rule_cls for rule_cls in BUILTIN_RULE_CLASSES
        }
        self._rule_order = {rule_id: idx for idx, rule_id in enumerate(BUILTIN_RULE_IDS)}

    def known_rule_ids(self) -> List[str]:
        return list(BUILTIN_RULE_IDS)

    def build_plan(
        self,
        rules_config: Dict[str, Any],
        *,
        record_family: str | None = None,
    ) -> Tuple[List[RuleBinding], List[str]]:
        warnings: List[str] = []
        bindings: List[RuleBinding] = []
        normalized_family = _normalize_record_family(record_family)

        for rule_id, setting in rules_config.items():
            rule_cls = self._rule_classes.get(rule_id)
            if rule_cls is None:
                raise ValueError(f"Unknown rule id in config: {rule_id}")

            params = _read_setting(setting, "params", {})
            if not isinstance(params, dict):
                raise ValueError(f"rules.{rule_id}.params must be an object")

            scoped_families = _record_families_for_setting(setting)
            if scoped_families and (not normalized_family or normalized_family not in scoped_families):
                continue

            enabled = bool(_read_setting(setting, "enabled", True))
            if not enabled:
                continue

            priority = int(_read_setting(setting, "priority", 100))
            bindings.append(RuleBinding(rule=rule_cls(params=params), priority=priority))

        bindings.sort(
            key=lambda item: (
                item.priority,
                self._rule_order.get(item.rule.rule_id(), 10**6),
            )
        )
        return bindings, warnings
