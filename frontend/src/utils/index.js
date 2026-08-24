/**
 * DOM and formatting utility functions.
 *
 * Pure helpers with no side-effects — safe to import anywhere.
 */

export const $ = (sel, ctx = document) => ctx.querySelector(sel);
export const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

export const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

export const text = (v) => {
  if (v == null) return "";
  return String(v);
};

export const escapeHtml = (v) =>
  text(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");

export const display = (v) => {
  const normalized = text(v).trim();
  return normalized ? normalized : "—";
};

// ── Time formatting ──

export function parseTs(ts) {
  if (!ts || typeof ts !== "string") return null;
  const d = new Date(ts.replace(" ", "T"));
  return isNaN(d.getTime()) ? null : d.getTime();
}

export function formatTimeAgo(ts) {
  const ms = parseTs(ts);
  if (!ms) return "";
  const diff = Date.now() - ms;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} 天前`;
  return new Date(ms).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function formatJobTime(ts) {
  const ms = parseTs(ts);
  if (!ms) return "";
  return new Date(ms).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
