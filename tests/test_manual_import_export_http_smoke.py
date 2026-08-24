from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from desktop_backend.app_backend import build_handler
from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.migrations import MigrationRunner

REPO_ROOT = Path(__file__).resolve().parents[1]


class ManualImportExportHttpSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        self.archive_root = os.path.join(self.temp_dir.name, "archive")
        self.export_root = os.path.join(self.temp_dir.name, "exports")
        self.input_dir = os.path.join(self.temp_dir.name, "manual-import")
        os.makedirs(self.archive_root, exist_ok=True)
        os.makedirs(self.export_root, exist_ok=True)
        os.makedirs(self.input_dir, exist_ok=True)

        self.rules_config_path = os.path.join(self.temp_dir.name, "postprocess_rules.json")
        Path(self.rules_config_path).write_text(
            json.dumps(
                {
                    "rules": {
                        "R010_filter_scrap_physical_asset": {
                            "enabled": True,
                            "priority": 5,
                            "params": {"active": True, "severity": "info", "search_all_fields": True},
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=str(REPO_ROOT))

        MigrationRunner.run(self.config.STREAMING_DB_PATH)
        self.service = AppService(config_obj=self.config)
        self.service._start_background_thread = lambda *, name, target: target()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.service))
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._shutdown_server)

    def _shutdown_server(self) -> None:
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        if hasattr(self, "server_thread"):
            self.server_thread.join(timeout=5)

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return int(exc.code), json.loads(exc.read().decode("utf-8"))

    def _ok_data(self, payload: dict) -> dict:
        self.assertTrue(payload.get("ok"), payload)
        return dict(payload.get("data") or {})

    def _records(self, **query: str) -> list[dict]:
        path = "/api/records"
        if query:
            path = f"{path}?{urlencode(query)}"
        status, payload = self._request("GET", path)
        self.assertEqual(status, 200)
        return list(self._ok_data(payload).get("rows") or [])

    def _write_fixture(self, relative_name: str, content: str) -> str:
        fixture_path = os.path.join(self.input_dir, relative_name)
        os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
        Path(fixture_path).write_text(content, encoding="utf-8")
        return fixture_path

    def _write_unknown_business_fixture(self) -> str:
        return self._write_fixture(
            "cbex-unknown-business.html",
            """
            <html>
              <head>
                <title>北交互联-未知业务</title>
                <meta name="keywords" content="北交互联" />
              </head>
              <body>
                <textarea id="jsonobj">{
                  "object": {
                    "projectcode": "G32026BJ1000099",
                    "object": "测试未知业务项目",
                    "publishdate": "2026-03-21",
                    "expiredate": "2026-03-31"
                  },
                  "sellerlist": {
                    "utrmcemsseller": [
                      {"sellername": "测试转让方"}
                    ]
                  }
                }</textarea>
              </body>
            </html>
            """,
        )

    def _write_equity_mapping_fixture(self) -> str:
        return self._write_fixture(
            "挂牌_股权转让/sse-equity-pending-mapping.html",
            """
            <html>
              <head>
                <title>上海联合产权交易所</title>
              </head>
              <body>
                <div class="project-detail-top">
                  <div class="title">上海电气集团恒联企业发展有限公司35%股权</div>
                  <div class="detail-top-label"><i>股权转让</i><i>正式披露</i><span>项目编号：G32026SH1000003</span></div>
                </div>
                <div class="project-price-box">
                  <span class="project-price-name">转让底价：</span>
                  <span class="project-price-num"><span class="fs30">108.00</span><span>万元</span></span>
                </div>
                <div class="xmjs-infor-box">
                  <div class="infor-date">
                    <ul>
                      <li><div class="text">公告开始</div><div class="numb">2026-04-09</div></li>
                      <li><div class="text">公告截止</div><div class="numb">2026-04-16</div></li>
                    </ul>
                  </div>
                  <div class="label-infor"><span class="name">标的所在地区</span><span class="cont">上海 闵行区</span></div>
                </div>
                <div class="detail-info">
                  <table class="table-info">
                    <tr>
                      <th>交易机构</th>
                      <td>
                        <span class="text">项目负责人</span>
                        <span class="text">陆文奕</span>
                        <span class="text">62657272-381</span>
                      </td>
                    </tr>
                  </table>
                </div>
                <table class="xm-tab">
                  <tr><td>转让方名称</td><td>上海电气集团恒联企业发展有限公司</td></tr>
                  <tr><td>所属集团或主管部门名称</td><td>上海电气集团</td></tr>
                </table>
              </body>
            </html>
            """,
        )

    def test_manual_import_export_stays_stable_when_fallback_business_is_rule_filtered_over_http(self) -> None:
        self._write_fixture(
            "cbex-otc-http-smoke.html",
            """
            <html>
              <head>
                <title>上海联合产权交易所</title>
              </head>
              <body>
                <div class="project-detail-top">
                  <div class="title">星科金朋半导体(江阴)有限公司部分资产（报废设备资产包1）</div>
                  <div class="detail-top-label"><i>实物资产</i><i>正式披露</i><span>项目编号：GR2026SH1000524</span></div>
                </div>
                <div class="project-price-box">
                  <span class="project-price-name">转让底价：</span>
                  <span class="project-price-num"><span class="fs30">1.469</span><span>万元</span></span>
                </div>
                <div class="xmjs-infor-box">
                  <div class="infor-date">
                    <ul>
                      <li><div class="text">公告开始</div><div class="numb">2026-04-09</div></li>
                      <li><div class="text">公告截止</div><div class="numb">2026-04-16</div></li>
                    </ul>
                  </div>
                  <div class="label-infor"><span class="name">标的所在地区</span><span class="cont">江苏省 无锡市</span></div>
                </div>
                <div class="detail-info">
                  <table class="table-info">
                    <tr>
                      <th>交易机构</th>
                      <td>
                        <span class="text">项目负责人</span>
                        <span class="text">汤佳锋</span>
                        <span class="text">021-62657272-385、16602122306</span>
                      </td>
                    </tr>
                  </table>
                </div>
                <table class="xm-tab">
                  <tr><td>转让方名称</td><td>星科金朋半导体(江阴)有限公司</td></tr>
                  <tr><td>所属集团或主管部门名称</td><td>中国华润有限公司</td></tr>
                </table>
              </body>
            </html>
            """,
        )

        status, payload = self._request(
            "POST",
            "/api/settings/basic",
            {
                "default_exchange": "shanghai",
                "default_concurrency": 2,
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "sse",
                },
                "paths": {
                    "archive_root": self.archive_root,
                    "export_root": self.export_root,
                },
            },
        )
        self.assertEqual(status, 200)
        basic = self._ok_data(payload)
        self.assertEqual(basic["effective_default_scope"]["business_id"], "physical_asset")
        self.assertEqual(basic["effective_default_scope"]["exchange"], "sse")

        status, payload = self._request(
            "POST",
            "/api/settings/advanced",
            {
                "processing": {
                    "postprocess_config": self.rules_config_path,
                },
                "ingest_paths": {
                    "raw_manual_root": self.input_dir,
                },
            },
        )
        self.assertEqual(status, 200)
        advanced = self._ok_data(payload)
        self.assertEqual(advanced["processing"]["postprocess_config"], self.rules_config_path)

        status, payload = self._request(
            "POST",
            "/api/jobs/manual-import",
            {
                "input_dir": self.input_dir,
                "record_family": "listing",
                "business_id": "physical_asset",
                "business_label": "实物资产",
                "exchange": "sse",
            },
        )
        self.assertEqual(status, 202)
        launch = self._ok_data(payload)
        self.assertTrue(launch["job_id"])

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "skipped")
        self.assertEqual(records[0]["project_type_label"], "实物资产")
        record_id = str(records[0]["record_id"])

        status, payload = self._request(
            "POST",
            "/api/mappings",
            {
                "rule_kind": "group_type",
                "source_name": "中国华润有限公司",
                "target_value": "央企",
            },
        )
        self.assertEqual(status, 200)
        mapping_save = self._ok_data(payload)
        self.assertTrue(mapping_save["entry_id"])
        self.assertEqual(mapping_save["affected_count"], 0)
        self.assertEqual(mapping_save["job_id"], "")

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_id"], record_id)
        self.assertEqual(records[0]["state"], "skipped")

        status, payload = self._request(
            "POST",
            "/api/exports",
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "sse",
                    "state": "all",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
        )
        self.assertEqual(status, 200)
        export_result = self._ok_data(payload)
        self.assertEqual(export_result["status"], "empty")
        self.assertEqual(export_result["empty_reason_code"], "skipped_only")
        self.assertEqual(export_result["scope_state_counts"]["skipped"], 1)

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_id"], record_id)
        self.assertEqual(records[0]["state"], "skipped")

    def test_manual_import_without_explicit_scope_infers_business_before_mapping_resolution_over_http(self) -> None:
        self._write_unknown_business_fixture()

        status, payload = self._request(
            "POST",
            "/api/settings/basic",
            {
                "default_exchange": "sse",
                "default_concurrency": 2,
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
                "paths": {
                    "archive_root": self.archive_root,
                    "export_root": self.export_root,
                },
            },
        )
        self.assertEqual(status, 200)
        basic = self._ok_data(payload)
        self.assertEqual(basic["effective_default_scope"]["business_id"], "equity_transfer")

        status, payload = self._request(
            "POST",
            "/api/settings/advanced",
            {
                "processing": {
                    "postprocess_config": self.rules_config_path,
                },
                "ingest_paths": {
                    "raw_manual_root": self.input_dir,
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(self._ok_data(payload)["ingest_paths"]["raw_manual_root"], self.input_dir)

        status, payload = self._request(
            "POST",
            "/api/jobs/manual-import",
            {
                "input_dir": self.input_dir,
            },
        )
        self.assertEqual(status, 202)
        self.assertTrue(self._ok_data(payload)["job_id"])

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "pending_mapping")
        self.assertEqual(records[0]["project_type_label"], "股权转让")
        self.assertEqual(records[0]["business_id"], "equity_transfer")

    def test_manual_import_explicit_scope_can_close_mapping_refresh_and_export_over_http(self) -> None:
        self._write_equity_mapping_fixture()

        status, payload = self._request(
            "POST",
            "/api/settings/basic",
            {
                "default_exchange": "cbex",
                "default_concurrency": 2,
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "cbex",
                },
                "paths": {
                    "archive_root": self.archive_root,
                    "export_root": self.export_root,
                },
            },
        )
        self.assertEqual(status, 200)

        status, payload = self._request(
            "POST",
            "/api/settings/advanced",
            {
                "processing": {
                    "postprocess_config": self.rules_config_path,
                },
                "ingest_paths": {
                    "raw_manual_root": self.input_dir,
                },
            },
        )
        self.assertEqual(status, 200)

        status, payload = self._request(
            "POST",
            "/api/jobs/manual-import",
            {
                "input_dir": self.input_dir,
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        )
        self.assertEqual(status, 202)
        launch = self._ok_data(payload)
        self.assertEqual(launch["business_id"], "equity_transfer")

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["state"], "pending_mapping")
        self.assertEqual(record["project_type_label"], "股权转让")
        self.assertEqual(record["business_id"], "equity_transfer")

        status, payload = self._request(
            "POST",
            "/api/mappings/preview",
            {
                "rule_kind": "group_type",
                "source_name": "上海电气集团",
                "target_value": "市属",
            },
        )
        self.assertEqual(status, 200)
        preview = self._ok_data(payload)
        self.assertEqual(preview["affected_count"], 1)

        status, payload = self._request(
            "POST",
            "/api/mappings",
            {
                "rule_kind": "group_type",
                "source_name": "上海电气集团",
                "target_value": "市属",
            },
        )
        self.assertEqual(status, 200)
        save_result = self._ok_data(payload)
        self.assertEqual(save_result["affected_count"], 1)
        self.assertTrue(save_result["job_id"])

        records = self._records(record_family="listing", state="all", exchange="all")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "ready")
        self.assertEqual(records[0]["display_values"]["隶属集团"], "上海电气集团")
        self.assertEqual(records[0]["display_values"]["类型"], "市属")

        status, payload = self._request(
            "POST",
            "/api/exports",
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                    "state": "ready",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
        )
        self.assertEqual(status, 200)
        export_result = self._ok_data(payload)
        self.assertEqual(export_result["status"], "completed")
        artifacts = export_result.get("artifacts") or []
        self.assertEqual(len(artifacts), 1)
        first_artifact = artifacts[0]
        export_path = str(first_artifact.get("file_path") or "") if isinstance(first_artifact, dict) else str(first_artifact)
        self.assertTrue(export_path.endswith(".xlsx"))
        self.assertTrue(Path(export_path).is_file())


if __name__ == "__main__":
    unittest.main()
