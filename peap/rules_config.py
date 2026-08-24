"""Normalize postprocess rules for runtime consumers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict


def load_effective_rules_config(config_path: str | None) -> Dict[str, Any]:
    target = str(config_path or "").strip()
    if not target:
        return {}
    from peap_postprocess.postprocess_engine.config import load_rules_config

    rules = load_rules_config(target)
    if not isinstance(rules, Mapping):
        raise TypeError("rules_config must be a mapping")
    return {
        rule_id: {
            "enabled": bool(rule.enabled),
            "priority": int(rule.priority),
            "params": dict(rule.params),
        }
        for rule_id, rule in rules.items()
    }


__all__ = ["load_effective_rules_config"]
