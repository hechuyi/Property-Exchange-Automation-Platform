"""Downloaders for collecting source pages before parsing."""

from .cbex_physical import (
    CbexCapitalIncreaseDownloader,
    CbexEquityTransferDownloader,
    CbexPhysicalAssetDownloader,
    CbexPreDisclosureDownloader,
)
from .common import DownloadSummary
from .cquae import (
    ChongqingCapitalIncreaseDownloader,
    ChongqingEquityTransferDownloader,
    ChongqingPhysicalAssetDownloader,
    ChongqingPreDisclosureDownloader,
)
from .sse_physical import (
    ShanghaiCapitalIncreaseDownloader,
    ShanghaiEquityTransferDownloader,
    ShanghaiPhysicalAssetDownloader,
    ShanghaiPreDisclosureDownloader,
)
from .tpre import (
    TianjinCapitalIncreaseDownloader,
    TianjinEquityTransferDownloader,
    TianjinPhysicalAssetDownloader,
    TianjinPreDisclosureDownloader,
)

__all__ = [
    "DownloadSummary",
    "ShanghaiPhysicalAssetDownloader",
    "ShanghaiEquityTransferDownloader",
    "ShanghaiCapitalIncreaseDownloader",
    "ShanghaiPreDisclosureDownloader",
    "CbexPhysicalAssetDownloader",
    "CbexEquityTransferDownloader",
    "CbexCapitalIncreaseDownloader",
    "CbexPreDisclosureDownloader",
    "TianjinPhysicalAssetDownloader",
    "TianjinEquityTransferDownloader",
    "TianjinCapitalIncreaseDownloader",
    "TianjinPreDisclosureDownloader",
    "ChongqingPhysicalAssetDownloader",
    "ChongqingEquityTransferDownloader",
    "ChongqingCapitalIncreaseDownloader",
    "ChongqingPreDisclosureDownloader",
]
