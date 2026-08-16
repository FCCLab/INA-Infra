/** Legacy lab hostnames → current Kubernetes node names. */

const ALIASES: Record<string, string> = {
  "central-0": "cpu-central-0",
  "central-1": "cpu-central-1",
  "regional-0": "cpu-regional-0",
  "regional-1": "cpu-regional-1",
  "edge-0": "cpu-edge-0",
  "edge-1": "cpu-edge-1",
  "edge-3": "gpu-a40",
  gh81: "gpu-gh81",
  gh82: "gpu-gh82",
};

export function canonicalNodeName(name: string): string {
  const n = (name || "").trim();
  return ALIASES[n] || n;
}

export function nodeNameAliases(name: string): Set<string> {
  const c = canonicalNodeName(name);
  const out = new Set<string>();
  if (name) out.add(name);
  if (c) out.add(c);
  for (const [old, neu] of Object.entries(ALIASES)) {
    if (neu === c) out.add(old);
  }
  return out;
}

export function findByNodeName<T extends { name: string }>(
  items: T[] | undefined,
  wanted: string,
): T | undefined {
  const aliases = nodeNameAliases(wanted);
  return (items || []).find(
    (n) => aliases.has(n.name) || aliases.has(canonicalNodeName(n.name)),
  );
}
