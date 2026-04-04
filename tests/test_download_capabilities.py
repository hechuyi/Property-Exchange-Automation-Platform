from peap.download_capabilities import DownloadDriverCapabilities, DownloadTaskManifest
from peap.download_tasks import DownloadTaskSpec, build_task_registry


class _FakeDownloader:
    pass


def test_build_task_registry_exposes_manifest_and_capabilities_for_sse_physical_asset() -> None:
    spec = build_task_registry()["sse:physical_asset"]

    assert isinstance(spec.manifest, DownloadTaskManifest)
    assert isinstance(spec.capabilities, DownloadDriverCapabilities)
    assert spec.capabilities.supports_list_only is True
    assert spec.capabilities.supports_prefetched_candidates is True
    assert spec.manifest.task_id == "sse:physical_asset"
    assert spec.manifest.source_id == "sse"
    assert isinstance(spec.manifest.list_endpoint, str)
    assert spec.manifest.list_endpoint
    assert isinstance(spec.manifest.detail_route, str)
    assert spec.manifest.detail_route
    assert len(spec.manifest.date_field_candidates) > 0


def test_download_task_spec_defaults_match_current_registry_capabilities() -> None:
    spec = DownloadTaskSpec(
        exchange_code="sse",
        project_type="physical_asset",
        display_name="SSE Physical",
        downloader_cls=_FakeDownloader,
        default_page_size=20,
    )

    assert spec.capabilities.supports_list_only is True
    assert spec.capabilities.supports_prefetched_candidates is True
    assert spec.manifest.source_id == "sse"
    assert spec.manifest.task_id == "sse:physical_asset"


def test_build_task_registry_uses_cbex_equity_transfer_subtype_route_for_manifest_surface() -> None:
    spec = build_task_registry()["cbex:equity_transfer"]

    assert spec.manifest.detail_route == "/xm/cqzr/"


def test_build_task_registry_exposes_four_task_types_for_each_exchange() -> None:
    registry = build_task_registry()
    by_exchange: dict[str, set[str]] = {}
    for task_id, spec in registry.items():
        by_exchange.setdefault(spec.exchange_code, set()).add(spec.project_type)

    expected = {"physical_asset", "equity_transfer", "capital_increase", "pre_disclosure"}
    assert by_exchange["sse"] == expected
    assert by_exchange["cbex"] == expected
    assert by_exchange["tpre"] == expected
    assert by_exchange["cquae"] == expected
    assert len(registry) == 16
