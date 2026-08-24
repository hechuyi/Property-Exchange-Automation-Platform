import test from "node:test";
import assert from "node:assert/strict";

import {
  businessTypeLabel,
  exchangeDisplayLabel,
  recordFamilyLabel,
  RECORD_EXCHANGES,
} from "../src/constants/index.js";

test("display label helpers localize known ids while keeping raw contract labels intact", () => {
  assert.equal(recordFamilyLabel("listing", "Listing"), "挂牌业务");
  assert.equal(businessTypeLabel("equity_transfer", "Equity Transfer"), "Equity Transfer");
  assert.equal(businessTypeLabel("equity_transfer", "equity_transfer"), "股权转让");
  assert.equal(businessTypeLabel("all", ""), "全部业务类型");
  assert.equal(businessTypeLabel("all", "all"), "全部业务类型");
  assert.equal(businessTypeLabel("deal_equity_transfer", "Deal Equity Transfer"), "Deal Equity Transfer");
  assert.equal(businessTypeLabel("deal_equity_transfer", "deal_equity_transfer"), "股权转让成交");
  assert.equal(businessTypeLabel("deal_physical_asset", "Deal Physical Asset"), "Deal Physical Asset");
  assert.equal(businessTypeLabel("deal_capital_increase", "Deal Capital Increase"), "Deal Capital Increase");
  assert.equal(exchangeDisplayLabel("shenzhen", "shenzhen"), "深圳联合产权交易所");
  assert.equal(exchangeDisplayLabel("shandong", "shandong"), "山东产权交易中心");
  assert.equal(exchangeDisplayLabel("guangdong", "guangdong"), "广东产权交易中心");
  assert.equal(exchangeDisplayLabel("guangzhou", "guangzhou"), "广州产权交易所");

  // Unknown ids should fall back to whatever the backend sent (or the id itself).
  assert.equal(recordFamilyLabel("unknown_family", "Unknown Family"), "Unknown Family");
  assert.equal(businessTypeLabel("unknown_business", "Unknown Business"), "Unknown Business");
  assert.equal(exchangeDisplayLabel("unknown_exchange", "Unknown Exchange"), "Unknown Exchange");
});

test("record exchange options expose canonical listing-only exchanges", () => {
  const values = new Set(RECORD_EXCHANGES.map(([value]) => value));
  assert.equal(values.has("shandong"), true);
  assert.equal(values.has("guangdong"), true);
  assert.equal(values.has("shenzhen"), true);
  assert.equal(values.has("guangzhou"), false);
});
