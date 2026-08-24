"""Real-browser UI interaction audit against a temporary PEAP workspace.

The audit starts the desktop backend and Vite frontend with every PEAP path
pointing at a temporary root, seeds a fixture SQLite database, then drives the
browser through the interactive desktop surfaces. It is deliberately a script,
not product code: its job is to prove the UI wiring and downstream projections
use the temporary truth sources without touching the real PEAP workspace.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.browser_runtime import launch_chromium_browser_sync
from peap.migrations import MigrationRunner
from peap.streaming_models import IngestedRecord, PostProcessFinding
from scripts._paths import is_forbidden_real_peap_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _under_real_peap(path_value: object) -> bool:
    return is_forbidden_real_peap_path(str(path_value or ""))


def _env_for_root(root: Path) -> dict[str, str]:
    return {
        "PEAP_APP_HOME": str(root / "app-home"),
        "PEAP_DATA_ROOT": str(root / "data"),
        "PEAP_ARCHIVE_ROOT": str(root / "archive"),
        "PEAP_EXPORT_ROOT": str(root / "exports"),
        "PEAP_CACHE_DIR": str(root / "cache"),
        "PEAP_STREAMING_DB_PATH": str(root / "data" / "streaming_ingest.sqlite3"),
        "PEAP_FAKE_LOCAL_PATH_INTERACTIONS": "1",
        "PEAP_FAKE_SELECTED_PATH": str(root / "picked"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _assert_temp_paths(root: Path, env: dict[str, str]) -> None:
    temp_root = root.resolve()
    for key, value in env.items():
        if not key.startswith("PEAP_") or key == "PEAP_FAKE_LOCAL_PATH_INTERACTIONS":
            continue
        resolved = Path(value).expanduser().resolve()
        if _under_real_peap(resolved):
            raise RuntimeError(f"{key} resolved to real PEAP workspace: {resolved}")
        if os.path.commonpath([str(temp_root), str(resolved)]) != str(temp_root):
            raise RuntimeError(f"{key} escaped temp root: {resolved}")


def _write_file(path: Path, text: str = "safe local fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _canonical_record(*, family: str = "listing", business_id: str = "physical_asset") -> dict[str, object]:
    return {
        "record_family": family,
        "business_identity": {
            "record_family": family,
            "business_id": business_id,
            "raw_business_label": "实物资产",
        },
        "canonical_fields": {},
    }


def _source_identity(*, family: str = "listing", business_id: str = "physical_asset", exchange: str = "sse", project_code: str) -> dict[str, str]:
    return {
        "record_family": family,
        "business_id": business_id,
        "source_id": exchange,
        "exchange": exchange,
        "project_code": project_code,
    }


def _record(
    *,
    record_id: str,
    state: str,
    archive_path: Path | str,
    project_code: str | None = None,
    project_name: str | None = None,
    project_type: str = "实物资产",
    source_identity: dict[str, str] | None = None,
    canonical_record: dict[str, object] | None = None,
    findings: list[PostProcessFinding] | None = None,
    postprocess_payload: dict[str, object] | None = None,
) -> IngestedRecord:
    resolved_project_code = project_code or f"CODE-{record_id}"
    resolved_project_name = project_name or f"项目-{record_id}"
    archive_text = str(archive_path)
    return IngestedRecord(
        record_id=record_id,
        revision_hash=f"hash-{record_id}",
        project_code=resolved_project_code,
        project_name=resolved_project_name,
        project_type=project_type,
        exchange="sse",
        listing_date="2026-05-01",
        state=state,
        source_file=archive_text,
        archive_path=archive_text,
        parser_payload={"项目编号": resolved_project_code, "项目名称": resolved_project_name},
        postprocess_payload=postprocess_payload
        or {"项目编号": resolved_project_code, "项目名称": resolved_project_name, "项目类型": project_type},
        findings=findings or [],
        record_family="listing",
        source_identity=source_identity or {},
        canonical_record=canonical_record or {},
    )


def _seed_fixture(root: Path, env: dict[str, str]) -> dict[str, str]:
    _assert_temp_paths(root, env)
    for value in env.values():
        if isinstance(value, str) and value.startswith(str(root)):
            Path(value).parent.mkdir(parents=True, exist_ok=True)
    Path(env["PEAP_FAKE_SELECTED_PATH"]).mkdir(parents=True, exist_ok=True)
    manual_root = Path(env["PEAP_APP_HOME"]) / "manual"
    manual_root.mkdir(parents=True, exist_ok=True)
    _write_file(manual_root / "manual-fixture.html", "safe manual fixture")

    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        config = AppConfig.from_env(project_root=str(REPO_ROOT))
        MigrationRunner.run(config.STREAMING_DB_PATH)
        service = AppService(config_obj=config)
        archive_root = Path(env["PEAP_ARCHIVE_ROOT"])
        export_root = Path(env["PEAP_EXPORT_ROOT"])

        verified = _write_file(archive_root / "verified.html")
        field_missing = _write_file(archive_root / "field-missing.html")
        field_missing_action = _write_file(archive_root / "field-missing-action.html")
        review = _write_file(archive_root / "review.html")
        mapping = _write_file(archive_root / "mapping.html")
        conflict = _write_file(archive_root / "conflict.html")
        export_live = _write_file(export_root / "retained-live.xlsx", "xlsx")
        export_pruned = _write_file(export_root / "retained-pruned.xlsx", "xlsx")

        ready_result = service.store.upsert_record(
            _record(
                record_id="ui-ready",
                state="ready",
                archive_path=verified,
                project_code="UI-READY",
                project_name="UI Ready Fixture",
                canonical_record=_canonical_record(),
                source_identity=_source_identity(project_code="UI-READY"),
            )
        )
        service.store.upsert_record(
            _record(
                record_id="ui-field-missing",
                state="field_missing",
                archive_path=field_missing,
                project_code="UI-FIELD-MISSING",
                project_name="UI Field Missing Fixture",
                canonical_record=_canonical_record(),
                source_identity=_source_identity(project_code="UI-FIELD-MISSING"),
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="export_field_missing",
                        message="missing export field",
                        evidence={"missing_fields": ["挂牌价格"]},
                    )
                ],
            )
        )
        service.store.upsert_record(
            _record(
                record_id="ui-field-missing-action",
                state="field_missing",
                archive_path=field_missing_action,
                project_code="UI-FIELD-ACTION",
                project_name="UI Field Action Fixture",
                canonical_record=_canonical_record(),
                source_identity=_source_identity(project_code="UI-FIELD-ACTION"),
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="export_field_missing",
                        message="missing export field",
                        evidence={"missing_fields": ["挂牌价格"]},
                    )
                ],
            )
        )
        service.store.upsert_record(
            _record(
                record_id="ui-review",
                state="pending_review",
                archive_path=review,
                project_code="UI-REVIEW",
                project_name="UI Review Fixture",
                project_type="未知",
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="business_resolution_required",
                        message="business resolution required",
                        evidence={"reason_code": "unrecognized_business"},
                    )
                ],
            )
        )
        mapping_result = service.store.upsert_record(
            _record(
                record_id="ui-mapping",
                state="pending_mapping",
                archive_path=mapping,
                project_code="UI-MAPPING",
                project_name="UI Mapping Fixture",
                project_type="实物资产",
                postprocess_payload={"项目编号": "UI-MAPPING", "项目名称": "UI Mapping Fixture", "转让方": "UI Transferor"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="missing group mapping",
                        evidence={"missing_fields": ["group_name"]},
                    )
                ],
            )
        )
        service.store.mark_mapping_pending(
            record_id=str(mapping_result["record_id"]),
            revision_id=int(mapping_result["revision_id"]),
            project_code="UI-MAPPING",
            payload={"transferor": "UI Transferor", "missing_fields": ["group_name"]},
        )
        conflict_result = service.store.upsert_record(
            _record(
                record_id="ui-conflict",
                state="mapping_conflict",
                archive_path=conflict,
                project_code="UI-CONFLICT",
                project_name="UI Conflict Fixture",
                project_type="实物资产",
                postprocess_payload={"项目编号": "UI-CONFLICT", "项目名称": "UI Conflict Fixture", "转让方": "UI Conflict Transferor"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_conflict",
                        message="mapping conflict",
                        evidence={"candidate_resolutions": ["央企", "地方国企"]},
                    )
                ],
            )
        )
        service.store.mark_mapping_pending(
            record_id=str(conflict_result["record_id"]),
            revision_id=int(conflict_result["revision_id"]),
            project_code="UI-CONFLICT",
            payload={
                "transferor": "UI Conflict Transferor",
                "candidate_resolutions": [
                    {"rule_kind": "transferor_to_source_type", "source_name": "UI Conflict Transferor", "target_value": "央企", "title": "候选一"},
                    {"rule_kind": "transferor_to_source_type", "source_name": "UI Conflict Transferor", "target_value": "地方国企", "title": "候选二"},
                ],
            },
        )
        service.store.upsert_mapping_entry(
            company_name="UI Existing Transferor",
            group_name="UI Existing Group",
            metadata={
                "rule_kind": "transferor_to_group",
                "match_field": "transferor",
                "target_field": "group_name",
                "notes": "fixture",
            },
        )
        service.store.mark_exported(
            export_id="ui-exp-pruned",
            cursor_id="ui-cursor-pruned",
            requested_export_mode="incremental",
            date_from="2026-05-01",
            date_to="2026-05-31",
            project_type="all",
            output_dir=str(export_root),
            summary={"artifacts": [str(export_pruned)], "requested_export_mode": "incremental", "revision_watermark": 1, "retention_count": 1},
            records=[],
            retention_count=1,
        )
        service.store.mark_exported(
            export_id="ui-exp-live",
            cursor_id="ui-cursor-live",
            requested_export_mode="incremental",
            date_from="2026-05-01",
            date_to="2026-05-31",
            project_type="all",
            output_dir=str(export_root),
            summary={"artifacts": [str(export_live)], "requested_export_mode": "incremental", "revision_watermark": 2, "retention_count": 2},
            records=[{"record_id": str(ready_result["record_id"]), "revision_id": int(ready_result["revision_id"]), "revision_hash": "hash-ui-ready"}],
            retention_count=2,
        )
        job_id = service.store.create_job(
            "fixture_job",
            metadata={
                "scope": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "sse",
                }
            },
        )
        service.store.start_job(job_id)
        service.store.finish_job(job_id, status="success", summary={"persisted_count": 1})
        if export_pruned.exists():
            export_pruned.unlink()
        return {
            "ready_record_id": str(ready_result["record_id"]),
            "db_path": config.STREAMING_DB_PATH,
            "manual_root": str(manual_root),
            "picked_path": env["PEAP_FAKE_SELECTED_PATH"],
            "download_dir": str(root / "downloaded-exports"),
        }
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def _wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"server did not become ready: {url}: {last_error}")


def _start_process(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _drain_output(process: subprocess.Popen[str] | None) -> str:
    if process is None or process.stdout is None:
        return ""
    try:
        return process.stdout.read() or ""
    except Exception:
        return ""


def _db_counts(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        ack_rows = conn.execute(
            """
            SELECT acknowledged_payload_json
            FROM records
            WHERE project_code = 'UI-FIELD-ACTION'
            """
        ).fetchall()
        acknowledged_count = 0
        for (raw_payload,) in ack_rows:
            try:
                payload = json.loads(raw_payload or "{}")
            except Exception:
                payload = {}
            field_missing = payload.get("field_missing") if isinstance(payload, dict) else {}
            if isinstance(field_missing, dict) and field_missing.get("acknowledged") is True:
                acknowledged_count += 1
        return {
            "exports": int(conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0]),
            "jobs": int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "ui_field_missing_action_acked": acknowledged_count,
            "mapping_entries": int(conn.execute("SELECT COUNT(*) FROM mapping_entries").fetchone()[0]),
        }


def _assert_no_real_path_in_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        for table, column in [("records", "archive_path"), ("records", "source_file"), ("exports", "output_dir")]:
            rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
            for (value,) in rows:
                if _under_real_peap(value):
                    raise RuntimeError(f"real PEAP path leaked into {table}.{column}: {value}")


def _run_browser(frontend_url: str, fixture: dict[str, str]) -> list[str]:
    from playwright.sync_api import expect, sync_playwright

    coverage: list[str] = []
    requests: list[str] = []

    def covered(name: str) -> None:
        coverage.append(name)

    with sync_playwright() as p:
        browser = launch_chromium_browser_sync(p, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("request", lambda request: requests.append(request.method + " " + request.url))
        page.goto(frontend_url, wait_until="networkidle")
        expect(page.locator("#panel-overview")).to_be_visible()
        covered("nav.overview")

        for panel, marker in [
            ("tasks", "#panel-tasks"),
            ("records", "#panel-records"),
            ("reviews", "#panel-reviews"),
            ("export-history", "#panel-export-history"),
            ("mappings", "#panel-mappings"),
            ("settings", "#panel-settings"),
            ("overview", "#panel-overview"),
        ]:
            page.locator(f'.sidebar-nav-link[data-panel="{panel}"]').click()
            expect(page.locator(marker)).to_be_visible()
            covered(f"nav.{panel}")

        page.locator("#btn-oneclick").click()
        expect(page.locator("#modal-oneclick")).to_be_visible()
        page.locator("#oneclick-start-date").fill("2026-05-01")
        page.locator("#oneclick-end-date").fill("2026-05-01")
        page.locator("#oneclick-max-pages").fill("1")
        page.locator("#oneclick-concurrency").fill("1")
        page.locator("#oneclick-cancel").click()
        covered("overview.one_click_modal.inputs_cancel")

        page.locator("#btn-historical").click()
        expect(page.locator("#modal-historical")).to_be_visible()
        page.locator("#hist-start").fill("2026-05-01")
        page.locator("#hist-end").fill("2026-05-01")
        page.locator("#hist-cancel").click()
        covered("overview.historical_modal.inputs_cancel")

        page.locator("#btn-import").click()
        expect(page.locator("#modal-manual-import")).to_be_visible()
        page.locator("#manual-import-browse").click()
        expect(page.locator("#manual-import-dir")).to_have_value(fixture["picked_path"], timeout=2000)
        page.locator("#manual-import-family").select_option(index=0)
        page.locator("#manual-import-business").select_option(index=0)
        page.locator("#manual-import-cancel").click()
        covered("overview.manual_import_modal.browse_select_cancel")

        family_tabs = page.locator("[data-family-tab]")
        if family_tabs.count() > 0:
            family_tabs.first.click()
            covered("overview.family_stats_tab")
        page.locator(".jobs-header-link").first.click()
        expect(page.locator("#panel-tasks")).to_be_visible()
        covered("overview.tasks_link")

        page.locator('.sidebar-nav-link[data-panel="records"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#record-family").select_option("listing")
        page.locator("#filter-keyword").fill("UI-FIELD-ACTION")
        page.locator("#filter-state").select_option("field_missing")
        page.locator("#btn-records-search").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#records-tbody")).to_contain_text("UI-FIELD-ACTION")
        first_ack_buttons = page.locator(".btn-field-missing-ack")
        if first_ack_buttons.count() <= 0:
            table_html = page.locator("#records-tbody").inner_html(timeout=2000)
            api_row = page.evaluate("""
                async () => {
                  const response = await fetch('/api/records?record_family=listing&state=field_missing&business_id=all&exchange=all&keyword=UI-FIELD-ACTION&page=1&page_size=20');
                  const payload = await response.json();
                  return payload.data && payload.data.rows ? payload.data.rows[0] : payload;
                }
            """)
            raise RuntimeError(f"field_missing ack button not rendered before other actions; row={api_row}; tbody={table_html[:2000]}")
        first_ack_buttons.first.click()
        page.wait_for_load_state("networkidle")
        covered("records.field_missing_ack")

        page.locator("#filter-keyword").fill("UI-READY")
        page.locator("#filter-state").select_option("ready")
        page.locator("#filter-business").select_option("physical_asset")
        page.locator("#filter-exchange").select_option("sse")
        page.locator("#filter-keyword").press("Enter")
        page.locator("#filter-date-from").fill("2026-05-01")
        page.locator("#filter-date-from").press("Enter")
        page.locator("#filter-date-to").fill("2026-05-31")
        page.locator("#filter-date-to").press("Enter")
        page.locator("#btn-records-search").click()
        page.wait_for_load_state("networkidle")
        covered("records.filters")
        folder_buttons = page.locator(".btn-record-folder")
        if folder_buttons.count() > 0:
            folder_buttons.first.click()
            covered("records.reveal_folder")
        page.locator("#btn-records-export").click()
        page.wait_for_load_state("networkidle")
        covered("records.export")
        next_page = page.locator("#btn-records-next")
        prev_page = page.locator("#btn-records-prev")
        expect(next_page).to_be_visible()
        expect(prev_page).to_be_visible()
        if next_page.is_enabled():
            next_page.click()
            if prev_page.is_enabled():
                prev_page.click()
        covered("records.pagination_buttons")

        page.locator('.sidebar-nav-link[data-panel="reviews"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#review-filter-kind").select_option(index=0)
        page.locator("#review-filter-state").select_option("pending_review")
        page.locator("#review-filter-keyword").fill("UI-REVIEW")
        page.locator("#review-filter-keyword").dispatch_event("change")
        page.wait_for_load_state("networkidle")
        prev = page.locator("#review-prev-page")
        nxt = page.locator("#review-next-page")
        if prev.count() > 0:
            prev.click(force=True)
        if nxt.count() > 0:
            nxt.click(force=True)
        covered("reviews.filters_pagination")

        page.locator('.sidebar-nav-link[data-panel="overview"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#btn-export").click()
        page.wait_for_load_state("networkidle")
        covered("overview.export_button")

        page.locator('.sidebar-nav-link[data-panel="export-history"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#btn-export-history-refresh").click()
        page.wait_for_load_state("networkidle")
        rows = page.locator(".export-history-row")
        expect(rows.first).to_be_visible()
        page.locator("#export-history-row-ui-exp-pruned").click()
        expect(page.locator("#btn-export-history-open")).to_be_disabled()
        page.locator("#export-history-row-ui-exp-live").click()
        expect(page.locator("#btn-export-history-open")).to_be_enabled()
        page.locator("#btn-export-history-open").click()
        page.wait_for_timeout(200)
        page.locator("#export-history-download-dir").fill(fixture["download_dir"])
        page.locator("#btn-export-history-download").click()
        page.wait_for_load_state("networkidle")
        covered("export_history.refresh_select_open_download")

        page.locator('.sidebar-nav-link[data-panel="mappings"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#mapping-rule-kind").select_option(index=0)
        page.locator("#mapping-source-name").fill("UI New Transferor")
        page.locator("#mapping-target-value").fill("UI New Group")
        page.locator("#mapping-notes").fill("ui audit")
        page.locator("#btn-mapping-preview").click()
        page.wait_for_load_state("networkidle")
        page.locator("#btn-mapping-save").click()
        page.wait_for_load_state("networkidle")
        covered("mappings.form_preview_save")
        use_rules = page.locator(".btn-use-rule")
        if use_rules.count() > 0:
            use_rules.first.click()
            covered("mappings.use_rule")
        section_actions = page.locator('[id^="btn-mappings-section-"]')
        if section_actions.count() > 0:
            section_actions.first.click()
            covered("mappings.section_action")
        edit_buttons = page.locator(".btn-edit-mapping-entry")
        if edit_buttons.count() > 0:
            edit_buttons.first.click()
            page.locator("#btn-mapping-reset").click()
            covered("mappings.edit_reset")
        delete_buttons = page.locator(".btn-delete-mapping-entry")
        if delete_buttons.count() > 0:
            delete_buttons.first.click()
            page.wait_for_load_state("networkidle")
            covered("mappings.delete")

        page.locator('.sidebar-nav-link[data-panel="settings"]').click()
        page.wait_for_load_state("networkidle")
        page.locator("#settings-default-exchange").select_option("all")
        page.locator("#settings-default-family").select_option("listing")
        page.locator("#settings-default-business").select_option("physical_asset")
        page.locator("#settings-default-scope-exchange").select_option("sse")
        page.locator("#settings-default-concurrency").fill("2")
        page.locator("#settings-retention-count").fill("3")
        page.locator("#btn-settings-archive-root-browse").click()
        page.locator("#btn-settings-export-root-browse").click()
        page.locator("#btn-settings-basic-save").click()
        page.wait_for_load_state("networkidle")
        covered("settings.basic_select_browse_save")
        page.locator("#settings-save-json").check()
        page.locator("#btn-settings-postprocess-browse").click()
        page.locator("#btn-settings-raw-manual-root-browse").click()
        open_buttons = page.locator(".btn-open-path")
        for index in range(min(open_buttons.count(), 3)):
            open_buttons.nth(index).click()
        page.locator("#btn-settings-advanced-save").click()
        page.wait_for_load_state("networkidle")
        covered("settings.advanced_checkbox_browse_open_save")

        browser.close()

    required_fragments = [
        "/api/records",
        "/api/exports",
        "/api/exports/history",
        "/api/mappings",
        "/api/settings/basic",
        "/api/settings/advanced",
        "/api/system/select-path",
        "/api/system/open-path",
    ]
    missing = [fragment for fragment in required_fragments if not any(fragment in request for request in requests)]
    if missing:
        raise RuntimeError(f"UI audit did not hit required API fragments: {missing}")
    return coverage


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="peap-ui-interaction-audit-")).resolve()
    env = _env_for_root(root)
    fixture = _seed_fixture(root, env)
    backend_port = _free_port()
    frontend_port = _free_port()
    backend_env = os.environ.copy() | env
    frontend_env = os.environ.copy() | {
        "PEAP_FRONTEND_BACKEND_TARGET": f"http://127.0.0.1:{backend_port}",
        "PEAP_FRONTEND_PORT": str(frontend_port),
    }
    backend = None
    frontend = None
    try:
        backend = _start_process(
            [sys.executable, "-m", "desktop_backend.app_backend", "--host", "127.0.0.1", "--port", str(backend_port), "--app-home", env["PEAP_APP_HOME"]],
            cwd=REPO_ROOT,
            env=backend_env,
        )
        _wait_http(f"http://127.0.0.1:{backend_port}/api/health")
        frontend = _start_process(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port)],
            cwd=REPO_ROOT / "frontend",
            env=frontend_env,
        )
        _wait_http(f"http://127.0.0.1:{frontend_port}")
        before = _db_counts(fixture["db_path"])
        coverage = _run_browser(f"http://127.0.0.1:{frontend_port}", fixture)
        after = _db_counts(fixture["db_path"])
        _assert_no_real_path_in_db(fixture["db_path"])
        required_coverage = {
            "records.field_missing_ack",
            "export_history.refresh_select_open_download",
            "mappings.form_preview_save",
            "settings.basic_select_browse_save",
            "settings.advanced_checkbox_browse_open_save",
        }
        missing_coverage = sorted(required_coverage.difference(coverage))
        if missing_coverage:
            raise RuntimeError(f"UI audit missed required interactions: {missing_coverage}")
        report = {
            "temp_root": str(root),
            "coverage": coverage,
            "db_before": before,
            "db_after": after,
            "download_dir_exists": Path(fixture["download_dir"]).exists(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if after["jobs"] <= before["jobs"]:
            raise RuntimeError("UI mutating interactions did not persist any job activity")
        if after["ui_field_missing_action_acked"] <= before["ui_field_missing_action_acked"]:
            raise RuntimeError("field_missing acknowledgement was not persisted")
        if after["mapping_entries"] <= before["mapping_entries"]:
            raise RuntimeError("mapping save interaction was not persisted")
        return 0
    finally:
        _stop_process(frontend)
        _stop_process(backend)
        if backend and backend.poll() not in {0, None}:
            print(_drain_output(backend), file=sys.stderr)
        if frontend and frontend.poll() not in {0, None}:
            print(_drain_output(frontend), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
