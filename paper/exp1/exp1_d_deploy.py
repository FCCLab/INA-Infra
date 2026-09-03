#!/usr/bin/env python3
"""Deploy Scheme D: Fixed Central Baseline to namespace exp1-d.

Placement:
  - All Slices (1, 2, 3, 4) CU-UP, UPF, and Applications deployed strictly on Central (`central@central`).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_exp1_gitops import deploy_scheme, render_scheme_manifests

def main():
    scheme_id = "exp1-d"
    print("================================================================")
    print(" Deploying Scheme D (Fixed Central Baseline) into Namespace: exp1-d")
    print("================================================================")
    render_scheme_manifests(scheme_id)
    deploy_scheme(scheme_id)
    print("\nScheme D (Fixed Central) deployed to namespace [exp1-d] successfully.")

if __name__ == "__main__":
    main()
