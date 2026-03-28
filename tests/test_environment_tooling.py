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
        self.assertIn("build", pyproject["dependency-groups"])
        self.assertTrue(
            any(str(dep).startswith("pytest") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertTrue(
            any(str(dep).startswith("ruff") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertTrue(
            any(
                str(dep).startswith("pyinstaller")
                for dep in pyproject["dependency-groups"]["build"]
            )
        )
        self.assertNotIn("optional-dependencies", pyproject["project"])

    def test_ci_workflow_uses_uv_project_commands(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("astral-sh/setup-uv@v7", workflow)
        self.assertIn("uv python install", workflow)
        self.assertIn("uv lock --check", workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run ruff check", workflow)
        self.assertIn("uv run python -m pytest tests", workflow)
        self.assertNotIn("pip install -r requirements-dev.lock", workflow)

    def test_desktop_package_workflow_uses_uv(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "desktop-package.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("astral-sh/setup-uv@v7", workflow)
        self.assertIn("uv python install", workflow)
        self.assertNotIn("actions/setup-python", workflow)

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


if __name__ == "__main__":
    unittest.main()
