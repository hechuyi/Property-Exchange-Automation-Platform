import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveCatalogBusinessScopeSelection,
  resolveCatalogFamilyScopePlan,
} from "../src/state/businessScopeSelector.js";

test("resolveCatalogBusinessScopeSelection derives family, business options, and exchange from catalog plus basic settings", () => {
  const selection = resolveCatalogBusinessScopeSelection({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "physical_asset", business_label: "实物资产" },
            { business_id: "equity_transfer", business_label: "股权转让" },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "sse",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "cbex",
    },
    selectedBusinessId: "equity_transfer",
  });

  assert.deepEqual(
    selection.family_options,
    [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        family_display_label: "挂牌业务",
      },
    ],
  );
  assert.equal(selection.selected_family_id, "listing");
  assert.deepEqual(
    selection.business_options,
    [
      { business_id: "physical_asset", business_label: "实物资产", business_display_label: "实物资产" },
      { business_id: "equity_transfer", business_label: "股权转让", business_display_label: "股权转让" },
    ],
  );
  assert.equal(selection.selected_business_id, "equity_transfer");
  assert.equal(selection.selected_exchange, "sse");
});

test("resolveCatalogBusinessScopeSelection clears a selected business that is not valid under the chosen family", () => {
  const selection = resolveCatalogBusinessScopeSelection({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "physical_asset", business_label: "实物资产" },
          ],
        },
        {
          family_id: "deal",
          family_label: "成交业务",
          businesses: [
            { business_id: "deal_notice", business_label: "成交公告" },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "physical_asset",
        business_label: "实物资产",
        exchange: "sse",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "physical_asset",
        exchange: "sse",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "cbex",
    },
    selectedFamilyId: "deal",
    selectedBusinessId: "physical_asset",
    selectedExchange: "cbex",
  });

  assert.equal(selection.selected_family_id, "deal");
  assert.deepEqual(selection.business_options, [
    { business_id: "deal_notice", business_label: "成交公告", business_display_label: "成交公告" },
  ]);
  assert.equal(selection.selected_business_id, "");
  assert.equal(selection.selected_exchange, "cbex");
});

test("resolveCatalogBusinessScopeSelection can require explicit exchange selection for manual-import style flows", () => {
  const selection = resolveCatalogBusinessScopeSelection({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "equity_transfer", business_label: "股权转让" },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "cbex",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "cbex",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "sse",
    },
    selectedBusinessId: "equity_transfer",
    allowImplicitExchangeSelection: false,
  });

  assert.equal(selection.selected_family_id, "listing");
  assert.equal(selection.selected_business_id, "equity_transfer");
  assert.equal(selection.selected_exchange, "");
});

test("resolveCatalogBusinessScopeSelection keeps deal family business selection without listing fallback", () => {
  const selection = resolveCatalogBusinessScopeSelection({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "equity_transfer", business_label: "股权转让" },
          ],
        },
        {
          family_id: "deal",
          family_label: "成交业务",
          businesses: [
            { business_id: "deal_equity_transfer", business_label: "股权转让成交" },
            { business_id: "deal_physical_asset", business_label: "实物资产成交" },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "sse",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "cbex",
    },
    selectedFamilyId: "deal",
    selectedBusinessId: "deal_equity_transfer",
    selectedExchange: "cbex",
  });

  assert.equal(selection.selected_family_id, "deal");
  assert.equal(selection.selected_business_id, "deal_equity_transfer");
  assert.equal(selection.selected_exchange, "cbex");
  assert.deepEqual(
    selection.business_options.map((item) => item.business_id),
    ["deal_equity_transfer", "deal_physical_asset"],
  );
});

test("resolveCatalogFamilyScopePlan builds catalog-wide one-click family scopes", () => {
  const plan = resolveCatalogFamilyScopePlan({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["one_click"] },
            { business_id: "physical_asset", business_label: "实物资产", supported_surfaces: ["one_click"] },
          ],
        },
        {
          family_id: "deal",
          family_label: "成交业务",
          businesses: [
            { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["one_click"] },
            { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["one_click"] },
            { business_id: "deal_capital_increase", business_label: "增资扩股成交", supported_surfaces: ["one_click"] },
          ],
        },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { one_click: true },
          physical_asset: { one_click: true },
        },
        deal: {
          deal_physical_asset: { one_click: true },
          deal_equity_transfer: { one_click: true },
          deal_capital_increase: { one_click: true },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { one_click: ["cbex", "sse"] },
          physical_asset: { one_click: ["cbex"] },
        },
        deal: {
          deal_physical_asset: { one_click: ["cbex", "sse"] },
          deal_equity_transfer: { one_click: ["cbex", "sse", "tpre", "cquae"] },
          deal_capital_increase: { one_click: ["cbex", "sse", "tpre", "cquae"] },
        },
      },
    },
    surface: "one_click",
  });

  assert.deepEqual(plan.family_scopes, [
    { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
    { record_family: "deal", business_id: "all", business_label: "", exchange: "all" },
  ]);
});
