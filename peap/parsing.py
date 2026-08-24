"""Parser orchestration layer for the v2 pipeline."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_CODE, KEY_PROJECT_TYPE, KEY_STATUS
from .parser_subsystem import ParserSubsystemError, run_parser_subsystem
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

_DEAL_SOURCE_ALIASES = {
    "beijing": "cbex",
    "shanghai": "sse",
    "tianjin": "tpre",
    "chongqing": "cquae",
}
_DEAL_SOURCE_MARKERS = {
    "cbex": ("北京产权交易所", "cbex.com"),
    "sse": ("上海联合产权交易所", "suaee.com"),
    "tpre": ("天津产权交易中心", "天津交易集团", "tpre.cn"),
    "cquae": ("重庆产权交易", "cquae.com"),
    "public_resource": ("全国公共资源交易平台", "ggzy.gov.cn"),
}
_DEAL_PAGE_MARKERS = (
    "成交公告",
    "成交结果",
    "成交公示",
    "交易结果公示",
    "成交信息",
)
_LISTING_PAGE_MARKERS = (
    "挂牌公告",
    "挂牌起始日期",
    "挂牌开始日期",
    "挂牌截止日期",
    "挂牌起止日期",
    "预披露公告",
)
_PROJECT_CODE_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?:[GQT][36R]\d{4}[A-Z]{2,4}\d+(?:-\d+)?|CQ\d{8,}(?:-\d+)?)(?![A-Z0-9])",
    re.IGNORECASE,
)
_DEAL_DATE_VALUE_PATTERN = re.compile(r"20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?")


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
        value = self.business_type or self.data.get(KEY_PROJECT_TYPE) or ""
        return str(value).strip()

    @property
    def is_pre_disclosure(self) -> bool:
        return bool(self.standard_record.is_pre_disclosure or self.data.get(KEY_IS_PRE_DISCLOSURE))

    @property
    def business_type(self) -> str:
        value = self.standard_record.business_type or self.data.get("business_type") or self.data.get(KEY_PROJECT_TYPE) or ""
        return str(value).strip()


def build_parsed_project(
    *,
    file_path: str,
    exchange: str,
    encoding: str,
    data: Dict[str, Any],
    standard_payload: Mapping[str, Any] | None = None,
) -> ParsedProject:
    if not isinstance(data, Mapping):
        raise TypeError("parsed project data must be a mapping")
    safe_data = dict(data)
    standard_record = build_standard_project(safe_data)
    if isinstance(standard_payload, Mapping):
        merged_standard_payload = standard_record.to_standard_dict()
        if "project_type" in standard_payload and "business_type" not in standard_payload:
            merged_standard_payload["business_type"] = standard_payload["project_type"]
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


def _sidecar_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, Mapping) else payload


def _canonical_deal_source(value: Any) -> str:
    source_id = str(value or "").strip().lower()
    return _DEAL_SOURCE_ALIASES.get(source_id, source_id)


def _normalize_identity_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_integrity_matches(file_path: str, payload: Mapping[str, Any]) -> bool:
    expected_hash = str(payload.get("archive_content_sha256") or "").strip()
    expected_bytes = payload.get("archive_content_bytes")
    if expected_hash:
        expected_hash = expected_hash.removeprefix("sha256:")
        try:
            if _file_sha256(file_path).lower() != expected_hash.lower():
                return False
        except OSError:
            return False
    if expected_bytes not in (None, ""):
        try:
            if os.path.getsize(file_path) != int(expected_bytes):
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def _sidecar_has_archive_integrity(payload: Mapping[str, Any]) -> bool:
    return any(
        payload.get(key) not in (None, "")
        for key in ("archive_content_sha256", "archive_content_bytes")
    )


_SUCCESSFUL_SIDECAR_SAVE_STATUSES = frozenset({"complete", "completed", "success", "succeeded"})


def _sidecar_save_status_is_trusted(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata")
    containers = (payload, metadata) if isinstance(metadata, Mapping) else (payload,)
    for container in containers:
        if "save_status" not in container:
            continue
        save_status = str(container.get("save_status") or "").strip().lower()
        if save_status not in _SUCCESSFUL_SIDECAR_SAVE_STATUSES:
            return False
    return True


def _embedded_deal_metadata(content: str) -> Mapping[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    node = soup.find("script", id="deal_metadata")
    if node is None:
        return {}
    raw = node.string if node.string is not None else node.get_text(" ", strip=False)
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _explicit_html_project_names(content: str) -> tuple[str, ...]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    candidates: list[str] = []
    project_name = soup.select_one("#js_projectName")
    if project_name is not None:
        value = re.sub(r"[（(].*?[）)]", "", project_name.get_text(" ", strip=True)).strip()
        if value:
            candidates.append(value)
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        for index, cell in enumerate(cells[:-1]):
            label = _normalize_identity_text(cell.get_text(" ", strip=True))
            if label not in {"项目名称", "标的名称"}:
                continue
            if str(getattr(cells[index + 1], "name", "")).lower() == "th":
                continue
            value = cells[index + 1].get_text(" ", strip=True)
            if value:
                candidates.append(value)
    return tuple(dict.fromkeys(candidates))


def _html_has_explicit_deal_date(content: str) -> bool:
    """Recognize result pages that expose a dated deal row but no notice heading."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells[:-1]):
            if _normalize_identity_text(cell.get_text(" ", strip=True)) != "成交日期":
                continue
            value = cells[index + 1].get_text(" ", strip=True)
            if _DEAL_DATE_VALUE_PATTERN.search(value):
                return True
    return False


def _integrity_bound_tpre_result_row_matches(
    content: str,
    *,
    project_code: str,
    project_name: str,
) -> bool:
    if not project_code or not project_name:
        return False

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    result_tab = soup.select_one("#tab-result")
    if result_tab is None:
        return False
    tab_classes = {str(value).strip().lower() for value in (result_tab.get("class") or ())}
    if str(result_tab.get("aria-selected") or "").strip().lower() != "true" and "is-active" not in tab_classes:
        return False

    normalized_code = _normalize_identity_text(project_code)
    normalized_name = _normalize_identity_text(project_name)
    for row in soup.find_all("tr"):
        row_text = _normalize_identity_text(row.get_text(" ", strip=True))
        if normalized_code in row_text and normalized_name in row_text:
            return True
    return False


def _html_has_strong_source_identity(
    content: str,
    *,
    source_id: str,
    detected_sources: set[str],
) -> bool:
    if source_id not in detected_sources:
        return False
    if detected_sources == {source_id}:
        return True

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    identity_text = " ".join(
        node.get_text(" ", strip=True)
        for node in soup.select("title, h1, meta[name='application-name']")
    ).lower()
    return any(str(marker).lower() in identity_text for marker in _DEAL_SOURCE_MARKERS[source_id])


def _deal_sidecar_is_trusted(
    *,
    file_path: str,
    content: str,
    payload: Mapping[str, Any],
) -> bool:
    metadata = _sidecar_metadata(payload)
    record_family = str(metadata.get("record_family") or payload.get("record_family") or "").strip().lower()
    has_archive_integrity = _sidecar_has_archive_integrity(payload)
    has_archive_hash = bool(str(payload.get("archive_content_sha256") or "").strip())
    if not _sidecar_save_status_is_trusted(payload) or record_family != "deal" or (
        has_archive_integrity and not _sidecar_integrity_matches(file_path, payload)
    ):
        return False

    source_id = _canonical_deal_source(metadata.get("source_id") or payload.get("source_id"))
    if source_id not in _DEAL_SOURCE_MARKERS:
        return False

    embedded = _embedded_deal_metadata(content)
    embedded_family = str(embedded.get("record_family") or "").strip().lower()
    embedded_source = _canonical_deal_source(embedded.get("source_id"))
    sidecar_code = str(metadata.get("project_code") or "").strip().upper()
    embedded_code = str(embedded.get("project_code") or "").strip().upper()
    content_lower = str(content or "").lower()
    detected_sources = {
        candidate_source
        for candidate_source, markers in _DEAL_SOURCE_MARKERS.items()
        if any(str(marker).lower() in content_lower for marker in markers)
    }
    html_codes = {match.group(0).upper() for match in _PROJECT_CODE_PATTERN.finditer(content or "")}

    if not has_archive_integrity:
        if embedded:
            if (
                embedded_family != "deal"
                or not embedded_source
                or embedded_source != source_id
                or not sidecar_code
                or not embedded_code
                or embedded_code != sidecar_code
            ):
                return False
        else:
            # Legacy captures often omit the source banner and project code from
            # the rendered shell.  Treat those fields as optional evidence, but
            # reject every explicit contradiction that is present in the HTML.
            if detected_sources and not _html_has_strong_source_identity(
                content,
                source_id=source_id,
                detected_sources=detected_sources,
            ):
                return False
            if html_codes and not sidecar_code:
                return False
            if sidecar_code and html_codes and sidecar_code not in html_codes:
                return False

    if embedded_family == "deal":
        if embedded_source and embedded_source != source_id:
            return False
        for key in ("project_code", "project_name"):
            embedded_value = _normalize_identity_text(embedded.get(key))
            sidecar_value = _normalize_identity_text(metadata.get(key))
            if embedded_value and sidecar_value and embedded_value != sidecar_value:
                return False
        return True

    if detected_sources and source_id not in detected_sources:
        return False

    if sidecar_code and html_codes and sidecar_code not in html_codes:
        return False

    if (
        has_archive_integrity
        and has_archive_hash
        and source_id == "tpre"
        and _integrity_bound_tpre_result_row_matches(
            content,
            project_code=sidecar_code,
            project_name=str(metadata.get("project_name") or "").strip(),
        )
    ):
        return True

    has_deal_marker = any(marker in content for marker in _DEAL_PAGE_MARKERS) or _html_has_explicit_deal_date(
        content
    )
    has_listing_marker = any(marker in content for marker in _LISTING_PAGE_MARKERS)
    if has_listing_marker and not has_deal_marker:
        return False
    return has_deal_marker


def _load_same_stem_sidecar(file_path: str) -> Mapping[str, Any] | None:
    sidecar_path = os.path.splitext(str(file_path))[0] + ".json"
    if os.path.islink(sidecar_path):
        raise ParseError(f"deal-sidecar-symlink: {sidecar_path}")
    if not os.path.isfile(sidecar_path):
        return None
    try:
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ParseError(f"deal-sidecar-invalid-json: {sidecar_path}: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"deal-sidecar-unreadable: {sidecar_path}: {exc}") from exc
    return payload if isinstance(payload, Mapping) else None


def _deal_sidecar_marks_deal_snapshot(file_path: str) -> bool:
    payload = _load_same_stem_sidecar(file_path)
    if payload is None:
        return False
    from .io_utils import read_text_with_fallback

    read_result = read_text_with_fallback(file_path)
    if read_result is None:
        return False
    return _deal_sidecar_is_trusted(
        file_path=file_path,
        content=read_result.content,
        payload=payload,
    )


def _parse_deal_snapshot_file(file_path: str) -> ParsedProject:
    from .io_utils import read_text_with_fallback

    read_result = read_text_with_fallback(file_path)
    if read_result is None:
        raise ParseError(f"read-failed: {file_path}")
    return _parse_registry_deal_file(
        file_path=file_path,
        content=read_result.content,
        encoding=read_result.encoding,
    )


def _parse_registry_deal_file(*, file_path: str, content: str, encoding: str) -> ParsedProject:
    from .streaming_ingest import _build_registry_parse_payload

    try:
        payload = _build_registry_parse_payload(file_path=file_path, content=content)
    except Exception as exc:
        raise ParseError(f"deal-registry-parse-failed: {file_path}: {exc}") from exc
    exchange = str(payload.get("source_id") or "").strip()
    if not exchange:
        raise ParseError(f"deal-registry-parse-missing-source: {file_path}")
    return build_parsed_project(
        file_path=file_path,
        exchange=exchange,
        encoding=encoding,
        data=dict(payload),
        standard_payload=payload,
    )


def parse_file(file_path: str) -> ParsedProject:
    from peap_core.error_contracts import PipelineFailure

    from .io_utils import read_text_with_fallback
    from .streaming_ingest import _build_registry_parse_context

    source_path = str(file_path)
    if os.path.islink(source_path) or (
        os.path.lexists(source_path) and not os.path.isfile(source_path)
    ):
        raise ParseError(
            "source_snapshot_invalid: source snapshot must be a regular "
            f"non-symlink file: {source_path}"
        )

    read_result = read_text_with_fallback(file_path)
    if read_result is not None:
        try:
            _document, source_match, _metadata = _build_registry_parse_context(
                file_path=file_path,
                content=read_result.content,
            )
        except PipelineFailure as exc:
            if exc.code != "no_source_match":
                raise ParseError(f"registry-classification-failed: {file_path}: {exc}") from exc
        else:
            if source_match.page_kind == "deal":
                return _parse_registry_deal_file(
                    file_path=file_path,
                    content=read_result.content,
                    encoding=read_result.encoding,
                )

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
