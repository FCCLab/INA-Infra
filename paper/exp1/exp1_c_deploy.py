#!/usr/bin/env python3
"""Deploy Scheme C: Fixed Regional Baseline to namespace exp1-c.

Placement:
  - All Slices (1, 2, 3, 4) CU-UP, UPF, and Applications deployed strictly on Regional (`regional@regional`).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_exp1_gitops import deploy_scheme, render_scheme_manifests

def main():
    scheme_id = "exp1-c"
    print("================================================================")
    print(" Deploying Scheme C (Fixed Regional Baseline) into Namespace: exp1-c")
    print("================================================================")
    render_scheme_manifests(scheme_id)
    deploy_scheme(scheme_id)
    print("\nScheme C (Fixed Regional) deployed to namespace [exp1-c] successfully.")

if __name__ == "__main__":
    main()
