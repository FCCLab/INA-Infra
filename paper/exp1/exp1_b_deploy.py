#!/usr/bin/env python3
"""Deploy Scheme B: Fixed Edge Baseline to namespace exp1-b.

Placement:
  - All Slices (1, 2, 3, 4) CU-UP, UPF, and Applications deployed strictly on Edge (`edge@edge`).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_exp1_gitops import deploy_scheme, render_scheme_manifests

def main():
    scheme_id = "exp1-b"
    print("================================================================")
    print(" Deploying Scheme B (Fixed Edge Baseline) into Namespace: exp1-b")
    print("================================================================")
    render_scheme_manifests(scheme_id)
    deploy_scheme(scheme_id)
    print("\nScheme B (Fixed Edge) deployed to namespace [exp1-b] successfully.")

if __name__ == "__main__":
    main()
