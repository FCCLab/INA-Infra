#!/usr/bin/env python3
"""Force-delete a stuck Terminating namespace on central/regional/edge.

Strips OAI/HNC finalizers, then clears Namespace spec.finalizers via the
finalize API. Config Sync will recreate the ns unless Gitea no longer has it.

  python3 paper/exp4/common/force_delete_ns.py
  python3 paper/exp4/common/force_delete_ns.py exp4-s3
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from k8s_cleanup import (  # noqa: E402
    hnc_fail_open,
    kubectl,
    strip_ns_finalizers,
    strip_resource_finalizers,
    wait_ns_gone,
)

CONTEXTS = ("central@central", "regional@regional", "edge@edge")


def _print_ns(ctx: str, ns: str) -> str:
    p = kubectl(ctx, ["get", "ns", ns, "-o", "json"], timeout=15)
    if p.returncode != 0:
        print(f"{ctx}: {ns} gone")
        return "gone"
    d = json.loads(p.stdout)
    phase = (d.get("status") or {}).get("phase")
    spec_f = (d.get("spec") or {}).get("finalizers")
    meta_f = (d.get("metadata") or {}).get("finalizers")
    print(f"{ctx}: phase={phase} spec.finalizers={spec_f} meta.finalizers={meta_f}")
    for c in (d.get("status") or {}).get("conditions") or []:
        msg = (c.get("message") or "")[:200]
        if msg:
            print(f"  {c.get('type')} {c.get('reason')}: {msg}")
    return phase or "?"


def force(ns: str) -> int:
    bad = 0
    for ctx in CONTEXTS:
        print(f"\n======== {ctx} ========")
        hnc_fail_open(ctx)
        phase = _print_ns(ctx, ns)
        if phase == "gone":
            continue
        strip_resource_finalizers(ctx, ns)
        kubectl(
            ctx,
            ["delete", "ns", ns, "--wait=false", "--ignore-not-found=true", "--grace-period=0"],
            timeout=20,
        )
        strip_ns_finalizers(ctx, ns)
        wait_ns_gone(ctx, ns, timeout_s=90)
        phase = _print_ns(ctx, ns)
        if phase not in ("gone",):
            print(f"{ctx}: STILL {phase}")
            bad += 1
    return 1 if bad else 0


def main() -> int:
    ns = (sys.argv[1] if len(sys.argv) > 1 else "exp4-s3").strip()
    print(f"force-delete ns {ns}")
    return force(ns)


if __name__ == "__main__":
    raise SystemExit(main())
