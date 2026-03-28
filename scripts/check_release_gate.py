from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_GATE_DOC = Path("docs/release_gate.md")
REQUIRED_ACTIVE_DOCS = (
    Path("README.md"),
    Path("docs/development_plan.md"),
    Path("docs/project_layout.md"),
    Path("docs/submission_guide.md"),
    Path("docs/desktop_product_runbook_2026-03-26.md"),
    RELEASE_GATE_DOC,
)
LEGACY_SCAN_DOCS = (
    Path("README.md"),
    Path("docs/project_layout.md"),
    Path("docs/submission_guide.md"),
    Path("docs/desktop_product_runbook_2026-03-26.md"),
    RELEASE_GATE_DOC,
)
LEGACY_ACTIVE_DOC_TERMS = (
    ".venv-desktop",
    "pyenv",
    "requirements.lock",
    "requirements-dev.lock",
    "desktop_backend/requirements.lock.txt",
    "desktop_backend/requirements.build.lock.txt",
)
AUTOMATED_COMMANDS = (
    ("uv lock --check", ("uv", "lock", "--check"), Path(".")),
    (
        "uv run python -m unittest discover -s tests -q",
        ("uv", "run", "python", "-m", "unittest", "discover", "-s", "tests", "-q"),
        Path("."),
    ),
    ("cd desktop_app && npm test", ("npm", "test"), Path("desktop_app")),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReleaseGateReport:
    passed: bool
    release_label: str
    checks: tuple[CheckResult, ...]
    summary: str


def _extract_markdown_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return match.group("body").strip()


def _parse_checkbox_lines(section_text: str) -> list[tuple[str, bool]]:
    items: list[tuple[str, bool]] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        match = re.match(r"- \[(?P<state>[ xX])\] (?P<label>.+)$", line)
        if not match:
            continue
        items.append((match.group("label").strip(), match.group("state").lower() == "x"))
    return items


def _parse_release_label(section_text: str) -> str:
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        match = re.match(r"- 当前标签：`?(?P<label>[^`]+)`?$", line)
        if match:
            return match.group("label").strip()
    return "release_candidate"


def check_active_docs(repo_root: Path) -> CheckResult:
    missing_docs = [str(path) for path in REQUIRED_ACTIVE_DOCS if not (repo_root / path).exists()]
    if missing_docs:
        return CheckResult(
            "active docs",
            False,
            f"Missing required active docs: {', '.join(missing_docs)}",
        )

    offenders: list[str] = []
    for relative_path in LEGACY_SCAN_DOCS:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        for legacy_term in LEGACY_ACTIVE_DOC_TERMS:
            if legacy_term in text:
                offenders.append(f"{relative_path}: {legacy_term}")

    if offenders:
        return CheckResult(
            "active docs",
            False,
            "Legacy environment terms found in active docs: " + "; ".join(offenders),
        )

    return CheckResult("active docs", True, "Active docs align with the uv-only mainline.")


def check_smoke_checklist(repo_root: Path) -> CheckResult:
    doc_path = repo_root / RELEASE_GATE_DOC
    if not doc_path.exists():
        return CheckResult("real Electron smoke", False, f"Missing {RELEASE_GATE_DOC.as_posix()}")

    text = doc_path.read_text(encoding="utf-8")
    smoke_items = _parse_checkbox_lines(_extract_markdown_section(text, "真实 Electron Smoke"))
    if not smoke_items:
        return CheckResult(
            "real Electron smoke",
            False,
            "No smoke checklist items found under `## 真实 Electron Smoke`.",
        )

    pending_items = [label for label, checked in smoke_items if not checked]
    if pending_items:
        return CheckResult(
            "real Electron smoke",
            False,
            "Pending smoke items: " + ", ".join(pending_items),
        )

    return CheckResult("real Electron smoke", True, "All required Electron smoke items are checked.")


def load_release_label(repo_root: Path) -> str:
    doc_path = repo_root / RELEASE_GATE_DOC
    if not doc_path.exists():
        return "release_candidate"
    text = doc_path.read_text(encoding="utf-8")
    return _parse_release_label(_extract_markdown_section(text, "当前发布状态"))


def run_automated_commands(repo_root: Path) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []
    for name, command, relative_cwd in AUTOMATED_COMMANDS:
        completed = subprocess.run(
            command,
            cwd=repo_root / relative_cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        detail = output.splitlines()[-1] if output else f"exit={completed.returncode}"
        results.append(CheckResult(name, completed.returncode == 0, detail))
    return tuple(results)


def evaluate_release_gate(
    repo_root: Path,
    *,
    automated_results: Sequence[CheckResult] | None = None,
) -> ReleaseGateReport:
    checks: list[CheckResult] = list(
        automated_results if automated_results is not None else run_automated_commands(repo_root)
    )
    checks.append(check_active_docs(repo_root))
    checks.append(check_smoke_checklist(repo_root))

    passed = all(result.passed for result in checks)
    release_label = load_release_label(repo_root)
    if passed:
        summary = "PASS"
    else:
        failed_details = [result.detail for result in checks if not result.passed]
        summary = "BLOCKED: " + " | ".join(failed_details)
    return ReleaseGateReport(
        passed=passed,
        release_label=release_label,
        checks=tuple(checks),
        summary=summary,
    )


def _format_report_lines(report: ReleaseGateReport) -> Iterable[str]:
    yield f"Release label: {report.release_label}"
    yield f"Overall: {'PASS' if report.passed else 'BLOCKED'}"
    for result in report.checks:
        status = "PASS" if result.passed else "BLOCKED"
        yield f"- [{status}] {result.name}: {result.detail}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the desktop product release gate.")
    parser.add_argument(
        "--skip-commands",
        action="store_true",
        help="Skip automated command execution and only evaluate docs + smoke checklist.",
    )
    args = parser.parse_args(argv)

    automated_results = (
        [
            CheckResult(name, True, "skipped")
            for name, _command, _relative_cwd in AUTOMATED_COMMANDS
        ]
        if args.skip_commands
        else None
    )
    report = evaluate_release_gate(REPO_ROOT, automated_results=automated_results)
    for line in _format_report_lines(report):
        print(line)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
