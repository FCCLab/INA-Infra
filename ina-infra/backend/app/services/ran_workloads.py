"""Emit profile-namespace gNB (CU-CP/DU/UE/FlexRIC) + co-located UPF/CU-UP Deployments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

import yaml

from app.schemas import IpPlan, PlacementOut, PlSolveResponse, SliceIps

SITE_TO_CLUSTER = {0: "edge", 1: "regional", 2: "central"}
SITE_NODES: Dict[str, List[str]] = {
    "central": ["central-0", "central-1"],
    "regional": ["regional-0", "regional-1"],
    "edge": ["edge-0", "edge-1"],
}

# OAI nws-v0.5-amd64 images — keep off arm64 GPU workers (gh81/gh82).
ARCH_AMD64 = {"kubernetes.io/arch": "amd64"}


def _hostname_affinity(node_names: Sequence[str]) -> dict:
    return {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/hostname",
                                "operator": "In",
                                "values": list(node_names),
                            }
                        ]
                    }
                ]
            }
        }
    }

IMAGE_CUCP = "10.1.132.30:5000/oai-cucp:nws-v0.5-amd64"
IMAGE_CUUP = "10.1.132.30:5000/oai-nr-cuup:nws-v0.5-amd64"
IMAGE_DU = "10.1.132.30:5000/oai-du:nws-v0.5-amd64"
IMAGE_UE = "10.1.132.30:5000/oai-nr-ue:nws-v0.5-amd64"
IMAGE_FLEXRIC = "10.1.132.30:5000/oai-flexric:nws-v0.5-amd64"
IMAGE_DEBUG = "docker.io/nicolaka/netshoot"

UE_KEY = "fec86ba6eb707ed08905757b1bb44b8f"
UE_OPC = "C42449363BBAD02B66D16BC975D77CC1"

WriteFn = Callable[[Path, str, List[str]], None]


def _dump(doc: dict) -> str:
    return yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)


def _networks_annot(entries: Sequence[tuple[str, str, str]], gw: str, plen: int) -> str:
    parts = [
        (
            f'{{"name": "{name}", "interface": "{iface}", '
            f'"ips": ["{ip}/{plen}"], "gateways": ["{gw}"]}}'
        )
        for name, iface, ip in entries
    ]
    return "[" + ", ".join(parts) + "]"


def _debug_sidecar() -> dict:
    return {
        "name": "debug",
        "image": IMAGE_DEBUG,
        "command": ["sleep", "infinity"],
        "securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}},
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
    }


def _sa(name: str, namespace: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/part-of": "ina-infra"},
        },
    }


def _slice_sd_hex(n: int) -> str:
    return f"{n:06x}"


def _imsi(n: int) -> str:
    return f"00101000000010{n}"


def _dnn(n: int) -> str:
    return f"oai{n}"


def _snssai_list(n_slices: int) -> str:
    items = ['{ sst = 1, sd = 0xFFFFFF }']
    for n in range(1, n_slices + 1):
        items.append(f"{{ sst = 1, sd = 0x{_slice_sd_hex(n)} }}")
    return ", ".join(items)


def _cucp_conf(shared, n_slices: int) -> str:
    return f"""Active_gNBs = ( "oai-cu-cp");
Asn1_verbosity = "none";
sa = 1;

gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_name  =  "oai-cu-cp";
    tracking_area_code  =  0x0051;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({_snssai_list(n_slices)})
                }});

    nr_cellid = 12345678;
    tr_s_preference = "f1";
    local_s_address = "{shared.cucp_f1c}";
    remote_s_address = "0.0.0.0";
    local_s_portc   = 501;
    local_s_portd   = 2152;
    remote_s_portc  = 500;
    remote_s_portd  = 2152;

    SCTP :
    {{
        SCTP_INSTREAMS  = 2;
        SCTP_OUTSTREAMS = 2;
    }};

    amf_ip_address      = ( {{ ipv4       = "{shared.amf_n2}"; }});

    E1_INTERFACE =
    (
      {{
        type = "cp";
        ipv4_cucp = "{shared.cucp_e1}";
        port_cucp = 38462;
        ipv4_cuup = "0.0.0.0";
        port_cuup = 38462;
      }}
    )

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{shared.cucp_n2}";
    }};
  }}
);

security = {{
  ciphering_algorithms = ( "nea0" );
  integrity_algorithms = ( "nia2", "nia0" );
  drb_ciphering = "yes";
  drb_integrity = "no";
}};

# Follow DU IQ sample time (OAI time_management.md CU/DU iq_samples).
time_management = {{
  mode = "client";
  server_ip = "{shared.du_f1}";
  server_port = 7374;
}};

log_config :
{{
global_log_level                      ="info";
hw_log_level                          ="info";
phy_log_level                         ="info";
mac_log_level                         ="info";
rlc_log_level                         ="debug";
pdcp_log_level                        ="info";
rrc_log_level                         ="info";
f1ap_log_level                        ="info";
ngap_log_level                        ="debug";
}};
"""


def _du_conf(shared, n_slices: int) -> str:
    """Lab-proven gnb.conf with Multus IPs substituted (n_slices reserved for future)."""
    _ = n_slices
    tmpl_path = (
        Path(__file__).resolve().parents[3] / "templates" / "du" / "gnb.conf.tmpl"
    )
    if not tmpl_path.is_file():
        # Fallback: repo layout ina-infra/templates/...
        tmpl_path = Path(__file__).resolve().parents[4] / "ina-infra" / "templates" / "du" / "gnb.conf.tmpl"
    text = tmpl_path.read_text(encoding="utf-8")
    return (
        text.replace("{{DU_F1}}", shared.du_f1)
        .replace("{{CUCP_F1C}}", shared.cucp_f1c)
        .replace("{{FLEXRIC_E2}}", shared.flexric_e2)
    )


def _cuup_conf(sl: SliceIps, shared) -> str:
    n = sl.n
    sd = _slice_sd_hex(n)
    return f"""Active_gNBs = ( "oai-cu-up-{n}");
Asn1_verbosity = "none";
sa = 1;
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_CU_UP_ID = 0xe0{n};
    gNB_name  =  "oai-cu-up-{n}";
    tracking_area_code  =  0x0051;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({{ sst = 1, sd = 0x{sd} }})
                }});

    tr_s_preference = "f1";
    local_s_address = "{sl.cuup_f1u}";
    remote_s_address = "0.0.0.0";
    local_s_portc   = 501;
    local_s_portd   = 2152;
    remote_s_portc  = 500;
    remote_s_portd  = 2152;

    SCTP :
    {{
        SCTP_INSTREAMS  = 2;
        SCTP_OUTSTREAMS = 2;
    }};

    E1_INTERFACE =
    (
      {{
        type = "up";
        ipv4_cucp = "{shared.cucp_e1}";
        ipv4_cuup = "{sl.cuup_e1}";
      }}
    )

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{sl.cuup_n3}";
        GNB_IPV4_ADDRESS_FOR_NGU                 = "{sl.cuup_n3}";
        GNB_PORT_FOR_S1U                         = 2152;
    }};
  }}
);

security = {{
  ciphering_algorithms = ( "nea0" );
  integrity_algorithms = ( "nia2", "nia0" );
  drb_ciphering = "yes";
  drb_integrity = "no";
}};

log_config :
{{
global_log_level                      ="info";
pdcp_log_level                        ="info";
f1ap_log_level                        ="info";
ngap_log_level                        ="info";
}};
"""


def _ue_conf(sl: SliceIps, du_rf: str) -> str:
    n = sl.n
    return f"""uicc0 = {{
  imsi = "{_imsi(n)}";
  key = "{UE_KEY}";
  opc = "{UE_OPC}";
  dnn = "{_dnn(n)}";
  nssai_sst = 1;
  nssai_sd = 0x{_slice_sd_hex(n)};
}}

thread-pool = "-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1"

rfsimulator = {{
  serveraddr = "{du_rf}";
}}

log_config = {{
  global_log_options = "level,nocolor,time";
}}

channelmod = {{
  max_chan = 10;
  modellist = "modellist_rfsimu_1";
  modellist_rfsimu_1 = (
    {{
      model_name = "rfsimu_channel_enB0";
      type = "AWGN";
      ploss_dB = 20;
      noise_power_dB = -4;
      forgetfact = 0;
      offset = 0;
      ds_tdl = 0;
    }},
    {{
      model_name = "rfsimu_channel_ue0";
      type = "AWGN";
      ploss_dB = 20;
      noise_power_dB = -2;
      forgetfact = 0;
      offset = 0;
      ds_tdl = 0;
    }}
  );
}}
"""


def _write_edge_gnb(
    ns_dir: Path,
    *,
    namespace: str,
    ip_plan: IpPlan,
    write: WriteFn,
    written: List[str],
    du_node: str = "usrp",
    ue_node: str = "usrp",
) -> None:
    shared = ip_plan.shared
    plen = shared.prefix_len
    gw = shared.gw_edge
    n_slices = ip_plan.n_slices

    write(
        ns_dir / "40-serviceaccount-oai-cu-cp-sa.yaml",
        _dump(_sa("oai-cu-cp-sa", namespace)),
        written,
    )
    write(
        ns_dir / "41-configmap-oai-cu-cp-configmap.yaml",
        _dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "oai-cu-cp-configmap",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/part-of": "ina-infra"},
                },
                "data": {"gnb.conf": _cucp_conf(shared, n_slices)},
            }
        ),
        written,
    )
    write(
        ns_dir / "42-deployment-oai-cu-cp.yaml",
        _dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "oai-cu-cp",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/name": "oai-cu-cp",
                        "app.kubernetes.io/part-of": "ina-infra",
                    },
                },
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-cu-cp"}},
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "oai-cu-cp",
                                "app.kubernetes.io/name": "oai-cu-cp",
                            },
                            "annotations": {
                                "k8s.v1.cni.cncf.io/networks": _networks_annot(
                                    [
                                        ("cucp-e1", "e1", shared.cucp_e1),
                                        ("cucp-f1c", "f1c", shared.cucp_f1c),
                                        ("cucp-n2", "n2", shared.cucp_n2),
                                    ],
                                    gw,
                                    plen,
                                )
                            },
                        },
                        "spec": {
                            "serviceAccountName": "oai-cu-cp-sa",
                            "terminationGracePeriodSeconds": 5,
                            "nodeSelector": dict(ARCH_AMD64),
                            "affinity": _hostname_affinity(SITE_NODES["edge"]),
                            "containers": [
                                {
                                    "name": "cucp",
                                    "image": IMAGE_CUCP,
                                    "imagePullPolicy": "IfNotPresent",
                                    "securityContext": {"privileged": True},
                                    "env": [
                                        {"name": "TZ", "value": "Europe/Paris"},
                                        {
                                            "name": "USE_ADDITIONAL_OPTIONS",
                                            "value": "--log_config.global_log_options level,nocolor,time",
                                        },
                                        {"name": "USE_VOLUMED_CONF", "value": "yes"},
                                    ],
                                    "ports": [
                                        {"name": "n2", "containerPort": 36412, "protocol": "SCTP"},
                                        {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                                        {"name": "f1c", "containerPort": 38472, "protocol": "UDP"},
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "configuration",
                                            "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                            "subPath": "gnb.conf",
                                        }
                                    ],
                                },
                                _debug_sidecar(),
                            ],
                            "volumes": [
                                {
                                    "name": "configuration",
                                    "configMap": {"name": "oai-cu-cp-configmap"},
                                }
                            ],
                        },
                    },
                },
            }
        ),
        written,
    )

    write(
        ns_dir / "43-serviceaccount-oai-du-sa.yaml",
        _dump(_sa("oai-du-sa", namespace)),
        written,
    )
    write(
        ns_dir / "44-configmap-oai-du-configmap.yaml",
        _dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "oai-du-configmap",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/part-of": "ina-infra"},
                },
                "data": {"gnb.conf": _du_conf(shared, n_slices)},
            }
        ),
        written,
    )
    write(
        ns_dir / "45-deployment-oai-du.yaml",
        _dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "oai-du",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/name": "oai-du",
                        "app.kubernetes.io/part-of": "ina-infra",
                    },
                },
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-du"}},
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "oai-du",
                                "app.kubernetes.io/name": "oai-du",
                            },
                            "annotations": {
                                "k8s.v1.cni.cncf.io/networks": _networks_annot(
                                    [
                                        ("du-f1", "f1", shared.du_f1),
                                        ("du-rf", "rf", shared.du_rf),
                                    ],
                                    gw,
                                    plen,
                                )
                            },
                        },
                        "spec": {
                            "serviceAccountName": "oai-du-sa",
                            "terminationGracePeriodSeconds": 5,
                            "nodeSelector": {
                                **ARCH_AMD64,
                                "kubernetes.io/hostname": du_node,
                            },
                            "containers": [
                                {
                                    "name": "du",
                                    "image": IMAGE_DU,
                                    "imagePullPolicy": "IfNotPresent",
                                    "securityContext": {"privileged": True},
                                    "env": [
                                        {
                                            "name": "USE_ADDITIONAL_OPTIONS",
                                            "value": "--rfsim --log_config.global_log_options level,nocolor,time",
                                        }
                                    ],
                                    "ports": [
                                        {"name": "f1c", "containerPort": 38472, "protocol": "SCTP"},
                                        {"name": "f1u", "containerPort": 2152, "protocol": "UDP"},
                                        {"name": "rfsim", "containerPort": 4043, "protocol": "TCP"},
                                        {"name": "tmgr", "containerPort": 7374, "protocol": "TCP"},
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "configuration",
                                            "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                            "subPath": "gnb.conf",
                                        }
                                    ],
                                },
                                _debug_sidecar(),
                            ],
                            "volumes": [
                                {
                                    "name": "configuration",
                                    "configMap": {"name": "oai-du-configmap"},
                                }
                            ],
                        },
                    },
                },
            }
        ),
        written,
    )

    write(
        ns_dir / "46-configmap-oai-flexric-configmap.yaml",
        _dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "oai-flexric-configmap",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/part-of": "ina-infra"},
                },
                "data": {
                    "flexric.conf": (
                        f"[NEAR-RIC]\nNEAR_RIC_IP = {shared.flexric_e2}\n\n"
                        "[XAPP]\nDB_DIR = /tmp/\nDB_NAME = xapp_db\n"
                    )
                },
            }
        ),
        written,
    )
    write(
        ns_dir / "47-deployment-oai-flexric.yaml",
        _dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "oai-flexric",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/name": "oai-flexric",
                        "app.kubernetes.io/part-of": "ina-infra",
                    },
                },
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-flexric"}},
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "oai-flexric",
                                "app.kubernetes.io/name": "oai-flexric",
                            },
                            "annotations": {
                                "k8s.v1.cni.cncf.io/networks": _networks_annot(
                                    [("flexric-e2", "e2", shared.flexric_e2)],
                                    gw,
                                    plen,
                                )
                            },
                        },
                        "spec": {
                            "terminationGracePeriodSeconds": 5,
                            "nodeSelector": {
                                **ARCH_AMD64,
                                "kubernetes.io/hostname": "edge-1",
                            },
                            "containers": [
                                {
                                    "name": "flexric",
                                    "image": IMAGE_FLEXRIC,
                                    "imagePullPolicy": "IfNotPresent",
                                    "workingDir": "/tmp",
                                    "securityContext": {
                                        "privileged": True,
                                        "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]},
                                    },
                                    "command": ["stdbuf", "-o0", "nearRT-RIC"],
                                    "env": [
                                        {"name": "E2AP_VERSION", "value": "E2AP_V3"},
                                        {"name": "KPM_VERSION", "value": "KPM_V3_00"},
                                        {"name": "ASAN_OPTIONS", "value": "detect_leaks=0"},
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "configuration",
                                            "mountPath": "/usr/local/etc/flexric/flexric.conf",
                                            "subPath": "flexric.conf",
                                        }
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "configuration",
                                    "configMap": {"name": "oai-flexric-configmap"},
                                }
                            ],
                        },
                    },
                },
            }
        ),
        written,
    )

    for sl in ip_plan.slices:
        n = sl.n
        write(
            ns_dir / f"50-serviceaccount-oai-ue-{n}-sa.yaml",
            _dump(_sa(f"oai-ue-{n}-sa", namespace)),
            written,
        )
        write(
            ns_dir / f"51-configmap-oai-ue-{n}-configmap.yaml",
            _dump(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": f"oai-ue-{n}-configmap",
                        "namespace": namespace,
                        "labels": {
                            "app.kubernetes.io/part-of": "ina-infra",
                            "ina-infra.nephio.lab/slice": str(n),
                        },
                    },
                    "data": {"ue.conf": _ue_conf(sl, shared.du_rf)},
                }
            ),
            written,
        )
        write(
            ns_dir / f"52-deployment-oai-ue-{n}.yaml",
            _dump(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": f"oai-ue-{n}",
                        "namespace": namespace,
                        "labels": {
                            "app.kubernetes.io/name": f"oai-ue-{n}",
                            "app.kubernetes.io/part-of": "ina-infra",
                            "slice": str(n),
                        },
                    },
                    "spec": {
                        "replicas": 1,
                        "strategy": {"type": "Recreate"},
                        "selector": {
                            "matchLabels": {"app.kubernetes.io/name": f"oai-ue-{n}"}
                        },
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app": f"oai-ue-{n}",
                                    "app.kubernetes.io/name": f"oai-ue-{n}",
                                    "slice": str(n),
                                },
                                "annotations": {
                                    "k8s.v1.cni.cncf.io/networks": _networks_annot(
                                        [(f"ue{n}-sim-rf", "rf", sl.ue_rf)],
                                        gw,
                                        plen,
                                    )
                                },
                            },
                            "spec": {
                                "serviceAccountName": f"oai-ue-{n}-sa",
                                "terminationGracePeriodSeconds": 5,
                                "nodeSelector": {
                                    **ARCH_AMD64,
                                    "kubernetes.io/hostname": ue_node,
                                },
                                "containers": [
                                    {
                                        "name": "ue",
                                        "image": IMAGE_UE,
                                        "imagePullPolicy": "IfNotPresent",
                                        "securityContext": {"privileged": True},
                                        "env": [
                                            {
                                                "name": "USE_ADDITIONAL_OPTIONS",
                                                "value": (
                                                    "-r 133 --numerology 1 -C 3325620000 "
                                                    "--ssb 144 --rfsim "
                                                    "--log_config.global_log_options level,nocolor,time "
                                                    f"--rfsimulator.serveraddr {shared.du_rf}"
                                                ),
                                            },
                                            {"name": "TZ", "value": "Europe/Paris"},
                                        ],
                                        "volumeMounts": [
                                            {
                                                "name": "configuration",
                                                "mountPath": "/opt/oai-nr-ue/etc/nr-ue.conf",
                                                "subPath": "ue.conf",
                                            }
                                        ],
                                    },
                                    _debug_sidecar(),
                                ],
                                "volumes": [
                                    {
                                        "name": "configuration",
                                        "configMap": {"name": f"oai-ue-{n}-configmap"},
                                    }
                                ],
                            },
                        },
                    },
                }
            ),
            written,
        )


def _write_upf(
    ns_dir: Path,
    *,
    namespace: str,
    cluster: str,
    sl: SliceIps,
    ip_plan: IpPlan,
    write: WriteFn,
    written: List[str],
) -> None:
    """UPF NFConfig + NFDeployment for one slice on the PL UPF site."""
    shared = ip_plan.shared
    plen = shared.prefix_len
    gw = {
        "central": shared.gw_central,
        "regional": shared.gw_regional,
        "edge": shared.gw_edge,
    }.get(cluster, shared.gw_central)
    n = sl.n
    sd = _slice_sd_hex(n)

    write(
        ns_dir / f"34-nfconfig-upf-slice-{n}.yaml",
        _dump(
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFConfig",
                "metadata": {
                    "name": f"upf-slice-{n}-config",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/part-of": "ina-infra",
                        "ina-infra.nephio.lab/slice": str(n),
                    },
                },
                "spec": {
                    "configRefs": [
                        {
                            "apiVersion": "cellular.nephio.org/v1alpha1",
                            "kind": "PLMN",
                            "metadata": {"name": "oai-plmn"},
                            "spec": {
                                "plmnInfo": [
                                    {
                                        "plmnID": {"mcc": "001", "mnc": "01"},
                                        "tac": 81,
                                        "nssai": [
                                            {
                                                "sst": 1,
                                                "sd": sd,
                                                "dnnInfo": [
                                                    {
                                                        "name": _dnn(n),
                                                        "sessionType": "ipv4",
                                                        "dns": "1.1.1.1",
                                                        "subnet": sl.dnn_cidr,
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        ),
        written,
    )
    write(
        ns_dir / f"35-nfdeployment-upf-slice-{n}.yaml",
        _dump(
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFDeployment",
                "metadata": {
                    "name": f"upf-slice-{n}",
                    "namespace": namespace,
                    # Do NOT put per-slice labels here: the UPF operator copies all
                    # NFDeployment labels onto the shared Service `oai-upf` selector.
                    # Differing labels break multi-UPF in one namespace (Conflict / wrong sel).
                    "labels": {
                        "app.kubernetes.io/part-of": "ina-infra",
                    },
                },
                "spec": {
                    "provider": "upf.openairinterface.org",
                    "capacity": {
                        "maxDownlinkThroughput": "5G",
                        "maxUplinkThroughput": "5G",
                    },
                    "parametersRefs": [
                        {
                            "name": f"upf-slice-{n}-config",
                            "apiVersion": "workload.nephio.org/v1alpha1",
                            "kind": "NFConfig",
                        }
                    ],
                    "interfaces": [
                        {
                            "name": "n3",
                            "ipv4": {"address": f"{sl.upf_n3}/{plen}", "gateway": gw},
                            "vlanID": 4,
                        },
                        {
                            "name": "n4",
                            "ipv4": {"address": f"{sl.upf_n4}/{plen}", "gateway": gw},
                            "vlanID": 2,
                        },
                        {
                            "name": "n6",
                            "ipv4": {"address": f"{sl.upf_n6}/{plen}", "gateway": gw},
                            "vlanID": 3,
                        },
                    ],
                    "networkInstances": [
                        {"name": "vpc-internal", "interfaces": ["n4"]},
                        {
                            "name": "vpc-internet",
                            "dataNetworks": [
                                {"name": _dnn(n), "pool": [{"prefix": sl.dnn_cidr}]}
                            ],
                            "interfaces": ["n6"],
                        },
                        {"name": "vpc-ran", "interfaces": ["n3"]},
                    ],
                },
            }
        ),
        written,
    )


def _write_cuup(
    ns_dir: Path,
    *,
    namespace: str,
    cluster: str,
    sl: SliceIps,
    ip_plan: IpPlan,
    write: WriteFn,
    written: List[str],
) -> None:
    """CU-UP Deployment for one slice on the PL CU site (may differ from UPF)."""
    shared = ip_plan.shared
    plen = shared.prefix_len
    gw = {
        "central": shared.gw_central,
        "regional": shared.gw_regional,
        "edge": shared.gw_edge,
    }.get(cluster, shared.gw_central)
    n = sl.n
    nodes = SITE_NODES.get(cluster, SITE_NODES["central"])

    write(
        ns_dir / f"36-serviceaccount-oai-cu-up-{n}-sa.yaml",
        _dump(_sa(f"oai-cu-up-{n}-sa", namespace)),
        written,
    )
    write(
        ns_dir / f"37-configmap-oai-cu-up-{n}-configmap.yaml",
        _dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": f"oai-cu-up-{n}-configmap",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/part-of": "ina-infra",
                        "ina-infra.nephio.lab/slice": str(n),
                    },
                },
                "data": {"gnb.conf": _cuup_conf(sl, shared)},
            }
        ),
        written,
    )
    write(
        ns_dir / f"38-deployment-oai-cu-up-{n}.yaml",
        _dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": f"oai-cu-up-{n}",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/name": f"oai-cu-up-{n}",
                        "app.kubernetes.io/part-of": "ina-infra",
                        "slice": str(n),
                    },
                },
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {
                        "matchLabels": {"app.kubernetes.io/name": f"oai-cu-up-{n}"}
                    },
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": f"oai-cu-up-{n}",
                                "app.kubernetes.io/name": f"oai-cu-up-{n}",
                                "slice": str(n),
                            },
                            "annotations": {
                                "k8s.v1.cni.cncf.io/networks": _networks_annot(
                                    [
                                        (f"cuup-slice{n}-e1", "e1", sl.cuup_e1),
                                        (f"cuup-slice{n}-f1u", "f1u", sl.cuup_f1u),
                                        (f"cuup-slice{n}-n3", "n3", sl.cuup_n3),
                                    ],
                                    gw,
                                    plen,
                                ),
                                "oai.nephio.org/upf-n3": sl.upf_n3,
                                "oai.nephio.org/site": cluster,
                            },
                        },
                        "spec": {
                            "serviceAccountName": f"oai-cu-up-{n}-sa",
                            "terminationGracePeriodSeconds": 5,
                            "nodeSelector": dict(ARCH_AMD64),
                            "affinity": _hostname_affinity(nodes),
                            "containers": [
                                {
                                    "name": "cuup",
                                    "image": IMAGE_CUUP,
                                    "imagePullPolicy": "IfNotPresent",
                                    "securityContext": {"privileged": True},
                                    "env": [
                                        {"name": "TZ", "value": "Europe/Paris"},
                                        {
                                            "name": "USE_ADDITIONAL_OPTIONS",
                                            "value": "--log_config.global_log_options level,nocolor,time",
                                        },
                                        {"name": "USE_VOLUMED_CONF", "value": "yes"},
                                    ],
                                    "ports": [
                                        {"name": "n3", "containerPort": 2152, "protocol": "UDP"},
                                        {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "configuration",
                                            "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                            "subPath": "gnb.conf",
                                        }
                                    ],
                                },
                                _debug_sidecar(),
                            ],
                            "volumes": [
                                {
                                    "name": "configuration",
                                    "configMap": {"name": f"oai-cu-up-{n}-configmap"},
                                }
                            ],
                        },
                    },
                },
            }
        ),
        written,
    )


def write_ran_for_cluster(
    ns_dir: Path,
    *,
    cluster: str,
    namespace: str,
    ip_plan: IpPlan,
    result: PlSolveResponse,
    write: WriteFn,
    written: List[str],
    du_node: str = "usrp",
    ue_node: str = "usrp",
) -> None:
    """Emit edge gNB stack and/or per-slice UPF / CU-UP for this cluster.

    UPF follows PL ``upf_id``; CU-UP follows PL ``cu_id`` (may differ, e.g. OTT).
    Application site (``app_id``) is planning-only — no APP Deployments are emitted.
    """
    if cluster == "edge":
        _write_edge_gnb(
            ns_dir,
            namespace=namespace,
            ip_plan=ip_plan,
            write=write,
            written=written,
            du_node=du_node,
            ue_node=ue_node,
        )

    for sl in ip_plan.slices:
        place = result.deploy_map.get(str(sl.slice_id))
        upf_cluster = (
            SITE_TO_CLUSTER.get(place.upf_id, "central") if place else "central"
        )
        cu_cluster = (
            SITE_TO_CLUSTER.get(place.cu_id, "edge") if place else "edge"
        )
        if cluster == upf_cluster:
            _write_upf(
                ns_dir,
                namespace=namespace,
                cluster=cluster,
                sl=sl,
                ip_plan=ip_plan,
                write=write,
                written=written,
            )
        if cluster == cu_cluster:
            _write_cuup(
                ns_dir,
                namespace=namespace,
                cluster=cluster,
                sl=sl,
                ip_plan=ip_plan,
                write=write,
                written=written,
            )


def expected_deployments(
    cluster: str,
    *,
    n_slices: int,
    deploy_map: Optional[Mapping[str, Union[PlacementOut, Dict[str, Any]]]] = None,
    include_core: bool = True,
    include_ran: bool = True,
) -> List[str]:
    """Expected Deployment names after include_core + include_ran Apply.

    deploy_map keys are slice_id strings; UPF uses ``upf_id``, CU-UP uses ``cu_id``.
    No application Deployments (APP site is PL-only).
    """
    names: List[str] = []
    if cluster == "central" and include_core:
        names.extend(
            [
                "mysql",
                "nrf-core",
                "ausf-core",
                "udm-core",
                "udr-core",
                "amf-core",
                "smf-core",
            ]
        )
    if not include_ran:
        return names

    if cluster == "edge":
        names.extend(["oai-cu-cp", "oai-du", "oai-flexric"])
        for n in range(1, n_slices + 1):
            names.append(f"oai-ue-{n}")

    dm = deploy_map or {}
    for n in range(1, n_slices + 1):
        place = dm.get(str(n))
        if place is None:
            continue
        upf_id = getattr(place, "upf_id", None)
        cu_id = getattr(place, "cu_id", None)
        if isinstance(place, dict):
            if upf_id is None:
                upf_id = place.get("upf_id")
            if cu_id is None:
                cu_id = place.get("cu_id")
        upf_cluster = SITE_TO_CLUSTER.get(
            int(upf_id) if upf_id is not None else 2, "central"
        )
        cu_cluster = SITE_TO_CLUSTER.get(
            int(cu_id) if cu_id is not None else 0, "edge"
        )
        if cluster == upf_cluster:
            names.append(f"upf-slice-{n}")
        if cluster == cu_cluster:
            names.append(f"oai-cu-up-{n}")
    return names

