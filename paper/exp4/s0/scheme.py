"""Exp4 Scheme 0 (S0): static baseline — five DL slices.

PL off, PM frozen at peak T_bar, PS equal PRB (nws-xapp idle).

Anti-pattern: CU-UP + UPF pinned at central, APP pinned at edge for every
slice (N6 hairpin). Same workloads and IPs as S1; only sites differ.
"""

from __future__ import annotations

SCHEME_ID = "exp4-s0"
SCHEME_NAME = "S0 Static"
NAMESPACE = "exp4-s0"
PART_OF = "exp4"

# S0: uncoordinated static — core UPF, MEC-everywhere apps.
SLICES = {
    1: {
        "name": "FTP",
        "label": "FTP 5 MB",
        "app_type": "iperf-sftp",
        "cu": "central",
        "upf": "central",
        "app": "edge",
        "t_bar": 20.0,
        "d_bar": 250.0,
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
        "cpu_app": 2.0,
        "mem_app": "1Gi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    2: {
        "name": "YOLO",
        "label": "YOLO bbox overlay DL",
        "app_type": "cctv",
        "cu": "central",
        "upf": "central",
        "app": "edge",
        "t_bar": 12.0,
        "d_bar": 45.0,
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
        "cpu_app": 4,
        "mem_app": "12Gi",
        "gpu_app": 1.0,
        "b_min": 54.6,
    },
    3: {
        "name": "VIDEO",
        "label": "gstreamer / OTT watch DL",
        "app_type": "ott",
        "cu": "central",
        "upf": "central",
        "app": "edge",
        "t_bar": 22.0,
        "d_bar": 58.0,
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
        "cpu_app": 2.0,
        "mem_app": "2Gi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    4: {
        "name": "CPU-OFF",
        "label": "encrypt+zip+scan+LUT then DL",
        "app_type": "cpu-offload",
        "cu": "central",
        "upf": "central",
        "app": "edge",
        "t_bar": 8.0,
        "d_bar": 400.0,
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
        "cpu_app": 2.0,
        "mem_app": "1Gi",
        "gpu_app": 0.0,
        "b_min": 54.6,
    },
    5: {
        "name": "MQTT",
        "label": "MQTT Get farm telemetry DL",
        "app_type": "iot",
        "cu": "central",
        "upf": "central",
        "app": "edge",
        "t_bar": 2.0,
        "d_bar": 80.0,
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
        "cpu_app": 0.5,
        "mem_app": "512Mi",
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

PL_ENABLED = False
PM_ENABLED = False
PS_ENABLED = False
XAPP_REPLICAS = 0
REGISTRY = "10.1.132.30:5000"


def site_label(cluster: str) -> str:
    return TIER_NAME[TIER_ID[cluster]]
