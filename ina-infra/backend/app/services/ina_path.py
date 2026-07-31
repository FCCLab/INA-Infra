"""Ensure algorithm/new_implementation is importable as ``ina``."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_ina_on_path() -> Path:
    """Add INA package parent to sys.path; return that directory."""
    candidates: list[Path] = []
    env = os.environ.get("INA_SRC")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/app/ina_src"))

    here = Path(__file__).resolve()
    # Prefer vendored copy under ina-infra, then sibling monorepo algorithm/.
    try:
        candidates.append(here.parents[3] / "algorithm" / "new_implementation")
    except IndexError:
        pass
    try:
        candidates.append(here.parents[3].parent / "algorithm" / "new_implementation")
    except IndexError:
        pass
    try:
        candidates.append(here.parents[4] / "algorithm" / "new_implementation")
    except IndexError:
        pass

    for path in candidates:
        if (path / "ina" / "__init__.py").is_file():
            sp = str(path.resolve())
            if sp not in sys.path:
                sys.path.insert(0, sp)
            return path
    raise RuntimeError(
        "Cannot find ina package. Set INA_SRC to algorithm/new_implementation "
        "(or place it at ina-infra/algorithm/new_implementation)."
    )
