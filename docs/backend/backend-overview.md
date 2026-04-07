# Backend Overview

This document gives the smallest useful picture of the backend runtime: where requests enter, which module owns which transformation, and why the current design routes every durable business fact through a canonical truth layer instead of letting raw parser payloads leak into UI or export behavior.

## Runtime Shape

The desktop HTTP adapter in `desktop_backend/app_backend.py` maps `/api/records` to `AppService.list_records()`, `/api/exports` to `AppService.run_export()`, mapping refresh endpoints to `AppService.launch_pending_mapping_refresh()`, and `/api/records/<id>/reprocess` to `AppService.reprocess_record()`. `AppService` is the orchestration-heavy service boundary: it normalizes request scope, shapes row display payloads, analyzes pending-mapping work items, coordinates job lifecycle, and delegates durable state to `StreamingStore`, ingest mutations to `StreamingIngestRunner`, and export generation to `run_ready_export()`.

## Module Responsibilities

| Module | Owns | Explicitly does not own |
| --- | --- | --- |
| `peap/streaming_ingest.py` | Parse invocation, postprocess invocation, source identity construction, `canonical_record` assembly, `canonical_projection` bootstrap, and record-state classification. | Query pagination, export artifact writing, and long-lived storage queries. |
| `peap/streaming_store.py` | SQLite schema, latest-row persistence, revision history, mapping pending queues, export cursor bookkeeping, and maintenance normalization. | Parser execution and workbook generation. |
| `peap/export_projection.py` | The canonical flat output boundary from `canonical_record` to export/API payload. | Storage and request handling. |
| `peap/streaming_export.py` | Ready-record selection, cursor-aware rebuild/incremental export, and workbook row writing. | Raw parse/postprocess normalization and API pagination. |
| `desktop_backend/app_service.py` | Request normalization, user-facing summaries, job orchestration, row display shaping, and mutation routing. | Canonical field derivation rules and low-level revision persistence. |

## Why The Design Uses A Canonical Truth Layer

The canonical layer exists because the parser payload vocabulary and the postprocess payload vocabulary are still source-facing, unstable, and intentionally verbose. `peap/streaming_ingest.py::_build_canonical_record_payload()` converts those snapshots into a smaller `canonical_fields` contract plus `export_extras`, while `peap/export_projection.py::project_canonical_record_to_export_payload()` projects only from canonical material. The regression tests in `tests/test_streaming_ingest.py`, `tests/test_streaming_export.py`, and `tests/test_app_service.py` all reinforce the same rule: exported or displayed business fields must come from canonical truth, not from whichever raw payload happened to carry a value.

## Where Mapping Refresh Fits

Mapping refresh is not a second parser. `StreamingIngestRunner.refresh_postprocess()` in `peap/streaming_ingest.py` reloads the stored `parser_payload`, reruns postprocess with current mapping entries, rebuilds `canonical_record`, and reclassifies the state without reparsing the HTML snapshot; `tests/test_streaming_ingest.py::test_refresh_postprocess_reuses_stored_parser_payload_without_reparsing` locks that behavior in. Full `reprocess_record()` in `desktop_backend/app_service.py` is the heavier path: it selects an evidence file and runs the whole ingest flow again.

## Store Maintenance Is Part Of The Mainline

The runtime treats maintenance normalization as part of normal operation, not as a one-off migration script. `run_streaming_store_maintenance()` calls `normalize_legacy_skip_parse_entries()`, `normalize_listing_dates()`, and `normalize_required_mapping_states()` at service startup, before export, and before mutating pipeline launches. Read endpoints such as `overview()` and `list_records()` do not invoke maintenance on every request. The practical implication is that persisted state may be repaired toward current contracts over time, but a read-only code path can still observe pre-maintenance rows until one of those maintenance triggers runs.
