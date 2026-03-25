"""Shared path and JSON helpers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict


def normalize_path(path_value: str) -> str:
    raw = str(path_value or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))


def resolve_path(path_value: str, *, base_dir: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("path value is empty")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(base_dir, expanded))


def load_json_file(path_value: str, *, encoding: str = "utf-8-sig") -> Any:
    with open(path_value, "r", encoding=encoding) as handle:
        return json.load(handle)


def load_json_object(
    path_value: str,
    *,
    encoding: str = "utf-8-sig",
    label: str = "json file",
) -> Dict[str, Any]:
    payload = load_json_file(path_value, encoding=encoding)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object: {path_value}")
    return payload


def read_optional_json_object(path_value: str, *, encoding: str = "utf-8") -> Dict[str, Any] | None:
    target = str(path_value or "").strip()
    if not target or not os.path.isfile(target):
        return None
    try:
        payload = load_json_file(target, encoding=encoding)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_json_file(
    path_value: str,
    payload: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int = 2,
    sort_keys: bool = True,
) -> str:
    output_path = os.path.abspath(str(path_value or "").strip())
    if not output_path:
        raise ValueError("output json path is empty")
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding=encoding) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=ensure_ascii,
            indent=indent,
            sort_keys=sort_keys,
        )
    return output_path


def write_json_file_atomic(
    path_value: str,
    payload: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int = 2,
    sort_keys: bool = True,
) -> str:
    output_path = os.path.abspath(str(path_value or "").strip())
    if not output_path:
        raise ValueError("output json path is empty")
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{output_path}.tmp"
    with open(tmp_path, "w", encoding=encoding) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=ensure_ascii,
            indent=indent,
            sort_keys=sort_keys,
        )
    os.replace(tmp_path, output_path)
    return output_path


def resolve_runtime_config_file(
    project_root: str,
    *,
    env_var: str = "PEAP_RUNTIME_CONFIG_FILE",
    relative_default: str | None = None,
) -> str:
    config_file_env = normalize_path(os.environ.get(env_var, ""))
    if config_file_env:
        return config_file_env

    default_relative = relative_default or os.path.join("assets", "runtime_config.json")
    return os.path.abspath(os.path.join(project_root, default_relative))


def load_runtime_config(
    project_root: str,
    *,
    env_var: str = "PEAP_RUNTIME_CONFIG_FILE",
    relative_default: str | None = None,
) -> tuple[str, Dict[str, Any]]:
    config_file = resolve_runtime_config_file(
        project_root,
        env_var=env_var,
        relative_default=relative_default,
    )
    if not os.path.isfile(config_file):
        template_file = os.path.abspath(
            os.path.join(project_root, "assets", "runtime_config.template.json")
        )
        raise RuntimeError(
            "Runtime config file not found. "
            f"set {env_var} or deploy {config_file}. "
            f"Bootstrap from template: {template_file}"
        )
    try:
        payload = load_json_object(
            config_file,
            encoding="utf-8-sig",
            label="runtime config",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to load runtime config: {config_file} ({exc})") from exc
    return config_file, payload
