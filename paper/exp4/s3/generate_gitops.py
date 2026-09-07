#!/usr/bin/env python3
"""Render this Exp4 scheme's GitOps manifests."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))

from cli import main_generate  # noqa: E402

if __name__ == "__main__":
    main_generate()
