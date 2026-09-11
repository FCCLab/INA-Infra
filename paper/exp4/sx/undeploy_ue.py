#!/usr/bin/env python3
"""Remove this scheme's UEs from edge."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))

from ue_teardown import main  # noqa: E402

if __name__ == "__main__":
    main()
