"""Exp4 Scheme 2 (S2): +PL +PM — five DL slices."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from pl_slices import (  # noqa: E402
    CLUSTERS,
    FABRIC,
    KUBE_CONTEXT,
    PART_OF,
    PL_SLICES,
    REGISTRY,
    REPO_FOR,
    TIER_ID,
    TIER_NAME,
    site_label,
)

SCHEME_ID = "exp4-s2"
SCHEME_NAME = "S2 +PL +PM"
NAMESPACE = "exp4-s2"
SLICES = {sid: dict(row) for sid, row in PL_SLICES.items()}

PL_ENABLED = True
PM_ENABLED = True
PS_ENABLED = False
XAPP_REPLICAS = 0
