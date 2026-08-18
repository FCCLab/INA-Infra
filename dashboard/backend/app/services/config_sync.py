"""Config Sync RootSync status for mgmt / central / regional / edge."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException

from app.services.k8s_client import (
    api_exc_message,
    custom_objects,
    with_timeout,
)

_CONFIGSYNC_NS = "config-management-system"
_GROUP = "configsync.gke.io"
_VERSION = "v1beta1"
_PLURAL = "rootsyncs"


def rootsync_name(cluster: str) -> str:
    if cluster == "mgmt":
        return "mgmt"
    return f"{cluster}-repo"


def stub_status(
    cluster: str,
    *,
    overall: str = "unknown",
    summary: str = "",
    error: Optional[str] = None,
    exists: bool = False,
) -> Dict[str, Any]:
    name = rootsync_name(cluster)
    return {
        "name": name,
        "namespace": _CONFIGSYNC_NS,
        "exists": exists,
        "overall": overall,
        "summary": summary or overall.replace("_", " ").title(),
        "source_commit": "",
        "render_commit": "",
        "sync_commit": "",
        "last_synced_commit": "",
        "syncing": False,
        "stalled": False,
        "reconciling": False,
        "error_count": 0,
        "message": "",
        "updated_at": None,
        "error": error,
        "repo": "",
        "branch": "",
    }


def _short_commit(sha: Optional[str]) -> str:
    if not sha:
        return ""
    return str(sha)[:7]


def _cond_status(
    conditions: List[dict], ctype: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    for c in conditions or []:
        if c.get("type") == ctype:
            return c.get("status"), c.get("reason"), c.get("message")
    return None, None, None


def _phase_err_count(phase: Optional[dict]) -> int:
    if not phase:
        return 0
    summary = phase.get("errorSummary") or {}
    try:
        return int(summary.get("totalCount") or 0)
    except (TypeError, ValueError):
        return 0


def parse_rootsync(cluster: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Interpret a RootSync object into the dashboard status shape."""
    name = rootsync_name(cluster)
    spec_git = ((raw.get("spec") or {}).get("git")) or {}
    st = raw.get("status") or {}
    conditions = st.get("conditions") or []
    source = st.get("source") or {}
    rendering = st.get("rendering") or {}
    sync = st.get("sync") or {}

    source_commit = source.get("commit") or ""
    render_commit = rendering.get("commit") or ""
    sync_commit = sync.get("commit") or ""
    last_synced = st.get("lastSyncedCommit") or ""

    syncing_s, _, syncing_msg = _cond_status(conditions, "Syncing")
    stalled_s, _, stalled_msg = _cond_status(conditions, "Stalled")
    reconciling_s, _, _ = _cond_status(conditions, "Reconciling")
    syncing = syncing_s == "True"
    stalled = stalled_s == "True"
    reconciling = reconciling_s == "True"

    err_count = (
        _phase_err_count(source) + _phase_err_count(rendering) + _phase_err_count(sync)
    )
    updated = (
        sync.get("lastUpdate") or rendering.get("lastUpdate") or source.get("lastUpdate")
    )
    message = (syncing_msg or stalled_msg or "").strip()

    short_src = _short_commit(source_commit)
    short_applied = _short_commit(last_synced or sync_commit)
    commits_aligned = bool(
        source_commit and source_commit == sync_commit and source_commit == last_synced
    )

    if stalled or err_count > 0:
        overall = "error"
        summary = "Stalled" if stalled and err_count == 0 else "Errors"
    elif syncing:
        overall = "syncing"
        if short_src and short_applied and short_src != short_applied:
            summary = f"Syncing {short_applied}→{short_src}"
        elif commits_aligned and short_src:
            summary = f"Applying {short_src}"
        else:
            summary = f"Syncing {short_src}" if short_src else "Syncing"
    elif commits_aligned and not syncing:
        overall = "synced"
        summary = f"Synced {short_src}" if short_src else "Synced"
    elif last_synced and err_count == 0 and not syncing:
        overall = "synced"
        summary = f"Synced {_short_commit(last_synced)}"
    elif reconciling:
        overall = "syncing"
        summary = "Reconciling"
    elif source_commit or sync_commit:
        overall = "syncing"
        summary = "Settling"
    else:
        overall = "unknown"
        summary = "Unknown"

    return {
        "name": (raw.get("metadata") or {}).get("name") or name,
        "namespace": _CONFIGSYNC_NS,
        "exists": True,
        "overall": overall,
        "summary": summary,
        "source_commit": short_src,
        "render_commit": _short_commit(render_commit),
        "sync_commit": _short_commit(sync_commit),
        "last_synced_commit": _short_commit(last_synced),
        "syncing": syncing,
        "stalled": stalled,
        "reconciling": reconciling,
        "error_count": err_count,
        "message": message[:160] if message else "",
        "updated_at": updated,
        "error": None,
        "repo": spec_git.get("repo") or "",
        "branch": spec_git.get("branch") or "",
    }


def fetch_config_sync(api: Any, cluster: str) -> Dict[str, Any]:
    """GET RootSync on an already-open ApiClient. Never raises."""
    name = rootsync_name(cluster)
    try:
        co = custom_objects(api)
        raw = co.get_namespaced_custom_object(
            _GROUP,
            _VERSION,
            _CONFIGSYNC_NS,
            _PLURAL,
            name,
            **with_timeout(),
        )
        if not isinstance(raw, dict) or not raw:
            return stub_status(cluster, overall="missing", summary="RootSync missing")
        return parse_rootsync(cluster, raw)
    except ApiException as exc:
        if getattr(exc, "status", None) == 404:
            return stub_status(cluster, overall="missing", summary="RootSync missing")
        return stub_status(
            cluster,
            overall="error",
            summary="RootSync query failed",
            error=api_exc_message(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return stub_status(
            cluster,
            overall="error",
            summary="RootSync query failed",
            error=f"{type(exc).__name__}: {exc}",
        )
