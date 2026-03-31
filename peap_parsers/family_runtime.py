from __future__ import annotations

from peap_core import Diagnostic, SourceMatch

from .base import ParserContext, WebPageParser
from .parser_registry import ParserFamilyBinding, ParserRegistry


def _build_page_identity(binding: ParserFamilyBinding, source_match: SourceMatch, document, data: dict[str, object]) -> dict[str, object]:
    project_code = str(data.get("项目编号") or data.get("project_code") or "").strip()
    project_name = str(data.get("项目名称") or data.get("project_name") or "").strip()
    return {
        "page_kind": binding.page_kind or source_match.page_kind,
        "project_code": project_code,
        "project_id": project_code,
        "page_url": str(document.metadata.get("source_url") or "").strip(),
        "listing_date": str(data.get("挂牌开始日期") or data.get("start_date") or "").strip(),
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
        return (
            (
                Diagnostic(
                    severity="warn",
                    type="parse_partial",
                    message="missing project code",
                    stage="parse",
                    evidence_refs=(),
                    recoverability="partial",
                ),
            ),
            "partial",
        )
    return (), "none"


def parse_document_with_registry(*, document, source_match: SourceMatch, registry: ParserRegistry, context: ParserContext):
    binding = registry.resolve(source_match, document=document)
    parser: WebPageParser = binding.parser_cls(str(document.dom), context=context)
    parse_result = parser.parse()
    if hasattr(parse_result, "compat_payload"):
        data = dict(parse_result.compat_payload)
    else:
        data = dict(parse_result)

    diagnostics, recoverability = _build_diagnostics(data)
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
