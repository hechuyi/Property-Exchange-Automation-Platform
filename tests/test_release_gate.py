from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.check_release_gate import CheckResult, evaluate_release_gate


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ReleaseGateTest(unittest.TestCase):
    def _make_repo(
        self,
        *,
        release_gate: str,
        readme: str = "# README\nuv sync\n",
        development_plan: str = "# 开发计划\n",
    ) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo_root = Path(temp_dir.name)

        _write_text(repo_root / "README.md", readme)
        _write_text(repo_root / "docs" / "development_plan.md", development_plan)
        _write_text(repo_root / "docs" / "project_layout.md", "# 项目结构\nuv sync\n")
        _write_text(repo_root / "docs" / "submission_guide.md", "# 提交归档指南\nuv run python\n")
        _write_text(repo_root / "docs" / "desktop_product_runbook_2026-03-26.md", "# 运行手册\nuv sync\n")
        _write_text(repo_root / "docs" / "release_gate.md", release_gate)
        return repo_root

    def test_release_gate_is_blocked_when_smoke_checklist_has_pending_items(self) -> None:
        repo_root = self._make_repo(
            release_gate=textwrap.dedent(
                """\
                # 发布门槛

                ## 真实 Electron Smoke
                - [x] one-click 主路径
                - [ ] manual-import 主路径

                ## 当前发布状态
                - 当前标签：`release_candidate`
                """
            )
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult("uv lock --check", True, "ok"),
                CheckResult("uv run python -m unittest discover -s tests -q", True, "ok"),
                CheckResult("cd desktop_app && npm test", True, "ok"),
            ],
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.release_label, "release_candidate")
        self.assertIn("manual-import 主路径", report.summary)

    def test_release_gate_is_blocked_when_active_docs_reintroduce_legacy_environment_terms(self) -> None:
        repo_root = self._make_repo(
            release_gate=textwrap.dedent(
                """\
                # 发布门槛

                ## 真实 Electron Smoke
                - [x] one-click 主路径
                - [x] manual-import 主路径

                ## 当前发布状态
                - 当前标签：`release_candidate`
                """
            ),
            readme="# README\n请先使用 .venv-desktop/bin/python\n",
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult("uv lock --check", True, "ok"),
                CheckResult("uv run python -m unittest discover -s tests -q", True, "ok"),
                CheckResult("cd desktop_app && npm test", True, "ok"),
            ],
        )

        self.assertFalse(report.passed)
        self.assertIn(".venv-desktop", report.summary)

    def test_release_gate_passes_only_when_commands_docs_and_smoke_are_all_green(self) -> None:
        repo_root = self._make_repo(
            release_gate=textwrap.dedent(
                """\
                # 发布门槛

                ## 真实 Electron Smoke
                - [x] one-click 主路径
                - [x] manual-import 主路径

                ## 当前发布状态
                - 当前标签：`final_release`
                """
            )
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult("uv lock --check", True, "ok"),
                CheckResult("uv run python -m unittest discover -s tests -q", True, "ok"),
                CheckResult("cd desktop_app && npm test", True, "ok"),
            ],
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.release_label, "final_release")
        self.assertEqual(report.summary, "PASS")

    def test_development_plan_can_reference_legacy_terms_as_forbidden_non_goal_without_blocking_gate(self) -> None:
        repo_root = self._make_repo(
            release_gate=textwrap.dedent(
                """\
                # 发布门槛

                ## 真实 Electron Smoke
                - [x] one-click 主路径
                - [x] manual-import 主路径

                ## 当前发布状态
                - 当前标签：`final_release`
                """
            ),
            development_plan="# 开发计划\n不要重新引入 .venv-desktop 或 pyenv。\n",
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult("uv lock --check", True, "ok"),
                CheckResult("uv run python -m unittest discover -s tests -q", True, "ok"),
                CheckResult("cd desktop_app && npm test", True, "ok"),
            ],
        )

        self.assertTrue(report.passed)


if __name__ == "__main__":
    unittest.main()
