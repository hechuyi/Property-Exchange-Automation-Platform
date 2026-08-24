"""Audit raw download archives and duplicate-download verification reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Iterable

from .downloaders.discovery_contract import expected_discovery_query_ids


@dataclass(frozen=True)
class DownloadArchiveAuditIssue:
    code: str
    path: str
    message: str
    task_id: str = ""
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "task_id": self.task_id,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class DownloadArchiveAuditResult:
    root: str
    ok: bool
    html_count: int = 0
    sidecar_count: int = 0
    issue_count: int = 0
    issues: tuple[DownloadArchiveAuditIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "ok": self.ok,
            "html_count": self.html_count,
            "sidecar_count": self.sidecar_count,
            "issue_count": self.issue_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class DiscoveryEvidenceAuditResult:
    root: str
    ok: bool
    task_count: int = 0
    manifest_count: int = 0
    page_count: int = 0
    issue_count: int = 0
    issues: tuple[DownloadArchiveAuditIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "ok": self.ok,
            "task_count": self.task_count,
            "manifest_count": self.manifest_count,
            "page_count": self.page_count,
            "issue_count": self.issue_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def audit_download_archive_root(
    root: str,
    *,
    require_detail_sidecar: bool = False,
) -> DownloadArchiveAuditResult:
    root_path = os.path.abspath(os.fspath(root))
    issues: list[DownloadArchiveAuditIssue] = []
    html_count = 0
    sidecar_count = 0

    if not os.path.isdir(root_path):
        issue = DownloadArchiveAuditIssue(
            code="archive_root_missing",
            path=root_path,
            message="download archive root does not exist or is not a directory",
        )
        return DownloadArchiveAuditResult(
            root=root_path,
            ok=False,
            issue_count=1,
            issues=(issue,),
        )

    for current_dir, dirnames, filenames in os.walk(root_path):
        rel_dir = os.path.relpath(current_dir, root_path)
        rel_parts = () if rel_dir == "." else tuple(rel_dir.split(os.sep))
        if "_evidence" in rel_parts:
            dirnames[:] = []
            continue

        for dirname in tuple(dirnames):
            if _is_partial_name(dirname):
                issues.append(
                    _issue(
                        "partial_artifact_leftover",
                        os.path.join(current_dir, dirname),
                        "partial download artifact remains in archive tree",
                    )
                )

        for filename in filenames:
            path = os.path.join(current_dir, filename)
            if _is_partial_name(filename):
                issues.append(
                    _issue(
                        "partial_artifact_leftover",
                        path,
                        "partial download artifact remains in archive tree",
                    )
                )
            if not filename.lower().endswith((".html", ".mhtml", ".mht")):
                continue
            html_count += 1
            expected_scope = _expected_scope_from_path(root_path, path)
            if not expected_scope:
                issues.append(
                    _issue(
                        "archive_scope_missing",
                        path,
                        "archive html is not under a source__record_family__business task root",
                    )
                )
            evidence_path = f"{path}.peap-evidence.json"
            if os.path.isfile(evidence_path):
                evidence_payload = _read_json_object(evidence_path)
                if evidence_payload is None:
                    issues.append(
                        _issue(
                            "invalid_artifact_evidence",
                            evidence_path,
                            "archive html has an invalid artifact evidence sidecar",
                            task_id=expected_scope.get("task_id", ""),
                        )
                    )
                elif str(evidence_payload.get("page_kind") or "").strip().lower() == "invalid_shell":
                    issues.append(
                        _issue(
                            "invalid_shell_evidence",
                            evidence_path,
                            "archive html is accompanied by invalid_shell evidence",
                            task_id=expected_scope.get("task_id", ""),
                        )
                    )
            sidecar_path = _archive_sidecar_path(
                path,
                require_detail_sidecar=require_detail_sidecar,
            )
            if not os.path.isfile(sidecar_path):
                issues.append(
                    _issue(
                        "missing_detail_sidecar" if require_detail_sidecar else "missing_sidecar",
                        path,
                        (
                            "archive html is missing its required same-stem JSON sidecar"
                            if require_detail_sidecar
                            else "archive html is missing its same-stem JSON sidecar"
                        ),
                        task_id=expected_scope.get("task_id", ""),
                    )
                )
                continue
            sidecar_count += 1
            payload = _read_json_object(sidecar_path)
            if payload is None:
                issues.append(
                    _issue(
                        "invalid_sidecar_json",
                        sidecar_path,
                        "archive sidecar is not a valid JSON object",
                        task_id=expected_scope.get("task_id", ""),
                    )
                )
                continue
            issues.extend(_audit_sidecar(path, sidecar_path, payload, expected_scope))

    return DownloadArchiveAuditResult(
        root=root_path,
        ok=not issues,
        html_count=html_count,
        sidecar_count=sidecar_count,
        issue_count=len(issues),
        issues=tuple(issues),
    )


def audit_discovery_evidence_root(
    root: str,
    *,
    require_task_manifest: bool = True,
) -> DiscoveryEvidenceAuditResult:
    root_path = os.path.abspath(os.fspath(root))
    issues: list[DownloadArchiveAuditIssue] = []
    manifest_paths: list[str] = []
    task_manifest_paths: list[str] = []
    page_count = 0

    if not os.path.isdir(root_path):
        issue = _issue(
            "discovery_root_missing",
            root_path,
            "discovery evidence root does not exist or is not a directory",
        )
        return DiscoveryEvidenceAuditResult(
            root=root_path,
            ok=False,
            issue_count=1,
            issues=(issue,),
        )

    for current_dir, _dirnames, filenames in os.walk(root_path):
        relative = os.path.relpath(current_dir, root_path)
        path_parts = () if relative == "." else tuple(relative.split(os.sep))
        for filename in filenames:
            path = os.path.join(current_dir, filename)
            if _is_partial_name(filename):
                issues.append(
                    _issue(
                        "partial_artifact_leftover",
                        path,
                        "partial discovery artifact remains in evidence tree",
                    )
                )
            if filename == "manifest.json" and "discovery" in path_parts:
                manifest_paths.append(path)
            if filename == "task_manifest.json" and "discovery" in path_parts:
                task_manifest_paths.append(path)

    if not manifest_paths:
        issues.append(
            _issue(
                "discovery_manifest_missing",
                root_path,
                "no discovery query manifest was found",
            )
        )

    if require_task_manifest and not task_manifest_paths:
        issues.append(
            _issue(
                "discovery_task_manifest_missing",
                root_path,
                "no discovery task manifest was found",
            )
        )

    referenced_query_manifests: set[str] = set()
    for task_manifest_path in sorted(task_manifest_paths):
        task_manifest = _read_json_object(task_manifest_path)
        if task_manifest is None:
            issues.append(
                _issue(
                    "invalid_discovery_task_manifest",
                    task_manifest_path,
                    "discovery task manifest is not a valid JSON object",
                )
            )
            continue
        task_issues, task_references = _audit_discovery_task_manifest(
            task_manifest_path,
            task_manifest,
        )
        issues.extend(task_issues)
        referenced_query_manifests.update(task_references)

    for manifest_path in sorted(manifest_paths):
        manifest = _read_json_object(manifest_path)
        if manifest is None:
            issues.append(
                _issue(
                    "invalid_discovery_manifest",
                    manifest_path,
                    "discovery manifest is not a valid JSON object",
                )
            )
            continue
        manifest_issues, manifest_pages = _audit_discovery_manifest(manifest_path, manifest)
        issues.extend(manifest_issues)
        page_count += manifest_pages

    if require_task_manifest:
        for manifest_path in manifest_paths:
            if os.path.abspath(manifest_path) not in referenced_query_manifests:
                issues.append(
                    _issue(
                        "orphan_discovery_query_manifest",
                        manifest_path,
                        "discovery query manifest is not bound by a task manifest",
                    )
                )

    return DiscoveryEvidenceAuditResult(
        root=root_path,
        ok=not issues,
        task_count=len(task_manifest_paths),
        manifest_count=len(manifest_paths),
        page_count=page_count,
        issue_count=len(issues),
        issues=tuple(issues),
    )


def _audit_discovery_task_manifest(
    manifest_path: str,
    manifest: Mapping[str, object],
) -> tuple[list[DownloadArchiveAuditIssue], set[str]]:
    issues: list[DownloadArchiveAuditIssue] = []
    references: set[str] = set()
    task_id = _first_text(manifest, "task_id")
    manifest_dir = os.path.dirname(manifest_path)
    if _first_text(manifest, "evidence_kind") != "discovery_task":
        issues.append(
            _issue(
                "invalid_discovery_task_manifest_kind",
                manifest_path,
                "task manifest must declare evidence_kind=discovery_task",
                task_id=task_id,
            )
        )
    if _first_text(manifest, "save_status") != "complete":
        issues.append(
            _issue(
                "discovery_task_manifest_not_complete",
                manifest_path,
                "discovery task manifest is not marked save_status=complete",
                task_id=task_id,
            )
        )
    if _first_text(manifest, "coverage_status") != "complete":
        issues.append(
            _issue(
                "discovery_task_coverage_not_complete",
                manifest_path,
                "discovery task manifest does not prove complete query coverage",
                task_id=task_id,
            )
        )

    expected_raw = manifest.get("expected_query_ids")
    if not isinstance(expected_raw, list) or not expected_raw:
        issues.append(
            _issue(
                "discovery_expected_queries_missing",
                manifest_path,
                "discovery task manifest has no expected_query_ids",
                task_id=task_id,
            )
        )
        expected: list[str] = []
    else:
        expected = [str(value or "").strip() for value in expected_raw]
        if any(not value for value in expected) or len(set(expected)) != len(expected):
            issues.append(
                _issue(
                    "discovery_expected_queries_invalid",
                    manifest_path,
                    "expected_query_ids must contain unique non-empty strings",
                    task_id=task_id,
                )
            )

    task_parts = task_id.split(":")
    source_id = _first_text(manifest, "source_id")
    if len(task_parts) != 3 or task_parts[0] != source_id:
        issues.append(
            _issue(
                "discovery_registry_scope_invalid",
                manifest_path,
                "discovery task scope cannot be resolved against the source-business registry",
                task_id=task_id,
                details={"source_id": source_id},
            )
        )
    else:
        try:
            registry_expected = list(
                expected_discovery_query_ids(
                    source_id=source_id,
                    record_family=task_parts[1],
                    business_id=task_parts[2],
                )
            )
        except (KeyError, ValueError) as exc:
            issues.append(
                _issue(
                    "discovery_registry_contract_unavailable",
                    manifest_path,
                    "authoritative discovery query contract is unavailable",
                    task_id=task_id,
                    details={"error": str(exc)},
                )
            )
        else:
            if expected != registry_expected:
                issues.append(
                    _issue(
                        "discovery_registry_query_set_mismatch",
                        manifest_path,
                        "task manifest query set differs from the authoritative registry",
                        task_id=task_id,
                        details={
                            "registry_expected": registry_expected,
                            "manifest_expected": expected,
                        },
                    )
                )

    candidate_count = manifest.get("candidate_count")
    candidate_fingerprints_raw = manifest.get("candidate_fingerprints")
    candidate_set_valid = (
        isinstance(candidate_count, int)
        and not isinstance(candidate_count, bool)
        and candidate_count >= 0
        and isinstance(candidate_fingerprints_raw, list)
    )
    if candidate_set_valid:
        candidate_fingerprints = [
            str(value or "") for value in candidate_fingerprints_raw
        ]
        candidate_set_valid = (
            candidate_count == len(candidate_fingerprints)
            and candidate_fingerprints == sorted(set(candidate_fingerprints))
            and all(
                re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
                for value in candidate_fingerprints
            )
        )
    if not candidate_set_valid:
        issues.append(
            _issue(
                "discovery_candidate_set_invalid",
                manifest_path,
                "candidate_count and candidate_fingerprints must declare one unique canonical set",
                task_id=task_id,
            )
        )

    queries_raw = manifest.get("queries")
    if not isinstance(queries_raw, list):
        issues.append(
            _issue(
                "discovery_task_queries_missing",
                manifest_path,
                "discovery task manifest has no queries array",
                task_id=task_id,
            )
        )
        queries_raw = []

    observed: list[str] = []
    for item in queries_raw:
        if not isinstance(item, Mapping):
            issues.append(
                _issue(
                    "invalid_discovery_task_query",
                    manifest_path,
                    "discovery task query entry is not an object",
                    task_id=task_id,
                )
            )
            continue
        query_id = _first_text(item, "query_id")
        observed.append(query_id)
        relative_path = _first_text(item, "manifest_path")
        query_manifest_path = _safe_child_path(manifest_dir, relative_path)
        if query_manifest_path is None or not os.path.isfile(query_manifest_path):
            issues.append(
                _issue(
                    "discovery_query_manifest_missing",
                    query_manifest_path or manifest_path,
                    "task-bound discovery query manifest is missing or unsafe",
                    task_id=task_id,
                    details={"query_id": query_id, "manifest_path": relative_path},
                )
            )
            continue
        references.add(os.path.abspath(query_manifest_path))
        expected_hash = _first_text(item, "manifest_sha256")
        actual_hash = _sha256_file(query_manifest_path)
        if not _hash_text_is_valid(expected_hash) or expected_hash != actual_hash:
            issues.append(
                _issue(
                    "discovery_query_manifest_hash_mismatch",
                    query_manifest_path,
                    "task manifest hash does not match discovery query manifest",
                    task_id=task_id,
                    details={"expected": expected_hash, "actual": actual_hash},
                )
            )
        if _int_value(item.get("manifest_bytes")) != os.path.getsize(query_manifest_path):
            issues.append(
                _issue(
                    "discovery_query_manifest_bytes_mismatch",
                    query_manifest_path,
                    "task manifest byte count does not match discovery query manifest",
                    task_id=task_id,
                )
            )
        query_manifest = _read_json_object(query_manifest_path)
        if query_manifest is None or any(
            _first_text(query_manifest, field) != _first_text(manifest, field)
            for field in ("source_id", "task_id", "run_id")
        ) or _first_text(query_manifest, "query_id") != query_id:
            issues.append(
                _issue(
                    "discovery_query_scope_mismatch",
                    query_manifest_path,
                    "task-bound discovery query manifest has a different runtime scope",
                    task_id=task_id,
                    details={"query_id": query_id},
                )
            )

    if observed != expected:
        issues.append(
            _issue(
                "discovery_expected_query_set_mismatch",
                manifest_path,
                "task manifest queries do not exactly match expected_query_ids",
                task_id=task_id,
                details={"expected": expected, "observed": observed},
            )
        )
    return issues, references


def _audit_discovery_manifest(
    manifest_path: str,
    manifest: Mapping[str, object],
) -> tuple[list[DownloadArchiveAuditIssue], int]:
    issues: list[DownloadArchiveAuditIssue] = []
    manifest_dir = os.path.dirname(manifest_path)
    task_id = _first_text(manifest, "task_id")

    if _first_text(manifest, "evidence_kind") != "discovery_query":
        issues.append(
            _issue(
                "invalid_discovery_manifest_kind",
                manifest_path,
                "discovery manifest must declare evidence_kind=discovery_query",
                task_id=task_id,
            )
        )
    if _first_text(manifest, "save_status") != "complete":
        issues.append(
            _issue(
                "discovery_manifest_not_complete",
                manifest_path,
                "discovery manifest is not marked save_status=complete",
                task_id=task_id,
                details={"save_status": manifest.get("save_status")},
            )
        )
    if _first_text(manifest, "coverage_status") != "complete":
        issues.append(
            _issue(
                "discovery_coverage_not_complete",
                manifest_path,
                "discovery manifest does not prove complete coverage",
                task_id=task_id,
                details={"coverage_status": manifest.get("coverage_status")},
            )
        )

    page_sidecars = manifest.get("page_sidecars")
    if not isinstance(page_sidecars, list):
        issues.append(
            _issue(
                "discovery_page_list_missing",
                manifest_path,
                "discovery manifest has no page_sidecars array",
                task_id=task_id,
            )
        )
        page_sidecars = []

    referenced_sidecars: set[str] = set()
    page_indices: list[int] = []
    observed_rows = 0
    row_identity_hashes: set[str] = set()
    duplicate_identity_hashes: set[str] = set()
    seen_row_identity_hashes: set[str] = set()
    for raw_sidecar_name in page_sidecars:
        if not isinstance(raw_sidecar_name, str) or not raw_sidecar_name.strip():
            issues.append(
                _issue(
                    "invalid_discovery_page_reference",
                    manifest_path,
                    "discovery page sidecar reference must be a non-empty string",
                    task_id=task_id,
                )
            )
            continue
        sidecar_path = _safe_child_path(manifest_dir, raw_sidecar_name)
        if sidecar_path is None:
            issues.append(
                _issue(
                    "unsafe_discovery_page_reference",
                    manifest_path,
                    "discovery page sidecar escapes its query directory",
                    task_id=task_id,
                    details={"sidecar_path": raw_sidecar_name},
                )
            )
            continue
        referenced_sidecars.add(os.path.abspath(sidecar_path))
        sidecar = _read_json_object(sidecar_path)
        if sidecar is None:
            issues.append(
                _issue(
                    "invalid_discovery_page_sidecar",
                    sidecar_path,
                    "discovery page sidecar is missing or invalid",
                    task_id=task_id,
                )
            )
            continue
        issues.extend(
            _audit_discovery_page_sidecar(
                sidecar_path=sidecar_path,
                sidecar=sidecar,
                manifest=manifest,
                task_id=task_id,
            )
        )
        page_index = _int_value(sidecar.get("page_index"))
        if page_index > 0:
            page_indices.append(page_index)
        extracted_rows = max(0, _int_value(sidecar.get("extracted_row_count")))
        observed_rows += extracted_rows
        identified_rows = max(0, _int_value(sidecar.get("identified_row_count")))
        raw_identity_hashes = sidecar.get("row_identity_hashes")
        if not isinstance(raw_identity_hashes, list):
            raw_identity_hashes = []
        page_row_hashes = [
            str(value or "").strip()
            for value in raw_identity_hashes
            if str(value or "").strip()
        ]
        if identified_rows != extracted_rows or len(page_row_hashes) != extracted_rows:
            issues.append(
                _issue(
                    "discovery_identified_row_count_mismatch",
                    sidecar_path,
                    "every extracted discovery row must have one identity digest",
                    task_id=task_id,
                    details={
                        "extracted": extracted_rows,
                        "identified": identified_rows,
                        "identity_hashes": len(page_row_hashes),
                    },
                )
            )
        page_hash_set = set(page_row_hashes)
        computed_duplicates = (
            len(page_row_hashes) - len(page_hash_set)
            + len(page_hash_set & seen_row_identity_hashes)
        )
        if computed_duplicates or _int_value(sidecar.get("duplicate_identity_count")):
            issues.append(
                _issue(
                    "discovery_row_identity_overlap",
                    sidecar_path,
                    "discovery rows overlap within or across pages",
                    task_id=task_id,
                    details={
                        "computed_duplicates": computed_duplicates,
                        "declared_duplicates": sidecar.get("duplicate_identity_count"),
                    },
                )
            )
        seen_row_identity_hashes.update(page_hash_set)
        identity_hash = _first_text(sidecar, "row_identity_sha256")
        if identity_hash and _int_value(sidecar.get("extracted_row_count")) > 0:
            if identity_hash in row_identity_hashes:
                duplicate_identity_hashes.add(identity_hash)
            row_identity_hashes.add(identity_hash)

    expected_indices = list(range(1, len(page_sidecars) + 1))
    if sorted(page_indices) != expected_indices:
        issues.append(
            _issue(
                "discovery_page_sequence_invalid",
                manifest_path,
                "discovery page indices are not contiguous from page one",
                task_id=task_id,
                details={"expected": expected_indices, "observed": sorted(page_indices)},
            )
        )
    if duplicate_identity_hashes:
        issues.append(
            _issue(
                "discovery_repeated_page_identity",
                manifest_path,
                "multiple non-empty discovery pages have the same row identity digest",
                task_id=task_id,
                details={"count": len(duplicate_identity_hashes)},
            )
        )

    manifest_page_count = _int_value(manifest.get("archived_page_count"))
    if manifest_page_count != len(page_sidecars):
        issues.append(
            _issue(
                "discovery_manifest_page_count_mismatch",
                manifest_path,
                "manifest archived_page_count does not match referenced page sidecars",
                task_id=task_id,
                details={"expected": manifest_page_count, "observed": len(page_sidecars)},
            )
        )
    manifest_rows = _int_value(manifest.get("observed_row_count"))
    if manifest_rows != observed_rows:
        issues.append(
            _issue(
                "discovery_manifest_row_count_mismatch",
                manifest_path,
                "manifest observed_row_count does not match page sidecars",
                task_id=task_id,
                details={"expected": manifest_rows, "observed": observed_rows},
            )
        )

    declared_pages = manifest.get("declared_total_pages")
    if declared_pages is not None and _int_value(declared_pages) != len(page_sidecars):
        issues.append(
            _issue(
                "discovery_declared_pages_not_covered",
                manifest_path,
                "archived pages do not cover the declared page count",
                task_id=task_id,
                details={"declared": declared_pages, "archived": len(page_sidecars)},
            )
        )
    if bool(manifest.get("authoritative_total")):
        declared_items = manifest.get("declared_total_items")
        if declared_items is not None and _int_value(declared_items) != observed_rows:
            issues.append(
                _issue(
                    "discovery_declared_total_mismatch",
                    manifest_path,
                    "observed rows do not match the authoritative declared total",
                    task_id=task_id,
                    details={"declared": declared_items, "observed": observed_rows},
                )
            )

    for filename in os.listdir(manifest_dir):
        if not filename.startswith("page_") or not filename.endswith(".meta.json"):
            continue
        sidecar_path = os.path.abspath(os.path.join(manifest_dir, filename))
        if sidecar_path not in referenced_sidecars:
            issues.append(
                _issue(
                    "orphan_discovery_page_sidecar",
                    sidecar_path,
                    "discovery page sidecar is not referenced by its manifest",
                    task_id=task_id,
                )
            )

    return issues, len(page_sidecars)


def _audit_discovery_page_sidecar(
    *,
    sidecar_path: str,
    sidecar: Mapping[str, object],
    manifest: Mapping[str, object],
    task_id: str,
) -> list[DownloadArchiveAuditIssue]:
    issues: list[DownloadArchiveAuditIssue] = []
    if _first_text(sidecar, "evidence_kind") != "discovery_page":
        issues.append(
            _issue(
                "invalid_discovery_page_kind",
                sidecar_path,
                "discovery page sidecar must declare evidence_kind=discovery_page",
                task_id=task_id,
            )
        )
    if _first_text(sidecar, "save_status") != "complete":
        issues.append(
            _issue(
                "discovery_page_not_complete",
                sidecar_path,
                "discovery page sidecar is not marked save_status=complete",
                task_id=task_id,
            )
        )
    if (
        _first_text(manifest, "coverage_status") == "complete"
        and _first_text(sidecar, "parse_status") != "complete"
    ):
        issues.append(
            _issue(
                "discovery_page_parse_not_complete",
                sidecar_path,
                "complete discovery coverage requires every page parse_status=complete",
                task_id=task_id,
                details={"parse_status": sidecar.get("parse_status")},
            )
        )
    for field_name in ("source_id", "task_id", "run_id", "query_id"):
        if _first_text(sidecar, field_name) != _first_text(manifest, field_name):
            issues.append(
                _issue(
                    f"discovery_{field_name}_mismatch",
                    sidecar_path,
                    f"discovery page {field_name} does not match its manifest",
                    task_id=task_id,
                )
            )

    for field_name in ("source_url", "final_url"):
        value = _first_text(sidecar, field_name)
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            issues.append(
                _issue(
                    f"discovery_{field_name}_invalid",
                    sidecar_path,
                    f"discovery page {field_name} must be an absolute HTTP(S) URL",
                    task_id=task_id,
                    details={field_name: value},
                )
            )
    status = _http_status_value(sidecar.get("http_status"))
    if not 200 <= status <= 299:
        issues.append(
            _issue(
                "discovery_http_status_invalid",
                sidecar_path,
                "discovery page http_status must be a successful 2xx status",
                task_id=task_id,
                details={"http_status": sidecar.get("http_status")},
            )
        )

    raw_name = _first_text(sidecar, "archive_path")
    raw_path = _safe_child_path(os.path.dirname(sidecar_path), raw_name)
    if raw_path is None or not os.path.isfile(raw_path):
        issues.append(
            _issue(
                "discovery_raw_page_missing",
                raw_path or sidecar_path,
                "discovery raw page is missing or escapes its query directory",
                task_id=task_id,
                details={"archive_path": raw_name},
            )
        )
        return issues

    expected_hash = _first_text(sidecar, "archive_content_sha256")
    actual_hash = _sha256_file(raw_path)
    if not _hash_text_is_valid(expected_hash) or expected_hash != actual_hash:
        issues.append(
            _issue(
                "discovery_page_hash_mismatch",
                raw_path,
                "discovery raw page hash does not match its sidecar",
                task_id=task_id,
                details={"expected": expected_hash, "actual": actual_hash},
            )
        )
    expected_bytes = sidecar.get("archive_content_bytes")
    if _int_value(expected_bytes) != os.path.getsize(raw_path):
        issues.append(
            _issue(
                "discovery_page_bytes_mismatch",
                raw_path,
                "discovery raw page byte size does not match its sidecar",
                task_id=task_id,
                details={
                    "expected": expected_bytes,
                    "actual": os.path.getsize(raw_path),
                },
            )
        )
    return issues


def _safe_child_path(parent: str, relative_path: str) -> str | None:
    if not relative_path:
        return None
    parent_path = os.path.abspath(parent)
    candidate = os.path.abspath(os.path.join(parent_path, relative_path))
    try:
        if os.path.commonpath((parent_path, candidate)) != parent_path:
            return None
    except ValueError:
        return None
    return candidate


def audit_download_verify_report(report_path: str) -> DownloadArchiveAuditResult:
    path = os.path.abspath(os.fspath(report_path))
    issues: list[DownloadArchiveAuditIssue] = []
    report = _read_json_object(path)
    if report is None:
        issue = DownloadArchiveAuditIssue(
            code="invalid_verify_report",
            path=path,
            message="duplicate verification report is not a valid JSON object",
        )
        return DownloadArchiveAuditResult(root=path, ok=False, issue_count=1, issues=(issue,))

    results = report.get("results")
    if not isinstance(results, list):
        issues.append(
            _issue(
                "verify_report_missing_results",
                path,
                "duplicate verification report has no results array",
            )
        )
        results = []

    html_count = _report_html_count(report, results)
    for index, item in enumerate(results):
        if not isinstance(item, Mapping):
            issues.append(
                _issue(
                    "invalid_verify_result",
                    path,
                    "duplicate verification result is not a JSON object",
                    details={"index": index},
                )
            )
            continue
        item_path = str(item.get("task_root") or path)
        task_id = str(item.get("task_id") or "").strip()
        second_error = item.get("second_error")
        if second_error:
            issues.append(
                _issue(
                    "second_run_error",
                    item_path,
                    "duplicate verification second run failed",
                    task_id=task_id,
                    details={"second_error": second_error},
                )
            )
        second_summary = item.get("second_summary")
        if isinstance(second_summary, Mapping):
            saved = _int_value(second_summary.get("saved"))
            if saved > 0:
                issues.append(
                    _issue(
                        "second_run_saved",
                        item_path,
                        "duplicate verification second run saved new archive files",
                        task_id=task_id,
                        details={"saved": saved},
                    )
                )
        snapshot_diff = item.get("snapshot_diff")
        if isinstance(snapshot_diff, Mapping):
            issues.extend(_audit_snapshot_diff(item_path, task_id, snapshot_diff))
        integrity_failures = item.get("integrity_failures")
        if isinstance(integrity_failures, list) and integrity_failures:
            issues.append(
                _issue(
                    "integrity_failure",
                    item_path,
                    "duplicate verification reported archive integrity failures",
                    task_id=task_id,
                    details={"count": len(integrity_failures), "samples": integrity_failures[:5]},
                )
            )

    return DownloadArchiveAuditResult(
        root=path,
        ok=not issues,
        html_count=html_count,
        sidecar_count=0,
        issue_count=len(issues),
        issues=tuple(issues),
    )


def _audit_sidecar(
    html_path: str,
    sidecar_path: str,
    payload: Mapping[str, object],
    expected_scope: Mapping[str, str],
) -> list[DownloadArchiveAuditIssue]:
    issues: list[DownloadArchiveAuditIssue] = []
    metadata = payload.get("metadata")
    task_id = _first_text(payload, "task_id") or _first_text(metadata, "task_id")
    expected_task_id = expected_scope.get("task_id", "")

    if str(payload.get("save_status") or "").strip().lower() != "complete":
        issues.append(
            _issue(
                "sidecar_not_complete",
                sidecar_path,
                "archive sidecar is not marked save_status=complete",
                task_id=task_id or expected_task_id,
                details={"save_status": payload.get("save_status")},
            )
        )

    issues.extend(_audit_source_fields(sidecar_path, payload, task_id or expected_task_id))
    issues.extend(_audit_integrity(html_path, sidecar_path, payload, task_id or expected_task_id))

    observed_scope = {
        "source_id": _first_text(payload, "source_id") or _first_text(metadata, "source_id"),
        "record_family": _first_text(payload, "record_family")
        or _first_text(metadata, "record_family"),
        "business_id": _first_text(payload, "business_id") or _first_text(metadata, "business_id"),
        "task_id": task_id,
    }
    for field_name, issue_code in (
        ("source_id", "source_id_mismatch"),
        ("record_family", "record_family_mismatch"),
        ("business_id", "business_id_mismatch"),
        ("task_id", "task_id_mismatch"),
    ):
        expected = expected_scope.get(field_name, "")
        observed = observed_scope.get(field_name, "")
        if expected and not observed:
            issues.append(
                _issue(
                    f"{field_name}_missing",
                    sidecar_path,
                    f"archive sidecar is missing {field_name} identity",
                    task_id=task_id or expected_task_id,
                )
            )
        elif expected and observed and expected != observed:
            issues.append(
                _issue(
                    issue_code,
                    sidecar_path,
                    f"archive sidecar {field_name} does not match task directory",
                    task_id=task_id or expected_task_id,
                    details={"expected": expected, "observed": observed},
                )
            )

    return issues


def _audit_source_fields(
    sidecar_path: str,
    payload: Mapping[str, object],
    task_id: str,
) -> list[DownloadArchiveAuditIssue]:
    issues: list[DownloadArchiveAuditIssue] = []
    source_url = _first_text(payload, "source_url") or _first_text(payload.get("metadata"), "source_url")
    if not source_url:
        issues.append(
            _issue(
                "source_url_missing",
                sidecar_path,
                "archive sidecar is missing source_url",
                task_id=task_id,
            )
        )
    else:
        parsed_source_url = urllib.parse.urlsplit(source_url)
        if (
            parsed_source_url.scheme.lower() not in {"http", "https"}
            or not parsed_source_url.netloc
        ):
            issues.append(
                _issue(
                    "source_url_invalid",
                    sidecar_path,
                    "archive sidecar source_url must be an absolute HTTP(S) URL",
                    task_id=task_id,
                    details={"source_url": source_url},
                )
            )

    http_status = _http_status_value(payload.get("http_status"))
    if http_status == 0:
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            http_status = _http_status_value(metadata.get("http_status"))
    if not 200 <= http_status <= 299:
        issues.append(
            _issue(
                "invalid_http_status",
                sidecar_path,
                "complete archive sidecar http_status must be a successful 2xx status",
                task_id=task_id,
                details={"http_status": payload.get("http_status")},
            )
        )
    return issues


def _archive_sidecar_path(html_path: str, *, require_detail_sidecar: bool = False) -> str:
    detail_sidecar_path = os.path.splitext(html_path)[0] + ".json"
    if require_detail_sidecar or os.path.isfile(detail_sidecar_path):
        return detail_sidecar_path
    return f"{html_path}.peap-save-status.json"


def _audit_integrity(
    html_path: str,
    sidecar_path: str,
    payload: Mapping[str, object],
    task_id: str,
) -> list[DownloadArchiveAuditIssue]:
    issues: list[DownloadArchiveAuditIssue] = []
    expected_hash = str(payload.get("archive_content_sha256") or "").strip()
    if not _hash_text_is_valid(expected_hash):
        issues.append(
            _issue(
                "archive_hash_missing",
                sidecar_path,
                "archive sidecar is missing a valid archive_content_sha256",
                task_id=task_id,
                details={"archive_content_sha256": expected_hash},
            )
        )
    else:
        actual_hash = _sha256_file(html_path)
        if actual_hash != expected_hash:
            issues.append(
                _issue(
                    "archive_hash_mismatch",
                    sidecar_path,
                    "archive sidecar hash does not match html content",
                    task_id=task_id,
                    details={"expected": expected_hash, "actual": actual_hash},
                )
            )

    expected_bytes = payload.get("archive_content_bytes")
    try:
        expected_size = int(expected_bytes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        issues.append(
            _issue(
                "archive_bytes_missing",
                sidecar_path,
                "archive sidecar is missing a valid archive_content_bytes",
                task_id=task_id,
                details={"archive_content_bytes": expected_bytes},
            )
        )
    else:
        actual_size = os.path.getsize(html_path)
        if actual_size != expected_size:
            issues.append(
                _issue(
                    "archive_bytes_mismatch",
                    sidecar_path,
                    "archive sidecar byte size does not match html content",
                    task_id=task_id,
                    details={"expected": expected_size, "actual": actual_size},
                )
            )
    return issues


def _audit_snapshot_diff(
    path: str,
    task_id: str,
    snapshot_diff: Mapping[str, object],
) -> list[DownloadArchiveAuditIssue]:
    issues: list[DownloadArchiveAuditIssue] = []
    fields = (
        ("added", "snapshot_added", "duplicate verification second run added files"),
        ("removed", "snapshot_removed", "duplicate verification second run removed files"),
        ("changed_content", "snapshot_changed_content", "duplicate verification second run changed file content"),
        (
            "touched_same_content",
            "snapshot_touched_same_content",
            "duplicate verification second run rewrote unchanged content",
        ),
    )
    for field_name, issue_code, message in fields:
        values = snapshot_diff.get(field_name)
        if isinstance(values, list) and values:
            issues.append(
                _issue(
                    issue_code,
                    path,
                    message,
                    task_id=task_id,
                    details={"count": len(values), "samples": values[:5]},
                )
            )
    return issues


def _expected_scope_from_path(root_path: str, html_path: str) -> dict[str, str]:
    relative = os.path.relpath(html_path, root_path)
    relative_parts = relative.split(os.sep)
    components = []
    if relative_parts:
        components.append(relative_parts[0])
    components.append(os.path.basename(os.path.normpath(root_path)))
    for component in components:
        parts = component.split("__", 2)
        if len(parts) != 3 or not all(str(part).strip() for part in parts):
            continue
        source_id, record_family, business_id = (str(part).strip() for part in parts)
        if record_family != "deal" and record_family != "listing":
            continue
        return {
            "source_id": source_id,
            "record_family": record_family,
            "business_id": business_id,
            "task_id": f"{source_id}:{record_family}:{business_id}",
        }
    return {}


def _first_text(source: object, key: str) -> str:
    if not isinstance(source, Mapping):
        return ""
    return str(source.get(key) or "").strip()


def _issue(
    code: str,
    path: str,
    message: str,
    *,
    task_id: str = "",
    details: Mapping[str, object] | None = None,
) -> DownloadArchiveAuditIssue:
    return DownloadArchiveAuditIssue(
        code=code,
        path=os.path.abspath(os.fspath(path)),
        message=message,
        task_id=str(task_id or "").strip(),
        details=dict(details or {}),
    )


def _is_partial_name(name: str) -> bool:
    text = str(name or "")
    return text.endswith(".part") or text.endswith("_files.part")


def _read_json_object(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _hash_text_is_valid(value: object) -> bool:
    text = str(value or "").strip()
    if not text.startswith("sha256:"):
        return False
    digest = text.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdefABCDEF" for char in digest)


def _int_value(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _http_status_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _report_html_count(report: Mapping[str, object], results: list[object]) -> int:
    manifest = report.get("manifest")
    if isinstance(manifest, Mapping):
        for key in ("effective_total_html", "total_html"):
            value = _int_value(manifest.get(key))
            if value:
                return value
    total = 0
    for item in results:
        if isinstance(item, Mapping):
            total += _int_value(item.get("html_count"))
    return total


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m peap.download_archive_audit",
        description="Audit PEAP raw download archives and duplicate-download verification reports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser(
        "archive",
        help="Audit a raw download archive root.",
    )
    archive_parser.add_argument("root", help="Archive root directory to audit")
    archive_parser.add_argument(
        "--require-detail-sidecar",
        action="store_true",
        help="Require same-stem detail JSON sidecars instead of accepting resume status markers.",
    )

    discovery_parser = subparsers.add_parser(
        "discovery",
        help="Audit raw list responses and pagination coverage manifests.",
    )
    discovery_parser.add_argument("root", help="Discovery evidence root directory to audit")

    report_parser = subparsers.add_parser(
        "duplicate-report",
        help="Audit a duplicate-download verification JSON report.",
    )
    report_parser.add_argument("path", help="duplicate_verify JSON report path")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "archive":
        result = audit_download_archive_root(
            args.root,
            require_detail_sidecar=bool(getattr(args, "require_detail_sidecar", False)),
        )
    elif args.command == "discovery":
        result = audit_discovery_evidence_root(
            args.root,
            require_task_manifest=True,
        )
    elif args.command == "duplicate-report":
        result = audit_download_verify_report(args.path)
    else:  # pragma: no cover - argparse enforces known subcommands
        parser.error(f"unsupported command: {args.command}")

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


__all__ = [
    "DiscoveryEvidenceAuditResult",
    "DownloadArchiveAuditIssue",
    "DownloadArchiveAuditResult",
    "audit_discovery_evidence_root",
    "audit_download_archive_root",
    "audit_download_verify_report",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
