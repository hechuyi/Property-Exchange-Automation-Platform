import test from "node:test";
import assert from "node:assert/strict";

import {
  buildRecordsScopeFromBrowseRuntime,
  describeDefaultScopeRuntime,
  describeRecordsBrowseRuntime,
  resolveRecordsBrowseRuntime,
  resolveDefaultScopeRuntime,
} from "../src/state/defaultScopeRuntime.js";

test("resolveDefaultScopeRuntime reports explicit blocked states instead of inventing defaults", () => {
  const missingCatalog = resolveDefaultScopeRuntime({
    catalog: {},
    basicSettings: {},
    surface: "records",
  });
  assert.equal(missingCatalog.state, "missing_catalog");
  assert.equal(missingCatalog.scope, null);

  const stale = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, one_click: true, export: true },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "cbex",
      },
      stale_default_metadata: {
        is_stale: true,
        reason: "unknown_business_id",
        hint: "原默认业务已失效，请重新选择。",
      },
    },
    surface: "records",
  });

  assert.equal(stale.state, "stale_default_scope");
  assert.equal(stale.scope, null);
  assert.match(describeDefaultScopeRuntime(stale), /已失效/);
});
test("resolveDefaultScopeRuntime distinguishes unsupported defaults from ready defaults", () => {
  const runtime = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records"],
            },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, one_click: true, export: true },
          physical_asset: { records: true, one_click: false, export: false },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { records: ["cbex", "sse"], one_click: ["cbex", "sse"], export: ["cbex", "sse"] },
          physical_asset: { records: ["cbex"], one_click: [], export: [] },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "physical_asset",
        business_label: "实物资产",
        exchange: "cbex",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    surface: "one_click",
  });

  assert.equal(runtime.state, "unsupported");
  assert.equal(runtime.scope, null);
  assert.equal(runtime.supportedBusinesses.length, 1);
  assert.equal(runtime.supportedBusinesses[0].business_id, "equity_transfer");
  assert.equal(buildRecordsScopeFromBrowseRuntime(runtime, { state: "ready" }), null);
});

test("resolveDefaultScopeRuntime rejects a default exchange that violates the source-aware surface contract", () => {
  const runtime = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "deal",
          businesses: [
            {
              business_id: "deal_physical_asset",
              business_label: "实物资产成交",
              supported_surfaces: ["records", "one_click", "export"],
            },
          ],
        },
      ],
      support_matrix: {
        deal: {
          deal_physical_asset: { records: true, one_click: true, export: true },
        },
      },
      surface_source_matrix: {
        deal: {
          deal_physical_asset: {
            records: ["cbex", "sse"],
            one_click: ["cbex", "sse"],
            export: ["cbex", "sse"],
          },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "deal",
        business_id: "deal_physical_asset",
        business_label: "实物资产成交",
        exchange: "tpre",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    surface: "one_click",
  });

  assert.equal(runtime.state, "unsupported");
  assert.equal(runtime.scope, null);
});

test("resolveDefaultScopeRuntime keeps all-business defaults actionable when the full family supports the surface", () => {
  const runtime = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records", "one_click", "export"],
            },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, one_click: true, export: true },
          physical_asset: { records: true, one_click: true, export: true },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { one_click: ["cbex"] },
          physical_asset: { one_click: ["cbex"] },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    surface: "one_click",
  });

  assert.equal(runtime.state, "ready");
  assert.deepEqual(runtime.scope, {
    record_family: "listing",
    business_id: "all",
    business_label: "",
    exchange: "all",
  });
});

test("resolveDefaultScopeRuntime attaches source-business requirements for the actionable scope", () => {
  const backendOnlyField = ["required", "query", "filters"].join("_");
  const runtime = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, one_click: true, export: true },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { one_click: ["shandong"], export: ["shandong"], records: ["shandong"] },
        },
      },
      source_business_requirements: {
        listing: {
          equity_transfer: {
            shandong: {
              scope_policy: "central_soe_ministry_only",
              scope_policy_label: "央企范围限定",
              scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
              [backendOnlyField]: { opaque: "backend-only" },
            },
          },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "shandong",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    surface: "one_click",
  });

  assert.equal(runtime.state, "ready");
  assert.deepEqual(runtime.source_business_requirements, [
    {
      record_family: "listing",
      business_id: "equity_transfer",
      source_id: "shandong",
      scope_policy: "central_soe_ministry_only",
      scope_policy_label: "央企范围限定",
      scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
    },
  ]);
});

test("resolveDefaultScopeRuntime expands all-business all-source requirements from the surface matrix", () => {
  const backendOnlyField = ["required", "query", "filters"].join("_");
  const runtime = resolveDefaultScopeRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records", "one_click", "export"],
            },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, one_click: true, export: true },
          physical_asset: { records: true, one_click: true, export: true },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { one_click: ["cbex", "shandong"], export: ["cbex", "shandong"], records: ["cbex", "shandong"] },
          physical_asset: { one_click: ["cbex", "tpre"], export: ["cbex", "tpre"], records: ["cbex", "tpre"] },
        },
      },
      source_business_requirements: {
        listing: {
          equity_transfer: {
            shandong: {
              scope_policy: "central_soe_ministry_only",
              scope_policy_label: "央企范围限定",
              scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
              [backendOnlyField]: { opaque: "backend-only" },
            },
          },
          physical_asset: {
            tpre: {
              scope_policy: "physical_asset_min_price_5000w",
              scope_policy_label: "实物资产金额门槛",
              scope_policy_summary: "仅覆盖挂牌价不低于 5000 万元的实物资产项目。",
              [backendOnlyField]: { opaque: ["backend-only"] },
            },
          },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    surface: "one_click",
  });

  assert.equal(runtime.state, "ready");
  assert.deepEqual(runtime.source_business_requirements, [
    {
      record_family: "listing",
      business_id: "equity_transfer",
      source_id: "shandong",
      scope_policy: "central_soe_ministry_only",
      scope_policy_label: "央企范围限定",
      scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
    },
    {
      record_family: "listing",
      business_id: "physical_asset",
      source_id: "tpre",
      scope_policy: "physical_asset_min_price_5000w",
      scope_policy_label: "实物资产金额门槛",
      scope_policy_summary: "仅覆盖挂牌价不低于 5000 万元的实物资产项目。",
    },
  ]);
});

test("resolveRecordsBrowseRuntime keeps records browsing available even when actionable default scope is stale", () => {
  const runtime = resolveRecordsBrowseRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "equity_transfer",
              business_label: "股权转让",
              supported_surfaces: ["records", "one_click", "export"],
            },
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records"],
            },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {},
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "not_a_real_exchange",
      },
      stale_default_metadata: {
        is_stale: true,
        reason: "invalid_exchange",
        hint: "reselect a supported exchange in settings",
      },
    },
  });

  assert.equal(runtime.state, "ready");
  assert.deepEqual(runtime.scope, {
    record_family: "listing",
    business_id: "all",
    business_label: "",
    exchange: "all",
  });
  assert.equal(describeRecordsBrowseRuntime(runtime), "");
  assert.deepEqual(
    buildRecordsScopeFromBrowseRuntime(runtime, { state: "pending_review", keyword: "国资" }, { page: 2, page_size: 20 }),
    {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
      state: "pending_review",
      keyword: "国资",
      date_from: "",
      date_to: "",
      page: 2,
      page_size: 20,
    },
  );
});

test("buildRecordsScopeFromBrowseRuntime rejects stale family filters outside catalog-visible records families", () => {
  const runtime = resolveRecordsBrowseRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records"],
            },
          ],
        },
      ],
    },
    basicSettings: {},
  });

  assert.deepEqual(
    buildRecordsScopeFromBrowseRuntime(runtime, { record_family: "deal", state: "all" }, { page: 1, page_size: 50 }),
    {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
      state: "all",
      keyword: "",
      date_from: "",
      date_to: "",
      page: 1,
      page_size: 50,
    },
  );
});

test("resolveRecordsBrowseRuntime ignores stale settings family outside catalog-visible records families", () => {
  const runtime = resolveRecordsBrowseRuntime({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          businesses: [
            {
              business_id: "physical_asset",
              business_label: "实物资产",
              supported_surfaces: ["records"],
            },
          ],
        },
      ],
    },
    basicSettings: {
      stored_preference: {
        record_family: "deal",
        business_id: "deal_physical_asset",
        exchange: "cbex",
      },
    },
  });

  assert.equal(runtime.state, "ready");
  assert.deepEqual(runtime.scope, {
    record_family: "listing",
    business_id: "all",
    business_label: "",
    exchange: "all",
  });
  assert.deepEqual(
    buildRecordsScopeFromBrowseRuntime(runtime, {}, { page: 1, page_size: 50 }),
    {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
      state: "all",
      keyword: "",
      date_from: "",
      date_to: "",
      page: 1,
      page_size: 50,
    },
  );
});
