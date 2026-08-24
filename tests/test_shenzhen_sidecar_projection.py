from __future__ import annotations

import hashlib
import json
from pathlib import Path

from peap.streaming_ingest import (
    StreamingIngestDependencies,
    StreamingIngestRunner,
)
from peap.streaming_models import ItemSavedPayload
from peap.streaming_postprocess import apply_mapping_entries
from peap.streaming_store import StreamingStore
from peap_parsers.base import ParserContext
from peap_parsers.shenzhen import ShenzhenParser

GOV_PROJECT_CODE = "G32026SZ1000064"
SOURCE_PROJECT_CODE = "CQ2026081300003"
PROJECT_NAME = "兰州倚能假日影城有限责任公司40%股权"
PACKAGE_ID = "2149abca5aa34171b31dfeb9176f30b7"
PAGE_URL = (
    "https://www.sotcbb.com/bdDetail.htm"
    f"?contentId={PACKAGE_ID}&channelId=3961&id=2430975"
)


def _write_shenzhen_snapshot(path: Path, *, html_project_code: str) -> None:
    html = f"""
    <html>
      <head><title>{html_project_code}</title></head>
      <body>
        <div class="title" id="js_projectName">{PROJECT_NAME}</div>
        <span id="gpqsrq">2026-08-13</span>
      </body>
    </html>
    """
    path.write_text(html, encoding="utf-8")
    html_bytes = path.read_bytes()
    sidecar = {
        "archive_content_bytes": len(html_bytes),
        "archive_content_sha256": hashlib.sha256(html_bytes).hexdigest(),
        "source_id": "shenzhen",
        "page_url": PAGE_URL,
        "detail_payload": {
            "data": {
                "noticeVoList": [
                    {"title": "项目名称", "value": "无关嵌套项目"},
                    {"key": "转让方名称", "value": "无关嵌套转让方"},
                ],
                "form": [
                    {
                        "inputType": "json",
                        "name": "标的基本信息",
                        "value": [
                            {"inputType": "input", "name": "项目编号", "value": SOURCE_PROJECT_CODE},
                            {"inputType": "input", "name": "标的名称", "value": PROJECT_NAME},
                        ],
                    },
                    {
                        "inputType": "json",
                        "name": "转让方简况",
                        "value": [
                            {
                                "inputType": "input",
                                "name": "转让方名称",
                                "value": "兰州倚能电力(集团)有限公司",
                            },
                            {
                                "inputType": "input",
                                "name": "国资监管机构",
                                "value": "国务院国资委监管",
                            },
                            {
                                "inputType": "input",
                                "name": "国家出资企业/主管部门名称",
                                "value": "国家电网有限公司",
                            },
                        ],
                    },
                ],
                "portalTPackage": {
                    "gzwCode": GOV_PROJECT_CODE,
                    "id": PACKAGE_ID,
                    "packageId": PACKAGE_ID,
                    "projectCode": SOURCE_PROJECT_CODE,
                    "projectName": PROJECT_NAME,
                    "projectTenderCategory": "产权转让",
                    "tenderCategory": "股权",
                    "tenderCategoryCode": "CQJY_GQ",
                },
                "reListingBidPackageId": "must-not-win-over-exact-package-id",
            }
        },
    }
    path.with_suffix(".json").write_text(
        json.dumps(sidecar, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_snapshot(path: Path) -> dict[str, object]:
    parser = ShenzhenParser(
        path.read_text(encoding="utf-8"),
        context=ParserContext(source_file=str(path)),
    )
    return parser.parse()


def _replace_html_and_refresh_integrity(path: Path, html: str) -> None:
    path.write_text(html, encoding="utf-8")
    html_bytes = path.read_bytes()
    sidecar_path = path.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["archive_content_bytes"] = len(html_bytes)
    sidecar["archive_content_sha256"] = hashlib.sha256(html_bytes).hexdigest()
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")


def test_detail_form_projects_fields_and_canonicalizes_cq_alias(tmp_path: Path) -> None:
    snapshot = tmp_path / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)

    payload = _parse_snapshot(snapshot)

    assert payload["项目编号"] == GOV_PROJECT_CODE
    assert payload["source_project_code"] == SOURCE_PROJECT_CODE
    assert payload["项目名称"] == PROJECT_NAME
    assert payload["项目类型"] == "股权转让"
    assert payload["转让方"] == payload["seller"] == "兰州倚能电力(集团)有限公司"
    assert payload["隶属集团"] == payload["group_name"] == "国家电网有限公司"
    assert payload["国家出资企业或主管部门名称"] == "国家电网有限公司"
    assert payload["state_funded_department"] == "国家电网有限公司"
    assert payload["国资监管机构"] == payload["state_asset_supervisor"] == "国务院国资委监管"
    assert payload["project_id"] == PACKAGE_ID
    assert payload["page_url"] == payload["source_url"] == PAGE_URL


def test_sidecar_project_type_preserves_capital_increase_semantics(tmp_path: Path) -> None:
    snapshot = tmp_path / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
    sidecar_path = snapshot.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    package = sidecar["detail_payload"]["data"]["portalTPackage"]
    package.update(
        {
            "projectTenderCategory": "企业增资",
            "tenderCategory": "增资扩股",
            "tenderCategoryCode": "CQJY_ZZKG",
        }
    )
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    payload = _parse_snapshot(snapshot)

    assert payload["项目类型"] == "增资扩股"


def test_generic_property_transfer_category_does_not_imply_equity(tmp_path: Path) -> None:
    snapshot = tmp_path / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
    sidecar_path = snapshot.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    package = sidecar["detail_payload"]["data"]["portalTPackage"]
    package.pop("tenderCategory")
    package.pop("tenderCategoryCode")
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    payload = _parse_snapshot(snapshot)

    assert "项目类型" not in payload


def test_multiple_package_scopes_never_cross_project_fields(tmp_path: Path) -> None:
    snapshot = tmp_path / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
    sidecar_path = snapshot.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    target_scope = sidecar["detail_payload"]["data"]
    wrong_scope = {
        "form": [
            {"name": "项目编号", "value": "CQ2026081200001"},
            {"name": "标的名称", "value": "不相关项目"},
            {"name": "转让方名称", "value": "不相关转让方"},
            {"name": "国家出资企业/主管部门名称", "value": "不相关集团"},
        ],
        "portalTPackage": {
            "gzwCode": "G32026SZ9999999",
            "packageId": "wrong-package-id",
            "projectCode": "CQ2026081200001",
            "projectName": "不相关项目",
        },
    }
    sidecar["detail_payload"]["data"] = {"packages": [wrong_scope, target_scope]}
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    payload = _parse_snapshot(snapshot)

    assert payload["项目编号"] == GOV_PROJECT_CODE
    assert payload["source_project_code"] == SOURCE_PROJECT_CODE
    assert payload["project_id"] == PACKAGE_ID
    assert payload["转让方"] == "兰州倚能电力(集团)有限公司"
    assert payload["隶属集团"] == "国家电网有限公司"
    assert "不相关" not in json.dumps(payload, ensure_ascii=False)


def test_ambiguous_package_scopes_with_same_cq_reject_sidecar(tmp_path: Path) -> None:
    snapshot = tmp_path / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
    sidecar_path = snapshot.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    first_scope = sidecar["detail_payload"]["data"]
    second_scope = json.loads(json.dumps(first_scope, ensure_ascii=False))
    second_scope["portalTPackage"].update(
        {
            "gzwCode": "G32026SZ9999998",
            "packageId": "ambiguous-package-id",
        }
    )
    sidecar["detail_payload"]["data"] = {"packages": [first_scope, second_scope]}
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    payload = _parse_snapshot(snapshot)

    assert payload["项目编号"] == SOURCE_PROJECT_CODE
    assert payload["项目名称"] == PROJECT_NAME
    assert "source_project_code" not in payload
    assert "project_id" not in payload
    assert "转让方" not in payload


def test_explicit_incomplete_sidecar_status_cannot_project_fields(tmp_path: Path) -> None:
    scenarios = {
        "top-pending": {"save_status": "pending"},
        "top-failed": {"save_status": "failed"},
        "nested-pending": {"metadata": {"save_status": "pending"}},
        "nested-failed": {"metadata": {"save_status": "failed"}},
        "conflicting-statuses": {
            "save_status": "complete",
            "metadata": {"save_status": "pending"},
        },
    }
    for scenario, overrides in scenarios.items():
        snapshot = tmp_path / scenario / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
        snapshot.parent.mkdir()
        _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
        sidecar_path = snapshot.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar.update(overrides)
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

        payload = _parse_snapshot(snapshot)

        assert payload["项目编号"] == SOURCE_PROJECT_CODE
        assert payload["项目名称"] == PROJECT_NAME
        assert "source_project_code" not in payload
        assert "转让方" not in payload
        assert "隶属集团" not in payload
        assert "project_id" not in payload


def test_complete_and_legacy_sidecar_statuses_can_project_fields(tmp_path: Path) -> None:
    scenarios = {
        "legacy": {},
        "top-complete": {"save_status": "complete"},
        "nested-complete": {"metadata": {"save_status": "complete"}},
        "both-complete": {
            "save_status": "complete",
            "metadata": {"save_status": "complete"},
        },
    }
    for scenario, overrides in scenarios.items():
        snapshot = tmp_path / scenario / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
        snapshot.parent.mkdir()
        _write_shenzhen_snapshot(snapshot, html_project_code=SOURCE_PROJECT_CODE)
        sidecar_path = snapshot.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar.update(overrides)
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

        payload = _parse_snapshot(snapshot)

        assert payload["项目编号"] == GOV_PROJECT_CODE
        assert payload["source_project_code"] == SOURCE_PROJECT_CODE
        assert payload["project_id"] == PACKAGE_ID
        assert payload["转让方"] == "兰州倚能电力(集团)有限公司"


def test_unbound_sidecar_cannot_inject_identity_into_identityless_html(tmp_path: Path) -> None:
    snapshot = tmp_path / "identityless.html"
    snapshot.write_text(
        "<html><head><title>深圳联合产权交易所</title></head><body>项目详情加载失败</body></html>",
        encoding="utf-8",
    )
    snapshot.with_suffix(".json").write_text(
        json.dumps(
            {
                "source_id": "shenzhen",
                "detail_payload": {
                    "data": {
                        "form": [
                            {"name": "项目编号", "value": GOV_PROJECT_CODE},
                            {"name": "项目名称", "value": PROJECT_NAME},
                            {"name": "转让方名称", "value": "不应注入的转让方"},
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = _parse_snapshot(snapshot)

    assert "项目编号" not in payload
    assert "项目名称" not in payload
    assert "转让方" not in payload


def test_hash_bound_identityless_html_requires_exactly_one_scope(tmp_path: Path) -> None:
    identityless_html = (
        "<html><head><title>深圳联合产权交易所</title></head>"
        "<body>项目详情加载失败</body></html>"
    )
    single_snapshot = tmp_path / "single" / "identityless.html"
    single_snapshot.parent.mkdir()
    _write_shenzhen_snapshot(single_snapshot, html_project_code=SOURCE_PROJECT_CODE)
    _replace_html_and_refresh_integrity(single_snapshot, identityless_html)

    single_payload = _parse_snapshot(single_snapshot)

    assert single_payload["项目编号"] == GOV_PROJECT_CODE
    assert single_payload["source_project_code"] == SOURCE_PROJECT_CODE
    assert single_payload["project_id"] == PACKAGE_ID

    multiple_snapshot = tmp_path / "multiple" / "identityless.html"
    multiple_snapshot.parent.mkdir()
    _write_shenzhen_snapshot(multiple_snapshot, html_project_code=SOURCE_PROJECT_CODE)
    _replace_html_and_refresh_integrity(multiple_snapshot, identityless_html)
    sidecar_path = multiple_snapshot.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    first_scope = sidecar["detail_payload"]["data"]
    second_scope = json.loads(json.dumps(first_scope, ensure_ascii=False))
    sidecar["detail_payload"]["data"] = {"packages": [first_scope, second_scope]}
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    multiple_payload = _parse_snapshot(multiple_snapshot)

    assert "项目编号" not in multiple_payload
    assert "source_project_code" not in multiple_payload
    assert "project_id" not in multiple_payload
    assert "转让方" not in multiple_payload


def test_supervision_and_terminal_group_mapping_resolve_type_without_blank_rule() -> None:
    central_payload, central_findings = apply_mapping_entries(
        {
            "record_family": "listing",
            "项目类型": "股权转让",
            "转让方": "兰州倚能电力(集团)有限公司",
            "隶属集团": "国家电网有限公司",
            "国资监管机构": "国务院国资委监管",
        },
        mapping_entries=[
            {
                "company_name": "国家电网有限公司",
                "source_type": "央企",
                "metadata": {
                    "rule_kind": "group_type",
                    "match_field": "group",
                    "target_field": "source_type",
                },
            }
        ],
    )
    ministry_payload, ministry_findings = apply_mapping_entries(
        {
            "record_family": "listing",
            "项目类型": "股权转让",
            "转让方": "金建（深圳）投资管理中心（有限合伙）",
            "隶属集团": "财政部",
            "国资监管机构": "财政部监管",
        },
        mapping_entries=[],
    )

    assert central_payload["类型"] == central_payload["source_type"] == "央企"
    assert ministry_payload["类型"] == ministry_payload["source_type"] == "部委"
    assert all(finding.type != "mapping_gap" for finding in [*central_findings, *ministry_findings])


def test_cq_and_g_snapshots_share_business_identity_and_candidate_tokens(tmp_path: Path) -> None:
    cq_snapshot = tmp_path / "incoming-cq" / f"{SOURCE_PROJECT_CODE}-{PROJECT_NAME}.html"
    g_snapshot = tmp_path / "incoming-g" / f"{GOV_PROJECT_CODE}-{PROJECT_NAME}.html"
    cq_snapshot.parent.mkdir()
    g_snapshot.parent.mkdir()
    _write_shenzhen_snapshot(cq_snapshot, html_project_code=SOURCE_PROJECT_CODE)
    _write_shenzhen_snapshot(g_snapshot, html_project_code=GOV_PROJECT_CODE)

    def parser(path: str) -> dict[str, object]:
        payload = _parse_snapshot(Path(path))
        payload.update(
            {
                "source_id": "shenzhen",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "项目类型": "股权转让",
            }
        )
        return payload

    store = StreamingStore(str(tmp_path / "streaming.sqlite3"), auto_migrate=True)
    runner = StreamingIngestRunner(
        store=store,
        archive_root=str(tmp_path / "archive"),
        dependencies=StreamingIngestDependencies(
            parser=parser,
            postprocess=lambda payload, **_kwargs: ({**payload, "类型": "央企"}, []),
        ),
    )

    cq_result = runner.ingest(ItemSavedPayload(source_file=str(cq_snapshot), exchange="shenzhen"))
    g_result = runner.ingest(ItemSavedPayload(source_file=str(g_snapshot), exchange="shenzhen"))

    assert cq_result["record_id"] == g_result["record_id"]
    records = store.iter_latest_records()
    assert len(records) == 1
    assert records[0]["project_code"] == GOV_PROJECT_CODE
    assert records[0]["source_identity_json"]["project_code"] == GOV_PROJECT_CODE
    assert set(records[0]["source_identity_json"]["candidate_tokens"]) == {
        f"project_code:{GOV_PROJECT_CODE}",
        f"project_code:{SOURCE_PROJECT_CODE}",
        f"project_id:{PACKAGE_ID.upper()}",
        f"page_url:{PAGE_URL}",
    }
