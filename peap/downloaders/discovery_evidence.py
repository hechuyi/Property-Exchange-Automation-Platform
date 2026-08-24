"""Fail-closed evidence for list discovery and pagination coverage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .common import HttpFetchedText

_COMPLETE_TERMINATION_REASONS = {
    "date_boundary_reached",
    "declared_pages_exhausted",
    "empty_page",
    "next_link_absent",
    "official_empty",
    "short_page",
    "single_page",
}


class DiscoveryEvidenceError(RuntimeError):
    """Raised when discovery evidence cannot prove the claimed coverage."""


def _canonical_evidence_root(root: str) -> str:
    """Create and canonicalize an evidence root before any child is opened.

    The evidence tree is a trust boundary.  Keeping the canonical root here
    means a configured alias (for example, a symlinked workspace directory)
    is resolved once, while symlinks introduced *inside* the tree are rejected
    by ``_assert_safe_path`` below.
    """

    raw_root = os.path.abspath(os.fspath(root))
    if not raw_root or raw_root == os.path.dirname(raw_root):
        raise ValueError("evidence root must be a non-root directory")
    os.makedirs(raw_root, exist_ok=True)
    if not os.path.isdir(raw_root):
        raise ValueError("evidence root is not a directory")
    # Preserve the caller's lexical root for stable relative references.  The
    # realpath containment check is performed separately for every child.
    return raw_root


def _assert_no_symlink_components(path: str, *, stop_at: str | None = None) -> None:
    """Reject a path whose existing components contain a symlink.

    ``os.path.commonpath`` alone is lexical and therefore insufficient: a
    child directory can be replaced with a symlink after the lexical check.
    Walking existing components also catches a symlink leaf before it is
    opened or replaced.
    """

    absolute = os.path.abspath(os.fspath(path))
    if "\x00" in absolute:
        raise DiscoveryEvidenceError("evidence path contains a NUL byte")
    stop_absolute = os.path.abspath(stop_at) if stop_at else None
    current = absolute
    while True:
        if current != stop_absolute and os.path.lexists(current) and os.path.islink(current):
            raise DiscoveryEvidenceError(f"evidence path must not use symlinks: {absolute}")
        if stop_absolute is not None and current == stop_absolute:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _assert_safe_path(path: str, root: str, *, require_file: bool = False) -> str:
    """Return ``path`` only when it remains inside the real evidence root."""

    root_abs = os.path.abspath(os.fspath(root))
    candidate = os.path.abspath(os.fspath(path))
    try:
        if os.path.commonpath((root_abs, candidate)) != root_abs:
            raise DiscoveryEvidenceError("evidence path escapes its root")
    except ValueError as exc:
        raise DiscoveryEvidenceError("evidence path is on a different volume") from exc

    # Resolve existing and missing components alike.  A missing leaf is fine,
    # but a symlinked parent or a symlink leaf is never accepted.  Check this
    # before realpath so callers receive the actionable symlink diagnosis.
    _assert_no_symlink_components(candidate, stop_at=root_abs)
    root_real = os.path.realpath(root_abs)
    candidate_real = os.path.realpath(candidate)
    try:
        if os.path.commonpath((root_real, candidate_real)) != root_real:
            raise DiscoveryEvidenceError("evidence path resolves outside its root")
    except ValueError as exc:
        raise DiscoveryEvidenceError("evidence path resolves on a different volume") from exc
    if require_file and not os.path.isfile(candidate):
        raise DiscoveryEvidenceError(f"evidence file is missing: {candidate}")
    return candidate


class DiscoveryTaskEvidence:
    """Bind the complete, declared set of list queries for one runtime task."""

    def __init__(
        self,
        *,
        root: str,
        source_id: str,
        task_id: str,
        run_id: str,
        expected_query_ids: Iterable[str],
    ) -> None:
        self.root = _canonical_evidence_root(root)
        self.source_id = _required_text(source_id, "source_id")
        self.task_id = _required_text(task_id, "task_id")
        self.run_id = _required_text(run_id, "run_id")
        normalized_ids = tuple(_required_text(value, "query_id") for value in expected_query_ids)
        if not normalized_ids:
            raise ValueError("expected_query_ids must not be empty")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("expected_query_ids must be unique")
        self.expected_query_ids = normalized_ids
        self.discovery_dir = os.path.join(
            self.root,
            "_evidence",
            _safe_component(self.run_id),
            _safe_component(self.task_id.replace(":", "__")),
            "discovery",
        )
        self.manifest_path = os.path.join(self.discovery_dir, "task_manifest.json")
        self._candidate_fingerprints: tuple[str, ...] = ()
        self._manifest_reference: dict[str, object] | None = None
        self._finalized = False
        _assert_safe_path(self.discovery_dir, self.root)
        os.makedirs(self.discovery_dir, exist_ok=True)
        _assert_safe_path(self.discovery_dir, self.root)

    def __enter__(self) -> "DiscoveryTaskEvidence":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if not self._finalized:
            self.fail(
                termination_reason="exception" if exc is not None else "unfinalized",
                details={"error": str(exc)} if exc is not None else None,
            )
        return False

    def query(
        self,
        query_id: str,
        *,
        authoritative_total: bool = False,
        page_size: int | None = None,
    ) -> "DiscoveryQueryEvidence":
        normalized_id = _required_text(query_id, "query_id")
        if normalized_id not in self.expected_query_ids:
            raise DiscoveryEvidenceError(
                f"query_id is not declared by task evidence: {normalized_id}"
            )
        return DiscoveryQueryEvidence(
            root=self.root,
            source_id=self.source_id,
            task_id=self.task_id,
            run_id=self.run_id,
            query_id=normalized_id,
            authoritative_total=authoritative_total,
            page_size=page_size,
        )

    def complete(
        self,
        *,
        candidate_entries: Iterable[Mapping[str, object]] = (),
    ) -> str:
        try:
            self._candidate_fingerprints = _candidate_fingerprint_set(candidate_entries)
        except DiscoveryEvidenceError as exc:
            self.fail(
                termination_reason="candidate_contract_invalid",
                details={"error": str(exc)},
            )
            raise

        query_entries: list[dict[str, Any]] = []
        missing: list[str] = []
        invalid: list[str] = []
        for query_id in self.expected_query_ids:
            relative_path = os.path.join(_safe_component(query_id), "manifest.json")
            path = _assert_safe_path(
                os.path.join(self.discovery_dir, relative_path),
                self.root,
            )
            try:
                payload = _read_json_object_required(path, root=self.root)
            except DiscoveryEvidenceError:
                missing.append(query_id)
                continue
            if (
                payload.get("evidence_kind") != "discovery_query"
                or payload.get("save_status") != "complete"
                or payload.get("coverage_status") != "complete"
                or payload.get("source_id") != self.source_id
                or payload.get("task_id") != self.task_id
                or payload.get("run_id") != self.run_id
                or payload.get("query_id") != query_id
            ):
                invalid.append(query_id)
                continue
            query_entries.append(
                {
                    "query_id": query_id,
                    "manifest_path": relative_path,
                    "manifest_sha256": _sha256_file(path, root=self.root),
                    "manifest_bytes": os.path.getsize(path),
                }
            )
        if missing or invalid:
            self.fail(
                termination_reason="query_coverage_incomplete",
                missing_query_ids=missing,
                invalid_query_ids=invalid,
            )
            parts = []
            if missing:
                parts.append(f"missing expected queries: {missing}")
            if invalid:
                parts.append(f"invalid expected queries: {invalid}")
            raise DiscoveryEvidenceError("; ".join(parts))

        payload = self._payload(
            save_status="complete",
            coverage_status="complete",
            termination_reason="all_expected_queries_complete",
            query_entries=query_entries,
        )
        _write_json_atomic(self.manifest_path, payload, root=self.root)
        self._finalized = True
        self._cache_manifest_reference()
        return self.manifest_path

    def fail(
        self,
        *,
        termination_reason: str,
        details: Mapping[str, object] | None = None,
        missing_query_ids: Iterable[str] = (),
        invalid_query_ids: Iterable[str] = (),
    ) -> str:
        if self._finalized:
            return self.manifest_path
        payload = self._payload(
            save_status="failed",
            coverage_status="failed",
            termination_reason=str(termination_reason or "").strip() or "failure",
            query_entries=[],
        )
        payload["missing_query_ids"] = list(missing_query_ids)
        payload["invalid_query_ids"] = list(invalid_query_ids)
        if details:
            payload["failure_details"] = dict(details)
        _write_json_atomic(self.manifest_path, payload, root=self.root)
        self._finalized = True
        self._cache_manifest_reference()
        return self.manifest_path

    def manifest_reference(self) -> dict[str, object]:
        if not self._finalized:
            raise DiscoveryEvidenceError("discovery task manifest is not finalized")
        if self._manifest_reference is None:
            raise DiscoveryEvidenceError("discovery task manifest reference is unavailable")
        return dict(self._manifest_reference)

    def _cache_manifest_reference(self) -> None:
        _assert_safe_path(self.manifest_path, self.root, require_file=True)
        relative_path = os.path.relpath(self.manifest_path, self.root)
        if relative_path == os.pardir or relative_path.startswith(os.pardir + os.sep):
            raise DiscoveryEvidenceError("discovery task manifest escapes evidence root")
        self._manifest_reference = {
            "source_id": self.source_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "path": relative_path,
            "sha256": _sha256_file(self.manifest_path, root=self.root),
            "bytes": os.path.getsize(self.manifest_path),
        }

    def _payload(
        self,
        *,
        save_status: str,
        coverage_status: str,
        termination_reason: str,
        query_entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "evidence_kind": "discovery_task",
            "save_status": save_status,
            "coverage_status": coverage_status,
            "source_id": self.source_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "expected_query_ids": list(self.expected_query_ids),
            "queries": query_entries,
            "candidate_count": len(self._candidate_fingerprints),
            "candidate_fingerprints": list(self._candidate_fingerprints),
            "termination_reason": termination_reason,
            "completed_at": _utc_now(),
        }


class DiscoveryQueryEvidence:
    """Archive every raw list response and one terminal coverage decision."""

    def __init__(
        self,
        *,
        root: str,
        source_id: str,
        task_id: str,
        run_id: str,
        query_id: str,
        authoritative_total: bool = False,
        page_size: int | None = None,
    ) -> None:
        self.root = _canonical_evidence_root(root)
        self.source_id = _required_text(source_id, "source_id")
        self.task_id = _required_text(task_id, "task_id")
        self.run_id = _required_text(run_id, "run_id")
        self.query_id = _required_text(query_id, "query_id")
        self.authoritative_total = bool(authoritative_total)
        self.page_size = (
            None if page_size is None else _positive_int(page_size, "page_size")
        )
        self.query_dir = os.path.join(
            self.root,
            "_evidence",
            _safe_component(self.run_id),
            _safe_component(self.task_id.replace(":", "__")),
            "discovery",
            _safe_component(self.query_id),
        )
        self.manifest_path = os.path.join(self.query_dir, "manifest.json")
        self._pages: list[dict[str, Any]] = []
        self._row_identity_hashes: set[str] = set()
        self._seen_row_identity_hashes: set[str] = set()
        self._termination_facts: dict[str, object] = {}
        self._finalized = False
        _assert_safe_path(self.query_dir, self.root)
        os.makedirs(self.query_dir, exist_ok=True)
        _assert_safe_path(self.query_dir, self.root)

    def __enter__(self) -> "DiscoveryQueryEvidence":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if not self._finalized:
            if exc is None:
                self.fail(termination_reason="unfinalized")
            else:
                self.fail(
                    termination_reason="exception",
                    details={
                        "error_type": getattr(exc_type, "__name__", str(exc_type or "")),
                        "error": str(exc),
                    },
                )
        return False

    def record_page(
        self,
        *,
        page_index: int,
        response: HttpFetchedText,
        body_format: str,
        extracted_row_count: int,
        row_identity_values: Iterable[object] = (),
        declared_total_items: int | None = None,
        declared_total_pages: int | None = None,
        request_metadata: Mapping[str, object] | None = None,
    ) -> str:
        raw_path = self.capture_page(
            page_index=page_index,
            response=response,
            body_format=body_format,
            request_metadata=request_metadata,
        )
        self.complete_page(
            page_index=page_index,
            extracted_row_count=extracted_row_count,
            row_identity_values=row_identity_values,
            declared_total_items=declared_total_items,
            declared_total_pages=declared_total_pages,
        )
        return raw_path

    def capture_page(
        self,
        *,
        page_index: int,
        response: HttpFetchedText,
        body_format: str,
        request_metadata: Mapping[str, object] | None = None,
    ) -> str:
        if self._finalized:
            raise DiscoveryEvidenceError("discovery query is already finalized")
        if not isinstance(response, HttpFetchedText):
            raise DiscoveryEvidenceError(
                "discovery page requires HttpFetchedText transport evidence"
            )
        page_number = _positive_int(page_index, "page_index")
        expected_page = len(self._pages) + 1
        if page_number != expected_page:
            raise DiscoveryEvidenceError(
                f"discovery page indices must be contiguous: expected {expected_page}, got {page_number}"
            )
        normalized_format = str(body_format or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]+", normalized_format):
            raise DiscoveryEvidenceError(f"invalid discovery body format: {body_format!r}")

        stem = f"page_{page_number:06d}"
        raw_name = f"{stem}.raw.{normalized_format}"
        sidecar_name = f"{stem}.meta.json"
        raw_path = _assert_safe_path(os.path.join(self.query_dir, raw_name), self.root)
        sidecar_path = _assert_safe_path(
            os.path.join(self.query_dir, sidecar_name), self.root
        )
        raw_bytes = getattr(response, "raw_bytes", None)
        if raw_bytes is None:
            raise DiscoveryEvidenceError(
                "discovery page requires original response bytes"
            )
        elif not isinstance(raw_bytes, bytes):
            raise DiscoveryEvidenceError("HttpFetchedText.raw_bytes must be bytes when present")

        base_sidecar: dict[str, Any] = {
            "schema_version": 1,
            "evidence_kind": "discovery_page",
            "save_status": "pending",
            "source_id": self.source_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "page_index": page_number,
            "source_url": response.source_url,
            "final_url": response.final_url,
            "http_status": response.http_status,
            "archive_path": raw_name,
            "body_format": normalized_format,
            "parse_status": "pending",
            "captured_at": _utc_now(),
        }
        if request_metadata:
            base_sidecar["request_metadata"] = dict(request_metadata)

        _write_json_atomic(sidecar_path, base_sidecar, root=self.root)
        _write_bytes_atomic(raw_path, raw_bytes, root=self.root)
        complete_sidecar = {
            **base_sidecar,
            "save_status": "complete",
            "archive_content_sha256": _sha256_bytes(raw_bytes),
            "archive_content_bytes": len(raw_bytes),
        }
        _write_json_atomic(sidecar_path, complete_sidecar, root=self.root)

        page_record = {
            "page_index": page_number,
            "sidecar_path": sidecar_name,
            "archive_path": raw_name,
            "archive_content_sha256": complete_sidecar["archive_content_sha256"],
            "parse_status": "pending",
            "extracted_row_count": None,
            "declared_total_items": None,
            "declared_total_pages": None,
            "row_identity_sha256": "",
        }
        self._pages.append(page_record)
        return raw_path

    def complete_page(
        self,
        *,
        page_index: int,
        extracted_row_count: int,
        row_identity_values: Iterable[object] = (),
        declared_total_items: int | None = None,
        declared_total_pages: int | None = None,
    ) -> str:
        page = self._page_record(page_index)
        if page.get("parse_status") != "pending":
            raise DiscoveryEvidenceError(
                f"discovery page {page_index} parse status is already {page.get('parse_status')}"
            )
        row_count = _nonnegative_int(extracted_row_count, "extracted_row_count")
        total_items = _optional_nonnegative_int(declared_total_items, "declared_total_items")
        total_pages = _optional_positive_int(declared_total_pages, "declared_total_pages")
        identities = [str(value or "").strip() for value in row_identity_values]
        if len(identities) != row_count or any(not value for value in identities):
            self.fail_page(
                page_index=page_index,
                reason="identity_count_mismatch",
                details={
                    "extracted_row_count": row_count,
                    "identified_row_count": len([value for value in identities if value]),
                },
            )
            raise DiscoveryEvidenceError(
                f"identified rows do not match extracted rows: "
                f"identified={len([value for value in identities if value])} extracted={row_count}"
            )
        identity_hash = ""
        per_identity_hashes = [
            _sha256_bytes(value.encode("utf-8")) for value in identities
        ]
        unique_identity_hashes = set(per_identity_hashes)
        overlap_hashes = unique_identity_hashes & self._seen_row_identity_hashes
        within_page_duplicate_count = len(per_identity_hashes) - len(unique_identity_hashes)
        duplicate_identity_count = within_page_duplicate_count + len(overlap_hashes)
        repeated_identity = False
        if identities:
            identity_hash = _sha256_bytes(
                json.dumps(identities, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            repeated_identity = row_count > 0 and identity_hash in self._row_identity_hashes

        sidecar_path = _assert_safe_path(
            os.path.join(self.query_dir, str(page["sidecar_path"])), self.root
        )
        sidecar = _read_json_object_required(sidecar_path, root=self.root)
        sidecar.update(
            {
                "parse_status": "complete",
                "parsed_at": _utc_now(),
                "extracted_row_count": row_count,
                "declared_total_items": total_items,
                "declared_total_pages": total_pages,
                "row_identity_sha256": identity_hash,
                "row_identity_hashes": per_identity_hashes,
                "identified_row_count": len(identities),
                "duplicate_identity_count": duplicate_identity_count,
            }
        )
        _write_json_atomic(sidecar_path, sidecar, root=self.root)
        page.update(
            {
                "parse_status": "complete",
                "extracted_row_count": row_count,
                "declared_total_items": total_items,
                "declared_total_pages": total_pages,
                "row_identity_sha256": identity_hash,
                "row_identity_hashes": per_identity_hashes,
                "identified_row_count": len(identities),
                "duplicate_identity_count": duplicate_identity_count,
            }
        )
        if identity_hash and row_count > 0:
            self._row_identity_hashes.add(identity_hash)
        self._seen_row_identity_hashes.update(unique_identity_hashes)
        if repeated_identity:
            raise DiscoveryEvidenceError(
                f"repeated page identity detected at page {page_index}"
            )
        if duplicate_identity_count:
            raise DiscoveryEvidenceError(
                f"overlapping row identities detected at page {page_index}: "
                f"duplicates={duplicate_identity_count}"
            )
        return sidecar_path

    def fail_page(
        self,
        *,
        page_index: int,
        reason: str,
        details: Mapping[str, object] | None = None,
    ) -> str:
        page = self._page_record(page_index)
        sidecar_path = _assert_safe_path(
            os.path.join(self.query_dir, str(page["sidecar_path"])), self.root
        )
        sidecar = _read_json_object_required(sidecar_path, root=self.root)
        sidecar.update(
            {
                "parse_status": "failed",
                "parse_failure_reason": str(reason or "").strip() or "parse_failed",
                "parsed_at": _utc_now(),
            }
        )
        if details:
            sidecar["parse_failure_details"] = dict(details)
        _write_json_atomic(sidecar_path, sidecar, root=self.root)
        page["parse_status"] = "failed"
        return sidecar_path

    def _page_record(self, page_index: int) -> dict[str, Any]:
        page_number = _positive_int(page_index, "page_index")
        if page_number > len(self._pages):
            raise DiscoveryEvidenceError(f"discovery page {page_number} was not captured")
        page = self._pages[page_number - 1]
        if int(page.get("page_index") or 0) != page_number:
            raise DiscoveryEvidenceError(f"discovery page {page_number} is not contiguous")
        return page

    def complete(
        self,
        *,
        termination_reason: str,
        termination_facts: Mapping[str, object] | None = None,
    ) -> str:
        reason = str(termination_reason or "").strip()
        self._termination_facts = dict(termination_facts or {})
        try:
            self._validate_complete(reason, self._termination_facts)
        except DiscoveryEvidenceError as exc:
            self.fail(
                termination_reason="coverage_validation_failed",
                details={"claim": reason, "error": str(exc)},
            )
            raise

        payload = self._manifest_payload(
            save_status="complete",
            coverage_status="complete",
            termination_reason=reason,
        )
        _write_json_atomic(self.manifest_path, payload, root=self.root)
        self._finalized = True
        return self.manifest_path

    def fail(
        self,
        *,
        termination_reason: str,
        details: Mapping[str, object] | None = None,
    ) -> str:
        if self._finalized:
            return self.manifest_path
        for page in self._pages:
            if page.get("parse_status") == "pending":
                self.fail_page(
                    page_index=int(page["page_index"]),
                    reason=str(termination_reason or "").strip() or "query_failed",
                )
        payload = self._manifest_payload(
            save_status="failed",
            coverage_status="failed",
            termination_reason=str(termination_reason or "").strip() or "failure",
        )
        if details:
            payload["failure_details"] = dict(details)
        _write_json_atomic(self.manifest_path, payload, root=self.root)
        self._finalized = True
        return self.manifest_path

    def _validate_complete(
        self,
        termination_reason: str,
        termination_facts: Mapping[str, object],
    ) -> None:
        if termination_reason not in _COMPLETE_TERMINATION_REASONS:
            raise DiscoveryEvidenceError(
                f"unsupported complete termination reason: {termination_reason!r}"
            )
        if not self._pages:
            raise DiscoveryEvidenceError("complete discovery query has no archived pages")
        incomplete_pages = [
            int(page["page_index"])
            for page in self._pages
            if page.get("parse_status") != "complete"
        ]
        if incomplete_pages:
            raise DiscoveryEvidenceError(
                f"discovery pages were not parsed successfully: {incomplete_pages}"
            )
        last_row_count = int(self._pages[-1].get("extracted_row_count") or 0)
        if termination_reason == "short_page":
            if self.page_size is None:
                raise DiscoveryEvidenceError("short_page requires a declared page_size")
            if last_row_count <= 0 or last_row_count >= self.page_size:
                raise DiscoveryEvidenceError(
                    f"last page is not short: rows={last_row_count} page_size={self.page_size}"
                )
        if termination_reason in {"empty_page", "official_empty"} and last_row_count != 0:
            raise DiscoveryEvidenceError(
                f"{termination_reason} requires zero rows on the terminal page"
            )
        if (
            termination_reason == "next_link_absent"
            and termination_facts.get("next_link_present") is not False
        ):
            raise DiscoveryEvidenceError(
                "next_link_absent requires termination_facts.next_link_present=false"
            )
        if termination_reason == "single_page" and len(self._pages) != 1:
            raise DiscoveryEvidenceError("single_page requires exactly one archived page")
        if termination_reason == "date_boundary_reached" and not (
            termination_facts.get("ordering") == "descending"
            and termination_facts.get("boundary_before_start") is True
        ):
            raise DiscoveryEvidenceError(
                "date_boundary_reached requires descending ordering and boundary_before_start=true"
            )

        declared_page_values = {
            int(page["declared_total_pages"])
            for page in self._pages
            if page.get("declared_total_pages") is not None
        }
        if len(declared_page_values) > 1:
            raise DiscoveryEvidenceError(
                f"declared pages changed during traversal: {sorted(declared_page_values)}"
            )
        declared_pages = next(iter(declared_page_values), None)
        if termination_reason == "declared_pages_exhausted" and declared_pages is None:
            raise DiscoveryEvidenceError("declared pages are missing")
        if declared_pages is not None and declared_pages != len(self._pages):
            raise DiscoveryEvidenceError(
                f"declared pages={declared_pages} but archived pages={len(self._pages)}"
            )

        declared_item_values = {
            int(page["declared_total_items"])
            for page in self._pages
            if page.get("declared_total_items") is not None
        }
        if len(declared_item_values) > 1:
            raise DiscoveryEvidenceError(
                f"declared total changed during traversal: {sorted(declared_item_values)}"
            )
        declared_items = next(iter(declared_item_values), None)
        observed_rows = sum(int(page.get("extracted_row_count") or 0) for page in self._pages)
        if (
            self.authoritative_total
            and declared_items is not None
            and declared_items != observed_rows
        ):
            raise DiscoveryEvidenceError(
                f"declared total={declared_items} but observed rows={observed_rows}"
            )

    def _manifest_payload(
        self,
        *,
        save_status: str,
        coverage_status: str,
        termination_reason: str,
    ) -> dict[str, Any]:
        declared_pages = _single_observed_value(self._pages, "declared_total_pages")
        declared_items = _single_observed_value(self._pages, "declared_total_items")
        return {
            "schema_version": 1,
            "evidence_kind": "discovery_query",
            "save_status": save_status,
            "coverage_status": coverage_status,
            "source_id": self.source_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "authoritative_total": self.authoritative_total,
            "page_size": self.page_size,
            "termination_reason": termination_reason,
            "termination_facts": dict(self._termination_facts),
            "archived_page_count": len(self._pages),
            "observed_row_count": sum(
                int(page.get("extracted_row_count") or 0) for page in self._pages
            ),
            "declared_total_items": declared_items,
            "declared_total_pages": declared_pages,
            "page_sidecars": [str(page["sidecar_path"]) for page in self._pages],
            "completed_at": _utc_now(),
        }


def _single_observed_value(pages: list[dict[str, Any]], field: str) -> int | None:
    values = {int(page[field]) for page in pages if page.get(field) is not None}
    return next(iter(values)) if len(values) == 1 else None


def _candidate_fingerprint(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise DiscoveryEvidenceError("discovery candidate must be an object")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DiscoveryEvidenceError(
            f"discovery candidate is not canonical JSON: {exc}"
        ) from exc
    return _sha256_bytes(encoded)


def _candidate_fingerprint_set(
    candidate_entries: Iterable[Mapping[str, object]],
) -> tuple[str, ...]:
    fingerprints = tuple(_candidate_fingerprint(entry) for entry in candidate_entries)
    if len(set(fingerprints)) != len(fingerprints):
        raise DiscoveryEvidenceError("discovery candidate set contains duplicates")
    return tuple(sorted(fingerprints))


def verify_discovery_candidate_subset(
    *,
    root: str,
    reference: Mapping[str, object],
    candidate_entries: Iterable[Mapping[str, object]],
    expected_source_id: str,
    expected_task_id: str,
    expected_run_id: str,
) -> None:
    root_path = _canonical_evidence_root(root)
    source_id = _required_text(expected_source_id, "expected_source_id")
    task_id = _required_text(expected_task_id, "expected_task_id")
    run_id = _required_text(expected_run_id, "expected_run_id")
    if not isinstance(reference, Mapping):
        raise DiscoveryEvidenceError("discovery task manifest reference must be an object")
    for key, expected in (
        ("source_id", source_id),
        ("task_id", task_id),
        ("run_id", run_id),
    ):
        if str(reference.get(key) or "").strip() != expected:
            raise DiscoveryEvidenceError(
                f"discovery task manifest reference {key} does not match expected scope"
            )

    relative_path = str(reference.get("path") or "").strip()
    if not relative_path or os.path.isabs(relative_path):
        raise DiscoveryEvidenceError("discovery task manifest path must be relative")
    manifest_path = _assert_safe_path(
        os.path.join(root_path, relative_path),
        root_path,
        require_file=True,
    )

    expected_hash = str(reference.get("sha256") or "").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash):
        raise DiscoveryEvidenceError("discovery task manifest reference hash is invalid")
    if _sha256_file(manifest_path, root=root_path) != expected_hash:
        raise DiscoveryEvidenceError("discovery task manifest hash does not match reference")
    expected_bytes = reference.get("bytes")
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
        raise DiscoveryEvidenceError("discovery task manifest reference bytes is invalid")
    if os.path.getsize(manifest_path) != expected_bytes:
        raise DiscoveryEvidenceError("discovery task manifest size does not match reference")

    manifest = _read_json_object_required(manifest_path, root=root_path)
    if manifest.get("save_status") != "complete" or manifest.get("coverage_status") != "complete":
        raise DiscoveryEvidenceError("discovery task manifest is not complete")
    for key, expected in (
        ("source_id", source_id),
        ("task_id", task_id),
        ("run_id", run_id),
    ):
        if str(manifest.get(key) or "").strip() != expected:
            raise DiscoveryEvidenceError(
                f"discovery task manifest {key} does not match expected scope"
            )

    declared_raw = manifest.get("candidate_fingerprints")
    if not isinstance(declared_raw, list) or any(
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")) is None
        for value in declared_raw
    ):
        raise DiscoveryEvidenceError("discovery task candidate fingerprints are invalid")
    declared = tuple(str(value) for value in declared_raw)
    if declared != tuple(sorted(set(declared))):
        raise DiscoveryEvidenceError("discovery task candidate fingerprints are not a unique set")
    if manifest.get("candidate_count") != len(declared):
        raise DiscoveryEvidenceError("discovery task candidate count does not match fingerprints")

    requested = _candidate_fingerprint_set(candidate_entries)
    missing = [fingerprint for fingerprint in requested if fingerprint not in set(declared)]
    if missing:
        raise DiscoveryEvidenceError(
            f"prefetched candidate is not present in discovery task manifest: {missing[0]}"
        )


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _safe_component(value: str) -> str:
    raw = str(value or "").strip()
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", raw).strip("._") or "unknown"
    # Sanitization alone makes distinct identities collide (``a/b`` and
    # ``a_b``).  Keep the readable component while adding a stable suffix
    # whenever the input had to be rewritten.
    if normalized != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        normalized = f"{normalized}--{digest}"
    return normalized


def _positive_int(value: object, field: str) -> int:
    number = _nonnegative_int(value, field)
    if number <= 0:
        raise DiscoveryEvidenceError(f"{field} must be positive")
    return number


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise DiscoveryEvidenceError(f"{field} must be an integer")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DiscoveryEvidenceError(f"{field} must be an integer") from exc
    if number < 0:
        raise DiscoveryEvidenceError(f"{field} must be nonnegative")
    return number


def _optional_nonnegative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field)


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)


def _sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _sha256_file(path: str, *, root: str | None = None) -> str:
    if root is None:
        _assert_no_symlink_components(path)
    else:
        _assert_safe_path(path, root, require_file=True)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_bytes_atomic(
    path: str,
    content: bytes,
    *,
    root: str | None = None,
) -> None:
    if root is not None:
        path = _assert_safe_path(path, root)
    else:
        _assert_no_symlink_components(path)
    parent = os.path.dirname(path)
    if root is not None:
        _assert_safe_path(parent, root)
    else:
        _assert_no_symlink_components(parent)
    os.makedirs(parent, exist_ok=True)
    if root is not None:
        _assert_safe_path(parent, root)
    part_path = f"{path}.part"
    if root is not None:
        _assert_safe_path(part_path, root)
    else:
        _assert_no_symlink_components(part_path)
    with open(part_path, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part_path, path)


def _write_json_atomic(
    path: str,
    payload: Mapping[str, object],
    *,
    root: str | None = None,
) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _write_bytes_atomic(path, content, root=root)


def _read_json_object_required(
    path: str,
    *,
    root: str | None = None,
) -> dict[str, Any]:
    if root is None:
        _assert_no_symlink_components(path)
    else:
        _assert_safe_path(path, root, require_file=True)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryEvidenceError(
            f"discovery page sidecar is missing or invalid: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise DiscoveryEvidenceError(f"discovery page sidecar is not an object: {path}")
    return payload


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


__all__ = [
    "DiscoveryEvidenceError",
    "DiscoveryQueryEvidence",
    "DiscoveryTaskEvidence",
    "verify_discovery_candidate_subset",
]
