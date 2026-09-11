"""kubectl helpers for Exp4 undeploy when HNC webhook is down (fail-closed)."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Sequence


def kubectl(ctx: str, args: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", f"--context={ctx}", f"--request-timeout={int(timeout)}s", *args],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )


def hnc_fail_open(ctx: str) -> None:
    """If Hierarchical Namespace Controller webhook is down, Fail=Ignore so deletes proceed."""
    for kind in ("validatingwebhookconfiguration", "mutatingwebhookconfiguration"):
        res = kubectl(ctx, ["get", kind, "-o", "json"], timeout=20)
        if res.returncode != 0 or not (res.stdout or "").strip():
            continue
        try:
            items = json.loads(res.stdout).get("items") or []
        except json.JSONDecodeError:
            continue
        for item in items:
            name = (item.get("metadata") or {}).get("name") or ""
            if "hnc" not in name.lower():
                continue
            hooks = item.get("webhooks") or []
            patch = []
            for i, wh in enumerate(hooks):
                if (wh or {}).get("failurePolicy") == "Ignore":
                    continue
                patch.append(
                    {"op": "replace", "path": f"/webhooks/{i}/failurePolicy", "value": "Ignore"}
                )
            if not patch:
                continue
            p = kubectl(
                ctx,
                ["patch", kind, name, "--type=json", "-p", json.dumps(patch)],
                timeout=20,
            )
            if p.returncode == 0:
                print(f"  {ctx}: {kind}/{name} failurePolicy=Ignore")
            else:
                err = (p.stderr or p.stdout or "").strip()
                print(f"  {ctx}: warn patch {kind}/{name}: {err}")


_STUCK_KINDS = (
    "nfdeployment",
    "nfconfig",
    "hierarchyconfiguration",
    "deployment",
    "statefulset",
    "daemonset",
    "pod",
    "pvc",
    "job",
    "networkattachmentdefinition",
)


def strip_resource_finalizers(ctx: str, ns: str) -> None:
    """OAI NFDeployment/NFConfig (and HNC) finalizers block ns deletion."""
    for kind in _STUCK_KINDS:
        listed = kubectl(ctx, ["-n", ns, "get", kind, "-o", "name"], timeout=25)
        for line in (listed.stdout or "").splitlines():
            obj = line.strip()
            if not obj:
                continue
            kubectl(
                ctx,
                ["-n", ns, "patch", obj, "--type", "merge", "-p", '{"metadata":{"finalizers":[]}}'],
                timeout=25,
            )
            kubectl(ctx, ["-n", ns, "delete", obj, "--wait=false", "--ignore-not-found=true"], timeout=25)


def strip_ns_finalizers(ctx: str, ns: str) -> None:
    res = kubectl(ctx, ["get", "ns", ns, "-o", "json"], timeout=20)
    if res.returncode != 0 or not (res.stdout or "").strip():
        return
    try:
        doc = json.loads(res.stdout)
    except json.JSONDecodeError:
        return
    meta_f = (doc.get("metadata") or {}).get("finalizers") or []
    spec_f = (doc.get("spec") or {}).get("finalizers") or []
    if meta_f:
        kubectl(
            ctx,
            ["patch", "ns", ns, "--type=merge", "-p", '{"metadata":{"finalizers":[]}}'],
            timeout=20,
        )
    if spec_f:
        doc["spec"]["finalizers"] = []
        if "status" in doc:
            del doc["status"]
        proc = subprocess.run(
            [
                "kubectl",
                f"--context={ctx}",
                "--request-timeout=20s",
                "replace",
                "--raw",
                f"/api/v1/namespaces/{ns}/finalize",
                "-f",
                "-",
            ],
            input=json.dumps(doc),
            capture_output=True,
            text=True,
            timeout=25,
        )
        if proc.returncode != 0:
            kubectl(
                ctx,
                ["patch", "ns", ns, "--type=merge", "-p", '{"spec":{"finalizers":[]}}'],
                timeout=20,
            )


def wait_ns_gone(ctx: str, ns: str, timeout_s: float = 180.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = kubectl(ctx, ["get", "ns", ns, "-o", "jsonpath={.status.phase}"], timeout=20)
        out = (res.stdout or "").strip()
        if res.returncode != 0 or not out:
            print(f"  {ctx}: ns {ns} gone")
            return
        if out == "Active":
            print(f"  {ctx}: ns {ns} Active again (Config Sync recreating?); deleting")
            kubectl(
                ctx,
                ["delete", "ns", ns, "--wait=false", "--ignore-not-found=true", "--grace-period=0"],
                timeout=25,
            )
        strip_resource_finalizers(ctx, ns)
        strip_ns_finalizers(ctx, ns)
        time.sleep(3)
    print(f"  warn: {ctx} ns {ns} still present after {timeout_s:.0f}s")
