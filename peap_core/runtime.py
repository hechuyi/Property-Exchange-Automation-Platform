"""Shared path and JSON helpers."""

from __future__ import annotations

import json
import os
import secrets
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
    return os.path.abspath(os.path.join(normalize_path(base_dir), expanded))


def resolve_runtime_data_root(configured_data_root: str, *, project_root: str) -> str:
    """Resolve the shared data root using the desktop environment precedence."""

    explicit_data_root = normalize_path(os.environ.get("PEAP_DATA_ROOT", ""))
    if explicit_data_root:
        return explicit_data_root
    workspace_root = normalize_path(
        os.environ.get("PEAP_WORKSPACE_ROOT")
        or os.environ.get("PEAP_APP_HOME")
        or os.environ.get("PEAP_DOCUMENTS_HOME")
        or ""
    )
    if workspace_root:
        return os.path.join(workspace_root, "data")
    return resolve_path(configured_data_root, base_dir=project_root)


def load_json_file(path_value: str, *, encoding: str = "utf-8-sig") -> Any:
    with open(normalize_path(path_value), "r", encoding=encoding) as handle:
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
    target = normalize_path(path_value)
    if not target or not os.path.exists(target):
        return None
    return load_json_object(target, encoding=encoding, label="optional json file")


def write_json_file(
    path_value: str,
    payload: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int = 2,
    sort_keys: bool = True,
) -> str:
    output_path = normalize_path(path_value)
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
    output_path = normalize_path(path_value)
    if not output_path:
        raise ValueError("output json path is empty")
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    temp_path: str | None = None
    temp_fd: int | None = None
    basename = os.path.basename(output_path)
    try:
        while temp_fd is None:
            candidate = os.path.join(parent, f".{basename}.{secrets.token_hex(16)}.tmp")
            try:
                temp_fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o666,
                )
            except FileExistsError:
                continue
            temp_path = candidate

        handle = os.fdopen(temp_fd, "w", encoding=encoding)
        temp_fd = None
        with handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=ensure_ascii,
                indent=indent,
                sort_keys=sort_keys,
            )
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, output_path)
        temp_path = None

        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
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
    return os.path.abspath(os.path.join(normalize_path(project_root), default_relative))


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
