from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

from peap_core import AssembledRecordCandidate, PageParseResult


def _page_kind(result: PageParseResult) -> str:
    return str(result.page_identity.get("page_kind") or result.source_match.page_kind or "").strip()


def _project_code(result: PageParseResult) -> str:
    return str(result.page_identity.get("project_code") or result.page_identity.get("project_id") or "").strip()


def _project_name(result: PageParseResult) -> str:
    for fact in result.facts:
        if not isinstance(fact, dict):
            continue
        if str(fact.get("field") or "").strip() in {"project_name", "项目名称"}:
            return str(fact.get("value") or "").strip()
    tokens = result.page_identity.get("candidate_tokens") or ()
    if len(tokens) >= 2:
        return str(tokens[1] or "").strip()
    return ""


def _fact_field(result: PageParseResult, *field_names: str) -> str:
    """Extract a field value from result facts, returning empty string if not found."""
    for fact in result.facts:
        if not isinstance(fact, Mapping):
            continue
        if str(fact.get("field") or "").strip() in field_names:
            return str(fact.get("value") or "").strip()
    return ""


def _page_url(result: PageParseResult) -> str:
    return str(result.page_identity.get("page_url") or "").strip()


def _candidate_tokens(result: PageParseResult) -> tuple[str, ...]:
    raw = result.page_identity.get("candidate_tokens") or ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def _correlation_hints(result: PageParseResult) -> tuple[str, ...]:
    hints: list[str] = []
    for outgoing_ref in result.outgoing_refs:
        if not isinstance(outgoing_ref, dict):
            continue
        for item in outgoing_ref.get("correlation_hints") or ():
            text = str(item or "").strip()
            if text and text not in hints:
                hints.append(text)
    return tuple(hints)


def _target_urls(result: PageParseResult) -> tuple[str, ...]:
    urls: list[str] = []
    for outgoing_ref in result.outgoing_refs:
        if not isinstance(outgoing_ref, dict):
            continue
        target_url = str(outgoing_ref.get("target_url") or "").strip()
        if target_url and target_url not in urls:
            urls.append(target_url)
    return tuple(urls)


def _entity_keys_for_group(results: tuple[PageParseResult, ...]) -> tuple[str, ...]:
    codes = [code for code in (_project_code(result) for result in results) if code]
    names = [name for name in (_project_name(result) for result in results) if name]
    entity_keys: list[str] = []
    if codes:
        entity_keys.append(codes[0])
    if names:
        entity_keys.append(names[0])
    return tuple(entity_keys)


def _business_object(results: tuple[PageParseResult, ...]) -> dict[str, object]:
    page_kinds = [_page_kind(result) for result in results]
    project_code = next((value for value in (_project_code(result) for result in results) if value), "")
    project_name = next((value for value in (_project_name(result) for result in results) if value), "")
    # Extract business fields from facts
    project_type = next((value for value in (_fact_field(r, "project_type", "项目类型") for r in results) if value), "")
    status = next((value for value in (_fact_field(r, "status", "项目状态") for r in results) if value), "")
    start_date = next((value for value in (_fact_field(r, "start_date", "listing_date", "挂牌日期", "开始日期") for r in results) if value), "")
    price = next((value for value in (_fact_field(r, "price", "价格", "挂牌价格") for r in results) if value), "")
    seller = next((value for value in (_fact_field(r, "seller", "转让方", "融资方") for r in results) if value), "")
    source_type = next((value for value in (_fact_field(r, "source_type", "类型") for r in results) if value), "")
    group_name = next((value for value in (_fact_field(r, "group_name", "隶属集团", "集团名称") for r in results) if value), "")
    return {
        "project_code": project_code,
        "project_name": project_name,
        "project_type": project_type,
        "status": status,
        "start_date": start_date,
        "price": price,
        "seller": seller,
        "source_type": source_type,
        "group_name": group_name,
        "page_kinds": page_kinds,
        "page_urls": [_page_url(result) for result in results if _page_url(result)],
    }


def _missing_requirements(results: tuple[PageParseResult, ...]) -> tuple[str, ...]:
    page_kinds = {_page_kind(result) for result in results}
    missing: list[str] = []
    if "listing" not in page_kinds:
        missing.append("listing")
    return tuple(missing)


def _completion_state(results: tuple[PageParseResult, ...]) -> tuple[str, tuple[str, ...]]:
    names = {name for name in (_project_name(result) for result in results) if name}
    if len(names) > 1:
        return "conflicted", ("project_name_conflict",)
    missing = _missing_requirements(results)
    if missing:
        return "partial", missing
    return "sufficient", ()


def _assembly_id(source_id: str, entity_keys: tuple[str, ...]) -> str:
    digest = hashlib.sha256("|".join((source_id, *entity_keys)).encode("utf-8")).hexdigest()[:12]
    return f"asm-{digest}"


def _belongs_in_group(candidate: PageParseResult, grouped: tuple[PageParseResult, ...]) -> bool:
    candidate_code = _project_code(candidate)
    grouped_codes = {value for value in (_project_code(item) for item in grouped) if value}
    if candidate_code and candidate_code in grouped_codes:
        return True

    candidate_url = _page_url(candidate)
    grouped_urls = {_page_url(item) for item in grouped if _page_url(item)}
    grouped_target_urls = {url for item in grouped for url in _target_urls(item)}
    if candidate_url and candidate_url in grouped_target_urls:
        return True
    if any(url in grouped_urls for url in _target_urls(candidate)):
        return True

    candidate_tokens = set(_candidate_tokens(candidate)) | set(_correlation_hints(candidate))
    grouped_tokens = {token for item in grouped for token in (_candidate_tokens(item) + _correlation_hints(item))}
    return bool(candidate_tokens and grouped_tokens and candidate_tokens & grouped_tokens)


def _group_page_results(page_results: tuple[PageParseResult, ...]) -> list[tuple[PageParseResult, ...]]:
    groups: list[list[PageParseResult]] = []
    for result in page_results:
        matched_group: list[PageParseResult] | None = None
        for group in groups:
            if _belongs_in_group(result, tuple(group)):
                matched_group = group
                break
        if matched_group is None:
            groups.append([result])
            continue
        matched_group.append(result)
    return [tuple(group) for group in groups]


def assemble_page_results(page_results: Iterable[PageParseResult]) -> list[AssembledRecordCandidate]:
    ordered_results = tuple(page_results)
    grouped_results = _group_page_results(ordered_results)
    assembled: list[AssembledRecordCandidate] = []
    for group in grouped_results:
        source_ids = tuple(sorted({result.source_match.source_id for result in group}))
        entity_keys = _entity_keys_for_group(group)
        completion_state, missing_requirements = _completion_state(group)
        raw_business_object = _business_object(group)
        assembly_id = _assembly_id(source_ids[0] if source_ids else "unknown", entity_keys)
        assembled.append(
            AssembledRecordCandidate(
                assembly_id=assembly_id,
                source_ids=source_ids,
                page_results=group,
                entity_keys=entity_keys,
                completion_state=completion_state,
                missing_requirements=missing_requirements,
                raw_business_object=raw_business_object,
            )
        )
    return assembled


__all__ = ["assemble_page_results"]
