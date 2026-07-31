"""Persist editable cluster positions and viewport for the topology canvas."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.services import clusters as cluster_svc

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "data"
_LAYOUT_FILE = Path(os.environ.get("DASHBOARD_TOPOLOGY_LAYOUT", str(_DEFAULT_DIR / "topology_layout.json")))


def layout_path() -> Path:
    return _LAYOUT_FILE


def _read_raw() -> Dict[str, Any]:
    path = layout_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_clusters(raw_clusters: Any) -> Dict[str, Dict[str, float]]:
    if not isinstance(raw_clusters, dict):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    known = set(cluster_svc.list_clusters())
    for name, pos in raw_clusters.items():
        if name not in known or not isinstance(pos, dict):
            continue
        try:
            out[str(name)] = {"x": float(pos["x"]), "y": float(pos["y"])}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _parse_viewport(raw_viewport: Any) -> Optional[Dict[str, float]]:
    if not isinstance(raw_viewport, dict):
        return None
    try:
        return {
            "x": float(raw_viewport["x"]),
            "y": float(raw_viewport["y"]),
            "zoom": float(raw_viewport["zoom"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def load_layout() -> Dict[str, Dict[str, float]]:
    return _parse_clusters(_read_raw().get("clusters"))


def load_viewport() -> Optional[Dict[str, float]]:
    return _parse_viewport(_read_raw().get("viewport"))


def load_full() -> Dict[str, Any]:
    raw = _read_raw()
    return {
        "clusters": _parse_clusters(raw.get("clusters")),
        "viewport": _parse_viewport(raw.get("viewport")),
    }


def save_layout(
    clusters: Optional[Dict[str, Any]] = None,
    viewport: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Save clusters and/or viewport, merging with any existing file contents."""
    existing = _read_raw()
    known = set(cluster_svc.list_clusters())

    if clusters is not None:
        cleaned: Dict[str, Dict[str, float]] = {}
        for name, pos in (clusters or {}).items():
            if name not in known or not isinstance(pos, dict):
                continue
            try:
                cleaned[str(name)] = {"x": float(pos["x"]), "y": float(pos["y"])}
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid position for {name}: {exc}") from exc
        existing["clusters"] = cleaned
    elif "clusters" not in existing:
        existing["clusters"] = {}

    if viewport is not None:
        try:
            existing["viewport"] = {
                "x": float(viewport["x"]),
                "y": float(viewport["y"]),
                "zoom": float(viewport["zoom"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid viewport: {exc}") from exc

    path = layout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return {
        "clusters": _parse_clusters(existing.get("clusters")),
        "viewport": _parse_viewport(existing.get("viewport")),
    }
