import test from "node:test";
import assert from "node:assert/strict";

import {
  buildExportRequest,
  buildRecordScopeQuery,
  normalizeRecordScope,
} from "../src/contracts/recordScope.js";

test("normalizeRecordScope canonicalizes explicit family-aware business scope", () => {
  const normalized = normalizeRecordScope({
    record_family: "listing",
    business_id: "physical_asset",
    business_label: "实物资产",
    exchange: "beijing",
    state: "ready",
    keyword: "华润",
    page: "2",
    page_size: "25",
    project_type: "legacy",
  });

  assert.deepEqual(normalized, {
    record_family: "listing",
    state: "ready",
    business_id: "physical_asset",
    business_label: "实物资产",
    exchange: "cbex",
    keyword: "华润",
    date_from: "",
    date_to: "",
    page: 2,
    page_size: 25,
  });
});

test("normalizeRecordScope accepts Guangdong canonical and legacy exchange aliases", () => {
  assert.equal(normalizeRecordScope({ exchange: "guangdong" }).exchange, "guangdong");
  assert.equal(normalizeRecordScope({ exchange: "guangzhou" }).exchange, "guangdong");
  assert.equal(normalizeRecordScope({ exchange: "广交所" }).exchange, "guangdong");
  assert.equal(normalizeRecordScope({ exchange: "广东联合产权交易中心" }).exchange, "guangdong");
});

test("record scope query and export payload use business_id instead of project_type routing", () => {
  const scope = {
    record_family: "listing",
    state: "ready",
    business_id: "equity_transfer",
    business_label: "股权转让",
    exchange: "cbex",
    keyword: "北交所",
    date_from: "2026-03-01",
    date_to: "2026-03-31",
    page: 1,
    page_size: 50,
    project_type: "legacy",
  };

  assert.equal(
    buildRecordScopeQuery(scope),
    new URLSearchParams({
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "北交所",
      date_from: "2026-03-01",
      date_to: "2026-03-31",
      page: "1",
      page_size: "50",
    }).toString(),
  );

  assert.deepEqual(
    buildExportRequest(scope, { requested_export_mode: "full", cursor_key: "cursor-1", output_dir: "/tmp/export" }),
    {
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "北交所",
      date_from: "2026-03-01",
      date_to: "2026-03-31",
      requested_export_mode: "full",
      output_dir: "/tmp/export",
    },
  );
});

test("export payload accepts explicit browse scope across all businesses and exchanges", () => {
  assert.deepEqual(
    buildExportRequest(
      {
        record_family: "listing",
        state: "ready",
        business_id: "all",
        exchange: "all",
        keyword: "国资",
        page: 1,
        page_size: 50,
      },
      { requested_export_mode: "incremental" },
    ),
    {
      record_family: "listing",
      state: "ready",
      business_id: "all",
      exchange: "all",
      keyword: "国资",
      date_from: "",
      date_to: "",
      requested_export_mode: "incremental",
    },
  );
});

test("normalizeRecordScope preserves unknown record_family instead of coercing it to listing", () => {
  assert.equal(
    normalizeRecordScope({
      record_family: "agreement",
      business_id: "contract_transfer",
    }).record_family,
    "agreement",
  );
});

test("export helpers fail closed instead of inventing listing/all scope", () => {
  assert.throws(
    () => buildExportRequest({}, { requested_export_mode: "full" }),
    /scope/i,
  );
  assert.deepEqual(
    normalizeRecordScope({
      page: "2",
      page_size: "25",
    }),
    {
      record_family: "",
      state: "all",
      business_id: "",
      business_label: "",
      exchange: "",
      keyword: "",
      date_from: "",
      date_to: "",
      page: 2,
      page_size: 25,
    },
  );
});

test("export payload never emits legacy mode or cursor_key and defaults to full export", () => {
  const payload = buildExportRequest({
    record_family: "listing",
    business_id: "equity_transfer",
    exchange: "sse",
  });

  assert.equal(payload.requested_export_mode, "full");
  assert.equal(Object.hasOwn(payload, "mode"), false);
  assert.equal(Object.hasOwn(payload, "cursor_key"), false);
});

test("export payload rejects legacy export_mode alias and only accepts requested_export_mode", () => {
  assert.throws(
    () => buildExportRequest(
      {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      { export_mode: "incremental" },
    ),
    /export_mode/i,
  );

  assert.equal(
    buildExportRequest(
      {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      { requested_export_mode: "incremental" },
    ).requested_export_mode,
    "incremental",
  );
});
