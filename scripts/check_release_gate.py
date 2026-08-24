from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from desktop_backend.error_codes import PUBLIC_ERROR_CODE_REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_GATE_DOC = Path("docs/release-gate.md")
API_CONTRACT_DOC = Path("docs/api.md")
ARCHITECTURE_DOC = Path("docs/architecture.md")
STORAGE_DOC = Path("docs/storage.md")
OPERATIONS_DOC = Path("docs/operations.md")
EXTENDING_DOC = Path("docs/extending.md")
RELEASE_READY_LABEL = "release_candidate"
UNKNOWN_RELEASE_LABEL = "unparseable_release_status"
AUTOMATED_INPUT_TOKEN_PATTERN = re.compile(r"(?P<path>(?:tests|frontend/tests)/[^\s`]+)")

AUTOMATED_COMMANDS = (
    (
        "uv run ruff check desktop_backend peap peap_core peap_parsers peap_postprocess tests config.py scripts",
        (
            "uv",
            "run",
            "ruff",
            "check",
            "desktop_backend",
            "peap",
            "peap_core",
            "peap_parsers",
            "peap_postprocess",
            "tests",
            "config.py",
            "scripts",
        ),
        Path("."),
    ),
    (
        "uv run python -m pytest tests/test_bs4_dependency_isolation.py tests/test_environment_tooling.py tests/test_parser_registry.py tests/test_parsing_contract.py tests/test_snapshot_contracts.py tests/test_scope_validation_contract.py tests/test_record_scope.py tests/test_request_contract.py tests/test_records_service_scope_contract.py tests/test_settings_service.py tests/test_settings_backend.py tests/test_export_service_scope.py tests/test_execution_download_service.py -q",
        (
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "tests/test_bs4_dependency_isolation.py",
            "tests/test_environment_tooling.py",
            "tests/test_parser_registry.py",
            "tests/test_parsing_contract.py",
            "tests/test_snapshot_contracts.py",
            "tests/test_scope_validation_contract.py",
            "tests/test_record_scope.py",
            "tests/test_request_contract.py",
            "tests/test_records_service_scope_contract.py",
            "tests/test_settings_service.py",
            "tests/test_settings_backend.py",
            "tests/test_export_service_scope.py",
            "tests/test_execution_download_service.py",
            "-q",
        ),
        Path("."),
    ),
    (
        "uv run python -m pytest tests/test_catalog_api.py tests/test_mapping_backlog_service.py tests/test_mapping_backlog_backend.py tests/test_job_result_contract.py tests/test_job_event_contract.py tests/test_progress_contract.py tests/test_progress_resource_contract.py tests/test_overview_runtime_contract.py tests/test_overview_runtime_backend.py tests/test_jobs_actions_backend.py -q",
        (
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "tests/test_catalog_api.py",
            "tests/test_mapping_backlog_service.py",
            "tests/test_mapping_backlog_backend.py",
            "tests/test_job_result_contract.py",
            "tests/test_job_event_contract.py",
            "tests/test_progress_contract.py",
            "tests/test_progress_resource_contract.py",
            "tests/test_overview_runtime_contract.py",
            "tests/test_overview_runtime_backend.py",
            "tests/test_jobs_actions_backend.py",
            "-q",
        ),
        Path("."),
    ),
    (
        "node --test frontend/tests/appConsumerGating.test.mjs frontend/tests/mappingsPanelConsumer.test.mjs frontend/tests/mappingActionsContract.test.mjs frontend/tests/mappingApiClient.test.mjs frontend/tests/contractAdapters.test.mjs frontend/tests/catalogContract.test.mjs frontend/tests/recordScopeContract.test.mjs frontend/tests/actionRequestsContract.test.mjs frontend/tests/jobPresentation.test.mjs frontend/tests/oneClickModal.test.mjs frontend/tests/overviewPresentation.test.mjs frontend/tests/settingsState.test.mjs frontend/tests/*.mjs",
        (
            "zsh",
            "-lc",
            "node --test frontend/tests/appConsumerGating.test.mjs frontend/tests/mappingsPanelConsumer.test.mjs frontend/tests/mappingActionsContract.test.mjs frontend/tests/mappingApiClient.test.mjs frontend/tests/contractAdapters.test.mjs frontend/tests/catalogContract.test.mjs frontend/tests/recordScopeContract.test.mjs frontend/tests/actionRequestsContract.test.mjs frontend/tests/jobPresentation.test.mjs frontend/tests/oneClickModal.test.mjs frontend/tests/overviewPresentation.test.mjs frontend/tests/settingsState.test.mjs frontend/tests/*.mjs",
        ),
        Path("."),
    ),
    (
        "uv run python -m pytest tests/test_frontend_fresh_settings_one_click_smoke.py tests/test_manual_import_export_http_smoke.py -q",
        (
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "tests/test_frontend_fresh_settings_one_click_smoke.py",
            "tests/test_manual_import_export_http_smoke.py",
            "-q",
        ),
        Path("."),
    ),
    (
        "uv run python -m pytest tests/test_release_gate.py -q",
        ("uv", "run", "python", "-m", "pytest", "tests/test_release_gate.py", "-q"),
        Path("."),
    ),
    ("uv run python -m pytest -q", ("uv", "run", "python", "-m", "pytest", "-q"), Path(".")),
    ("cd frontend && npm run build", ("npm", "run", "build"), Path("frontend")),
)
AUTOMATED_INPUT_FILES = (
    Path("frontend/index.html"),
)
API_CONTRACT_REQUIRED_MARKERS = (
    "/api/overview/stream",
    "/api/jobs/download-ingest",
    "/api/jobs/archive-reprocess",
    "/api/jobs/{job_id}/retry",
    "actions.retry",
    "fail-closed",
    "/api/records/{record_id}/reprocess",
    "/api/exports/history",
    "/api/exports/history/{export_id}",
    "/api/exports/history/{export_id}/open",
    "/api/exports/history/{export_id}/download",
    "/api/mappings/{entry_id}",
    "/api/mappings/undo",
    "/api/mappings/re-evaluate-business",
    "/api/review-problems",
    "project_type_unresolved",
    "business_family_unresolved",
    "deal_data_incomplete",
    "export_fields_missing",
    "manual_review_unclassified",
    "只读",
    "不会补字段",
    "不会允许导出",
    "不得扫描 `pending_review`",
    "business_resolution_count",
    "re_evaluate_business",
    "mapping_gap_resolution",
    "mapping_conflict_resolution",
    "mapping_gap_count",
    "mapping_conflict_count",
    "audit_count",
    "sections",
    "summary",
    "entries",
    "effective_default_scope",
    "stored_preference",
    "ok",
    "data",
    "error",
    "details",
)
API_CONTRACT_FORBIDDEN_ROUTE_MARKERS = (
    "/api/mappings/import",
    "/api/mappings/export",
)

HISTORICAL_MARKERS = ("historical", "maintenance-only", "legacy", "compat", "evidence", "历史", "证据")
STABLE_DOC_REFERENCES = (API_CONTRACT_DOC.as_posix(), RELEASE_GATE_DOC.as_posix())
STALE_CONTRACT_PATTERN = re.compile(
    r"\b("
    r"project_type|default_project_type|effective_default_scope|stored_preference|"
    r"cursor-aware\s+rebuild|rebuild\s*/\s*incremental|rebuild\s+/\s+incremental"
    r")\b"
)
ENGLISH_TOMBSTONE_CLAIM_PATTERN = re.compile(r"(?i)\b(openability|openable|open|rebuildable|rebuild)\b")
CHINESE_TOMBSTONE_CLAIM_PATTERN = re.compile(r"(可打开|可重建|可重构)")
TOMBSTONE_CLAUSE_BOUNDARY_PATTERN = re.compile(r"[，,;；/|。.!?！？:：]")
BUSINESS_RE_EVALUATION_CLAUSE_BOUNDARY_PATTERN = re.compile(r"[，,;；。.!?！？]|\b(?:but|and|also)\b", re.IGNORECASE)
LEGACY_BUSINESS_RE_EVALUATION_TOKENS = (
    "business_re_evaluation",
    "re_evaluate_business",
    "business_resolution_count",
)
LIVE_BUSINESS_RE_EVALUATION_SEMANTIC_TOKENS = (
    "CTA",
    "UI",
    "frontend",
    "adapter",
    "presenter",
    "前端",
    "按钮",
    "导航",
    "活跃",
    "操作入口",
    "review 页面",
    "review page",
    "live",
    "mappings section",
    "publish",
    "publishes",
    "published",
    "return",
    "returns",
    "returned",
    "response includes",
    "includes",
    "发布",
    "返回",
    "暴露",
)
LEGACY_BUSINESS_RE_EVALUATION_NEGATIVE_MARKERS = (
    "不发布",
    "不属于",
    "不出现在",
    "不允许",
    "不能",
    "没有",
    "无",
    "does not",
    "no ",
    "只保留",
    "只作为",
    "hidden/internal",
)
ALLOWED_UNTRACKED_REVIEW_PREFIXES = (
)
OBSOLETE_TRACKED_RUNTIME_DATA_DELETE_PREFIXES = (
    r"C:\\temp\\manual_html/",
    r"C:\temp\manual_html/",
    "submission/",
)
OBSOLETE_TRACKED_PROCESS_DOC_DELETE_PREFIXES = (
    "qa-fix-plan.md",
    "PLAN.md",
    "SPEC.md",
    "todo.md",
    "requirements.txt",
    "requirements-dev.lock",
    "docs/desktop_electron_smoke_report_2026-03-28.md",
    "docs/desktop_product_delivery_report_2026-03-26.md",
    "docs/desktop_product_release_blockers_2026-03-26.md",
    "docs/frontend_redesign_real_validation_2026-04-10.md",
    "docs/frontend_redesign_test_matrix_2026-04-09.md",
    "docs/native_file_picker_smoke_2026-04-18.md",
    "docs/parser_rule_risk_report.md",
    "docs/README.md",
    "docs/explanations/",
    "docs/guides/",
    "docs/governance/",
    "docs/reference/",
    "Reference/",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReleaseGateDoc:
    automated_commands: tuple[str, ...]
    active_docs: tuple[Path, ...]
    smoke_items: tuple[tuple[str, bool], ...]
    release_label: str


@dataclass(frozen=True)
class ReleaseGateReport:
    passed: bool
    release_label: str
    checks: tuple[CheckResult, ...]
    summary: str


class ReleaseGateDocParseError(ValueError):
    def __init__(self, section: str, line: str) -> None:
        self.section = section
        self.line = line
        super().__init__(f"`## {section}` 包含不可解析的条目: {line}")


def _extract_markdown_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return match.group("body").strip()


def _is_bullet_like_line(line: str) -> bool:
    return line.startswith(("-", "*", "+"))


def _parse_backtick_list(section_text: str, *, section: str) -> tuple[str, ...]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"-\s+`(?P<item>[^`]+)`\s*$", line)
        if not match:
            if _is_bullet_like_line(line):
                raise ReleaseGateDocParseError(section, line)
            continue
        items.append(match.group("item").strip())
    return tuple(items)


def _parse_checkbox_lines(section_text: str, *, section: str) -> tuple[tuple[str, bool], ...]:
    items: list[tuple[str, bool]] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"- \[(?P<state>[ xX])\] (?P<label>.+)$", line)
        if not match:
            if _is_bullet_like_line(line):
                raise ReleaseGateDocParseError(section, line)
            continue
        items.append((match.group("label").strip(), match.group("state").lower() == "x"))
    return tuple(items)


def _parse_release_label(section_text: str) -> str:
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        match = re.match(r"- 当前标签：`(?P<label>[^`]+)`$", line)
        if not match:
            match = re.match(r"- 当前标签：(?P<label>[^`]+)$", line)
        if match:
            return match.group("label").strip()
    return UNKNOWN_RELEASE_LABEL


def parse_release_gate_doc(text: str) -> ReleaseGateDoc:
    automated_commands = _parse_backtick_list(_extract_markdown_section(text, "自动化基线"), section="自动化基线")
    active_docs = tuple(
        Path(item) for item in _parse_backtick_list(_extract_markdown_section(text, "活跃文档"), section="活跃文档")
    )
    smoke_items = _parse_checkbox_lines(_extract_markdown_section(text, "真实产品烟测"), section="真实产品烟测")
    release_label = _parse_release_label(_extract_markdown_section(text, "当前发布状态"))
    return ReleaseGateDoc(
        automated_commands=automated_commands,
        active_docs=active_docs,
        smoke_items=smoke_items,
        release_label=release_label,
    )


def load_release_gate_doc(repo_root: Path) -> ReleaseGateDoc:
    text = (repo_root / RELEASE_GATE_DOC).read_text(encoding="utf-8")
    return parse_release_gate_doc(text)


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo_root), *args),
        capture_output=True,
        text=True,
        check=False,
    )


def _read_text(repo_root: Path, relative_path: str) -> str:
    return (repo_root / relative_path).read_text(encoding="utf-8")


def _extract_python_function(text: str, function_name: str) -> str:
    pattern = re.compile(
        rf"^def\s+{re.escape(function_name)}\s*\(.*?(?=^def\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(0) if match else ""


def _extract_js_export_function(text: str, function_name: str) -> str:
    pattern = re.compile(
        rf"^export\s+function\s+{re.escape(function_name)}\s*\(.*?(?=^export\s+function\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(0) if match else ""


def _has_all_markers(text: str, markers: tuple[str, ...]) -> bool:
    return all(marker in text for marker in markers)


def _iter_automated_input_paths(repo_root: Path) -> tuple[Path, ...]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for relative_path in AUTOMATED_INPUT_FILES:
        if relative_path not in seen:
            resolved.append(relative_path)
            seen.add(relative_path)
    for command_name, _command, _cwd in AUTOMATED_COMMANDS:
        for match in AUTOMATED_INPUT_TOKEN_PATTERN.finditer(command_name):
            token = match.group("path").strip()
            if any(char in token for char in "*?[]"):
                matched_paths = sorted(path for path in repo_root.glob(token) if path.is_file())
                if not matched_paths:
                    placeholder = Path(token)
                    if placeholder not in seen:
                        resolved.append(placeholder)
                        seen.add(placeholder)
                    continue
                for matched in matched_paths:
                    relative = matched.relative_to(repo_root)
                    if relative not in seen:
                        resolved.append(relative)
                        seen.add(relative)
                continue
            relative = Path(token)
            if relative not in seen:
                resolved.append(relative)
                seen.add(relative)
    return tuple(resolved)


def _status_target_path(line: str) -> str:
    payload = line[3:].strip()
    if " -> " in payload:
        payload = payload.split(" -> ", 1)[1].strip()
    return payload


def _is_allowed_untracked_review_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_UNTRACKED_REVIEW_PREFIXES)


def _path_matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    raw_path = str(path or "").strip()
    unquoted_path = raw_path[1:-1] if raw_path.startswith('"') and raw_path.endswith('"') else raw_path
    collapsed_git_escapes = unquoted_path.replace("\\\\", "\\")
    candidates = (raw_path, unquoted_path, collapsed_git_escapes)
    return any(candidate == prefix or candidate.startswith(prefix) for candidate in candidates for prefix in prefixes)


def _is_allowed_obsolete_delete(line: str) -> bool:
    if not (line.startswith(" D ") or line.startswith("D  ")):
        return False
    target_path = _status_target_path(line)
    return _path_matches_prefix(
        target_path,
        OBSOLETE_TRACKED_RUNTIME_DATA_DELETE_PREFIXES + OBSOLETE_TRACKED_PROCESS_DOC_DELETE_PREFIXES,
    )


def _is_direct_active_doc_candidate(path: Path) -> bool:
    path_text = path.as_posix()
    if path_text == "README.md":
        return True
    return len(path.parts) == 2 and path.parts[0] == "docs" and path.suffix == ".md"


def _is_existing_policy_outside_active_docs(path: Path) -> bool:
    return _path_matches_prefix(path.as_posix(), OBSOLETE_TRACKED_PROCESS_DOC_DELETE_PREFIXES)


def _tracked_active_doc_candidates(repo_root: Path) -> tuple[Path, ...]:
    tracked_files = _run_git(repo_root, "ls-files")
    if tracked_files.returncode != 0:
        detail = (tracked_files.stderr or tracked_files.stdout or "").strip() or "git ls-files failed"
        raise RuntimeError(detail)
    return tuple(
        Path(line.strip())
        for line in tracked_files.stdout.splitlines()
        if line.strip()
        and _is_direct_active_doc_candidate(Path(line.strip()))
        and not _is_existing_policy_outside_active_docs(Path(line.strip()))
    )


def check_release_gate_doc(repo_root: Path) -> CheckResult:
    doc_path = repo_root / RELEASE_GATE_DOC
    if not doc_path.exists():
        return CheckResult("release gate doc", False, f"Missing {RELEASE_GATE_DOC.as_posix()}")

    text = doc_path.read_text(encoding="utf-8")
    try:
        parsed = parse_release_gate_doc(text)
    except ReleaseGateDocParseError as exc:
        return CheckResult("release gate doc", False, str(exc))
    expected_commands = tuple(name for name, _command, _cwd in AUTOMATED_COMMANDS)
    if parsed.automated_commands != expected_commands:
        return CheckResult(
            "release gate doc",
            False,
            f"{RELEASE_GATE_DOC.as_posix()} 的 `## 自动化基线` 与脚本 AUTOMATED_COMMANDS 不一致。",
        )

    missing_docs = [path.as_posix() for path in parsed.active_docs if not (repo_root / path).exists()]
    if missing_docs:
        return CheckResult(
            "release gate doc",
            False,
            "活跃文档缺失: " + ", ".join(missing_docs),
        )

    required_docs = {Path("README.md"), API_CONTRACT_DOC, RELEASE_GATE_DOC, ARCHITECTURE_DOC, STORAGE_DOC, OPERATIONS_DOC, EXTENDING_DOC}
    if not required_docs.issubset(set(parsed.active_docs)):
        return CheckResult(
            "release gate doc",
            False,
            (
                "活跃文档列表必须包含 README.md、"
                f"{API_CONTRACT_DOC.as_posix()}、"
                f"{RELEASE_GATE_DOC.as_posix()}、"
                f"{ARCHITECTURE_DOC.as_posix()}、"
                f"{STORAGE_DOC.as_posix()}、"
                f"{OPERATIONS_DOC.as_posix()}、"
                f"{EXTENDING_DOC.as_posix()}。"
            ),
        )

    try:
        tracked_active_docs = set(_tracked_active_doc_candidates(repo_root))
    except RuntimeError as exc:
        return CheckResult(
            "release gate doc",
            False,
            "无法验证活跃文档闭合集: " + str(exc),
        )
    unregistered_docs = sorted(path.as_posix() for path in tracked_active_docs - set(parsed.active_docs))
    if unregistered_docs:
        return CheckResult(
            "release gate doc",
            False,
            "tracked root/docs Markdown 未注册到活跃文档: " + ", ".join(unregistered_docs),
        )
    extra_active_docs = sorted(path.as_posix() for path in set(parsed.active_docs) - tracked_active_docs)
    if extra_active_docs:
        return CheckResult(
            "release gate doc",
            False,
            "活跃文档列表包含多余条目: " + ", ".join(extra_active_docs),
        )

    if not parsed.smoke_items:
        return CheckResult(
            "release gate doc",
            False,
            "`## 真实产品烟测` 缺少机器可解析的 checkbox 条目。",
        )

    release_doc_problems: list[str] = []
    freeze_section = _extract_markdown_section(text, "阶段一冻结边界")
    if "待复核" not in freeze_section or "六个主页面" not in freeze_section:
        release_doc_problems.append("阶段一冻结边界必须把待复核纳入六个主页面。")
    smoke_claims = "\n".join(label for label, _checked in parsed.smoke_items)
    if "已留截图" in smoke_claims or "已留存" in smoke_claims:
        release_doc_problems.append("真实产品烟测不得声明未由 gate 校验的不可复现证据声明。")
    if release_doc_problems:
        return CheckResult("release gate doc", False, " ".join(release_doc_problems))

    return CheckResult("release gate doc", True, "release_gate.md 已提供单一机器可读基线。")


def check_release_readiness(repo_root: Path) -> CheckResult:
    try:
        parsed = load_release_gate_doc(repo_root)
    except FileNotFoundError:
        return CheckResult("release readiness", False, f"Missing {RELEASE_GATE_DOC.as_posix()}")
    except ReleaseGateDocParseError as exc:
        return CheckResult("release readiness", False, str(exc))

    unchecked_smoke = [label for label, checked in parsed.smoke_items if not checked]
    if unchecked_smoke:
        return CheckResult(
            "release readiness",
            False,
            "真实产品烟测未完成: " + ", ".join(unchecked_smoke),
        )

    if parsed.release_label == UNKNOWN_RELEASE_LABEL:
        return CheckResult(
            "release readiness",
            False,
            f"{RELEASE_GATE_DOC.as_posix()} 的 `## 当前发布状态` 缺少机器可解析的 `- 当前标签：...` 条目。",
        )

    if parsed.release_label != RELEASE_READY_LABEL:
        return CheckResult(
            "release readiness",
            False,
            f"当前发布标签为 {parsed.release_label!r}，只有 {RELEASE_READY_LABEL!r} 才允许放行。",
        )

    return CheckResult("release readiness", True, "release label 与 smoke checklist 均已满足放行条件。")


def _line_has_allowed_context(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in HISTORICAL_MARKERS) or any(reference in line for reference in STABLE_DOC_REFERENCES)


def _claim_is_negated(text: str, match: re.Match[str]) -> bool:
    start = match.start()
    if match.group(0).isascii():
        prefix = text[max(0, start - 4):start].lower()
        return prefix.endswith("non-") or prefix.endswith("not ")
    return start > 0 and text[start - 1] == "不"


def _window_has_positive_tombstone_claim(window: str) -> bool:
    for pattern in (ENGLISH_TOMBSTONE_CLAIM_PATTERN, CHINESE_TOMBSTONE_CLAIM_PATTERN):
        for claim_match in pattern.finditer(window):
            if _claim_is_negated(window, claim_match):
                continue
            return True
    return False


def _tombstone_clause_window(line: str, tombstone_match: re.Match[str]) -> str:
    preceding_boundaries = [
        match.end()
        for match in TOMBSTONE_CLAUSE_BOUNDARY_PATTERN.finditer(line, 0, tombstone_match.start())
    ]
    following_boundary = TOMBSTONE_CLAUSE_BOUNDARY_PATTERN.search(line, tombstone_match.end())
    clause_start = preceding_boundaries[-1] if preceding_boundaries else 0
    clause_end = following_boundary.start() if following_boundary else len(line)
    return line[clause_start:clause_end]


def _line_has_tombstone_openability_drift(line: str) -> bool:
    for tombstone_match in re.finditer(r"(?i)\btombstone\b", line):
        if _window_has_positive_tombstone_claim(_tombstone_clause_window(line, tombstone_match)):
            return True
    return False


def _line_has_live_business_re_evaluation_drift(line: str) -> bool:
    lowered_line = line.lower()
    if not any(token.lower() in lowered_line for token in LEGACY_BUSINESS_RE_EVALUATION_TOKENS):
        return False
    clauses = [
        clause.strip()
        for clause in BUSINESS_RE_EVALUATION_CLAUSE_BOUNDARY_PATTERN.split(line)
        if clause.strip()
    ]
    return any(
        any(token.lower() in clause.lower() for token in LIVE_BUSINESS_RE_EVALUATION_SEMANTIC_TOKENS)
        and not any(marker.lower() in clause.lower() for marker in LEGACY_BUSINESS_RE_EVALUATION_NEGATIVE_MARKERS)
        for clause in clauses
    )


def check_active_doc_contract_drift(repo_root: Path, active_docs: Sequence[Path]) -> CheckResult:
    offenders: list[str] = []

    for relative_path in active_docs:
        file_path = repo_root / relative_path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        if relative_path == Path("README.md") and "authoritative baseline" in text.lower():
            offenders.append("README.md: claims to be an authoritative baseline instead of deferring to release-gate/api")

        if relative_path == API_CONTRACT_DOC:
            continue

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            has_business_drift = _line_has_live_business_re_evaluation_drift(raw_line)
            has_general_drift = STALE_CONTRACT_PATTERN.search(raw_line) or _line_has_tombstone_openability_drift(raw_line)
            if not (has_general_drift or has_business_drift):
                continue
            if has_general_drift and not has_business_drift and _line_has_allowed_context(raw_line):
                continue
            offenders.append(f"{relative_path.as_posix()}:{line_number}: {raw_line.strip()}")

    if offenders:
        return CheckResult(
            "active doc drift",
            False,
            "文档仍在发布当前 contract/gate 漂移: " + "; ".join(offenders),
        )

    return CheckResult(
        "active doc drift",
        True,
        f"活跃文档已清理 stale contract 残留；{API_CONTRACT_DOC.as_posix()} / {RELEASE_GATE_DOC.as_posix()} 是真相源。",
    )


def check_api_contract_sync(repo_root: Path) -> CheckResult:
    doc_path = repo_root / API_CONTRACT_DOC
    if not doc_path.exists():
        return CheckResult("api contract sync", False, f"Missing {API_CONTRACT_DOC.as_posix()}")
    text = doc_path.read_text(encoding="utf-8")

    missing_items: list[str] = []
    for marker in API_CONTRACT_REQUIRED_MARKERS:
        if marker not in text:
            missing_items.append(marker)

    for error_code in PUBLIC_ERROR_CODE_REGISTRY:
        if error_code not in text:
            missing_items.append(error_code)

    forbidden_route_lines: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if any(marker in raw_line for marker in API_CONTRACT_FORBIDDEN_ROUTE_MARKERS):
            forbidden_route_lines.append(f"{API_CONTRACT_DOC.as_posix()}:{line_number}: {raw_line.strip()}")

    forbidden_live_lines: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not _line_has_live_business_re_evaluation_drift(raw_line):
            continue
        forbidden_live_lines.append(f"{API_CONTRACT_DOC.as_posix()}:{line_number}: {raw_line.strip()}")

    if missing_items:
        return CheckResult(
            "api contract sync",
            False,
            f"{API_CONTRACT_DOC.as_posix()} 缺少当前 contract 标记: " + ", ".join(missing_items),
        )

    if forbidden_route_lines:
        return CheckResult(
            "api contract sync",
            False,
            "docs/api.md 声明了当前后端不存在的 mappings import/export 路由: "
            + "; ".join(forbidden_route_lines),
        )

    if forbidden_live_lines:
        return CheckResult(
            "api contract sync",
            False,
            "forbidden live mappings semantics: " + "; ".join(forbidden_live_lines),
        )

    return CheckResult("api contract sync", True, f"{API_CONTRACT_DOC.as_posix()} 与当前 HTTP / error-code contract 同步。")


def check_semantic_contract_residue(repo_root: Path) -> CheckResult:
    offenders: list[str] = []

    request_contract = _extract_python_function(
        _read_text(repo_root, "desktop_backend/request_contract.py"),
        "normalize_one_click_request",
    )
    if not request_contract:
        offenders.append("desktop_backend/request_contract.py: missing normalize_one_click_request")
    else:
        if "record_family, business_id, and exchange are required for one-click request" not in request_contract:
            offenders.append("desktop_backend/request_contract.py: one-click request no longer documents explicit required scope fields")
        if "effective_scope" in request_contract or 'missing actionable default scope' in request_contract:
            offenders.append("desktop_backend/request_contract.py: one-click request still carries effective_default_scope fallback residue")

    action_contract = _extract_python_function(
        _read_text(repo_root, "desktop_backend/action_contract.py"),
        "_normalize_scope",
    )
    if not action_contract:
        offenders.append("desktop_backend/action_contract.py: missing _normalize_scope")
    elif '"project_type"' in action_contract or "'project_type'" in action_contract:
        offenders.append("desktop_backend/action_contract.py: action scope still leaks legacy project_type")

    mapping_resource_contract = _extract_python_function(
        _read_text(repo_root, "desktop_backend/mapping_resource_contract.py"),
        "build_mappings_resource",
    )
    if not mapping_resource_contract:
        offenders.append("desktop_backend/mapping_resource_contract.py: missing build_mappings_resource")
    elif 'item_key="pending"' in mapping_resource_contract or "list(backlog or [])" in mapping_resource_contract:
        offenders.append("desktop_backend/mapping_resource_contract.py: mappings resource still publishes legacy pending backlog compatibility shape")

    frontend_mappings_contract = _extract_js_export_function(
        _read_text(repo_root, "frontend/src/contracts/mappings.js"),
        "normalizeMappingsResource",
    )
    if not frontend_mappings_contract:
        offenders.append("frontend/src/contracts/mappings.js: missing normalizeMappingsResource")
    elif "legacyPending" in frontend_mappings_contract or "source.pending" in frontend_mappings_contract:
        offenders.append("frontend/src/contracts/mappings.js: frontend adapter still consumes legacy pending backlog residue")

    api_contract_text = _read_text(repo_root, API_CONTRACT_DOC.as_posix())
    if not _has_all_markers(
        api_contract_text,
        ("records browse runtime", "`listing/all/all`", "总览页导出", "显式 canonical scope", "scope 缺失时自行合成"),
    ):
        offenders.append(
            f"{API_CONTRACT_DOC.as_posix()}: missing records browse runtime vs shared actionable default semantics"
        )

    technical_doc_text = _read_text(repo_root, ARCHITECTURE_DOC.as_posix())
    if not _has_all_markers(
        technical_doc_text,
        ("records browse runtime", "`listing/all/all`", "action consumer", "总览导出 helper", "显式 records scope"),
    ):
        offenders.append(
            f"{ARCHITECTURE_DOC.as_posix()}: missing explicit records browse runtime boundary"
        )

    product_guide_text = _read_text(repo_root, OPERATIONS_DOC.as_posix())
    if not _has_all_markers(
        product_guide_text,
        ("记录页仍可浏览", "records browse runtime", "总览页导出", "记录页里的"),
    ):
        offenders.append(
            f"{OPERATIONS_DOC.as_posix()}: product guide still conflates browse/runtime default semantics"
        )

    registration_doc_text = _read_text(repo_root, EXTENDING_DOC.as_posix())
    if not _has_all_markers(
        registration_doc_text,
        (
            "docs/api.md",
            "docs/release-gate.md",
            "source-backed visibility",
            "peap_core/source_catalog.py",
            "peap_core/family_catalog.py",
            "peap_core/business_catalog.py",
            "peap/business_runtime.py",
            "supported_record_families",
            "family.source_ids",
            "/api/catalog",
        ),
    ):
        offenders.append(
            f"{EXTENDING_DOC.as_posix()}: missing source-backed family/business registration boundary"
        )

    if offenders:
        return CheckResult(
            "semantic contract residue",
            False,
            "当前 worktree 仍保留已知 contract residue: " + "; ".join(offenders),
        )

    return CheckResult("semantic contract residue", True, "已知 one-click / mappings / action contract residue 已清理。")


def check_smoke_checklist(repo_root: Path) -> CheckResult:
    try:
        parsed = load_release_gate_doc(repo_root)
    except FileNotFoundError:
        return CheckResult("real product smoke", False, f"Missing {RELEASE_GATE_DOC.as_posix()}")
    except ReleaseGateDocParseError as exc:
        return CheckResult("real product smoke", False, str(exc))
    if not parsed.smoke_items:
        return CheckResult("real product smoke", False, "`## 真实产品烟测` 缺少 checkbox 条目。")
    unchecked_smoke = [label for label, checked in parsed.smoke_items if not checked]
    if unchecked_smoke:
        return CheckResult(
            "real product smoke",
            False,
            "Smoke checklist is incomplete: " + ", ".join(unchecked_smoke),
        )
    return CheckResult("real product smoke", True, "Smoke checklist is complete.")


def check_automated_inputs_tracked(repo_root: Path) -> CheckResult:
    git_root = _run_git(repo_root, "rev-parse", "--show-toplevel")
    if git_root.returncode != 0:
        return CheckResult("automated inputs tracked", False, "当前目录不是 git worktree，无法验证自动化输入是否可复现。")

    missing: list[str] = []
    untracked: list[str] = []
    for relative_path in _iter_automated_input_paths(repo_root):
        file_path = repo_root / relative_path
        if "*" in relative_path.as_posix():
            missing.append(relative_path.as_posix())
            continue
        if not file_path.exists():
            missing.append(relative_path.as_posix())
            continue
        tracked = _run_git(repo_root, "ls-files", "--error-unmatch", relative_path.as_posix())
        if tracked.returncode != 0:
            untracked.append(relative_path.as_posix())

    if missing or untracked:
        problems: list[str] = []
        if missing:
            problems.append("missing: " + ", ".join(missing))
        if untracked:
            problems.append("untracked: " + ", ".join(untracked))
        return CheckResult(
            "automated inputs tracked",
            False,
            "release gate 引用了不可复现的自动化输入，" + "; ".join(problems),
        )

    return CheckResult("automated inputs tracked", True, "所有自动化 gate 输入文件都已存在且受 git 跟踪。")


def check_worktree_hygiene(repo_root: Path) -> CheckResult:
    git_root = _run_git(repo_root, "rev-parse", "--show-toplevel")
    if git_root.returncode != 0:
        return CheckResult("worktree hygiene", False, "当前目录不是 git worktree，无法验证工作树卫生。")

    status = _run_git(repo_root, "status", "--short", "--untracked-files=all")
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip() or f"git status failed: exit={status.returncode}"
        return CheckResult("worktree hygiene", False, detail)

    dirty_lines = [line.rstrip() for line in status.stdout.splitlines() if line.strip()]
    allowed_obsolete_delete_targets = {
        _status_target_path(line)
        for line in dirty_lines
        if _is_allowed_obsolete_delete(line)
    }
    tracked_files = _run_git(repo_root, "ls-files")
    if tracked_files.returncode != 0:
        detail = (tracked_files.stderr or tracked_files.stdout or "").strip() or f"git ls-files failed: exit={tracked_files.returncode}"
        return CheckResult("worktree hygiene", False, detail)
    tracked_runtime_artifacts = [
        line.strip()
        for line in tracked_files.stdout.splitlines()
        if line.strip()
        and _path_matches_prefix(line.strip(), OBSOLETE_TRACKED_RUNTIME_DATA_DELETE_PREFIXES)
        and line.strip() not in allowed_obsolete_delete_targets
    ]
    if tracked_runtime_artifacts:
        preview = "; ".join(tracked_runtime_artifacts[:10])
        if len(tracked_runtime_artifacts) > 10:
            preview += f"; ... ({len(tracked_runtime_artifacts)} entries total)"
        return CheckResult(
            "worktree hygiene",
            False,
            "git 仍跟踪 runtime artifact，发布边界不可复现；只能删除这些历史污染: " + preview,
        )

    forbidden_untracked = [
        line for line in dirty_lines
        if line.startswith("?? ") and not _is_allowed_untracked_review_path(_status_target_path(line))
    ]
    if forbidden_untracked:
        preview = "; ".join(forbidden_untracked[:10])
        if len(forbidden_untracked) > 10:
            preview += f"; ... ({len(forbidden_untracked)} entries total)"
        return CheckResult(
            "worktree hygiene",
            False,
            "工作树存在未跟踪的可执行/评审输入文件，发布边界不可复现: " + preview,
        )

    pollution_lines = [
        line
        for line in dirty_lines
        if not _is_allowed_obsolete_delete(line)
    ]
    if pollution_lines:
        preview = "; ".join(pollution_lines[:10])
        if len(pollution_lines) > 10:
            preview += f"; ... ({len(pollution_lines)} entries total)"
        return CheckResult(
            "worktree hygiene",
            False,
            "工作树存在未提交变更，发布边界不可复现；请在干净 commit 上运行 gate: " + preview,
        )

    if dirty_lines:
        return CheckResult(
            "worktree hygiene",
            True,
            "仅存在允许的历史 runtime-data 删除；未发现其他工作树污染。",
        )

    return CheckResult("worktree hygiene", True, "工作树无非评审污染，可复现 merge gate 结果。")


def load_release_label(repo_root: Path) -> str:
    doc_path = repo_root / RELEASE_GATE_DOC
    if not doc_path.exists():
        return UNKNOWN_RELEASE_LABEL
    text = doc_path.read_text(encoding="utf-8")
    return parse_release_gate_doc(text).release_label


def _bootstrap_frontend_dependencies(repo_root: Path, relative_cwd: Path) -> CheckResult | None:
    frontend_root = repo_root / relative_cwd
    lockfile = frontend_root / "package-lock.json"
    if not lockfile.exists():
        return None

    completed = subprocess.run(
        ("npm", "ci"),
        cwd=frontend_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return None

    output = (completed.stdout or completed.stderr or "").strip()
    detail = output.splitlines()[-1] if output else f"exit={completed.returncode}"
    return CheckResult("frontend bootstrap", False, detail)


def run_automated_commands(repo_root: Path) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []
    for name, command, relative_cwd in AUTOMATED_COMMANDS:
        if relative_cwd == Path("frontend") and command[:2] == ("npm", "run"):
            bootstrap_result = _bootstrap_frontend_dependencies(repo_root, relative_cwd)
            if bootstrap_result is not None:
                results.append(CheckResult(name, False, bootstrap_result.detail))
                continue
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
    try:
        parsed = load_release_gate_doc(repo_root)
        active_docs = parsed.active_docs
        release_label = parsed.release_label
    except FileNotFoundError:
        active_docs = ()
        release_label = UNKNOWN_RELEASE_LABEL
    except ReleaseGateDocParseError:
        active_docs = ()
        release_label = UNKNOWN_RELEASE_LABEL
    checks.append(check_release_gate_doc(repo_root))
    checks.append(check_release_readiness(repo_root))
    checks.append(check_active_doc_contract_drift(repo_root, active_docs))
    checks.append(check_api_contract_sync(repo_root))
    checks.append(check_semantic_contract_residue(repo_root))
    checks.append(check_automated_inputs_tracked(repo_root))
    checks.append(check_worktree_hygiene(repo_root))
    checks.append(check_smoke_checklist(repo_root))

    passed = all(result.passed for result in checks)
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
        help="Skip automated command execution and only evaluate docs + release-gate metadata.",
    )
    args = parser.parse_args(argv)

    automated_results = (
        [
            CheckResult(
                "automated commands",
                False,
                "--skip-commands 只做文档/元数据检查，非发布门禁；必须运行完整自动化基线才能发布。",
            )
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
