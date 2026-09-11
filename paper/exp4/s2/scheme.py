"""Exp4 Scheme 2 (S2): +PL +PM — five DL slices.

PL on (same sites as S1), PM on (compute_policy=pm-resize), PS equal PRB
(nws-xapp idle).

Copied from S1: same workloads, IPs, console IPs, and placement.

PM requests = S1 server usage (run 20260909-110400_300s) × 1.25,
rounded up. GPU stays 1.0 (cannot fraction an A40). Limits still burst
to 8 CPU / 8Gi.
"""

from __future__ import annotations

SCHEME_ID = "exp4-s2"
SCHEME_NAME = "S2 +PL +PM"
NAMESPACE = "exp4-s2"
PART_OF = "exp4"

# S2: same PL sites as S1. CU-UP, UPF, and APP are co-located.
# C=central, R=regional, E=edge
SLICES = {
    1: {
        "name": "FTP",
        "label": "FTP 5 MB",
        "app_type": "iperf-sftp",
        "cu": "central",
        "upf": "central",
        "app": "central",
        "t_bar": 20.2,
        "d_bar": 88.5,
        "strict_sla": False,
        "h_s": 0,
        "eta_t0": 2.4,
        "app_ip": "10.1.137.211",
        "app_mac": "02:0a:89:a0:00:01",
        "ue_rf": "10.1.140.141",
        "ue_console_ip": "10.1.137.221",
        "dnn": "oai1",
        "sd": "0x000001",
        "imsi": "001010000000101",
        # S1 mean 204m / 240Mi → ×1.25
        "cpu_app": 0.30,
        "mem_app": "320Mi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    2: {
        "name": "YOLO",
        "label": "YOLO bbox overlay DL",
        "app_type": "cctv",
        "cu": "edge",
        "upf": "edge",
        "app": "edge",
        "t_bar": 16.8,
        "d_bar": 130.5,
        "strict_sla": True,
        "h_s": 1,
        "eta_t0": 2.2,
        "app_ip": "10.1.137.212",
        "app_mac": "02:0a:89:a0:00:02",
        "ue_rf": "10.1.140.142",
        "ue_console_ip": "10.1.137.222",
        "dnn": "oai2",
        "sd": "0x000002",
        "imsi": "001010000000102",
        # S1 mean 5627m / 1622Mi → ×1.25; GPU still 1 (device plugin)
        "cpu_app": 7.1,
        "mem_app": "2Gi",
        "gpu_app": 1.0,
        "b_min": 54.6,
    },
    3: {
        "name": "VIDEO",
        "label": "gstreamer / OTT watch DL",
        "app_type": "ott",
        "cu": "regional",
        "upf": "regional",
        "app": "regional",
        "t_bar": 56.9,
        "d_bar": 66.0,
        "strict_sla": True,
        "h_s": 0,
        "eta_t0": 2.5,
        "app_ip": "10.1.137.213",
        "app_mac": "02:0a:89:a0:00:03",
        "ue_rf": "10.1.140.143",
        "ue_console_ip": "10.1.137.223",
        "dnn": "oai3",
        "sd": "0x000003",
        "imsi": "001010000000103",
        # S1 mean 65m / 94Mi → ×1.25
        "cpu_app": 0.10,
        "mem_app": "128Mi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    4: {
        "name": "CPU-OFF",
        "label": "encrypt+zip+scan+LUT then DL",
        "app_type": "cpu-offload",
        "cu": "central",
        "upf": "central",
        "app": "central",
        "t_bar": 18.3,
        "d_bar": 309.7,
        "strict_sla": False,
        "h_s": 0,
        "eta_t0": 2.3,
        "app_ip": "10.1.137.214",
        "app_mac": "02:0a:89:a0:00:04",
        "ue_rf": "10.1.140.144",
        "ue_console_ip": "10.1.137.224",
        "dnn": "oai4",
        "sd": "0x000004",
        "imsi": "001010000000104",
        # S1 mean 372m / 239Mi → ×1.25
        "cpu_app": 0.50,
        "mem_app": "320Mi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    5: {
        "name": "MQTT",
        "label": "MQTT Get farm telemetry DL",
        "app_type": "iot",
        "cu": "central",
        "upf": "central",
        "app": "central",
        "t_bar": 3.67,
        "d_bar": 75.2,
        "strict_sla": True,
        "h_s": 0,
        "eta_t0": 2.6,
        "app_ip": "10.1.137.215",
        "app_mac": "02:0a:89:a0:00:05",
        "ue_rf": "10.1.140.145",
        "ue_console_ip": "10.1.137.225",
        "dnn": "oai5",
        "sd": "0x000005",
        "imsi": "001010000000105",
        # S1 mean 818m / 106Mi → ×1.25
        "cpu_app": 1.05,
        "mem_app": "192Mi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
}

TIER_ID = {"edge": 0, "regional": 1, "central": 2}
TIER_NAME = {0: "Edge", 1: "Regional", 2: "Central"}
REPO_FOR = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
}
CLUSTERS = ("central", "regional", "edge")
KUBE_CONTEXT = {
    "central": "central@central",
    "regional": "regional@regional",
    "edge": "edge@edge",
}

FABRIC = {
    sid: {
        "upf_n3": f"10.1.140.{20 + sid}",
        "upf_n4": f"10.1.140.{40 + sid}",
        "cuup_e1": f"10.1.140.{80 + sid}",
        "cuup_f1u": f"10.1.140.{100 + sid}",
        "cuup_n3": f"10.1.140.{120 + sid}",
        "ue_rf": f"10.1.140.{140 + sid}",
        "dnn_cidr": f"10.140.{sid}.0/24",
    }
    for sid in SLICES
}

PL_ENABLED = True
PM_ENABLED = True
PS_ENABLED = False
XAPP_REPLICAS = 0
REGISTRY = "10.1.132.30:5000"


def site_label(cluster: str) -> str:
    return TIER_NAME[TIER_ID[cluster]]
