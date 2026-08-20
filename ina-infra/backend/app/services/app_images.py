"""Canonical application images and PL defaults (CCTV, Physical AI, OTT, IoT).

GitOps generate_server_manifests and new-profile defaults must use these, not
legacy names (slicea-analyzer, hd-stream-server, …).
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from app.schemas import SliceApplicationConfig, SliceIn

REG = "10.1.132.30:5000"

CCTV_SERVER = f"{REG}/application-cctv:nws-v0.9-amd64"
CCTV_FRONTEND = f"{REG}/application-cctv-frontend:nws-v0.15-amd64"
CCTV_UE_CONSOLE = f"{REG}/cctv-ue-console:nws-v0.1-amd64"
CCTV_CLIENT = CCTV_UE_CONSOLE
PHYSICAL_AI_SERVER = f"{REG}/cosmo3-vllm:nws-v0.7"
PHYSICAL_AI_CLIENT = f"{REG}/cosmo3-ue-console:nws-v0.18-amd64"
OTT_SERVER = f"{REG}/application-ott:nws-v0.10-amd64"
OTT_CLIENT = f"{REG}/ott-ue-console:nws-v0.33-amd64"
IOT_SERVER = f"{REG}/sliced-edge:nws-v0.9-amd64"
IOT_CLIENT = f"{REG}/iot-ue-console:nws-v0.10-amd64"
RTT_PROBE = f"{REG}/rtt-probe:nws-v0.7-amd64"
THROUGHPUT_STATS = f"{REG}/throughput-statistics:nws-v0.2-amd64"

STALE_SERVER_REPOS = frozenset({"slicea-analyzer", "hd-stream-server"})
STALE_CLIENT_REPOS = frozenset(
    {"slicea-publisher", "hd-stream-client", "cosmo3-aiperf", "sliced-client"}
)

_SERVER = {
    "cctv": CCTV_SERVER,
    "physical_ai": PHYSICAL_AI_SERVER,
    "ott": OTT_SERVER,
    "iot": IOT_SERVER,
}
_CLIENT = {
    "cctv": CCTV_CLIENT,
    "physical_ai": PHYSICAL_AI_CLIENT,
    "ott": OTT_CLIENT,
    "iot": IOT_CLIENT,
}


def _image_repo(image: str) -> str:
    return (image or "").rsplit("/", 1)[-1].split(":")[0]


def _prefer_newer_nws(stored: str, canonical: str) -> str:
    """Same image repo: keep the newer nws-v0.N tag (saved profiles pin old minors)."""
    if not canonical or _image_repo(stored) != _image_repo(canonical):
        return stored
    stored_m = _nws_minor(stored)
    canon_m = _nws_minor(canonical)
    if stored_m is not None and canon_m is not None and stored_m < canon_m:
        return canonical
    return stored


def resolve_server_image(app_type: str, stored: Optional[str] = None) -> str:
    canonical = _SERVER.get((app_type or "").lower(), "")
    img = (stored or "").strip()
    if not img:
        return canonical
    if _image_repo(img) in STALE_SERVER_REPOS:
        return canonical
    if _image_repo(img) == "cosmo3-vllm" and "nws-v0.5" in img:
        return canonical
    return _prefer_newer_nws(img, canonical)


def _nws_minor(image: str) -> Optional[int]:
    m = re.search(r"nws-v0\.(\d+)", image or "")
    return int(m.group(1)) if m else None


def resolve_client_image(app_type: str, stored: Optional[str] = None) -> str:
    canonical = _CLIENT.get((app_type or "").lower(), "")
    img = (stored or "").strip()
    if not img:
        return canonical
    if _image_repo(img) in STALE_CLIENT_REPOS:
        return canonical
    img = _prefer_newer_nws(img, canonical)
    # OTT: force roll when 4K is not re-pinned after ads (< v0.33).
    if (app_type or "").lower() == "ott" and _image_repo(img) == "ott-ue-console":
        m = _nws_minor(img)
        if m is not None and m < 33:
            return canonical
    return img


def canonicalize_config(cfg: SliceApplicationConfig) -> SliceApplicationConfig:
    t = (cfg.app_type or "").lower()
    if t not in _SERVER:
        return cfg
    preset = preset_config(cfg.slice_id)
    params = dict(cfg.params or {})
    if (preset.app_type or "").lower() == t:
        merged = dict(preset.params or {})
        merged.update(params)
        params = merged
        if t == "cctv" and str(params.get("stream_path") or "") in ("", "slicea"):
            params["stream_path"] = f"cctv/ue{cfg.slice_id}"
    if t == "physical_ai":
        model = str(params.get("model") or "")
        if not model or "Nemotron" in model:
            params["model"] = "nvidia/Cosmos3-Nano"
        gpu_arch = str(params.get("gpu_arch") or "auto").lower()
        if gpu_arch in ("arm64-gh200", "arm64", "gh200", "aarch64"):
            # Slice 2 runs on edge A40; do not persist GH200 from an old profile.
            params["gpu_arch"] = "auto"
    updates = {
        "server_image": resolve_server_image(t, cfg.server_image),
        "client_image": resolve_client_image(t, cfg.client_image),
        "params": params,
    }
    if t == "physical_ai" and (cfg.target_cluster or "").strip().lower() in ("", "auto"):
        updates["target_cluster"] = "edge"
    return cfg.model_copy(update=updates)


def preset_config(slice_id: int) -> SliceApplicationConfig:
    """Current PL application config for slices 1–4 (else a disabled stub)."""
    if slice_id == 1:
        return SliceApplicationConfig(
            slice_id=1,
            app_type="cctv",
            name="CCTV Vision Streaming",
            enabled=True,
            target_cluster="auto",
            server_image=CCTV_SERVER,
            client_image=CCTV_CLIENT,
            server_port=8554,
            metrics_port=9102,
            params={
                "stream_path": "cctv/ue1",
                "yolo_model": "yolov8n.pt",
                "yolo_device": "cpu",
                "frame_skip": 1,
                "rtsp_port": 8554,
                "http_port": 8080,
                "fps": 25,
                "bitrate_kbps": 4000,
                "rtsp_protocol": "tcp",
                "video_clip_ids": ["classroom"],
                "client_count": 1,
            },
        )
    if slice_id == 2:
        return SliceApplicationConfig(
            slice_id=2,
            app_type="physical_ai",
            name="Physical AI (Cosmos3 VLM)",
            enabled=True,
            target_cluster="edge",
            server_image=PHYSICAL_AI_SERVER,
            client_image=PHYSICAL_AI_CLIENT,
            server_port=8000,
            metrics_port=8002,
            params={
                "model": "nvidia/Cosmos3-Nano",
                "tensor_parallel_size": 1,
                "max_model_len": 4096,
                "gpu_arch": "auto",
                "gpu_memory_utilization": 0.75,
                "request_rate": 10,
                "client_count": 1,
                "prompt_interval_s": 2,
                "max_tokens": 128,
            },
        )
    if slice_id == 3:
        return SliceApplicationConfig(
            slice_id=3,
            app_type="ott",
            name="OTT HD Video Streaming",
            enabled=True,
            target_cluster="auto",
            server_image=OTT_SERVER,
            client_image=OTT_CLIENT,
            server_port=8554,
            metrics_port=9103,
            params={
                "stream_protocol": "rtsp",
                "stream_path": "live/hd",
                "bitrate_kbps": 6000,
                "resolution": "4k",
                "play_quality": "4k",
                "client_count": 1,
            },
        )
    if slice_id == 4:
        return SliceApplicationConfig(
            slice_id=4,
            app_type="iot",
            name="Background IoT (MQTT)",
            enabled=True,
            target_cluster="auto",
            server_image=IOT_SERVER,
            client_image=IOT_CLIENT,
            server_port=1883,
            metrics_port=9105,
            params={
                "num_devices": 5,
                "fast_period_s": 60,
                "med_period_s": 1800,
                "slow_period_s": 3600,
                "dl_fast_period_s": 300,
                "dl_slow_period_s": 3600,
                "mqtt_qos": 0,
                "client_count": 1,
            },
        )
    return SliceApplicationConfig(
        slice_id=slice_id,
        app_type="none",
        name=f"Slice {slice_id} Workload",
        enabled=False,
        target_cluster="auto",
        server_image="",
        client_image="",
        server_port=8080,
        metrics_port=9100 + slice_id,
        params={"client_count": 1},
    )


def default_applications(
    slices: Optional[list[SliceIn]] = None,
) -> Dict[str, SliceApplicationConfig]:
    from app.services import pl_solver

    sl = slices if slices is not None else pl_solver.default_slices()
    return {str(s.id): preset_config(s.id) for s in sl}
