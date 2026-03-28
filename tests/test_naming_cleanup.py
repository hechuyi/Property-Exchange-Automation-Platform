from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
SCAN_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".ps1", ".bat", ".gitignore"}
SCAN_TARGETS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / ".gitignore",
    REPO_ROOT / "config.py",
    REPO_ROOT / "bin",
    REPO_ROOT / "docs",
    REPO_ROOT / "peap",
    REPO_ROOT / "peap_parsers",
    REPO_ROOT / "peap_postprocess",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tests",
]
RETIRED_NAMES = (
    "app_v2",
    "postprocess_system",
    "main_v2.py",
    "main_download_sse.py",
    "main_download_oneclick.py",
    "main_daily_oneclick.py",
    "main_public_resource_deals.py",
    "parser_regression_test.py",
    "main_download_auto_split.py",
    "main_postprocess.py",
    "_bootstrap.py",
    "PROJECT_LAYOUT.md",
    "DEVELOPMENT_PLAN.md",
    "PARSER_RULE_RISK_REPORT.md",
    "SUBMISSION_GUIDE.md",
    "POSTPROCESS_ENGINE_PLAN.md",
    "PPE_BUSINESS_USER_GUIDE.md",
    "postprocess.external.template.json",
    "run_parser.ps1",
    "run_parser.bat",
    "run_daily_pipeline.ps1",
    "run_daily_pipeline.bat",
    "run_postprocess.ps1",
    "run_postprocess.bat",
)


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for target in SCAN_TARGETS:
        if not target.exists():
            continue
        if target.is_file():
            files.append(target)
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if path == THIS_FILE:
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in SCAN_SUFFIXES and path.name not in {".gitignore"}:
                continue
            files.append(path)
    return files


class NamingCleanupTest(unittest.TestCase):
    def test_retired_entrypoints_and_packages_are_removed(self) -> None:
        retired_paths = [
            REPO_ROOT / "app_v2",
            REPO_ROOT / "parsers",
            REPO_ROOT / "postprocess_system",
            REPO_ROOT / "bin" / "_bootstrap.py",
            REPO_ROOT / "bin" / "bootstrap.py",
            REPO_ROOT / "bin" / "daily_pipeline.py",
            REPO_ROOT / "bin" / "download.py",
            REPO_ROOT / "bin" / "download_oneclick.py",
            REPO_ROOT / "bin" / "main_v2.py",
            REPO_ROOT / "bin" / "main_download_sse.py",
            REPO_ROOT / "bin" / "main_download_oneclick.py",
            REPO_ROOT / "bin" / "main_daily_oneclick.py",
            REPO_ROOT / "bin" / "main_public_resource_deals.py",
            REPO_ROOT / "bin" / "parse.py",
            REPO_ROOT / "bin" / "parser_regression.py",
            REPO_ROOT / "bin" / "peap.py",
            REPO_ROOT / "bin" / "parser_regression_test.py",
            REPO_ROOT / "bin" / "public_resource_deals.py",
            REPO_ROOT / "bin" / "main_download_auto_split.py",
            REPO_ROOT / "bin" / "run_parser.ps1",
            REPO_ROOT / "bin" / "run_parser.bat",
            REPO_ROOT / "bin" / "run_daily_pipeline.ps1",
            REPO_ROOT / "bin" / "run_daily_pipeline.bat",
            REPO_ROOT / "peap_postprocess" / "run_postprocess.ps1",
            REPO_ROOT / "peap_postprocess" / "run_postprocess.bat",
        ]

        for path in retired_paths:
            self.assertFalse(path.exists(), str(path))

    def test_canonical_entrypoints_and_packages_exist(self) -> None:
        canonical_paths = [
            REPO_ROOT / "peap",
            REPO_ROOT / "peap_parsers",
            REPO_ROOT / "peap_postprocess",
            REPO_ROOT / "peap_postprocess" / "run_postprocess.py",
            REPO_ROOT / "docs" / "project_layout.md",
            REPO_ROOT / "docs" / "development_plan.md",
            REPO_ROOT / "docs" / "parser_rule_risk_report.md",
            REPO_ROOT / "docs" / "submission_guide.md",
            REPO_ROOT / "peap_postprocess" / "postprocess_engine_plan.md",
            REPO_ROOT / "peap_postprocess" / "ppe_business_user_guide.md",
            REPO_ROOT / "peap_postprocess" / "ppe_config" / "postprocess_external_template.json",
        ]

        for path in canonical_paths:
            self.assertTrue(path.exists(), str(path))

    def test_active_surface_no_longer_mentions_retired_names(self) -> None:
        offenders: list[str] = []
        for path in _iter_scan_files():
            content = path.read_text(encoding="utf-8")
            for retired_name in RETIRED_NAMES:
                if retired_name in content:
                    relative = path.relative_to(REPO_ROOT)
                    offenders.append(f"{relative}: {retired_name}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
