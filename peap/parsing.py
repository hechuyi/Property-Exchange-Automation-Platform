"""Parser orchestration layer for the v2 pipeline."""

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from peap_parsers import detect_exchange

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_CODE, KEY_PROJECT_TYPE, KEY_STATUS
from .finance_fallback import apply_finance_fallback
from .group_fallback import apply_group_fallback
from .io_utils import read_text_with_fallback
from .pathing import detect_category_from_path
from .pre_disclosure_fallback import apply_pre_disclosure_fallback
from .parser_subsystem import PARSER_MAP, ParserSubsystemError, run_parser_subsystem
from .standard_model import (
    STANDARD_PROJECT_FIELD_NAMES,
    StandardProject,
    build_standard_project,
    hydrate_standard_project,
)

COMPAT_PROFILE_FULL = "full"
COMPAT_PROFILE_PPE_READY = "ppe_ready"
VALID_COMPAT_PROFILES = {COMPAT_PROFILE_FULL, COMPAT_PROFILE_PPE_READY}
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

    def to_cache_payload(self) -> Dict[str, Any]:
        return {
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

        standard_candidate = payload.get(PARSED_PROJECT_CACHE_STANDARD_RECORD_KEY)
        standard_payload: Mapping[str, Any] | None = (
            standard_candidate if isinstance(standard_candidate, Mapping) else None
        )

        return build_parsed_project(
            file_path=file_path,
            exchange=exchange,
            encoding=encoding,
            data={},
            standard_payload=standard_payload,
        )

    @property
    def project_code(self) -> str:
        value = self.standard_record.project_code or self.data.get(KEY_PROJECT_CODE) or ""
        return str(value).strip()

    @property
    def project_name(self) -> str:
        value = self.standard_record.project_name or self.data.get("项目名称") or ""
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


def parse_file(file_path: str) -> ParsedProject:
    try:
        result = run_parser_subsystem(file_path)
    except ParserSubsystemError as exc:
        raise ParseError(str(exc)) from exc

    return build_parsed_project(
        file_path=file_path,
        exchange=result.exchange,
        encoding=result.encoding,
        data=dict(result.data),
        standard_payload=result.standard_payload,
    )


__all__ = [
    "PARSED_PROJECT_CACHE_STANDARD_RECORD_KEY",
    "ParseError",
    "ParsedProject",
    "SkipParse",
    "build_parsed_project",
    "parse_file",
]
