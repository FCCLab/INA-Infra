#!/usr/bin/env python3
"""Deploy Scheme A: Full Algorithm (Proposed PL) to namespace exp1-a.

Placement:
  - Slice 1 (CCTV): Regional (`gpu-gh82`)
  - Slice 2 (Physical-AI): Edge (`gpu-a40` / `usrp`)
  - Slice 3 (OTT 4K): Central (`gpu-gh81` / 5GC)
  - Slice 4 (IoT): Central (`gpu-gh81` / 5GC)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_exp1_gitops import deploy_scheme, render_scheme_manifests

def main():
    scheme_id = "exp1-a"
    print("================================================================")
    print(" Deploying Scheme A (Proposed PL) into Namespace: exp1-a")
    print("================================================================")
    render_scheme_manifests(scheme_id)
    deploy_scheme(scheme_id)
    print("\nScheme A (Proposed PL) deployed to namespace [exp1-a] successfully.")

if __name__ == "__main__":
    main()
