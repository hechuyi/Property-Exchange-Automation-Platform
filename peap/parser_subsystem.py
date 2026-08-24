from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from peap.constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    TYPE_UNKNOWN,
)
from peap.finance_fallback import apply_finance_fallback
from peap.group_fallback import apply_group_fallback
from peap.io_utils import read_text_with_fallback
from peap.pathing import detect_category_from_path
from peap.pre_disclosure_fallback import apply_pre_disclosure_fallback
from peap_core.source_catalog import canonical_source_code
from peap_parsers import (
    BeijingParser,
    ChongqingParser,
    GuangzhouParser,
    ParserContext,
    ParserOutput,
    ShandongParser,
    ShanghaiParser,
    ShenzhenParser,
    TianjinParser,
    detect_exchange,
)

PARSER_MAP = {
    "shenzhen": ShenzhenParser,
    "beijing": BeijingParser,
    "shanghai": ShanghaiParser,
    "chongqing": ChongqingParser,
    "tianjin": TianjinParser,
    "shandong": ShandongParser,
    "guangdong": GuangzhouParser,
}


def _canonical_parser_source_id(source_id: object) -> str:
    text = str(source_id or "").strip()
    return str(
        canonical_source_code(
            text,
            allow_substring=False,
            allowed_source_ids={"guangdong"},
        )
        or text
    ).strip()


@dataclass(frozen=True)
class ParserSubsystemResult:
    exchange: str
    encoding: str
    data: dict[str, Any]
    standard_payload: Mapping[str, Any] | None = None


class ParserSubsystemError(RuntimeError):
    pass


def _coerce_standard_payload(
    *,
    file_path: str | None = None,
    standard_payload: object,
) -> Mapping[str, Any] | None:
    if standard_payload is None:
        return None
    if not isinstance(standard_payload, Mapping):
        location = f": {file_path}" if file_path else ""
        raise ParserSubsystemError(f"invalid-parser-output: standard_payload must be a mapping or None{location}")
    return standard_payload


def _coerce_parser_output(*, file_path: str, parse_result: object) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    if isinstance(parse_result, ParserOutput):
        standard_payload = _coerce_standard_payload(
            file_path=file_path,
            standard_payload=parse_result.standard_payload,
        )
        if standard_payload is None:
            return {}, None
        return dict(standard_payload), dict(standard_payload)

    if isinstance(parse_result, Mapping):
        return dict(parse_result), None

    raise ParserSubsystemError(f"invalid-parser-output: {file_path}")


def _resolved_business_type(*, data: Mapping[str, Any], standard_payload: object) -> str:
    standard = _coerce_standard_payload(standard_payload=standard_payload)
    if standard is None:
        standard = {}
    value = (
        standard.get("business_type")
        or standard.get("project_type")
        or data.get("business_type")
        or data.get(KEY_PROJECT_TYPE)
        or TYPE_UNKNOWN
    )
    text = str(value or "").strip()
    return text or TYPE_UNKNOWN


def _has_cbex_otc_identity(*, data: Mapping[str, Any], standard_payload: object) -> bool:
    standard = _coerce_standard_payload(standard_payload=standard_payload)
    if standard is None:
        standard = {}
    values = (
        data.get(KEY_PROJECT_CODE),
        data.get("项目名称"),
        standard.get("project_code"),
        standard.get("project_name"),
    )
    return any(str(value or "").strip() for value in values)


def _is_cbex_otc_page(html_text: str) -> bool:
    import re

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
    import re

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


def run_parser_subsystem(
    file_path: str,
    *,
    read_text_with_fallback_override=None,
    detect_exchange_override=None,
    parser_map_override: Mapping[str, type] | None = None,
    detect_category_from_path_override=None,
    apply_pre_disclosure_fallback_override=None,
    apply_finance_fallback_override=None,
    apply_group_fallback_override=None,
) -> ParserSubsystemResult:
    if os.path.islink(file_path) or (
        os.path.lexists(file_path) and not os.path.isfile(file_path)
    ):
        raise ParserSubsystemError(
            "source_snapshot_invalid: source snapshot must be a regular "
            f"non-symlink file: {file_path}"
        )
    read_text = read_text_with_fallback if read_text_with_fallback_override is None else read_text_with_fallback_override
    detect_source = detect_exchange if detect_exchange_override is None else detect_exchange_override
    parser_map = PARSER_MAP if parser_map_override is None else parser_map_override
    detect_category = detect_category_from_path if detect_category_from_path_override is None else detect_category_from_path_override
    apply_pre_disclosure = apply_pre_disclosure_fallback if apply_pre_disclosure_fallback_override is None else apply_pre_disclosure_fallback_override
    apply_finance = apply_finance_fallback if apply_finance_fallback_override is None else apply_finance_fallback_override
    apply_group = apply_group_fallback if apply_group_fallback_override is None else apply_group_fallback_override

    read_result = read_text(file_path)
    if read_result is None:
        raise ParserSubsystemError(f"read-failed: {file_path}")

    is_cbex_otc_page = _is_cbex_otc_page(read_result.content)
    if is_cbex_otc_page and not _can_recover_cbex_otc_page(read_result.content):
        raise ParserSubsystemError(f"cbex-otc-page-unrecoverable: {file_path}")

    detected_exchange_type = detect_source(read_result.content)
    if not detected_exchange_type:
        raise ParserSubsystemError(f"exchange-detect-failed: {file_path}")
    exchange_type = _canonical_parser_source_id(detected_exchange_type)

    parser_class = parser_map.get(exchange_type)
    if parser_class is None:
        raise ParserSubsystemError(f"unsupported-exchange: {exchange_type}")

    parser = parser_class(
        read_result.content,
        context=ParserContext(source_file=file_path),
    )
    parse_result = parser.parse()
    data, standard_payload = _coerce_parser_output(file_path=file_path, parse_result=parse_result)
    data["source_id"] = _canonical_parser_source_id(data.get("source_id") or exchange_type)
    if standard_payload is not None:
        standard_payload = dict(standard_payload)
        standard_payload["source_id"] = data["source_id"]
    shared_soup = getattr(parser, "soup", None)
    apply_pre_disclosure(data, read_result.content, soup=shared_soup)
    apply_finance(data, read_result.content, soup=shared_soup)
    apply_group(data, read_result.content, soup=shared_soup)

    status, _ = detect_category(file_path)
    business_type = _resolved_business_type(data=data, standard_payload=standard_payload)
    if is_cbex_otc_page and not _has_cbex_otc_identity(data=data, standard_payload=standard_payload):
        raise ParserSubsystemError(f"cbex-otc-page-unrecoverable: {file_path}")
    project_code = data.get(KEY_PROJECT_CODE)
    if not project_code and standard_payload is not None:
        project_code = standard_payload.get("project_code")
    if project_code:
        data.setdefault(KEY_PROJECT_CODE, project_code)
        try:
            if parser.is_pre_disclosure(project_code):
                data[KEY_IS_PRE_DISCLOSURE] = True
        except Exception as exc:
            raise ParserSubsystemError(f"pre-disclosure-detect-failed: {file_path}") from exc

    data[KEY_STATUS] = status
    data[KEY_PROJECT_TYPE] = business_type
    data["business_type"] = business_type
    return ParserSubsystemResult(
        exchange=exchange_type,
        encoding=read_result.encoding,
        data=data,
        standard_payload=standard_payload,
    )


__all__ = ["ParserSubsystemError", "ParserSubsystemResult", "PARSER_MAP", "run_parser_subsystem"]
