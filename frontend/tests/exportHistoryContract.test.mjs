import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeExportHistoryActionResult,
  normalizeExportHistoryCollection,
  normalizeExportHistoryDetail,
} from "../src/contracts/exportHistory.js";

test("export history detail rejects explicitly malformed detail DTO fields", () => {
  for (const [field, value] of [
    ["manifest", []],
    ["cursor_value", "cursor-a"],
    ["artifacts", {}],
    ["retention_count", {}],
  ]) {
    assert.throws(
      () => normalizeExportHistoryDetail({ export_id: "exp-1", [field]: value }),
      /Invalid export history detail/,
      `${field} should not be silently normalized`,
    );
  }
});

test("export history collection normalizes list rows without dropping retention state", () => {
  const data = normalizeExportHistoryCollection({
    rows: [
      {
        export_id: " exp-1 ",
        cursor_id: " cursor-a ",
        requested_export_mode: " full ",
        revision_watermark: "42",
        created_at: "2026-05-18T09:30:00+08:00",
        artifact_count: "2",
        openable: true,
        rebuildable: true,
        is_tombstone: false,
        pruned_by_retention: false,
        retention_count: "20",
      },
      {
        export_id: "exp-old",
        requested_export_mode: "incremental",
        openable: false,
        rebuildable: false,
        is_tombstone: true,
        pruned_by_retention: true,
        retention_count: 3,
      },
    ],
  });

  assert.deepEqual(data.rows.map((row) => row.export_id), ["exp-1", "exp-old"]);
  assert.equal(data.rows[0].revision_watermark, 42);
  assert.equal(data.rows[0].artifact_count, 2);
  assert.equal(data.rows[1].openable, false);
  assert.equal(data.rows[1].retention_status, "pruned_by_retention");
  assert.equal(data.rows[1].retention_count, 3);
});

test("export history detail exposes manifest and cursor fields used by the UI", () => {
  const detail = normalizeExportHistoryDetail({
    export_id: "exp-1",
    cursor_id: "cursor-a",
    requested_export_mode: "full",
    revision_watermark: 42,
    created_at: "2026-05-18T09:30:00+08:00",
    artifacts: ["/tmp/export/a.xlsx", ""],
    existing_artifacts: ["/tmp/export/a.xlsx"],
    manifest: {
      effective_export_mode: "full",
      export_profile_id: "listing/equity_transfer",
      canonical_scope_hash: "hash-1",
      schema_version: "schema-v1",
      header_version: "headers-v1",
      included_count: 7,
      excluded_count: 1,
      artifact_checksum: "sha256:abc",
      field_missing_blocked_records: 2,
      cursor_basis: { export_id: "exp-1", eligible_set_hash: "eligible-1" },
      scope: { record_family: "listing", business_id: "equity_transfer", exchange: "sse" },
    },
    cursor_value: {
      last_successful_export_id: "exp-1",
      last_successful_revision_watermark: 42,
    },
    openable: true,
    rebuildable: true,
    retention_count: 20,
  });

  assert.equal(detail.export_id, "exp-1");
  assert.equal(detail.manifest.effective_export_mode, "full");
  assert.equal(detail.manifest.export_profile_id, "listing/equity_transfer");
  assert.equal(detail.manifest.schema_version, "schema-v1");
  assert.equal(detail.manifest.header_version, "headers-v1");
  assert.equal(detail.manifest.included_count, 7);
  assert.equal(detail.manifest.cursor_basis.export_id, "exp-1");
  assert.equal(detail.cursor_value.last_successful_revision_watermark, 42);
  assert.deepEqual(detail.artifacts, ["/tmp/export/a.xlsx"]);
  assert.equal(detail.retention_status, "available");
});

test("export history action result keeps unavailable tombstone responses explicit", () => {
  const result = normalizeExportHistoryActionResult({
    export_id: "exp-old",
    opened: false,
    downloaded: false,
    openable: false,
    rebuildable: false,
    is_tombstone: true,
    artifacts: [],
  });

  assert.equal(result.export_id, "exp-old");
  assert.equal(result.opened, false);
  assert.equal(result.downloaded, false);
  assert.equal(result.openable, false);
  assert.equal(result.rebuildable, false);
  assert.equal(result.is_tombstone, true);
  assert.deepEqual(result.artifacts, []);
});
