from __future__ import annotations

import os
import unittest
from typing import Iterable

PEAP_PATH_ENV_NAMES = (
    "PEAP_APP_HOME",
    "PEAP_DATA_ROOT",
    "PEAP_ARCHIVE_ROOT",
    "PEAP_EXPORT_ROOT",
    "PEAP_CACHE_DIR",
    "PEAP_STREAMING_DB_PATH",
)

PROTECTED_PEAP_HOME = os.path.join(os.path.abspath(os.sep), "peap-protected-test-workspace")


def isolated_peap_env(temp_root: str, *, app_home: str | None = None) -> dict[str, str]:
    resolved_root = os.path.abspath(temp_root)
    resolved_app_home = os.path.abspath(app_home or os.path.join(resolved_root, "app_home"))
    return {
        "HOME": os.path.join(resolved_root, "home"),
        "PEAP_PROTECTED_WORKSPACE_ROOTS": PROTECTED_PEAP_HOME,
        "PEAP_APP_HOME": resolved_app_home,
        "PEAP_DATA_ROOT": os.path.join(resolved_app_home, "data"),
        "PEAP_ARCHIVE_ROOT": os.path.join(resolved_app_home, "archive"),
        "PEAP_EXPORT_ROOT": os.path.join(resolved_app_home, "exports"),
        "PEAP_CACHE_DIR": os.path.join(resolved_app_home, "cache"),
        "PEAP_STREAMING_DB_PATH": os.path.join(resolved_app_home, "data", "streaming_ingest.sqlite3"),
        "PEAP_DOCUMENTS_HOME": os.path.join(resolved_root, "legacy_documents"),
    }


def assert_paths_under_temp(
    testcase: unittest.TestCase,
    temp_root: str,
    paths: Iterable[str],
) -> None:
    resolved_root = os.path.abspath(temp_root)
    for path_value in paths:
        testcase.assertEqual(
            os.path.commonpath([resolved_root, os.path.abspath(path_value)]),
            resolved_root,
        )


def assert_peap_env_under_temp(testcase: unittest.TestCase, temp_root: str) -> None:
    assert_paths_under_temp(
        testcase,
        temp_root,
        (os.environ[name] for name in PEAP_PATH_ENV_NAMES),
    )
