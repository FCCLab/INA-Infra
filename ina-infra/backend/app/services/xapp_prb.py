"""Near-RT RIC nws-xapp client — NS PRB policy GET/PATCH/PUT.

Lab xApp (oai-slice-deployment): http://10.1.132.230:18080
  GET/PUT/PATCH /api/v1/slices  → dedicated / min / max ratios via E2.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def xapp_base_url() -> str:
    # oai-benchmark near-RT RIC xApp (port 18081); override with INA_XAPP_URL.
    return env("INA_XAPP_URL", "http://10.1.132.230:18081").rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    body: Optional[dict] = None,
    timeout: float = 15.0,
) -> Any:
    url = f"{xapp_base_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"xApp {method} {path} HTTP {exc.code}: {err}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"xApp {method} {path} failed: {exc}") from exc


def get_slices() -> Dict[str, Any]:
    return _request("GET", "/api/v1/slices")


def put_slices(slices: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _request("PUT", "/api/v1/slices", body={"slices": slices})


def patch_slice(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one slice into current NS policy then SET via E2."""
    return _request("PATCH", "/api/v1/slices", body=entry)


def normalize_sd(sd: str | int) -> str:
    if isinstance(sd, int):
        return f"0x{sd:06x}"
    s = str(sd).strip().lower()
    if s.startswith("0x"):
        return f"0x{int(s, 16):06x}"
    return f"0x{int(s):06x}"


def find_slice(
    payload: Dict[str, Any],
    *,
    sst: int,
    sd: str,
    direction: str,
) -> Optional[Dict[str, Any]]:
    want = normalize_sd(sd)
    direction = direction.lower()
    for row in payload.get("slices") or []:
        if int(row.get("sst") or 0) != int(sst):
            continue
        if normalize_sd(row.get("sd") or 0) != want:
            continue
        if str(row.get("direction") or "").lower() != direction:
            continue
        return row
    return None


def set_prb(
    *,
    sst: int,
    sd: str,
    direction: str,
    dedicated: float,
    min_prb: float,
    max_prb: float,
) -> Dict[str, Any]:
    if not (0.0 <= dedicated <= min_prb <= max_prb <= 100.0):
        raise ValueError(
            f"require 0 ≤ dedicated ≤ min ≤ max ≤ 100 "
            f"(got {dedicated}/{min_prb}/{max_prb})"
        )
    entry = {
        "sst": int(sst),
        "sd": normalize_sd(sd),
        "direction": direction.lower(),
        "dedicated": float(dedicated),
        "min": float(min_prb),
        "max": float(max_prb),
    }
    return patch_slice(entry)
