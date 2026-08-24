from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from peap.business_runtime import SourceBusinessBinding
from peap.download_capabilities import DownloadDriverCapabilities, DownloadTaskManifest
from peap.download_tasks import (
    DownloadTaskRegistrySettings,
    DownloadTaskSpec,
    build_download_task_registry_settings,
    build_task_registry,
)


class _FakeDownloader:
    pass


class _DealDownloader:
    manifest_list_endpoint = "/mock/deal/list"
    manifest_detail_route = "/mock/deal/detail"
    manifest_render_page_route = "/mock/deal/render"
    manifest_detail_api_endpoint = "/mock/deal/api"
    manifest_transferee_details_endpoint = "/mock/deal/transferees"
    manifest_date_field_candidates = ("deal_disclosure_start",)


def test_build_task_registry_exposes_manifest_and_capabilities_for_sse_physical_asset() -> None:
    spec = build_task_registry()["sse:listing:physical_asset"]

    assert isinstance(spec.manifest, DownloadTaskManifest)
    assert isinstance(spec.capabilities, DownloadDriverCapabilities)
    assert spec.capabilities.supports_list_only is True
    assert spec.capabilities.supports_prefetched_candidates is True
    assert spec.manifest.task_id == "sse:listing:physical_asset"
    assert spec.manifest.source_id == "sse"
    assert spec.manifest.record_family == "listing"
    assert spec.manifest.business_id == "physical_asset"
    assert isinstance(spec.manifest.list_endpoint, str)
    assert spec.manifest.list_endpoint
    assert isinstance(spec.manifest.detail_route, str)
    assert spec.manifest.detail_route
    assert len(spec.manifest.date_field_candidates) > 0


def test_download_task_spec_defaults_match_current_registry_capabilities() -> None:
    spec = DownloadTaskSpec(
        exchange_code="sse",
        record_family="listing",
        business_id="physical_asset",
        display_name="SSE Physical",
        progress_label="挂牌实物资产",
        downloader_cls=_FakeDownloader,
        default_page_size=20,
    )

    assert spec.capabilities.supports_list_only is True
    assert spec.capabilities.supports_prefetched_candidates is True
    assert spec.manifest.source_id == "sse"
    assert spec.manifest.task_id == "sse:listing:physical_asset"


def test_build_task_registry_uses_cbex_equity_transfer_subtype_route_for_manifest_surface() -> None:
    spec = build_task_registry()["cbex:listing:equity_transfer"]

    assert spec.manifest.detail_route == "/xm/cqzr/"


def test_build_task_registry_exposes_listing_and_deal_task_types_for_each_exchange() -> None:
    registry = build_task_registry()
    by_exchange_family: dict[str, dict[str, set[str]]] = {}
    for _task_id, spec in registry.items():
        family_map = by_exchange_family.setdefault(spec.exchange_code, {})
        family_map.setdefault(spec.record_family, set()).add(spec.business_id)

    expected_listing = {"physical_asset", "equity_transfer", "capital_increase", "pre_disclosure"}
    expected_deal_by_exchange = {
        "sse": {"deal_physical_asset", "deal_equity_transfer", "deal_capital_increase"},
        "cbex": {"deal_physical_asset", "deal_equity_transfer", "deal_capital_increase"},
        "tpre": {"deal_equity_transfer", "deal_capital_increase"},
        "cquae": {"deal_equity_transfer", "deal_capital_increase"},
    }
    for exchange_code in ("sse", "cbex", "tpre", "cquae"):
        assert by_exchange_family[exchange_code]["listing"] == expected_listing
        assert by_exchange_family[exchange_code]["deal"] == expected_deal_by_exchange[exchange_code]
    assert by_exchange_family["shandong"]["listing"] == {"equity_transfer", "capital_increase"}
    assert by_exchange_family["guangdong"]["listing"] == {"equity_transfer", "capital_increase"}
    assert by_exchange_family["shenzhen"]["listing"] == {"equity_transfer", "capital_increase"}
    assert "deal" not in by_exchange_family["shandong"]
    assert "deal" not in by_exchange_family["guangdong"]
    assert "deal" not in by_exchange_family["shenzhen"]
    assert "public_resource" not in by_exchange_family
    assert len(registry) == 32


def test_build_task_registry_exposes_only_supported_deal_task_manifests() -> None:
    registry = build_task_registry()
    expected_manifest_surface = {
        "sse:deal:deal_equity_transfer": (
            "/si/notice/getDealNoticeList",
            "/si/notice/getNoticeDetail",
            "/si/notice/getNoticeDetail",
            "",
            "",
        ),
        "sse:deal:deal_physical_asset": (
            "/si/notice/getDealNoticeList",
            "/si/notice/getNoticeDetail",
            "/si/notice/getNoticeDetail",
            "",
            "",
        ),
        "sse:deal:deal_capital_increase": (
            "/si/notice/getDealNoticeList",
            "/si/notice/getNoticeDetail",
            "/si/notice/getNoticeDetail",
            "",
            "",
        ),
        "cbex:deal:deal_equity_transfer": ("/xm/cqzr/cjjggs/", "/xm/cqzr/", "/xm/cqzr/", "", ""),
        "cbex:deal:deal_physical_asset": ("/xm/zczr/cjjggs/", "/xm/zczr/", "/xm/zczr/", "", ""),
        "cbex:deal:deal_capital_increase": ("/xm/qyzz/cjjggs/", "/xm/qyzz/", "/xm/qyzz/", "", ""),
        "tpre:deal:deal_equity_transfer": (
            "/transaction/biz/transaction-management/anmuas/result-notice/page?bizType=PROPERTY_RIGHT_TRANSFER",
            "/transaction-view/data/common/transaction-announcement",
            "/transaction-view/data/common/transaction-announcement",
            "",
            "",
        ),
        "tpre:deal:deal_capital_increase": (
            "/transaction/biz/increase/transaction/anmuas/result-notice/page?bizType=ENTERPRISE_CAPITAL_INCREASE",
            "/transaction-view/data/common/transaction-announcement",
            "/transaction-view/data/common/transaction-announcement",
            "",
            "/transaction/biz/increase/transaction/transferee/anmuas/result-notice/details",
        ),
        "cquae:deal:deal_equity_transfer": ("/CquaeNews/cjgs/List.cshtml", "/CquaeNews/cjgs/", "/CquaeNews/cjgs/", "", ""),
        "cquae:deal:deal_capital_increase": ("/CquaeNews/cjgs/List.cshtml?type=1", "/CquaeNews/cjgs/", "/CquaeNews/cjgs/", "", ""),
    }
    deal_task_ids = {task_id for task_id, spec in registry.items() if spec.record_family == "deal"}

    assert deal_task_ids == set(expected_manifest_surface)
    for task_id, (
        list_endpoint,
        detail_route,
        render_page_route,
        detail_api_endpoint,
        transferee_details_endpoint,
    ) in expected_manifest_surface.items():
        spec = registry[task_id]
        assert spec.manifest.task_id == task_id
        assert spec.manifest.list_endpoint == list_endpoint
        assert spec.manifest.detail_route == detail_route
        assert spec.manifest.render_page_route == render_page_route
        assert spec.manifest.detail_api_endpoint == detail_api_endpoint
        assert spec.manifest.transferee_details_endpoint == transferee_details_endpoint
        assert len(spec.manifest.date_field_candidates) > 0
        assert spec.default_page_size > 0


def test_deal_task_runtime_capabilities_are_executable_after_real_downloaders_are_registered() -> None:
    registry = build_task_registry()
    deal_specs = [spec for spec in registry.values() if spec.record_family == "deal"]
    listing_specs = [spec for spec in registry.values() if spec.record_family == "listing"]
    deal_specs_by_task_id = {spec.task_id: spec for spec in deal_specs}

    assert len(deal_specs) == 10
    assert "tpre:deal:deal_physical_asset" not in deal_specs_by_task_id
    assert "cquae:deal:deal_physical_asset" not in deal_specs_by_task_id
    assert all(spec.implemented is True for spec in deal_specs)
    assert all(spec.capabilities.supports_list_only is True for spec in deal_specs)
    assert all(spec.capabilities.supports_prefetched_candidates is True for spec in deal_specs)
    assert all(spec.capabilities.supports_list_only is True for spec in listing_specs)
    assert all(spec.capabilities.supports_prefetched_candidates is True for spec in listing_specs)


def test_build_task_registry_includes_non_listing_runtime_bindings_when_registry_expands() -> None:
    listing_binding = SourceBusinessBinding(
        source_id="mock",
        record_family="listing",
        business_id="physical_asset",
        downloader_cls=_FakeDownloader,
        progress_label="挂牌实物资产",
    )
    deal_binding = SourceBusinessBinding(
        source_id="mock",
        record_family="deal",
        business_id="deal_asset",
        downloader_cls=_DealDownloader,
        progress_label="成交资产",
        manifest_list_endpoint="/mock/deal/list",
        manifest_detail_route="/mock/deal/detail",
        manifest_date_field_candidates=("deal_disclosure_start",),
    )

    def fake_iter_source_business_bindings(*, source_id=None, record_family=None):
        for binding in (listing_binding, deal_binding):
            if source_id and binding.source_id != source_id:
                continue
            if record_family and record_family not in {"all", binding.record_family}:
                continue
            yield binding

    with patch(
        "peap.download_tasks.iter_source_business_bindings",
        side_effect=fake_iter_source_business_bindings,
    ), patch.object(
        SourceBusinessBinding,
        "display_name",
        new_callable=PropertyMock,
        return_value="Mock Display",
    ):
        registry = build_task_registry(
            settings=DownloadTaskRegistrySettings(
                task_page_size={
                    "mock:listing:physical_asset": 20,
                    "mock:deal:deal_asset": 7,
                }
            )
    )

    assert "mock:deal:deal_asset" in registry
    assert registry["mock:deal:deal_asset"].manifest.detail_route == "/mock/deal/detail"
    assert registry["mock:deal:deal_asset"].manifest.render_page_route == "/mock/deal/render"
    assert registry["mock:deal:deal_asset"].manifest.detail_api_endpoint == "/mock/deal/api"
    assert registry["mock:deal:deal_asset"].manifest.transferee_details_endpoint == "/mock/deal/transferees"


def test_two_segment_page_size_keys_no_longer_alias_into_runtime_task_ids() -> None:
    config = SimpleNamespace(
        DOWNLOADER_TASK_PAGE_SIZE={
            "sse:physical_asset": 20,
            "cbex:listing:physical_asset": 16,
            "sse:listing:equity_transfer": 20,
            "sse:listing:capital_increase": 20,
            "sse:listing:pre_disclosure": 20,
            "cbex:listing:equity_transfer": 15,
            "cbex:listing:capital_increase": 15,
            "cbex:listing:pre_disclosure": 15,
            "tpre:listing:physical_asset": 20,
            "tpre:listing:equity_transfer": 20,
            "tpre:listing:capital_increase": 20,
            "tpre:listing:pre_disclosure": 20,
            "cquae:listing:physical_asset": 10,
            "cquae:listing:equity_transfer": 10,
            "cquae:listing:capital_increase": 10,
            "cquae:listing:pre_disclosure": 10,
            "shandong:listing:equity_transfer": 20,
            "shandong:listing:capital_increase": 20,
            "guangdong:listing:equity_transfer": 20,
            "guangdong:listing:capital_increase": 20,
            "shenzhen:listing:equity_transfer": 20,
            "shenzhen:listing:capital_increase": 20,
        }
    )

    settings = build_download_task_registry_settings(config)

    assert "sse:physical_asset" in settings.task_page_size
    try:
        build_task_registry(settings=settings)
    except KeyError as exc:
        assert exc.args[0] == "sse:listing:physical_asset"
    else:
        raise AssertionError("legacy two-segment task ids should fail explicitly")


@pytest.mark.parametrize("raw_page_size", [None, []])
def test_registry_settings_rejects_explicit_invalid_page_size_mapping(raw_page_size: object) -> None:
    config = SimpleNamespace(DOWNLOADER_TASK_PAGE_SIZE=raw_page_size)

    with pytest.raises(TypeError, match="DOWNLOADER_TASK_PAGE_SIZE"):
        build_download_task_registry_settings(config)


def test_registry_settings_requires_downloader_task_page_size_config_contract() -> None:
    with pytest.raises(AttributeError, match="DOWNLOADER_TASK_PAGE_SIZE"):
        build_download_task_registry_settings(SimpleNamespace())


def test_registry_settings_backfill_deal_page_sizes_from_listing_only_config() -> None:
    config = SimpleNamespace(
        DOWNLOADER_TASK_PAGE_SIZE={
            "sse:listing:physical_asset": 20,
            "cbex:listing:physical_asset": 16,
            "sse:listing:equity_transfer": 21,
            "sse:listing:capital_increase": 22,
            "sse:listing:pre_disclosure": 20,
            "cbex:listing:equity_transfer": 17,
            "cbex:listing:capital_increase": 18,
            "cbex:listing:pre_disclosure": 15,
            "tpre:listing:physical_asset": 23,
            "tpre:listing:equity_transfer": 24,
            "tpre:listing:capital_increase": 25,
            "tpre:listing:pre_disclosure": 20,
            "cquae:listing:physical_asset": 11,
            "cquae:listing:equity_transfer": 12,
            "cquae:listing:capital_increase": 13,
            "cquae:listing:pre_disclosure": 10,
            "shandong:listing:equity_transfer": 20,
            "shandong:listing:capital_increase": 20,
            "guangdong:listing:equity_transfer": 20,
            "guangdong:listing:capital_increase": 20,
            "shenzhen:listing:equity_transfer": 20,
            "shenzhen:listing:capital_increase": 20,
        }
    )

    settings = build_download_task_registry_settings(config)
    registry = build_task_registry(settings=settings)

    assert settings.task_page_size["sse:deal:deal_equity_transfer"] == 21
    assert settings.task_page_size["sse:deal:deal_capital_increase"] == 22
    assert settings.task_page_size["cbex:deal:deal_physical_asset"] == 16
    assert settings.task_page_size["tpre:deal:deal_equity_transfer"] == 24
    assert settings.task_page_size["cquae:deal:deal_capital_increase"] == 13
    assert registry["sse:deal:deal_equity_transfer"].default_page_size == 21
