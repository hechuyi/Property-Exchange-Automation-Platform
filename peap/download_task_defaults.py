"""Shared defaults for downloader task runtime configuration."""

from __future__ import annotations

from typing import Dict

DEFAULT_DOWNLOAD_TASK_PAGE_SIZE: Dict[str, int] = {
    "sse:listing:physical_asset": 20,
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
    "sse:deal:deal_physical_asset": 20,
    "sse:deal:deal_equity_transfer": 20,
    "sse:deal:deal_capital_increase": 20,
    "cbex:deal:deal_physical_asset": 15,
    "cbex:deal:deal_equity_transfer": 15,
    "cbex:deal:deal_capital_increase": 15,
    "tpre:deal:deal_equity_transfer": 20,
    "tpre:deal:deal_capital_increase": 20,
    "cquae:deal:deal_equity_transfer": 10,
    "cquae:deal:deal_capital_increase": 10,
}


def task_page_size_defaults() -> Dict[str, int]:
    return dict(DEFAULT_DOWNLOAD_TASK_PAGE_SIZE)


__all__ = ["DEFAULT_DOWNLOAD_TASK_PAGE_SIZE", "task_page_size_defaults"]
