# Debugging Playbook

This playbook is for operational diagnosis, not for architecture overview. It starts from the symptom you can observe in storage, `/api/records`, or export, then narrows the fault domain by checking which layer last contained the missing field or blocker.

## Field Missing

### 1. Field missing in parser output

If the field is already absent from `parser_payload`, the problem is upstream of mappings and canonicalization. Inspect the latest revision in `record_revisions`, or use `StreamingStore.get_record()` / `iter_latest_records()` to compare the stored `parser_payload` against the source file. If `parser_payload` never carried the field, `refresh_postprocess()` will not invent it; only parser fixes plus `reprocess_record` can change that. On success-path rows, remember that top-level `source_file` usually points at the archived snapshot, not the original raw file; use `source_identity_json["original_source_file"]` when you need the original capture path.

### 2. Field present in parser output but missing after postprocess

If `parser_payload` contains the field and `postprocess_payload` does not, the fault is in postprocess normalization, rule application, or mapping enrichment. Look at the stored `findings` and at `postprocess_payload` itself. For mapping-sensitive fields such as `source_type` and `group_name`, the usual explanation is a `mapping_missing`, `mapping_gap`, `mapping_ambiguous`, or `mapping_conflict` finding. This is the layer where `refresh_postprocess()` is appropriate.

### 3. Field present after postprocess but missing in `canonical_record`

If the source-facing field survives postprocess but disappears from `canonical_record["canonical_fields"]` or `canonical_record["export_extras"]`, the problem is in `_build_canonical_record_payload()` or in project-type resolution before canonical assembly. This is the boundary checked by `tests/test_streaming_ingest.py` and the canonical/export regressions in `tests/test_pipeline_state_machine.py`.

### 4. Field present in `canonical_record` but missing only in API/export

At this point the problem is downstream of business truth. Check `project_canonical_record_to_export_payload()` in `peap/export_projection.py`, then `record_to_export_payload()` / `run_ready_export()` in `peap/streaming_export.py`, and finally `AppService._build_record_display_payload()` plus `_build_record_display_values()` in `desktop_backend/app_service.py`. `tests/test_streaming_export.py` and `tests/test_app_service.py` cover the most important regressions here, including stale projection preference and projection-only leakage.

## `pending_mapping` Diagnosis

Start with the stored `findings` for the latest revision, then correlate them with `AppService.list_pending_mappings()`:

- Missing type/group mappings: look for `mapping_missing`, `mapping_gap`, or `project_type_unknown`. These are normal `pending_mapping` blockers and usually mean new mapping entries or a project-type template fix are needed.
- Mapping conflicts: look for `mapping_conflict`. This is review work, but it is a different state from `pending_mapping` and should not be counted as a missing-rule backlog.
- Non-mapping blockers: `AppService._build_mapping_work_item()` can still surface a pending record with `non_mapping_blocker` when the row state is pending-like but the current analysis sees no missing mapping rule. In that case the stored findings, not just the latest recommendation list, are the authority.

`AppService.list_pending_mappings()` is a UI-facing analysis view over the latest `pending_mapping` / `mapping_conflict` rows, not a raw pass-through of the `mapping_pending` table. If you need the unresolved queue rows themselves, compare it with `StreamingStore.list_pending_mappings()`.

## Refresh Behavior

Choose the refresh path by the layer that changed:

- Use `refresh_postprocess` when the parser snapshot is still valid and only mappings, normalization rules, or classification logic changed.
- Use `reprocess_record` when parser behavior changed, the archived/original evidence changed, or the record is already failed and needs a fresh parse attempt.
- Do not use `refresh_postprocess` to re-check archive naming collisions. It preserves previously observed `conflict` state from the stored row; it does not rerun archive materialization.
- Use no refresh at all when `canonical_record` and `/api/records` already show the correct canonical field values. A changed `canonical_projection` alone is not enough reason to reparse because projection is derived and store will rebuild it on the next canonical write.

## Common False Conclusions

- “`source_identity_json` key count proves parser failed” is false. `source_identity_json` is identity evidence for row anchoring and reprocess path selection, not a parser health metric.
- “The projection has a field, so business truth has it” is false. `canonical_projection` is derived output cache; check `canonical_record` before trusting the field.
- “Writing a mapping rule should trigger full reparse” is false. Mapping refresh is designed to reuse `parser_payload` and rerun postprocess only.
- “`AppService.list_pending_mappings()` is just the raw `mapping_pending` table” is false. The app-service view recomputes operator-facing work items from latest pending-like records.
- “A projection-only ready row is good enough for export” is false. Export requires canonical truth; `tests/test_streaming_export.py::test_run_ready_export_rejects_projection_only_record_even_when_projection_is_complete` exists because that shortcut caused regressions before.
