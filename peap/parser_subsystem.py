from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from peap.constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    STATUS_DEAL,
    TYPE_EQUITY_TRANSFER,
    TYPE_PRE_DISCLOSURE,
    TYPE_UNKNOWN,
)
from peap.finance_fallback import apply_finance_fallback
from peap.group_fallback import apply_group_fallback
from peap.io_utils import read_text_with_fallback
from peap.pathing import detect_category_from_path
from peap.pre_disclosure_fallback import apply_pre_disclosure_fallback
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


@dataclass(frozen=True)
class ParserSubsystemResult:
    exchange: str
    encoding: str
    data: dict[str, Any]
    standard_payload: Mapping[str, Any] | None = None


class ParserSubsystemError(RuntimeError):
    pass


def _coerce_parser_output(*, file_path: str, parse_result: object) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    if isinstance(parse_result, ParserOutput):
        standard_payload = parse_result.standard_payload
        if not isinstance(standard_payload, Mapping):
            raise ParserSubsystemError(f"invalid-parser-output: {file_path}")
        return dict(standard_payload), dict(standard_payload)

    if isinstance(parse_result, Mapping):
        return dict(parse_result), None

    raise ParserSubsystemError(f"invalid-parser-output: {file_path}")


def _resolved_project_type(*, data: Mapping[str, Any], standard_payload: Mapping[str, Any] | None) -> str:
    value = (
        (standard_payload or {}).get("project_type")
        or data.get(KEY_PROJECT_TYPE)
        or TYPE_UNKNOWN
    )
    text = str(value or "").strip()
    return text or TYPE_UNKNOWN


def _has_cbex_otc_identity(*, data: Mapping[str, Any], standard_payload: Mapping[str, Any] | None) -> bool:
    values = (
        data.get(KEY_PROJECT_CODE),
        data.get("项目名称"),
        (standard_payload or {}).get("project_code"),
        (standard_payload or {}).get("project_name"),
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

    exchange_type = detect_source(read_result.content)
    if not exchange_type:
        raise ParserSubsystemError(f"exchange-detect-failed: {file_path}")

    parser_class = parser_map.get(exchange_type)
    if parser_class is None:
        raise ParserSubsystemError(f"unsupported-exchange: {exchange_type}")

    parser = parser_class(
        read_result.content,
        context=ParserContext(source_file=file_path),
    )
    parse_result = parser.parse()
    data, standard_payload = _coerce_parser_output(file_path=file_path, parse_result=parse_result)
    shared_soup = getattr(parser, "soup", None)
    if exchange_type != "public_resource":
        apply_pre_disclosure(data, read_result.content, soup=shared_soup)
        apply_finance(data, read_result.content, soup=shared_soup)
        apply_group(data, read_result.content, soup=shared_soup)

    status, path_project_type = detect_category(file_path)
    project_type = _resolved_project_type(data=data, standard_payload=standard_payload)
    if not is_cbex_otc_page and project_type in ("", TYPE_UNKNOWN):
        project_type = str(path_project_type or "").strip() or TYPE_UNKNOWN
    if is_cbex_otc_page and not _has_cbex_otc_identity(data=data, standard_payload=standard_payload):
        raise ParserSubsystemError(f"cbex-otc-page-unrecoverable: {file_path}")
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
    return ParserSubsystemResult(
        exchange=exchange_type,
        encoding=read_result.encoding,
        data=data,
        standard_payload=standard_payload,
    )


__all__ = ["ParserSubsystemError", "ParserSubsystemResult", "PARSER_MAP", "run_parser_subsystem"]
