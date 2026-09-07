#!/usr/bin/env python3
"""Remove Exp4 S1 UEs and app clients from edge."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scheme import NAMESPACE, SLICES  # noqa: E402


def undeploy_ues(namespace: str = NAMESPACE, context: str = "edge@edge") -> None:
    print(f"Undeploying Exp4 S1 UEs from {namespace} on {context}")
    subprocess.run(
        [
            "kubectl",
            f"--context={context}",
            "delete",
            "deployment,svc",
            "-n",
            namespace,
            "-l",
            "ina.lab/role=ue-client",
            "--grace-period=0",
            "--force",
            "--ignore-not-found=true",
        ],
        check=False,
    )
    for sid in SLICES:
        for kind, name in (
            ("deployment", f"oai-ue-slice-{sid}-client-1"),
            ("svc", f"oai-ue-slice-{sid}-client-1"),
            ("sa", f"oai-ue-{sid}-sa"),
            ("cm", f"oai-ue-{sid}-configmap"),
            ("network-attachment-definition", f"ue{sid}-sim-rf"),
            ("network-attachment-definition", f"ue{sid}-console-multus"),
        ):
            subprocess.run(
                [
                    "kubectl",
                    f"--context={context}",
                    "delete",
                    kind,
                    name,
                    "-n",
                    namespace,
                    "--ignore-not-found=true",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    print("UEs removed.")


def main() -> None:
    undeploy_ues()


if __name__ == "__main__":
    main()
