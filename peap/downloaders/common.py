"""Shared downloader helpers and summary contract."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from peap_core.business_catalog import resolve_business_descriptor

from ..download_errors import DownloadError, duplicate_download_target_error


@dataclass
class DownloadSummary:
    pages_requested: int = 0
    listed_items: int = 0
    detail_fetched: int = 0
    saved: int = 0
    skipped_by_list_date: int = 0
    skipped_by_detail_date: int = 0
    date_missing_skipped: int = 0
    skipped_by_resume: int = 0
    skipped_by_duplicate: int = 0
    skipped_by_business_filter: int = 0
    skipped_by_missing_xmid: int = 0
    skipped_by_detail_unavailable: int = 0
    detail_candidates: int = 0
    detail_failed: int = 0
    list_unaccounted: int = 0
    detail_unaccounted: int = 0
    candidate_dates: list[str] = field(default_factory=list)
    candidate_entries: list[dict[str, Any]] = field(default_factory=list)
    list_page_observations: list[dict[str, Any]] = field(default_factory=list)
    discovery_task_manifest: dict[str, Any] | None = None
    duplicate_samples: list[dict[str, str]] = field(default_factory=list)
    typed_errors: list[DownloadError] = field(default_factory=list)
    downloaded_this_run: set[str] = field(default_factory=set)
    reserved_download_targets: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DetailUnavailableEvidence:
    reason: str
    final_url: str = ""
    status: int | None = None
    title: str = ""
    html_len: int = 0
    expected_identifier: str = ""


class DetailUnavailableError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        final_url: str = "",
        status: int | None = None,
        title: str = "",
        html_len: int = 0,
        expected_identifier: str = "",
    ) -> None:
        self.evidence = DetailUnavailableEvidence(
            reason=str(reason or "").strip() or "detail_unavailable",
            final_url=str(final_url or "").strip(),
            status=status,
            title=str(title or "").strip(),
            html_len=int(html_len or 0),
            expected_identifier=str(expected_identifier or "").strip(),
        )
        super().__init__(self._message())

    @property
    def reason(self) -> str:
        return self.evidence.reason

    def _message(self) -> str:
        parts = [f"reason={self.evidence.reason}"]
        if self.evidence.status is not None:
            parts.append(f"status={self.evidence.status}")
        if self.evidence.final_url:
            parts.append(f"final_url={self.evidence.final_url}")
        if self.evidence.title:
            parts.append(f"title={self.evidence.title!r}")
        if self.evidence.html_len:
            parts.append(f"html_len={self.evidence.html_len}")
        if self.evidence.expected_identifier:
            parts.append(f"expected={self.evidence.expected_identifier}")
        return "detail-page-unavailable: " + " ".join(parts)


@dataclass
class ProgressLogThrottle:
    total: int
    min_step: int | None = None
    min_interval_seconds: float = 30.0
    _last_done: int = 0
    _last_at: float = 0.0

    def should_log(self, done: int, *, now: float | None = None) -> bool:
        current_done = max(0, int(done or 0))
        total = max(0, int(self.total or 0))
        if current_done <= 0:
            return False
        current_at = time.monotonic() if now is None else float(now)
        if current_done >= total and self._last_done < total:
            self._mark(current_done, current_at)
            return True
        if self._last_done <= 0:
            self._mark(current_done, current_at)
            return True
        step = int(self.min_step) if self.min_step is not None else max(1, total // 20)
        if current_done - self._last_done >= max(1, step):
            self._mark(current_done, current_at)
            return True
        if current_at - self._last_at >= max(0.0, float(self.min_interval_seconds)):
            self._mark(current_done, current_at)
            return True
        return False

    def _mark(self, done: int, now: float) -> None:
        self._last_done = int(done)
        self._last_at = float(now)


def parse_loose_date(value: Any) -> Optional[dt.date]:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raw_numeric = str(int(value)).strip()
        if len(raw_numeric) == 8 and raw_numeric.isdigit():
            try:
                return dt.datetime.strptime(raw_numeric, "%Y%m%d").date()
            except ValueError:
                return None
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return dt.datetime.utcfromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        if len(raw) == 8:
            try:
                return dt.datetime.strptime(raw, "%Y%m%d").date()
            except ValueError:
                return None
        try:
            return parse_loose_date(int(raw))
        except ValueError:
            return None

    raw = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", r"\1-\2-\3", raw)
    raw = raw.replace("/", "-").replace(".", "-")
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    if " " in raw:
        raw = raw.split(" ", 1)[0]

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        return dt.date(year, month, day)
    except ValueError:
        return None



def parse_bound(raw: Optional[str], name: str) -> Optional[dt.date]:
    if raw in (None, ""):
        return None
    parsed = parse_loose_date(raw)
    if parsed is None:
        raise ValueError(f"invalid {name}: {raw!r} (expected YYYY-MM-DD)")
    return parsed



def in_date_range(value: Optional[dt.date], start: Optional[dt.date], end: Optional[dt.date]) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def real_date_outside_requested_range(
    value: Optional[dt.date],
    start: Optional[dt.date],
    end: Optional[dt.date],
) -> bool:
    """Return True only when a real business date exists and contradicts the request range."""
    return value is not None and not in_date_range(value, start, end)


def deal_date_outside_requested_range(
    deal_date: Optional[dt.date],
    collection_date: Optional[dt.date],
    start: Optional[dt.date],
    end: Optional[dt.date],
) -> bool:
    range_date = deal_date if deal_date is not None else collection_date
    return range_date is not None and not in_date_range(range_date, start, end)



def detail_accounted_count(summary: DownloadSummary, *, detail_resume_skipped: int = 0) -> int:
    return (
        summary.saved
        + summary.skipped_by_detail_date
        + summary.skipped_by_detail_unavailable
        + summary.detail_failed
        + detail_resume_skipped
    )


def read_json_object(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
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


def _archive_integrity_matches(
    html_path: str,
    payload: dict[str, Any],
    *,
    require_integrity: bool = False,
) -> bool:
    expected_hash = str(payload.get("archive_content_sha256") or "").strip()
    expected_bytes = payload.get("archive_content_bytes")
    if not expected_hash and expected_bytes in (None, ""):
        return not require_integrity
    if expected_hash and (not _hash_text_is_valid(expected_hash) or _sha256_file(html_path) != expected_hash):
        return False
    if expected_bytes not in (None, ""):
        try:
            if os.path.getsize(html_path) != int(expected_bytes):
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def archive_integrity_fields(html_path: str) -> dict[str, Any]:
    if not os.path.isfile(html_path):
        return {}
    return {
        "archive_content_sha256": _sha256_file(html_path),
        "archive_content_bytes": os.path.getsize(html_path),
    }


def successful_http_evidence(*, source_url: str, http_status: object) -> dict[str, Any]:
    normalized_url = str(source_url or "").strip()
    parsed_url = urllib.parse.urlsplit(normalized_url)
    if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("successful archive evidence requires an absolute HTTP(S) source_url")
    if isinstance(http_status, bool) or not isinstance(http_status, int):
        raise ValueError("successful archive evidence requires an integer HTTP status")
    if not 200 <= http_status <= 299:
        raise ValueError(f"successful archive evidence requires a 2xx HTTP status: {http_status}")
    return {
        "source_url": normalized_url,
        "http_status": http_status,
    }


class HttpFetchedText(str):
    def __new__(
        cls,
        content: str,
        *,
        source_url: str,
        final_url: str,
        http_status: object,
        raw_bytes: bytes | None = None,
    ) -> "HttpFetchedText":
        evidence = successful_http_evidence(
            source_url=source_url,
            http_status=http_status,
        )
        normalized_final_url = str(final_url or "").strip()
        parsed_final_url = urllib.parse.urlsplit(normalized_final_url)
        if parsed_final_url.scheme.lower() not in {"http", "https"} or not parsed_final_url.netloc:
            raise ValueError("fetched HTTP text requires an absolute HTTP(S) final_url")
        instance = super().__new__(cls, str(content or ""))
        instance.source_url = evidence["source_url"]
        instance.final_url = normalized_final_url
        instance.http_status = evidence["http_status"]
        if raw_bytes is not None and not isinstance(raw_bytes, bytes):
            raise TypeError("raw_bytes must be bytes or None")
        instance.raw_bytes = raw_bytes
        return instance


def attach_archive_integrity_to_sidecar(*, json_path: str, html_path: str) -> bool:
    payload = read_json_object(json_path)
    if payload is None or not os.path.isfile(html_path):
        return False
    payload.update(archive_integrity_fields(html_path))
    temp_json_path = f"{json_path}.tmp"
    with open(temp_json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_json_path, json_path)
    return True


def has_blocking_evidence_sidecar(html_path: str) -> bool:
    evidence_path = f"{html_path}.peap-evidence.json"
    if not os.path.exists(evidence_path):
        return False
    payload = read_json_object(evidence_path)
    if payload is None:
        return True
    return str(payload.get("page_kind") or "").strip().lower() == "invalid_shell"


def clear_artifact_evidence_sidecar(html_path: str) -> None:
    """Remove diagnostic evidence once a later fetch produces a valid artifact."""
    try:
        os.remove(f"{html_path}.peap-evidence.json")
    except FileNotFoundError:
        return


def complete_resume_sidecar_exists(
    html_path: str,
    *,
    sidecar_path: str | None = None,
    require_integrity: bool = False,
    require_assets_dir: bool = False,
    expected_fields: Mapping[str, object] | None = None,
) -> bool:
    if not os.path.isfile(html_path):
        return False
    if require_assets_dir and not os.path.isdir(f"{os.path.splitext(html_path)[0]}_files"):
        return False
    if has_blocking_evidence_sidecar(html_path):
        return False
    payload = read_json_object(sidecar_path or (os.path.splitext(str(html_path or ""))[0] + ".json"))
    if payload is None:
        return False
    raw_save_status = payload.get("save_status")
    if require_integrity and str(raw_save_status or "").strip() == "":
        return False
    if str(raw_save_status or "complete").strip().lower() != "complete":
        return False
    metadata = payload.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    for key, expected in dict(expected_fields or {}).items():
        observed = payload.get(key)
        if observed in (None, ""):
            observed = metadata_map.get(key)
        if str(observed or "").strip() != str(expected or "").strip():
            return False
    return _archive_integrity_matches(html_path, payload, require_integrity=require_integrity)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(name or "").strip())
    return cleaned or "unknown"


def business_id_key(output_type: str) -> str:
    normalized = str(output_type or "").strip()
    descriptor = resolve_business_descriptor(normalized)
    if descriptor is not None:
        return descriptor.business_id
    if normalized in {"equity_transfer", "physical_asset", "capital_increase", "pre_disclosure"}:
        return normalized
    raise ValueError(f"unsupported business output type: {output_type!r}")


def runtime_task_id(source_id: str, output_type: str, *, record_family: str = "listing") -> str:
    from peap.business_runtime import get_source_business_binding

    business_id = business_id_key(output_type)
    return get_source_business_binding(
        source_id,
        record_family=record_family,
        business_id=business_id,
    ).task_id


def download_target_relpath(*, html_root: str, html_path: str) -> str:
    return os.path.relpath(os.path.abspath(html_path), os.path.abspath(html_root))


def record_duplicate_candidate(
    summary: DownloadSummary,
    *,
    candidate_id: str,
    source_url: str = "",
    project_code: str = "",
    project_name: str = "",
    max_samples: int = 20,
) -> None:
    if len(summary.duplicate_samples) >= max(0, int(max_samples or 0)):
        return
    sample = {
        "candidate_id": str(candidate_id or "").strip(),
        "source_url": str(source_url or "").strip(),
        "project_code": str(project_code or "").strip(),
        "project_name": str(project_name or "").strip(),
    }
    summary.duplicate_samples.append({key: value for key, value in sample.items() if value})


def reserve_download_target(
    summary: DownloadSummary,
    *,
    html_root: str,
    html_path: str,
    source_id: str,
    task_id: str,
) -> bool:
    relpath = download_target_relpath(html_root=html_root, html_path=html_path)
    if relpath in summary.reserved_download_targets or relpath in summary.downloaded_this_run:
        summary.typed_errors.append(
            duplicate_download_target_error(
                source_id=source_id,
                task_id=task_id,
                raw_reason=f"target={relpath}",
            )
        )
        return False
    summary.reserved_download_targets.add(relpath)
    return True


def record_downloaded_target(
    summary: DownloadSummary,
    *,
    html_root: str,
    html_path: str,
) -> None:
    summary.downloaded_this_run.add(download_target_relpath(html_root=html_root, html_path=html_path))


def mark_artifact_save_failed(
    *,
    html_path: str,
    save_json: bool,
    write_json: Callable[[str, dict[str, Any]], None],
    write_resume_status: Callable[[str, str], None],
    failure_identity: Mapping[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    if not os.path.isfile(html_path):
        return False

    try:
        if save_json:
            json_path = os.path.splitext(html_path)[0] + ".json"
            payload: dict[str, Any] = {}
            if os.path.isfile(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        payload = dict(loaded)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
            # Preserve task ownership even when the failure happened before the
            # normal pending sidecar could be written.  Do not add integrity
            # hashes here: a failed artifact may be only a partial snapshot.
            if failure_identity:
                payload.update(dict(failure_identity))
            payload["save_status"] = "failed"
            try:
                write_json(json_path, payload)
                return True
            except Exception:  # noqa: BLE001
                write_resume_status(html_path, "failed")
                return True

        write_resume_status(html_path, "failed")
        return True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("failed to mark artifact save failure: html_path=%s error=%s", html_path, exc)
        return False


__all__ = [
    "archive_integrity_fields",
    "DetailUnavailableError",
    "DetailUnavailableEvidence",
    "DownloadSummary",
    "ProgressLogThrottle",
    "attach_archive_integrity_to_sidecar",
    "business_id_key",
    "complete_resume_sidecar_exists",
    "deal_date_outside_requested_range",
    "detail_accounted_count",
    "has_blocking_evidence_sidecar",
    "HttpFetchedText",
    "in_date_range",
    "parse_bound",
    "parse_loose_date",
    "read_json_object",
    "real_date_outside_requested_range",
    "record_downloaded_target",
    "reserve_download_target",
    "runtime_task_id",
    "safe_filename",
    "successful_http_evidence",
]
