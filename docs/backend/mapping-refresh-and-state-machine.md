# Mapping Refresh And State Machine

This document describes the active record-state machine in the streaming backend and the two refresh paths that change a record after initial ingest. The emphasis is on what the current runtime actually emits, not on every enum value that still exists in historical contracts.

## `reprocess_record` vs `refresh_postprocess`

`refresh_postprocess` is the narrow path. In `peap/streaming_ingest.py`, `StreamingIngestRunner.refresh_postprocess()` loads the stored record, reuses `parser_payload`, reruns postprocess with current mappings, rebuilds `canonical_record`, and updates the record through `StreamingStore.upsert_record()`. It is the right operation when mapping tables or postprocess rules changed but the archived evidence file did not. It does not rerun archive materialization; its conflict input is the stored latest-row state, not a fresh archive-collision check.

`reprocess_record` is the wide path. In `desktop_backend/app_service.py`, `AppService.reprocess_record()` resolves an evidence file from `archive_path`, `source_file`, and `source_identity_json`, then calls `StreamingIngestRunner.ingest()` again. Use it when parse output itself may have changed, the archived snapshot changed, or the original record is already in a failed state and needs a fresh parse attempt.

## Why Mapping Refresh Is Postprocess-Only

The backend deliberately keeps mapping refresh below the parse boundary. `tests/test_streaming_ingest.py::test_refresh_postprocess_reuses_stored_parser_payload_without_reparsing` asserts that `refresh_postprocess()` must not call the parser. The reason is architectural: mapping rules operate on the parsed snapshot already stored in `parser_payload`, so rerunning the parser would change more than the mapping layer and would blur diagnosis between parse drift and mapping drift.

## Conflict Preservation In Refresh

Initial ingest classifies `conflict` from the live result of snapshot materialization. `refresh_postprocess()` does not repeat that file-system step; instead it passes `had_conflict=(record.state == "conflict")` into the classifier. That means refresh preserves previously observed archive-conflict state, but it also does not discover that an archive collision was manually resolved outside the ingest path. If you need a new archive materialization decision, use `reprocess_record`, not `refresh_postprocess`.

## Findings That Lead To Review States

The active classifier is `classify_record_state()` in `peap_core/record_state_policy.py`, consumed by `peap/streaming_ingest.py` and by store maintenance:

- `mapping_conflict` finding -> state `mapping_conflict`
- any of `mapping_missing`, `mapping_gap`, `mapping_ambiguous`, `project_type_unknown` -> state `pending_mapping`
- no review findings, but archive naming collision during snapshot materialization -> state `conflict`
- otherwise -> state `ready`

The maintenance path in `StreamingStore.normalize_required_mapping_states()` now uses the same shared policy and then reconciles `mapping_pending` from latest-row state in a separate backlog pass.

## Active Runtime States

| State | Meaning in the current mainline |
| --- | --- |
| `ready` | Canonical/postprocess checks passed, no mapping blocker remains, and no archive rename conflict blocks normal completion. |
| `pending_mapping` | The record is persisted and reviewable, but it still has unresolved mapping or project-type gaps. |
| `mapping_conflict` | The record is persisted and reviewable, but the mapping layer found conflicting candidate resolutions that require human choice. |
| `conflict` | Business data is otherwise usable, but archive materialization had a naming collision and the snapshot had to be stored under a conflict path. |
| `parse_failed` | Parsing failed, or a later reprocess/refresh path explicitly transitioned the original row into a parse-failed state. |
| `postprocess_failed` | Parsing succeeded, but the postprocess pipeline raised before canonical success-path persistence completed. On fresh ingest this failure is stored through the failed-row path, not through the success-path canonical row. |
| `skipped` | The parser intentionally skipped the page according to skip rules; the row is tracked but does not enter exportable work. |

`tests/test_streaming_store.py::test_mapping_conflict_is_persisted_review_work_not_exception_work` is the key regression anchor for the interpretation of `mapping_conflict` as persisted review work rather than exception work. `tests/test_app_service.py::test_run_export_reports_pending_mapping_blockers_when_no_ready_rows` anchors the operational consequence that `pending_mapping` blocks ready export.

## Current Enum Drift To Keep In Mind

`peap_core/pipeline_state_contracts.py` still defines `pending_review`, but the current streaming ingest/service path does not classify records into that state. For backend mainline documentation, treat `pending_review` as inactive contract residue until code starts emitting it from `streaming_ingest`, `streaming_store` maintenance, or `app_service`.

## Operational Consequences

- `pending_mapping` records create unresolved `mapping_pending` work items and are counted by `count_pending_mappings()`.
- `mapping_conflict` records remain persisted review work, not missing-rule backlog. Maintenance preserves that distinction and resolves stale historical `mapping_pending` rows whose latest state no longer owns backlog.
- `refresh_postprocess` can move a record from `pending_mapping` to `ready` without reparsing if new mapping rules resolve the findings and the stored record is not already carrying `conflict`.
- `reprocess_record` is the only path that intentionally revisits parse output; it is also the recovery path for failed records when the original evidence still exists.
- Fresh ingest failure paths and background refresh/reprocess failure paths are not identical. Fresh parse/postprocess failures create or update failed sibling rows through `upsert_failed_record()`, while some app-service-managed refresh/reprocess exception handlers transition the original row in place to `parse_failed`.
