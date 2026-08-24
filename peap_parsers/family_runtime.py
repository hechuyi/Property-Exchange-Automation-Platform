from __future__ import annotations

from dataclasses import replace

from peap_core import Diagnostic, SourceMatch
from peap_core.source_catalog import canonical_source_code

from .base import ParserContext, ParserOutput, WebPageParser
from .parser_registry import ParserFamilyBinding, ParserRegistry


def _canonical_registry_source_id(source_id: object) -> str:
    text = str(source_id or "").strip()
    return str(
        canonical_source_code(
            text,
            allow_substring=False,
            allowed_source_ids={"guangdong"},
        )
        or text
    ).strip()


def _build_page_identity(binding: ParserFamilyBinding, source_match: SourceMatch, document, data: dict[str, object]) -> dict[str, object]:
    project_code = str(data.get("项目编号") or data.get("project_code") or "").strip()
    project_name = str(data.get("项目名称") or data.get("project_name") or "").strip()
    record_family = str(data.get("record_family") or source_match.page_kind or "").strip()
    business_id = str(data.get("business_id") or "").strip()
    business_type = str(data.get("项目类型") or data.get("business_type") or data.get("project_type") or "").strip()
    raw_source_id = str(data.get("source_id") or source_match.source_id or "").strip()
    source_id = _canonical_registry_source_id(raw_source_id)
    if record_family == "deal":
        listing_date = str(
            data.get("collection_date")
            or data.get("采集日期")
            or data.get("fbsj")
            or data.get("start_date")
            or data.get("listing_date")
            or data.get("成交日期")
            or data.get("deal_date")
            or ""
        ).strip()
    else:
        listing_date = str(
            data.get("挂牌开始日期")
            or data.get("start_date")
            or data.get("成交日期")
            or data.get("deal_date")
            or ""
        ).strip()
    return {
        "page_kind": binding.page_kind or source_match.page_kind,
        "record_family": record_family,
        "business_id": business_id,
        "business_type": business_type,
        "source_id": source_id,
        "project_code": project_code,
        "project_id": project_code,
        "page_url": str(document.metadata.get("source_url") or "").strip(),
        "listing_date": listing_date,
        "candidate_tokens": tuple(token for token in (project_code, project_name) if token),
    }


def _build_facts(data: dict[str, object]) -> tuple[dict[str, object], ...]:
    preferred_order = ["项目名称", "项目编号"]
    seen: set[str] = set()
    facts: list[dict[str, object]] = []
    for key in preferred_order:
        value = data.get(key)
        if value not in (None, ""):
            facts.append({"field": key, "value": value})
            seen.add(key)
    for key, value in data.items():
        if key in seen or value in (None, ""):
            continue
        facts.append({"field": key, "value": value})
    return tuple(facts)


def _build_diagnostics(data: dict[str, object]) -> tuple[tuple[Diagnostic, ...], str]:
    diagnostics: list[Diagnostic] = []
    project_code = str(data.get("项目编号") or data.get("project_code") or "").strip()
    project_name = str(data.get("项目名称") or data.get("project_name") or "").strip()
    if not project_code and not project_name:
        return (
            (
                Diagnostic(
                    severity="error",
                    type="parse_unrecoverable",
                    message="missing project identity",
                    stage="parse",
                    evidence_refs=(),
                    recoverability="unrecoverable",
                ),
            ),
            "unrecoverable",
        )
    if not project_code:
        diagnostics.append(
            Diagnostic(
                severity="warn",
                type="parse_partial",
                message="missing project code",
                stage="parse",
                evidence_refs=(),
                recoverability="partial",
            )
        )
    return tuple(diagnostics), "partial" if diagnostics else "none"


def _has_unknown_source_identity(source_id: object, source_match: SourceMatch) -> bool:
    normalized_source_id = str(source_id or "").strip().lower()
    return source_match.status == "unknown" or normalized_source_id in {"", "mystery", "unknown"}


def _unknown_family_values(*values: object) -> tuple[str, ...]:
    unknown_values: list[str] = []
    for value in values:
        normalized_value = str(value or "").strip()
        if not normalized_value:
            continue
        if normalized_value.lower() in {"unknown", "mystery"}:
            unknown_values.append(normalized_value)
    return tuple(unknown_values)


def _append_identity_diagnostics(
    diagnostics: tuple[Diagnostic, ...],
    recoverability: str,
    *,
    data: dict[str, object],
    source_match: SourceMatch,
    binding: ParserFamilyBinding,
) -> tuple[tuple[Diagnostic, ...], str]:
    source_id = str(data.get("source_id") or source_match.source_id or "").strip()
    record_family = str(data.get("record_family") or source_match.page_kind or "").strip()
    unknown_family_values = _unknown_family_values(record_family, source_match.page_kind, binding.page_kind)
    appended = list(diagnostics)

    if _has_unknown_source_identity(source_id, source_match):
        appended.append(
            Diagnostic(
                severity="warn",
                type="parse_partial",
                message=f"unknown source_id {source_id or '<empty>'}",
                stage="parse",
                evidence_refs=(),
                recoverability="partial",
            )
        )
    for value in unknown_family_values:
        appended.append(
            Diagnostic(
                severity="warn",
                type="parse_partial",
                message=f"unknown page_kind or record_family {value}",
                stage="parse",
                evidence_refs=(),
                recoverability="partial",
            )
        )
    if appended != list(diagnostics) and recoverability == "none":
        recoverability = "partial"
    return tuple(appended), recoverability


def parse_document_with_registry(*, document, source_match: SourceMatch, registry: ParserRegistry, context: ParserContext):
    binding = registry.resolve(source_match, document=document)
    if binding.page_kind == "deal" or source_match.page_kind == "deal":
        context = replace(context, allow_unknown_deal_business_type=True)
    parser: WebPageParser = binding.parser_cls(str(document.dom), context=context)
    parse_result = parser.parse()
    if isinstance(parse_result, ParserOutput):
        data = dict(parse_result.standard_payload)
    else:
        data = dict(parse_result)
    raw_source_id = str(data.get("source_id") or source_match.source_id or "").strip()
    canonical_source_id = _canonical_registry_source_id(raw_source_id)
    if canonical_source_id:
        data["source_id"] = canonical_source_id

    diagnostics, recoverability = _build_diagnostics(data)
    diagnostics, recoverability = _append_identity_diagnostics(
        diagnostics,
        recoverability,
        data=data,
        source_match=source_match,
        binding=binding,
    )
    return parser.build_page_parse_result(
        snapshot_id=document.snapshot_id,
        source_match=source_match,
        parser_family_id=binding.family_id,
        parser_family_version=binding.family_version,
        variant_id=binding.variant_id,
        variant_version=binding.variant_version,
        page_identity=_build_page_identity(binding, source_match, document, data),
        facts=_build_facts(data),
        diagnostics=diagnostics,
        recoverability=recoverability,
    )


__all__ = ["parse_document_with_registry"]
