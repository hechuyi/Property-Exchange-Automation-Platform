"""Downloader task registry built from runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Type

from peap_core.source_catalog import get_source_descriptor, list_source_descriptors

from .download_capabilities import DownloadDriverCapabilities, DownloadTaskManifest
from .download_models import DownloadCandidateEntry
from .downloaders import (
    CbexCapitalIncreaseDownloader,
    CbexEquityTransferDownloader,
    CbexPhysicalAssetDownloader,
    CbexPreDisclosureDownloader,
    ChongqingCapitalIncreaseDownloader,
    ChongqingEquityTransferDownloader,
    ChongqingPhysicalAssetDownloader,
    ChongqingPreDisclosureDownloader,
    ShanghaiCapitalIncreaseDownloader,
    ShanghaiEquityTransferDownloader,
    ShanghaiPhysicalAssetDownloader,
    ShanghaiPreDisclosureDownloader,
    TianjinCapitalIncreaseDownloader,
    TianjinEquityTransferDownloader,
    TianjinPhysicalAssetDownloader,
    TianjinPreDisclosureDownloader,
)


@dataclass(frozen=True)
class DownloadTaskSpec:
    exchange_code: str
    project_type: str
    display_name: str
    downloader_cls: Type
    default_page_size: int
    manifest_list_endpoint: str | None = None
    manifest_detail_route: str | None = None
    manifest_date_field_candidates: tuple[str, ...] | None = None
    manifest: DownloadTaskManifest = field(init=False)
    capabilities: DownloadDriverCapabilities = field(
        default_factory=lambda: DownloadDriverCapabilities(
            supports_list_only=True,
            supports_prefetched_candidates=True,
        ),
    )

    def __post_init__(self) -> None:
        list_endpoint = self.manifest_list_endpoint
        if list_endpoint is None:
            list_endpoint = str(getattr(self.downloader_cls, "manifest_list_endpoint", "") or "")

        detail_route = self.manifest_detail_route
        if detail_route is None:
            detail_route = str(getattr(self.downloader_cls, "manifest_detail_route", "") or "")

        date_field_candidates = self.manifest_date_field_candidates
        if date_field_candidates is None:
            date_field_candidates = tuple(
                str(value)
                for value in getattr(self.downloader_cls, "manifest_date_field_candidates", ())
                if str(value)
            )

        object.__setattr__(
            self,
            "manifest",
            DownloadTaskManifest(
                source_id=self.exchange_code,
                project_type=self.project_type,
                task_id=self.task_id,
                display_name=self.display_name,
                list_endpoint=str(list_endpoint or ""),
                detail_route=str(detail_route or ""),
                date_field_candidates=tuple(str(value) for value in date_field_candidates if str(value)),
            ),
        )

    @property
    def task_id(self) -> str:
        return f"{self.exchange_code}:{self.project_type}"


PROJECT_TYPE_CHOICES = [
    "physical_asset",
    "equity_transfer",
    "capital_increase",
    "pre_disclosure",
]

PROJECT_TYPE_DISPLAY_NAMES = {
    "physical_asset": "Physical Asset",
    "equity_transfer": "Equity Transfer",
    "capital_increase": "Capital Increase",
    "pre_disclosure": "Pre Disclosure",
}


@dataclass(frozen=True)
class DownloadTaskRegistrySettings:
    task_page_size: Dict[str, int]


def build_download_task_registry_settings(config_obj: object) -> DownloadTaskRegistrySettings:
    return DownloadTaskRegistrySettings(
        task_page_size=dict(getattr(config_obj, "DOWNLOADER_TASK_PAGE_SIZE", {})),
    )


def _load_default_download_task_registry_settings() -> DownloadTaskRegistrySettings:
    try:
        from config import config as default_config
    except Exception:
        return DownloadTaskRegistrySettings(
            task_page_size={
                "sse:physical_asset": 20,
                "cbex:physical_asset": 16,
                "sse:equity_transfer": 20,
                "sse:capital_increase": 20,
                "sse:pre_disclosure": 20,
                "cbex:equity_transfer": 15,
                "cbex:capital_increase": 15,
                "cbex:pre_disclosure": 15,
                "tpre:physical_asset": 20,
                "tpre:equity_transfer": 20,
                "tpre:capital_increase": 20,
                "tpre:pre_disclosure": 20,
                "cquae:physical_asset": 10,
                "cquae:equity_transfer": 10,
                "cquae:capital_increase": 10,
                "cquae:pre_disclosure": 10,
            }
        )

    return build_download_task_registry_settings(default_config)


_DEFAULT_DOWNLOAD_TASK_REGISTRY_SETTINGS = _load_default_download_task_registry_settings()


def get_default_download_task_registry_settings() -> DownloadTaskRegistrySettings:
    return _DEFAULT_DOWNLOAD_TASK_REGISTRY_SETTINGS


def set_default_download_task_registry_settings(
    settings: DownloadTaskRegistrySettings | None,
) -> DownloadTaskRegistrySettings:
    global _DEFAULT_DOWNLOAD_TASK_REGISTRY_SETTINGS
    _DEFAULT_DOWNLOAD_TASK_REGISTRY_SETTINGS = settings or DownloadTaskRegistrySettings(task_page_size={})
    return _DEFAULT_DOWNLOAD_TASK_REGISTRY_SETTINGS


def _resolve_task_registry_settings(
    settings: DownloadTaskRegistrySettings | None = None,
    *,
    config_obj: object | None = None,
) -> DownloadTaskRegistrySettings:
    if settings is not None:
        return settings
    if config_obj is not None:
        return build_download_task_registry_settings(config_obj)
    return get_default_download_task_registry_settings()


_TASK_BINDINGS: tuple[tuple[str, str, Type], ...] = (
    ("sse", "physical_asset", ShanghaiPhysicalAssetDownloader),
    ("sse", "equity_transfer", ShanghaiEquityTransferDownloader),
    ("sse", "capital_increase", ShanghaiCapitalIncreaseDownloader),
    ("sse", "pre_disclosure", ShanghaiPreDisclosureDownloader),
    ("cbex", "physical_asset", CbexPhysicalAssetDownloader),
    ("cbex", "equity_transfer", CbexEquityTransferDownloader),
    ("cbex", "capital_increase", CbexCapitalIncreaseDownloader),
    ("cbex", "pre_disclosure", CbexPreDisclosureDownloader),
    ("tpre", "physical_asset", TianjinPhysicalAssetDownloader),
    ("tpre", "equity_transfer", TianjinEquityTransferDownloader),
    ("tpre", "capital_increase", TianjinCapitalIncreaseDownloader),
    ("tpre", "pre_disclosure", TianjinPreDisclosureDownloader),
    ("cquae", "physical_asset", ChongqingPhysicalAssetDownloader),
    ("cquae", "equity_transfer", ChongqingEquityTransferDownloader),
    ("cquae", "capital_increase", ChongqingCapitalIncreaseDownloader),
    ("cquae", "pre_disclosure", ChongqingPreDisclosureDownloader),
)


_TASK_MANIFEST_OVERRIDES: dict[str, dict[str, str]] = {
    "sse:equity_transfer": {"detail_route": "jymhchanquan"},
    "sse:capital_increase": {"detail_route": "jymhzengzi"},
    "sse:pre_disclosure": {"detail_route": "jymhchanquanyu"},
    "cbex:equity_transfer": {"detail_route": "/xm/cqzr/"},
    "cbex:capital_increase": {"detail_route": "/xm/qyzz/"},
}


def _manifest_overrides(task_id: str) -> dict[str, str]:
    return _TASK_MANIFEST_OVERRIDES.get(task_id, {})


def _task_display_name(exchange_code: str, project_type: str) -> str:
    source = get_source_descriptor(exchange_code)
    return f"{source.site_label} - {PROJECT_TYPE_DISPLAY_NAMES[project_type]}"


def build_task_registry(
    config_obj: object | None = None,
    *,
    settings: DownloadTaskRegistrySettings | None = None,
) -> Dict[str, DownloadTaskSpec]:
    page_size = _resolve_task_registry_settings(settings, config_obj=config_obj).task_page_size
    registry: Dict[str, DownloadTaskSpec] = {}
    for exchange_code, project_type, downloader_cls in _TASK_BINDINGS:
        task_id = f"{exchange_code}:{project_type}"
        manifest_overrides = _manifest_overrides(task_id)
        registry[task_id] = DownloadTaskSpec(
            exchange_code=exchange_code,
            project_type=project_type,
            display_name=_task_display_name(exchange_code, project_type),
            downloader_cls=downloader_cls,
            default_page_size=page_size[task_id],
            manifest_list_endpoint=manifest_overrides.get("list_endpoint"),
            manifest_detail_route=manifest_overrides.get("detail_route"),
            capabilities=DownloadDriverCapabilities(
                supports_list_only=True,
                supports_prefetched_candidates=True,
            ),
        )
    return registry


def exchange_choices(
    config_obj: object | None = None,
    *,
    settings: DownloadTaskRegistrySettings | None = None,
) -> List[str]:
    _ = _resolve_task_registry_settings(settings, config_obj=config_obj)
    return sorted(
        descriptor.source_id
        for descriptor in list_source_descriptors(record_family="listing")
    )
