"""Persist PlanningLayer solve input/output as JSON under backend/results/."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.schemas import NetworkIn, PlSolveRequest, PlSolveResponse, Profile


def _results_root() -> Path:
    env = os.environ.get("INA_PL_RESULTS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # .../ina-infra/backend/app/services → .../ina-infra/backend/results
    return Path(__file__).resolve().parents[2] / "results"


def results_dir() -> Path:
    path = _results_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "unnamed").strip()) or "unnamed"
    return cleaned[:80]


def write_pl_run(
    req: PlSolveRequest,
    resp: PlSolveResponse,
    *,
    profile_name: Optional[str] = None,
) -> Path:
    """Write one PL run JSON; also refresh ``<profile>_latest.json``.

    Returns path to the timestamped file.
    """
    root = results_dir()
    pname = profile_name or (req.profile.name if req.profile else None) or "anonymous"
    safe = _safe_name(pname)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = root / f"{safe}_{stamp}.json"

    network = req.network
    if network is None:
        from app.services import pl_solver

        network = pl_solver.default_network_in()

    payload: dict[str, Any] = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "profile_name": pname,
        "input": {
            "profile": (req.profile or Profile()).model_dump(exclude_none=True),
            "slices": [s.model_dump() for s in req.slices],
            "network": network.model_dump(exclude_none=True)
            if isinstance(network, NetworkIn)
            else network,
        },
        "output": resp.model_dump(exclude_none=True),
    }

    text = json.dumps(payload, indent=2, sort_keys=False)
    out_path.write_text(text, encoding="utf-8")
    latest = root / f"{safe}_latest.json"
    latest.write_text(text, encoding="utf-8")
    return out_path
