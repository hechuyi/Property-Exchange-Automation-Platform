import test from "node:test";
import assert from "node:assert/strict";

import { resolveSettingsDefaultScopeEditor } from "../src/state/settingsDefaultScopeEditor.js";

test("settings default-scope editor derives the selected family from the single visible catalog family on a fresh install", () => {
  const editor = resolveSettingsDefaultScopeEditor({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "physical_asset", business_label: "实物资产" },
            { business_id: "equity_transfer", business_label: "股权转让" },
            { business_id: "capital_increase", business_label: "增资扩股" },
            { business_id: "pre_disclosure", business_label: "预披露" },
          ],
        },
      ],
    },
    basicSettings: {
      effective_default_scope: {},
      stored_preference: {},
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });

  assert.deepEqual(
    editor.family_options,
    [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        family_display_label: "挂牌业务",
      },
    ],
  );
  assert.equal(editor.selected_family_id, "listing");
  assert.deepEqual(
    editor.business_options.map((item) => ({
      business_id: item.business_id,
      business_label: item.business_label,
    })),
    [
      { business_id: "all", business_label: "全部" },
      { business_id: "physical_asset", business_label: "实物资产" },
      { business_id: "equity_transfer", business_label: "股权转让" },
      { business_id: "capital_increase", business_label: "增资扩股" },
      { business_id: "pre_disclosure", business_label: "预披露" },
    ],
  );
  assert.equal(editor.selected_business_id, "all");
  assert.equal(editor.family_selection_required, false);
});

test("settings default-scope editor preserves the stale stored family so the operator can recover from an invalid business", () => {
  const editor = resolveSettingsDefaultScopeEditor({
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
      effective_default_scope: {},
      stored_preference: {
        record_family: "listing",
        business_id: "not_a_real_business",
        exchange: "sse",
      },
      stale_default_metadata: {
        is_stale: true,
        reason: "unknown_business_id",
        hint: "reselect a supported business in settings",
      },
      default_exchange: "sse",
    },
  });

  assert.equal(editor.selected_family_id, "listing");
  assert.equal(editor.selected_business_id, "not_a_real_business");
  assert.equal(editor.business_options.length, 4);
  assert.equal(editor.business_options[0].business_id, "not_a_real_business");
  assert.equal(editor.business_options[0].unavailable, true);
  assert.equal(editor.stale_default_metadata.reason, "unknown_business_id");
});

test("settings default-scope editor falls back to the visible family when the stored family is no longer valid", () => {
  const editor = resolveSettingsDefaultScopeEditor({
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
      effective_default_scope: {},
      stored_preference: {
        record_family: "legacy_family",
        business_id: "legacy_business",
        exchange: "sse",
      },
      stale_default_metadata: {
        is_stale: true,
        reason: "unknown_record_family",
        hint: "reselect a supported business family in settings",
      },
      default_exchange: "sse",
    },
  });

  assert.equal(editor.selected_family_id, "listing");
  assert.equal(editor.selected_business_id, "all");
  assert.deepEqual(
    editor.business_options.map((item) => item.business_id),
    ["all", "physical_asset", "equity_transfer"],
  );
  assert.equal(editor.stale_default_metadata.reason, "unknown_record_family");
});

test("settings default-scope editor defaults a newly selected family to all-business instead of empty scope", () => {
  const editor = resolveSettingsDefaultScopeEditor({
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
        business_id: "equity_transfer",
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
      default_exchange: "all",
    },
    selectedFamilyId: "deal",
  });

  assert.equal(editor.selected_family_id, "deal");
  assert.equal(editor.selected_business_id, "all");
  assert.deepEqual(
    editor.business_options.map((item) => item.business_id),
    ["all", "deal_notice"],
  );
});

test("settings default-scope editor scopes exchange options to exportable family+business and all-business intersection", () => {
  const editor = resolveSettingsDefaultScopeEditor({
    catalog: {
      visible_families: [
        {
          family_id: "deal",
          family_label: "成交业务",
          businesses: [
            { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "export"] },
            { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "export"] },
          ],
        },
      ],
      sources: [
        { source_id: "cbex", source_label: "北京产权交易所" },
        { source_id: "sse", source_label: "上海联合产权交易所" },
        { source_id: "tpre", source_label: "天津产权交易中心" },
        { source_id: "cquae", source_label: "重庆联交所" },
      ],
      support_matrix: {
        deal: {
          deal_equity_transfer: { records: true, export: true },
          deal_physical_asset: { records: true, export: true },
        },
      },
      surface_source_matrix: {
        deal: {
          deal_equity_transfer: { records: ["cbex", "sse", "tpre", "cquae"], export: ["cbex", "sse", "tpre", "cquae"] },
          deal_physical_asset: { records: ["cbex", "sse"], export: ["cbex", "sse"] },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "deal",
        business_id: "all",
        exchange: "tpre",
      },
      stored_preference: {
        record_family: "deal",
        business_id: "all",
        exchange: "tpre",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });

  assert.equal(editor.selected_family_id, "deal");
  assert.equal(editor.selected_business_id, "all");
  assert.equal(editor.selected_exchange, "all");
  assert.deepEqual(
    editor.exchange_options.map((item) => item.source_id),
    ["all", "cbex", "sse"],
  );
  assert.deepEqual(
    editor.partial_exchange_options.map((item) => item.source_id),
    ["tpre", "cquae"],
  );
});

test("settings default-scope editor does not invent exchange options when export source matrix is missing", () => {
  const editor = resolveSettingsDefaultScopeEditor({
    catalog: {
      visible_families: [
        {
          family_id: "deal",
          family_label: "成交业务",
          businesses: [
            { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "export"] },
          ],
        },
      ],
      sources: [
        { source_id: "cbex", source_label: "北京产权交易所" },
        { source_id: "sse", source_label: "上海联合产权交易所" },
      ],
      support_matrix: {
        deal: {
          deal_physical_asset: { records: true, export: true },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "deal",
        business_id: "deal_physical_asset",
        exchange: "all",
      },
      stored_preference: {
        record_family: "deal",
        business_id: "deal_physical_asset",
        exchange: "all",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });

  assert.deepEqual(
    editor.exchange_options.map((item) => item.source_id),
    ["all"],
  );
});

test("settings default-scope editor export options hide unsupported listing capital-increase sources", () => {
  const baseCatalog = {
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        businesses: [
          { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "export"] },
          { business_id: "capital_increase", business_label: "增资扩股", supported_surfaces: ["records", "export"] },
        ],
      },
    ],
    sources: [
      { source_id: "shandong", source_label: "山东产权交易中心" },
      { source_id: "guangdong", source_label: "广东联合产权交易中心" },
      { source_id: "shenzhen", source_label: "深圳联合产权交易所" },
    ],
    support_matrix: {
      listing: {
        equity_transfer: { records: true, export: true },
        capital_increase: { records: true, export: true },
      },
    },
    surface_source_matrix: {
      listing: {
        equity_transfer: { records: ["shandong", "guangdong", "shenzhen"], export: ["shandong", "guangdong", "shenzhen"] },
        capital_increase: { records: ["shenzhen"], export: ["shenzhen"] },
      },
    },
  };

  const equityEditor = resolveSettingsDefaultScopeEditor({
    catalog: baseCatalog,
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "all",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "all",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });
  const capitalEditor = resolveSettingsDefaultScopeEditor({
    catalog: baseCatalog,
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "capital_increase",
        exchange: "guangdong",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "capital_increase",
        exchange: "guangdong",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });

  assert.deepEqual(
    equityEditor.exchange_options.map((item) => item.source_id),
    ["all", "shandong", "guangdong", "shenzhen"],
  );
  assert.deepEqual(
    capitalEditor.exchange_options.map((item) => item.source_id),
    ["all", "shenzhen"],
  );
  assert.equal(capitalEditor.selected_exchange, "all");
});

test("settings default-scope editor exposes structured scope policy IDs for the selected default scope", () => {
  const editor = resolveSettingsDefaultScopeEditor({
    catalog: {
      visible_families: [
        {
          family_id: "listing",
          family_label: "挂牌业务",
          businesses: [
            { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "export"] },
          ],
        },
      ],
      sources: [
        { source_id: "shandong", source_label: "山东产权交易中心" },
        { source_id: "guangdong", source_label: "广东联合产权交易中心" },
      ],
      support_matrix: {
        listing: {
          equity_transfer: { records: true, export: true },
        },
      },
      surface_source_matrix: {
        listing: {
          equity_transfer: { records: ["shandong", "guangdong"], export: ["shandong", "guangdong"] },
        },
      },
      source_business_requirements: {
        listing: {
          equity_transfer: {
            shandong: {
              scope_policy: "central_soe_ministry_only",
              scope_policy_label: "央企范围限定",
              scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
            },
            guangdong: {
              scope_policy: "central_soe_ministry_only",
              scope_policy_label: "央企范围限定",
              scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
            },
          },
        },
      },
    },
    basicSettings: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "all",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "all",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "all",
    },
  });

  assert.deepEqual(editor.scope_policy_ids, ["central_soe_ministry_only"]);
  assert.deepEqual(editor.scope_policies, [
    {
      policy_id: "central_soe_ministry_only",
      label: "央企范围限定",
      summary: "仅覆盖中央企业及其所属单位项目。",
    },
  ]);
});
