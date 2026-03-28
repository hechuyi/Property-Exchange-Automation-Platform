"""Parser orchestration layer for the v2 pipeline."""

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from peap_parsers import (
    BeijingParser,
    ChongqingParser,
    GuangzhouParser,
    ParserContext,
    ParserOutput,
    PublicResourceParser,
    ShandongParser,
    ShanghaiParser,
    ShenzhenParser,
    TianjinParser,
    detect_exchange,
)

from .constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_DEAL,
    TYPE_EQUITY_TRANSFER,
    TYPE_PRE_DISCLOSURE,
    TYPE_UNKNOWN,
)
from .finance_fallback import apply_finance_fallback
from .group_fallback import apply_group_fallback
from .io_utils import read_text_with_fallback
from .pathing import detect_category_from_path
from .pre_disclosure_fallback import apply_pre_disclosure_fallback
from .standard_model import (
    STANDARD_PROJECT_FIELD_NAMES,
    StandardProject,
    build_standard_project,
    hydrate_standard_project,
)

PARSER_MAP = {
    "shenzhen": ShenzhenParser,
    "beijing": BeijingParser,
    "shanghai": ShanghaiParser,
    "chongqing": ChongqingParser,
    "tianjin": TianjinParser,
    "shandong": ShandongParser,
    "guangzhou": GuangzhouParser,
    "public_resource": PublicResourceParser,
}

COMPAT_PROFILE_FULL = "full"
COMPAT_PROFILE_PPE_READY = "ppe_ready"
VALID_COMPAT_PROFILES = {COMPAT_PROFILE_FULL, COMPAT_PROFILE_PPE_READY}
PARSED_PROJECT_CACHE_COMPAT_PAYLOAD_KEY = "compat_payload"
PARSED_PROJECT_CACHE_STANDARD_RECORD_KEY = "standard_record"


class ParseError(RuntimeError):
    """Raised when a file cannot be parsed into structured data."""


class SkipParse(ParseError):
    """Raised when a file should be intentionally skipped."""


@dataclass
class ParsedProject:
    file_path: str
    exchange: str
    encoding: str
    data: Dict[str, Any]
    standard_record: StandardProject

    def to_compat_payload(self, *, include_raw: bool = True) -> Dict[str, Any]:
        """Expose the legacy-compatible payload without leaking parser-specific internals downstream."""
        return self.standard_record.to_legacy_payload(include_raw=include_raw)

    def to_cache_payload(self) -> Dict[str, Any]:
        return {
            PARSED_PROJECT_CACHE_COMPAT_PAYLOAD_KEY: dict(self.data),
            PARSED_PROJECT_CACHE_STANDARD_RECORD_KEY: self.standard_record.to_standard_dict(),
        }

    @classmethod
    def from_cache_payload(
        cls,
        *,
        file_path: str,
        exchange: str,
        encoding: str,
        payload: Mapping[str, Any],
    ) -> "ParsedProject":
        if not isinstance(payload, Mapping):
            raise TypeError("cache payload must be a mapping")

        compat_payload = payload
        standard_payload: Mapping[str, Any] | None = None
        compat_candidate = payload.get(PARSED_PROJECT_CACHE_COMPAT_PAYLOAD_KEY)
        standard_candidate = payload.get(PARSED_PROJECT_CACHE_STANDARD_RECORD_KEY)
        if isinstance(compat_candidate, Mapping) and isinstance(standard_candidate, Mapping):
            compat_payload = compat_candidate
            standard_payload = standard_candidate

        return build_parsed_project(
            file_path=file_path,
            exchange=exchange,
            encoding=encoding,
            data=dict(compat_payload),
            standard_payload=standard_payload,
        )

    @property
    def project_code(self) -> str:
        value = self.standard_record.project_code or self.data.get(KEY_PROJECT_CODE) or ""
        return str(value).strip()

    @property
    def project_name(self) -> str:
        value = self.standard_record.project_name or self.data.get("\u9879\u76ee\u540d\u79f0") or ""
        return str(value).strip()

    @property
    def status(self) -> str:
        value = self.standard_record.status or self.data.get(KEY_STATUS) or ""
        return str(value).strip()

    @property
    def project_type(self) -> str:
        value = self.standard_record.project_type or self.data.get(KEY_PROJECT_TYPE) or ""
        return str(value).strip()

    @property
    def is_pre_disclosure(self) -> bool:
        return bool(self.standard_record.is_pre_disclosure or self.data.get(KEY_IS_PRE_DISCLOSURE))


def build_parsed_project(
    *,
    file_path: str,
    exchange: str,
    encoding: str,
    data: Dict[str, Any],
    standard_payload: Mapping[str, Any] | None = None,
) -> ParsedProject:
    safe_data = dict(data or {})
    standard_record = build_standard_project(safe_data)
    if isinstance(standard_payload, Mapping):
        merged_standard_payload = standard_record.to_standard_dict()
        for field_name in STANDARD_PROJECT_FIELD_NAMES:
            if field_name in standard_payload:
                merged_standard_payload[field_name] = standard_payload[field_name]
        standard_record = hydrate_standard_project(merged_standard_payload, raw=safe_data)
    return ParsedProject(
        file_path=file_path,
        exchange=exchange,
        encoding=encoding,
        data=safe_data,
        standard_record=standard_record,
    )


def _coerce_parser_output(
    *,
    file_path: str,
    parse_result: object,
) -> tuple[Dict[str, Any], Mapping[str, Any] | None]:
    if isinstance(parse_result, ParserOutput):
        compat_payload = parse_result.compat_payload
        if not isinstance(compat_payload, Mapping):
            raise ParseError(f"invalid-parser-output: {file_path}")
        standard_payload = parse_result.standard_payload
        if standard_payload is None:
            return dict(compat_payload), None
        if not isinstance(standard_payload, Mapping):
            raise ParseError(f"invalid-parser-output: {file_path}")
        return dict(compat_payload), dict(standard_payload)

    if isinstance(parse_result, Mapping):
        return dict(parse_result), None

    raise ParseError(f"invalid-parser-output: {file_path}")


def _resolve_compat_profile(raw: str) -> str:
    profile = str(raw or COMPAT_PROFILE_FULL).strip().lower()
    if profile in VALID_COMPAT_PROFILES:
        return profile
    raise ParseError(
        f"invalid-compat-profile: {raw!r} (expected one of {sorted(VALID_COMPAT_PROFILES)})"
    )


def _is_cbex_otc_page(html_text: str) -> bool:
    # Use strong OTC markers to avoid false positives from normal cbex.com pages
    # that merely contain otc links in navigation.
    markers = (
        r"<title>\s*北交互联",
        r"欢迎来到北交互联",
        r'name=["\']keywords["\'][^>]*北交互联',
        r"otc\.cbex\.com/page/tyrz/login",
    )
    for pattern in markers:
        if re.search(pattern, html_text, flags=re.IGNORECASE):
            return True
    return False


def _can_recover_cbex_otc_page(html_text: str) -> bool:
    markers = (
        r'<textarea[^>]+id=["\']jsonobj["\']',
        r'class=["\']projectcode["\']',
        r'class=["\']object["\']',
        r'class=["\']bd_detail_num["\']',
        r"\bprojectcode\b",
    )
    for pattern in markers:
        if re.search(pattern, html_text, flags=re.IGNORECASE):
            return True
    return False


def _resolved_project_type(
    *,
    data: Mapping[str, Any],
    standard_payload: Mapping[str, Any] | None,
) -> str:
    value = (
        (standard_payload or {}).get("project_type")
        or data.get(KEY_PROJECT_TYPE)
        or TYPE_UNKNOWN
    )
    text = str(value or "").strip()
    return text or TYPE_UNKNOWN


def _has_cbex_otc_identity(
    *,
    data: Mapping[str, Any],
    standard_payload: Mapping[str, Any] | None,
) -> bool:
    values = (
        data.get(KEY_PROJECT_CODE),
        data.get("项目名称"),
        (standard_payload or {}).get("project_code"),
        (standard_payload or {}).get("project_name"),
    )
    return any(str(value or "").strip() for value in values)


def parse_file(
    file_path: str,
    *,
    compat_profile: str = COMPAT_PROFILE_FULL,
) -> ParsedProject:
    compat_profile = _resolve_compat_profile(compat_profile)

    read_result = read_text_with_fallback(file_path)
    if read_result is None:
        raise ParseError(f"read-failed: {file_path}")

    is_cbex_otc_page = _is_cbex_otc_page(read_result.content)
    if is_cbex_otc_page and not _can_recover_cbex_otc_page(read_result.content):
        raise ParseError(f"cbex-otc-page-unrecoverable: {file_path}")

    exchange_type = detect_exchange(read_result.content)
    if not exchange_type:
        raise ParseError(f"exchange-detect-failed: {file_path}")

    parser_class = PARSER_MAP.get(exchange_type)
    if parser_class is None:
        raise ParseError(f"unsupported-exchange: {exchange_type}")

    parser = parser_class(
        read_result.content,
        context=ParserContext(source_file=file_path),
    )
    parse_result = parser.parse()
    data, standard_payload = _coerce_parser_output(
        file_path=file_path,
        parse_result=parse_result,
    )
    shared_soup = getattr(parser, "soup", None)
    if exchange_type != "public_resource":
        apply_pre_disclosure_fallback(data, read_result.content, soup=shared_soup)
        apply_finance_fallback(data, read_result.content, soup=shared_soup)
        apply_group_fallback(data, read_result.content, soup=shared_soup)

    status, path_project_type = detect_category_from_path(file_path)
    project_type = _resolved_project_type(data=data, standard_payload=standard_payload)
    if not is_cbex_otc_page and project_type in ("", TYPE_UNKNOWN):
        project_type = str(path_project_type or "").strip() or TYPE_UNKNOWN
    if is_cbex_otc_page and not _has_cbex_otc_identity(data=data, standard_payload=standard_payload):
        raise ParseError(f"cbex-otc-page-unrecoverable: {file_path}")
    if exchange_type == "public_resource":
        status = STATUS_DEAL
        if project_type in ("", TYPE_UNKNOWN):
            project_type = TYPE_EQUITY_TRANSFER

    project_code = data.get(KEY_PROJECT_CODE)
    if project_code:
        try:
            if parser.is_pre_disclosure(project_code):
                data[KEY_IS_PRE_DISCLOSURE] = True
                project_type = TYPE_PRE_DISCLOSURE
        except Exception:
            pass

    data[KEY_STATUS] = status
    data[KEY_PROJECT_TYPE] = project_type

    return build_parsed_project(
        file_path=file_path,
        exchange=exchange_type,
        encoding=read_result.encoding,
        data=data,
        standard_payload=standard_payload,
    )
