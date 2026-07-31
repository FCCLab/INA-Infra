"""PromQL client against per-cluster Prometheus.

Order:
1. NodePort on CP mgmt IP (PROM_NODEPORT / 30909)
2. kube-apiserver service proxy
3. short-lived kubectl port-forward (edge NodePort + API proxy often fail in this lab)
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException

from app.services.clusters import kube_context, kubeconfig_path, prometheus_base_url
from app.services.k8s_client import close_quietly, core_v1, load_api_client, with_timeout

_TIMEOUT_S = 4.0
_PROM_NS = "monitoring"
_PROM_SVC = "prometheus"
_PROM_PORT = 9090

# cluster -> (proc, local_port, expires_monotonic)
_PF: Dict[str, Tuple[subprocess.Popen, int, float]] = {}
_PF_LOCK = threading.Lock()
_PF_TTL_S = 45.0


def finite(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _parse_prom_body(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if body.get("status") != "success":
        return [], f"prometheus: {body.get('errorType', 'error')}: {body.get('error', body)}"
    data = body.get("data") or {}
    result = data.get("result") or []
    if not isinstance(result, list):
        return [], "prometheus: unexpected result shape"
    return result, None


def _http_get_json(url: str, timeout: float) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            detail = str(exc)
        return None, f"prometheus HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return None, f"prometheus: {type(exc).__name__}: {exc}"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _stop_pf(cluster: str) -> None:
    ent = _PF.pop(cluster, None)
    if not ent:
        return
    proc, _port, _exp = ent
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _ensure_port_forward(cluster: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (http://127.0.0.1:port, error). Keeps PF warm for a short TTL."""
    now = time.monotonic()
    with _PF_LOCK:
        ent = _PF.get(cluster)
        if ent and ent[0].poll() is None and ent[2] > now:
            _PF[cluster] = (ent[0], ent[1], now + _PF_TTL_S)
            return f"http://127.0.0.1:{ent[1]}", None
        if ent:
            _stop_pf(cluster)

        local = _free_port()
        cmd = [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig_path(cluster)),
            "--context",
            kube_context(cluster),
            "-n",
            _PROM_NS,
            "port-forward",
            f"svc/{_PROM_SVC}",
            f"{local}:{_PROM_PORT}",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        last_err: Optional[BaseException] = None
        for _ in range(40):
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
                return None, f"port-forward exited: {err.strip() or proc.returncode}"
            time.sleep(0.15)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{local}/-/ready",
                    timeout=1.5,
                ) as resp:
                    if resp.status == 200:
                        _PF[cluster] = (proc, local, time.monotonic() + _PF_TTL_S)
                        return f"http://127.0.0.1:{local}", None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_err = exc
                continue
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        return None, f"port-forward timeout: {last_err}"


def _query_via_apiserver(
    cluster: str, api_path: str, params: Dict[str, str], timeout: float
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    api, err = load_api_client(cluster)
    if err or api is None:
        return [], err or "no kube client for prometheus proxy"
    try:
        v1 = core_v1(api)
        rel = api_path.lstrip("/")
        q = urllib.parse.urlencode(params)
        path_with_query = f"{rel}?{q}"
        raw = v1.connect_get_namespaced_service_proxy_with_path(
            name=f"http:{_PROM_SVC}:{_PROM_PORT}",
            namespace=_PROM_NS,
            path=path_with_query,
            **with_timeout({"_request_timeout": (2, min(timeout, 3.0))}),
        )
        if isinstance(raw, (bytes, bytearray)):
            body = json.loads(raw.decode("utf-8"))
        elif isinstance(raw, str):
            body = json.loads(raw)
        elif isinstance(raw, dict):
            body = raw
        else:
            return [], f"prometheus proxy: unexpected type {type(raw).__name__}"
        return _parse_prom_body(body)
    except ApiException as exc:
        return [], f"prometheus proxy: {exc.status} {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return [], f"prometheus proxy: {type(exc).__name__}: {exc}"
    finally:
        close_quietly(api)


def _query_with_fallback(
    cluster: str, api_path: str, params: Dict[str, str], timeout: float = _TIMEOUT_S
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    errors: List[str] = []

    base = prometheus_base_url(cluster)
    url = f"{base}{api_path}?{urllib.parse.urlencode(params)}"
    body, err = _http_get_json(url, min(timeout, 1.5))
    if body is not None:
        return _parse_prom_body(body)
    if err:
        errors.append(err)

    result, proxy_err = _query_via_apiserver(cluster, api_path, params, timeout)
    if result:
        return result, None
    if proxy_err:
        errors.append(proxy_err)

    pf_base, pf_err = _ensure_port_forward(cluster)
    if pf_base:
        pf_url = f"{pf_base}{api_path}?{urllib.parse.urlencode(params)}"
        body, err = _http_get_json(pf_url, timeout)
        if body is not None:
            return _parse_prom_body(body)
        if err:
            errors.append(err)
            with _PF_LOCK:
                _stop_pf(cluster)
    elif pf_err:
        errors.append(pf_err)

    return [], "; ".join(errors) if errors else "prometheus unreachable"


def query(cluster: str, promql: str, timeout: float = _TIMEOUT_S) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Instant query. Returns (result vector, error)."""
    return _query_with_fallback(cluster, "/api/v1/query", {"query": promql}, timeout)


def query_range(
    cluster: str,
    promql: str,
    start: float,
    end: float,
    step: str = "15s",
    timeout: float = _TIMEOUT_S,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Range query. Returns (result matrix, error)."""
    params = {
        "query": promql,
        "start": f"{start:.3f}",
        "end": f"{end:.3f}",
        "step": step,
    }
    return _query_with_fallback(cluster, "/api/v1/query_range", params, timeout)


def vector_by_label(
    result: List[Dict[str, Any]], label: str = "node"
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for sample in result:
        metric = sample.get("metric") or {}
        key = metric.get(label) or metric.get("Hostname") or metric.get("hostname")
        if not key:
            continue
        value = sample.get("value")
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        num = finite(value[1])
        if num is None:
            continue
        prev = out.get(key)
        out[key] = num if prev is None else max(prev, num)
    return out


def sample_value(sample: Dict[str, Any]) -> Optional[float]:
    value = sample.get("value")
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return finite(value[1])
