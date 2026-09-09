"""Remove Exp4 UEs for whichever scheme is on sys.path."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

import scheme

_UE_TOKEN = re.compile(
    r"^(?:oai-ue-slice-|oai-ue-|ue-slice-|slice-|ue|s)?(\d+)(?:-client-1)?$",
    re.IGNORECASE,
)


def parse_ue_ids(values: list[str] | None, *, default_all: bool = True) -> list[int]:
    """Parse UE/slice ids: ``5``, ``s5``, ``ue5``, ``oai-ue-5``, ``1,2,4``."""
    valid = sorted(scheme.SLICES)
    if not values:
        return valid if default_all else []
    ids: list[int] = []
    for raw in values:
        for part in re.split(r"[,\s]+", str(raw).strip()):
            if not part:
                continue
            m = _UE_TOKEN.fullmatch(part)
            if not m:
                sys.exit(f"cannot parse UE {part!r}; expected one of {valid}")
            sid = int(m.group(1))
            if sid not in scheme.SLICES:
                sys.exit(f"unknown UE/slice {sid}; valid: {valid}")
            ids.append(sid)
    return sorted(set(ids))


def undeploy_ues(
    namespace: str | None = None,
    context: str = "edge@edge",
    sids: list[int] | None = None,
) -> None:
    ns = namespace or scheme.NAMESPACE
    targets = list(sids) if sids is not None else sorted(scheme.SLICES)
    all_ues = targets == sorted(scheme.SLICES)
    print(
        f"Undeploying {scheme.SCHEME_ID} UE(s) {','.join(str(s) for s in targets)} "
        f"from {ns} on {context}"
    )
    if all_ues:
        subprocess.run(
            [
                "kubectl",
                f"--context={context}",
                "delete",
                "deployment,svc",
                "-n",
                ns,
                "-l",
                "ina.lab/role=ue-client",
                "--grace-period=0",
                "--force",
                "--ignore-not-found=true",
            ],
            check=False,
        )
    for sid in targets:
        for kind, name in (
            ("deployment", f"oai-ue-slice-{sid}-client-1"),
            ("svc", f"oai-ue-slice-{sid}-client-1"),
            ("sa", f"oai-ue-{sid}-sa"),
            ("cm", f"oai-ue-{sid}-configmap"),
            ("network-attachment-definition", f"ue{sid}-sim-rf"),
            ("network-attachment-definition", f"ue{sid}-console-multus"),
        ):
            cmd = [
                "kubectl",
                f"--context={context}",
                "delete",
                kind,
                name,
                "-n",
                ns,
                "--ignore-not-found=true",
            ]
            if kind == "deployment":
                cmd.extend(["--grace-period=0", "--force"])
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    print("UEs removed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Undeploy {scheme.SCHEME_ID} UEs")
    parser.add_argument(
        "ues",
        nargs="*",
        help="UE/slice ids (default: all). Examples: 5  1,2,4  s3  oai-ue-1",
    )
    parser.add_argument(
        "--ue",
        action="append",
        default=[],
        metavar="ID",
        help="UE/slice id (repeatable or comma-separated)",
    )
    args = parser.parse_args()
    undeploy_ues(sids=parse_ue_ids(list(args.ues) + list(args.ue)))


if __name__ == "__main__":
    main()
