"""Configuration model and loader for PostProcess Engine."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .rules import BUILTIN_RULE_IDS

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional
    yaml = None


VALID_MODES = {"plan", "apply"}
DEFAULT_INCLUDE_GLOBS = ["*.xlsx", "*.xls", "*.csv"]


@dataclass
class RuleSettings:
    enabled: bool = True
    priority: int = 100
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PPEConfig:
    input_dir: str
    output_dir: str
    audit_dir: str
    exclude_dirs: List[str] = field(default_factory=list)
    mode: str = "plan"
    overwrite: bool = False
    output_suffix: str = "_postprocessed"
    scan_recursive: bool = True
    input_targets: List[str] = field(default_factory=list)
    include_globs: List[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE_GLOBS))
    rules: Dict[str, RuleSettings] = field(default_factory=dict)


def _default_rules() -> Dict[str, RuleSettings]:
    return {
        rule_id: RuleSettings(
            enabled=True,
            priority=(index + 1) * 10,
            params={},
        )
        for index, rule_id in enumerate(BUILTIN_RULE_IDS)
    }


def _normalize_mode(raw_mode: Any, *, field_name: str = "mode") -> str:
    mode = str(raw_mode or "plan").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"invalid {field_name}: {raw_mode!r}, expected one of {sorted(VALID_MODES)}")
    return mode


def _normalize_bool(raw_value: Any, *, default: bool, field_name: str) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)

    text = str(raw_value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid {field_name}: {raw_value!r}, expected boolean")


def _default_peap_data_root() -> str:
    system_dir = os.path.normpath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    project_root = os.path.normpath(os.path.abspath(os.path.join(system_dir, "..")))
    return os.path.abspath(os.path.join(project_root, "..", "PEAP_DATA"))


def _resolve_peap_data_root() -> str:
    raw = str(os.environ.get("PEAP_DATA_ROOT") or "").strip().strip('"').strip("'")
    if raw:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    return _default_peap_data_root()


def _expand_path_value(raw_value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(raw_value))
    if any(token in expanded for token in ("%PEAP_DATA_ROOT%", "${PEAP_DATA_ROOT}", "$PEAP_DATA_ROOT")):
        data_root = _resolve_peap_data_root()
        expanded = expanded.replace("%PEAP_DATA_ROOT%", data_root)
        expanded = expanded.replace("${PEAP_DATA_ROOT}", data_root)
        expanded = expanded.replace("$PEAP_DATA_ROOT", data_root)
        expanded = os.path.expandvars(expanded)
    return expanded


def _resolve_path(base_dir: str, raw_path: Any) -> str:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("path value is empty")
    expanded = _expand_path_value(value)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.abspath(os.path.join(base_dir, expanded)))


def _load_payload(config_path: str) -> Dict[str, Any]:
    path = os.path.abspath(config_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")

    suffix = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8-sig") as handle:
        content = handle.read()

    if suffix in {".json"}:
        payload = json.loads(content)
    elif suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError(
                "PyYAML is required to load YAML config. Install it or use a JSON config file."
            )
        payload = yaml.safe_load(content)  # type: ignore[union-attr]
    else:
        raise ValueError(f"unsupported config extension: {suffix!r}")

    if not isinstance(payload, dict):
        raise ValueError("config root must be an object")
    return payload


def _normalize_rule_params(base_dir: str, params: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(params or {})
    path_keys = [
        "mapping_file",
        "entity_type_mapping_file",
        "transferor_type_mapping_file",
        "transferor_group_mapping_file",
        "group_group_mapping_file",
        "group_type_mapping_file",
        "central_whitelist_file",
        "ministry_reference_file",
    ]
    for key in path_keys:
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = _resolve_path(base_dir, value)
    return normalized


def _parse_rules(raw_rules: Any, *, base_dir: str) -> Dict[str, RuleSettings]:
    rules = _default_rules()
    if raw_rules is None:
        return rules

    if isinstance(raw_rules, list):
        # If list is provided, only listed rules are enabled.
        listed: Dict[str, RuleSettings] = {
            rule_id: RuleSettings(enabled=False, priority=(idx + 1) * 10, params={})
            for idx, rule_id in enumerate(BUILTIN_RULE_IDS)
        }
        for idx, item in enumerate(raw_rules):
            if isinstance(item, str):
                if item in listed:
                    listed[item].enabled = True
                    listed[item].priority = (idx + 1) * 10
                continue
            if isinstance(item, dict):
                rule_id = str(item.get("id") or "").strip()
                if not rule_id:
                    continue
                listed[rule_id] = RuleSettings(
                    enabled=bool(item.get("enabled", True)),
                    priority=int(item.get("priority", (idx + 1) * 10)),
                    params=_normalize_rule_params(base_dir, dict(item.get("params") or {})),
                )
        return listed

    if not isinstance(raw_rules, dict):
        raise ValueError("rules must be an object or array")

    for rule_id, raw_setting in raw_rules.items():
        if isinstance(raw_setting, bool):
            base = rules.get(rule_id, RuleSettings())
            rules[rule_id] = RuleSettings(
                enabled=raw_setting,
                priority=base.priority,
                params=dict(base.params),
            )
            continue
        if not isinstance(raw_setting, dict):
            raise ValueError(f"rules.{rule_id} must be bool or object")

        base = rules.get(rule_id, RuleSettings())
        params = raw_setting.get("params", base.params)
        if not isinstance(params, dict):
            params = {}
        rules[rule_id] = RuleSettings(
            enabled=bool(raw_setting.get("enabled", base.enabled)),
            priority=int(raw_setting.get("priority", base.priority)),
            params=_normalize_rule_params(base_dir, dict(params)),
        )
    return rules


def resolve_mode(config: PPEConfig, mode_override: str | None) -> str:
    if mode_override is None:
        return config.mode
    return _normalize_mode(mode_override, field_name="mode override")


def load_config(config_path: str) -> PPEConfig:
    payload = _load_payload(config_path)
    base_dir = os.path.dirname(os.path.abspath(config_path))

    input_dir = _resolve_path(base_dir, payload.get("input_dir"))
    output_dir = _resolve_path(base_dir, payload.get("output_dir", "postprocess_output"))
    audit_dir = _resolve_path(
        base_dir,
        payload.get("audit_dir", os.path.join(os.path.dirname(output_dir), "postprocess_audit")),
    )
    mode = _normalize_mode(payload.get("mode", "plan"))

    raw_exclude_dirs = payload.get("exclude_dirs", [])
    exclude_dirs: List[str] = []
    if isinstance(raw_exclude_dirs, list):
        for item in raw_exclude_dirs:
            value = str(item or "").strip()
            if not value:
                continue
            exclude_dirs.append(_resolve_path(base_dir, value))
    exclude_dirs.extend([output_dir])
    normalized_exclude: List[str] = []
    seen_exclude = set()
    for path in exclude_dirs:
        normalized = os.path.normpath(os.path.abspath(path))
        if normalized in seen_exclude:
            continue
        seen_exclude.add(normalized)
        normalized_exclude.append(normalized)

    include_globs = payload.get("include_globs", DEFAULT_INCLUDE_GLOBS)
    if not isinstance(include_globs, list) or not include_globs:
        include_globs = list(DEFAULT_INCLUDE_GLOBS)
    else:
        include_globs = [str(item).strip() for item in include_globs if str(item).strip()]
        if not include_globs:
            include_globs = list(DEFAULT_INCLUDE_GLOBS)

    input_targets = payload.get("input_targets", [])
    if input_targets is None:
        input_targets = []
    if not isinstance(input_targets, list):
        raise ValueError("input_targets must be an array")
    input_targets = [str(item).strip() for item in input_targets if str(item).strip()]

    config = PPEConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        audit_dir=audit_dir,
        exclude_dirs=normalized_exclude,
        mode=mode,
        overwrite=_normalize_bool(payload.get("overwrite"), default=False, field_name="overwrite"),
        output_suffix=str(payload.get("output_suffix", "_postprocessed") or "_postprocessed"),
        scan_recursive=_normalize_bool(payload.get("scan_recursive"), default=True, field_name="scan_recursive"),
        input_targets=input_targets,
        include_globs=include_globs,
        rules=_parse_rules(payload.get("rules"), base_dir=base_dir),
    )

    if not os.path.isdir(config.input_dir):
        raise FileNotFoundError(f"input_dir does not exist: {config.input_dir}")
    return config
