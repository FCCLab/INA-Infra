"""Apply Hugging Face tokens as a K8s Secret for Physical AI vLLM (not GitOps)."""

from __future__ import annotations

import subprocess
from typing import Any, Dict, Optional, Tuple

import yaml

from app.schemas import PhysicalAiHfTokenStatus, SliceApplicationConfig
from app.services import application_deploy, profile_store

HF_SECRET_NAME = "ina-hf-token"
HF_SECRET_KEY = "token"
HF_DEPLOY_NAME = "application-physical-ai"


def _target(profile_name: str) -> Tuple[str, str]:
    rec = profile_store.get_profile(profile_name)
    if rec is None:
        raise ValueError(f"profile not found: {profile_name}")
    cluster = "edge"
    apps = rec.applications or {}
    for cfg in apps.values():
        if cfg is None:
            continue
        if isinstance(cfg, dict):
            cfg = SliceApplicationConfig.model_validate(cfg)
        if (cfg.app_type or "").lower() == "physical_ai":
            cluster = application_deploy.resolve_target_cluster(cfg, rec.pl_result)
            break
    return cluster, profile_name


def _kubectl(cluster: str, args: list[str], *, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    kc = application_deploy._kube_cmd(cluster)
    return subprocess.run(
        [*kc, *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


def status(profile_name: str) -> PhysicalAiHfTokenStatus:
    cluster, ns = _target(profile_name)
    proc = _kubectl(cluster, ["-n", ns, "get", "secret", HF_SECRET_NAME, "-o", "jsonpath={.data}"])
    configured = proc.returncode == 0 and bool((proc.stdout or "").strip())
    msg = "Hugging Face token is saved on the cluster." if configured else "No Hugging Face token saved yet."
    if proc.returncode != 0 and "NotFound" not in (proc.stderr or ""):
        err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        msg = err[0] or msg
    return PhysicalAiHfTokenStatus(
        ok=True,
        configured=configured,
        cluster=cluster,
        namespace=ns,
        secret=HF_SECRET_NAME,
        message=msg,
    )


def save(profile_name: str, token: str) -> PhysicalAiHfTokenStatus:
    token = (token or "").strip()
    if not token:
        raise ValueError("token is required")
    cluster, ns = _target(profile_name)
    _kubectl(cluster, ["create", "ns", ns])
    secret: Dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": HF_SECRET_NAME,
            "namespace": ns,
            "labels": {
                "app.kubernetes.io/name": HF_SECRET_NAME,
                "app.kubernetes.io/part-of": ns,
                "ina.lab/role": "hf-token",
            },
        },
        "type": "Opaque",
        "stringData": {HF_SECRET_KEY: token},
    }
    apply = _kubectl(cluster, ["apply", "-f", "-"], input_text=yaml.safe_dump(secret))
    if apply.returncode != 0:
        raise RuntimeError((apply.stderr or apply.stdout or "failed to apply secret").strip()[:400])
    restart = _kubectl(
        cluster,
        ["-n", ns, "rollout", "restart", "deploy", HF_DEPLOY_NAME],
    )
    restarted = restart.returncode == 0
    note = "Token saved."
    if restarted:
        note += f" Restarted {HF_DEPLOY_NAME} on {cluster}."
    else:
        note += " Server will pick it up on the next Physical AI deploy."
    return PhysicalAiHfTokenStatus(
        ok=True,
        configured=True,
        cluster=cluster,
        namespace=ns,
        secret=HF_SECRET_NAME,
        restarted=restarted,
        message=note,
    )


def delete(profile_name: str) -> PhysicalAiHfTokenStatus:
    cluster, ns = _target(profile_name)
    _kubectl(cluster, ["-n", ns, "delete", "secret", HF_SECRET_NAME, "--ignore-not-found=true"])
    _kubectl(cluster, ["-n", ns, "rollout", "restart", "deploy", HF_DEPLOY_NAME])
    return PhysicalAiHfTokenStatus(
        ok=True,
        configured=False,
        cluster=cluster,
        namespace=ns,
        secret=HF_SECRET_NAME,
        message="Hugging Face token removed.",
    )
