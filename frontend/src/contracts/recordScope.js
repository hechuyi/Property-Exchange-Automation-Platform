const EXCHANGE_CODE_ALIASES = {
  all: "all",
  sse: "sse",
  shanghai: "sse",
  "上海联合产权交易所": "sse",
  "上交所": "sse",
  cbex: "cbex",
  beijing: "cbex",
  "北京产权交易所": "cbex",
  "北交所": "cbex",
  "北交互联": "cbex",
  tpre: "tpre",
  tianjin: "tpre",
  "天津产权交易中心": "tpre",
  "天交所": "tpre",
  cquae: "cquae",
  chongqing: "cquae",
  "重庆联交所": "cquae",
  "重交所": "cquae",
  guangdong: "guangdong",
  guangzhou: "guangdong",
  "广东联合产权交易中心": "guangdong",
  "广东产权": "guangdong",
  "广东产权交易中心": "guangdong",
  "广州产权交易所": "guangdong",
  "广交所": "guangdong",
};

function asText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function asPositiveInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeExchange(value) {
  const raw = asText(value, "all");
  return EXCHANGE_CODE_ALIASES[raw.toLowerCase()] || raw;
}

export function normalizeRecordScope(scope = {}) {
  const source = scope && typeof scope === "object" ? scope : {};
  const recordFamily = asText(source.record_family).toLowerCase();
  return {
    record_family: recordFamily,
    state: asText(source.state, "all"),
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    exchange: asText(source.exchange) ? normalizeExchange(source.exchange) : "",
    keyword: asText(source.keyword),
    date_from: asText(source.date_from),
    date_to: asText(source.date_to),
    page: asPositiveInt(source.page, 1),
    page_size: asPositiveInt(source.page_size, 50),
  };
}

function assertExportScope(scope = {}) {
  const normalized = normalizeRecordScope(scope);
  if (!normalized.record_family) {
    throw new Error("record scope is not exportable");
  }
  return {
    ...normalized,
    business_id: normalized.business_id || "all",
    exchange: normalized.exchange || "all",
  };
}

export function buildRecordScopeQuery(scope = {}) {
  const normalized = normalizeRecordScope(scope);
  const params = new URLSearchParams();
  Object.entries(normalized).forEach(([key, value]) => {
    params.set(key, String(value));
  });
  return params.toString();
}

export function buildExportRequest(scope = {}, options = {}) {
  const normalized = assertExportScope(scope);
  if (Object.hasOwn(options, "export_mode")) {
    throw new Error("export_mode is not supported; use requested_export_mode");
  }
  const requestedExportMode = asText(options.requested_export_mode, "full").toLowerCase() || "full";
  const payload = {
    record_family: normalized.record_family,
    state: normalized.state,
    business_id: normalized.business_id,
    exchange: normalized.exchange,
    keyword: normalized.keyword,
    date_from: normalized.date_from,
    date_to: normalized.date_to,
    requested_export_mode: requestedExportMode,
  };
  if (normalized.business_label) {
    payload.business_label = normalized.business_label;
  }
  if (options.output_dir != null && String(options.output_dir).trim()) {
    payload.output_dir = String(options.output_dir).trim();
  }
  return payload;
}
