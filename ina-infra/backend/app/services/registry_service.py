"""Docker registry discovery service for OAI and application container images."""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_HOST = "10.1.132.30:5000"

# Component mapping to registry repositories and fallback default tags
OAI_COMPONENT_REPOS: Dict[str, Dict[str, Any]] = {
    "cucp": {
        "repo": "oai-cucp",
        "name": "OAI CU-CP",
        "fallback_tag": "nws-v0.8.3-amd64",
    },
    "du": {
        "repo": "oai-du",
        "name": "OAI DU",
        "fallback_tag": "nws-v0.8.3-amd64",
    },
    "cuup": {
        "repo": "oai-nr-cuup",
        "name": "OAI CU-UP",
        "fallback_tag": "nws-v0.8.2-amd64",
    },
    "ue": {
        "repo": "oai-nr-ue",
        "name": "OAI UE Simulator",
        "fallback_tag": "nws-v0.8.2-amd64",
    },
    "flexric": {
        "repo": "oai-flexric",
        "name": "OAI FlexRIC",
        "fallback_tag": "nws-v0.8.2-amd64",
    },
    "xapp": {
        "repo": "nws-xapp",
        "name": "NWS xApp",
        "fallback_tag": "nws-v0.6-amd64",
    },
    "smf": {
        "repo": "oaisoftwarealliance/oai-smf",
        "name": "OAI SMF (Multi-slice)",
        "fallback_tag": "v2.2.1-dnn-fix-4",
    },
}

_CACHE: Dict[str, Any] = {"timestamp": 0, "data": None}
_CACHE_TTL_S = 30


def _parse_tag_version(tag: str) -> tuple:
    """Parse tag like 'nws-v0.8.3-amd64' or 'v2.2.1-dnn-fix-4' into sortable semantic version key."""
    clean = re.sub(r"-(amd64|arm64|plain)$", "", tag, flags=re.I)
    nums = tuple(int(n) for n in re.findall(r"\d+", clean))
    padded = nums + (0,) * (5 - len(nums))
    is_amd64 = 1 if "amd64" in tag.lower() else (0 if "arm64" in tag.lower() else 0.5)
    return (padded, is_amd64, len(tag))


def get_latest_tag_from_list(tags: List[str], fallback: str) -> str:
    """Sort tags and pick the newest release tag."""
    if not tags:
        return fallback

    # Filter out plain or debug tags if versioned ones exist
    versioned = [t for t in tags if any(c.isdigit() for c in t)]
    candidates = versioned if versioned else tags

    try:
        sorted_tags = sorted(candidates, key=_parse_tag_version, reverse=True)
        return sorted_tags[0]
    except Exception:
        return tags[0] if tags else fallback


def fetch_registry_tags(
    repo: str, host: str = DEFAULT_REGISTRY_HOST, timeout_s: float = 3.0
) -> List[str]:
    """Query registry tags for a given repository."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"https://{host}/v2/{repo}/tags/list"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "INA-Infra-Registry/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tags = data.get("tags") or []
            return [t for t in tags if isinstance(t, str)]
    except Exception as e:
        logger.debug(f"Registry query failed for {repo} at {host}: {e}")
        return []


def get_oai_registry_status(
    host: str = DEFAULT_REGISTRY_HOST, force_refresh: bool = False
) -> Dict[str, Any]:
    """Return all OAI image repositories, available tags, and resolved latest versions."""
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["timestamp"] < _CACHE_TTL_S):
        return _CACHE["data"]

    components: Dict[str, Any] = {}
    defaults: Dict[str, str] = {}
    is_live = False

    for key, spec in OAI_COMPONENT_REPOS.items():
        repo = spec["repo"]
        tags = fetch_registry_tags(repo, host=host)
        if tags:
            is_live = True
        latest_tag = get_latest_tag_from_list(tags, spec["fallback_tag"])
        full_image = f"{host}/{repo}:{latest_tag}"

        components[key] = {
            "key": key,
            "name": spec["name"],
            "repo": repo,
            "latest_tag": latest_tag,
            "latest_image": full_image,
            "available_tags": tags,
            "fallback_tag": spec["fallback_tag"],
        }
        defaults[key] = full_image

    result = {
        "registry_host": host,
        "connected": is_live,
        "components": components,
        "defaults": defaults,
        "queried_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }

    _CACHE["timestamp"] = now
    _CACHE["data"] = result
    return result


def resolve_oai_image(
    component: str,
    override: Optional[str] = None,
    host: str = DEFAULT_REGISTRY_HOST,
) -> str:
    """Resolve the image string for an OAI component (cucp, du, cuup, ue, flexric, xapp, smf)."""
    if override and override.strip() and override.strip() != "latest" and override.strip() != "auto":
        val = override.strip()
        # If user passed only a tag (e.g. "nws-v0.8.3-amd64"), prepend registry repo
        if ":" not in val and "/" not in val:
            spec = OAI_COMPONENT_REPOS.get(component)
            if spec:
                return f"{host}/{spec['repo']}:{val}"
        return val

    # Resolve dynamically from registry or fallback
    status = get_oai_registry_status(host=host)
    comp_info = status.get("components", {}).get(component)
    if comp_info and comp_info.get("latest_image"):
        return comp_info["latest_image"]

    spec = OAI_COMPONENT_REPOS.get(component)
    if spec:
        return f"{host}/{spec['repo']}:{spec['fallback_tag']}"
    return f"{host}/oai-{component}:latest"
