export function toText(value: unknown, fallback = ""): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

export function toPositiveNumber(value: unknown, fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}
