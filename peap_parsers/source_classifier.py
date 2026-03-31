from __future__ import annotations

from peap_core import DecodedDocument, SourceMatch

from .source_detection_rules import RuleMatch, collect_source_rule_matches

CLASSIFIER_VERSION = "source_classifier/v1"


def _group_reasons(matches: list[RuleMatch], source_id: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in matches:
        if match.source_id != source_id:
            continue
        if match.reason not in seen:
            seen.append(match.reason)
    return tuple(seen)


def classify_decoded_document(document: DecodedDocument) -> SourceMatch:
    matches = collect_source_rule_matches(document)
    if not matches:
        return SourceMatch(
            source_id="",
            page_kind="listing",
            confidence=0.0,
            status="unknown",
            reasons=("no source markers matched",),
            classifier_version=CLASSIFIER_VERSION,
        )

    unique_sources: list[str] = []
    for match in matches:
        if match.source_id not in unique_sources:
            unique_sources.append(match.source_id)

    if len(unique_sources) > 1:
        return SourceMatch(
            source_id="",
            page_kind=matches[0].page_kind,
            confidence=max(match.confidence for match in matches),
            status="ambiguous",
            reasons=tuple(match.reason for match in matches),
            classifier_version=CLASSIFIER_VERSION,
        )

    source_id = unique_sources[0]
    selected = next(match for match in matches if match.source_id == source_id)
    return SourceMatch(
        source_id=source_id,
        page_kind=selected.page_kind,
        confidence=max(match.confidence for match in matches if match.source_id == source_id),
        status="matched",
        reasons=_group_reasons(matches, source_id),
        classifier_version=CLASSIFIER_VERSION,
    )


def detect_source_from_content(raw_content: str) -> str | None:
    document_kind = "mhtml" if "content-type: multipart/related" in raw_content.lower() else "html"
    match = classify_decoded_document(
        DecodedDocument(
            snapshot_id="compat-detect",
            document_kind=document_kind,
            primary_text=raw_content,
            dom=raw_content,
            metadata={},
            decoder_version=CLASSIFIER_VERSION,
        )
    )
    if match.status == "matched":
        return match.source_id
    matches = collect_source_rule_matches(
        DecodedDocument(
            snapshot_id="compat-detect",
            document_kind=document_kind,
            primary_text=raw_content,
            dom=raw_content,
            metadata={},
            decoder_version=CLASSIFIER_VERSION,
        )
    )
    return matches[0].source_id if matches else None


__all__ = ["CLASSIFIER_VERSION", "classify_decoded_document", "detect_source_from_content"]
