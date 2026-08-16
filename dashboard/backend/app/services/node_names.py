"""Map legacy lab hostnames to current Kubernetes node names.

Prometheus may still expose old `node` / DCGM Hostname values
(`regional-1`, `gh82`, …) after a node rename.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

# Old SSH / OS names → current kube node names.
_ALIASES: Dict[str, str] = {
    "central-0": "cpu-central-0",
    "central-1": "cpu-central-1",
    "regional-0": "cpu-regional-0",
    "regional-1": "cpu-regional-1",
    "edge-0": "cpu-edge-0",
    "edge-1": "cpu-edge-1",
    "edge-3": "gpu-a40",
    "gh81": "gpu-gh81",
    "gh82": "gpu-gh82",
}


def canonical_node_name(name: Optional[str]) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    return _ALIASES.get(n, n)


def node_label_values(name: str) -> List[str]:
    """All label values that may appear on series for this node."""
    c = canonical_node_name(name)
    out: List[str] = []
    for cand in (name, c):
        cand = (cand or "").strip()
        if cand and cand not in out:
            out.append(cand)
    for old, new in _ALIASES.items():
        if new == c and old not in out:
            out.append(old)
    return out


def prom_node_regex(name: str) -> str:
    """PromQL-safe alternation for node= / Hostname= matchers.

    Do not use Python re.escape: Prometheus RE2 treats ``\\-`` as invalid.
    Kubernetes node names are already ``[a-z0-9.-]``.
    """
    vals = node_label_values(name)
    cleaned = []
    for v in vals:
        if re.fullmatch(r"[A-Za-z0-9._-]+", v):
            cleaned.append(v.replace(".", r"\."))
    return "|".join(cleaned) or re.escape(canonical_node_name(name) or name)


def pick_matching(
    wanted: str,
    available: Iterable[str],
) -> Optional[str]:
    """Return the available name that corresponds to wanted (alias-aware)."""
    aliases = set(node_label_values(wanted))
    for n in available:
        if n in aliases or canonical_node_name(n) in aliases:
            return n
    return None
