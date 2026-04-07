# Backend Documentation

This directory is the authority map for the current streaming backend mainline in this worktree. It explains the live parse -> postprocess -> canonical -> store -> API/export chain, names which modules are authoritative at each boundary, and excludes older compatibility-era habits such as treating raw payloads or cached flat projections as business truth.

## Read Order

- [Backend Overview](backend-overview.md): runtime shape, entrypoints, and module responsibilities.
- [Backend Dataflow And Contracts](backend-dataflow-and-contracts.md): terminology, truth layers, invariants, and field lineage.
- [API Contracts](api-contracts.md): `/api/records` output contract and export-facing row semantics.
- [Storage Schema](storage-schema.md): `records`, `record_revisions`, mapping work queues, jobs, and revisions.
- [Mapping Refresh And State Machine](mapping-refresh-and-state-machine.md): `reprocess_record`, `refresh_postprocess`, and active record states.
- [Debugging Playbook](debugging-playbook.md): diagnosis paths for missing fields, mapping blockers, and refresh choices.

## What Counts As The Backend Mainline

For these docs, the backend mainline is the streaming path implemented by `peap/streaming_ingest.py`, `peap/streaming_store.py`, `peap/export_projection.py`, `peap/streaming_export.py`, `desktop_backend/app_service.py`, and the route adapter in `desktop_backend/app_backend.py`. The design center is not “whatever raw parser payload happened to contain”; it is the persisted canonical truth layer that ingest builds, store re-derives, and API/export consume.

## Authority Map

| Concern | Authoritative module(s) | Why |
| --- | --- | --- |
| Ingest and canonicalization | `peap/streaming_ingest.py` | `StreamingIngestRunner.ingest()` and `StreamingIngestRunner.refresh_postprocess()` build `parser_payload`, `postprocess_payload`, and `canonical_record`, then persist them through the store boundary. |
| Record-state policy | `peap_core/record_state_policy.py` | Shared classification/backlog/export-blocker policy consumed by ingest, store maintenance, and app-service export empty-result handling. |
| Persistence and revision semantics | `peap/streaming_store.py` | `StreamingStore.upsert_record()` owns success-path latest rows and revisions; `StreamingStore.upsert_failed_record()` owns failed-row persistence, while the store owns `mapping_pending` synchronization and export cursors. |
| Export projection and file generation | `peap/export_projection.py`, `peap/streaming_export.py` | `project_canonical_record_to_export_payload()` is the primary canonical-to-flat projection boundary used by export and API shaping; `run_ready_export()` groups ready records into output artifacts. |
| App-service/API behavior | `desktop_backend/app_service.py`, `desktop_backend/app_backend.py` | `list_records()`, `run_export()`, `reprocess_record()`, and mapping endpoints define the service contract exposed under `/api/*`. |

## Truth Layers

| Representation | Status | Intended role |
| --- | --- | --- |
| `parser_payload` | audit / refresh input | Snapshot of parse output plus ingest-added identifiers such as `page_url` and `project_id`; kept for audit and for later `refresh_postprocess()` reuse. |
| `postprocess_payload` | audit / refresh input | Normalized and mapping-enriched payload that still follows source-field vocabulary; it is not the business truth layer. |
| `canonical_record` | authoritative | Business truth layer built by ingest and consumed by store/export logic; `canonical_fields` and `export_extras` are the main contract surfaces. |
| `canonical_projection` | derived | Flat export/API cache re-derived from `canonical_record`; it is convenient output shape, not business truth. |
| `records` | authoritative for latest row identity and latest state | Current row for one business key, plus duplicated filter/status columns and `latest_revision_id`. |
| `record_revisions` | authoritative revision history | Revision snapshots of payloads, findings, and canonical material for the latest or changed content. |
| `source_identity_json` | audit / identity evidence | Evidence for record identity, reprocess source selection, failed-record anchoring, and replay context such as `source_url` or snapshot metadata; not a business payload. |

## Historical Mistakes This Doc Set Intentionally Avoids

- Do not merge `parser_payload` and `postprocess_payload` back into export/API rows when canonical material is incomplete; `peap/streaming_export.py` and `tests/test_streaming_export.py` explicitly reject that fallback.
- Do not treat `canonical_projection` as authoritative; `StreamingStore.upsert_record()` recomputes it from `canonical_record`, and `tests/test_streaming_store.py::test_upsert_record_recomputes_canonical_projection_from_canonical_record` exists to keep that rule true.
- Do not treat `source_identity_json` as business content; `desktop_backend/app_service.py::reprocess_record()` uses it for evidence lookup and replay context, but not as parser-health proof or business truth.
- Do not assume every enum value in `peap_core/pipeline_state_contracts.py` is an active mainline state. The current ingest/service path actively emits `ready`, `pending_mapping`, `mapping_conflict`, `conflict`, `skipped`, `parse_failed`, and `postprocess_failed`; `pending_review` remains contract residue, not a live branch in the streaming path.
