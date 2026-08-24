import test from "node:test";
import assert from "node:assert/strict";

import {
  getCatalogDefaultScope,
  listSurfaceSourceOptions,
  listSurfaceBusinesses,
  normalizeCatalogResource,
  resolveActionableDefaultScope,
} from "../src/contracts/catalog.js";

test("normalizeCatalogResource keeps only canonical catalog directory fields", () => {
  const normalized = normalizeCatalogResource({
    active_profile: { profile_id: "desktop_listing", legacy: true },
    visible_families: [
      {
        family_id: "listing",
        family_label: "Listing",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
            legacy: true,
          },
          {
            business_id: "physical_asset",
            business_label: "Physical Asset",
            supported_surfaces: ["records"],
          },
        ],
      },
    ],
    sources: [
      {
        source_id: "cbex",
        source_label: "北京产权交易所",
        record_families: ["listing"],
        legacy: true,
      },
    ],
    support_matrix: {
      listing: {
        equity_transfer: { records: true, one_click: true, export: true, legacy: true },
        physical_asset: { records: true, one_click: false, export: false },
      },
    },
    surface_source_matrix: {
      listing: {
        equity_transfer: { records: ["cbex", "sse"], one_click: ["cbex"], export: ["cbex", "sse"] },
        physical_asset: { records: ["cbex"], one_click: [], export: [] },
      },
    },
    source_business_requirements: {},
    default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "Equity Transfer",
      exchange: "cbex",
      project_type: "legacy",
    },
    visibility: {
      mode: "listing_only",
      visible_families: ["listing"],
      legacy: true,
    },
    product_profile: { legacy: true },
  });

  assert.deepEqual(normalized, {
    active_profile: { profile_id: "desktop_listing" },
    visible_families: [
      {
        family_id: "listing",
        family_label: "Listing",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "physical_asset",
            business_label: "Physical Asset",
            supported_surfaces: ["records"],
          },
        ],
      },
    ],
    sources: [
      {
        source_id: "cbex",
        source_label: "北京产权交易所",
        record_families: ["listing"],
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
        equity_transfer: { records: ["cbex", "sse"], one_click: ["cbex"], export: ["cbex", "sse"] },
        physical_asset: { records: ["cbex"], one_click: [], export: [] },
      },
    },
    source_business_requirements: {},
    default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "Equity Transfer",
      exchange: "cbex",
    },
    visibility: {
      mode: "listing_only",
      visible_families: ["listing"],
    },
  });
});

test("catalog helpers expose per-surface business options and actionable defaults", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "listing",
        family_label: "Listing",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "physical_asset",
            business_label: "Physical Asset",
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
        equity_transfer: { records: ["cbex", "sse"], one_click: ["cbex"], export: ["cbex", "sse"] },
        physical_asset: { records: ["cbex"], one_click: [], export: [] },
      },
    },
    default_scope: {
      record_family: "listing",
      business_id: "physical_asset",
      business_label: "Physical Asset",
      exchange: "sse",
    },
    visibility: { mode: "listing_only", visible_families: ["listing"] },
  });

  assert.deepEqual(
    listSurfaceBusinesses(catalog, { record_family: "listing", surface: "one_click" }),
    [
      {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "Equity Transfer",
        supported: true,
        supported_surfaces: ["records", "one_click", "export"],
      },
      {
        record_family: "listing",
        business_id: "physical_asset",
        business_label: "Physical Asset",
        supported: false,
        supported_surfaces: ["records"],
      },
    ],
  );

  assert.equal(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "listing",
        business_id: "physical_asset",
        business_label: "Physical Asset",
        exchange: "sse",
      },
      { surface: "one_click" },
    ),
    null,
  );

  assert.deepEqual(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "Equity Transfer",
        exchange: "cbex",
      },
      { surface: "one_click" },
    ),
    {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "Equity Transfer",
      exchange: "cbex",
    },
  );
});

test("catalog helpers reject defaults when the exchange is outside the surface source contract", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "deal",
        family_label: "Deal",
        businesses: [
          {
            business_id: "deal_physical_asset",
            business_label: "Deal Physical",
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
    default_scope: {
      record_family: "deal",
      business_id: "deal_physical_asset",
      business_label: "Deal Physical",
      exchange: "tpre",
    },
  });

  assert.equal(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "deal_physical_asset",
        business_label: "Deal Physical",
        exchange: "tpre",
      },
      { surface: "one_click" },
    ),
    null,
  );

  assert.deepEqual(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "deal_physical_asset",
        business_label: "Deal Physical",
        exchange: "cbex",
      },
      { surface: "one_click" },
    ),
    {
      record_family: "deal",
      business_id: "deal_physical_asset",
      business_label: "Deal Physical",
      exchange: "cbex",
    },
  );
});

test("catalog helpers treat all-business scopes as actionable only when every business supports the surface", () => {
  const fullySupportedCatalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "listing",
        family_label: "Listing",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "physical_asset",
            business_label: "Physical Asset",
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
    default_scope: {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
    },
  });

  assert.deepEqual(
    getCatalogDefaultScope(fullySupportedCatalog),
    {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
    },
  );

  assert.deepEqual(
    resolveActionableDefaultScope(
      fullySupportedCatalog,
      {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      { surface: "one_click" },
    ),
    {
      record_family: "listing",
      business_id: "all",
      business_label: "",
      exchange: "all",
    },
  );

  const partiallySupportedCatalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "listing",
        family_label: "Listing",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "physical_asset",
            business_label: "Physical Asset",
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
  });

  assert.equal(
    resolveActionableDefaultScope(
      partiallySupportedCatalog,
      {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      { surface: "one_click" },
    ),
    null,
  );
});

test("catalog helpers allow all-business all-exchange scopes with per-business source subsets", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "deal",
        family_label: "Deal",
        businesses: [
          {
            business_id: "deal_equity_transfer",
            business_label: "Deal Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "deal_physical_asset",
            business_label: "Deal Physical Asset",
            supported_surfaces: ["records", "one_click", "export"],
          },
        ],
      },
    ],
    sources: [
      { source_id: "cbex", source_label: "北交所", record_families: ["deal"] },
      { source_id: "sse", source_label: "上交所", record_families: ["deal"] },
      { source_id: "tpre", source_label: "天交所", record_families: ["deal"] },
      { source_id: "cquae", source_label: "重交所", record_families: ["deal"] },
    ],
    support_matrix: {
      deal: {
        deal_equity_transfer: { records: true, one_click: true, export: true },
        deal_physical_asset: { records: true, one_click: true, export: true },
      },
    },
    surface_source_matrix: {
      deal: {
        deal_equity_transfer: { export: ["cbex"] },
        deal_physical_asset: { export: ["cbex"] },
      },
    },
    surface_source_matrix: {
      deal: {
        deal_equity_transfer: {
          records: ["cbex", "sse", "tpre", "cquae"],
          one_click: ["cbex", "sse", "tpre", "cquae"],
          export: ["cbex", "sse", "tpre", "cquae"],
        },
        deal_physical_asset: {
          records: ["cbex", "sse"],
          one_click: ["cbex", "sse"],
          export: ["cbex", "sse"],
        },
      },
    },
  });

  assert.deepEqual(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      { surface: "one_click" },
    ),
    {
      record_family: "deal",
      business_id: "all",
      business_label: "",
      exchange: "all",
    },
  );

  assert.equal(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "all",
        business_label: "",
        exchange: "tpre",
      },
      { surface: "one_click" },
    ),
    null,
  );
});

test("getCatalogDefaultScope only returns a catalog-declared executable default", () => {
  assert.equal(getCatalogDefaultScope({}), null);
  assert.equal(
    getCatalogDefaultScope({
      default_scope: {
        record_family: "listing",
        exchange: "cbex",
      },
    }),
    null,
  );
  assert.deepEqual(
    getCatalogDefaultScope({
      default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "Equity Transfer",
        exchange: "cbex",
      },
    }),
    {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "Equity Transfer",
      exchange: "cbex",
    },
  );
});

test("catalog helpers can resolve executable deal surfaces after backend gate opens", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "deal",
        family_label: "Deal",
        businesses: [
          {
            business_id: "deal_equity_transfer",
            business_label: "Deal Equity Transfer",
            supported_surfaces: ["records", "one_click", "export"],
          },
          {
            business_id: "deal_physical_asset",
            business_label: "Deal Physical Asset",
            supported_surfaces: ["records", "one_click", "export"],
          },
        ],
      },
    ],
    support_matrix: {
      deal: {
        deal_equity_transfer: { records: true, one_click: true, export: true },
        deal_physical_asset: { records: true, one_click: true, export: true },
      },
    },
    surface_source_matrix: {
      deal: {
        deal_equity_transfer: { export: ["cbex"] },
        deal_physical_asset: { export: ["cbex"] },
      },
    },
    default_scope: {
      record_family: "deal",
      business_id: "deal_equity_transfer",
      business_label: "Deal Equity Transfer",
      exchange: "cbex",
    },
  });

  assert.deepEqual(
    listSurfaceBusinesses(catalog, { record_family: "deal", surface: "export" }),
    [
      {
        record_family: "deal",
        business_id: "deal_equity_transfer",
        business_label: "Deal Equity Transfer",
        supported: true,
        supported_surfaces: ["records", "one_click", "export"],
      },
      {
        record_family: "deal",
        business_id: "deal_physical_asset",
        business_label: "Deal Physical Asset",
        supported: true,
        supported_surfaces: ["records", "one_click", "export"],
      },
    ],
  );

  assert.deepEqual(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "deal_equity_transfer",
        business_label: "Deal Equity Transfer",
        exchange: "cbex",
      },
      { surface: "export" },
    ),
    {
      record_family: "deal",
      business_id: "deal_equity_transfer",
      business_label: "Deal Equity Transfer",
      exchange: "cbex",
    },
  );
});

test("catalog helpers derive records/export sources from surface source matrix and block illegal deal combinations", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "deal",
        family_label: "Deal",
        businesses: [
          {
            business_id: "deal_equity_transfer",
            business_label: "Deal Equity Transfer",
            supported_surfaces: ["records", "export"],
          },
          {
            business_id: "deal_physical_asset",
            business_label: "Deal Physical Asset",
            supported_surfaces: ["records", "export"],
          },
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
        deal_equity_transfer: {
          records: ["cbex", "sse", "tpre", "cquae"],
          export: ["cbex", "sse", "tpre", "cquae"],
        },
        deal_physical_asset: {
          records: ["cbex", "sse"],
          export: ["cbex", "sse"],
        },
      },
    },
  });

  assert.deepEqual(
    listSurfaceSourceOptions(catalog, {
      record_family: "deal",
      business_id: "deal_physical_asset",
      surface: "records",
      include_all: true,
      all_label: "全部交易所",
    }),
    [
      { source_id: "all", source_label: "全部交易所" },
      { source_id: "cbex", source_label: "北京产权交易所" },
      { source_id: "sse", source_label: "上海联合产权交易所" },
    ],
  );

  assert.deepEqual(
    listSurfaceSourceOptions(catalog, {
      record_family: "deal",
      business_id: "all",
      surface: "records",
      all_business_source_mode: "union",
      include_all: true,
      all_label: "全部交易所",
    }),
    [
      { source_id: "all", source_label: "全部交易所" },
      { source_id: "cbex", source_label: "北京产权交易所" },
      { source_id: "sse", source_label: "上海联合产权交易所" },
      { source_id: "tpre", source_label: "天津产权交易中心" },
      { source_id: "cquae", source_label: "重庆联交所" },
    ],
  );

  assert.equal(
    resolveActionableDefaultScope(
      catalog,
      {
        record_family: "deal",
        business_id: "deal_physical_asset",
        business_label: "Deal Physical Asset",
        exchange: "tpre",
      },
      { surface: "export" },
    ),
    null,
  );
});

test("catalog source options do not invent exchanges when a supported surface has no source matrix declaration", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "deal",
        family_label: "Deal",
        businesses: [
          {
            business_id: "deal_physical_asset",
            business_label: "Deal Physical Asset",
            supported_surfaces: ["records", "export"],
          },
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
  });

  assert.deepEqual(
    listSurfaceSourceOptions(catalog, {
      record_family: "deal",
      business_id: "deal_physical_asset",
      surface: "records",
      include_all: true,
      all_label: "全部交易所",
    }),
    [
      { source_id: "all", source_label: "全部交易所" },
    ],
  );

  assert.deepEqual(
    listSurfaceSourceOptions(catalog, {
      record_family: "deal",
      business_id: "deal_physical_asset",
      surface: "export",
      include_all: true,
      all_label: "全部交易所",
    }),
    [
      { source_id: "all", source_label: "全部交易所" },
    ],
  );
});

test("listing exchange surfaces expose only implemented new-source scopes", () => {
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        businesses: [
          { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
          { business_id: "capital_increase", business_label: "增资扩股", supported_surfaces: ["records", "one_click", "export"] },
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
        equity_transfer: { records: true, one_click: true, export: true },
        capital_increase: { records: true, one_click: true, export: true },
      },
    },
    surface_source_matrix: {
      listing: {
        equity_transfer: {
          records: ["shandong", "guangdong", "shenzhen"],
          one_click: ["shandong", "guangdong", "shenzhen"],
          export: ["shandong", "guangdong", "shenzhen"],
        },
        capital_increase: {
          records: ["shenzhen"],
          one_click: ["shenzhen"],
          export: ["shenzhen"],
        },
      },
    },
  });

  for (const surface of ["records", "one_click", "export"]) {
    assert.deepEqual(
      listSurfaceSourceOptions(catalog, {
        record_family: "listing",
        business_id: "equity_transfer",
        surface,
      }).map((item) => item.source_id),
      ["shandong", "guangdong", "shenzhen"],
    );
    assert.deepEqual(
      listSurfaceSourceOptions(catalog, {
        record_family: "listing",
        business_id: "capital_increase",
        surface,
      }).map((item) => item.source_id),
      ["shenzhen"],
    );
  }

});

test("catalog normalizer keeps source-business requirements display-only", () => {
  const backendOnlyField = ["required", "query", "filters"].join("_");
  const catalog = normalizeCatalogResource({
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        businesses: [
          { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
        ],
      },
    ],
    source_business_requirements: {
      listing: {
        equity_transfer: {
          shandong: {
            scope_policy: "central_soe_ministry_only",
            scope_policy_label: "央企范围限定",
            scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
            [backendOnlyField]: { opaque: "backend-only" },
          },
          shenzhen: {
            scope_policy: "central_soe_ministry_only",
            scope_policy_label: "央企范围限定",
            scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
            [backendOnlyField]: { opaque: ["backend-only"] },
          },
        },
      },
    },
  });

  assert.deepEqual(
    catalog.source_business_requirements.listing.equity_transfer.shandong,
    {
      scope_policy: "central_soe_ministry_only",
      scope_policy_label: "央企范围限定",
      scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
    },
  );
  assert.equal(
    Object.hasOwn(catalog.source_business_requirements.listing.equity_transfer.shenzhen, backendOnlyField),
    false,
  );
});
