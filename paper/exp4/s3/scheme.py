"""Exp4 Scheme 3 (S3): +PL +PM +PS — five DL slices.

PL + PM same as S2. PS on: NSDL (dl_scheduler_type=1, ul_scheduler_type=0)
with DL min PRB 20/20/20/20/10 % (slices 1–5, sum 90%). dedicated stays 0.
nws-xapp replicas = 1.
"""

from __future__ import annotations

SCHEME_ID = "exp4-s3"
SCHEME_NAME = "S3 +PL +PM +PS"
NAMESPACE = "exp4-s3"
PART_OF = "exp4"

# S3: same PL sites and PM requests as S2.
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
        "cpu_app": 0.30,
        "mem_app": "320Mi",
        "gpu_app": 0.0,
        "dedicated_prb_ratio": 0.0,
        "min_prb_ratio": 20.0,
        "b_min": 20.0,
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
        "cpu_app": 7.1,
        "mem_app": "2Gi",
        "gpu_app": 1.0,
        "dedicated_prb_ratio": 0.0,
        "min_prb_ratio": 20.0,
        "b_min": 20.0,
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
        "cpu_app": 0.10,
        "mem_app": "128Mi",
        "gpu_app": 0.0,
        "dedicated_prb_ratio": 0.0,
        "min_prb_ratio": 20.0,
        "b_min": 20.0,
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
        "cpu_app": 0.50,
        "mem_app": "320Mi",
        "gpu_app": 0.0,
        "dedicated_prb_ratio": 0.0,
        "min_prb_ratio": 20.0,
        "b_min": 20.0,
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
        "cpu_app": 1.05,
        "mem_app": "192Mi",
        "gpu_app": 0.0,
        "dedicated_prb_ratio": 0.0,
        "min_prb_ratio": 10.0,
        "b_min": 10.0,
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
PS_ENABLED = True
XAPP_REPLICAS = 1
REGISTRY = "10.1.132.30:5000"


def site_label(cluster: str) -> str:
    return TIER_NAME[TIER_ID[cluster]]
