# Backend Dataflow And Contracts

This document is the contract core for the backend docs set. It defines the stable terminology once, separates authoritative layers from audit-only and derived layers, and describes the parse -> postprocess -> canonical -> projection -> store -> API/export chain in the exact order the current code executes it.

## Terminology

| Term | Definition in the current mainline |
| --- | --- |
| `parser_payload` | The parser snapshot created by `StreamingIngestRunner.ingest()` or reused by `refresh_postprocess()`. It keeps source-facing fields and ingest-added identity hints such as `page_url` and `project_id`. |
| `postprocess_payload` | The normalized payload returned by postprocess and `normalize_record_payload()`. It still uses source-facing field names and is the input to state classification and canonical assembly, not the final business contract. |
| `canonical_record` | The business truth layer assembled by `_build_canonical_record_payload()` in `peap/streaming_ingest.py`. Its effective contract is `canonical_fields` + `export_extras` + diagnostics/policy metadata. |
| `canonical_projection` | The flat export/API cache derived from `canonical_record` by `project_canonical_record_to_export_payload()`. It is stored for convenience but re-derived by `StreamingStore.upsert_record()`. |
| `export_extras` | Non-core flat output fields copied into `canonical_record` from `parser_payload`/`postprocess_payload` when they belong to the output contract but are not part of `canonical_fields`; for example `挂牌次数` and public-resource export fields. |
| `records` | The latest-row table in SQLite. It stores the current state, current filter columns, row identity, and `latest_revision_id`. |
| `record_revisions` | The revision table in SQLite. It stores the latest or changed snapshots of `parser_payload`, `postprocess_payload`, `canonical_record`, `canonical_projection`, findings, and state. |
| `reprocess_record` | The app-service operation in `desktop_backend/app_service.py` that selects an evidence file and reruns full ingest, including parse. |
| `refresh_postprocess` | The ingest operation in `peap/streaming_ingest.py` that reruns postprocess and canonical assembly from stored `parser_payload` without reparsing the HTML file. |

## Do Not Confuse These Layers

- `canonical_record` is the authoritative business truth layer. If a business field matters for export or row display, it must survive here.
- `canonical_projection` is a derived output cache. It is convenient for display/export shape, but store recomputes it from `canonical_record` and tests reject projection-only truth.
- `parser_payload` and `postprocess_payload` are retained for audit, diagnosis, and refresh input only. They are not authoritative business payloads.
- `source_identity_json` is identity evidence for row anchoring and reprocess source lookup. It is not business content and it does not prove parser success or failure by itself.

## Canonical Dataflow

The current chain is `parse -> postprocess -> canonical_record -> derived canonical_projection -> store -> API/export`. The implementation path is `StreamingIngestRunner.ingest()` / `StreamingIngestRunner.refresh_postprocess()` in `peap/streaming_ingest.py`, `StreamingStore.upsert_record()` in `peap/streaming_store.py`, `record_to_export_payload()` / `run_ready_export()` in `peap/streaming_export.py`, and `AppService.list_records()` in `desktop_backend/app_service.py`.

| Stage | What is written | What is intentionally not written | Who reads it next | Guaranteed invariant |
| --- | --- | --- | --- | --- |
| Parse | `parser_payload` | No canonical fields, no export cache, no durable state | Postprocess, refresh input, audit/debug views | Parse output is preserved as a source snapshot. |
| Postprocess | `postprocess_payload`, findings | No authoritative flat export payload; `canonical_projection` is explicitly removed from the payload | State classifier and canonical builder | Postprocess findings determine review blockers before persistence. |
| Canonical assembly | `canonical_record` with `canonical_fields`, `export_extras`, `source_identity`, diagnostics, policy metadata | No raw-payload merge fallback semantics | Store, export projection, API row builder | Business-facing fields now have a canonical name and a canonical owner. |
| Projection | `canonical_projection` | No new business truth; no raw-field invention | Store cache, API/export formatting | Flat output is derived from canonical material only. |
| Store | `records`, `record_revisions`, `mapping_pending`, export cursor rows, job/audit tables | No hidden schema-level truth outside stored JSON and latest-row columns | App-service, export, maintenance, refresh | `records.latest_revision_id` points at the revision currently exposed by API/export. |
| API/export | `/api/records` envelope and ready-export artifacts | No raw payload passthrough contract | Desktop frontend and workbook writer | Display/export rows are shaped from canonical truth or explicit top-level fallback identifiers only. |

Current streaming ingest populates `field_provenance` as `{}` even though the richer contract in `peap_core/record_contracts.py` supports provenance. That means downstream code should treat provenance as optional metadata, not as a required source of truth.

## Identity And Ownership Boundaries

- On the success path, `StreamingIngestRunner` builds a candidate record, but `StreamingStore` owns the persisted `business_key` and the final `record_id`.
- For success rows, `business_key` is `project_code.upper()` when a project code exists; otherwise it falls back to `source:<sha1(source_file)>`. Failed rows use a different namespace, `failed:{identity_anchor}`, through `upsert_failed_record()`.
- Successful ingest rewrites the latest-row `source_file` to the archived snapshot path. The original raw path is preserved in `source_identity_json["original_source_file"]`, not in the top-level latest-row columns.
- `revision_hash` is computed from `postprocess_payload`. When that hash stays the same, the latest revision row is updated in place with refreshed state, findings, canonical record, and derived projection; when it changes, a new revision row is appended.

## Field Lineage

| Canonical field | Output field name(s) | Sourced from | Consumed by |
| --- | --- | --- | --- |
| `seller` | `转让方` | `_build_canonical_record_payload()` takes the first non-empty value from `postprocess_payload["转让方"]` and `parser_payload["转让方"]`. An explicit empty string in postprocess falls through. | `project_canonical_record_to_export_payload()`, `AppService._build_record_top_level_fields()`, workbook writers. |
| `price` | `挂牌价格` | `_build_canonical_record_payload()` takes the first non-empty value from `postprocess_payload["挂牌价格"]` and `parser_payload["挂牌价格"]`. | Export projection, `AppService` top-level `price`, workbook writers. |
| `start_date` | `挂牌开始日期` | `_build_canonical_record_payload()` uses the first non-empty listing-date field from parse/top-level ingest input. | Export projection, date filtering via top-level `records.listing_date`, workbook rows. |
| `project_type` | top-level `project_type`, `项目类型` | `_run_postprocess_pipeline()` resolves project type from postprocess/parser/upstream fallback and writes the resolved label into both record columns and `canonical_fields`. | API scope filtering, export grouping, row display. |
| `source_type` | `类型` | `_build_canonical_record_payload()` takes the first non-empty value from `postprocess_payload["类型"]` and `parser_payload["类型"]`. | Export projection, mapping work items, UI row display. |
| `group_name` | `隶属集团` | `_build_canonical_record_payload()` takes the first non-empty value from `postprocess_payload["隶属集团"]` and `parser_payload["隶属集团"]`. | Export projection and mapping diagnostics. |
| `listing_times` | `挂牌次数` via `export_extras`, plus `canonical_fields["listing_times"]` | `_build_canonical_record_payload()` reads `挂牌次数` from postprocess unless it is blank/`None`, otherwise from parser; it writes the value into `canonical_fields["listing_times"]` and also copies a non-empty value into `export_extras["挂牌次数"]`. | `project_canonical_record_to_export_payload()` and `AppService.list_records()` row values. |

The tests that keep this chain honest are spread across `tests/test_streaming_ingest.py` (canonical construction and refresh), `tests/test_streaming_store.py` (projection recomputation and revision semantics), `tests/test_streaming_export.py` (projection-only rejection and extra fields), and `tests/test_app_service.py` (row display contract).
