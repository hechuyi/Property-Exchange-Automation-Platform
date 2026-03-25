"""Downloader task registry built from runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Type

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

    @property
    def task_id(self) -> str:
        return f"{self.exchange_code}:{self.project_type}"


PROJECT_TYPE_CHOICES = [
    "physical_asset",
    "equity_transfer",
    "capital_increase",
    "pre_disclosure",
]


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


def build_task_registry(
    config_obj: object | None = None,
    *,
    settings: DownloadTaskRegistrySettings | None = None,
) -> Dict[str, DownloadTaskSpec]:
    page_size = _resolve_task_registry_settings(settings, config_obj=config_obj).task_page_size
    return {
        "sse:physical_asset": DownloadTaskSpec(
            exchange_code="sse",
            project_type="physical_asset",
            display_name="Shanghai (SSE) - Physical Asset",
            downloader_cls=ShanghaiPhysicalAssetDownloader,
            default_page_size=page_size["sse:physical_asset"],
        ),
        "cbex:physical_asset": DownloadTaskSpec(
            exchange_code="cbex",
            project_type="physical_asset",
            display_name="Beijing (CBEX) - Physical Asset",
            downloader_cls=CbexPhysicalAssetDownloader,
            default_page_size=page_size["cbex:physical_asset"],
        ),
        "sse:equity_transfer": DownloadTaskSpec(
            exchange_code="sse",
            project_type="equity_transfer",
            display_name="Shanghai (SSE) - Equity Transfer",
            downloader_cls=ShanghaiEquityTransferDownloader,
            default_page_size=page_size["sse:equity_transfer"],
        ),
        "sse:capital_increase": DownloadTaskSpec(
            exchange_code="sse",
            project_type="capital_increase",
            display_name="Shanghai (SSE) - Capital Increase",
            downloader_cls=ShanghaiCapitalIncreaseDownloader,
            default_page_size=page_size["sse:capital_increase"],
        ),
        "sse:pre_disclosure": DownloadTaskSpec(
            exchange_code="sse",
            project_type="pre_disclosure",
            display_name="Shanghai (SSE) - Pre Disclosure",
            downloader_cls=ShanghaiPreDisclosureDownloader,
            default_page_size=page_size["sse:pre_disclosure"],
        ),
        "cbex:equity_transfer": DownloadTaskSpec(
            exchange_code="cbex",
            project_type="equity_transfer",
            display_name="Beijing (CBEX) - Equity Transfer",
            downloader_cls=CbexEquityTransferDownloader,
            default_page_size=page_size["cbex:equity_transfer"],
        ),
        "cbex:capital_increase": DownloadTaskSpec(
            exchange_code="cbex",
            project_type="capital_increase",
            display_name="Beijing (CBEX) - Capital Increase",
            downloader_cls=CbexCapitalIncreaseDownloader,
            default_page_size=page_size["cbex:capital_increase"],
        ),
        "cbex:pre_disclosure": DownloadTaskSpec(
            exchange_code="cbex",
            project_type="pre_disclosure",
            display_name="Beijing (CBEX) - Pre Disclosure",
            downloader_cls=CbexPreDisclosureDownloader,
            default_page_size=page_size["cbex:pre_disclosure"],
        ),
        "tpre:physical_asset": DownloadTaskSpec(
            exchange_code="tpre",
            project_type="physical_asset",
            display_name="Tianjin (TPRE) - Physical Asset",
            downloader_cls=TianjinPhysicalAssetDownloader,
            default_page_size=page_size["tpre:physical_asset"],
        ),
        "tpre:equity_transfer": DownloadTaskSpec(
            exchange_code="tpre",
            project_type="equity_transfer",
            display_name="Tianjin (TPRE) - Equity Transfer",
            downloader_cls=TianjinEquityTransferDownloader,
            default_page_size=page_size["tpre:equity_transfer"],
        ),
        "tpre:capital_increase": DownloadTaskSpec(
            exchange_code="tpre",
            project_type="capital_increase",
            display_name="Tianjin (TPRE) - Capital Increase",
            downloader_cls=TianjinCapitalIncreaseDownloader,
            default_page_size=page_size["tpre:capital_increase"],
        ),
        "tpre:pre_disclosure": DownloadTaskSpec(
            exchange_code="tpre",
            project_type="pre_disclosure",
            display_name="Tianjin (TPRE) - Pre Disclosure",
            downloader_cls=TianjinPreDisclosureDownloader,
            default_page_size=page_size["tpre:pre_disclosure"],
        ),
        "cquae:physical_asset": DownloadTaskSpec(
            exchange_code="cquae",
            project_type="physical_asset",
            display_name="Chongqing (CQUAE) - Physical Asset",
            downloader_cls=ChongqingPhysicalAssetDownloader,
            default_page_size=page_size["cquae:physical_asset"],
        ),
        "cquae:equity_transfer": DownloadTaskSpec(
            exchange_code="cquae",
            project_type="equity_transfer",
            display_name="Chongqing (CQUAE) - Equity Transfer",
            downloader_cls=ChongqingEquityTransferDownloader,
            default_page_size=page_size["cquae:equity_transfer"],
        ),
        "cquae:capital_increase": DownloadTaskSpec(
            exchange_code="cquae",
            project_type="capital_increase",
            display_name="Chongqing (CQUAE) - Capital Increase",
            downloader_cls=ChongqingCapitalIncreaseDownloader,
            default_page_size=page_size["cquae:capital_increase"],
        ),
        "cquae:pre_disclosure": DownloadTaskSpec(
            exchange_code="cquae",
            project_type="pre_disclosure",
            display_name="Chongqing (CQUAE) - Pre Disclosure",
            downloader_cls=ChongqingPreDisclosureDownloader,
            default_page_size=page_size["cquae:pre_disclosure"],
        ),
    }


def exchange_choices(
    config_obj: object | None = None,
    *,
    settings: DownloadTaskRegistrySettings | None = None,
) -> List[str]:
    return sorted(
        {
            task.exchange_code
            for task in build_task_registry(config_obj, settings=settings).values()
        }
    )
