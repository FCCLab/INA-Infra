"""Remove Exp4 UEs for whichever scheme is on sys.path."""

from __future__ import annotations

import subprocess

import scheme


def undeploy_ues(namespace: str | None = None, context: str = "edge@edge") -> None:
    ns = namespace or scheme.NAMESPACE
    print(f"Undeploying {scheme.SCHEME_ID} UEs from {ns} on {context}")
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
    for sid in scheme.SLICES:
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
                    ns,
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
