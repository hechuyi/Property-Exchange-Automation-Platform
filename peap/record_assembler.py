from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

from peap_core import AssembledRecordCandidate, PageParseResult
from peap_core.business_catalog import resolve_business_descriptor_by_project_type
from peap_core.family_catalog import get_family_descriptor
from peap_core.source_catalog import resolve_source_descriptor


def _page_kind(result: PageParseResult) -> str:
    return str(result.page_identity.get("page_kind") or result.source_match.page_kind or "").strip()


def _identity_mapping(result: PageParseResult, key: str) -> Mapping[str, object]:
    payload = result.page_identity.get(key)
    if isinstance(payload, Mapping):
        return payload
    return {}


def _canonical_source_id(result: PageParseResult) -> str:
    raw_source_id = _source_identity_candidate(result)
    if not raw_source_id:
        return ""
    descriptor = resolve_source_descriptor(raw_source_id, allow_substring=True)
    return descriptor.source_id if descriptor is not None else ""


def _source_identity_candidate(result: PageParseResult) -> str:
    source_identity = _identity_mapping(result, "source_identity")
    candidates = (
        source_identity.get("source_id"),
        source_identity.get("exchange"),
        result.page_identity.get("source_id"),
        result.page_identity.get("exchange"),
        result.source_match.source_id,
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _unknown_source_diagnostics(results: tuple[PageParseResult, ...]) -> tuple[dict[str, str], ...]:
    diagnostics: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        source_id = _source_identity_candidate(result)
        if not source_id or _canonical_source_id(result):
            continue
        key = (result.snapshot_id, source_id)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            {
                "code": "unknown_source_identity",
                "source_id": source_id,
                "snapshot_id": result.snapshot_id,
            }
        )
    return tuple(diagnostics)


def _record_family(result: PageParseResult) -> str:
    source_identity = _identity_mapping(result, "source_identity")
    business_identity = _identity_mapping(result, "business_identity")
    candidates = (
        result.page_identity.get("record_family"),
        source_identity.get("record_family"),
        business_identity.get("record_family"),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            return get_family_descriptor(text).family_id
        except KeyError:
            continue
    page_kind = _page_kind(result)
    if page_kind:
        try:
            return get_family_descriptor(page_kind).family_id
        except KeyError:
            pass
    return ""


def _business_id(result: PageParseResult) -> str:
    source_identity = _identity_mapping(result, "source_identity")
    business_identity = _identity_mapping(result, "business_identity")
    candidates = (
        result.page_identity.get("business_id"),
        business_identity.get("business_id"),
        source_identity.get("business_id"),
        source_identity.get("business_id_hint"),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _business_id_from_project_type(*, record_family: str, project_type: str) -> str:
    try:
        descriptor = resolve_business_descriptor_by_project_type(
            project_type,
            family_id=record_family,
        )
    except KeyError:
        descriptor = None
    return descriptor.business_id if descriptor is not None else ""


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
    value = _fact_value(result, *field_names)
    if value in (None, ""):
        return ""
    return str(value).strip()


def _fact_field_with_name(result: PageParseResult, *field_names: str) -> tuple[str, str]:
    """Extract a field value plus the matched fact field name."""
    for fact in result.facts:
        if not isinstance(fact, Mapping):
            continue
        field_name = str(fact.get("field") or "").strip()
        if field_name not in field_names:
            continue
        value = fact.get("value")
        if value in (None, ""):
            continue
        return str(value).strip(), field_name
    return "", ""


def _fact_value(result: PageParseResult, *field_names: str) -> object:
    """Extract a raw fact value from result facts, returning empty string if not found."""
    for fact in result.facts:
        if not isinstance(fact, Mapping):
            continue
        if str(fact.get("field") or "").strip() in field_names:
            return fact.get("value")
    return ""


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return False


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
    record_family = next((value for value in (_record_family(result) for result in results) if value), "")
    business_id = next((value for value in (_business_id(result) for result in results) if value), "")
    business_type = next((value for value in (_fact_field(r, "business_type", "project_type", "项目类型") for r in results) if value), "")
    if not business_id and record_family and business_type:
        business_id = _business_id_from_project_type(
            record_family=record_family,
            project_type=business_type,
        )
    status = next((value for value in (_fact_field(r, "status", "项目状态") for r in results) if value), "")
    listing_start_date = next(
        (
            value
            for value in (
                _fact_field(
                    r,
                    "start_date",
                    "listing_date",
                    "挂牌日期",
                    "开始日期",
                )
                for r in results
            )
            if value
        ),
        "",
    )
    collection_date = next((
        value
        for value in (
            _fact_field(r, "collection_date", "采集日期", "collectionDate", "fbsj", "publishDate")
            for r in results
        )
        if value
    ), "")
    price = next((value for value in (_fact_field(r, "price", "价格", "挂牌价格") for r in results) if value), "")
    seller = next((value for value in (_fact_field(r, "seller", "转让方", "融资方") for r in results) if value), "")
    source_type = next((value for value in (_fact_field(r, "source_type", "类型") for r in results) if value), "")
    group_name = next((value for value in (_fact_field(r, "group_name", "隶属集团", "集团名称") for r in results) if value), "")
    deal_date = next((
        value
        for value in (
            _fact_field(r, "deal_date", "成交日期", "contractSignTime", "contract_sign_time", "CJRQ")
            for r in results
        )
        if value
    ), "")
    start_date = deal_date or collection_date if record_family == "deal" else collection_date or listing_start_date
    deal_date_basis = next((
        value
        for value in (_fact_field(r, "deal_date_basis") for r in results)
        if value
    ), "")
    raw_deal_date_is_imputed = next((
        value
        for value in (_fact_value(r, "deal_date_is_imputed") for r in results)
        if value not in (None, "")
    ), None)
    deal_date_is_imputed = _coerce_bool(raw_deal_date_is_imputed)
    if (
        record_family == "deal"
        and deal_date
        and deal_date_is_imputed
        and deal_date_basis in {"", "collection_date"}
    ):
        collection_date = collection_date or deal_date
        deal_date = ""
        deal_date_basis = "collection_date"
    deal_price = ""
    deal_price_unit_hint = ""
    for result in results:
        value, matched_field = _fact_field_with_name(
            result,
            "deal_price",
            "dealPrice",
            "CJJG",
            "cjjg",
            "tradevalue",
            "transactionPrice",
            "交易价格",
            "交易价格（万元）",
            "交易价格（元）",
            "交易价格（亿元）",
            "成交金额",
            "成交金额（万元）",
            "成交金额（元）",
            "成交金额（亿元）",
            "成交价格",
            "成交价",
            "dealAmount",
        )
        if value:
            deal_price = value
            deal_price_unit_hint = matched_field
            break
    valuation = next((
        value
        for value in (
            _fact_field(r, "valuation", "valuationValue", "DWPGZ", "PGZ", "DJPGZ", "转让标的评估值", "转让标的评估结果", "评估值")
            for r in results
        )
        if value
    ), "")
    reserve_price = next((
        value
        for value in (
            _fact_field(r, "reserve_price", "reservePrice", "ZRDJ", "ZRDANJ", "转让底价", "转让底价（万元）", "挂牌底价")
            for r in results
        )
        if value
    ), "")
    export_extras: dict[str, object] = {}
    for output_field, aliases in {
        "交易方式": ("deal_method", "交易方式"),
        "受让方名称": ("buyer_name", "受让方名称"),
        "备注": ("remark", "备注"),
        "是否竞价": ("auction_flag", "是否竞价"),
        "是否成交": ("deal_status", "是否成交"),
        "investors": ("investors", "investorList"),
        "transferors": ("transferors", "transferorNames"),
        "financing_party_names": ("financing_party_names", "financingPartyNames"),
        "project_parties": ("project_parties", "projectParties", "partyList"),
    }.items():
        value = next((candidate for candidate in (_fact_value(r, *aliases) for r in results) if candidate not in (None, "", [], (), {})), "")
        if value not in (None, "", [], (), {}):
            export_extras[output_field] = value
    source_ids = tuple(sorted({_canonical_source_id(result) for result in results if _canonical_source_id(result)}))
    source_id = source_ids[0] if source_ids else ""
    business_fields = {
        "business_type": business_type,
        "status": status,
        "start_date": start_date,
        "price": price,
        "seller": seller,
        "source_type": source_type,
        "group_name": group_name,
    }
    if record_family == "deal":
        business_fields.update(
            {
                "deal_date": deal_date,
                "deal_date_basis": deal_date_basis,
                "deal_date_is_imputed": deal_date_is_imputed,
                "collection_date": collection_date,
                "deal_price": deal_price or price,
                "deal_price_unit_hint": deal_price_unit_hint,
                "valuation": valuation,
                "reserve_price": reserve_price,
            }
        )
    return {
        "record_family": record_family,
        "business_id": business_id,
        "project_code": project_code,
        "project_name": project_name,
        "source_identity": {
            "record_family": record_family,
            "business_id": business_id,
            "source_id": source_id,
            "source_ids": source_ids,
        },
        "business_identity": {
            "record_family": record_family,
            "business_id": business_id,
            "project_code": project_code,
            "project_name": project_name,
            "business_type": business_type,
        },
        "business_fields": business_fields,
        "export_extras": export_extras,
        "page_kinds": page_kinds,
        "page_urls": [_page_url(result) for result in results if _page_url(result)],
    }


def _missing_requirements(results: tuple[PageParseResult, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    if not any(_canonical_source_id(result) for result in results):
        missing.append("source_id")
    if not any(_record_family(result) for result in results):
        missing.append("record_family")
    if not any(_project_code(result) for result in results):
        missing.append("project_code")
    if not any(_project_name(result) for result in results):
        missing.append("project_name")
    return tuple(missing)


def _completion_state(results: tuple[PageParseResult, ...]) -> tuple[str, tuple[str, ...]]:
    names = {name for name in (_project_name(result) for result in results) if name}
    if len(names) > 1:
        return "conflicted", ("project_name_conflict",)
    missing = _missing_requirements(results)
    if missing:
        return "partial", missing
    return "sufficient", ()


def _assembly_id(
    source_id: str,
    entity_keys: tuple[str, ...],
    *,
    record_family: str,
    business_id: str,
) -> str:
    digest = hashlib.sha256(
        "|".join((record_family, business_id, source_id, *entity_keys)).encode("utf-8")
    ).hexdigest()[:12]
    return f"asm-{digest}"


def _belongs_in_group(candidate: PageParseResult, grouped: tuple[PageParseResult, ...]) -> bool:
    candidate_family = _record_family(candidate)
    grouped_families = {value for value in (_record_family(item) for item in grouped) if value}
    if grouped_families:
        if candidate_family and candidate_family not in grouped_families:
            return False
    elif candidate_family and any(_record_family(item) for item in grouped):
        return False

    candidate_business_id = _business_id(candidate)
    grouped_business_ids = {value for value in (_business_id(item) for item in grouped) if value}
    if grouped_business_ids:
        if candidate_business_id and candidate_business_id not in grouped_business_ids:
            return False

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
        source_ids = tuple(sorted({_canonical_source_id(result) for result in group if _canonical_source_id(result)}))
        entity_keys = _entity_keys_for_group(group)
        completion_state, missing_requirements = _completion_state(group)
        assembly_diagnostics = _unknown_source_diagnostics(group)
        raw_business_object = _business_object(group)
        assembly_id = _assembly_id(
            source_ids[0] if source_ids else "unknown",
            entity_keys,
            record_family=str(raw_business_object.get("record_family") or ""),
            business_id=str(raw_business_object.get("business_id") or ""),
        )
        assembled.append(
            AssembledRecordCandidate(
                assembly_id=assembly_id,
                source_ids=source_ids,
                page_results=group,
                entity_keys=entity_keys,
                completion_state=completion_state,
                missing_requirements=missing_requirements,
                assembly_diagnostics=assembly_diagnostics,
                raw_business_object=raw_business_object,
            )
        )
    return assembled


__all__ = ["assemble_page_results"]
