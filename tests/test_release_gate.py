from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_release_gate import (
    API_CONTRACT_DOC,
    ARCHITECTURE_DOC,
    AUTOMATED_COMMANDS,
    EXTENDING_DOC,
    OPERATIONS_DOC,
    RELEASE_GATE_DOC,
    STORAGE_DOC,
    CheckResult,
    check_active_doc_contract_drift,
    check_api_contract_sync,
    check_automated_inputs_tracked,
    check_release_gate_doc,
    check_release_readiness,
    check_semantic_contract_residue,
    check_worktree_hygiene,
    evaluate_release_gate,
    main,
    parse_release_gate_doc,
    run_automated_commands,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _release_gate_doc(
    *,
    automated_commands: list[str] | None = None,
    active_docs: list[str] | None = None,
    smoke_items: list[str] | None = None,
    checked_smoke: bool = True,
    label: str = "release_candidate",
) -> str:
    commands = automated_commands if automated_commands is not None else [name for name, _command, _cwd in AUTOMATED_COMMANDS]
    docs = active_docs if active_docs is not None else [
        "README.md",
        API_CONTRACT_DOC.as_posix(),
        RELEASE_GATE_DOC.as_posix(),
        ARCHITECTURE_DOC.as_posix(),
        STORAGE_DOC.as_posix(),
        OPERATIONS_DOC.as_posix(),
        EXTENDING_DOC.as_posix(),
    ]
    smoke = smoke_items if smoke_items is not None else ["frontend smoke"]
    lines = [
        "# 发布门槛",
        "",
        "## 自动化基线",
        *[f"- `{item}`" for item in commands],
        "",
        "## 活跃文档",
        *[f"- `{item}`" for item in docs],
        "",
        "## 真实产品烟测",
        *[f"- [{'x' if checked_smoke else ' '}] {item}" for item in smoke],
        "",
        "## 阶段一冻结边界",
        "冻结的产品面：总览 / 任务 / 记录 / 待复核 / 映射 / 设置六个主页面可打开。",
        "",
        "## 当前发布状态",
        f"- 当前标签：`{label}`",
        "",
    ]
    return "\n".join(lines)


def _canonical_api_contract(extra: str = "") -> str:
    return textwrap.dedent(
        f"""\
        # API Contract

        成功 envelope：`ok`, `data`, `meta`
        错误 envelope：`ok`, `error.code`, `error.message`, `error.details`

        活跃 mappings 路由：
        - `/api/overview/stream`
        - `/api/jobs/download-ingest`
        - `/api/jobs/archive-reprocess`
        - `/api/jobs/{{job_id}}/retry`
        - `/api/records/{{record_id}}/reprocess`
        - `/api/exports/history`
        - `/api/exports/history/{{export_id}}`
        - `/api/exports/history/{{export_id}}/open`
        - `/api/exports/history/{{export_id}}/download`
        - `/api/mappings`
        - `/api/mappings/{{entry_id}}`
        - `/api/mappings/undo`
        - `/api/mappings/re-evaluate-business`
        - `/api/review-problems`

        mappings 资源使用 `sections`、`summary`、`entries`。
        当前 sections 包括 `mapping_gap_resolution`、`mapping_conflict_resolution` 与 `audit`。
        summary 暴露 `mapping_gap_count`、`mapping_conflict_count` 与 `audit_count`。
        `GET /api/mappings` 不发布 `business_resolution_count` 或 `re_evaluate_business` CTA。
        `GET /api/review-problems` 只读展示 `project_type_unresolved`、`business_family_unresolved`、`deal_data_incomplete`、`source_artifact_unavailable`、`export_fields_missing`、`manual_review_unclassified`。
        field-missing acknowledgement 不会补字段，也不会允许导出；mapping refresh 不得扫描 `pending_review`。
        job view 暴露 `actions.retry`；未知或缺失能力必须 fail-closed。

        settings/basic 暴露 `effective_default_scope`、`stored_preference`、`stale_default_metadata`。

        `records browse runtime` 可以在 shared actionable default scope 缺失时公开 `listing/all/all`。
        `POST /api/exports` 必须接收显式 canonical scope；records 页导出通常传当前 browse scope，总览页导出通常传当前 actionable default scope。
        export helper / adapter / panel 不能在 scope 缺失时自行合成。

        错误码：
        - `invalid_input`
        - `invalid_request`
        - `invalid_path_selection_kind`
        - `local_path_required`
        - `local_path_picker_failed`
        - `local_path_open_failed`
        - `not_found`
        - `record_artifact_not_found`
        - `record_artifact_open_failed`
        - `mutating_job_in_progress`
        - `browser_runtime_missing`
        - `manual_import_input_dir_not_found`
        - `unauthorized`
        - `internal_error`
        - `state_conflict`
        - `dependency_not_ready`
        - `schema_not_ready`
        - `product_error`

        {extra}
        """
    )


def _canonical_architecture() -> str:
    return textwrap.dedent(
        """\
        # 架构

        依赖默认动作范围的 action consumer 必须读取 backend-owned shared actionable default scope truth。
        `records browse runtime` 是独立 read model，可以公开 `listing/all/all`。
        one-click / 总览导出 helper 不得回退到伪造 scope。
        记录页导出只能消费显式 records scope。
        """
    )


def _canonical_operations() -> str:
    return textwrap.dedent(
        """\
        # 运维与排障

        one-click、历史区间和总览页导出依赖 shared actionable default scope。
        记录页仍可浏览，此时使用 `records browse runtime`。
        记录页里的"导出 Excel"直接消费当前 records browse scope。
        """
    )


def _canonical_extending() -> str:
    return textwrap.dedent(
        """\
        # 业务网页注册规范

        当前接口真相看 `docs/api.md`，发布门禁与活跃文档集合看 `docs/release-gate.md`。
        新 family / business / source 注册必须保持 source-backed visibility：`/api/catalog` 只暴露已经由真实 source 支撑的 family。
        注册边界必须同步 `peap_core/source_catalog.py`、`peap_core/family_catalog.py`、`peap_core/business_catalog.py` 与 `peap/business_runtime.py`。
        `supported_record_families` 与 `family.source_ids` 必须保持一致，frontend/backend 不得各自维护业务 truth table。
        """
    )


EXPECTED_AUTOMATED_COMMANDS = (
    "uv run ruff check desktop_backend peap peap_core peap_parsers peap_postprocess tests config.py scripts",
    "uv run python -m pytest tests/test_bs4_dependency_isolation.py tests/test_environment_tooling.py tests/test_parser_registry.py tests/test_parsing_contract.py tests/test_snapshot_contracts.py tests/test_scope_validation_contract.py tests/test_record_scope.py tests/test_request_contract.py tests/test_records_service_scope_contract.py tests/test_settings_service.py tests/test_settings_backend.py tests/test_export_service_scope.py tests/test_execution_download_service.py -q",
    "uv run python -m pytest tests/test_catalog_api.py tests/test_mapping_backlog_service.py tests/test_mapping_backlog_backend.py tests/test_job_result_contract.py tests/test_job_event_contract.py tests/test_progress_contract.py tests/test_progress_resource_contract.py tests/test_overview_runtime_contract.py tests/test_overview_runtime_backend.py tests/test_jobs_actions_backend.py -q",
    "node --test frontend/tests/appConsumerGating.test.mjs frontend/tests/mappingsPanelConsumer.test.mjs frontend/tests/mappingActionsContract.test.mjs frontend/tests/mappingApiClient.test.mjs frontend/tests/contractAdapters.test.mjs frontend/tests/catalogContract.test.mjs frontend/tests/recordScopeContract.test.mjs frontend/tests/actionRequestsContract.test.mjs frontend/tests/jobPresentation.test.mjs frontend/tests/oneClickModal.test.mjs frontend/tests/overviewPresentation.test.mjs frontend/tests/settingsState.test.mjs frontend/tests/*.mjs",
    "uv run python -m pytest tests/test_frontend_fresh_settings_one_click_smoke.py tests/test_manual_import_export_http_smoke.py -q",
    "uv run python -m pytest tests/test_release_gate.py -q",
    "uv run python -m pytest -q",
    "cd frontend && npm run build",
)

AUTOMATED_INPUT_PATTERNS = tuple(
    token
    for command in EXPECTED_AUTOMATED_COMMANDS
    for token in command.split()
    if token.startswith("tests/") or token.startswith("frontend/tests/")
)


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _materialize_automated_inputs(repo_root: Path) -> None:
    for token in AUTOMATED_INPUT_PATTERNS:
        if "*" in token:
            continue
        suffix = Path(token).suffix
        placeholder = "import test from 'node:test';\n" if suffix == ".mjs" else "def test_placeholder():\n    assert True\n"
        _write_text(repo_root / token, placeholder)
    _write_text(repo_root / "frontend" / "index.html", "<!doctype html><html><body><div id='app'></div></body></html>\n")


def _materialize_semantic_contract_sources(
    repo_root: Path,
    *,
    one_click_legacy_fallback: bool = False,
    action_project_type_leak: bool = False,
    backend_pending_fallback: bool = False,
    frontend_pending_fallback: bool = False,
) -> None:
    one_click_body = (
        """\
def normalize_one_click_request(payload, *, basic_settings, advanced_settings):
    request = dict(payload or {})
    if not request.get("record_family") or not request.get("business_id") or not request.get("exchange"):
        raise ValueError("missing actionable default scope for one-click request")
    effective_scope = dict((basic_settings or {}).get("effective_default_scope") or {})
    return {"record_family": request.get("record_family") or effective_scope.get("record_family")}
"""
        if one_click_legacy_fallback
        else
        """\
def normalize_one_click_request(payload, *, basic_settings, advanced_settings):
    request = dict(payload or {})
    if not request.get("record_family") or not request.get("business_id") or not request.get("exchange"):
        raise ValueError("record_family, business_id, and exchange are required for one-click request")
    return {
        "record_family": request["record_family"],
        "business_id": request["business_id"],
        "exchange": request["exchange"],
    }
"""
    )
    _write_text(repo_root / "desktop_backend" / "request_contract.py", one_click_body)

    action_body = (
        """\
def _normalize_scope(scope):
    return {"record_family": scope.get("record_family", ""), "project_type": scope.get("project_type", "")}
"""
        if action_project_type_leak
        else
        """\
def _normalize_scope(scope):
    return {"record_family": scope.get("record_family", ""), "business_id": scope.get("business_id", "")}
"""
    )
    _write_text(repo_root / "desktop_backend" / "action_contract.py", action_body)

    mapping_resource_body = (
        """\
def build_mappings_resource(*, entries, backlog):
    payload = {}
    pending_views = list(backlog or [])
    payload["pending"] = pending_views
    payload["item_key"] = "pending"
    payload["entries"] = list(entries or [])
    return payload
"""
        if backend_pending_fallback
        else
        """\
from collections.abc import Mapping


def build_mappings_resource(*, entries, backlog):
    if not isinstance(backlog, Mapping):
        raise TypeError("mappings backlog must be a canonical mapping resource with sections")
    payload = dict(backlog or {})
    payload["entries"] = list(entries or [])
    return payload
"""
    )
    _write_text(repo_root / "desktop_backend" / "mapping_resource_contract.py", mapping_resource_body)

    frontend_mappings_body = (
        """\
export function normalizeMappingsResource(resource = {}) {
  const source = resource && typeof resource === "object" ? resource : {};
  const legacyPending = Array.isArray(source.pending) ? source.pending : [];
  return {
    entries: Array.isArray(source.entries) ? source.entries : [],
    sections: legacyPending.length ? [{ section_id: "mapping_resolution", items: legacyPending }] : [],
    summary: source.summary || {},
    returned_count: 0,
    total_count: 0,
    truncated: false,
  };
}
"""
        if frontend_pending_fallback
        else
        """\
export function normalizeMappingsResource(resource = {}) {
  const source = resource && typeof resource === "object" ? resource : {};
  return {
    entries: Array.isArray(source.entries) ? source.entries : [],
    sections: Array.isArray(source.sections) ? source.sections : [],
    summary: source.summary || {},
    returned_count: 0,
    total_count: 0,
    truncated: false,
  };
}
"""
    )
    _write_text(repo_root / "frontend" / "src" / "contracts" / "mappings.js", frontend_mappings_body)


def _init_git_repo(repo_root: Path) -> None:
    init = _git(repo_root, "init", "-q")
    if init.returncode != 0:
        raise RuntimeError(init.stderr.strip() or "git init failed")
    _git(repo_root, "config", "user.email", "codex@example.com")
    _git(repo_root, "config", "user.name", "Codex")
    add = _git(repo_root, "add", ".")
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add failed")
    commit = _git(repo_root, "commit", "-qm", "test baseline")
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or "git commit failed")


class ReleaseGateTest(unittest.TestCase):
    def _make_repo(
        self,
        *,
        release_gate: str,
        readme: str = "README 指向 docs/api.md 与 docs/release-gate.md。\n",
        api_contract: str | None = None,
        extra_docs: dict[str, str] | None = None,
    ) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo_root = Path(temp_dir.name)

        _write_text(repo_root / "README.md", readme)
        _write_text(repo_root / API_CONTRACT_DOC, api_contract or _canonical_api_contract())
        _write_text(repo_root / RELEASE_GATE_DOC, release_gate)
        _write_text(repo_root / ARCHITECTURE_DOC, _canonical_architecture())
        _write_text(repo_root / STORAGE_DOC, "# 存储\n当前 SQLite 主表与工作区布局参考代码。\n")
        _write_text(repo_root / OPERATIONS_DOC, _canonical_operations())
        _write_text(repo_root / EXTENDING_DOC, _canonical_extending())
        _materialize_automated_inputs(repo_root)
        _materialize_semantic_contract_sources(repo_root)
        if extra_docs:
            for relative_path, content in extra_docs.items():
                _write_text(repo_root / relative_path, content)
        _init_git_repo(repo_root)
        return repo_root

    def test_parse_release_gate_doc_extracts_machine_readable_sections(self) -> None:
        parsed = parse_release_gate_doc(
            _release_gate_doc(
                automated_commands=["uv run pytest -q", "cd frontend && npm run build"],
                active_docs=["README.md", API_CONTRACT_DOC.as_posix(), RELEASE_GATE_DOC.as_posix()],
                smoke_items=["frontend smoke", "backend smoke"],
                label="validation_in_progress",
            )
        )

        self.assertEqual(
            [path.as_posix() for path in parsed.active_docs],
            ["README.md", API_CONTRACT_DOC.as_posix(), RELEASE_GATE_DOC.as_posix()],
        )
        self.assertEqual(list(parsed.automated_commands), ["uv run pytest -q", "cd frontend && npm run build"])
        self.assertEqual(parsed.release_label, "validation_in_progress")
        self.assertEqual(list(parsed.smoke_items), [("frontend smoke", True), ("backend smoke", True)])

    def test_automated_commands_match_task5_blocker_regression_matrix(self) -> None:
        self.assertEqual(
            tuple(name for name, _command, _cwd in AUTOMATED_COMMANDS),
            EXPECTED_AUTOMATED_COMMANDS,
        )

    def test_release_gate_doc_blocks_when_automated_baseline_drifts_from_script(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(
                automated_commands=["uv run pytest -q", "cd frontend && npm run build"],
            ),
        )

        result = check_release_gate_doc(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("自动化基线", result.detail)

    def test_release_gate_doc_blocks_malformed_automated_baseline_bullet(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc().replace(
                f"- `{AUTOMATED_COMMANDS[0][0]}`",
                "- uv run python -m pytest -q",
            ),
        )

        result = check_release_gate_doc(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("自动化基线", result.detail)
        self.assertIn("- uv run python -m pytest -q", result.detail)

    def test_evaluate_release_gate_blocks_malformed_smoke_checkbox(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc().replace(
                "- [x] frontend smoke",
                "- [done] smoke",
            ),
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult(name, True, "ok")
                for name, _command, _cwd in AUTOMATED_COMMANDS
            ],
        )

        self.assertFalse(report.passed)
        self.assertIn("真实产品烟测", report.summary)
        self.assertIn("- [done] smoke", report.summary)

    def test_release_gate_doc_requires_review_page_in_frozen_surface_and_no_untracked_evidence_claims(self) -> None:
        doc = _release_gate_doc(
            smoke_items=["总览 / 任务 / 记录 / 映射 / 设置 五个主页面真实打开并已留截图"],
        )
        doc = doc.replace(
            "冻结的产品面：总览 / 任务 / 记录 / 待复核 / 映射 / 设置六个主页面可打开。",
            "冻结的产品面：五个主页面可打开。",
        )
        repo_root = self._make_repo(release_gate=doc)

        result = check_release_gate_doc(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("待复核", result.detail)
        self.assertIn("不可复现证据声明", result.detail)

    def test_release_gate_doc_blocks_tracked_root_docs_markdown_omitted_from_active_docs(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                "docs/current_surface.md": "# Current Surface\nThis tracked doc is not registered.\n",
            },
        )

        result = check_release_gate_doc(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("未注册", result.detail)
        self.assertIn("docs/current_surface.md", result.detail)

    def test_release_gate_doc_blocks_untracked_extra_active_doc_entry(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(
                active_docs=[
                    "README.md",
                    API_CONTRACT_DOC.as_posix(),
                    RELEASE_GATE_DOC.as_posix(),
                    ARCHITECTURE_DOC.as_posix(),
                    STORAGE_DOC.as_posix(),
                    OPERATIONS_DOC.as_posix(),
                    EXTENDING_DOC.as_posix(),
                    "docs/obsolete_extra.md",
                ],
            ),
        )
        _write_text(repo_root / "docs" / "obsolete_extra.md", "# Obsolete Extra\n")

        result = check_release_gate_doc(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("多余", result.detail)
        self.assertIn("docs/obsolete_extra.md", result.detail)

    def test_run_automated_commands_bootstraps_frontend_build_from_lockfile(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "frontend" / "package-lock.json", '{"name":"peap-frontend","lockfileVersion":3}\n')

        calls: list[tuple[tuple[str, ...], Path]] = []

        def fake_run(command, *, cwd, capture_output, text, check):
            calls.append((tuple(command), Path(cwd)))
            return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

        with patch("scripts.check_release_gate.subprocess.run", side_effect=fake_run):
            results = run_automated_commands(repo_root)

        build_index = next(
            index for index, result in enumerate(results) if result.name == "cd frontend && npm run build"
        )
        bootstrap_index = calls.index((("npm", "ci"), repo_root / "frontend"))
        build_call_index = calls.index((("npm", "run", "build"), repo_root / "frontend"))
        self.assertLess(bootstrap_index, build_call_index)
        self.assertEqual(results[build_index].detail, "ok")

    def test_release_readiness_blocks_unchecked_smoke_items(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(
                checked_smoke=False,
            ),
        )

        result = check_release_readiness(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("真实产品烟测", result.detail)

    def test_release_readiness_blocks_non_release_candidate_label(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(
                label="validation_in_progress",
            ),
        )

        result = check_release_readiness(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("validation_in_progress", result.detail)

    def test_release_readiness_blocks_missing_release_status_section(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc().replace(
                "\n## 当前发布状态\n- 当前标签：`release_candidate`\n",
                "\n",
            ),
        )

        result = check_release_readiness(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("当前发布状态", result.detail)

    def test_evaluate_release_gate_blocks_unparseable_release_status_label(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc().replace(
                "- 当前标签：`release_candidate`",
                "- release status: `release_candidate`",
            ),
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult(name, True, "ok")
                for name, _command, _cwd in AUTOMATED_COMMANDS
            ],
        )

        self.assertFalse(report.passed)
        self.assertIn("当前发布状态", report.summary)

    def test_evaluate_release_gate_blocks_missing_release_gate_doc_with_structured_report(self) -> None:
        repo_root = self._make_repo(release_gate=_release_gate_doc())
        (repo_root / RELEASE_GATE_DOC).unlink()

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult(name, True, "ok")
                for name, _command, _cwd in AUTOMATED_COMMANDS
            ],
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.release_label, "unparseable_release_status")
        self.assertIn("BLOCKED", report.summary)
        self.assertIn(RELEASE_GATE_DOC.as_posix(), report.summary)
        self.assertTrue(
            any(
                result.name == "release gate doc"
                and not result.passed
                and RELEASE_GATE_DOC.as_posix() in result.detail
                for result in report.checks
            )
        )

    def test_worktree_hygiene_blocks_dirty_entries(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "dirty.txt", "dirty\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("dirty.txt", result.detail)

    def test_worktree_hygiene_blocks_tracked_review_input_changes(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "README.md", "README changed during review.\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("README.md", result.detail)

    def test_worktree_hygiene_blocks_untracked_evidence_snapshot_docs(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "docs" / "native_file_picker_smoke_2026-04-18.md", "evidence\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("native_file_picker_smoke_2026-04-18.md", result.detail)

    def test_worktree_hygiene_blocks_arbitrary_untracked_process_docs(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "docs" / "temporary_review_notes.md", "temporary review notes\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("temporary_review_notes.md", result.detail)

    def test_worktree_hygiene_allows_only_deleting_obsolete_tracked_runtime_data(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        obsolete_asset = repo_root / r"C:\temp\manual_html" / "legacy.html"
        _write_text(obsolete_asset, "<html>legacy runtime capture</html>\n")
        add = _git(repo_root, "add", obsolete_asset.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track obsolete runtime data")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        obsolete_asset.unlink()

        result = check_worktree_hygiene(repo_root)

        self.assertTrue(result.passed)

    def test_worktree_hygiene_allows_deleting_retired_qa_fix_plan(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        retired_doc = repo_root / "qa-fix-plan.md"
        _write_text(retired_doc, "legacy process snapshot\n")
        add = _git(repo_root, "add", retired_doc.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track retired qa fix plan")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        retired_doc.unlink()

        result = check_worktree_hygiene(repo_root)

        self.assertTrue(result.passed)

    def test_worktree_hygiene_allows_only_deleting_obsolete_submission_runtime_data(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        tracked_submission = repo_root / "submission" / "archive.html"
        _write_text(tracked_submission, "<html>tracked submission archive</html>\n")
        add = _git(repo_root, "add", tracked_submission.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track obsolete submission artifact")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        tracked_submission.unlink()

        result = check_worktree_hygiene(repo_root)

        self.assertTrue(result.passed)

    def test_worktree_hygiene_blocks_clean_tracked_submission_runtime_data(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        tracked_submission = repo_root / "submission" / "archive.html"
        _write_text(tracked_submission, "<html>tracked submission archive</html>\n")
        add = _git(repo_root, "add", tracked_submission.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track obsolete submission artifact")
        self.assertEqual(commit.returncode, 0, commit.stderr)

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("submission/archive.html", result.detail)

    def test_worktree_hygiene_allows_only_deleting_retired_root_process_docs(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        retired_doc = repo_root / "PLAN.md"
        _write_text(retired_doc, "legacy plan\n")
        add = _git(repo_root, "add", retired_doc.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track retired process doc")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        retired_doc.unlink()

        result = check_worktree_hygiene(repo_root)

        self.assertTrue(result.passed)

    def test_worktree_hygiene_blocks_modifying_obsolete_runtime_data(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        obsolete_asset = repo_root / r"C:\temp\manual_html" / "legacy.html"
        _write_text(obsolete_asset, "<html>legacy runtime capture</html>\n")
        add = _git(repo_root, "add", obsolete_asset.relative_to(repo_root).as_posix())
        self.assertEqual(add.returncode, 0, add.stderr)
        commit = _git(repo_root, "commit", "-qm", "track obsolete runtime data")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        _write_text(obsolete_asset, "<html>changed runtime capture</html>\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("manual_html", result.detail)

    def test_worktree_hygiene_blocks_untracked_runtime_code_even_under_review_prefixes(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _write_text(repo_root / "desktop_backend" / "runtime_scope.py", "VALUE = 1\n")

        result = check_worktree_hygiene(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("desktop_backend/runtime_scope.py", result.detail)

    def test_automated_inputs_must_be_git_tracked(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        remove = _git(repo_root, "rm", "--cached", "-q", "tests/test_bs4_dependency_isolation.py")
        self.assertEqual(remove.returncode, 0, remove.stderr)
        commit = _git(repo_root, "commit", "-qm", "drop tracked test input")
        self.assertEqual(commit.returncode, 0, commit.stderr)

        result = check_automated_inputs_tracked(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("tests/test_bs4_dependency_isolation.py", result.detail)

    def test_frontend_build_entry_must_be_git_tracked(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        remove = _git(repo_root, "rm", "--cached", "-q", "frontend/index.html")
        self.assertEqual(remove.returncode, 0, remove.stderr)
        commit = _git(repo_root, "commit", "-qm", "drop frontend build entry")
        self.assertEqual(commit.returncode, 0, commit.stderr)

        result = check_automated_inputs_tracked(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("frontend/index.html", result.detail)

    def test_active_docs_block_live_project_type_contract_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            readme="README 声称 project_type 是当前 records 请求字段。\n",
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                Path("README.md"),
                API_CONTRACT_DOC,
                RELEASE_GATE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("project_type", result.detail)

    def test_active_docs_block_export_rebuild_contract_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "streaming_export owns cursor-aware rebuild / incremental export。\n"
                ),
                STORAGE_DOC.as_posix(): (
                    "# 存储\n"
                    "exports, export_cursor_records are export runs + rebuild/incremental cursor。\n"
                ),
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("cursor-aware rebuild", result.detail)
        self.assertIn("rebuild/incremental", result.detail)

    def test_active_docs_block_tombstone_openability_contract_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): "# 架构\n`tombstone 可打开 workbook 输出` 仍写在当前职责里。\n",
                STORAGE_DOC.as_posix(): "# 存储\nexports 仍记录 tombstone openability 状态。\n",
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn(ARCHITECTURE_DOC.as_posix(), result.detail)
        self.assertIn(STORAGE_DOC.as_posix(), result.detail)
        self.assertIn("可打开", result.detail)

    def test_active_docs_allow_negative_tombstone_contract_and_block_positive_claims(self) -> None:
        allowed_repo = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "retention tombstone 不可打开不可重建。\n"
                    "retention tombstone 明确不可打开且不可重建。\n"
                    "Tombstone rows are non-openable and non-rebuildable.\n"
                ),
                STORAGE_DOC.as_posix(): "# 存储\n当前 contract 仅允许 tombstone 负向约束表述。\n",
            },
        )

        allowed_result = check_active_doc_contract_drift(
            allowed_repo,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertTrue(allowed_result.passed, allowed_result.detail)

        blocked_repo = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "tombstone 可打开 workbook 输出。\n"
                    "tombstone 可重建。\n"
                ),
                STORAGE_DOC.as_posix(): (
                    "# 存储\n"
                    "exports record tombstone openability state.\n"
                    "tombstone openable/rebuildable.\n"
                ),
            },
        )

        blocked_result = check_active_doc_contract_drift(
            blocked_repo,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertFalse(blocked_result.passed)
        self.assertIn("可打开", blocked_result.detail)
        self.assertIn("openability", blocked_result.detail)

    def test_active_docs_still_block_mixed_tombstone_claims_when_positive_term_remains(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): "# 架构\nretention tombstone 不可打开但可重建。\n",
                STORAGE_DOC.as_posix(): "# 存储\nTombstone rows are non-openable but rebuildable.\n",
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("可重建", result.detail)
        self.assertIn("rebuildable", result.detail)

    def test_active_docs_block_claims_before_tombstone_and_allow_local_negation(self) -> None:
        allowed_repo = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): "# 架构\nnon-openable tombstone.\n",
                STORAGE_DOC.as_posix(): "# 存储\nTombstone rows remain non-openable and non-rebuildable.\n",
            },
        )

        allowed_result = check_active_doc_contract_drift(
            allowed_repo,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertTrue(allowed_result.passed, allowed_result.detail)

        blocked_repo = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "rebuildable tombstone.\n"
                    "non-openable but rebuildable tombstone.\n"
                ),
                STORAGE_DOC.as_posix(): "# 存储\nopenable tombstone.\n",
            },
        )

        blocked_result = check_active_doc_contract_drift(
            blocked_repo,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertFalse(blocked_result.passed)
        self.assertIn("rebuildable tombstone", blocked_result.detail)
        self.assertIn("openable tombstone", blocked_result.detail)

    def test_active_docs_allow_artifact_openability_when_tombstone_clause_is_negative(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "streaming_export 负责保留 artifact 可打开下载，retention tombstone 明确不可打开且不可重建。\n"
                ),
                STORAGE_DOC.as_posix(): (
                    "# 存储\n"
                    "exports 记录保留 artifact 可打开 / retention tombstone 不可打开不可重建的状态。\n"
                ),
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
                STORAGE_DOC,
            ],
        )

        self.assertTrue(result.passed, result.detail)

    def test_active_docs_block_legacy_business_re_evaluation_as_live_cta_contract(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "legacy `business_re_evaluation` 是 live mappings UI CTA on review 页面。\n"
                ),
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("business_re_evaluation", result.detail)
        self.assertIn("CTA", result.detail)

    def test_active_docs_allow_explicitly_negative_legacy_business_re_evaluation_context(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "`business_re_evaluation` 只保留 hidden/internal legacy compatibility 的 distinct job metrics / copy，"
                    "不属于活跃 mappings UI、review 页面或 CTA 边界。\n"
                ),
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
            ],
        )

        self.assertTrue(result.passed, result.detail)

    def test_active_docs_block_mixed_negative_and_live_legacy_business_re_evaluation_claim(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                ARCHITECTURE_DOC.as_posix(): (
                    "# 架构\n"
                    "hidden/internal legacy `business_re_evaluation` compatibility；"
                    "also live mappings UI CTA on review 页面。\n"
                ),
            },
        )

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                ARCHITECTURE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("business_re_evaluation", result.detail)
        self.assertIn("live mappings UI CTA", result.detail)

    def test_api_contract_check_blocks_when_mappings_route_or_schema_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract="# API Contract\n错误码：`invalid_input`\n",
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("/api/mappings/re-evaluate-business", result.detail)

    def test_api_contract_check_blocks_when_live_frontend_routes_are_omitted(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract().replace("- `/api/overview/stream`\n", ""),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("/api/overview/stream", result.detail)

    def test_api_contract_check_blocks_when_current_action_routes_are_omitted(self) -> None:
        api_contract = _canonical_api_contract()
        for marker in (
            "- `/api/jobs/{job_id}/retry`\n",
            "- `/api/exports/history`\n",
            "- `/api/exports/history/{export_id}`\n",
            "- `/api/exports/history/{export_id}/open`\n",
            "- `/api/exports/history/{export_id}/download`\n",
            "- `/api/mappings/undo`\n",
        ):
            api_contract = api_contract.replace(marker, "")
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=api_contract,
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("/api/exports/history", result.detail)
        self.assertIn("/api/jobs/{job_id}/retry", result.detail)
        self.assertIn("/api/mappings/undo", result.detail)

    def test_api_contract_check_blocks_removed_mapping_import_export_routes(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra=(
                    "| `POST` | `/api/mappings/import` | import mapping rules |\n"
                    "| `POST` | `/api/mappings/export` | export mapping rules |\n"
                )
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("/api/mappings/import", result.detail)
        self.assertIn("/api/mappings/export", result.detail)

    def test_api_contract_check_blocks_stale_mapping_section_names(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract()
            .replace("`mapping_gap_resolution`", "`mapping_resolution`")
            .replace("`mapping_conflict_resolution`", "`mapping_resolution`")
            .replace("`mapping_gap_count`", "`mapping_resolution_count`")
            .replace("`mapping_conflict_count`", "`mapping_resolution_count`"),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("mapping_gap_resolution", result.detail)
        self.assertIn("mapping_conflict_count", result.detail)

    def test_api_contract_check_blocks_forbidden_mappings_fields_as_live_contract(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra=(
                    "`GET /api/mappings` publishes live `business_resolution_count` and `re_evaluate_business` CTA.\n"
                    "`business_re_evaluation` is a live mappings UI CTA.\n"
                )
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("forbidden live mappings semantics", result.detail)

    def test_api_contract_check_blocks_forbidden_mappings_fields_without_cta_wording(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra="`GET /api/mappings` publishes `business_resolution_count` and returns `re_evaluate_business`.\n"
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("business_resolution_count", result.detail)
        self.assertIn("re_evaluate_business", result.detail)

    def test_api_contract_check_blocks_forbidden_mappings_fields_with_publish_return_variants(self) -> None:
        for extra in (
            "`GET /api/mappings` publish `business_resolution_count` and return `re_evaluate_business`.\n",
            "`GET /api/mappings` published `business_resolution_count` and returned `re_evaluate_business`.\n",
            "`GET /api/mappings` response includes `business_resolution_count` and `re_evaluate_business`.\n",
        ):
            with self.subTest(extra=extra):
                repo_root = self._make_repo(
                    release_gate=_release_gate_doc(),
                    api_contract=_canonical_api_contract(extra=extra),
                )

                result = check_api_contract_sync(repo_root)

                self.assertFalse(result.passed)
                self.assertIn("business_resolution_count", result.detail)

    def test_api_contract_check_blocks_business_re_evaluation_as_live_contract(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra="`business_re_evaluation` is a live mappings UI CTA.\n"
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("business_re_evaluation", result.detail)

    def test_api_contract_check_blocks_mixed_negative_and_live_business_re_evaluation_claim(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra=(
                    "hidden/internal legacy `business_re_evaluation` compatibility; "
                    "also live mappings UI CTA on review 页面.\n"
                )
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("business_re_evaluation", result.detail)

    def test_api_contract_check_blocks_same_clause_negative_then_live_return_claim(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra="`GET /api/mappings` does not publish `business_resolution_count` but returns `re_evaluate_business`.\n"
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("re_evaluate_business", result.detail)

    def test_api_contract_check_blocks_lowercase_live_ui_cta_review_page_claim(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            api_contract=_canonical_api_contract(
                extra="`business_re_evaluation` is a live mappings ui cta on review page.\n"
            ),
        )

        result = check_api_contract_sync(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("business_re_evaluation", result.detail)

    def test_active_docs_scan_release_gate_for_legacy_business_re_evaluation_contamination(self) -> None:
        release_gate = (
            _release_gate_doc()
            + "\nhidden/internal legacy `business_re_evaluation` compatibility; also live mappings UI CTA on review 页面.\n"
        )
        repo_root = self._make_repo(release_gate=release_gate)

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                RELEASE_GATE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn(RELEASE_GATE_DOC.as_posix(), result.detail)
        self.assertIn("business_re_evaluation", result.detail)

    def test_active_docs_scan_release_gate_for_general_contract_drift(self) -> None:
        release_gate = _release_gate_doc() + "\n当前 gate 仍声明 `project_type` 是 records routing truth。\n"
        repo_root = self._make_repo(release_gate=release_gate)

        result = check_active_doc_contract_drift(
            repo_root,
            active_docs=[
                RELEASE_GATE_DOC,
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn(RELEASE_GATE_DOC.as_posix(), result.detail)
        self.assertIn("project_type", result.detail)

    def test_semantic_contract_residue_blocks_known_one_click_pending_and_project_type_regressions(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )
        _materialize_semantic_contract_sources(
            repo_root,
            one_click_legacy_fallback=True,
            action_project_type_leak=True,
            backend_pending_fallback=True,
            frontend_pending_fallback=True,
        )

        result = check_semantic_contract_residue(repo_root)

        self.assertFalse(result.passed)
        self.assertIn("effective_default_scope fallback residue", result.detail)
        self.assertIn("project_type", result.detail)
        self.assertIn("legacy pending backlog", result.detail)

    def test_semantic_contract_residue_blocks_active_doc_scope_semantic_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                API_CONTRACT_DOC.as_posix(): "# API Contract\nexport 共享 scope。\n",
                ARCHITECTURE_DOC.as_posix(): "# 架构\n不能重新引入 `listing/all`。\n",
                OPERATIONS_DOC.as_posix(): "# 运维\n默认范围 stale / unsupported 需要先重选。\n",
            },
        )

        result = check_semantic_contract_residue(repo_root)

        self.assertFalse(result.passed)
        self.assertIn(API_CONTRACT_DOC.as_posix(), result.detail)
        self.assertIn(ARCHITECTURE_DOC.as_posix(), result.detail)
        self.assertIn(OPERATIONS_DOC.as_posix(), result.detail)

    def test_semantic_contract_residue_blocks_registration_guide_drift(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
            extra_docs={
                EXTENDING_DOC.as_posix(): (
                    "# 业务网页注册规范\n\n"
                    "新增业务时只需补前端选项和 parser。\n"
                ),
            },
        )

        result = check_semantic_contract_residue(repo_root)

        self.assertFalse(result.passed)
        self.assertIn(EXTENDING_DOC.as_posix(), result.detail)

    def test_release_gate_passes_when_docs_commands_and_smoke_align(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )

        report = evaluate_release_gate(
            repo_root,
            automated_results=[
                CheckResult(name, True, "ok")
                for name, _command, _cwd in AUTOMATED_COMMANDS
            ],
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.summary, "PASS")

    def test_main_skip_commands_reports_non_release_gate_instead_of_pass(self) -> None:
        repo_root = self._make_repo(
            release_gate=_release_gate_doc(),
        )

        output = io.StringIO()
        with patch("scripts.check_release_gate.REPO_ROOT", repo_root), contextlib.redirect_stdout(output):
            exit_code = main(["--skip-commands"])

        rendered = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Overall: BLOCKED", rendered)
        self.assertIn("非发布门禁", rendered)
        self.assertNotIn("Overall: PASS", rendered)


if __name__ == "__main__":
    unittest.main()
