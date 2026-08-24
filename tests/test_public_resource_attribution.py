from __future__ import annotations

import pytest

from peap.public_resource_attribution import normalize_public_resource_exchange
from peap.public_resource_deals import _normalize_exchange as normalize_legacy_workbook_exchange
from peap_parsers.public_resource import _normalize_exchange as normalize_parser_exchange


@pytest.mark.parametrize(
    ("source_label", "original_link", "expected"),
    (
        ("北交互联", "https://www.cbex.com/project/1", "北交互联"),
        (
            "上海联合产权交易所",
            "https://www.shggzy.com/project/1",
            "上海联合产权交易所",
        ),
        (
            "天津市公共资源交易平台交易系统",
            "https://trade.tpre.cn/project/1",
            "天津产权交易中心",
        ),
        (
            "重庆市公共资源交易服务平台",
            "https://www.cquae.com/project/1",
            "重庆联合产权交易所",
        ),
    ),
)
def test_normalize_public_resource_exchange_requires_matching_label_and_host(
    source_label: str,
    original_link: str,
    expected: str,
) -> None:
    assert normalize_public_resource_exchange(source_label, original_link) == expected


@pytest.mark.parametrize(
    "normalizer",
    (normalize_legacy_workbook_exchange, normalize_parser_exchange),
)
def test_project_code_does_not_guess_chongqing(normalizer) -> None:
    assert (
        normalizer(
            "云南省公共资源交易中心",
            "https://ggzy.yn.gov.cn/project/1",
            "CQ530300202600192",
        )
        == "云南省公共资源交易中心"
    )
    assert (
        normalizer(
            "徐州市建设工程网上招投标系统",
            "http://218.3.177.85/project/1",
            "2026063005",
        )
        == "徐州市建设工程网上招投标系统"
    )


def test_known_platform_label_with_conflicting_host_fails_closed() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        normalize_public_resource_exchange(
            "北交互联",
            "https://www.cquae.com/project/1",
        )


def test_known_host_without_matching_platform_label_remains_unattributed() -> None:
    assert (
        normalize_public_resource_exchange(
            "其他产权交易平台",
            "https://www.cquae.com/project/1",
        )
        == "其他产权交易平台"
    )
