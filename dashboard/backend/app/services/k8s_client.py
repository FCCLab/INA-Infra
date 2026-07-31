"""Per-cluster Kubernetes API clients loaded from kubeconfig."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from kubernetes import client, config
from kubernetes.client import ApiClient
from kubernetes.config.config_exception import ConfigException

from app.services.clusters import kube_context, kubeconfig_path

# Short socket timeout so one dead cluster does not hang the overview.
_REQUEST_TIMEOUT = 8


def load_api_client(cluster: str) -> Tuple[Optional[ApiClient], Optional[str]]:
    """Return (ApiClient, error). Client is None on failure."""
    path = kubeconfig_path(cluster)
    ctx = kube_context(cluster)
    if not path.is_file():
        return None, f"kubeconfig not found: {path}"
    try:
        conf = client.Configuration()
        config.load_kube_config(
            config_file=str(path),
            context=ctx,
            client_configuration=conf,
        )
        conf.retries = 0
        # Fail fast on unreachable APIs so one cluster does not stall the overview.
        conf.connection_pool_maxsize = 4
        api = ApiClient(configuration=conf)
        return api, None
    except ConfigException as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — surface any load error to API
        return None, f"{type(exc).__name__}: {exc}"


def with_timeout(kwargs: Optional[dict] = None) -> dict:
    out = dict(kwargs or {})
    # (connect, read) — short connect so unreachable clusters fail fast
    out.setdefault("_request_timeout", (3, _REQUEST_TIMEOUT))
    return out


def core_v1(api: ApiClient) -> client.CoreV1Api:
    return client.CoreV1Api(api)


def apps_v1(api: ApiClient) -> client.AppsV1Api:
    return client.AppsV1Api(api)


def custom_objects(api: ApiClient) -> client.CustomObjectsApi:
    return client.CustomObjectsApi(api)


def close_quietly(api: Optional[ApiClient]) -> None:
    if api is None:
        return
    try:
        api.close()
    except Exception:  # noqa: BLE001
        pass


def api_exc_message(exc: BaseException) -> str:
    body: Any = getattr(exc, "body", None)
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = str(body)
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    parts = [p for p in (str(status) if status else None, reason, str(body) if body else str(exc)) if p]
    return ": ".join(parts) if parts else str(exc)
