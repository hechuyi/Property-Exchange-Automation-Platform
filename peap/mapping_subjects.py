"""Mapping match-subject extraction helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class MappingSubject:
    name: str
    ratio: float | None = None
    primary: bool = False


@dataclass(frozen=True)
class MappingSubjectResolution:
    raw_value: str
    match_field: str
    subjects: tuple[MappingSubject, ...] = field(default_factory=tuple)
    primary_subject: str = ""
    ambiguous: bool = False
    reason_code: str = ""

    def match_names(self) -> tuple[str, ...]:
        if self.primary_subject:
            return (self.primary_subject,)
        if self.ambiguous:
            return (self.raw_value,) if self.raw_value else ()
        return tuple(item.name for item in self.subjects if item.name)


@dataclass(frozen=True)
class _ParsedTransferorSubjects:
    subjects: tuple[MappingSubject, ...] = field(default_factory=tuple)
    partial_ratio_coverage: bool = False


def normalize_match_text(value: Any) -> str:
    # Mapping keys must treat full-width and compatibility Unicode forms as
    # the same subject while preserving the original display name elsewhere.
    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    return " ".join(normalized.lower().split())


def resolve_mapping_subject(raw_value: Any, *, match_field: str) -> MappingSubjectResolution:
    field = str(match_field or "").strip().lower() or "transferor"
    text = str(raw_value or "").strip()
    if not text:
        return MappingSubjectResolution(raw_value="", match_field=field, reason_code="empty")
    if field != "transferor":
        return MappingSubjectResolution(
            raw_value=text,
            match_field=field,
            subjects=(MappingSubject(text, primary=True),),
            primary_subject=text,
            reason_code="single_subject",
        )
    return _resolve_transferor_subject(text, match_field=field)


def match_subject_names(raw_value: Any, *, match_field: str) -> tuple[str, ...]:
    return resolve_mapping_subject(raw_value, match_field=match_field).match_names()


def first_match_subject(raw_value: Any, *, match_field: str) -> str:
    names = match_subject_names(raw_value, match_field=match_field)
    return names[0] if names else ""


def subject_matches_source(raw_value: Any, *, match_field: str, source_name: Any) -> bool:
    normalized_source = normalize_match_text(source_name)
    if not normalized_source:
        return False
    return any(normalize_match_text(item) == normalized_source for item in match_subject_names(raw_value, match_field=match_field))


def _resolve_transferor_subject(text: str, *, match_field: str) -> MappingSubjectResolution:
    parsed = _parse_transferor_subjects(text)
    subjects = parsed.subjects
    if not subjects:
        return MappingSubjectResolution(raw_value=text, match_field=match_field, reason_code="unparsed")
    if len(subjects) == 1:
        subject = MappingSubject(subjects[0].name, subjects[0].ratio, primary=True)
        return MappingSubjectResolution(
            raw_value=text,
            match_field=match_field,
            subjects=(subject,),
            primary_subject=subject.name,
            reason_code="single_subject",
        )
    if parsed.partial_ratio_coverage:
        return MappingSubjectResolution(
            raw_value=text,
            match_field=match_field,
            subjects=subjects,
            ambiguous=True,
            reason_code="partial_ratio_coverage",
        )
    ratio_subjects = [item for item in subjects if item.ratio is not None]
    if not ratio_subjects:
        return MappingSubjectResolution(
            raw_value=text,
            match_field=match_field,
            subjects=subjects,
            ambiguous=True,
            reason_code="multiple_subjects_without_ratios",
        )
    max_ratio = max(item.ratio or 0.0 for item in ratio_subjects)
    top_subjects = [item for item in ratio_subjects if item.ratio == max_ratio]
    if len(top_subjects) != 1:
        return MappingSubjectResolution(
            raw_value=text,
            match_field=match_field,
            subjects=subjects,
            ambiguous=True,
            reason_code="tied_primary_ratio",
        )
    primary_name = top_subjects[0].name
    marked = tuple(MappingSubject(item.name, item.ratio, primary=item.name == primary_name) for item in subjects)
    return MappingSubjectResolution(
        raw_value=text,
        match_field=match_field,
        subjects=marked,
        primary_subject=primary_name,
        reason_code="primary_ratio",
    )


def _parse_transferor_subjects(text: str) -> _ParsedTransferorSubjects:
    normalized_text = _normalize_subject_source_text(text)
    parsed_subjects, partial_ratio_coverage = _parse_parenthesized_ratio_subjects(normalized_text)
    if not parsed_subjects:
        parsed_subjects, partial_ratio_coverage = _parse_delimited_inline_ratio_subjects(normalized_text)
    if not parsed_subjects:
        parsed_subjects, partial_ratio_coverage = _parse_trailing_ratio_subjects(normalized_text)
    if not parsed_subjects:
        parsed_subjects = [MappingSubject(name=part, ratio=None) for part in _split_unrated_subjects(normalized_text)]
    return _ParsedTransferorSubjects(
        subjects=tuple(_dedupe_subjects(parsed_subjects)),
        partial_ratio_coverage=partial_ratio_coverage,
    )


def _dedupe_subjects(subjects: Iterable[MappingSubject]) -> Iterable[MappingSubject]:
    seen: set[str] = set()
    for subject in subjects:
        normalized = normalize_match_text(re.sub(r"\s+", "", subject.name))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield subject


def _normalize_subject_source_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _parse_parenthesized_ratio_subjects(text: str) -> tuple[list[MappingSubject], bool]:
    ratio_pattern = re.compile(
        r"\(\s*(?:(?:占\s*)?(?:持股比例|持股|出资比例|股权比例|比例)|占)?\s*(\d+(?:\.\d+)?)\s*%?\s*\)"
    )
    matches = list(ratio_pattern.finditer(text))
    if not matches:
        return [], False

    subjects: list[MappingSubject] = []
    partial_ratio_coverage = False
    cursor = 0
    for match in matches:
        segment_parts = _split_unrated_subjects(text[cursor : match.start()])
        if len(segment_parts) > 1:
            partial_ratio_coverage = True
            subjects.extend(MappingSubject(name=part, ratio=None) for part in segment_parts[:-1])
        if segment_parts:
            subjects.append(MappingSubject(name=segment_parts[-1], ratio=float(match.group(1))))
        cursor = match.end()

    tail_parts = _split_unrated_subjects(text[cursor:])
    if tail_parts:
        partial_ratio_coverage = True
        subjects.extend(MappingSubject(name=part, ratio=None) for part in tail_parts)
    return subjects, partial_ratio_coverage


def _parse_delimited_inline_ratio_subjects(text: str) -> tuple[list[MappingSubject], bool]:
    raw_parts = [part.strip() for part in re.split(r"[，、；;/|]+", text) if part.strip()]
    if len(raw_parts) <= 1:
        return [], False

    ratio_pattern = re.compile(
        r"^(?P<name>.+?)\s*(?:(?::|：)\s*|(?:(?:占\s*)?(?:持股比例|持股|出资比例|股权比例|比例)|占)\s*)"
        r"(?P<ratio>\d+(?:\.\d+)?)\s*%$"
    )
    subjects: list[MappingSubject] = []
    parsed_any = False
    partial_ratio_coverage = False
    for part in raw_parts:
        match = ratio_pattern.match(part)
        if match:
            name = _clean_subject_name(match.group("name"))
            if name:
                parsed_any = True
                subjects.append(MappingSubject(name=name, ratio=float(match.group("ratio"))))
            continue
        cleaned = _clean_subject_name(part)
        if cleaned:
            partial_ratio_coverage = True
            subjects.append(MappingSubject(name=cleaned, ratio=None))
    if not parsed_any:
        return [], False
    return subjects, partial_ratio_coverage


def _parse_trailing_ratio_subjects(text: str) -> tuple[list[MappingSubject], bool]:
    trailing_pattern = re.compile(r"(.+?)\s+(\d+(?:\.\d+)?)\s*%")
    trailing_matches = list(trailing_pattern.finditer(text))
    if not trailing_matches:
        return [], False

    subjects: list[MappingSubject] = []
    partial_ratio_coverage = False
    cursor = 0
    for match in trailing_matches:
        segment_parts = _split_unrated_subjects(text[cursor : match.start(2)])
        if len(segment_parts) > 1:
            partial_ratio_coverage = True
            subjects.extend(MappingSubject(name=part, ratio=None) for part in segment_parts[:-1])
        if segment_parts:
            subjects.append(MappingSubject(name=segment_parts[-1], ratio=float(match.group(2))))
        cursor = match.end()

    tail_parts = _split_unrated_subjects(text[cursor:])
    if tail_parts:
        partial_ratio_coverage = True
        subjects.extend(MappingSubject(name=part, ratio=None) for part in tail_parts)
    return subjects, partial_ratio_coverage


def _split_unrated_subjects(text: str) -> list[str]:
    parts = [
        cleaned
        for item in re.split(r"[，、；;/|]+", text)
        if (cleaned := _clean_subject_name(item))
    ]
    if len(parts) != 1:
        return parts

    value = parts[0]
    suffixes = (
        "有限责任公司",
        "股份有限公司",
        "有限公司",
        "集团公司",
        "集团",
        "公司",
        "企业",
        "中心",
        "厂",
        "院",
        "所",
        "社",
    )
    suffix_pattern = "|".join(re.escape(item) for item in suffixes)
    matches = list(re.finditer(rf".+?(?:{suffix_pattern})(?=\s+|$)", value))
    if len(matches) <= 1:
        return parts
    extracted: list[str] = []
    cursor = 0
    for match in matches:
        between = value[cursor : match.start()]
        if between.strip():
            return parts
        extracted.append(match.group(0))
        cursor = match.end()
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
    if value[cursor:].strip():
        return parts
    return extracted


def _clean_subject_name(value: str) -> str:
    cleaned = re.sub(r"^[\s,，、；;/|]+|[\s,，、；;/|]+$", "", str(value or "")).strip()
    return re.sub(r"^(?:转让方|融资方|出让方|受让方|投资方|股东|标的企业|企业名称)\s*[:：]\s*", "", cleaned).strip()


__all__ = [
    "MappingSubject",
    "MappingSubjectResolution",
    "first_match_subject",
    "match_subject_names",
    "normalize_match_text",
    "resolve_mapping_subject",
    "subject_matches_source",
]
