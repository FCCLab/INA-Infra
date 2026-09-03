#!/usr/bin/env python3
"""Sync Hugging Face token between gpu-a40 persistent hostPath and Kubernetes secrets."""
import base64
import json
import logging
import shutil
import subprocess
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hf-sync")

KUBECTL_BIN = shutil.which("kubectl") or "/home/fcp/.local/bin/kubectl"
SSH_CMD = ["ssh", "-F", "/home/fcp/INA-Infra/utils/ssh_config/config", "gpu-a40"]
REMOTE_PATH = "/var/lib/ina-infra/hf-token/token"
NAMESPACES = ["exp1-a", "exp1-b", "exp1-c", "exp1-d", "ina-infra"]


def get_volume_token() -> str:
    try:
        p = subprocess.run(
            [*SSH_CMD, f"cat {REMOTE_PATH} 2>/dev/null || true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return p.stdout.strip()
    except Exception as exc:
        logger.debug(f"get_volume_token error: {exc}")
        return ""


def set_volume_token(token: str) -> bool:
    if not token or not token.startswith("hf_"):
        return False
    try:
        p = subprocess.run(
            [
                *SSH_CMD,
                f"sudo mkdir -p /var/lib/ina-infra/hf-token && echo -n '{token}' | sudo tee {REMOTE_PATH} >/dev/null && sudo chmod 666 {REMOTE_PATH}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode == 0:
            logger.info(f"Saved HF token to volume {REMOTE_PATH} on gpu-a40")
            return True
    except Exception as exc:
        logger.error(f"set_volume_token error: {exc}")
    return False


def get_secret_token(namespace: str) -> str:
    try:
        p = subprocess.run(
            [KUBECTL_BIN, "--context=edge@edge", "-n", namespace, "get", "secret", "ina-hf-token", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode != 0:
            return ""
        data = json.loads(p.stdout).get("data", {})
        raw_b64 = data.get("token", "")
        if raw_b64:
            return base64.b64decode(raw_b64).decode("utf-8").strip()
    except Exception as exc:
        logger.debug(f"get_secret_token error for {namespace}: {exc}")
    return ""


def set_secret_token(namespace: str, token: str) -> bool:
    if not token:
        return False
    try:
        p = subprocess.run(
            [
                KUBECTL_BIN,
                "--context=edge@edge",
                "-n",
                namespace,
                "patch",
                "secret",
                "ina-hf-token",
                "-p",
                json.dumps({"stringData": {"token": token}}),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode == 0:
            logger.info(f"Updated Secret ina-hf-token in namespace {namespace}")
            return True
    except Exception as exc:
        logger.error(f"set_secret_token error for {namespace}: {exc}")
    return False


def sync_cycle() -> None:
    vol_tok = get_volume_token()

    # 1. If volume is empty, check if any namespace secret has a token
    if not vol_tok:
        for ns in NAMESPACES:
            sec_tok = get_secret_token(ns)
            if sec_tok and sec_tok.startswith("hf_"):
                logger.info(f"Found HF token in Secret {ns}/ina-hf-token. Persisting to volume on gpu-a40...")
                set_volume_token(sec_tok)
                vol_tok = sec_tok
                break

    # 2. If volume has a token, ensure all active namespaces have it in their secret
    if vol_tok and vol_tok.startswith("hf_"):
        for ns in NAMESPACES:
            sec_tok = get_secret_token(ns)
            if not sec_tok:
                # Secret might not exist yet or has empty token
                set_secret_token(ns, vol_tok)


def main() -> None:
    logger.info("Starting HF token persistent volume sync worker...")
    while True:
        try:
            sync_cycle()
        except Exception as exc:
            logger.error(f"Sync loop error: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()
