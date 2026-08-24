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
from .deal_cbex import (
    CbexDealCapitalIncreaseDownloader,
    CbexDealEquityTransferDownloader,
    CbexDealPhysicalAssetDownloader,
)
from .deal_cquae import (
    ChongqingDealCapitalIncreaseDownloader,
    ChongqingDealEquityTransferDownloader,
    ChongqingDealPhysicalAssetDownloader,
)
from .deal_sse import (
    ShanghaiDealCapitalIncreaseDownloader,
    ShanghaiDealEquityTransferDownloader,
    ShanghaiDealPhysicalAssetDownloader,
)
from .deal_tpre import (
    TianjinDealCapitalIncreaseDownloader,
    TianjinDealEquityTransferDownloader,
    TianjinDealPhysicalAssetDownloader,
)
from .listing_exchanges import (
    GuangdongCapitalIncreaseDownloader,
    GuangdongEquityTransferDownloader,
    ShandongCapitalIncreaseDownloader,
    ShandongEquityTransferDownloader,
    ShenzhenCapitalIncreaseDownloader,
    ShenzhenEquityTransferDownloader,
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
    "CbexDealPhysicalAssetDownloader",
    "CbexDealEquityTransferDownloader",
    "CbexDealCapitalIncreaseDownloader",
    "TianjinPhysicalAssetDownloader",
    "TianjinEquityTransferDownloader",
    "TianjinCapitalIncreaseDownloader",
    "TianjinPreDisclosureDownloader",
    "TianjinDealPhysicalAssetDownloader",
    "TianjinDealEquityTransferDownloader",
    "TianjinDealCapitalIncreaseDownloader",
    "ChongqingPhysicalAssetDownloader",
    "ChongqingEquityTransferDownloader",
    "ChongqingCapitalIncreaseDownloader",
    "ChongqingPreDisclosureDownloader",
    "ChongqingDealPhysicalAssetDownloader",
    "ChongqingDealEquityTransferDownloader",
    "ChongqingDealCapitalIncreaseDownloader",
    "ShanghaiDealPhysicalAssetDownloader",
    "ShanghaiDealEquityTransferDownloader",
    "ShanghaiDealCapitalIncreaseDownloader",
    "ShandongCapitalIncreaseDownloader",
    "ShandongEquityTransferDownloader",
    "GuangdongCapitalIncreaseDownloader",
    "GuangdongEquityTransferDownloader",
    "ShenzhenEquityTransferDownloader",
    "ShenzhenCapitalIncreaseDownloader",
]
