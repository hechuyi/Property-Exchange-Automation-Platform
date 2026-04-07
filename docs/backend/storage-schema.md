# Storage Schema

This document describes persistence semantics rather than service behavior. The goal is to say exactly what the backend stores in SQLite, which rows are authoritative for latest-state queries, which JSON blobs are retained for audit/refresh, and how ingest and refresh change revisions over time.

## Main Tables

| Table | Role |
| --- | --- |
| `records` | Latest row per business key, with duplicated filter/status columns and `latest_revision_id`. |
| `record_revisions` | Revision snapshots of payloads, findings, canonical material, and per-revision state. |
| `mapping_pending` | Open mapping work items keyed by record and revision, deduped to the latest unresolved entry per record. |
| `exports`, `export_cursor_records` | Export runs and cursor bookkeeping for incremental/rebuild modes. |
| `jobs`, `job_events` | Background-job lifecycle, counters, and per-item progress. |
| `audit_log` | Durable operational audit trail, including maintenance repairs and manual actions. |

## What `records` Stores

`records` is the authoritative latest-row table. It stores row identity (`record_id`, `business_key`, `record_family`, `identity_anchor`), identity evidence (`source_identity_json`), current search/filter columns (`project_code`, `project_name`, `project_type`, `exchange`, `listing_date`), current lifecycle state, source/archive paths, latest error metadata, and `latest_revision_id`. The table is not just a pointer table: API listing queries these top-level columns directly through `StreamingStore.iter_latest_records()`, while export-scope empty-result counting now layers `keyword`/`state` filtering in `peap/streaming_export.py` over the same latest-row material.

`StreamingStore`, not ingest, owns the persisted identity rule. On the success path, `upsert_record()` computes `business_key` from `project_code.upper()` or `source:<sha1(source_file)>` and decides whether an incoming record becomes a new row or an update to an existing `record_id`. Failed rows live in a different identity namespace, `failed:{identity_anchor}`, through `upsert_failed_record()`.

## What `record_revisions` Stores

`record_revisions` stores revision-local snapshots: `revision_hash`, `parser_payload_json`, `postprocess_payload_json`, `canonical_record_json`, `canonical_projection_json`, `findings_json`, revision `state`, and `source_file`. This is the table that keeps the evidence needed for `refresh_postprocess()` and the regression trail needed to compare changed vs unchanged exports.

## `canonical_projection_json` Is Derived, Not Authoritative

`StreamingStore.upsert_record()` ignores the caller-provided `record.canonical_projection` and recomputes the stored projection with `_derive_canonical_projection(record.canonical_record)`. That means `canonical_projection_json` is explicitly a derived cache over `canonical_record_json`, not an independent truth source. `tests/test_streaming_store.py::test_upsert_record_recomputes_canonical_projection_from_canonical_record` and `tests/test_streaming_export.py::test_record_to_export_payload_ignores_projection_without_canonical_record` are the clearest guards for this rule.

## How Revisions Change

Revision behavior is hash-sensitive rather than blindly append-only:

- When `revision_hash` changes, `StreamingStore.upsert_record()` inserts a new `record_revisions` row and advances `records.latest_revision_id`.
- When `revision_hash` is unchanged, the store updates the existing latest revision in place with refreshed `parser_payload`, `postprocess_payload`, `canonical_record`, `canonical_projection`, findings, and state.
- `tests/test_streaming_store.py::test_upsert_record_same_revision_hash_does_not_create_new_revision` and `tests/test_streaming_store.py::test_upsert_record_refreshes_state_and_findings_when_revision_hash_is_unchanged` cover the unchanged-hash path.

The practical implication is that `record_revisions` behaves like append-on-content-change and update-on-same-content-state-change, not as a strictly immutable event log.

## Ingest Vs. Postprocess Refresh

Initial ingest and postprocess refresh both end at `upsert_record()`, so both ultimately write the same tables. The difference is upstream:

- Initial ingest produces fresh `parser_payload`, fresh `postprocess_payload`, a new `canonical_record`, and a new state classification inside `StreamingIngestRunner.ingest()`.
- `refresh_postprocess()` reuses the stored `parser_payload`, reruns only postprocess, rebuilds `canonical_record`, and then calls `upsert_record()` with a recomputed `revision_hash`. That hash may or may not differ from the previous one, because it is derived from `postprocess_payload`.

If the refreshed `postprocess_payload` is identical, the revision row is updated in place; if it changes, a new revision row is created. `tests/test_streaming_ingest.py::test_refresh_postprocess_reuses_stored_parser_payload_without_reparsing` is the main evidence for the refresh path.

## Failed Records And Identity Evidence

Failed records do not go through the success-path canonical chain. `StreamingStore.upsert_failed_record()` creates or updates a failed-record row keyed by a failed identity anchor, stores failure metadata on `records`, and writes a corresponding revision row containing the failure payload and findings. Fresh parse/postprocess failure paths therefore create or update failed sibling rows rather than mutating an existing success-path row in place. The important boundary is that `source_identity_json` still matters for failed rows because `desktop_backend/app_service.py::reprocess_record()` and `peap_core/record_identity.py::pick_reprocess_evidence_path()` use it to locate the original evidence file and replay source context.

## Export Cursor Persistence

Export cursor bookkeeping is intentionally sticky. When a record becomes non-ready, `export_cursor_records` is not silently cleared; removal or withdrawal semantics must be derived by comparing `get_exported_revision_map()` with the current ready-set query. `tests/test_streaming_store.py::test_cursor_does_not_silently_clear_for_non_ready_transition` and `tests/test_streaming_store.py::test_non_ready_record_with_cursor_entry_appears_in_removal_candidate_set` are the main regression anchors for that rule.

## Maintenance Normalization

`run_streaming_store_maintenance()` is part of live semantics, not archival cleanup. It can rewrite legacy skip-parse rows into `skipped`, normalize `listing_date`, and reclassify old rows toward the current mapping-review vocabulary. The current store implementation splits this work into a payload/state normalization pass plus a bidirectional `mapping_pending` reconciliation pass, both driven by the shared policy in `peap_core/record_state_policy.py`. `tests/test_streaming_store_maintenance.py` verifies the rewritten row states and the audit entries that record those repairs, while `tests/test_streaming_store.py::BacklogReconcileTest` locks in the backlog reconciliation semantics.
