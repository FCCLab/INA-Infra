/** Safe numeric formatting — never returns the string "NaN". */

/** Coerce API values to a finite number, or null (never NaN). */
export function toFinite(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function isFiniteNumber(v: unknown): boolean {
  return toFinite(v) !== null;
}

export function fmtNum(v: unknown, digits = 2, fallback = "—"): string {
  const n = toFinite(v);
  if (n === null) return fallback;
  return n.toFixed(digits);
}

export function finiteOrNull(v: unknown): number | null {
  return toFinite(v);
}

export function finiteOrZero(v: unknown): number {
  return toFinite(v) ?? 0;
}

/** Chart.js tick labels — blank instead of "NaN". */
export function fmtTick(v: unknown, digits = 2): string {
  const n = toFinite(v);
  if (n === null) return "";
  return Number.isInteger(n) ? String(n) : n.toFixed(digits);
}
