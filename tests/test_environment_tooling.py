from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class EnvironmentToolingTest(unittest.TestCase):
    def test_pyproject_uses_uv_dependency_groups(self) -> None:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(pyproject["tool"]["uv"]["default-groups"], ["dev"])
        self.assertIn("dev", pyproject["dependency-groups"])
        self.assertTrue(
            any(str(dep).startswith("pytest") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertTrue(
            any(str(dep).startswith("ruff") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertNotIn("build", pyproject["dependency-groups"])
        self.assertNotIn("optional-dependencies", pyproject["project"])

    def test_pyproject_exports_shared_runtime_packages_for_non_parser_contracts(self) -> None:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_includes = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
        streaming_store = (REPO_ROOT / "peap" / "streaming_store.py").read_text(encoding="utf-8")

        self.assertIn("from peap_core.record_identity import", streaming_store)
        self.assertIn("peap_core*", package_includes)
        self.assertIn("desktop_backend*", package_includes)

    def test_parser_subsystem_contract_modules_stay_runtime_free(self) -> None:
        for relative_path in (
            "peap_core/snapshot_contracts.py",
            "peap_core/page_parse_contracts.py",
            "peap_core/record_contracts.py",
        ):
            contract_file = REPO_ROOT / relative_path
            self.assertTrue(contract_file.exists(), msg=f"missing contract file: {relative_path}")
            source = contract_file.read_text(encoding="utf-8")
            self.assertNotIn("from peap import", source, msg=relative_path)
            self.assertNotIn("import peap", source, msg=relative_path)
            self.assertNotIn("from peap_parsers import", source, msg=relative_path)
            self.assertNotIn("import peap_parsers", source, msg=relative_path)
            self.assertNotIn("from desktop_backend", source, msg=relative_path)
            self.assertNotIn("import desktop_backend", source, msg=relative_path)
            self.assertNotIn("download_runner", source, msg=relative_path)
            self.assertNotIn("download_tasks", source, msg=relative_path)
            self.assertNotIn("download_oneclick", source, msg=relative_path)

        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("astral-sh/setup-uv@v7", workflow)
        self.assertIn("uv python install", workflow)
        self.assertIn("uv lock --check", workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run ruff check", workflow)
        self.assertIn("uv run python -m pytest tests", workflow)
        self.assertNotIn("pip install -r requirements-dev.lock", workflow)

    def test_desktop_package_workflow_is_removed(self) -> None:
        self.assertFalse((REPO_ROOT / ".github" / "workflows" / "desktop-package.yml").exists())

    def test_bootstrap_script_uses_uv_managed_project_environment(self) -> None:
        script = (REPO_ROOT / "scripts" / "bootstrap_desktop_env.sh").read_text(encoding="utf-8")

        self.assertIn("uv sync --locked", script)
        self.assertIn("uv run python -m playwright install chromium", script)
        self.assertIn('echo "  python: $VENV_DIR/bin/python"', script)
        self.assertNotIn("pyenv", script)
        self.assertNotIn(".venv-desktop", script)
        self.assertNotIn("pip install", script)

    def test_readme_uses_uv_as_the_only_python_workflow(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync", readme)
        self.assertIn("uv run python -m desktop_backend.app_backend", readme)
        self.assertNotIn("brew install pyenv", readme)
        self.assertNotIn(".venv-desktop", readme)
        self.assertNotIn("requirements.txt", readme)

    def test_active_docs_avoid_retired_packaged_runtime_narrative(self) -> None:
        banned_phrases = ("packaged runtime", "backend sidecar")
        active_docs = (
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "desktop_product_runbook_2026-03-26.md",
            REPO_ROOT / "docs" / "release_gate.md",
        )

        for doc_path in active_docs:
            text = doc_path.read_text(encoding="utf-8").lower()
            for phrase in banned_phrases:
                self.assertNotIn(phrase, text, msg=f"{doc_path} still contains {phrase!r}")

    def test_desktop_app_npm_test_covers_all_checked_in_node_tests(self) -> None:
        package_json = json.loads((REPO_ROOT / "desktop_app" / "package.json").read_text(encoding="utf-8"))
        test_script = package_json["scripts"]["test"]
        expected_tests = sorted(
            f"./{path.relative_to(REPO_ROOT / 'desktop_app').as_posix()}"
            for path in (REPO_ROOT / "desktop_app").rglob("*.test.js")
            if "node_modules" not in path.parts
        )

        self.assertIn("node --test", test_script)
        for test_path in expected_tests:
            self.assertIn(test_path, test_script)

    def test_desktop_app_omits_packaging_scripts_and_builder_dependency(self) -> None:
        package_json = json.loads((REPO_ROOT / "desktop_app" / "package.json").read_text(encoding="utf-8"))
        scripts = package_json["scripts"]
        dev_dependencies = package_json["devDependencies"]

        self.assertNotIn("build:backend", scripts)
        self.assertNotIn("package:desktop", scripts)
        self.assertNotIn("pack", scripts)
        self.assertNotIn("dist:mac", scripts)
        self.assertNotIn("dist:win", scripts)
        self.assertNotIn("electron-builder", dev_dependencies)


if __name__ == "__main__":
    unittest.main()
