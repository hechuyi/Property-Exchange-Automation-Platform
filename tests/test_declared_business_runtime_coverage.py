from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from peap.business_runtime import iter_source_business_bindings
from peap.download_tasks import build_task_registry
from peap.export_projection import project_canonical_record_to_export_payload
from peap.output_contract import get_supported_source_ids_for_kind, list_deal_workbook_sheet_specs
from peap.projection_registry import resolve_projection_profile
from peap.surface_contract import KNOWN_SURFACES, supported_sources_for_surface
from peap_core.family_catalog import list_declared_source_business_support
from peap_core.source_business_contract import (
    get_export_readiness_requirement,
    get_optional_postprocess_rule_requirement,
    list_export_workbook_support,
    list_source_business_requirements,
)
from peap_postprocess.postprocess_engine.config import load_config

EXPECTED_RUNTIME_SCOPE_KEYS = frozenset({
    ("listing", "cbex", "capital_increase"),
    ("listing", "cbex", "equity_transfer"),
    ("listing", "cbex", "physical_asset"),
    ("listing", "cbex", "pre_disclosure"),
    ("listing", "cquae", "capital_increase"),
    ("listing", "cquae", "equity_transfer"),
    ("listing", "cquae", "physical_asset"),
    ("listing", "cquae", "pre_disclosure"),
    ("listing", "guangdong", "capital_increase"),
    ("listing", "guangdong", "equity_transfer"),
    ("listing", "shandong", "capital_increase"),
    ("listing", "shandong", "equity_transfer"),
    ("listing", "shenzhen", "capital_increase"),
    ("listing", "shenzhen", "equity_transfer"),
    ("listing", "sse", "capital_increase"),
    ("listing", "sse", "equity_transfer"),
    ("listing", "sse", "physical_asset"),
    ("listing", "sse", "pre_disclosure"),
    ("listing", "tpre", "capital_increase"),
    ("listing", "tpre", "equity_transfer"),
    ("listing", "tpre", "physical_asset"),
    ("listing", "tpre", "pre_disclosure"),
    ("deal", "cbex", "deal_capital_increase"),
    ("deal", "cbex", "deal_equity_transfer"),
    ("deal", "cbex", "deal_physical_asset"),
    ("deal", "cquae", "deal_capital_increase"),
    ("deal", "cquae", "deal_equity_transfer"),
    ("deal", "sse", "deal_capital_increase"),
    ("deal", "sse", "deal_equity_transfer"),
    ("deal", "sse", "deal_physical_asset"),
    ("deal", "tpre", "deal_capital_increase"),
    ("deal", "tpre", "deal_equity_transfer"),
})


def _deal_record(
    business_id: str,
    *,
    export_extras: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "record_family": "deal",
        "business_identity": {"business_id": business_id},
        "source_identity": {"source_id": "sse"},
        "canonical_fields": {
            "project_code": "G32026TEST0001",
            "project_name": "成交测试项目",
            "status": "已成交",
            "deal_date": "2026-04-20",
        },
        "export_extras": export_extras or {},
    }


def _declared_scope_keys() -> set[tuple[str, str, str]]:
    return {
        (support.family_id, source_id, support.business_id)
        for support in list_declared_source_business_support()
        for source_id in support.source_ids
    }


def _format_scope_keys(keys: set[tuple[str, str, str]]) -> list[str]:
    return [
        f"{source_id}:{record_family}:{business_id}"
        for record_family, source_id, business_id in sorted(keys)
    ]


def test_documented_runtime_capability_matrix_is_exact_and_catalog_backed() -> None:
    runtime_scope_keys = {
        (binding.record_family, binding.source_id, binding.business_id)
        for binding in iter_source_business_bindings()
        if binding.implemented
    }

    assert len(EXPECTED_RUNTIME_SCOPE_KEYS) == 32
    assert runtime_scope_keys == EXPECTED_RUNTIME_SCOPE_KEYS
    assert _declared_scope_keys() == EXPECTED_RUNTIME_SCOPE_KEYS


def test_declared_family_business_sources_have_executable_coverage() -> None:
    declared_scope_keys = _declared_scope_keys()
    runtime_scope_keys = {
        (binding.record_family, binding.source_id, binding.business_id)
        for binding in iter_source_business_bindings()
        if binding.implemented
    }
    task_scope_keys = {
        (spec.record_family, spec.exchange_code, spec.business_id)
        for spec in build_task_registry().values()
        if spec.implemented
    }

    missing_runtime = declared_scope_keys - runtime_scope_keys
    missing_download_tasks = declared_scope_keys - task_scope_keys
    missing_projection: dict[str, list[str]] = {}
    missing_surface: dict[str, list[str]] = {}

    for support in list_declared_source_business_support():
        declared_sources = set(support.source_ids)
        profile = resolve_projection_profile(support.family_id, support.business_id)
        if profile is None:
            missing_projection[f"{support.family_id}:{support.business_id}"] = sorted(declared_sources)
        else:
            constrained_sources = get_supported_source_ids_for_kind(profile.output_kind)
            projected_sources = (
                declared_sources
                if constrained_sources is None
                else set(constrained_sources)
            )
            missing = declared_sources - projected_sources
            if missing:
                missing_projection[f"{support.family_id}:{support.business_id}"] = sorted(missing)

        for surface in KNOWN_SURFACES:
            supported_sources = set(
                supported_sources_for_surface(
                    record_family=support.family_id,
                    business_id=support.business_id,
                    surface=surface,
                )
            )
            missing = declared_sources - supported_sources
            if missing:
                missing_surface[f"{support.family_id}:{support.business_id}:{surface}"] = sorted(missing)

    mismatches = {
        "missing_runtime": _format_scope_keys(missing_runtime),
        "missing_download_tasks": _format_scope_keys(missing_download_tasks),
        "missing_projection": missing_projection,
        "missing_surface": missing_surface,
    }
    assert mismatches == {
        "missing_runtime": [],
        "missing_download_tasks": [],
        "missing_projection": {},
        "missing_surface": {},
    }


def test_deal_runtime_manifests_align_with_source_business_contract() -> None:
    contract_by_task_id = {
        f"{item.source_id}:{item.record_family}:{item.business_id}": item
        for item in list_source_business_requirements()
        if item.record_family == "deal"
    }
    runtime_by_task_id = {
        binding.task_id: binding
        for binding in iter_source_business_bindings(record_family="deal")
        if binding.implemented
    }
    manifest_by_task_id = {
        task_id: spec.manifest
        for task_id, spec in build_task_registry().items()
        if spec.record_family == "deal" and spec.implemented
    }

    assert set(contract_by_task_id) == set(runtime_by_task_id) == set(manifest_by_task_id)
    assert "tpre:deal:deal_physical_asset" not in contract_by_task_id
    assert "cquae:deal:deal_physical_asset" not in contract_by_task_id

    for task_id, requirement in contract_by_task_id.items():
        binding = runtime_by_task_id[task_id]
        manifest = manifest_by_task_id[task_id]
        assert binding.manifest_list_endpoint == requirement.list_endpoint
        assert binding.manifest_detail_route == requirement.detail_route
        assert binding.manifest_render_page_route == requirement.render_page_route
        assert binding.manifest_detail_api_endpoint == requirement.detail_api_endpoint
        assert binding.manifest_transferee_details_endpoint == requirement.transferee_details_endpoint
        assert binding.manifest_date_field_candidates == requirement.date_field_candidates
        assert manifest.list_endpoint == requirement.list_endpoint
        assert manifest.detail_route == requirement.detail_route
        assert manifest.render_page_route == requirement.render_page_route
        assert manifest.detail_api_endpoint == requirement.detail_api_endpoint
        assert manifest.transferee_details_endpoint == requirement.transferee_details_endpoint
        assert manifest.date_field_candidates == requirement.date_field_candidates


def test_supported_deal_downloader_instances_resolve_metadata_from_source_business_contract(
    monkeypatch,
) -> None:
    import peap_core.source_business_contract as source_business_contract

    original_get_requirement = source_business_contract.get_source_business_requirement

    def fake_get_requirement(source_id: str, record_family: str, business_id: str):
        requirement = original_get_requirement(source_id, record_family, business_id)
        if record_family != "deal":
            return requirement
        prefix = f"/contract-test/{source_id}/{business_id}"
        return replace(
            requirement,
            list_endpoint=f"{prefix}/list",
            detail_route=f"{prefix}/detail",
            render_page_route=f"{prefix}/render",
            detail_api_endpoint=f"{prefix}/api",
            transferee_details_endpoint=f"{prefix}/transferees",
            date_field_candidates=(f"{business_id}_contract_date", "collection_date"),
            date_basis=f"{business_id}_contract_date",
        )

    monkeypatch.setattr(
        source_business_contract,
        "get_source_business_requirement",
        fake_get_requirement,
    )

    for binding in iter_source_business_bindings(record_family="deal"):
        requirement = fake_get_requirement(binding.source_id, binding.record_family, binding.business_id)
        downloader = binding.downloader_cls(html_root="/tmp/test")

        assert downloader.manifest_list_endpoint == requirement.list_endpoint
        assert downloader.manifest_detail_route == requirement.detail_route
        assert downloader.manifest_render_page_route == requirement.render_page_route
        assert downloader.manifest_detail_api_endpoint == requirement.detail_api_endpoint
        assert downloader.manifest_transferee_details_endpoint == requirement.transferee_details_endpoint
        assert downloader.manifest_date_field_candidates == requirement.date_field_candidates

        query = getattr(downloader, "query", None)
        if binding.source_id == "cbex":
            assert query.list_path == requirement.list_endpoint
            assert query.detail_path_prefix == requirement.detail_route
        elif binding.source_id == "tpre":
            assert query.list_endpoint == requirement.list_endpoint
            assert query.render_page_route == requirement.render_page_route
            assert query.detail_api_endpoint == requirement.detail_api_endpoint
            assert query.transferee_details_endpoint == requirement.transferee_details_endpoint
            assert query.preferred_date_field == requirement.date_basis
        elif binding.source_id == "cquae":
            assert query.list_path == requirement.list_endpoint
        elif binding.source_id == "sse":
            assert downloader._list_api_url().endswith(requirement.list_endpoint)
            assert downloader._detail_api_url().endswith(requirement.detail_route)


def test_deal_export_workbook_surface_and_sheet_specs_align_with_source_business_contract() -> None:
    support_by_business = {
        business_id: tuple(
            item
            for item in list_export_workbook_support(record_family="deal", business_id=business_id)
        )
        for business_id in (
            "deal_physical_asset",
            "deal_equity_transfer",
            "deal_capital_increase",
        )
    }

    for business_id, declarations in support_by_business.items():
        profile = resolve_projection_profile("deal", business_id)
        assert profile is not None
        supported = tuple(item for item in declarations if item.supported)
        unsupported = tuple(item for item in declarations if not item.supported)
        expected_sources = tuple(item.source_id for item in supported)
        expected_sheets = {
            item.source_id: item.sheet_name
            for item in supported
        }

        specs = list_deal_workbook_sheet_specs(profile.output_kind)
        assert tuple(spec.source_id for spec in specs) == expected_sources
        assert {spec.source_id: spec.sheet_name for spec in specs} == expected_sheets
        assert tuple(get_supported_source_ids_for_kind(profile.output_kind) or ()) == expected_sources
        assert supported_sources_for_surface(
            record_family="deal",
            business_id=business_id,
            surface="export",
        ) == expected_sources

        for item in unsupported:
            assert item.unsupported_reason
            assert item.source_id not in expected_sources

    unsupported_physical = {
        item.source_id: item.unsupported_reason
        for item in support_by_business["deal_physical_asset"]
        if not item.supported
    }
    assert unsupported_physical == {
        "tpre": "source_has_no_deal_physical_workbook_sheet",
        "cquae": "source_has_no_deal_physical_workbook_sheet",
    }


def test_declared_deal_capital_readiness_requirement_aligns_with_export_projection() -> None:
    requirement = get_export_readiness_requirement("deal", "deal_capital_increase")
    assert requirement.requires_non_summary_investor
    assert requirement.requires_investor_amount

    _payload, findings = project_canonical_record_to_export_payload(
        _deal_record(
            "deal_capital_increase",
            export_extras={"investors": [{"name": "总计", "amount": "1200"}]},
        ),
        fail_on_missing=False,
    )
    assert len(findings) == 1
    assert findings[0].evidence["missing_fields"] == ["investors"]

    _payload, findings = project_canonical_record_to_export_payload(
        _deal_record(
            "deal_capital_increase",
            export_extras={"investors": [{"name": "投资方甲", "amount": ""}]},
        ),
        fail_on_missing=False,
    )
    assert len(findings) == 1
    assert findings[0].evidence["missing_fields"] == ["investors[0].投资金额（万元）"]


def test_declared_physical_asset_scrap_filter_aligns_with_default_postprocess_config(tmp_path) -> None:
    requirement = get_optional_postprocess_rule_requirement(
        "listing",
        "physical_asset",
        "R010_filter_scrap_physical_asset",
    )

    template_path = Path("peap_postprocess/ppe_config/postprocess_external_template.json")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    audit_dir = tmp_path / "audit"
    input_dir.mkdir()
    payload.update(
        {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "audit_dir": str(audit_dir),
            "exclude_dirs": [str(output_dir)],
        }
    )
    config_path = tmp_path / "postprocess_external_template.json"
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    config = load_config(str(config_path))
    rule = config.rules[requirement.rule_id]

    assert rule.enabled
    assert tuple(rule.record_families) == ("listing",)
    assert rule.params["active"] is True
    assert requirement.optional
    assert requirement.listing_only
