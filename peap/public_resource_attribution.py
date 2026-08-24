"""Evidence-based exchange attribution for public-resource deal pages."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class PublicResourceExchangeRule:
    exchange_name: str
    platform_names: tuple[str, ...]
    original_source_host_suffixes: tuple[str, ...]


EXCHANGE_RULES = (
    PublicResourceExchangeRule(
        exchange_name="北交互联",
        platform_names=("北交互联",),
        original_source_host_suffixes=("cbex.com", "cbex.com.cn"),
    ),
    PublicResourceExchangeRule(
        exchange_name="上海联合产权交易所",
        platform_names=("上海联合产权交易所",),
        original_source_host_suffixes=("shggzy.com", "suaee.com"),
    ),
    PublicResourceExchangeRule(
        exchange_name="天津产权交易中心",
        platform_names=("天津市公共资源交易平台交易系统",),
        original_source_host_suffixes=("tpre.cn",),
    ),
    PublicResourceExchangeRule(
        exchange_name="重庆联合产权交易所",
        platform_names=("重庆", "重庆市", "重庆市公共资源交易服务平台"),
        original_source_host_suffixes=("cquae.com",),
    ),
    PublicResourceExchangeRule(
        exchange_name="深圳联合产权交易所",
        platform_names=("深圳联合产权交易所", "深圳联合产权交易所股份有限公司"),
        original_source_host_suffixes=("ygp.gdzwfw.gov.cn",),
    ),
)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _host_matches_suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def normalize_public_resource_exchange(source_label: str, original_link: str) -> str:
    """Return a canonical exchange only when label and original host agree."""

    source = _clean_text(source_label)
    host = (urlparse(_clean_text(original_link)).hostname or "").lower()
    label_matches = [rule for rule in EXCHANGE_RULES if source in rule.platform_names]
    host_matches = [
        rule
        for rule in EXCHANGE_RULES
        if any(_host_matches_suffix(host, suffix) for suffix in rule.original_source_host_suffixes)
    ]

    if len(label_matches) > 1 or len(host_matches) > 1:
        raise ValueError("public-resource attribution matches multiple exchange rules")
    if label_matches:
        if len(host_matches) != 1 or label_matches[0] != host_matches[0]:
            raise ValueError(
                "public-resource platform label conflicts with original-source host"
            )
        return label_matches[0].exchange_name

    # A host without its corroborating platform label is not enough to claim ownership.
    return source or host or "全国公共资源交易平台"


__all__ = (
    "EXCHANGE_RULES",
    "PublicResourceExchangeRule",
    "normalize_public_resource_exchange",
)
