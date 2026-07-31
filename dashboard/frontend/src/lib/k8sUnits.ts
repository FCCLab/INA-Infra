function finiteOrZero(n: number): number {
  return Number.isFinite(n) ? n : 0;
}

/** Parse Kubernetes CPU quantity (cores / millicores) to cores. */
export function parseCpuCores(raw?: string | null): number {
  if (!raw) return 0;
  const s = String(raw).trim();
  if (!s) return 0;
  if (s.endsWith("m")) return finiteOrZero(parseFloat(s.slice(0, -1)) / 1000);
  return finiteOrZero(parseFloat(s));
}

/** Parse Kubernetes memory quantity to bytes. */
export function parseMemBytes(raw?: string | null): number {
  if (!raw) return 0;
  const s = String(raw).trim();
  if (!s) return 0;
  const m = s.match(/^([0-9.]+)\s*([KMGTPEi]*)b?$/i);
  if (!m) return finiteOrZero(parseFloat(s));
  const n = parseFloat(m[1]);
  if (!Number.isFinite(n)) return 0;
  const u = (m[2] || "").toUpperCase();
  const mult: Record<string, number> = {
    "": 1,
    K: 1000,
    M: 1000 ** 2,
    G: 1000 ** 3,
    T: 1000 ** 4,
    KI: 1024,
    MI: 1024 ** 2,
    GI: 1024 ** 3,
    TI: 1024 ** 4,
    PI: 1024 ** 5,
    EI: 1024 ** 6,
  };
  return finiteOrZero(n * (mult[u] ?? 1));
}

export function bytesToGi(n?: number): number {
  if (!n || !Number.isFinite(n)) return 0;
  return Math.round((n / 1024 ** 3) * 100) / 100;
}
