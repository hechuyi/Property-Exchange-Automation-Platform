"""Downloader task registry built from runtime bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Dict, List, Type

from peap.business_runtime import (
    iter_source_business_bindings,
    task_page_size_key,
)
from peap_core.business_catalog import get_business_descriptor

from .download_capabilities import DownloadDriverCapabilities, DownloadTaskManifest
from .download_task_defaults import task_page_size_defaults


@dataclass(frozen=True)
class DownloadTaskSpec:
    exchange_code: str
    record_family: str
    business_id: str
    display_name: str
    progress_label: str
    downloader_cls: Type
    default_page_size: int
    implemented: bool = True
    manifest_list_endpoint: str | None = None
    manifest_detail_route: str | None = None
    manifest_render_page_route: str | None = None
    manifest_detail_api_endpoint: str | None = None
    manifest_transferee_details_endpoint: str | None = None
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

        render_page_route = self.manifest_render_page_route
        if render_page_route is None:
            class_render_page_route = getattr(self.downloader_cls, "manifest_render_page_route", None)
            if class_render_page_route is None:
                render_page_route = str(detail_route or "")
            else:
                render_page_route = str(class_render_page_route or "")

        detail_api_endpoint = self.manifest_detail_api_endpoint
        if detail_api_endpoint is None:
            class_detail_api_endpoint = getattr(self.downloader_cls, "manifest_detail_api_endpoint", None)
            detail_api_endpoint = str(class_detail_api_endpoint or "")

        transferee_details_endpoint = self.manifest_transferee_details_endpoint
        if transferee_details_endpoint is None:
            class_transferee_details_endpoint = getattr(
                self.downloader_cls,
                "manifest_transferee_details_endpoint",
                None,
            )
            transferee_details_endpoint = str(class_transferee_details_endpoint or "")

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
                record_family=self.record_family,
                business_id=self.business_id,
                task_id=self.task_id,
                display_name=self.display_name,
                list_endpoint=str(list_endpoint or ""),
                detail_route=str(detail_route or ""),
                render_page_route=str(render_page_route or ""),
                detail_api_endpoint=str(detail_api_endpoint or ""),
                transferee_details_endpoint=str(transferee_details_endpoint or ""),
                date_field_candidates=tuple(str(value) for value in date_field_candidates if str(value)),
            ),
        )

    @property
    def task_id(self) -> str:
        return f"{self.exchange_code}:{self.record_family}:{self.business_id}"


@dataclass(frozen=True)
class DownloadTaskRegistrySettings:
    task_page_size: Dict[str, int]


def _normalize_task_page_size_keys(raw_mapping: object) -> Dict[str, int]:
    if not isinstance(raw_mapping, Mapping):
        raise TypeError("DOWNLOADER_TASK_PAGE_SIZE must be a mapping")
    mapping = dict(raw_mapping)
    normalized: Dict[str, int] = {}
    for key, value in mapping.items():
        task_key = str(key or "").strip()
        if not task_key:
            continue
        normalized[task_key] = int(value)
    return normalized


def _apply_deal_task_page_size_fallbacks(page_size: Dict[str, int]) -> Dict[str, int]:
    normalized = dict(page_size)
    listing_business_by_deal_business = {
        "deal_physical_asset": "physical_asset",
        "deal_equity_transfer": "equity_transfer",
        "deal_capital_increase": "capital_increase",
    }
    for binding in iter_source_business_bindings(record_family="deal"):
        listing_business_id = listing_business_by_deal_business.get(binding.business_id)
        if not listing_business_id:
            continue
        task_id = task_page_size_key(binding)
        fallback_task_id = f"{binding.source_id}:listing:{listing_business_id}"
        if task_id not in normalized and fallback_task_id in normalized:
            normalized[task_id] = normalized[fallback_task_id]
    return normalized


def build_download_task_registry_settings(config_obj: object) -> DownloadTaskRegistrySettings:
    return DownloadTaskRegistrySettings(
        task_page_size=_apply_deal_task_page_size_fallbacks(
            _normalize_task_page_size_keys(
                config_obj.DOWNLOADER_TASK_PAGE_SIZE
            )
        ),
    )


def _load_default_download_task_registry_settings() -> DownloadTaskRegistrySettings:
    try:
        from config import config as default_config
    except Exception:
        return DownloadTaskRegistrySettings(
            task_page_size=task_page_size_defaults()
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
    registry: Dict[str, DownloadTaskSpec] = {}
    for binding in iter_source_business_bindings():
        task_id = task_page_size_key(binding)
        if task_id not in page_size:
            raise KeyError(task_id)
        registry[task_id] = DownloadTaskSpec(
            exchange_code=binding.source_id,
            record_family=binding.record_family,
            business_id=binding.business_id,
            display_name=binding.display_name,
            progress_label=binding.progress_label,
            downloader_cls=binding.downloader_cls,
            default_page_size=page_size[task_id],
            implemented=binding.implemented,
            manifest_list_endpoint=binding.manifest_list_endpoint,
            manifest_detail_route=binding.manifest_detail_route,
            manifest_render_page_route=binding.manifest_render_page_route,
            manifest_detail_api_endpoint=binding.manifest_detail_api_endpoint,
            manifest_transferee_details_endpoint=binding.manifest_transferee_details_endpoint,
            manifest_date_field_candidates=binding.manifest_date_field_candidates,
            capabilities=DownloadDriverCapabilities(
                supports_list_only=binding.implemented,
                supports_prefetched_candidates=binding.implemented,
            ),
        )
    return registry


def exchange_choices(
    config_obj: object | None = None,
    *,
    record_family: str | None = None,
    settings: DownloadTaskRegistrySettings | None = None,
) -> List[str]:
    _ = _resolve_task_registry_settings(settings, config_obj=config_obj)
    family_filter = None if str(record_family or "").strip() in {"", "all"} else str(record_family)
    return sorted({binding.source_id for binding in iter_source_business_bindings(record_family=family_filter)})


def business_choices(*, record_family: str | None = None) -> List[str]:
    family_filter = None if str(record_family or "").strip() in {"", "all"} else str(record_family)
    return sorted({binding.business_id for binding in iter_source_business_bindings(record_family=family_filter)})


def business_display_name(business_id: str, *, record_family: str = "listing") -> str:
    return get_business_descriptor(business_id, family_id=record_family).canonical_label
