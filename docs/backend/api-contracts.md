# API Contracts

This document describes the current service/API output contract rather than the internals of parsing or storage. Its focus is `/api/records`, the row shape returned by `AppService.list_records()`, and the precise boundary between authoritative canonical fields, derived row `values`, and user-facing summary metadata.

## `/api/records` Surface

`desktop_backend/app_backend.py` maps `GET /api/records` query parameters into a payload consumed by `AppService.list_records()`. The service returns an envelope containing `scope`, `columns`, paging metadata, `summary`, and `rows`. `tests/test_app_service.py::test_list_records_and_run_export_share_same_scope_contract` verifies that listing and export share the same normalized scope envelope and that `run_export()` now forwards the normalized `state` and `keyword` filters into `ExportRequest`.

## Envelope Contract

| Key | Meaning |
| --- | --- |
| `scope` | Normalized request scope echoed by `/api/records`: `record_family`, `state`, `project_type`, `keyword`, `date_from`, `date_to`, `page`, `page_size`. `run_export()` also echoes this scope object back in job metadata and summary payloads. |
| `columns` | Output columns for `values`. When a project kind is known, this comes from the output contract; otherwise it is inferred from ordered export headers. |
| `summary.filtered_state_counts` | Counts over all filtered rows before pagination. |
| `summary.page_state_counts` | Counts over only the visible page rows. |
| `rows` | One row per latest record exposed by the query. |

`tests/test_app_service.py::test_list_records_summary_splits_filtered_counts_and_page_counts` is the regression anchor for the split between filtered and visible counts.

## Row Contract

Each row produced by `AppService.list_records()` contains stable top-level identity and status fields plus a derived `values` object:

| Row key | Source |
| --- | --- |
| `record_id`, `project_code`, `project_name`, `project_type`, `listing_date`, `archive_path`, `source_file`, `updated_at` | Latest-row columns from `records`. |
| `exchange` | Normalized display label derived from the top-level `records.exchange`. |
| `state`, `status_label`, `status_detail` | Latest-row state plus humanized status in `AppService._status_label()` and `_record_status_detail()`. |
| `seller`, `price` | Top-level convenience fields built only from `canonical_record["canonical_fields"]` in `_build_record_top_level_fields()`. |
| `values` | Export-shaped row values built by `_build_record_display_values()` from canonical projection rules. |

The important consequence is that top-level `seller` and `price` come from canonical fields only. `tests/test_app_service.py::test_list_records_does_not_promote_projection_only_top_level_fields` locks that boundary in, while `tests/test_app_service.py::test_list_records_prefers_canonical_fields_over_stale_raw_payloads` and `tests/test_app_service.py::test_list_records_uses_canonical_export_extras_for_cli_contract_fields` verify canonical precedence and `export_extras` propagation.

## How `values` Are Derived

`AppService._build_record_display_payload()` first calls `record_to_export_payload(record)` from `peap/streaming_export.py`. That function projects from `canonical_record` only; if canonical fields are absent, it returns `{}` instead of merging `parser_payload` or `postprocess_payload`. The service then backfills only the basic identifier fields `项目编号`, `项目名称`, and `项目类型` from latest-row columns when needed. This is why projection-only or raw-payload-only values do not appear as export-facing truth in `values`.

## What `/api/records` Explicitly Does Not Promise

- It does not promise raw `parser_payload` or `postprocess_payload` passthrough in row `values`.
- It does not promise that `canonical_projection` alone is enough; a projection-only record is intentionally treated as insufficient truth by export and effectively empty for canonical-only display fields.
- It does not promise that top-level convenience fields mirror every column in `values`; only selected fields such as `seller` and `price` are promoted, and they are canonical-only.
- It does not promise that `summary` is a single undifferentiated state-count blob; current behavior intentionally separates filtered counts from page-local counts.

## Export Relationship

`AppService.run_export()` reuses the normalized scope envelope for job metadata and response payloads, and the current `ExportRequest` consumes `record_family`, `date_from`, `date_to`, resolved `business_types` (from `project_type`), plus the normalized `state` and `keyword` filters. Pagination-only fields (`page`, `page_size`) are still echoed in `scope` but do not constrain export selection.

Export is also stricter than listing: `peap/streaming_export.py` still builds artifacts only from ready rows, but the scoped empty-result classifier now counts rows under the same `date/project_type/state/keyword/record_family` filter set that the request carried. It returns summaries such as `pending_mapping_blocked`, `mapping_conflict_blocked`, `skipped_only`, or `no_matching_records` when that scoped export candidate set yields no artifact rows. `tests/test_app_service.py::test_run_export_reports_pending_mapping_blockers_when_no_ready_rows` anchors the `pending_mapping_blocked` branch, `tests/test_app_service.py::test_run_export_empty_reason_respects_keyword_filtered_scope` locks the keyword-filtered empty path, and `tests/test_app_service.py::test_run_export_reports_mapping_conflict_blocker_when_only_mapping_conflict_records` plus `tests/test_app_service.py::test_run_export_reports_mapping_conflict_blocker_when_only_conflict_records` lock in the current conflict-like empty-result behavior.

The current service keeps a compatibility alias at this boundary: both `mapping_conflict` and `conflict` empty scopes surface as `empty_reason_code="mapping_conflict_blocked"`, with the message text distinguishing pure mapping conflict from more general conflict. By contrast, `pending_review` is still an enum residue in `peap_core/pipeline_state_contracts.py`, not an active export blocker in the streaming mainline.
